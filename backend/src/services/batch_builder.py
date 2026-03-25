"""
BatchBuilder service — collects all +EV opportunities, deduplicates across cluster
siblings, ranks by tier (sharp first) then expected profit, allocates balance, and
returns a ready-to-fire batch.
"""

from __future__ import annotations

import itertools
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
TIER_PRIORITY = {"polymarket": 2, "pinnacle": 1, "soft": 0}
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

    # Wagering info
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
            key=lambda b: (-TIER_PRIORITY.get(b.tier, 0), -b.expected_profit),
        )

        # Split sharp and soft for different allocation strategies
        sharp_ranked = [b for b in ranked if b.tier in ("polymarket", "pinnacle")]
        soft_ranked = [b for b in ranked if b.tier == "soft"]

        # Sharp: direct allocation (existing dedup logic)
        sharp_batch, sharp_missed = self._allocate_with_dedup(sharp_ranked, provider_balances)

        # Soft: round-robin allocation
        soft_batch, soft_missed = self._allocate_with_round_robin(soft_ranked, provider_balances)

        batch = sharp_batch + soft_batch
        missed = sharp_missed + soft_missed

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
        kelly_fraction = getattr(profile, "kelly_fraction", 0.75) or 0.75
        max_stake_pct = getattr(profile, "max_stake_pct", 5.0) or 5.0
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
                        single_bet_cap_pct=max_stake_pct / 100.0,
                        min_edge=min_edge_pct / 100.0,
                        min_odds=0,
                        min_stake=dynamic_min_stake(total_bankroll),
                        max_kelly=kelly_fraction,
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

        # If this provider has no balance, try to reroute to a funded sibling
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
                return None  # No funded sibling in cluster

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

        if provider_id == "polymarket":
            tier = "polymarket"
        elif provider_id == "pinnacle":
            tier = "pinnacle"
        else:
            tier = "soft"
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
            key=lambda k: -best_per_key[k].expected_profit,
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
                pb = provider_balances.get(template_bet.provider_id)
                if pb:
                    pb.missed_bets += 1
                    pb.missed_ev += template_bet.expected_profit
                missed.append(template_bet)

        return batch, missed

    def _build_summary(self, batch: list[BatchBet]) -> dict:
        polymarket_bets = [b for b in batch if b.tier == "polymarket"]
        pinnacle_bets = [b for b in batch if b.tier == "pinnacle"]
        soft_bets = [b for b in batch if b.tier == "soft"]
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
        Build a unified capital plan with 5 recommendation types, priority-ordered.

        Priority 1 — DEPOSIT (sharp): Polymarket/Pinnacle with missed bets or unfunded
        Priority 2 — DEPOSIT (bonus): Soft providers with active wagering requirement
        Priority 3 — DEPOSIT (soft shortfall): Soft providers with missed bets, no bonus
        Priority 4 — TRANSFER: Move funds from excess (dormant) to shortfall targets
        Priority 5 — WITHDRAW: Dormant providers with balance and no missed bets

        Returns {"total_deployed": float, "withdrawable": float, "actions": list[dict]}
        """
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

        # --- Priority 1: Sharp deposits (funded with missed bets) ---
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
            amount = max(missed_stake, 0)

            actions.append({
                "type": "deposit",
                "provider_id": pid,
                "amount": round(amount, 2),
                "unlocks": missed_count,
                "avg_edge": stats.get("avg_edge", 0),
                "expected_ev": round(missed_ev, 2),
                "currency": currency,
                "priority": 1,
                "priority_label": "sharp_deposit",
            })
            sharp_already_handled.add(pid)

        # --- Priority 1 (continued): Unfunded sharp providers with opportunities ---
        for info in (unfunded_sharp or []):
            pid = info["provider_id"]
            if pid in sharp_already_handled:
                continue
            actions.append({
                "type": "deposit",
                "provider_id": pid,
                "amount": round(info["total_stake"], 2),
                "unlocks": info["opp_count"],
                "avg_edge": info["avg_edge"],
                "expected_ev": round(info["total_ev"], 2),
                "currency": info["currency"],
                "priority": 1,
                "priority_label": "sharp_deposit",
            })

        # --- Priority 2: Bonus deposits (active wagering) ---
        # --- Priority 3: Soft shortfall deposits ---
        for pid in sorted(providers_with_shortfall):
            if pid in SHARP_PROVIDERS:
                continue
            pb = provider_balances.get(pid)
            if pb is None:
                continue

            m_bets = missed_by_provider.get(pid, [])
            missed_ev = sum(b.expected_profit for b in m_bets) if m_bets else pb.missed_ev
            missed_stake = sum(b.stake for b in m_bets) if m_bets else 0
            missed_count = len(m_bets) if m_bets else pb.missed_bets
            if missed_count == 0 and pb.missed_bets == 0:
                continue

            cluster = pb.cluster or pid
            stats = cluster_opp_stats.get(cluster, {})
            amount = max(missed_stake, 0)

            has_bonus = pb.wagering_remaining > 0

            if has_bonus:
                # Check wagering feasibility
                effective_wager = avg_daily_wager if avg_daily_wager > 0 else 1000
                days_needed = pb.wagering_remaining / effective_wager
                if pb.days_remaining is not None and days_needed > pb.days_remaining:
                    # Infeasible — skip this bonus deposit
                    continue

                actions.append({
                    "type": "deposit",
                    "provider_id": pid,
                    "amount": round(amount, 2),
                    "unlocks": missed_count,
                    "avg_edge": stats.get("avg_edge", 0),
                    "expected_ev": round(missed_ev, 2),
                    "currency": "SEK",
                    "priority": 2,
                    "priority_label": "bonus_deposit",
                })
            else:
                actions.append({
                    "type": "deposit",
                    "provider_id": pid,
                    "amount": round(amount, 2),
                    "unlocks": missed_count,
                    "avg_edge": stats.get("avg_edge", 0),
                    "expected_ev": round(missed_ev, 2),
                    "currency": "SEK",
                    "priority": 3,
                    "priority_label": "soft_deposit",
                })

        # --- Identify excess providers (balance remaining after batch allocation) ---
        # Sources: dormant providers (full balance), or any provider with excess
        # after allocation that has NO bets in the current batch
        excess_providers: list[tuple[str, float]] = []  # (pid, amount)
        for pid, pb in provider_balances.items():
            if pid in SHARP_PROVIDERS:
                continue
            if pid in providers_with_shortfall:
                continue
            excess = pb.remaining  # balance - allocated
            if excess <= 0:
                continue
            # Dormant providers: full balance available
            # Playing/wagering with excess: only the unallocated portion
            excess_providers.append((pid, excess))
        # Sort by excess descending — withdraw largest idle balances first
        excess_providers.sort(key=lambda x: -x[1])

        # --- Priority 4: Transfers replace deposits when excess is available ---
        # For each soft deposit, check if we can cover it (partially or fully) via
        # transfer from an excess provider. Reduce the deposit amount accordingly.
        remaining_excess = list(excess_providers)
        transfer_actions: list[dict] = []

        for target in actions:
            if target["type"] != "deposit" or target["priority"] not in (2, 3):
                continue
            if not remaining_excess:
                break

            original_amount = target["amount"]
            shortfall = original_amount
            while shortfall > 0 and remaining_excess:
                source_pid, source_amount = remaining_excess[0]
                transfer_amount = min(source_amount, shortfall)
                if transfer_amount <= 0:
                    remaining_excess.pop(0)
                    continue

                # Proportional share of unlocks/ev based on amount covered
                ratio = transfer_amount / original_amount if original_amount > 0 else 0
                transfer_actions.append({
                    "type": "transfer",
                    "from_provider_id": source_pid,
                    "to_provider_id": target["provider_id"],
                    "amount": round(transfer_amount, 2),
                    "unlocks": round(target["unlocks"] * ratio),
                    "avg_edge": target["avg_edge"],
                    "expected_ev": round(target["expected_ev"] * ratio, 2),
                    "currency": "SEK",
                    "priority": 4,
                    "priority_label": "transfer",
                })

                shortfall -= transfer_amount
                remaining_excess[0] = (source_pid, source_amount - transfer_amount)
                if remaining_excess[0][1] <= 0:
                    remaining_excess.pop(0)

            # Reduce the deposit to only the remaining shortfall after transfers
            if shortfall < original_amount:
                covered = original_amount - shortfall
                ratio_remaining = shortfall / original_amount if original_amount > 0 else 0
                target["amount"] = round(max(shortfall, 0), 2)
                target["unlocks"] = round(target["unlocks"] * ratio_remaining)
                target["expected_ev"] = round(target["expected_ev"] * ratio_remaining, 2)

        # Remove deposits that are fully covered by transfers, and 0-amount transfers
        actions = [a for a in actions if not (a["type"] == "deposit" and a["amount"] <= 0)]
        actions.extend([t for t in transfer_actions if t["amount"] > 1])

        # --- Priority 5: Withdrawals (remaining excess after transfers) ---
        # Only recommend withdrawing from dormant/cleared providers (no active bets or bonus)
        for pid, pb in provider_balances.items():
            if pid in SHARP_PROVIDERS:
                continue
            if pid in providers_with_shortfall:
                continue
            # Only dormant or fully-cleared playing providers
            if pb.lifecycle not in ("dormant", "playing", "limited"):
                continue
            if pb.wagering_remaining > 0:
                continue  # Still wagering — don't withdraw

            # Subtract any amount already committed to transfers
            transferred = sum(
                a["amount"] for a in actions
                if a["type"] == "transfer" and a.get("from_provider_id") == pid
            )
            remaining_amount = pb.remaining - transferred
            if remaining_amount <= 0:
                continue

            actions.append({
                "type": "withdraw",
                "provider_id": pid,
                "amount": round(remaining_amount, 2),
                "unlocks": 0,
                "avg_edge": 0,
                "expected_ev": 0,
                "currency": "SEK",
                "priority": 5,
                "priority_label": "withdraw_excess",
            })

        # Sort by priority, then by expected_ev descending within same priority
        actions.sort(key=lambda a: (a["priority"], -a.get("expected_ev", 0)))

        # Compute totals
        deployed = sum(pb.initial_balance for pb in provider_balances.values())
        withdrawable = sum(
            a["amount"] for a in actions if a["type"] == "withdraw"
        )

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
                "avg_edge": round(avg_edge, 1),
                "avg_stake": round(avg_stake, 0),
                "ev_per_session": round(total_ev, 2),  # assumes drain balance = 1 session
            }
        return result

