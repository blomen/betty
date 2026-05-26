"""
Betty — Min Stake Strategy Analysis
==========================================
When Kelly says "bet 15 kr" but min_stake is 25 kr, should we:
  A) Skip the bet entirely (current behavior)
  B) Bet 25 kr anyway (bump to min)
  C) Use higher Kelly at low bankroll to naturally reach 25 kr

Run: python scripts/min_stake_analysis.py
"""

import io
import random
import sys
from dataclasses import dataclass, field

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SOFT_VALUE_EDGE_DIST = [
    (2.0, 4.0, 0.35, 3.50),
    (4.0, 6.0, 0.25, 4.10),
    (6.0, 10.0, 0.20, 5.50),
    (10.0, 20.0, 0.15, 8.10),
    (20.0, 35.0, 0.05, 11.40),
]
POLYMARKET_EDGE_DIST = [
    (3.0, 5.0, 0.40, 2.80),
    (5.0, 8.0, 0.30, 3.40),
    (8.0, 15.0, 0.20, 4.50),
    (15.0, 30.0, 0.10, 6.00),
]
PINNACLE_REVERSE_EDGE_DIST = [
    (3.0, 5.0, 0.35, 4.50),
    (5.0, 8.0, 0.30, 6.00),
    (8.0, 12.0, 0.25, 8.50),
    (12.0, 20.0, 0.10, 11.00),
]
SPECIALS_EDGE_DIST = [
    (4.0, 8.0, 0.40, 3.00),
    (8.0, 15.0, 0.35, 4.00),
    (15.0, 30.0, 0.20, 5.50),
    (30.0, 50.0, 0.05, 8.00),
]
STREAM_WEIGHTS = [
    ("soft_value", 0.913, SOFT_VALUE_EDGE_DIST),
    ("polymarket", 0.071, POLYMARKET_EDGE_DIST),
    ("pinnacle_reverse", 0.014, PINNACLE_REVERSE_EDGE_DIST),
    ("specials", 0.002, SPECIALS_EDGE_DIST),
]

ABSOLUTE_MIN_STAKE = 5.0
NUM_SIMS = 3000


def kelly_fraction(edge_pct, min_kelly=0.25, max_kelly=0.75):
    if edge_pct <= 2.0:
        return min_kelly
    elif edge_pct >= 6.0:
        return max_kelly
    t = (edge_pct - 2.0) / 4.0
    return min_kelly + t * (max_kelly - min_kelly)


def round_stake_natural(stake):
    if stake < 50:
        return max(5.0, round(stake / 5) * 5)
    elif stake < 200:
        return round(stake / 10) * 10
    elif stake < 500:
        return round(stake / 25) * 25
    else:
        return round(stake / 50) * 50


def sample_from_stream(edge_dist, min_odds=1.10):
    for _ in range(50):
        r = random.random()
        cum = 0.0
        for min_e, max_e, weight, avg_odds in edge_dist:
            cum += weight
            if r <= cum:
                edge = random.uniform(min_e, max_e)
                odds = max(1.15, avg_odds * random.uniform(0.7, 1.3))
                if odds >= min_odds:
                    return edge, odds
                break
    return 4.0, max(min_odds, 2.50)


def sample_bet(min_odds=1.10):
    r = random.random()
    cum = 0.0
    for name, weight, dist in STREAM_WEIGHTS:
        cum += weight
        if r <= cum:
            edge, odds = sample_from_stream(dist, min_odds)
            return edge, odds, name
    edge, odds = sample_from_stream(SOFT_VALUE_EDGE_DIST, min_odds)
    return edge, odds, "soft_value"


def simulate_bet(stake, edge_pct, odds):
    fair_odds = odds / (1.0 + edge_pct / 100.0)
    win_prob = 1.0 / fair_odds
    return stake * (odds - 1.0) if random.random() < win_prob else -stake


# =====================================================================
# Strategy A: Current — skip if stake < min_stake (25 kr)
# =====================================================================
def stake_strategy_skip(bankroll, edge_pct, odds, cap_pct=0.03, min_stake=25.0, mep=2.0):
    edge = edge_pct / 100.0
    frac = kelly_fraction(edge_pct)
    raw = bankroll * frac * edge / (odds - 1.0)
    capped = min(raw, bankroll * cap_pct)
    stake = round_stake_natural(max(0.0, capped))
    if stake < min_stake:
        return 0.0
    if mep > 0 and stake * edge < mep:
        return 0.0
    return stake


# =====================================================================
# Strategy B: Bump to min — if Kelly says 10-24 kr, bet 25 kr anyway
# =====================================================================
def stake_strategy_bump(bankroll, edge_pct, odds, cap_pct=0.03, min_stake=25.0, mep=0.75):
    edge = edge_pct / 100.0
    frac = kelly_fraction(edge_pct)
    raw = bankroll * frac * edge / (odds - 1.0)
    capped = min(raw, bankroll * cap_pct)
    stake = round_stake_natural(max(0.0, capped))

    # If Kelly says >= 5 kr (meaningful) but < min_stake, bump up
    if ABSOLUTE_MIN_STAKE <= stake < min_stake:
        stake = min_stake

    if stake < ABSOLUTE_MIN_STAKE:
        return 0.0
    if mep > 0 and stake * edge < mep:
        return 0.0
    return stake


# =====================================================================
# Strategy C: Dynamic Kelly — increase Kelly fraction when bankroll is low
# so raw stakes naturally clear 25 kr more often
# =====================================================================
def stake_strategy_dynamic_kelly(bankroll, edge_pct, odds, cap_pct=0.03, min_stake=25.0, mep=0.75):
    edge = edge_pct / 100.0

    # Scale Kelly up when bankroll is small
    # At 3k: max_kelly=1.25, at 10k: 1.0, at 20k+: 0.75
    if bankroll <= 5000:
        mk = 1.25
    elif bankroll <= 10000:
        t = (bankroll - 5000) / 5000
        mk = 1.25 - t * 0.25  # 1.25 -> 1.0
    elif bankroll <= 20000:
        t = (bankroll - 10000) / 10000
        mk = 1.0 - t * 0.25  # 1.0 -> 0.75
    else:
        mk = 0.75

    frac = kelly_fraction(edge_pct, min_kelly=min(0.25, mk), max_kelly=mk)
    raw = bankroll * frac * edge / (odds - 1.0)
    capped = min(raw, bankroll * cap_pct)
    stake = round_stake_natural(max(0.0, capped))
    if stake < min_stake:
        return 0.0
    if mep > 0 and stake * edge < mep:
        return 0.0
    return stake


# =====================================================================
# Strategy D: Dynamic min_stake — lower min_stake when bankroll is small
# At 3k: min=10, at 5k: min=15, at 10k+: min=25
# =====================================================================
def stake_strategy_dynamic_min(bankroll, edge_pct, odds, cap_pct=0.03, mep=0.75):
    edge = edge_pct / 100.0
    frac = kelly_fraction(edge_pct)
    raw = bankroll * frac * edge / (odds - 1.0)
    capped = min(raw, bankroll * cap_pct)
    stake = round_stake_natural(max(0.0, capped))

    # Dynamic min stake: 0.3% of bankroll, floor 5, cap 25
    min_stake = max(ABSOLUTE_MIN_STAKE, min(bankroll * 0.003, 25.0))
    min_stake = round(min_stake / 5) * 5
    min_stake = max(ABSOLUTE_MIN_STAKE, min_stake)

    if stake < min_stake:
        return 0.0
    if mep > 0 and stake * edge < mep:
        return 0.0
    return stake


# =====================================================================
# Strategy E: Combo — dynamic Kelly + bump to min + low mEP
# =====================================================================
def stake_strategy_combo(bankroll, edge_pct, odds, cap_pct=0.03, mep=0.75):
    edge = edge_pct / 100.0

    if bankroll <= 5000:
        mk = 1.0
    elif bankroll <= 15000:
        t = (bankroll - 5000) / 10000
        mk = 1.0 - t * 0.25
    else:
        mk = 0.75

    frac = kelly_fraction(edge_pct, min_kelly=min(0.25, mk), max_kelly=mk)
    raw = bankroll * frac * edge / (odds - 1.0)
    capped = min(raw, bankroll * cap_pct)
    stake = round_stake_natural(max(0.0, capped))

    # Bump to min if Kelly gives meaningful but sub-25 stake
    min_stake = 25.0
    if 10 <= stake < min_stake:
        stake = min_stake

    if stake < 10:
        return 0.0
    if mep > 0 and stake * edge < mep:
        return 0.0
    return stake


STRATEGIES = [
    ("A: Skip (current)", stake_strategy_skip, "Kelly 0.75, skip if <25kr, mEP=2.0"),
    ("B: Bump to 25kr", stake_strategy_bump, "Kelly 0.75, bump 5-24kr→25kr, mEP=0.75"),
    ("C: Dynamic Kelly", stake_strategy_dynamic_kelly, "Kelly 1.25@3k→0.75@20k, skip <25, mEP=0.75"),
    ("D: Dynamic min stake", stake_strategy_dynamic_min, "Kelly 0.75, min_stake=0.3% of BR, mEP=0.75"),
    ("E: Combo", stake_strategy_combo, "Dynamic Kelly + bump + mEP=0.75"),
]


@dataclass
class SimResult:
    final_bankroll: float = 0.0
    profit: float = 0.0
    bets_played: int = 0
    bets_skipped: int = 0
    total_staked: float = 0.0
    peak: float = 0.0
    trough: float = 0.0
    max_dd_pct: float = 0.0
    ruin: bool = False
    weekly: list[float] = field(default_factory=list)


def simulate(starting, bpw, weeks, strategy_fn, track_weekly=False):
    bankroll = starting
    profit = 0.0
    played = skipped = 0
    staked = 0.0
    peak = trough = bankroll
    max_dd = 0.0
    weekly = [bankroll] if track_weekly else []

    for w in range(weeks):
        n = max(1, bpw + random.randint(-3, 3))
        for _ in range(n):
            if bankroll < ABSOLUTE_MIN_STAKE:
                return SimResult(bankroll, profit, played, skipped, staked, peak, trough, max_dd, True, weekly)

            edge, odds, _ = sample_bet()
            stake = strategy_fn(bankroll, edge, odds)

            if stake <= 0:
                skipped += 1
                continue

            result = simulate_bet(stake, edge, odds)
            bankroll += result
            profit += result
            staked += stake
            played += 1
            if bankroll > peak:
                peak = bankroll
            if bankroll < trough:
                trough = bankroll
            dd = (peak - bankroll) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        if track_weekly:
            weekly.append(bankroll)

    return SimResult(
        bankroll, profit, played, skipped, staked, peak, trough, max_dd, bankroll < ABSOLUTE_MIN_STAKE, weekly
    )


def pct(vals, p):
    s = sorted(vals)
    return s[min(int(len(s) * p / 100), len(s) - 1)]


# =====================================================================
# Analysis 1: Coverage — what % of bets does each strategy play?
# =====================================================================
def coverage_test():
    print("=" * 100)
    print("  ANALYSIS 1: BET COVERAGE BY STRATEGY (20,000 sampled bets)")
    print("=" * 100)

    bankrolls = [3000, 5000, 7500, 10000, 15000, 20000]
    n = 20000

    for name, fn, desc in STRATEGIES:
        print(f"\n  {name}  ({desc})")
        print(
            f"  {'Bankroll':>10s}  {'Playable':>9s}  {'Skipped':>8s}  {'Avg Stake':>10s}  {'Avg EV':>8s}  {'Stake/BR':>9s}"
        )
        print(f"  {'-' * 10}  {'-' * 9}  {'-' * 8}  {'-' * 10}  {'-' * 8}  {'-' * 9}")

        for br in bankrolls:
            play = 0
            tot_stake = 0.0
            tot_ev = 0.0
            for _ in range(n):
                e, o, _ = sample_bet()
                s = fn(br, e, o)
                if s > 0:
                    play += 1
                    tot_stake += s
                    tot_ev += s * e / 100.0

            pp = play / n * 100
            avg_s = tot_stake / max(1, play)
            avg_ev = tot_ev / max(1, play)
            s_br = avg_s / br * 100
            print(f"  {br:>9,}  {pp:>8.1f}%  {n - play:>7,}  {avg_s:>9.0f} kr  {avg_ev:>7.2f}  {s_br:>8.2f}%")


# =====================================================================
# Analysis 2: Monte Carlo growth comparison
# =====================================================================
def growth_comparison():
    print("\n\n" + "=" * 100)
    print("  ANALYSIS 2: GROWTH COMPARISON — Monte Carlo")
    print(f"  {NUM_SIMS:,} runs | 35 bets/week | 52 weeks")
    print("=" * 100)

    bankrolls = [3000, 5000, 10000, 20000]

    for br in bankrolls:
        print(f"\n  {'━' * 96}")
        print(f"  Starting: {br:,} kr")
        print(f"  {'━' * 96}")
        print(
            f"  {'Strategy':<24s}  {'Med Final':>10s}  {'Growth':>7s}  {'P10':>10s}  "
            f"{'Play%':>6s}  {'Med DD':>7s}  {'P90 DD':>7s}  {'Ruin':>5s}  {'Bets':>6s}"
        )
        print(f"  {'-' * 24}  {'-' * 10}  {'-' * 7}  {'-' * 10}  {'-' * 6}  {'-' * 7}  {'-' * 7}  {'-' * 5}  {'-' * 6}")

        for name, fn, desc in STRATEGIES:
            results = [simulate(br, 35, 52, fn) for _ in range(NUM_SIMS)]

            finals = [r.final_bankroll for r in results]
            played = [r.bets_played for r in results]
            skipped = [r.bets_skipped for r in results]
            dds = [r.max_dd_pct for r in results]
            ruin = sum(1 for r in results if r.ruin) / len(results) * 100

            mf = pct(finals, 50)
            p10 = pct(finals, 10)
            mp = pct(played, 50)
            ms = pct(skipped, 50)
            pp = mp / max(1, mp + ms) * 100
            gr = (mf / br - 1) * 100

            print(
                f"  {name:<24s}  {mf:>10,.0f}  {gr:>+6.0f}%  {p10:>10,.0f}  "
                f"{pp:>5.1f}%  {pct(dds, 50):>6.1f}%  {pct(dds, 90):>6.1f}%  {ruin:>4.1f}%  {mp:>5,.0f}"
            )


# =====================================================================
# Analysis 3: Equity curves
# =====================================================================
def equity_curves():
    print("\n\n" + "=" * 100)
    print("  ANALYSIS 3: EQUITY CURVES — 5,000 kr start")
    print(f"  {NUM_SIMS:,} runs | 35 bets/week | P10, P25, Median, P75, P90")
    print("=" * 100)

    br = 5000
    for name, fn, desc in STRATEGIES:
        results = [simulate(br, 35, 52, fn, track_weekly=True) for _ in range(NUM_SIMS)]
        tracked = [r for r in results if r.weekly]
        if not tracked:
            continue
        max_w = max(len(r.weekly) for r in tracked)
        ruin = sum(1 for r in results if r.ruin) / len(results) * 100

        print(f"\n  {name}  Ruin: {ruin:.1f}%")
        print(f"  {'Week':>6s}  {'P10':>9s}  {'P25':>9s}  {'Median':>9s}  {'P75':>9s}  {'P90':>9s}")
        print(f"  {'-' * 6}  {'-' * 9}  {'-' * 9}  {'-' * 9}  {'-' * 9}  {'-' * 9}")

        for w in range(max_w):
            if w == 0 or w % 8 == 0 or w == max_w - 1:
                vals = [r.weekly[w] if w < len(r.weekly) else r.weekly[-1] for r in tracked]
                print(
                    f"  {w:>6d}  {pct(vals, 10):>9,.0f}  {pct(vals, 25):>9,.0f}  "
                    f"{pct(vals, 50):>9,.0f}  {pct(vals, 75):>9,.0f}  {pct(vals, 90):>9,.0f}"
                )


# =====================================================================
# Analysis 4: What happens to the "bumped" bets specifically?
# =====================================================================
def bump_analysis():
    print("\n\n" + "=" * 100)
    print("  ANALYSIS 4: BUMP-TO-MIN DEEP DIVE")
    print("  When Kelly says 10-24 kr and we bet 25 instead — is it profitable?")
    print("=" * 100)

    bankrolls = [3000, 5000, 7500, 10000]
    n_bets = 100000

    for br in bankrolls:
        bumped = 0
        bumped_ev = 0.0
        bumped_overbet = 0.0  # How much we're over-Kelly
        normal = 0
        normal_ev = 0.0
        skipped = 0

        for _ in range(n_bets):
            e, o, _ = sample_bet()
            edge = e / 100.0
            frac = kelly_fraction(e)
            raw = br * frac * edge / (o - 1.0)
            capped = min(raw, br * 0.03)
            stake = round_stake_natural(max(0.0, capped))

            if stake >= 25:
                normal += 1
                normal_ev += stake * edge
            elif stake >= 5:  # Would be bumped from 5-24 to 25
                bumped += 1
                bumped_ev += 25 * edge  # EV at bumped stake
                bumped_overbet += (25 - stake) / br * 100  # Over-Kelly as % of BR
            else:
                skipped += 1

        total = n_bets
        print(f"\n  Bankroll: {br:,} kr  ({n_bets:,} sampled bets)")
        print(
            f"    Normal (≥25 kr):   {normal:>6,}  ({normal / total * 100:.1f}%)  avg EV: {normal_ev / max(1, normal):.2f} kr"
        )
        print(
            f"    Bumped (5→25 kr):  {bumped:>6,}  ({bumped / total * 100:.1f}%)  avg EV: {bumped_ev / max(1, bumped):.2f} kr  avg overbet: {bumped_overbet / max(1, bumped):.3f}% of BR"
        )
        print(f"    Skipped (<5 kr):   {skipped:>6,}  ({skipped / total * 100:.1f}%)")
        if bumped > 0:
            print(f"    Total extra EV from bumping: {bumped_ev:.0f} kr across {bumped:,} bets")
            print(
                f"    Per-scan equivalent (~2039 opps): ~{bumped / n_bets * 2039:.0f} bumped bets, ~{bumped_ev / n_bets * 2039:.0f} kr extra EV"
            )


def main():
    random.seed(42)

    print("=" * 100)
    print("  ARNOLD — MIN STAKE STRATEGY ANALYSIS")
    print("  Should we skip, bump, or dynamically adjust when Kelly < 25 kr?")
    print("=" * 100)

    coverage_test()
    bump_analysis()
    growth_comparison()
    equity_curves()

    print("\n\n" + "=" * 100)
    print("  SUMMARY")
    print("=" * 100)
    print("""
  5 strategies compared:
    A) Skip (current)     — Kelly < 25kr → skip. mEP=2.0
    B) Bump to 25kr       — Kelly 5-24kr → bet 25 anyway. mEP=0.75
    C) Dynamic Kelly      — Higher Kelly at low bankroll. mEP=0.75
    D) Dynamic min_stake  — Lower min_stake at low bankroll. mEP=0.75
    E) Combo              — Dynamic Kelly + bump + mEP=0.75

  The key question: is slightly over-Kelly better than skipping +EV bets?
  Over-Kelly increases variance but captures EV. Skipping is safer but
  leaves money on the table.
""")


if __name__ == "__main__":
    main()
