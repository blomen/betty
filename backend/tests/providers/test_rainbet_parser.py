"""Tests for the Rainbet/Betby pure parser functions.

The fixtures use real payload samples captured during the discovery pass; see
docs/superpowers/research/2026-05-10-rainbet-discovery.md for provenance.
"""

import pytest

from src.providers.rainbet import betby_sport_id_to_arnold


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
