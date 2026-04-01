"""
Firev — Single Bet Cap Comparison Simulation
=============================================================
Compare 1%, 2%, 3% single bet caps across bankroll levels.

Key metrics:
  - Median growth & profit
  - Variance (P5/P25/P75/P95 spread)
  - Max drawdown from peak
  - Per-provider drain frequency (how often a 1k provider goes bust)
  - Ruin rate
  - Kelly cap hit rate (how often the cap overrides Kelly)

Run: python scripts/cap_comparison_sim.py
"""

import random
import sys
import io
import math
from dataclasses import dataclass, field
from typing import List, Tuple

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Edge distributions (from live opportunity data) ──
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

# Kelly parameters
MIN_KELLY = 0.25
MAX_KELLY = 0.75
MIN_STAKE = 25.0
ABSOLUTE_MIN_STAKE = 5.0

# Dynamic Kelly boost at low bankrolls
DYNAMIC_KELLY_LOW = 5000
DYNAMIC_KELLY_HIGH = 10000
DYNAMIC_KELLY_BOOST = 1.5

NUM_SIMS = 5000
WEEKS = 52
BETS_PER_WEEK = 35


def kelly_fraction(edge_pct: float, max_kelly: float = MAX_KELLY) -> float:
    if edge_pct <= 2.0:
        return MIN_KELLY
    elif edge_pct >= 6.0:
        return max_kelly
    t = (edge_pct - 2.0) / 4.0
    return MIN_KELLY + t * (max_kelly - MIN_KELLY)


def effective_max_kelly(bankroll: float) -> float:
    if bankroll <= DYNAMIC_KELLY_LOW:
        return MAX_KELLY * DYNAMIC_KELLY_BOOST
    elif bankroll < DYNAMIC_KELLY_HIGH:
        t = (bankroll - DYNAMIC_KELLY_LOW) / (DYNAMIC_KELLY_HIGH - DYNAMIC_KELLY_LOW)
        boosted = MAX_KELLY * DYNAMIC_KELLY_BOOST
        return boosted - t * (boosted - MAX_KELLY)
    return MAX_KELLY


def dynamic_min_stake(bankroll: float) -> float:
    raw = max(ABSOLUTE_MIN_STAKE, bankroll * 0.005)
    capped = min(raw, MIN_STAKE)
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
    if random.random() < win_prob:
        return stake * (odds - 1.0)
    return -stake


@dataclass
class SimResult:
    final_bankroll: float = 0.0
    profit: float = 0.0
    bets_played: int = 0
    bets_skipped: int = 0
    total_staked: float = 0.0
    peak: float = 0.0
    trough: float = 0.0
    max_drawdown_pct: float = 0.0
    ruin: bool = False
    cap_hits: int = 0  # How many times cap overrode Kelly
    provider_drains: int = 0  # How many times a 1k provider would bust
    weekly_bankrolls: List[float] = field(default_factory=list)


def simulate(
    starting_bankroll: float,
    cap_pct: float,
    bets_per_week: int = BETS_PER_WEEK,
    weeks: int = WEEKS,
    provider_balance: float = 1000.0,
) -> SimResult:
    bankroll = starting_bankroll
    profit = 0.0
    bets_played = 0
    bets_skipped = 0
    total_staked = 0.0
    peak = bankroll
    trough = bankroll
    max_dd_pct = 0.0
    cap_hits = 0

    # Track per-provider drain simulation
    # Simulate a single provider with `provider_balance` initial balance
    # Count how many times it goes to 0 (needing redeposit)
    prov_balance = provider_balance
    prov_drains = 0

    weekly = []

    for w in range(weeks):
        n_bets = bets_per_week + random.randint(-3, 3)
        n_bets = max(1, n_bets)

        for _ in range(n_bets):
            if bankroll < ABSOLUTE_MIN_STAKE:
                weekly.extend([bankroll] * (weeks - w))
                return SimResult(
                    final_bankroll=bankroll, profit=profit,
                    bets_played=bets_played, bets_skipped=bets_skipped,
                    total_staked=total_staked, peak=peak, trough=trough,
                    max_drawdown_pct=max_dd_pct, ruin=True,
                    cap_hits=cap_hits, provider_drains=prov_drains,
                    weekly_bankrolls=weekly,
                )

            edge_pct, odds, _ = sample_bet()
            edge = edge_pct / 100.0
            mk = effective_max_kelly(bankroll)
            frac = kelly_fraction(edge_pct, mk)
            min_s = dynamic_min_stake(bankroll)

            # Dynamic cap at low bankrolls (same logic as stake_calculator)
            eff_cap = cap_pct
            if bankroll <= DYNAMIC_KELLY_LOW:
                eff_cap = max(cap_pct, cap_pct + 0.01)  # +1% boost at low BR
            elif bankroll < DYNAMIC_KELLY_HIGH:
                t = (bankroll - DYNAMIC_KELLY_LOW) / (DYNAMIC_KELLY_HIGH - DYNAMIC_KELLY_LOW)
                eff_cap = max(cap_pct, (cap_pct + 0.01) - t * 0.01)

            raw = bankroll * frac * edge / (odds - 1.0)
            cap_val = bankroll * eff_cap
            was_capped = raw > cap_val
            stake = min(raw, cap_val)
            stake = max(0.0, stake)
            stake = round_stake_natural(stake)

            if stake < min_s:
                bets_skipped += 1
                continue

            # Min expected profit guard
            min_ep = max(0.10, bankroll * 0.000075)
            min_ep = min(min_ep, 0.75)
            if stake * edge < min_ep:
                bets_skipped += 1
                continue

            if was_capped:
                cap_hits += 1

            result = simulate_bet(stake, edge_pct, odds)
            bankroll += result
            profit += result
            total_staked += stake
            bets_played += 1
            peak = max(peak, bankroll)
            trough = min(trough, bankroll)
            if peak > 0:
                dd = (peak - bankroll) / peak
                max_dd_pct = max(max_dd_pct, dd)

            # Provider drain tracking (assume ~1/5 of bets go to this provider)
            if random.random() < 0.2:  # ~20% chance bet is at tracked provider
                prov_balance += result
                if prov_balance <= 0:
                    prov_drains += 1
                    prov_balance = provider_balance  # Redeposit

        weekly.append(bankroll)

    return SimResult(
        final_bankroll=bankroll, profit=profit,
        bets_played=bets_played, bets_skipped=bets_skipped,
        total_staked=total_staked, peak=peak, trough=trough,
        max_drawdown_pct=max_dd_pct, ruin=False,
        cap_hits=cap_hits, provider_drains=prov_drains,
        weekly_bankrolls=weekly,
    )


def pct(values, p):
    s = sorted(values)
    idx = int(len(s) * p / 100.0)
    return s[min(idx, len(s) - 1)]


def run_comparison():
    caps = [0.01, 0.02, 0.03]
    bankrolls = [5000, 7500, 10000, 15000]

    print("=" * 100)
    print("  FIREV — SINGLE BET CAP COMPARISON (1% vs 2% vs 3%)")
    print(f"  {NUM_SIMS:,} MC runs | {BETS_PER_WEEK} bets/week | {WEEKS} weeks | Kelly 0.25-0.75")
    print("=" * 100)

    for bankroll in bankrolls:
        print(f"\n  {'=' * 96}")
        print(f"  STARTING BANKROLL: {bankroll:,} kr")
        print(f"  {'=' * 96}")

        print(f"\n  {'Cap':>5s}  {'Med Final':>10s}  {'Med Profit':>11s}  {'Growth':>8s}  "
              f"{'P5':>9s}  {'P25':>9s}  {'P75':>9s}  {'P95':>9s}  "
              f"{'Ruin%':>6s}")
        print(f"  {'-'*5}  {'-'*10}  {'-'*11}  {'-'*8}  "
              f"{'-'*9}  {'-'*9}  {'-'*9}  {'-'*9}  "
              f"{'-'*6}")

        for cap in caps:
            random.seed(42)  # Same seed for fair comparison
            results = [simulate(bankroll, cap) for _ in range(NUM_SIMS)]

            finals = [r.final_bankroll for r in results]
            profits = [r.profit for r in results]
            ruin_rate = sum(1 for r in results if r.ruin) / len(results) * 100
            med_final = pct(finals, 50)
            growth = (med_final / bankroll - 1) * 100

            print(f"  {cap*100:>4.0f}%  {med_final:>10,.0f}  {pct(profits,50):>+10,.0f}  {growth:>+7.0f}%  "
                  f"{pct(finals,5):>9,.0f}  {pct(finals,25):>9,.0f}  "
                  f"{pct(finals,75):>9,.0f}  {pct(finals,95):>9,.0f}  "
                  f"{ruin_rate:>5.1f}%")

        # Detailed breakdown
        print(f"\n  {'Cap':>5s}  {'Max DD%':>8s}  {'Med DD%':>8s}  "
              f"{'Cap Hits':>10s}  {'Cap Hit%':>9s}  "
              f"{'Played':>8s}  {'Skipped':>8s}  {'Play%':>7s}  "
              f"{'Prov Drains':>12s}")
        print(f"  {'-'*5}  {'-'*8}  {'-'*8}  "
              f"{'-'*10}  {'-'*9}  "
              f"{'-'*8}  {'-'*8}  {'-'*7}  "
              f"{'-'*12}")

        for cap in caps:
            random.seed(42)
            results = [simulate(bankroll, cap) for _ in range(NUM_SIMS)]

            max_dds = [r.max_drawdown_pct * 100 for r in results]
            cap_hits_list = [r.cap_hits for r in results]
            played = [r.bets_played for r in results]
            skipped = [r.bets_skipped for r in results]
            drains = [r.provider_drains for r in results]

            med_played = pct(played, 50)
            med_skipped = pct(skipped, 50)
            play_pct = med_played / max(1, med_played + med_skipped) * 100
            med_cap_hits = pct(cap_hits_list, 50)
            cap_hit_pct = med_cap_hits / max(1, med_played) * 100

            print(f"  {cap*100:>4.0f}%  {pct(max_dds,95):>7.1f}%  {pct(max_dds,50):>7.1f}%  "
                  f"{med_cap_hits:>10,.0f}  {cap_hit_pct:>8.1f}%  "
                  f"{med_played:>8,.0f}  {med_skipped:>8,.0f}  {play_pct:>6.1f}%  "
                  f"{pct(drains,50):>12,.0f}")


def run_polymarket_focus():
    """Focused sim: Polymarket with $100 deposit, different caps."""
    print("\n\n" + "=" * 100)
    print("  POLYMARKET FOCUS — $100 USDC deposit, sizing from total bankroll")
    print("  How many bets before the $100 provider balance drains?")
    print("=" * 100)

    caps = [0.01, 0.02, 0.03]
    total_bankrolls = [5000, 7500, 10000]  # Total portfolio in kr

    # Polymarket bets only, $100 provider balance
    # Assume 1 USD ≈ 10 kr for simplicity
    poly_deposit_usd = 100

    print(f"\n  {'Total BR':>10s}  {'Cap':>5s}  {'Med bets':>10s}  {'Med $ left':>11s}  "
          f"{'Drain%':>7s}  {'Avg stake $':>12s}  {'Med profit $':>13s}")
    print(f"  {'-'*10}  {'-'*5}  {'-'*10}  {'-'*11}  "
          f"{'-'*7}  {'-'*12}  {'-'*13}")

    for total_br in total_bankrolls:
        for cap in caps:
            random.seed(42)
            n_sims = 5000
            bets_before_drain = []
            balances_after_20 = []
            profits = []

            for _ in range(n_sims):
                balance_usd = float(poly_deposit_usd)
                bankroll_kr = float(total_br)
                bet_count = 0
                pnl = 0.0

                # Simulate 20 Polymarket bets
                for _ in range(20):
                    if balance_usd < 1.0:
                        break

                    edge_pct, odds = sample_from_stream(POLYMARKET_EDGE_DIST)
                    edge = edge_pct / 100.0
                    mk = effective_max_kelly(bankroll_kr)
                    frac = kelly_fraction(edge_pct, mk)

                    # Stake in kr, then convert to USD
                    raw_kr = bankroll_kr * frac * edge / (odds - 1.0)
                    cap_kr = bankroll_kr * cap
                    stake_kr = min(raw_kr, cap_kr)
                    stake_usd = stake_kr / 10.0  # ~10 kr per USD

                    # Can't bet more than provider balance
                    stake_usd = min(stake_usd, balance_usd)
                    if stake_usd < 1.0:
                        break

                    result = simulate_bet(stake_usd, edge_pct, odds)
                    balance_usd += result
                    pnl += result
                    bankroll_kr += result * 10  # Update total BR too
                    bet_count += 1

                bets_before_drain.append(bet_count)
                balances_after_20.append(balance_usd)
                profits.append(pnl)

            drain_pct = sum(1 for b in bets_before_drain if b < 20) / n_sims * 100
            avg_stake = poly_deposit_usd / max(1, pct(bets_before_drain, 50)) if pct(bets_before_drain, 50) > 0 else 0

            print(f"  {total_br:>9,}  {cap*100:>4.0f}%  {pct(bets_before_drain,50):>10,.0f}  "
                  f"${pct(balances_after_20,50):>9.1f}  "
                  f"{drain_pct:>6.1f}%  "
                  f"${poly_deposit_usd / max(1, pct(bets_before_drain, 50)):>10.1f}  "
                  f"${pct(profits,50):>11.1f}")


def run_stake_distribution():
    """Show what actual stake sizes look like at each cap level."""
    print("\n\n" + "=" * 100)
    print("  STAKE SIZE DISTRIBUTION — what does Kelly actually produce?")
    print("  10,000 sampled bets at 7,500 kr bankroll")
    print("=" * 100)

    bankroll = 7500
    caps = [0.01, 0.02, 0.03]
    n_samples = 10000

    for cap in caps:
        random.seed(42)
        stakes = []
        cap_count = 0

        for _ in range(n_samples):
            edge_pct, odds, _ = sample_bet()
            edge = edge_pct / 100.0
            mk = effective_max_kelly(bankroll)
            frac = kelly_fraction(edge_pct, mk)

            raw = bankroll * frac * edge / (odds - 1.0)
            cap_val = bankroll * cap
            was_capped = raw > cap_val
            stake = min(raw, cap_val)
            stake = round_stake_natural(stake)
            min_s = dynamic_min_stake(bankroll)

            if stake >= min_s:
                stakes.append(stake)
                if was_capped:
                    cap_count += 1

        stakes.sort()
        n = len(stakes)
        cap_pct_val = cap_count / max(1, n) * 100

        print(f"\n  Cap: {cap*100:.0f}% ({bankroll * cap:.0f} kr max)  |  {n:,} playable of {n_samples:,}  |  {cap_pct_val:.0f}% hit cap")
        print(f"    P5={stakes[n//20]:.0f}  P25={stakes[n//4]:.0f}  Median={stakes[n//2]:.0f}  "
              f"P75={stakes[3*n//4]:.0f}  P95={stakes[19*n//20]:.0f}  Max={stakes[-1]:.0f}")

        # Histogram buckets
        buckets = [(0, 25), (25, 50), (50, 75), (75, 100), (100, 150), (150, 200), (200, 250), (250, 500)]
        print(f"    Distribution:")
        for lo, hi in buckets:
            count = sum(1 for s in stakes if lo <= s < hi)
            bar = "#" * (count * 40 // n)
            print(f"      {lo:>4}-{hi:<4} kr: {count:>5,} ({count/n*100:>5.1f}%) {bar}")


def run_worst_case_streaks():
    """Simulate worst-case losing streaks at each cap level."""
    print("\n\n" + "=" * 100)
    print("  WORST-CASE LOSING STREAKS — bankroll impact of consecutive losses")
    print("  Starting: 7,500 kr | 10 consecutive losses at different edges/odds")
    print("=" * 100)

    bankroll_start = 7500
    caps = [0.01, 0.02, 0.03]

    # Typical bet profiles
    profiles = [
        ("Low edge / low odds",   3.0, 2.50),
        ("Med edge / med odds",   8.0, 4.00),
        ("High edge / high odds", 20.0, 8.00),
    ]

    for name, edge_pct, odds in profiles:
        print(f"\n  {name} (edge={edge_pct:.0f}%, odds={odds:.1f})")
        print(f"  {'Cap':>5s}  {'After 5L':>10s}  {'After 10L':>10s}  {'5L DD%':>8s}  {'10L DD%':>8s}  {'Stake':>8s}")
        print(f"  {'-'*5}  {'-'*10}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*8}")

        for cap in caps:
            br = float(bankroll_start)
            stakes_used = []

            for i in range(10):
                edge = edge_pct / 100.0
                mk = effective_max_kelly(br)
                frac = kelly_fraction(edge_pct, mk)
                raw = br * frac * edge / (odds - 1.0)
                cap_val = br * cap
                stake = round_stake_natural(min(raw, cap_val))
                stakes_used.append(stake)
                br -= stake  # Loss

            br5 = bankroll_start
            for s in stakes_used[:5]:
                br5 -= s

            dd5 = (bankroll_start - br5) / bankroll_start * 100
            dd10 = (bankroll_start - br) / bankroll_start * 100

            print(f"  {cap*100:>4.0f}%  {br5:>10,.0f}  {br:>10,.0f}  {dd5:>7.1f}%  {dd10:>7.1f}%  {stakes_used[0]:>7.0f}")


def main():
    print("=" * 100)
    print("  FIREV — BET CAP COMPARISON: 1% vs 2% vs 3%")
    print("  Does lowering the cap reduce variance without killing growth?")
    print("=" * 100)

    run_comparison()
    run_polymarket_focus()
    run_stake_distribution()
    run_worst_case_streaks()

    print("\n\n" + "=" * 100)
    print("  SUMMARY")
    print("=" * 100)
    print("""
  Compare the three caps across:
  1. GROWTH: How much median profit do you sacrifice?
  2. VARIANCE: P5/P95 spread — how wide is the outcome range?
  3. DRAWDOWN: Max peak-to-trough loss
  4. CAP HIT RATE: How often Kelly is overridden (higher = more flat betting)
  5. PROVIDER DRAINS: How often a 1k provider balance goes bust
  6. POLYMARKET: How many bets before $100 deposit drains
  7. LOSING STREAKS: Bankroll impact of 5/10 consecutive losses

  The ideal cap:
  - Lets Kelly differentiate between high/low edge (low cap hit rate)
  - Limits worst-case drawdowns to survivable levels
  - Keeps provider balances alive long enough to be practical
  - Doesn't sacrifice so much growth that the edge isn't worth playing
""")


if __name__ == "__main__":
    main()
