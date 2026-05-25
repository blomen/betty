"""
Pattern detection for bet and trade postmortem data.

Segments postmortem rows across multiple dimensions and surfaces
actionable insights (losing segments, winning segments, erosion hotspots, etc.).
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..db.models import Bet, BetPostmortem

MIN_BET_SAMPLE = 10

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
                patterns.append(
                    {
                        "rule": "losing_segment",
                        "severity": "red",
                        "message": f"ROI {roi:+.1f}% in {seg_label} ({len(entries)} bets)",
                        "segment": seg_label,
                        "sample_size": len(entries),
                    }
                )
            elif roi > 5:
                patterns.append(
                    {
                        "rule": "winning_segment",
                        "severity": "green",
                        "message": f"ROI {roi:+.1f}% in {seg_label} ({len(entries)} bets)",
                        "segment": seg_label,
                        "sample_size": len(entries),
                    }
                )

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
            patterns.append(
                {
                    "rule": "edge_erosion_hotspot",
                    "severity": "amber",
                    "message": (
                        f"{erosion_count}/{n_losses} losses are edge_erosion in {pm_key} ({len(entries)} bets)"
                    ),
                    "segment": pm_key,
                    "sample_size": len(entries),
                }
            )

        # Rule 4: False edge concentration
        false_edge_count = sum(1 for _, p in losses if p.classification == "false_edge")
        if false_edge_count / n_losses >= 0.30:
            patterns.append(
                {
                    "rule": "false_edge_concentration",
                    "severity": "red",
                    "message": (
                        f"{false_edge_count}/{n_losses} losses are false_edge in {pm_key} ({len(entries)} bets)"
                    ),
                    "segment": pm_key,
                    "sample_size": len(entries),
                }
            )

    # Rule 5: Sizing alert — >=3 sizing_error in trailing 30 days
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    recent_sizing = [
        (b, p)
        for b, p in rows
        if p.classification == "sizing_error" and b.placed_at is not None and b.placed_at >= cutoff
    ]
    if len(recent_sizing) >= 3:
        patterns.append(
            {
                "rule": "sizing_alert",
                "severity": "amber",
                "message": f"{len(recent_sizing)} sizing errors in trailing 30 days",
                "segment": "trailing_30d",
                "sample_size": len(recent_sizing),
            }
        )

    return _sorted_patterns(patterns)
