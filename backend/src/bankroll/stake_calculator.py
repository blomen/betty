"""
Stake Calculator - Dynamic Kelly with Safety Rails
===================================================

Total bankroll approach:
- All provider balances are fungible
- Stakes sized from total bankroll
- Bonuses just add EV + wagering constraints

Key safety features:
1. Edge haircut (0.6x) - accounts for estimation error
2. Dynamic Kelly scaling by edge
3. Single bet cap (3% of bankroll)
4. Event/cluster exposure cap (5%)
5. Daily exposure cap (10%)
6. Minimum stake guard (25 kr)

Bonus clearing:
- Track wagered amount per provider
- When bonus fully wagered: skip min_odds requirement
- Use same stake formula regardless of bonus status
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional


# Default minimum stake (skip bets below this)
DEFAULT_MIN_STAKE = 25.0

# Bonus wagering min odds requirement
BONUS_MIN_ODDS = 1.80


@dataclass
class StakeResult:
    """Result of stake calculation with full transparency."""
    stake: float
    kelly_fraction: float
    edge_used: float  # After haircut
    edge_raw: float
    bankroll: float

    # Caps applied
    raw_kelly_stake: float
    single_bet_cap: float
    was_capped_single: bool
    was_capped_event: bool
    was_capped_daily: bool

    # Reasoning
    skip_reason: Optional[str] = None


def get_kelly_fraction(edge_used: float, high_confidence: bool = True) -> float:
    """
    Dynamic Kelly fraction based on edge (after haircut).

    Scaling:
    - <= 2% edge: 0.25 (Quarter Kelly) - conservative base
    - 2-6% edge: Linear scale 0.25 -> 0.75
    - >= 6% edge: 0.75 (capped) - never full degen

    If low confidence, clamp to Quarter Kelly regardless of edge.

    Args:
        edge_used: Edge after haircut (decimal, e.g., 0.03 for 3%)
        high_confidence: Whether this is a high-confidence bet (strong match, fresh odds)

    Returns:
        Kelly fraction (0.25 to 0.75)
    """
    # Low confidence = always Quarter Kelly
    if not high_confidence:
        return 0.25

    # Normalized interpolation (cleaner, avoids unit mistakes)
    if edge_used <= 0.02:
        return 0.25
    elif edge_used >= 0.06:
        return 0.75
    else:
        # Linear interpolation: 2% -> 0.25, 6% -> 0.75
        t = (edge_used - 0.02) / 0.04  # 0..1
        return 0.25 + t * 0.50


def calculate_stake(
    bankroll_total: float,
    edge_raw: float,
    odds: float,
    edge_haircut: float = 0.60,
    single_bet_cap_pct: float = 0.03,
    event_exposure_remaining: Optional[float] = None,
    daily_exposure_remaining: Optional[float] = None,
    min_edge: float = 0.01,
    min_odds: float = BONUS_MIN_ODDS,
    min_odds_sanity: float = 1.10,
    min_stake: float = DEFAULT_MIN_STAKE,
    high_confidence: bool = True,
) -> StakeResult:
    """
    Calculate optimal stake using dynamic Kelly with safety rails.

    Args:
        bankroll_total: Total bankroll across all providers
        edge_raw: Raw estimated edge (decimal, e.g., 0.05 for 5%)
        odds: Decimal odds (e.g., 2.0)
        edge_haircut: Multiplier to account for estimation error (0.6 = 60%)
        single_bet_cap_pct: Max stake as % of bankroll (0.03 = 3%)
        event_exposure_remaining: Max additional exposure allowed on this event
        daily_exposure_remaining: Max additional daily exposure allowed
        min_edge: Minimum edge to place bet
        min_odds: Minimum odds (for bonus requirements, 0 to disable)
        min_odds_sanity: Minimum odds for sanity (avoid division issues)
        min_stake: Minimum stake (skip tiny bets)
        high_confidence: Whether this is a high-confidence bet

    Returns:
        StakeResult with stake amount and full breakdown
    """
    was_capped_daily = False

    # Sanity guard: odds too close to 1.0 cause absurd stakes
    if odds <= min_odds_sanity:
        return StakeResult(
            stake=0.0,
            kelly_fraction=0.0,
            edge_used=0.0,
            edge_raw=edge_raw,
            bankroll=bankroll_total,
            raw_kelly_stake=0.0,
            single_bet_cap=0.0,
            was_capped_single=False,
            was_capped_event=False,
            was_capped_daily=False,
            skip_reason=f"Odds {odds:.2f} below sanity minimum {min_odds_sanity}"
        )

    # Check min odds (for bonus wagering - skip if min_odds is 0)
    if min_odds > 0 and odds < min_odds:
        return StakeResult(
            stake=0.0,
            kelly_fraction=0.0,
            edge_used=0.0,
            edge_raw=edge_raw,
            bankroll=bankroll_total,
            raw_kelly_stake=0.0,
            single_bet_cap=0.0,
            was_capped_single=False,
            was_capped_event=False,
            was_capped_daily=False,
            skip_reason=f"Odds {odds:.2f} below minimum {min_odds} (bonus requirement)"
        )

    if edge_raw < min_edge:
        return StakeResult(
            stake=0.0,
            kelly_fraction=0.0,
            edge_used=0.0,
            edge_raw=edge_raw,
            bankroll=bankroll_total,
            raw_kelly_stake=0.0,
            single_bet_cap=0.0,
            was_capped_single=False,
            was_capped_event=False,
            was_capped_daily=False,
            skip_reason=f"Edge {edge_raw*100:.1f}% below minimum {min_edge*100:.1f}%"
        )

    if bankroll_total <= 0:
        return StakeResult(
            stake=0.0,
            kelly_fraction=0.0,
            edge_used=0.0,
            edge_raw=edge_raw,
            bankroll=bankroll_total,
            raw_kelly_stake=0.0,
            single_bet_cap=0.0,
            was_capped_single=False,
            was_capped_event=False,
            was_capped_daily=False,
            skip_reason="No bankroll"
        )

    # Apply edge haircut (accounts for estimation error)
    edge_used = edge_raw * edge_haircut

    # Get dynamic Kelly fraction (clamped if low confidence)
    kelly = get_kelly_fraction(edge_used, high_confidence=high_confidence)

    # Calculate raw Kelly stake
    raw_stake = bankroll_total * kelly * edge_used / (odds - 1)

    # Apply single bet cap
    single_bet_cap = bankroll_total * single_bet_cap_pct
    stake = min(raw_stake, single_bet_cap)
    was_capped_single = raw_stake > single_bet_cap

    # Apply event exposure cap (if provided)
    was_capped_event = False
    if event_exposure_remaining is not None and stake > event_exposure_remaining:
        stake = event_exposure_remaining
        was_capped_event = True

    # Apply daily exposure cap (if provided)
    if daily_exposure_remaining is not None and stake > daily_exposure_remaining:
        stake = daily_exposure_remaining
        was_capped_daily = True

    # Ensure non-negative
    stake = max(0.0, stake)

    # Minimum stake guard - skip tiny bets
    if stake < min_stake:
        return StakeResult(
            stake=0.0,
            kelly_fraction=kelly,
            edge_used=edge_used,
            edge_raw=edge_raw,
            bankroll=bankroll_total,
            raw_kelly_stake=round(raw_stake, 2),
            single_bet_cap=round(single_bet_cap, 2),
            was_capped_single=was_capped_single,
            was_capped_event=was_capped_event,
            was_capped_daily=was_capped_daily,
            skip_reason=f"Stake {stake:.0f} kr below minimum {min_stake:.0f} kr"
        )

    return StakeResult(
        stake=round(stake, 2),
        kelly_fraction=kelly,
        edge_used=edge_used,
        edge_raw=edge_raw,
        bankroll=bankroll_total,
        raw_kelly_stake=round(raw_stake, 2),
        single_bet_cap=round(single_bet_cap, 2),
        was_capped_single=was_capped_single,
        was_capped_event=was_capped_event,
        was_capped_daily=was_capped_daily,
    )


class EventExposureTracker:
    """
    Track exposure per event/cluster to prevent correlation blowups.

    Example: Multiple bets on same match (home/away/draw) or
    correlated events (same team in multiple markets).
    """

    def __init__(self, max_event_exposure_pct: float = 0.05):
        """
        Args:
            max_event_exposure_pct: Max exposure per event as % of bankroll (0.05 = 5%)
        """
        self.max_event_exposure_pct = max_event_exposure_pct
        self.exposures: dict[str, float] = {}  # event_id -> total exposure

    def get_remaining_exposure(self, event_id: str, bankroll: float) -> float:
        """Get remaining allowed exposure for an event."""
        max_exposure = bankroll * self.max_event_exposure_pct
        current = self.exposures.get(event_id, 0.0)
        return max(0.0, max_exposure - current)

    def record_bet(self, event_id: str, stake: float):
        """Record a bet's exposure."""
        self.exposures[event_id] = self.exposures.get(event_id, 0.0) + stake

    def get_exposure(self, event_id: str) -> float:
        """Get current exposure for an event."""
        return self.exposures.get(event_id, 0.0)

    def reset(self):
        """Reset all exposures (e.g., after events settle)."""
        self.exposures.clear()


class DailyExposureTracker:
    """
    Track daily exposure to prevent over-trading.

    Resets automatically at midnight.
    """

    def __init__(self, max_daily_exposure_pct: float = 0.10):
        """
        Args:
            max_daily_exposure_pct: Max daily exposure as % of bankroll (0.10 = 10%)
        """
        self.max_daily_exposure_pct = max_daily_exposure_pct
        self.daily_exposure: float = 0.0
        self.last_reset: date = date.today()

    def _maybe_reset(self):
        """Reset if it's a new day."""
        today = date.today()
        if today > self.last_reset:
            self.daily_exposure = 0.0
            self.last_reset = today

    def get_remaining_exposure(self, bankroll: float) -> float:
        """Get remaining allowed exposure for today."""
        self._maybe_reset()
        max_daily = bankroll * self.max_daily_exposure_pct
        return max(0.0, max_daily - self.daily_exposure)

    def record_bet(self, stake: float):
        """Record a bet's exposure."""
        self._maybe_reset()
        self.daily_exposure += stake

    def get_daily_exposure(self) -> float:
        """Get current daily exposure."""
        self._maybe_reset()
        return self.daily_exposure

    def reset(self):
        """Force reset (for testing)."""
        self.daily_exposure = 0.0
        self.last_reset = date.today()


class BonusTracker:
    """
    Track bonus wagering progress per provider.

    When wagered_amount >= wagering_requirement, bonus is "cleared"
    and min_odds requirement is removed.
    """

    def __init__(self):
        # provider_id -> {wagered: float, requirement: float}
        self.bonuses: dict[str, dict] = {}

    def start_bonus(
        self,
        provider_id: str,
        bonus_amount: float,
        wagering_multiplier: float = 10.0
    ):
        """
        Start tracking a new bonus.

        Args:
            provider_id: The provider where bonus was claimed
            bonus_amount: The bonus amount received
            wagering_multiplier: Times bonus must be wagered (default 10x)
        """
        self.bonuses[provider_id] = {
            "wagered": 0.0,
            "requirement": bonus_amount * wagering_multiplier,
            "bonus_amount": bonus_amount,
        }

    def record_bet(self, provider_id: str, stake: float, odds: float):
        """
        Record a bet toward wagering requirement.

        Only bets with odds >= 1.80 count toward wagering.
        """
        if provider_id not in self.bonuses:
            return

        # Only bets with qualifying odds count
        if odds >= BONUS_MIN_ODDS:
            self.bonuses[provider_id]["wagered"] += stake

    def is_cleared(self, provider_id: str) -> bool:
        """Check if bonus wagering requirement is met."""
        if provider_id not in self.bonuses:
            return True  # No bonus = already cleared

        bonus = self.bonuses[provider_id]
        return bonus["wagered"] >= bonus["requirement"]

    def get_progress(self, provider_id: str) -> dict:
        """Get wagering progress for a provider."""
        if provider_id not in self.bonuses:
            return {"wagered": 0.0, "requirement": 0.0, "cleared": True}

        bonus = self.bonuses[provider_id]
        return {
            "wagered": bonus["wagered"],
            "requirement": bonus["requirement"],
            "bonus_amount": bonus.get("bonus_amount", 0.0),
            "progress_pct": min(100.0, bonus["wagered"] / bonus["requirement"] * 100) if bonus["requirement"] > 0 else 100.0,
            "cleared": bonus["wagered"] >= bonus["requirement"],
        }

    def get_all_progress(self) -> dict[str, dict]:
        """Get wagering progress for all providers."""
        return {pid: self.get_progress(pid) for pid in self.bonuses}


class StakeCalculator:
    """
    Main stake calculator with exposure tracking and bonus awareness.

    Usage:
        calc = StakeCalculator(bankroll=10000)

        # Check if bonus is cleared for this provider
        min_odds = 0.0 if calc.bonus_tracker.is_cleared("unibet") else 1.80

        result = calc.calculate(
            edge_raw=0.05,
            odds=2.0,
            event_id="match_123",
            provider_id="unibet",
            min_odds=min_odds,
        )

        if result.stake > 0:
            calc.record_bet(
                event_id="match_123",
                provider_id="unibet",
                stake=result.stake,
                odds=2.0,
            )
    """

    def __init__(
        self,
        bankroll: float,
        edge_haircut: float = 0.60,
        single_bet_cap_pct: float = 0.03,
        event_cap_pct: float = 0.05,
        daily_cap_pct: float = 0.10,
        min_edge: float = 0.01,
        min_stake: float = DEFAULT_MIN_STAKE,
    ):
        self.bankroll = bankroll
        self.edge_haircut = edge_haircut
        self.single_bet_cap_pct = single_bet_cap_pct
        self.min_edge = min_edge
        self.min_stake = min_stake

        self.event_tracker = EventExposureTracker(event_cap_pct)
        self.daily_tracker = DailyExposureTracker(daily_cap_pct)
        self.bonus_tracker = BonusTracker()

    def update_bankroll(self, new_bankroll: float):
        """Update bankroll after wins/losses."""
        self.bankroll = new_bankroll

    def get_min_odds_for_provider(self, provider_id: str) -> float:
        """
        Get minimum odds for a provider based on bonus status.

        If bonus is cleared (or no bonus), returns 0.0 (no restriction).
        If bonus is not cleared, returns BONUS_MIN_ODDS (1.80).
        """
        if self.bonus_tracker.is_cleared(provider_id):
            return 0.0  # No restriction
        return BONUS_MIN_ODDS

    def calculate(
        self,
        edge_raw: float,
        odds: float,
        event_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        high_confidence: bool = True,
        min_odds: Optional[float] = None,
    ) -> StakeResult:
        """
        Calculate stake for a bet.

        Args:
            edge_raw: Raw estimated edge (decimal)
            odds: Decimal odds
            event_id: Optional event ID for exposure tracking
            provider_id: Optional provider ID for bonus tracking
            high_confidence: Whether this is a high-confidence bet
                (strong match score, fresh odds, low slippage history)
            min_odds: Override for minimum odds (None = auto-detect from bonus status)

        Returns:
            StakeResult
        """
        # Auto-detect min_odds from bonus status if not specified
        if min_odds is None:
            if provider_id:
                min_odds = self.get_min_odds_for_provider(provider_id)
            else:
                min_odds = BONUS_MIN_ODDS  # Default to bonus requirement

        event_exposure = None
        if event_id:
            event_exposure = self.event_tracker.get_remaining_exposure(
                event_id, self.bankroll
            )

        daily_exposure = self.daily_tracker.get_remaining_exposure(self.bankroll)

        return calculate_stake(
            bankroll_total=self.bankroll,
            edge_raw=edge_raw,
            odds=odds,
            edge_haircut=self.edge_haircut,
            single_bet_cap_pct=self.single_bet_cap_pct,
            event_exposure_remaining=event_exposure,
            daily_exposure_remaining=daily_exposure,
            min_edge=self.min_edge,
            min_odds=min_odds,
            min_stake=self.min_stake,
            high_confidence=high_confidence,
        )

    def record_bet(
        self,
        event_id: str,
        provider_id: str,
        stake: float,
        odds: float,
    ):
        """
        Record a placed bet for exposure and bonus tracking.

        Args:
            event_id: The event ID
            provider_id: The provider ID
            stake: The stake amount
            odds: The odds at which bet was placed
        """
        self.event_tracker.record_bet(event_id, stake)
        self.daily_tracker.record_bet(stake)
        self.bonus_tracker.record_bet(provider_id, stake, odds)

    def start_bonus(
        self,
        provider_id: str,
        bonus_amount: float,
        wagering_multiplier: float = 10.0,
    ):
        """Start tracking a new bonus for a provider."""
        self.bonus_tracker.start_bonus(provider_id, bonus_amount, wagering_multiplier)

    def get_status(self) -> dict:
        """Get current calculator status."""
        return {
            "bankroll": self.bankroll,
            "daily_exposure": self.daily_tracker.get_daily_exposure(),
            "daily_remaining": self.daily_tracker.get_remaining_exposure(self.bankroll),
            "event_exposures": dict(self.event_tracker.exposures),
            "bonus_progress": self.bonus_tracker.get_all_progress(),
        }

    def reset_event_exposures(self):
        """Reset event exposure tracking (after events settle)."""
        self.event_tracker.reset()

    def reset_daily_exposure(self):
        """Force reset daily exposure (for testing)."""
        self.daily_tracker.reset()


# Convenience function for quick calculations
def quick_stake(
    bankroll: float,
    edge: float,
    odds: float,
    haircut: float = 0.60,
) -> float:
    """
    Quick stake calculation without exposure tracking.

    Args:
        bankroll: Total bankroll
        edge: Raw edge estimate
        odds: Decimal odds
        haircut: Edge haircut (default 0.60)

    Returns:
        Recommended stake
    """
    result = calculate_stake(
        bankroll_total=bankroll,
        edge_raw=edge,
        odds=odds,
        edge_haircut=haircut,
        min_odds=0.0,  # No restriction for quick calc
    )
    return result.stake


if __name__ == "__main__":
    # Demo
    print("="*60)
    print("STAKE CALCULATOR DEMO")
    print("="*60)

    calc = StakeCalculator(bankroll=10000)

    print("\n[KELLY FRACTION SCALING]")
    print("-" * 50)
    print(f"{'Raw Edge':<12} {'Haircut Edge':<14} {'Kelly (high conf)':<18} {'Kelly (low conf)':<15}")
    print("-" * 50)
    for edge_pct in [1, 2, 3, 4, 5, 6, 7, 8, 10]:
        edge = edge_pct / 100
        edge_used = edge * 0.60  # haircut
        kelly_high = get_kelly_fraction(edge_used, high_confidence=True)
        kelly_low = get_kelly_fraction(edge_used, high_confidence=False)
        print(f"{edge_pct}%{'':<10} {edge_used*100:.1f}%{'':<12} {kelly_high:.2f}{'':<16} {kelly_low:.2f}")

    print("\n[STAKE EXAMPLES - WITH BONUS REQUIREMENT (min odds 1.80)]")
    print("-" * 60)
    print(f"{'Edge':<8} {'Odds':<8} {'Stake':<10} {'Kelly':<8} {'Skip Reason':<25}")
    print("-" * 60)

    examples = [
        (0.02, 2.0),   # Low edge
        (0.03, 2.0),   # Normal edge
        (0.05, 2.0),   # Good edge
        (0.05, 1.5),   # Below min odds
        (0.02, 2.0),   # Edge too low after stake calc -> min stake
    ]

    for edge, odds in examples:
        result = calc.calculate(edge, odds, event_id="test", min_odds=1.80)
        if result.stake > 0:
            print(f"{edge*100:.0f}%{'':<6} {odds:<8.2f} {result.stake:<10.0f} {result.kelly_fraction:<8.2f} -")
        else:
            print(f"{edge*100:.0f}%{'':<6} {odds:<8.2f} {'SKIP':<10} {'-':<8} {result.skip_reason}")

    print("\n[BONUS CLEARED - NO MIN ODDS RESTRICTION]")
    print("-" * 60)

    # Simulate bonus being cleared
    calc2 = StakeCalculator(bankroll=10000)

    # Bet at 1.50 odds (below 1.80) - should SKIP with bonus
    result_with_bonus = calc2.calculate(0.05, 1.50, min_odds=1.80)
    print(f"With bonus (min 1.80): odds=1.50 -> {result_with_bonus.stake:.0f} ({result_with_bonus.skip_reason})")

    # Same bet without bonus requirement
    result_cleared = calc2.calculate(0.05, 1.50, min_odds=0.0)
    print(f"Bonus cleared (no min): odds=1.50 -> {result_cleared.stake:.0f} kr")

    print("\n[DAILY CAP DEMO - 10% MAX PER DAY]")
    print("-" * 40)

    calc3 = StakeCalculator(bankroll=10000)
    print(f"Max daily: {calc3.bankroll * 0.10:.0f} kr")

    for i in range(6):
        result = calc3.calculate(edge_raw=0.08, odds=2.0, event_id=f"event_{i}", min_odds=0.0)
        if result.stake > 0:
            calc3.record_bet(f"event_{i}", "provider", result.stake, 2.0)
            print(f"Bet {i+1}: {result.stake:.0f} kr (daily capped: {result.was_capped_daily})")
        else:
            print(f"Bet {i+1}: SKIP - {result.skip_reason}")

    print(f"\nTotal daily: {calc3.daily_tracker.get_daily_exposure():.0f} kr")
