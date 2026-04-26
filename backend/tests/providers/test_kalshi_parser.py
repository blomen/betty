"""Tests for Kalshi market parser."""

import json
from pathlib import Path

import pytest

from src.providers.kalshi import (
    KalshiRetriever,
    _extract_teams_from_title,
    parse_event,
    series_to_sport,
)


class TestExtractTeamsFromTitle:
    def test_us_at_convention_flips_order(self):
        # "Lakers at Warriors" → visitor at host → home=Warriors, away=Lakers.
        assert _extract_teams_from_title("Lakers at Warriors") == ("Warriors", "Lakers")

    def test_us_at_symbol_convention_flips_order(self):
        assert _extract_teams_from_title("Lakers @ Warriors") == ("Warriors", "Lakers")

    def test_vs_convention_preserves_order(self):
        # European: "Arsenal vs Chelsea" → home=Arsenal, away=Chelsea.
        assert _extract_teams_from_title("Arsenal vs Chelsea") == ("Arsenal", "Chelsea")

    def test_game_prefix_stripped(self):
        # Real Kalshi NBA titles include "Game N:" prefix.
        home, away = _extract_teams_from_title("Game 4: Los Angeles L at Houston")
        assert home == "Houston"
        assert away == "Los Angeles L"

    def test_no_separator_returns_empty_away(self):
        assert _extract_teams_from_title("Mystery") == ("Mystery", "")


class TestSeriesToSport:
    @pytest.mark.parametrize(
        "ticker,expected",
        [
            ("KXNBAGAME-26APR18LALGSW-LAL", "basketball"),
            ("KXNFLGAME-26WEEK5-KC", "american_football"),
            ("KXMLBGAME-26APR18NYY-NYY", "baseball"),
            ("KXNHLGAME-26APR18BOS-BOS", "ice_hockey"),
            ("KXNCAAFGAME-26WEEK3-ALA", "american_football"),
            ("KXNCAABGAME-26MAR21-DUKE", "basketball"),
            ("KXTENNISAUSOPEN-26-DJOKOVIC", "tennis"),
            ("KXUFC300-26-JONES", "mma"),
            ("KXBOXINGFURY-26-FURY", "boxing"),
            ("KXEPL-26MAY-ARS", "football"),
            ("KXUCL-26APR-RMA", "football"),
            ("KXWC26-26JUL-ARG", "football"),
        ],
    )
    def test_known_prefixes(self, ticker, expected):
        assert series_to_sport(ticker) == expected

    def test_unknown_prefix_returns_none(self):
        assert series_to_sport("KXWEATHERNYC-26-75F") is None
        assert series_to_sport("KXPREZ-26-DEM") is None


class TestParseEvent:
    def _event(self, ticker: str, markets: list[dict], title: str = "LAL vs GSW") -> dict:
        return {
            "event_ticker": ticker,
            "title": title,
            "markets": markets,
        }

    def _market(
        self,
        ticker: str,
        yes_sub_title: str,
        yes_ask: float,
        volume: float = 5000.0,
        status: str = "active",
    ) -> dict:
        # Match live Kalshi schema: yes_ask_dollars (float 0–1), volume_fp (USD notional).
        return {
            "ticker": ticker,
            "status": status,
            "yes_sub_title": yes_sub_title,
            "yes_ask_dollars": yes_ask,
            "no_ask_dollars": round(1.0 - yes_ask, 4),
            "volume_fp": volume,
        }

    def test_nba_moneyline_two_contracts(self):
        # "Lakers at Warriors" → US convention: Lakers is the visitor, Warriors
        # is home. Kalshi's yes_sub_title identifies each contract's team, so
        # we must NOT rely on volume ordering.
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                # Lakers has HIGHER volume but must still be "away".
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "Lakers", 0.60, volume=9000),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "Warriors", 0.42, volume=3000),
            ],
            title="Lakers at Warriors",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "basketball"
        assert result.provider == "kalshi"
        # Canonical-team override resolves GSW → "warriors", LAL → "lakers"
        # via KALSHI_TICKER_CODES so home/away match Pinnacle's stored aliases
        # (lowercase). Pre-fix this assertion checked title-derived "Warriors"
        # / "Lakers" but the canonical lookup runs after _match_market_to_side
        # and overrides them — the case-difference is intentional, not a bug.
        assert result.home_team == "warriors"
        assert result.away_team == "lakers"
        assert len(result.markets) == 1
        mkt = result.markets[0]
        assert mkt["type"] == "moneyline"
        assert len(mkt["outcomes"]) == 2
        # Outcomes must be labelled by team-match, not volume order.
        by_name = {o["name"]: o for o in mkt["outcomes"]}
        assert set(by_name) == {"home", "away"}
        # Lakers @ 0.60 → effective=0.6048 → odds ~1.653 is the AWAY outcome.
        assert 1.6 < by_name["away"]["odds"] < 1.7
        # Warriors @ 0.42 → effective=0.4249 → odds ~2.35 is the HOME outcome.
        assert 2.3 < by_name["home"]["odds"] < 2.4

    def test_below_volume_threshold_dropped(self):
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.60, volume=50),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.42, volume=50),
            ],
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_all_50_50_dropped(self):
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.50, volume=5000),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.50, volume=5000),
            ],
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_unknown_sport_skipped(self):
        event = self._event(
            "KXWEATHERNYC-26-75F",
            [self._market("KXWEATHERNYC-26-75F-YES", "75F", 0.30, volume=5000)],
        )
        assert parse_event(event, min_volume_usd=100.0, fee_rate=0.02) is None

    def test_soccer_3way_1x2(self):
        # "Arsenal vs Chelsea" → European convention: Arsenal is home.
        event = self._event(
            "KXEPL-26MAY-ARSCHE",
            [
                self._market("KXEPL-26MAY-ARSCHE-ARS", "Arsenal", 0.55),
                self._market("KXEPL-26MAY-ARSCHE-DRAW", "Draw", 0.25),
                self._market("KXEPL-26MAY-ARSCHE-CHE", "Chelsea", 0.22),
            ],
            title="Arsenal vs Chelsea",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "football"
        assert result.home_team == "Arsenal"
        assert result.away_team == "Chelsea"
        mkt = result.markets[0]
        assert mkt["type"] == "1x2"
        assert len(mkt["outcomes"]) == 3
        names = {o["name"] for o in mkt["outcomes"]}
        assert names == {"home", "draw", "away"}
        # The team tagged as HOME must indeed be the one matching Arsenal's
        # yes_sub_title, not a volume-dependent assignment.
        by_name = {o["name"]: o for o in mkt["outcomes"]}
        assert by_name["home"]["provider_meta"]["ticker"] == "KXEPL-26MAY-ARSCHE-ARS"
        assert by_name["away"]["provider_meta"]["ticker"] == "KXEPL-26MAY-ARSCHE-CHE"

    def test_unresolved_title_skipped(self):
        """If yes_sub_title and ticker suffix both fail to map to home/away,
        the event is dropped rather than emitting bad data."""
        event = self._event(
            "KXNBAGAME-26APR18AAABBB",
            [
                # yes_sub_title and ticker suffix don't match either title side.
                self._market("KXNBAGAME-26APR18AAABBB-FOO", "Unknown Contract A", 0.55),
                self._market("KXNBAGAME-26APR18AAABBB-BAR", "Unknown Contract B", 0.45),
            ],
            title="Lakers at Warriors",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_unresolved_title_no_separator_skipped(self):
        """Title with no ' at ' / ' vs ' separator cannot yield home/away."""
        event = self._event(
            "KXNBAGAME-26APR18X",
            [
                self._market("KXNBAGAME-26APR18X-A", "Team A", 0.55),
                self._market("KXNBAGAME-26APR18X-B", "Team B", 0.45),
            ],
            title="Mystery Showdown",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None


class TestKalshiRetriever:
    def test_parse_fixture_produces_events(self):
        fixture_path = Path(__file__).parent / "fixtures" / "kalshi" / "events_sports.json"
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))

        config = {"id": "kalshi", "params": {"min_volume_usd": 100}}
        retriever = KalshiRetriever(config)
        events = retriever.parse(raw, sport="basketball")

        # Fixture is captured with &series_ticker=KXNBAGAME, so at least one
        # NBA event should parse. If this is zero, the parser or fixture is wrong.
        assert len(events) > 0, "Expected at least one basketball event from NBA fixture"
        for e in events:
            assert e.provider == "kalshi"
            assert e.sport == "basketball"
            assert e.markets and e.markets[0]["type"] in ("moneyline", "1x2")
            for outcome in e.markets[0]["outcomes"]:
                assert outcome["odds"] > 1.0
                assert "provider_meta" in outcome
                assert "ticker" in outcome["provider_meta"]
