"""Pinnacle parser extracts maxRiskStake from market.limits into each outcome."""

from src.providers.pinnacle import PinnacleRetriever


def _market_with_limits(limits):
    return {
        "status": "open",
        "type": "moneyline",
        "period": 0,
        "isAlternate": False,
        "lineId": 1,
        "matchupId": 1,
        "limits": limits,
        "prices": [
            {"designation": "home", "price": -110},
            {"designation": "away", "price": -110},
        ],
    }


def test_max_stake_extracted_from_limits():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_market_with_limits([{"amount": 1500, "type": "maxRiskStake"}])])
    assert parsed
    for m in parsed:
        for o in m["outcomes"]:
            assert o.get("max_stake") == 1500.0


def test_max_stake_none_when_limits_missing():
    p = PinnacleRetriever({"id": "pinnacle"})
    market = _market_with_limits([])
    del market["limits"]  # remove entirely
    parsed = p._parse_markets([market])
    assert parsed
    for m in parsed:
        for o in m["outcomes"]:
            assert o.get("max_stake") is None


def test_max_stake_none_when_limits_empty():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_market_with_limits([])])
    assert parsed
    for m in parsed:
        for o in m["outcomes"]:
            assert o.get("max_stake") is None
