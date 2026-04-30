"""Anchor stake cap — ArbRunner._sized_anchor must clamp to learned site caps."""

from __future__ import annotations

import math

from arnold.mirror.arb_runner import ArbRunner


def test_no_cap_uses_balance():
    assert ArbRunner._sized_anchor(200.0, None) == 200.0


def test_cap_lower_than_balance_clamps():
    assert ArbRunner._sized_anchor(200.0, 50.0) == 50.0


def test_balance_lower_than_cap_uses_balance():
    assert ArbRunner._sized_anchor(150.0, 500.0) == 150.0


def test_zero_cap_falls_back_to_balance():
    assert ArbRunner._sized_anchor(200.0, 0.0) == 200.0


def test_negative_cap_falls_back_to_balance():
    assert ArbRunner._sized_anchor(200.0, -5.0) == 200.0


def test_nan_cap_falls_back_to_balance():
    assert ArbRunner._sized_anchor(200.0, math.nan) == 200.0


def test_zero_balance_returns_zero():
    assert ArbRunner._sized_anchor(0.0, 50.0) == 0.0
