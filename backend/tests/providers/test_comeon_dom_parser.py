from __future__ import annotations

import pytest
from datetime import datetime
from freezegun import freeze_time
from zoneinfo import ZoneInfo

from src.providers.comeon_dom_parser import (
    parse_aria_label,
    parse_swedish_datetime,
    build_outcomes_from_labels,
    select_market_pills,
)


class TestParseAriaLabel:
    def test_1x2_home(self):
        result = parse_aria_label("Lag till val: Burnley FC, Odds: 4.18")
        assert result == {"name": "Burnley FC", "odds": 4.18}

    def test_1x2_draw(self):
        result = parse_aria_label("Lag till val: Oavgjort, Odds: 3.92")
        assert result == {"name": "Oavgjort", "odds": 3.92}

    def test_spread_positive(self):
        result = parse_aria_label("Lag till val: Burnley FC (+0.5), Odds: 1.97")
        assert result == {"name": "Burnley FC", "odds": 1.97, "point": 0.5}

    def test_spread_negative(self):
        result = parse_aria_label("Lag till val: Bournemouth (-0.5), Odds: 1.81")
        assert result == {"name": "Bournemouth", "odds": 1.81, "point": -0.5}

    def test_total_over(self):
        result = parse_aria_label("Lag till val: Over 2.5, Odds: 1.71")
        assert result == {"name": "Over 2.5", "odds": 1.71, "point": 2.5}

    def test_total_under(self):
        result = parse_aria_label("Lag till val: Under 2.5, Odds: 2.16")
        assert result == {"name": "Under 2.5", "odds": 2.16, "point": 2.5}

    def test_invalid_format(self):
        assert parse_aria_label("some random text") is None

    def test_suspended_no_odds(self):
        assert parse_aria_label("Lag till val: Burnley FC, Odds: ") is None

    def test_team_name_with_parentheses_non_spread(self):
        result = parse_aria_label("Lag till val: AIK (Dam), Odds: 2.50")
        assert result == {"name": "AIK (Dam)", "odds": 2.5}

    def test_spread_integer_point(self):
        result = parse_aria_label("Lag till val: Real Madrid (+1), Odds: 1.45")
        assert result == {"name": "Real Madrid", "odds": 1.45, "point": 1.0}


class TestParseSwedishDatetime:
    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_idag(self):
        result = parse_swedish_datetime("Idag16:00")
        assert result == datetime(2026, 3, 14, 16, 0, tzinfo=ZoneInfo("Europe/Stockholm"))

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_imorgon(self):
        result = parse_swedish_datetime("Imorgon15:00")
        assert result == datetime(2026, 3, 15, 15, 0, tzinfo=ZoneInfo("Europe/Stockholm"))

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_named_day_with_date(self):
        result = parse_swedish_datetime("Fre 20 Mars21:00")
        assert result == datetime(2026, 3, 20, 21, 0, tzinfo=ZoneInfo("Europe/Stockholm"))

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_short_month(self):
        result = parse_swedish_datetime("Lör 21 Mar21:00")
        assert result == datetime(2026, 3, 21, 21, 0, tzinfo=ZoneInfo("Europe/Stockholm"))

    @freeze_time("2026-12-28 12:00:00", tz_offset=1)
    def test_year_rollover(self):
        result = parse_swedish_datetime("Fre 3 Jan18:00")
        assert result == datetime(2027, 1, 3, 18, 0, tzinfo=ZoneInfo("Europe/Stockholm"))

    def test_invalid_text(self):
        assert parse_swedish_datetime("random text") is None

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_date_header_format(self):
        result = parse_swedish_datetime("Idag 14 Mars")
        assert result == datetime(2026, 3, 14, 0, 0, tzinfo=ZoneInfo("Europe/Stockholm"))


class TestBuildOutcomesFromLabels:
    def test_1x2_three_way(self):
        labels = [
            {"name": "Burnley FC", "odds": 4.18},
            {"name": "Oavgjort", "odds": 3.92},
            {"name": "Bournemouth", "odds": 1.88},
        ]
        market = build_outcomes_from_labels(labels, "1x2", "Burnley FC", "Bournemouth")
        assert market["type"] == "1x2"
        assert len(market["outcomes"]) == 3
        assert market["outcomes"][0] == {"name": "home", "odds": 4.18}
        assert market["outcomes"][1] == {"name": "draw", "odds": 3.92}
        assert market["outcomes"][2] == {"name": "away", "odds": 1.88}

    def test_moneyline_two_way(self):
        labels = [
            {"name": "LA Lakers", "odds": 1.55},
            {"name": "Boston Celtics", "odds": 2.40},
        ]
        market = build_outcomes_from_labels(labels, "1x2", "LA Lakers", "Boston Celtics")
        assert market["type"] == "moneyline"
        assert len(market["outcomes"]) == 2

    def test_spread(self):
        labels = [
            {"name": "Burnley FC", "odds": 1.97, "point": 0.5},
            {"name": "Bournemouth", "odds": 1.81, "point": -0.5},
        ]
        market = build_outcomes_from_labels(labels, "spread", "Burnley FC", "Bournemouth")
        assert market["type"] == "spread"
        assert market["outcomes"][0] == {"name": "home", "odds": 1.97, "point": 0.5}
        assert market["outcomes"][1] == {"name": "away", "odds": 1.81, "point": -0.5}

    def test_total(self):
        labels = [
            {"name": "Over 2.5", "odds": 1.71, "point": 2.5},
            {"name": "Under 2.5", "odds": 2.16, "point": 2.5},
        ]
        market = build_outcomes_from_labels(labels, "total", "", "")
        assert market["type"] == "total"
        assert market["outcomes"][0] == {"name": "over", "odds": 1.71, "point": 2.5}
        assert market["outcomes"][1] == {"name": "under", "odds": 2.16, "point": 2.5}

    def test_empty_labels(self):
        assert build_outcomes_from_labels([], "1x2", "A", "B") is None


class TestSelectMarketPills:
    def test_football_pills(self):
        pills = ["Populara", "Båda lagen gör mål", "Over/Under mål", "Handikapp", "Dubbelchans", "Over/Under mål i 1a halvlek"]
        spread, total = select_market_pills(pills, "football")
        assert spread == "Handikapp"
        assert total == "Over/Under mål"

    def test_ice_hockey_ot_pills(self):
        pills = ["Populara", "Vinnare (Inkl. övertid)", "Handikapp (Inkl. övertid)", "Over/Under mål (Inkl. övertid)"]
        spread, total = select_market_pills(pills, "ice_hockey")
        assert spread == "Handikapp (Inkl. övertid)"
        assert total == "Over/Under mål (Inkl. övertid)"

    def test_basketball_ot_pills(self):
        pills = ["Populara", "Over/Under poäng (Inkl övertid)", "Handikapp (Inkl övertid)"]
        spread, total = select_market_pills(pills, "basketball")
        assert spread == "Handikapp (Inkl övertid)"
        assert total == "Over/Under poäng (Inkl övertid)"

    def test_no_spread_pill(self):
        pills = ["Populara", "Over/Under mål"]
        spread, total = select_market_pills(pills, "football")
        assert spread is None
        assert total == "Over/Under mål"

    def test_no_pills(self):
        spread, total = select_market_pills([], "football")
        assert spread is None
        assert total is None

    def test_ice_hockey_fallback_no_ot(self):
        pills = ["Populara", "Handikapp", "Over/Under mål"]
        spread, total = select_market_pills(pills, "ice_hockey")
        assert spread == "Handikapp"
        assert total == "Over/Under mål"
