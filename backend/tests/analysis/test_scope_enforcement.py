"""Scanner.group_odds refuses to bucket cross-scope odds together."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.analysis.scanner import OpportunityScanner


def _odds(provider, market, outcome, value, point=None, scope="ft"):
    return SimpleNamespace(
        provider_id=provider, market=market, outcome=outcome, odds=value,
        point=point, scope=scope,
        updated_at=datetime.now(timezone.utc), bid=None, ask=None,
    )


def _event(sport, odds_list):
    return SimpleNamespace(id="evt:t1", sport=sport, odds=odds_list)


def test_canonical_scope_rows_grouped():
    scanner = OpportunityScanner(session=None)
    ev = _event("ice_hockey", [
        _odds("pinnacle", "total", "over", 1.85, 4.5, "ft"),
        _odds("betinia", "total", "under", 2.35, 4.5, "ft"),
    ])
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    assert "over" in bucket and "under" in bucket
    assert len(bucket["over"]) == 1
    assert len(bucket["under"]) == 1


def test_non_canonical_scope_rows_filtered_out():
    """Pinnacle 'reg' hockey + Betinia 'ft' hockey -> only the 'ft' row makes it through."""
    scanner = OpportunityScanner(session=None)
    ev = _event("ice_hockey", [
        _odds("pinnacle", "total", "over", 1.85, 4.5, "reg"),   # WRONG scope
        _odds("betinia", "total", "under", 2.35, 4.5, "ft"),    # canonical
    ])
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    # Pinnacle 'reg' row must NOT appear in the canonical-scope bucket.
    assert "over" not in bucket or len(bucket["over"]) == 0, \
        "scope='reg' Pinnacle row leaked into canonical bucket"
    assert "under" in bucket
    assert len(bucket["under"]) == 1


def test_iihf_worlds_false_arb_no_longer_groups():
    """The exact 2026-05-25 Slovenia v Italy bug: no cross-scope grouping."""
    scanner = OpportunityScanner(session=None)
    ev = _event("ice_hockey", [
        _odds("pinnacle", "total", "over", 1.85, 4.5, "reg"),
        _odds("pinnacle", "total", "under", 2.00, 4.5, "reg"),
        _odds("betinia", "total", "over", 1.6061, 4.5, "ft"),
        _odds("betinia", "total", "under", 2.35, 4.5, "ft"),
    ])
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    # Only Betinia 'ft' rows should remain -- no Pinnacle 'reg' rows.
    providers = {row["provider"] for outcome_rows in bucket.values() for row in outcome_rows}
    assert "pinnacle" not in providers, \
        "Pinnacle 'reg' row reached the canonical-scope bucket -- false arb possible"
    assert "betinia" in providers
