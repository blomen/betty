"""
Combo Profit Boost Optimizer
=============================

Builds optimal combo/parlay recommendations from +EV value bets,
factoring in provider-specific profit boost tables.

Combo profit boost: bookmaker adds X% to parlay profit based on leg count.
Payout = stake + (combined_odds - 1) × stake × (1 + boost_pct/100)

Algorithm:
1. Filter value bets to target provider + min odds per leg
2. Deduplicate by event (one leg per event for independence)
3. Sort by edge descending
4. Build combos of various sizes (greedy top-N by edge — provably optimal for independent legs)
5. Calculate boost-adjusted EV and Kelly stake
6. Return ranked by EV per unit staked
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from .value import ValueBet
from ..bankroll.stake_calculator import round_stake_natural, dynamic_min_stake

logger = logging.getLogger(__name__)

# Combo-specific safety caps
COMBO_KELLY_CAP = 0.25          # Quarter-Kelly max for combos (high variance)
COMBO_BANKROLL_CAP_PCT = 0.01   # 1% of bankroll absolute cap per combo
MAX_COMBO_EDGE_PCT = 200.0      # Sanity: reject if edge exceeds this


@dataclass
class ComboLegInfo:
    """A single leg in a combo recommendation."""
    event_id: str
    market: str
    outcome: str
    provider_odds: float
    fair_odds: float
    fair_probability: float
    edge_pct: float
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    sport: Optional[str] = None
    start_time: Optional[str] = None
    point: Optional[float] = None


@dataclass
class ComboRecommendation:
    """A recommended combo bet with boost analysis."""
    provider: str
    legs: list[ComboLegInfo]
    num_legs: int

    # Raw combo math
    combined_offered_odds: float
    combined_fair_odds: float

    # Boost
    boost_pct: float
    profit_multiplier: float

    # Effective odds after boost
    effective_odds: float

    # Edge analysis
    edge_pct: float
    win_probability: float

    # Stake recommendation
    kelly_fraction: float
    recommended_stake: Optional[float] = None
    skip_reason: Optional[str] = None

    # EV metrics
    ev_per_unit: float = 0.0


class ComboOptimizer:
    """
    Builds optimal combo recommendations from +EV value bets.

    Uses greedy selection (top-N by edge) which is provably optimal
    when legs are independent (different events).
    """

    def __init__(
        self,
        boost_config: dict,
        bankroll: float,
        max_kelly: float = 0.75,
    ):
        self.min_odds_per_leg = boost_config.get("min_odds_per_leg", 1.40)
        self.max_legs = boost_config.get("max_legs", 20)
        self.boost_table: dict[int, float] = {
            int(k): float(v) for k, v in boost_config.get("boost_table", {}).items()
        }
        self.bankroll = bankroll
        self.max_kelly = max_kelly

    def _get_boost_pct(self, num_legs: int) -> float:
        """Get boost percentage for a given number of legs."""
        if num_legs in self.boost_table:
            return self.boost_table[num_legs]
        # If exact key missing, find the closest lower key
        available = sorted(k for k in self.boost_table if k <= num_legs)
        if available:
            return self.boost_table[available[-1]]
        return 0.0

    def _filter_and_deduplicate(
        self, value_bets: list[ValueBet], provider: str,
    ) -> list[ValueBet]:
        """Filter to target provider + min odds, deduplicate by event (best edge wins)."""
        # Filter: correct provider, meets min odds
        eligible = [
            vb for vb in value_bets
            if vb.provider == provider
            and vb.provider_odds >= self.min_odds_per_leg
            and vb.edge_pct > 0
        ]

        # Deduplicate by event_id — pick highest edge per event
        best_per_event: dict[str, ValueBet] = {}
        for vb in eligible:
            existing = best_per_event.get(vb.event_id)
            if existing is None or vb.edge_pct > existing.edge_pct:
                best_per_event[vb.event_id] = vb

        # Sort by edge descending
        return sorted(best_per_event.values(), key=lambda vb: vb.edge_pct, reverse=True)

    def _calculate_kelly(
        self, effective_odds: float, win_probability: float,
    ) -> float:
        """Calculate Kelly fraction for a combo bet with conservative caps."""
        if effective_odds <= 1.0 or win_probability <= 0 or win_probability >= 1:
            return 0.0

        net_odds = effective_odds - 1.0
        loss_prob = 1.0 - win_probability

        # Standard Kelly: f* = (b*p - q) / b
        raw_kelly = (net_odds * win_probability - loss_prob) / net_odds

        if raw_kelly <= 0:
            return 0.0

        # Apply conservative combo caps
        kelly_cap = self.max_kelly * COMBO_KELLY_CAP
        return min(raw_kelly, kelly_cap)

    def _build_combo(
        self, legs: list[ValueBet], num_legs: int,
    ) -> Optional[ComboRecommendation]:
        """Build a single combo from the top N legs."""
        if len(legs) < num_legs:
            return None

        selected = legs[:num_legs]
        boost_pct = self._get_boost_pct(num_legs)
        if boost_pct <= 0:
            return None

        # Combined odds = product of individual odds
        combined_offered = math.prod(vb.provider_odds for vb in selected)
        combined_fair = math.prod(vb.fair_odds for vb in selected)

        if combined_fair <= 1.0:
            return None

        # Boost applies to profit portion only
        profit_multiplier = 1.0 + boost_pct / 100.0
        effective_odds = 1.0 + (combined_offered - 1.0) * profit_multiplier

        # Edge
        edge = (effective_odds / combined_fair) - 1.0
        edge_pct = edge * 100.0

        if edge_pct <= 0 or edge_pct > MAX_COMBO_EDGE_PCT:
            return None

        # Win probability (assumes independence — different events)
        win_prob = 1.0 / combined_fair

        # EV per unit staked
        ev_per_unit = effective_odds * win_prob - 1.0

        # Kelly sizing
        kelly_fraction = self._calculate_kelly(effective_odds, win_prob)

        # Stake calculation
        recommended_stake = None
        skip_reason = None

        if kelly_fraction > 0 and self.bankroll > 0:
            raw_stake = self.bankroll * kelly_fraction
            capped_stake = min(raw_stake, self.bankroll * COMBO_BANKROLL_CAP_PCT)
            rounded = round_stake_natural(capped_stake)

            min_stake = dynamic_min_stake(self.bankroll)
            if rounded >= min_stake:
                recommended_stake = rounded
            else:
                skip_reason = f"Stake {rounded:.0f} < min {min_stake:.0f} kr"
        else:
            skip_reason = "Negative EV or zero bankroll"

        # Build leg info
        combo_legs = [
            ComboLegInfo(
                event_id=vb.event_id,
                market=vb.market,
                outcome=vb.outcome,
                provider_odds=vb.provider_odds,
                fair_odds=vb.fair_odds,
                fair_probability=vb.fair_probability,
                edge_pct=vb.edge_pct,
                home_team=vb.home_team,
                away_team=vb.away_team,
                sport=vb.sport,
                start_time=vb.start_time,
                point=vb.point,
            )
            for vb in selected
        ]

        return ComboRecommendation(
            provider=selected[0].provider,
            legs=combo_legs,
            num_legs=num_legs,
            combined_offered_odds=combined_offered,
            combined_fair_odds=combined_fair,
            boost_pct=boost_pct,
            profit_multiplier=profit_multiplier,
            effective_odds=effective_odds,
            edge_pct=edge_pct,
            win_probability=win_prob,
            kelly_fraction=kelly_fraction,
            recommended_stake=recommended_stake,
            skip_reason=skip_reason,
            ev_per_unit=ev_per_unit,
        )

    def optimize(
        self,
        value_bets: list[ValueBet],
        provider: str,
        min_legs: int = 3,
    ) -> list[ComboRecommendation]:
        """
        Build optimal combo recommendations for a provider.

        Returns one combo per valid leg count (3, 4, 5, ..., max_legs),
        ranked by EV per unit staked.
        """
        legs = self._filter_and_deduplicate(value_bets, provider)

        if len(legs) < min_legs:
            logger.debug(
                f"[ComboOptimizer] {provider}: only {len(legs)} eligible legs "
                f"(need {min_legs}+)"
            )
            return []

        # Determine valid combo sizes from boost table
        min_boost_legs = min(self.boost_table.keys()) if self.boost_table else min_legs
        effective_min = max(min_legs, min_boost_legs)
        effective_max = min(len(legs), self.max_legs)

        combos = []
        for n in range(effective_min, effective_max + 1):
            combo = self._build_combo(legs, n)
            if combo:
                combos.append(combo)

        # Sort by EV per unit (best first)
        combos.sort(key=lambda c: c.ev_per_unit, reverse=True)

        logger.info(
            f"[ComboOptimizer] {provider}: {len(legs)} eligible legs → "
            f"{len(combos)} combos (best EV/unit: "
            f"{combos[0].ev_per_unit:.4f})" if combos else
            f"[ComboOptimizer] {provider}: {len(legs)} eligible legs → 0 combos"
        )

        return combos
