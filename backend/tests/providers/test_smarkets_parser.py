"""Tests for Smarkets signal-only parser."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.providers.smarkets import (
    SmarketsRetriever,
    _contract_side,
    classify_market_type,
    extract_home_away_from_event_name,
    parse_market_prices,
    price_integer_to_odds,
    type_scope_to_sport,
)


class TestTypeScopeToSport:
    @pytest.mark.parametrize(
        "scope,expected",
        [
            ("football", "football"),
            ("basketball", "basketball"),
            ("tennis", "tennis"),
            ("ice-hockey", "ice_hockey"),
            ("american-football", "american_football"),
            ("baseball", "baseball"),
            ("mma", "mma"),
            ("boxing", "boxing"),
        ],
    )
    def test_known_scopes(self, scope, expected):
        assert type_scope_to_sport(scope) == expected

    def test_politics_not_mapped(self):
        assert type_scope_to_sport("politics") is None
        assert type_scope_to_sport("entertainment") is None


class TestPriceIntegerToOdds:
    """Smarkets /quotes/ encodes prices as integers 0-10000 (percent x 100)."""

    def test_happy_path(self):
        # 5000 = 50% -> decimal odds 2.00
        assert price_integer_to_odds(5000) == 2.00
        # 2500 = 25% -> decimal odds 4.00
        assert price_integer_to_odds(2500) == 4.00
        # 7500 = 75% -> decimal odds ~1.333
        assert price_integer_to_odds(7500) == pytest.approx(1.333, abs=0.01)

    def test_zero_or_negative_returns_zero(self):
        assert price_integer_to_odds(0) == 0.0
        assert price_integer_to_odds(-1) == 0.0


class TestParseMarketPrices:
    """parse_market_prices expects the real Smarkets schema:

    - last_executed_prices: nested {"<market_id>": [{contract_id,
      last_executed_price: "<percent-string>"}, ...]}
    - quotes: nested {"<contract_id>": {"bids": [{"price": int}, ...],
      "offers": [{"price": int}, ...]}}
    """

    def test_last_executed_preferred(self):
        # Percentages as strings: 55% -> 100/55 = 1.818; 45% -> 100/45 = 2.222
        raw = {
            "last_executed_prices": {
                "M1": [
                    {"contract_id": "A", "last_executed_price": "55"},
                    {"contract_id": "B", "last_executed_price": "45"},
                ]
            },
            "quotes": {},
        }
        out = parse_market_prices(raw)
        assert out["A"] == pytest.approx(1.818, abs=0.01)
        assert out["B"] == pytest.approx(2.222, abs=0.01)

    def test_quotes_fallback_when_no_trades(self):
        # Best back = highest bid (5800); best lay = lowest offer (6000);
        # mid = 5900 -> decimal odds 10000/5900 ~= 1.695
        raw = {
            "last_executed_prices": {
                "M1": [{"contract_id": "A", "last_executed_price": None}],
            },
            "quotes": {
                "A": {
                    "bids": [
                        {"price": 5800, "quantity": 1000},
                        {"price": 5700, "quantity": 500},
                    ],
                    "offers": [
                        {"price": 6000, "quantity": 1000},
                        {"price": 6100, "quantity": 500},
                    ],
                },
            },
        }
        out = parse_market_prices(raw)
        assert out["A"] == pytest.approx(1.695, abs=0.01)

    def test_no_price_no_quote_dropped(self):
        raw = {
            "last_executed_prices": {
                "M1": [{"contract_id": "A", "last_executed_price": None}],
            },
            "quotes": {},
        }
        assert parse_market_prices(raw) == {}

    def test_fixture_shape(self):
        """Parse the real captured fixtures — must yield 3 outcomes for the
        Nottm Forest vs Burnley full-time result market."""
        lep = json.loads(
            (Path(__file__).parent / "fixtures" / "smarkets" / "last_executed_prices_example.json").read_text(
                encoding="utf-8"
            )
        )
        quotes = json.loads(
            (Path(__file__).parent / "fixtures" / "smarkets" / "quotes_example.json").read_text(encoding="utf-8")
        )
        raw = {
            "last_executed_prices": lep.get("last_executed_prices", {}),
            "quotes": quotes,
        }
        out = parse_market_prices(raw)
        assert len(out) == 3
        # Sanity: all odds are finite and >= 1.0 (implied prob <= 100%)
        for cid, odds in out.items():
            assert odds >= 1.0
            assert odds < 100.0


class TestSmarketsRetriever:
    def test_filter_events_by_sport_matches_type_field(self):
        """In real Smarkets data `type_scope` is null and `type` carries the
        sport as `<sport>_match`. filter_events_by_sport must use `type`."""
        fixture = Path(__file__).parent / "fixtures" / "smarkets" / "events_upcoming.json"
        raw = json.loads(fixture.read_text(encoding="utf-8"))

        config = {"id": "smarkets", "params": {"min_trades_24h": 1}}
        retriever = SmarketsRetriever(config)

        footballs = retriever.filter_events_by_sport(raw.get("events", []), "football")
        assert isinstance(footballs, list)
        assert len(footballs) > 0, "Fixture must contain football matches — got 0"
        for ev in footballs:
            assert ev.get("type") == "football_match"

    def test_filter_events_by_sport_unknown_returns_empty(self):
        config = {"id": "smarkets"}
        retriever = SmarketsRetriever(config)
        assert retriever.filter_events_by_sport([{"type": "football_match"}], "cricket") == []

    def test_get_sport_url_uses_type_filter(self):
        """URL must include both type_domain and type=<sport>_match so the
        listing returns matches (not category nodes)."""
        config = {"id": "smarkets"}
        retriever = SmarketsRetriever(config)
        url = retriever._get_sport_url("basketball")
        assert "type_domain=basketball" in url
        assert "type=basketball_match" in url
        assert "state=upcoming" in url

    def test_proxy_url_from_config_root(self):
        config = {"id": "smarkets", "proxy_url": "socks5://host:1080"}
        r = SmarketsRetriever(config)
        assert r.proxy_url == "socks5://host:1080"

    def test_proxy_url_from_params(self):
        config = {
            "id": "smarkets",
            "params": {"proxy_url": "socks5://host:1080"},
        }
        r = SmarketsRetriever(config)
        assert r.proxy_url == "socks5://host:1080"

    def test_proxy_url_empty_is_none(self):
        config = {
            "id": "smarkets",
            "params": {"proxy_url": ""},
        }
        r = SmarketsRetriever(config)
        assert r.proxy_url is None


class TestClassifyMarketType:
    def test_winner_markets_mapped(self):
        assert classify_market_type("Full-time result", {"name": "WINNER_3_WAY"}) == "1x2"
        assert classify_market_type("Match winner", {"name": "WINNER_2_WAY"}) == "moneyline"
        assert classify_market_type("", {"name": "MATCH_WINNER"}) == "moneyline"

    def test_spread_and_total_classified_per_line(self):
        # ASIAN_HANDICAP (2-way) and OVER_UNDER (per-line) are now classified;
        # HANDICAP_3_WAY remains skipped because of the draw outcome.
        assert classify_market_type("", {"name": "ASIAN_HANDICAP"}) == "spread"
        assert classify_market_type("", {"name": "OVER_UNDER"}) == "total"
        assert classify_market_type("", {"name": "HANDICAP_3_WAY"}) is None

    def test_fallback_winner_heuristic(self):
        assert classify_market_type("Full-time result 3-way", None) == "1x2"
        assert classify_market_type("Match winner", None) == "moneyline"

    def test_fallback_no_longer_matches_handicap_or_totals(self):
        assert classify_market_type("Asian Handicap -1.5", None) is None
        assert classify_market_type("Over/under 2.5 goals", None) is None


class TestExtractHomeAwayFromEventName:
    def test_vs_splits_cleanly(self):
        assert extract_home_away_from_event_name("Nottm Forest vs Burnley") == (
            "Nottm Forest",
            "Burnley",
        )

    def test_no_separator_returns_empty(self):
        assert extract_home_away_from_event_name("El Clasico Special") == ("", "")


class TestContractSide:
    def test_contract_type_home_away_draw(self):
        assert _contract_side({"contract_type": {"name": "HOME"}, "slug": "home"}) == "home"
        assert _contract_side({"contract_type": {"name": "DRAW"}, "slug": "draw"}) == "draw"
        assert _contract_side({"contract_type": {"name": "AWAY"}, "slug": "away"}) == "away"

    def test_falls_back_to_slug(self):
        assert _contract_side({"contract_type": None, "slug": "home"}) == "home"

    def test_over_under_recognized(self):
        # OVER/UNDER are now first-class outcome names for total markets.
        assert _contract_side({"contract_type": {"name": "OVER"}, "slug": "over"}) == "over"
        assert _contract_side({"contract_type": {"name": "UNDER"}, "slug": "under"}) == "under"

    def test_unknown_returns_none(self):
        assert _contract_side({"contract_type": {"name": "YES"}, "slug": "yes"}) is None
        assert _contract_side({"contract_type": {"name": "ODD"}, "slug": "odd"}) is None


class TestBuildEventLive:
    """End-to-end parsing of a single event using captured fixtures.

    Patches `_fetch_json` so `_build_event` reads the captured JSON for
    markets / contracts / last_executed_prices / quotes — exercising the
    real home/away assignment and outcome-by-name emission.
    """

    def _load(self, name: str) -> dict:
        return json.loads((Path(__file__).parent / "fixtures" / "smarkets" / name).read_text(encoding="utf-8"))

    def test_build_event_produces_home_away_and_named_outcomes(self):
        ev_raw = {
            "id": "44964935",
            "name": "Nottm Forest vs Burnley",
            "type": "football_match",
            "start_datetime": "2026-04-18T17:30:00Z",
            "full_slug": "/football/premier-league/nottm-forest-vs-burnley",
        }
        markets_body = self._load("markets_example.json")
        contracts_body = self._load("contracts_example.json")
        prices_body = self._load("last_executed_prices_example.json")
        quotes_body = self._load("quotes_example.json")

        async def fake_fetch(self, url):  # noqa: ARG001
            if url.endswith(f"/events/{ev_raw['id']}/markets/"):
                return markets_body
            if "/contracts/" in url:
                return contracts_body
            if "/last_executed_prices/" in url:
                return prices_body
            if "/quotes/" in url:
                return quotes_body
            return None

        config = {"id": "smarkets"}
        retriever = SmarketsRetriever(config)

        async def run():
            with patch.object(SmarketsRetriever, "_get_json", new=fake_fetch):
                return await retriever._build_event(ev_raw, "football")

        ev = asyncio.run(run())

        assert ev is not None
        # Home/away populated from event name.
        assert ev.home_team == "Nottm Forest"
        assert ev.away_team == "Burnley"
        # 1x2 produced with canonical side names, not numeric contract IDs.
        one_x_two = next((k for k in ev.markets if k["type"] == "1x2"), None)
        assert one_x_two is not None, "Expected a 1x2 market from WINNER_3_WAY"
        names = {o["name"] for o in one_x_two["outcomes"]}
        assert names == {"home", "draw", "away"}, f"Expected canonical side names, got {names}"
        for o in one_x_two["outcomes"]:
            assert not str(o["name"]).isdigit(), f"Numeric contract ID leaked as outcome name: {o['name']!r}"
            assert o["odds"] >= 1.0
        # Spread / total must NOT appear (deferred on Smarkets).
        assert not any(k["type"] in ("spread", "total") for k in ev.markets), (
            "Smarkets must not emit spread/total markets"
        )

    def test_build_event_skips_when_name_unsplittable(self):
        """No home/away separator in event name → skip event entirely."""
        ev_raw = {
            "id": "999",
            "name": "Some Special Market",
            "type": "football_match",
        }

        async def fake_fetch(self, url):  # noqa: ARG001
            return None  # should not be reached

        retriever = SmarketsRetriever({"id": "smarkets"})

        async def run():
            with patch.object(SmarketsRetriever, "_get_json", new=fake_fetch):
                return await retriever._build_event(ev_raw, "football")

        assert asyncio.run(run()) is None
