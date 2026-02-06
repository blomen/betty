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

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import Event, Odds
from .value import find_value, ValueBet
from ..bankroll.stake_calculator import StakeCalculator, StakeResult
from .devig import (
    calculate_margin,
    devig_multiplicative,
    get_fair_odds_for_outcome,
)
from ..constants import SHARP_PROVIDERS, EXCLUDED_FROM_SCANS

logger = logging.getLogger(__name__)

# Minimum probability sum for a valid market (accounts for margin)
# Normal markets: 1.02-1.10, incomplete markets: < 0.90
MIN_VALID_PROB_SUM = 0.90

# Maximum odds ratio for same outcome across providers
# If max/min > 1.35, likely event mismatch or stale odds
# Real odds rarely differ more than 35% across providers for same event
MAX_ODDS_RATIO = 1.35

# Maximum odds age in hours for value scanning
# Odds older than this are considered stale and skipped
MAX_ODDS_AGE_HOURS = 2


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
        Opportunities with edge >100% are filtered out as data errors.
    """

    def __init__(self, session: Session):
        self.session = session

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

        # Enrich with stakes and event context
        enriched_bets = []
        for vb in raw_bets:
            # Get event for context
            event = self.session.query(Event).filter(Event.id == vb.event_id).first()

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
                )
                opportunities.extend(bonus_opps)

        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge_pct, reverse=True)

        logger.info(
            f"[Scanner] Found {len(opportunities)} bonus opportunities for {anchor_provider}"
        )
        return opportunities

    def _get_multi_provider_events(self, min_providers: int = 2) -> list[Event]:
        """Get events with odds from N+ providers."""
        return (
            self.session.query(Event)
            .join(Odds)
            .group_by(Event.id)
            .having(func.count(func.distinct(Odds.provider_id)) >= min_providers)
            .all()
        )

    def _get_events_with_provider(self, provider_id: str) -> list[Event]:
        """Get events where a specific provider has odds."""
        return (
            self.session.query(Event)
            .join(Odds)
            .filter(Odds.provider_id == provider_id)
            .distinct()
            .all()
        )

    def group_odds(
        self, event: Event, exclude_providers: set[str] = None, check_staleness: bool = True
    ) -> dict[str, dict[str, list[dict]]]:
        """
        Group event odds by market -> outcome -> provider list.

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

        return dict(grouped)

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
    ) -> list[ValueBet]:
        """Find value bets in a single market using Pinnacle as sharp source."""
        values = []

        # Count outcomes per provider to detect market type mismatch
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)

        # Find Pinnacle's outcome count (sole sharp source)
        sharp_outcome_count = provider_outcome_counts.get("pinnacle", 0)

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
            # Get fair odds from de-vigged Pinnacle
            fair_result = self._get_fair_odds(
                outcome=outcome,
                odds_by_outcome=odds_by_outcome,
            )

            if fair_result is None:
                continue

            fair_odds, fair_source = fair_result

            # Check each soft provider
            for po in provider_odds_list:
                if po["provider"] in SHARP_PROVIDERS:
                    continue  # Don't compare sharp vs sharp

                # Skip if market types don't match (different outcome counts)
                soft_count = provider_outcome_counts.get(po["provider"], 0)
                if soft_count > 0 and sharp_outcome_count > 0:
                    if soft_count != sharp_outcome_count:
                        continue  # Don't compare 3-way vs 2-way markets

                # Validate soft provider's market completeness
                soft_provider = po["provider"]
                soft_prob_sum = sum(
                    1.0 / p["odds"]
                    for out, providers in odds_by_outcome.items()
                    for p in providers
                    if p["provider"] == soft_provider
                )
                if soft_prob_sum < MIN_VALID_PROB_SUM:
                    continue  # Incomplete market at soft provider

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
                    # Sanity check: edges > 100% are likely data quality issues
                    if vb.edge_pct > 100:
                        logger.debug(
                            f"Skipping suspicious value {vb.edge_pct:+.1f}% for "
                            f"{event_id} {market} {outcome}"
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
    ) -> list[BonusOpportunity]:
        """Find bonus opportunities in a single market using Pinnacle as sharp source."""
        opportunities = []

        # Count outcomes per provider to detect market type mismatch
        # (e.g., 3-way 1x2 vs 2-way moneyline)
        provider_outcome_counts = self._count_outcomes_per_provider(odds_by_outcome)
        anchor_outcome_count = provider_outcome_counts.get(anchor_provider, 0)

        # Find Pinnacle's outcome count (sole sharp source)
        sharp_outcome_count = provider_outcome_counts.get("pinnacle", 0)

        # Skip if market types don't match (different outcome counts)
        if anchor_outcome_count > 0 and sharp_outcome_count > 0:
            if anchor_outcome_count != sharp_outcome_count:
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

            # Sanity check: edges > 100% are almost certainly data quality issues
            # (mismatched markets, wrong point values, stale data)
            if abs(edge_pct) > 100:
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
    ) -> Optional[tuple[float, str]]:
        """
        Get fair odds for an outcome from Pinnacle (sole sharp source).

        Pinnacle's ~2.5% margin is removed using multiplicative de-vigging.

        Args:
            outcome: The outcome to get fair odds for
            odds_by_outcome: All market odds
            devig: Whether to de-vig (default True)

        Returns:
            (fair_odds, "pinnacle") or None if Pinnacle not found
        """
        # Find Pinnacle odds for this outcome
        outcome_providers = odds_by_outcome.get(outcome, [])

        pinnacle_odds = None
        for po in outcome_providers:
            if po["provider"] == "pinnacle":
                pinnacle_odds = po["odds"]
                break

        if pinnacle_odds is None:
            return None

        # De-vig Pinnacle if requested
        if devig:
            # Need full market odds to de-vig properly
            pinnacle_market = {}
            for out, providers in odds_by_outcome.items():
                for p in providers:
                    if p["provider"] == "pinnacle":
                        pinnacle_market[out] = p["odds"]
                        break

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
                # Single outcome, can't de-vig properly - skip
                return None
        else:
            return (pinnacle_odds, "pinnacle(raw)")


# Quick test
if __name__ == "__main__":
    print("=== OpportunityScanner Test ===")
    print("Run with database session to test scanning functionality")
