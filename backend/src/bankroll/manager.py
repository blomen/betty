"""
Bankroll Manager

Handles:
- Total bankroll calculation (sum of provider balances)
- Kelly criterion stake calculation
- Auto-calculated bonus matching stakes
"""

from dataclasses import dataclass
from typing import Optional
import logging

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
