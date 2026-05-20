"""Kalshi strategy — event-ticker derivation + live-price side tests.

Regression coverage for:
  - the spread/total navigation bug: market tickers whose outcome suffix
    contains digits (-3, -ALN1, -CAG2) must still strip down to the
    2-segment event ticker the /v1/cached/events endpoint expects.
  - the NO-side live-price bug: check_live_price always read the contract's
    yes_ask, so a total "under" / spread non-yes_side bet was re-priced off
    the wrong side and its edge went sharply negative on click.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from arnold.mirror.workflows.strategies import kalshi
from arnold.mirror.workflows.strategies.kalshi import (
    _bet_is_no_side,
    _check_live_price,
    _event_ticker_from_market,
)


@pytest.mark.parametrize(
    "market_ticker,expected",
    [
        # Spread/total markets — outcome suffix carries digits. These broke
        # navigation: the old heuristic misread "-3"/"-ALN1" as a date segment.
        ("KXEPLTOTAL-26MAY24BRIMUN-3", "KXEPLTOTAL-26MAY24BRIMUN"),
        ("KXUELTOTAL-26MAY20SCFAVL-2", "KXUELTOTAL-26MAY20SCFAVL"),
        ("KXSAUDIPLSPREAD-26MAY21ALNDAM-ALN1", "KXSAUDIPLSPREAD-26MAY21ALNDAM"),
        ("KXSERIEASPREAD-26MAY24ACMCAG-CAG2", "KXSERIEASPREAD-26MAY24ACMCAG"),
        # Moneyline markets — alphabetic outcome suffix. Must keep working.
        ("KXBOXING-26MAY16DOHERTHATIM-HATIM", "KXBOXING-26MAY16DOHERTHATIM"),
        # Single-market events (2-segment) — market ticker IS the event ticker.
        ("KXBOXING-26MAY16DOHERTHATIM", "KXBOXING-26MAY16DOHERTHATIM"),
        ("KXTRUMPMENTION-26APR30", "KXTRUMPMENTION-26APR30"),
        # Degenerate input.
        ("", ""),
    ],
)
def test_event_ticker_from_market(market_ticker, expected):
    assert _event_ticker_from_market(market_ticker) == expected


@pytest.mark.parametrize(
    "outcome,meta,expected",
    [
        # Moneyline — each side is its own YES contract → never NO.
        ("home", {}, False),
        ("away", {}, False),
        ("draw", {}, False),
        # Total — the YES contract is always "Over N.5"; "under" is the NO complement.
        ("over", {}, False),
        ("under", {}, True),
        # Spread — provider_meta carries yes_side; outcome != yes_side is NO.
        ("home", {"yes_side": "home"}, False),
        ("away", {"yes_side": "home"}, True),
        ("home", {"yes_side": "away"}, True),
        ("away", {"yes_side": "away"}, False),
        # Unknown outcome — default to YES (pre-fix behaviour, safe).
        ("", {}, False),
    ],
)
def test_bet_is_no_side(outcome, meta, expected):
    # Mirror _bet_ns: provider_meta kept as a dict AND its keys flattened.
    bet = SimpleNamespace(outcome=outcome, provider_meta=meta, **meta)
    assert _bet_is_no_side(bet) is expected


def test_check_live_price_inverts_no_side(monkeypatch):
    """A total "under" bet must be re-priced off (100 - yes_ask), not yes_ask."""

    # Stub the API: one "Over 1.5" YES contract trading at yes_ask = 73c.
    async def fake_get(page, path):
        return {"events": [{"markets": [{"ticker_name": "KXT-1", "yes_ask": 73}]}]}

    monkeypatch.setattr(kalshi, "_api_get", fake_get)
    kalshi._pending["event_ticker"] = "KXT"
    kalshi._pending["market_ticker"] = "KXT-1"

    # UNDER (NO side) — odds come off the 27c complement.
    under = SimpleNamespace(outcome="under", provider_meta={}, fair_odds=3.5)
    odds, edge = asyncio.run(_check_live_price(None, under, None))
    assert odds == round(100.0 / 27, 4)

    # OVER (YES side) — odds come off the 73c yes_ask, unchanged.
    over = SimpleNamespace(outcome="over", provider_meta={}, fair_odds=1.3)
    odds_o, _ = asyncio.run(_check_live_price(None, over, None))
    assert odds_o == round(100.0 / 73, 4)
