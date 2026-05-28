"""Scanner refuses to emit value bets for spread buckets where soft and sharp
devigged probabilities disagree by >30pp on the same outcome."""

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


def _event(sport, odds_list, **kwargs):
    base = dict(
        id="evt:t1",
        sport=sport,
        home_team="A",
        away_team="B",
        league="Test",
        start_time=None,
        home_away_validated=True,
        odds=odds_list,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_spread_disagreement_drops_phantom_bucket():
    """Quarter-handicap convention mismatch: passes existing ratio filter but
    devigged probability disagrees by >30pp — gate must refuse the bucket.

    Pinnacle home@+0.5@1.24, away@-0.5@3.96 → devig P_home≈76%.
    Unibet home@+0.5@1.90, away@-0.5@1.62 → devig P_home≈46% (diff 30.1pp > 30pp).
    Odds ratio home: 1.90/1.24=1.532 < 1.55 (passes ratio filter).
    Without the gate, the phantom 44% edge on home would surface as a value bet.
    """
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "football",
        [
            # Pinnacle: home@+0.5@1.24, away@-0.5@3.96 → devig P_home≈76%
            _odds("pinnacle", "spread", "home", 1.24, 0.5, "ft"),
            _odds("pinnacle", "spread", "away", 3.96, -0.5, "ft"),
            # Unibet: home@+0.5@1.90, away@-0.5@1.62 → devig P_home≈46%
            # Passes ratio filter (1.532 < 1.55) but devig diff=30.1pp > 30pp threshold.
            _odds("unibet", "spread", "home", 1.90, 0.5, "ft"),
            _odds("unibet", "spread", "away", 1.62, -0.5, "ft"),
        ],
    )
    values = scanner.scan_value(events=[ev])
    spread_values = [v for v in values if v.market.startswith("spread")]
    assert not spread_values, f"expected zero value bets from disagreement bucket, got {spread_values}"


def test_spread_small_disagreement_emits_normally():
    """Small disagreement (4pp) is within 30pp tolerance — value bet emitted.

    Pinnacle home@1.60, away@2.40 → fair home≈1.667 (P_home≈0.60).
    Unibet home@1.76, away@2.20 → devigged P_home≈55.6% (diff ≈4.4pp vs Pinnacle).
    Edge on home ≈ 5.6% → should pass MIN_EDGE and the disagreement gate.
    """
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "football",
        [
            # Pinnacle: home@+0.5@1.60, away@-0.5@2.40 → fair home≈1.667
            _odds("pinnacle", "spread", "home", 1.60, 0.5, "ft"),
            _odds("pinnacle", "spread", "away", 2.40, -0.5, "ft"),
            # Unibet: home@+0.5@1.76, away@-0.5@2.20 → devig P_home≈55.6%
            # Disagreement vs Pinnacle: |0.556 - 0.600| ≈ 4.4pp — well within 30pp
            _odds("unibet", "spread", "home", 1.76, 0.5, "ft"),
            _odds("unibet", "spread", "away", 2.20, -0.5, "ft"),
        ],
    )
    values = scanner.scan_value(events=[ev])
    spread_values = [v for v in values if v.market.startswith("spread")]
    assert spread_values, "small disagreement (4.4pp) should still emit"


def test_total_market_not_affected_by_spread_gate():
    """The gate applies only to spread markets, not total.

    Pinnacle over@1.85, under@2.00 → fair over≈1.925.
    Unibet over@2.05 (edge ≈6.5%), under@2.10 (both sides present) — should emit.
    """
    scanner = OpportunityScanner(session=None)
    ev = _event(
        "football",
        [
            _odds("pinnacle", "total", "over", 1.85, 2.5, "ft"),
            _odds("pinnacle", "total", "under", 2.00, 2.5, "ft"),
            # Both sides required so scanner doesn't skip as market-type mismatch
            _odds("unibet", "total", "over", 2.05, 2.5, "ft"),
            _odds("unibet", "total", "under", 1.90, 2.5, "ft"),
        ],
    )
    values = scanner.scan_value(events=[ev])
    total_values = [v for v in values if v.market.startswith("total")]
    assert total_values, "totals must still emit (gate is spread-only)"
