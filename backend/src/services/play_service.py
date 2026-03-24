"""Play session service — computes session data for the Play panel."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.constants import PLATFORM_GROUPS, PLATFORM_MAP
from src.bankroll.stake_calculator import dynamic_min_stake
from src.repositories.profile_repo import ProfileRepo
from src.risk.allocator import ProviderAllocator


def derive_lifecycle(
    balance: float,
    bonus_status: str | None,
    limit_level: int | None,
) -> str:
    """Derive provider lifecycle state from existing data.

    Returns one of: available, deposited, wagering, freebet, playing, limited, dormant.
    """
    has_balance = balance > 0

    is_limited = limit_level is not None and limit_level > 0

    if not has_balance and bonus_status in (None, "available", "completed", "claimed"):
        return "dormant" if bonus_status in ("completed", "claimed") else "available"

    if bonus_status == "trigger_needed":
        return "deposited"
    if bonus_status == "freebet_available":
        return "freebet"
    if bonus_status == "in_progress":
        return "wagering"

    # Has balance, no active bonus restriction
    if is_limited:
        return "limited"
    return "playing"


class PlaySessionService:
    """Builds session data for the Play panel."""

    def __init__(self, db: Session):
        self.db = db
        self.profile_repo = ProfileRepo(db)

    def get_session(self, profile_id: int) -> dict:
        """Build complete session data: clusters with siblings, states, opp counts."""

        # Create allocator with profile_id (required by constructor)
        allocator = ProviderAllocator(self.db, profile_id)

        # Preload all data in bulk (parameterless, use self.profile_id internally)
        allocator.preload_daily_bets()
        allocator.preload_wagering()
        allocator.preload_balances()
        allocator.preload_limits()

        balances = allocator._balances
        wagering = allocator._wagering
        limits = allocator._limits

        # Count unique opps per cluster
        cluster_opp_counts = self._count_unique_opps_per_cluster()

        # Get min stake threshold
        total_bankroll = sum(balances.values())
        min_stake = dynamic_min_stake(total_bankroll)

        clusters = []
        for group_name, group_info in PLATFORM_GROUPS.items():
            cluster = self._build_cluster(
                group_name, group_info["members"], group_info["canonical"],
                balances, wagering, limits,
                cluster_opp_counts.get(group_name, 0),
                min_stake, profile_id,
            )
            if cluster:
                clusters.append(cluster)

        # Standalone providers (not in any group, not sharp/prediction)
        grouped = set()
        for g in PLATFORM_GROUPS.values():
            grouped.update(g["members"])

        for pid in PLATFORM_MAP:
            if pid not in grouped and pid not in ("pinnacle", "polymarket"):
                cluster = self._build_cluster(
                    pid, [pid], pid,
                    balances, wagering, limits,
                    cluster_opp_counts.get(pid, 0),
                    min_stake, profile_id,
                )
                if cluster:
                    clusters.append(cluster)

        # Sort clusters by wagering urgency (highest urgency first)
        clusters.sort(key=lambda c: c["urgency"], reverse=True)

        return {
            "clusters": clusters,
            "total_bankroll": round(total_bankroll, 2),
            "min_stake": round(min_stake, 2),
        }

    def _build_cluster(
        self, name: str, members: list[str], canonical: str,
        balances: dict, wagering: dict, limits: dict,
        unique_opps: int, min_stake: float, profile_id: int,
    ) -> dict | None:
        """Build cluster dict with active siblings and lifecycle states."""

        siblings = []
        for pid in members:
            balance = balances.get(pid, 0)
            wag = wagering.get(pid, {})
            limit_level = limits.get(pid)
            bonus_status = wag.get("status")

            lifecycle = derive_lifecycle(balance, bonus_status, limit_level)

            # get_bonus_status always returns a dict (never None)
            bonus_info = self.profile_repo.get_bonus_status(profile_id, pid)

            siblings.append({
                "provider_id": pid,
                "balance": round(balance, 2),
                "lifecycle": lifecycle,
                "bonus_status": bonus_status,
                "trigger_mode": bonus_info.get("trigger_mode", "cumulative"),
                "wagering_remaining": round(wag.get("remaining", 0), 2),
                "wagering_progress_pct": round(bonus_info.get("progress_pct", 0), 1),
                "min_odds": bonus_info.get("min_odds", 1.80),
                "bonus_amount": bonus_info.get("bonus_amount", 0),
                "limit_level": limit_level,
                "expires_at": bonus_info.get("expires_at"),
                "days_remaining": bonus_info.get("days_remaining"),
            })

        # Determine max active siblings: 2 if >=30 unique opps, else 1
        max_siblings = 2 if unique_opps >= 30 else 1

        # Pick active siblings: non-dormant, non-available, sorted by urgency
        active_states = ("deposited", "wagering", "freebet", "playing", "limited")
        active = [s for s in siblings if s["lifecycle"] in active_states]
        active.sort(key=lambda s: self._sibling_urgency(s), reverse=True)
        active = active[:max_siblings]

        total_balance = sum(s["balance"] for s in active)
        available = [s for s in siblings if s["lifecycle"] == "available"]
        dormant = [s for s in siblings if s["lifecycle"] == "dormant"]

        # Hide cluster only if zero opps AND no active/available siblings
        if unique_opps == 0 and not active and not available:
            return None

        urgency = max((self._sibling_urgency(s) for s in active), default=0)

        # Recommend depositing if: has opps but fewer active siblings than max
        needs_deposit = unique_opps > 0 and len(active) < max_siblings and len(available) > 0
        # How many more siblings to recommend
        recommended_count = min(max_siblings - len(active), len(available))
        # Pick the recommended ones (first N available)
        recommended = available[:recommended_count] if needs_deposit else []

        return {
            "id": name,
            "label": name.replace("_", " ").title(),
            "canonical": canonical,
            "active_siblings": active,
            "available_siblings": available,
            "recommended_siblings": recommended,
            "dormant_siblings": dormant,
            "total_balance": round(total_balance, 2),
            "playable_count": len(active),
            "unique_opps": unique_opps,
            "urgency": round(urgency, 2),
            "needs_deposit": needs_deposit,
        }

    @staticmethod
    def _sibling_urgency(sibling: dict) -> float:
        """Score sibling by wagering urgency. Higher = more urgent."""
        remaining = sibling.get("wagering_remaining", 0)
        days = sibling.get("days_remaining") or 60

        phase_bonus = {
            "deposited": 100,
            "freebet": 90,
            "wagering": 50 + (remaining / max(days, 1)),
            "playing": 10,
            "limited": 5,
        }
        return phase_bonus.get(sibling["lifecycle"], 0)

    def _count_unique_opps_per_cluster(self) -> dict[str, int]:
        """Count unique (event+market+outcome) opportunities per cluster."""
        from sqlalchemy import text

        provider_cluster: dict[str, str] = {}
        for group_name, group_info in PLATFORM_GROUPS.items():
            for pid in group_info["members"]:
                provider_cluster[pid] = group_name
        grouped = set()
        for g in PLATFORM_GROUPS.values():
            grouped.update(g["members"])
        for pid in PLATFORM_MAP:
            if pid not in grouped and pid not in ("pinnacle", "polymarket"):
                provider_cluster[pid] = pid

        result = self.db.execute(text("""
            SELECT provider1_id, event_id, market, outcome1
            FROM opportunities
            WHERE type = 'value' AND is_active = 1
        """))

        cluster_unique: dict[str, set] = {}
        for row in result:
            cluster = provider_cluster.get(row[0])
            if cluster:
                if cluster not in cluster_unique:
                    cluster_unique[cluster] = set()
                cluster_unique[cluster].add((row[1], row[2], row[3]))

        return {c: len(keys) for c, keys in cluster_unique.items()}
