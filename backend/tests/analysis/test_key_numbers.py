"""Unit tests for NFL key-number annotation.

Pure-function tests — no DB, no I/O. Verifies that we correctly identify
points sitting on key numbers, points straddling key numbers (the
high-leverage half-point zone), and that non-NFL or non-spread/total
markets return None.
"""

import pytest

from src.analysis.key_numbers import (
    NFL_SPREAD_KEY_NUMBERS,
    NFL_TOTAL_KEY_NUMBERS,
    SPREAD_HALF_POINT_VALUE_PP,
    annotate,
    annotate_spread,
    annotate_total,
    is_nfl,
)


class TestIsNfl:
    @pytest.mark.parametrize(
        "sport",
        ["americanfootball_nfl", "nfl", "NFL", "AmericanFootball_NFL", "americanfootball-nfl", "football_nfl"],
    )
    def test_nfl_variants(self, sport):
        assert is_nfl(sport) is True

    @pytest.mark.parametrize(
        "sport",
        ["soccer", "basketball_nba", "nba", "americanfootball_ncaaf", "baseball_mlb", "", None, "  "],
    )
    def test_non_nfl_rejected(self, sport):
        assert is_nfl(sport) is False


class TestAnnotateSpread:
    def test_on_key_number_minus_three(self):
        info = annotate_spread("nfl", -3.0)
        assert info is not None
        assert info.on_key is True
        assert info.straddles_key is False
        assert info.nearest_key == 3
        assert info.distance == 0.0
        assert info.half_point_value_pp == SPREAD_HALF_POINT_VALUE_PP[3]

    def test_on_key_number_plus_three(self):
        # Sign of the spread doesn't matter — favorite vs dog have
        # symmetric exposure to the same key.
        info = annotate_spread("nfl", 3.0)
        assert info is not None
        assert info.on_key is True
        assert info.nearest_key == 3

    def test_straddles_key_minus_2_5(self):
        info = annotate_spread("nfl", -2.5)
        assert info is not None
        assert info.on_key is False
        assert info.straddles_key is True
        assert info.nearest_key == 3
        assert info.distance == pytest.approx(-0.5)
        assert info.half_point_value_pp == SPREAD_HALF_POINT_VALUE_PP[3]

    def test_straddles_key_minus_3_5(self):
        info = annotate_spread("nfl", -3.5)
        assert info is not None
        assert info.on_key is False
        assert info.straddles_key is True
        assert info.nearest_key == 3
        assert info.distance == pytest.approx(0.5)

    def test_straddles_key_7(self):
        info = annotate_spread("nfl", -7.5)
        assert info is not None
        assert info.straddles_key is True
        assert info.nearest_key == 7
        assert info.half_point_value_pp == SPREAD_HALF_POINT_VALUE_PP[7]

    def test_not_near_key_minus_4(self):
        # -4 is 1pt from 3, 2pt from 6 — outside the half-point straddle
        # zone for either, even if it's closest to 3.
        info = annotate_spread("nfl", -4.0)
        assert info is not None
        assert info.on_key is False
        assert info.straddles_key is False
        assert info.nearest_key == 3
        assert info.half_point_value_pp is None

    def test_extreme_spread_uses_furthest_key(self):
        # -17 is closer to 14 (key) than anything else.
        info = annotate_spread("nfl", -17.0)
        assert info is not None
        # 17 → nearest among (3, 7, 6, 10, 14) is 14, distance 3
        assert info.nearest_key == 14
        assert info.on_key is False
        assert info.straddles_key is False

    def test_non_nfl_returns_none(self):
        assert annotate_spread("nba", -3.0) is None
        assert annotate_spread("soccer", -3.0) is None
        assert annotate_spread(None, -3.0) is None

    def test_no_point_returns_none(self):
        assert annotate_spread("nfl", None) is None


class TestAnnotateTotal:
    def test_on_total_key_44(self):
        info = annotate_total("nfl", 44.0)
        assert info is not None
        assert info.on_key is True
        assert info.nearest_key == 44
        # Totals don't have a published half-point value table here.
        assert info.half_point_value_pp is None

    def test_on_total_key_41(self):
        info = annotate_total("nfl", 41.0)
        assert info is not None
        assert info.on_key is True
        assert info.nearest_key == 41

    def test_straddles_total_43_5(self):
        info = annotate_total("nfl", 43.5)
        assert info is not None
        assert info.on_key is False
        assert info.straddles_key is True
        assert info.nearest_key == 44

    def test_not_near_total_key_42(self):
        # 42 is 1 from 41 and 2 from 44 — closest to 41 but outside
        # half-point zone.
        info = annotate_total("nfl", 42.0)
        assert info is not None
        assert info.on_key is False
        assert info.straddles_key is False
        assert info.nearest_key == 41

    def test_non_nfl_total_returns_none(self):
        assert annotate_total("baseball_mlb", 8.5) is None
        assert annotate_total("nba", 220.5) is None


class TestAnnotateDispatcher:
    def test_routes_spread_market(self):
        info = annotate("nfl", "spread", -3.0)
        assert info is not None
        assert info.nearest_key == 3

    def test_routes_handicap_alias(self):
        # Many providers call it "handicap" instead of "spread".
        info = annotate("nfl", "handicap", -3.0)
        assert info is not None
        assert info.nearest_key == 3

    def test_routes_total_market(self):
        info = annotate("nfl", "total", 44.0)
        assert info is not None
        assert info.nearest_key == 44

    def test_routes_totals_alias(self):
        info = annotate("nfl", "totals", 44.0)
        assert info is not None
        assert info.nearest_key == 44

    def test_moneyline_returns_none(self):
        # 1x2/moneyline has no key-number concept.
        assert annotate("nfl", "moneyline", None) is None
        assert annotate("nfl", "1x2", None) is None

    def test_unknown_market_returns_none(self):
        assert annotate("nfl", "props", -3.0) is None

    def test_to_dict_serialisable(self):
        info = annotate("nfl", "spread", -3.0)
        assert info is not None
        d = info.to_dict()
        # Must be a plain dict with primitive values so it round-trips
        # through JSON on the API surface.
        assert isinstance(d, dict)
        assert d["on_key"] is True
        assert d["nearest_key"] == 3
        assert d["distance"] == 0.0
        assert d["half_point_value_pp"] == SPREAD_HALF_POINT_VALUE_PP[3]


class TestConstants:
    def test_three_is_most_important_spread_key(self):
        # 3 is the densest NFL margin and must be present.
        assert 3 in NFL_SPREAD_KEY_NUMBERS

    def test_seven_is_present(self):
        # 7 is the second-most-important margin.
        assert 7 in NFL_SPREAD_KEY_NUMBERS

    def test_total_keys_include_41_and_44(self):
        # 41 and 44 are the densest total landing spots historically.
        assert 41 in NFL_TOTAL_KEY_NUMBERS
        assert 44 in NFL_TOTAL_KEY_NUMBERS

    def test_half_point_value_decreasing_through_keys(self):
        # The 3 hook is more valuable than any other half-point — sanity
        # check that we didn't accidentally invert the table.
        assert SPREAD_HALF_POINT_VALUE_PP[3] >= SPREAD_HALF_POINT_VALUE_PP[7]
        assert SPREAD_HALF_POINT_VALUE_PP[7] >= SPREAD_HALF_POINT_VALUE_PP[6]
        assert SPREAD_HALF_POINT_VALUE_PP[6] >= SPREAD_HALF_POINT_VALUE_PP[10]


class TestValueBetIntegration:
    """ValueBet dataclass accepts the key_number field; scanner safely
    handles non-NFL sports (annotation is None)."""

    def test_value_bet_accepts_key_number_field(self):
        from src.analysis.value import ValueBet

        vb = ValueBet(
            event_id="nfl:abc",
            market="spread",
            outcome="home",
            provider="pinnacle",
            provider_odds=1.95,
            fair_odds=2.0,
            fair_probability=0.5,
            edge_pct=2.5,
            point=-2.5,
            key_number={
                "on_key": False,
                "straddles_key": True,
                "nearest_key": 3,
                "distance": -0.5,
                "half_point_value_pp": 2.5,
            },
        )
        assert vb.key_number is not None
        assert vb.key_number["nearest_key"] == 3
        assert vb.key_number["straddles_key"] is True

    def test_value_bet_key_number_defaults_none(self):
        from src.analysis.value import ValueBet

        vb = ValueBet(
            event_id="soccer:abc",
            market="moneyline",
            outcome="home",
            provider="pinnacle",
            provider_odds=1.95,
            fair_odds=2.0,
            fair_probability=0.5,
            edge_pct=2.5,
        )
        assert vb.key_number is None
