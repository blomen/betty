"""
BatchBuilder service — two-phase pipeline:

Phase 1 (collect): Find ALL +EV opportunities, compute Kelly stakes from total
bankroll.  Balance-blind — one candidate per (cluster, event, market, outcome, point).

Phase 2 (allocate): Distribute ALL candidates across siblings. Capital-blind —
every +EV bet is included. 10-bet-per-provider cap, with sibling scaling:
>10 bets in a cluster → spill to next sibling. Funding status is annotated
after assignment (funded=True/False) but never gates inclusion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..bankroll.stake_calculator import (
    ABSOLUTE_MIN_STAKE,
    OPTIMAL_MAX_KELLY,
    OPTIMAL_SINGLE_BET_CAP,
    calculate_stake,
    dynamic_min_stake,
    provider_fee_rate,
    provider_min_edge_pct,
    provider_min_stake_sek,
)
from ..constants import PLATFORM_GROUPS, PROVIDER_CANONICAL, UNLIMITED_PROVIDERS
from ..repositories.opportunity_repo import OpportunityRepo
from ..repositories.profile_repo import ProfileRepo
from ..services.play_service import derive_lifecycle


def _utc_iso(dt: datetime | None) -> str | None:
    """Serialize datetime as UTC ISO string (ensures JS parses as UTC, not local)."""
    if dt is None:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


logger = logging.getLogger(__name__)

# Tier priority: higher is ranked first
TIER_PRIORITY = {"polymarket": 2, "pinnacle": 1, "soft": 0}
SHARP_PROVIDERS = frozenset({"pinnacle", "polymarket"})

MAX_TTK_HOURS = 168.0  # 1 week — frontend TTK filter handles the rest


@dataclass
class BatchBet:
    """A single bet candidate in the batch pipeline."""

    # Ranking / tier
    rank: int
    tier: str  # "sharp" or "soft"

    # Bet identity
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None
    odds: float
    fair_odds: float
    edge_pct: float  # percentage (e.g. 3.5 for 3.5%)

    # Stake & EV
    stake: float
    expected_profit: float

    # Bonus context
    is_bonus: bool
    bonus_type: str | None

    # Display
    display_home: str
    display_away: str
    sport: str
    league: str | None
    start_time: object | None
    detected_at: object | None  # when opportunity was last refreshed
    odds_age_minutes: float | None  # staleness of the odds

    # Lifecycle / cluster
    lifecycle: str
    cluster: str  # cluster / group name (e.g. "kambi", "vbet")

    # Funding status
    funded: bool = True  # False = needs deposit to play
    skip_reason: str | None = None
    bankroll_needed: float = 0.0

    # Provider metadata (for navigation — altenar event IDs, kambi matchup IDs, etc.)
    provider_meta: dict | None = None


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
    is_bonus_phase: bool = False  # True when in freebet_available phase

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


def _bonus_retention_rate(wagering_multiplier: float, bonus_type: str = "bonusdeposit") -> float:
    """Estimate what fraction of a bonus survives wagering.

    Lower wagering = higher retention. Freebets only return profit (not stake),
    so effective retention is halved.
    """
    if wagering_multiplier <= 1:
        rate = 0.95
    elif wagering_multiplier <= 6:
        rate = 0.80
    elif wagering_multiplier <= 12:
        rate = 0.60
    elif wagering_multiplier <= 20:
        rate = 0.40
    else:
        rate = 0.25
    # Freebets: only profit is kept, not stake — roughly halve retention
    if bonus_type == "freebet":
        rate *= 0.5
    return rate


def _get_unclaimed_bonuses(profile_repo, profile_id: int) -> dict[str, dict]:
    """Return {provider_id: bonus_config} for providers with unclaimed bonuses.

    Unclaimed = bonus config exists in providers.yaml but bonus_status is
    'available' or 'claimed' (with amount=0) in the profile.
    """
    from ..config import load_config

    config = load_config()
    providers_with_bonus = {}
    for pid in config.get_enabled_providers():
        pc = config.get_provider(pid)
        if pc and pc.bonus and pc.bonus.get("amount", 0) > 0:
            providers_with_bonus[pid] = pc.bonus

    if not providers_with_bonus:
        return {}

    # Check which ones are already claimed/in-progress
    statuses = profile_repo.get_bonus_statuses_batch(profile_id, list(providers_with_bonus.keys()))
    unclaimed = {}
    for pid, bonus_cfg in providers_with_bonus.items():
        st = statuses.get(pid, {})
        status = st.get("status", "available")
        # "available" = never claimed; "claimed" with amount=0 = already redeemed
        if status == "available":
            unclaimed[pid] = bonus_cfg
    return unclaimed


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

    def build(self, profile_id: int, exclude: list[str] | None = None, priority_provider: str | None = None) -> dict:
        """
        Main entry point — two-phase pipeline:

        Phase 1 (collect): balance-blind, all +EV opportunities with Kelly stakes.
        Phase 2 (allocate): assign providers, enforce balance/cap/bonus constraints.
        """
        self._priority_provider = priority_provider
        profile = self.profile_repo.get_active()
        total_bankroll = self.profile_repo.get_stake_bankroll(profile_id)

        # Sizing model:
        #   - Unlimited providers (pinnacle/cloudbet/kalshi/polymarket) share
        #     ONE pooled bankroll = total_bankroll (get_stake_bankroll). The
        #     user arbs cash freely between them, so a value bet at any one is
        #     sized off the combined unlimited pool. See _make_candidate.
        #   - Soft books size per-provider / per-cluster off their own balance.
        #
        # No open-position augmentation: polymarket's synced balance is its
        # Portfolio (cash + open-position value), so pending bets are already
        # counted. Adding pending stakes back double-counted positions and
        # inflated stakes (~$110 portfolio + ~$76 pending → bogus ~$186 basis).
        from ..config import get_exchange_rate

        raw_balances = self.profile_repo.get_all_balances(profile_id)
        provider_bankroll_sek = {pid: bal * get_exchange_rate(pid) for pid, bal in raw_balances.items()}

        # Cluster bankroll = sum of all sibling balances. Used when the
        # opp's provider has 0 balance but a sibling in the same cluster
        # has funds (cluster siblings share odds + are fungible for placement).
        cluster_bankroll_sek: dict[str, float] = {}
        for grp_name, grp_info in PLATFORM_GROUPS.items():
            cluster_bankroll_sek[grp_name] = sum(provider_bankroll_sek.get(p, 0.0) for p in grp_info["members"])

        # -- Phase 1: candidate collection (per-provider Kelly) ----------------
        candidates = self._collect_candidates(total_bankroll, profile, provider_bankroll_sek, cluster_bankroll_sek)

        # Filter out blacklisted bets (persisted across sessions)
        from ..db.models import Bet, BetBlacklist

        blacklisted = {
            (bl.event_id, bl.provider_id)
            for bl in self.db.query(BetBlacklist).filter(BetBlacklist.profile_id == profile_id).all()
        }
        if blacklisted:
            candidates = [c for c in candidates if (c.event_id, c.provider_id) not in blacklisted]

        # Filter out events that already have a pending bet (already placed)
        placed_events = set()
        for cluster_name, group_info in PLATFORM_GROUPS.items():
            members = set(group_info["members"])
            pending_bets = (
                self.db.query(Bet.event_id)
                .filter(
                    Bet.profile_id == profile_id,
                    Bet.result == "pending",
                    Bet.provider_id.in_(members),
                )
                .all()
            )
            for (eid,) in pending_bets:
                placed_events.add((eid, cluster_name))
        # Also check standalone providers
        standalone_pending = (
            self.db.query(Bet.event_id, Bet.provider_id)
            .filter(
                Bet.profile_id == profile_id,
                Bet.result == "pending",
            )
            .all()
        )
        for eid, pid in standalone_pending:
            placed_events.add((eid, _provider_to_cluster(pid)))
        if placed_events:
            before = len(candidates)
            candidates = [c for c in candidates if (c.event_id, c.cluster) not in placed_events]
            skipped = before - len(candidates)
            if skipped:
                logger.info(f"[batch] Skipped {skipped} already-placed events")

        # Filter out session-excluded bets (UI "remove" action, non-persisted)
        if exclude:
            exclude_set = set(exclude)
            candidates = [
                c for c in candidates if f"{c.cluster}:{c.event_id}:{c.market}:{c.outcome}:{c.point}" not in exclude_set
            ]

        # Rank: sharp first, then by edge descending
        ranked = sorted(
            candidates,
            key=lambda b: (-TIER_PRIORITY.get(b.tier, 0), -b.edge_pct),
        )

        # -- Phase 2: allocate providers + balances ----------------------------
        provider_balances = self._load_provider_balances(profile_id)
        registered = self.profile_repo.get_all_registered_providers(profile_id)

        funded_batch, missed = self._allocate_batch(ranked, provider_balances, registered)

        # Merge: funded bets + missed (unfunded) in one list
        batch = funded_batch + missed

        for i, bet in enumerate(batch):
            bet.rank = i + 1

        # Bulk-populate odds_age_minutes from Odds.updated_at
        self._populate_odds_age(batch)

        # Bulk-populate provider_meta for navigation (altenar IDs, etc.)
        self._populate_provider_meta(batch)

        # Count opportunity volume per cluster (from ALL candidates, not just batch)
        cluster_opp_stats = self._compute_cluster_opp_stats(candidates)

        # Get wagering history for capital plan
        wager_info = self.profile_repo.get_avg_daily_wager(profile_id)
        avg_daily_wager = wager_info.get("avg_daily_wager", 0)
        has_wager_history = wager_info.get("has_history", False)

        unclaimed = _get_unclaimed_bonuses(self.profile_repo, profile_id)

        capital_plan = self._build_capital_plan_v3(
            provider_balances=provider_balances,
            missed=missed,
            total_bankroll=total_bankroll,
            cluster_opp_stats=cluster_opp_stats,
            avg_daily_wager=avg_daily_wager,
            has_wager_history=has_wager_history,
            unclaimed_bonuses=unclaimed,
        )

        # Get exchange rate for USDC → SEK conversion
        from ..config import get_exchange_rate

        usdc_rate = get_exchange_rate("polymarket")

        # Build provider balance map for frontend
        bal_map = {pid: round(pb.initial_balance, 2) for pid, pb in provider_balances.items()}

        # Count bets placed today per provider
        placed_today = self._count_placed_today(profile_id)

        return {
            "batch": [self._bet_to_dict(b) for b in batch],
            "summary": self._build_summary(batch, usdc_rate),
            "balance_status": self._build_balance_status(provider_balances, missed),
            "missed_opportunities": self._build_missed_summary(missed),
            "deposit_recommendations": [],
            "withdrawal_recommendations": [],
            "capital_plan": {**capital_plan, "usdc_rate": usdc_rate},
            "wagering_projections": self._compute_wagering_projections(batch, provider_balances),
            "provider_balances": bal_map,
            "placed_today": placed_today,
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
                wagering_total=(bonus_info.get("wagering_requirement", 0) or 0),
                wagering_remaining=max(
                    0, (bonus_info.get("wagering_requirement", 0) or 0) - (bonus_info.get("wagered_amount", 0) or 0)
                ),
                days_remaining=bonus_info.get("days_remaining"),
            )

        return result

    def _collect_candidates(
        self,
        total_bankroll: float,
        profile,
        provider_bankroll_sek: dict[str, float],
        cluster_bankroll_sek: dict[str, float],
    ) -> list[BatchBet]:
        """
        Query all opportunity types and compute Kelly stakes.

        Balance-blind: returns one candidate per (cluster, event, market,
        outcome, point), keeping the highest edge when duplicates exist.

        Refreshes odds from the DB to avoid batching stale edges.
        """
        min_edge_pct = getattr(profile, "min_edge_pct", 2.0) or 2.0
        single_bet_cap_pct = OPTIMAL_SINGLE_BET_CAP
        min_edge = min_edge_pct / 100.0
        min_stake = dynamic_min_stake(total_bankroll)

        raw: list[BatchBet] = []

        for opp_type in ("value", "reverse_value"):
            for opp, event in self.opp_repo.find_active(type=opp_type):
                bet = self._make_candidate(
                    opp,
                    event,
                    opp_type,
                    total_bankroll,
                    single_bet_cap_pct,
                    min_edge,
                    min_stake,
                    provider_bankroll_sek,
                    cluster_bankroll_sek,
                )
                if bet is not None:
                    raw.append(bet)

        # Dedup: one per (cluster, event, market, outcome, point) — keep highest edge
        best: dict[tuple, BatchBet] = {}
        for c in raw:
            key = (c.cluster, c.event_id, c.market, c.outcome, c.point)
            if key not in best or c.edge_pct > best[key].edge_pct:
                best[key] = c

        # One bet per event per cluster — betting multiple outcomes on the same
        # event is correlated risk. Keep the outcome with the highest edge.
        best_per_event: dict[tuple, BatchBet] = {}
        for c in best.values():
            ekey = (c.cluster, c.event_id)
            if ekey not in best_per_event or c.edge_pct > best_per_event[ekey].edge_pct:
                best_per_event[ekey] = c
        return list(best_per_event.values())

    def _make_candidate(
        self,
        opp,
        event,
        opp_type: str,
        total_bankroll: float,
        single_bet_cap_pct: float,
        min_edge: float,
        min_stake: float,
        provider_bankroll_sek: dict[str, float],
        cluster_bankroll_sek: dict[str, float],
    ) -> BatchBet | None:
        """
        Convert an Opportunity+Event into a BatchBet candidate, or None to skip.

        Per-provider Kelly: stake sized to THIS provider's own balance, OR
        the cluster's combined balance for soft siblings (altenar tenants,
        kambi tenants, etc. — they share odds and are fungible for
        placement). Skips entirely if both are 0 — no fallback to total
        bankroll, which would over-stake an unfunded provider relative to
        what the user can actually place. Provider routing + bonus logic
        still happens in _allocate_batch().
        """

        # Skip live events (TTK <= 0) and events beyond 48h
        if event.start_time:
            now = datetime.now(timezone.utc)
            st = event.start_time if event.start_time.tzinfo else event.start_time.replace(tzinfo=timezone.utc)
            ttk_hours = (st - now).total_seconds() / 3600
            if ttk_hours <= 0:
                return None
            if ttk_hours > MAX_TTK_HOURS:
                return None
        else:
            ttk_hours = None

        provider_id = opp.provider1_id
        odds = opp.odds1 or 0.0
        fair_odds = opp.odds2 or 0.0
        edge_raw = (opp.edge_pct or 0.0) / 100.0

        # Per-provider min-edge filter — skip outright before Kelly. Polymarket
        # has ~$0.07 Polygon gas per trade, so sub-5% edge bets at the $1 min
        # are net-EV-negative after gas (gas-aware MC showed median bankroll
        # collapsing to $1 / 61.5% bust). Pinnacle/Cloudbet/Kalshi keep 1%
        # floor — no per-trade gas, fee already in odds.
        prov_min_edge_pct = provider_min_edge_pct(provider_id)
        if (opp.edge_pct or 0.0) < prov_min_edge_pct:
            return None

        # Kelly bankroll:
        #   - Unlimited providers (pinnacle/cloudbet/kalshi/polymarket) share
        #     ONE pooled bankroll — total_bankroll (the unlimited-only sum from
        #     get_stake_bankroll). Cash moves freely between them, so a value
        #     bet at any one is sized off the combined pool.
        #   - Soft books: per-provider own balance, with cluster fallback when
        #     the opp's provider has 0 balance but a sibling does (siblings
        #     share odds + are interchangeable for placement).
        #   - 0 → calculate_stake returns stake=0 with skip_reason +
        #     bankroll_needed so the UI shows a "deposit to unlock" hint.
        if provider_id in UNLIMITED_PROVIDERS:
            kelly_bankroll = total_bankroll
        else:
            own = provider_bankroll_sek.get(provider_id, 0.0)
            cluster = _provider_to_cluster(provider_id)
            cluster_sum = cluster_bankroll_sek.get(cluster, 0.0)
            kelly_bankroll = own if own > 0 else cluster_sum

        # Fee-aware edge: subtract round-trip provider fees before Kelly so
        # we don't over-stake bets whose +EV gets eaten by the cost.
        # Pinnacle/cluster soft = 0% (vig in odds, already netted by fair).
        # Polymarket 2% maker, Kalshi ~5% taker — meaningful at low edges.
        fee = provider_fee_rate(provider_id)
        edge_after_fees = max(0.0, edge_raw - fee)

        # Per-provider min stake: profile (Pinnacle 20 kr, Poly $2≈21 kr,
        # Kalshi $2≈21 kr, soft books fall back to dynamic_min_stake).
        # provider_min_stake_sek converts the profile's native-currency
        # minimum (USD/USDC) to SEK so it can be compared against Kelly's
        # SEK-equivalent bankroll math directly.
        from ..config import get_exchange_rate

        provider_min = provider_min_stake_sek(
            provider_id,
            exchange_rate=get_exchange_rate(provider_id),
            fallback=min_stake,
        )

        result = calculate_stake(
            bankroll_total=kelly_bankroll,
            edge_raw=edge_after_fees,
            odds=odds,
            single_bet_cap_pct=single_bet_cap_pct,
            min_edge=min_edge,
            min_odds=0.0,
            min_stake=provider_min,
            max_kelly=OPTIMAL_MAX_KELLY,
        )
        # "low EV" = no real edge after Kelly + caps; drop. Other skip_reasons
        # ("Kelly too small" / "add Xkr to play") indicate a legitimate +EV bet
        # the user could play after a deposit — surface them with stake=0 and
        # bankroll_needed populated so the UI can render a deposit hint.
        skip_reason = result.skip_reason
        if skip_reason == "low EV":
            return None
        stake = result.stake
        if stake <= 0 and not skip_reason:
            return None

        # Convert SEK stake to native for USD-denominated providers (poly, kalshi).
        # Their balances and bet placements are in USDC/USD; the balance-vs-stake
        # check downstream compares native-to-native.
        from .. import bankroll
        from ..config import get_exchange_rate

        prof = bankroll.stake_calculator.PROVIDER_STAKE_PROFILES.get(provider_id)
        if prof and prof.currency != "SEK" and stake > 0:
            exchange_rate = get_exchange_rate(provider_id)
            if exchange_rate > 0:
                stake = stake / exchange_rate

        expected_profit = stake * edge_raw

        if provider_id == "polymarket":
            tier = "polymarket"
        elif provider_id == "pinnacle":
            tier = "pinnacle"
        else:
            tier = "soft"
        cluster = _provider_to_cluster(provider_id)

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
            is_bonus=False,
            bonus_type=None,
            display_home=event.display_home or event.home_team or "",
            display_away=event.display_away or event.away_team or "",
            sport=event.sport or "",
            league=event.league,
            start_time=event.start_time,
            detected_at=opp.detected_at,
            odds_age_minutes=opp.odds_age_minutes,
            lifecycle="available",
            cluster=cluster,
            funded=False,  # allocation will flip to True if there's balance
            skip_reason=skip_reason,
            bankroll_needed=getattr(result, "bankroll_needed", 0.0) or 0.0,
        )

    def _populate_odds_age(self, batch: list[BatchBet]) -> None:
        """Bulk-lookup Odds.updated_at to compute odds_age_minutes for each bet."""
        if not batch:
            return
        now = datetime.now(timezone.utc)
        keys = [(b.event_id, b.provider_id, b.market, b.outcome, b.point) for b in batch]
        # Single query for all odds timestamps
        from ..db.models import Odds

        rows = (
            self.db.query(Odds.event_id, Odds.provider_id, Odds.market, Odds.outcome, Odds.point, Odds.updated_at)
            .filter(
                Odds.event_id.in_(list({k[0] for k in keys})),
                Odds.provider_id.in_(list({k[1] for k in keys})),
            )
            .all()
        )
        lookup = {}
        for r in rows:
            lookup[(r.event_id, r.provider_id, r.market, r.outcome, r.point)] = r.updated_at
        for b in batch:
            ts = lookup.get((b.event_id, b.provider_id, b.market, b.outcome, b.point))
            if ts:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                b.odds_age_minutes = (now - ts).total_seconds() / 60.0

    def _populate_provider_meta(self, batch: list[BatchBet]) -> None:
        """Bulk-lookup Odds.provider_meta for navigation (altenar IDs, kambi matchup, etc.)."""
        if not batch:
            return
        from ..db.models import Odds

        # Collect all provider IDs we need to query — include canonical IDs
        # for cloned bets (e.g. quickcasino odds are stored under betinia)
        provider_ids = set()
        for b in batch:
            provider_ids.add(b.provider_id)
            canonical = PROVIDER_CANONICAL.get(b.provider_id)
            if canonical:
                provider_ids.add(canonical)

        keys = {(b.event_id, b.market, b.outcome) for b in batch}
        rows = (
            self.db.query(Odds.event_id, Odds.provider_id, Odds.market, Odds.outcome, Odds.provider_meta)
            .filter(
                Odds.event_id.in_(list({k[0] for k in keys})),
                Odds.provider_id.in_(list(provider_ids)),
                Odds.provider_meta.isnot(None),
            )
            .all()
        )
        lookup = {}
        event_lookup: dict[tuple[str, str], dict] = {}  # (event_id, provider_id) → any meta
        for r in rows:
            if r.provider_meta:
                meta = r.provider_meta if isinstance(r.provider_meta, dict) else {}
                lookup[(r.event_id, r.provider_id, r.market, r.outcome)] = meta
                # Keep first meta per event+provider for fallback navigation
                if meta.get("event_id") and (r.event_id, r.provider_id) not in event_lookup:
                    event_lookup[(r.event_id, r.provider_id)] = meta
        for b in batch:
            meta = lookup.get((b.event_id, b.provider_id, b.market, b.outcome))
            # Fallback: look up via canonical provider (clone shares same odds/meta)
            if not meta:
                canonical = PROVIDER_CANONICAL.get(b.provider_id)
                if canonical:
                    meta = lookup.get((b.event_id, canonical, b.market, b.outcome))
            # Fallback: any meta for same event+provider (navigation IDs are event-level)
            if not meta:
                meta = event_lookup.get((b.event_id, b.provider_id))
                if not meta:
                    canonical = PROVIDER_CANONICAL.get(b.provider_id)
                    if canonical:
                        meta = event_lookup.get((b.event_id, canonical))
            if meta:
                b.provider_meta = meta

    # Daily placement cap per provider (enforced in play loop, not here).
    # Batch returns ALL +EV bets so the UI shows the full picture.
    BETS_PER_PROVIDER = 10

    @staticmethod
    def _clone_bet_to_provider(
        bet: BatchBet,
        new_provider_id: str,
        pb: ProviderBalance,
        *,
        stake: float | None = None,
        is_bonus: bool | None = None,
        bonus_type: str | None = None,
    ) -> BatchBet:
        """Clone a bet to a different provider (same cluster = same odds).

        Optional overrides for stake/bonus when the target provider has
        freebet or trigger constraints.
        """
        actual_stake = stake if stake is not None else bet.stake
        actual_is_bonus = is_bonus if is_bonus is not None else bet.is_bonus
        actual_bonus_type = bonus_type if bonus_type is not None else bet.bonus_type
        edge_raw = bet.edge_pct / 100.0
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
            stake=actual_stake,
            expected_profit=actual_stake * edge_raw,
            is_bonus=actual_is_bonus,
            bonus_type=actual_bonus_type,
            display_home=bet.display_home,
            display_away=bet.display_away,
            sport=bet.sport,
            league=bet.league,
            start_time=bet.start_time,
            detected_at=bet.detected_at,
            odds_age_minutes=bet.odds_age_minutes,
            lifecycle=pb.lifecycle,
            cluster=bet.cluster,
            provider_meta=bet.provider_meta,
        )

    def _allocate_batch(
        self,
        candidates: list[BatchBet],
        provider_balances: dict[str, ProviderBalance],
        registered_providers: set[str],
    ) -> tuple[list[BatchBet], list[BatchBet]]:
        """
        Fill-then-spill allocation: for each cluster, fill the first sibling
        with up to 10 bets, then spill to the next sibling. Top edge first.

        Sharp (pinnacle/polymarket): no cap, stays on own provider.
        """
        cap = self.BETS_PER_PROVIDER
        batch: list[BatchBet] = []
        bets_assigned: dict[str, int] = {}  # provider_id → count

        # Build sibling list per cluster, sorted by balance desc
        siblings: dict[str, list[str]] = {}
        for group_name, group_info in PLATFORM_GROUPS.items():
            sibs = list(group_info["members"])
            sibs.sort(
                key=lambda pid: -(provider_balances.get(pid, ProviderBalance(pid, group_name, 0)).initial_balance)
            )
            siblings[group_name] = sibs

        # If a priority provider is set, move it to the front of its cluster
        if hasattr(self, "_priority_provider") and self._priority_provider:
            for group_name, sibs in siblings.items():
                if self._priority_provider in sibs:
                    sibs.remove(self._priority_provider)
                    sibs.insert(0, self._priority_provider)
                    break

        # Standalone providers (not in any platform group)
        registered = registered_providers or set(provider_balances.keys())
        for pid in registered:
            cluster = _provider_to_cluster(pid)
            if cluster not in siblings:
                siblings[cluster] = [pid]

        for bet in candidates:
            cluster = bet.cluster

            # Deposit-hint candidate: stake=0 + skip_reason set means the
            # calculator already decided the user can't fund this bet.
            # Append as-is (unfunded) so the UI can render it with the
            # bankroll_needed deposit hint instead of silently dropping it.
            if bet.stake <= 0 and bet.skip_reason:
                bet.funded = False
                batch.append(bet)
                continue

            if bet.tier in ("polymarket", "pinnacle") or bet.provider_id == "cloudbet":
                # Sharp/signal: no cap, stays on own provider
                pid = bet.provider_id
                pb = provider_balances.get(pid)
                if pb is None:
                    pb = ProviderBalance(provider_id=pid, cluster=cluster, initial_balance=0)
                placed = self._clone_bet_to_provider(bet, pid, pb)
                if pb.remaining >= bet.stake:
                    placed.funded = True
                    pb.allocated += placed.stake
                elif pb.remaining >= ABSOLUTE_MIN_STAKE:
                    # Cap stake to available balance
                    placed.stake = pb.remaining
                    placed.expected_profit = placed.stake * (bet.edge_pct / 100.0)
                    placed.funded = True
                    pb.allocated += placed.stake
                else:
                    placed.funded = False
                    placed.skip_reason = f"insufficient balance on {pid}"
                    pb.missed_bets += 1
                    pb.missed_ev += bet.expected_profit
                bets_assigned[pid] = bets_assigned.get(pid, 0) + 1
                batch.append(placed)
                continue

            # Soft: fill-then-spill — first sibling up to cap, then next
            sibs = siblings.get(cluster, [bet.provider_id])
            for pid in sibs:
                if bets_assigned.get(pid, 0) >= cap:
                    continue
                pb = provider_balances.get(pid)
                if pb is None:
                    pb = ProviderBalance(provider_id=pid, cluster=cluster, initial_balance=0)

                placed = self._clone_bet_to_provider(bet, pid, pb)
                if pb.remaining >= bet.stake:
                    placed.funded = True
                    pb.allocated += placed.stake
                elif pb.remaining >= ABSOLUTE_MIN_STAKE:
                    # Cap stake to available balance
                    placed.stake = pb.remaining
                    placed.expected_profit = placed.stake * (bet.edge_pct / 100.0)
                    placed.funded = True
                    pb.allocated += placed.stake
                else:
                    placed.funded = False
                    placed.skip_reason = f"insufficient balance on {pid}"
                    pb.missed_bets += 1
                    pb.missed_ev += bet.expected_profit

                bets_assigned[pid] = bets_assigned.get(pid, 0) + 1
                batch.append(placed)
                break

            # All siblings at cap — bet dropped

        funded = [b for b in batch if b.funded]
        missed = [b for b in batch if not b.funded]
        return funded, missed

    def _count_placed_today(self, profile_id: int) -> dict[str, int]:
        """Count bets placed today per provider."""
        from ..db.models import Bet

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            self.db.query(Bet.provider_id, Bet.id)
            .filter(
                Bet.profile_id == profile_id,
                Bet.placed_at >= today_start,
            )
            .all()
        )
        counts: dict[str, int] = {}
        for pid, _ in rows:
            counts[pid] = counts.get(pid, 0) + 1
        return counts

    def _build_summary(self, batch: list[BatchBet], usdc_rate: float = 1.0) -> dict:
        polymarket_bets = [b for b in batch if b.tier == "polymarket"]
        pinnacle_bets = [b for b in batch if b.tier == "pinnacle"]
        soft_bets = [b for b in batch if b.tier == "soft"]

        # Priority tier breakdown for soft bets (funded only)
        [b for b in soft_bets if b.funded]

        poly_ev_usdc = round(sum(b.expected_profit for b in polymarket_bets), 2)
        pinnacle_ev = round(sum(b.expected_profit for b in pinnacle_bets), 2)
        soft_ev = round(sum(b.expected_profit for b in soft_bets), 2)
        # Convert Polymarket USDC EV to SEK for the total
        total_ev_sek = round(poly_ev_usdc * usdc_rate + pinnacle_ev + soft_ev, 2)

        return {
            "total_bets": len(batch),
            "total_stake": round(sum(b.stake for b in batch), 2),
            "total_expected_profit": total_ev_sek,
            "polymarket_bets": len(polymarket_bets),
            "polymarket_ev": poly_ev_usdc,
            "pinnacle_bets": len(pinnacle_bets),
            "pinnacle_ev": pinnacle_ev,
            "soft_bets": len(soft_bets),
            "soft_ev": soft_ev,
            "usdc_rate": usdc_rate,
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
            if batch_stake <= 0:
                continue
            projected_remaining = max(0, pb.wagering_remaining - batch_stake)
            projections.append(
                {
                    "provider_id": pid,
                    "cluster": pb.cluster,
                    "wagering_total": round(pb.wagering_total, 2),
                    "wagering_remaining": round(pb.wagering_remaining, 2),
                    "batch_stake": round(batch_stake, 2),
                    "projected_remaining": round(projected_remaining, 2),
                    "days_remaining": pb.days_remaining,
                }
            )
        return projections

    def _compute_wagering_projections_from_dicts(
        self,
        batch: list[dict],
        provider_balances: dict[str, ProviderBalance],
    ) -> list[dict]:
        """Same as _compute_wagering_projections but for dict-based locked batch."""
        provider_stakes: dict[str, float] = {}
        for bet in batch:
            pid = bet.get("provider_id", "")
            provider_stakes[pid] = provider_stakes.get(pid, 0) + bet.get("stake", 0)

        projections = []
        for pid, pb in provider_balances.items():
            if pb.wagering_remaining <= 0:
                continue
            batch_stake = provider_stakes.get(pid, 0)
            if batch_stake <= 0:
                continue
            projected_remaining = max(0, pb.wagering_remaining - batch_stake)
            projections.append(
                {
                    "provider_id": pid,
                    "cluster": pb.cluster,
                    "wagering_total": round(pb.wagering_total, 2),
                    "wagering_remaining": round(pb.wagering_remaining, 2),
                    "batch_stake": round(batch_stake, 2),
                    "projected_remaining": round(projected_remaining, 2),
                    "days_remaining": pb.days_remaining,
                }
            )
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
                "wagering_total": round(pb.wagering_total, 2),
                "wagering_remaining": round(pb.wagering_remaining, 2),
                "days_remaining": pb.days_remaining,
                "trigger_mode": pb.trigger_mode,
                "bonus_amount": round(pb.bonus_amount, 2),
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
            "start_time": _utc_iso(bet.start_time),
            "detected_at": _utc_iso(bet.detected_at),
            "odds_age_minutes": round(bet.odds_age_minutes, 1) if bet.odds_age_minutes is not None else None,
            "lifecycle": bet.lifecycle,
            "cluster": bet.cluster,
            "funded": bet.funded,
            "skip_reason": bet.skip_reason,
            "bankroll_needed": round(bet.bankroll_needed, 2) if bet.bankroll_needed else 0.0,
            "provider_meta": bet.provider_meta,
        }

    @staticmethod
    def _build_capital_plan_v3(
        provider_balances: dict[str, ProviderBalance],
        missed: list[BatchBet],
        total_bankroll: float,
        cluster_opp_stats: dict[str, dict],
        avg_daily_wager: float = 0.0,
        has_wager_history: bool = False,
        unclaimed_bonuses: dict[str, dict] | None = None,
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
            actions.append(
                {
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
                }
            )
            sharp_already_handled.add(pid)

        # --- Priority 2/3: Soft deposits ---
        # Aggregate missed bets per cluster, then recommend a single deposit
        # to the best-funded provider in that cluster (drain-first model).
        cluster_missed: dict[str, dict] = {}

        for pid in sorted(providers_with_shortfall):
            if pid in SHARP_PROVIDERS:
                continue
            pb = provider_balances.get(pid)
            m_bets = missed_by_provider.get(pid, [])

            if pb is None:
                cluster = _provider_to_cluster(pid)
                if not m_bets:
                    continue
            else:
                cluster = pb.cluster or pid

            missed_count = len(m_bets) if m_bets else (pb.missed_bets if pb else 0)
            missed_stake = sum(b.stake for b in m_bets) if m_bets else 0
            missed_ev = sum(b.expected_profit for b in m_bets) if m_bets else (pb.missed_ev if pb else 0)
            if missed_count == 0:
                continue

            if cluster not in cluster_missed:
                cluster_missed[cluster] = {"count": 0, "stake": 0, "ev": 0}
            cm = cluster_missed[cluster]
            # Avoid double-counting: only add if this cluster hasn't seen these bets
            cm["count"] = max(cm["count"], missed_count)
            cm["stake"] = max(cm["stake"], missed_stake)
            cm["ev"] = max(cm["ev"], missed_ev)

        for cluster, info in cluster_missed.items():
            missed_stake = info["stake"]
            missed_ev = info["ev"]
            missed_count = info["count"]
            if missed_stake <= 0:
                continue

            stats = cluster_opp_stats.get(cluster, {})

            # Find the best-funded provider in this cluster to deposit into
            best_pid = None
            best_bal = -1
            for pid, pb in provider_balances.items():
                if (pb.cluster or pid) != cluster:
                    continue
                if pb.lifecycle in ("dormant", "available"):
                    continue
                if pb.remaining > best_bal:
                    best_bal = pb.remaining
                    best_pid = pid

            if best_pid is None:
                # No funded provider — pick first sibling from cluster
                all_siblings = PLATFORM_GROUPS.get(cluster, {}).get("members", [cluster])
                best_pid = all_siblings[0] if all_siblings else cluster

            target_pb = provider_balances.get(best_pid)
            current_bal = target_pb.initial_balance if target_pb else 0

            # Bonus wagering → priority 2, otherwise → priority 3
            has_bonus = target_pb and target_pb.wagering_remaining > 0
            if has_bonus:
                effective_wager = avg_daily_wager if avg_daily_wager > 0 else 1000
                days_needed = target_pb.wagering_remaining / effective_wager
                if target_pb.days_remaining is not None and days_needed > target_pb.days_remaining:
                    continue
                priority = 2
                label = "bonus_deposit"
            else:
                priority = 3
                label = "soft_deposit"

            actions.append(
                {
                    "type": "deposit",
                    "provider_id": best_pid,
                    "cluster": cluster,
                    "amount": round(missed_stake, 2),
                    "target_balance": round(current_bal + missed_stake, 2),
                    "unlocks": missed_count,
                    "avg_edge": stats.get("avg_edge", 0),
                    "expected_ev": round(missed_ev, 2),
                    "currency": "SEK",
                    "priority": priority,
                    "priority_label": label,
                }
            )

        # --- Priority 2 (continued): wagering providers with low balance ---
        already_recommended = {a["provider_id"] for a in actions if a["type"] == "deposit"}
        already_recommended_clusters = set()
        for a in actions:
            if a["type"] == "deposit":
                apb = provider_balances.get(a["provider_id"])
                if apb:
                    already_recommended_clusters.add(apb.cluster or a["provider_id"])

        for pid, pb in provider_balances.items():
            if pid in SHARP_PROVIDERS or pid in already_recommended:
                continue
            if pb.wagering_remaining <= 0:
                continue
            cluster = pb.cluster or pid
            if cluster in already_recommended_clusters:
                continue
            effective_wager = avg_daily_wager if avg_daily_wager > 0 else 1000
            days_needed = pb.wagering_remaining / effective_wager
            if pb.days_remaining is not None and days_needed > pb.days_remaining:
                continue
            if pb.remaining > effective_wager:
                continue
            amount = round(max(effective_wager - pb.remaining, 100), -2)
            actions.append(
                {
                    "type": "deposit",
                    "provider_id": pid,
                    "cluster": cluster,
                    "amount": amount,
                    "target_balance": round(pb.initial_balance + amount, 2),
                    "unlocks": 0,
                    "avg_edge": 0,
                    "expected_ev": 0,
                    "currency": "SEK",
                    "priority": 2,
                    "priority_label": "bonus_deposit",
                }
            )
            already_recommended_clusters.add(cluster)

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

            actions.append(
                {
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
                }
            )

        # --- Priority 1.5: Transfer for bonus cycling ---
        # If a sibling finished wagering, suggest transferring to an unclaimed
        # sibling in the same cluster to claim its bonus.
        if unclaimed_bonuses:
            for group_name, group_info in PLATFORM_GROUPS.items():
                members = group_info["members"]
                # Find siblings with completed wagering + balance
                donors = []
                for pid in members:
                    pb = provider_balances.get(pid)
                    if not pb:
                        continue
                    if pb.wagering_remaining <= 0 and pb.remaining > 0 and pid not in providers_with_shortfall:
                        donors.append(pid)
                # Find unclaimed bonus siblings in this cluster
                targets = [pid for pid in members if pid in unclaimed_bonuses]
                if not donors or not targets:
                    continue
                for target_pid in targets:
                    bonus_cfg = unclaimed_bonuses[target_pid]
                    bonus_amt = bonus_cfg.get("amount", 0)
                    wager_mult = bonus_cfg.get("wagering_multiplier", 12)
                    bonus_type = bonus_cfg.get("type", "bonusdeposit")
                    retention = _bonus_retention_rate(wager_mult, bonus_type)
                    # Pick donor with most remaining balance
                    donor = max(donors, key=lambda p: provider_balances[p].remaining)
                    transfer_amt = min(provider_balances[donor].remaining, bonus_amt)
                    if transfer_amt <= 0:
                        continue
                    actions.append(
                        {
                            "type": "transfer",
                            "provider_id": target_pid,
                            "from_provider": donor,
                            "cluster": group_name,
                            "amount": round(transfer_amt, 2),
                            "target_balance": round(transfer_amt, 2),
                            "unlocks": 0,
                            "avg_edge": 0,
                            "expected_ev": round(bonus_amt * retention, 2),
                            "currency": "SEK",
                            "priority": 2,
                            "priority_label": "bonus_cycle_transfer",
                            "bonus_amount": bonus_amt,
                            "wagering_multiplier": wager_mult,
                        }
                    )

        # Sort by priority, then by deposit amount ascending (smallest trigger first
        # per MC sims — 0% ruin vs 57% with largest-first), then by EV descending.
        actions.sort(key=lambda a: (a["priority"], a.get("amount", 0), -a.get("expected_ev", 0)))

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
