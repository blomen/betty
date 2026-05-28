"""Money(amount, currency) value type — typed money refuses to silently cross
currencies. This is the single missing abstraction behind every cross-currency
bug fixed in the May 2026 review.
"""

from __future__ import annotations

import pytest

from local.mirror.currency import _FX_SEK_PER_UNIT, Money


# --- construction + equality ------------------------------------------------


def test_money_construction():
    m = Money(100.0, "SEK")
    assert m.amount == 100.0
    assert m.currency == "SEK"


def test_money_equality_same_currency():
    assert Money(100.0, "SEK") == Money(100.0, "SEK")


def test_money_equality_different_amount():
    assert Money(100.0, "SEK") != Money(101.0, "SEK")


def test_money_equality_different_currency():
    assert Money(100.0, "SEK") != Money(100.0, "USDC")


def test_money_is_hashable():
    # frozen dataclass → hashable → usable in sets / dict keys
    s = {Money(100.0, "SEK"), Money(100.0, "SEK"), Money(50.0, "USDC")}
    assert len(s) == 2


def test_money_str_format():
    assert str(Money(100.0, "SEK")) == "100.00 SEK"
    assert str(Money(99.99, "USDC")) == "99.99 USDC"


# --- arithmetic refuses cross-currency --------------------------------------


def test_money_add_same_currency_ok():
    assert Money(100.0, "SEK") + Money(50.0, "SEK") == Money(150.0, "SEK")


def test_money_add_different_currency_raises():
    with pytest.raises(ValueError, match="cannot add SEK and USDC"):
        _ = Money(100.0, "SEK") + Money(50.0, "USDC")


def test_money_sub_same_currency_ok():
    assert Money(100.0, "SEK") - Money(40.0, "SEK") == Money(60.0, "SEK")


def test_money_sub_different_currency_raises():
    with pytest.raises(ValueError, match="cannot subtract"):
        _ = Money(100.0, "SEK") - Money(50.0, "USDC")


def test_money_sum_works_with_zero_start():
    # Python's sum() starts from 0; __radd__ supports Money + 0
    total = sum([Money(10.0, "SEK"), Money(20.0, "SEK"), Money(30.0, "SEK")])
    assert total == Money(60.0, "SEK")


def test_money_sum_refuses_mixed_currencies():
    with pytest.raises(ValueError):
        sum([Money(10.0, "SEK"), Money(20.0, "USDC")])


# --- scalar arithmetic preserves currency -----------------------------------


def test_money_mul_scalar():
    assert Money(100.0, "SEK") * 2 == Money(200.0, "SEK")
    assert Money(100.0, "SEK") * 1.5 == Money(150.0, "SEK")


def test_money_rmul_scalar():
    # Allow `2 * Money(...)` as well as `Money(...) * 2`
    assert 2 * Money(100.0, "SEK") == Money(200.0, "SEK")


def test_money_mul_money_raises():
    with pytest.raises(TypeError, match="meaningless dimensions"):
        _ = Money(100.0, "SEK") * Money(2.0, "SEK")


def test_money_truediv_scalar():
    assert Money(100.0, "SEK") / 2 == Money(50.0, "SEK")
    assert Money(100.0, "SEK") / 2.5 == Money(40.0, "SEK")


def test_money_truediv_money_raises():
    with pytest.raises(TypeError, match="returns a ratio"):
        _ = Money(100.0, "SEK") / Money(2.0, "SEK")


def test_money_negate():
    assert -Money(100.0, "SEK") == Money(-100.0, "SEK")


# --- conversion -------------------------------------------------------------


def test_money_to_same_currency_returns_self():
    sek = Money(100.0, "SEK")
    assert sek.to("SEK") == sek


def test_money_to_sek_from_usdc():
    rate = _FX_SEK_PER_UNIT["USDC"]
    assert Money(100.0, "USDC").to("SEK") == Money(100.0 * rate, "SEK")


def test_money_to_usdc_from_sek():
    rate = _FX_SEK_PER_UNIT["USDC"]
    assert Money(1050.0, "SEK").to("USDC") == Money(1050.0 / rate, "USDC")


def test_money_to_unknown_currency_raises():
    with pytest.raises(ValueError, match="unknown currency"):
        _ = Money(100.0, "SEK").to("XYZ")


def test_money_to_roundtrip_preserves_within_rounding():
    rate = _FX_SEK_PER_UNIT["USDC"]
    sek = Money(100.0, "SEK")
    roundtrip = sek.to("USDC").to("SEK")
    # SEK → USDC → SEK; intermediate division by 10.5 then multiplied back —
    # exact in IEEE 754 for these values
    assert roundtrip == Money(100.0 * 1.0, "SEK")
    # And rate is real
    assert rate == 10.5


# --- comparison -------------------------------------------------------------


def test_money_lt_same_currency():
    assert Money(100.0, "SEK") < Money(101.0, "SEK")
    assert not (Money(101.0, "SEK") < Money(100.0, "SEK"))


def test_money_comparison_different_currency_returns_notimplemented():
    # Python falls back when both sides return NotImplemented → TypeError
    with pytest.raises(TypeError):
        _ = Money(100.0, "SEK") < Money(50.0, "USDC")


# --- round ------------------------------------------------------------------


def test_money_round_default_cents():
    assert Money(99.999, "SEK").round() == Money(100.0, "SEK")


def test_money_round_explicit_places():
    assert Money(3.14159, "SEK").round(2) == Money(3.14, "SEK")
    assert Money(3.14159, "SEK").round(0) == Money(3.0, "SEK")
