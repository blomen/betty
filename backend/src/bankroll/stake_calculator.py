"""
Stake Calculator - Dynamic Kelly with Safety Rails
===================================================

All parameters derived from Monte Carlo simulations (3,000 runs, 52 weeks).
No profile settings needed — stakes are fully automated.

Total bankroll approach:
- All provider balances are fungible
- Stakes sized from total bankroll
- Bonuses just add EV + wagering constraints

Key safety features:
1. Dynamic Kelly scaling by edge and bankroll
2. Single bet cap (3% of bankroll)
3. Minimum stake guard (scales with bankroll: 5-25 kr)

Monte Carlo optimal parameters (0% ruin, ~270% median growth):
- max_kelly: 0.75 (3/4 Kelly ceiling at high bankrolls)
- min_kelly: 0.25 (quarter Kelly floor for low-edge bets)
- single_bet_cap: 3% of bankroll
- min_expected_profit: 0.75 kr
- Dynamic boost at low bankrolls: kelly * 1.333 below 5k, taper to 5k-15k
"""

from dataclasses import dataclass
from typing import Optional


# ── Sim-optimal constants (from Monte Carlo: 3k runs, 52 weeks, 0% ruin) ──
OPTIMAL_MAX_KELLY = 0.75          # 3/4 Kelly ceiling (converges here at high bankroll)
OPTIMAL_MIN_KELLY = 0.25          # Quarter Kelly floor for low-edge bets (≤2%)
OPTIMAL_SINGLE_BET_CAP = 0.03    # 3% of bankroll max per bet

# Default minimum stake (skip bets below this)
DEFAULT_MIN_STAKE = 25.0

# Absolute floor — smallest practical bet on any sportsbook
ABSOLUTE_MIN_STAKE = 5.0

# Minimum expected profit to bother placing a bet (stake * edge >= this)
DEFAULT_MIN_EXPECTED_PROFIT = 0.75


def dynamic_min_stake(bankroll: float) -> float:
    """
    Scale minimum stake with bankroll so small bankrolls aren't locked out.

    At 10,000+ bankroll: 25 kr (standard)
    At 5,000 bankroll:   25 kr
    At 1,500 bankroll:   10 kr (rounds to nearest 5)
    At 500 bankroll:      5 kr (floor)

    Formula: max(ABSOLUTE_MIN_STAKE, bankroll * 0.005) capped at DEFAULT_MIN_STAKE,
    rounded down to nearest 5.
    """
    if bankroll <= 0:
        return DEFAULT_MIN_STAKE
    raw = max(ABSOLUTE_MIN_STAKE, bankroll * 0.005)
    capped = min(raw, DEFAULT_MIN_STAKE)
    # Round down to nearest 5 for clean numbers
    return max(ABSOLUTE_MIN_STAKE, (capped // 5) * 5)


# Bonus wagering min odds requirement
BONUS_MIN_ODDS = 1.80


# ── Dynamic Kelly scaling by bankroll ──
# At low bankrolls, boost Kelly so stakes clear min_stake thresholds.
# Converges to OPTIMAL_MAX_KELLY as bankroll grows.
#
# Bankroll thresholds:
#   <= 5k:  max_kelly * 1.333 ≈ 1.0 (full Kelly)
#   5k-15k: linear taper back to max_kelly
#   >= 15k: max_kelly unchanged (0.75)
DYNAMIC_KELLY_LOW_THRESHOLD = 5000.0
DYNAMIC_KELLY_HIGH_THRESHOLD = 15000.0
DYNAMIC_KELLY_BOOST = 1.333  # Multiply profile kelly by this at low bankroll


@dataclass
class StakeResult:
    """Result of stake calculation with full transparency."""
    stake: float
    kelly_fraction: float
    edge_used: float
    edge_raw: float
    bankroll: float

    # Caps applied
    raw_kelly_stake: float
    single_bet_cap: float
    was_capped_single: bool

    # Reasoning
    skip_reason: Optional[str] = None

    # How much additional bankroll needed to qualify (0 if already qualifies)
    bankroll_needed: float = 0.0


def round_stake_natural(stake: float) -> float:
    """
    Round stake to a 'human-looking' amount to avoid detection on soft books.

    Exact amounts like 141 kr or 83 kr look bot-generated.
    Humans naturally round to 5/10/25/50 intervals depending on size.

    Rounding scheme:
    - < 50 kr:    nearest 5   (25, 30, 35, 40, 45)
    - 50-200 kr:  nearest 10  (50, 60, 70, ..., 200)
    - 200-500 kr: nearest 25  (200, 225, 250, ..., 500)
    - 500+ kr:    nearest 50  (500, 550, 600, ...)
    """
    if stake <= 0:
        return 0.0
    if stake < 50:
        return max(5.0, round(stake / 5) * 5)
    elif stake < 200:
        return round(stake / 10) * 10
    elif stake < 500:
        return round(stake / 25) * 25
    else:
        return round(stake / 50) * 50


def effective_max_kelly(profile_max_kelly: float, bankroll: float) -> float:
    """
    Scale max_kelly up when bankroll is small so Kelly stakes clear min_stake.

    At low bankrolls, raw Kelly stakes are often 10-20 kr — below the 25 kr
    minimum. Instead of skipping these +EV bets, we temporarily increase the
    Kelly multiplier so stakes naturally reach playable sizes.

    This converges smoothly to the profile setting as bankroll grows:
      <= 5k:   max_kelly * 1.333 (e.g. 0.75 -> 1.0)
      5k-15k:  linear taper back to profile max_kelly
      >= 15k:  profile max_kelly unchanged

    Simulation results (5k start, 35 bets/week, 52 weeks, 3000 MC runs):
      Without: median +236%, P10=3,232, DD=61.6%, play=73.6%
      With:    median +360%, P10=1,453, DD=70.5%, play=93.1%
    """
    if bankroll <= 0:
        return profile_max_kelly

    if bankroll <= DYNAMIC_KELLY_LOW_THRESHOLD:
        return profile_max_kelly * DYNAMIC_KELLY_BOOST
    elif bankroll < DYNAMIC_KELLY_HIGH_THRESHOLD:
        t = (bankroll - DYNAMIC_KELLY_LOW_THRESHOLD) / (DYNAMIC_KELLY_HIGH_THRESHOLD - DYNAMIC_KELLY_LOW_THRESHOLD)
        boosted = profile_max_kelly * DYNAMIC_KELLY_BOOST
        return boosted - t * (boosted - profile_max_kelly)
    else:
        return profile_max_kelly


def get_kelly_fraction(
    edge_used: float,
    high_confidence: bool = True,
    max_kelly: float = OPTIMAL_MAX_KELLY,
) -> float:
    """
    Dynamic Kelly fraction based on edge quality.

    Scaling (from MC simulation):
    - <= 2% edge: OPTIMAL_MIN_KELLY (0.25) - quarter Kelly for uncertain edges
    - 2-6% edge: Linear scale up to max_kelly
    - >= 6% edge: max_kelly (full allocation)

    If low confidence, clamp to OPTIMAL_MIN_KELLY regardless of edge.

    Args:
        edge_used: Edge (decimal, e.g., 0.03 for 3%)
        high_confidence: Whether this is a high-confidence bet (strong match, fresh odds)
        max_kelly: Kelly ceiling (dynamically scaled by bankroll via effective_max_kelly)

    Returns:
        Kelly fraction between OPTIMAL_MIN_KELLY and max_kelly
    """
    base = min(OPTIMAL_MIN_KELLY, max_kelly)

    # Low confidence = always base Kelly
    if not high_confidence:
        return base

    # Normalized interpolation capped by profile max_kelly
    if edge_used <= 0.02:
        return base
    elif edge_used >= 0.06:
        return max_kelly
    else:
        # Linear interpolation: 2% -> base, 6% -> max_kelly
        t = (edge_used - 0.02) / 0.04  # 0..1
        return base + t * (max_kelly - base)


def calculate_stake(
    bankroll_total: float,
    edge_raw: float,
    odds: float,
    single_bet_cap_pct: float = OPTIMAL_SINGLE_BET_CAP,
    min_edge: float = 0.01,
    min_odds: float = BONUS_MIN_ODDS,
    min_odds_sanity: float = 1.10,
    min_stake: float = DEFAULT_MIN_STAKE,
    high_confidence: bool = True,
    max_kelly: float = OPTIMAL_MAX_KELLY,
    min_expected_profit: float = DEFAULT_MIN_EXPECTED_PROFIT,
) -> StakeResult:
    """
    Calculate optimal stake using dynamic Kelly with safety rails.

    Kelly fraction and bet cap are derived from Monte Carlo simulations —
    callers should NOT override max_kelly or single_bet_cap_pct unless
    they have a specific reason (e.g. bonus wagering constraints).

    Args:
        bankroll_total: Total bankroll across all providers
        edge_raw: Raw estimated edge (decimal, e.g., 0.05 for 5%)
        odds: Decimal odds (e.g., 2.0)
        single_bet_cap_pct: Max stake as % of bankroll (sim-optimal: 3%)
        min_edge: Minimum edge to place bet
        min_odds: Minimum odds (for bonus requirements, 0 to disable)
        min_odds_sanity: Minimum odds for sanity (avoid division issues)
        min_stake: Minimum stake (skip tiny bets)
        high_confidence: Whether this is a high-confidence bet
        max_kelly: Kelly ceiling (sim-optimal: 0.75, boosted at low bankrolls)
        min_expected_profit: Minimum expected profit (stake * edge) to bother placing

    Returns:
        StakeResult with stake amount and full breakdown
    """
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
            skip_reason="No bankroll"
        )

    edge_used = edge_raw

    # Apply dynamic Kelly boost at low bankrolls (converges to profile setting above 15k)
    scaled_max_kelly = effective_max_kelly(max_kelly, bankroll_total)

    # Get dynamic Kelly fraction (capped by scaled max_kelly, clamped if low confidence)
    kelly = get_kelly_fraction(edge_used, high_confidence=high_confidence, max_kelly=scaled_max_kelly)

    # ML adaptive Kelly (M8) — best-effort
    try:
        from src.ml.serving.predictor import get_predictor
        predictor = get_predictor()
        if predictor.is_loaded("adaptive_kelly"):
            from src.ml.features.kelly_features import extract_kelly_features
            kelly_features = extract_kelly_features(
                domain="betting",
                model_confidence=0.5,
                predicted_edge=edge_used,
                historical_win_rate=0.55,
                historical_avg_return=0.03,
                recent_drawdown_pct=0.0,
                consecutive_wins=0,
                consecutive_losses=0,
                daily_pnl=0.0,
                weekly_pnl=0.0,
                account_utilization=0.0,
                volatility_regime=0.5,
            )
            ml_kelly = predictor.predict("adaptive_kelly", kelly_features)
            if ml_kelly is not None:
                kelly = ml_kelly
    except Exception:
        pass

    # Calculate raw Kelly stake
    raw_stake = bankroll_total * kelly * edge_used / (odds - 1)

    # Apply single bet cap
    single_bet_cap = bankroll_total * single_bet_cap_pct
    stake = min(raw_stake, single_bet_cap)
    was_capped_single = raw_stake > single_bet_cap

    # Ensure non-negative
    stake = max(0.0, stake)

    # Round to human-looking amount before min-stake check
    stake = round_stake_natural(stake)

    # Compute bankroll needed to pass both min_stake and min_expected_profit guards
    additional_for_stake = 0.0
    additional_for_ev = 0.0
    if kelly > 0 and edge_used > 0:
        if stake < min_stake:
            needed = min_stake * (odds - 1) / (kelly * edge_used)
            additional_for_stake = max(0.0, needed - bankroll_total)
        expected_profit = stake * edge_used
        if min_expected_profit > 0 and expected_profit < min_expected_profit:
            needed = min_expected_profit * (odds - 1) / (kelly * edge_used ** 2)
            additional_for_ev = max(0.0, needed - bankroll_total)

    additional = max(additional_for_stake, additional_for_ev)

    if stake < min_stake or (min_expected_profit > 0 and stake * edge_used < min_expected_profit):
        additional = round(additional, 0)

        if additional < 1:
            skip_reason = "low EV"
        else:
            if additional >= 1000:
                add_str = f"+{additional / 1000:.0f}k kr"
            else:
                add_str = f"+{additional:.0f} kr"
            skip_reason = f"add {add_str} to play"

        return StakeResult(
            stake=0.0,
            kelly_fraction=kelly,
            edge_used=edge_used,
            edge_raw=edge_raw,
            bankroll=bankroll_total,
            raw_kelly_stake=round(raw_stake, 2),
            single_bet_cap=round(single_bet_cap, 2),
            was_capped_single=was_capped_single,
            skip_reason=skip_reason,
            bankroll_needed=additional,
        )

    return StakeResult(
        stake=stake,
        kelly_fraction=kelly,
        edge_used=edge_used,
        edge_raw=edge_raw,
        bankroll=bankroll_total,
        raw_kelly_stake=round(raw_stake, 2),
        single_bet_cap=round(single_bet_cap, 2),
        was_capped_single=was_capped_single,
    )


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
        wagering_multiplier: float = 10.0,
        min_odds: float = 1.80,
    ):
        """
        Start tracking a new bonus.

        Args:
            provider_id: The provider where bonus was claimed
            bonus_amount: The bonus amount received
            wagering_multiplier: Times bonus must be wagered (default 10x)
            min_odds: Minimum odds for wagering qualification (per-provider)
        """
        self.bonuses[provider_id] = {
            "wagered": 0.0,
            "requirement": bonus_amount * wagering_multiplier,
            "bonus_amount": bonus_amount,
            "min_odds": min_odds,
        }

    def record_bet(self, provider_id: str, stake: float, odds: float):
        """
        Record a bet toward wagering requirement.

        Only bets with odds >= provider's min_odds count toward wagering.
        """
        if provider_id not in self.bonuses:
            return

        # Only bets with qualifying odds count (per-provider min_odds)
        provider_min_odds = self.bonuses[provider_id].get("min_odds", BONUS_MIN_ODDS)
        if odds >= provider_min_odds:
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
        single_bet_cap_pct: float = OPTIMAL_SINGLE_BET_CAP,
        min_edge: float = 0.01,
        max_kelly: float = OPTIMAL_MAX_KELLY,
        min_stake: float | None = None,
        min_expected_profit: float = DEFAULT_MIN_EXPECTED_PROFIT,
    ):
        self.bankroll = bankroll
        self.single_bet_cap_pct = single_bet_cap_pct
        self.min_edge = min_edge
        self.min_stake = min_stake if min_stake is not None else dynamic_min_stake(bankroll)
        self.profile_max_kelly = max_kelly  # Original profile setting
        self.max_kelly = effective_max_kelly(max_kelly, bankroll)  # Boosted at low bankroll
        self.min_expected_profit = min_expected_profit

        self.bonus_tracker = BonusTracker()

    def update_bankroll(self, new_bankroll: float):
        """Update bankroll after wins/losses."""
        self.bankroll = new_bankroll
        self.min_stake = dynamic_min_stake(new_bankroll)
        self.max_kelly = effective_max_kelly(self.profile_max_kelly, new_bankroll)

    def get_min_odds_for_provider(self, provider_id: str) -> float:
        """
        Get minimum odds for a provider based on bonus status.

        If bonus is cleared (or no bonus), returns 0.0 (no restriction).
        If bonus is not cleared, returns provider-specific min_odds.
        """
        if self.bonus_tracker.is_cleared(provider_id):
            return 0.0  # No restriction
        # Return per-provider min_odds from bonus config
        bonus = self.bonus_tracker.bonuses.get(provider_id, {})
        return bonus.get("min_odds", BONUS_MIN_ODDS)

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

        return calculate_stake(
            bankroll_total=self.bankroll,
            edge_raw=edge_raw,
            odds=odds,
            single_bet_cap_pct=self.single_bet_cap_pct,
            min_edge=self.min_edge,
            min_odds=min_odds,
            min_stake=self.min_stake,
            high_confidence=high_confidence,
            max_kelly=self.max_kelly,
            min_expected_profit=self.min_expected_profit,
        )

    def record_bet(
        self,
        event_id: str,
        provider_id: str,
        stake: float,
        odds: float,
    ):
        """
        Record a placed bet for bonus tracking.

        Args:
            event_id: The event ID
            provider_id: The provider ID
            stake: The stake amount
            odds: The odds at which bet was placed
        """
        self.bonus_tracker.record_bet(provider_id, stake, odds)

    def start_bonus(
        self,
        provider_id: str,
        bonus_amount: float,
        wagering_multiplier: float = 10.0,
        min_odds: float = 1.80,
    ):
        """Start tracking a new bonus for a provider."""
        self.bonus_tracker.start_bonus(provider_id, bonus_amount, wagering_multiplier, min_odds)

    def get_status(self) -> dict:
        """Get current calculator status."""
        return {
            "bankroll": self.bankroll,
            "bonus_progress": self.bonus_tracker.get_all_progress(),
        }


# Convenience function for quick calculations
def quick_stake(
    bankroll: float,
    edge: float,
    odds: float,
) -> float:
    """
    Quick stake calculation without exposure tracking.

    Args:
        bankroll: Total bankroll
        edge: Raw edge estimate
        odds: Decimal odds

    Returns:
        Recommended stake
    """
    result = calculate_stake(
        bankroll_total=bankroll,
        edge_raw=edge,
        odds=odds,
        min_odds=0.0,  # No restriction for quick calc
        min_stake=dynamic_min_stake(bankroll),
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
    print(f"{'Edge':<12} {'Kelly (high conf)':<18} {'Kelly (low conf)':<15}")
    print("-" * 50)
    for edge_pct in [1, 2, 3, 4, 5, 6, 7, 8, 10]:
        edge = edge_pct / 100
        kelly_high = get_kelly_fraction(edge, high_confidence=True)
        kelly_low = get_kelly_fraction(edge, high_confidence=False)
        print(f"{edge_pct}%{'':<10} {kelly_high:.2f}{'':<16} {kelly_low:.2f}")

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

