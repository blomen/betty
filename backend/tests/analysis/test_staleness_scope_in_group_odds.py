"""scanner.group_odds applies staleness only to SHARP providers.

Soft books are placement targets — the user verifies live odds in the
browser before betting, so the scanner surfaces stale soft rows. Sharp
providers (pinnacle) are the fair-odds reference: a stale pinnacle row
would corrupt every devig downstream, so it stays gated.

Reverse-value's consensus calc has its own tighter gate
(consensus_staleness_minutes_for) that runs downstream of group_odds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.analysis.scanner import OpportunityScanner


def _odds(provider, market, outcome, value, *, updated_at, point=None, scope="ft"):
    return SimpleNamespace(
        provider_id=provider,
        market=market,
        outcome=outcome,
        odds=value,
        point=point,
        scope=scope,
        updated_at=updated_at,
        bid=None,
        ask=None,
    )


def _event(odds_list, sport="football"):
    return SimpleNamespace(id="evt:s1", sport=sport, odds=odds_list, home_away_validated=True)


def test_stale_soft_row_kept_in_group_odds():
    # 2 h is well beyond betinia's 18-min cadence-based window, but the
    # placement path no longer enforces that for soft books — the user
    # validates manually in the browser before placing.
    now = datetime.now(UTC)
    stale = now - timedelta(hours=2)
    fresh = now - timedelta(seconds=30)

    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "moneyline", "home", 1.90, updated_at=fresh),
            _odds("pinnacle", "moneyline", "away", 2.00, updated_at=fresh),
            _odds("betinia", "moneyline", "home", 2.20, updated_at=stale),
        ]
    )
    grouped = scanner.group_odds(ev, check_staleness=True)
    home = grouped.get("moneyline", {}).get("home", [])
    providers = {entry["provider"] for entry in home}
    assert "betinia" in providers, "stale soft row was filtered — placement gate should ignore soft staleness"
    assert "pinnacle" in providers


def test_stale_sharp_row_dropped_in_group_odds():
    # Pinnacle interval is 1 min; the floor pushes staleness to 15 min. A 20-min-old
    # pinnacle row corrupts the devig reference, so it must be dropped.
    now = datetime.now(UTC)
    stale_sharp = now - timedelta(minutes=20)
    fresh = now - timedelta(seconds=30)

    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "moneyline", "home", 1.90, updated_at=stale_sharp),
            _odds("betinia", "moneyline", "home", 2.20, updated_at=fresh),
        ]
    )
    grouped = scanner.group_odds(ev, check_staleness=True)
    home = grouped.get("moneyline", {}).get("home", [])
    providers = {entry["provider"] for entry in home}
    assert "pinnacle" not in providers, "stale sharp row should be dropped — corrupts fair-odds reference"
    assert "betinia" in providers


def test_fresh_sharp_row_kept():
    # Sanity: a fresh pinnacle row passes the gate.
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)

    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "moneyline", "home", 1.90, updated_at=fresh),
        ]
    )
    grouped = scanner.group_odds(ev, check_staleness=True)
    home = grouped.get("moneyline", {}).get("home", [])
    assert any(entry["provider"] == "pinnacle" for entry in home)
