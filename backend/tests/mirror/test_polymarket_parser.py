"""Tests for Polymarket mirror parser."""
import pytest
from src.mirror.parsers.polymarket import PolymarketParser


class TestBalanceParsing:
    def test_parse_value_response(self):
        """data-api.polymarket.com/value returns [{"user": "0x...", "value": 123.45}]"""
        parser = PolymarketParser()
        body = '[{"user": "0x71fca29E6B31a93d262D2972C9b361Af371D426d", "value": 123.45}]'
        result = parser.parse_balance("https://data-api.polymarket.com/value?user=0x71fca", body)
        assert result == 123.45

    def test_parse_zero_balance(self):
        parser = PolymarketParser()
        body = '[{"user": "0x71fca29E6B31a93d262D2972C9b361Af371D426d", "value": 0}]'
        result = parser.parse_balance("https://data-api.polymarket.com/value?user=0x71fca", body)
        assert result == 0.0

    def test_parse_invalid_json(self):
        parser = PolymarketParser()
        result = parser.parse_balance("https://data-api.polymarket.com/value", "not json")
        assert result is None

    def test_parse_empty_array(self):
        parser = PolymarketParser()
        result = parser.parse_balance("https://data-api.polymarket.com/value", "[]")
        assert result is None


class TestOrderParsing:
    def test_parse_open_orders(self):
        """clob.polymarket.com/data/orders returns order list."""
        parser = PolymarketParser()
        body = '''[{
            "id": "order-123",
            "status": "live",
            "asset_id": "token-abc",
            "side": "BUY",
            "price": "0.62",
            "original_size": "25.0",
            "size_matched": "10.0",
            "outcome": "Yes",
            "market": "Will X happen?",
            "created_at": 1774997100
        }]'''
        orders = parser.parse_orders(body)
        assert len(orders) == 1
        assert orders[0]["id"] == "order-123"
        assert orders[0]["status"] == "live"
        assert orders[0]["token_id"] == "token-abc"
        assert orders[0]["side"] == "BUY"
        assert orders[0]["price"] == 0.62
        assert orders[0]["size"] == 25.0
        assert orders[0]["filled"] == 10.0
        assert orders[0]["outcome"] == "Yes"
        assert orders[0]["market"] == "Will X happen?"

    def test_parse_empty_orders(self):
        parser = PolymarketParser()
        assert parser.parse_orders("[]") == []


class TestDepositParsing:
    def test_parse_swapped_order(self):
        """widget.swapped.com/api/v1/order/create_order response."""
        parser = PolymarketParser()
        body = '{"orderId": "sw-123", "amount": 100, "currency": "USD", "status": "pending"}'
        result = parser.parse_deposit("https://widget.swapped.com/api/v1/order/create_order", body)
        assert result is not None
        assert result["amount"] == 100
        assert result["order_id"] == "sw-123"

    def test_non_deposit_url(self):
        parser = PolymarketParser()
        result = parser.parse_deposit("https://other.com/api", '{"foo": 1}')
        assert result is None


class TestPriceVerification:
    def test_price_within_slippage(self):
        parser = PolymarketParser()
        assert parser.check_slippage(expected=0.62, actual=0.63, max_pct=2.0) is True

    def test_price_exceeds_slippage(self):
        parser = PolymarketParser()
        assert parser.check_slippage(expected=0.62, actual=0.70, max_pct=2.0) is False

    def test_parse_book_best_ask(self):
        parser = PolymarketParser()
        book_body = '{"asks": [{"price": "0.63", "size": "150"}, {"price": "0.65", "size": "200"}], "bids": [{"price": "0.61", "size": "100"}]}'
        best_ask = parser.parse_best_ask(book_body)
        assert best_ask == 0.63

    def test_parse_book_empty(self):
        parser = PolymarketParser()
        assert parser.parse_best_ask('{"asks": [], "bids": []}') is None
