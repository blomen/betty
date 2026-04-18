"""Tests for Kalshi market parser."""
import pytest

from src.providers.kalshi import series_to_sport


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
