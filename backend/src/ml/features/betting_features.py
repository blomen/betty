"""Extract feature vectors for sports betting opportunities (M1 Edge Quality)."""

import statistics
from datetime import datetime, timezone

from src.constants import PLATFORM_MAP, SHARP_PROVIDERS


def extract_betting_features(
    edge_pct: float,
    provider_odds: float,
    fair_odds: float,
    fair_probability: float,
    provider: str,
    sport: str,
    market: str,
    event_id: str,
    prob_sum: float,
    odds_by_outcome: dict[str, list[dict]],
    pinnacle_overround: float,
    event_start_time: datetime | None,
    point: float | None = None,
) -> dict:
    now = datetime.now(timezone.utc)

    # Find the outcome that contains this provider's odds
    all_outcome_odds = odds_by_outcome.get(_find_outcome_for_provider(odds_by_outcome, provider, provider_odds), [])
    soft_odds = [p for p in all_outcome_odds if p["provider"] not in SHARP_PROVIDERS]
    soft_odds_values = [p["odds"] for p in soft_odds]

    # Provider rank (1 = best price)
    sorted_odds = sorted(soft_odds_values, reverse=True)
    provider_odds_rank = sorted_odds.index(provider_odds) + 1 if provider_odds in sorted_odds else len(sorted_odds)

    # Market consensus spread
    consensus_spread = statistics.stdev(soft_odds_values) if len(soft_odds_values) >= 2 else 0.0

    # Odds age
    provider_entry = next((p for p in all_outcome_odds if p["provider"] == provider), None)
    odds_age_minutes = _compute_age_minutes(provider_entry, now) if provider_entry else None

    # Sharp age
    sharp_entry = next((p for p in all_outcome_odds if p["provider"] in SHARP_PROVIDERS), None)
    sharp_age_minutes = _compute_age_minutes(sharp_entry, now) if sharp_entry else None

    # Time to start
    time_to_start = None
    if event_start_time:
        if event_start_time.tzinfo is None:
            event_start_time = event_start_time.replace(tzinfo=timezone.utc)
        time_to_start = (event_start_time - now).total_seconds() / 60

    return {
        "edge_pct": edge_pct,
        "prob_sum": prob_sum,
        "odds_ratio": provider_odds / fair_odds if fair_odds > 0 else None,
        "odds_age_minutes": odds_age_minutes,
        "sharp_age_minutes": sharp_age_minutes,
        "time_to_start_minutes": time_to_start,
        "pinnacle_overround": pinnacle_overround,
        "num_providers_with_odds": len(soft_odds),
        "provider_odds_rank": provider_odds_rank,
        "market_consensus_spread": round(consensus_spread, 4),
        "provider_platform": PLATFORM_MAP.get(provider, provider),
        "sport": sport,
        "market_type": market,
        "point": point,
        "hour_of_day": now.hour,
        "day_of_week": now.weekday(),
    }


def _compute_age_minutes(entry: dict, now: datetime) -> float | None:
    updated = entry.get("updated_at")
    if not updated:
        return None
    if isinstance(updated, str):
        updated = datetime.fromisoformat(updated)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (now - updated).total_seconds() / 60


def _find_outcome_for_provider(odds_by_outcome: dict, provider: str, odds: float) -> str | None:
    for outcome, providers in odds_by_outcome.items():
        for p in providers:
            if p["provider"] == provider and abs(p["odds"] - odds) < 0.001:
                return outcome
    for outcome, providers in odds_by_outcome.items():
        for p in providers:
            if p["provider"] == provider:
                return outcome
    return None
