"""
Dynamic stake schedule: optimal cap/max_kelly/min_stake at each bankroll tier.

Runs MC sim across bankroll sizes from $100 → $20k. For each tier, sweeps
params and picks Pareto-optimal (max growth subject to bust <= 10%).

Edge distribution is CALIBRATED from live value-bet batch (n=138 pinnacle +
polymarket + kalshi candidates, 2026-05-17). Distribution snapshot:

    bin     n   weight  avg_odds  avg_edge
    1-2%   43   0.312   3.53     1.44%
    2-4%   51   0.370   3.52     2.84%
    4-7%   22   0.159   3.63     5.06%
    7-12%  11   0.080   4.52     8.51%
    12-25%  11  0.080   6.50    18.66%

The pre-calibration POLY_EDGE_DIST overweighted high-edge bets — real
production sees a much more bottom-heavy distribution.

Run: cd backend && python scripts/dynamic_stake_sim.py
"""

import io
import random
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# (edge_min%, edge_max%, weight, avg_odds_in_bin) — calibrated from live data
VALUE_EDGE_DIST = [
    (1.0, 2.0, 0.31, 3.53),
    (2.0, 4.0, 0.37, 3.52),
    (4.0, 7.0, 0.16, 3.63),
    (7.0, 12.0, 0.08, 4.52),
    (12.0, 25.0, 0.08, 6.50),
]

EDGE_MULT = 0.7  # realistic — true edge is ~70% of displayed (slippage + fair-odds noise)
BETS_PER_DAY = 10
DAYS = 180
TOTAL_BETS = BETS_PER_DAY * DAYS

# Bankroll tiers — start at $100, end at $20k
BANKROLL_TIERS = [100, 250, 500, 1000, 2500, 5000, 10000, 20000]

# Param sweep
CAPS = [0.02, 0.03, 0.05, 0.08, 0.10, 0.15]
MAX_KS = [0.5, 0.75, 1.0, 1.25, 1.5]
# min_stake as % of bankroll instead of absolute (so it scales naturally)
MIN_STAKE_PCT = [0.001, 0.002, 0.005, 0.01, 0.02]

NUM_SIMS = 500


def sample_bet():
    r = random.random()
    cum = 0.0
    for emin, emax, w, odds in VALUE_EDGE_DIST:
        cum += w
        if r <= cum:
            return random.uniform(emin, emax), odds
    return 5.0, 3.0


def kelly_fraction(edge_pct, max_kelly):
    if edge_pct <= 2.0:
        return 0.25
    if edge_pct >= 6.0:
        return max_kelly
    t = (edge_pct - 2.0) / 4.0
    return 0.25 + t * (max_kelly - 0.25)


def compute_stake(bankroll, edge_pct, odds, cap_pct, max_kelly, min_stake_abs):
    if bankroll < min_stake_abs:
        return 0.0
    k = kelly_fraction(edge_pct, max_kelly)
    raw = bankroll * k * (edge_pct / 100) / (odds - 1)
    cap = bankroll * cap_pct
    stake = min(raw, cap)
    if stake < min_stake_abs:
        stake = min_stake_abs
    return min(stake, bankroll)


def simulate_one(start, cap, mk, min_stake_pct, seed):
    rng = random.Random(seed)
    bankroll = start
    bust = False
    t2x = None
    target_2x = start * 2
    for i in range(TOTAL_BETS):
        day = i // BETS_PER_DAY + 1
        edge_disp, odds = sample_bet()
        edge_true = edge_disp * EDGE_MULT / 100
        p_win = min(0.99, max(0.01, (1.0 / odds) * (1.0 + edge_true)))
        # min_stake scales with CURRENT bankroll for adaptive sizing
        min_stake_abs = max(0.05, bankroll * min_stake_pct)
        stake = compute_stake(bankroll, edge_disp, odds, cap, mk, min_stake_abs)
        if stake <= 0:
            bust = True
            break
        if rng.random() < p_win:
            bankroll += stake * (odds - 1)
        else:
            bankroll -= stake
        if t2x is None and bankroll >= target_2x:
            t2x = day
        if bankroll <= min_stake_abs:
            bust = True
            break
    return bankroll, bust, t2x


def median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0


def find_best(start):
    """Sweep all combos for this bankroll; return top Pareto picks."""
    results = []
    for cap in CAPS:
        for mk in MAX_KS:
            for ms_pct in MIN_STAKE_PCT:
                terms = []
                t2s = []
                busts = 0
                for s in range(NUM_SIMS):
                    bank, bust, t2 = simulate_one(start, cap, mk, ms_pct, seed=s)
                    terms.append(bank)
                    if t2 is not None:
                        t2s.append(t2)
                    if bust:
                        busts += 1
                med_term = median(terms)
                med_t2 = median(t2s) if t2s else 999
                bust_pct = 100 * busts / NUM_SIMS
                pct_doubled = 100 * len(t2s) / NUM_SIMS
                growth_x = med_term / start
                results.append((cap, mk, ms_pct, growth_x, med_term, med_t2, pct_doubled, bust_pct))
    # Pareto: bust <= 10%, then max growth
    safe = [r for r in results if r[7] <= 10]
    if not safe:
        safe = [r for r in results if r[7] <= 20]
    safe.sort(key=lambda r: -r[3])
    return safe[:3]


def main():
    print(
        f"Dynamic stake sim — REAL value-bet edge dist (0.7× realism), {BETS_PER_DAY}/day × {DAYS}d, {NUM_SIMS} runs/combo"
    )
    print(f"Bankroll tiers: {BANKROLL_TIERS}")
    print(f"\n{'=' * 95}")
    print("OPTIMAL PARAMS PER BANKROLL TIER (top 3 Pareto picks, bust<=10%)")
    print(f"{'=' * 95}")
    print(
        f"{'start$':>8} | {'cap':>5} {'maxK':>5} {'min%':>6} (min$) | {'growth':>7} {'medTerm':>9} {'days2x':>7} {'%>2x':>6} {'bust%':>6}"
    )
    print("-" * 95)

    schedule = []
    for start in BANKROLL_TIERS:
        picks = find_best(start)
        if not picks:
            print(f"{start:>8} | (no safe options)")
            continue
        for i, (cap, mk, ms_pct, gx, term, t2, pct2x, bust) in enumerate(picks):
            ms_abs = max(0.05, start * ms_pct)
            tag = "★" if i == 0 else " "
            print(
                f"{start:>7}{tag} | {cap * 100:>4.0f}% {mk:>5.2f} {ms_pct * 100:>5.2f}% (${ms_abs:>5.2f}) | "
                f"{gx:>6.2f}× {term:>9.1f} {t2 if t2 < 999 else '—':>7} "
                f"{pct2x:>5.0f}% {bust:>5.1f}%"
            )
            if i == 0:
                schedule.append((start, cap, mk, ms_pct, ms_abs))

    print(f"\n{'=' * 95}")
    print("DERIVED DYNAMIC SCHEDULE (top pick per tier)")
    print(f"{'=' * 95}")
    print(f"{'bankroll':>10} | {'cap_pct':>8} {'max_kelly':>10} {'min_stake$':>11}")
    for start, cap, mk, ms_pct, ms_abs in schedule:
        print(f"  ${start:>7}  | {cap * 100:>6.1f}% {mk:>9.2f} ${ms_abs:>9.2f}")

    print(f"\n{'=' * 95}")
    print("PIECEWISE FUNCTION (recommended)")
    print(f"{'=' * 95}")
    print("def dynamic_cap_pct(bankroll):")
    for i, (start, cap, _, _, _) in enumerate(schedule):
        op = "if" if i == 0 else "elif"
        nxt = schedule[i + 1][0] if i + 1 < len(schedule) else None
        if nxt:
            print(f"    {op} bankroll < {nxt}: return {cap:.3f}  # ${start} tier")
        else:
            print(f"    else: return {cap:.3f}  # ${start}+ tier")
    print()
    print("def dynamic_max_kelly(bankroll):")
    for i, (start, _, mk, _, _) in enumerate(schedule):
        op = "if" if i == 0 else "elif"
        nxt = schedule[i + 1][0] if i + 1 < len(schedule) else None
        if nxt:
            print(f"    {op} bankroll < {nxt}: return {mk:.2f}  # ${start} tier")
        else:
            print(f"    else: return {mk:.2f}  # ${start}+ tier")


if __name__ == "__main__":
    main()
