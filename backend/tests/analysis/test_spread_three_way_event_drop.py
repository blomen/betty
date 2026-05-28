"""Any provider that emits a `draw` outcome on ANY spread market_key of an
event must be dropped from ALL spread market_keys of that event.

The existing per-key drop at scanner.py:_fix_asian_spread_grouping only
removes the draw provider from the SAME key — but the scanner's sign-flip
keying (away_point → line = -point) sends the away leg into a DIFFERENT
key than home+draw. The away leg leaks past the per-key drop and surfaces
as phantom value (Tipwin 2026-05-26 bug, ~66 false opps).

Tipwin's extractor was patched separately to skip 3-way spreads, but this
event-level drop is the scanner-side defense for any future provider that
slips a 3-way handicap past extraction.
"""

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


def _event(odds_list, sport="football"):
    return SimpleNamespace(
        id="evt:t1",
        sport=sport,
        home_team="A",
        away_team="B",
        league="Test",
        start_time=None,
        home_away_validated=True,
        odds=odds_list,
    )


def test_event_level_three_way_drop_kills_away_in_opposite_key():
    """tipwin-style 3-way handicap: home@+1 and draw@+1 land in spread_+1,
    away@+1 lands in spread_-1 after sign flip. The away leg must also be
    dropped (event-level scope), not just the spread_+1 entries."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            # Pinnacle: standard 2-way Asian on the home-1 line.
            _odds("pinnacle", "spread", "home", 3.62, -1.0),
            _odds("pinnacle", "spread", "away", 1.27, 1.0),
            # Tipwin 3-way European: all three legs at point=+1.
            # Under scanner keying: home@+1 → spread_+1, draw@+1 → spread_+1,
            # away@+1 → spread_-1 (sign flipped).
            _odds("tipwin", "spread", "home", 4.95, 1.0),
            _odds("tipwin", "spread", "away", 1.45, 1.0),
            _odds("tipwin", "spread", "draw", 4.00, 1.0),
        ]
    )
    grouped = scanner.group_odds(ev)

    # tipwin must NOT appear in ANY surviving spread_* key.
    for key, by_outcome in grouped.items():
        if not key.startswith("spread"):
            continue
        for outcome_entries in by_outcome.values():
            providers = {e["provider"] for e in outcome_entries}
            assert "tipwin" not in providers, (
                f"tipwin's 3-way handicap leaked into {key}.{outcome_entries} — "
                "event-level drop must remove tipwin from EVERY spread key"
            )


def test_event_level_three_way_drop_keeps_pinnacle_two_way():
    """The drop is provider-scoped to the offending 3-way provider only."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "spread", "home", 3.62, -1.0),
            _odds("pinnacle", "spread", "away", 1.27, 1.0),
            _odds("tipwin", "spread", "home", 4.95, 1.0),
            _odds("tipwin", "spread", "draw", 4.00, 1.0),
        ]
    )
    grouped = scanner.group_odds(ev)
    spread_keys = [k for k in grouped if k.startswith("spread")]
    assert spread_keys, "Pinnacle's 2-way spread must remain after dropping tipwin"
    pinnacle_present = any(
        any("pinnacle" in {e["provider"] for e in entries} for entries in grouped[k].values()) for k in spread_keys
    )
    assert pinnacle_present, "Pinnacle's 2-way spread legs must survive event-level 3-way drop"
