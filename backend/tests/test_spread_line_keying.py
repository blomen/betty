"""Spread markets must be grouped by LINE (home handicap), not raw per-outcome point.

Regression test for the Polymarket cross-handicap false-edge bug: Polymarket offers
alternate spread lines for BOTH teams (e.g. "Spread: Spurs (-1.5)" AND
"Spread: Thunder (-3.5)"). The old `group_odds` keyed every odds row by its own
signed point, so a key like `spread_-3.5` aggregated outcomes from two physically
different lines — Pinnacle's `home -3.5` (Spurs -3.5) and Polymarket's `away -3.5`
(Thunder -3.5 = the Spurs +3.5 line). The scanner then value-compared them across
handicaps and produced a fake edge.

A spread "line" is identified by the home-team handicap: `home@P` belongs to line P,
`away@P` belongs to line -P. Keying by line keeps both sides of one physical line
together and never mixes two lines under one key.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analysis.scanner import OpportunityScanner
from src.db.models import Base, Event, Odds

NOW = datetime.now(timezone.utc)


@pytest.fixture
def scanner():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield OpportunityScanner(s)
    s.close()
    engine.dispose()


def _odds(provider, market, outcome, odds, point=None):
    return Odds(
        provider_id=provider,
        market=market,
        outcome=outcome,
        odds=odds,
        point=point,
        updated_at=NOW,
    )


def _event(event_id, odds_rows):
    ev = Event(id=event_id, sport="basketball", home_team="spurs", away_team="thunder", start_time=NOW)
    ev.odds = odds_rows
    return ev


def _all_value_bets(scanner, event):
    grouped = scanner.group_odds(event, check_staleness=False)
    bets = []
    for market_key, odds_by_outcome in grouped.items():
        bets.extend(
            scanner.find_value_in_market(
                event_id=event.id,
                market=market_key,
                odds_by_outcome=odds_by_outcome,
                min_edge_pct=2.0,
                all_markets=grouped,
            )
        )
    return bets


def test_group_odds_keys_spread_by_line():
    """home@P → line P; away@P → line -P. Polymarket's `away -3.5` (Thunder -3.5)
    is the home+3.5 line and must NOT land in the same key as Pinnacle's `home -3.5`."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    scanner = OpportunityScanner(s)

    event = _event(
        "basketball:spurs:thunder:20260523",
        [
            # Polymarket "Spread: Thunder (-3.5)" — home(Spurs)+3.5, away(Thunder)-3.5.
            _odds("polymarket", "spread", "home", 1.58, point=3.5),
            _odds("polymarket", "spread", "away", 2.60, point=-3.5),
            # Pinnacle's Spurs-favored line: home(Spurs)-3.5, away(Thunder)+3.5.
            _odds("pinnacle", "spread", "home", 2.10, point=-3.5),
            _odds("pinnacle", "spread", "away", 1.80, point=3.5),
        ],
    )
    grouped = scanner.group_odds(event, check_staleness=False)
    s.close()
    engine.dispose()

    # Pinnacle's line is -3.5 (home handicap); Polymarket's is +3.5. Different keys.
    pinn_key = "spread_-3.5"
    poly_key = "spread_3.5"
    assert pinn_key in grouped and poly_key in grouped

    # Pinnacle's two outcomes are the SAME line -> same key.
    pinn_providers = {e["provider"] for o in grouped[pinn_key].values() for e in o}
    poly_providers = {e["provider"] for o in grouped[poly_key].values() for e in o}
    assert pinn_providers == {"pinnacle"}, f"line -3.5 should be Pinnacle-only, got {pinn_providers}"
    assert poly_providers == {"polymarket"}, f"line +3.5 should be Polymarket-only, got {poly_providers}"


def test_no_false_value_across_opposite_favorite_lines(scanner):
    """Pinnacle prices a Spurs-favored line; Polymarket prices the Thunder-favored
    line at the same |handicap|. They are different bets — no value bet may pair them."""
    event = _event(
        "basketball:spurs:thunder:20260523",
        [
            # Polymarket "Thunder -3.5" line (Spurs are the +3.5 underdog here).
            _odds("polymarket", "spread", "home", 1.58, point=3.5),
            _odds("polymarket", "spread", "away", 2.60, point=-3.5),
            # Pinnacle "Spurs -3.5" line only.
            _odds("pinnacle", "spread", "home", 2.10, point=-3.5),
            _odds("pinnacle", "spread", "away", 1.80, point=3.5),
        ],
    )
    poly_bets = [vb for vb in _all_value_bets(scanner, event) if vb.provider == "polymarket"]
    assert poly_bets == [], (
        "Polymarket's Thunder-favored line was value-compared against Pinnacle's "
        f"Spurs-favored line — false edges: {[(vb.market, vb.outcome, vb.edge_pct) for vb in poly_bets]}"
    )


def test_same_line_value_bet_still_detected(scanner):
    """Control: when Polymarket and Pinnacle price the SAME line, a genuine value
    bet is still found — the line-keying fix must not suppress real opportunities."""
    event = _event(
        "basketball:spurs:thunder:20260523",
        [
            # Both books price the Spurs -1.5 line; Polymarket's home side is generous.
            _odds("pinnacle", "spread", "home", 1.85, point=-1.5),
            _odds("pinnacle", "spread", "away", 2.05, point=1.5),
            _odds("polymarket", "spread", "home", 2.00, point=-1.5),
            _odds("polymarket", "spread", "away", 1.95, point=1.5),
        ],
    )
    poly_bets = [vb for vb in _all_value_bets(scanner, event) if vb.provider == "polymarket"]
    home_bets = [vb for vb in poly_bets if vb.outcome == "home"]
    assert home_bets, "expected a genuine Polymarket value bet on the shared Spurs -1.5 line"
    vb = home_bets[0]
    assert vb.provider_odds == 2.00, f"value bet must carry Polymarket's real home -1.5 odds, got {vb.provider_odds}"
    assert vb.edge_pct > 2.0
