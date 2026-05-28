"""Pure arb math — no I/O, no async. Used by ArbRunner + SlipOddsStream."""

from __future__ import annotations


def recalc_profit_pct(anchor_odds: float, counter_odds: list[float]) -> float | None:
    """Guaranteed-profit % for an equal-payout arb.

    profit% = (1 / (1/anchor_odds + Σ 1/counter_odds) - 1) × 100
    Returns None if any odds are zero/negative.
    """
    if anchor_odds <= 0 or any(o <= 0 for o in counter_odds):
        return None
    inv_sum = 1.0 / anchor_odds + sum(1.0 / o for o in counter_odds)
    if inv_sum <= 0:
        return None
    return (1.0 / inv_sum - 1.0) * 100.0


def recalc_counter_stakes(
    anchor_stake: float, anchor_odds: float, counter_odds: list[float]
) -> list[float]:
    """Per-counter stakes for equal-payout: counter_stake = total_payout / counter_odds.

    Total payout = anchor_stake × anchor_odds. Each counter sized so it pays the same.
    Returns stakes rounded to 2 decimals (currency cents).
    """
    total_payout = anchor_stake * anchor_odds
    return [round(total_payout / o, 2) for o in counter_odds]


def is_valid_arb_shape(legs: list[dict], unlimited: set[str]) -> bool:
    """Arb must be exactly 1 soft leg + ≥1 unlimited counter leg(s).

    Rejects: two softs, all-unlimited, empty.
    """
    if len(legs) < 2:
        return False
    soft_count = sum(1 for leg in legs if leg.get("provider") not in unlimited)
    if soft_count != 1:
        return False
    return True


def should_update_stake(old: float, new: float) -> bool:
    """Whether a counter slip's stake field should be re-written.

    Re-write when drift exceeds EITHER the absolute floor (1.0 SEK) OR the
    relative floor (1% of old) — whichever is smaller, so small stakes still
    react to proportional drift and large stakes still react to absolute drift.
    Avoids spamming the slip widget with sub-cent updates while still reacting
    to real drift.
    """
    if old <= 0:
        return True
    delta = abs(new - old)
    abs_threshold = 1.0
    pct_threshold = old * 0.01
    return delta >= min(abs_threshold, pct_threshold)


def equalise_payouts(stake_a_base: float, odds_a: float, odds_b: float) -> float:
    """Stake for side B that makes winning-outcome payouts equal in base currency.

    Currency conversion to provider-B native currency happens at the
    placement layer, not here. Returns 0.0 on non-positive odds — the
    scanner treats that as "no candidate".
    """
    if odds_a <= 0 or odds_b <= 0:
        return 0.0
    return stake_a_base * odds_a / odds_b
