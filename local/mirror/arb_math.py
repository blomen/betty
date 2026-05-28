"""Pure arb math — no I/O, no async. Used by ArbRunner + SlipOddsStream."""

from __future__ import annotations

from .currency import from_sek, to_sek


def recalc_profit_pct(anchor_odds: float, counter_odds: list[float]) -> float | None:
    """Guaranteed-profit % for an equal-payout arb.

    profit% = (1 / (1/anchor_odds + Σ 1/counter_odds) - 1) × 100

    Currency-independent — odds are unitless multipliers; per-leg stakes
    cancel out of the profit ratio. Returns None if any odds are zero/negative.
    """
    if anchor_odds <= 0 or any(o <= 0 for o in counter_odds):
        return None
    inv_sum = 1.0 / anchor_odds + sum(1.0 / o for o in counter_odds)
    if inv_sum <= 0:
        return None
    return (1.0 / inv_sum - 1.0) * 100.0


def recalc_counter_stakes(
    anchor_stake: float,
    anchor_odds: float,
    anchor_currency: str,
    counter_legs: list[dict],
) -> list[float]:
    """Per-counter stakes IN EACH COUNTER'S NATIVE CURRENCY for equal-payout.

    Each counter leg must carry its own `odds` and `currency`. The anchor's
    `total_payout = anchor_stake × anchor_odds` is in anchor's currency; the
    counter must pay the SAME real-money amount from a stake in ITS currency,
    so we route the payout through SEK before dividing by each counter's odds.

    Without this conversion a SEK anchor paired with a USDC counter sizes the
    counter ~10× too large (USD/SEK ≈ 0.095). See CLAUDE.md "Currencies"
    section — this is the first hypothesis when a sizing/hedge number looks
    off by 5-10×.

    Returns stakes rounded to 2 decimals (cents in each native currency).
    """
    total_payout_sek = to_sek(anchor_stake * anchor_odds, anchor_currency)
    out: list[float] = []
    for leg in counter_legs:
        odds = float(leg.get("odds") or 0)
        currency = leg.get("currency") or "SEK"
        if odds <= 0:
            out.append(0.0)
            continue
        stake_sek = total_payout_sek / odds
        out.append(round(from_sek(stake_sek, currency), 2))
    return out


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
