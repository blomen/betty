"""
Opportunity Scanner

Unified scanning interface for finding betting opportunities:
- Value: Edge vs de-vigged sharp odds
- Bonus: Any edge for bonus clearing (no threshold)

This module queries the database and returns opportunity dataclasses.
Storage/persistence is handled by the caller (analyzer.py).
"""

from dataclasses import dataclass
from typing import Optional
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import logging
import math

from sqlalchemy.orm import Session

from ..db.models import Event, Odds
from ..repositories import EventRepo
from .value import find_value, ValueBet
from ..bankroll.stake_calculator import StakeCalculator
from .devig import (
    get_fair_odds_for_outcome,
    compute_consensus_fair_odds,
)
from ..constants import SHARP_PROVIDERS, PLATFORM_MAP

logger = logging.getLogger(__name__)

# Minimum probability sum for a valid market (accounts for margin)
# Normal markets: 1.02-1.10, incomplete markets: < 0.90
MIN_VALID_PROB_SUM = 0.90

# Maximum odds ratio for same outcome across providers
# If max/min > threshold, likely event mismatch or stale odds
# Real odds rarely differ more than 35% across providers for same event
MAX_ODDS_RATIO = 1.35
# Spread/total markets have wider natural variance between books (different
# handicap conventions, vig structures). Relaxed from 1.35 to 1.55.
MAX_ODDS_RATIO_SPREAD = 1.55

# Maximum edge percentage for a value bet to be considered valid
# Edges above this are almost certainly data quality issues (wrong odds, event mismatch)
# Real value bets rarely exceed 30% edge; 50% gives comfortable headroom
MAX_EDGE_PCT = 50.0

# Maximum odds age in hours for value scanning
# Odds older than this are considered stale and skipped
MAX_ODDS_AGE_HOURS = 2

# Reverse value: minimum independent platforms for consensus
MIN_CONSENSUS_PLATFORMS = 2

# Reverse value: minimum odds (mid-range underdogs and up)
MIN_REVERSE_ODDS = 2.50

# Reverse value: maximum odds to avoid extreme longshot noise
MAX_REVERSE_ODDS = 15.0


@dataclass
class DutchOpportunity:
    """A dutch betting opportunity: opposing outcomes both +EV at different providers."""

    event_id: str
    market: str

    # Each leg: {outcome, provider, odds, edge_pct, fair_odds, stake_pct}
    legs: list[dict]

    # Combined metrics
    combined_edge_pct: float       # Weighted average edge across legs
    guaranteed_profit_pct: float   # >0 = guaranteed profit regardless of outcome

    # Arb detection: true arb using only real bettable soft odds
    arb_profit_pct: Optional[float] = None  # >0 = true executable arb (all soft legs)
    arb_legs: Optional[list[dict]] = None   # All-soft version of legs (when arb_profit_pct > 0)

    # Event context
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    sport: Optional[str] = None
    league: Optional[str] = None
    start_time: Optional[str] = None


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
    point: Optional[float] = None

    # Event context
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    sport: Optional[str] = None
    league: Optional[str] = None


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
        removed using multiplicative de-vigging. Skips odds older than
        MAX_ODDS_AGE_HOURS (2 hours).

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

            for market, odds_by_outcome in odds_grouped.items():
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
            )
            # Log ML features (best-effort, never blocks scanning)
            try:
                from src.ml.features.betting_features import extract_betting_features
                from src.ml.feature_store import log_features
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
        logger.info(
            f"[Scanner] {actionable}/{len(enriched_bets)} value bets have stake recommendations"
        )

        return enriched_bets

    def _apply_routing_priority(self, bets: list[ValueBet]) -> list[ValueBet]:
        """
        Re-rank value bets using bankroll planner routing priority.

        When multiple bets have similar edges, prefer providers that need
        wagering progress. Uses a tiny continuous penalty so routing priority
        only acts as a tiebreaker — never overrides meaningful edge differences.
        """
        try:
            from ..services.planner_service import BankrollPlannerService
            from ..repositories import ProfileRepo

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

            for market, odds_by_outcome in odds_grouped.items():
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

        logger.info(
            f"[Scanner] Found {len(opportunities)} bonus opportunities for {anchor_provider}"
        )
        return opportunities

    def scan_dutch(self, min_edge_pct: float = 0.0, events: list = None) -> list[DutchOpportunity]:
        """
        Find pure dutch opportunities: ALL legs +EV at different providers.

        Every outcome must beat Pinnacle fair odds (positive edge on every leg).
        These are the highest-quality cross-book opportunities.

        Args:
            min_edge_pct: Minimum combined edge % to include (default 0 = all)
            events: Pre-loaded events list (skips DB query if provided)

        Returns:
            List of DutchOpportunity sorted by guaranteed_profit_pct (highest first)
        """
        opportunities = []

        if events is None:
            events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self.group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                dutch = self._find_dutch_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    all_markets=odds_grouped,
                )
                if dutch and dutch.combined_edge_pct >= min_edge_pct:
                    # Dutch: at least one soft +EV leg, others at fair odds (0% edge)
                    if any(leg["edge_pct"] > 0 and not leg["is_sharp"] for leg in dutch.legs):
                        opportunities.append(dutch)

        opportunities.sort(key=lambda x: x.guaranteed_profit_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} dutch opportunities "
            f"({sum(1 for o in opportunities if o.guaranteed_profit_pct > 0)} guaranteed profit)"
        )
        return opportunities

    def scan_reverse(self, min_edge_pct: float = 0.0, events: list = None) -> list[DutchOpportunity]:
        """
        Find reverse dutch opportunities: at least one soft +EV leg, others covered.

        Unlike pure dutch (all legs +EV), reverse dutch has some legs at negative
        edge (typically covered by Pinnacle raw odds). Useful for reducing variance
        on strong single-leg value bets.

        Args:
            min_edge_pct: Minimum combined edge % to include (default 0 = all)
            events: Pre-loaded events list (skips DB query if provided)

        Returns:
            List of DutchOpportunity sorted by guaranteed_profit_pct (highest first)
        """
        opportunities = []

        if events is None:
            events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self.group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                dutch = self._find_dutch_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    all_markets=odds_grouped,
                )
                if dutch and dutch.combined_edge_pct >= min_edge_pct:
                    # Reverse: has at least one negative-edge leg
                    if any(leg["edge_pct"] <= 0 for leg in dutch.legs):
                        opportunities.append(dutch)

        opportunities.sort(key=lambda x: x.guaranteed_profit_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} reverse dutch opportunities "
            f"({sum(1 for o in opportunities if o.guaranteed_profit_pct > 0)} guaranteed profit)"
        )
        return opportunities

    def scan_dutch_for_provider(
        self,
        provider_id: str,
        counterpart_providers: list[str] | None = None,
    ) -> list[DutchOpportunity]:
        """
        Find dutch opportunities where provider_id is forced as one of the legs.

        Unlike scan_dutch(), this does NOT require +EV — returns all dutch including
        negative edge. Used by the dutch workflow for balance draining.

        Args:
            provider_id: Provider to force into the dutch (e.g. 'betinia')
            counterpart_providers: If set, only allow these providers for non-anchor legs

        Returns:
            List of DutchOpportunity sorted by combined_edge_pct (highest first)
        """
        opportunities = []

        events = self._get_events_with_provider(provider_id)

        for event in events:
            odds_grouped = self.group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                dutch = self._find_dutch_in_market(
                    event=event,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    all_markets=odds_grouped,
                    anchor_provider=provider_id,
                    counterpart_providers=counterpart_providers,
                )
                if dutch is None:
                    continue
                # Only include if the provider actually appears in a leg
                if any(leg["provider"] == provider_id for leg in dutch.legs):
                    opportunities.append(dutch)

        opportunities.sort(key=lambda x: x.combined_edge_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} dutch-workflow opportunities for {provider_id}"
        )
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

            for market, odds_by_outcome in odds_grouped.items():
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
            f"[Scanner] Found {len(opportunities)} reverse value bets "
            f"(>={min_edge_pct}% edge, Pinnacle vs consensus)"
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

        # Odds discrepancy check (same as regular value scan)
        if self._has_odds_discrepancy(odds_by_outcome):
            return []

        for outcome in pinnacle_market:
            pin_raw = pinnacle_market[outcome]

            if pin_raw < MIN_REVERSE_ODDS or pin_raw > MAX_REVERSE_ODDS:
                continue

            # Compute consensus fair odds from soft book platforms
            consensus_result = compute_consensus_fair_odds(
                outcome=outcome,
                odds_by_outcome=odds_by_outcome,
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
                values.append(vb)

        return values

    def _find_dutch_in_market(
        self,
        event: Event,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
        anchor_provider: str | None = None,
        counterpart_providers: list[str] | None = None,
    ) -> Optional[DutchOpportunity]:
        """
        Find a dutch opportunity in a single market.

        Cross-book dutch: uses best odds per outcome from ANY provider (soft or
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

        if sharp_outcome_count < 2:
            return None  # Need Pinnacle on 2+ outcomes for fair odds

        # Odds discrepancy check (likely event mismatch)
        if self._has_odds_discrepancy(odds_by_outcome):
            return None  # Skip entire market

        # For each outcome: find best odds (soft OR Pinnacle raw) and compute edge vs fair
        best_per_outcome = {}  # {outcome: {provider, odds, edge_pct, fair_odds, is_sharp}}
        soft_per_outcome = {}  # {outcome: {provider, odds, fair_odds}} — best soft for arb check
        # All valid soft candidates per outcome (ranked by odds desc) for conflict resolution
        soft_candidates = {}  # {outcome: [(odds, provider), ...]}
        fair_odds_map = {}    # {outcome: fair_odds}

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
                    # Non-anchor soft below fair — use Pinnacle fair (0% edge)
                    best_odds = fair_odds
                    best_provider = "pinnacle"
                    is_sharp = True
            else:
                # No soft book beats fair odds — use Pinnacle fair (0% edge)
                best_odds = fair_odds
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

        # In constrained-provider mode: reject market if any leg needed a Pinnacle fallback.
        # The user asked to only see results between their selected providers.
        if counterpart_providers and any(d["is_sharp"] for d in best_per_outcome.values()):
            return None

        # ── Enforce max 1 soft outcome per canonical platform ──
        # Placing multiple outcomes on the same bookmaker flags the account.
        # Resolve conflicts: keep the higher-edge leg, demote the other to
        # its next-best provider from a different platform (or Pinnacle).
        self._resolve_platform_conflicts(
            best_per_outcome, soft_candidates, fair_odds_map, anchor_provider
        )

        # Check again after conflict resolution (a demotion may have assigned Pinnacle)
        if counterpart_providers and any(d["is_sharp"] for d in best_per_outcome.values()):
            return None

        # Need all outcomes covered
        all_outcomes = list(best_per_outcome.keys())
        if len(all_outcomes) < 2:
            return None

        # Require at least one soft +EV leg (unless anchor mode)
        if not anchor_provider and not any(
            data["edge_pct"] > 0 and not data["is_sharp"]
            for data in best_per_outcome.values()
        ):
            return None

        # Require at least 2 different providers across all legs
        all_providers = set(data["provider"] for data in best_per_outcome.values())
        if len(all_providers) < 2:
            return None

        # Dutch calculation: stake all outcomes, guaranteed return = 1/sum(1/odds)
        dutch_sum = sum(1.0 / best_per_outcome[out]["odds"] for out in all_outcomes)
        guaranteed_return_per_unit = 1.0 / dutch_sum
        guaranteed_profit_pct = round((guaranteed_return_per_unit - 1) * 100, 2)

        # Per-leg stake percentages (how much of total stake goes to each leg)
        legs = []
        for out in all_outcomes:
            data = best_per_outcome[out]
            stake_pct = round((1.0 / data["odds"]) / dutch_sum * 100, 2)
            legs.append({
                "outcome": out,
                "provider": data["provider"],
                "odds": data["odds"],
                "edge_pct": data["edge_pct"],
                "fair_odds": data["fair_odds"],
                "stake_pct": stake_pct,
                "is_sharp": data["is_sharp"],
            })

        # Sort legs: highest edge first
        legs.sort(key=lambda x: x["edge_pct"], reverse=True)

        # Combined edge = weighted average of individual edges (weighted by stake)
        total_stake_pct = sum(leg["stake_pct"] for leg in legs)
        combined_edge = sum(
            leg["edge_pct"] * leg["stake_pct"] / total_stake_pct
            for leg in legs
        ) if total_stake_pct > 0 else 0

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
                                arb_legs.append({
                                    "outcome": out,
                                    "provider": sdata["provider"],
                                    "odds": sdata["odds"],
                                    "edge_pct": arb_edge,
                                    "fair_odds": sdata["fair_odds"],
                                    "stake_pct": arb_stake_pct,
                                    "is_sharp": False,
                                })
                            arb_legs.sort(key=lambda x: x["edge_pct"], reverse=True)

        return DutchOpportunity(
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
        separately — dutching across them is pointless.
        """
        return PLATFORM_MAP.get(provider, provider)

    def _resolve_platform_conflicts(
        self,
        best_per_outcome: dict,
        soft_candidates: dict,
        fair_odds_map: dict,
        anchor_provider: str | None,
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
            used_canonicals = {
                self._canonical(d["provider"])
                for d in best_per_outcome.values()
                if not d["is_sharp"]
            }
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
                    # No alternative soft — fall back to Pinnacle fair (0% edge)
                    best_per_outcome[loser_outcome] = {
                        "provider": "pinnacle",
                        "odds": fair_odds,
                        "edge_pct": 0.0,
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
        self, event: Event, exclude_providers: set[str] = None, check_staleness: bool = True
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
            check_staleness: Skip odds older than MAX_ODDS_AGE_HOURS (default: True)

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

        # Calculate staleness cutoff
        now = datetime.now(timezone.utc)
        staleness_cutoff = now - timedelta(hours=MAX_ODDS_AGE_HOURS)

        for odds in event.odds:
            # Skip excluded providers
            if odds.provider_id in exclude_providers:
                continue

            # Skip stale odds (older than MAX_ODDS_AGE_HOURS)
            if check_staleness and odds.updated_at:
                # Handle naive datetime (assume UTC)
                updated = odds.updated_at
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if updated < staleness_cutoff:
                    logger.debug(
                        f"Skipping stale odds for {event.id}/{odds.provider_id}: "
                        f"updated {updated.isoformat()}"
                    )
                    continue

            # Create market key (point field preserved for compatibility but not used for 1x2/moneyline)
            if odds.point is not None:
                market_key = f"{odds.market}_{odds.point}"
            else:
                market_key = odds.market

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

            grouped[market_key][outcome].append({
                "provider": odds.provider_id,
                "odds": odds.odds,
                "point": odds.point,
                "updated_at": odds.updated_at,
            })

        # Fix Asian-style spread providers that store 2 outcomes at the same point.
        # Pinnacle convention: home at spread_-X, away at spread_+X (1 outcome per key).
        # Some providers (e.g. Kambi) store both home@-X and away@-X as separate Asian
        # lines. Detect via implied probability sum < 100% and relocate the "away"
        # outcome to the complement point where it belongs.
        self._fix_asian_spread_grouping(grouped)

        return dict(grouped)

    def _fix_asian_spread_grouping(
        self, grouped: dict[str, dict[str, list[dict]]]
    ) -> None:
        """
        Normalize spread outcome labels to match Pinnacle's convention.

        Providers use three different conventions for spread outcome labels:
        1. Pinnacle/ComeOn: home@negative, away@positive (one outcome per point)
        2. 888sport/Spectate: home@positive, away@negative (SWAPPED labels)
        3. Betinia/Altenar: both outcomes at both points (labels = "which team")

        The old approach relocated by label, which broke when labels were swapped
        or duplicated — creating huge odds ratios that killed the entire market.

        New approach: use odds proximity to Pinnacle to determine which soft entry
        matches Pinnacle's outcome, regardless of the provider's label convention.

        This mutates the grouped dict in-place.
        """
        spread_keys = [k for k in grouped if k.startswith("spread_")]

        # First pass: remove 3-way European handicap providers.
        # European handicap has home/draw/away at integer points — fundamentally
        # different from 2-way Asian handicap (Pinnacle). Comparing them produces
        # false edges because the draw outcome absorbs probability.
        for market_key in spread_keys:
            odds_by_outcome = grouped[market_key]
            if "draw" in odds_by_outcome:
                european_providers = {
                    e["provider"] for e in odds_by_outcome["draw"]
                }
                for outcome_type in list(odds_by_outcome.keys()):
                    odds_by_outcome[outcome_type] = [
                        e for e in odds_by_outcome[outcome_type]
                        if e["provider"] not in european_providers
                    ]
                    if not odds_by_outcome[outcome_type]:
                        del odds_by_outcome[outcome_type]
                logger.debug(
                    f"Removed 3-way European handicap providers {european_providers} "
                    f"from {market_key}"
                )

        # Second pass: at each spread point where Pinnacle has an outcome,
        # keep only the closest soft entry per provider (by odds proximity).
        # Discard mismatched entries — the complement point will independently
        # pick the right entries from its own Pinnacle reference.
        # No cross-point relocation avoids bidirectional bounce issues.
        for market_key in [k for k in grouped if k.startswith("spread_")]:
            try:
                point = float(market_key.split("_", 1)[1])
            except (ValueError, IndexError):
                continue

            odds_by_outcome = grouped[market_key]

            # Find Pinnacle's outcome and odds at this point
            pin_outcome = None
            pin_odds = None
            for otype in ("home", "away"):
                for entry in odds_by_outcome.get(otype, []):
                    if entry["provider"] in SHARP_PROVIDERS:
                        pin_outcome = otype
                        pin_odds = entry["odds"]
                        break
                if pin_outcome:
                    break

            if not pin_outcome or not pin_odds:
                continue

            other_outcome = "away" if pin_outcome == "home" else "home"

            # Find Pinnacle's complement odds at the opposite point.
            # Used to reject soft entries that are actually on the wrong side
            # (e.g., VBet stores home@-X when Pinnacle has away@-X for different teams).
            pin_complement_odds = None
            complement_key = f"spread_{-point}"
            complement_data = grouped.get(complement_key, {})
            for otype in ("home", "away"):
                for entry in complement_data.get(otype, []):
                    if entry["provider"] in SHARP_PROVIDERS:
                        pin_complement_odds = entry["odds"]
                        break
                if pin_complement_odds:
                    break

            # Collect all soft entries at this point, grouped by provider
            soft_by_provider: dict[str, list[tuple[str, dict]]] = defaultdict(list)
            for otype in ("home", "away"):
                for entry in odds_by_outcome.get(otype, []):
                    if entry["provider"] not in SHARP_PROVIDERS:
                        soft_by_provider[entry["provider"]].append((otype, entry))

            keep_entries = []

            for provider, entries in soft_by_provider.items():
                # Keep only the entry closest to Pinnacle's odds (regardless of label).
                # Discard all others — they belong to the complement side.
                best_idx = min(
                    range(len(entries)),
                    key=lambda i: abs(entries[i][1]["odds"] / pin_odds - 1),
                )
                best_label, best_entry = entries[best_idx]
                ratio = best_entry["odds"] / pin_odds
                if not (0.65 < ratio < 1.55):
                    continue

                # Cross-check: if the complement Pinnacle odds exist, verify the
                # soft entry is closer to THIS point's Pinnacle than to the complement.
                # Providers like VBet always assign home@negative regardless of who's
                # favored, which can place a "home -X" entry at the same market_key
                # as Pinnacle's "away -X" — different bets on different teams.
                # Using log ratio for symmetric distance comparison.
                # Only reject when Pinnacle sides are far enough apart (>2%)
                # to make the complement check reliable — near-identical sides
                # (e.g., 1.90 vs 1.91) are too close for this heuristic.
                if pin_complement_odds and pin_complement_odds > 1:
                    pin_spread = abs(math.log(pin_odds / pin_complement_odds))
                    if pin_spread > 0.02:
                        dist_to_pin = abs(math.log(best_entry["odds"] / pin_odds))
                        dist_to_complement = abs(math.log(best_entry["odds"] / pin_complement_odds))
                        if dist_to_complement < dist_to_pin:
                            logger.debug(
                                f"Rejected {provider} {market_key} odds={best_entry['odds']:.2f}: "
                                f"closer to complement Pinnacle {pin_complement_odds:.2f} "
                                f"than to this side {pin_odds:.2f}"
                            )
                            continue

                keep_entries.append(best_entry)

            # Rebuild: sharp entries + correctly matched soft entries
            sharp_at_pin = [
                e for e in odds_by_outcome.get(pin_outcome, [])
                if e["provider"] in SHARP_PROVIDERS
            ]
            sharp_at_other = [
                e for e in odds_by_outcome.get(other_outcome, [])
                if e["provider"] in SHARP_PROVIDERS
            ]

            odds_by_outcome.clear()
            if sharp_at_pin or keep_entries:
                odds_by_outcome[pin_outcome] = sharp_at_pin + keep_entries
            if sharp_at_other:
                odds_by_outcome[other_outcome] = sharp_at_other

    def _count_outcomes_per_provider(
        self, odds_by_outcome: dict[str, list[dict]]
    ) -> dict[str, int]:
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

        # Compute Pinnacle overround for ML features
        pinnacle_overround = (
            sum(1.0 / o for o in pinnacle_market.values() if o > 1) - 1.0
        ) if pinnacle_market else 0

        # Pre-compute probability sums per soft provider (used in completeness check)
        soft_prob_sums = defaultdict(float)
        for out, providers in odds_by_outcome.items():
            for p in providers:
                if p["provider"] not in SHARP_PROVIDERS:
                    soft_prob_sums[p["provider"]] += 1.0 / p["odds"]

        # Check for odds discrepancy (likely event mismatch)
        # Exclude Polymarket from ratio calc — prediction market pricing naturally
        # diverges from traditional sportsbooks, especially for underdogs
        if self._has_odds_discrepancy(odds_by_outcome, exclude_providers={"polymarket"}, market=market):
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
                        market.startswith("spread")
                        and sharp_outcome_count in (1, 2)
                        and soft_count in (1, 2)
                    )
                    is_polymarket_mismatch = (
                        po["provider"] == "polymarket"
                        and soft_count <= sharp_outcome_count
                    )
                    if soft_count != sharp_outcome_count and not is_spread_asymmetry and not is_polymarket_mismatch:
                        continue  # Don't compare 3-way vs 2-way markets

                # Validate soft provider's market completeness (pre-computed)
                # Skip for Polymarket — prediction markets can have single-outcome listings
                # Skip for spread markets — after Asian spread fix, each point has only 1
                # outcome per provider (prob_sum ~0.5), which is expected. The complement
                # lookup already reconstructs the full Pinnacle market for proper de-vigging.
                is_spread_market = market.startswith("spread")
                if soft_prob_sums.get(po["provider"], 0) < MIN_VALID_PROB_SUM:
                    if po["provider"] != "polymarket" and not is_spread_market:
                        continue  # Incomplete market at soft provider

                # Per-provider odds ratio vs Pinnacle raw (catches bad odds even with 1 soft provider)
                # Skip for Polymarket — prediction market pricing naturally diverges from
                # sportsbooks; the MAX_EDGE_PCT cap already catches truly bad data
                # Spread/total markets use relaxed threshold — handicap pricing
                # naturally diverges more between books than 1x2/moneyline
                if po["provider"] != "polymarket" and pinnacle_raw and pinnacle_raw > 1:
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
                )
                if vb:
                    vb.odds_updated_at = po.get("updated_at").isoformat() if po.get("updated_at") else None
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
                market.startswith("spread")
                and sharp_outcome_count == 1
                and anchor_outcome_count in (2, 3)
            )
            if anchor_outcome_count != sharp_outcome_count and not is_spread_asymmetry:
                return []  # Don't compare 3-way vs 2-way markets

        # Check for odds discrepancy (likely event mismatch)
        if self._has_odds_discrepancy(odds_by_outcome, market=market):
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

    def _enrich_spread_complement(
        self,
        pinnacle_market: dict,
        market: str,
        all_markets: dict,
    ) -> tuple[dict, bool]:
        """
        For spread markets, find the complement point to build full 2-way Pinnacle market.

        Pinnacle stores Asian handicap as home@-X, away@+X under different market keys.
        This finds the complement point and merges the missing outcome into pinnacle_market.

        Args:
            pinnacle_market: Current {outcome: odds} dict for Pinnacle (mutated in place)
            market: Market key (e.g. "spread_-6.5")
            all_markets: Full odds_grouped dict for this event

        Returns:
            (pinnacle_market, enriched) where enriched is True if complement was found
        """
        if not (all_markets and market.startswith("spread_") and len(pinnacle_market) == 1):
            return pinnacle_market, False

        try:
            point = float(market.split("_", 1)[1])
            complement_key = f"spread_{-point}"
            complement_data = all_markets.get(complement_key, {})
            for out, providers in complement_data.items():
                if out not in pinnacle_market:
                    for p in providers:
                        if p["provider"] == "pinnacle":
                            pinnacle_market[out] = p["odds"]
                            break
            return pinnacle_market, True
        except (ValueError, IndexError):
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

        Args:
            odds_by_outcome: {outcome: [provider_odds_entries]}
            exclude_providers: Provider IDs to exclude from the ratio calculation
            market: Market key (used to select appropriate ratio threshold)

        Returns:
            True if discrepancy found (market should be skipped)
        """
        is_spread = market.startswith("spread") or market.startswith("total")
        threshold = MAX_ODDS_RATIO_SPREAD if is_spread else MAX_ODDS_RATIO

        for outcome, provider_odds_list in odds_by_outcome.items():
            if exclude_providers:
                odds_values = [
                    po["odds"] for po in provider_odds_list
                    if po["provider"] not in exclude_providers
                ]
            else:
                odds_values = [po["odds"] for po in provider_odds_list]
            if len(odds_values) >= 3:
                odds_ratio = max(odds_values) / min(odds_values)
                if odds_ratio > threshold:
                    return True
        return False

    def _get_fair_odds(
        self,
        outcome: str,
        odds_by_outcome: dict[str, list[dict]],
        devig: bool = True,
        pinnacle_market: dict[str, float] = None,
    ) -> Optional[tuple[float, str]]:
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
                    logger.debug(
                        f"Skipping incomplete market: {outcome} prob_sum={prob_sum:.2f}"
                    )
                    return None

                fair_odds = get_fair_odds_for_outcome(
                    outcome, pinnacle_market, method="multiplicative"
                )
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
