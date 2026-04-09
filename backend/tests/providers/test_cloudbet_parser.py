"""Tests for Cloudbet REST Feed API response parsing."""
import pytest
from src.providers.cloudbet import parse_selections_to_market, parse_event


class TestParseSelectionsToMarket:
    def test_moneyline_two_selections(self):
        selections = [
            {"outcome": "home", "params": "", "price": 1.80, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.00, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "basketball.moneyline")
        assert result is not None
        assert result["type"] == "moneyline"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0] == {"name": "home", "odds": 1.80}
        assert result["outcomes"][1] == {"name": "away", "odds": 2.00}

    def test_1x2_three_selections_with_draw(self):
        selections = [
            {"outcome": "home", "params": "", "price": 2.80, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "draw", "params": "", "price": 3.40, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.50, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.match_odds")
        assert result is not None
        assert result["type"] == "1x2"
        assert len(result["outcomes"]) == 3
        assert result["outcomes"][0] == {"name": "home", "odds": 2.80}
        assert result["outcomes"][1] == {"name": "draw", "odds": 3.40}
        assert result["outcomes"][2] == {"name": "away", "odds": 2.50}

    def test_handicap_selections_main_line_only(self):
        selections = [
            {"outcome": "home", "params": "handicap=-1.5", "price": 2.10, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "handicap=-1.5", "price": 1.75, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "home", "params": "handicap=-2.5", "price": 3.00, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "handicap=-2.5", "price": 1.40, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.asian_handicap")
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        # Main line = smallest absolute handicap = 1.5
        home = next(o for o in result["outcomes"] if o["name"] == "home")
        away = next(o for o in result["outcomes"] if o["name"] == "away")
        assert home["point"] == -1.5
        assert home["odds"] == 2.10
        assert away["point"] == 1.5
        assert away["odds"] == 1.75

    def test_totals_selections_main_line_only(self):
        selections = [
            {"outcome": "over", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "over", "params": "total=3.5", "price": 2.20, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=3.5", "price": 1.65, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.total_goals")
        assert result is not None
        assert result["type"] == "total"
        assert len(result["outcomes"]) == 2
        # Main line = smallest total = 2.5
        over = next(o for o in result["outcomes"] if o["name"] == "over")
        under = next(o for o in result["outcomes"] if o["name"] == "under")
        assert over["point"] == 2.5
        assert over["odds"] == 1.90
        assert under["point"] == 2.5
        assert under["odds"] == 1.90

    def test_disabled_selection_returns_none(self):
        selections = [
            {"outcome": "home", "params": "", "price": 2.80, "status": "SELECTION_DISABLED", "side": "BACK"},
            {"outcome": "draw", "params": "", "price": 3.40, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.50, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.match_odds")
        assert result is None

    def test_empty_selections_returns_none(self):
        result = parse_selections_to_market([], "soccer.match_odds")
        assert result is None

    def test_suspended_selection_returns_none(self):
        selections = [
            {"outcome": "home", "params": "", "price": 2.80, "status": "SELECTION_SUSPENDED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.50, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "basketball.moneyline")
        assert result is None

    def test_market_key_basketball_handicap(self):
        selections = [
            {"outcome": "home", "params": "handicap=-5.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "away", "params": "handicap=-5.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "basketball.handicap")
        assert result is not None
        assert result["type"] == "spread"

    def test_market_key_basketball_totals(self):
        selections = [
            {"outcome": "over", "params": "total=220.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=220.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "basketball.totals")
        assert result is not None
        assert result["type"] == "total"
        over = next(o for o in result["outcomes"] if o["name"] == "over")
        assert over["point"] == 220.5

    def test_totals_single_line(self):
        """Single-line total — no filtering needed, just return it."""
        selections = [
            {"outcome": "over", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
            {"outcome": "under", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(selections, "soccer.total_goals")
        assert result is not None
        assert result["type"] == "total"
        assert len(result["outcomes"]) == 2


class TestParseEvent:
    def _make_football_event(self):
        return {
            "id": 12345,
            "name": "Manchester City V Liverpool",
            "status": "TRADING",
            "startTime": "2026-04-15T15:00:00Z",
            "home": {"name": "Manchester City", "key": "c1-manchester-city"},
            "away": {"name": "Liverpool", "key": "c2-liverpool"},
            "markets": {
                "soccer.match_odds": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {"outcome": "home", "params": "", "price": 2.80, "status": "SELECTION_ENABLED", "side": "BACK"},
                                {"outcome": "draw", "params": "", "price": 3.40, "status": "SELECTION_ENABLED", "side": "BACK"},
                                {"outcome": "away", "params": "", "price": 2.50, "status": "SELECTION_ENABLED", "side": "BACK"},
                            ]
                        }
                    }
                },
                "soccer.asian_handicap": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {"outcome": "home", "params": "handicap=-1.5", "price": 2.10, "status": "SELECTION_ENABLED", "side": "BACK"},
                                {"outcome": "away", "params": "handicap=-1.5", "price": 1.75, "status": "SELECTION_ENABLED", "side": "BACK"},
                                {"outcome": "home", "params": "handicap=-2.5", "price": 3.00, "status": "SELECTION_ENABLED", "side": "BACK"},
                                {"outcome": "away", "params": "handicap=-2.5", "price": 1.40, "status": "SELECTION_ENABLED", "side": "BACK"},
                            ]
                        }
                    }
                },
                "soccer.total_goals": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {"outcome": "over", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
                                {"outcome": "under", "params": "total=2.5", "price": 1.90, "status": "SELECTION_ENABLED", "side": "BACK"},
                            ]
                        }
                    }
                },
            },
        }

    def test_football_event_all_markets(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        market_types = {m["type"] for m in event.markets}
        assert "1x2" in market_types
        assert "spread" in market_types
        assert "total" in market_types

    def test_football_event_basic_fields(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        assert event.id == "cloudbet_12345"
        assert event.sport == "football"
        assert event.provider == "cloudbet"
        assert event.start_time == "2026-04-15T15:00:00Z"

    def test_football_event_team_names_normalized(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        assert event.home_team == "manchester city"
        assert event.away_team == "liverpool"

    def test_live_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "TRADING_LIVE"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_resulted_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "RESULTED"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_cancelled_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "CANCELLED"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_suspended_event_returns_none(self):
        event_data = self._make_football_event()
        event_data["status"] = "SUSPENDED"
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_no_home_returns_none(self):
        event_data = self._make_football_event()
        event_data["home"] = None
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_no_away_returns_none(self):
        event_data = self._make_football_event()
        event_data["away"] = None
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_event_name_format(self):
        event_data = self._make_football_event()
        event = parse_event(event_data, "football", "cloudbet")
        assert event is not None
        assert " vs " in event.name

    def test_no_markets_returns_none(self):
        event_data = self._make_football_event()
        event_data["markets"] = {}
        result = parse_event(event_data, "football", "cloudbet")
        assert result is None

    def test_basketball_event_moneyline(self):
        event_data = {
            "id": 99001,
            "name": "Lakers V Celtics",
            "status": "TRADING",
            "startTime": "2026-04-15T02:00:00Z",
            "home": {"name": "Los Angeles Lakers", "key": "c1-lakers"},
            "away": {"name": "Boston Celtics", "key": "c2-celtics"},
            "markets": {
                "basketball.moneyline": {
                    "submarkets": {
                        "period=ft": {
                            "selections": [
                                {"outcome": "home", "params": "", "price": 1.80, "status": "SELECTION_ENABLED", "side": "BACK"},
                                {"outcome": "away", "params": "", "price": 2.00, "status": "SELECTION_ENABLED", "side": "BACK"},
                            ]
                        }
                    }
                }
            },
        }
        event = parse_event(event_data, "basketball", "cloudbet")
        assert event is not None
        assert len(event.markets) == 1
        assert event.markets[0]["type"] == "moneyline"
