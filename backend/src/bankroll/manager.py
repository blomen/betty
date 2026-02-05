"""
Bankroll Manager

Handles:
- Total bankroll calculation (sum of provider balances)
- Kelly criterion stake calculation
- Auto-calculated bonus matching stakes
- Risk-aware stake adjustment with noise injection
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class StakeRecommendation:
    """Recommended stake for an opportunity."""
    stake: float
    max_stake: float          # Limited by bankroll %
    kelly_stake: float        # Pure Kelly (before limits)
    provider_balance: float   # Available at provider
    reason: str               # Why this stake


def kelly_stake(
    odds: float,
    win_probability: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    max_stake_pct: float = 5.0,
) -> StakeRecommendation:
    """
    Calculate stake using Kelly Criterion.
    
    Kelly formula: f* = (bp - q) / b
    where:
        b = odds - 1 (net odds)
        p = win probability
        q = 1 - p (loss probability)
    
    Args:
        odds: Decimal odds
        win_probability: Probability of winning (from Polymarket)
        bankroll: Total bankroll
        kelly_fraction: Fraction of Kelly to use (0.25 = quarter Kelly)
        max_stake_pct: Maximum stake as % of bankroll
    
    Returns:
        StakeRecommendation with calculated stake
    """
    if odds <= 1 or win_probability <= 0 or win_probability >= 1:
        return StakeRecommendation(
            stake=0,
            max_stake=0,
            kelly_stake=0,
            provider_balance=0,
            reason="Invalid odds or probability",
        )
    
    b = odds - 1  # Net odds
    p = win_probability
    q = 1 - p
    
    # Kelly fraction
    kelly_f = (b * p - q) / b
    
    # No edge = no bet
    if kelly_f <= 0:
        return StakeRecommendation(
            stake=0,
            max_stake=bankroll * max_stake_pct / 100,
            kelly_stake=0,
            provider_balance=0,
            reason="No edge (Kelly <= 0)",
        )
    
    # Apply Kelly fraction (e.g., quarter Kelly)
    adjusted_f = kelly_f * kelly_fraction
    kelly_stake_amount = bankroll * adjusted_f
    
    # Apply max stake limit
    max_stake = bankroll * max_stake_pct / 100
    final_stake = min(kelly_stake_amount, max_stake)
    
    reason = "Kelly" if final_stake == kelly_stake_amount else "Max stake limit"
    
    return StakeRecommendation(
        stake=round(final_stake, 2),
        max_stake=round(max_stake, 2),
        kelly_stake=round(kelly_stake_amount, 2),
        provider_balance=0,  # Filled by caller
        reason=reason,
    )


def arb_stakes(
    outcomes: list[dict],
    total_stake: float,
) -> list[dict]:
    """
    Calculate stakes for arbitrage betting.
    
    Distributes total stake across outcomes to guarantee equal return.
    
    Args:
        outcomes: [{outcome, odds}, ...]
        total_stake: Total amount to stake across all outcomes
    
    Returns:
        [{outcome, stake, return}, ...]
    """
    implied_sum = sum(1 / o["odds"] for o in outcomes)
    
    if implied_sum >= 1:
        # No arb exists
        return []
    
    stakes = []
    for o in outcomes:
        stake = (1 / o["odds"]) / implied_sum * total_stake
        potential_return = stake * o["odds"]
        stakes.append({
            "outcome": o["outcome"],
            "stake": round(stake, 2),
            "return": round(potential_return, 2),
        })
    
    return stakes


def bonus_stakes(
    anchor_odds: float,
    anchor_stake: float,
    hedge_odds: float,
    is_free_bet: bool = False,
) -> dict:
    """
    Calculate stakes for bonus matching.
    
    Args:
        anchor_odds: Odds for the bonus/anchor bet
        anchor_stake: Amount of the anchor bet
        hedge_odds: Best opposing odds at another bookie
        is_free_bet: True if anchor is a free bet (stake not returned)
    
    Returns:
        {anchor_stake, hedge_stake, guaranteed_profit, retention_pct}
    """
    if is_free_bet:
        # SNR free bet: only profit returned on win
        anchor_return = anchor_stake * (anchor_odds - 1)
    else:
        # Normal bet: stake + profit returned
        anchor_return = anchor_stake * anchor_odds
    
    # Hedge to cover the anchor return
    hedge_stake = anchor_return / hedge_odds
    
    if is_free_bet:
        # Profit is anchor_return minus hedge_stake
        profit = anchor_return - hedge_stake
        retention = profit / anchor_stake * 100
    else:
        # Qualifying bet: total loss
        total_staked = anchor_stake + hedge_stake
        profit = anchor_return - total_staked  # Usually negative
        retention = -profit / anchor_stake * 100  # Flip sign for clarity
    
    return {
        "anchor_stake": anchor_stake,
        "hedge_stake": round(hedge_stake, 2),
        "guaranteed_profit": round(profit, 2),
        "retention_pct": round(retention, 1),
    }


class BankrollManager:
    """
    Manages bankroll across providers and calculates stakes.
    
    In production, this queries the database for provider balances
    and profile settings. For now, it's a simple in-memory version.
    """
    
    def __init__(
        self,
        total_bankroll: float = 0,
        kelly_fraction: float = 0.25,
        max_stake_pct: float = 5.0,
        min_edge_pct: float = 2.0,
    ):
        self.total_bankroll = total_bankroll
        self.kelly_fraction = kelly_fraction
        self.max_stake_pct = max_stake_pct
        self.min_edge_pct = min_edge_pct
        self.provider_balances: dict[str, float] = {}
    
    def set_balance(self, provider: str, balance: float) -> None:
        """Set balance for a provider."""
        self.provider_balances[provider] = balance
        self.total_bankroll = sum(self.provider_balances.values())
    
    def get_balance(self, provider: str) -> float:
        """Get balance for a provider."""
        return self.provider_balances.get(provider, 0)
    
    def calculate_value_stake(
        self,
        odds: float,
        fair_odds: float,
        provider: str,
    ) -> StakeRecommendation:
        """Calculate stake for a value bet."""
        win_prob = 1 / fair_odds
        rec = kelly_stake(
            odds=odds,
            win_probability=win_prob,
            bankroll=self.total_bankroll,
            kelly_fraction=self.kelly_fraction,
            max_stake_pct=self.max_stake_pct,
        )
        rec.provider_balance = self.get_balance(provider)
        
        # Limit to provider balance
        if rec.stake > rec.provider_balance:
            rec.stake = rec.provider_balance
            rec.reason = "Limited by provider balance"
        
        return rec
    
    def calculate_arb_stakes(
        self,
        outcomes: list[dict],
        total_stake: Optional[float] = None,
    ) -> list[dict]:
        """
        Calculate stakes for arbitrage.
        
        If total_stake not provided, uses max_stake_pct of bankroll.
        """
        if total_stake is None:
            total_stake = self.total_bankroll * self.max_stake_pct / 100
        
        return arb_stakes(outcomes, total_stake)
    
    def calculate_bonus_stakes(
        self,
        anchor_odds: float,
        anchor_stake: float,
        hedge_odds: float,
        is_free_bet: bool = False,
    ) -> dict:
        """Calculate stakes for bonus matching."""
        return bonus_stakes(anchor_odds, anchor_stake, hedge_odds, is_free_bet)


@dataclass
class RiskAwareStakeRecommendation:
    """Stake recommendation with risk adjustment."""

    # Core stake values
    base_stake: float          # Kelly stake before adjustment
    risk_adjusted_stake: float  # After risk reduction
    final_stake: float          # After noise injection
    max_stake: float            # Profile limit

    # Risk metrics
    risk_score: float           # Provider risk score (0-1)
    risk_level: str             # "low", "medium", "high", "critical"

    # EV and utility
    expected_value: float       # EV at base stake
    risk_penalty: float         # λ * stake * risk * multiplier
    utility: float              # EV - risk_penalty

    # Noise tracking
    noise_applied: float        # Noise amount added
    noise_pct: float            # Noise as percentage

    # Metadata
    provider_balance: float     # Available at provider
    reason: str                 # Why this stake
    skip_reason: Optional[str] = None  # If provider should be skipped


class RiskAwareBankrollManager:
    """
    Bankroll manager with risk-aware stake calculation.

    Extends standard Kelly criterion with:
    1. Risk regularization: U = EV - λ · RiskPenalty
    2. Stake reduction based on provider risk score
    3. Noise injection for behavioral entropy
    4. Provider cooldown enforcement

    Usage:
        db = get_session()
        manager = RiskAwareBankrollManager(db)
        rec = manager.calculate_risk_aware_stake(
            odds=2.40,
            fair_odds=2.22,
            provider_id="unibet"
        )
    """

    def __init__(self, db: Session):
        self.db = db
        self._risk_calculator = None
        self._regularizer = None
        self._noise_injector = None
        self._profile = None

    @property
    def risk_calculator(self):
        """Lazy load risk calculator."""
        if self._risk_calculator is None:
            from ..risk.calculator import RiskCalculator
            self._risk_calculator = RiskCalculator(self.db)
        return self._risk_calculator

    @property
    def regularizer(self):
        """Lazy load utility regularizer."""
        if self._regularizer is None:
            from ..risk.regularizer import UtilityRegularizer
            self._regularizer = UtilityRegularizer(self.db)
        return self._regularizer

    @property
    def noise_injector(self):
        """Lazy load stake noise injector."""
        if self._noise_injector is None:
            from ..risk.stake_noise import StakeNoiseInjector
            self._noise_injector = StakeNoiseInjector(self.db)
        return self._noise_injector

    def _get_profile(self):
        """Get active profile with settings."""
        if self._profile is None:
            from ..db.models import get_active_profile
            self._profile = get_active_profile(self.db)
        return self._profile

    def _get_provider_balance(self, provider_id: str) -> float:
        """Get provider balance for active profile."""
        from ..db.models import get_profile_balance
        profile = self._get_profile()
        if not profile:
            return 0.0
        return get_profile_balance(self.db, profile.id, provider_id)

    def _get_total_bankroll(self) -> float:
        """Get total bankroll for active profile."""
        from ..db.models import get_total_profile_bankroll
        profile = self._get_profile()
        if not profile:
            return 0.0
        return get_total_profile_bankroll(self.db, profile.id)

    def calculate_risk_aware_stake(
        self,
        odds: float,
        fair_odds: float,
        provider_id: str,
        force: bool = False,
    ) -> RiskAwareStakeRecommendation:
        """
        Calculate risk-aware stake for a betting opportunity.

        Steps:
        1. Calculate base Kelly stake
        2. Check if provider should be skipped (cooldown/critical)
        3. Apply risk regularization
        4. Reduce stake based on risk score
        5. Apply account warmup multiplier for new accounts
        6. Inject noise for behavioral entropy

        Args:
            odds: Provider odds
            fair_odds: Sharp/true odds
            provider_id: Provider offering the odds
            force: Skip provider checks (cooldown, etc.)

        Returns:
            RiskAwareStakeRecommendation with adjusted stake
        """
        profile = self._get_profile()
        if not profile:
            return self._empty_recommendation("No active profile")

        # Get provider balance and total bankroll
        provider_balance = self._get_provider_balance(provider_id)
        total_bankroll = self._get_total_bankroll()

        if total_bankroll <= 0:
            return self._empty_recommendation("No bankroll available")

        # Check if provider should be skipped
        if not force:
            should_skip, skip_reason = self.regularizer.should_skip_provider(provider_id)
            if should_skip:
                return self._empty_recommendation(skip_reason)

        # Calculate base Kelly stake
        win_prob = 1 / fair_odds if fair_odds > 1 else 0.5
        base_rec = kelly_stake(
            odds=odds,
            win_probability=win_prob,
            bankroll=total_bankroll,
            kelly_fraction=profile.kelly_fraction,
            max_stake_pct=profile.max_stake_pct,
        )

        if base_rec.stake <= 0:
            return self._empty_recommendation(base_rec.reason)

        # Apply risk regularization
        regularized = self.regularizer.regularize(
            event_id="",  # Not tracking event here
            provider_id=provider_id,
            outcome="",
            odds=odds,
            fair_odds=fair_odds,
            base_stake=base_rec.stake,
        )

        # Get risk assessment for account warmup info
        assessment = self.risk_calculator.assess_provider(provider_id)
        account_age = assessment.features.account_age_days

        # Apply account warmup multiplier for new accounts
        # Ramp up: day 0 = 30%, day 14 = 100%
        warmup_multiplier = 1.0
        warmup_reason = None
        if account_age < 14:
            warmup_multiplier = 0.3 + (account_age / 14) * 0.7
            warmup_reason = f"Account warmup ({account_age}d old, {warmup_multiplier:.0%} stake)"

        # Apply warmup multiplier to risk-adjusted stake
        warmup_adjusted_stake = regularized.risk_adjusted_stake * warmup_multiplier

        # Inject noise
        noisy = self.noise_injector.inject_noise(
            stake=warmup_adjusted_stake,
            risk_score=regularized.risk_score,
            max_stake=min(base_rec.max_stake, provider_balance),
            min_stake=1.0,
        )

        # Limit to provider balance
        final_stake = min(noisy.final_stake, provider_balance)

        # Determine reason (prioritize warmup reason for new accounts)
        if warmup_reason:
            reason = warmup_reason
        elif final_stake < base_rec.stake * 0.9:
            reason = f"Risk-adjusted (score={regularized.risk_score:.2f})"
        elif noisy.was_rounded:
            reason = "Adjusted from round number"
        else:
            reason = base_rec.reason

        return RiskAwareStakeRecommendation(
            base_stake=base_rec.stake,
            risk_adjusted_stake=regularized.risk_adjusted_stake,
            final_stake=final_stake,
            max_stake=base_rec.max_stake,
            risk_score=regularized.risk_score,
            risk_level=assessment.risk_level,
            expected_value=regularized.expected_value,
            risk_penalty=regularized.risk_penalty,
            utility=regularized.utility,
            noise_applied=noisy.noise_applied,
            noise_pct=noisy.noise_pct,
            provider_balance=provider_balance,
            reason=reason,
        )

    def _empty_recommendation(self, reason: str) -> RiskAwareStakeRecommendation:
        """Create empty recommendation for skip cases."""
        return RiskAwareStakeRecommendation(
            base_stake=0,
            risk_adjusted_stake=0,
            final_stake=0,
            max_stake=0,
            risk_score=0,
            risk_level="unknown",
            expected_value=0,
            risk_penalty=0,
            utility=0,
            noise_applied=0,
            noise_pct=0,
            provider_balance=0,
            reason="Skipped",
            skip_reason=reason,
        )

    def select_opportunity(
        self,
        opportunities: list[dict],
        stake: float,
        deterministic: bool = False,
    ):
        """
        Select an opportunity using stochastic selection.

        Args:
            opportunities: List of opportunity dicts with:
                - event_id, provider_id, outcome, odds, fair_odds
            stake: Base stake for utility calculation
            deterministic: If True, always pick highest utility

        Returns:
            RankedOpportunity or None
        """
        from ..risk.selector import StochasticSelector

        selector = StochasticSelector(self.db)

        if deterministic:
            return selector.select_deterministic(opportunities, stake)
        else:
            return selector.select(opportunities, stake)

    def record_bet_behavioral_data(
        self,
        bet_id: int,
        risk_score: float,
        utility: float,
        selection_probability: Optional[float] = None,
        noise_applied: Optional[float] = None,
    ) -> None:
        """
        Record behavioral tracking data for a bet.

        Call this after placing a bet to store risk metrics
        for future analysis.
        """
        from ..db.models import Bet

        bet = self.db.query(Bet).filter(Bet.id == bet_id).first()
        if not bet:
            return

        # Timing data
        now = datetime.utcnow()
        bet.hour_of_day = now.hour
        bet.day_of_week = now.weekday()

        # Stake pattern data
        bet.stake_rounded = bet.stake in {10, 20, 25, 50, 100, 200, 500, 1000}
        bet.stake_noise_applied = noise_applied

        # Risk metrics
        bet.risk_score_at_bet = risk_score
        bet.utility_score = utility
        bet.selection_probability = selection_probability

        self.db.commit()


# Quick test
if __name__ == "__main__":
    print("=== Kelly Stake Test ===")
    # 2.40 odds, 45% win probability (fair odds 2.22), $1000 bankroll
    rec = kelly_stake(
        odds=2.40,
        win_probability=0.45,
        bankroll=1000,
        kelly_fraction=0.25,
        max_stake_pct=5.0,
    )
    print(f"Odds: 2.40, Win prob: 45%")
    print(f"Kelly stake: ${rec.kelly_stake}")
    print(f"Final stake: ${rec.stake} ({rec.reason})")
    print()

    print("=== Bankroll Manager Test ===")
    mgr = BankrollManager(kelly_fraction=0.25, max_stake_pct=5.0)
    mgr.set_balance("unibet", 500)
    mgr.set_balance("bet365", 300)

    print(f"Total bankroll: ${mgr.total_bankroll}")

    rec = mgr.calculate_value_stake(odds=2.40, fair_odds=2.22, provider="unibet")
    print(f"Value bet stake: ${rec.stake}")
