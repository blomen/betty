"""
BatchBuilder service — collects all +EV opportunities, deduplicates across cluster
siblings, ranks by tier (sharp first) then expected profit, allocates balance, and
returns a ready-to-fire batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..constants import PLATFORM_GROUPS, PLATFORM_MAP
from ..bankroll.stake_calculator import calculate_stake, dynamic_min_stake
from ..repositories.opportunity_repo import OpportunityRepo
from ..repositories.profile_repo import ProfileRepo
from ..services.play_service import derive_lifecycle

logger = logging.getLogger(__name__)

# Tier priority: higher is ranked first
TIER_PRIORITY = {"sharp": 1, "soft": 0}
SHARP_PROVIDERS = frozenset({"pinnacle", "polymarket"})


@dataclass
class BatchBet:
    """A single bet candidate in the batch pipeline."""

    # Ranking / tier
    rank: int
    tier: str                       # "sharp" or "soft"

    # Bet identity
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: Optional[float]
    odds: float
    fair_odds: float
    edge_pct: float                 # percentage (e.g. 3.5 for 3.5%)

    # Stake & EV
    stake: float
    expected_profit: float

    # Bonus context
    is_bonus: bool
    bonus_type: Optional[str]

    # Display
    display_home: str
    display_away: str
    sport: str
    league: Optional[str]
    start_time: Optional[object]

    # Lifecycle / cluster
    lifecycle: str
    cluster: str                    # cluster / group name (e.g. "kambi", "vbet")

    # Skip info (populated for missed bets)
    skip_reason: Optional[str] = None


@dataclass
class ProviderBalance:
    """Tracks balance and allocation state for one provider during batch building."""

    provider_id: str
    cluster: str
    initial_balance: float
    allocated: float = 0.0

    # Lifecycle state (derive_lifecycle result)
    lifecycle: str = "playing"

    # Bonus constraint
    min_odds: float = 0.0
    trigger_mode: str = "cumulative"
    bonus_amount: float = 0.0
    is_bonus_phase: bool = False    # True when in freebet_available phase

    # Missed bets stats
    missed_bets: int = 0
    missed_ev: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.initial_balance - self.allocated)


def _provider_to_cluster(provider_id: str) -> str:
    """Return the cluster name for a provider_id."""
    for group_name, group_info in PLATFORM_GROUPS.items():
        if provider_id in group_info["members"]:
            return group_name
    # Standalone
    return provider_id


class BatchBuilder:
    """
    Builds a ready-to-fire batch across all opportunity types for a given profile.

    Usage:
        builder = BatchBuilder(db)
        result = builder.build(profile_id=1)
    """

    def __init__(self, db: Session):
        self.db = db
        self.opp_repo = OpportunityRepo(db)
        self.profile_repo = ProfileRepo(db)

    def build(self, profile_id: int) -> dict:
        """
        Main entry point. Returns a dict with:
          - batch: list of bet dicts (ranked, allocated)
          - summary: aggregate stats
          - balance_status: per-provider status
          - missed_opportunities: summary of bets that couldn't be placed
        """
        profile = self.profile_repo.get_active()
        total_bankroll = self.profile_repo.get_total_bankroll(profile_id)

        provider_balances = self._load_provider_balances(profile_id)

        candidates = self._collect_candidates(
            total_bankroll, provider_balances, profile
        )

        # Sort ALL candidates: sharp first, then by expected_profit desc
        # Don't deduplicate before allocation — dedup happens during allocation
        # so bets distribute across siblings by remaining balance
        ranked = sorted(
            candidates,
            key=lambda b: (-TIER_PRIORITY.get(b.tier, 0), -b.expected_profit),
        )

        batch, missed = self._allocate_with_dedup(ranked, provider_balances)
        for i, bet in enumerate(batch):
            bet.rank = i + 1

        return {
            "batch": [self._bet_to_dict(b) for b in batch],
            "summary": self._build_summary(batch),
            "balance_status": self._build_balance_status(provider_balances, missed),
            "missed_opportunities": self._build_missed_summary(missed),
            "deposit_recommendations": self._build_deposit_recommendations(
                provider_balances, missed, total_bankroll
            ),
        }

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _load_provider_balances(self, profile_id: int) -> dict[str, ProviderBalance]:
        """Load balances and bonus states for all providers with balance > 0."""
        raw_balances = self.profile_repo.get_all_balances(profile_id)
        result: dict[str, ProviderBalance] = {}

        for provider_id, balance in raw_balances.items():
            bonus_info = self.profile_repo.get_bonus_status(profile_id, provider_id)
            bonus_status = bonus_info.get("status")
            limit_level = None  # not used for batch building (lifecycle only)

            lifecycle = derive_lifecycle(balance, bonus_status, limit_level)

            is_bonus_phase = bonus_status == "freebet_available"

            result[provider_id] = ProviderBalance(
                provider_id=provider_id,
                cluster=_provider_to_cluster(provider_id),
                initial_balance=balance,
                lifecycle=lifecycle,
                min_odds=bonus_info.get("min_odds", 0.0),
                trigger_mode=bonus_info.get("trigger_mode", "cumulative"),
                bonus_amount=bonus_info.get("bonus_amount", 0.0),
                is_bonus_phase=is_bonus_phase,
            )

        return result

    def _collect_candidates(
        self,
        total_bankroll: float,
        provider_balances: dict[str, ProviderBalance],
        profile,
    ) -> list[BatchBet]:
        """Query all opportunity types and compute stakes."""

        # Profile settings for stake calculation
        kelly_fraction = getattr(profile, "kelly_fraction", 0.75) or 0.75
        max_stake_pct = getattr(profile, "max_stake_pct", 5.0) or 5.0
        min_edge_pct = getattr(profile, "min_edge_pct", 2.0) or 2.0

        single_bet_cap_pct = max_stake_pct / 100.0
        min_edge = min_edge_pct / 100.0
        min_stake = dynamic_min_stake(total_bankroll)

        candidates: list[BatchBet] = []

        # Collect value opps (soft providers + polymarket stored as type="value")
        for opp, event in self.opp_repo.find_active(type="value"):
            bet = self._make_candidate(
                opp, event, "value",
                total_bankroll, provider_balances,
                kelly_fraction, single_bet_cap_pct, min_edge, min_stake,
            )
            if bet is not None:
                candidates.append(bet)

        # Collect reverse_value opps (Pinnacle vs consensus)
        for opp, event in self.opp_repo.find_active(type="reverse_value"):
            bet = self._make_candidate(
                opp, event, "reverse_value",
                total_bankroll, provider_balances,
                kelly_fraction, single_bet_cap_pct, min_edge, min_stake,
            )
            if bet is not None:
                candidates.append(bet)

        return candidates

    def _make_candidate(
        self,
        opp,
        event,
        opp_type: str,
        total_bankroll: float,
        provider_balances: dict[str, ProviderBalance],
        kelly_fraction: float,
        single_bet_cap_pct: float,
        min_edge: float,
        min_stake: float,
    ) -> Optional[BatchBet]:
        """Convert an Opportunity+Event into a BatchBet candidate, or None to skip."""

        provider_id = opp.provider1_id
        pb = provider_balances.get(provider_id)

        # Skip if provider has no balance tracked
        if pb is None:
            return None

        # Skip dormant / available providers
        if pb.lifecycle in ("dormant", "available"):
            return None

        odds = opp.odds1 or 0.0
        fair_odds = opp.odds2 or 0.0
        edge_raw = (opp.edge_pct or 0.0) / 100.0

        # Determine min_odds for this bet
        # Bonus phase (wagering / trigger_needed): enforce per-provider min_odds
        # Cleared or playing: no restriction
        if pb.lifecycle in ("wagering", "deposited"):
            bet_min_odds = pb.min_odds if pb.min_odds else 1.80
        else:
            bet_min_odds = 0.0

        # Skip if odds don't meet bonus requirement
        if bet_min_odds > 0 and odds < bet_min_odds:
            return None

        # Detect bonus bet types
        is_freebet = pb.is_bonus_phase
        is_trigger = pb.lifecycle == "deposited" and pb.trigger_mode == "single"

        # Calculate stake
        if is_freebet:
            # Freebet: stake = bonus_amount (fixed), no bankroll consumption
            stake = pb.bonus_amount if pb.bonus_amount > 0 else 0.0
            is_bonus = True
            bonus_type = "freebet"
        elif is_trigger:
            # Single-shot trigger: fixed stake = bonus_amount
            stake = pb.bonus_amount if pb.bonus_amount > 0 else 0.0
            is_bonus = False
            bonus_type = "trigger"
        else:
            result = calculate_stake(
                bankroll_total=total_bankroll,
                edge_raw=edge_raw,
                odds=odds,
                single_bet_cap_pct=single_bet_cap_pct,
                min_edge=min_edge,
                min_odds=bet_min_odds,
                min_stake=min_stake,
                max_kelly=kelly_fraction,
            )
            if result.skip_reason:
                return None
            stake = result.stake
            is_bonus = False
            bonus_type = None

        if stake <= 0:
            return None

        expected_profit = stake * edge_raw

        tier = "sharp" if provider_id in SHARP_PROVIDERS else "soft"
        cluster = pb.cluster

        return BatchBet(
            rank=0,  # assigned later
            tier=tier,
            provider_id=provider_id,
            event_id=opp.event_id,
            market=opp.market,
            outcome=opp.outcome1 or "",
            point=opp.point,
            odds=odds,
            fair_odds=fair_odds,
            edge_pct=opp.edge_pct or 0.0,
            stake=stake,
            expected_profit=expected_profit,
            is_bonus=is_bonus,
            bonus_type=bonus_type,
            display_home=event.display_home or event.home_team or "",
            display_away=event.display_away or event.away_team or "",
            sport=event.sport or "",
            league=event.league,
            start_time=event.start_time,
            lifecycle=pb.lifecycle,
            cluster=cluster,
        )

    def _deduplicate(
        self,
        candidates: list[BatchBet],
        provider_balances: dict[str, ProviderBalance],
    ) -> list[BatchBet]:
        """
        Within a cluster, keep only one copy of each (event_id, market, outcome, point).
        When duplicates exist, pick the provider with the most remaining balance.
        """
        # Group by (cluster, event_id, market, outcome, point)
        seen: dict[tuple, BatchBet] = {}

        for bet in candidates:
            key = (bet.cluster, bet.event_id, bet.market, bet.outcome, bet.point)
            if key not in seen:
                seen[key] = bet
            else:
                existing = seen[key]
                existing_balance = provider_balances.get(
                    existing.provider_id, ProviderBalance(
                        provider_id=existing.provider_id, cluster=existing.cluster,
                        initial_balance=0.0
                    )
                ).remaining
                new_balance = provider_balances.get(
                    bet.provider_id, ProviderBalance(
                        provider_id=bet.provider_id, cluster=bet.cluster,
                        initial_balance=0.0
                    )
                ).remaining
                if new_balance > existing_balance:
                    seen[key] = bet

        return list(seen.values())

    def _allocate_with_dedup(
        self,
        ranked: list[BatchBet],
        provider_balances: dict[str, ProviderBalance],
    ) -> tuple[list[BatchBet], list[BatchBet]]:
        """
        Greedy allocation with inline dedup across cluster siblings.

        For each bet, check if the same (cluster, event, market, outcome, point)
        was already placed on another sibling. If so, skip (not missed — just
        a duplicate). This naturally distributes bets across siblings by remaining
        balance since we process highest-balance providers first for each event.
        """
        batch: list[BatchBet] = []
        missed: list[BatchBet] = []
        # Track placed bet keys per cluster to avoid duplicates
        placed_keys: set[tuple] = set()

        for bet in ranked:
            # Dedup key: within a cluster, only one copy per event+market+outcome+point
            # Sharp providers use provider_id as cluster (no dedup across sharps)
            cluster_key = bet.cluster if bet.tier == "soft" and bet.cluster else bet.provider_id
            dedup_key = (cluster_key, bet.event_id, bet.market, bet.outcome, bet.point)

            if dedup_key in placed_keys:
                continue  # Already placed on another sibling — skip silently

            pb = provider_balances.get(bet.provider_id)
            if pb is None:
                bet.skip_reason = "no balance record"
                missed.append(bet)
                continue

            # Freebets don't consume real balance
            if bet.is_bonus and bet.bonus_type == "freebet":
                placed_keys.add(dedup_key)
                batch.append(bet)
                continue

            if pb.remaining >= bet.stake:
                pb.allocated += bet.stake
                placed_keys.add(dedup_key)
                batch.append(bet)
            else:
                # Don't mark as placed — another sibling might have balance
                # Only mark as missed if no sibling can take it
                # (this happens naturally: if sibling B has balance, its candidate
                # will appear later in the ranked list and get placed)
                bet.skip_reason = (
                    f"insufficient balance "
                    f"(need {bet.stake:.0f}, have {pb.remaining:.0f})"
                )
                pb.missed_bets += 1
                pb.missed_ev += bet.expected_profit
                missed.append(bet)

        return batch, missed

    def _build_summary(self, batch: list[BatchBet]) -> dict:
        sharp_bets = [b for b in batch if b.tier == "sharp"]
        soft_bets = [b for b in batch if b.tier == "soft"]
        return {
            "total_bets": len(batch),
            "total_stake": round(sum(b.stake for b in batch), 2),
            "total_expected_profit": round(sum(b.expected_profit for b in batch), 2),
            "sharp_bets": len(sharp_bets),
            "sharp_ev": round(sum(b.expected_profit for b in sharp_bets), 2),
            "soft_bets": len(soft_bets),
            "soft_ev": round(sum(b.expected_profit for b in soft_bets), 2),
        }

    def _build_balance_status(
        self,
        provider_balances: dict[str, ProviderBalance],
        missed: list[BatchBet],
    ) -> list[dict]:
        rows = []
        for pid, pb in sorted(provider_balances.items()):
            row: dict = {
                "provider_id": pid,
                "cluster": pb.cluster,
                "balance": round(pb.initial_balance, 2),
                "allocated": round(pb.allocated, 2),
                "remaining": round(pb.remaining, 2),
                "lifecycle": pb.lifecycle,
                "missed_bets": pb.missed_bets,
                "missed_ev": round(pb.missed_ev, 2),
            }
            # Flag excess balance (more allocated than initial — shouldn't happen)
            if pb.allocated > pb.initial_balance:
                row["excess"] = round(pb.allocated - pb.initial_balance, 2)
            rows.append(row)
        return rows

    def _build_missed_summary(self, missed: list[BatchBet]) -> dict:
        if not missed:
            return {"total_bets": 0, "total_ev": 0.0, "reason": "all bets allocated"}
        reasons = {}
        for b in missed:
            r = b.skip_reason or "unknown"
            reasons[r] = reasons.get(r, 0) + 1
        primary_reason = max(reasons, key=lambda k: reasons[k])
        return {
            "total_bets": len(missed),
            "total_ev": round(sum(b.expected_profit for b in missed), 2),
            "reason": primary_reason,
            "reason_breakdown": reasons,
        }

    @staticmethod
    def _bet_to_dict(bet: BatchBet) -> dict:
        return {
            "rank": bet.rank,
            "tier": bet.tier,
            "provider_id": bet.provider_id,
            "event_id": bet.event_id,
            "market": bet.market,
            "outcome": bet.outcome,
            "point": bet.point,
            "odds": round(bet.odds, 3),
            "fair_odds": round(bet.fair_odds, 3),
            "edge_pct": round(bet.edge_pct, 2),
            "stake": round(bet.stake, 2),
            "expected_profit": round(bet.expected_profit, 2),
            "is_bonus": bet.is_bonus,
            "bonus_type": bet.bonus_type,
            "display_home": bet.display_home,
            "display_away": bet.display_away,
            "sport": bet.sport,
            "league": bet.league,
            "start_time": bet.start_time.isoformat() if bet.start_time else None,
            "lifecycle": bet.lifecycle,
            "cluster": bet.cluster,
        }

    def _build_deposit_recommendations(
        self,
        provider_balances: dict[str, ProviderBalance],
        missed: list[BatchBet],
        total_bankroll: float,
    ) -> list[dict]:
        """
        Calculate optimal deposit amounts per cluster.

        For providers with shortfall (missed bets due to insufficient balance),
        recommend depositing enough to cover the missed stake.
        For unfunded clusters with opportunities, recommend a default deposit.
        """
        # Group missed bets by cluster
        cluster_missed: dict[str, float] = {}  # cluster -> total missed stake
        cluster_missed_ev: dict[str, float] = {}
        cluster_missed_count: dict[str, int] = {}

        for bet in missed:
            cluster = bet.cluster or bet.provider_id
            cluster_missed[cluster] = cluster_missed.get(cluster, 0) + bet.stake
            cluster_missed_ev[cluster] = cluster_missed_ev.get(cluster, 0) + bet.expected_profit
            cluster_missed_count[cluster] = cluster_missed_count.get(cluster, 0) + 1

        # Also check funded providers with shortfall
        for pid, pb in provider_balances.items():
            if pb.missed_bets > 0:
                cluster = pb.cluster or pid
                if cluster not in cluster_missed:
                    cluster_missed[cluster] = pb.missed_ev  # approximate
                    cluster_missed_ev[cluster] = pb.missed_ev
                    cluster_missed_count[cluster] = pb.missed_bets

        recommendations = []
        for cluster, needed_stake in sorted(cluster_missed.items(), key=lambda x: -x[1]):
            recommendations.append({
                "cluster": cluster,
                "deposit_amount": round(needed_stake, -1),  # Round to nearest 10
                "missed_bets": cluster_missed_count.get(cluster, 0),
                "missed_ev": round(cluster_missed_ev.get(cluster, 0), 2),
            })

        return recommendations
