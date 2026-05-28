"""Scanner.group_odds refuses to bucket cross-scope odds together."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from src.analysis.scanner import OpportunityScanner


def _odds(provider, market, outcome, value, point=None, scope="ft"):
    return SimpleNamespace(
        provider_id=provider,
        market=market,
        outcome=outcome,
        odds=value,
        point=point,
        scope=scope,
        updated_at=datetime.now(UTC),
        bid=None,
        ask=None,
        max_stake=None,
    )


def _event(sport, odds_list):
    return SimpleNamespace(id="evt:t1", sport=sport, odds=odds_list)


def test_canonical_scope_rows_grouped():
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "ice_hockey",
        [
            _odds("pinnacle", "total", "over", 1.85, 4.5, "ft"),
            _odds("betinia", "total", "under", 2.35, 4.5, "ft"),
        ],
    )
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    assert "over" in bucket and "under" in bucket
    assert len(bucket["over"]) == 1
    assert len(bucket["under"]) == 1


def test_non_canonical_scope_rows_filtered_out():
    """Pinnacle 'reg' hockey + Betinia 'ft' hockey -> only the 'ft' row makes it through."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "ice_hockey",
        [
            _odds("pinnacle", "total", "over", 1.85, 4.5, "reg"),  # WRONG scope
            _odds("betinia", "total", "under", 2.35, 4.5, "ft"),  # canonical
        ],
    )
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    # Pinnacle 'reg' row must NOT appear in the canonical-scope bucket.
    assert "over" not in bucket or len(bucket["over"]) == 0, "scope='reg' Pinnacle row leaked into canonical bucket"
    assert "under" in bucket
    assert len(bucket["under"]) == 1


def test_iihf_worlds_false_arb_no_longer_groups():
    """The exact 2026-05-25 Slovenia v Italy bug: no cross-scope grouping."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "ice_hockey",
        [
            _odds("pinnacle", "total", "over", 1.85, 4.5, "reg"),
            _odds("pinnacle", "total", "under", 2.00, 4.5, "reg"),
            _odds("betinia", "total", "over", 1.6061, 4.5, "ft"),
            _odds("betinia", "total", "under", 2.35, 4.5, "ft"),
        ],
    )
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    # Only Betinia 'ft' rows should remain -- no Pinnacle 'reg' rows.
    providers = {row["provider"] for outcome_rows in bucket.values() for row in outcome_rows}
    assert "pinnacle" not in providers, "Pinnacle 'reg' row reached the canonical-scope bucket -- false arb possible"
    assert "betinia" in providers


# ── Multi-scope per sport (PR 2.1 foundation) ──────────────────────────


def test_scannable_scopes_for_baseball_includes_f5():
    """SPORT_SCANNABLE_SCOPES['baseball'] = {ft, f5} — F5 is opt-in for MLB only."""
    from src.constants import scannable_scopes_for

    assert scannable_scopes_for("baseball") == frozenset({"ft", "f5"})


def test_scannable_scopes_for_default_is_canonical_only():
    """Sports without explicit period coverage scan canonical only — no behaviour change."""
    from src.constants import scannable_scopes_for

    assert scannable_scopes_for("football") == frozenset({"ft"})
    assert scannable_scopes_for("ice_hockey") == frozenset({"ft"})
    assert scannable_scopes_for(None) == frozenset({"ft"})
    assert scannable_scopes_for("unknown_sport") == frozenset({"ft"})


def test_group_odds_scope_parameter_filters_to_f5():
    """group_odds(scope='f5') returns ONLY f5 rows; ft rows are filtered out."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "baseball",
        [
            _odds("pinnacle", "total", "over", 1.85, 8.5, "ft"),
            _odds("pinnacle", "total", "over", 1.95, 4.5, "f5"),
            _odds("kambi_mlb", "total", "under", 2.10, 4.5, "f5"),
        ],
    )
    grouped = scanner.group_odds(ev, scope="f5", check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    # Only the two f5 rows should appear
    providers_over = {r["provider"] for r in bucket.get("over", [])}
    providers_under = {r["provider"] for r in bucket.get("under", [])}
    assert providers_over == {"pinnacle"}
    assert providers_under == {"kambi_mlb"}
    # The 8.5 ft total must not appear in this scope's grouping
    assert "total_8.5" not in grouped


def test_group_odds_default_scope_unchanged_for_baseball():
    """When scope is not passed, baseball still scans canonical ft only — backward compat."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "baseball",
        [
            _odds("pinnacle", "total", "over", 1.85, 8.5, "ft"),
            _odds("pinnacle", "total", "over", 1.95, 4.5, "f5"),
        ],
    )
    grouped = scanner.group_odds(ev, check_staleness=False)
    # No scope arg → canonical ft → only the 8.5 ft total appears
    assert "total_8.5" in grouped
    assert "total_4.5" not in grouped
