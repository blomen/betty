import pytest

from money.currency import Currency


def test_currency_has_two_members():
    assert {c.name for c in Currency} == {"SEK", "USD"}


def test_parse_plain_codes():
    assert Currency.parse("SEK") is Currency.SEK
    assert Currency.parse("USD") is Currency.USD


def test_parse_usdc_normalizes_to_usd():
    assert Currency.parse("USDC") is Currency.USD


def test_parse_is_case_and_whitespace_insensitive():
    assert Currency.parse("  usd ") is Currency.USD
    assert Currency.parse("usdc") is Currency.USD


def test_parse_unknown_raises_valueerror():
    with pytest.raises(ValueError):
        Currency.parse("BTC")
    with pytest.raises(ValueError):
        Currency.parse("")


def test_str_is_the_code():
    assert str(Currency.SEK) == "SEK"
    assert str(Currency.USD) == "USD"
