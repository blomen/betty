"""
How much should I seed each soft book so I rarely have to transfer?

Operational model: stakes are sized off the TOTAL bankroll (pooled). PnL on
each bet hits whichever soft was used. We periodically arb excess soft balance
to unlimited. The question is the minimum starting seed per soft such that
the soft doesn't run out of cash to cover a stake (a 'refill event') too
often over 180 days.

For each starting total bankroll and seed-% per soft, reports:
- pct of runs with ≥1 refill event at any soft
- median day of first refill event
- median refills per soft over 180 days
- median peak balance at each soft (so you know how much accumulates before arbing)

Run: cd backend && python scripts/soft_seeding_sim.py
"""

import io
import random
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Calibrated value-bet edge distribution (matches dynamic_stake_sim)
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

# 4 soft books, each gets ~17.5% of bets. Unlimited gets the rest.
SOFT_PROVIDERS = ["soft_1", "soft_2", "soft_3", "soft_4"]
BET_WEIGHTS = {
    "unlimited": 0.30,
    "soft_1": 0.175,
    "soft_2": 0.175,
    "soft_3": 0.175,
    "soft_4": 0.175,
}

# Seed levels to test — as % of total starting bankroll, allocated to EACH soft
SEED_PCT_LEVELS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]


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


MIN_STAKE_PCT = 0.005
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
    for name, w in BET_WEIGHTS.items():
        cum += w
        if r <= cum:
            return name
    return "soft_4"


# Operational rules for soft bookkeeping
ARB_TRIGGER = 2.0  # when soft balance > ARB_TRIGGER × target, sweep down to target
REFILL_TRIGGER = 0.5  # when soft balance < REFILL_TRIGGER × target, top up to target


def simulate_one(start_total, seed_pct, seed_rng):
    """Pooled stake sizing with realistic per-soft bookkeeping.

    Each soft has a 'target' = seed_pct × current pool. Whenever soft balance
    crosses the arb-out or refill-in trigger, count it as a transfer event.
    Target scales with pool growth so we don't artificially over/under-seed
    as the bankroll compounds.
    """
    rng = random.Random(seed_rng)
    pool_total = start_total
    soft_bal = {p: start_total * seed_pct for p in SOFT_PROVIDERS}
    soft_arbs = {p: 0 for p in SOFT_PROVIDERS}
    soft_refills = {p: 0 for p in SOFT_PROVIDERS}
    soft_arb_total = {p: 0.0 for p in SOFT_PROVIDERS}
    soft_refill_total = {p: 0.0 for p in SOFT_PROVIDERS}

    for i in range(TOTAL_BETS):
        edge_disp, odds = sample_bet(rng)
        edge_true = edge_disp * EDGE_MULT / 100
        p_win = min(0.99, max(0.01, (1.0 / odds) * (1.0 + edge_true)))
        provider = pick_provider(rng)
        won = rng.random() < p_win

        # Pool sizing — independent of which provider gets the bet
        cap = dynamic_cap_pct(pool_total)
        mk = dynamic_max_kelly(pool_total)
        min_abs = max(MIN_STAKE_FLOOR, pool_total * MIN_STAKE_PCT)
        stake = compute_stake(pool_total, edge_disp, odds, cap, mk, min_abs)
        if stake <= 0:
            continue

        if won:
            pool_total += stake * (odds - 1)
        else:
            pool_total -= stake

        if provider in SOFT_PROVIDERS:
            target = pool_total * seed_pct

            # Force-refill if the soft can't cover the stake at all
            if soft_bal[provider] < stake:
                amt = target - soft_bal[provider]
                if amt < stake:
                    amt = stake - soft_bal[provider] + target * 0.5
                soft_refills[provider] += 1
                soft_refill_total[provider] += amt
                soft_bal[provider] += amt

            if won:
                soft_bal[provider] += stake * (odds - 1)
            else:
                soft_bal[provider] -= stake

            # Periodic rebalance: arb-out or top-up around target
            if soft_bal[provider] > target * ARB_TRIGGER:
                excess = soft_bal[provider] - target
                soft_arbs[provider] += 1
                soft_arb_total[provider] += excess
                soft_bal[provider] = target
            elif soft_bal[provider] < target * REFILL_TRIGGER and soft_bal[provider] < target:
                amt = target - soft_bal[provider]
                soft_refills[provider] += 1
                soft_refill_total[provider] += amt
                soft_bal[provider] = target

    total_arbs = sum(soft_arbs.values())
    total_refills = sum(soft_refills.values())
    return {
        "pool_term": pool_total,
        "arbs": total_arbs,
        "refills": total_refills,
        "transfers": total_arbs + total_refills,
        "arb_dollars": sum(soft_arb_total.values()),
        "refill_dollars": sum(soft_refill_total.values()),
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


def summarize(start_total, seed_pct):
    runs = [simulate_one(start_total, seed_pct, seed_rng=s) for s in range(NUM_SIMS)]
    transfers = [r["transfers"] for r in runs]
    arbs = [r["arbs"] for r in runs]
    refills = [r["refills"] for r in runs]
    arb_dollars = [r["arb_dollars"] for r in runs]
    refill_dollars = [r["refill_dollars"] for r in runs]
    pool_terms = [r["pool_term"] for r in runs]
    # Capital allocation snapshot
    seed_per_soft = start_total * seed_pct
    softs_total_seed = seed_per_soft * len(SOFT_PROVIDERS)
    unlimited_seed = start_total - softs_total_seed
    return {
        "start_total": start_total,
        "seed_pct": seed_pct,
        "seed_per_soft": seed_per_soft,
        "softs_total_seed": softs_total_seed,
        "unlimited_seed": unlimited_seed,
        "med_transfers": median(transfers),
        "med_arbs": median(arbs),
        "med_refills": median(refills),
        "transfers_per_month": median(transfers) / (DAYS / 30),
        "med_arb_dollars": median(arb_dollars),
        "med_refill_dollars": median(refill_dollars),
        "med_pool_term": median(pool_terms),
        "growth": median(pool_terms) / start_total,
    }


def main():
    print(f"Soft seeding — pooled stake calc, {BETS_PER_DAY}/day × {DAYS}d × {NUM_SIMS} runs/cell")
    print("4 softs each get ~17.5% of bets, target = seed_pct × current pool, scales as pool grows.")
    print(f"Arb-out trigger: balance > {ARB_TRIGGER:.1f}× target → sweep excess to unlimited.")
    print(f"Refill trigger:  balance < {REFILL_TRIGGER:.1f}× target → top up from unlimited.\n")

    for start in START_TIERS:
        unlimited_share = 1 - 4 * 0  # placeholder
        print(f"\n{'=' * 120}")
        print(f"START TOTAL = ${start}")
        print(f"{'=' * 120}")
        print(
            f"{'seed%':>5} {'seed/soft':>10} {'softs all':>10} {'unlim':>7} | "
            f"{'transfers':>9} {'/month':>7} {'arbs':>5} {'refills':>7} | "
            f"{'$ arbed':>9} {'$ refilled':>10} | {'growth':>6}"
        )
        print("-" * 120)
        for seed_pct in SEED_PCT_LEVELS:
            # Skip impossible (would need more cash than total bankroll)
            if seed_pct * 4 >= 1.0:
                continue
            s = summarize(start, seed_pct)
            print(
                f"{seed_pct * 100:>4.0f}% ${s['seed_per_soft']:>8.0f} ${s['softs_total_seed']:>8.0f} ${s['unlimited_seed']:>5.0f} | "
                f"{s['med_transfers']:>9.0f} {s['transfers_per_month']:>6.1f}/mo "
                f"{s['med_arbs']:>5.0f} {s['med_refills']:>7.0f} | "
                f"${s['med_arb_dollars']:>8.0f} ${s['med_refill_dollars']:>9.0f} | {s['growth']:>5.2f}×"
            )

    print(f"\n{'=' * 120}")
    print("READING THIS")
    print(f"{'=' * 120}")
    print("• 'seed/soft'    = cash at EACH of 4 softs at the start; sum of 4 + 'unlim' = total bankroll.")
    print("• 'transfers'    = total bookkeeping events (arb-out + refill-in) over 180 days.")
    print("• '/month'       = transfers expressed as a monthly cadence — the convenience cost.")
    print("• '$ arbed'      = total dollars swept from softs → unlimited over 180 days.")
    print("• '$ refilled'   = total dollars sent from unlimited → softs to keep bets coverable.")
    print("• 'growth'       = pool terminal / start. Same across seed levels (sizing is pool-based).")
    print("\nSweet spot: smallest seed% where transfers/month feels acceptable AND no row above it")
    print("shows a 10× jump in transfer count. Larger seed = fewer transfers but more cash exposed at softs.")


if __name__ == "__main__":
    main()
