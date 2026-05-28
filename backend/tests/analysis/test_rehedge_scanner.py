"""Tests for rehedge_scanner — Case 1 (post-placement middle) emit logic.

Uses the shared db_session fixture (in-memory SQLite). Builds minimal
Event + Bet + Odds rows, runs the scanner, asserts on emitted candidates.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.analysis.rehedge_scanner import RehedgeCandidate
from src.db.models import Bet, Event, Provider


@pytest.fixture
def future_event(db_session):
    """A future NFL event suitable for rehedge tests."""
    # Provider rows required by FK constraints.
    for pid in ["pinnacle", "unibet", "betsson"]:
        db_session.add(Provider(id=pid, name=pid.title()))
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


class TestQueryOpenBets:
    """The scanner must filter to bets that are actually scannable:
    pending result, future event, has event_id (boost bets excluded),
    has point (moneylines excluded for Case 1)."""

    def test_includes_open_nfl_spread(self, db_session, future_event):
        from src.analysis.rehedge_scanner import _query_open_bets

        db_session.add(
            Bet(
                id=1,
                event_id=future_event.id,
                provider_id="unibet",
                market="spread",
                outcome="home",
                point=-2.5,
                odds=1.91,
                stake=100.0,
                currency="SEK",
                result="pending",
                bet_type="value",
                start_time=future_event.start_time,
            )
        )
        db_session.flush()

        bets = _query_open_bets(db_session)
        assert [b.id for b in bets] == [1]

    def test_excludes_settled(self, db_session, future_event):
        from src.analysis.rehedge_scanner import _query_open_bets

        db_session.add(
            Bet(
                id=1,
                event_id=future_event.id,
                provider_id="unibet",
                market="spread",
                outcome="home",
                point=-2.5,
                odds=1.91,
                stake=100.0,
                currency="SEK",
                result="won",
                bet_type="value",
                start_time=future_event.start_time,
            )
        )
        db_session.flush()
        assert _query_open_bets(db_session) == []

    def test_excludes_past_events(self, db_session, future_event):
        from src.analysis.rehedge_scanner import _query_open_bets

        future_event.start_time = datetime.now(UTC) - timedelta(hours=1)
        db_session.add(
            Bet(
                id=1,
                event_id=future_event.id,
                provider_id="unibet",
                market="spread",
                outcome="home",
                point=-2.5,
                odds=1.91,
                stake=100.0,
                currency="SEK",
                result="pending",
                bet_type="value",
                start_time=future_event.start_time,
            )
        )
        db_session.flush()
        assert _query_open_bets(db_session) == []

    def test_excludes_boost_bets_no_event(self, db_session):
        # Boost bets often lack event_id (free-text boost_event field instead).
        from src.analysis.rehedge_scanner import _query_open_bets

        db_session.add(Provider(id="unibet", name="Unibet"))
        db_session.add(
            Bet(
                id=1,
                event_id=None,
                provider_id="unibet",
                market="moneyline",
                outcome="home",
                odds=2.5,
                stake=50.0,
                currency="SEK",
                result="pending",
                bet_type="boost",
                boost_event="Arsenal vs Sunderland",
            )
        )
        db_session.flush()
        assert _query_open_bets(db_session) == []


class TestOppositeOutcome:
    """For spreads: opposite of 'home' is 'away' (and point flips sign).
    For totals: opposite of 'over' is 'under' (point stays the same)."""

    def test_spread_home_to_away(self):
        from src.analysis.rehedge_scanner import _opposite_outcome

        assert _opposite_outcome("spread", "home") == "away"
        assert _opposite_outcome("spread", "away") == "home"

    def test_total_over_to_under(self):
        from src.analysis.rehedge_scanner import _opposite_outcome

        assert _opposite_outcome("total", "over") == "under"
        assert _opposite_outcome("total", "under") == "over"

    def test_runline_handicap_aliases(self):
        # MLB runline and NHL puckline use home/away too.
        from src.analysis.rehedge_scanner import _opposite_outcome

        assert _opposite_outcome("runline", "home") == "away"
        assert _opposite_outcome("handicap", "away") == "home"

    def test_unknown_market_returns_none(self):
        from src.analysis.rehedge_scanner import _opposite_outcome

        assert _opposite_outcome("1x2", "home") is None  # 3-way, no clean opposite
        assert _opposite_outcome("moneyline", "home") is None  # no point/no middle


class TestPointForOppositeSide:
    def test_spread_point_flips_sign(self):
        from src.analysis.rehedge_scanner import _opposite_point

        # home -2.5 → away side prices at +2.5 in our normalised storage
        # (Betty stores both rows with the same magnitude; the outcome
        # column carries the side, NOT the sign. So opposite "point" equals
        # the original.)
        assert _opposite_point("spread", point=-2.5) == 2.5
        assert _opposite_point("spread", point=2.5) == -2.5

    def test_total_point_unchanged(self):
        from src.analysis.rehedge_scanner import _opposite_point

        assert _opposite_point("total", point=43.5) == 43.5

    def test_no_point_returns_none(self):
        from src.analysis.rehedge_scanner import _opposite_point

        assert _opposite_point("spread", point=None) is None
