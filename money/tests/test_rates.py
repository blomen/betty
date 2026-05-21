import pytest

from money import rates
from money.currency import Currency
from money.errors import RateNotConfigured


@pytest.fixture(autouse=True)
def _clean_rates():
    """Each test starts with no rate configured; clean up afterward."""
    rates.reset()
    yield
    rates.reset()


def test_convert_before_configure_raises():
    with pytest.raises(RateNotConfigured):
        rates.convert(100.0, Currency.USD, Currency.SEK)


def test_identity_conversion_needs_no_config():
    # frm is to — no rate lookup, works before configure().
    assert rates.convert(100.0, Currency.SEK, Currency.SEK) == 100.0
    assert rates.convert(100.0, Currency.USD, Currency.USD) == 100.0


def test_is_configured_reflects_state():
    assert rates.is_configured() is False
    rates.configure(10.5)
    assert rates.is_configured() is True


def test_convert_usd_to_sek():
    rates.configure(10.5)
    assert rates.convert(100.0, Currency.USD, Currency.SEK) == pytest.approx(1050.0)


def test_convert_sek_to_usd():
    rates.configure(10.5)
    assert rates.convert(1050.0, Currency.SEK, Currency.USD) == pytest.approx(100.0)


def test_configure_rejects_nonpositive_rate():
    with pytest.raises(ValueError):
        rates.configure(0.0)
    with pytest.raises(ValueError):
        rates.configure(-1.0)
