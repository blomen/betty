"""
Monte Carlo: optimal stake structure for small ($45) polymarket-heavy bankroll.

Reports time-to-double, time-to-5x, monthly growth rate alongside terminal
median + bust risk so the user can pick a risk profile, not just survival.

Run: cd backend && python scripts/small_bankroll_sim.py
"""

import io
import random
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Live polymarket value-bet edge distribution
POLY_EDGE_DIST = [
    (1.0, 2.0, 0.20, 4.0),
    (2.0, 4.0, 0.30, 3.5),
    (4.0, 7.0, 0.25, 3.0),
    (7.0, 12.0, 0.15, 4.5),
    (12.0, 25.0, 0.10, 6.0),
]

EDGE_REALIZATIONS = [
    ("optimistic (1.0x)", 1.0),
    ("realistic (0.7x)", 0.7),
    ("pessimistic (0.4x)", 0.4),
]

BETS_PER_DAY = 10
DAYS = 180  # 6 months — long enough to see 5-10x growth in aggressive scenarios
TOTAL_BETS = BETS_PER_DAY * DAYS

# Wider sweep — include aggressive options
CAP_VALUES = [0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.25]
MAX_KELLY_VALUES = [0.5, 0.75, 1.0, 1.5]
MIN_STAKE_VALUES = [0.25, 0.5, 1.0]

START_BANKROLL = 45.0
NUM_SIMS = 1000


def sample_bet():
    r = random.random()
    cum = 0.0
    for emin, emax, w, odds in POLY_EDGE_DIST:
        cum += w
        if r <= cum:
            return random.uniform(emin, emax), odds
    return 5.0, 3.0


def kelly_fraction(edge_pct: float, max_kelly: float) -> float:
    if edge_pct <= 2.0:
        return 0.25
    if edge_pct >= 6.0:
        return max_kelly
    t = (edge_pct - 2.0) / 4.0
    return 0.25 + t * (max_kelly - 0.25)


def effective_max_kelly(profile_max: float, bankroll: float) -> float:
    if bankroll >= 100.0:
        return profile_max
    if bankroll <= 20.0:
        return profile_max * 1.5
    t = (bankroll - 20.0) / 80.0
    return profile_max * (1.5 - 0.5 * t)


def compute_stake(bankroll, edge_pct, odds, cap_pct, max_kelly, min_stake):
    if bankroll < min_stake:
        return 0.0
    scaled_max = effective_max_kelly(max_kelly, bankroll)
    k = kelly_fraction(edge_pct, scaled_max)
    raw = bankroll * k * (edge_pct / 100) / (odds - 1)
    cap = bankroll * cap_pct
    stake = min(raw, cap)
    if stake < min_stake:
        stake = min_stake
    return min(stake, bankroll)


def simulate_one(cap_pct, max_kelly, min_stake, edge_mult, seed):
    """Returns dict of metrics including time-to-Nx and trajectory snapshots."""
    rng = random.Random(seed)
    bankroll = START_BANKROLL
    peak = bankroll
    max_dd = 0.0
    bust = False
    t2x = None
    t5x = None
    t10x = None
    snapshots = {30: None, 60: None, 90: None, 180: None}
    targets = {2: START_BANKROLL * 2, 5: START_BANKROLL * 5, 10: START_BANKROLL * 10}

    for i in range(TOTAL_BETS):
        day = i // BETS_PER_DAY + 1
        edge_disp, odds = sample_bet()
        edge_true = edge_disp * edge_mult / 100
        p_win = min(0.99, max(0.01, (1.0 / odds) * (1.0 + edge_true)))
        stake = compute_stake(bankroll, edge_disp, odds, cap_pct, max_kelly, min_stake)
        if stake <= 0:
            bust = True
            break
        if rng.random() < p_win:
            bankroll += stake * (odds - 1)
        else:
            bankroll -= stake
        peak = max(peak, bankroll)
        dd = (peak - bankroll) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
        if t2x is None and bankroll >= targets[2]:
            t2x = day
        if t5x is None and bankroll >= targets[5]:
            t5x = day
        if t10x is None and bankroll >= targets[10]:
            t10x = day
        for d in snapshots:
            if snapshots[d] is None and day >= d:
                snapshots[d] = bankroll
        if bankroll <= min_stake:
            bust = True
            break

    for d in snapshots:
        if snapshots[d] is None:
            snapshots[d] = bankroll

    return {
        "terminal": bankroll,
        "max_dd": max_dd,
        "bust": bust,
        "t2x": t2x,
        "t5x": t5x,
        "t10x": t10x,
        "snap30": snapshots[30],
        "snap60": snapshots[60],
        "snap90": snapshots[90],
        "snap180": snapshots[180],
    }


def median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0


def pct(xs, p):
    s = sorted(xs)
    return s[max(0, int(p * len(s)) - 1)] if s else 0


def summarize(combo_results):
    terms = [r["terminal"] for r in combo_results]
    busts = sum(1 for r in combo_results if r["bust"])
    t2 = [r["t2x"] for r in combo_results if r["t2x"] is not None]
    t5 = [r["t5x"] for r in combo_results if r["t5x"] is not None]
    t10 = [r["t10x"] for r in combo_results if r["t10x"] is not None]
    snap30s = [r["snap30"] for r in combo_results]
    snap90s = [r["snap90"] for r in combo_results]
    return {
        "median_term": median(terms),
        "p5_term": pct(terms, 0.05),
        "p95_term": pct(terms, 0.95),
        "median_30d": median(snap30s),
        "median_90d": median(snap90s),
        "pct_doubled": 100 * len(t2) / len(combo_results),
        "median_t2x_days": median(t2) if t2 else None,
        "pct_5xed": 100 * len(t5) / len(combo_results),
        "median_t5x_days": median(t5) if t5 else None,
        "pct_10xed": 100 * len(t10) / len(combo_results),
        "median_t10x_days": median(t10) if t10 else None,
        "bust_pct": 100 * busts / len(combo_results),
        "median_dd": median([r["max_dd"] for r in combo_results]),
    }


def main():
    print(f"MC sim: ${START_BANKROLL} polymarket value bets, {BETS_PER_DAY}/day × {DAYS} days, {NUM_SIMS} runs/combo\n")

    # Compare specific risk profiles head-to-head across edge realizations
    profiles = [
        ("CURRENT", 0.02, 0.75, 1.0),
        ("SAFE", 0.02, 0.50, 0.5),
        ("BALANCED", 0.05, 0.75, 0.5),
        ("AGGRESSIVE", 0.10, 1.00, 0.5),
        ("VERY_AGGR", 0.15, 1.00, 0.25),
        ("FULL_KELLY", 0.25, 1.50, 0.25),
    ]

    for label, edge_mult in EDGE_REALIZATIONS:
        print(f"\n{'=' * 110}")
        print(f"EDGE: {label}")
        print(f"{'=' * 110}")
        print(
            f"{'profile':>12} {'cap':>5} {'maxK':>5} {'min$':>5} | "
            f"{'med30d':>7} {'med90d':>7} {'medTerm':>8} {'P5':>7} {'P95':>8} | "
            f"{'%>2x':>6} {'days2x':>7} {'%>5x':>6} {'days5x':>7} {'%>10x':>7} {'days10x':>8} | "
            f"{'bust%':>6}"
        )
        print("-" * 130)
        for name, cap, mk, ms in profiles:
            runs = [simulate_one(cap, mk, ms, edge_mult, seed=s) for s in range(NUM_SIMS)]
            s = summarize(runs)
            t2 = f"{s['median_t2x_days']:.0f}" if s["median_t2x_days"] else "—"
            t5 = f"{s['median_t5x_days']:.0f}" if s["median_t5x_days"] else "—"
            t10 = f"{s['median_t10x_days']:.0f}" if s["median_t10x_days"] else "—"
            print(
                f"{name:>12} {cap * 100:>4.0f}% {mk:>5.2f} {ms:>5.2f} | "
                f"{s['median_30d']:>7.1f} {s['median_90d']:>7.1f} {s['median_term']:>8.1f} "
                f"{s['p5_term']:>7.1f} {s['p95_term']:>8.1f} | "
                f"{s['pct_doubled']:>5.0f}% {t2:>7} {s['pct_5xed']:>5.0f}% {t5:>7} "
                f"{s['pct_10xed']:>6.0f}% {t10:>8} | "
                f"{s['bust_pct']:>5.1f}%"
            )


if __name__ == "__main__":
    main()
