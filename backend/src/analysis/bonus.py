"""
Bonus Matching (Bookie vs Bookie)

Finds the best opposing bet at another bookmaker to minimize loss
when qualifying for a bonus or extracting a free bet.

Key concepts:
- Anchor bet: The bet you MUST place (e.g., using free bet at Unibet)
- Hedge bet: The opposing bet at another bookie to lock in value

Formula (for qualifying bets):
    loss = anchor_stake - (hedge_stake * anchor_opposing_odds / hedge_odds)

We minimize loss while covering all outcomes.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BonusMatch:
    """A matched bet opportunity for bonus extraction."""

    event_id: str
    market: str

    # Anchor bet (the bonus/required bet)
    anchor_provider: str
    anchor_outcome: str
    anchor_odds: float
    anchor_stake: float

    # Hedge bet (opposing bet at different bookie)
    hedge_provider: str
    hedge_outcome: str
    hedge_odds: float
    hedge_stake: float

    # Results
    qualifying_loss: float  # Negative = profit
    retention_pct: float  # For free bets: % of free bet value retained

    @property
    def is_profitable(self) -> bool:
        return self.qualifying_loss < 0


def find_best_hedge(
    event_id: str,
    market: str,
    anchor_provider: str,
    anchor_outcome: str,
    anchor_odds: float,
    anchor_stake: float,
    opposing_odds_list: list[dict],
    is_free_bet: bool = False,
) -> BonusMatch | None:
    """
    Find the best hedge bet for bonus matching.

    For 2-way markets (e.g., tennis), the opposing outcome is obvious.
    For 3-way markets (e.g., 1x2), we pick the outcome with best hedge value.

    Args:
        event_id: Canonical event ID
        market: Market type
        anchor_provider: Provider where bonus bet is placed
        anchor_outcome: Outcome of the bonus bet ("home", "over", etc.)
        anchor_odds: Odds for the anchor bet
        anchor_stake: Stake for anchor bet (e.g., free bet amount)
        opposing_odds_list: [{provider, outcome, odds}, ...] for OTHER outcomes
        is_free_bet: True if anchor stake is a free bet (stake not returned on win)

    Returns:
        Best BonusMatch or None if no valid hedge found
    """
    if not opposing_odds_list:
        return None

    best_match = None
    best_loss = float("inf")

    for hedge in opposing_odds_list:
        # Skip same provider (can't hedge with same bookie)
        if hedge["provider"] == anchor_provider:
            continue

        # Calculate hedge stake for guaranteed return
        if is_free_bet:
            # Free bet: stake not returned, only profit
            # If anchor wins: profit = (anchor_odds - 1) * anchor_stake
            # If hedge wins: profit = hedge_stake * (hedge_odds - 1) - hedge_stake
            # We want anchor_win_profit = hedge_loss, and vice versa

            anchor_profit = (anchor_odds - 1) * anchor_stake
            hedge_stake = anchor_profit / hedge["odds"]

            # Loss is just the hedge stake (anchor is "free")
            loss = hedge_stake

            # Calculate retention (how much of free bet value we keep)
            # Retention = guaranteed profit / free bet value
            guaranteed = anchor_profit - hedge_stake
            retention = guaranteed / anchor_stake if anchor_stake > 0 else 0

        else:
            # Qualifying bet: real money, minimize loss
            # Stake hedge so that:
            # If anchor wins: we get anchor_stake * anchor_odds
            # If hedge wins: we get hedge_stake * hedge_odds
            # These should be equal for arb-style matching

            total_return = anchor_stake * anchor_odds
            hedge_stake = total_return / hedge["odds"]

            # Total staked vs guaranteed return
            total_staked = anchor_stake + hedge_stake
            loss = total_staked - total_return
            retention = -loss / anchor_stake if anchor_stake > 0 else 0  # Negative loss = profit

        if loss < best_loss:
            best_loss = loss
            best_match = BonusMatch(
                event_id=event_id,
                market=market,
                anchor_provider=anchor_provider,
                anchor_outcome=anchor_outcome,
                anchor_odds=anchor_odds,
                anchor_stake=anchor_stake,
                hedge_provider=hedge["provider"],
                hedge_outcome=hedge["outcome"],
                hedge_odds=hedge["odds"],
                hedge_stake=round(hedge_stake, 2),
                qualifying_loss=round(loss, 2),
                retention_pct=round(retention * 100, 1),
            )

    return best_match


def calculate_free_bet_value(
    free_bet_amount: float,
    anchor_odds: float,
    hedge_odds: float,
    stake_returned: bool = False,
) -> dict:
    """
    Calculate expected value extraction from a free bet.

    Args:
        free_bet_amount: Face value of free bet
        anchor_odds: Odds for the free bet
        hedge_odds: Best opposing odds at another bookie
        stake_returned: True if free bet stake is returned on win (rare)

    Returns:
        {profit, retention_pct, hedge_stake}
    """
    if stake_returned:
        # SNR (Stake Not Returned) = False, stake comes back
        anchor_return = free_bet_amount * anchor_odds
    else:
        # SNR = True (most common), only profit returned
        anchor_return = free_bet_amount * (anchor_odds - 1)

    # Hedge stake to guarantee anchor_return if hedge wins
    hedge_stake = anchor_return / hedge_odds

    # Guaranteed profit (regardless of outcome)
    profit = anchor_return - hedge_stake

    # What % of free bet face value we extract
    retention_pct = (profit / free_bet_amount) * 100

    return {
        "profit": round(profit, 2),
        "retention_pct": round(retention_pct, 1),
        "hedge_stake": round(hedge_stake, 2),
        "anchor_return": round(anchor_return, 2),
    }
