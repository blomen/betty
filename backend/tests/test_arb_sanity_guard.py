"""Arb sanity guard — a guaranteed profit above MAX_PLAUSIBLE_ARB_PCT always
means a mispriced leg, never a real arbitrage. Regression for the fake
+45.05% "DUTCH ARB" surfaced as placeable on 2026-05-20."""

from src.analysis.scanner import MAX_PLAUSIBLE_ARB_PCT, _implausible_arb_profit


def test_normal_arb_is_plausible():
    assert _implausible_arb_profit(2.5, 1.8) is False
    assert _implausible_arb_profit(MAX_PLAUSIBLE_ARB_PCT, None) is False  # at the ceiling, allowed


def test_absurd_arb_is_rejected():
    # 45% guaranteed profit — the Buse/Humbert fake. Mispriced leg, never real.
    assert _implausible_arb_profit(45.05, None) is True
    assert _implausible_arb_profit(2.0, 45.05) is True
    assert _implausible_arb_profit(MAX_PLAUSIBLE_ARB_PCT + 0.01, None) is True


def test_none_profits_are_plausible():
    assert _implausible_arb_profit(None, None) is False
