import json
from datetime import datetime, timedelta
from src.db.models import DeferredEvent


def test_deferred_event_to_standard_event():
    """DeferredEvent.to_standard_event() reconstructs a valid StandardEvent."""
    markets = [
        {"type": "moneyline", "outcomes": [
            {"name": "home", "odds": 1.85},
            {"name": "away", "odds": 2.05},
        ]}
    ]
    de = DeferredEvent(
        provider_id="betsson",
        sport="football",
        league="Premier League",
        home_team="Arsenal",
        away_team="Chelsea",
        normalized_home="arsenal",
        normalized_away="chelsea",
        start_time=datetime(2026, 3, 25, 15, 0),
        markets_json=json.dumps(markets),
    )
    event = de.to_standard_event()
    assert event.sport == "football"
    assert event.home_team == "Arsenal"
    assert event.away_team == "Chelsea"
    assert event.markets == markets
    assert event.provider == "betsson"
    assert event._from_deferred is True
