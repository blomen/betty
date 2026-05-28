"""Altenar placeholder-odds filter.

Altenar's API ships ``1.8334 / 1.8334`` (= 11/6, implied 0.5455 each side) on
spread/total markets the bookmaker hasn't actively priced. The customer
website renders the real alternate-handicap ladder from a separate feed Betty
doesn't subscribe to, so the sentinel never matches anything bettable — and
comparing it to Pinnacle's real odds produces phantom +EV / arb opportunities.

Discovered 2026-05-29: NBL1 Women Logan Thunder vs North Gold Coast Seahawks.
Betinia DB had spread -23.5 @ 1.8334 / +23.5 @ 1.8334 (matching API exactly),
while the website showed -23.5 @ 2.270 / +23.5 @ 1.510. Scanner surfaced a
+7.52% arb vs Pinnacle that was completely phantom.
"""

from __future__ import annotations

from src.providers.altenar import AltenarRetriever, _is_placeholder_market


def _ref(market: dict, odds: dict[int, dict]) -> dict:
    return {
        "_comp_idx": {1: {"id": 1, "name": "Logan Thunder"}, 2: {"id": 2, "name": "North Gold Coast Seahawks"}},
        "_champ_idx": {100: {"id": 100, "name": "NBL1 Women", "catId": 999}},
        "_market_idx": {market["id"]: market},
        "_odd_idx": odds,
    }


def _event(market_id: int) -> dict:
    return {
        "id": "evt-1",
        "name": "Logan Thunder vs North Gold Coast Seahawks",
        "startDate": "2026-05-29T08:00:00Z",
        "competitorIds": [1, 2],
        "champId": 100,
        "marketIds": [market_id],
    }


def test_is_placeholder_market_detects_double_sentinel():
    """Both legs at exactly 1.8334 → placeholder."""
    outcomes = [{"odds": 1.8334}, {"odds": 1.8334}]
    assert _is_placeholder_market(outcomes) is True


def test_is_placeholder_market_within_tolerance():
    """Tiny float drift around 1.8334 still trips the filter."""
    outcomes = [{"odds": 1.83340001}, {"odds": 1.83339999}]
    assert _is_placeholder_market(outcomes) is True


def test_is_placeholder_market_rejects_real_balanced_pickem():
    """A genuine pick'em mainline lands around 1.91/1.91 — not the sentinel."""
    outcomes = [{"odds": 1.91}, {"odds": 1.91}]
    assert _is_placeholder_market(outcomes) is False


def test_is_placeholder_market_rejects_asymmetric_real_odds():
    """Normal sharp lines have varied odds across legs."""
    outcomes = [{"odds": 2.27}, {"odds": 1.51}]
    assert _is_placeholder_market(outcomes) is False


def test_is_placeholder_market_rejects_one_sided_sentinel():
    """If only one leg matches the sentinel, the market is probably real."""
    outcomes = [{"odds": 1.8334}, {"odds": 1.51}]
    assert _is_placeholder_market(outcomes) is False


def test_parse_event_drops_placeholder_spread():
    """Spread market with both legs at the sentinel is dropped entirely."""
    retriever = AltenarRetriever({"id": "betinia"})
    market = {
        "id": 555,
        "typeId": 223,  # basketball spread (incl. OT)
        "name": "Spread (incl. OT)",
        "sv": "-23.5",
        "oddIds": [1001, 1002],
    }
    odds = {
        1001: {"id": 1001, "name": "Logan Thunder (W) (-23.5)", "price": 1.8334},
        1002: {"id": 1002, "name": "North Gold Coast Seahawks (W) (+23.5)", "price": 1.8334},
    }
    se = retriever._parse_event(_event(555), sport="basketball", reference_data=_ref(market, odds), sport_id=67)
    assert se is not None
    assert [m for m in se.markets if m["type"] == "spread"] == []


def test_parse_event_drops_placeholder_total():
    """Total market with both legs at the sentinel is dropped."""
    retriever = AltenarRetriever({"id": "betinia"})
    market = {
        "id": 666,
        "typeId": 225,  # basketball total (incl. OT)
        "name": "Total (incl. OT)",
        "sv": "157.5",
        "oddIds": [2001, 2002],
    }
    odds = {
        2001: {"id": 2001, "name": "Over 157.5", "price": 1.8334},
        2002: {"id": 2002, "name": "Under 157.5", "price": 1.8334},
    }
    se = retriever._parse_event(_event(666), sport="basketball", reference_data=_ref(market, odds), sport_id=67)
    assert se is not None
    assert [m for m in se.markets if m["type"] == "total"] == []


def test_parse_event_keeps_real_spread_with_varied_odds():
    """Real spread (varied odds across legs) is preserved."""
    retriever = AltenarRetriever({"id": "betinia"})
    market = {
        "id": 777,
        "typeId": 223,
        "name": "Spread (incl. OT)",
        "sv": "-23.5",
        "oddIds": [3001, 3002],
    }
    odds = {
        3001: {"id": 3001, "name": "Logan Thunder (W) (-23.5)", "price": 2.27},
        3002: {"id": 3002, "name": "North Gold Coast Seahawks (W) (+23.5)", "price": 1.51},
    }
    se = retriever._parse_event(_event(777), sport="basketball", reference_data=_ref(market, odds), sport_id=67)
    assert se is not None
    spreads = [m for m in se.markets if m["type"] == "spread"]
    assert len(spreads) == 1
    by_name = {o["name"]: o for o in spreads[0]["outcomes"]}
    assert by_name["home"]["odds"] == 2.27
    assert by_name["away"]["odds"] == 1.51


def test_parse_event_keeps_moneyline_even_at_sentinel_value():
    """Moneyline is NOT filtered — only spread/total. A 1.83/1.83 ML could be a
    real pick'em, and ML markets in the observed data were never placeholder.
    """
    retriever = AltenarRetriever({"id": "betinia"})
    market = {
        "id": 888,
        "typeId": 219,  # basketball winner (incl. OT)
        "name": "Winner (incl. OT)",
        "oddIds": [4001, 4002],
    }
    odds = {
        4001: {"id": 4001, "name": "Logan Thunder (W)", "price": 1.8334},
        4002: {"id": 4002, "name": "North Gold Coast Seahawks (W)", "price": 1.8334},
    }
    se = retriever._parse_event(_event(888), sport="basketball", reference_data=_ref(market, odds), sport_id=67)
    assert se is not None
    mls = [m for m in se.markets if m["type"] == "moneyline"]
    assert len(mls) == 1
