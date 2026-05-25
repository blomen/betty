"""Batch builder refuses to surface value bets at edge > MAX_BATCH_VALUE_EDGE_PCT
or arbs at profit > MAX_BATCH_ARB_PROFIT_PCT."""

from __future__ import annotations


def test_constants_exposed():
    from src.services.batch_builder import MAX_BATCH_ARB_PROFIT_PCT, MAX_BATCH_VALUE_EDGE_PCT

    assert MAX_BATCH_VALUE_EDGE_PCT > 0
    assert MAX_BATCH_ARB_PROFIT_PCT > 0


def test_value_bet_above_cap_returns_none():
    from src.services.batch_builder import MAX_BATCH_VALUE_EDGE_PCT, _is_phantom_value_bet

    assert not _is_phantom_value_bet(edge_pct=MAX_BATCH_VALUE_EDGE_PCT - 0.1)
    assert not _is_phantom_value_bet(edge_pct=MAX_BATCH_VALUE_EDGE_PCT)
    assert _is_phantom_value_bet(edge_pct=MAX_BATCH_VALUE_EDGE_PCT + 0.01)
    assert _is_phantom_value_bet(edge_pct=45.0)


def test_arb_above_cap_returns_none():
    from src.services.batch_builder import MAX_BATCH_ARB_PROFIT_PCT, _is_phantom_arb

    assert not _is_phantom_arb(profit_pct=MAX_BATCH_ARB_PROFIT_PCT - 0.1)
    assert not _is_phantom_arb(profit_pct=MAX_BATCH_ARB_PROFIT_PCT)
    assert _is_phantom_arb(profit_pct=MAX_BATCH_ARB_PROFIT_PCT + 0.01)
    assert _is_phantom_arb(profit_pct=20.0)
