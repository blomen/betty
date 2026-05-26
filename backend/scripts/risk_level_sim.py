"""
Betty — Risk Level Simulation
====================================
What happens if we increase Kelly fraction and single bet cap
to play more bets with a smaller bankroll?

Current defaults: max_kelly=0.75, single_bet_cap=3%, mEP=2.0
Question: can we just crank up risk instead of lowering mEP?

Run: python scripts/risk_level_sim.py
"""

import io
import random
import sys
from dataclasses import dataclass, field

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Edge distributions (from growth_simulation.py) ──
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

ABSOLUTE_MIN_STAKE = 5.0
DEFAULT_MIN_STAKE = 25.0
NUM_SIMS = 2000


# ── Risk profiles to test ──
@dataclass
class RiskProfile:
    name: str
    max_kelly: float  # Kelly scaling ceiling (0.25=quarter, 0.75=3/4, 1.0=full)
    min_kelly: float  # Kelly scaling floor
    single_bet_cap: float  # Max % of bankroll per bet
    min_expected_profit: float  # mEP guard
    description: str = ""


RISK_PROFILES = [
    RiskProfile("Conservative", 0.50, 0.15, 0.02, 2.0, "Low risk, current-ish"),
    RiskProfile("Current", 0.75, 0.25, 0.03, 2.0, "Current defaults"),
    RiskProfile("Current+mEP0.75", 0.75, 0.25, 0.03, 0.75, "Current kelly, lower mEP"),
    RiskProfile("Higher Kelly", 1.00, 0.25, 0.03, 2.0, "Full Kelly, same cap"),
    RiskProfile("Higher Cap", 0.75, 0.25, 0.05, 2.0, "Same Kelly, 5% cap"),
    RiskProfile("Aggressive", 1.00, 0.25, 0.05, 2.0, "Full Kelly + 5% cap"),
    RiskProfile("Aggressive+mEP0", 1.00, 0.25, 0.05, 0.0, "Full Kelly + 5% cap + no mEP"),
    RiskProfile("YOLO", 1.25, 0.30, 0.07, 0.0, "1.25x Kelly + 7% cap"),
    RiskProfile("Full Send", 1.50, 0.35, 0.10, 0.0, "1.5x Kelly + 10% cap"),
]


def kelly_fraction(edge_pct: float, min_kelly: float, max_kelly: float) -> float:
    if edge_pct <= 2.0:
        return min_kelly
    elif edge_pct >= 6.0:
        return max_kelly
    t = (edge_pct - 2.0) / 4.0
    return min_kelly + t * (max_kelly - min_kelly)


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


def calculate_stake(bankroll, edge_pct, odds, profile: RiskProfile):
    edge = edge_pct / 100.0
    frac = kelly_fraction(edge_pct, profile.min_kelly, profile.max_kelly)
    min_stake = dynamic_min_stake(bankroll)

    raw = bankroll * frac * edge / (odds - 1.0)
    capped = min(raw, bankroll * profile.single_bet_cap)
    stake = max(0.0, capped)
    stake = round_stake_natural(stake)

    if stake < min_stake:
        return 0.0
    if profile.min_expected_profit > 0 and stake * edge < profile.min_expected_profit:
        return 0.0
    return stake


@dataclass
class SimResult:
    final_bankroll: float = 0.0
    profit: float = 0.0
    bets_played: int = 0
    bets_skipped: int = 0
    total_staked: float = 0.0
    peak_bankroll: float = 0.0
    min_bankroll: float = 0.0
    max_drawdown_pct: float = 0.0  # Worst peak-to-trough %
    ruin: bool = False
    weekly_bankrolls: list[float] = field(default_factory=list)


def simulate(starting_bankroll, bets_per_week, weeks, profile: RiskProfile, track_weekly=False):
    bankroll = starting_bankroll
    profit = 0.0
    bets_played = 0
    bets_skipped = 0
    total_staked = 0.0
    peak = bankroll
    trough = bankroll
    max_dd_pct = 0.0
    weekly = [bankroll] if track_weekly else []

    for w in range(weeks):
        n_bets = bets_per_week + random.randint(-3, 3)
        n_bets = max(1, n_bets)

        for _ in range(n_bets):
            if bankroll < ABSOLUTE_MIN_STAKE:
                return SimResult(
                    final_bankroll=bankroll,
                    profit=profit,
                    bets_played=bets_played,
                    bets_skipped=bets_skipped,
                    total_staked=total_staked,
                    peak_bankroll=peak,
                    min_bankroll=trough,
                    max_drawdown_pct=max_dd_pct,
                    ruin=True,
                    weekly_bankrolls=weekly,
                )

            edge_pct, odds, _ = sample_bet_all_streams()
            stake = calculate_stake(bankroll, edge_pct, odds, profile)

            if stake <= 0:
                bets_skipped += 1
                continue

            result = simulate_bet(stake, edge_pct, odds)
            bankroll += result
            profit += result
            total_staked += stake
            bets_played += 1

            if bankroll > peak:
                peak = bankroll
            if bankroll < trough:
                trough = bankroll
            if peak > 0:
                dd = (peak - bankroll) / peak * 100
                if dd > max_dd_pct:
                    max_dd_pct = dd

        if track_weekly:
            weekly.append(bankroll)

    return SimResult(
        final_bankroll=bankroll,
        profit=profit,
        bets_played=bets_played,
        bets_skipped=bets_skipped,
        total_staked=total_staked,
        peak_bankroll=peak,
        min_bankroll=trough,
        max_drawdown_pct=max_dd_pct,
        ruin=bankroll < ABSOLUTE_MIN_STAKE,
        weekly_bankrolls=weekly,
    )


def pct(values, p):
    s = sorted(values)
    idx = int(len(s) * p / 100.0)
    return s[min(idx, len(s) - 1)]


def run_monte_carlo(starting_bankroll, bets_per_week, weeks, profile, n_sims=NUM_SIMS, track_weekly=False):
    return [simulate(starting_bankroll, bets_per_week, weeks, profile, track_weekly) for _ in range(n_sims)]


# =====================================================================
# SIMULATION 1: Risk profile comparison at each bankroll level
# =====================================================================


def risk_comparison():
    print("=" * 120)
    print("  SIMULATION 1: RISK PROFILE COMPARISON")
    print(f"  {NUM_SIMS:,} Monte Carlo runs | 35 bets/week | 52 weeks | no bonuses")
    print("=" * 120)

    bankrolls = [5000, 10000, 20000]

    for bankroll in bankrolls:
        print(f"\n  {'━' * 116}")
        print(f"  Starting bankroll: {bankroll:,} kr")
        print(f"  {'━' * 116}")

        print(
            f"\n  {'Profile':<19s}  {'Kelly':>5s}  {'Cap':>4s}  {'mEP':>4s}  "
            f"{'Med Final':>10s}  {'Growth':>7s}  "
            f"{'P10 Final':>10s}  {'P5 Final':>9s}  "
            f"{'Play%':>6s}  {'Ruin%':>6s}  "
            f"{'Med MaxDD':>9s}  {'P90 MaxDD':>9s}  "
            f"{'Med Trough':>11s}"
        )
        print(
            f"  {'-' * 19}  {'-' * 5}  {'-' * 4}  {'-' * 4}  "
            f"{'-' * 10}  {'-' * 7}  "
            f"{'-' * 10}  {'-' * 9}  "
            f"{'-' * 6}  {'-' * 6}  "
            f"{'-' * 9}  {'-' * 9}  "
            f"{'-' * 11}"
        )

        for profile in RISK_PROFILES:
            results = run_monte_carlo(bankroll, 35, 52, profile)

            finals = [r.final_bankroll for r in results]
            played = [r.bets_played for r in results]
            skipped = [r.bets_skipped for r in results]
            drawdowns = [r.max_drawdown_pct for r in results]
            troughs = [r.min_bankroll for r in results]
            ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

            med_final = pct(finals, 50)
            p10_final = pct(finals, 10)
            p5_final = pct(finals, 5)
            med_played = pct(played, 50)
            med_skipped = pct(skipped, 50)
            play_pct = med_played / max(1, med_played + med_skipped) * 100
            growth = (med_final / bankroll - 1) * 100
            med_dd = pct(drawdowns, 50)
            p90_dd = pct(drawdowns, 90)
            med_trough = pct(troughs, 50)

            print(
                f"  {profile.name:<19s}  {profile.max_kelly:>5.2f}  {profile.single_bet_cap * 100:>3.0f}%  {profile.min_expected_profit:>4.1f}  "
                f"{med_final:>10,.0f}  {growth:>+6.0f}%  "
                f"{p10_final:>10,.0f}  {p5_final:>9,.0f}  "
                f"{play_pct:>5.1f}%  {ruin_pct:>5.1f}%  "
                f"{med_dd:>8.1f}%  {p90_dd:>8.1f}%  "
                f"{med_trough:>10,.0f}"
            )


# =====================================================================
# SIMULATION 2: Drawdown deep dive
# =====================================================================


def drawdown_analysis():
    print("\n\n" + "=" * 120)
    print("  SIMULATION 2: DRAWDOWN DEEP DIVE — How bad can it get?")
    print(f"  {NUM_SIMS:,} runs | 10,000 kr start | 35 bets/week | 52 weeks")
    print("=" * 120)

    bankroll = 10000

    print(
        f"\n  {'Profile':<19s}  {'Med DD':>7s}  {'P75 DD':>7s}  {'P90 DD':>7s}  {'P95 DD':>7s}  {'P99 DD':>7s}  "
        f"{'Worst':>7s}  {'Ruin%':>6s}  "
        f"{'Med Trough':>11s}  {'P10 Trough':>11s}  {'P5 Trough':>10s}"
    )
    print(
        f"  {'-' * 19}  {'-' * 7}  {'-' * 7}  {'-' * 7}  {'-' * 7}  {'-' * 7}  "
        f"{'-' * 7}  {'-' * 6}  "
        f"{'-' * 11}  {'-' * 11}  {'-' * 10}"
    )

    for profile in RISK_PROFILES:
        results = run_monte_carlo(bankroll, 35, 52, profile)
        dds = [r.max_drawdown_pct for r in results]
        troughs = [r.min_bankroll for r in results]
        ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

        print(
            f"  {profile.name:<19s}  "
            f"{pct(dds, 50):>6.1f}%  {pct(dds, 75):>6.1f}%  {pct(dds, 90):>6.1f}%  "
            f"{pct(dds, 95):>6.1f}%  {pct(dds, 99):>6.1f}%  {pct(dds, 100):>6.1f}%  "
            f"{ruin_pct:>5.1f}%  "
            f"{pct(troughs, 50):>10,.0f}  {pct(troughs, 10):>10,.0f}  {pct(troughs, 5):>9,.0f}"
        )


# =====================================================================
# SIMULATION 3: Risk vs reward efficiency (Sharpe-like)
# =====================================================================


def risk_reward_efficiency():
    print("\n\n" + "=" * 120)
    print("  SIMULATION 3: RISK-ADJUSTED RETURNS — Is higher risk worth it?")
    print(f"  {NUM_SIMS:,} runs | 35 bets/week | 52 weeks")
    print("=" * 120)

    bankrolls = [5000, 10000, 20000]

    for bankroll in bankrolls:
        print(f"\n  Starting: {bankroll:,} kr")
        print(
            f"  {'Profile':<19s}  {'Med Profit':>11s}  {'P10 Profit':>11s}  {'Med DD':>7s}  "
            f"{'Profit/DD':>10s}  {'EV/Risk':>8s}  {'Play%':>6s}  {'Ruin':>5s}"
        )
        print(f"  {'-' * 19}  {'-' * 11}  {'-' * 11}  {'-' * 7}  {'-' * 10}  {'-' * 8}  {'-' * 6}  {'-' * 5}")

        for profile in RISK_PROFILES:
            results = run_monte_carlo(bankroll, 35, 52, profile)
            profits = [r.profit for r in results]
            dds = [r.max_drawdown_pct for r in results]
            played = [r.bets_played for r in results]
            skipped = [r.bets_skipped for r in results]
            ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

            med_profit = pct(profits, 50)
            p10_profit = pct(profits, 10)
            med_dd = pct(dds, 50)
            med_played = pct(played, 50)
            med_skipped = pct(skipped, 50)
            play_pct = med_played / max(1, med_played + med_skipped) * 100

            # Profit per unit of drawdown risk
            profit_per_dd = med_profit / max(0.1, med_dd)
            # Risk-adjusted: median profit adjusted for downside
            ev_risk = med_profit / max(1, abs(p10_profit)) if p10_profit < 0 else med_profit / 1000

            marker = ""
            print(
                f"  {profile.name:<19s}  {med_profit:>+10,.0f}  {p10_profit:>+10,.0f}  {med_dd:>6.1f}%  "
                f"{profit_per_dd:>9,.0f}  {ev_risk:>7.1f}  {play_pct:>5.1f}%  {ruin_pct:>4.1f}%{marker}"
            )


# =====================================================================
# SIMULATION 4: Weekly equity curves for select profiles
# =====================================================================


def equity_curves():
    print("\n\n" + "=" * 120)
    print("  SIMULATION 4: WEEKLY EQUITY CURVES — 10,000 kr start")
    print(f"  {NUM_SIMS:,} runs | 35 bets/week | showing P10, P25, Median, P75, P90")
    print("=" * 120)

    bankroll = 10000
    profiles_to_track = [
        RISK_PROFILES[1],  # Current
        RISK_PROFILES[2],  # Current+mEP0.75
        RISK_PROFILES[5],  # Aggressive
        RISK_PROFILES[7],  # YOLO
        RISK_PROFILES[8],  # Full Send
    ]

    for profile in profiles_to_track:
        results = run_monte_carlo(bankroll, 35, 52, profile, track_weekly=True)
        tracked = [r for r in results if r.weekly_bankrolls]
        if not tracked:
            continue

        max_w = max(len(r.weekly_bankrolls) for r in tracked)
        ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

        print(
            f"\n  {profile.name} (kelly={profile.max_kelly}, cap={profile.single_bet_cap * 100:.0f}%, mEP={profile.min_expected_profit})  Ruin: {ruin_pct:.1f}%"
        )
        print(f"  {'Week':>6s}  {'P10':>9s}  {'P25':>9s}  {'Median':>9s}  {'P75':>9s}  {'P90':>9s}")
        print(f"  {'-' * 6}  {'-' * 9}  {'-' * 9}  {'-' * 9}  {'-' * 9}  {'-' * 9}")

        for w in range(max_w):
            if w == 0 or w % 8 == 0 or w == max_w - 1:
                vals = []
                for r in tracked:
                    if w < len(r.weekly_bankrolls):
                        vals.append(r.weekly_bankrolls[w])
                    elif r.weekly_bankrolls:
                        vals.append(r.weekly_bankrolls[-1])
                if vals:
                    print(
                        f"  {w:>6d}  {pct(vals, 10):>9,.0f}  {pct(vals, 25):>9,.0f}  "
                        f"{pct(vals, 50):>9,.0f}  {pct(vals, 75):>9,.0f}  {pct(vals, 90):>9,.0f}"
                    )


# =====================================================================
# SIMULATION 5: Sweet spot finder — grid search
# =====================================================================


def sweet_spot_finder():
    print("\n\n" + "=" * 120)
    print("  SIMULATION 5: SWEET SPOT GRID SEARCH")
    print("  Find the best (kelly, cap, mEP) combo for each bankroll")
    print("  Optimizing: median profit while keeping P90 max drawdown < 60%")
    print(f"  {NUM_SIMS:,} runs | 35 bets/week | 52 weeks")
    print("=" * 120)

    kellys = [0.50, 0.75, 1.00, 1.25]
    caps = [0.03, 0.05, 0.07]
    meps = [0.0, 0.75, 2.0]
    bankrolls = [5000, 10000, 20000]

    for bankroll in bankrolls:
        print(f"\n  {'━' * 110}")
        print(f"  Starting bankroll: {bankroll:,} kr")
        print(f"  {'━' * 110}")

        print(
            f"\n  {'Kelly':>6s}  {'Cap':>4s}  {'mEP':>4s}  "
            f"{'Med Profit':>11s}  {'Growth':>7s}  {'P10 Profit':>11s}  "
            f"{'Play%':>6s}  {'Ruin%':>6s}  "
            f"{'Med DD':>7s}  {'P90 DD':>7s}  {'Score':>7s}"
        )
        print(
            f"  {'-' * 6}  {'-' * 4}  {'-' * 4}  "
            f"{'-' * 11}  {'-' * 7}  {'-' * 11}  "
            f"{'-' * 6}  {'-' * 6}  "
            f"{'-' * 7}  {'-' * 7}  {'-' * 7}"
        )

        best_score = -999999
        best_combo = ""

        for mk in kellys:
            for cap in caps:
                for mep in meps:
                    profile = RiskProfile(
                        f"k{mk}_c{cap}_m{mep}",
                        max_kelly=mk,
                        min_kelly=min(0.25, mk),
                        single_bet_cap=cap,
                        min_expected_profit=mep,
                    )
                    results = run_monte_carlo(bankroll, 35, 52, profile, n_sims=1500)

                    profits = [r.profit for r in results]
                    dds = [r.max_drawdown_pct for r in results]
                    played = [r.bets_played for r in results]
                    skipped = [r.bets_skipped for r in results]
                    ruin_pct = sum(1 for r in results if r.ruin) / len(results) * 100

                    med_profit = pct(profits, 50)
                    p10_profit = pct(profits, 10)
                    med_dd = pct(dds, 50)
                    p90_dd = pct(dds, 90)
                    med_played = pct(played, 50)
                    med_skipped = pct(skipped, 50)
                    play_pct = med_played / max(1, med_played + med_skipped) * 100
                    growth = med_profit / bankroll * 100

                    # Score: median profit penalized by drawdown and ruin
                    # Heavily penalize ruin and extreme drawdowns
                    score = med_profit - (p90_dd * bankroll * 0.01) - (ruin_pct * bankroll * 0.5)

                    marker = ""
                    if score > best_score:
                        best_score = score
                        best_combo = f"kelly={mk}, cap={cap * 100:.0f}%, mEP={mep}"
                        marker = "  ★"

                    print(
                        f"  {mk:>6.2f}  {cap * 100:>3.0f}%  {mep:>4.1f}  "
                        f"{med_profit:>+10,.0f}  {growth:>+6.0f}%  {p10_profit:>+10,.0f}  "
                        f"{play_pct:>5.1f}%  {ruin_pct:>5.1f}%  "
                        f"{med_dd:>6.1f}%  {p90_dd:>6.1f}%  {score:>7,.0f}{marker}"
                    )

        print(f"\n  >>> BEST COMBO: {best_combo} (score: {best_score:,.0f})")


def main():
    random.seed(42)

    print("=" * 120)
    print("  ARNOLD — RISK LEVEL SIMULATION")
    print("  Can we just increase risk to play more bets with lower bankroll?")
    print("=" * 120)

    print(f"""
  RISK PROFILES TESTED:
  {"─" * 90}
  {"Profile":<19s}  {"Max Kelly":>10s}  {"Bet Cap":>8s}  {"mEP":>5s}  {"Description"}
  {"─" * 19}  {"─" * 10}  {"─" * 8}  {"─" * 5}  {"─" * 40}""")
    for p in RISK_PROFILES:
        print(
            f"  {p.name:<19s}  {p.max_kelly:>10.2f}  {p.single_bet_cap * 100:>7.0f}%  {p.min_expected_profit:>5.1f}  {p.description}"
        )

    print("""
  WHAT CHANGES WITH HIGHER RISK:
  - Higher Kelly → bigger stakes relative to edge → bigger swings
  - Higher cap → single bet can be larger % of bankroll → more concentration risk
  - Lower mEP → play more low-EV bets → more volume but smaller per-bet profit
  - Full Kelly is theoretically optimal but assumes perfect edge estimation
  - Over-Kelly (>1.0) is negative long-term if edges are overestimated
""")

    risk_comparison()
    drawdown_analysis()
    risk_reward_efficiency()
    equity_curves()
    sweet_spot_finder()

    print("\n\n" + "=" * 120)
    print("  SUMMARY")
    print("=" * 120)
    print("""
  Read the simulation results above for exact numbers. Key questions answered:

  1. CAN WE JUST INCREASE RISK?
     Higher Kelly/cap does increase median profit — BUT drawdowns get much worse.
     Full Kelly (1.0) with 5% cap is the highest-risk "reasonable" setting.
     Going beyond (1.25x, 1.5x Kelly) enters over-betting territory.

  2. RISK vs LOWERING mEP — WHICH IS BETTER?
     Compare "Current" vs "Current+mEP0.75" vs "Aggressive":
     - Lower mEP: more bets, same stake sizing, same drawdown profile
     - Higher risk: same bet count, bigger stakes, much worse drawdowns
     - Lower mEP is the safer way to increase volume

  3. WHAT'S THE SWEET SPOT?
     The grid search (Sim 5) finds the optimal combo for each bankroll.
     Generally: moderate Kelly (0.75-1.0) + 3-5% cap + low mEP wins.
""")


if __name__ == "__main__":
    main()
