"""
BatchBuilder service — collects all +EV opportunities, deduplicates across cluster
siblings, ranks by tier (sharp first) then expected profit, allocates balance, and
returns a ready-to-fire batch.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..constants import PLATFORM_GROUPS, PLATFORM_MAP
from ..bankroll.stake_calculator import (
    calculate_stake, dynamic_min_stake,
    OPTIMAL_MAX_KELLY, OPTIMAL_SINGLE_BET_CAP,
)
from ..repositories.opportunity_repo import OpportunityRepo
from ..repositories.profile_repo import ProfileRepo
from ..services.play_service import derive_lifecycle

logger = logging.getLogger(__name__)

# Tier priority: higher is ranked first
TIER_PRIORITY = {"polymarket": 2, "pinnacle": 1, "soft": 0}
SHARP_PROVIDERS = frozenset({"pinnacle", "polymarket"})

# Priority tier boundaries for soft bet allocation
# Edge buckets (descending): 10%+, 5-10%, 2-5%
# TTK buckets (ascending): 0-12h, 12-24h, 24-48h
# Priority = edge_tier_index * 3 + ttk_tier_index + 1  (1 = best, 9 = worst)
EDGE_THRESHOLDS = [10.0, 5.0, 2.0]   # edge_pct cutoffs (descending)
TTK_THRESHOLDS = [12.0, 24.0, 48.0]  # hours cutoffs (ascending)
MAX_TTK_HOURS = 48.0


def compute_priority(edge_pct: float, ttk_hours: float | None) -> int:
    """
    Compute priority tier 1-9 from edge % and time-to-kickoff hours.
    Lower number = higher priority. Returns 99 if outside all tiers.
    """
    if ttk_hours is None or ttk_hours > MAX_TTK_HOURS:
        return 99

    # Edge bucket index: 0 = 10%+, 1 = 5-10%, 2 = 2-5%
    edge_idx = -1
    for i, threshold in enumerate(EDGE_THRESHOLDS):
        if edge_pct >= threshold:
            edge_idx = i
            break
    if edge_idx == -1:
        return 99  # Below min edge

    # TTK bucket index: 0 = 0-12h, 1 = 12-24h, 2 = 24-48h
    ttk_idx = -1
    for i, threshold in enumerate(TTK_THRESHOLDS):
        if ttk_hours <= threshold:
            ttk_idx = i
            break
    if ttk_idx == -1:
        return 99

    return edge_idx * len(TTK_THRESHOLDS) + ttk_idx + 1


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

    # Funding status
    funded: bool = True               # False = needs deposit to play
    skip_reason: Optional[str] = None

    # Priority tier (1-9, lower = better; 99 = outside all tiers)
    priority: int = 99


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

    # Wagering info
    wagering_total: float = 0.0
    wagering_remaining: float = 0.0
    days_remaining: int | None = None

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

    def build(self, profile_id: int, exclude: list[str] | None = None) -> dict:
        """
        Main entry point. Returns a dict with:
          - batch: list of bet dicts (ranked, allocated)
          - summary: aggregate stats
          - balance_status: per-provider status
          - missed_opportunities: summary of bets that couldn't be placed
          - wagering_projections: projected bonus wagering progress
        """
        profile = self.profile_repo.get_active()
        total_bankroll = self.profile_repo.get_total_bankroll(profile_id)

        provider_balances = self._load_provider_balances(profile_id)

        candidates = self._collect_candidates(
            total_bankroll, provider_balances, profile
        )

        # Filter out excluded bets (from UI "remove" action)
        if exclude:
            exclude_set = set(exclude)
            candidates = [
                c for c in candidates
                if f"{c.provider_id}:{c.event_id}:{c.market}:{c.outcome}:{c.point}" not in exclude_set
            ]

        # Sort ALL candidates: sharp first, then by expected_profit desc
        # Don't deduplicate before allocation — dedup happens during allocation
        # so bets distribute across siblings by remaining balance
        ranked = sorted(
            candidates,
            key=lambda b: (-TIER_PRIORITY.get(b.tier, 0), b.priority, -b.expected_profit),
        )

        # Split sharp and soft for different allocation strategies
        sharp_ranked = [b for b in ranked if b.tier in ("polymarket", "pinnacle")]
        soft_ranked = [b for b in ranked if b.tier == "soft"]

        # Sharp: direct allocation (existing dedup logic)
        sharp_batch, sharp_missed = self._allocate_with_dedup(sharp_ranked, provider_balances)

        # Soft: round-robin allocation
        soft_batch, soft_missed = self._allocate_with_round_robin(soft_ranked, provider_balances)

        # Merge: all funded bets + unfunded (missed) bets in one list
        # Funded bets keep funded=True, missed bets get funded=False
        for bet in sharp_missed + soft_missed:
            bet.funded = False
        batch = sharp_batch + soft_batch + sharp_missed + soft_missed
        missed = sharp_missed + soft_missed  # Keep reference for capital plan

        for i, bet in enumerate(batch):
            bet.rank = i + 1

        # Count opportunity volume per cluster (from ALL candidates, not just batch)
        cluster_opp_stats = self._compute_cluster_opp_stats(candidates)

        # Check for unfunded sharp providers that have opportunities in the DB
        unfunded_sharp = self._check_unfunded_sharp_opps(
            provider_balances, total_bankroll, profile
        )

        # Get wagering history for capital plan
        wager_info = self.profile_repo.get_avg_daily_wager(profile_id)
        avg_daily_wager = wager_info.get("avg_daily_wager", 0)
        has_wager_history = wager_info.get("has_history", False)

        capital_plan = self._build_capital_plan_v3(
            provider_balances=provider_balances,
            missed=missed,
            total_bankroll=total_bankroll,
            cluster_opp_stats=cluster_opp_stats,
            avg_daily_wager=avg_daily_wager,
            has_wager_history=has_wager_history,
            unfunded_sharp=unfunded_sharp,
        )

        # Get exchange rate for USDC → SEK conversion
        from ..config import get_exchange_rate
        usdc_rate = get_exchange_rate("polymarket")

        return {
            "batch": [self._bet_to_dict(b) for b in batch],
            "summary": self._build_summary(batch),
            "balance_status": self._build_balance_status(provider_balances, missed),
            "missed_opportunities": self._build_missed_summary(missed),
            "deposit_recommendations": [],
            "withdrawal_recommendations": [],
            "capital_plan": {**capital_plan, "usdc_rate": usdc_rate},
            "wagering_projections": self._compute_wagering_projections(batch, provider_balances),
        }

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _check_unfunded_sharp_opps(
        self,
        provider_balances: dict[str, ProviderBalance],
        total_bankroll: float,
        profile,
    ) -> list[dict]:
        """
        Check for unfunded sharp providers (Pinnacle, Polymarket) that have
        active opportunities in the DB. Returns summary info for capital plan.
        """
        from ..config import get_exchange_rate

        result = []
        min_edge_pct = getattr(profile, "min_edge_pct", 2.0) or 2.0

        for sharp_pid in ("pinnacle", "polymarket"):
            if sharp_pid in provider_balances:
                continue  # Already funded, handled normally

            is_usdc = sharp_pid == "polymarket"
            exchange_rate = get_exchange_rate(sharp_pid) if is_usdc else 1.0

            # Count opportunities for this sharp provider
            opp_count = 0
            total_stake = 0.0
            total_ev = 0.0
            total_edge = 0.0

            for opp_type in ("value", "reverse_value"):
                for opp, event in self.opp_repo.find_active(type=opp_type):
                    if opp.provider1_id != sharp_pid:
                        continue
                    edge_raw = (opp.edge_pct or 0.0) / 100.0
                    if edge_raw < min_edge_pct / 100.0:
                        continue
                    odds = opp.odds1 or 0.0
                    stake_result = calculate_stake(
                        bankroll_total=total_bankroll,
                        edge_raw=edge_raw,
                        odds=odds,
                        min_edge=min_edge_pct / 100.0,
                        min_odds=0,
                        min_stake=dynamic_min_stake(total_bankroll),
                    )
                    if stake_result.skip_reason or stake_result.stake <= 0:
                        continue
                    # Convert SEK stake to provider currency (USDC for Polymarket)
                    stake_in_currency = stake_result.stake / exchange_rate if is_usdc else stake_result.stake
                    opp_count += 1
                    total_stake += stake_in_currency
                    total_ev += stake_in_currency * edge_raw
                    total_edge += opp.edge_pct or 0.0

            if opp_count > 0:
                result.append({
                    "provider_id": sharp_pid,
                    "opp_count": opp_count,
                    "total_stake": round(total_stake, 2),
                    "total_ev": round(total_ev, 2),
                    "avg_edge": round(total_edge / opp_count, 1),
                    "currency": "USDC" if is_usdc else "SEK",
                })

        return result

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
                wagering_total=(bonus_info.get("wagering_requirement", 0) or 0),
                wagering_remaining=max(0, (bonus_info.get("wagering_requirement", 0) or 0) - (bonus_info.get("wagered_amount", 0) or 0)),
                days_remaining=bonus_info.get("days_remaining"),
            )

        return result

    def _collect_candidates(
        self,
        total_bankroll: float,
        provider_balances: dict[str, ProviderBalance],
        profile,
    ) -> list[BatchBet]:
        """Query all opportunity types and compute stakes."""

        # Stake sizing: kelly + cap from sim-optimal constants, only min_edge from profile
        min_edge_pct = getattr(profile, "min_edge_pct", 2.0) or 2.0

        single_bet_cap_pct = OPTIMAL_SINGLE_BET_CAP
        min_edge = min_edge_pct / 100.0
        min_stake = dynamic_min_stake(total_bankroll)

        candidates: list[BatchBet] = []

        # Collect value opps (soft providers + polymarket stored as type="value")
        for opp, event in self.opp_repo.find_active(type="value"):
            bet = self._make_candidate(
                opp, event, "value",
                total_bankroll, provider_balances,
                single_bet_cap_pct, min_edge, min_stake,
            )
            if bet is not None:
                candidates.append(bet)

        # Collect reverse_value opps (Pinnacle vs consensus)
        for opp, event in self.opp_repo.find_active(type="reverse_value"):
            bet = self._make_candidate(
                opp, event, "reverse_value",
                total_bankroll, provider_balances,
                single_bet_cap_pct, min_edge, min_stake,
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
        single_bet_cap_pct: float,
        min_edge: float,
        min_stake: float,
    ) -> Optional[BatchBet]:
        """Convert an Opportunity+Event into a BatchBet candidate, or None to skip."""

        # Enforce 48h TTK cap for soft providers
        if opp.provider1_id not in SHARP_PROVIDERS:
            if event.start_time:
                now = datetime.now(timezone.utc)
                st = event.start_time if event.start_time.tzinfo else event.start_time.replace(tzinfo=timezone.utc)
                ttk_hours = (st - now).total_seconds() / 3600
                if ttk_hours > MAX_TTK_HOURS:
                    return None
                if ttk_hours <= 0:
                    return None
            else:
                ttk_hours = None
        else:
            ttk_hours = None

        provider_id = opp.provider1_id
        pb = provider_balances.get(provider_id)

        # If this provider has no balance, try to reroute to a funded sibling
        unfunded = False
        if pb is None or pb.lifecycle in ("dormant", "available"):
            cluster = _provider_to_cluster(provider_id)
            # Find a funded sibling in the same cluster
            funded_sibling = None
            for pid, spb in provider_balances.items():
                if spb.cluster == cluster and spb.lifecycle not in ("dormant", "available") and spb.remaining > 0:
                    if funded_sibling is None or spb.remaining > provider_balances[funded_sibling].remaining:
                        funded_sibling = pid
            if funded_sibling:
                provider_id = funded_sibling
                pb = provider_balances[funded_sibling]
            else:
                unfunded = True  # Keep as candidate — will be missed due to no balance

        odds = opp.odds1 or 0.0
        fair_odds = opp.odds2 or 0.0
        edge_raw = (opp.edge_pct or 0.0) / 100.0

        if unfunded:
            # Unfunded: only skip if edge below threshold — funding issues
            # are resolved in the capital plan step
            if edge_raw < min_edge:
                return None
            result = calculate_stake(
                bankroll_total=total_bankroll,
                edge_raw=edge_raw,
                odds=odds,
                single_bet_cap_pct=single_bet_cap_pct,
                min_edge=min_edge,
                min_odds=0.0,
                min_stake=0.0,
                max_kelly=OPTIMAL_MAX_KELLY,
            )
            stake = result.stake if result.stake > 0 else min_stake
            is_bonus = False
            bonus_type = None
            bet_min_odds = 0.0
        else:
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
                    max_kelly=OPTIMAL_MAX_KELLY,
                )
                if result.skip_reason:
                    return None
                stake = result.stake
                is_bonus = False
                bonus_type = None

        if stake <= 0:
            return None

        # Convert SEK stake to USDC for Polymarket
        if provider_id == "polymarket":
            from ..config import get_exchange_rate
            exchange_rate = get_exchange_rate("polymarket")
            if exchange_rate > 0:
                stake = stake / exchange_rate

        expected_profit = stake * edge_raw

        if provider_id == "polymarket":
            tier = "polymarket"
        elif provider_id == "pinnacle":
            tier = "pinnacle"
        else:
            tier = "soft"
        cluster = pb.cluster if pb else _provider_to_cluster(provider_id)

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
            lifecycle=pb.lifecycle if pb else "available",
            cluster=cluster,
            funded=not unfunded,
            priority=compute_priority(opp.edge_pct or 0.0, ttk_hours) if provider_id not in SHARP_PROVIDERS else 0,
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

    @staticmethod
    def _clone_bet_to_provider(
        bet: BatchBet, new_provider_id: str, pb: ProviderBalance,
    ) -> BatchBet:
        """Clone a bet to a different provider in the same cluster (same platform = same odds)."""
        return BatchBet(
            rank=0,
            tier=bet.tier,
            provider_id=new_provider_id,
            event_id=bet.event_id,
            market=bet.market,
            outcome=bet.outcome,
            point=bet.point,
            odds=bet.odds,
            fair_odds=bet.fair_odds,
            edge_pct=bet.edge_pct,
            stake=bet.stake,
            expected_profit=bet.expected_profit,
            is_bonus=bet.is_bonus,
            bonus_type=bet.bonus_type,
            display_home=bet.display_home,
            display_away=bet.display_away,
            sport=bet.sport,
            league=bet.league,
            start_time=bet.start_time,
            lifecycle=pb.lifecycle,
            cluster=bet.cluster,
        )

    # Every BETS_PER_PROVIDER bets in a cluster adds another sibling.
    # 1-10 → 1 provider, 11-20 → 2, 21-30 → 3, etc.
    BETS_PER_PROVIDER = 10

    @staticmethod
    def _allocate_with_round_robin(
        ranked: list[BatchBet],
        provider_balances: dict[str, ProviderBalance],
    ) -> tuple[list[BatchBet], list[BatchBet]]:
        """
        Allocation for soft tier with scaled provider spreading.

        Number of siblings used = ceil(bets / BETS_PER_PROVIDER), capped at
        available funded siblings. Below BETS_PER_PROVIDER, single provider.
        """
        import math

        batch: list[BatchBet] = []
        missed: list[BatchBet] = []

        # Deduplicate: keep best candidate per (cluster, event, market, outcome, point)
        best_per_key: dict[tuple, BatchBet] = {}
        for bet in ranked:
            dedup_key = (bet.cluster, bet.event_id, bet.market, bet.outcome, bet.point)
            if dedup_key not in best_per_key:
                best_per_key[dedup_key] = bet

        # Count unique bets per cluster
        cluster_bet_count: dict[str, int] = {}
        for key in best_per_key:
            cluster = key[0]
            cluster_bet_count[cluster] = cluster_bet_count.get(cluster, 0) + 1

        # Build cluster siblings (funded providers only, sorted by balance desc)
        cluster_siblings: dict[str, list[str]] = {}
        for pid, pb in provider_balances.items():
            if pb.lifecycle in ("dormant", "available"):
                continue
            cluster = pb.cluster or pid
            if cluster not in cluster_siblings:
                cluster_siblings[cluster] = []
            cluster_siblings[cluster].append(pid)
        for cluster in cluster_siblings:
            cluster_siblings[cluster].sort(key=lambda pid: -provider_balances[pid].remaining)

        # For each cluster, determine how many siblings to use and build rotation
        cluster_rotation: dict[str, itertools.cycle] = {}
        cluster_active_siblings: dict[str, list[str]] = {}
        for cluster, siblings in cluster_siblings.items():
            if not siblings:
                continue
            bet_count = cluster_bet_count.get(cluster, 0)
            needed = math.ceil(bet_count / BatchBuilder.BETS_PER_PROVIDER)
            active = siblings[:min(needed, len(siblings))]
            cluster_active_siblings[cluster] = active
            if len(active) > 1:
                cluster_rotation[cluster] = itertools.cycle(active)

        # Walk opportunities in ranked order (by expected_profit descending)
        sorted_keys = sorted(
            best_per_key.keys(),
            key=lambda k: (best_per_key[k].priority, -best_per_key[k].expected_profit),
        )

        for dedup_key in sorted_keys:
            template_bet = best_per_key[dedup_key]
            cluster = dedup_key[0]
            rotation = cluster_rotation.get(cluster)
            active = cluster_active_siblings.get(cluster, [])

            if not active:
                template_bet.skip_reason = "no funded sibling in cluster"
                missed.append(template_bet)
                continue

            assigned = False

            if rotation:
                # Multiple siblings: round-robin across active set
                for _ in range(len(active)):
                    next_pid = next(rotation)
                    pb = provider_balances[next_pid]

                    if pb.lifecycle in ("wagering", "deposited") and pb.min_odds > 0:
                        if template_bet.odds < pb.min_odds:
                            continue

                    if template_bet.is_bonus and template_bet.bonus_type == "freebet":
                        placed = BatchBuilder._clone_bet_to_provider(template_bet, next_pid, pb)
                        batch.append(placed)
                        assigned = True
                        break

                    if pb.remaining >= template_bet.stake:
                        placed = BatchBuilder._clone_bet_to_provider(template_bet, next_pid, pb)
                        pb.allocated += placed.stake
                        batch.append(placed)
                        assigned = True
                        break
            else:
                # Single provider: use best-funded (first in list)
                for pid in active:
                    pb = provider_balances[pid]

                    if pb.lifecycle in ("wagering", "deposited") and pb.min_odds > 0:
                        if template_bet.odds < pb.min_odds:
                            continue

                    if template_bet.is_bonus and template_bet.bonus_type == "freebet":
                        placed = BatchBuilder._clone_bet_to_provider(template_bet, pid, pb)
                        batch.append(placed)
                        assigned = True
                        break

                    if pb.remaining >= template_bet.stake:
                        placed = BatchBuilder._clone_bet_to_provider(template_bet, pid, pb)
                        pb.allocated += placed.stake
                        batch.append(placed)
                        assigned = True
                        break

            if not assigned:
                template_bet.skip_reason = f"insufficient balance in cluster {cluster}"
                # Track missed stats on the best-funded active sibling (first in list)
                # so capital plan targets the right provider for deposits
                target_pid = active[0] if active else template_bet.provider_id
                target_pb = provider_balances.get(target_pid)
                if target_pb:
                    target_pb.missed_bets += 1
                    target_pb.missed_ev += template_bet.expected_profit
                missed.append(template_bet)

        return batch, missed

    def _build_summary(self, batch: list[BatchBet]) -> dict:
        polymarket_bets = [b for b in batch if b.tier == "polymarket"]
        pinnacle_bets = [b for b in batch if b.tier == "pinnacle"]
        soft_bets = [b for b in batch if b.tier == "soft"]

        # Priority tier breakdown for soft bets (funded only)
        funded_soft = [b for b in soft_bets if b.funded]
        tier_breakdown = {}
        for b in funded_soft:
            p = b.priority
            if p not in tier_breakdown:
                tier_breakdown[p] = {"count": 0, "stake": 0.0, "ev": 0.0}
            tier_breakdown[p]["count"] += 1
            tier_breakdown[p]["stake"] += b.stake
            tier_breakdown[p]["ev"] += b.expected_profit
        # Round values
        for v in tier_breakdown.values():
            v["stake"] = round(v["stake"], 2)
            v["ev"] = round(v["ev"], 2)

        return {
            "total_bets": len(batch),
            "total_stake": round(sum(b.stake for b in batch), 2),
            "total_expected_profit": round(sum(b.expected_profit for b in batch), 2),
            "polymarket_bets": len(polymarket_bets),
            "polymarket_ev": round(sum(b.expected_profit for b in polymarket_bets), 2),
            "pinnacle_bets": len(pinnacle_bets),
            "pinnacle_ev": round(sum(b.expected_profit for b in pinnacle_bets), 2),
            "soft_bets": len(soft_bets),
            "soft_ev": round(sum(b.expected_profit for b in soft_bets), 2),
            "tier_breakdown": tier_breakdown,
        }

    def _compute_wagering_projections(
        self,
        batch: list[BatchBet],
        provider_balances: dict[str, ProviderBalance],
    ) -> list[dict]:
        """Compute projected wagering progress for providers with active bonuses."""
        provider_stakes: dict[str, float] = {}
        for bet in batch:
            provider_stakes[bet.provider_id] = provider_stakes.get(bet.provider_id, 0) + bet.stake

        projections = []
        for pid, pb in provider_balances.items():
            if pb.wagering_remaining <= 0:
                continue
            batch_stake = provider_stakes.get(pid, 0)
            projected_remaining = max(0, pb.wagering_remaining - batch_stake)
            projections.append({
                "provider_id": pid,
                "cluster": pb.cluster,
                "wagering_total": round(pb.wagering_total, 2),
                "wagering_remaining": round(pb.wagering_remaining, 2),
                "batch_stake": round(batch_stake, 2),
                "projected_remaining": round(projected_remaining, 2),
                "days_remaining": pb.days_remaining,
            })
        return projections

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
            "funded": bet.funded,
            "priority": bet.priority,
            "skip_reason": bet.skip_reason,
        }

    @staticmethod
    def _build_capital_plan_v3(
        provider_balances: dict[str, ProviderBalance],
        missed: list[BatchBet],
        total_bankroll: float,
        cluster_opp_stats: dict[str, dict],
        avg_daily_wager: float = 0.0,
        has_wager_history: bool = False,
        unfunded_sharp: list[dict] | None = None,
    ) -> dict:
        """
        Build capital plan: deposit where balance is short, withdraw where idle.

        This is a checklist — the user does deposits/withdrawals in the mirror
        browser, which auto-syncs balances. Confirm just rebuilds the batch.

        Priority 1 — DEPOSIT (sharp): Polymarket/Pinnacle shortfalls
        Priority 2 — DEPOSIT (bonus): Soft providers with active wagering
        Priority 3 — DEPOSIT (soft): Soft providers with missed bets
        Priority 4 — WITHDRAW: Idle providers with no missed bets

        Returns {"total_deployed": float, "withdrawable": float, "actions": list[dict]}
        """
        import math

        actions: list[dict] = []

        # Aggregate missed bets by provider
        missed_by_provider: dict[str, list[BatchBet]] = {}
        for bet in missed:
            missed_by_provider.setdefault(bet.provider_id, []).append(bet)

        # Also consider providers with missed_bets set on their ProviderBalance
        providers_with_shortfall: set[str] = set(missed_by_provider.keys())
        for pid, pb in provider_balances.items():
            if pb.missed_bets > 0:
                providers_with_shortfall.add(pid)

        # --- Priority 1: Sharp deposits ---
        sharp_already_handled: set[str] = set()
        for pid in sorted(providers_with_shortfall):
            if pid not in SHARP_PROVIDERS:
                continue
            pb = provider_balances.get(pid)
            m_bets = missed_by_provider.get(pid, [])
            missed_ev = sum(b.expected_profit for b in m_bets) if m_bets else (pb.missed_ev if pb else 0)
            missed_stake = sum(b.stake for b in m_bets) if m_bets else 0
            missed_count = len(m_bets) if m_bets else (pb.missed_bets if pb else 0)
            if missed_count == 0 and (pb is None or pb.missed_bets == 0):
                continue

            cluster = pb.cluster if pb else pid
            stats = cluster_opp_stats.get(cluster, {})
            currency = "USDC" if pid == "polymarket" else "SEK"

            current_bal = pb.initial_balance if pb else 0
            deposit_amount = round(max(missed_stake, 0), 2)
            actions.append({
                "type": "deposit",
                "provider_id": pid,
                "cluster": cluster,
                "amount": deposit_amount,
                "target_balance": round(current_bal + deposit_amount, 2),
                "unlocks": missed_count,
                "avg_edge": stats.get("avg_edge", 0),
                "expected_ev": round(missed_ev, 2),
                "currency": currency,
                "priority": 1,
                "priority_label": "sharp_deposit",
            })
            sharp_already_handled.add(pid)

        # Unfunded sharp providers with opportunities in DB
        for info in (unfunded_sharp or []):
            pid = info["provider_id"]
            if pid in sharp_already_handled:
                continue
            deposit_amount = round(info["total_stake"], 2)
            actions.append({
                "type": "deposit",
                "provider_id": pid,
                "cluster": pid,
                "amount": deposit_amount,
                "target_balance": deposit_amount,
                "unlocks": info["opp_count"],
                "avg_edge": info["avg_edge"],
                "expected_ev": round(info["total_ev"], 2),
                "currency": info["currency"],
                "priority": 1,
                "priority_label": "sharp_deposit",
            })

        # --- Priority 2/3: Soft deposits ---
        # Aggregate missed bets per cluster (siblings share opps).
        # Avoid double-counting: round-robin tracks the same missed bet in both
        # missed_by_provider[canonical] and active[0].missed_bets.  Use the
        # missed_by_provider list as primary source; only fall back to
        # pb.missed_bets when the cluster has no missed_by_provider data.
        cluster_missed: dict[str, dict] = {}
        cluster_has_bet_list: set[str] = set()  # clusters with real bet objects

        for pid in sorted(providers_with_shortfall):
            if pid in SHARP_PROVIDERS:
                continue
            pb = provider_balances.get(pid)
            if pb is None:
                # Provider has missed bets but no balance — find a funded sibling
                # as source_pid so the cluster still gets a deposit recommendation.
                cluster = _provider_to_cluster(pid)
                m_bets = missed_by_provider.get(pid, [])
                if not m_bets:
                    continue
                missed_count = len(m_bets)
                missed_stake = sum(b.stake for b in m_bets)
                missed_ev = sum(b.expected_profit for b in m_bets)
                cluster_has_bet_list.add(cluster)
                funded_source = next(
                    (spid for spid, spb in provider_balances.items()
                     if (spb.cluster or spid) == cluster),
                    pid,
                )
                if cluster not in cluster_missed:
                    cluster_missed[cluster] = {"count": 0, "stake": 0, "ev": 0, "source_pid": funded_source}
                cluster_missed[cluster]["count"] += missed_count
                cluster_missed[cluster]["stake"] += missed_stake
                cluster_missed[cluster]["ev"] += missed_ev
                continue

            m_bets = missed_by_provider.get(pid, [])
            cluster = pb.cluster or pid

            if m_bets:
                missed_count = len(m_bets)
                missed_stake = sum(b.stake for b in m_bets)
                missed_ev = sum(b.expected_profit for b in m_bets)
                cluster_has_bet_list.add(cluster)
            elif cluster in cluster_has_bet_list:
                # Already counted via missed_by_provider for this cluster
                continue
            else:
                missed_count = pb.missed_bets
                missed_stake = 0
                missed_ev = pb.missed_ev
            if missed_count == 0:
                continue

            if cluster not in cluster_missed:
                cluster_missed[cluster] = {"count": 0, "stake": 0, "ev": 0, "source_pid": pid}
            cluster_missed[cluster]["count"] += missed_count
            cluster_missed[cluster]["stake"] += missed_stake
            cluster_missed[cluster]["ev"] += missed_ev

        for cluster, info in cluster_missed.items():
            missed_count = info["count"]
            missed_stake = info["stake"]
            missed_ev = info["ev"]
            source_pid = info["source_pid"]
            stats = cluster_opp_stats.get(cluster, {})

            funded_in_cluster = [
                pid for pid, pb in provider_balances.items()
                if (pb.cluster or pid) == cluster and pb.lifecycle not in ("dormant", "available")
            ]

            # Skip if cluster already has enough capital to cover nearly all bets.
            # The missed bet is marginal — a rebuild after other deposits will likely
            # re-order allocation so it fits within existing balance.
            cluster_balance = sum(
                pb.initial_balance for pb in provider_balances.values()
                if (pb.cluster or pb.provider_id) == cluster
            )
            cluster_placed_stake = sum(
                pb.allocated for pb in provider_balances.values()
                if (pb.cluster or pb.provider_id) == cluster
            )
            total_needed = cluster_placed_stake + missed_stake
            if total_needed > 0 and cluster_balance >= total_needed * 0.95:
                continue  # existing balance covers ≥95% — no deposit needed

            providers_needed = max(1, math.ceil(missed_count / BatchBuilder.BETS_PER_PROVIDER))

            all_siblings = PLATFORM_GROUPS.get(cluster, {}).get("members", [cluster])
            funded_pids = {pid for pid, pb in provider_balances.items() if (pb.cluster or pid) == cluster}
            unfunded_siblings = [s for s in all_siblings if s not in funded_pids]

            # Bonus wagering → priority 2, otherwise → priority 3
            funded_pb = provider_balances.get(source_pid)
            has_bonus = funded_pb and funded_pb.wagering_remaining > 0
            if has_bonus:
                effective_wager = avg_daily_wager if avg_daily_wager > 0 else 1000
                days_needed = funded_pb.wagering_remaining / effective_wager
                if funded_pb.days_remaining is not None and days_needed > funded_pb.days_remaining:
                    continue  # Infeasible wagering
                priority = 2
                label = "bonus_deposit"
            else:
                priority = 3
                label = "soft_deposit"

            # Use unique opps for display (missed_count can inflate via multi-market)
            display_unlocks = min(missed_count, stats.get("unique_opps", missed_count))

            if providers_needed <= 1:
                source_bal = provider_balances.get(source_pid)
                current_bal = source_bal.initial_balance if source_bal else 0
                dep_amount = round(missed_stake, 2)
                actions.append({
                    "type": "deposit",
                    "provider_id": source_pid,
                    "cluster": cluster,
                    "amount": dep_amount,
                    "target_balance": round(current_bal + dep_amount, 2),
                    "unlocks": display_unlocks,
                    "avg_edge": stats.get("avg_edge", 0),
                    "expected_ev": round(missed_ev, 2),
                    "currency": "SEK",
                    "priority": priority,
                    "priority_label": label,
                })
            else:
                # Spread across siblings: funded + unfunded
                targets = funded_in_cluster[:] + unfunded_siblings
                targets = targets[:providers_needed]
                if not targets:
                    targets = [source_pid]

                # Total stake needed across the cluster (placed + missed)
                total_cluster_stake = cluster_placed_stake + missed_stake
                per_provider_need = total_cluster_stake / len(targets)
                per_provider_ev = round(missed_ev / len(targets), 2)
                per_provider_unlocks = math.ceil(display_unlocks / len(targets))

                for target_pid in targets:
                    is_new = target_pid not in funded_pids
                    target_pb = provider_balances.get(target_pid)
                    current_bal = target_pb.initial_balance if target_pb else 0
                    shortfall = per_provider_need - current_bal
                    if shortfall < 10:
                        continue  # Already has enough balance for its share
                    actions.append({
                        "type": "deposit",
                        "provider_id": target_pid,
                        "cluster": cluster,
                        "amount": round(shortfall, 2),
                        "target_balance": round(per_provider_need, 2),
                        "unlocks": per_provider_unlocks,
                        "avg_edge": stats.get("avg_edge", 0),
                        "expected_ev": per_provider_ev,
                        "currency": "SEK",
                        "priority": priority,
                        "priority_label": f"{label}_new" if is_new else label,
                    })

        # --- Priority 2 (continued): Every wagering provider needs funding ---
        # Ensure any provider with active bonus wagering and low balance gets
        # a deposit recommendation, even if the cluster_missed path missed it.
        # Cluster-aware: aggregate per-cluster first, then spread across siblings.
        already_recommended = {a["provider_id"] for a in actions if a["type"] == "deposit"}
        # Also track which clusters already have recommendations
        already_recommended_clusters: set[str] = set()
        for a in actions:
            if a["type"] == "deposit":
                apb = provider_balances.get(a["provider_id"])
                if apb:
                    already_recommended_clusters.add(apb.cluster or a["provider_id"])

        bonus_clusters: dict[str, list[str]] = {}  # cluster -> [provider_ids needing funding]
        for pid, pb in provider_balances.items():
            if pid in SHARP_PROVIDERS or pid in already_recommended:
                continue
            if pb.wagering_remaining <= 0:
                continue
            effective_wager = avg_daily_wager if avg_daily_wager > 0 else 1000
            days_needed = pb.wagering_remaining / effective_wager
            if pb.days_remaining is not None and days_needed > pb.days_remaining:
                continue  # Infeasible
            # Needs deposit if balance can't cover even one session
            if pb.remaining > effective_wager:
                continue
            cluster = pb.cluster or pid
            if cluster in already_recommended_clusters:
                continue
            bonus_clusters.setdefault(cluster, []).append(pid)

        for cluster, pids in bonus_clusters.items():
            stats = cluster_opp_stats.get(cluster, {})
            unique_opps = stats.get("unique_opps", 0)
            total_cluster_stake = stats.get("total_stake", 0)
            all_siblings = PLATFORM_GROUPS.get(cluster, {}).get("members", [cluster])

            # Skip if existing balance already covers the bets
            cluster_balance = sum(
                pb.initial_balance for pb in provider_balances.values()
                if (pb.cluster or pb.provider_id) == cluster
            )
            if cluster_balance >= total_cluster_stake * 0.95:
                continue

            # How many siblings should we spread across?
            providers_needed = max(1, math.ceil(unique_opps / BatchBuilder.BETS_PER_PROVIDER))
            # Include current wagering providers + recruit unfunded siblings
            funded_pids_in_cluster = {p for p, pb in provider_balances.items()
                                      if (pb.cluster or p) == cluster}
            unfunded_siblings = [s for s in all_siblings if s not in funded_pids_in_cluster]
            targets = pids[:] + [s for s in unfunded_siblings if s not in pids]
            targets = targets[:providers_needed]
            if not targets:
                targets = pids[:1]

            per_provider_opps = math.ceil(unique_opps / len(targets))
            effective_wager = avg_daily_wager if avg_daily_wager > 0 else 1000
            per_provider_ev = round(stats.get("total_ev", 0) / len(targets), 2)

            for target_pid in targets:
                tpb = provider_balances.get(target_pid)
                remaining = tpb.remaining if tpb else 0
                current_bal = tpb.initial_balance if tpb else 0
                amount = round(max(effective_wager - remaining, 100), -2)
                is_new = target_pid not in funded_pids_in_cluster
                actions.append({
                    "type": "deposit",
                    "provider_id": target_pid,
                    "cluster": cluster,
                    "amount": amount,
                    "target_balance": round(current_bal + amount, 2),
                    "unlocks": per_provider_opps,
                    "avg_edge": stats.get("avg_edge", 0),
                    "expected_ev": per_provider_ev,
                    "currency": "SEK",
                    "priority": 2,
                    "priority_label": f"bonus_deposit_new" if is_new else "bonus_deposit",
                })

        # --- Priority 4: Withdraw idle balance ---
        for pid, pb in provider_balances.items():
            if pid in SHARP_PROVIDERS:
                continue
            if pid in providers_with_shortfall:
                continue
            if pb.lifecycle not in ("dormant", "playing", "limited"):
                continue
            if pb.wagering_remaining > 0:
                continue
            if pb.remaining <= 0:
                continue

            actions.append({
                "type": "withdraw",
                "provider_id": pid,
                "cluster": pb.cluster or pid,
                "amount": round(pb.remaining, 2),
                "target_balance": 0,
                "unlocks": 0,
                "avg_edge": 0,
                "expected_ev": 0,
                "currency": "SEK",
                "priority": 4,
                "priority_label": "withdraw_excess",
            })

        # Sort by priority, then by expected_ev descending
        actions.sort(key=lambda a: (a["priority"], -a.get("expected_ev", 0)))

        deployed = sum(pb.initial_balance for pb in provider_balances.values())
        withdrawable = sum(a["amount"] for a in actions if a["type"] == "withdraw")

        return {
            "total_deployed": round(deployed, 2),
            "withdrawable": round(withdrawable, 2),
            "actions": actions,
        }

    def _compute_cluster_opp_stats(self, candidates: list[BatchBet]) -> dict[str, dict]:
        """
        Compute opportunity volume stats per cluster from candidates.
        Returns {cluster: {unique_opps, total_ev, avg_edge, avg_stake}}.
        """
        cluster_data: dict[str, dict] = {}
        cluster_keys: dict[str, set] = {}  # for dedup counting

        for c in candidates:
            cluster = c.cluster or c.provider_id
            if cluster not in cluster_data:
                cluster_data[cluster] = {"edges": [], "stakes": [], "evs": []}
                cluster_keys[cluster] = set()

            opp_key = (c.event_id, c.market, c.outcome, c.point)
            cluster_keys[cluster].add(opp_key)
            cluster_data[cluster]["edges"].append(c.edge_pct)
            cluster_data[cluster]["stakes"].append(c.stake)
            cluster_data[cluster]["evs"].append(c.expected_profit)

        result = {}
        for cluster, data in cluster_data.items():
            unique = len(cluster_keys[cluster])
            total_ev = sum(data["evs"])
            avg_edge = sum(data["edges"]) / len(data["edges"]) if data["edges"] else 0
            avg_stake = sum(data["stakes"]) / len(data["stakes"]) if data["stakes"] else 0
            result[cluster] = {
                "unique_opps": unique,
                "total_ev": round(total_ev, 2),
                "total_stake": round(sum(data["stakes"]), 2),
                "avg_edge": round(avg_edge, 1),
                "avg_stake": round(avg_stake, 0),
                "ev_per_session": round(total_ev, 2),  # assumes drain balance = 1 session
            }
        return result

