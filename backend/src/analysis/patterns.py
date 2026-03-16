"""
Pattern detection for bet and trade postmortem data.

Segments postmortem rows across multiple dimensions and surfaces
actionable insights (losing segments, winning segments, erosion hotspots, etc.).
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta

from ..db.models import Bet, Trade, BetPostmortem, TradePostmortem

MIN_BET_SAMPLE = 10
MIN_TRADE_SAMPLE = 5

SEVERITY_ORDER = {"red": 0, "amber": 1, "purple": 2, "green": 3}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edge_band(edge: float | None) -> str:
    if edge is None:
        return "unknown"
    if edge < 2:
        return "<2%"
    if edge < 5:
        return "2-5%"
    if edge < 10:
        return "5-10%"
    return "10%+"


def _odds_range(odds: float) -> str:
    if odds < 1.5:
        return "<1.5"
    if odds < 2.5:
        return "1.5-2.5"
    if odds < 4.0:
        return "2.5-4.0"
    return "4.0+"


def _ttk_band(bet: Bet) -> str:
    """Time-to-kickoff band from placed_at to start_time."""
    if not bet.start_time or not bet.placed_at:
        return "unknown"
    delta = bet.start_time - bet.placed_at
    hours = delta.total_seconds() / 3600
    if hours < 6:
        return "<6h"
    if hours < 24:
        return "6-24h"
    if hours < 48:
        return "24-48h"
    return "48h+"


_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day_of_week(bet: Bet) -> str:
    if not bet.placed_at:
        return "unknown"
    return _DOW_NAMES[bet.placed_at.weekday()]


def _segment_roi(entries: list[tuple]) -> float:
    """ROI % for a list of (bet, pm) tuples."""
    total_stake = sum(b.stake for b, _ in entries)
    if total_stake == 0:
        return 0.0
    total_profit = sum(b.profit for b, _ in entries)
    return (total_profit / total_stake) * 100


def _sorted_patterns(patterns: list[dict]) -> list[dict]:
    return sorted(patterns, key=lambda p: SEVERITY_ORDER.get(p["severity"], 99))


# ---------------------------------------------------------------------------
# Bet patterns
# ---------------------------------------------------------------------------

def detect_bet_patterns(rows: list[tuple[Bet, BetPostmortem]]) -> list[dict]:
    """
    Segment bet postmortem data and return pattern insights.

    Each row is a (Bet, BetPostmortem) tuple.
    Returns list of dicts with keys: rule, severity, message, segment, sample_size.
    """
    if not rows:
        return []

    patterns: list[dict] = []

    # Build segments
    segments: dict[str, dict[str, list]] = {
        "market": defaultdict(list),
        "provider": defaultdict(list),
        "sport": defaultdict(list),
        "edge_band": defaultdict(list),
        "odds_range": defaultdict(list),
        "ttk_band": defaultdict(list),
        "day_of_week": defaultdict(list),
        "classification": defaultdict(list),
    }

    for bet, pm in rows:
        segments["market"][bet.market or "unknown"].append((bet, pm))
        segments["provider"][bet.provider_id or "unknown"].append((bet, pm))

        sport = getattr(bet.event, "sport", None) if bet.event else None
        segments["sport"][sport or "unknown"].append((bet, pm))

        segments["edge_band"][_edge_band(pm.edge_at_placement)].append((bet, pm))
        segments["odds_range"][_odds_range(bet.odds)].append((bet, pm))
        segments["ttk_band"][_ttk_band(bet)].append((bet, pm))
        segments["day_of_week"][_day_of_week(bet)].append((bet, pm))
        segments["classification"][pm.classification or "unknown"].append((bet, pm))

    # Rule 1 & 2: ROI thresholds across all segment dimensions
    for dim_name, dim in segments.items():
        for key, entries in dim.items():
            if len(entries) < MIN_BET_SAMPLE:
                continue

            roi = _segment_roi(entries)
            seg_label = f"{dim_name}={key}"

            if roi < -10:
                patterns.append({
                    "rule": "losing_segment",
                    "severity": "red",
                    "message": f"ROI {roi:+.1f}% in {seg_label} ({len(entries)} bets)",
                    "segment": seg_label,
                    "sample_size": len(entries),
                })
            elif roi > 5:
                patterns.append({
                    "rule": "winning_segment",
                    "severity": "green",
                    "message": f"ROI {roi:+.1f}% in {seg_label} ({len(entries)} bets)",
                    "segment": seg_label,
                    "sample_size": len(entries),
                })

    # Rule 3 & 4: Classification concentration in provider x market segments
    provider_market: dict[str, list] = defaultdict(list)
    for bet, pm in rows:
        pm_key = f"{bet.provider_id or 'unknown'}|{bet.market or 'unknown'}"
        provider_market[pm_key].append((bet, pm))

    for pm_key, entries in provider_market.items():
        if len(entries) < MIN_BET_SAMPLE:
            continue

        losses = [(b, p) for b, p in entries if b.result == "lost"]
        if not losses:
            continue

        n_losses = len(losses)

        # Rule 3: Edge erosion hotspot
        erosion_count = sum(1 for _, p in losses if p.classification == "edge_erosion")
        if erosion_count / n_losses >= 0.40:
            patterns.append({
                "rule": "edge_erosion_hotspot",
                "severity": "amber",
                "message": (
                    f"{erosion_count}/{n_losses} losses are edge_erosion "
                    f"in {pm_key} ({len(entries)} bets)"
                ),
                "segment": pm_key,
                "sample_size": len(entries),
            })

        # Rule 4: False edge concentration
        false_edge_count = sum(1 for _, p in losses if p.classification == "false_edge")
        if false_edge_count / n_losses >= 0.30:
            patterns.append({
                "rule": "false_edge_concentration",
                "severity": "red",
                "message": (
                    f"{false_edge_count}/{n_losses} losses are false_edge "
                    f"in {pm_key} ({len(entries)} bets)"
                ),
                "segment": pm_key,
                "sample_size": len(entries),
            })

    # Rule 5: Sizing alert — >=3 sizing_error in trailing 30 days
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    recent_sizing = [
        (b, p) for b, p in rows
        if p.classification == "sizing_error"
        and b.placed_at is not None
        and b.placed_at >= cutoff
    ]
    if len(recent_sizing) >= 3:
        patterns.append({
            "rule": "sizing_alert",
            "severity": "amber",
            "message": f"{len(recent_sizing)} sizing errors in trailing 30 days",
            "segment": "trailing_30d",
            "sample_size": len(recent_sizing),
        })

    return _sorted_patterns(patterns)


# ---------------------------------------------------------------------------
# Trade patterns
# ---------------------------------------------------------------------------

def detect_trade_patterns(rows: list[tuple[Trade, TradePostmortem]]) -> list[dict]:
    """
    Segment trade postmortem data and return pattern insights.

    Each row is a (Trade, TradePostmortem) tuple.
    Returns list of dicts with keys: rule, severity, message, segment, sample_size.
    """
    if not rows:
        return []

    patterns: list[dict] = []

    # Build segments
    setup_seg: dict[str, list] = defaultdict(list)
    instrument_seg: dict[str, list] = defaultdict(list)
    direction_seg: dict[str, list] = defaultdict(list)
    dir_inst_seg: dict[str, list] = defaultdict(list)

    for trade, pm in rows:
        setup_seg[trade.setup_type or "unknown"].append((trade, pm))
        instrument_seg[trade.instrument or "unknown"].append((trade, pm))
        direction_seg[trade.direction or "unknown"].append((trade, pm))
        di_key = f"{trade.direction or 'unknown'}|{trade.instrument or 'unknown'}"
        dir_inst_seg[di_key].append((trade, pm))

    # Rule 1 & 2: Setup performance
    for setup, entries in setup_seg.items():
        if len(entries) < MIN_TRADE_SAMPLE:
            continue

        r_values = [pm.r_multiple for _, pm in entries if pm.r_multiple is not None]
        if not r_values:
            continue
        avg_r = sum(r_values) / len(r_values)
        seg_label = f"setup={setup}"

        if avg_r < 0:
            patterns.append({
                "rule": "setup_underperformer",
                "severity": "red",
                "message": f"Avg R {avg_r:+.2f} in {seg_label} ({len(entries)} trades)",
                "segment": seg_label,
                "sample_size": len(entries),
            })
        elif avg_r > 0.5:
            patterns.append({
                "rule": "setup_performer",
                "severity": "green",
                "message": f"Avg R {avg_r:+.2f} in {seg_label} ({len(entries)} trades)",
                "segment": seg_label,
                "sample_size": len(entries),
            })

    # Rule 3: Direction x instrument
    for di_key, entries in dir_inst_seg.items():
        if len(entries) < MIN_TRADE_SAMPLE:
            continue

        r_values = [pm.r_multiple for _, pm in entries if pm.r_multiple is not None]
        if not r_values:
            continue
        avg_r = sum(r_values) / len(r_values)
        seg_label = f"dir_inst={di_key}"

        if avg_r < -0.3:
            patterns.append({
                "rule": "direction_instrument_underperformer",
                "severity": "red",
                "message": f"Avg R {avg_r:+.2f} in {seg_label} ({len(entries)} trades)",
                "segment": seg_label,
                "sample_size": len(entries),
            })
        elif avg_r > 0.5:
            patterns.append({
                "rule": "direction_instrument_performer",
                "severity": "green",
                "message": f"Avg R {avg_r:+.2f} in {seg_label} ({len(entries)} trades)",
                "segment": seg_label,
                "sample_size": len(entries),
            })

    # Rule 4: Streak impact — win rate after 2+ consecutive losses vs baseline
    all_r = [(pm.r_multiple, pm.streak_position) for _, pm in rows
             if pm.r_multiple is not None and pm.streak_position is not None]

    if len(all_r) >= MIN_TRADE_SAMPLE:
        wins_total = sum(1 for r, _ in all_r if r > 0)
        baseline_wr = wins_total / len(all_r) if all_r else 0

        # After 2+ consecutive losses = streak_position <= -2
        after_streak = [(r, sp) for r, sp in all_r if sp <= -2]
        if len(after_streak) >= MIN_TRADE_SAMPLE:
            streak_wins = sum(1 for r, _ in after_streak if r > 0)
            streak_wr = streak_wins / len(after_streak)
            deviation_pp = (streak_wr - baseline_wr) * 100

            if abs(deviation_pp) > 15:
                patterns.append({
                    "rule": "streak_impact",
                    "severity": "red",
                    "message": (
                        f"Win rate after 2+ losses: {streak_wr:.0%} vs "
                        f"baseline {baseline_wr:.0%} ({deviation_pp:+.0f}pp, "
                        f"{len(after_streak)} trades)"
                    ),
                    "segment": "post_losing_streak",
                    "sample_size": len(after_streak),
                })

    # Rule 5: Psych correlation — avg R differs >0.5R between psych <6 and >=7
    low_psych = [pm.r_multiple for _, pm in rows
                 if pm.routine_psych_avg is not None
                 and pm.routine_psych_avg < 6
                 and pm.r_multiple is not None]
    high_psych = [pm.r_multiple for _, pm in rows
                  if pm.routine_psych_avg is not None
                  and pm.routine_psych_avg >= 7
                  and pm.r_multiple is not None]

    if len(low_psych) >= MIN_TRADE_SAMPLE and len(high_psych) >= MIN_TRADE_SAMPLE:
        avg_low = sum(low_psych) / len(low_psych)
        avg_high = sum(high_psych) / len(high_psych)
        diff = avg_high - avg_low

        if abs(diff) > 0.5:
            patterns.append({
                "rule": "psych_correlation",
                "severity": "purple",
                "message": (
                    f"Psych >=7 avg R {avg_high:+.2f} vs <6 avg R {avg_low:+.2f} "
                    f"(delta {diff:+.2f}R)"
                ),
                "segment": "psych_band",
                "sample_size": len(low_psych) + len(high_psych),
            })

    return _sorted_patterns(patterns)
