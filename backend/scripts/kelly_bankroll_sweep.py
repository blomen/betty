"""
Kelly × Bankroll Sweep — Monte Carlo parameter search
======================================================
Tests whether the current stake calc is optimal across bankroll levels,
or if we can improve growth / reduce ruin by tuning Kelly parameters.

Sweeps:
  - Bankrolls: 500, 1000, 1500, 2000, 3000, 5000, 7500, 10000, 15000, 20000
  - Kelly configs: current production, higher/lower min_kelly, different caps,
    alternative boost curves, flat Kelly variants

For each (bankroll, config) pair: 3000 MC runs × 52 weeks pure value betting.
No bonuses — isolates the stake calc itself.

Run: python scripts/kelly_bankroll_sweep.py
"""

import random
import sys
import io
from dataclasses import dataclass, field
from typing import List, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Edge distributions (from live data) ──
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

NUM_SIMS = 3000
WEEKS = 52
BETS_PER_WEEK = 35


# ── Sampling ──

def sample_bet() -> Tuple[float, float]:
    """Sample (edge_pct, odds) from all streams."""
    r = random.random()
    cumulative = 0.0
    for _, weight, dist in STREAM_WEIGHTS:
        cumulative += weight
        if r <= cumulative:
            return _sample_from(dist)
    return _sample_from(SOFT_VALUE_EDGE_DIST)


def _sample_from(dist) -> Tuple[float, float]:
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


# ── Kelly configs to test ──

@dataclass
class KellyConfig:
    name: str
    min_kelly: float          # Floor for low-edge bets
    max_kelly: float          # Ceiling for high-edge bets
    edge_low: float           # Edge threshold for min_kelly (pct)
    edge_high: float          # Edge threshold for max_kelly (pct)
    single_bet_cap: float     # Max % of bankroll per bet
    min_stake: float          # Absolute min stake (before dynamic scaling)
    # Low-bankroll boost
    boost_factor: float       # Multiply kelly by this at low bankrolls
    boost_threshold: float    # Below this bankroll, apply full boost
    boost_taper: float        # Above this, boost is fully tapered off
    # Dynamic min stake
    dynamic_min_stake: bool   # Scale min_stake with bankroll


def make_configs() -> List[KellyConfig]:
    return [
        # ── Current production ──
        KellyConfig(
            name="CURRENT (0.25-0.75, 3% cap, boost≤5k)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.03, min_stake=25.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=True,
        ),
        # ── More aggressive at low bankroll ──
        KellyConfig(
            name="Aggressive low-BR (boost 1.5x ≤5k)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.03, min_stake=25.0,
            boost_factor=1.5, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=True,
        ),
        # ── Even more aggressive: full Kelly + 4% cap at low BR ──
        KellyConfig(
            name="Full Kelly + 4% cap ≤5k",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.04, min_stake=25.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=True,
        ),
        # ── Higher min Kelly (0.35 floor) ──
        KellyConfig(
            name="Higher floor (0.35-0.75)",
            min_kelly=0.35, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.03, min_stake=25.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=True,
        ),
        # ── Lower ceiling (half Kelly max) ──
        KellyConfig(
            name="Conservative (0.25-0.50, 2.5% cap)",
            min_kelly=0.25, max_kelly=0.50,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.025, min_stake=25.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=True,
        ),
        # ── Flat half Kelly ──
        KellyConfig(
            name="Flat 0.50 Kelly, 3% cap",
            min_kelly=0.50, max_kelly=0.50,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.03, min_stake=25.0,
            boost_factor=1.0, boost_threshold=0, boost_taper=0,
            dynamic_min_stake=True,
        ),
        # ── No boost at low bankroll ──
        KellyConfig(
            name="No low-BR boost (0.25-0.75)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.03, min_stake=25.0,
            boost_factor=1.0, boost_threshold=0, boost_taper=0,
            dynamic_min_stake=True,
        ),
        # ── Wider edge ramp (1%-8%) ──
        KellyConfig(
            name="Wider ramp (1%-8% edge)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=1.0, edge_high=8.0,
            single_bet_cap=0.03, min_stake=25.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=True,
        ),
        # ── 5% cap (more aggressive sizing) ──
        KellyConfig(
            name="5% bet cap (0.25-0.75)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.05, min_stake=25.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=True,
        ),
        # ── Aggressive low BR: boost 1.5x + 4% cap + lower taper ──
        KellyConfig(
            name="Aggro low-BR (1.5x boost, 4% cap, taper 10k)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.04, min_stake=25.0,
            boost_factor=1.5, boost_threshold=5000, boost_taper=10000,
            dynamic_min_stake=True,
        ),
        # ── No dynamic min stake (fixed 25 kr) ──
        KellyConfig(
            name="Fixed min_stake=25 (no scaling)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.03, min_stake=25.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=False,
        ),
        # ── Very low min stake (5 kr fixed) ──
        KellyConfig(
            name="Fixed min_stake=5 (micro bets ok)",
            min_kelly=0.25, max_kelly=0.75,
            edge_low=2.0, edge_high=6.0,
            single_bet_cap=0.03, min_stake=5.0,
            boost_factor=1.333, boost_threshold=5000, boost_taper=15000,
            dynamic_min_stake=False,
        ),
    ]


# ── Stake calculation per config ──

def calc_stake(bankroll: float, edge_pct: float, odds: float, cfg: KellyConfig) -> float:
    """Calculate stake for given config. Returns 0 if skip."""
    if bankroll <= 0 or edge_pct < 1.0 or odds < 1.15:
        return 0.0

    edge = edge_pct / 100.0

    # Kelly fraction with edge ramp
    if edge_pct <= cfg.edge_low:
        kelly = cfg.min_kelly
    elif edge_pct >= cfg.edge_high:
        kelly = cfg.max_kelly
    else:
        t = (edge_pct - cfg.edge_low) / (cfg.edge_high - cfg.edge_low)
        kelly = cfg.min_kelly + t * (cfg.max_kelly - cfg.min_kelly)

    # Low-bankroll boost
    if cfg.boost_factor > 1.0 and cfg.boost_threshold > 0:
        if bankroll <= cfg.boost_threshold:
            kelly *= cfg.boost_factor
        elif bankroll < cfg.boost_taper:
            t = (bankroll - cfg.boost_threshold) / (cfg.boost_taper - cfg.boost_threshold)
            kelly *= cfg.boost_factor - t * (cfg.boost_factor - 1.0)

    # Raw Kelly stake
    raw = bankroll * kelly * edge / (odds - 1.0)

    # Single bet cap
    capped = min(raw, bankroll * cfg.single_bet_cap)

    # Min stake check
    if cfg.dynamic_min_stake:
        # Same formula as production
        effective_min = max(5.0, bankroll * 0.005)
        effective_min = min(effective_min, cfg.min_stake)
        effective_min = max(5.0, (effective_min // 5) * 5)
    else:
        effective_min = cfg.min_stake

    if capped < effective_min:
        return 0.0

    # Round to natural amount
    if capped < 50:
        return max(5.0, round(capped / 5) * 5)
    elif capped < 200:
        return round(capped / 10) * 10
    elif capped < 500:
        return round(capped / 25) * 25
    else:
        return round(capped / 50) * 50


# ── Single simulation run ──

@dataclass
class SimResult:
    final: float
    peak: float
    trough: float
    bets: int
    skipped: int  # bets that were 0 (below min stake)
    ruin: bool


def run_sim(bankroll: float, cfg: KellyConfig) -> SimResult:
    peak = bankroll
    trough = bankroll
    total_bets = 0
    skipped = 0

    for _ in range(WEEKS):
        n = BETS_PER_WEEK + random.randint(-3, 3)
        n = max(1, n)
        for _ in range(n):
            if bankroll < 5.0:
                return SimResult(bankroll, peak, trough, total_bets, skipped, True)

            edge_pct, odds = sample_bet()
            stake = calc_stake(bankroll, edge_pct, odds, cfg)
            if stake <= 0:
                skipped += 1
                continue

            result = simulate_bet(stake, edge_pct, odds)
            bankroll += result
            total_bets += 1
            peak = max(peak, bankroll)
            trough = min(trough, bankroll)

    return SimResult(bankroll, peak, trough, total_bets, skipped, bankroll < 5.0)


# ── Monte Carlo sweep ──

def pct(vals, p):
    s = sorted(vals)
    idx = int(len(s) * p / 100.0)
    return s[min(idx, len(s) - 1)]


def run_sweep():
    bankrolls = [500, 1000, 1500, 2000, 3000, 5000, 7500, 10000, 15000, 20000]
    configs = make_configs()

    print("=" * 100)
    print("  KELLY × BANKROLL SWEEP — Monte Carlo Parameter Search")
    print(f"  {NUM_SIMS} sims × {WEEKS} weeks × {BETS_PER_WEEK} bets/week per (bankroll, config) pair")
    print("=" * 100)

    # Store results for comparison table
    # results_grid[bankroll_idx][config_idx] = {median, p10, ruin%, play%, ...}
    results_grid = {}

    for bi, bankroll in enumerate(bankrolls):
        print(f"\n{'#' * 100}")
        print(f"  BANKROLL: {bankroll:,} kr")
        print(f"{'#' * 100}")

        hdr = (f"  {'Config':<42s}  {'Median':>8s}  {'P10':>8s}  {'P25':>8s}  {'P75':>8s}  "
               f"{'P90':>8s}  {'Ruin%':>6s}  {'Play%':>6s}  {'MaxDD':>6s}")
        print(hdr)
        print(f"  {'-'*42}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*6}")

        results_grid[bankroll] = {}

        for ci, cfg in enumerate(configs):
            random.seed(42)  # Same seed per config for fair comparison
            sims = [run_sim(bankroll, cfg) for _ in range(NUM_SIMS)]

            finals = [s.final for s in sims]
            ruin_count = sum(1 for s in sims if s.ruin)
            play_rates = []
            max_dds = []
            for s in sims:
                total_opps = s.bets + s.skipped
                play_rates.append(s.bets / max(1, total_opps) * 100)
                dd = (s.peak - s.trough) / max(1, s.peak) * 100 if s.peak > 0 else 0
                max_dds.append(dd)

            med = pct(finals, 50)
            p10 = pct(finals, 10)
            p25 = pct(finals, 25)
            p75 = pct(finals, 75)
            p90 = pct(finals, 90)
            ruin_pct = ruin_count / NUM_SIMS * 100
            play_med = pct(play_rates, 50)
            dd_med = pct(max_dds, 50)

            growth = med / bankroll if bankroll > 0 else 0

            results_grid[bankroll][cfg.name] = {
                "median": med, "p10": p10, "p25": p25, "p75": p75, "p90": p90,
                "ruin": ruin_pct, "play": play_med, "dd": dd_med, "growth": growth,
            }

            marker = " ◄" if ci == 0 else ""
            print(f"  {cfg.name:<42s}  {med:>8,.0f}  {p10:>8,.0f}  {p25:>8,.0f}  {p75:>8,.0f}  "
                  f"{p90:>8,.0f}  {ruin_pct:>5.1f}%  {play_med:>5.1f}%  {dd_med:>5.1f}%{marker}")

    # ── Summary: best config per bankroll ──
    print(f"\n\n{'=' * 100}")
    print("  SUMMARY: BEST CONFIG PER BANKROLL (by median final, ruin < 5%)")
    print(f"{'=' * 100}")
    print(f"  {'Bankroll':>10s}  {'Best Config':<42s}  {'Median':>8s}  {'Growth':>7s}  {'Ruin%':>6s}  {'vs Current':>10s}")
    print(f"  {'-'*10}  {'-'*42}  {'-'*8}  {'-'*7}  {'-'*6}  {'-'*10}")

    current_name = configs[0].name
    for bankroll in bankrolls:
        # Find best by median with ruin < 5%
        best_name = None
        best_med = -1
        for name, stats in results_grid[bankroll].items():
            if stats["ruin"] < 5.0 and stats["median"] > best_med:
                best_med = stats["median"]
                best_name = name

        if best_name is None:
            # All have >= 5% ruin, pick lowest ruin
            best_name = min(results_grid[bankroll], key=lambda n: results_grid[bankroll][n]["ruin"])
            best_med = results_grid[bankroll][best_name]["median"]

        current_med = results_grid[bankroll][current_name]["median"]
        diff = (best_med / max(1, current_med) - 1) * 100

        stats = results_grid[bankroll][best_name]
        marker = "  (current)" if best_name == current_name else ""
        print(f"  {bankroll:>10,}  {best_name:<42s}  {best_med:>8,.0f}  {stats['growth']:>6.2f}x  "
              f"{stats['ruin']:>5.1f}%  {diff:>+9.1f}%{marker}")

    # ── Summary: current config across all bankrolls ──
    print(f"\n\n{'=' * 100}")
    print("  CURRENT PRODUCTION CONFIG ACROSS ALL BANKROLLS")
    print(f"{'=' * 100}")
    print(f"  {'Bankroll':>10s}  {'Median':>8s}  {'P10':>8s}  {'Growth':>7s}  {'Ruin%':>6s}  {'Play%':>6s}  {'MaxDD':>6s}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*6}")

    for bankroll in bankrolls:
        s = results_grid[bankroll][current_name]
        print(f"  {bankroll:>10,}  {s['median']:>8,.0f}  {s['p10']:>8,.0f}  {s['growth']:>6.2f}x  "
              f"{s['ruin']:>5.1f}%  {s['play']:>5.1f}%  {s['dd']:>5.1f}%")

    # ── Stake examples at different bankrolls ──
    print(f"\n\n{'=' * 100}")
    print("  STAKE EXAMPLES: CURRENT CONFIG vs BEST ALTERNATIVES")
    print(f"{'=' * 100}")

    cfg_current = configs[0]
    test_bets = [(3.0, 2.50), (5.0, 3.00), (8.0, 5.00), (15.0, 8.00)]

    for bankroll in [500, 1000, 2000, 5000, 10000, 20000]:
        print(f"\n  Bankroll: {bankroll:,} kr")
        print(f"  {'Edge':>6s}  {'Odds':>5s}  {'Current':>8s}  {'Flat0.5':>8s}  {'Aggro':>8s}  {'Conserv':>8s}  {'5%cap':>8s}")
        print(f"  {'-'*6}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
        for edge_pct, odds in test_bets:
            s_cur = calc_stake(bankroll, edge_pct, odds, configs[0])
            s_flat = calc_stake(bankroll, edge_pct, odds, configs[5])   # Flat 0.50
            s_aggro = calc_stake(bankroll, edge_pct, odds, configs[9])  # Aggro low-BR
            s_cons = calc_stake(bankroll, edge_pct, odds, configs[4])   # Conservative
            s_5pct = calc_stake(bankroll, edge_pct, odds, configs[8])   # 5% cap
            print(f"  {edge_pct:>5.1f}%  {odds:>5.2f}  {s_cur:>8.0f}  {s_flat:>8.0f}  {s_aggro:>8.0f}  {s_cons:>8.0f}  {s_5pct:>8.0f}")


if __name__ == "__main__":
    run_sweep()
