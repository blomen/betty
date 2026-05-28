"""When Pinnacle has BOTH home and away outcomes in a spread market_key
(the normal case under line-keying), a soft provider with only ONE
outcome in that key is structurally suspicious — they're either a
DOM-scrape that missed the other side (comeon Cerro game 2026-05-27),
a stale orphan from a previous main-line shift (unibet Orgryte/Bahia),
or a 3-way handicap leg that lost its complement.

In all three cases, devigging the full Pinnacle 2-way market against a
single soft leg produces phantom 30%+ edges. Require matching outcome
counts (sharp == soft) for spread markets; Polymarket's binary
exemption stays.

Regression test for the 2026-05-27 post-deploy residual: 161 30%+
value opps where the soft provider had only one outcome on a Pinnacle
2-outcome line.
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


def test_drops_soft_with_single_outcome_in_two_outcome_spread_key():
    """Pinnacle has both sides of spread_+0.5; comeon only has the home
    side (DOM scrape missed away). Devigging full Pinnacle against the
    lone comeon leg produces a phantom 31% edge — must be suppressed."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            # Pinnacle home@+0.5 + away@-0.5 = the +0.5 line, both sides.
            _odds("pinnacle", "spread", "home", 1.25, 0.5),
            _odds("pinnacle", "spread", "away", 3.90, -0.5),
            # comeon offers only the home side of this line at 1.73.
            _odds("comeon", "spread", "home", 1.73, 0.5),
        ]
    )
    values = scanner.scan_value(events=[ev])
    soft_values = [v for v in values if v.provider == "comeon" and v.market.startswith("spread")]
    assert not soft_values, (
        "comeon with only one outcome in a Pinnacle 2-outcome spread "
        f"key must be dropped, not compared against full devig: {soft_values}"
    )


def test_keeps_soft_when_both_sides_present():
    """Control: if the soft provider has both home and away on the same
    line, the prob-sum gate validates them — single-outcome restriction
    must not kick in."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "spread", "home", 1.91, -0.5),
            _odds("pinnacle", "spread", "away", 1.94, 0.5),
            # Soft normal vig: 1/2.10 + 1/1.85 = 0.476 + 0.541 = 1.017 — ok
            _odds("comeon", "spread", "home", 2.10, -0.5),
            _odds("comeon", "spread", "away", 1.85, 0.5),
        ]
    )
    grouped = scanner.group_odds(ev)
    spread_keys = [k for k in grouped if k.startswith("spread")]
    found_comeon = False
    for k in spread_keys:
        for outcome_entries in grouped[k].values():
            if any(e["provider"] == "comeon" for e in outcome_entries):
                found_comeon = True
                break
    assert found_comeon, "comeon with both sides should survive group_odds"


def test_polymarket_binary_market_still_allowed():
    """Polymarket has its own exemption for binary markets — single-side
    quoting is the design, not a bug."""
    scanner = OpportunityScanner(session=None)
    ev = _event(
        [
            _odds("pinnacle", "spread", "home", 1.91, -0.5),
            _odds("pinnacle", "spread", "away", 1.94, 0.5),
            # Polymarket binary contract: only one side of a line.
            _odds("polymarket", "spread", "home", 2.00, -0.5),
        ]
    )
    # We don't assert a value bet emerges (depends on edge), but the
    # provider must not be unconditionally dropped at the count check.
    grouped = scanner.group_odds(ev)
    polymarket_present = any(
        any("polymarket" in {e["provider"] for e in entries} for entries in grouped[k].values())
        for k in grouped
        if k.startswith("spread")
    )
    assert polymarket_present, "Polymarket binary spread must survive (it has its own exemption)"
