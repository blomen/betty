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


def brackets_key_number(
    point_a: float | None,
    point_b: float | None,
    keys: tuple[int, ...],
) -> int | None:
    """Return a key number that sits strictly between |point_a| and |point_b|.

    For spreads, the opposite side's point has the opposite sign — we
    compare absolute values to detect crossing. For totals, both points
    are positive so abs() is a no-op. If multiple keys are bracketed,
    return the one closest to the midpoint of the two lines (this gives
    the most balanced middle window).

    Returns None when either point is missing or the lines don't bracket
    any key in `keys`.
    """
    if point_a is None or point_b is None:
        return None
    a, b = abs(point_a), abs(point_b)
    lo, hi = (a, b) if a < b else (b, a)
    bracketed = [k for k in keys if lo < k < hi]
    if not bracketed:
        return None
    midpoint = (point_a + point_b) / 2.0
    return min(bracketed, key=lambda k: abs(k - midpoint))


def middle_size(
    stake_a_base: float,
    odds_a: float,
    odds_b: float,
    target_wing_pct: float,
) -> float:
    """Stake for side B such that wing_loss / total_stake == target_wing_pct.

    Wing loss = the loss when the result does NOT land in the middle.
    Smaller stake_b → bigger wing loss but bigger middle payout.
    Larger stake_b → smaller wing loss but smaller middle payout.

    At target_wing_pct=0 this reduces to equalise_payouts (both winning
    payouts equal total stake, refunding exactly). At target_wing_pct>0
    we deliberately under-stake one side so that:
        total_stake = stake_a + stake_b
        min(stake_a * odds_a, stake_b * odds_b) = total_stake * (1 - target_wing_pct)

    We try both cases (A is minimum, B is minimum) and return the one where
    the minimum payout assumption is valid.

    Returns 0.0 on invalid inputs. Clamps negative target_wing_pct to 0.
    """
    if odds_a <= 0 or odds_b <= 0 or stake_a_base <= 0:
        return 0.0
    w = max(0.0, target_wing_pct)

    # Case 1: B is the minimum payout side (under-stake B side).
    # Derivation: S_b * odds_b = (S_a + S_b) * (1 - w)
    #             S_b = S_a * (1 - w) / (odds_b - (1 - w))
    denominator_b = odds_b - (1.0 - w)
    if denominator_b > 0:
        stake_b_case1 = stake_a_base * (1.0 - w) / denominator_b
        # Verify B is indeed minimum
        if stake_b_case1 * odds_b <= stake_a_base * odds_a:
            return stake_b_case1

    # Case 2: A is the minimum payout side (under-stake A side).
    # Derivation: S_a * odds_a = (S_a + S_b) * (1 - w)
    #             S_b = S_a * (odds_a - (1 - w)) / (1 - w)
    denominator_a = 1.0 - w
    if denominator_a > 0:
        stake_b_case2 = stake_a_base * (odds_a - (1.0 - w)) / denominator_a
        # Verify A is indeed minimum
        if stake_a_base * odds_a <= stake_b_case2 * odds_b:
            return stake_b_case2

    # Fallback: equalise (no wing loss).
    return equalise_payouts(stake_a_base, odds_a, odds_b)
