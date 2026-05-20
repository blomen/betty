"""Kalshi strategy — event-ticker derivation tests.

Regression coverage for the spread/total navigation bug: market tickers whose
outcome suffix contains digits (-3, -ALN1, -CAG2) must still strip down to the
2-segment event ticker the /v1/cached/events endpoint expects.
"""

from __future__ import annotations

import pytest

from arnold.mirror.workflows.strategies.kalshi import _event_ticker_from_market


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
