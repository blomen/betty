"""scanner.group_odds staleness gate.

Sharp providers (pinnacle) are always gated — a stale pinnacle row
corrupts every devig downstream.

Soft books are also gated at every market. Originally moneyline/1x2 was
exempt on the theory that "the user verifies live odds in the browser
before betting"; cloudbet 2026-05-28 disproved that: cloudbet's affiliate
API marked the FT moneyline SELECTION_DISABLED in the hours before
kickoff while the consumer site kept live prices. The 07:59 row stayed
in the DB for 13.5 h and kept pairing against fresh Pinnacle as a fake
+7% arb until the user opened the bet and noticed the mismatch.

Spread/total alt-line ladders have a second failure mode — when the
mainline drifts (e.g. total 10.5 → 11.5), upsert_odds doesn't DELETE
the old point, so the orphan row keeps pairing against Pinnacle's
permanent alt-line ladder as a phantom arb the user can't place
(the dropped point no longer appears at the bookmaker at all).

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
        max_stake=None,
    )


def _event(odds_list, sport="football"):
    return SimpleNamespace(id="evt:s1", sport=sport, odds=odds_list, home_away_validated=True)


def test_stale_soft_moneyline_row_dropped_in_group_odds():
    # 2 h is well beyond betinia's 18-min cadence-based window. Regression
    # for cloudbet 2026-05-28: affiliate API marked FT moneyline
    # SELECTION_DISABLED for hours before kickoff while consumer site kept
    # live prices, so the old row stayed in DB and surfaced a phantom +7%
    # arb until the user opened the bet.
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
    assert "betinia" not in providers, "stale soft moneyline leaked — surfaces phantom arbs against fresh Pinnacle"
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


def test_stale_soft_total_row_dropped_in_group_odds():
    # Phantom alt-line scenario (the Durango/Saraperos bug 2026-05-27):
    # Betinia's total mainline drifted 10.5 → 11.5; upsert_odds never deleted
    # the 10.5 row, so it kept pairing with Pinnacle's permanent 10.5 alt-line
    # ladder as a phantom arb the user couldn't place. Betinia interval is
    # 3 min → floor pushes window to 15 min, so a 30-min-old row is stale.
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=30)
    fresh = now - timedelta(seconds=30)

    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "total", "over", 1.66, updated_at=fresh, point=10.5),
            _odds("pinnacle", "total", "under", 2.23, updated_at=fresh, point=10.5),
            _odds("betinia", "total", "over", 1.92, updated_at=stale, point=10.5),
            _odds("betinia", "total", "under", 1.80, updated_at=stale, point=10.5),
            _odds("betinia", "total", "over", 1.90, updated_at=fresh, point=11.5),
            _odds("betinia", "total", "under", 1.75, updated_at=fresh, point=11.5),
        ],
        sport="baseball",
    )
    grouped = scanner.group_odds(ev, check_staleness=True)

    # Stale 10.5 betinia rows must be dropped; pinnacle 10.5 stays (it's fresh)
    bucket_10_5 = grouped.get("total_10.5", {})
    providers_10_5 = {e["provider"] for entries in bucket_10_5.values() for e in entries}
    assert "betinia" not in providers_10_5, "stale soft total row leaked into scanner — produces phantom arb"
    assert "pinnacle" in providers_10_5

    # Fresh 11.5 betinia rows survive
    bucket_11_5 = grouped.get("total_11.5", {})
    providers_11_5 = {e["provider"] for entries in bucket_11_5.values() for e in entries}
    assert "betinia" in providers_11_5


def test_stale_soft_spread_row_dropped_in_group_odds():
    # Same disease as totals — spread mainline drifts and the old point
    # orphans. Confirmed on Durango/Saraperos: betinia spread_-1.5 had a
    # 176-min-old home@2.10 + away@2.20 pair next to the current
    # -1.5/away + 1.5/home mainline, surfacing a second phantom arb.
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=60)
    fresh = now - timedelta(seconds=30)

    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "spread", "home", 1.85, updated_at=fresh, point=-1.5),
            _odds("pinnacle", "spread", "away", 1.95, updated_at=fresh, point=1.5),
            _odds("betinia", "spread", "home", 2.10, updated_at=stale, point=-1.5),
            _odds("betinia", "spread", "away", 1.60, updated_at=fresh, point=1.5),
        ],
        sport="baseball",
    )
    grouped = scanner.group_odds(ev, check_staleness=True)

    # Stale betinia home@-1.5 must be dropped; fresh betinia away@1.5 survives
    # (spread keyed by line — both legs collapse into spread_-1.5)
    bucket = grouped.get("spread_-1.5", {})
    home_providers = {e["provider"] for e in bucket.get("home", [])}
    away_providers = {e["provider"] for e in bucket.get("away", [])}
    assert "betinia" not in home_providers, "stale soft spread row leaked — produces phantom arb"
    assert "betinia" in away_providers


def test_fresh_soft_spread_total_row_kept():
    # Sanity: fresh soft spread/total rows pass through.
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)

    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("betinia", "total", "over", 1.90, updated_at=fresh, point=11.5),
            _odds("betinia", "spread", "away", 1.60, updated_at=fresh, point=1.5),
        ]
    )
    grouped = scanner.group_odds(ev, check_staleness=True)
    assert any(e["provider"] == "betinia" for e in grouped.get("total_11.5", {}).get("over", []))
    assert any(e["provider"] == "betinia" for e in grouped.get("spread_-1.5", {}).get("away", []))
