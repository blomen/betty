"""Tests for Polymarket mirror wiring in MirrorService."""
import pytest
from src.mirror.service import MirrorService


class TestPolymarketBalanceExtraction:
    def test_extract_polymarket_balance(self):
        """_extract_balance should handle Polymarket value response."""
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        data = [{"user": "0x71fca29E6B31a93d262D2972C9b361Af371D426d", "value": 87.5}]
        balance = service._extract_balance("polymarket", data)
        assert balance == 87.5

    def test_extract_polymarket_zero(self):
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        data = [{"user": "0x71fca", "value": 0}]
        balance = service._extract_balance("polymarket", data)
        assert balance == 0.0

    def test_extract_polymarket_empty(self):
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        balance = service._extract_balance("polymarket", [])
        assert balance is None


class TestPolymarketProviderDetection:
    def test_detect_polymarket_from_value_url(self):
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        assert service._detect_provider("https://data-api.polymarket.com/value?user=0x71fca") == "polymarket"

    def test_detect_polymarket_from_clob_url(self):
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        assert service._detect_provider("https://clob.polymarket.com/data/orders") == "polymarket"

    def test_detect_polymarket_from_swapped_url(self):
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        assert service._detect_provider("https://widget.swapped.com/api/v1/order/create_order") == "polymarket"
