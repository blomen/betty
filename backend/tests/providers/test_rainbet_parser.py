"""Tests for the Rainbet/Betby pure parser functions.

The fixtures use real payload samples captured during the discovery pass; see
docs/superpowers/research/2026-05-10-rainbet-discovery.md for provenance.
"""

import json
from pathlib import Path

import pytest

from src.providers.rainbet import (
    betby_sport_id_to_arnold,
    categorize_market,
    parse_variant_key,
)

# Real market-descriptions catalogue captured 2026-05-10 (full 577 KB body).
# Keep the path here so individual test cases can read sub-dicts from it.
_DISCOVERY_DIR = Path("c:/tmp/rainbet_discovery")
_DESCRIPTIONS_PATH = _DISCOVERY_DIR / "markets_descriptions.json"


@pytest.fixture(scope="module")
def descriptions() -> dict:
    """Real market-descriptions catalogue, loaded once per test module."""
    if not _DESCRIPTIONS_PATH.is_file():
        pytest.skip(f"Discovery catalogue not present at {_DESCRIPTIONS_PATH}")
    with _DESCRIPTIONS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


class TestBetbySportIdToArnold:
    """sport_id (string or int) -> arnold internal sport key."""

    def test_soccer(self):
        assert betby_sport_id_to_arnold(1) == "football"
        assert betby_sport_id_to_arnold("1") == "football"

    def test_basketball(self):
        assert betby_sport_id_to_arnold(2) == "basketball"
        assert betby_sport_id_to_arnold("2") == "basketball"

    def test_baseball(self):
        assert betby_sport_id_to_arnold(3) == "baseball"
        assert betby_sport_id_to_arnold("3") == "baseball"

    def test_ice_hockey(self):
        assert betby_sport_id_to_arnold(4) == "ice_hockey"
        assert betby_sport_id_to_arnold("4") == "ice_hockey"

    def test_tennis(self):
        assert betby_sport_id_to_arnold(5) == "tennis"
        assert betby_sport_id_to_arnold("5") == "tennis"

    def test_boxing(self):
        assert betby_sport_id_to_arnold(10) == "boxing"
        assert betby_sport_id_to_arnold("10") == "boxing"

    def test_american_football(self):
        assert betby_sport_id_to_arnold(16) == "american_football"
        assert betby_sport_id_to_arnold("16") == "american_football"

    def test_mma(self):
        # 117 is its own bucket — `mma`, NOT `esports`.
        assert betby_sport_id_to_arnold(117) == "mma"
        assert betby_sport_id_to_arnold("117") == "mma"

    @pytest.mark.parametrize(
        "sport_id",
        [109, 110, 111, 112, 118, 125, 134, 194, 201],
    )
    def test_esports_buckets(self, sport_id):
        # All of these collapse into a single `esports` arnold key.
        assert betby_sport_id_to_arnold(sport_id) == "esports"
        assert betby_sport_id_to_arnold(str(sport_id)) == "esports"

    def test_unknown_sport_id_returns_none(self):
        # Handball (6), golf (9), formula 1 (40) — not extracted by arnold.
        assert betby_sport_id_to_arnold(6) is None
        assert betby_sport_id_to_arnold(9) is None
        assert betby_sport_id_to_arnold(40) is None
        assert betby_sport_id_to_arnold("999") is None

    def test_empty_or_none_returns_none(self):
        assert betby_sport_id_to_arnold("") is None
        assert betby_sport_id_to_arnold(None) is None

    def test_non_numeric_string_returns_none(self):
        assert betby_sport_id_to_arnold("abc") is None


class TestCategorizeMarket:
    """Market descriptor -> arnold market type ('1x2', 'moneyline', 'spread', 'total', or None)."""

    def test_market_1_is_1x2(self, descriptions):
        # Market 1: name "1x2", market_type "Result", no specifiers, 3 outcomes.
        assert categorize_market(descriptions["1"]) == "1x2"

    def test_market_219_is_moneyline(self, descriptions):
        # Market 219: "Winner (incl. overtime)" — basketball-class.
        assert categorize_market(descriptions["219"]) == "moneyline"

    def test_market_186_is_moneyline(self, descriptions):
        # Market 186: "Winner" — tennis/MMA/boxing/esports.
        assert categorize_market(descriptions["186"]) == "moneyline"

    def test_market_251_is_moneyline(self, descriptions):
        # Market 251: "Winner (incl. extra innings)" — baseball.
        assert categorize_market(descriptions["251"]) == "moneyline"

    def test_market_406_is_moneyline(self, descriptions):
        # Market 406: "Winner (incl. overtime and penalties)" — ice hockey.
        assert categorize_market(descriptions["406"]) == "moneyline"

    def test_market_16_is_spread(self, descriptions):
        # Market 16: "Handicap" — soccer.
        assert categorize_market(descriptions["16"]) == "spread"

    def test_market_223_is_spread(self, descriptions):
        # Market 223: "Handicap (incl. overtime)" — basketball/NFL.
        assert categorize_market(descriptions["223"]) == "spread"

    def test_market_188_is_spread(self, descriptions):
        # Market 188: "Set handicap" — tennis.
        assert categorize_market(descriptions["188"]) == "spread"

    def test_market_327_is_spread(self, descriptions):
        # Market 327: "Map handicap" — esports.
        assert categorize_market(descriptions["327"]) == "spread"

    def test_market_18_is_total(self, descriptions):
        # Market 18: "Total" — soccer/hockey/etc.
        assert categorize_market(descriptions["18"]) == "total"

    def test_market_225_is_total(self, descriptions):
        # Market 225: "Total (incl. overtime)" — basketball/NFL.
        assert categorize_market(descriptions["225"]) == "total"

    def test_market_258_is_total(self, descriptions):
        # Market 258: "Total (incl. extra innings)" — baseball.
        assert categorize_market(descriptions["258"]) == "total"

    def test_market_189_is_total(self, descriptions):
        # Market 189: "Total games" — tennis.
        assert categorize_market(descriptions["189"]) == "total"

    def test_double_chance_is_none(self, descriptions):
        # Market 10: "Double chance" — Result type but no_specifiers, name doesn't match.
        assert categorize_market(descriptions["10"]) is None

    def test_correct_score_is_none(self, descriptions):
        # Market 50097: "Correct score (...)" — market_type="CorrectScore".
        assert categorize_market(descriptions["50097"]) is None

    def test_yes_no_market_is_none(self, descriptions):
        # Market 911: "Will the fight go the distance" — YesNo type.
        assert categorize_market(descriptions["911"]) is None

    def test_set_winner_with_setnr_specifier_is_none(self, descriptions):
        # Market 202: "{!setnr} set - winner" — Result type but has setnr specifier
        # (a per-set winner is not arnold's idea of moneyline; categorizer returns None
        # because name does not start with "winner" — it has the "{!setnr} set - "
        # prefix).
        assert categorize_market(descriptions["202"]) is None

    def test_multi_specifier_handicap_is_none(self, descriptions):
        # Market 555: "{!mapnr} map - kill handicap", market_type="Handicap" but
        # specifiers=["mapnr","hcp"] — not the primary spread market arnold extracts.
        assert categorize_market(descriptions["555"]) is None

    def test_handles_none_specifiers(self):
        # Real catalogue uses None (not []) for markets without specifiers.
        # Hand-crafted: ensure parser handles that.
        desc = {"name": "1x2", "market_type": "Result", "specifiers": None}
        assert categorize_market(desc) == "1x2"

    def test_handles_missing_specifiers_key(self):
        desc = {"name": "Winner", "market_type": "Result"}
        assert categorize_market(desc) == "moneyline"

    def test_winner_case_insensitive(self):
        desc = {"name": "WINNER", "market_type": "Result"}
        assert categorize_market(desc) == "moneyline"

    def test_empty_descriptor_returns_none(self):
        assert categorize_market({}) is None


class TestParseVariantKey:
    """Variant key string -> dict of specifier name -> float value."""

    def test_empty_string(self):
        assert parse_variant_key("") == {}

    def test_total_decimal(self):
        assert parse_variant_key("total=2.5") == {"total": 2.5}

    def test_total_integer(self):
        # Soccer market 18 sometimes ships keys like "total=3" (no decimal point).
        assert parse_variant_key("total=3") == {"total": 3.0}

    def test_handicap_negative(self):
        assert parse_variant_key("hcp=-1.5") == {"hcp": -1.5}

    def test_handicap_large_negative(self):
        # Basketball / NFL spread markets ship larger lines (e.g. -10.5 points).
        assert parse_variant_key("hcp=-10.5") == {"hcp": -10.5}

    def test_handicap_positive(self):
        assert parse_variant_key("hcp=1.5") == {"hcp": 1.5}

    def test_handicap_zero(self):
        assert parse_variant_key("hcp=0") == {"hcp": 0.0}

    def test_multi_specifier_dota(self):
        # Real example from market 555: "{!mapnr} map - kill handicap".
        assert parse_variant_key("mapnr=1|hcp=-0.5") == {"mapnr": 1.0, "hcp": -0.5}

    def test_multi_specifier_setnr(self):
        # Real example from market 202: per-set winner.
        assert parse_variant_key("setnr=2") == {"setnr": 2.0}

    def test_unknown_specifier_passes_through(self):
        # Parser preserves unknown specifier names so debugging info isn't lost.
        # Downstream code only cares about hcp / total.
        result = parse_variant_key("foo=4.2")
        assert result == {"foo": 4.2}

    def test_malformed_segment_skipped(self):
        # If a segment doesn't contain '=' we skip it rather than raise; keeps
        # the parser tolerant of unexpected payload shapes.
        result = parse_variant_key("hcp=-1.5|garbage")
        assert result == {"hcp": -1.5}

    def test_non_numeric_value_skipped(self):
        # Defensive: if Betby ever ships "hcp=abc" we don't want a crash.
        result = parse_variant_key("hcp=abc")
        assert result == {}
