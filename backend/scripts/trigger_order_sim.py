"""
Trigger Order Optimization — MC sim
====================================
Tests whether ordering freebets smallest-first and requiring bankroll > 2x trigger
reduces ruin at low starting bankrolls.

Strategies:
  A) CURRENT: sequential as listed (unibet 1000 first)
  B) SMALLEST-FIRST: sort by trigger amount ascending
  C) SAFE-2X: smallest-first + skip if bankroll < 2x trigger
  D) SAFE-1.5X: smallest-first + skip if bankroll < 1.5x trigger
  E) DEFERRED-UNIBET: smallest-first, defer unibet to last

Run: python scripts/trigger_order_sim.py
"""

import random
import sys
import io
from dataclasses import dataclass, field
from typing import List, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def fprint(*args, **kwargs):
    print(*args, **kwargs)
    sys.stdout.flush()


# ── Edge distributions ──
SOFT_VALUE_EDGE_DIST = [
    (2.0,  4.0,  0.35, 3.50),
    (4.0,  6.0,  0.25, 4.10),
    (6.0, 10.0,  0.20, 5.50),
    (10.0, 20.0, 0.15, 8.10),
    (20.0, 35.0, 0.05, 11.40),
]
POLYMARKET_EDGE_DIST = [
    (3.0,  5.0,  0.40, 2.80),
    (5.0,  8.0,  0.30, 3.40),
    (8.0, 15.0,  0.20, 4.50),
    (15.0, 30.0, 0.10, 6.00),
]
PINNACLE_REVERSE_EDGE_DIST = [
    (3.0,  5.0,  0.35, 4.50),
    (5.0,  8.0,  0.30, 6.00),
    (8.0, 12.0,  0.25, 8.50),
    (12.0, 20.0, 0.10, 11.00),
]
SPECIALS_EDGE_DIST = [
    (4.0,  8.0,  0.40, 3.00),
    (8.0, 15.0,  0.35, 4.00),
    (15.0, 30.0, 0.20, 5.50),
    (30.0, 50.0, 0.05, 8.00),
]
STREAM_WEIGHTS = [
    ("soft_value",       0.913, SOFT_VALUE_EDGE_DIST),
    ("polymarket",       0.071, POLYMARKET_EDGE_DIST),
    ("pinnacle_reverse", 0.014, PINNACLE_REVERSE_EDGE_DIST),
    ("specials",         0.002, SPECIALS_EDGE_DIST),
]

FREEBETS = [
    ("unibet",    1000, 1000),
    ("betmgm",     500,  500),
    ("dbet",       500,  500),
    ("mrgreen",    500,  500),
    ("hajper",     500,  500),
    ("betsson",    250,  250),
    ("betsafe",    100,  100),
    ("nordicbet",  100,  100),
    ("lyllo",      100,  100),
]

DEPOSIT_BONUSES = [
    ("888sport",     500,   1, 1.80),
    ("interwetten", 1000,   5, 1.70),
    ("leovegas",     600,   6, 1.80),
    ("betinia",     1000,   6, 1.80),
    ("swiper",      1000,   6, 1.50),
    ("lodur",       1000,   6, 1.80),
    ("coolbet",     1000,   6, 1.50),
    ("campobet",     500,   6, 1.80),
    ("quickcasino",  500,   6, 1.80),
    ("comeon",       500,   6, 1.80),
    ("tipwin",      1000,   7, 1.80),
    ("10bet",       1000,   8, 1.80),
    ("snabbare",     600,   8, 1.80),
    ("vbet",         800,  10, 1.80),
    ("speedybet",    500,  12, 1.80),
    ("x3000",        500,  12, 1.80),
    ("goldenbull",   500,  12, 1.80),
    ("1x2",          500,  12, 1.80),
    ("spelklubben",  500,  15, 1.90),
    ("bethard",      500,  15, 1.90),
    ("expekt",      1000,  20, 1.80),
]

MIN_KELLY = 0.25
MAX_KELLY = 0.75
SINGLE_BET_CAP_PCT = 0.03
NUM_SIMS = 5000
WEEKS = 52
BETS_PER_WEEK = 35


def sample_bet(min_odds=1.10):
    for _ in range(50):
        r = random.random()
        cumulative = 0.0
        for _, weight, dist in STREAM_WEIGHTS:
            cumulative += weight
            if r <= cumulative:
                edge, odds = _sample_from(dist)
                if odds >= min_odds:
                    return edge, odds
                break
    return 4.0, max(min_odds, 2.50)


def _sample_from(dist):
    r = random.random()
    cumulative = 0.0
    for min_e, max_e, weight, avg_odds in dist:
        cumulative += weight
        if r <= cumulative:
            return random.uniform(min_e, max_e), max(1.15, avg_odds * random.uniform(0.7, 1.3))
    return 4.0, 2.50


def simulate_bet(stake, edge_pct, odds):
    fair_odds = odds / (1.0 + edge_pct / 100.0)
    win_prob = 1.0 / fair_odds
    return stake * (odds - 1.0) if random.random() < win_prob else -stake


def dynamic_min_stake(bankroll):
    if bankroll <= 0:
        return 25.0
    raw = max(5.0, bankroll * 0.005)
    return max(5.0, (min(raw, 25.0) // 5) * 5)


def kelly_fraction(edge_pct):
    if edge_pct <= 2.0:
        return MIN_KELLY
    elif edge_pct >= 6.0:
        return MAX_KELLY
    return MIN_KELLY + (edge_pct - 2.0) / 4.0 * (MAX_KELLY - MIN_KELLY)


def kelly_stake(bankroll, edge_pct, odds):
    edge = edge_pct / 100.0
    # Apply boost at low bankroll
    kelly = kelly_fraction(edge_pct)
    if bankroll <= 5000:
        kelly *= 1.333
    elif bankroll < 15000:
        t = (bankroll - 5000) / 10000
        kelly *= 1.333 - t * 0.333
    raw = bankroll * kelly * edge / (odds - 1.0)
    capped = min(raw, bankroll * SINGLE_BET_CAP_PCT)
    ms = dynamic_min_stake(bankroll)
    return round(capped, 0) if capped >= ms else 0.0


# ── Trigger strategies ──

def order_current():
    """Original order as listed."""
    return list(FREEBETS)


def order_smallest_first():
    """Sort by trigger amount ascending."""
    return sorted(FREEBETS, key=lambda x: x[2])


def order_deferred_unibet():
    """Smallest first, but Unibet (largest) goes last."""
    small = [(p, f, t) for p, f, t in FREEBETS if t < 1000]
    big = [(p, f, t) for p, f, t in FREEBETS if t >= 1000]
    return sorted(small, key=lambda x: x[2]) + big


@dataclass
class Strategy:
    name: str
    order_fn: object  # callable returning ordered freebets
    min_ratio: float  # skip trigger if bankroll < trigger * min_ratio (0 = no check)
    # If True, do a second pass for skipped triggers after growing bankroll
    second_pass: bool = False


STRATEGIES = [
    Strategy("A) CURRENT ORDER (as listed)", order_current, 0),
    Strategy("B) SMALLEST-FIRST", order_smallest_first, 0),
    Strategy("C) SAFE-2X (skip if BR < 2x trigger)", order_smallest_first, 2.0),
    Strategy("D) SAFE-1.5X (skip if BR < 1.5x trigger)", order_smallest_first, 1.5),
    Strategy("E) DEFERRED-UNIBET (last)", order_deferred_unibet, 0),
    Strategy("F) SMALLEST-FIRST + SAFE-2X + 2nd pass", order_smallest_first, 2.0, True),
    Strategy("G) SMALLEST-FIRST + value bets between triggers", order_smallest_first, 1.5),
]


# ── Simulation ──

@dataclass
class SimResult:
    final: float = 0.0
    ruin: bool = False
    freebets_claimed: int = 0
    bonuses_unlocked: int = 0
    trough: float = 0.0
    peak: float = 0.0


def simulate_freebet_phase(bankroll, strategy):
    """Run freebet triggers with given strategy."""
    ordered = strategy.order_fn()
    bonus_profit = 0.0
    betting_profit = 0.0
    claimed = 0
    skipped_providers = []

    for provider, fb_amount, trigger_amount in ordered:
        # Can't afford trigger at all
        if bankroll < trigger_amount:
            skipped_providers.append((provider, fb_amount, trigger_amount))
            continue

        # Safety ratio check
        if strategy.min_ratio > 0 and bankroll < trigger_amount * strategy.min_ratio:
            skipped_providers.append((provider, fb_amount, trigger_amount))
            continue

        # Place trigger bet
        edge_pct, odds = sample_bet(min_odds=1.80)
        result = simulate_bet(trigger_amount, edge_pct, odds)
        bankroll += result
        betting_profit += result

        # Freebet (SNR)
        fb_edge, fb_odds = sample_bet(min_odds=1.80)
        fair_odds = fb_odds / (1.0 + fb_edge / 100.0)
        if random.random() < 1.0 / fair_odds:
            bankroll += fb_amount * (fb_odds - 1.0)
            bonus_profit += fb_amount * (fb_odds - 1.0)

        claimed += 1

        if bankroll < 5.0:
            return bankroll, bonus_profit, betting_profit, claimed

    # Strategy G: do value bets to grow between passes
    # Strategy F: second pass for skipped triggers
    if strategy.second_pass and skipped_providers and bankroll >= 5.0:
        # Do 4 weeks of value betting first to grow bankroll
        for _ in range(4):
            n = BETS_PER_WEEK + random.randint(-3, 3)
            for _ in range(max(1, n)):
                if bankroll < 5.0:
                    break
                edge_pct, odds = sample_bet()
                stake = kelly_stake(bankroll, edge_pct, odds)
                if stake <= 0:
                    continue
                result = simulate_bet(stake, edge_pct, odds)
                bankroll += result
                betting_profit += result

        # Retry skipped
        for provider, fb_amount, trigger_amount in skipped_providers:
            if bankroll < trigger_amount:
                continue
            if strategy.min_ratio > 0 and bankroll < trigger_amount * strategy.min_ratio:
                continue

            edge_pct, odds = sample_bet(min_odds=1.80)
            result = simulate_bet(trigger_amount, edge_pct, odds)
            bankroll += result
            betting_profit += result

            fb_edge, fb_odds = sample_bet(min_odds=1.80)
            fair_odds = fb_odds / (1.0 + fb_edge / 100.0)
            if random.random() < 1.0 / fair_odds:
                bankroll += fb_amount * (fb_odds - 1.0)
                bonus_profit += fb_amount * (fb_odds - 1.0)

            claimed += 1

            if bankroll < 5.0:
                break

    return bankroll, bonus_profit, betting_profit, claimed


def simulate_deposit_bonus(bankroll, bonus_amount, wagering_mult, min_odds):
    wagering_target = bonus_amount * wagering_mult
    effective = bankroll + bonus_amount
    wagered = 0.0
    betting_pnl = 0.0
    bets = 0

    while wagered < wagering_target:
        if effective < 5.0:
            return effective, -bonus_amount, betting_pnl, bets, max(1, bets // max(BETS_PER_WEEK, 1))

        edge_pct, odds = sample_bet(min_odds=min_odds)
        stake = kelly_stake(effective, edge_pct, odds)
        if stake <= 0:
            stake = dynamic_min_stake(effective)
        remaining = wagering_target - wagered
        if stake > remaining:
            stake = max(dynamic_min_stake(effective), round(remaining, 0))
        if stake > effective:
            stake = round(effective, 0)
        if stake < 5.0:
            break

        result = simulate_bet(stake, edge_pct, odds)
        effective += result
        betting_pnl += result
        wagered += stake
        bets += 1

    return effective, bonus_amount, betting_pnl, bets, max(1, bets // max(BETS_PER_WEEK, 1))


def run_full_sim(start, strategy):
    bankroll = start
    peak = bankroll
    trough = bankroll

    # Phase 1: Freebets
    bankroll, bp, betp, claimed = simulate_freebet_phase(bankroll, strategy)
    peak = max(peak, bankroll)
    trough = min(trough, bankroll)
    weeks_used = 3

    if bankroll < 5.0:
        return SimResult(bankroll, True, claimed, 0, trough, peak)

    # Phase 2: Deposit bonuses
    bonuses = 0
    for _, bonus_amt, wager_mult, min_odds in DEPOSIT_BONUSES:
        if bankroll < bonus_amt or weeks_used >= WEEKS:
            continue
        new_br, _, _, _, wks = simulate_deposit_bonus(bankroll, bonus_amt, wager_mult, min_odds)
        bankroll = new_br
        bonuses += 1
        weeks_used += wks
        peak = max(peak, bankroll)
        trough = min(trough, bankroll)
        if bankroll < 5.0:
            return SimResult(bankroll, True, claimed, bonuses, trough, peak)

    # Phase 3: Snowball
    remaining = max(0, WEEKS - weeks_used)
    for _ in range(remaining):
        n = BETS_PER_WEEK + random.randint(-3, 3)
        for _ in range(max(1, n)):
            if bankroll < 5.0:
                return SimResult(bankroll, True, claimed, bonuses, trough, peak)
            edge_pct, odds = sample_bet()
            stake = kelly_stake(bankroll, edge_pct, odds)
            if stake <= 0:
                continue
            result = simulate_bet(stake, edge_pct, odds)
            bankroll += result
            peak = max(peak, bankroll)
            trough = min(trough, bankroll)

    return SimResult(bankroll, bankroll < 5.0, claimed, bonuses, trough, peak)


def pct(vals, p):
    s = sorted(vals)
    return s[min(int(len(s) * p / 100.0), len(s) - 1)]


def main():
    random.seed(42)

    starts = [500, 750, 1000, 1250, 1500, 1750, 2000, 2500, 3000, 3550, 4000, 5000, 7500, 10000]

    fprint("=" * 120)
    fprint("  TRIGGER ORDER OPTIMIZATION — Monte Carlo")
    fprint(f"  {NUM_SIMS} sims × {WEEKS} weeks × {BETS_PER_WEEK} bets/week")
    fprint("=" * 120)

    fprint(f"\n  Freebets sorted by trigger size:")
    for p, f, t in sorted(FREEBETS, key=lambda x: x[2]):
        fprint(f"    {p:<12s}  freebet: {f:>5,} kr  trigger: {t:>5,} kr")

    # ── Run all strategies at all bankrolls ──
    all_results = {}

    for strat in STRATEGIES:
        fprint(f"\n\n{'#' * 120}")
        fprint(f"  STRATEGY: {strat.name}")
        fprint(f"{'#' * 120}")
        fprint(f"  {'Start':>8s}  {'Median':>10s}  {'P10':>10s}  {'P25':>10s}  {'Ruin%':>6s}  "
              f"{'FBs':>5s}  {'Bonuses':>8s}  {'Trough P10':>11s}  {'Growth':>7s}")
        fprint(f"  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*6}  {'-'*5}  {'-'*8}  {'-'*11}  {'-'*7}")

        strat_results = {}
        for start in starts:
            random.seed(42)
            sims = [run_full_sim(start, strat) for _ in range(NUM_SIMS)]

            finals = [s.final for s in sims]
            ruin_n = sum(1 for s in sims if s.ruin)
            fbs = [s.freebets_claimed for s in sims]
            bonuses = [s.bonuses_unlocked for s in sims]
            troughs = [s.trough for s in sims]

            med = pct(finals, 50)
            ruin_pct = ruin_n / NUM_SIMS * 100
            strat_results[start] = {"median": med, "ruin": ruin_pct, "p10": pct(finals, 10),
                                     "fbs": pct(fbs, 50), "bonuses": pct(bonuses, 50),
                                     "trough_p10": pct(troughs, 10)}

            fprint(f"  {start:>8,}  {med:>10,.0f}  {pct(finals,10):>10,.0f}  {pct(finals,25):>10,.0f}  "
                  f"{ruin_pct:>5.1f}%  {pct(fbs,50):>4.0f}/{len(FREEBETS)}  "
                  f"{pct(bonuses,50):>3.0f}/{len(DEPOSIT_BONUSES):>2d}    "
                  f"{pct(troughs,10):>10,.0f}  {med/start if start > 0 else 0:>6.1f}x")

        all_results[strat.name] = strat_results

    # ── Comparison table: ruin % across strategies ──
    fprint(f"\n\n{'=' * 120}")
    fprint("  RUIN % COMPARISON — All Strategies × All Bankrolls")
    fprint(f"{'=' * 120}")

    # Header
    names_short = ["A)Current", "B)Small1st", "C)Safe2x", "D)Safe1.5x", "E)DeferUni", "F)2x+2ndPass", "G)Small+VB"]
    hdr = f"  {'Start':>8s}  "
    for n in names_short:
        hdr += f"{n:>13s}  "
    fprint(hdr)
    fprint(f"  {'-'*8}  " + "  ".join(['-'*13] * len(STRATEGIES)))

    for start in starts:
        row = f"  {start:>8,}  "
        best_ruin = min(all_results[s.name][start]["ruin"] for s in STRATEGIES)
        for si, strat in enumerate(STRATEGIES):
            ruin = all_results[strat.name][start]["ruin"]
            marker = " ◄" if ruin == best_ruin and ruin < 100 else "  "
            row += f"{ruin:>11.1f}%{marker}"
        fprint(row)

    # ── Comparison table: median final ──
    fprint(f"\n\n{'=' * 120}")
    fprint("  MEDIAN FINAL BANKROLL — All Strategies × All Bankrolls")
    fprint(f"{'=' * 120}")

    hdr = f"  {'Start':>8s}  "
    for n in names_short:
        hdr += f"{n:>13s}  "
    fprint(hdr)
    fprint(f"  {'-'*8}  " + "  ".join(['-'*13] * len(STRATEGIES)))

    for start in starts:
        row = f"  {start:>8,}  "
        best_med = max(all_results[s.name][start]["median"] for s in STRATEGIES)
        for strat in STRATEGIES:
            med = all_results[strat.name][start]["median"]
            marker = " ◄" if med == best_med else "  "
            row += f"{med:>11,.0f}{marker}"
        fprint(row)

    # ── Recommendation ──
    fprint(f"\n\n{'=' * 120}")
    fprint("  RECOMMENDATION: Best strategy per bankroll (lowest ruin, then highest median)")
    fprint(f"{'=' * 120}")

    for start in starts:
        candidates = []
        for strat in STRATEGIES:
            r = all_results[strat.name][start]
            candidates.append((r["ruin"], -r["median"], strat.name, r["median"]))
        candidates.sort()
        best = candidates[0]
        fprint(f"  {start:>8,} kr  →  {best[2]:<50s}  Ruin: {best[0]:>5.1f}%  Median: {best[3]:>10,.0f}")


if __name__ == "__main__":
    main()
