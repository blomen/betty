import pytest

from money.currency import Currency
from money.errors import CurrencyMismatch
from money.money import Money

# --- construction, zero, repr ---


def test_construct_holds_amount_and_currency():
    m = Money(242.0, Currency.SEK)
    assert m.amount == 242.0
    assert m.currency is Currency.SEK


def test_amount_is_coerced_to_float():
    m = Money(5, Currency.USD)
    assert isinstance(m.amount, float)
    assert m.amount == 5.0


def test_construct_rejects_non_currency():
    with pytest.raises(TypeError):
        Money(10.0, "SEK")


def test_zero_classmethod():
    z = Money.zero(Currency.USD)
    assert z.amount == 0.0
    assert z.currency is Currency.USD


def test_is_zero_and_bool():
    assert Money.zero(Currency.SEK).is_zero is True
    assert bool(Money.zero(Currency.SEK)) is False
    assert Money(0.01, Currency.SEK).is_zero is False
    assert bool(Money(0.01, Currency.SEK)) is True


def test_repr():
    assert repr(Money(242.0, Currency.SEK)) == "Money(242.00, SEK)"
    assert repr(Money(20.2, Currency.USD)) == "Money(20.20, USD)"


def test_money_is_frozen():
    from dataclasses import FrozenInstanceError

    m = Money(1.0, Currency.SEK)
    with pytest.raises(FrozenInstanceError):
        m.amount = 2.0  # type: ignore[misc]


# --- addition / subtraction ---


def test_add_same_currency():
    assert Money(100.0, Currency.SEK) + Money(42.0, Currency.SEK) == Money(142.0, Currency.SEK)


def test_sub_same_currency():
    assert Money(100.0, Currency.SEK) - Money(42.0, Currency.SEK) == Money(58.0, Currency.SEK)


def test_add_currency_mismatch_raises():
    with pytest.raises(CurrencyMismatch):
        Money(100.0, Currency.SEK) + Money(10.0, Currency.USD)


def test_sub_currency_mismatch_raises():
    with pytest.raises(CurrencyMismatch):
        Money(100.0, Currency.SEK) - Money(10.0, Currency.USD)


def test_add_non_money_raises_typeerror():
    with pytest.raises(TypeError):
        Money(100.0, Currency.SEK) + 5  # type: ignore[operator]


# --- scalar multiplication / division ---


def test_mul_by_scalar():
    assert Money(100.0, Currency.SEK) * 2 == Money(200.0, Currency.SEK)
    assert Money(100.0, Currency.SEK) * 0.5 == Money(50.0, Currency.SEK)


def test_rmul_by_scalar():
    assert 2 * Money(100.0, Currency.SEK) == Money(200.0, Currency.SEK)


def test_truediv_by_scalar():
    assert Money(420.0, Currency.SEK) / 2 == Money(210.0, Currency.SEK)


def test_mul_money_by_money_raises():
    with pytest.raises(TypeError):
        Money(2.0, Currency.SEK) * Money(2.0, Currency.SEK)  # type: ignore[operator]


def test_div_money_by_money_raises():
    with pytest.raises(TypeError):
        Money(2.0, Currency.SEK) / Money(2.0, Currency.SEK)  # type: ignore[operator]


# --- comparisons / equality ---


def test_ordering_same_currency():
    a = Money(10.0, Currency.SEK)
    b = Money(20.0, Currency.SEK)
    assert a < b
    assert a <= b
    assert b > a
    assert b >= a


def test_ordering_currency_mismatch_raises():
    a = Money(10.0, Currency.SEK)
    b = Money(20.0, Currency.USD)
    for op in ("__lt__", "__le__", "__gt__", "__ge__"):
        with pytest.raises(CurrencyMismatch):
            getattr(a, op)(b)


def test_eq_same_currency_and_amount():
    assert Money(10.0, Currency.SEK) == Money(10.0, Currency.SEK)


def test_eq_false_for_different_currency_never_raises():
    # == must NEVER raise, so Money is safe in dicts/sets.
    assert (Money(10.0, Currency.SEK) == Money(10.0, Currency.USD)) is False


def test_eq_false_for_non_money_never_raises():
    assert (Money(10.0, Currency.SEK) == 10.0) is False


def test_equal_money_have_equal_hash():
    assert hash(Money(10.0, Currency.SEK)) == hash(Money(10.0, Currency.SEK))
    # Usable as dict keys.
    d = {Money(10.0, Currency.SEK): "ten"}
    assert d[Money(10.0, Currency.SEK)] == "ten"


# --- convert / rounded ---


@pytest.fixture
def _rates_configured():
    from money import rates

    rates.reset()
    rates.configure(10.5)
    yield
    rates.reset()


def test_convert_changes_currency_and_amount(_rates_configured):
    sek = Money(1050.0, Currency.SEK)
    usd = sek.convert(Currency.USD)
    assert usd.currency is Currency.USD
    assert usd.amount == pytest.approx(100.0)


def test_convert_identity_returns_same_value(_rates_configured):
    m = Money(50.0, Currency.SEK)
    assert m.convert(Currency.SEK) == m


def test_convert_before_configure_raises():
    from money import rates
    from money.errors import RateNotConfigured

    rates.reset()
    with pytest.raises(RateNotConfigured):
        Money(100.0, Currency.USD).convert(Currency.SEK)


def test_rounded_to_two_places():
    assert Money(213.91037, Currency.USD).rounded() == Money(213.91, Currency.USD)
    assert Money(1.005, Currency.SEK).rounded(dp=2).currency is Currency.SEK


# --- public API ---


def test_public_api_is_importable_from_package_root():
    import money

    assert {
        "Money",
        "Currency",
        "CurrencyMismatch",
        "RateNotConfigured",
        "configure",
        "convert",
        "is_configured",
        "reset",
    }.issubset(set(money.__all__))
    # Smoke: the re-exported names are the real objects.
    assert money.Money is money.money.Money
    assert money.Currency is money.currency.Currency
