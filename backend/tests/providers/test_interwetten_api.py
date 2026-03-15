"""Tests for interwetten JSON API response parsing."""
import pytest
from src.providers.interwetten_api_parser import (
    parse_event_json,
    parse_spread_from_template,
    parse_total_from_template,
    parse_main_market,
    parse_top_leagues_response,
)

class TestParseMainMarket:
    def test_1x2_three_way(self):
        main = {"outcomes": [
            {"name": "Leverkusen", "tip": "1", "odd": 4.9},
            {"name": "Draw", "tip": "X", "odd": 4.1},
            {"name": "Bayern Munich", "tip": "2", "odd": 1.65},
        ]}
        result = parse_main_market(main, "Team A", "Team B")
        assert result["type"] == "1x2"
        assert len(result["outcomes"]) == 3
        assert result["outcomes"][0] == {"name": "home", "odds": 4.9}
        assert result["outcomes"][1] == {"name": "draw", "odds": 4.1}
        assert result["outcomes"][2] == {"name": "away", "odds": 1.65}

    def test_moneyline_two_way(self):
        main = {"outcomes": [
            {"name": "Lakers", "tip": "1", "odd": 1.8},
            {"name": "Celtics", "tip": "2", "odd": 2.0},
        ]}
        result = parse_main_market(main, "Lakers", "Celtics")
        assert result["type"] == "moneyline"
        assert len(result["outcomes"]) == 2

    def test_empty_outcomes(self):
        assert parse_main_market({"outcomes": []}, "A", "B") is None

    def test_locked_outcomes_skipped(self):
        main = {"outcomes": [
            {"name": "A", "tip": "1", "odd": 0},
            {"name": "B", "tip": "2", "odd": 1.5},
        ]}
        result = parse_main_market(main, "A", "B")
        assert result is None


class TestParseSpreadFromTemplate:
    def test_asian_handicap(self):
        template = {"name": "Asian Handicap", "markets": [{"outcomes": [
            {"name": "Leverkusen (+1)", "tip": "1", "odd": 1.9},
            {"name": "Bayern Munich (-1)", "tip": "2", "odd": 1.83},
        ]}]}
        result = parse_spread_from_template(template)
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0]["point"] == 1.0
        assert result["outcomes"][1]["point"] == -1.0

    def test_handicap_generic(self):
        template = {"name": "Handicap", "markets": [{"outcomes": [
            {"name": "Lakers (+5.5)", "tip": "1", "odd": 1.85},
            {"name": "Celtics (-5.5)", "tip": "2", "odd": 1.95},
        ]}]}
        result = parse_spread_from_template(template)
        assert result is not None
        assert result["outcomes"][0]["point"] == 5.5

    def test_no_point_in_name(self):
        template = {"name": "Asian Handicap", "markets": [{"outcomes": [
            {"name": "Team A", "tip": "1", "odd": 1.9},
            {"name": "Team B", "tip": "2", "odd": 1.83},
        ]}]}
        result = parse_spread_from_template(template)
        assert result is None

    def test_empty_markets(self):
        template = {"name": "Asian Handicap", "markets": []}
        assert parse_spread_from_template(template) is None


class TestParseTotalFromTemplate:
    def test_how_many_goals(self):
        template = {"name": "How many goals", "markets": [{"outcomes": [
            {"name": "Over 3.5", "tip": " ", "odd": 1.7},
            {"name": "Under 3.5", "tip": " ", "odd": 2.1},
        ]}]}
        result = parse_total_from_template(template)
        assert result is not None
        assert result["type"] == "total"
        assert result["outcomes"][0] == {"name": "over", "odds": 1.7, "point": 3.5}
        assert result["outcomes"][1] == {"name": "under", "odds": 2.1, "point": 3.5}

    def test_over_under_basketball(self):
        template = {"name": "Over/Under", "markets": [{"outcomes": [
            {"name": "Over 220.5", "tip": " ", "odd": 1.85},
            {"name": "Under 220.5", "tip": " ", "odd": 1.95},
        ]}]}
        result = parse_total_from_template(template)
        assert result is not None
        assert result["outcomes"][0]["point"] == 220.5

    def test_empty_markets(self):
        template = {"name": "How many goals", "markets": []}
        assert parse_total_from_template(template) is None


class TestParseEventJson:
    def test_full_event_with_all_markets(self):
        data = {
            "event": {
                "id": 18058921,
                "name": "Leverkusen - Bayern Munich",
                "startTime": "2026-03-14T14:30:00Z",
                "mainMarket": {"outcomes": [
                    {"name": "Leverkusen", "tip": "1", "odd": 4.9},
                    {"name": "Draw", "tip": "X", "odd": 4.1},
                    {"name": "Bayern Munich", "tip": "2", "odd": 1.65},
                ]},
                "templateGroups": [
                    {"name": "Asian Handicaps", "templates": [
                        {"name": "Asian Handicap", "markets": [{"outcomes": [
                            {"name": "Leverkusen (+1)", "tip": "1", "odd": 1.9},
                            {"name": "Bayern Munich (-1)", "tip": "2", "odd": 1.83},
                        ]}]}
                    ]},
                    {"name": "Goals", "templates": [
                        {"name": "How many goals", "markets": [{"outcomes": [
                            {"name": "Over 2.5", "tip": " ", "odd": 1.7},
                            {"name": "Under 2.5", "tip": " ", "odd": 2.1},
                        ]}]}
                    ]},
                ],
            },
            "league": {"id": 1019, "name": "Germany Bundesliga"},
            "sport": {"id": 10, "name": "Football"},
        }
        event = parse_event_json(data, provider_id="interwetten")
        assert event is not None
        assert event.home_team is not None
        assert event.away_team is not None
        assert len(event.markets) == 3
        types = [m["type"] for m in event.markets]
        assert "1x2" in types
        assert "spread" in types
        assert "total" in types

    def test_event_without_template_groups(self):
        data = {
            "event": {
                "id": 123,
                "name": "A - B",
                "startTime": "2026-03-14T14:30:00Z",
                "mainMarket": {"outcomes": [
                    {"name": "A", "tip": "1", "odd": 1.5},
                    {"name": "B", "tip": "2", "odd": 2.5},
                ]},
            },
            "league": {"id": 1, "name": "Test"},
            "sport": {"id": 10, "name": "Football"},
        }
        event = parse_event_json(data, provider_id="interwetten")
        assert event is not None
        assert len(event.markets) == 1


class TestParseTopLeaguesResponse:
    def test_extracts_event_ids_and_hrefs(self):
        data = {
            "leagues": [{
                "id": 1021, "name": "England Premier League",
                "events": [
                    {"id": 18058921, "name": "Arsenal - Everton",
                     "startTime": "2026-03-15T15:00:00Z",
                     "marketCount": 45,
                     "href": "/en/sportsbook/e/18058921/arsenal---everton",
                     "mainMarket": {"outcomes": [
                         {"name": "Arsenal", "tip": "1", "odd": 1.5},
                         {"name": "Draw", "tip": "X", "odd": 4.1},
                         {"name": "Everton", "tip": "2", "odd": 6.5},
                     ]}},
                ],
            }],
        }
        events_info = parse_top_leagues_response(data)
        assert len(events_info) == 1
        assert events_info[0]["id"] == 18058921
        assert events_info[0]["href"] == "/en/sportsbook/e/18058921/arsenal---everton"
        assert events_info[0]["league"] == "England Premier League"

    def test_empty_leagues(self):
        assert parse_top_leagues_response({"leagues": []}) == []
