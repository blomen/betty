"""Reverse-value scanner uses a tighter freshness gate on soft-book consensus
than the placement-staleness gate in group_odds.

Soft books that have not updated within ~3x their extraction cadence no longer
represent the market's current view, so they must not be counted as evidence
of consensus drift away from Pinnacle's price.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.analysis.scanner import OpportunityScanner


def _odds(provider, market, outcome, value, *, age_minutes=0, point=None, scope="ft"):
    return SimpleNamespace(
        provider_id=provider,
        market=market,
        outcome=outcome,
        odds=value,
        point=point,
        scope=scope,
        updated_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
        bid=None,
        ask=None,
    )


def _event(odds_list):
    return SimpleNamespace(
        id="evt:rv1",
        sport="football",
        home_team="A",
        away_team="B",
        league="Test",
        start_time=None,
        home_away_validated=True,
        odds=odds_list,
    )


# Pinnacle quotes 5.0 / 1.25 (~80% favorite). Three independent soft platforms
# (altenar, kambi, gecko_obg) all quote the dog at ~4.0 — reverse-value reads
# Pinnacle dog @ 5.0 vs soft consensus fair ≈ 4.05 → ~+23% edge on home leg.
def _make_event(home_ages: dict[str, int]):
    """Build a fresh-Pinnacle / variable-age-soft event.

    home_ages keys provider, value is row age in minutes (same age applied to
    that provider's away leg).
    """
    odds = [
        _odds("pinnacle", "moneyline", "home", 5.0),
        _odds("pinnacle", "moneyline", "away", 1.25),
    ]
    # One provider per platform — keeps platform count = 3 when all fresh.
    for pid, age in home_ages.items():
        odds.append(_odds(pid, "moneyline", "home", 4.0, age_minutes=age))
        odds.append(_odds(pid, "moneyline", "away", 1.30, age_minutes=age))
    return _event(odds)


def test_reverse_value_emits_when_all_soft_fresh():
    """Baseline: three fresh soft platforms → reverse_value bet surfaces."""
    scanner = OpportunityScanner(session=None)
    # betinia (altenar, 3min cadence), unibet (kambi, 3min), spelklubben (gecko_obg, 3min)
    ev = _make_event({"betinia": 1, "unibet": 1, "spelklubben": 1})
    values = scanner.scan_reverse_value(min_edge_pct=2.0, events=[ev])
    rv = [v for v in values if v.market == "moneyline" and v.outcome == "home"]
    assert rv, "expected one reverse_value bet on home (longshot) leg"
    assert rv[0].provider == "pinnacle"
    assert rv[0].edge_pct > 15


def test_reverse_value_drops_stale_soft_consensus():
    """Three soft platforms but all stale beyond consensus cutoff →
    consensus fails MIN_CONSENSUS_PLATFORMS and the bet is suppressed.

    api_soft cadence is 3 min, consensus_staleness_minutes_for floors at 15,
    so any soft row older than 15 min is dropped from the consensus input.
    """
    scanner = OpportunityScanner(session=None)
    ev = _make_event({"betinia": 30, "unibet": 30, "spelklubben": 30})
    values = scanner.scan_reverse_value(min_edge_pct=2.0, events=[ev])
    rv = [v for v in values if v.market == "moneyline" and v.outcome == "home"]
    assert not rv, "stale soft consensus must not emit reverse_value"


def test_reverse_value_drops_when_only_two_soft_platforms_fresh():
    """Mix: two platforms fresh, one stale → drops below MIN_CONSENSUS_PLATFORMS=3
    and the bet is suppressed even though some current consensus exists."""
    scanner = OpportunityScanner(session=None)
    ev = _make_event({"betinia": 1, "unibet": 1, "spelklubben": 30})
    values = scanner.scan_reverse_value(min_edge_pct=2.0, events=[ev])
    rv = [v for v in values if v.market == "moneyline" and v.outcome == "home"]
    assert not rv, "fewer than 3 fresh platforms must not emit"


def test_prediction_markets_contribute_to_consensus():
    """Polymarket and Kalshi (prediction markets) are now included in the
    consensus alongside soft books. Previously excluded as "non-sportsbook"
    pricing, but for the reverse-value question — where does the broader
    non-Pinnacle market sit? — sharper inputs only strengthen the call.

    Setup: only 2 soft books (would fail MIN_CONSENSUS_PLATFORMS=3) +
    polymarket. After the fix polymarket counts → 3 platforms → bet emits.
    """
    scanner = OpportunityScanner(session=None)
    odds = [
        _odds("pinnacle", "moneyline", "home", 5.0),
        _odds("pinnacle", "moneyline", "away", 1.25),
        _odds("betinia", "moneyline", "home", 4.0),
        _odds("betinia", "moneyline", "away", 1.30),
        _odds("unibet", "moneyline", "home", 4.0),
        _odds("unibet", "moneyline", "away", 1.30),
        # Polymarket — third platform via prediction-market inclusion.
        _odds("polymarket", "moneyline", "home", 4.0),
        _odds("polymarket", "moneyline", "away", 1.30),
    ]
    ev = _event(odds)
    values = scanner.scan_reverse_value(min_edge_pct=2.0, events=[ev])
    rv = [v for v in values if v.market == "moneyline" and v.outcome == "home"]
    assert rv, "polymarket should count as a consensus platform"
    # n_platforms recorded in prob_sum field — 3 = altenar + kambi + polymarket
    assert rv[0].prob_sum >= 3


def test_outlier_provider_dropped_from_consensus():
    """A provider whose price is >30% off the median (e.g. unibet hockey
    total returning over/under-swapped odds) is excluded from consensus.

    Without this filter, the unibet 1.40 vs the rest at 2.10-2.35 averaged
    into "fair 1.92", inflating Pinnacle's 2.22 to a +15.65% phantom edge.
    With the filter, unibet drops out → consensus tightens around the real
    median → edge collapses below threshold.
    """
    scanner = OpportunityScanner(session=None)
    odds_dict = {
        "under": [
            {"provider": "pinnacle", "odds": 2.22, "updated_at": datetime.now(UTC)},
            {"provider": "coolbet", "odds": 2.16, "updated_at": datetime.now(UTC)},
            {"provider": "polymarket", "odds": 2.35, "updated_at": datetime.now(UTC)},
            {"provider": "tipwin", "odds": 2.10, "updated_at": datetime.now(UTC)},
            {"provider": "unibet", "odds": 1.40, "updated_at": datetime.now(UTC)},
        ],
        "over": [
            {"provider": "pinnacle", "odds": 1.70, "updated_at": datetime.now(UTC)},
            {"provider": "coolbet", "odds": 1.70, "updated_at": datetime.now(UTC)},
            {"provider": "polymarket", "odds": 1.68, "updated_at": datetime.now(UTC)},
            {"provider": "tipwin", "odds": 1.65, "updated_at": datetime.now(UTC)},
            {"provider": "unibet", "odds": 3.00, "updated_at": datetime.now(UTC)},
        ],
    }
    filtered = scanner._drop_consensus_outliers(odds_dict)
    under_providers = {e["provider"] for e in filtered.get("under", [])}
    over_providers = {e["provider"] for e in filtered.get("over", [])}
    # Unibet was outlier on BOTH outcomes — dropped from both
    assert "unibet" not in under_providers
    assert "unibet" not in over_providers
    # Everyone else preserved
    assert {"pinnacle", "coolbet", "polymarket", "tipwin"} <= under_providers


def test_outlier_filter_keeps_provider_within_threshold():
    """A provider whose price is within 30% of median is kept, even if
    it's the highest or lowest in the group. The filter is for catching
    extraction errors, not soft-book micro-noise around a real market."""
    scanner = OpportunityScanner(session=None)
    odds_dict = {
        "home": [
            {"provider": "pinnacle", "odds": 2.00, "updated_at": datetime.now(UTC)},
            {"provider": "betinia", "odds": 1.80, "updated_at": datetime.now(UTC)},
            {"provider": "unibet", "odds": 2.20, "updated_at": datetime.now(UTC)},  # +22% off median 1.80
            {"provider": "comeon", "odds": 1.85, "updated_at": datetime.now(UTC)},
        ],
    }
    filtered = scanner._drop_consensus_outliers(odds_dict)
    providers = {e["provider"] for e in filtered.get("home", [])}
    assert providers == {"pinnacle", "betinia", "unibet", "comeon"}


def test_outlier_filter_skipped_with_too_few_providers():
    """Outlier detection needs 3+ non-sharp providers to compute a stable
    median. With only 2, even a large gap doesn't justify dropping either
    one — they're a 50/50 disagreement, not an outlier."""
    scanner = OpportunityScanner(session=None)
    odds_dict = {
        "home": [
            {"provider": "pinnacle", "odds": 2.00, "updated_at": datetime.now(UTC)},
            {"provider": "betinia", "odds": 1.50, "updated_at": datetime.now(UTC)},
            {"provider": "unibet", "odds": 2.50, "updated_at": datetime.now(UTC)},
        ],
    }
    # Only 2 non-sharp providers — skip outlier detection regardless of gap
    filtered = scanner._drop_consensus_outliers(odds_dict)
    providers = {e["provider"] for e in filtered.get("home", [])}
    assert providers == {"pinnacle", "betinia", "unibet"}


def test_pinnacle_keeps_full_window_in_consensus_filter():
    """Pinnacle row is the bet provider, not a consensus input. Its freshness
    is gated only by group_odds (the broader placement-staleness window), so
    it should remain in the filtered dict regardless of its age — the
    filter applies only to non-sharp providers."""
    scanner = OpportunityScanner(session=None)
    odds_dict = {
        "home": [
            {
                "provider": "pinnacle",
                "odds": 5.0,
                "updated_at": datetime.now(UTC) - timedelta(minutes=90),
            },
            {
                "provider": "betinia",
                "odds": 4.0,
                "updated_at": datetime.now(UTC) - timedelta(minutes=90),
            },
        ],
    }
    filtered = scanner._filter_to_consensus_fresh(odds_dict)
    providers = {e["provider"] for e in filtered.get("home", [])}
    assert "pinnacle" in providers, "Pinnacle must always pass the consensus filter"
    assert "betinia" not in providers, "stale soft provider must be dropped (90 min > 15 min floor)"
