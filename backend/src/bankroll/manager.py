"""
Bankroll Manager - Legacy utilities

Note: Primary stake calculation is now in stake_calculator.py
This file contains utility functions for arbitrage and bonus calculations.
"""

from dataclasses import dataclass
from typing import Optional


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
