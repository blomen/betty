"""
Opportunity Scanner

Unified scanning interface for finding betting opportunities:
- Value: Edge vs de-vigged sharp odds
- Bonus: Any edge for bonus clearing (no threshold)

This module queries the database and returns opportunity dataclasses.
Storage/persistence is handled by the caller (analyzer.py).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..bankroll.stake_calculator import StakeCalculator
from ..config import get_provider_currency
from ..constants import (
    PLATFORM_MAP,
    PREDICTION_MARKETS,
    SHARP_PROVIDERS,
    SIGNAL_ONLY_PROVIDERS,
    canonical_scope_for,
    consensus_staleness_minutes_for,
    staleness_minutes_for,
)
from ..db.models import Event
from ..repositories import EventRepo
from .devig import (
    compute_consensus_fair_odds,
    get_fair_odds_for_outcome,
)
from .value import ValueBet, find_value

logger = logging.getLogger(__name__)

# Minimum probability sum for a valid market (accounts for margin)
# Normal markets: 1.02-1.10, incomplete markets: < 0.90
MIN_VALID_PROB_SUM = 0.90

# Maximum odds ratio for same outcome across providers
# If max/min > threshold, likely event mismatch or stale odds
# Real odds rarely differ more than 35% across providers for same event
MAX_ODDS_RATIO = 1.35
# Spread/total markets have wider natural variance between books (different
# handicap conventions, vig structures). Tightened from 1.55 to 1.40 on
# 2026-05-27 after seeing Kambi alt-line upsert collisions sneak past the
# 1.55 limit (Bahia ratio 1.92/1.25 = 1.536). Real soft-vs-Pinnacle spread
# disagreements >40% on raw odds are almost always extraction bugs.
MAX_ODDS_RATIO_SPREAD = 1.40

# Hard safety net — edges above this are almost certainly data quality issues
# (wrong event match, stale odds, prediction market divergence).
MAX_EDGE_PCT = 50.0

# 2026-05-26: spread quarter-handicap convention mismatches surface as
# devigged-probability disagreement of >30pp on the same nominal outcome.
# Refuse value bets for soft providers in such buckets — they're pricing a
# different bet than Pinnacle, not offering value.
SPREAD_DISAGREEMENT_MAX_PP = 0.30

# 2026-05-27: defense-in-depth against provider point-sign convention bugs.
# A real 2-way Asian handicap with normal vig sums to ~1.04-1.08; sums far
# outside this range mean two opposite-side bets got filed under one key
# (extractor stored book-line convention without normalizing) or the same
# bet got duplicated. The per-outcome disagreement gate has a blind spot
# when both mis-keyed legs survive normalization within the 30pp window.
SPREAD_PROB_SUM_MIN = 0.93
SPREAD_PROB_SUM_MAX = 1.12

# Arb sanity ceiling — a *guaranteed* profit above this never reflects a real
# cross-book arbitrage; it means a leg is mispriced (e.g. a Polymarket outcome
# priced off the wrong side of the order book). Surfacing such an "arb" as
# placeable produces fake "ALL GREEN" bets. Real arbs are low single digits.
MAX_PLAUSIBLE_ARB_PCT = 15.0


def _implausible_arb_profit(*profit_pcts: float | None) -> bool:
    """True if any profit % exceeds MAX_PLAUSIBLE_ARB_PCT — i.e. a leg is
    mispriced and the opportunity must not be surfaced as a real arb."""
    return any((p or 0) > MAX_PLAUSIBLE_ARB_PCT for p in profit_pcts)


# Sports where Pinnacle uses SET handicaps but soft providers use GAME handicaps.
# Comparing spread markets across providers would produce phantom edges because
# e.g. "+1.5 sets" (Pinnacle) ≠ "+1.5 games" (Kambi/Altenar/VBet).
SET_SPREAD_SPORTS = frozenset({"tennis"})

# Legacy global staleness cap. Retained for the few callers that still log
# it as a reference, but the live freshness gate in group_odds is now
# per-provider via constants.staleness_minutes_for() — tied to each
# provider's extraction cadence. A 2-h-global cap left dropped-event rows
# pairing against fresh Pinnacle as ghost arbs for the full 2 h window.
MAX_ODDS_AGE_HOURS = 2

# Reverse value: minimum independent platforms for consensus
MIN_CONSENSUS_PLATFORMS = 3

# Reverse value: minimum odds (include favorites — consensus works at all odds levels)
MIN_REVERSE_ODDS = 1.20

# Reverse value: maximum odds to avoid extreme longshot noise
MAX_REVERSE_ODDS = 15.0


@dataclass
class ArbOpportunity:
    """An arbitrage opportunity: opposing outcomes both +EV at different providers."""

    event_id: str
    market: str

    # Each leg: {outcome, provider, odds, edge_pct, fair_odds, stake_pct}
    legs: list[dict]

    # Combined metrics
    combined_edge_pct: float  # Weighted average edge across legs
    guaranteed_profit_pct: float  # >0 = guaranteed profit regardless of outcome

    # Arb detection: true arb using only real bettable soft odds
    arb_profit_pct: float | None = None  # >0 = true executable arb (all soft legs)
    arb_legs: list[dict] | None = None  # All-soft version of legs (when arb_profit_pct > 0)

    # Event context
    home_team: str | None = None
    away_team: str | None = None
    sport: str | None = None
    league: str | None = None
    start_time: str | None = None


@dataclass
class BonusOpportunity:
    """An opportunity for bonus clearing (edge vs fair odds)."""

    event_id: str
    market: str
    outcome: str

    # The bet at anchor provider
    anchor_provider: str
    anchor_odds: float

    # Fair odds from Pinnacle (de-vigged)
    fair_odds: float
    fair_source: str  # "pinnacle" or "pinnacle(raw)"

    # The edge (can be negative for bonus clearing)
    edge_pct: float

    # Point/line value (for spread/total markets)
    point: float | None = None

    # Event context
    home_team: str | None = None
    away_team: str | None = None
    sport: str | None = None
    league: str | None = None


class OpportunityScanner:
    """
    Scans database for betting opportunities.

    Usage:
        scanner = OpportunityScanner(session)

        # Standard analysis
        values = scanner.scan_value(min_edge_pct=5.0)

        # Bonus mode (no threshold, all opportunities)
        bonus = scanner.scan_bonus("unibet", ["pinnacle", "polymarket"])

    Quality classification:
        Opportunities with edge >50% are filtered out as data errors.
        Individual provider odds >35% above Pinnacle raw are skipped.
    """

    def __init__(self, session: Session):
        self.session = session
        self.event_repo = EventRepo(session)

    def scan_value(self, min_edge_pct: float = 5.0, events: list = None) -> list[ValueBet]:
        """
        Find value bets against de-vigged Pinnacle odds.

        Uses Pinnacle as the sole sharp source. Their ~2.5% margin is
        removed using multiplicative de-vigging. Skips odds older than the
        provider's per-cadence staleness window (see
        constants.staleness_minutes_for()).

        Args:
            min_edge_pct: Minimum edge percentage (default 5%)
            events: Pre-loaded events list (skips DB query if provided)

        Returns:
            List of ValueBet sorted by edge (highest first)
        """
        opportunities = []

        # Get events with odds from 2+ providers
        if events is None:
            events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self.group_odds(event)

            # Tennis (and other set-based sports): Pinnacle spread = set handicap,
            # soft providers spread = game handicap. Same point value, different
            # unit → phantom edges. Skip spread comparisons for these sports.
            skip_spreads = event.sport in SET_SPREAD_SPORTS

            for market, odds_by_outcome in odds_grouped.items():
                if skip_spreads and market.startswith("spread"):
                    continue

                values = self.find_value_in_market(
                    event_id=event.id,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    min_edge_pct=min_edge_pct,
                    all_markets=odds_grouped,
                )
                opportunities.extend(values)

        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge_pct, reverse=True)

        logger.info(f"[Scanner] Found {len(opportunities)} value bets (>={min_edge_pct}% edge)")
        return opportunities

    def scan_value_with_stakes(
        self,
        stake_calculator: StakeCalculator,
        min_edge_pct: float = 5.0,
        confidence_threshold: int = 90,
    ) -> list[ValueBet]:
        """
        Find value bets with stake recommendations.

        Uses the StakeCalculator to compute recommended stakes for each
        opportunity, taking into account:
        - Dynamic Kelly sizing based on edge
        - Event/daily exposure caps
        - Bonus wagering requirements
        - Confidence scoring based on match quality

        Args:
            stake_calculator: Configured StakeCalculator instance
            min_edge_pct: Minimum edge percentage (default 5%)
            confidence_threshold: Match score threshold for high confidence (default 90)

        Returns:
            List of ValueBet with stake recommendations, sorted by edge
        """
        # Get base value bets
        raw_bets = self.scan_value(min_edge_pct=min_edge_pct)

        # Pre-fetch all events in one query (avoid N+1)
        event_ids = list({vb.event_id for vb in raw_bets})
        events_by_id = self.event_repo.get_by_ids(event_ids)

        # Pre-fetch steam-move signals once (single query) and key them on
        # (event_id, market, outcome, point, scope) so the per-bet lookup
        # below is O(1). Empty dict when STEAM_DETECTOR_ENABLED is off.
        from .steam_detector import detect_steam_moves

        steam_by_key: dict[tuple, dict] = {}
        try:
            for sig in detect_steam_moves(self.session):
                steam_by_key[(sig.event_id, sig.market, sig.outcome, sig.point, sig.scope)] = sig.to_dict()
        except Exception:
            steam_by_key = {}

        # Enrich with stakes and event context
        enriched_bets = []
        for vb in raw_bets:
            # Get event for context (from pre-fetched dict)
            event = events_by_id.get(vb.event_id)

            # Determine confidence based on match quality
            # High confidence = strong match score AND odds ratio within bounds
            is_high_confidence = self._assess_confidence(vb, confidence_threshold)

            # Calculate stake
            edge_decimal = vb.edge_pct / 100.0
            result = stake_calculator.calculate(
                edge_raw=edge_decimal,
                odds=vb.provider_odds,
                event_id=vb.event_id,
                provider_id=vb.provider,
                high_confidence=is_high_confidence,
            )

            # NFL key-number annotation (None for non-NFL or non-spread/total).
            # Diagnostic-only — does not affect edge_pct or stake.
            from .key_numbers import annotate as key_number_annotate

            key_info = key_number_annotate(
                sport=event.sport if event else None,
                market=vb.market,
                point=vb.point,
            )

            # Steam-signal lookup — diagnostic-only annotation. Uses the
            # canonical scope for this sport since that's what the
            # detector indexes on. None when the feature is disabled.
            canonical_scope = canonical_scope_for(event.sport if event else None)
            steam_sig = steam_by_key.get((vb.event_id, vb.market, vb.outcome, vb.point, canonical_scope))

            # Soft-consensus lean — Arnold's substitute for paid public-vs-
            # sharp bet-% feeds. Reads the cross-book implied-probability
            # spread to flag whether the public has loaded this side.
            from .consensus_lean import compute_consensus_lean

            lean_obj = compute_consensus_lean(
                odds_snapshot=vb.odds_snapshot,
                sharp_fair_probability=vb.fair_probability,
                bet_provider=vb.provider,
            )

            # Create enriched ValueBet
            enriched = ValueBet(
                event_id=vb.event_id,
                market=vb.market,
                outcome=vb.outcome,
                provider=vb.provider,
                provider_odds=vb.provider_odds,
                fair_odds=vb.fair_odds,
                fair_probability=vb.fair_probability,
                edge_pct=vb.edge_pct,
                recommended_stake=result.stake if result.stake > 0 else None,
                kelly_fraction=result.kelly_fraction,
                is_high_confidence=is_high_confidence,
                skip_reason=result.skip_reason,
                home_team=event.home_team if event else None,
                away_team=event.away_team if event else None,
                sport=event.sport if event else None,
                start_time=(event.start_time.isoformat() + "Z") if event and event.start_time else None,
                prob_sum=vb.prob_sum,
                pinnacle_overround=vb.pinnacle_overround,
                odds_snapshot=vb.odds_snapshot,
                point=vb.point,
                key_number=key_info.to_dict() if key_info else None,
                steam_signal=steam_sig,
                consensus_lean=lean_obj.to_dict() if lean_obj else None,
            )
            # Log ML features (best-effort, never blocks scanning)
            try:
                from src.ml.feature_store import log_features
                from src.ml.features.betting_features import extract_betting_features

                outcome_odds = {vb.outcome: vb.odds_snapshot or []}
                ml_features = extract_betting_features(
                    edge_pct=vb.edge_pct,
                    provider_odds=vb.provider_odds,
                    fair_odds=vb.fair_odds,
                    fair_probability=vb.fair_probability,
                    provider=vb.provider,
                    sport=event.sport if event else "unknown",
                    market=vb.market,
                    event_id=vb.event_id,
                    prob_sum=vb.prob_sum or 0,
                    odds_by_outcome=outcome_odds,
                    pinnacle_overround=vb.pinnacle_overround or 0,
                    event_start_time=event.start_time if event else None,
                    point=vb.point,
                )
                log_features(
                    session=self.session,
                    domain="betting",
                    source_id=f"{vb.event_id}_{vb.provider}_{vb.market}_{vb.outcome}",
                    source_type="opportunity",
                    features=ml_features,
                )
            except Exception as e:
                logger.debug(f"ML feature logging skipped: {e}")
            enriched_bets.append(enriched)

        # Sort by edge (highest first), then by stake
        enriched_bets.sort(key=lambda x: (x.edge_pct, x.recommended_stake or 0), reverse=True)

        # Apply routing priority from bankroll planner (if available)
        enriched_bets = self._apply_routing_priority(enriched_bets)

        # Count actionable bets
        actionable = sum(1 for b in enriched_bets if b.recommended_stake and b.recommended_stake > 0)
        logger.info(f"[Scanner] {actionable}/{len(enriched_bets)} value bets have stake recommendations")

        return enriched_bets

    def _apply_routing_priority(self, bets: list[ValueBet]) -> list[ValueBet]:
        """
        Re-rank value bets using bankroll planner routing priority.

        When multiple bets have similar edges, prefer providers that need
        wagering progress. Uses a tiny continuous penalty so routing priority
        only acts as a tiebreaker — never overrides meaningful edge differences.
        """
        try:
            from ..repositories import ProfileRepo
            from ..services.planner_service import BankrollPlannerService

            profile = ProfileRepo(self.session).get_active()
            if not profile:
                return bets

            service = BankrollPlannerService(self.session)
            recommendation = service.get_latest_recommendation(profile.id)
            if not recommendation or not recommendation.routing_priority:
                return bets

            priority_map = {p: i for i, p in enumerate(recommendation.routing_priority)}

            def sort_key(vb: ValueBet) -> float:
                provider_rank = priority_map.get(vb.provider, 999)
                return -vb.edge_pct + provider_rank * 0.001

            return sorted(bets, key=sort_key)
        except Exception:
            return bets

    def _assess_confidence(self, vb: ValueBet, threshold: int = 90) -> bool:
        """
        Assess confidence level for a value bet.

        High confidence criteria:
        - Odds difference from Pinnacle is reasonable (not likely mismatch)
        - Edge is not suspiciously high (< 25%)

        Args:
            vb: The value bet to assess
            threshold: Fuzzy match threshold (not used currently, reserved for future)

        Returns:
            True if high confidence, False otherwise
        """
        # Suspiciously high edge = likely data quality issue
        if vb.edge_pct > 25:
            return False

        # Odds ratio check (already filtered in scan_value, but double-check)
        if vb.fair_odds > 0:
            odds_ratio = vb.provider_odds / vb.fair_odds
            if odds_ratio > MAX_ODDS_RATIO:
                return False

        return True

    def scan_bonus(
        self,
        anchor_provider: str,
        devig: bool = True,
    ) -> list[BonusOpportunity]:
        """
        Find ALL opportunities for bonus clearing at anchor provider.

        Unlike value scan, this has NO edge threshold - returns all matches
        sorted by edge (best first, including negative edges).

        Uses Pinnacle as the sole sharp source for fair odds.

        Args:
            anchor_provider: Provider where bonus bet must be placed
            devig: Whether to de-vig Pinnacle odds (default True)

        Returns:
            List of BonusOpportunity sorted by edge (highest first, negatives last)
        """
        opportunities = []

        # Get events where anchor provider has odds
        events = self._get_events_with_provider(anchor_provider)

        for event in events:
            odds_grouped = self.group_odds(event)
            skip_spreads = event.sport in SET_SPREAD_SPORTS

            for market, odds_by_outcome in odds_grouped.items():
                if skip_spreads and market.startswith("spread"):
                    continue

                bonus_opps = self._find_bonus_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    anchor_provider=anchor_provider,
                    devig=devig,
                    all_markets=odds_grouped,
                )
                opportunities.extend(bonus_opps)

        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge_pct, reverse=True)

        logger.info(f"[Scanner] Found {len(opportunities)} bonus opportunities for {anchor_provider}")
        return opportunities

    def scan_arb(self, min_edge_pct: float = 0.0, events: list = None) -> list[ArbOpportunity]:
        """
        Find pure arbitrage opportunities: ALL legs +EV at different providers.

        Every outcome must beat Pinnacle fair odds (positive edge on every leg).
        These are the highest-quality cross-book opportunities.

        Args:
            min_edge_pct: Minimum combined edge % to include (default 0 = all)
            events: Pre-loaded events list (skips DB query if provided)

        Returns:
            List of ArbOpportunity sorted by guaranteed_profit_pct (highest first)
        """
        opportunities = []

        if events is None:
            events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self.group_odds(event)
            skip_spreads = event.sport in SET_SPREAD_SPORTS

            for market, odds_by_outcome in odds_grouped.items():
                if skip_spreads and market.startswith("spread"):
                    continue

                arb = self._find_arb_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    all_markets=odds_grouped,
                )
                if arb and arb.combined_edge_pct >= min_edge_pct:
                    # Arb: at least one soft +EV leg, others at fair odds (0% edge)
                    if any(leg["edge_pct"] > 0 and not leg["is_sharp"] for leg in arb.legs):
                        opportunities.append(arb)

        opportunities.sort(key=lambda x: x.guaranteed_profit_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} arb opportunities "
            f"({sum(1 for o in opportunities if o.guaranteed_profit_pct > 0)} guaranteed profit)"
        )
        return opportunities

    def scan_reverse(self, min_edge_pct: float = 0.0, events: list = None) -> list[ArbOpportunity]:
        """
        Find reverse arbitrage opportunities: at least one soft +EV leg, others covered.

        Unlike pure arb (all legs +EV), reverse arb has some legs at negative
        edge (typically covered by Pinnacle raw odds). Useful for reducing variance
        on strong single-leg value bets.

        Args:
            min_edge_pct: Minimum combined edge % to include (default 0 = all)
            events: Pre-loaded events list (skips DB query if provided)

        Returns:
            List of ArbOpportunity sorted by guaranteed_profit_pct (highest first)
        """
        opportunities = []

        if events is None:
            events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self.group_odds(event)
            skip_spreads = event.sport in SET_SPREAD_SPORTS

            for market, odds_by_outcome in odds_grouped.items():
                if skip_spreads and market.startswith("spread"):
                    continue

                arb = self._find_arb_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    all_markets=odds_grouped,
                )
                if arb and arb.combined_edge_pct >= min_edge_pct:
                    # Reverse: has at least one negative-edge leg
                    if any(leg["edge_pct"] <= 0 for leg in arb.legs):
                        opportunities.append(arb)

        opportunities.sort(key=lambda x: x.guaranteed_profit_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} reverse arb opportunities "
            f"({sum(1 for o in opportunities if o.guaranteed_profit_pct > 0)} guaranteed profit)"
        )
        return opportunities

    def scan_arb_for_provider(
        self,
        provider_id: str,
        counterpart_providers: list[str] | None = None,
    ) -> list[ArbOpportunity]:
        """
        Find arb opportunities where provider_id is forced as one of the legs.

        Unlike scan_arb(), this does NOT require +EV — returns all arbs including
        negative edge. Used by the arb workflow for balance draining.

        Args:
            provider_id: Provider to force into the arb (e.g. 'betinia')
            counterpart_providers: If set, only allow these providers for non-anchor legs

        Returns:
            List of ArbOpportunity sorted by combined_edge_pct (highest first)
        """
        opportunities = []

        events = self._get_events_with_provider(provider_id)

        for event in events:
            odds_grouped = self.group_odds(event)
            skip_spreads = event.sport in SET_SPREAD_SPORTS

            for market, odds_by_outcome in odds_grouped.items():
                if skip_spreads and market.startswith("spread"):
                    continue

                arb = self._find_arb_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    all_markets=odds_grouped,
                    anchor_provider=provider_id,
                    counterpart_providers=counterpart_providers,
                )
                if arb is None:
                    continue
                # Only include if the provider actually appears in a leg
                if any(leg["provider"] == provider_id for leg in arb.legs):
                    opportunities.append(arb)

        opportunities.sort(key=lambda x: x.combined_edge_pct, reverse=True)

        logger.info(f"[Scanner] Found {len(opportunities)} arb-workflow opportunities for {provider_id}")
        return opportunities

    def scan_reverse_value(self, min_edge_pct: float = 2.0, events: list = None) -> list[ValueBet]:
        """
        Find reverse value bets: Pinnacle odds beating soft book consensus.

        Uses platform-weighted harmonic mean of devigged soft books as
        the fair odds source. Considers 1x2, moneyline, spread, and total
        markets where Pinnacle offers odds >= MIN_REVERSE_ODDS.

        Requires MIN_CONSENSUS_PLATFORMS independent pricing sources.

        Args:
            min_edge_pct: Minimum edge percentage (default 2%)
            events: Pre-loaded events list (skips DB query if provided)

        Returns:
            List of ValueBet (provider="pinnacle") sorted by edge
        """
        opportunities = []

        if events is None:
            events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self.group_odds(event)
            skip_spreads = event.sport in SET_SPREAD_SPORTS

            for market, odds_by_outcome in odds_grouped.items():
                if skip_spreads and market.startswith("spread"):
                    continue

                base_market = market.split("_")[0] if "_" in market else market
                if base_market not in ("1x2", "moneyline", "spread", "total"):
                    continue

                values = self.find_reverse_value_in_market(
                    event_id=event.id,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    min_edge_pct=min_edge_pct,
                    all_markets=odds_grouped,
                )
                opportunities.extend(values)

        opportunities.sort(key=lambda x: x.edge_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} reverse value bets (>={min_edge_pct}% edge, Pinnacle vs consensus)"
        )
        return opportunities

    def find_reverse_value_in_market(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        min_edge_pct: float,
        all_markets: dict[str, dict[str, list[dict]]] = None,
    ) -> list[ValueBet]:
        """Find reverse value bets in a single market: Pinnacle raw vs soft consensus."""
        values = []

        # Need Pinnacle odds
        pinnacle_market = self._build_pinnacle_market(odds_by_outcome)

        # Spread complement lookup
        self._enrich_spread_complement(pinnacle_market, market, all_markets)

        if len(pinnacle_market) < 2:
            return []

        # Odds discrepancy check — exclude prediction markets (see find_value_in_market)
        if self._has_odds_discrepancy(odds_by_outcome, exclude_providers=set(PREDICTION_MARKETS), market=market):
            return []

        # Tighter freshness gate for soft-book consensus. group_odds already
        # dropped truly-stale rows (6x cadence), but for *consensus* — "where
        # is the market now?" — even 3-4h old browser-soft prices are just a
        # memory of where the book sat, not a current view. Use 3x cadence
        # here so single-cycle hiccups don't drop a book, but multi-cycle
        # gaps do. Pinnacle row keeps full window — its freshness is gated
        # by group_odds and the bet itself sits at Pinnacle.
        consensus_odds = self._filter_to_consensus_fresh(odds_by_outcome)

        # Outlier filter: drop providers whose price is >30% off the median.
        # Catches extraction bugs (over/under swaps, wrong-scope reads, dead
        # lines that didn't refresh past the freshness gate) that otherwise
        # pollute the consensus and surface phantom reverse-value edges —
        # the unibet hockey total bug being the canonical example.
        consensus_odds = self._drop_consensus_outliers(consensus_odds)

        for outcome in pinnacle_market:
            pin_raw = pinnacle_market[outcome]

            if pin_raw < MIN_REVERSE_ODDS or pin_raw > MAX_REVERSE_ODDS:
                continue

            # Compute consensus fair odds from soft book platforms (fresh only)
            consensus_result = compute_consensus_fair_odds(
                outcome=outcome,
                odds_by_outcome=consensus_odds,
                platform_map=PLATFORM_MAP,
                sharp_providers=SHARP_PROVIDERS,
                min_platforms=MIN_CONSENSUS_PLATFORMS,
            )

            if consensus_result is None:
                continue

            consensus_fair, n_platforms = consensus_result

            if consensus_fair <= 1:
                continue

            # Edge: Pinnacle raw odds vs consensus fair
            vb = find_value(
                event_id=event_id,
                market=market,
                outcome=outcome,
                provider="pinnacle",
                provider_odds=pin_raw,
                fair_odds=consensus_fair,
                min_edge_pct=min_edge_pct,
            )

            if vb and vb.edge_pct <= MAX_EDGE_PCT:
                vb.prob_sum = float(n_platforms)  # Store platform count
                values.append(vb)

        return values

    def _find_arb_in_market(
        self,
        event: Event,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
        anchor_provider: str | None = None,
        counterpart_providers: list[str] | None = None,
    ) -> ArbOpportunity | None:
        """
        Find an arb opportunity in a single market.

        Cross-book arb: uses best odds per outcome from ANY provider (soft or
        Pinnacle raw). Edge is always computed vs Pinnacle de-vigged fair odds.

        When anchor_provider is None: requires at least one soft +EV leg.
        When anchor_provider is set: forces that provider's odds even if below
        fair (negative edge), and relaxes the +EV requirement.
        When counterpart_providers is set: only considers those providers for
        non-anchor legs (restricts which books can be counterparts).
        """
        # Count outcomes per provider for market type mismatch detection
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)
        sharp_outcome_count = provider_outcome_counts.get("pinnacle", 0)

        # Pre-compute Pinnacle market dict (raw odds)
        pinnacle_market = self._build_pinnacle_market(odds_by_outcome)

        # Spread complement lookup
        self._enrich_spread_complement(pinnacle_market, market, all_markets)
        if len(pinnacle_market) > sharp_outcome_count:
            sharp_outcome_count = len(pinnacle_market)

        # 2026-05-27: prob-sum gate runs FIRST — catches sign-convention
        # residuals where the per-outcome disagreement gate has a blind spot.
        self._drop_spread_prob_sum_anomalies(market, odds_by_outcome)
        # 2026-05-26: spread disagreement gate — drop soft providers whose
        # devigged probability diverges from Pinnacle by >30pp on the same
        # outcome (catches quarter-handicap convention mismatches by symptom).
        self._drop_spread_disagreement_providers(market, odds_by_outcome, pinnacle_market)

        if sharp_outcome_count < 2:
            return None  # Need Pinnacle on 2+ outcomes for fair odds

        # Odds discrepancy check — exclude prediction markets (see find_value_in_market)
        if self._has_odds_discrepancy(odds_by_outcome, exclude_providers=set(PREDICTION_MARKETS), market=market):
            return None  # Skip entire market

        # For each outcome: find best odds (soft OR Pinnacle raw) and compute edge vs fair
        best_per_outcome = {}  # {outcome: {provider, odds, edge_pct, fair_odds, is_sharp}}
        soft_per_outcome = {}  # {outcome: {provider, odds, fair_odds}} — best soft for arb check
        # All valid soft candidates per outcome (ranked by odds desc) for conflict resolution
        soft_candidates = {}  # {outcome: [(odds, provider), ...]}
        fair_odds_map = {}  # {outcome: fair_odds}
        # Handicap/total line per outcome — same value across providers within
        # one (market, point) scan. Carried onto each leg so the UI can show
        # "Over 2.5" / "Team -1.5" and the user can verify both legs share a line.
        point_by_outcome = {out: (lst[0].get("point") if lst else None) for out, lst in odds_by_outcome.items()}

        for outcome, provider_odds_list in odds_by_outcome.items():
            fair_result = self._get_fair_odds(
                outcome=outcome,
                odds_by_outcome=odds_by_outcome,
                pinnacle_market=pinnacle_market,
            )
            if fair_result is None:
                continue

            fair_odds, _ = fair_result

            if fair_odds <= 1:
                continue

            fair_odds_map[outcome] = fair_odds

            # Get Pinnacle raw odds for this outcome
            pinnacle_raw = pinnacle_market.get(outcome, 0.0)

            # Collect ALL valid soft providers for this outcome (sorted best-first)
            candidates = []
            for po in provider_odds_list:
                if po["provider"] in SHARP_PROVIDERS:
                    continue
                if po["provider"] in SIGNAL_ONLY_PROVIDERS:
                    continue  # Signal-only — can't place bets
                # Counterpart filter: non-anchor providers must be in counterpart list
                if counterpart_providers and po["provider"] != anchor_provider:
                    if po["provider"] not in counterpart_providers:
                        continue
                # Market type mismatch check
                soft_count = provider_outcome_counts.get(po["provider"], 0)
                if soft_count > 0 and soft_count != sharp_outcome_count:
                    continue
                # Per-provider odds ratio vs Pinnacle raw
                if pinnacle_raw > 1:
                    ratio = po["odds"] / pinnacle_raw
                    if ratio > MAX_ODDS_RATIO:
                        continue
                if po["odds"] > 1:
                    candidates.append((po["odds"], po["provider"]))

            # Sort by odds descending (best first)
            candidates.sort(reverse=True)
            soft_candidates[outcome] = candidates

            # Best soft for initial greedy selection
            best_soft_odds = candidates[0][0] if candidates else 0.0
            best_soft_provider = candidates[0][1] if candidates else None

            # Save best soft for arb check (even if below fair)
            if best_soft_provider and best_soft_odds > 1:
                soft_per_outcome[outcome] = {
                    "provider": best_soft_provider,
                    "odds": best_soft_odds,
                    "fair_odds": round(fair_odds, 3),
                }

            # Use soft book if it beats fair odds; otherwise fall back to Pinnacle fair (0% edge)
            if best_soft_provider and best_soft_odds > fair_odds:
                best_odds = best_soft_odds
                best_provider = best_soft_provider
                is_sharp = False
            elif anchor_provider and best_soft_provider:
                # Anchor mode: keep -EV soft if it IS the anchor provider OR
                # if counterpart providers are set (force the pair, don't fall back to Pinnacle)
                if best_soft_provider == anchor_provider or counterpart_providers:
                    best_odds = best_soft_odds
                    best_provider = best_soft_provider
                    is_sharp = False
                else:
                    # Non-anchor soft below fair — use Pinnacle's RAW offered
                    # price (with vig), not the devigged fair_odds. The arb
                    # math must sum prices we can ACTUALLY bet at; using
                    # devigged fair prices produces synthetic arbs that
                    # vanish the moment you place because Pinnacle's actual
                    # offer carries the vig. (Pre-2026-05-10 this used
                    # fair_odds → every Pinnacle-fallback arb was a ghost.)
                    best_odds = pinnacle_raw if pinnacle_raw > 1 else fair_odds
                    best_provider = "pinnacle"
                    is_sharp = True
            else:
                # No soft book beats fair odds — use Pinnacle's RAW offered
                # price (see comment above for rationale).
                best_odds = pinnacle_raw if pinnacle_raw > 1 else fair_odds
                best_provider = "pinnacle"
                is_sharp = True

            edge_pct = (best_odds / fair_odds - 1) * 100

            # Skip suspicious edges (data quality)
            if abs(edge_pct) > MAX_EDGE_PCT:
                return None  # Entire market suspect

            best_per_outcome[outcome] = {
                "provider": best_provider,
                "odds": best_odds,
                "edge_pct": round(edge_pct, 2),
                "fair_odds": round(fair_odds, 3),
                "is_sharp": is_sharp,
            }

        # In constrained-provider mode: reject market if any leg fell back to
        # Pinnacle AND pinnacle is NOT in the user's counterpart_providers list.
        # is_sharp=True is only set when the leg was demoted to Pinnacle's fair
        # odds (lines 791-792, 796-797 above) — so when pinnacle is explicitly
        # included as an allowed counterpart, the fallback is the desired
        # behavior, not a violation. Bug-fix 2026-05-08: previous check was
        # `if counterpart_providers and any(is_sharp)` which rejected every
        # arb that used pinnacle as counter, even when the user had requested
        # pinnacle. Symptom: GET /api/opportunities/arb-workflow with
        # counterpart_providers=pinnacle,polymarket,... returned 0 results
        # (scanner found arbs, then this check zeroed them out). The
        # arb_runner had a Python-side workaround that dropped the URL filter;
        # the frontend's loadArbOpps did the same. Both can now drop the
        # workaround once this fix deploys.
        pinnacle_excluded = counterpart_providers and "pinnacle" not in counterpart_providers
        if pinnacle_excluded and any(d["is_sharp"] for d in best_per_outcome.values()):
            return None

        # ── Enforce max 1 soft outcome per canonical platform ──
        # Placing multiple outcomes on the same bookmaker flags the account.
        # Resolve conflicts: keep the higher-edge leg, demote the other to
        # its next-best provider from a different platform (or Pinnacle).
        self._resolve_platform_conflicts(
            best_per_outcome,
            soft_candidates,
            fair_odds_map,
            anchor_provider,
            pinnacle_market=pinnacle_market,
        )

        # Check again after conflict resolution (a demotion may have assigned Pinnacle)
        if pinnacle_excluded and any(d["is_sharp"] for d in best_per_outcome.values()):
            return None

        # Need all outcomes covered
        all_outcomes = list(best_per_outcome.keys())
        if len(all_outcomes) < 2:
            return None

        # Require at least one soft +EV leg (unless anchor mode)
        if not anchor_provider and not any(
            data["edge_pct"] > 0 and not data["is_sharp"] for data in best_per_outcome.values()
        ):
            return None

        # Require at least 2 different providers across all legs
        all_providers = set(data["provider"] for data in best_per_outcome.values())
        if len(all_providers) < 2:
            return None

        # Arb calculation: stake all outcomes, guaranteed return = 1/sum(1/odds)
        total_inv_sum = sum(1.0 / best_per_outcome[out]["odds"] for out in all_outcomes)
        guaranteed_return_per_unit = 1.0 / total_inv_sum
        guaranteed_profit_pct = round((guaranteed_return_per_unit - 1) * 100, 2)

        # Per-leg stake percentages (how much of total stake goes to each leg)
        legs = []
        for out in all_outcomes:
            data = best_per_outcome[out]
            stake_pct = round((1.0 / data["odds"]) / total_inv_sum * 100, 2)
            legs.append(
                {
                    "outcome": out,
                    "provider": data["provider"],
                    "odds": data["odds"],
                    "edge_pct": data["edge_pct"],
                    "fair_odds": data["fair_odds"],
                    "stake_pct": stake_pct,
                    "is_sharp": data["is_sharp"],
                    "point": point_by_outcome.get(out),
                    "currency": get_provider_currency(data["provider"]),
                }
            )

        # Sort legs: highest edge first
        legs.sort(key=lambda x: x["edge_pct"], reverse=True)

        # Combined edge = weighted average of individual edges (weighted by stake)
        total_stake_pct = sum(leg["stake_pct"] for leg in legs)
        combined_edge = (
            sum(leg["edge_pct"] * leg["stake_pct"] / total_stake_pct for leg in legs) if total_stake_pct > 0 else 0
        )

        # Arb check: can we cover all outcomes using only real bettable soft odds?
        # Also enforces platform uniqueness — no 2 arb legs on same canonical provider.
        arb_profit_pct = None
        arb_legs = None
        if all(out in soft_per_outcome for out in all_outcomes):
            # Resolve platform conflicts for arb legs too
            arb_soft = {out: dict(soft_per_outcome[out]) for out in all_outcomes}
            self._resolve_platform_conflicts_soft(arb_soft, soft_candidates, fair_odds_map)
            # After resolution some legs may have been removed (no alt provider)
            if all(out in arb_soft for out in all_outcomes):
                soft_providers = set(arb_soft[out]["provider"] for out in all_outcomes)
                if len(soft_providers) >= 2:
                    arb_sum = sum(1.0 / arb_soft[out]["odds"] for out in all_outcomes)
                    if arb_sum > 0:
                        arb_profit = round((1.0 / arb_sum - 1) * 100, 2)
                        if arb_profit > 0:
                            arb_profit_pct = arb_profit
                            # Build arb legs with proper stake percentages
                            arb_legs = []
                            for out in all_outcomes:
                                sdata = arb_soft[out]
                                arb_edge = round((sdata["odds"] / sdata["fair_odds"] - 1) * 100, 2)
                                arb_stake_pct = round((1.0 / sdata["odds"]) / arb_sum * 100, 2)
                                arb_legs.append(
                                    {
                                        "outcome": out,
                                        "provider": sdata["provider"],
                                        "odds": sdata["odds"],
                                        "edge_pct": arb_edge,
                                        "fair_odds": sdata["fair_odds"],
                                        "stake_pct": arb_stake_pct,
                                        "is_sharp": False,
                                        "point": point_by_outcome.get(out),
                                        "currency": get_provider_currency(sdata["provider"]),
                                    }
                                )
                            arb_legs.sort(key=lambda x: x["edge_pct"], reverse=True)

        # Sanity guard: a guaranteed profit this large is always a mispriced
        # leg (e.g. a Polymarket outcome priced off the wrong side of the
        # book), never a real arb. Drop the whole opportunity — its legs are
        # built on the same corrupt odds.
        if _implausible_arb_profit(guaranteed_profit_pct, arb_profit_pct):
            logger.debug(
                f"[arb] Dropping {event.id} {market}: implausible profit "
                f"(guaranteed={guaranteed_profit_pct}%, arb={arb_profit_pct}%) — mispriced leg"
            )
            return None

        return ArbOpportunity(
            event_id=event.id,
            market=market,
            legs=legs,
            combined_edge_pct=round(combined_edge, 2),
            guaranteed_profit_pct=guaranteed_profit_pct,
            arb_profit_pct=arb_profit_pct,
            arb_legs=arb_legs,
            home_team=event.home_team,
            away_team=event.away_team,
            sport=event.sport,
            league=event.league,
            start_time=(event.start_time.isoformat() + "Z") if event.start_time else None,
        )

    def get_multi_provider_events(self, min_providers: int = 2) -> list[Event]:
        """Get events with odds from N+ providers (public for pre-loading)."""
        return self.event_repo.get_multi_provider_events(min_providers)

    # Keep private alias for backward compatibility
    _get_multi_provider_events = get_multi_provider_events

    def _get_events_with_provider(self, provider_id: str) -> list[Event]:
        """Get events where a specific provider has odds."""
        return self.event_repo.get_events_with_provider(provider_id)

    @staticmethod
    def _canonical(provider: str) -> str:
        """Return the canonical platform for a provider (or itself if standalone).

        Uses PLATFORM_MAP (underlying odds engine) for conflict detection,
        NOT PROVIDER_CANONICAL (extraction consolidation groups).
        e.g. dbet and betinia are both 'altenar' even though dbet is extracted
        separately — arbing across them is pointless.
        """
        return PLATFORM_MAP.get(provider, provider)

    def _resolve_platform_conflicts(
        self,
        best_per_outcome: dict,
        soft_candidates: dict,
        fair_odds_map: dict,
        anchor_provider: str | None,
        pinnacle_market: dict | None = None,
    ) -> None:
        """Ensure no two soft legs share the same canonical platform.

        Mutates *best_per_outcome* in place.  For each collision the leg with
        lower edge is demoted to the next-best provider from a *different*
        canonical platform (or Pinnacle fair if none available).
        """
        # Group soft outcomes by canonical platform
        platform_outcomes: dict[str, list[str]] = defaultdict(list)
        for outcome, data in best_per_outcome.items():
            if data["is_sharp"]:
                continue
            canon = self._canonical(data["provider"])
            platform_outcomes[canon].append(outcome)

        for canon, outcomes in platform_outcomes.items():
            if len(outcomes) < 2:
                continue
            # Sort by edge descending — keep the best leg, demote the rest
            outcomes.sort(key=lambda o: best_per_outcome[o]["edge_pct"], reverse=True)
            # Canonical platforms already claimed by kept legs
            used_canonicals = {self._canonical(d["provider"]) for d in best_per_outcome.values() if not d["is_sharp"]}
            for loser_outcome in outcomes[1:]:
                fair_odds = fair_odds_map[loser_outcome]
                replaced = False
                for alt_odds, alt_prov in soft_candidates.get(loser_outcome, []):
                    alt_canon = self._canonical(alt_prov)
                    if alt_canon in used_canonicals:
                        continue
                    # Found a provider on a different platform
                    alt_edge = (alt_odds / fair_odds - 1) * 100
                    if abs(alt_edge) > MAX_EDGE_PCT:
                        continue
                    best_per_outcome[loser_outcome] = {
                        "provider": alt_prov,
                        "odds": alt_odds,
                        "edge_pct": round(alt_edge, 2),
                        "fair_odds": round(fair_odds, 3),
                        "is_sharp": False,
                    }
                    used_canonicals.add(alt_canon)
                    replaced = True
                    break
                if not replaced:
                    # No alternative soft — fall back to Pinnacle's RAW
                    # offered price (with vig). Using devigged fair_odds
                    # produced synthetic arbs that vanished on placement;
                    # see the comment in find_arb_in_market for the rationale.
                    pinn_raw = (pinnacle_market or {}).get(loser_outcome, 0.0)
                    leg_odds = pinn_raw if pinn_raw > 1 else fair_odds
                    leg_edge = (leg_odds / fair_odds - 1) * 100 if fair_odds > 0 else 0.0
                    best_per_outcome[loser_outcome] = {
                        "provider": "pinnacle",
                        "odds": leg_odds,
                        "edge_pct": leg_edge,
                        "fair_odds": round(fair_odds, 3),
                        "is_sharp": True,
                    }

    def _resolve_platform_conflicts_soft(
        self,
        soft_map: dict,
        soft_candidates: dict,
        fair_odds_map: dict,
    ) -> None:
        """Like _resolve_platform_conflicts but for soft-only arb map.

        Mutates *soft_map* in place.  Removes outcomes that can't be resolved
        (no alternative soft on a different platform).
        """
        platform_outcomes: dict[str, list[str]] = defaultdict(list)
        for outcome, data in soft_map.items():
            canon = self._canonical(data["provider"])
            platform_outcomes[canon].append(outcome)

        for canon, outcomes in platform_outcomes.items():
            if len(outcomes) < 2:
                continue
            # Keep highest-odds leg for arb
            outcomes.sort(key=lambda o: soft_map[o]["odds"], reverse=True)
            used_canonicals = {self._canonical(d["provider"]) for d in soft_map.values()}
            for loser_outcome in outcomes[1:]:
                fair_odds = fair_odds_map.get(loser_outcome, 0)
                replaced = False
                for alt_odds, alt_prov in soft_candidates.get(loser_outcome, []):
                    alt_canon = self._canonical(alt_prov)
                    if alt_canon in used_canonicals:
                        continue
                    soft_map[loser_outcome] = {
                        "provider": alt_prov,
                        "odds": alt_odds,
                        "fair_odds": round(fair_odds, 3) if fair_odds else 0,
                    }
                    used_canonicals.add(alt_canon)
                    replaced = True
                    break
                if not replaced:
                    del soft_map[loser_outcome]

    def group_odds(
        self,
        event: Event,
        exclude_providers: set[str] = None,
        check_staleness: bool = True,
        scope: str | None = None,
    ) -> dict[str, dict[str, list[dict]]]:
        """
        Group event odds by market -> outcome -> provider list.

        For spread markets, detects providers that store 2 outcomes at the same
        point (Asian-style: home@-0.2 and away@-0.2 are separate markets) vs
        Pinnacle's convention (1 outcome per point side). When detected, relocates
        the "wrong-side" outcome to its correct market key so comparisons are valid.

        Args:
            event: The event to group odds for
            exclude_providers: Set of provider IDs to exclude (default: empty set)
            check_staleness: Skip odds older than the provider's per-cadence
                staleness window — see constants.staleness_minutes_for()
                (default: True)
            scope: Which scope to filter odds to. When None (default), uses
                canonical_scope_for(event.sport) — preserves prior behaviour.
                Callers that want to scan period markets (e.g. baseball F5)
                iterate scannable_scopes_for(sport) and pass each value here.

        Returns:
            {
                "1x2": {
                    "home": [{"provider": "unibet", "odds": 2.10}, ...],
                    "draw": [...],
                    "away": [...]
                }
            }
        """
        if exclude_providers is None:
            exclude_providers = frozenset()

        grouped = defaultdict(lambda: defaultdict(list))

        # 2026-05-26: skip events where the home/away inversion check did
        # not resolve cleanly. Only skip when EXPLICITLY False — None means
        # an unflushed Python object (test fixtures) or a pre-migration row;
        # both column default + server_default make True the real default.
        if getattr(event, "home_away_validated", None) is False:
            logger.debug(
                "home_away_unvalidated: drop %s (sport=%s)",
                event.id,
                getattr(event, "sport", None),
            )
            return {}

        # Per-provider staleness cutoffs — tied to each provider's extraction
        # cadence. A betinia row 30+ min stale almost certainly means the
        # bookmaker pulled the listing (10 missed 3-min cycles); a comeon row
        # 30 min stale is normal (25-min cadence). The old global 2-h gate
        # let dropped-event rows pair against fresh Pinnacle for up to two
        # hours as ghost arbs. See constants.staleness_minutes_for().
        now = datetime.now(UTC)

        target_scope = scope if scope is not None else canonical_scope_for(getattr(event, "sport", None))

        for odds in event.odds:
            # Scope filter: only rows at the target scope participate. Cross-scope
            # comparisons (e.g. regulation vs OT-inclusive hockey totals, or full
            # game vs F5 baseball) are structurally invalid — refusing to group
            # them prevents false arbs like the 2026-05-25 IIHF bug.
            row_scope = getattr(odds, "scope", None) or "ft"
            if row_scope != target_scope:
                logger.debug(
                    "scope_filter: drop %s/%s scope=%s (target=%s for sport=%s)",
                    event.id,
                    odds.provider_id,
                    row_scope,
                    target_scope,
                    getattr(event, "sport", None),
                )
                continue

            # Skip excluded providers
            if odds.provider_id in exclude_providers:
                continue

            # Skip stale odds — threshold is per-provider, based on extraction cadence.
            if check_staleness and odds.updated_at:
                # Handle naive datetime (assume UTC)
                updated = odds.updated_at
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                cutoff = now - timedelta(minutes=staleness_minutes_for(odds.provider_id))
                if updated < cutoff:
                    logger.debug(
                        f"Skipping stale odds for {event.id}/{odds.provider_id}: "
                        f"updated {updated.isoformat()} (cutoff {cutoff.isoformat()})"
                    )
                    continue

            # Normalize outcome labels: 10bet stores "Home +4.5" / "Away -4.5"
            # and "Over 163.5" / "Under 163.5" instead of "home"/"away"/"over"/"under".
            outcome = odds.outcome
            outcome_lower = outcome.lower()
            if outcome_lower.startswith("home"):
                outcome = "home"
            elif outcome_lower.startswith("away"):
                outcome = "away"
            elif outcome_lower.startswith("draw"):
                outcome = "draw"
            elif outcome_lower.startswith("over"):
                outcome = "over"
            elif outcome_lower.startswith("under"):
                outcome = "under"

            # Create market key. Spread markets are keyed by the LINE — the
            # home-team handicap — NOT the raw per-outcome point. A spread row
            # stores its OWN side's handicap in `point`: `home@P` belongs to
            # line P, `away@P` to line -P (the away team getting +P means the
            # home team is at -P). Keying by raw point would file `home@-3.5`
            # (e.g. Spurs -3.5) and another book's `away@-3.5` (Thunder -3.5 —
            # which is the Spurs +3.5 line) under one key, letting the scanner
            # value-compare two physically different bets. Keying by line keeps
            # both sides of one line together and never merges two lines.
            # (point preserved on each entry below; not used for 1x2/moneyline.)
            if odds.point is None:
                market_key = odds.market
            elif odds.market == "spread" and outcome in ("home", "away"):
                line = odds.point if outcome == "home" else -odds.point
                if line == 0:
                    line = 0.0  # normalise -0.0 so home@0 and away@0 share a key
                market_key = f"spread_{line}"
            else:
                market_key = f"{odds.market}_{odds.point}"

            grouped[market_key][outcome].append(
                {
                    "provider": odds.provider_id,
                    "odds": odds.odds,
                    "point": odds.point,
                    "updated_at": odds.updated_at,
                    "bid": odds.bid,
                    "ask": odds.ask,
                }
            )

        # Fix Asian-style spread providers that store 2 outcomes at the same point.
        # Pinnacle convention: home at spread_-X, away at spread_+X (1 outcome per key).
        # Some providers (e.g. Kambi) store both home@-X and away@-X as separate Asian
        # lines. Detect via implied probability sum < 100% and relocate the "away"
        # outcome to the complement point where it belongs.
        self._fix_asian_spread_grouping(grouped)

        return dict(grouped)

    def _fix_asian_spread_grouping(self, grouped: dict[str, dict[str, list[dict]]]) -> None:
        """
        Drop 3-way European handicap providers from spread markets.

        group_odds keys spread odds by LINE (home handicap), so every provider's
        home/away entries under one key already describe the same physical 2-way
        line. No odds-proximity relabeling is needed — and it was actively
        harmful: the old heuristic re-labeled a book's outcome to match
        Pinnacle's by price closeness, which mis-filed Polymarket's genuine
        opposite-team alternate lines (e.g. "Thunder -3.5") as the wrong team.

        European handicap (home/draw/away at integer points) IS a fundamentally
        different market: the draw outcome absorbs probability, so comparing it
        against 2-way Asian handicap produces false edges. Any provider quoting
        a `draw` outcome in a spread market is removed.

        This mutates the grouped dict in-place.
        """
        # Remove 3-way European handicap providers — EVENT-WIDE. The away leg
        # of a 3-way handicap (sign-flipped to a different market_key under
        # the scanner's keying) leaks past per-key drops. Collect every
        # provider that emits `draw` in ANY spread_* key, then strip them
        # from ALL spread keys on this event.
        spread_keys = [k for k in grouped if k.startswith("spread_")]
        european_providers: set[str] = set()
        for market_key in spread_keys:
            draw_entries = grouped[market_key].get("draw") or []
            european_providers.update(e["provider"] for e in draw_entries)
        if european_providers:
            for market_key in spread_keys:
                odds_by_outcome = grouped[market_key]
                for outcome_type in list(odds_by_outcome.keys()):
                    odds_by_outcome[outcome_type] = [
                        e for e in odds_by_outcome[outcome_type] if e["provider"] not in european_providers
                    ]
                    if not odds_by_outcome[outcome_type]:
                        del odds_by_outcome[outcome_type]
            logger.debug(f"Removed 3-way European handicap providers {european_providers} from all spread_* keys")

    def _drop_spread_prob_sum_anomalies(
        self,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
    ) -> None:
        """Drop soft providers whose home+away implied prob sum on this spread
        market_key falls outside [SPREAD_PROB_SUM_MIN, SPREAD_PROB_SUM_MAX].

        Catches sign-convention residuals where two opposite physical bets
        land in one key (sum << 1.0) or two same-side bets duplicate
        (sum >> 1.0) — bugs the per-outcome 30pp gate can miss when both
        normalized halves stay within tolerance. Per-provider, both legs must
        be present (single-leg providers skip this check; pinnacle never
        dropped).
        """
        if not market.startswith("spread"):
            return

        # Index entries by provider so we can compute home+away sums per provider.
        per_provider: dict[str, dict[str, float]] = defaultdict(dict)
        for outcome, providers in odds_by_outcome.items():
            for po in providers:
                pid = po["provider"]
                if pid == "pinnacle" or pid in SIGNAL_ONLY_PROVIDERS:
                    continue
                if outcome not in ("home", "away"):
                    continue
                odds = po.get("odds", 0)
                if odds and odds > 1:
                    per_provider[pid][outcome] = 1.0 / odds

        dropped: set[str] = set()
        for pid, sides in per_provider.items():
            if "home" not in sides or "away" not in sides:
                continue
            prob_sum = sides["home"] + sides["away"]
            if prob_sum < SPREAD_PROB_SUM_MIN or prob_sum > SPREAD_PROB_SUM_MAX:
                dropped.add(pid)
                logger.debug(
                    "spread_prob_sum: drop %s from %s (sum=%.3f outside [%.2f, %.2f])",
                    pid,
                    market,
                    prob_sum,
                    SPREAD_PROB_SUM_MIN,
                    SPREAD_PROB_SUM_MAX,
                )

        if not dropped:
            return
        for outcome in list(odds_by_outcome.keys()):
            odds_by_outcome[outcome] = [po for po in odds_by_outcome[outcome] if po["provider"] not in dropped]
            if not odds_by_outcome[outcome]:
                del odds_by_outcome[outcome]

    def _drop_spread_disagreement_providers(
        self,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        pinnacle_market: dict[str, float],
    ) -> None:
        """For each outcome in a spread market, drop soft providers whose
        devigged probability disagrees with Pinnacle's by >SPREAD_DISAGREEMENT_MAX_PP.
        Mutates odds_by_outcome in place. Non-spread markets are no-ops."""
        if not market.startswith("spread"):
            return

        # Pinnacle devig per outcome
        total_pinnacle_inv = sum(1.0 / o for o in pinnacle_market.values() if o > 1)
        if total_pinnacle_inv <= 0:
            return
        pinnacle_devig = {out: (1.0 / odds) / total_pinnacle_inv for out, odds in pinnacle_market.items() if odds > 1}

        # For each soft provider, compute their devig per outcome
        soft_devig: dict[str, dict[str, float]] = {}
        for outcome, providers in odds_by_outcome.items():
            for po in providers:
                if po["provider"] == "pinnacle" or po["provider"] in SIGNAL_ONLY_PROVIDERS:
                    continue
                soft_devig.setdefault(po["provider"], {})[outcome] = 1.0 / po["odds"]

        # Normalize each soft provider's devig (sum to 1)
        for _prov, devig in soft_devig.items():
            total = sum(devig.values())
            if total <= 0:
                continue
            for outcome in list(devig.keys()):
                devig[outcome] = devig[outcome] / total

        # Drop providers whose devig differs from Pinnacle by > threshold on any outcome
        dropped = set()
        for prov, devig in soft_devig.items():
            for outcome, soft_p in devig.items():
                pinnacle_p = pinnacle_devig.get(outcome)
                if pinnacle_p is None:
                    continue
                if abs(soft_p - pinnacle_p) > SPREAD_DISAGREEMENT_MAX_PP:
                    dropped.add(prov)
                    logger.debug(
                        "spread_disagreement: drop %s from %s (outcome=%s, soft_p=%.2f, sharp_p=%.2f)",
                        prov,
                        market,
                        outcome,
                        soft_p,
                        pinnacle_p,
                    )
                    break

        # Mutate odds_by_outcome to remove dropped providers
        for outcome in list(odds_by_outcome.keys()):
            odds_by_outcome[outcome] = [po for po in odds_by_outcome[outcome] if po["provider"] not in dropped]
            if not odds_by_outcome[outcome]:
                del odds_by_outcome[outcome]

    def _count_outcomes_per_provider(self, odds_by_outcome: dict[str, list[dict]]) -> dict[str, int]:
        """
        Count how many outcomes each provider has in this market.

        Used to detect market type mismatches (e.g., 3-way 1x2 vs 2-way moneyline).

        Returns:
            {provider_id: outcome_count}
        """
        provider_outcomes = defaultdict(set)

        for outcome, provider_list in odds_by_outcome.items():
            for po in provider_list:
                provider_outcomes[po["provider"]].add(outcome)

        return {p: len(outcomes) for p, outcomes in provider_outcomes.items()}

    def find_value_in_market(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        min_edge_pct: float,
        all_markets: dict[str, dict[str, list[dict]]] = None,
    ) -> list[ValueBet]:
        """Find value bets in a single market using Pinnacle as sharp source."""
        values = []

        # Count outcomes per provider to detect market type mismatch
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)

        # Find Pinnacle's outcome count (sole sharp source)
        sharp_outcome_count = provider_outcome_counts.get("pinnacle", 0)

        # Pre-compute Pinnacle market dict once for this market (used by _get_fair_odds)
        pinnacle_market = self._build_pinnacle_market(odds_by_outcome)

        # Spread complement lookup: Pinnacle stores home at +X and away at -X
        # (Asian handicap style) under different market keys. Soft providers store
        # both outcomes under the same point. Reconstruct the full 2-way Pinnacle
        # market so we can properly de-vig instead of using raw odds.
        self._enrich_spread_complement(pinnacle_market, market, all_markets)
        # Update sharp count to reflect enriched market
        if len(pinnacle_market) > sharp_outcome_count:
            sharp_outcome_count = len(pinnacle_market)

        # 2026-05-27: prob-sum gate runs FIRST — catches sign-convention
        # residuals where the per-outcome disagreement gate has a blind spot.
        self._drop_spread_prob_sum_anomalies(market, odds_by_outcome)
        # 2026-05-26: spread disagreement gate — drop soft providers whose
        # devigged probability diverges from Pinnacle by >30pp on the same
        # outcome (catches quarter-handicap convention mismatches by symptom).
        self._drop_spread_disagreement_providers(market, odds_by_outcome, pinnacle_market)

        # Compute Pinnacle overround for ML features
        pinnacle_overround = (sum(1.0 / o for o in pinnacle_market.values() if o > 1) - 1.0) if pinnacle_market else 0

        # Pre-compute probability sums per soft provider (used in completeness check)
        soft_prob_sums = defaultdict(float)
        for _out, providers in odds_by_outcome.items():
            for p in providers:
                if p["provider"] not in SHARP_PROVIDERS:
                    soft_prob_sums[p["provider"]] += 1.0 / p["odds"]

        # Check for odds discrepancy (likely event mismatch)
        # Exclude prediction markets (Polymarket, Kalshi) from ratio calc — their
        # pricing naturally diverges from traditional sportsbooks (binary contracts,
        # underdog longshots, illiquidity-driven floor/ceiling prints).
        if self._has_odds_discrepancy(odds_by_outcome, exclude_providers=set(PREDICTION_MARKETS), market=market):
            return []  # Skip entire market if any outcome has high discrepancy

        for outcome, provider_odds_list in odds_by_outcome.items():
            # Get fair odds from de-vigged Pinnacle (using pre-computed market dict)
            fair_result = self._get_fair_odds(
                outcome=outcome,
                odds_by_outcome=odds_by_outcome,
                pinnacle_market=pinnacle_market,
            )

            if fair_result is None:
                continue

            fair_odds, fair_source = fair_result

            # Direct provider-vs-Pinnacle odds ratio check (works with any provider count)
            pinnacle_raw = pinnacle_market.get(outcome)

            # Check each soft provider
            for po in provider_odds_list:
                if po["provider"] in SHARP_PROVIDERS:
                    continue  # Don't compare sharp vs sharp

                if po["provider"] in SIGNAL_ONLY_PROVIDERS:
                    continue  # Signal-only — used for consensus, can't place bets

                # Skip Polymarket for per-map markets — prediction markets only
                # meaningfully price overall match outcomes, not individual maps
                if po["provider"] == "polymarket" and "_m" in market:
                    continue

                # Skip if market types don't match (different outcome counts)
                # Exceptions:
                # 1. Spread markets — Pinnacle stores 1 outcome per point (Asian),
                #    soft providers store 2 (home+away). 3-way European spreads excluded.
                # 2. Polymarket binary markets — always 2-way (yes/no = home/away),
                #    but football maps to 3-way 1x2 at Pinnacle. De-vigged fair odds
                #    for each outcome are still valid from the full 3-way market.
                soft_count = provider_outcome_counts.get(po["provider"], 0)
                if soft_count > 0 and sharp_outcome_count > 0:
                    is_spread_asymmetry = (
                        market.startswith("spread") and sharp_outcome_count in (1, 2) and soft_count in (1, 2)
                    )
                    is_polymarket_mismatch = po["provider"] == "polymarket" and soft_count <= sharp_outcome_count
                    if soft_count != sharp_outcome_count and not is_spread_asymmetry and not is_polymarket_mismatch:
                        continue  # Don't compare 3-way vs 2-way markets

                # Validate soft provider's market completeness (pre-computed)
                # Skip for prediction markets (Polymarket, Kalshi) — binary contracts
                # and single-side-of-book quoting routinely produce prob_sum < 0.90.
                # Skip for spread markets — after Asian spread fix, each point has only 1
                # outcome per provider (prob_sum ~0.5), which is expected. The complement
                # lookup already reconstructs the full Pinnacle market for proper de-vigging.
                is_spread_market = market.startswith("spread")
                if soft_prob_sums.get(po["provider"], 0) < MIN_VALID_PROB_SUM:
                    if po["provider"] not in PREDICTION_MARKETS and not is_spread_market:
                        continue  # Incomplete market at soft provider

                # Per-provider odds ratio vs Pinnacle raw (catches bad odds even with 1 soft provider)
                # Spread/total markets use relaxed threshold — handicap pricing
                # naturally diverges more between books than 1x2/moneyline
                if pinnacle_raw and pinnacle_raw > 1:
                    ratio = po["odds"] / pinnacle_raw
                    ratio_limit = MAX_ODDS_RATIO_SPREAD if is_spread_market else MAX_ODDS_RATIO
                    if ratio > ratio_limit:
                        logger.debug(
                            f"Skipping {po['provider']} {event_id} {market} {outcome}: "
                            f"odds {po['odds']:.2f} vs Pinnacle {pinnacle_raw:.2f} "
                            f"(ratio {ratio:.2f} > {ratio_limit})"
                        )
                        continue

                vb = find_value(
                    event_id=event_id,
                    market=market,
                    outcome=outcome,
                    provider=po["provider"],
                    provider_odds=po["odds"],
                    fair_odds=fair_odds,
                    min_edge_pct=min_edge_pct,
                    bid=po.get("bid"),
                    ask=po.get("ask"),
                )
                if vb:
                    vb.odds_updated_at = (po.get("updated_at").isoformat() + "Z") if po.get("updated_at") else None
                    # Hard cap: edges above MAX_EDGE_PCT are data quality issues
                    if vb.edge_pct > MAX_EDGE_PCT:
                        logger.debug(
                            f"Skipping suspicious value {vb.edge_pct:+.1f}% for "
                            f"{event_id} {market} {outcome} ({po['provider']})"
                        )
                        continue
                    # Attach ML feature data
                    vb.prob_sum = soft_prob_sums.get(po["provider"], 0)
                    vb.pinnacle_overround = pinnacle_overround
                    vb.odds_snapshot = [
                        {"provider": p["provider"], "odds": p["odds"], "updated_at": str(p.get("updated_at", ""))}
                        for p in provider_odds_list
                    ]

                    # ML edge quality check (M1) — best-effort additional filter
                    try:
                        from src.ml.serving.predictor import get_predictor

                        predictor = get_predictor()
                        if predictor.is_loaded("edge_quality"):
                            from src.ml.features.betting_features import extract_betting_features

                            _ml_features = extract_betting_features(
                                edge_pct=vb.edge_pct,
                                provider_odds=vb.provider_odds,
                                fair_odds=fair_odds,
                                fair_probability=vb.fair_probability,
                                provider=po["provider"],
                                sport="",
                                market=market,
                                event_id=event_id,
                                prob_sum=vb.prob_sum or 0,
                                odds_by_outcome=odds_by_outcome,
                                pinnacle_overround=pinnacle_overround,
                                event_start_time=None,
                                point=vb.point,
                            )
                            ml_prob = predictor.predict("edge_quality", _ml_features)
                            if ml_prob is not None and ml_prob < 0.5:
                                continue  # ML says edge is likely noise
                    except Exception:
                        pass

                    values.append(vb)

        return values

    def _find_bonus_in_market(
        self,
        event: Event,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        anchor_provider: str,
        devig: bool,
        all_markets: dict[str, dict[str, list[dict]]] = None,
    ) -> list[BonusOpportunity]:
        """Find bonus opportunities in a single market using Pinnacle as sharp source."""
        opportunities = []

        # Count outcomes per provider to detect market type mismatch
        # (e.g., 3-way 1x2 vs 2-way moneyline)
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)
        anchor_outcome_count = provider_outcome_counts.get(anchor_provider, 0)

        # Find Pinnacle's outcome count (sole sharp source)
        sharp_outcome_count = provider_outcome_counts.get("pinnacle", 0)

        # Pre-compute Pinnacle market dict for ratio checks
        pinnacle_market = self._build_pinnacle_market(odds_by_outcome)

        # Spread complement lookup (same as find_value_in_market)
        self._enrich_spread_complement(pinnacle_market, market, all_markets)
        if len(pinnacle_market) > sharp_outcome_count:
            sharp_outcome_count = len(pinnacle_market)

        # Skip if market types don't match (different outcome counts)
        # Exception: spread markets where Pinnacle stores 1 outcome per point
        if anchor_outcome_count > 0 and sharp_outcome_count > 0:
            is_spread_asymmetry = (
                market.startswith("spread") and sharp_outcome_count == 1 and anchor_outcome_count in (2, 3)
            )
            if anchor_outcome_count != sharp_outcome_count and not is_spread_asymmetry:
                return []  # Don't compare 3-way vs 2-way markets

        # Check for odds discrepancy — exclude prediction markets (see find_value_in_market)
        if self._has_odds_discrepancy(odds_by_outcome, exclude_providers=set(PREDICTION_MARKETS), market=market):
            return []  # Skip market if likely event mismatch

        is_spread_market = market.startswith("spread") or market.startswith("total")

        for outcome, provider_odds_list in odds_by_outcome.items():
            # Find anchor provider odds for this outcome
            anchor_odds_entry = next(
                (p for p in provider_odds_list if p["provider"] == anchor_provider),
                None,
            )
            if anchor_odds_entry is None:
                continue

            anchor_odds = anchor_odds_entry["odds"]

            # Per-provider odds ratio vs Pinnacle raw
            pinnacle_raw = pinnacle_market.get(outcome)
            if pinnacle_raw and pinnacle_raw > 1:
                ratio = anchor_odds / pinnacle_raw
                ratio_limit = MAX_ODDS_RATIO_SPREAD if is_spread_market else MAX_ODDS_RATIO
                if ratio > ratio_limit:
                    logger.debug(
                        f"Skipping {anchor_provider} {event.id} {market} {outcome}: "
                        f"odds {anchor_odds:.2f} vs Pinnacle {pinnacle_raw:.2f} "
                        f"(ratio {ratio:.2f} > {ratio_limit})"
                    )
                    continue

            # Get fair odds from Pinnacle (de-vigged)
            fair_result = self._get_fair_odds(
                outcome=outcome,
                odds_by_outcome=odds_by_outcome,
                devig=devig,
            )

            if fair_result is None:
                continue

            fair_odds, fair_source = fair_result

            # Calculate edge (can be negative)
            if fair_odds <= 1 or anchor_odds <= 1:
                continue

            edge = (anchor_odds / fair_odds) - 1
            edge_pct = round(edge * 100, 2)

            # Sanity check: edges above MAX_EDGE_PCT are data quality issues
            if abs(edge_pct) > MAX_EDGE_PCT:
                logger.debug(
                    f"Skipping suspicious edge {edge_pct:+.1f}% for {event.id} "
                    f"{market} {outcome}: anchor={anchor_odds}, fair={fair_odds}"
                )
                continue

            opportunities.append(
                BonusOpportunity(
                    event_id=event.id,
                    market=market,
                    outcome=outcome,
                    anchor_provider=anchor_provider,
                    anchor_odds=anchor_odds,
                    fair_odds=round(fair_odds, 3),
                    fair_source=fair_source,
                    edge_pct=edge_pct,
                    home_team=event.home_team,
                    away_team=event.away_team,
                    sport=event.sport,
                    league=event.league,
                )
            )

        return opportunities

    def _build_pinnacle_market(self, odds_by_outcome: dict) -> dict:
        """Build {outcome: best_odds} dict from Pinnacle odds entries."""
        pinnacle_market = {}
        for outcome, odds_list in odds_by_outcome.items():
            for entry in odds_list:
                if entry["provider"] == "pinnacle":
                    pinnacle_market[outcome] = entry["odds"]
                    break
        return pinnacle_market

    def _filter_to_consensus_fresh(
        self,
        odds_by_outcome: dict[str, list[dict]],
    ) -> dict[str, list[dict]]:
        """Drop non-Pinnacle entries whose updated_at is older than the
        per-provider consensus cutoff. Only Pinnacle is exempt — it's the
        BET provider in reverse_value, not a consensus input, and its
        freshness is gated by group_odds upstream.

        Signal-only providers (marathon, stake, smarkets) ARE consensus
        inputs and must be gated like soft books. A 70-min-old marathon
        line is not "where the market is now," just a memory of where
        the book sat. The earlier bypass of signal-only providers was a
        bug — sharp-like pricing doesn't make a stale row current.

        Entries without an updated_at timestamp are kept (test fixtures
        and pre-migration rows); the broader placement-staleness gate
        already ran in group_odds.
        """
        now = datetime.now(UTC)
        filtered: dict[str, list[dict]] = {}
        for outcome, entries in odds_by_outcome.items():
            kept = []
            for entry in entries:
                pid = entry.get("provider", "")
                if pid in SHARP_PROVIDERS:
                    kept.append(entry)
                    continue
                updated = entry.get("updated_at")
                if updated is None:
                    kept.append(entry)
                    continue
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                cutoff = now - timedelta(minutes=consensus_staleness_minutes_for(pid))
                if updated < cutoff:
                    continue
                kept.append(entry)
            if kept:
                filtered[outcome] = kept
        return filtered

    def _drop_consensus_outliers(
        self,
        odds_by_outcome: dict[str, list[dict]],
    ) -> dict[str, list[dict]]:
        """Drop non-Pinnacle providers whose odds deviate >30% from the
        per-outcome median. Catches extraction errors (e.g. unibet hockey
        total returning over/under-swapped prices: 1.40 / 3.00 when the
        rest of the market is 2.10 / 1.70) that would otherwise pollute
        the consensus and surface phantom reverse-value edges.

        Outlier dropping is per-PROVIDER, not per-entry — if a provider's
        price is off-median on ANY outcome, that provider is removed from
        ALL outcomes in this market. The underlying assumption is a
        convention/extraction error affects the whole market for that
        provider, not just one side.

        Skipped when fewer than 3 non-sharp providers cover the outcome
        (median is unstable below that). Pinnacle is never dropped — it's
        the bet provider, its odds are the thing we're measuring drift
        against.
        """
        outlier_threshold = 0.30
        bad_providers: set[str] = set()

        for entries in odds_by_outcome.values():
            non_sharp = [e for e in entries if e.get("provider", "") not in SHARP_PROVIDERS]
            if len(non_sharp) < 3:
                continue
            odds_values = sorted(e["odds"] for e in non_sharp if e.get("odds", 0) > 1)
            if len(odds_values) < 3:
                continue
            mid = len(odds_values) // 2
            median = odds_values[mid] if len(odds_values) % 2 else (odds_values[mid - 1] + odds_values[mid]) / 2
            if median <= 0:
                continue
            for e in non_sharp:
                odds = e.get("odds", 0)
                if odds <= 0:
                    continue
                deviation = abs(odds - median) / median
                if deviation > outlier_threshold:
                    bad_providers.add(e["provider"])
                    logger.debug(
                        "consensus_outlier: drop %s (odds=%.2f, median=%.2f, dev=%.0f%%)",
                        e["provider"],
                        odds,
                        median,
                        deviation * 100,
                    )

        if not bad_providers:
            return odds_by_outcome

        filtered: dict[str, list[dict]] = {}
        for outcome, entries in odds_by_outcome.items():
            kept = [e for e in entries if e.get("provider", "") not in bad_providers]
            if kept:
                filtered[outcome] = kept
        return filtered

    def _enrich_spread_complement(
        self,
        pinnacle_market: dict,
        market: str,
        all_markets: dict,
    ) -> tuple[dict, bool]:
        """No-op: spread odds are keyed by LINE (home handicap) in group_odds.

        Both sides of a physical line — Pinnacle's `home@L` and `away@-L` — now
        co-locate under the same key (`spread_L`), so ``_build_pinnacle_market``
        already yields the full 2-way market and there is nothing to reconstruct.

        The old implementation pulled the missing outcome from `spread_{-L}` —
        which, under line keying, is the OPPOSITE line. That cross-line pull is
        exactly the bug that let a book's `away` odds on one handicap be
        value-compared against Pinnacle's fair for a different handicap.
        Kept as a stable hook for the call sites; returns ``pinnacle_market``
        unchanged.
        """
        return pinnacle_market, False

    def _has_odds_discrepancy(
        self,
        odds_by_outcome: dict,
        exclude_providers: set = None,
        market: str = "",
    ) -> bool:
        """
        Check if any outcome has odds ratio exceeding the threshold.

        Uses relaxed threshold for spread/total markets where books
        naturally diverge more on handicap pricing.

        Tolerates a single outlier: if removing one value brings the ratio
        below the threshold, it's a rogue provider (handled per-provider
        downstream), not a systemic event mismatch.

        Args:
            odds_by_outcome: {outcome: [provider_odds_entries]}
            exclude_providers: Provider IDs to exclude from the ratio calculation
            market: Market key (used to select appropriate ratio threshold)

        Returns:
            True if discrepancy found (market should be skipped)
        """
        is_spread = market.startswith("spread") or market.startswith("total")
        threshold = MAX_ODDS_RATIO_SPREAD if is_spread else MAX_ODDS_RATIO

        for _outcome, provider_odds_list in odds_by_outcome.items():
            if exclude_providers:
                odds_values = [po["odds"] for po in provider_odds_list if po["provider"] not in exclude_providers]
            else:
                odds_values = [po["odds"] for po in provider_odds_list]
            if len(odds_values) >= 3:
                odds_ratio = max(odds_values) / min(odds_values)
                if odds_ratio > threshold:
                    # Tolerate a single outlier: remove the value causing the
                    # biggest gap and re-check. If ratio falls below threshold,
                    # it's one rogue provider, not a market-wide mismatch.
                    sorted_vals = sorted(odds_values)
                    # Try removing the extreme high
                    trimmed_high = sorted_vals[:-1]
                    if max(trimmed_high) / min(trimmed_high) <= threshold:
                        continue
                    # Try removing the extreme low
                    trimmed_low = sorted_vals[1:]
                    if max(trimmed_low) / min(trimmed_low) <= threshold:
                        continue
                    return True
        return False

    def _get_fair_odds(
        self,
        outcome: str,
        odds_by_outcome: dict[str, list[dict]],
        devig: bool = True,
        pinnacle_market: dict[str, float] = None,
    ) -> tuple[float, str] | None:
        """
        Get fair odds for an outcome from Pinnacle (sole sharp source).

        Pinnacle's ~2.5% margin is removed using multiplicative de-vigging.

        Args:
            outcome: The outcome to get fair odds for
            odds_by_outcome: All market odds
            devig: Whether to de-vig (default True)
            pinnacle_market: Pre-computed {outcome: odds} dict for Pinnacle

        Returns:
            (fair_odds, "pinnacle") or None if Pinnacle not found
        """
        # Build Pinnacle market dict if not pre-computed
        if pinnacle_market is None:
            pinnacle_market = self._build_pinnacle_market(odds_by_outcome)

        pinnacle_odds = pinnacle_market.get(outcome)
        if pinnacle_odds is None:
            return None

        # De-vig Pinnacle if requested
        if devig:
            if len(pinnacle_market) >= 2:
                # Validate market completeness via probability sum
                prob_sum = sum(1.0 / o for o in pinnacle_market.values())
                if prob_sum < MIN_VALID_PROB_SUM:
                    # Incomplete market data - skip to avoid false signals
                    logger.debug(f"Skipping incomplete market: {outcome} prob_sum={prob_sum:.2f}")
                    return None

                fair_odds = get_fair_odds_for_outcome(outcome, pinnacle_market, method="multiplicative")
                return (fair_odds, "pinnacle")
            else:
                # Single outcome — can't de-vig with multiplicative method.
                # For spread markets (Pinnacle stores only home OR away per point),
                # use raw Pinnacle odds as fair baseline. Pinnacle's Asian handicap
                # lines carry ~2% margin on each side, so raw is a conservative
                # estimate (slightly underestimates true fair odds → fewer false positives).
                return (pinnacle_odds, "pinnacle(raw)")
        else:
            return (pinnacle_odds, "pinnacle(raw)")


# Quick test
if __name__ == "__main__":
    print("=== OpportunityScanner Test ===")
    print("Run with database session to test scanning functionality")
