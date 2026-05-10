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
    parse_event,
    parse_variant_key,
    pick_main_market,
)

# Real market-descriptions catalogue captured 2026-05-10 (full 577 KB body).
# Keep the path here so individual test cases can read sub-dicts from it.
_DISCOVERY_DIR = Path("c:/tmp/rainbet_discovery")
_DESCRIPTIONS_PATH = _DISCOVERY_DIR / "markets_descriptions.json"
_PREMATCH_CHUNK_PATH = _DISCOVERY_DIR / "prematch_chunk_1.json"


@pytest.fixture(scope="module")
def descriptions() -> dict:
    """Real market-descriptions catalogue, loaded once per test module."""
    if not _DESCRIPTIONS_PATH.is_file():
        pytest.skip(f"Discovery catalogue not present at {_DESCRIPTIONS_PATH}")
    with _DESCRIPTIONS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def prematch_chunk() -> dict:
    """Real prematch chunk (one of 5) captured 2026-05-10."""
    if not _PREMATCH_CHUNK_PATH.is_file():
        pytest.skip(f"Discovery chunk not present at {_PREMATCH_CHUNK_PATH}")
    with _PREMATCH_CHUNK_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sports_map(prematch_chunk) -> dict:
    return prematch_chunk.get("sports") or {}


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


class TestPickMainMarket:
    """(market_id, variants_dict, market_type) -> chosen (variant_key, variant_data)."""

    # ----- 1x2 / moneyline (single variant, key "") -----

    def test_1x2_single_variant_returned(self):
        variants = {"": {"1": {"k": "2.6"}, "2": {"k": "3.5"}, "3": {"k": "2.34"}}}
        chosen = pick_main_market("1", variants, "1x2")
        assert chosen is not None
        key, data = chosen
        assert key == ""
        assert data == {"1": {"k": "2.6"}, "2": {"k": "3.5"}, "3": {"k": "2.34"}}

    def test_moneyline_single_variant_returned(self):
        variants = {"": {"4": {"k": "1.18"}, "5": {"k": "4.7"}}}
        chosen = pick_main_market("219", variants, "moneyline")
        assert chosen is not None
        assert chosen[0] == ""
        assert chosen[1] == {"4": {"k": "1.18"}, "5": {"k": "4.7"}}

    def test_1x2_no_empty_key_returns_none(self):
        # If there's no "" variant the data is malformed for a no-specifier market.
        variants = {"hcp=0": {"1": {"k": "2.0"}}}
        assert pick_main_market("1", variants, "1x2") is None

    def test_1x2_empty_variants_returns_none(self):
        assert pick_main_market("1", {}, "1x2") is None

    # ----- spread: smallest |hcp| wins, tie-break prefers negative -----

    def test_spread_picks_smallest_abs_hcp(self):
        variants = {
            "hcp=-3.5": {"1714": {"k": "1.9"}, "1715": {"k": "1.9"}},
            "hcp=-1.5": {"1714": {"k": "2.1"}, "1715": {"k": "1.75"}},
            "hcp=-2.5": {"1714": {"k": "2.5"}, "1715": {"k": "1.55"}},
        }
        chosen = pick_main_market("16", variants, "spread")
        assert chosen is not None
        assert chosen[0] == "hcp=-1.5"

    def test_spread_tie_prefers_negative(self):
        # Real tennis-style 0.5 / -0.5 -- pick the negative one (favourite laying).
        variants = {
            "hcp=0.5": {"1714": {"k": "2.0"}, "1715": {"k": "1.85"}},
            "hcp=-0.5": {"1714": {"k": "1.85"}, "1715": {"k": "2.0"}},
        }
        chosen = pick_main_market("188", variants, "spread")
        assert chosen is not None
        assert chosen[0] == "hcp=-0.5"

    def test_spread_zero_handicap(self):
        # Pick'em — abs(0) is the smallest; one variant wins outright.
        variants = {
            "hcp=0": {"1714": {"k": "1.9"}, "1715": {"k": "1.9"}},
            "hcp=-1.5": {"1714": {"k": "2.4"}, "1715": {"k": "1.55"}},
        }
        chosen = pick_main_market("16", variants, "spread")
        assert chosen is not None
        assert chosen[0] == "hcp=0"

    def test_spread_with_invalid_variant_key_skipped(self):
        # If variant key cannot be parsed (no hcp), skip it.
        variants = {
            "garbage": {"1714": {"k": "1.5"}, "1715": {"k": "2.5"}},
            "hcp=-2.5": {"1714": {"k": "1.85"}, "1715": {"k": "1.95"}},
        }
        chosen = pick_main_market("16", variants, "spread")
        assert chosen is not None
        assert chosen[0] == "hcp=-2.5"

    def test_spread_no_valid_variants_returns_none(self):
        variants = {
            "garbage": {"1714": {"k": "1.5"}, "1715": {"k": "2.5"}},
        }
        assert pick_main_market("16", variants, "spread") is None

    # ----- total: most balanced odds, tie-break = median total -----

    def test_total_picks_most_balanced(self):
        # 5 lines, the most balanced is the one whose over/under odds differ least.
        variants = {
            "total=1.5": {"12": {"k": "1.24"}, "13": {"k": "3.45"}},  # |1.24-3.45|=2.21
            "total=2": {"12": {"k": "1.34"}, "13": {"k": "2.88"}},  # 1.54
            "total=2.5": {"12": {"k": "1.69"}, "13": {"k": "1.99"}},  # 0.30 <- pick
            "total=3": {"12": {"k": "2.16"}, "13": {"k": "1.58"}},  # 0.58
            "total=3.5": {"12": {"k": "2.8"}, "13": {"k": "1.36"}},  # 1.44
        }
        chosen = pick_main_market("18", variants, "total")
        assert chosen is not None
        assert chosen[0] == "total=2.5"

    def test_total_single_variant(self):
        variants = {"total=167.5": {"12": {"k": "1.9"}, "13": {"k": "1.86"}}}
        chosen = pick_main_market("225", variants, "total")
        assert chosen is not None
        assert chosen[0] == "total=167.5"

    def test_total_balance_tie_prefers_median(self):
        # Three lines with identical balance — picker should fall back to median.
        variants = {
            "total=1.5": {"12": {"k": "1.9"}, "13": {"k": "1.9"}},
            "total=2.5": {"12": {"k": "1.9"}, "13": {"k": "1.9"}},
            "total=3.5": {"12": {"k": "1.9"}, "13": {"k": "1.9"}},
        }
        chosen = pick_main_market("18", variants, "total")
        assert chosen is not None
        assert chosen[0] == "total=2.5"

    def test_total_skips_variant_missing_outcome(self):
        # If a line lacks "12" or "13" we cannot evaluate balance — skip it.
        variants = {
            "total=2.5": {"12": {"k": "1.9"}},  # missing under -> skip
            "total=3.5": {"12": {"k": "2.1"}, "13": {"k": "1.7"}},  # ok
        }
        chosen = pick_main_market("18", variants, "total")
        assert chosen is not None
        assert chosen[0] == "total=3.5"

    def test_total_no_valid_variants_returns_none(self):
        variants = {
            "total=2.5": {"12": {"k": "1.9"}},  # missing under
            "total=3.5": {"13": {"k": "1.7"}},  # missing over
        }
        assert pick_main_market("18", variants, "total") is None

    def test_total_skips_invalid_odds(self):
        # Non-numeric "k" should not crash; skip the variant.
        variants = {
            "total=2.5": {"12": {"k": "not_a_number"}, "13": {"k": "1.9"}},
            "total=3.5": {"12": {"k": "2.1"}, "13": {"k": "1.7"}},
        }
        chosen = pick_main_market("18", variants, "total")
        assert chosen is not None
        assert chosen[0] == "total=3.5"


def _market_by_type(event, market_type):
    """Helper: return the first market of a given type, or None."""
    for m in event.markets:
        if m["type"] == market_type:
            return m
    return None


class TestParseEvent:
    """Per-event parsing into StandardEvent."""

    # ---- Real soccer event from prematch_chunk_1.json ----

    def test_soccer_event_parses_1x2_and_total(self, prematch_chunk, descriptions, sports_map):
        ev_id = "2664825045611843589"  # Zaglebie Sosnowiec vs KS Hutnik Krakow SSA
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        assert result is not None
        assert result.id == ev_id
        assert result.sport == "football"
        assert result.provider == "rainbet"
        # 1x2 + total — double chance (id 10) is dropped (not in ALLOWED_MARKETS).
        types = {m["type"] for m in result.markets}
        assert types == {"1x2", "total"}

    def test_soccer_1x2_outcomes(self, prematch_chunk, descriptions, sports_map):
        ev_id = "2664825045611843589"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        assert result is not None
        m = _market_by_type(result, "1x2")
        assert m is not None
        outcomes = {o["name"]: o for o in m["outcomes"]}
        assert outcomes["home"]["odds"] == 2.6
        assert outcomes["draw"]["odds"] == 3.5
        assert outcomes["away"]["odds"] == 2.34

    def test_soccer_total_picks_balanced_line(self, prematch_chunk, descriptions, sports_map):
        # The 5 lines in market 18 — most balanced is total=2.5 (1.69 vs 1.99).
        ev_id = "2664825045611843589"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        m = _market_by_type(result, "total")
        assert m is not None
        for o in m["outcomes"]:
            assert o["point"] == 2.5
        over = next(o for o in m["outcomes"] if o["name"] == "over")
        under = next(o for o in m["outcomes"] if o["name"] == "under")
        assert over["odds"] == 1.69
        assert under["odds"] == 1.99

    def test_soccer_team_names_normalized(self, prematch_chunk, descriptions, sports_map):
        ev_id = "2664825045611843589"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        # normalize_team_name lowercases + strips common prefixes.
        assert result.home_team == "zaglebie sosnowiec"
        assert result.away_team == "ks hutnik krakow ssa"
        assert " vs " in result.name

    def test_soccer_start_time_iso(self, prematch_chunk, descriptions, sports_map):
        # scheduled = 1778425200 -> 2026-05-10T15:00:00Z
        ev_id = "2664825045611843589"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        assert result.start_time == "2026-05-10T15:00:00Z"

    # ---- Real basketball event from prematch_chunk_1.json ----

    def test_basketball_drops_1x2_uses_moneyline(self, prematch_chunk, descriptions, sports_map):
        # Sample basketball event has both market 1 (3-way) and 219 (2-way moneyline).
        # arnold should drop the 1x2 in favour of the 2-way moneyline.
        ev_id = "2664852545721212974"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        assert result is not None
        types = {m["type"] for m in result.markets}
        assert "1x2" not in types
        assert types == {"moneyline", "spread", "total"}

    def test_basketball_moneyline_uses_outcome_ids_4_5(self, prematch_chunk, descriptions, sports_map):
        ev_id = "2664852545721212974"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        m = _market_by_type(result, "moneyline")
        assert m is not None
        outcomes = {o["name"]: o for o in m["outcomes"]}
        # Market 219: outcome 4=home, 5=away. From real capture: 1.18 / 4.7.
        assert outcomes["home"]["odds"] == 1.18
        assert outcomes["away"]["odds"] == 4.7

    def test_basketball_spread_signed_handicap(self, prematch_chunk, descriptions, sports_map):
        ev_id = "2664852545721212974"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        m = _market_by_type(result, "spread")
        assert m is not None
        outcomes = {o["name"]: o for o in m["outcomes"]}
        # hcp=-10.5 -> home gets -10.5, away gets +10.5.
        assert outcomes["home"]["point"] == -10.5
        assert outcomes["away"]["point"] == 10.5

    def test_basketball_total_balanced(self, prematch_chunk, descriptions, sports_map):
        ev_id = "2664852545721212974"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        m = _market_by_type(result, "total")
        assert m is not None
        for o in m["outcomes"]:
            assert o["point"] == 167.5

    # ---- Real ice hockey event ----

    def test_hockey_uses_406_moneyline_drops_1x2(self, prematch_chunk, descriptions, sports_map):
        # Hockey events ship both market 1 (3-way reg time) and market 406
        # (winner incl. OT and penalties). arnold prefers 406 as moneyline.
        ev_id = "2665148250939592707"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        assert result is not None
        types = {m["type"] for m in result.markets}
        assert "1x2" not in types
        assert "moneyline" in types

    # ---- Real tennis event ----

    def test_tennis_prefers_set_handicap_over_game_handicap(self, prematch_chunk, descriptions, sports_map):
        # Tennis event ships both market 188 (set handicap) and 187 (game handicap).
        # categorize_market returns "spread" for both — but only ONE spread should
        # survive in the output.
        ev_id = "2664749539319230505"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        assert result is not None
        spread_count = sum(1 for m in result.markets if m["type"] == "spread")
        assert spread_count == 1

    def test_tennis_prefers_total_games_over_total_sets(self, prematch_chunk, descriptions, sports_map):
        ev_id = "2664749539319230505"
        ev_data = prematch_chunk["events"][ev_id]
        result = parse_event(ev_id, ev_data, descriptions, sports_map)
        assert result is not None
        total_count = sum(1 for m in result.markets if m["type"] == "total")
        assert total_count == 1
        # Market 189 has totals 20.5-24.5 (game-level); market 314 ships total=2.5
        # (set-level). The kept market should be the games one.
        m = _market_by_type(result, "total")
        # Any of the picked game-level lines is in the 20-25 range.
        assert m["outcomes"][0]["point"] > 10

    # ---- Filters ----

    def test_live_event_returns_none(self, sports_map):
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "match",
                "sport": "1",
                "competitors": [{"name": "A"}, {"name": "B"}],
            },
            "markets": {"1": {"": {"1": {"k": "2.0"}, "2": {"k": "3.0"}, "3": {"k": "2.0"}}}},
            "state": {"status": 21, "match_status": 0},
        }
        assert parse_event("123", ev_data, {"1": {"name": "1x2", "market_type": "Result"}}, sports_map) is None

    def test_non_match_returns_none(self, sports_map):
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "stage",
                "sport": "9",
                "competitors": [{"name": "Tournament"}, {"name": "Winner"}],
            },
            "markets": {},
            "state": {"status": 0, "match_status": 0},
        }
        assert parse_event("999", ev_data, {}, sports_map) is None

    def test_missing_home_team_returns_none(self, sports_map):
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "match",
                "sport": "1",
                "competitors": [{"name": ""}, {"name": "Away"}],
            },
            "markets": {"1": {"": {"1": {"k": "2.0"}, "2": {"k": "3.0"}, "3": {"k": "2.0"}}}},
            "state": {"status": 0, "match_status": 0},
        }
        descs = {"1": {"name": "1x2", "market_type": "Result"}}
        assert parse_event("abc", ev_data, descs, sports_map) is None

    def test_missing_away_team_returns_none(self, sports_map):
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "match",
                "sport": "1",
                "competitors": [{"name": "Home"}],
            },
            "markets": {"1": {"": {"1": {"k": "2.0"}, "2": {"k": "3.0"}, "3": {"k": "2.0"}}}},
            "state": {"status": 0, "match_status": 0},
        }
        descs = {"1": {"name": "1x2", "market_type": "Result"}}
        assert parse_event("abc", ev_data, descs, sports_map) is None

    def test_unknown_sport_returns_none(self, sports_map):
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "match",
                "sport": "9",  # golf -- not in arnold scope
                "competitors": [{"name": "A"}, {"name": "B"}],
            },
            "markets": {"1": {"": {"1": {"k": "2.0"}, "2": {"k": "3.0"}, "3": {"k": "2.0"}}}},
            "state": {"status": 0, "match_status": 0},
        }
        descs = {"1": {"name": "1x2", "market_type": "Result"}}
        assert parse_event("xyz", ev_data, descs, sports_map) is None

    def test_event_with_no_recognized_markets_returns_none(self, sports_map):
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "match",
                "sport": "1",
                "competitors": [{"name": "A"}, {"name": "B"}],
            },
            "markets": {"10": {"": {"9": {"k": "1.5"}, "10": {"k": "1.3"}, "11": {"k": "1.4"}}}},
            "state": {"status": 0, "match_status": 0},
        }
        descs = {"10": {"name": "Double chance", "market_type": "Result"}}
        assert parse_event("dchance", ev_data, descs, sports_map) is None

    def test_invalid_odds_outcome_skipped(self, sports_map):
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "match",
                "sport": "1",
                "competitors": [{"name": "Home"}, {"name": "Away"}],
            },
            # Home odds invalid (<= 1.0); should be dropped, leaving draw + away.
            "markets": {"1": {"": {"1": {"k": "0.5"}, "2": {"k": "3.0"}, "3": {"k": "2.0"}}}},
            "state": {"status": 0, "match_status": 0},
        }
        descs = {"1": {"name": "1x2", "market_type": "Result"}}
        result = parse_event("oddtest", ev_data, descs, sports_map)
        # Only 2 valid outcomes — market is still emitted (>=2 outcomes), but
        # without home.
        assert result is not None
        m = _market_by_type(result, "1x2")
        assert m is not None
        names = {o["name"] for o in m["outcomes"]}
        assert "home" not in names
        assert names == {"draw", "away"}

    def test_event_id_used_directly(self, sports_map):
        # The event_id is the dict key — parser should use it as StandardEvent.id
        # without prefixing.
        ev_data = {
            "desc": {
                "scheduled": 1778425200,
                "type": "match",
                "sport": "1",
                "competitors": [{"name": "Home"}, {"name": "Away"}],
            },
            "markets": {"1": {"": {"1": {"k": "2.0"}, "2": {"k": "3.0"}, "3": {"k": "2.5"}}}},
            "state": {"status": 0, "match_status": 0},
        }
        descs = {"1": {"name": "1x2", "market_type": "Result"}}
        result = parse_event("event-id-123", ev_data, descs, sports_map)
        assert result is not None
        assert result.id == "event-id-123"
