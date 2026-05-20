"""Regression: Polymarket CLOB /books returns asks DESCENDING by price.

_calc_vwap_from_asks must fill the depth-VWAP walk from the best (lowest)
ask, regardless of input order. Walking a descending array front-to-back
fills from the most expensive asks → wrong VWAP → mispriced stored odds
(the fake +45% "arb" of 2026-05-20).
"""

from src.providers.polymarket import PolymarketRetriever


def test_calc_vwap_from_asks_handles_descending_order():
    # Polymarket /books order: highest price first. True best ask = 0.34.
    asks = [
        {"price": "0.99", "size": "100"},
        {"price": "0.40", "size": "100"},
        {"price": "0.34", "size": "100"},
    ]
    vwap, _depth = PolymarketRetriever._calc_vwap_from_asks(asks, fill_size_usd=25)
    # $25 fills entirely inside the 0.34 level ($34 available) → VWAP ~= 0.34.
    assert abs(vwap - 0.34) < 0.01, f"expected ~0.34 (best ask), got {vwap}"


def test_calc_vwap_from_asks_ascending_still_correct():
    # Already-ascending input must still produce the same result.
    asks = [
        {"price": "0.34", "size": "100"},
        {"price": "0.40", "size": "100"},
        {"price": "0.99", "size": "100"},
    ]
    vwap, _depth = PolymarketRetriever._calc_vwap_from_asks(asks, fill_size_usd=25)
    assert abs(vwap - 0.34) < 0.01, f"expected ~0.34, got {vwap}"


def test_calc_vwap_from_asks_walks_into_second_level():
    # Thin best level: $25 target, best ask 0.34 has only $10 of depth, so the
    # walk must continue into the 0.40 level — VWAP lands between the two.
    asks = [
        {"price": "0.40", "size": "100"},
        {"price": "0.34", "size": "29.41"},  # 29.41 * 0.34 ~= $10 depth
    ]
    vwap, _depth = PolymarketRetriever._calc_vwap_from_asks(asks, fill_size_usd=25)
    assert 0.34 < vwap < 0.40, f"expected blended VWAP in (0.34, 0.40), got {vwap}"
