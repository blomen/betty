"""Tests for Gecko V2 bet response parser.

Updated with real API schema discovered 2026-03-20.
Endpoint: POST /api/sb/v2/coupons
"""
import json
import pytest
from src.mirror.parsers.gecko import GeckoBetParser


class TestGeckoBetParser:
    def setup_method(self):
        self.parser = GeckoBetParser()

    # --- URL detection ---

    def test_is_bet_placement_url_coupons(self):
        assert self.parser.is_bet_placement_url(
            "https://d-cf.spelklubbenplayground.net/api/sb/v2/coupons"
        )

    def test_is_bet_placement_url_betslip(self):
        assert self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/betslip/place"
        )

    def test_is_bet_placement_url_negative(self):
        assert not self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/widgets/events-table/v2"
        )
        assert not self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/odds/123"
        )

    # --- Rejection detection ---

    def test_is_rejection_success(self):
        body = {
            "couponStatus": {
                "couponStatusPollingResult": "Success",
                "couponId": "123",
                "couponPlacementErrors": [],
            }
        }
        assert self.parser.is_rejection(body) is False

    def test_is_rejection_failed(self):
        body = {
            "couponStatus": {
                "couponStatusPollingResult": "Failed",
                "couponPlacementErrors": [{"code": "OddsChanged"}],
            }
        }
        assert self.parser.is_rejection(body) is True

    def test_is_rejection_with_errors(self):
        body = {
            "couponStatus": {
                "couponStatusPollingResult": "Success",
                "couponPlacementErrors": [{"code": "StakeTooHigh"}],
            }
        }
        assert self.parser.is_rejection(body) is True

    # --- Parse real schema ---

    def test_parse_total_over(self):
        """Real bet: total over 3.5 @ 2.65, 225 SEK."""
        request_body = json.dumps({
            "bets": [{
                "stake": 225,
                "currencyCode": "SEK",
                "betSelections": [{
                    "marketSelectionId": "s-m-f-K4Qf1s_QPkeNqhDn68MZsQ-MTG2W-3.5-over",
                    "odds": "2.65",
                }],
            }],
        })
        response_body = {
            "couponStatus": {
                "couponStatusPollingResult": "Success",
                "couponId": "172497283293644800",
                "couponPlacementErrors": [],
            }
        }
        result = self.parser.parse(response_body, request_body)
        assert result is not None
        assert result["confirmation_id"] == "172497283293644800"
        assert result["odds"] == 2.65
        assert result["stake"] == 225.0
        assert result["market"] == "total"
        assert result["outcome"] == "over"
        assert result["point"] == 3.5
        assert result["gecko_event_id"] == "K4Qf1s_QPkeNqhDn68MZsQ"

    def test_parse_1x2_home(self):
        request_body = json.dumps({
            "bets": [{
                "stake": 100,
                "betSelections": [{
                    "marketSelectionId": "s-m-f-eventABC-MW3W-home",
                    "odds": "1.95",
                }],
            }],
        })
        response_body = {
            "couponStatus": {
                "couponStatusPollingResult": "Success",
                "couponId": "99887766",
                "couponPlacementErrors": [],
            }
        }
        result = self.parser.parse(response_body, request_body)
        assert result is not None
        assert result["market"] == "1x2"
        assert result["outcome"] == "home"
        assert result["point"] is None
        assert result["stake"] == 100.0
        assert result["odds"] == 1.95

    def test_parse_spread(self):
        request_body = json.dumps({
            "bets": [{
                "stake": 50,
                "betSelections": [{
                    "marketSelectionId": "s-m-f-eventXYZ-M2WHCP-1.5-HANDICAPHOME",
                    "odds": "1.80",
                }],
            }],
        })
        response_body = {
            "couponStatus": {
                "couponStatusPollingResult": "Success",
                "couponId": "55544433",
                "couponPlacementErrors": [],
            }
        }
        result = self.parser.parse(response_body, request_body)
        assert result is not None
        assert result["market"] == "spread"
        assert result["outcome"] == "home"
        assert result["point"] == 1.5

    def test_parse_rejected_returns_none(self):
        request_body = json.dumps({
            "bets": [{"stake": 100, "betSelections": [{"marketSelectionId": "s-m-f-x-MW3W-home", "odds": "2.0"}]}],
        })
        response_body = {
            "couponStatus": {
                "couponStatusPollingResult": "Failed",
                "couponPlacementErrors": [{"code": "OddsChanged"}],
            }
        }
        result = self.parser.parse(response_body, request_body)
        assert result is None

    def test_parse_no_request_body(self):
        response_body = {
            "couponStatus": {
                "couponStatusPollingResult": "Success",
                "couponId": "123",
                "couponPlacementErrors": [],
            }
        }
        result = self.parser.parse(response_body, None)
        assert result is None

    # --- Selection ID parsing ---

    def test_parse_selection_id_total(self):
        result = self.parser._parse_selection_id("s-m-f-K4Qf1s_QPkeNqhDn68MZsQ-MTG2W-3.5-over")
        assert result["event_id"] == "K4Qf1s_QPkeNqhDn68MZsQ"
        assert result["market"] == "total"
        assert result["point"] == 3.5
        assert result["outcome"] == "over"

    def test_parse_selection_id_1x2(self):
        result = self.parser._parse_selection_id("s-m-f-abc123-MW3W-home")
        assert result["event_id"] == "abc123"
        assert result["market"] == "1x2"
        assert result["point"] is None
        assert result["outcome"] == "home"

    def test_parse_selection_id_spread(self):
        result = self.parser._parse_selection_id("s-m-f-xyz-M2WHCP-1.5-HANDICAPHOME")
        assert result["market"] == "spread"
        assert result["point"] == 1.5
        assert result["outcome"] == "home"

    def test_parse_selection_id_empty(self):
        result = self.parser._parse_selection_id("")
        assert result["market"] is None
