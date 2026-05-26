"""Pinnacle extractor sets scope='ft' on period 0 and 'reg' on period 6 hockey."""

from __future__ import annotations

from src.providers.pinnacle import PinnacleRetriever


def _make_market(period: int, market_type: str = "total"):
    return {
        "status": "open",
        "type": market_type,
        "period": period,
        "isAlternate": False,
        "lineId": 1,
        "matchupId": 1,
        "prices": [
            {"designation": "over", "price": -110, "points": 4.5},
            {"designation": "under", "price": -110, "points": 4.5},
        ],
    }


def test_period_0_emits_scope_ft():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=0)], sport="ice_hockey")
    assert all(m.get("scope") == "ft" for m in parsed), (
        f"expected all scope='ft', got {[m.get('scope') for m in parsed]}"
    )


def test_period_6_hockey_emits_scope_reg():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=6)], sport="ice_hockey")
    assert parsed, "no markets parsed"
    assert all(m.get("scope") == "reg" for m in parsed), f"expected scope='reg', got {[m.get('scope') for m in parsed]}"


def test_period_6_hockey_1x2_also_emits_scope_reg():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=6, market_type="moneyline")], sport="ice_hockey")
    assert parsed
    assert all(m.get("scope") == "reg" for m in parsed)


def test_esports_map_period_emits_scope_map_n():
    p = PinnacleRetriever({"id": "pinnacle"})
    for map_n in (1, 2, 3):
        parsed = p._parse_markets([_make_market(period=map_n, market_type="moneyline")], sport="esports")
        assert parsed, f"no markets for map period {map_n}"
        assert all(m.get("scope") == f"map_{map_n}" for m in parsed), (
            f"expected scope='map_{map_n}', got {[m.get('scope') for m in parsed]}"
        )


def test_period_0_moneyline_emits_scope_ft():
    p = PinnacleRetriever({"id": "pinnacle"})
    market = _make_market(period=0, market_type="moneyline")
    market["prices"] = [
        {"designation": "home", "price": -150},
        {"designation": "away", "price": 130},
    ]
    parsed = p._parse_markets([market], sport="basketball")
    assert parsed
    assert all(m.get("scope") == "ft" for m in parsed)


def test_baseball_period_1_emits_scope_f5():
    """MLB period 1 = first 5 innings → scope='f5'. Applies to all three core markets."""
    p = PinnacleRetriever({"id": "pinnacle"})
    for market_type in ("total", "spread"):
        parsed = p._parse_markets([_make_market(period=1, market_type=market_type)], sport="baseball")
        assert parsed, f"no markets parsed for {market_type}"
        assert all(m.get("scope") == "f5" for m in parsed), (
            f"expected scope='f5' for {market_type}, got {[m.get('scope') for m in parsed]}"
        )

    ml = _make_market(period=1, market_type="moneyline")
    ml["prices"] = [
        {"designation": "home", "price": -120},
        {"designation": "away", "price": 105},
    ]
    parsed_ml = p._parse_markets([ml], sport="baseball")
    assert parsed_ml
    assert all(m.get("scope") == "f5" for m in parsed_ml)


def test_baseball_period_3_emits_scope_f3():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=3, market_type="total")], sport="baseball")
    assert parsed
    assert all(m.get("scope") == "f3" for m in parsed)


def test_non_baseball_period_1_does_not_emit_f5():
    """Period 1 on non-baseball/non-esports sports is dropped, not tagged f5."""
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=1, market_type="total")], sport="football")
    assert parsed == [], f"expected period 1 dropped for football, got {parsed}"
