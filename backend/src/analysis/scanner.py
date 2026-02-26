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

from sqlalchemy.orm import Session

from ..db.models import Event, Odds
from ..repositories import EventRepo
from .value import find_value, ValueBet
from ..bankroll.stake_calculator import StakeCalculator, StakeResult
from .devig import (
    calculate_margin,
    devig_multiplicative,
    get_fair_odds_for_outcome,
    compute_consensus_fair_odds,
)
from ..constants import SHARP_PROVIDERS, EXCLUDED_FROM_SCANS, PLATFORM_MAP

logger = logging.getLogger(__name__)

# Minimum probability sum for a valid market (accounts for margin)
# Normal markets: 1.02-1.10, incomplete markets: < 0.90
MIN_VALID_PROB_SUM = 0.90

# Maximum odds ratio for same outcome across providers
# If max/min > 1.35, likely event mismatch or stale odds
# Real odds rarely differ more than 35% across providers for same event
MAX_ODDS_RATIO = 1.35

# Maximum edge percentage for a value bet to be considered valid
# Edges above this are almost certainly data quality issues (wrong odds, event mismatch)
# Real value bets rarely exceed 30% edge; 50% gives comfortable headroom
MAX_EDGE_PCT = 50.0

# Maximum odds age in hours for value scanning
# Odds older than this are considered stale and skipped
MAX_ODDS_AGE_HOURS = 2

# Reverse value: minimum independent platforms for consensus
MIN_CONSENSUS_PLATFORMS = 5

# Reverse value: only bet longshots where Pinnacle is less efficient
MIN_REVERSE_ODDS = 3.50

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

    def scan_value(self, min_edge_pct: float = 5.0) -> list[ValueBet]:
        """
        Find value bets against de-vigged Pinnacle odds.

        Uses Pinnacle as the sole sharp source. Their ~2.5% margin is
        removed using multiplicative de-vigging. Skips odds older than
        MAX_ODDS_AGE_HOURS (2 hours).

        Args:
            min_edge_pct: Minimum edge percentage (default 5%)

        Returns:
            List of ValueBet sorted by edge (highest first)
        """
        opportunities = []

        # Get events with odds from 2+ providers
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
                start_time=event.start_time.isoformat() if event and event.start_time else None,
            )
            enriched_bets.append(enriched)

        # Sort by edge (highest first), then by stake
        enriched_bets.sort(key=lambda x: (x.edge_pct, x.recommended_stake or 0), reverse=True)

        # Count actionable bets
        actionable = sum(1 for b in enriched_bets if b.recommended_stake and b.recommended_stake > 0)
        logger.info(
            f"[Scanner] {actionable}/{len(enriched_bets)} value bets have stake recommendations"
        )

        return enriched_bets

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

    def scan_dutch(self, min_edge_pct: float = 0.0) -> list[DutchOpportunity]:
        """
        Find pure dutch opportunities: ALL legs +EV at different providers.

        Every outcome must beat Pinnacle fair odds (positive edge on every leg).
        These are the highest-quality cross-book opportunities.

        Args:
            min_edge_pct: Minimum combined edge % to include (default 0 = all)

        Returns:
            List of DutchOpportunity sorted by guaranteed_profit_pct (highest first)
        """
        opportunities = []

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

    def scan_reverse(self, min_edge_pct: float = 0.0) -> list[DutchOpportunity]:
        """
        Find reverse dutch opportunities: at least one soft +EV leg, others covered.

        Unlike pure dutch (all legs +EV), reverse dutch has some legs at negative
        edge (typically covered by Pinnacle raw odds). Useful for reducing variance
        on strong single-leg value bets.

        Args:
            min_edge_pct: Minimum combined edge % to include (default 0 = all)

        Returns:
            List of DutchOpportunity sorted by guaranteed_profit_pct (highest first)
        """
        opportunities = []

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

    def scan_reverse_value(self, min_edge_pct: float = 3.0) -> list[ValueBet]:
        """
        Find reverse value bets: Pinnacle odds beating soft book consensus.

        Uses platform-weighted harmonic mean of devigged soft books as
        the fair odds source. Only considers 1x2/moneyline markets where
        Pinnacle offers odds >= MIN_REVERSE_ODDS (longshots where Pinnacle
        is less efficient).

        Requires MIN_CONSENSUS_PLATFORMS independent pricing sources.

        Args:
            min_edge_pct: Minimum edge percentage (default 3%)

        Returns:
            List of ValueBet (provider="pinnacle") sorted by edge
        """
        opportunities = []

        events = self._get_multi_provider_events(min_providers=2)

        for event in events:
            odds_grouped = self.group_odds(event)

            for market, odds_by_outcome in odds_grouped.items():
                # Only 1x2 and moneyline — no spread/total (point matching issues)
                base_market = market.split("_")[0] if "_" in market else market
                if base_market not in ("1x2", "moneyline"):
                    continue

                values = self.find_reverse_value_in_market(
                    event_id=event.id,
                    market=market,
                    odds_by_outcome=odds_by_outcome,
                    min_edge_pct=min_edge_pct,
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
        pinnacle_market = {}
        for out, providers in odds_by_outcome.items():
            for p in providers:
                if p["provider"] == "pinnacle":
                    pinnacle_market[out] = p["odds"]
                    break

        # Spread complement lookup
        if (
            all_markets
            and market.startswith("spread_")
            and len(pinnacle_market) == 1
        ):
            try:
                point = float(market.split("_", 1)[1])
                complement_key = f"spread_{-point:g}"
                complement_data = all_markets.get(complement_key, {})
                for out, providers in complement_data.items():
                    if out not in pinnacle_market:
                        for p in providers:
                            if p["provider"] == "pinnacle":
                                pinnacle_market[out] = p["odds"]
                                break
            except (ValueError, IndexError):
                pass

        if len(pinnacle_market) < 2:
            return []

        # Odds discrepancy check (same as regular value scan)
        for outcome, provider_odds_list in odds_by_outcome.items():
            if len(provider_odds_list) >= 3:
                odds_values = [po["odds"] for po in provider_odds_list]
                odds_ratio = max(odds_values) / min(odds_values)
                if odds_ratio > MAX_ODDS_RATIO:
                    return []

        for outcome in pinnacle_market:
            pin_raw = pinnacle_market[outcome]

            # Only longshots where Pinnacle is less efficient
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
    ) -> Optional[DutchOpportunity]:
        """
        Find a dutch opportunity in a single market.

        Cross-book dutch: uses best odds per outcome from ANY provider (soft or
        Pinnacle raw). Edge is always computed vs Pinnacle de-vigged fair odds.
        Requires at least one soft +EV leg (otherwise no edge exists).
        """
        # Count outcomes per provider for market type mismatch detection
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)
        sharp_outcome_count = provider_outcome_counts.get("pinnacle", 0)

        # Pre-compute Pinnacle market dict (raw odds)
        pinnacle_market = {}
        for out, providers in odds_by_outcome.items():
            for p in providers:
                if p["provider"] == "pinnacle":
                    pinnacle_market[out] = p["odds"]
                    break

        # Spread complement lookup
        if (
            all_markets
            and market.startswith("spread_")
            and len(pinnacle_market) == 1
        ):
            try:
                point = float(market.split("_", 1)[1])
                complement_key = f"spread_{-point:g}"
                complement_data = all_markets.get(complement_key, {})
                for out, providers in complement_data.items():
                    if out not in pinnacle_market:
                        for p in providers:
                            if p["provider"] == "pinnacle":
                                pinnacle_market[out] = p["odds"]
                                break
                if len(pinnacle_market) > sharp_outcome_count:
                    sharp_outcome_count = len(pinnacle_market)
            except (ValueError, IndexError):
                pass

        if sharp_outcome_count < 2:
            return None  # Need Pinnacle on 2+ outcomes for fair odds

        # Odds discrepancy check (likely event mismatch)
        for outcome, provider_odds_list in odds_by_outcome.items():
            if len(provider_odds_list) >= 3:
                odds_values = [po["odds"] for po in provider_odds_list]
                odds_ratio = max(odds_values) / min(odds_values)
                if odds_ratio > MAX_ODDS_RATIO:
                    return None  # Skip entire market

        # For each outcome: find best odds (soft OR Pinnacle raw) and compute edge vs fair
        best_per_outcome = {}  # {outcome: {provider, odds, edge_pct, fair_odds, is_sharp}}

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

            # Get Pinnacle raw odds for this outcome
            pinnacle_raw = pinnacle_market.get(outcome, 0.0)

            # Find best soft provider for this outcome
            best_soft_odds = 0.0
            best_soft_provider = None
            for po in provider_odds_list:
                if po["provider"] in SHARP_PROVIDERS:
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
                if po["odds"] > best_soft_odds:
                    best_soft_odds = po["odds"]
                    best_soft_provider = po["provider"]

            # Use soft book if it beats fair odds; otherwise fall back to fair odds (0% edge)
            if best_soft_provider and best_soft_odds > fair_odds:
                best_odds = best_soft_odds
                best_provider = best_soft_provider
                is_sharp = False
            else:
                # No soft book beats fair odds — use fair odds (0% edge coverage)
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

        # Need all outcomes covered
        all_outcomes = list(best_per_outcome.keys())
        if len(all_outcomes) < 2:
            return None

        # Require at least one soft +EV leg
        if not any(
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

        return DutchOpportunity(
            event_id=event.id,
            market=market,
            legs=legs,
            combined_edge_pct=round(combined_edge, 2),
            guaranteed_profit_pct=guaranteed_profit_pct,
            home_team=event.home_team,
            away_team=event.away_team,
            sport=event.sport,
            league=event.league,
            start_time=event.start_time.isoformat() if event.start_time else None,
        )

    def _get_multi_provider_events(self, min_providers: int = 2) -> list[Event]:
        """Get events with odds from N+ providers."""
        return self.event_repo.get_multi_provider_events(min_providers)

    def _get_events_with_provider(self, provider_id: str) -> list[Event]:
        """Get events where a specific provider has odds."""
        return self.event_repo.get_events_with_provider(provider_id)

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
            exclude_providers: Set of provider IDs to exclude (default: EXCLUDED_FROM_SCANS)
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
            exclude_providers = EXCLUDED_FROM_SCANS

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

            grouped[market_key][odds.outcome].append({
                "provider": odds.provider_id,
                "odds": odds.odds,
                "point": odds.point,
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
        Fix providers that store 2 Asian handicap outcomes at the same spread point.

        Kambi (and similar) stores alternate handicap lines where each outcome has
        its own point value. This means at spread_-1.5 we can find both:
          - home (Solary -1.5, correct)
          - away (Galions -1.5, from a SEPARATE betOffer — NOT Galions +1.5)

        Pinnacle's convention: 1 outcome per point side (home at -X, away at +X).
        Use Pinnacle's structure as ground truth: at each spread point, only keep
        the outcome type(s) that Pinnacle has. This is deterministic and avoids
        the fragile prob_sum heuristic that misses borderline cases.

        This mutates the grouped dict in-place.
        """
        spread_keys = [k for k in grouped if k.startswith("spread_")]

        for market_key in spread_keys:
            try:
                point = float(market_key.split("_", 1)[1])
            except (ValueError, IndexError):
                continue

            odds_by_outcome = grouped[market_key]

            # Only relevant when both home and away exist at this point
            if "home" not in odds_by_outcome or "away" not in odds_by_outcome:
                continue

            # Determine which outcome types Pinnacle has at this point
            pinnacle_outcomes = set()
            for outcome_type in ("home", "away"):
                if outcome_type in odds_by_outcome:
                    for entry in odds_by_outcome[outcome_type]:
                        if entry["provider"] in SHARP_PROVIDERS:
                            pinnacle_outcomes.add(outcome_type)

            # If Pinnacle has exactly one outcome type, remove the other from soft providers.
            # Pinnacle always stores home@negative, away@positive — one per side.
            if len(pinnacle_outcomes) == 1:
                keep_outcome = pinnacle_outcomes.pop()
                remove_outcome = "away" if keep_outcome == "home" else "home"

                original_count = len(odds_by_outcome.get(remove_outcome, []))
                odds_by_outcome[remove_outcome] = [
                    e for e in odds_by_outcome.get(remove_outcome, [])
                    if e["provider"] in SHARP_PROVIDERS
                ]
                removed_count = original_count - len(odds_by_outcome[remove_outcome])

                # Clean up empty outcome key
                if not odds_by_outcome[remove_outcome]:
                    del odds_by_outcome[remove_outcome]

                if removed_count > 0:
                    logger.debug(
                        f"Fixed Asian spread at {market_key}: removed {removed_count} "
                        f"soft '{remove_outcome}' entries (Pinnacle only has '{keep_outcome}')"
                    )

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
        pinnacle_market = {}
        for out, providers in odds_by_outcome.items():
            for p in providers:
                if p["provider"] == "pinnacle":
                    pinnacle_market[out] = p["odds"]
                    break

        # Spread complement lookup: Pinnacle stores home at +X and away at -X
        # (Asian handicap style) under different market keys. Soft providers store
        # both outcomes under the same point. Reconstruct the full 2-way Pinnacle
        # market so we can properly de-vig instead of using raw odds.
        if (
            all_markets
            and market.startswith("spread_")
            and len(pinnacle_market) == 1
        ):
            try:
                point = float(market.split("_", 1)[1])
                complement_key = f"spread_{-point:g}"
                complement_data = all_markets.get(complement_key, {})
                for out, providers in complement_data.items():
                    if out not in pinnacle_market:
                        for p in providers:
                            if p["provider"] == "pinnacle":
                                pinnacle_market[out] = p["odds"]
                                break
                # Update sharp count to reflect enriched market
                if len(pinnacle_market) > sharp_outcome_count:
                    sharp_outcome_count = len(pinnacle_market)
            except (ValueError, IndexError):
                pass  # Malformed market key — skip complement lookup

        # Pre-compute probability sums per soft provider (used in completeness check)
        soft_prob_sums = defaultdict(float)
        for out, providers in odds_by_outcome.items():
            for p in providers:
                if p["provider"] not in SHARP_PROVIDERS:
                    soft_prob_sums[p["provider"]] += 1.0 / p["odds"]

        # Check for odds discrepancy (likely event mismatch)
        for outcome, provider_odds_list in odds_by_outcome.items():
            if len(provider_odds_list) >= 3:
                odds_values = [po["odds"] for po in provider_odds_list]
                odds_ratio = max(odds_values) / min(odds_values)
                if odds_ratio > MAX_ODDS_RATIO:
                    logger.debug(
                        f"Skipping {event_id} {market}: {outcome} odds ratio {odds_ratio:.2f} "
                        f"exceeds {MAX_ODDS_RATIO} (likely event mismatch)"
                    )
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
                # Exception: spread markets — Pinnacle stores 1 outcome per
                # point (Asian handicap), soft providers store 2 (home+away).
                # 3-way spreads (European handicap with draw) are NOT comparable.
                soft_count = provider_outcome_counts.get(po["provider"], 0)
                if soft_count > 0 and sharp_outcome_count > 0:
                    is_spread_asymmetry = (
                        market.startswith("spread")
                        and sharp_outcome_count in (1, 2)
                        and soft_count == 2
                    )
                    if soft_count != sharp_outcome_count and not is_spread_asymmetry:
                        continue  # Don't compare 3-way vs 2-way markets

                # Validate soft provider's market completeness (pre-computed)
                if soft_prob_sums.get(po["provider"], 0) < MIN_VALID_PROB_SUM:
                    continue  # Incomplete market at soft provider

                # Per-provider odds ratio vs Pinnacle raw (catches bad odds even with 1 soft provider)
                if pinnacle_raw and pinnacle_raw > 1:
                    ratio = po["odds"] / pinnacle_raw
                    if ratio > MAX_ODDS_RATIO:
                        logger.debug(
                            f"Skipping {po['provider']} {event_id} {market} {outcome}: "
                            f"odds {po['odds']:.2f} vs Pinnacle {pinnacle_raw:.2f} "
                            f"(ratio {ratio:.2f} > {MAX_ODDS_RATIO})"
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
                    # Hard cap: edges above MAX_EDGE_PCT are data quality issues
                    if vb.edge_pct > MAX_EDGE_PCT:
                        logger.debug(
                            f"Skipping suspicious value {vb.edge_pct:+.1f}% for "
                            f"{event_id} {market} {outcome} ({po['provider']})"
                        )
                        continue
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
        pinnacle_market = {}
        for out, providers in odds_by_outcome.items():
            for p in providers:
                if p["provider"] == "pinnacle":
                    pinnacle_market[out] = p["odds"]
                    break

        # Spread complement lookup (same as find_value_in_market)
        if (
            all_markets
            and market.startswith("spread_")
            and len(pinnacle_market) == 1
        ):
            try:
                point = float(market.split("_", 1)[1])
                complement_key = f"spread_{-point:g}"
                complement_data = all_markets.get(complement_key, {})
                for out, providers in complement_data.items():
                    if out not in pinnacle_market:
                        for p in providers:
                            if p["provider"] == "pinnacle":
                                pinnacle_market[out] = p["odds"]
                                break
                if len(pinnacle_market) > sharp_outcome_count:
                    sharp_outcome_count = len(pinnacle_market)
            except (ValueError, IndexError):
                pass

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
        for outcome, provider_odds_list in odds_by_outcome.items():
            if len(provider_odds_list) >= 3:
                odds_values = [po["odds"] for po in provider_odds_list]
                odds_ratio = max(odds_values) / min(odds_values)
                if odds_ratio > MAX_ODDS_RATIO:
                    return []  # Skip market if likely event mismatch

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
                if ratio > MAX_ODDS_RATIO:
                    logger.debug(
                        f"Skipping {anchor_provider} {event.id} {market} {outcome}: "
                        f"odds {anchor_odds:.2f} vs Pinnacle {pinnacle_raw:.2f} "
                        f"(ratio {ratio:.2f} > {MAX_ODDS_RATIO})"
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
            pinnacle_market = {}
            for out, providers in odds_by_outcome.items():
                for p in providers:
                    if p["provider"] == "pinnacle":
                        pinnacle_market[out] = p["odds"]
                        break

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
