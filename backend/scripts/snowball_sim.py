"""
Bankroll Snowball Simulator (EV-Only, No Hedging/Arb)
=====================================================
Simulates optimal strategy to grow from minimum deposit to full provider coverage
using ONLY +EV value bets. No hedging, no arbitrage.

Models:
- Free bet as +EV straight bet (SNR = stake not returned, only profit on win)
- Trigger bets as +EV qualifying bets (real money, must meet min_odds)
- Bonus deposit wagering via +EV value bets
- Deposit sequencing to minimize capital at risk
- Monte Carlo for variance estimation

Key assumptions:
- Average value bet edge: 3-5% vs Pinnacle fair odds
- Free bets placed on best +EV opportunity (typically 2.00-3.50 odds)
- Trigger bets are real-money +EV bets at >= min_odds
- Bet frequency: ~5-10 qualifying bets per provider per day
"""

import random
import statistics
from dataclasses import dataclass, field
from typing import Optional


# --- Provider Bonus Data -------------------------------------------

@dataclass
class ProviderBonus:
    name: str
    bonus_type: str          # "freebet" or "bonusdeposit"
    deposit_required: float  # Minimum deposit to claim bonus
    bonus_amount: float      # Bonus received
    wagering_x: float        # Wagering multiplier
    min_odds: float          # Minimum odds for qualifying bets
    platform: str            # For grouping (kambi, altenar, etc.)
    # Derived
    wagering_req: float = 0  # Auto-calculated

    def __post_init__(self):
        self.wagering_req = self.bonus_amount * self.wagering_x
        if self.bonus_type == "freebet" and self.deposit_required == 0:
            self.deposit_required = self.bonus_amount


# All providers from providers.yaml
PROVIDERS = [
    # === FREE BETS (highest priority - 1x wagering) ===
    ProviderBonus("Lyllo",     "freebet",      100,   100, 1,  1.80, "comeon"),
    ProviderBonus("NordicBet", "freebet",      100,   100, 1,  1.80, "gecko"),
    ProviderBonus("Betsson",   "freebet",      250,   250, 1,  1.80, "gecko"),
    ProviderBonus("Hajper",    "freebet",      500,   500, 1,  1.80, "comeon"),
    ProviderBonus("MrGreen",   "freebet",      500,   500, 1,  1.80, "spectate"),
    ProviderBonus("Dbet",      "freebet",      500,   500, 1,  1.80, "altenar"),
    ProviderBonus("BetMGM",    "freebet",      500,   500, 1,  1.80, "kambi"),
    ProviderBonus("Unibet",    "freebet",     1000,  1000, 1,  1.80, "kambi"),

    # === BONUS DEPOSITS (lower priority - 5-20x wagering) ===
    ProviderBonus("Interwetten","bonusdeposit",1000, 1000, 5,  1.70, "interwetten"),
    ProviderBonus("LeoVegas",  "bonusdeposit", 600,  600, 6,  1.80, "kambi"),
    ProviderBonus("Betinia",   "bonusdeposit",1000, 1000, 6,  1.80, "altenar"),
    ProviderBonus("ComeOn",    "bonusdeposit", 500,  500, 6,  1.80, "comeon"),
    ProviderBonus("QuickCasino","bonusdeposit", 500,  500, 6,  1.80, "altenar"),
    ProviderBonus("CampoBet",  "bonusdeposit", 500,  500, 6,  1.80, "altenar"),
    ProviderBonus("Swiper",    "bonusdeposit",1000, 1000, 6,  1.50, "altenar"),
    ProviderBonus("Lodur",     "bonusdeposit",1000, 1000, 6,  1.80, "altenar"),
    ProviderBonus("Coolbet",   "bonusdeposit",1000, 1000, 6,  1.50, "coolbet"),
    ProviderBonus("Tipwin",    "bonusdeposit",1000, 1000, 7,  1.80, "tipwin"),
    ProviderBonus("10Bet",     "bonusdeposit",1000, 1000, 8,  1.80, "10bet"),
    ProviderBonus("Snabbare",  "bonusdeposit", 600,  600, 8,  1.80, "comeon"),
    ProviderBonus("Vbet",      "bonusdeposit", 800,  800, 10, 1.80, "vbet"),
    ProviderBonus("888sport",  "bonusdeposit", 500,  500, 1,  1.80, "spectate"),
    ProviderBonus("SpeedyBet", "bonusdeposit", 500,  500, 12, 1.80, "kambi"),
    ProviderBonus("X3000",     "bonusdeposit", 500,  500, 12, 1.80, "kambi"),
    ProviderBonus("GoldenBull","bonusdeposit", 500,  500, 12, 1.80, "kambi"),
    ProviderBonus("1X2",       "bonusdeposit", 500,  500, 12, 1.80, "kambi"),
    ProviderBonus("Spelklubben","bonusdeposit", 500,  500, 15, 1.90, "gecko"),
    ProviderBonus("Bethard",   "bonusdeposit", 500,  500, 15, 1.90, "gecko"),
    ProviderBonus("Expekt",    "bonusdeposit",1000, 1000, 20, 1.80, "kambi"),
]


# --- Simulation Parameters -----------------------------------------

@dataclass
class SimParams:
    # --- Trigger bet parameters ---
    # Goal: preserve capital while clearing requirement. Low odds = high win prob.
    # Best picks: spread/total at ~1.90, 1x2 favorites at ~1.80-2.10
    trigger_odds_mean: float = 1.95           # Low odds favorites / spread-total lines
    trigger_odds_std: float = 0.15            # Tight range near 1.80-2.10
    trigger_edge_mean: float = 3.5            # Average edge on trigger bets
    trigger_edge_std: float = 2.0

    # --- Free bet (SNR) parameters ---
    # Goal: maximize (odds-1) * win_prob. Higher odds = more upside, same downside (0).
    # Best picks: underdogs/draws with edge at 2.50-4.00+
    freebet_odds_mean: float = 3.20           # Target high odds for SNR value
    freebet_odds_std: float = 0.70            # Wider range, picking best opportunity
    freebet_edge_mean: float = 5.0            # Can be pickier (wait for best spot)
    freebet_edge_std: float = 2.5

    # --- Bonus wagering parameters ---
    # All markets available. Mix of spreads/totals near 1.90 and 1x2 picks.
    avg_edge_pct: float = 3.5                 # Average edge on qualifying bets
    edge_std: float = 2.0                     # Edge variance
    avg_odds: float = 2.10                    # Slightly lower avg (lots of spread/total)
    odds_std: float = 0.35                    # Odds variance

    # Frequency
    bets_per_day: int = 8                     # Qualifying bets per day per provider

    # Kelly & risk
    kelly_base: float = 0.25                  # Quarter Kelly for low edge
    kelly_max: float = 0.75                   # Max Kelly for high edge
    max_stake_pct: float = 0.03               # 3% single bet cap
    min_stake: float = 25                     # Minimum bet size

    # --- Phase 3: Post-bonus pure EV grinding ---
    # All bonuses cleared, no min_odds restrictions, Kelly autostake
    grind_edge_mean: float = 4.0              # Average edge (can be pickier post-bonus)
    grind_edge_std: float = 2.0
    grind_odds_mean: float = 2.15             # Mix of all markets
    grind_odds_std: float = 0.40
    grind_bets_per_day: int = 10              # Bets placed per day across all providers
    grind_days: int = 365                     # How many days to simulate post-bonus

    # Ruin
    ruin_threshold: float = 50                # Stop if bankroll < this

    # Event exposure cap (same as production)
    max_event_exposure_pct: float = 0.05      # 5% max on any single event

    # Bookmaker limits (reality check)
    max_stake_absolute: float = 5000          # Swedish books cap ~2-5k per bet
    bankroll_ceiling: float = 0               # 0 = no ceiling (Kelly grows forever)


@dataclass
class SimState:
    bankroll: float = 0.0
    total_deposited: float = 0.0
    provider_balances: dict = field(default_factory=dict)
    day: float = 0
    total_bets: int = 0
    total_profit: float = 0.0
    wins: int = 0
    losses: int = 0


def kelly_fraction(edge_pct: float, params: SimParams) -> float:
    """Dynamic Kelly scaling by edge size."""
    edge = edge_pct / 100
    if edge <= 0:
        return 0
    if edge <= 0.02:
        return params.kelly_base
    if edge >= 0.06:
        return params.kelly_max
    # Linear interpolation 2%-6%
    t = (edge - 0.02) / 0.04
    return params.kelly_base + t * (params.kelly_max - params.kelly_base)


# --- Free Bet Phase Simulation ------------------------------------

def simulate_freebet_provider(provider: ProviderBonus, state: SimState,
                               params: SimParams) -> dict:
    """
    Simulate free bet provider: trigger bet + free bet, both as straight +EV.

    BOTH trigger and freebet stakes are FIXED at provider.bonus_amount.
    E.g. Unibet 1000kr = 1000kr trigger + 1000kr freebet.
         Lyllo 100kr = 100kr trigger + 100kr freebet.

    Flow:
    1. Deposit bonus_amount to provider
    2. Place trigger bet: real money, FIXED stake = bonus_amount, +EV, >= min_odds
    3. Wait for trigger to settle (trigger win/lose doesn't affect freebet unlock)
    4. Place freebet: SNR, FIXED stake = bonus_amount, +EV, >= min_odds
    5. Withdraw remaining balance
    """
    deposit = provider.deposit_required      # = bonus_amount for freebets
    fixed_stake = provider.bonus_amount      # Both trigger and freebet are this amount

    # --- Trigger bet (real money, fixed stake, +EV, LOW ODDS for safety) ---
    # Pick spread/total or favorite at ~1.90-2.10 to maximize win probability
    trigger_edge = max(0.5, random.gauss(params.trigger_edge_mean, params.trigger_edge_std))
    trigger_odds = max(provider.min_odds, random.gauss(params.trigger_odds_mean, params.trigger_odds_std))

    trigger_win_prob = (1.0 / trigger_odds) * (1 + trigger_edge / 100)
    trigger_won = random.random() < trigger_win_prob
    if trigger_won:
        trigger_pnl = fixed_stake * (trigger_odds - 1)
    else:
        trigger_pnl = -fixed_stake

    # Balance at provider after trigger settles
    provider_balance = deposit + trigger_pnl

    state.total_bets += 1
    state.day += 1

    # --- Free bet (SNR, fixed stake, +EV) ---
    fb_edge = max(1.0, random.gauss(params.freebet_edge_mean, params.freebet_edge_std))
    fb_odds = max(provider.min_odds, random.gauss(params.freebet_odds_mean, params.freebet_odds_std))

    fb_win_prob = (1.0 / fb_odds) * (1 + fb_edge / 100)
    fb_won = random.random() < fb_win_prob
    if fb_won:
        fb_pnl = (fb_odds - 1) * fixed_stake  # SNR: only profit, stake not returned
    else:
        fb_pnl = 0  # Free bet lost = 0 cost

    provider_balance += fb_pnl
    state.total_bets += 1
    state.day += 1

    # --- Withdraw ---
    net_profit = provider_balance - deposit

    state.bankroll += net_profit
    state.total_profit += net_profit
    if trigger_won:
        state.wins += 1
    else:
        state.losses += 1
    if fb_won:
        state.wins += 1
    else:
        state.losses += 1

    return {
        "provider": provider.name,
        "type": "freebet",
        "deposit": deposit,
        "fixed_stake": fixed_stake,
        "trigger_odds": round(trigger_odds, 2),
        "trigger_edge": round(trigger_edge, 1),
        "trigger_won": trigger_won,
        "trigger_pnl": round(trigger_pnl, 0),
        "freebet_odds": round(fb_odds, 2),
        "freebet_edge": round(fb_edge, 1),
        "freebet_won": fb_won,
        "freebet_pnl": round(fb_pnl, 0),
        "net_profit": round(net_profit, 0),
        "bankroll_after": round(state.bankroll, 0),
        "day": round(state.day, 1),
    }


# --- Bonus Deposit Phase Simulation --------------------------------

def simulate_bonus_provider(provider: ProviderBonus, state: SimState,
                             params: SimParams) -> dict:
    """
    Simulate bonus deposit clearing via +EV value bets.

    Flow:
    1. Deposit to provider
    2. Place +EV bets at >= min_odds until wagering requirement met
    3. Bonus amount becomes withdrawable after clearing
    4. Withdraw everything
    """
    deposit = provider.deposit_required
    bonus = provider.bonus_amount
    wagering_req = provider.wagering_req
    min_odds = provider.min_odds

    # Balance at provider = deposit + bonus (bonus locked until cleared)
    provider_balance = deposit + bonus
    wagered = 0.0
    bets = 0
    bet_pnl = 0.0
    wins = 0
    losses = 0

    while wagered < wagering_req:
        # Don't bet if we've lost everything at this provider
        if provider_balance < params.min_stake:
            break

        # Sample +EV opportunity
        edge_pct = max(0.5, random.gauss(params.avg_edge_pct, params.edge_std))
        odds = max(min_odds, random.gauss(params.avg_odds, params.odds_std))

        # Kelly-based stake on provider balance
        edge = edge_pct / 100
        kf = kelly_fraction(edge_pct, params)
        raw_stake = provider_balance * kf * edge / (odds - 1)
        max_s = provider_balance * params.max_stake_pct
        stake = min(raw_stake, max_s, provider_balance * 0.10)
        stake = max(params.min_stake, stake)
        stake = min(stake, provider_balance)

        if stake < params.min_stake:
            break

        # Simulate outcome
        win_prob = (1.0 / odds) * (1 + edge)
        won = random.random() < win_prob

        if won:
            pnl = stake * (odds - 1)
            wins += 1
        else:
            pnl = -stake
            losses += 1

        provider_balance += pnl
        bet_pnl += pnl
        wagered += stake
        bets += 1

        if bets > 500:
            break

    days_to_clear = bets / params.bets_per_day

    # Net result: deposited `deposit`, get back `provider_balance`
    # (bonus was free money, now part of balance if not lost)
    net_profit = provider_balance - deposit

    state.bankroll += net_profit
    state.total_profit += net_profit
    state.total_bets += bets
    state.wins += wins
    state.losses += losses
    state.day += days_to_clear

    return {
        "provider": provider.name,
        "type": "bonusdeposit",
        "deposit": deposit,
        "bonus": bonus,
        "wagering_x": provider.wagering_x,
        "wagering_req": wagering_req,
        "wagered": round(wagered, 0),
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "bet_pnl": round(bet_pnl, 0),
        "net_profit": round(net_profit, 0),
        "days": round(days_to_clear, 1),
        "bankroll_after": round(state.bankroll, 0),
        "day": round(state.day, 1),
        "busted": provider_balance < params.min_stake and wagered < wagering_req,
    }


# --- Phase 3: Post-Bonus EV Grinding --------------------------------

def simulate_grind_phase(state: SimState, params: SimParams) -> dict:
    """
    Simulate post-bonus pure EV grinding with Kelly autostaking.

    All bonuses cleared. No min_odds restrictions. No wagering requirements.
    Just find +EV bets across all 29 providers and Kelly-size them.

    Returns weekly snapshots for trajectory analysis.
    """
    starting_bankroll = state.bankroll
    weekly_snapshots = []
    peak_bankroll = state.bankroll
    max_drawdown_pct = 0.0
    bets_total = 0
    wins_total = 0
    losses_total = 0
    daily_profits = []

    for day in range(params.grind_days):
        day_profit = 0.0
        day_bets = 0

        # Number of bets varies day to day
        n_bets = max(1, int(random.gauss(params.grind_bets_per_day, 2)))

        for _ in range(n_bets):
            if state.bankroll < params.ruin_threshold:
                break

            # Sample +EV opportunity
            edge_pct = max(0.5, random.gauss(params.grind_edge_mean, params.grind_edge_std))
            odds = max(1.50, random.gauss(params.grind_odds_mean, params.grind_odds_std))

            # Kelly-sized stake
            edge = edge_pct / 100
            kf = kelly_fraction(edge_pct, params)
            raw_stake = state.bankroll * kf * edge / (odds - 1)

            # Caps: 3% single bet, 5% event exposure
            max_s = state.bankroll * params.max_stake_pct
            event_cap = state.bankroll * params.max_event_exposure_pct
            stake = min(raw_stake, max_s, event_cap)
            stake = max(params.min_stake, stake)

            # Round to natural amounts (25 kr increments)
            stake = round(stake / 25) * 25
            stake = max(25, min(stake, state.bankroll))

            if stake < params.min_stake or stake > state.bankroll:
                continue

            # Simulate outcome
            win_prob = (1.0 / odds) * (1 + edge)
            won = random.random() < win_prob

            if won:
                pnl = stake * (odds - 1)
                wins_total += 1
            else:
                pnl = -stake
                losses_total += 1

            state.bankroll += pnl
            day_profit += pnl
            bets_total += 1
            day_bets += 1

        daily_profits.append(day_profit)
        state.day += 1
        state.total_bets += day_bets

        # Track peak and drawdown
        if state.bankroll > peak_bankroll:
            peak_bankroll = state.bankroll
        dd = (peak_bankroll - state.bankroll) / peak_bankroll if peak_bankroll > 0 else 0
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

        # Weekly snapshot (every 7 days)
        if (day + 1) % 7 == 0:
            week_num = (day + 1) // 7
            weekly_snapshots.append({
                "week": week_num,
                "day": day + 1,
                "bankroll": round(state.bankroll, 0),
                "profit_this_week": round(sum(daily_profits[-7:]), 0),
                "total_profit": round(state.bankroll - starting_bankroll, 0),
                "bets_this_week": bets_total - (weekly_snapshots[-1]["cumulative_bets"] if weekly_snapshots else 0),
                "cumulative_bets": bets_total,
                "max_drawdown_pct": round(100 * max_drawdown_pct, 1),
            })

        if state.bankroll < params.ruin_threshold:
            break

    total_grind_profit = state.bankroll - starting_bankroll
    state.total_profit += total_grind_profit
    state.wins += wins_total
    state.losses += losses_total

    return {
        "starting_bankroll": round(starting_bankroll, 0),
        "final_bankroll": round(state.bankroll, 0),
        "grind_profit": round(total_grind_profit, 0),
        "days": len(daily_profits),
        "total_bets": bets_total,
        "wins": wins_total,
        "losses": losses_total,
        "win_rate": round(100 * wins_total / max(bets_total, 1), 1),
        "max_drawdown_pct": round(100 * max_drawdown_pct, 1),
        "peak_bankroll": round(peak_bankroll, 0),
        "weekly_snapshots": weekly_snapshots,
        "ruined": state.bankroll < params.ruin_threshold,
        "avg_daily_profit": round(statistics.mean(daily_profits), 0) if daily_profits else 0,
        "daily_profit_std": round(statistics.stdev(daily_profits), 0) if len(daily_profits) > 1 else 0,
    }


# --- Full Snowball Simulation --------------------------------------

def simulate_snowball(starting_capital: float, n_sims: int = 1000,
                      params: Optional[SimParams] = None,
                      include_grind: bool = False) -> dict:
    """
    Full Monte Carlo simulation of the EV-only snowball strategy.

    Order:
    1. Cheapest free bets first (low deposit, +EV trigger + free bet)
    2. Easy bonus deposits (low wagering_x)
    3. Harder bonus deposits (high wagering_x)
    4. (Optional) Post-bonus pure EV grinding with Kelly autostake
    """
    if params is None:
        params = SimParams()

    freebets = sorted(
        [p for p in PROVIDERS if p.bonus_type == "freebet"],
        key=lambda p: p.deposit_required
    )
    deposits = sorted(
        [p for p in PROVIDERS if p.bonus_type == "bonusdeposit"],
        key=lambda p: (p.wagering_x, p.deposit_required)
    )

    all_results = []

    for sim in range(n_sims):
        state = SimState(bankroll=starting_capital, total_deposited=starting_capital)
        completed = []
        skipped = []

        # -- Phase 1: Free Bets --
        for provider in freebets:
            if state.bankroll < provider.deposit_required:
                skipped.append((provider.name, provider.deposit_required))
                continue

            state.bankroll -= provider.deposit_required  # Lock deposit
            result = simulate_freebet_provider(provider, state, params)
            completed.append(result)

            if state.bankroll < params.ruin_threshold:
                break

        # -- Phase 2: Bonus Deposits --
        if state.bankroll >= params.ruin_threshold:
            for provider in deposits:
                if state.bankroll < provider.deposit_required:
                    skipped.append((provider.name, provider.deposit_required))
                    continue

                state.bankroll -= provider.deposit_required  # Lock deposit
                result = simulate_bonus_provider(provider, state, params)
                completed.append(result)

                if state.bankroll < params.ruin_threshold:
                    break

        # -- Phase 3: Post-bonus EV grinding (if enabled) --
        grind_result = None
        bankroll_after_bonuses = state.bankroll
        if include_grind and state.bankroll >= params.ruin_threshold:
            grind_result = simulate_grind_phase(state, params)

        all_results.append({
            "final_bankroll": round(state.bankroll, 0),
            "total_deposited": starting_capital,
            "total_profit": round(state.total_profit, 0),
            "total_bets": state.total_bets,
            "wins": state.wins,
            "losses": state.losses,
            "days": round(state.day, 1),
            "completed": len(completed),
            "skipped": len(skipped),
            "completed_list": completed,
            "skipped_list": skipped,
            "ruined": state.bankroll < params.ruin_threshold,
            "bankroll_after_bonuses": round(bankroll_after_bonuses, 0),
            "grind_result": grind_result,
        })

    return {"starting_capital": starting_capital, "n_sims": n_sims, "results": all_results}


def analyze_results(sim_output: dict) -> dict:
    """Compute summary statistics from simulation results."""
    results = sim_output["results"]
    starting = sim_output["starting_capital"]
    n = len(results)

    finals = sorted([r["final_bankroll"] for r in results])
    profits = sorted([r["total_profit"] for r in results])
    days = sorted([r["days"] for r in results])
    completed = sorted([r["completed"] for r in results])
    ruined = sum(1 for r in results if r["ruined"])
    win_rates = [r["wins"] / max(r["wins"] + r["losses"], 1) for r in results]

    return {
        "starting_capital": starting,
        "n_sims": n,
        "ruin_pct": round(100 * ruined / n, 1),
        "final_bankroll": {
            "mean": round(statistics.mean(finals), 0),
            "median": round(statistics.median(finals), 0),
            "p5": round(finals[int(n * 0.05)], 0),
            "p10": round(finals[int(n * 0.10)], 0),
            "p25": round(finals[int(n * 0.25)], 0),
            "p75": round(finals[int(n * 0.75)], 0),
            "p90": round(finals[int(n * 0.90)], 0),
            "p95": round(finals[int(n * 0.95)], 0),
            "min": round(min(finals), 0),
            "max": round(max(finals), 0),
        },
        "total_profit": {
            "mean": round(statistics.mean(profits), 0),
            "median": round(statistics.median(profits), 0),
            "p5": round(profits[int(n * 0.05)], 0),
            "p10": round(profits[int(n * 0.10)], 0),
            "p90": round(profits[int(n * 0.90)], 0),
            "p95": round(profits[int(n * 0.95)], 0),
        },
        "days_to_complete": {
            "mean": round(statistics.mean(days), 1),
            "median": round(statistics.median(days), 1),
        },
        "providers_completed": {
            "mean": round(statistics.mean(completed), 1),
            "median": statistics.median(completed),
            "min": min(completed),
            "max": max(completed),
        },
        "win_rate_pct": round(100 * statistics.mean(win_rates), 1),
        "roi_pct": round(100 * statistics.mean(profits) / starting, 1),
    }


# --- Output Functions -----------------------------------------------

def run_minimum_viable_start():
    """Test different starting capitals."""
    print(f"\n{'='*90}")
    print(f"  MINIMUM VIABLE STARTING CAPITAL (EV-only, no hedging)")
    print(f"  Monte Carlo: 3000 sims per amount")
    print(f"{'='*90}\n")

    header = (f"  {'Start':>8}  {'Median':>9}  {'P5 bad':>9}  {'P95 good':>10}  "
              f"{'Ruin%':>6}  {'Providers':>10}  {'Days':>6}  {'WinRate':>8}")
    print(header)
    print(f"  {'-'*86}")

    for capital in [100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000]:
        sim = simulate_snowball(capital, n_sims=3000)
        a = analyze_results(sim)

        print(f"  {capital:>7,} kr  "
              f"{a['total_profit']['median']:>+8,.0f} kr  "
              f"{a['total_profit']['p5']:>+8,.0f} kr  "
              f"{a['total_profit']['p95']:>+9,.0f} kr  "
              f"{a['ruin_pct']:>5.1f}%  "
              f"{a['providers_completed']['median']:>5.0f}/{len(PROVIDERS)}  "
              f"{a['days_to_complete']['median']:>5.0f}d  "
              f"{a['win_rate_pct']:>6.1f}%")


def run_scenario_comparison():
    """Compare different edge/variance scenarios."""
    print(f"\n{'='*90}")
    print(f"  SCENARIO COMPARISON (starting 1,000 kr, 3000 sims each)")
    print(f"{'='*90}\n")

    scenarios = {
        "Conservative (2.5% edge)":  SimParams(avg_edge_pct=2.5, edge_std=1.5, freebet_edge_mean=3.5, kelly_max=0.50),
        "Baseline (3.5% edge)":      SimParams(),
        "Good scanner (5% edge)":    SimParams(avg_edge_pct=5.0, edge_std=2.5, freebet_edge_mean=6.0, kelly_max=0.75),
        "High variance":             SimParams(avg_edge_pct=3.5, edge_std=3.5, freebet_edge_mean=5.0, odds_std=0.60),
        "Low frequency (4/day)":     SimParams(bets_per_day=4),
        "High frequency (12/day)":   SimParams(bets_per_day=12),
    }

    header = (f"  {'Scenario':<27} {'Median':>9} {'P5 bad':>9} {'P95 good':>10} "
              f"{'Ruin%':>6} {'Days':>6} {'Providers':>5}")
    print(header)
    print(f"  {'-'*80}")

    for name, params in scenarios.items():
        sim = simulate_snowball(1000, n_sims=3000, params=params)
        a = analyze_results(sim)

        print(f"  {name:<27} "
              f"{a['total_profit']['median']:>+8,.0f} kr "
              f"{a['total_profit']['p5']:>+8,.0f} kr "
              f"{a['total_profit']['p95']:>+9,.0f} kr "
              f"{a['ruin_pct']:>5.1f}% "
              f"{a['days_to_complete']['median']:>5.0f}d "
              f"{a['providers_completed']['median']:>5.0f}/{len(PROVIDERS)}")


def run_trigger_vs_freebet_odds_analysis():
    """Show why trigger bets want low odds and freebets want high odds."""
    print(f"\n{'='*90}")
    print(f"  TRIGGER vs FREEBET: OPTIMAL ODDS SELECTION")
    print(f"  All markets: 1x2, moneyline, spread, total")
    print(f"{'='*90}\n")

    # Trigger bet: real money, stake returned on win
    print(f"  TRIGGER BET (real money, stake returned on win)")
    print(f"  Goal: clear requirement with minimum variance")
    print(f"  {'-'*70}")
    print(f"  {'Odds':>6} {'WinProb':>8} {'Edge':>6} {'EV/bet':>8} {'StdDev':>8} {'Sharpe':>7} {'Best Market':>20}")
    print(f"  {'-'*70}")

    for odds, edge, market in [
        (1.85, 3.5, "spread/total"),
        (1.95, 3.5, "spread/total"),
        (2.10, 4.0, "1x2 favorite"),
        (2.50, 5.0, "1x2 draw/underdog"),
        (3.00, 5.0, "1x2 underdog"),
        (3.50, 6.0, "1x2 underdog"),
    ]:
        fair_odds = odds / (1 + edge / 100)
        win_prob = 1 / fair_odds
        stake = 500  # example
        ev = stake * edge / 100
        # Variance of a single bet
        win_payout = stake * (odds - 1)
        loss_payout = -stake
        var = win_prob * (win_payout ** 2) + (1 - win_prob) * (loss_payout ** 2) - ev ** 2
        std = var ** 0.5
        sharpe = ev / std if std > 0 else 0

        print(f"  {odds:>5.2f}  {win_prob:>6.1%}  {edge:>4.1f}%  {ev:>+7.0f} kr  {std:>7.0f} kr  {sharpe:>6.3f}  {market:>20}")

    print(f"\n  >> Lower odds = same EV per edge %, but MUCH lower variance (higher Sharpe)")
    print(f"  >> Spread/total at 1.85-1.95 is ideal for triggers")

    # Free bet: SNR, stake NOT returned
    print(f"\n  FREE BET (SNR: stake NOT returned, only profit on win)")
    print(f"  Goal: maximize expected payout")
    print(f"  {'-'*70}")
    print(f"  {'Odds':>6} {'WinProb':>8} {'Edge':>6} {'EV(SNR)':>9} {'P(win)xPay':>11} {'Best Market':>20}")
    print(f"  {'-'*70}")

    for odds, edge, market in [
        (1.85, 3.5, "spread/total"),
        (1.95, 3.5, "spread/total"),
        (2.10, 4.0, "1x2 favorite"),
        (2.50, 5.0, "1x2 draw/underdog"),
        (3.00, 5.0, "1x2 underdog"),
        (3.50, 6.0, "1x2 underdog"),
        (4.00, 7.0, "1x2 big underdog"),
    ]:
        fair_odds = odds / (1 + edge / 100)
        win_prob = 1 / fair_odds
        stake = 500  # example freebet amount
        # SNR: win pays (odds-1)*stake, lose pays 0
        snr_ev = win_prob * (odds - 1) * stake
        win_payout = (odds - 1) * stake

        print(f"  {odds:>5.2f}  {win_prob:>6.1%}  {edge:>4.1f}%  {snr_ev:>+8.0f} kr  {win_prob:.1%} x {win_payout:>5,.0f} kr  {market:>20}")

    print(f"\n  >> Higher odds = higher EV for SNR freebets!")
    print(f"  >> At same edge %, a 3.50 freebet is worth ~2x a 1.85 freebet")
    print(f"  >> Underdogs/draws with edge are the play for freebets")


def run_freebet_ev_analysis():
    """Analyze free bet EV without hedging."""
    print(f"\n{'='*90}")
    print(f"  FREE BET EV ANALYSIS (straight +EV bets, no hedging)")
    print(f"{'='*90}\n")

    freebets = sorted(
        [p for p in PROVIDERS if p.bonus_type == "freebet"],
        key=lambda p: p.deposit_required
    )

    params = SimParams()
    n_sims = 10000

    print(f"  Simulating {n_sims} runs per provider...\n")
    print(f"  {'Provider':<12} {'Deposit':>8} {'Bonus':>7}  "
          f"{'EV(median)':>11} {'EV(P10)':>9} {'EV(P90)':>9}  "
          f"{'Win%':>6} {'AvgDays':>8}")
    print(f"  {'-'*82}")

    for p in freebets:
        results = []
        for _ in range(n_sims):
            state = SimState(bankroll=p.deposit_required * 2)  # Enough to cover
            state.bankroll -= p.deposit_required
            r = simulate_freebet_provider(p, state, params)
            results.append(r["net_profit"])

        results.sort()
        median = statistics.median(results)
        p10 = results[int(n_sims * 0.10)]
        p90 = results[int(n_sims * 0.90)]
        # Win = any positive result
        win_pct = 100 * sum(1 for r in results if r > 0) / n_sims

        # Expected value = mean (theoretical)
        # For SNR freebet at odds o with edge e:
        #   EV_freebet = (o-1) * p.bonus * win_prob
        #   EV_trigger = trigger_stake * edge (small positive)
        # Combined EV per provider cycle

        print(f"  {p.name:<12} {p.deposit_required:>7,.0f} kr {p.bonus_amount:>6,.0f} kr  "
              f"{median:>+10,.0f} kr {p10:>+8,.0f} kr {p90:>+8,.0f} kr  "
              f"{win_pct:>5.1f}% {'2d':>8}")

    # Sequential simulation from 500 kr
    print(f"\n  SEQUENTIAL PATH from 500 kr (10,000 sims):")
    print(f"  {'-'*70}")

    path_results = []
    for _ in range(n_sims):
        bankroll = 500
        path = []
        for p in freebets:
            if bankroll < p.deposit_required:
                path.append((p.name, "SKIP", bankroll))
                continue
            state = SimState(bankroll=bankroll)
            state.bankroll -= p.deposit_required
            r = simulate_freebet_provider(p, state, params)
            bankroll = state.bankroll
            path.append((p.name, r["net_profit"], bankroll))
        path_results.append((bankroll, path))

    finals = sorted([pr[0] for pr in path_results])
    median_final = statistics.median(finals)
    p10_final = finals[int(n_sims * 0.10)]
    p90_final = finals[int(n_sims * 0.90)]

    # Show one example (median-ish) path
    # Find the run closest to median
    target = median_final
    best_run = min(path_results, key=lambda x: abs(x[0] - target))

    for name, profit, bal in best_run[1]:
        if profit == "SKIP":
            print(f"  [--] {name:<12} SKIP (need more, have {bal:,.0f} kr)")
        else:
            status = "[OK]" if profit >= 0 else "[!!]"
            print(f"  {status} {name:<12} {profit:>+7,.0f} kr -> bankroll: {bal:>7,.0f} kr")

    print(f"\n  After free bets (from 500 kr start):")
    print(f"    Median: {median_final:>+,.0f} kr  |  P10: {p10_final:>+,.0f} kr  |  P90: {p90_final:>+,.0f} kr")
    blocked = sum(1 for pr in path_results if any(p[1] == "SKIP" for p in pr[1]))
    print(f"    Runs where at least one freebet was skipped: {100*blocked/n_sims:.1f}%")


def run_bonus_deposit_ev_analysis():
    """
    Show the guaranteed scaling from bonus deposits.

    Each bonus deposit adds GUARANTEED capital (deposit + bonus) to work with.
    The EV from wagering is ON TOP of the guaranteed bonus.
    This is why bonus deposits are the safe scaling engine.
    """
    print(f"\n{'='*90}")
    print(f"  BONUS DEPOSIT: GUARANTEED CAPITAL SCALING")
    print(f"  Each deposit adds bonus money you bet with. EV wagering is extra profit.")
    print(f"{'='*90}")

    params = SimParams()
    n_sims = 10000

    deposits = sorted(
        [p for p in PROVIDERS if p.bonus_type == "bonusdeposit"],
        key=lambda p: (p.wagering_x, p.deposit_required)
    )

    print(f"\n  THEORETICAL EV PER PROVIDER (bonus is guaranteed, wagering adds +EV):")
    print(f"  {'-'*95}")
    print(f"  {'Provider':<14} {'Deposit':>8} {'Bonus':>7} {'WagerX':>7} {'WagerReq':>9} "
          f"{'EV(wager)':>10} {'Total EV':>9} {'ROI':>6} {'BustRisk':>9}")
    print(f"  {'-'*95}")

    total_deposit_needed = 0
    total_bonus = 0
    total_ev = 0

    for p in deposits:
        # EV from wagering = wagering_req * avg_edge_pct / 100
        # This is the expected extra profit from placing +EV bets
        wager_ev = p.wagering_req * params.avg_edge_pct / 100

        # Total EV = bonus + wagering EV (you get the bonus + extra from +EV bets)
        total_provider_ev = p.bonus_amount + wager_ev

        # ROI = total_ev / deposit
        roi = 100 * total_provider_ev / p.deposit_required

        # Simulate bust risk (losing provider balance below min_stake before clearing)
        busts = 0
        for _ in range(n_sims):
            s = SimState(bankroll=p.deposit_required * 3)
            s.bankroll -= p.deposit_required
            r = simulate_bonus_provider(p, s, params)
            if r.get("busted"):
                busts += 1
        bust_pct = 100 * busts / n_sims

        total_deposit_needed += p.deposit_required
        total_bonus += p.bonus_amount
        total_ev += total_provider_ev

        print(f"  {p.name:<14} {p.deposit_required:>7,.0f} kr {p.bonus_amount:>6,.0f} kr "
              f"{p.wagering_x:>5}x {p.wagering_req:>8,.0f} kr "
              f"{wager_ev:>+9,.0f} kr {total_provider_ev:>+8,.0f} kr "
              f"{roi:>5.0f}% {bust_pct:>7.1f}%")

    total_wager_ev = sum(p.wagering_req * params.avg_edge_pct / 100 for p in deposits)

    print(f"  {'-'*95}")
    print(f"  {'TOTAL':<14} {total_deposit_needed:>7,.0f} kr {total_bonus:>6,.0f} kr "
          f"{'':>7} {'':>9} "
          f"{total_wager_ev:>+9,.0f} kr {total_ev:>+8,.0f} kr "
          f"{100 * total_ev / total_deposit_needed:>5.0f}%")

    print(f"\n  KEY INSIGHT:")
    print(f"    Total deposits needed:     {total_deposit_needed:>10,.0f} kr (recycled, not lost)")
    print(f"    Guaranteed bonus capital:   {total_bonus:>+10,.0f} kr (free money)")
    print(f"    Expected wagering profit:   {total_wager_ev:>+10,.0f} kr (from +EV bets)")
    print(f"    Total expected value:       {total_ev:>+10,.0f} kr")
    print(f"    You DON'T need {total_deposit_needed:,.0f} kr upfront -- you recycle from each provider")

    # Show the sequential capital flow
    print(f"\n  SEQUENTIAL CAPITAL FLOW (how much cash you actually need):")
    print(f"  {'-'*80}")
    print(f"  {'Provider':<14} {'Need':>8} {'You Have':>9} {'After':>9} {'Gained':>8} {'Cumulative':>11}")
    print(f"  {'-'*80}")

    # Use median outcomes from simulation
    bankroll = 3000  # start assumption
    cumulative_gained = 0

    # First do freebets
    freebets = sorted(
        [p for p in PROVIDERS if p.bonus_type == "freebet"],
        key=lambda p: p.deposit_required
    )

    random.seed(42)
    print(f"\n  -- PHASE 1: Free Bets --")
    for p in freebets:
        if bankroll < p.deposit_required:
            print(f"  {p.name:<14} {p.deposit_required:>7,.0f} kr {bankroll:>8,.0f} kr  ** SKIP **")
            continue
        before = bankroll
        state = SimState(bankroll=bankroll)
        state.bankroll -= p.deposit_required
        simulate_freebet_provider(p, state, params)
        bankroll = state.bankroll
        gained = bankroll - before
        cumulative_gained += gained
        print(f"  {p.name:<14} {p.deposit_required:>7,.0f} kr {before:>8,.0f} kr "
              f"{bankroll:>8,.0f} kr {gained:>+7,.0f} kr {cumulative_gained:>+10,.0f} kr")

    print(f"\n  -- PHASE 2: Bonus Deposits (guaranteed bonus + EV wagering) --")
    for p in deposits:
        if bankroll < p.deposit_required:
            print(f"  {p.name:<14} {p.deposit_required:>7,.0f} kr {bankroll:>8,.0f} kr  ** SKIP **")
            continue
        before = bankroll
        state = SimState(bankroll=bankroll)
        state.bankroll -= p.deposit_required
        simulate_bonus_provider(p, state, params)
        bankroll = state.bankroll
        gained = bankroll - before
        cumulative_gained += gained
        print(f"  {p.name:<14} {p.deposit_required:>7,.0f} kr {before:>8,.0f} kr "
              f"{bankroll:>8,.0f} kr {gained:>+7,.0f} kr {cumulative_gained:>+10,.0f} kr")

    print(f"\n  Final bankroll: {bankroll:,.0f} kr  |  Total gained: {cumulative_gained:+,.0f} kr")


def show_detailed_sequence(starting_capital: float):
    """Show one detailed run with annotations."""
    print(f"\n{'='*90}")
    print(f"  DETAILED SEQUENCE -- Starting with {starting_capital:,.0f} kr (seed=42)")
    print(f"{'='*90}")

    params = SimParams()
    random.seed(42)
    sim = simulate_snowball(starting_capital, n_sims=1, params=params)
    run = sim["results"][0]

    # Phase 1: Free bets
    fb_steps = [s for s in run["completed_list"] if s["type"] == "freebet"]
    bd_steps = [s for s in run["completed_list"] if s["type"] == "bonusdeposit"]

    if fb_steps:
        print(f"\n  PHASE 1: FREE BETS (trigger + freebet, both +EV)")
        print(f"  {'-'*85}")
        print(f"  {'Provider':<12} {'Trigger':>10} {'':>4} {'FreeBet':>10} {'':>4} {'Net':>8} {'Bankroll':>10} {'Day':>5}")
        print(f"  {'-'*85}")

        for s in fb_steps:
            t_result = "W" if s["trigger_won"] else "L"
            f_result = "W" if s["freebet_won"] else "L"
            t_str = f"{s['trigger_pnl']:>+,}({t_result})"
            f_str = f"{s['freebet_pnl']:>+,}({f_result})"
            print(f"  {s['provider']:<12} {t_str:>10} @{s['trigger_odds']:.2f} "
                  f"{f_str:>10} @{s['freebet_odds']:.2f} "
                  f"{s['net_profit']:>+7,.0f} kr "
                  f"{s['bankroll_after']:>9,.0f} kr "
                  f"{s['day']:>5.0f}")

    if bd_steps:
        print(f"\n  PHASE 2: BONUS DEPOSITS (+EV wagering to clear)")
        print(f"  {'-'*85}")
        print(f"  {'Provider':<12} {'Bonus':>7} {'WagerX':>7} {'Bets':>5} {'W/L':>7} "
              f"{'BetP&L':>8} {'Net':>8} {'Bankroll':>10} {'Days':>5}")
        print(f"  {'-'*85}")

        for s in bd_steps:
            busted = " BUST" if s.get("busted") else ""
            print(f"  {s['provider']:<12} {s['bonus']:>6,.0f} kr {s['wagering_x']:>5}x "
                  f"{s['bets']:>5} {s['wins']:>3}/{s['losses']:>3} "
                  f"{s['bet_pnl']:>+7,.0f} kr "
                  f"{s['net_profit']:>+7,.0f} kr "
                  f"{s['bankroll_after']:>9,.0f} kr "
                  f"{s['days']:>4.0f}d{busted}")

    if run["skipped_list"]:
        print(f"\n  SKIPPED:")
        for name, needed in run["skipped_list"]:
            print(f"    {name}: needed {needed:,.0f} kr")

    print(f"\n  {'-'*85}")
    print(f"  RESULT: {run['total_profit']:>+,.0f} kr profit | "
          f"{run['final_bankroll']:>,.0f} kr final | "
          f"{run['total_bets']} bets ({run['wins']}W/{run['losses']}L) | "
          f"{run['days']:.0f} days")
    print(f"  ROI: {100 * run['total_profit'] / starting_capital:.0f}%")


def run_bankroll_trajectory():
    """Show percentile bankroll trajectories over time."""
    print(f"\n{'='*90}")
    print(f"  BANKROLL TRAJECTORY (1,000 kr start, 5000 sims)")
    print(f"{'='*90}\n")

    params = SimParams()
    n_sims = 5000
    sim = simulate_snowball(1000, n_sims=n_sims, params=params)

    # Collect (day, bankroll) snapshots from all runs
    # We'll bucket by provider completion count
    by_providers = {}
    for r in sim["results"]:
        for step in r["completed_list"]:
            n_done = r["completed_list"].index(step) + 1
            if n_done not in by_providers:
                by_providers[n_done] = []
            by_providers[n_done].append(step.get("bankroll_after", 0))

    print(f"  {'After N':>10}  {'Median':>9}  {'P10':>9}  {'P90':>9}  {'Min':>9}  {'Max':>10}")
    print(f"  {'-'*65}")

    for n in sorted(by_providers.keys()):
        vals = sorted(by_providers[n])
        count = len(vals)
        if count < 100:
            continue
        med = statistics.median(vals)
        p10 = vals[int(count * 0.10)]
        p90 = vals[int(count * 0.90)]
        print(f"  {n:>7} done  {med:>8,.0f} kr  {p10:>8,.0f} kr  {p90:>8,.0f} kr  "
              f"{min(vals):>8,.0f} kr  {max(vals):>9,.0f} kr")


# --- Pure Grind Simulation (standalone, no bonus overhead) ----------

def simulate_pure_grind(starting_bankroll: float, params: SimParams,
                        n_sims: int = 3000, weeks: int = 104,
                        bets_per_week: int = 70) -> list:
    """
    Pure Kelly EV grinding from a fixed starting bankroll.

    Simulates in WEEKLY batches (not daily) — because you can fire all bets
    in one session. The only limits are per-bet stake cap and per-event exposure.

    No artificial daily cap. If there are 100 +EV opps, bet all 100.
    """
    results = []

    for _ in range(n_sims):
        bankroll = starting_bankroll
        peak = bankroll
        max_dd = 0.0
        bets = 0
        wins = 0
        losses = 0
        weekly = []

        for week in range(1, weeks + 1):
            week_pnl = 0.0
            # Vary weekly volume slightly (some weeks more events, some less)
            n_bets = max(5, int(random.gauss(bets_per_week, bets_per_week * 0.15)))

            for _ in range(n_bets):
                if bankroll < params.min_stake:
                    bankroll = max(bankroll, params.min_stake)  # top-up if needed

                edge_pct = max(0.5, random.gauss(params.grind_edge_mean, params.grind_edge_std))
                odds = max(1.50, random.gauss(params.grind_odds_mean, params.grind_odds_std))

                edge = edge_pct / 100
                kf = kelly_fraction(edge_pct, params)
                raw_stake = bankroll * kf * edge / (odds - 1)
                stake = min(raw_stake, bankroll * params.max_stake_pct,
                           bankroll * params.max_event_exposure_pct)
                # Bookmaker per-bet limit
                if params.max_stake_absolute > 0:
                    stake = min(stake, params.max_stake_absolute)
                stake = max(params.min_stake, stake)
                stake = round(stake / 25) * 25
                stake = max(25, min(stake, bankroll))

                if stake < params.min_stake:
                    continue

                win_prob = (1.0 / odds) * (1 + edge)
                if random.random() < win_prob:
                    pnl = stake * (odds - 1)
                    wins += 1
                else:
                    pnl = -stake
                    losses += 1

                bankroll += pnl
                week_pnl += pnl
                bets += 1

            if bankroll > peak:
                peak = bankroll
            dd = (peak - bankroll) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

            weekly.append({
                "week": week,
                "bankroll": round(bankroll, 0),
                "profit": round(bankroll - starting_bankroll, 0),
                "bets": bets,
                "week_pnl": round(week_pnl, 0),
            })

        results.append({
            "final": round(bankroll, 0),
            "profit": round(bankroll - starting_bankroll, 0),
            "bets": bets,
            "wins": wins,
            "losses": losses,
            "peak": round(peak, 0),
            "max_dd_pct": round(100 * max_dd, 1),
            "weekly": weekly,
        })

    return results


# --- Phase 3 Analysis -----------------------------------------------

def _get_week_bankrolls(results: list, target_week: int) -> list:
    """Extract bankroll values at a specific week from sim results."""
    vals = []
    for r in results:
        for snap in r["weekly"]:
            if snap["week"] == target_week:
                vals.append(snap["bankroll"])
                break
    return sorted(vals) if vals else []


def _percentiles(vals: list) -> dict:
    """Get standard percentiles from sorted list."""
    if not vals:
        return {}
    n = len(vals)
    return {
        "med": statistics.median(vals),
        "p10": vals[int(n * 0.10)],
        "p25": vals[int(n * 0.25)],
        "p75": vals[int(n * 0.75)],
        "p90": vals[int(n * 0.90)],
    }


def run_exponential_growth_analysis():
    """
    Post-bonus exponential growth with realistic bookmaker limits.

    Constraints modeled:
    - Per-bet stake cap (Swedish books ~2-5k kr)
    - Per-event exposure cap (5% of bankroll)
    - Kelly fraction (0.25-0.75 based on edge)
    - NO daily volume cap (you can fire entire bankroll in one session)

    Variable: bets/week = how many +EV opps you find across 29 providers.
    """
    freebets = [p for p in PROVIDERS if p.bonus_type == "freebet"]
    deposits = [p for p in PROVIDERS if p.bonus_type == "bonusdeposit"]
    dep_bonus_total = sum(p.bonus_amount for p in deposits)
    params = SimParams()
    wager_ev = sum(p.wagering_req * params.avg_edge_pct / 100 for p in deposits)
    fb_ev = sum(p.bonus_amount * 0.65 for p in freebets)
    total_bonus_ev = dep_bonus_total + wager_ev + fb_ev
    starting_deposit = 3000
    post_bonus_bankroll = starting_deposit + round(total_bonus_ev)

    print(f"\n{'='*90}")
    print(f"  EXPONENTIAL SNOWBALL GROWTH")
    print(f"{'='*90}")

    print(f"\n  BONUS COLLECTION (guaranteed free money):")
    print(f"    Freebets ({len(freebets)} providers):  ~{fb_ev:+,.0f} kr EV")
    print(f"    Bonus deposits ({len(deposits)} providers): +{dep_bonus_total:,.0f} kr + {wager_ev:+,.0f} kr wagering")
    print(f"    Total bonus EV:            ~{total_bonus_ev:+,.0f} kr")
    print(f"    Starting deposit:           {starting_deposit:,} kr")
    print(f"    --> Post-bonus bankroll:    ~{post_bonus_bankroll:,} kr")

    print(f"\n  CONSTRAINTS:")
    print(f"    Per-bet stake cap:    {params.max_stake_absolute:,.0f} kr (Swedish book limits)")
    print(f"    Per-event exposure:   {params.max_event_exposure_pct*100:.0f}% of bankroll")
    print(f"    Kelly fraction:       {params.kelly_base}-{params.kelly_max}")
    print(f"    Daily volume cap:     NONE (fire all bets whenever)")

    n_sims = 3000

    # === BETS/WEEK vs GROWTH (the key table) ===
    print(f"\n  {'='*85}")
    print(f"  BETS PER WEEK vs GROWTH ({n_sims} sims, start: {post_bonus_bankroll:,} kr)")
    print(f"  Edge: {params.grind_edge_mean}% | Stake cap: {params.max_stake_absolute:,.0f} kr/bet")
    print(f"  {'='*85}")
    print(f"\n  {'Bets/wk':>8}  "
          f"{'3 months':>10}  {'6 months':>10}  {'1 year':>10}  {'2 years':>10}")
    print(f"  {'-'*55}")

    volume_results = {}
    for bpw in [20, 35, 50, 70, 100, 150, 200]:
        results = simulate_pure_grind(post_bonus_bankroll, params,
                                      n_sims=n_sims, weeks=104,
                                      bets_per_week=bpw)
        volume_results[bpw] = results

        m3 = _get_week_bankrolls(results, 13)
        m6 = _get_week_bankrolls(results, 26)
        y1 = _get_week_bankrolls(results, 52)
        y2 = sorted([r["final"] for r in results])

        print(f"  {bpw:>7}  "
              f"{statistics.median(m3) if m3 else 0:>9,.0f} kr  "
              f"{statistics.median(m6) if m6 else 0:>9,.0f} kr  "
              f"{statistics.median(sorted(y1)) if y1 else 0:>9,.0f} kr  "
              f"{statistics.median(y2):>9,.0f} kr")

    # === DETAILED MONTH-BY-MONTH @ 70/week (baseline) ===
    baseline = volume_results[70]
    print(f"\n  {'='*85}")
    print(f"  MONTH-BY-MONTH GROWTH @ 70 bets/week")
    print(f"  Start: {post_bonus_bankroll:,} kr | Stake cap: {params.max_stake_absolute:,.0f} kr/bet")
    print(f"  {'='*85}")
    print(f"\n  {'Month':>6}  {'Median':>10}  {'P10':>10}  {'P25':>10}  "
          f"{'P75':>10}  {'P90':>10}  {'xGrowth':>8}")
    print(f"  {'-'*70}")

    for month in range(1, 25):
        target_week = round(month * 52 / 12)
        if target_week > 104:
            break
        vals = _get_week_bankrolls(baseline, target_week)
        if not vals or len(vals) < 100:
            continue
        p = _percentiles(vals)
        growth_x = p["med"] / post_bonus_bankroll
        print(f"  {month:>5}m  {p['med']:>9,.0f} kr  {p['p10']:>9,.0f} kr  {p['p25']:>9,.0f} kr  "
              f"{p['p75']:>9,.0f} kr  {p['p90']:>9,.0f} kr  {growth_x:>7.1f}x")

    # === MONTHLY PROFIT (actual per-month) ===
    print(f"\n  MONTHLY PROFIT (median profit earned THAT month):")
    print(f"  {'-'*55}")
    print(f"  {'Month':>6}  {'Profit this month':>18}  {'Cumulative':>12}  {'Bankroll':>10}")
    print(f"  {'-'*55}")

    for month in range(1, 25):
        w_start = round((month - 1) * 52 / 12) if month > 1 else 0
        w_end = round(month * 52 / 12)
        if w_end > 104:
            break
        month_profits = []
        for r in baseline:
            start_val = post_bonus_bankroll
            end_val = post_bonus_bankroll
            for snap in r["weekly"]:
                if snap["week"] == w_start and w_start > 0:
                    start_val = snap["bankroll"]
                if snap["week"] == w_end:
                    end_val = snap["bankroll"]
            month_profits.append(end_val - start_val)

        vals_end = _get_week_bankrolls(baseline, w_end)
        if not vals_end:
            continue
        month_profits.sort()
        mp = _percentiles(month_profits)
        ep = _percentiles(vals_end)
        cum_profit = ep["med"] - post_bonus_bankroll

        print(f"  {month:>5}m  {mp['med']:>+17,.0f} kr  {cum_profit:>+11,.0f} kr  {ep['med']:>9,.0f} kr")

    # === EDGE SENSITIVITY ===
    print(f"\n  {'='*85}")
    print(f"  EDGE SENSITIVITY @ 70 bets/week")
    print(f"  {'='*85}")
    print(f"\n  {'Edge':>6}  {'3 months':>10}  {'6 months':>10}  {'1 year':>10}  "
          f"{'2 years':>10}  {'Note':>15}")
    print(f"  {'-'*65}")

    for edge, note in [(2.0, "conservative"), (3.0, "cautious"),
                       (4.0, "baseline"), (5.0, "good scanner"),
                       (6.0, "great"), (8.0, "elite")]:
        p = SimParams(grind_edge_mean=edge, grind_edge_std=edge * 0.5)
        results = simulate_pure_grind(post_bonus_bankroll, p,
                                      n_sims=2000, weeks=104, bets_per_week=70)

        m3 = _get_week_bankrolls(results, 13)
        m6 = _get_week_bankrolls(results, 26)
        y1 = _get_week_bankrolls(results, 52)
        y2 = sorted([r["final"] for r in results])

        print(f"  {edge:>4.1f}%  "
              f"{statistics.median(m3) if m3 else 0:>9,.0f} kr  "
              f"{statistics.median(m6) if m6 else 0:>9,.0f} kr  "
              f"{statistics.median(y1) if y1 else 0:>9,.0f} kr  "
              f"{statistics.median(y2):>9,.0f} kr  {note:>15}")

    # === STAKE CAP SENSITIVITY ===
    print(f"\n  {'='*85}")
    print(f"  STAKE CAP SENSITIVITY @ 70 bets/week, 4% edge")
    print(f"  {'='*85}")
    print(f"\n  {'Cap':>10}  {'6 months':>10}  {'1 year':>10}  {'2 years':>10}  {'Note':>20}")
    print(f"  {'-'*60}")

    for cap, note in [(1000, "limited account"),
                      (2000, "normal soft"),
                      (5000, "baseline"),
                      (10000, "sharp-friendly"),
                      (0, "no cap (Pinnacle)")]:
        p = SimParams(max_stake_absolute=cap)
        results = simulate_pure_grind(post_bonus_bankroll, p,
                                      n_sims=2000, weeks=104, bets_per_week=70)

        m6 = _get_week_bankrolls(results, 26)
        y1 = _get_week_bankrolls(results, 52)
        y2 = sorted([r["final"] for r in results])

        print(f"  {(f'{cap:,} kr' if cap else 'none'):>10}  "
              f"{statistics.median(m6) if m6 else 0:>9,.0f} kr  "
              f"{statistics.median(y1) if y1 else 0:>9,.0f} kr  "
              f"{statistics.median(y2):>9,.0f} kr  {note:>20}")

    # === SUMMARY ===
    finals = sorted([r["final"] for r in baseline])
    drawdowns = sorted([r["max_dd_pct"] for r in baseline])
    n = len(finals)

    print(f"\n  {'='*85}")
    print(f"  SUMMARY: 70 bets/week, 4% edge, {params.max_stake_absolute:,.0f} kr cap, 2 years")
    print(f"  {'='*85}")
    print(f"    Start:                   {post_bonus_bankroll:>10,} kr")
    print(f"    Median final (2yr):      {statistics.median(finals):>10,.0f} kr  "
          f"({statistics.median(finals)/post_bonus_bankroll:.0f}x)")
    print(f"    P10 (unlucky):           {finals[int(n*0.10)]:>10,.0f} kr")
    print(f"    P25:                     {finals[int(n*0.25)]:>10,.0f} kr")
    print(f"    P75:                     {finals[int(n*0.75)]:>10,.0f} kr")
    print(f"    P90 (lucky):             {finals[int(n*0.90)]:>10,.0f} kr")
    print(f"    Max drawdown (median):   {statistics.median(drawdowns):>9.0f}%")
    print(f"    Total bets (2yr):        {statistics.median([r['bets'] for r in baseline]):>10,.0f}")


# --- Main -----------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Allow running just Phase 3 with: python snowball_sim.py --grind
    grind_only = "--grind" in sys.argv
    full_only = "--full" in sys.argv

    print("+" + "="*88 + "+")
    print("|" + " ARNOLD -- BANKROLL SNOWBALL SIMULATOR (EV-ONLY) ".center(88) + "|")
    print("+" + "="*88 + "+")

    if grind_only or "--growth" in sys.argv:
        # Just the exponential growth analysis
        run_exponential_growth_analysis()
    elif full_only:
        # Everything: bonus analysis + growth
        run_bonus_deposit_ev_analysis()
        run_minimum_viable_start()
        show_detailed_sequence(3000)
        run_exponential_growth_analysis()
    else:
        # Default: bonus summary + exponential growth (the main event)
        run_bonus_deposit_ev_analysis()
        run_exponential_growth_analysis()
