"""Provider allocation — scores providers to prevent limits while clearing wagering."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..constants import PLATFORM_MAP
from ..db.models import Bet, ProfileProviderBalance, ProfileProviderBonus, ProfileProviderLimit, RiskConfig

logger = logging.getLogger(__name__)

# Override platform grouping for limit tracking.
# 10bet uses Playtech but shares Altenar risk signals with betinia/dbet.
LIMIT_PLATFORM_OVERRIDES: dict[str, str] = {
    "10bet": "altenar",
}

DEFAULT_DAILY_CAP = 0  # 0 = no cap


@dataclass
class AllocationResult:
    provider_id: str
    score: float  # 0-100 (higher = bet here), -1 if capped
    reason: str  # Human-readable recommendation
    daily_bets_group: int  # Bets placed today across platform group
    daily_cap: int  # Max bets per day per platform group
    is_capped: bool  # True if at/over daily cap
    wagering_remaining: float  # Wagering requirement remaining (0 if cleared)
    edge_routing: str | None = None  # "high_edge_unlimited", "grind_ok", or None


class ProviderAllocator:
    """Scores providers to balance wagering clearance vs limit avoidance."""

    def __init__(self, db: Session, profile_id: int):
        self.db = db
        self.profile_id = profile_id
        self._daily_bets: dict[str, int] = {}  # provider_id -> bets today
        self._wagering: dict[str, dict] = {}  # provider_id -> bonus info
        self._balances: dict[str, float] = {}  # provider_id -> balance
        self._limits: dict[str, int] = {}  # provider_id -> limit_level
        self._daily_cap = self._load_daily_cap()

    def _load_daily_cap(self) -> int:
        cfg = self.db.query(RiskConfig).filter(RiskConfig.profile_id == self.profile_id).first()
        if cfg and hasattr(cfg, "daily_bet_cap") and cfg.daily_bet_cap is not None:
            return cfg.daily_bet_cap
        return DEFAULT_DAILY_CAP

    def _get_limit_platform(self, provider_id: str) -> str:
        """Get the platform group for limit tracking (with overrides)."""
        if provider_id in LIMIT_PLATFORM_OVERRIDES:
            return LIMIT_PLATFORM_OVERRIDES[provider_id]
        return PLATFORM_MAP.get(provider_id, provider_id)

    def _get_group_providers(self, platform: str) -> list[str]:
        """Get all providers in a limit platform group."""
        providers = []
        for pid, plat in PLATFORM_MAP.items():
            if plat == platform:
                providers.append(pid)
        # Also include override members
        for pid, plat in LIMIT_PLATFORM_OVERRIDES.items():
            if plat == platform and pid not in providers:
                providers.append(pid)
        return providers

    def preload_daily_bets(self) -> None:
        """Single query: count today's bets per provider."""
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            self.db.query(Bet.provider_id, func.count(Bet.id))
            .filter(
                Bet.profile_id == self.profile_id,
                Bet.placed_at >= today_start,
            )
            .group_by(Bet.provider_id)
            .all()
        )
        self._daily_bets = {pid: count for pid, count in rows}

    def preload_wagering(self) -> None:
        """Single query: get all active wagering bonuses."""
        rows = (
            self.db.query(ProfileProviderBonus)
            .filter(
                ProfileProviderBonus.profile_id == self.profile_id,
                ProfileProviderBonus.bonus_status.in_(["in_progress", "trigger_needed", "freebet_available"]),
            )
            .all()
        )
        for bonus in rows:
            req = bonus.wagering_requirement or 0
            wagered = bonus.wagered_amount or 0
            remaining = max(0, req - wagered)
            self._wagering[bonus.provider_id] = {
                "status": bonus.bonus_status,
                "remaining": remaining,
                "bonus_amount": bonus.bonus_amount or 0,
            }

    def preload_balances(self) -> None:
        """Single query: get all provider balances."""
        rows = self.db.query(ProfileProviderBalance).filter(ProfileProviderBalance.profile_id == self.profile_id).all()
        for row in rows:
            self._balances[row.provider_id] = row.balance or 0.0

    def preload_limits(self) -> None:
        """Single query: get all provider limits (highest level per provider)."""
        rows = self.db.query(ProfileProviderLimit).filter(ProfileProviderLimit.profile_id == self.profile_id).all()
        for row in rows:
            existing = self._limits.get(row.provider_id, 0)
            self._limits[row.provider_id] = max(existing, row.limit_level)

    def get_balance(self, provider_id: str) -> float:
        """Get preloaded balance for a provider."""
        return self._balances.get(provider_id, 0.0)

    def get_wagering_info(self, provider_id: str) -> dict:
        """Get preloaded wagering info for a provider."""
        return self._wagering.get(provider_id, {})

    def get_limit_level(self, provider_id: str) -> int:
        """Get preloaded limit level for a provider (0 = no limit)."""
        return self._limits.get(provider_id, 0)

    def _count_group_bets(self, provider_id: str) -> int:
        """Count today's bets across the provider's limit platform group."""
        platform = self._get_limit_platform(provider_id)
        group = self._get_group_providers(platform)
        return sum(self._daily_bets.get(pid, 0) for pid in group)

    def score_provider(self, provider_id: str, edge_pct: float | None = None) -> AllocationResult:
        """Compute allocation score for a provider (0-100, higher = bet here).

        Args:
            provider_id: The provider to score.
            edge_pct: Optional edge percentage for edge-based routing.
                      High-edge bets are penalized at limited providers;
                      low-edge bets get a bonus (good for wagering grind).
        """
        group_bets = self._count_group_bets(provider_id)
        cap = self._daily_cap
        is_capped = cap > 0 and group_bets >= cap

        # --- Banned check (level 5 = account closed) ---
        limit_level = self._limits.get(provider_id, 0)
        if limit_level >= 5:
            return AllocationResult(
                provider_id=provider_id,
                score=-1,
                reason="Banned — account closed",
                daily_bets_group=group_bets,
                daily_cap=cap,
                is_capped=True,
                wagering_remaining=0,
                edge_routing=None,
            )

        # --- Wagering score (0-40) ---
        wager_info = self._wagering.get(provider_id, {})
        remaining = wager_info.get("remaining", 0)
        wagering_score = min(40, remaining / 300)

        # --- Daily room score (0-30) ---
        if cap > 0:
            room = max(0, cap - group_bets)
            daily_room_score = 30 * room / cap
        else:
            daily_room_score = 30  # No cap = full room score

        # --- Balance score (0-20) ---
        balance = self._balances.get(provider_id, 0)
        balance_score = 20 if balance > 0 else 0

        # --- Bonus type score (0-10) ---
        status = wager_info.get("status", "")
        if status in ("trigger_needed", "freebet_available"):
            bonus_type_score = 10
        elif status == "in_progress":
            bonus_type_score = 5
        else:
            bonus_type_score = 0

        total = wagering_score + daily_room_score + balance_score + bonus_type_score

        # --- Edge-based routing (limited provider handling) ---
        edge_routing = None
        if edge_pct is not None and limit_level >= 1:
            if edge_pct >= 5.0 and limit_level >= 2:
                # Don't waste high-edge bets on limited providers
                total -= 15
                edge_routing = "high_edge_unlimited"
            elif edge_pct < 4.0:
                # Low-edge bets are fine for wagering grind at limited providers
                total += 10
                edge_routing = "grind_ok"

        # Build reason string
        reason = self._build_reason(remaining, status, group_bets, cap, balance, limit_level, edge_routing)

        if is_capped:
            total = -1
            reason = f"Daily cap reached ({group_bets}/{cap})"
        elif cap == 0:
            # No cap - never block
            pass

        return AllocationResult(
            provider_id=provider_id,
            score=round(total, 1),
            reason=reason,
            daily_bets_group=group_bets,
            daily_cap=cap,
            is_capped=is_capped,
            wagering_remaining=remaining,
            edge_routing=edge_routing,
        )

    def _build_reason(
        self,
        remaining: float,
        status: str,
        group_bets: int,
        cap: int,
        balance: float,
        limit_level: int = 0,
        edge_routing: str | None = None,
    ) -> str:
        """Build a concise human-readable reason for the allocation score."""
        parts = []
        if status == "trigger_needed":
            parts.append("Trigger needed")
        elif status == "freebet_available":
            parts.append("Freebet ready")
        elif remaining > 0:
            parts.append(f"{remaining:.0f} kr wager left")

        if balance <= 0:
            parts.append("No balance")

        if limit_level >= 1:
            parts.append(f"Lim L{limit_level}")

        if edge_routing == "high_edge_unlimited":
            parts.append("high-edge→unlimited")
        elif edge_routing == "grind_ok":
            parts.append("grind-ok")

        if cap > 0:
            parts.append(f"{group_bets}/{cap} today")
        else:
            parts.append(f"{group_bets} today")

        return " · ".join(parts)
