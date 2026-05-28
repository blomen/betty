"""Tests for rehedge_scanner — Case 1 (post-placement middle) emit logic.

Uses the shared db_session fixture (in-memory SQLite). Builds minimal
Event + Bet + Odds rows, runs the scanner, asserts on emitted candidates.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.analysis.rehedge_scanner import RehedgeCandidate
from src.db.models import Event, Provider


@pytest.fixture
def future_event(db_session):
    """A future NFL event suitable for rehedge tests."""
    # Provider rows required by FK constraints.
    for pid, cur in [("pinnacle", "USD"), ("unibet", "SEK"), ("betsson", "SEK")]:
        db_session.add(Provider(id=pid, name=pid.title(), currency=cur))
    event = Event(
        id="evt-test-1",
        sport="americanfootball_nfl",
        home_team="Patriots",
        away_team="Jets",
        start_time=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(event)
    db_session.flush()
    yield event


class TestRehedgeCandidateDataclass:
    def test_candidate_fields(self):
        # Just enforce the shape the scanner will emit.
        c = RehedgeCandidate(
            bet_id=42,
            case="post_placement_middle",
            hedge_provider="betsson",
            hedge_market="spread",
            hedge_outcome="away",
            hedge_point=3.5,
            hedge_odds=1.91,
            recommended_stake_base=95.0,
            base_currency="SEK",
            metadata={"key_number": 3, "wing_loss_pct": 0.012},
        )
        assert c.bet_id == 42
        assert c.case == "post_placement_middle"
        assert c.metadata["key_number"] == 3
