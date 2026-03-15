"""Tests for 10bet event detail market parsing."""
import pytest
from src.providers.tenbet import TenBetRetriever


class TestParseDetailSpread:
    def test_parse_asian_handicap(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        raw = {
            "outcomes": [
                {"name": "Arsenal", "point": "-1.5", "odds": "2.20"},
                {"name": "Everton", "point": "+1.5", "odds": "1.67"},
            ],
        }
        result = retriever._parse_detail_spread(raw)
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0]["point"] == -1.5
        assert result["outcomes"][1]["point"] == 1.5

    def test_no_outcomes(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        assert retriever._parse_detail_spread({"outcomes": []}) is None

    def test_missing_point(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        raw = {
            "outcomes": [
                {"name": "Arsenal", "point": "", "odds": "2.20"},
                {"name": "Everton", "point": "", "odds": "1.67"},
            ],
        }
        assert retriever._parse_detail_spread(raw) is None


class TestParseDetailTotal:
    def test_parse_over_under(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        raw = {
            "outcomes": [
                {"name": "Over 2.5", "odds": "1.95"},
                {"name": "Under 2.5", "odds": "1.83"},
            ],
        }
        result = retriever._parse_detail_total(raw)
        assert result is not None
        assert result["type"] == "total"
        assert result["outcomes"][0]["point"] == 2.5
        assert result["outcomes"][1]["point"] == 2.5

    def test_no_outcomes(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        assert retriever._parse_detail_total({"outcomes": []}) is None

    def test_swedish_over_under(self):
        """10bet.se uses Swedish 'Över' / 'Under'."""
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        raw = {
            "outcomes": [
                {"name": "Över 2.5", "odds": "1.95"},
                {"name": "Under 2.5", "odds": "1.83"},
            ],
        }
        result = retriever._parse_detail_total(raw)
        assert result is not None
        assert result["outcomes"][0]["name"] == "over"
