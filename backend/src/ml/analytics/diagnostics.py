"""Rule-based diagnostic engine for provider health.

Philosophy: diagnose -> recommend fix -> track -> deprioritize only as last resort.

Each rule checks a specific condition and produces a recommendation
with category, severity, message, and diagnostic data.
"""

import logging

logger = logging.getLogger(__name__)

# Thresholds for diagnostics
MATCH_RATE_WARNING = 0.65
MATCH_RATE_CRITICAL = 0.40
MATCH_RATE_DROP_THRESHOLD = 0.15  # 15% drop triggers warning
SLOW_SECONDS_PER_VB = 30.0  # >30s per value bet is slow
SLOW_DURATION = 120.0  # >120s extraction is slow


def diagnose_provider(provider_data: dict) -> list[dict]:
    """Run all diagnostic rules against a provider's metrics.

    Args:
        provider_data: dict with keys like avg_match_rate, avg_events,
            avg_duration, total_opportunities, seconds_per_value_bet,
            spread_count, total_count, prev_match_rate, etc.

    Returns:
        List of recommendation dicts with: category, severity, message, diagnostic_data
    """
    recommendations = []

    # Rule 1: Match rate drop
    match_rate = provider_data.get("avg_match_rate", 1.0)
    prev_rate = provider_data.get("prev_match_rate")
    provider_id = provider_data.get("provider_id", "unknown")

    if match_rate < MATCH_RATE_CRITICAL:
        recommendations.append(
            {
                "category": "match_rate",
                "severity": "critical",
                "message": f"{provider_id}: match rate is {match_rate:.0%} -- check sports.yaml aliases and API changes",
                "diagnostic_data": {"current": match_rate, "threshold": MATCH_RATE_CRITICAL},
            }
        )
    elif prev_rate is not None and (prev_rate - match_rate) > MATCH_RATE_DROP_THRESHOLD:
        recommendations.append(
            {
                "category": "match_rate",
                "severity": "warning",
                "message": f"{provider_id}: match rate dropped from {prev_rate:.0%} to {match_rate:.0%} -- check API changes or team name normalization",
                "diagnostic_data": {"current": match_rate, "previous": prev_rate, "drop": prev_rate - match_rate},
            }
        )
    elif match_rate < MATCH_RATE_WARNING:
        recommendations.append(
            {
                "category": "match_rate",
                "severity": "warning",
                "message": f"{provider_id}: match rate is {match_rate:.0%} -- review sports.yaml aliases",
                "diagnostic_data": {"current": match_rate, "threshold": MATCH_RATE_WARNING},
            }
        )

    # Rule 2: Missing markets (spread or total = 0)
    spread = provider_data.get("spread_count")
    total = provider_data.get("total_count")
    if spread is not None and spread == 0 and provider_data.get("avg_events", 0) > 20:
        recommendations.append(
            {
                "category": "market_gap",
                "severity": "warning",
                "message": f"{provider_id}: 0 spread markets -- needs Pass 2 enrichment or API endpoint check",
                "diagnostic_data": {"spread_count": 0, "total_count": total},
            }
        )
    if total is not None and total == 0 and provider_data.get("avg_events", 0) > 20:
        recommendations.append(
            {
                "category": "market_gap",
                "severity": "warning",
                "message": f"{provider_id}: 0 total markets -- needs enrichment or API endpoint check",
                "diagnostic_data": {"spread_count": spread, "total_count": 0},
            }
        )

    # Rule 3: Slow extraction / poor ROI
    sec_per_vb = provider_data.get("seconds_per_value_bet")
    duration = provider_data.get("avg_duration", 0)

    if sec_per_vb is not None and sec_per_vb > SLOW_SECONDS_PER_VB:
        recommendations.append(
            {
                "category": "timing",
                "severity": "warning",
                "message": f"{provider_id}: {sec_per_vb:.1f}s per value bet (threshold: {SLOW_SECONDS_PER_VB}s) -- investigate extraction bottleneck before deprioritizing",
                "diagnostic_data": {"seconds_per_value_bet": sec_per_vb, "avg_duration": duration},
            }
        )
    elif duration > SLOW_DURATION and (provider_data.get("total_opportunities", 0) < 5):
        recommendations.append(
            {
                "category": "timing",
                "severity": "info",
                "message": f"{provider_id}: {duration:.0f}s extraction for {provider_data.get('total_opportunities', 0)} opportunities -- low yield",
                "diagnostic_data": {
                    "avg_duration": duration,
                    "total_opportunities": provider_data.get("total_opportunities", 0),
                },
            }
        )

    return recommendations
