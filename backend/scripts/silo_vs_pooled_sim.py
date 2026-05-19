"""
Pooled bankroll vs per-provider bankroll: what does the silo cost us?

Compares three stake-sizing regimes over the SAME bet sequence + outcomes:

  POOLED  — stake = f(total bankroll across all providers).
            Assumes free transfer between books. Upper bound: what we could
            do with zero friction.

  SILO    — stake = f(that provider's balance only).
            No transfers, ever. Busted softs skip bets. Lower bound.

  HYBRID  — unlimited bucket (Pinnacle/Cloudbet/Kalshi/Polymarket) treated
            as one pooled balance. Softs are siloed for stake sizing, but
            any profit above their starting balance is continuously arbed
            out to the unlimited pool. This is the regime we actually run:
            softs are feeders, the unlimited pool is where compounding
            happens.

Realistic Pinnacle-heavy split (40% unlimited / 4×15% softs). Same calibrated
value-bet edge distribution + the dynamic cap/maxK schedule from the existing
dynamic_stake_sim.out work.

Run: cd backend && python scripts/silo_vs_pooled_sim.py
"""

import io
import random
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# (edge_min%, edge_max%, weight, avg_odds) — calibrated, matches dynamic_stake_sim
VALUE_EDGE_DIST = [
    (1.0, 2.0, 0.31, 3.53),
    (2.0, 4.0, 0.37, 3.52),
    (4.0, 7.0, 0.16, 3.63),
    (7.0, 12.0, 0.08, 4.52),
    (12.0, 25.0, 0.08, 6.50),
]

EDGE_MULT = 0.7
BETS_PER_DAY = 10
DAYS = 180
TOTAL_BETS = BETS_PER_DAY * DAYS
NUM_SIMS = 500

START_TIERS = [250, 500, 1000, 2500, 5000]

# Realistic skew: 1 unlimited bucket (Pinnacle/Cloudbet/Kalshi/Polymarket grouped)
# holding most cash, 4 soft books on equal small slices.
PROVIDERS = ["unlimited", "soft_1", "soft_2", "soft_3", "soft_4"]
# Starting balance share per provider
BALANCE_SHARE = {
    "unlimited": 0.40,
    "soft_1": 0.15,
    "soft_2": 0.15,
    "soft_3": 0.15,
    "soft_4": 0.15,
}
# Bet routing weights — ~30% of value lands at the unlimited bucket,
# 70% spread across the 4 soft books.
BET_WEIGHTS = {
    "unlimited": 0.30,
    "soft_1": 0.175,
    "soft_2": 0.175,
    "soft_3": 0.175,
    "soft_4": 0.175,
}


def dynamic_cap_pct(b):
    if b < 250:
        return 0.100
    if b < 500:
        return 0.050
    if b < 1000:
        return 0.080
    if b < 2500:
        return 0.150
    if b < 5000:
        return 0.150
    if b < 10000:
        return 0.100
    if b < 20000:
        return 0.080
    return 0.100


def dynamic_max_kelly(b):
    if b < 250:
        return 0.50
    if b < 500:
        return 0.75
    if b < 1000:
        return 0.75
    if b < 2500:
        return 1.00
    return 0.75


MIN_STAKE_PCT = 0.005  # 0.5% of bankroll, with a $0.25 floor
MIN_STAKE_FLOOR = 0.25


def kelly_fraction(edge_pct, max_kelly):
    if edge_pct <= 2.0:
        return 0.25
    if edge_pct >= 6.0:
        return max_kelly
    t = (edge_pct - 2.0) / 4.0
    return 0.25 + t * (max_kelly - 0.25)


def compute_stake(bankroll, edge_pct, odds, cap_pct, max_kelly, min_stake):
    if bankroll < min_stake:
        return 0.0
    k = kelly_fraction(edge_pct, max_kelly)
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


def pick_provider(rng):
    r = rng.random()
    cum = 0.0
    for name in PROVIDERS:
        cum += BET_WEIGHTS[name]
        if r <= cum:
            return name
    return PROVIDERS[-1]


def initial_balances(total):
    return {p: total * BALANCE_SHARE[p] for p in PROVIDERS}


def simulate_pair(start, seed):
    """Run all three regimes on the same RNG-determined bet sequence + outcomes.

    Paired draws mean every bet has the same edge, odds, provider, and win/loss
    in all regimes. The only difference is how the bankroll is sized + organized.
    """
    rng = random.Random(seed)
    initial = initial_balances(start)

    pool_total = start

    silo_bal = dict(initial)
    silo_busted_at = {p: False for p in PROVIDERS}
    silo_skipped = 0
    silo_skipped_by = {p: 0 for p in PROVIDERS}

    # Hybrid: unlimited starts at its share; softs at their share; arb drain on each soft profit
    hyb_unlimited = initial["unlimited"]
    hyb_soft_bal = {p: initial[p] for p in PROVIDERS if p != "unlimited"}
    hyb_soft_start = dict(hyb_soft_bal)
    hyb_soft_busted = {p: False for p in hyb_soft_bal}
    hyb_skipped = 0
    hyb_drained = 0.0  # cumulative $ moved soft → unlimited

    pool_2x = silo_2x = hyb_2x = None
    target = start * 2

    for i in range(TOTAL_BETS):
        day = i // BETS_PER_DAY + 1
        edge_disp, odds = sample_bet(rng)
        edge_true = edge_disp * EDGE_MULT / 100
        p_win = min(0.99, max(0.01, (1.0 / odds) * (1.0 + edge_true)))
        provider = pick_provider(rng)
        u = rng.random()
        won = u < p_win

        # POOLED
        cap = dynamic_cap_pct(pool_total)
        mk = dynamic_max_kelly(pool_total)
        min_abs = max(MIN_STAKE_FLOOR, pool_total * MIN_STAKE_PCT)
        stake = compute_stake(pool_total, edge_disp, odds, cap, mk, min_abs)
        if stake > 0:
            if won:
                pool_total += stake * (odds - 1)
            else:
                pool_total -= stake

        # SILO
        bal = silo_bal[provider]
        if silo_busted_at[provider]:
            silo_skipped += 1
            silo_skipped_by[provider] += 1
        else:
            cap_s = dynamic_cap_pct(bal)
            mk_s = dynamic_max_kelly(bal)
            min_s = max(MIN_STAKE_FLOOR, bal * MIN_STAKE_PCT)
            stake_s = compute_stake(bal, edge_disp, odds, cap_s, mk_s, min_s)
            if stake_s <= 0:
                silo_busted_at[provider] = True
                silo_skipped += 1
                silo_skipped_by[provider] += 1
            else:
                if won:
                    silo_bal[provider] += stake_s * (odds - 1)
                else:
                    silo_bal[provider] -= stake_s
                if silo_bal[provider] <= min_s:
                    silo_busted_at[provider] = True

        # HYBRID — unlimited pooled, softs siloed with continuous arb drain
        if provider == "unlimited":
            cap_h = dynamic_cap_pct(hyb_unlimited)
            mk_h = dynamic_max_kelly(hyb_unlimited)
            min_h = max(MIN_STAKE_FLOOR, hyb_unlimited * MIN_STAKE_PCT)
            stake_h = compute_stake(hyb_unlimited, edge_disp, odds, cap_h, mk_h, min_h)
            if stake_h > 0:
                if won:
                    hyb_unlimited += stake_h * (odds - 1)
                else:
                    hyb_unlimited -= stake_h
        else:
            sbal = hyb_soft_bal[provider]
            if hyb_soft_busted[provider]:
                hyb_skipped += 1
            else:
                cap_s = dynamic_cap_pct(sbal)
                mk_s = dynamic_max_kelly(sbal)
                min_s = max(MIN_STAKE_FLOOR, sbal * MIN_STAKE_PCT)
                stake_s = compute_stake(sbal, edge_disp, odds, cap_s, mk_s, min_s)
                if stake_s <= 0:
                    hyb_soft_busted[provider] = True
                    hyb_skipped += 1
                else:
                    if won:
                        hyb_soft_bal[provider] += stake_s * (odds - 1)
                    else:
                        hyb_soft_bal[provider] -= stake_s
                    # Continuous arb drain: any balance above starting flows to unlimited
                    excess = hyb_soft_bal[provider] - hyb_soft_start[provider]
                    if excess > 0:
                        hyb_soft_bal[provider] -= excess
                        hyb_unlimited += excess
                        hyb_drained += excess

        silo_total_now = sum(silo_bal.values())
        hyb_total_now = hyb_unlimited + sum(hyb_soft_bal.values())
        if pool_2x is None and pool_total >= target:
            pool_2x = day
        if silo_2x is None and silo_total_now >= target:
            silo_2x = day
        if hyb_2x is None and hyb_total_now >= target:
            hyb_2x = day

    return {
        "pool_term": pool_total,
        "pool_2x": pool_2x,
        "silo_terms": dict(silo_bal),
        "silo_total": sum(silo_bal.values()),
        "silo_busts": sum(1 for v in silo_busted_at.values() if v),
        "silo_skipped": silo_skipped,
        "silo_skipped_by": dict(silo_skipped_by),
        "silo_2x": silo_2x,
        "hyb_unlimited": hyb_unlimited,
        "hyb_soft_bal": dict(hyb_soft_bal),
        "hyb_total": hyb_unlimited + sum(hyb_soft_bal.values()),
        "hyb_busts": sum(1 for v in hyb_soft_busted.values() if v),
        "hyb_skipped": hyb_skipped,
        "hyb_drained": hyb_drained,
        "hyb_2x": hyb_2x,
    }


def median(xs):
    if not xs:
        return 0
    s = sorted(xs)
    return s[len(s) // 2]


def pct(xs, p):
    if not xs:
        return 0
    s = sorted(xs)
    return s[max(0, int(p * len(s)) - 1)]


def summarize_tier(start):
    runs = [simulate_pair(start, seed=s) for s in range(NUM_SIMS)]
    pool_terms = [r["pool_term"] for r in runs]
    silo_totals = [r["silo_total"] for r in runs]
    hyb_totals = [r["hyb_total"] for r in runs]
    pool_2xs = [r["pool_2x"] for r in runs if r["pool_2x"] is not None]
    silo_2xs = [r["silo_2x"] for r in runs if r["silo_2x"] is not None]
    hyb_2xs = [r["hyb_2x"] for r in runs if r["hyb_2x"] is not None]
    silo_full_busts = sum(1 for r in runs if r["silo_busts"] == len(PROVIDERS))
    skips = [r["silo_skipped"] for r in runs]
    hyb_skips = [r["hyb_skipped"] for r in runs]
    silo_bust_counts = [r["silo_busts"] for r in runs]
    hyb_drained = [r["hyb_drained"] for r in runs]
    hyb_unlimited_terms = [r["hyb_unlimited"] for r in runs]
    per_prov_med = {p: median([r["silo_terms"][p] for r in runs]) for p in PROVIDERS}
    per_prov_skip = {p: median([r["silo_skipped_by"][p] for r in runs]) for p in PROVIDERS}
    return {
        "start": start,
        "pool_med": median(pool_terms),
        "pool_p5": pct(pool_terms, 0.05),
        "pool_p95": pct(pool_terms, 0.95),
        "pool_growth": median(pool_terms) / start,
        "pool_2x_med": median(pool_2xs) if pool_2xs else None,
        "pool_2x_pct": 100 * len(pool_2xs) / NUM_SIMS,
        "silo_med": median(silo_totals),
        "silo_p5": pct(silo_totals, 0.05),
        "silo_p95": pct(silo_totals, 0.95),
        "silo_growth": median(silo_totals) / start,
        "silo_2x_med": median(silo_2xs) if silo_2xs else None,
        "silo_2x_pct": 100 * len(silo_2xs) / NUM_SIMS,
        "silo_full_bust_pct": 100 * silo_full_busts / NUM_SIMS,
        "silo_avg_busts": sum(silo_bust_counts) / NUM_SIMS,
        "silo_skips_pct": 100 * median(skips) / TOTAL_BETS,
        "hyb_med": median(hyb_totals),
        "hyb_p5": pct(hyb_totals, 0.05),
        "hyb_p95": pct(hyb_totals, 0.95),
        "hyb_growth": median(hyb_totals) / start,
        "hyb_2x_med": median(hyb_2xs) if hyb_2xs else None,
        "hyb_2x_pct": 100 * len(hyb_2xs) / NUM_SIMS,
        "hyb_drained_med": median(hyb_drained),
        "hyb_unlimited_med": median(hyb_unlimited_terms),
        "hyb_skips_pct": 100 * median(hyb_skips) / TOTAL_BETS,
        "per_prov_med": per_prov_med,
        "per_prov_skip": per_prov_skip,
    }


def main():
    print(f"Pooled / Silo / Hybrid (real workflow) — {BETS_PER_DAY}/day × {DAYS}d × {NUM_SIMS} runs/tier")
    print("Providers: 1 unlimited @ 40% start / 4 softs @ 15% each. Bet routing 30% / 17.5%×4.")
    print("Hybrid = unlimited pooled, softs siloed for sizing, any soft profit above starting is arbed to unlimited.")
    print("Stake schedule: dynamic cap+maxK per current bankroll (matches dynamic_stake_sim).\n")

    print("=" * 140)
    print("THREE REGIMES — median terminal + time-to-2x")
    print("=" * 140)
    print(
        f"{'start$':>7} | {'POOLED (free transfer)':^38} | {'SILO (no transfer)':^46} | {'HYBRID (soft arbs → unlimited)':^46}"
    )
    print(
        f"{'':>7} | {'med':>8} {'×grw':>5} {'2x%':>5} {'2xd':>5} {'P5':>6} |"
        f" {'med':>8} {'×grw':>5} {'2x%':>5} {'2xd':>5} {'P5':>6} {'bust':>5} {'skip%':>5} |"
        f" {'med':>8} {'×grw':>5} {'2x%':>5} {'2xd':>5} {'P5':>6} {'drain':>6}"
    )
    print("-" * 140)

    summaries = []
    for start in START_TIERS:
        s = summarize_tier(start)
        summaries.append(s)
        p2 = f"{s['pool_2x_med']}" if s["pool_2x_med"] else "—"
        s2 = f"{s['silo_2x_med']}" if s["silo_2x_med"] else "—"
        h2 = f"{s['hyb_2x_med']}" if s["hyb_2x_med"] else "—"
        print(
            f"{start:>7} |"
            f" {s['pool_med']:>8.0f} {s['pool_growth']:>4.2f}× {s['pool_2x_pct']:>4.0f}% {p2:>5} {s['pool_p5']:>6.0f} |"
            f" {s['silo_med']:>8.0f} {s['silo_growth']:>4.2f}× {s['silo_2x_pct']:>4.0f}% {s2:>5} {s['silo_p5']:>6.0f}"
            f" {s['silo_full_bust_pct']:>4.1f}% {s['silo_skips_pct']:>4.1f}% |"
            f" {s['hyb_med']:>8.0f} {s['hyb_growth']:>4.2f}× {s['hyb_2x_pct']:>4.0f}% {h2:>5} {s['hyb_p5']:>6.0f}"
            f" {s['hyb_drained_med']:>6.0f}"
        )

    print(f"\n{'=' * 140}")
    print("DELTA — pooled vs hybrid vs silo")
    print("=" * 140)
    print(
        f"{'start$':>7} | {'pool med':>9} {'hyb med':>9} {'silo med':>9} |"
        f" {'hyb / silo':>10} {'pool / hyb':>10} {'pool / silo':>11} |"
        f" {'2xd P/H/S':>13} {'S→H saved':>10}"
    )
    print("-" * 140)
    for s in summaries:
        h_over_s = s["hyb_med"] / s["silo_med"] if s["silo_med"] else 0
        p_over_h = s["pool_med"] / s["hyb_med"] if s["hyb_med"] else 0
        p_over_s = s["pool_med"] / s["silo_med"] if s["silo_med"] else 0
        p2 = s["pool_2x_med"] or "—"
        h2 = s["hyb_2x_med"] or "—"
        s2 = s["silo_2x_med"] or "—"
        days_sh = (s["silo_2x_med"] - s["hyb_2x_med"]) if (s["hyb_2x_med"] and s["silo_2x_med"]) else None
        days_s = f"{days_sh}" if days_sh is not None else "—"
        triple = f"{p2}/{h2}/{s2}"
        print(
            f"{s['start']:>7} | {s['pool_med']:>9.0f} {s['hyb_med']:>9.0f} {s['silo_med']:>9.0f} |"
            f" {h_over_s:>9.2f}× {p_over_h:>9.2f}× {p_over_s:>10.2f}× |"
            f" {triple:>13} {days_s:>10}"
        )

    print(f"\n{'=' * 140}")
    print("HYBRID DETAILS — unlimited terminal + $ arbed in from softs")
    print("=" * 140)
    print(
        f"{'start$':>7} | {'unlim start':>11} {'unlim term':>11} {'unlim ×':>8} |"
        f" {'arbed in $':>10} {'arb / start':>11}"
    )
    print("-" * 140)
    for s in summaries:
        unlim_start = s["start"] * BALANCE_SHARE["unlimited"]
        print(
            f"{s['start']:>7} | {unlim_start:>11.0f} {s['hyb_unlimited_med']:>11.0f}"
            f" {s['hyb_unlimited_med'] / unlim_start:>7.2f}× |"
            f" {s['hyb_drained_med']:>10.0f} {100 * s['hyb_drained_med'] / s['start']:>10.1f}%"
        )

    print(f"\n{'=' * 140}")
    print("READING THIS")
    print("=" * 140)
    print("• POOLED = upper bound. Stakes sized off TOTAL bankroll, zero transfer friction.")
    print("• SILO   = lower bound. Stakes sized off each provider's own balance, no transfers ever.")
    print("• HYBRID = the regime we actually run. Unlimited pooled (Pinnacle/Cloudbet/Kalshi/Polymarket)")
    print("           as one balance, softs siloed for sizing, profit above starting arbs to unlimited.")
    print("• 'drain' = median $ arbed from softs → unlimited over the 180-day run.")
    print("• 'arb / start' = drain expressed as fraction of starting TOTAL bankroll.")
    print("• 'S→H saved' = days hybrid saves vs pure silo on time-to-2x.")


if __name__ == "__main__":
    main()
