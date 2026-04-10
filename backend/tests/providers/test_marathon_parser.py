"""Tests for Marathonbet HTML parser."""
import pytest
from src.providers.marathon import parse_event_html, parse_page


# ---------------------------------------------------------------------------
# Helpers for building minimal HTML fixtures
# ---------------------------------------------------------------------------

def _make_sel(epr: str) -> str:
    """Build a minimal data-sel attribute value."""
    return (
        f"data-sel='{{"
        f'"cid":123456789,"prt":"CP","ewf":"1.0","epr":"{epr}",'
        f'"prices":{{"0":"1/1","1":"{epr}"}}'
        f"}}'"
    )


def _make_event_block(
    event_id: str,
    event_name: str,
    is_live: bool,
    eprs: list,
) -> str:
    """Build a minimal coupon-row div block with the given odds."""
    live_str = "true" if is_live else "false"
    sels = "\n".join(
        f'<span {_make_sel(epr)}></span>' for epr in eprs
    )
    return (
        f'<div class="bg coupon-row" '
        f'data-event-eventId="{event_id}" '
        f'data-event-treeId="99999" '
        f'data-event-name="{event_name}" '
        f'data-live="{live_str}">'
        f'{sels}'
        f'</div>'
    )


def _make_page(events: list) -> str:
    """Wrap multiple event blocks in a minimal page structure."""
    return "<html><body>" + "\n".join(events) + "</body></html>"


# ---------------------------------------------------------------------------
# parse_event_html tests
# ---------------------------------------------------------------------------

class TestParseEventHtml:
    def test_football_1x2_three_selections(self):
        block = _make_event_block("11111", "Atletico Madrid vs Barcelona", False, ["3.88", "3.50", "2.10"])
        event = parse_event_html(block, "football", "11111", "Atletico Madrid vs Barcelona")
        assert event is not None
        assert event.id == "marathon_11111"
        assert event.sport == "football"
        assert len(event.markets) == 1
        market = event.markets[0]
        assert market["type"] == "1x2"
        assert len(market["outcomes"]) == 3
        assert market["outcomes"][0] == {"name": "home", "odds": 3.88}
        assert market["outcomes"][1] == {"name": "draw", "odds": 3.50}
        assert market["outcomes"][2] == {"name": "away", "odds": 2.10}

    def test_basketball_moneyline_two_selections(self):
        block = _make_event_block("22222", "Lakers vs Celtics", False, ["1.80", "2.00"])
        event = parse_event_html(block, "basketball", "22222", "Lakers vs Celtics")
        assert event is not None
        assert event.id == "marathon_22222"
        market = event.markets[0]
        assert market["type"] == "moneyline"
        assert len(market["outcomes"]) == 2
        assert market["outcomes"][0] == {"name": "home", "odds": 1.80}
        assert market["outcomes"][1] == {"name": "away", "odds": 2.00}

    def test_tennis_moneyline(self):
        block = _make_event_block("33333", "Djokovic vs Alcaraz", False, ["1.55", "2.40"])
        event = parse_event_html(block, "tennis", "33333", "Djokovic vs Alcaraz")
        assert event is not None
        market = event.markets[0]
        assert market["type"] == "moneyline"

    def test_football_with_seven_selections_all_markets(self):
        """Seven selections: 3 x 1x2 + 2 x total + 2 x spread."""
        eprs = ["2.10", "3.40", "3.20", "1.90", "1.95", "1.85", "2.00"]
        block = _make_event_block("44444", "Real Madrid vs PSG", False, eprs)
        event = parse_event_html(block, "football", "44444", "Real Madrid vs PSG")
        assert event is not None
        assert len(event.markets) == 3
        types = [m["type"] for m in event.markets]
        assert types == ["1x2", "total", "spread"]
        total = event.markets[1]
        assert total["outcomes"][0] == {"name": "over", "odds": 1.90}
        assert total["outcomes"][1] == {"name": "under", "odds": 1.95}
        spread = event.markets[2]
        assert spread["outcomes"][0] == {"name": "home", "odds": 1.85}
        assert spread["outcomes"][1] == {"name": "away", "odds": 2.00}

    def test_basketball_with_five_selections_total_and_spread(self):
        """Basketball: 2 moneyline + 2 total + 1 spread (incomplete spread skipped)."""
        eprs = ["1.80", "2.00", "1.90", "1.95", "2.10"]
        block = _make_event_block("55555", "Warriors vs Heat", False, eprs)
        event = parse_event_html(block, "basketball", "55555", "Warriors vs Heat")
        assert event is not None
        # 2 moneyline + 2 total = offset 4, only 1 left for spread → no spread market
        assert len(event.markets) == 2
        assert event.markets[0]["type"] == "moneyline"
        assert event.markets[1]["type"] == "total"

    def test_team_names_normalized_lowercase(self):
        block = _make_event_block("66666", "Manchester City vs Liverpool", False, ["2.10", "3.40", "3.20"])
        event = parse_event_html(block, "football", "66666", "Manchester City vs Liverpool")
        assert event is not None
        assert event.home_team == event.home_team.lower()
        assert event.away_team == event.away_team.lower()
        assert "manchester" in event.home_team
        assert "liverpool" in event.away_team

    def test_event_id_format(self):
        block = _make_event_block("12345678", "Team A vs Team B", False, ["2.00", "3.20", "3.50"])
        event = parse_event_html(block, "football", "12345678", "Team A vs Team B")
        assert event is not None
        assert event.id == "marathon_12345678"

    def test_no_selections_returns_none(self):
        block = (
            '<div class="bg coupon-row" data-event-eventId="99999" '
            'data-event-name="Team A vs Team B" data-live="false">'
            '<span>no odds here</span></div>'
        )
        event = parse_event_html(block, "football", "99999", "Team A vs Team B")
        assert event is None

    def test_zero_odds_returns_none(self):
        block = _make_event_block("88888", "Team A vs Team B", False, ["0", "0", "0"])
        event = parse_event_html(block, "football", "88888", "Team A vs Team B")
        assert event is None

    def test_invalid_odds_string_returns_none(self):
        block = (
            '<div class="bg coupon-row" data-event-eventId="77777" '
            'data-event-name="Team A vs Team B" data-live="false">'
            '<span data-sel=\'{"epr":"N/A"}\'></span>'
            '<span data-sel=\'{"epr":"N/A"}\'></span>'
            '<span data-sel=\'{"epr":"N/A"}\'></span>'
            '</div>'
        )
        event = parse_event_html(block, "football", "77777", "Team A vs Team B")
        assert event is None

    def test_event_name_preserved_raw(self):
        block = _make_event_block("11112", "Atletico Madrid vs Barcelona", False, ["3.88", "3.50", "2.10"])
        event = parse_event_html(block, "football", "11112", "Atletico Madrid vs Barcelona")
        assert event is not None
        assert event.name == "Atletico Madrid vs Barcelona"

    def test_provider_field(self):
        block = _make_event_block("11113", "Team A vs Team B", False, ["2.00", "3.20", "3.50"])
        event = parse_event_html(block, "football", "11113", "Team A vs Team B")
        assert event is not None
        # provider is set by parse_page; parse_event_html sets it to "marathon"
        assert event.provider == "marathon"

    def test_too_few_selections_for_sport_returns_none(self):
        """Football needs 3 match-winner odds; 2 is not enough."""
        block = _make_event_block("00001", "Team A vs Team B", False, ["2.00", "3.20"])
        event = parse_event_html(block, "football", "00001", "Team A vs Team B")
        assert event is None

    def test_too_few_selections_basketball_returns_none(self):
        """Basketball needs at least 2 selections."""
        block = _make_event_block("00002", "Team A vs Team B", False, ["2.00"])
        event = parse_event_html(block, "basketball", "00002", "Team A vs Team B")
        assert event is None


# ---------------------------------------------------------------------------
# parse_page tests
# ---------------------------------------------------------------------------

class TestParsePage:
    def _football_block(self, event_id: str, name: str, eprs: list, live: bool = False) -> str:
        return _make_event_block(event_id, name, live, eprs)

    def test_parse_page_returns_list_of_events(self):
        blocks = [
            self._football_block("1", "Team A vs Team B", ["2.00", "3.20", "3.50"]),
            self._football_block("2", "Team C vs Team D", ["1.80", "3.80", "4.20"]),
        ]
        html = _make_page(blocks)
        events = parse_page(html, "football", "marathon")
        assert len(events) == 2

    def test_parse_page_skips_live_events(self):
        blocks = [
            self._football_block("10", "Live Team A vs Team B", ["2.00", "3.20", "3.50"], live=True),
            self._football_block("11", "Pre Team C vs Team D", ["1.80", "3.80", "4.20"], live=False),
        ]
        html = _make_page(blocks)
        events = parse_page(html, "football", "marathon")
        assert len(events) == 1
        assert events[0].id == "marathon_11"

    def test_parse_page_sets_provider_id(self):
        blocks = [self._football_block("20", "Team A vs Team B", ["2.00", "3.20", "3.50"])]
        html = _make_page(blocks)
        events = parse_page(html, "football", "marathon_custom")
        assert events[0].provider == "marathon_custom"

    def test_parse_page_empty_html_returns_empty(self):
        events = parse_page("<html><body></body></html>", "football", "marathon")
        assert events == []

    def test_parse_page_skips_events_with_bad_odds(self):
        good = self._football_block("30", "Good Team vs Other Team", ["2.00", "3.20", "3.50"])
        bad = (
            '<div class="bg coupon-row" data-event-eventId="31" '
            'data-event-name="Bad Team vs Other" data-live="false">'
            '</div>'
        )
        html = _make_page([good, bad])
        events = parse_page(html, "football", "marathon")
        assert len(events) == 1
        assert events[0].id == "marathon_30"

    def test_parse_page_team_names_lowercase(self):
        blocks = [self._football_block("40", "Manchester United vs Arsenal", ["2.10", "3.40", "3.20"])]
        html = _make_page(blocks)
        events = parse_page(html, "football", "marathon")
        assert len(events) == 1
        e = events[0]
        assert e.home_team == e.home_team.lower()
        assert e.away_team == e.away_team.lower()

    def test_parse_page_basketball_moneyline(self):
        block = _make_event_block("50", "Lakers vs Celtics", False, ["1.80", "2.00"])
        html = _make_page([block])
        events = parse_page(html, "basketball", "marathon")
        assert len(events) == 1
        assert events[0].markets[0]["type"] == "moneyline"

    def test_parse_page_all_live_returns_empty(self):
        blocks = [
            self._football_block("60", "Live A vs B", ["2.00", "3.20", "3.50"], live=True),
            self._football_block("61", "Live C vs D", ["1.80", "3.80", "4.20"], live=True),
        ]
        html = _make_page(blocks)
        events = parse_page(html, "football", "marathon")
        assert events == []
