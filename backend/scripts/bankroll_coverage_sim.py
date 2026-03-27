"""
Firev — Bankroll Coverage & Min Expected Profit Simulation
================================================================
How much bankroll do you need to play most value bets?
What happens if you lower min_expected_profit from 2.0 to 0.5?

Key question: for low-edge bets (~5%) at high odds (6+),
the min_expected_profit guard skips them because stake*edge < 2 kr.
How many bets do we miss, and what's the profit impact?

Run: python scripts/bankroll_coverage_sim.py
"""

import random
import sys
import io
from dataclasses import dataclass
from typing import List, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Constants from stake_calculator.py ──
MIN_KELLY = 0.25
MAX_KELLY = 0.75
ABSOLUTE_MIN_STAKE = 5.0
DEFAULT_MIN_STAKE = 25.0
DEFAULT_MIN_EXPECTED_PROFIT = 2.0

# ── Edge distributions (from growth_simulation.py) ──
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

NUM_SIMS = 5000


def kelly_fraction(edge_pct: float) -> float:
    if edge_pct <= 2.0:
        return MIN_KELLY
    elif edge_pct >= 6.0:
        return MAX_KELLY
    t = (edge_pct - 2.0) / 4.0
    return MIN_KELLY + t * (MAX_KELLY - MIN_KELLY)


def dynamic_min_stake(bankroll: float) -> float:
    raw = max(ABSOLUTE_MIN_STAKE, bankroll * 0.005)
    capped = min(raw, DEFAULT_MIN_STAKE)
    return max(ABSOLUTE_MIN_STAKE, (capped // 5) * 5)


def round_stake_natural(stake: float) -> float:
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


def sample_bet_all_streams(min_odds=1.10):
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
    if random.random() < win_prob:
        return stake * (odds - 1.0)
    return -stake


def calculate_stake_with_guards(
    bankroll: float,
    edge_pct: float,
    odds: float,
    min_expected_profit: float,
    single_bet_cap_pct: float = 0.03,
) -> Tuple[float, str]:
    """Returns (stake, skip_reason). skip_reason="" if playable."""
    edge = edge_pct / 100.0
    frac = kelly_fraction(edge_pct)
    min_stake = dynamic_min_stake(bankroll)

    raw = bankroll * frac * edge / (odds - 1.0)
    capped = min(raw, bankroll * single_bet_cap_pct)
    stake = max(0.0, capped)
    stake = round_stake_natural(stake)

    if stake < min_stake:
        return 0.0, "min_stake"

    if min_expected_profit > 0 and stake * edge < min_expected_profit:
        return 0.0, "min_ev"

    return stake, ""


# =====================================================================
# SIMULATION 1: Coverage analysis — what % of bets can you play?
# =====================================================================

def coverage_analysis():
    """For each bankroll level and min_expected_profit, sample 10k bets
    and check how many pass the guards."""

    print("=" * 90)
    print("  SIMULATION 1: BET COVERAGE — % of opportunities you can actually play")
    print("  (10,000 sampled bets per scenario)")
    print("=" * 90)

    bankrolls = [3000, 5000, 7500, 10000, 15000, 20000, 30000, 50000]
    min_evs = [0.0, 0.5, 1.0, 2.0, 3.0]
    n_samples = 10000

    # Header
    print(f"\n  {'Bankroll':>10s}", end="")
    for mev in min_evs:
        print(f"  {'mEP=%.1f' % mev:>10s}", end="")
    print()
    print(f"  {'-'*10}", end="")
    for _ in min_evs:
        print(f"  {'-'*10}", end="")
    print()

    for bankroll in bankrolls:
        print(f"  {bankroll:>9,}", end="")
        for mev in min_evs:
            playable = 0
            for _ in range(n_samples):
                edge_pct, odds, _ = sample_bet_all_streams()
                stake, reason = calculate_stake_with_guards(bankroll, edge_pct, odds, mev)
                if stake > 0:
                    playable += 1
            pct_play = playable / n_samples * 100
            print(f"  {pct_play:>9.1f}%", end="")
        print()

    print(f"\n  mEP = min expected profit (stake * edge). Default = 2.0 kr")
    print(f"  mEP=0.0 means only min_stake guard applies (no EV filter)")


# =====================================================================
# SIMULATION 2: Which bets get skipped? Profile of missed bets
# =====================================================================

def skipped_bet_profile():
    """Analyze the characteristics of bets skipped by min_expected_profit."""

    print("\n\n" + "=" * 90)
    print("  SIMULATION 2: PROFILE OF SKIPPED BETS (bankroll=10,000 kr, mEP=2.0)")
    print("  What do the bets we're missing look like?")
    print("=" * 90)

    bankroll = 10000
    n_samples = 50000
    min_ev = 2.0

    skipped_by_ev = []
    skipped_by_stake = []
    played = []

    for _ in range(n_samples):
        edge_pct, odds, stream = sample_bet_all_streams()
        stake, reason = calculate_stake_with_guards(bankroll, edge_pct, odds, min_ev)

        bet_info = (edge_pct, odds, stream)
        if reason == "min_ev":
            skipped_by_ev.append(bet_info)
        elif reason == "min_stake":
            skipped_by_stake.append(bet_info)
        else:
            played.append(bet_info)

    total = n_samples
    print(f"\n  Played:           {len(played):>6,}  ({len(played)/total*100:.1f}%)")
    print(f"  Skipped (min EV): {len(skipped_by_ev):>6,}  ({len(skipped_by_ev)/total*100:.1f}%)")
    print(f"  Skipped (stake):  {len(skipped_by_stake):>6,}  ({len(skipped_by_stake)/total*100:.1f}%)")

    if skipped_by_ev:
        edges = [b[0] for b in skipped_by_ev]
        odds_list = [b[1] for b in skipped_by_ev]
        print(f"\n  SKIPPED BY MIN EV (n={len(skipped_by_ev):,}):")
        print(f"    Edge:  min={min(edges):.1f}%  median={sorted(edges)[len(edges)//2]:.1f}%  max={max(edges):.1f}%")
        print(f"    Odds:  min={min(odds_list):.2f}  median={sorted(odds_list)[len(odds_list)//2]:.2f}  max={max(odds_list):.2f}")

        # By stream
        stream_counts = {}
        for _, _, s in skipped_by_ev:
            stream_counts[s] = stream_counts.get(s, 0) + 1
        print(f"    By stream:")
        for s in ["soft_value", "polymarket", "pinnacle_reverse", "specials"]:
            c = stream_counts.get(s, 0)
            print(f"      {s:<20s}  {c:>5,}  ({c/len(skipped_by_ev)*100:.1f}%)")

    if played:
        edges = [b[0] for b in played]
        odds_list = [b[1] for b in played]
        print(f"\n  PLAYED (n={len(played):,}):")
        print(f"    Edge:  min={min(edges):.1f}%  median={sorted(edges)[len(edges)//2]:.1f}%  max={max(edges):.1f}%")
        print(f"    Odds:  min={min(odds_list):.2f}  median={sorted(odds_list)[len(odds_list)//2]:.2f}  max={max(odds_list):.2f}")


# =====================================================================
# SIMULATION 3: Monte Carlo — growth with different min_expected_profit
# =====================================================================

@dataclass
class GrowthResult:
    final_bankroll: float = 0.0
    total_bets: int = 0
    bets_played: int = 0
    bets_skipped: int = 0
    total_staked: float = 0.0
    profit: float = 0.0
    ruin: bool = False
    missed_ev: float = 0.0  # Theoretical EV we left on the table


def simulate_growth(
    starting_bankroll: float,
    bets_per_week: int,
    weeks: int,
    min_expected_profit: float,
    single_bet_cap_pct: float = 0.03,
) -> GrowthResult:
    bankroll = starting_bankroll
    bets_played = 0
    bets_skipped = 0
    total_staked = 0.0
    profit = 0.0
    missed_ev = 0.0

    for w in range(weeks):
        n_bets = bets_per_week + random.randint(-3, 3)
        n_bets = max(1, n_bets)

        for _ in range(n_bets):
            if bankroll < ABSOLUTE_MIN_STAKE:
                return GrowthResult(
                    final_bankroll=bankroll,
                    total_bets=bets_played + bets_skipped,
                    bets_played=bets_played,
                    bets_skipped=bets_skipped,
                    total_staked=total_staked,
                    profit=profit,
                    ruin=True,
                    missed_ev=missed_ev,
                )

            edge_pct, odds, _ = sample_bet_all_streams()
            stake, reason = calculate_stake_with_guards(
                bankroll, edge_pct, odds, min_expected_profit, single_bet_cap_pct
            )

            if stake <= 0:
                bets_skipped += 1
                # Calculate what we would have staked and its EV
                edge = edge_pct / 100.0
                frac = kelly_fraction(edge_pct)
                would_stake = round_stake_natural(
                    max(0, min(bankroll * frac * edge / (odds - 1), bankroll * single_bet_cap_pct))
                )
                if would_stake >= ABSOLUTE_MIN_STAKE:
                    missed_ev += would_stake * edge
                continue

            result = simulate_bet(stake, edge_pct, odds)
            bankroll += result
            profit += result
            total_staked += stake
            bets_played += 1

    return GrowthResult(
        final_bankroll=bankroll,
        total_bets=bets_played + bets_skipped,
        bets_played=bets_played,
        bets_skipped=bets_skipped,
        total_staked=total_staked,
        profit=profit,
        ruin=bankroll < ABSOLUTE_MIN_STAKE,
        missed_ev=missed_ev,
    )


def pct(values, p):
    s = sorted(values)
    idx = int(len(s) * p / 100.0)
    return s[min(idx, len(s) - 1)]


def growth_simulation():
    """Monte Carlo: compare different min_expected_profit values."""

    print("\n\n" + "=" * 90)
    print("  SIMULATION 3: GROWTH COMPARISON — different min_expected_profit thresholds")
    print(f"  {NUM_SIMS:,} Monte Carlo runs | 35 bets/week | 52 weeks | no bonuses")
    print("=" * 90)

    bankrolls = [5000, 10000, 20000]
    min_evs = [0.0, 0.5, 1.0, 2.0]

    for bankroll in bankrolls:
        print(f"\n  {'─'*86}")
        print(f"  Starting bankroll: {bankroll:,} kr")
        print(f"  {'─'*86}")

        print(f"\n  {'mEP':>5s}  {'Med Final':>10s}  {'Med Profit':>11s}  {'Growth':>8s}  "
              f"{'Played':>7s}  {'Skipped':>8s}  {'Play%':>6s}  "
              f"{'Missed EV':>10s}  {'Ruin%':>6s}")
        print(f"  {'-'*5}  {'-'*10}  {'-'*11}  {'-'*8}  "
              f"{'-'*7}  {'-'*8}  {'-'*6}  "
              f"{'-'*10}  {'-'*6}")

        for mev in min_evs:
            results = []
            for _ in range(NUM_SIMS):
                r = simulate_growth(bankroll, 35, 52, mev)
                results.append(r)

            finals = [r.final_bankroll for r in results]
            profits = [r.profit for r in results]
            played = [r.bets_played for r in results]
            skipped = [r.bets_skipped for r in results]
            missed = [r.missed_ev for r in results]
            ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

            med_final = pct(finals, 50)
            med_profit = pct(profits, 50)
            med_played = pct(played, 50)
            med_skipped = pct(skipped, 50)
            med_missed = pct(missed, 50)
            play_pct = med_played / max(1, med_played + med_skipped) * 100
            growth = (med_final / bankroll - 1) * 100

            print(f"  {mev:>5.1f}  {med_final:>10,.0f}  {med_profit:>+10,.0f}  {growth:>+7.0f}%  "
                  f"{med_played:>7,.0f}  {med_skipped:>8,.0f}  {play_pct:>5.1f}%  "
                  f"{med_missed:>+9,.0f}  {ruin_pct:>5.1f}%")


# =====================================================================
# SIMULATION 4: Bankroll needed to play X% of bets
# =====================================================================

def bankroll_threshold_analysis():
    """Find the bankroll needed to play 90%, 95%, 99% of all bets."""

    print("\n\n" + "=" * 90)
    print("  SIMULATION 4: BANKROLL NEEDED FOR TARGET COVERAGE")
    print("  What bankroll do you need to play 90/95/99% of opportunities?")
    print("=" * 90)

    min_evs = [0.0, 0.5, 1.0, 2.0]
    bankrolls = [2000, 3000, 5000, 7500, 10000, 15000, 20000, 30000, 50000, 75000, 100000]
    n_samples = 20000

    for mev in min_evs:
        print(f"\n  min_expected_profit = {mev:.1f} kr")
        print(f"  {'Bankroll':>10s}  {'Playable':>9s}  {'Skipped':>8s}  {'Avg stake':>10s}  {'Avg EV/bet':>11s}")
        print(f"  {'-'*10}  {'-'*9}  {'-'*8}  {'-'*10}  {'-'*11}")

        for bankroll in bankrolls:
            playable = 0
            total_stake = 0.0
            total_ev = 0.0

            for _ in range(n_samples):
                edge_pct, odds, _ = sample_bet_all_streams()
                stake, reason = calculate_stake_with_guards(bankroll, edge_pct, odds, mev)
                if stake > 0:
                    playable += 1
                    total_stake += stake
                    total_ev += stake * edge_pct / 100.0

            pct_play = playable / n_samples * 100
            avg_stake = total_stake / max(1, playable)
            avg_ev = total_ev / max(1, playable)
            skipped = n_samples - playable

            marker = ""
            if pct_play >= 99:
                marker = " <-- 99%+"
            elif pct_play >= 95:
                marker = " <-- 95%+"
            elif pct_play >= 90:
                marker = " <-- 90%+"

            print(f"  {bankroll:>9,}  {pct_play:>8.1f}%  {skipped:>7,}  {avg_stake:>9.0f} kr  {avg_ev:>10.2f} kr{marker}")


# =====================================================================
# SIMULATION 5: Variance impact — does playing more bets reduce variance?
# =====================================================================

def variance_analysis():
    """Compare variance of outcomes with different min_expected_profit."""

    print("\n\n" + "=" * 90)
    print("  SIMULATION 5: VARIANCE IMPACT — more bets = smoother equity curve?")
    print(f"  {NUM_SIMS:,} runs | 10,000 kr start | 35 bets/week | 52 weeks")
    print("=" * 90)

    bankroll = 10000
    min_evs = [0.0, 0.5, 1.0, 2.0]

    print(f"\n  {'mEP':>5s}  {'P5':>9s}  {'P10':>9s}  {'P25':>9s}  {'Median':>9s}  "
          f"{'P75':>9s}  {'P90':>9s}  {'P95':>9s}  {'Ruin%':>6s}  {'Bets':>6s}")
    print(f"  {'-'*5}  {'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}  "
          f"{'-'*9}  {'-'*9}  {'-'*9}  {'-'*6}  {'-'*6}")

    for mev in min_evs:
        results = []
        for _ in range(NUM_SIMS):
            r = simulate_growth(bankroll, 35, 52, mev)
            results.append(r)

        finals = [r.final_bankroll for r in results]
        bets = [r.bets_played for r in results]
        ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

        print(f"  {mev:>5.1f}  {pct(finals,5):>9,.0f}  {pct(finals,10):>9,.0f}  {pct(finals,25):>9,.0f}  "
              f"{pct(finals,50):>9,.0f}  {pct(finals,75):>9,.0f}  {pct(finals,90):>9,.0f}  "
              f"{pct(finals,95):>9,.0f}  {ruin_pct:>5.1f}%  {pct(bets,50):>5,.0f}")


# =====================================================================
# SIMULATION 6: Optimal min_expected_profit by bankroll
# =====================================================================

def optimal_threshold():
    """For each bankroll, find the min_expected_profit that maximizes median profit."""

    print("\n\n" + "=" * 90)
    print("  SIMULATION 6: OPTIMAL min_expected_profit BY BANKROLL")
    print(f"  Which threshold maximizes median profit at each bankroll level?")
    print(f"  {NUM_SIMS:,} runs | 35 bets/week | 52 weeks | no bonuses")
    print("=" * 90)

    bankrolls = [3000, 5000, 7500, 10000, 15000, 20000, 50000]
    min_evs = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]

    for bankroll in bankrolls:
        print(f"\n  Bankroll: {bankroll:,} kr")
        print(f"  {'mEP':>5s}  {'Med Profit':>11s}  {'Med Growth':>10s}  {'Play%':>6s}  {'Ruin%':>6s}  {'P10 Profit':>11s}")
        print(f"  {'-'*5}  {'-'*11}  {'-'*10}  {'-'*6}  {'-'*6}  {'-'*11}")

        best_mev = 0.0
        best_profit = -999999

        for mev in min_evs:
            results = []
            for _ in range(NUM_SIMS):
                r = simulate_growth(bankroll, 35, 52, mev)
                results.append(r)

            profits = [r.profit for r in results]
            played = [r.bets_played for r in results]
            skipped = [r.bets_skipped for r in results]
            ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

            med_profit = pct(profits, 50)
            p10_profit = pct(profits, 10)
            med_played = pct(played, 50)
            med_skipped = pct(skipped, 50)
            play_pct = med_played / max(1, med_played + med_skipped) * 100
            growth = med_profit / bankroll * 100

            marker = ""
            if med_profit > best_profit:
                best_profit = med_profit
                best_mev = mev

            print(f"  {mev:>5.2f}  {med_profit:>+10,.0f}  {growth:>+9.0f}%  {play_pct:>5.1f}%  {ruin_pct:>5.1f}%  {p10_profit:>+10,.0f}")

        print(f"  >>> Best: mEP={best_mev:.2f} (median profit {best_profit:+,.0f} kr)")


# =====================================================================
# MAIN
# =====================================================================

def main():
    random.seed(42)

    print("=" * 90)
    print("  FIREV — BANKROLL COVERAGE & MIN EXPECTED PROFIT ANALYSIS")
    print("  How bankroll size and mEP threshold affect bet coverage and growth")
    print("=" * 90)

    coverage_analysis()
    skipped_bet_profile()
    growth_simulation()
    bankroll_threshold_analysis()
    variance_analysis()
    optimal_threshold()

    print("\n\n" + "=" * 90)
    print("  CONCLUSIONS")
    print("=" * 90)
    print("""
  The min_expected_profit guard filters bets where stake * edge < threshold.
  For low-edge bets at high odds, this requires a very large bankroll because:

    bankroll_needed = mEP * (odds - 1) / (kelly * edge^2)

  edge^2 in the denominator means a 5% edge needs 4x the bankroll of a 10% edge.
  (odds - 1) means longshots need proportionally more bankroll.

  RECOMMENDATION (read simulation results above for data):
  - If you have < 10k kr: consider mEP=0.5 to play more bets
  - If you have 10-20k kr: mEP=1.0 is a good balance
  - If you have > 20k kr: mEP=2.0 is fine (most bets already playable)
  - mEP=0.0 (no guard) plays everything but includes tiny-EV bets

  The guard's purpose is "not worth the click for 0.50 kr expected profit."
  If you're automated or don't mind small bets, lower it.
""")


if __name__ == "__main__":
    main()
