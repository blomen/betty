"""
Full Bankroll Sweep — Fresh Account (freebets + bonuses + snowball)
===================================================================
Two sections:
  A) Pure value betting across ALL bankrolls (500 → 200k) — isolates Kelly
  B) Fresh account: freebets → deposit bonuses → snowball — finds optimal start

Uses the "Aggro low-BR" config (best from prior sweep) vs current production.

Run: python scripts/kelly_full_sweep.py
"""

import io
import random
import sys
from dataclasses import dataclass

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Edge distributions (from live data) ──
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

# Freebets (provider, freebet_amount, trigger_bet_amount)
FREEBETS = [
    ("unibet", 1000, 1000),
    ("betmgm", 500, 500),
    ("dbet", 500, 500),
    ("mrgreen", 500, 500),
    ("hajper", 500, 500),
    ("betsson", 250, 250),
    ("betsafe", 100, 100),
    ("nordicbet", 100, 100),
    ("lyllo", 100, 100),
]

# Deposit bonuses (provider, bonus_amount, wagering_multiplier, min_odds)
DEPOSIT_BONUSES = [
    ("888sport", 500, 1, 1.80),
    ("leovegas", 600, 6, 1.80),
    ("betinia", 1000, 6, 1.80),
    ("swiper", 1000, 6, 1.50),
    ("lodur", 1000, 6, 1.80),
    ("coolbet", 1000, 6, 1.50),
    ("campobet", 500, 6, 1.80),
    ("quickcasino", 500, 6, 1.80),
    ("comeon", 500, 6, 1.80),
    ("tipwin", 1000, 7, 1.80),
    ("10bet", 1000, 8, 1.80),
    ("snabbare", 600, 8, 1.80),
    ("vbet", 800, 10, 1.80),
    ("speedybet", 500, 12, 1.80),
    ("x3000", 500, 12, 1.80),
    ("goldenbull", 500, 12, 1.80),
    ("1x2", 500, 12, 1.80),
    ("spelklubben", 500, 15, 1.90),
    ("bethard", 500, 15, 1.90),
    ("expekt", 1000, 20, 1.80),
]

TOTAL_FREEBET_VALUE = sum(f[1] for f in FREEBETS)
TOTAL_TRIGGER_CAPITAL = sum(f[2] for f in FREEBETS)
TOTAL_DEPOSIT_BONUS = sum(d[1] for d in DEPOSIT_BONUSES)

NUM_SIMS = 5000
WEEKS = 52
BETS_PER_WEEK = 35


def fprint(*args, **kwargs):
    """Print with immediate flush."""
    print(*args, **kwargs)
    sys.stdout.flush()


# ── Sampling ──


def sample_bet(min_odds: float = 1.10) -> tuple[float, float]:
    """Sample (edge_pct, odds) from all streams, respecting min_odds."""
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


def _sample_from(dist) -> tuple[float, float]:
    r = random.random()
    cumulative = 0.0
    for min_e, max_e, weight, avg_odds in dist:
        cumulative += weight
        if r <= cumulative:
            edge = random.uniform(min_e, max_e)
            odds = max(1.15, avg_odds * random.uniform(0.7, 1.3))
            return edge, odds
    return 4.0, 2.50


def simulate_bet(stake: float, edge_pct: float, odds: float) -> float:
    fair_odds = odds / (1.0 + edge_pct / 100.0)
    win_prob = 1.0 / fair_odds
    return stake * (odds - 1.0) if random.random() < win_prob else -stake


# ── Kelly configs ──


@dataclass
class KellyConfig:
    name: str
    min_kelly: float
    max_kelly: float
    edge_low: float
    edge_high: float
    single_bet_cap: float
    boost_factor: float
    boost_threshold: float
    boost_taper: float


CURRENT = KellyConfig(
    name="CURRENT",
    min_kelly=0.25,
    max_kelly=0.75,
    edge_low=2.0,
    edge_high=6.0,
    single_bet_cap=0.03,
    boost_factor=1.333,
    boost_threshold=5000,
    boost_taper=15000,
)

PROPOSED = KellyConfig(
    name="PROPOSED (1.5x boost, 4% cap, taper 10k)",
    min_kelly=0.25,
    max_kelly=0.75,
    edge_low=2.0,
    edge_high=6.0,
    single_bet_cap=0.04,
    boost_factor=1.5,
    boost_threshold=5000,
    boost_taper=10000,
)


def dynamic_min_stake(bankroll: float) -> float:
    if bankroll <= 0:
        return 25.0
    raw = max(5.0, bankroll * 0.005)
    capped = min(raw, 25.0)
    return max(5.0, (capped // 5) * 5)


def calc_stake(bankroll: float, edge_pct: float, odds: float, cfg: KellyConfig) -> float:
    if bankroll <= 0 or edge_pct < 1.0 or odds < 1.15:
        return 0.0

    edge = edge_pct / 100.0

    # Kelly fraction
    if edge_pct <= cfg.edge_low:
        kelly = cfg.min_kelly
    elif edge_pct >= cfg.edge_high:
        kelly = cfg.max_kelly
    else:
        t = (edge_pct - cfg.edge_low) / (cfg.edge_high - cfg.edge_low)
        kelly = cfg.min_kelly + t * (cfg.max_kelly - cfg.min_kelly)

    # Boost
    if cfg.boost_factor > 1.0 and cfg.boost_threshold > 0:
        if bankroll <= cfg.boost_threshold:
            kelly *= cfg.boost_factor
        elif bankroll < cfg.boost_taper:
            t = (bankroll - cfg.boost_threshold) / (cfg.boost_taper - cfg.boost_threshold)
            kelly *= cfg.boost_factor - t * (cfg.boost_factor - 1.0)

    raw = bankroll * kelly * edge / (odds - 1.0)

    # Dynamic bet cap: use cfg cap at low BR, converge to 3% at high BR
    if cfg.single_bet_cap > 0.03 and cfg.boost_threshold > 0:
        if bankroll <= cfg.boost_threshold:
            cap_pct = cfg.single_bet_cap
        elif bankroll < cfg.boost_taper:
            t = (bankroll - cfg.boost_threshold) / (cfg.boost_taper - cfg.boost_threshold)
            cap_pct = cfg.single_bet_cap - t * (cfg.single_bet_cap - 0.03)
        else:
            cap_pct = 0.03
    else:
        cap_pct = cfg.single_bet_cap

    capped = min(raw, bankroll * cap_pct)
    min_s = dynamic_min_stake(bankroll)
    if capped < min_s:
        return 0.0

    # Round natural
    if capped < 50:
        return max(5.0, round(capped / 5) * 5)
    elif capped < 200:
        return round(capped / 10) * 10
    elif capped < 500:
        return round(capped / 25) * 25
    else:
        return round(capped / 50) * 50


# ── Simulation result ──


@dataclass
class SimResult:
    final: float = 0.0
    peak: float = 0.0
    trough: float = 0.0
    total_bets: int = 0
    skipped: int = 0
    ruin: bool = False
    bonus_profit: float = 0.0
    betting_profit: float = 0.0
    bonuses_unlocked: int = 0
    freebets_claimed: int = 0
    weeks_used: int = 0


# ── Phase simulators ──


def simulate_freebet_phase(bankroll: float, cfg: KellyConfig) -> tuple[float, float, float, int, int, int]:
    """Returns: (bankroll, bonus_profit, betting_profit, bets, freebets_claimed, skipped)"""
    bonus_profit = 0.0
    betting_profit = 0.0
    bets = 0
    claimed = 0
    skipped = 0

    for _, fb_amount, trigger_amount in FREEBETS:
        if bankroll < trigger_amount:
            skipped += 1
            continue

        # Trigger bet (static amount on +EV selection)
        edge_pct, odds = sample_bet(min_odds=1.80)
        result = simulate_bet(trigger_amount, edge_pct, odds)
        bankroll += result
        betting_profit += result
        bets += 1

        # Freebet (SNR)
        fb_edge, fb_odds = sample_bet(min_odds=1.80)
        fair_odds = fb_odds / (1.0 + fb_edge / 100.0)
        win_prob = 1.0 / fair_odds
        if random.random() < win_prob:
            fb_win = fb_amount * (fb_odds - 1.0)
            bankroll += fb_win
            bonus_profit += fb_win

        bets += 1
        claimed += 1

    return bankroll, bonus_profit, betting_profit, bets, claimed, skipped


def simulate_deposit_bonus(
    bankroll: float,
    bonus_amount: float,
    wagering_mult: float,
    min_odds: float,
    cfg: KellyConfig,
) -> tuple[float, float, float, int, float, int]:
    """Returns: (bankroll, bonus_profit, betting_profit, bets, wagered, weeks)"""
    wagering_target = bonus_amount * wagering_mult
    effective = bankroll + bonus_amount
    wagered = 0.0
    betting_pnl = 0.0
    bets = 0

    while wagered < wagering_target:
        if effective < 5.0:
            return effective, -bonus_amount, betting_pnl, bets, wagered, max(1, bets // max(BETS_PER_WEEK, 1))

        edge_pct, odds = sample_bet(min_odds=min_odds)
        stake = calc_stake(effective, edge_pct, odds, cfg)
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

    weeks = max(1, bets // max(BETS_PER_WEEK, 1))
    return effective, bonus_amount, betting_pnl, bets, wagered, weeks


def simulate_value_phase(bankroll: float, weeks: int, cfg: KellyConfig) -> tuple[float, float, int, int, float, float]:
    """Returns: (bankroll, profit, bets, skipped, peak, trough)"""
    profit = 0.0
    bets = 0
    skipped = 0
    peak = bankroll
    trough = bankroll

    for _ in range(weeks):
        n = BETS_PER_WEEK + random.randint(-3, 3)
        for _ in range(max(1, n)):
            if bankroll < 5.0:
                return bankroll, profit, bets, skipped, peak, trough

            edge_pct, odds = sample_bet()
            stake = calc_stake(bankroll, edge_pct, odds, cfg)
            if stake <= 0:
                skipped += 1
                continue

            result = simulate_bet(stake, edge_pct, odds)
            bankroll += result
            profit += result
            bets += 1
            peak = max(peak, bankroll)
            trough = min(trough, bankroll)

    return bankroll, profit, bets, skipped, peak, trough


# ── Full fresh-account sim ──


def run_full_sim(start: float, cfg: KellyConfig, do_freebets: bool = True, do_bonuses: bool = True) -> SimResult:
    bankroll = start
    res = SimResult()
    res.peak = bankroll
    res.trough = bankroll
    weeks_used = 0

    # Phase 1: Freebets
    if do_freebets:
        bankroll, bp, betp, bets, claimed, sk = simulate_freebet_phase(bankroll, cfg)
        res.bonus_profit += bp
        res.betting_profit += betp
        res.total_bets += bets
        res.freebets_claimed = claimed
        res.skipped += sk
        res.peak = max(res.peak, bankroll)
        res.trough = min(res.trough, bankroll)
        weeks_used += 3

    # Phase 2: Deposit bonuses
    if do_bonuses:
        for _, bonus_amt, wager_mult, min_odds in DEPOSIT_BONUSES:
            if bankroll < bonus_amt:
                continue
            if weeks_used >= WEEKS:
                break

            new_br, bp, betp, bets, _, wks = simulate_deposit_bonus(bankroll, bonus_amt, wager_mult, min_odds, cfg)
            bankroll = new_br
            res.bonus_profit += bp
            res.betting_profit += betp
            res.total_bets += bets
            res.bonuses_unlocked += 1
            weeks_used += wks
            res.peak = max(res.peak, bankroll)
            res.trough = min(res.trough, bankroll)

            if bankroll < 5.0:
                res.final = bankroll
                res.ruin = True
                return res

    # Phase 3: Snowball
    remaining = max(0, WEEKS - weeks_used)
    if remaining > 0 and bankroll >= 5.0:
        bankroll, vp, vb, vsk, pk, tr = simulate_value_phase(bankroll, remaining, cfg)
        res.betting_profit += vp
        res.total_bets += vb
        res.skipped += vsk
        res.peak = max(res.peak, pk)
        res.trough = min(res.trough, tr)

    res.final = bankroll
    res.weeks_used = weeks_used
    res.ruin = bankroll < 5.0
    return res


# ── Stats ──


def pct(vals, p):
    s = sorted(vals)
    idx = int(len(s) * p / 100.0)
    return s[min(idx, len(s) - 1)]


def run_mc(start, cfg, do_fb=True, do_bonus=True):
    return [run_full_sim(start, cfg, do_fb, do_bonus) for _ in range(NUM_SIMS)]


# ── Main ──


def main():
    random.seed(42)

    fprint("=" * 110)
    fprint("  FULL BANKROLL SWEEP — Fresh Account + Pure Value + High Bankrolls")
    fprint(f"  {NUM_SIMS} sims × {WEEKS} weeks × {BETS_PER_WEEK} bets/week")
    fprint("=" * 110)

    configs = [CURRENT, PROPOSED]

    # ═══════════════════════════════════════════════════════════════════
    # SECTION A: PURE VALUE (no bonuses) — all bankroll levels
    # ═══════════════════════════════════════════════════════════════════
    fprint("\n\n" + "#" * 110)
    fprint("  SECTION A: PURE VALUE BETTING (no freebets, no bonuses)")
    fprint("  Isolates Kelly performance at every bankroll level")
    fprint("#" * 110)

    pure_bankrolls = [
        500,
        1000,
        1500,
        2000,
        3000,
        5000,
        7500,
        10000,
        15000,
        20000,
        30000,
        50000,
        75000,
        100000,
        150000,
        200000,
    ]

    for cfg in configs:
        fprint(f"\n  ── {cfg.name} ──")
        fprint(
            f"  {'Bankroll':>10s}  {'Median':>10s}  {'P10':>10s}  {'P25':>10s}  {'P75':>10s}  "
            f"{'P90':>10s}  {'Growth':>7s}  {'Ruin%':>6s}  {'Play%':>6s}  {'MaxDD':>6s}"
        )
        fprint(
            f"  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 7}  {'-' * 6}  {'-' * 6}  {'-' * 6}"
        )

        for bankroll in pure_bankrolls:
            random.seed(42)
            sims = run_mc(bankroll, cfg, do_fb=False, do_bonus=False)

            finals = [s.final for s in sims]
            ruin_n = sum(1 for s in sims if s.ruin)
            plays = [s.total_bets / max(1, s.total_bets + s.skipped) * 100 for s in sims]
            dds = [(s.peak - s.trough) / max(1, s.peak) * 100 for s in sims]

            med = pct(finals, 50)
            fprint(
                f"  {bankroll:>10,}  {med:>10,.0f}  {pct(finals, 10):>10,.0f}  {pct(finals, 25):>10,.0f}  "
                f"{pct(finals, 75):>10,.0f}  {pct(finals, 90):>10,.0f}  {med / bankroll:>6.2f}x  "
                f"{ruin_n / NUM_SIMS * 100:>5.1f}%  {pct(plays, 50):>5.1f}%  {pct(dds, 50):>5.1f}%"
            )

    # ═══════════════════════════════════════════════════════════════════
    # SECTION B: FRESH ACCOUNT — freebets + bonuses + snowball
    # ═══════════════════════════════════════════════════════════════════
    fprint("\n\n" + "#" * 110)
    fprint("  SECTION B: FRESH ACCOUNT (freebets → deposit bonuses → snowball)")
    fprint(
        f"  Finding optimal minimum start. Trigger capital: {TOTAL_TRIGGER_CAPITAL:,} kr, "
        f"Deposit bonuses: {TOTAL_DEPOSIT_BONUS:,} kr"
    )
    fprint("#" * 110)

    fresh_bankrolls = [
        250,
        500,
        750,
        1000,
        1250,
        1500,
        2000,
        2500,
        3000,
        3550,
        4000,
        5000,
        6000,
        7500,
        10000,
        12500,
        15000,
        20000,
        25000,
        30000,
    ]

    for cfg in configs:
        fprint(f"\n  ── {cfg.name} ──")
        fprint(
            f"  {'Start':>10s}  {'Median':>10s}  {'P10':>10s}  {'P25':>10s}  {'P75':>10s}  "
            f"{'P90':>10s}  {'Growth':>7s}  {'Ruin%':>6s}  {'FBs':>5s}  {'Bonuses':>8s}  "
            f"{'Trough P10':>11s}"
        )
        fprint(
            f"  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 7}  {'-' * 6}  "
            f"{'-' * 5}  {'-' * 8}  {'-' * 11}"
        )

        for start in fresh_bankrolls:
            random.seed(42)
            sims = run_mc(start, cfg, do_fb=True, do_bonus=True)

            finals = [s.final for s in sims]
            ruin_n = sum(1 for s in sims if s.ruin)
            fbs = [s.freebets_claimed for s in sims]
            bonuses = [s.bonuses_unlocked for s in sims]
            troughs = [s.trough for s in sims]

            med = pct(finals, 50)
            growth = med / start if start > 0 else 0
            fprint(
                f"  {start:>10,}  {med:>10,.0f}  {pct(finals, 10):>10,.0f}  {pct(finals, 25):>10,.0f}  "
                f"{pct(finals, 75):>10,.0f}  {pct(finals, 90):>10,.0f}  {growth:>6.1f}x  "
                f"{ruin_n / NUM_SIMS * 100:>5.1f}%  {pct(fbs, 50):>4.0f}/{len(FREEBETS)}  "
                f"{pct(bonuses, 50):>3.0f}/{len(DEPOSIT_BONUSES):>2d}    "
                f"{pct(troughs, 10):>10,.0f}"
            )

    # ═══════════════════════════════════════════════════════════════════
    # SECTION C: ROI per kr deposited (efficiency of starting capital)
    # ═══════════════════════════════════════════════════════════════════
    fprint("\n\n" + "#" * 110)
    fprint("  SECTION C: CAPITAL EFFICIENCY — Median Profit per kr Deposited")
    fprint("  Which starting amount gives the best return on your initial deposit?")
    fprint("#" * 110)

    cfg = PROPOSED  # Use best config
    fprint(f"\n  Using: {cfg.name}")
    fprint(
        f"  {'Start':>10s}  {'Median Final':>13s}  {'Median Profit':>14s}  {'ROI':>7s}  "
        f"{'Profit/kr':>10s}  {'Ruin%':>6s}  {'Bonuses':>8s}  {'Rating':>8s}"
    )
    fprint(f"  {'-' * 10}  {'-' * 13}  {'-' * 14}  {'-' * 7}  {'-' * 10}  {'-' * 6}  {'-' * 8}  {'-' * 8}")

    best_roi = 0
    best_start = 0
    best_profit_per_kr = 0
    best_pp_start = 0

    for start in fresh_bankrolls:
        random.seed(42)
        sims = run_mc(start, cfg, do_fb=True, do_bonus=True)

        finals = [s.final for s in sims]
        profits = [s.final - start for s in sims]
        ruin_n = sum(1 for s in sims if s.ruin)
        bonuses = [s.bonuses_unlocked for s in sims]

        med_final = pct(finals, 50)
        med_profit = pct(profits, 50)
        roi = med_profit / start * 100 if start > 0 else 0
        profit_per_kr = med_profit / start if start > 0 else 0

        # Rating: balance growth vs risk vs capital efficiency
        # Penalize ruin, reward high profit/kr and bonus unlocks
        ruin_pct = ruin_n / NUM_SIMS * 100
        med_bonuses = pct(bonuses, 50)
        rating = profit_per_kr * (1 - ruin_pct / 100) * (1 + med_bonuses / len(DEPOSIT_BONUSES))
        stars = "★" * min(5, max(0, int(rating / 0.5)))

        if roi > best_roi and ruin_pct < 2:
            best_roi = roi
            best_start = start
        if profit_per_kr > best_profit_per_kr and ruin_pct < 2:
            best_profit_per_kr = profit_per_kr
            best_pp_start = start

        fprint(
            f"  {start:>10,}  {med_final:>13,.0f}  {med_profit:>14,.0f}  {roi:>6.0f}%  "
            f"{profit_per_kr:>9.2f}x  {ruin_pct:>5.1f}%  {med_bonuses:>3.0f}/{len(DEPOSIT_BONUSES):>2d}    "
            f"{stars:<8s}"
        )

    fprint(f"\n  Best ROI: {best_start:,} kr start → {best_roi:.0f}% ROI")
    fprint(f"  Best profit/kr: {best_pp_start:,} kr start → {best_profit_per_kr:.2f}x return per kr deposited")

    # ═══════════════════════════════════════════════════════════════════
    # SECTION D: CURRENT vs PROPOSED side-by-side at key bankrolls
    # ═══════════════════════════════════════════════════════════════════
    fprint("\n\n" + "#" * 110)
    fprint("  SECTION D: HEAD-TO-HEAD — CURRENT vs PROPOSED at key fresh-account starts")
    fprint("#" * 110)

    key_starts = [1000, 2000, 3550, 5000, 10000, 20000, 50000]

    fprint(
        f"\n  {'Start':>10s}  │ {'CURRENT Median':>15s} {'P10':>10s} {'Ruin':>6s}  │ "
        f"{'PROPOSED Median':>16s} {'P10':>10s} {'Ruin':>6s}  │ {'Δ Median':>9s}"
    )
    fprint(f"  {'-' * 10}  │ {'-' * 15} {'-' * 10} {'-' * 6}  │ {'-' * 16} {'-' * 10} {'-' * 6}  │ {'-' * 9}")

    for start in key_starts:
        random.seed(42)
        sims_c = run_mc(start, CURRENT, do_fb=True, do_bonus=True)
        random.seed(42)
        sims_p = run_mc(start, PROPOSED, do_fb=True, do_bonus=True)

        fc = [s.final for s in sims_c]
        fp = [s.final for s in sims_p]
        mc = pct(fc, 50)
        mp = pct(fp, 50)
        rc = sum(1 for s in sims_c if s.ruin) / NUM_SIMS * 100
        rp = sum(1 for s in sims_p if s.ruin) / NUM_SIMS * 100
        delta = (mp / max(1, mc) - 1) * 100

        fprint(
            f"  {start:>10,}  │ {mc:>15,.0f} {pct(fc, 10):>10,.0f} {rc:>5.1f}%  │ "
            f"{mp:>16,.0f} {pct(fp, 10):>10,.0f} {rp:>5.1f}%  │ {delta:>+8.1f}%"
        )

    # ═══════════════════════════════════════════════════════════════════
    # SECTION E: Minimum viable bankroll analysis
    # ═══════════════════════════════════════════════════════════════════
    fprint("\n\n" + "#" * 110)
    fprint("  SECTION E: MINIMUM VIABLE BANKROLL — Fresh Account")
    fprint("  Fine-grained sweep from 100-2000 kr to find the floor")
    fprint("#" * 110)

    micro_starts = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1750, 2000]

    cfg = PROPOSED
    fprint(f"\n  Using: {cfg.name}")
    fprint(
        f"  {'Start':>8s}  {'Median':>10s}  {'P10':>10s}  {'Ruin%':>6s}  {'FBs':>5s}  "
        f"{'Bonuses':>8s}  {'Play%':>6s}  {'Verdict':>20s}"
    )
    fprint(f"  {'-' * 8}  {'-' * 10}  {'-' * 10}  {'-' * 6}  {'-' * 5}  {'-' * 8}  {'-' * 6}  {'-' * 20}")

    for start in micro_starts:
        random.seed(42)
        sims = run_mc(start, cfg, do_fb=True, do_bonus=True)

        finals = [s.final for s in sims]
        ruin_n = sum(1 for s in sims if s.ruin)
        fbs = [s.freebets_claimed for s in sims]
        bonuses = [s.bonuses_unlocked for s in sims]
        plays = [s.total_bets / max(1, s.total_bets + s.skipped) * 100 for s in sims]

        med = pct(finals, 50)
        ruin_pct = ruin_n / NUM_SIMS * 100
        med_fbs = pct(fbs, 50)
        med_bonuses = pct(bonuses, 50)
        play = pct(plays, 50)

        if ruin_pct > 5:
            verdict = "TOO RISKY"
        elif ruin_pct > 1:
            verdict = "Marginal"
        elif med_fbs < len(FREEBETS) * 0.5:
            verdict = "Under-capitalized"
        elif med_bonuses < 5:
            verdict = "Few bonuses"
        elif play < 80:
            verdict = "Low play rate"
        else:
            profit = med - start
            if profit / start > 3:
                verdict = "EXCELLENT"
            elif profit / start > 2:
                verdict = "GOOD"
            elif profit / start > 1:
                verdict = "OK"
            else:
                verdict = "Viable"

        fprint(
            f"  {start:>8,}  {med:>10,.0f}  {pct(finals, 10):>10,.0f}  {ruin_pct:>5.1f}%  "
            f"{med_fbs:>4.0f}/{len(FREEBETS)}  {med_bonuses:>3.0f}/{len(DEPOSIT_BONUSES):>2d}    "
            f"{play:>5.1f}%  {verdict:>20s}"
        )


if __name__ == "__main__":
    main()
