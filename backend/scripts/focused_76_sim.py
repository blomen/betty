"""
Focused MC sim @ $76 starting bankroll — with REALISTIC fee/gas modeling.

Models the actual production cost structure:
  - Pinnacle / Cloudbet: 0% fee, vig already netted in edge → no extra friction
  - Polymarket: 0% trading fee, but ~$0.05 Polygon gas per BUY (real bleed)
  - Kalshi: 7% fee already netted in displayed price → no extra friction

Provider mix sampled from live batch (2026-05-17):
  pinnacle 32%, polymarket 64%, kalshi 4%

Adds optional per-provider min_edge filter — useful for polymarket where
sub-5% edge bets bleed gas faster than EV accrues.

Run: cd backend && python scripts/focused_76_sim.py
"""

import io
import random
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Calibrated edge dist from live batch (n=138, 2026-05-17). Per-provider mix:
PROVIDER_MIX = [
    ("pinnacle", 0.32),
    ("polymarket", 0.64),
    ("kalshi", 0.04),
]

VALUE_EDGE_DIST = [
    (1.0, 2.0, 0.31, 3.53),
    (2.0, 4.0, 0.37, 3.52),
    (4.0, 7.0, 0.16, 3.63),
    (7.0, 12.0, 0.08, 4.52),
    (12.0, 25.0, 0.08, 6.50),
]

# Per-provider per-trade gas (USDC equivalent). Pinnacle/Cloudbet/Kalshi: 0
# (fees baked into odds). Polymarket: 1 buy txn on Polygon ≈ $0.03-0.05; using
# $0.05 conservative central estimate. Auto-resolution means no redeem gas in
# most cases — bake another $0.02 buffer for the cases where you do redeem.
PROVIDER_GAS = {
    "pinnacle": 0.0,
    "polymarket": 0.07,  # 0.05 placement + 0.02 amortized redeem
    "kalshi": 0.0,
    "cloudbet": 0.0,
}

# Edge realism multipliers — sim 3 scenarios per profile to stress-test
# unmodeled risks (slippage, adverse selection, fair-odds drift, partial fills)
EDGE_MULT = 0.7  # central case
EDGE_MULT_PESSIMISTIC = 0.4  # heavy slippage + adverse selection
EDGE_MULT_OPTIMISTIC = 0.9  # fast clean fills
BETS_PER_DAY = 10
DAYS = 365
TOTAL_BETS = BETS_PER_DAY * DAYS

START_BANKROLL = 76.0

# Each profile: (label, cap, maxK, min_stake, polymarket_min_edge_pct)
PROFILES = [
    ("CURRENT (2%/0.75, no filter)", 0.02, 0.75, 1.0, 0.0),
    ("cap 5%, poly ≥5%, 0.75K", 0.05, 0.75, 1.0, 5.0),
    ("cap 5%, poly ≥5%, 1.0K", 0.05, 1.00, 1.0, 5.0),
    ("cap 8%, poly ≥5%, 1.0K", 0.08, 1.00, 1.0, 5.0),
    ("cap 10%, poly ≥5%, 1.0K", 0.10, 1.00, 1.0, 5.0),
    ("cap 15%, poly ≥5%, 1.5K", 0.15, 1.50, 1.0, 5.0),
    ("cap 20%, poly ≥5%, 1.5K", 0.20, 1.50, 1.0, 5.0),
    ("cap 5%, poly ≥7%, 1.0K", 0.05, 1.00, 1.0, 7.0),
    ("cap 5%, poly ≥7%, 1.5K", 0.05, 1.50, 1.0, 7.0),
]

NUM_SIMS = 5000


def sample_provider(rng):
    r = rng.random()
    cum = 0.0
    for pid, w in PROVIDER_MIX:
        cum += w
        if r <= cum:
            return pid
    return "polymarket"


def sample_bet(rng):
    r = rng.random()
    cum = 0.0
    for emin, emax, w, odds in VALUE_EDGE_DIST:
        cum += w
        if r <= cum:
            return rng.uniform(emin, emax), odds
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


def percentile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def median(xs):
    return percentile(xs, 50)


def show_stakes(label, cap, mk, min_abs, poly_min_edge):
    print(f"\n{label}  (cap={cap * 100:.0f}%, maxK={mk}, min=${min_abs}, poly_min_edge={poly_min_edge}%)")
    print(f"  Per-bet stake at ${START_BANKROLL:.0f}:")
    print(f"    {'edge':>6}  {'odds':>5}  {'pinnacle':>9}  {'polymarket':>12}  {'kalshi':>8}")
    for edge_pct, odds in [(1.5, 3.5), (3.0, 3.5), (5.0, 3.6), (8.5, 4.5), (15.0, 6.5), (25.0, 7.0)]:
        stake = compute_stake(START_BANKROLL, edge_pct, odds, cap, mk, min_abs)
        poly_stake = stake if edge_pct >= poly_min_edge else 0.0
        poly_str = f"${poly_stake:.2f}" if poly_stake > 0 else "SKIP"
        print(f"    {edge_pct:5.1f}%  {odds:5.2f}  ${stake:>6.2f}    {poly_str:>10}    ${stake:>5.2f}")


def main():
    print(f"Gas-aware MC sim @ ${START_BANKROLL:.0f} bankroll  ({BETS_PER_DAY}/day × {DAYS}d, {NUM_SIMS} sims/profile)")
    print(f"Edge: 0.7× realism. Polymarket gas: ${PROVIDER_GAS['polymarket']:.2f} per trade.")
    print(f"Provider mix: {PROVIDER_MIX}\n")

    print("=" * 110)
    print("STAKE SCHEDULE")
    print("=" * 110)
    for label, cap, mk, min_abs, poly_filt in PROFILES:
        show_stakes(label, cap, mk, min_abs, poly_filt)

    for scenario_label, edge_mult in [
        ("REALISTIC (edge × 0.7)", EDGE_MULT),
        ("PESSIMISTIC (edge × 0.4) — heavy slippage + adverse selection", EDGE_MULT_PESSIMISTIC),
        ("OPTIMISTIC (edge × 0.9) — clean fills", EDGE_MULT_OPTIMISTIC),
    ]:
        print("\n" + "=" * 130)
        print(f"OUTCOMES — {scenario_label}")
        print("=" * 130)
        header = (
            f"{'profile':<34} | {'med 30d':>8} {'med 90d':>8} {'med 180d':>9} {'med term':>9} "
            f"| {'P25 term':>8} {'P75 term':>8} | {'days 2x':>8} {'days 5x':>8} {'days 10x':>9} "
            f"| {'%>2x':>5} {'%>5x':>5} {'%>10x':>5} | {'bust':>5} {'eff/day':>7}"
        )
        print(header)
        print("-" * len(header))

        for label, cap, mk, min_abs, poly_filt in PROFILES:
            terms = []
            snapshots = {30: [], 90: [], 180: []}
            horizons = {2: [], 5: [], 10: []}
            busts = 0
            bets_per_day_eff = []
            for s in range(NUM_SIMS):
                rng = random.Random(s)
                bankroll = START_BANKROLL
                bust = False
                h = {k: None for k in horizons}
                targets = {k: START_BANKROLL * k for k in horizons}
                snaps = {30: None, 90: None, 180: None}
                placed = 0
                for i in range(TOTAL_BETS):
                    day = i // BETS_PER_DAY + 1
                    provider = sample_provider(rng)
                    edge_disp, odds = sample_bet(rng)
                    if provider == "polymarket" and edge_disp < poly_filt:
                        if day == 30 and snaps[30] is None:
                            snaps[30] = bankroll
                        if day == 90 and snaps[90] is None:
                            snaps[90] = bankroll
                        if day == 180 and snaps[180] is None:
                            snaps[180] = bankroll
                        continue
                    edge_true = edge_disp * edge_mult / 100
                    p_win = min(0.99, max(0.01, (1.0 / odds) * (1.0 + edge_true)))
                    stake = compute_stake(bankroll, edge_disp, odds, cap, mk, min_abs)
                    if stake <= 0:
                        bust = True
                        break
                    gas = PROVIDER_GAS.get(provider, 0.0)
                    placed += 1
                    if rng.random() < p_win:
                        bankroll += stake * (odds - 1) - gas
                    else:
                        bankroll -= stake + gas
                    for k in h:
                        if h[k] is None and bankroll >= targets[k]:
                            h[k] = day
                    for d in (30, 90, 180):
                        if day == d and snaps[d] is None:
                            snaps[d] = bankroll
                    if bankroll <= min_abs:
                        bust = True
                        break
                terms.append(bankroll)
                for d in snaps:
                    snapshots[d].append(snaps[d] if snaps[d] is not None else bankroll)
                for k in h:
                    if h[k] is not None:
                        horizons[k].append(h[k])
                if bust:
                    busts += 1
                bets_per_day_eff.append(placed / DAYS)

            m_term = median(terms)
            p25 = percentile(terms, 25)
            p75 = percentile(terms, 75)
            m30 = median(snapshots[30])
            m90 = median(snapshots[90])
            m180 = median(snapshots[180])
            d2 = median(horizons[2]) if horizons[2] else "—"
            d5 = median(horizons[5]) if horizons[5] else "—"
            d10 = median(horizons[10]) if horizons[10] else "—"
            pct2 = 100 * len(horizons[2]) / NUM_SIMS
            pct5 = 100 * len(horizons[5]) / NUM_SIMS
            pct10 = 100 * len(horizons[10]) / NUM_SIMS
            bust_pct = 100 * busts / NUM_SIMS
            eff = sum(bets_per_day_eff) / NUM_SIMS
            print(
                f"{label:<34} | ${m30:>6.0f}  ${m90:>6.0f}   ${m180:>7.0f}  ${m_term:>7.0f} "
                f"|  ${p25:>6.0f}  ${p75:>6.0f} | {d2!s:>8} {d5!s:>8} {d10!s:>9} "
                f"| {pct2:>4.0f}% {pct5:>4.0f}% {pct10:>4.0f}% | {bust_pct:>4.1f}%  {eff:>5.1f}"
            )


if __name__ == "__main__":
    main()
