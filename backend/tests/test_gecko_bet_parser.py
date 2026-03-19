"""Tests for Gecko V2 bet response parser."""
import pytest
from src.mirror.parsers.gecko import GeckoBetParser


class TestGeckoBetParser:
    def setup_method(self):
        self.parser = GeckoBetParser()

    def test_is_bet_placement_url_positive(self):
        assert self.parser.is_bet_placement_url(
            "https://sb2frontend-altenar2.bfrp.io/api/sb/v1/betslip/place"
        )
        assert self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/betslip/coupon"
        )

    def test_is_bet_placement_url_negative(self):
        assert not self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/widgets/events-table/v2"
        )
        assert not self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/odds/123"
        )

    def test_parse_confirmed_bet(self):
        response_body = {
            "data": {
                "betId": "bet_abc123",
                "status": "Confirmed",
                "stakes": [{"amount": 100.0}],
                "selections": [
                    {
                        "eventName": "Virginia United vs North Lakes United",
                        "marketTemplateName": "Match Winner",
                        "selectionName": "Virginia United",
                        "odds": 2.10,
                        "eventId": "evt_456",
                        "participants": [
                            {"label": "Virginia United", "side": 1},
                            {"label": "North Lakes United", "side": 2},
                        ],
                    }
                ],
            }
        }
        result = self.parser.parse(response_body)
        assert result is not None
        assert result["confirmation_id"] == "bet_abc123"
        assert result["odds"] == 2.10
        assert result["stake"] == 100.0
        assert result["home_team"] is not None
        assert result["away_team"] is not None

    def test_parse_rejected_bet(self):
        response_body = {
            "data": {
                "status": "Rejected",
                "rejectionReason": "Odds changed",
            }
        }
        result = self.parser.parse(response_body)
        assert result is None

    def test_is_rejection(self):
        rejected = {"data": {"status": "Rejected"}}
        confirmed = {"data": {"status": "Confirmed", "betId": "123"}}
        assert self.parser.is_rejection(rejected) is True
        assert self.parser.is_rejection(confirmed) is False
