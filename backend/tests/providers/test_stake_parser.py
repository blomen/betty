"""Tests for Stake.com GraphQL response parsing."""
import pytest
from src.providers.stake import parse_outcomes_to_market, parse_fixture


class TestParseOutcomesToMarket:
    def test_1x2_three_outcomes_with_draw(self):
        outcomes = [
            {"id": "1", "active": True, "odds": 2.10, "name": "Manchester City"},
            {"id": "2", "active": True, "odds": 3.40, "name": "Draw"},
            {"id": "3", "active": True, "odds": 3.20, "name": "Liverpool"},
        ]
        result = parse_outcomes_to_market(outcomes, "Match Winner", "football")
        assert result is not None
        assert result["type"] == "1x2"
        assert len(result["outcomes"]) == 3
        assert result["outcomes"][0] == {"name": "home", "odds": 2.10}
        assert result["outcomes"][1] == {"name": "draw", "odds": 3.40}
        assert result["outcomes"][2] == {"name": "away", "odds": 3.20}

    def test_moneyline_two_outcomes_no_draw(self):
        outcomes = [
            {"id": "1", "active": True, "odds": 1.80, "name": "Lakers"},
            {"id": "2", "active": True, "odds": 2.00, "name": "Celtics"},
        ]
        result = parse_outcomes_to_market(outcomes, "Match Winner", "basketball")
        assert result is not None
        assert result["type"] == "moneyline"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0] == {"name": "home", "odds": 1.80}
        assert result["outcomes"][1] == {"name": "away", "odds": 2.00}

    def test_draw_detected_by_x(self):
        outcomes = [
            {"id": "1", "active": True, "odds": 2.50, "name": "Team A"},
            {"id": "2", "active": True, "odds": 3.10, "name": "X"},
            {"id": "3", "active": True, "odds": 2.80, "name": "Team B"},
        ]
        result = parse_outcomes_to_market(outcomes, "Match Winner", "football")
        assert result is not None
        assert result["type"] == "1x2"
        assert result["outcomes"][1] == {"name": "draw", "odds": 3.10}

    def test_draw_detected_by_tie(self):
        outcomes = [
            {"id": "1", "active": True, "odds": 2.50, "name": "Team A"},
            {"id": "2", "active": True, "odds": 3.10, "name": "Tie"},
            {"id": "3", "active": True, "odds": 2.80, "name": "Team B"},
        ]
        result = parse_outcomes_to_market(outcomes, "Match Winner", "football")
        assert result is not None
        assert result["type"] == "1x2"

    def test_inactive_outcomes_returns_none(self):
        outcomes = [
            {"id": "1", "active": False, "odds": 2.10, "name": "Team A"},
            {"id": "2", "active": False, "odds": 3.20, "name": "Team B"},
        ]
        result = parse_outcomes_to_market(outcomes, "Match Winner", "football")
        assert result is None

    def test_empty_outcomes_returns_none(self):
        result = parse_outcomes_to_market([], "Match Winner", "football")
        assert result is None

    def test_mixed_active_inactive_all_active_required(self):
        outcomes = [
            {"id": "1", "active": True, "odds": 2.10, "name": "Team A"},
            {"id": "2", "active": False, "odds": 3.20, "name": "Team B"},
        ]
        result = parse_outcomes_to_market(outcomes, "Match Winner", "football")
        assert result is None


class TestParseFixture:
    def _make_football_fixture(self):
        return {
            "id": "abc123",
            "slug": "manchester-city-vs-liverpool",
            "status": "upcoming",
            "startTime": "2026-04-15T19:45:00.000Z",
            "home": {"name": "Manchester City"},
            "away": {"name": "Liverpool"},
            "tournament": {"name": "Premier League"},
            "betGroups": [
                {
                    "name": "Match Winner",
                    "outcomes": [
                        {"id": "1", "active": True, "odds": 2.10, "name": "Manchester City"},
                        {"id": "2", "active": True, "odds": 3.40, "name": "Draw"},
                        {"id": "3", "active": True, "odds": 3.20, "name": "Liverpool"},
                    ],
                }
            ],
        }

    def test_football_fixture_parsing(self):
        fixture = self._make_football_fixture()
        event = parse_fixture(fixture, "football", "stake")
        assert event is not None
        assert event.id == "stake_abc123"
        assert event.sport == "football"
        assert event.provider == "stake"
        assert event.league == "Premier League"
        assert event.start_time == "2026-04-15T19:45:00.000Z"

    def test_football_fixture_team_names_normalized(self):
        fixture = self._make_football_fixture()
        event = parse_fixture(fixture, "football", "stake")
        assert event is not None
        # normalize_team_name lowercases but does not strip "city" — verify lowercase
        assert event.home_team == "manchester city"
        assert event.away_team == "liverpool"
        assert event.home_team == event.home_team.lower()
        assert event.away_team == event.away_team.lower()

    def test_football_fixture_has_1x2_market(self):
        fixture = self._make_football_fixture()
        event = parse_fixture(fixture, "football", "stake")
        assert event is not None
        assert len(event.markets) == 1
        assert event.markets[0]["type"] == "1x2"

    def test_basketball_fixture_moneyline(self):
        fixture = {
            "id": "bball1",
            "slug": "lakers-vs-celtics",
            "status": "upcoming",
            "startTime": "2026-04-15T02:00:00.000Z",
            "home": {"name": "Los Angeles Lakers"},
            "away": {"name": "Boston Celtics"},
            "tournament": {"name": "NBA"},
            "betGroups": [
                {
                    "name": "Match Winner",
                    "outcomes": [
                        {"id": "1", "active": True, "odds": 1.80, "name": "Los Angeles Lakers"},
                        {"id": "2", "active": True, "odds": 2.00, "name": "Boston Celtics"},
                    ],
                }
            ],
        }
        event = parse_fixture(fixture, "basketball", "stake")
        assert event is not None
        assert len(event.markets) == 1
        assert event.markets[0]["type"] == "moneyline"

    def test_live_fixture_returns_none(self):
        fixture = {
            "id": "live1",
            "slug": "team-a-vs-team-b",
            "status": "in_progress",
            "startTime": "2026-04-15T19:45:00.000Z",
            "home": {"name": "Team A"},
            "away": {"name": "Team B"},
            "tournament": {"name": "Some League"},
            "betGroups": [
                {
                    "name": "Match Winner",
                    "outcomes": [
                        {"id": "1", "active": True, "odds": 2.10, "name": "Team A"},
                        {"id": "2", "active": True, "odds": 3.40, "name": "Draw"},
                        {"id": "3", "active": True, "odds": 3.20, "name": "Team B"},
                    ],
                }
            ],
        }
        event = parse_fixture(fixture, "football", "stake")
        assert event is None

    def test_ended_fixture_returns_none(self):
        fixture = {
            "id": "ended1",
            "slug": "team-a-vs-team-b",
            "status": "ended",
            "startTime": "2026-04-10T19:45:00.000Z",
            "home": {"name": "Team A"},
            "away": {"name": "Team B"},
            "tournament": {"name": "Some League"},
            "betGroups": [],
        }
        event = parse_fixture(fixture, "football", "stake")
        assert event is None

    def test_no_bet_groups_returns_none(self):
        fixture = {
            "id": "nogroups1",
            "slug": "team-a-vs-team-b",
            "status": "upcoming",
            "startTime": "2026-04-15T19:45:00.000Z",
            "home": {"name": "Team A"},
            "away": {"name": "Team B"},
            "tournament": {"name": "Some League"},
            "betGroups": [],
        }
        event = parse_fixture(fixture, "football", "stake")
        assert event is None

    def test_event_name_format(self):
        fixture = self._make_football_fixture()
        event = parse_fixture(fixture, "football", "stake")
        assert event is not None
        # Event name should be "Home vs Away" format
        assert " vs " in event.name

    def test_cancelled_fixture_returns_none(self):
        fixture = {
            "id": "cancel1",
            "slug": "team-a-vs-team-b",
            "status": "cancelled",
            "startTime": "2026-04-15T19:45:00.000Z",
            "home": {"name": "Team A"},
            "away": {"name": "Team B"},
            "tournament": {"name": "Some League"},
            "betGroups": [],
        }
        event = parse_fixture(fixture, "football", "stake")
        assert event is None

    def test_suspended_fixture_returns_none(self):
        fixture = {
            "id": "susp1",
            "slug": "team-a-vs-team-b",
            "status": "suspended",
            "startTime": "2026-04-15T19:45:00.000Z",
            "home": {"name": "Team A"},
            "away": {"name": "Team B"},
            "tournament": {"name": "Some League"},
            "betGroups": [],
        }
        event = parse_fixture(fixture, "football", "stake")
        assert event is None
