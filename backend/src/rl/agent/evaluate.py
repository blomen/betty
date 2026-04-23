"""Evaluation metrics for the RL trading agent.

Computes episode-level statistics including win rate, profit factor,
drawdown, and per-level-type breakdowns.
"""

from __future__ import annotations

import math
from collections import defaultdict

from src.rl.config import Action


def compute_metrics(episodes: list[dict]) -> dict:
    """Compute evaluation metrics from a list of episode dicts.

    Each episode dict must have:
        action     (int)   — 0=LONG, 1=SHORT, 2=SKIP
        reward     (float) — R-multiple for the trade (0 if skipped)
        level_type (str)   — which level type triggered the episode

    Returns a metrics dict with aggregate and per-level breakdowns.
    """
    total_episodes = len(episodes)
    if total_episodes == 0:
        return {
            "total_episodes": 0,
            "trades_taken": 0,
            "skip_rate": 0.0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "total_r": 0.0,
            "profit_factor": float("inf"),
            "max_drawdown_r": 0.0,
            "equity_curve": [],
            "level_breakdown": {},
        }

    skip_action = Action.SKIP.value

    trades: list[float] = []
    skips = 0

    # Equity curve over ALL episodes (skips contribute 0 change)
    equity_curve: list[float] = []
    running_total = 0.0

    # Per-level-type accumulators
    level_data: dict[str, dict] = defaultdict(lambda: {"total": 0, "trades": [], "skips": 0})

    for ep in episodes:
        action = ep["action"]
        reward = ep["reward"]
        level_type = ep["level_type"]

        level_data[level_type]["total"] += 1

        if action == skip_action:
            skips += 1
            level_data[level_type]["skips"] += 1
        else:
            trades.append(reward)
            level_data[level_type]["trades"].append(reward)

        # Equity curve: only trade rewards move the curve
        if action != skip_action:
            running_total += reward
        equity_curve.append(running_total)

    trades_taken = len(trades)
    skip_rate = skips / total_episodes

    # Win rate
    if trades_taken > 0:
        wins = sum(1 for r in trades if r > 0)
        win_rate = wins / trades_taken
        avg_r = sum(trades) / trades_taken
        total_r = sum(trades)
    else:
        win_rate = 0.0
        avg_r = 0.0
        total_r = 0.0

    # Profit factor
    gross_wins = sum(r for r in trades if r > 0)
    gross_losses = abs(sum(r for r in trades if r < 0))
    profit_factor = float("inf") if gross_losses == 0 else gross_wins / gross_losses

    # Max drawdown on the equity curve
    max_drawdown_r = _max_drawdown(equity_curve)

    # Level breakdown
    level_breakdown: dict[str, dict] = {}
    for lt, data in level_data.items():
        lt_total = data["total"]
        lt_trades = data["trades"]
        lt_skips = data["skips"]
        lt_trades_taken = len(lt_trades)

        lt_skip_rate = lt_skips / lt_total if lt_total > 0 else 0.0
        lt_win_rate = sum(1 for r in lt_trades if r > 0) / lt_trades_taken if lt_trades_taken > 0 else 0.0
        lt_avg_r = sum(lt_trades) / lt_trades_taken if lt_trades_taken > 0 else 0.0

        level_breakdown[lt] = {
            "total": lt_total,
            "trades": lt_trades_taken,
            "skip_rate": lt_skip_rate,
            "win_rate": lt_win_rate,
            "avg_r": lt_avg_r,
        }

    return {
        "total_episodes": total_episodes,
        "trades_taken": trades_taken,
        "skip_rate": skip_rate,
        "win_rate": win_rate,
        "avg_r": avg_r,
        "total_r": total_r,
        "profit_factor": profit_factor,
        "max_drawdown_r": max_drawdown_r,
        "equity_curve": equity_curve,
        "level_breakdown": level_breakdown,
    }


def _max_drawdown(equity_curve: list[float]) -> float:
    """Compute max peak-to-trough drawdown in cumulative R.

    The starting equity is 0, so the initial peak is 0 — this ensures a
    drawdown is captured even when the very first trade is a loss.
    """
    if not equity_curve:
        return 0.0
    peak = 0.0  # Starting equity before any trades
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = peak - value
        if dd > max_dd:
            max_dd = dd
    return max_dd


def print_evaluation_report(metrics: dict) -> None:
    """Print a formatted evaluation report to the terminal."""
    total = metrics["total_episodes"]
    trades = metrics["trades_taken"]
    skip_rate = metrics["skip_rate"]
    win_rate = metrics["win_rate"]
    avg_r = metrics["avg_r"]
    total_r = metrics["total_r"]
    pf = metrics["profit_factor"]
    mdd = metrics["max_drawdown_r"]
    level_breakdown = metrics["level_breakdown"]

    pf_str = f"{pf:.2f}" if not math.isinf(pf) else "inf"

    print("=" * 56)
    print("  RL AGENT EVALUATION REPORT")
    print("=" * 56)
    print(f"  Episodes        : {total:>8,}")
    print(f"  Trades taken    : {trades:>8,}  ({(1 - skip_rate) * 100:.1f}% of episodes)")
    print(f"  Skip rate       : {skip_rate:>8.1%}")
    print(f"  Win rate        : {win_rate:>8.1%}")
    print(f"  Avg R / trade   : {avg_r:>+8.3f} R")
    print(f"  Total R         : {total_r:>+8.3f} R")
    print(f"  Profit factor   : {pf_str:>8}")
    print(f"  Max drawdown    : {mdd:>8.3f} R")
    print()

    if level_breakdown:
        print("  LEVEL-TYPE BREAKDOWN (sorted by avg R)")
        print(f"  {'Level':<20} {'Total':>6} {'Trades':>7} {'Skip%':>6} {'Win%':>6} {'Avg R':>7}")
        print("  " + "-" * 54)

        sorted_levels = sorted(
            level_breakdown.items(),
            key=lambda kv: kv[1]["avg_r"],
            reverse=True,
        )
        for lt, data in sorted_levels:
            print(
                f"  {lt:<20} {data['total']:>6,} {data['trades']:>7,} "
                f"{data['skip_rate']:>5.1%} {data['win_rate']:>5.1%} {data['avg_r']:>+7.3f}"
            )

    print("=" * 56)
