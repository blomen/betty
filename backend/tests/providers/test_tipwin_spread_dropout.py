"""Tipwin emits 3-way European handicap (home/draw/away at integer
handicaps) — fundamentally different from the 2-way Asian handicap
Pinnacle prices. Comparing them produces phantom 14% edges on the
"away" leg (which leaks past scanner's 3-way drop because it lands in a
different market_key after the away sign flip).

Drop any spread market emitted with a `draw` outcome at the extractor
boundary so it never reaches storage or the scanner.

Regression test for the 2026-05-26 tipwin spread bug across multiple
football matches (Lahti/Ilves, Colegiales/Agropecuario, etc.).
"""

from __future__ import annotations

from src.providers.tipwin import TipwinRetriever


def _handicap_offer(point_hcp: str, home_odds: float, away_odds: float, draw_odds: float | None):
    """Build a tipwin-style market_offer dict for a handicap line."""
    inner_offers = [
        {"tip": "1", "value": str(home_odds)},
        {"tip": "2", "value": str(away_odds)},
    ]
    if draw_odds is not None:
        inner_offers.append({"tip": "X", "value": str(draw_odds)})
    return {
        "bettingTypeId": 7,
        "key": {"specifier": {"hcp": point_hcp}},
        "offers": inner_offers,
    }


_BTYPES_HCP = {7: {"abrv": "handicap-hcp"}}
_BTYPES_TOTAL = {18: {"abrv": "over-under"}}


def test_tipwin_three_way_handicap_market_dropped():
    """A handicap market that emits a `draw` outcome must NOT be returned
    as a spread market — it's European 3-way, not Asian 2-way."""
    retriever = TipwinRetriever({"id": "tipwin"})
    offer = _handicap_offer(point_hcp="0:1", home_odds=4.95, away_odds=1.45, draw_odds=4.00)
    markets = retriever._parse_markets([offer], _BTYPES_HCP)
    spread_markets = [m for m in markets if m.get("type") == "spread"]
    assert not spread_markets, (
        f"tipwin emitted a 3-way European handicap as a 2-way spread — phantom value edges incoming: {spread_markets}"
    )


def test_tipwin_two_way_asian_handicap_market_kept():
    """A 2-way Asian handicap (no draw outcome) is legitimate and stays."""
    retriever = TipwinRetriever({"id": "tipwin"})
    offer = _handicap_offer(point_hcp="0:1", home_odds=2.10, away_odds=1.80, draw_odds=None)
    markets = retriever._parse_markets([offer], _BTYPES_HCP)
    spread_markets = [m for m in markets if m.get("type") == "spread"]
    assert len(spread_markets) == 1
    names = sorted(o["name"] for o in spread_markets[0]["outcomes"])
    assert names == ["away", "home"]
    assert all(o["name"] != "draw" for o in spread_markets[0]["outcomes"])


def test_tipwin_total_market_with_two_sides_unaffected():
    """Drop logic is spread-only — totals never have draw outcomes; this
    test pins the scope so a future overzealous filter doesn't catch totals."""
    retriever = TipwinRetriever({"id": "tipwin"})
    offer = {
        "bettingTypeId": 18,
        "key": {"specifier": {"total": "2.5"}},
        "offers": [
            {"tip": "+", "value": "1.85"},
            {"tip": "-", "value": "2.00"},
        ],
    }
    markets = retriever._parse_markets([offer], _BTYPES_TOTAL)
    total_markets = [m for m in markets if m.get("type") == "total"]
    assert total_markets, "tipwin total market got incorrectly dropped"
