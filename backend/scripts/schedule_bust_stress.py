"""
Stress sweep on the LIVE production stake schedule.

Imports dynamic_cap_pct + dynamic_max_kelly from src.bankroll.stake_calculator,
so this tests the exact code that's running in the container — not a copy.

Sweeps:
- Bankroll tiers: 250, 500, 1000, 2500, 5000, 10000
- Edge realization: 1.0× (optimistic) / 0.7× (realistic) / 0.4× (pessimistic) / 0.2× (worst case)
- Horizon: 180d / 365d / 730d (6mo / 1yr / 2yr)

Bust = bankroll falls below min-stake floor. Reports bust%, median growth,
and 5th percentile terminal so we can see the tail.

Run: cd backend && python scripts/schedule_bust_stress.py
"""

import io
import random
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.bankroll.stake_calculator import dynamic_cap_pct, dynamic_max_kelly

# Calibrated live value-bet edge distribution (matches dynamic_stake_sim)
VALUE_EDGE_DIST = [
    (1.0, 2.0, 0.31, 3.53),
    (2.0, 4.0, 0.37, 3.52),
    (4.0, 7.0, 0.16, 3.63),
    (7.0, 12.0, 0.08, 4.52),
    (12.0, 25.0, 0.08, 6.50),
]

START_TIERS = [250, 500, 1000, 2500, 5000, 10000]
EDGE_MULTS = [
    ("optimistic 1.0×", 1.0),
    ("realistic 0.7×", 0.7),
    ("pessimistic 0.4×", 0.4),
    ("worst-case 0.2×", 0.2),
]
HORIZONS = [
    ("6mo", 180),
    ("1yr", 365),
    ("2yr", 730),
]
BETS_PER_DAY = 10
NUM_SIMS = 1000

MIN_STAKE_PCT = 0.005
MIN_STAKE_FLOOR = 0.25


def kelly_fraction(edge_pct, max_kelly):
    if edge_pct <= 2.0:
        return 0.25
    if edge_pct >= 6.0:
        return max_kelly
    t = (edge_pct - 2.0) / 4.0
    return 0.25 + t * (max_kelly - 0.25)


def compute_stake(bankroll, edge_pct, odds, min_stake):
    if bankroll < min_stake:
        return 0.0
    cap_pct = dynamic_cap_pct(bankroll)
    max_k = dynamic_max_kelly(bankroll)
    k = kelly_fraction(edge_pct, max_k)
    raw = bankroll * k * (edge_pct / 100) / (odds - 1)
    cap = bankroll * cap_pct
    stake = min(raw, cap)
    if stake < min_stake:
        stake = min_stake
    return min(stake, bankroll)


def sample_bet(rng):
    r = rng.random()
    cum = 0.0
    for emin, emax, w, odds in VALUE_EDGE_DIST:
        cum += w
        if r <= cum:
            return rng.uniform(emin, emax), odds
    return 5.0, 3.0


def simulate_one(start, edge_mult, total_bets, seed):
    rng = random.Random(seed)
    bankroll = start
    bust = False
    for _ in range(total_bets):
        edge_disp, odds = sample_bet(rng)
        edge_true = edge_disp * edge_mult / 100
        p_win = min(0.99, max(0.01, (1.0 / odds) * (1.0 + edge_true)))
        min_stake = max(MIN_STAKE_FLOOR, bankroll * MIN_STAKE_PCT)
        stake = compute_stake(bankroll, edge_disp, odds, min_stake)
        if stake <= 0:
            bust = True
            break
        if rng.random() < p_win:
            bankroll += stake * (odds - 1)
        else:
            bankroll -= stake
        if bankroll <= min_stake:
            bust = True
            break
    return bankroll, bust


def median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0


def pct(xs, p):
    s = sorted(xs)
    return s[max(0, int(p * len(s)) - 1)] if s else 0


def main():
    print(f"Stress sweep on LIVE schedule (production stake_calculator) — {NUM_SIMS} runs/cell")
    print("Bust = bankroll falls below min-stake floor.\n")

    for label_h, days in HORIZONS:
        total_bets = days * BETS_PER_DAY
        print(f"\n{'=' * 130}")
        print(f"HORIZON: {label_h} ({days} days, {total_bets:,} bets)")
        print(f"{'=' * 130}")
        print(f"{'start$':>7} | " + " | ".join(f"{label:^25}" for label, _ in EDGE_MULTS))
        print(f"{'':>7} | " + " | ".join(f"{'bust%':>5} {'medX':>5} {'P5':>6} {'P1':>6}" for _ in EDGE_MULTS))
        print("-" * 130)
        for start in START_TIERS:
            cells = []
            for _, em in EDGE_MULTS:
                results = [simulate_one(start, em, total_bets, seed=s) for s in range(NUM_SIMS)]
                terms = [r[0] for r in results]
                busts = sum(1 for r in results if r[1])
                bust_pct = 100 * busts / NUM_SIMS
                med = median(terms)
                p5 = pct(terms, 0.05)
                p1 = pct(terms, 0.01)
                cells.append((bust_pct, med / start, p5, p1))
            row = f"{start:>7} | "
            row += " | ".join(f"{c[0]:>4.1f}% {c[1]:>4.2f}× {c[2]:>6.0f} {c[3]:>6.0f}" for c in cells)
            print(row)

    # Flag-only summary at the end
    print(f"\n{'=' * 130}")
    print("BUST-RISK SUMMARY (only flags cells with bust > 1%)")
    print(f"{'=' * 130}")
    flagged = []
    for label_h, days in HORIZONS:
        total_bets = days * BETS_PER_DAY
        for start in START_TIERS:
            for label_e, em in EDGE_MULTS:
                results = [simulate_one(start, em, total_bets, seed=s) for s in range(NUM_SIMS)]
                busts = sum(1 for r in results if r[1])
                bust_pct = 100 * busts / NUM_SIMS
                if bust_pct > 1.0:
                    flagged.append((label_h, start, label_e, bust_pct))
    if not flagged:
        print("✓ No cell crosses 1% bust risk in any combination.")
        print("  Full-send is safe across all sim'd horizons and edge realizations.")
    else:
        print(f"⚠ {len(flagged)} cell(s) above 1% bust:")
        for h, s, e, b in flagged:
            print(f"   {h:>4} @ ${s:>5} under {e:>20}: {b:.1f}% bust")


if __name__ == "__main__":
    main()
