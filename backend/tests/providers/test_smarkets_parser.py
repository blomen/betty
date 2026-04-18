"""Tests for Smarkets signal-only parser."""
import json
from pathlib import Path

import pytest

from src.providers.smarkets import (
    parse_market_prices,
    price_integer_to_odds,
    type_scope_to_sport,
)


class TestTypeScopeToSport:
    @pytest.mark.parametrize("scope,expected", [
        ("football", "football"),
        ("basketball", "basketball"),
        ("tennis", "tennis"),
        ("ice-hockey", "ice_hockey"),
        ("american-football", "american_football"),
        ("baseball", "baseball"),
        ("mma", "mma"),
        ("boxing", "boxing"),
    ])
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
            (
                Path(__file__).parent
                / "fixtures"
                / "smarkets"
                / "last_executed_prices_example.json"
            ).read_text(encoding="utf-8")
        )
        quotes = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "smarkets"
                / "quotes_example.json"
            ).read_text(encoding="utf-8")
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
