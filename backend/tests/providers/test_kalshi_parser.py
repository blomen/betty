"""Tests for Kalshi market parser."""
import pytest

from src.providers.kalshi import parse_event, series_to_sport


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
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.60),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.42),
            ],
            title="Lakers vs Warriors",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "basketball"
        assert result.provider == "kalshi"
        assert len(result.markets) == 1
        mkt = result.markets[0]
        assert mkt["type"] == "moneyline"
        assert len(mkt["outcomes"]) == 2
        # Odds = 1 / (price + fee_rate * price * (1-price))
        # For price=0.60 -> effective=0.6 + 0.02*0.6*0.4 = 0.6048 -> 1/0.6048 ~ 1.653
        assert mkt["outcomes"][0]["name"] in ("home", "away")
        assert 1.6 < mkt["outcomes"][0]["odds"] < 1.7

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
        event = self._event(
            "KXEPL-26MAY-ARSCHE",
            [
                self._market("KXEPL-26MAY-ARSCHE-ARS", "Arsenal win", 0.55),
                self._market("KXEPL-26MAY-ARSCHE-DRAW", "Draw", 0.25),
                self._market("KXEPL-26MAY-ARSCHE-CHE", "Chelsea win", 0.22),
            ],
            title="Arsenal vs Chelsea",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "football"
        mkt = result.markets[0]
        assert mkt["type"] == "1x2"
        assert len(mkt["outcomes"]) == 3
        names = {o["name"] for o in mkt["outcomes"]}
        assert names == {"home", "draw", "away"}
