"""Cloudbet bet-placement interception classification.

Cloudbet's placement endpoint (inferred POST /sports-betting/v4/bets) was
undiscovered. These tests pin the placement classifier so:
  - a bets POST/PUT is captured as a placement,
  - the positions READ (GET .../bets/positions) is never mistaken for one,
  - existing providers keep working and method-gated reads stay excluded.
"""

from local.mirror.browser import _is_bet_placement


def test_cloudbet_bets_post_is_placement():
    assert _is_bet_placement("https://www.cloudbet.com/sports-betting/v4/bets", "POST")


def test_cloudbet_positions_read_is_not_placement():
    # The positions GET is a history read (consumed by _HISTORY_KEYWORDS first).
    assert not _is_bet_placement(
        "https://www.cloudbet.com/sports-betting/v4/bets/positions?status=ACCEPTED",
        "GET",
    )


def test_placement_requires_post_or_put():
    assert not _is_bet_placement(
        "https://www.cloudbet.com/sports-betting/v4/bets", "GET"
    )
    assert _is_bet_placement("https://www.cloudbet.com/sports-betting/v4/bets", "PUT")


def test_existing_placement_keywords_still_match():
    assert _is_bet_placement("https://clob.polymarket.com/order", "POST")
    assert _is_bet_placement(
        "https://api.elections.kalshi.com/v1/users/abc/orders", "POST"
    )


def test_kalshi_resting_orders_read_is_not_placement():
    # Kalshi's /orders?status=resting GET read must not classify as a placement.
    assert not _is_bet_placement(
        "https://api.elections.kalshi.com/v1/users/abc/orders?status=resting",
        "GET",
    )
