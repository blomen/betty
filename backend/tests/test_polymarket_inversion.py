"""Polymarket odds-inversion correction on store.

Regression test for the Knicks/Cavaliers same-side "arb" bug: when Polymarket's
home/away normalization lands a moneyline market's odds on the wrong team, the
arb scanner pairs the same physical team on both legs. Soft books are corrected
by detect_and_fix_inversion(); Polymarket previously only logged the mismatch.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core import StandardEvent
from src.db.models import Base, Event, Odds
from src.matching.normalizer import generate_canonical_id
from src.pipeline.storage import store_polymarket_event

START = datetime(2026, 5, 22, 23, 0, 0)
START_ISO = "2026-05-22T23:00:00"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def _seed_canonical(session, pinn_home_odds, pinn_away_odds):
    """Canonical event (home=Knicks) + Pinnacle moneyline odds. Returns its id."""
    canonical_id = generate_canonical_id("basketball", "Knicks", "Cavaliers", START)
    session.add(
        Event(
            id=canonical_id,
            sport="basketball",
            league="NBA",
            home_team="Knicks",
            away_team="Cavaliers",
            display_home="New York Knicks",
            display_away="Cleveland Cavaliers",
            start_time=START,
        )
    )
    session.add(
        Odds(event_id=canonical_id, provider_id="pinnacle", market="moneyline", outcome="home", odds=pinn_home_odds)
    )
    session.add(
        Odds(event_id=canonical_id, provider_id="pinnacle", market="moneyline", outcome="away", odds=pinn_away_odds)
    )
    session.commit()
    return canonical_id


def _poly_event(knicks_odds, cavaliers_odds):
    """Polymarket lists the game as 'Cavaliers vs Knicks' (teams reversed vs
    canonical), moneyline priced with the given per-team odds."""
    return StandardEvent(
        id="",
        name="Cavaliers vs Knicks",
        sport="basketball",
        markets=[
            {
                "type": "moneyline",
                "outcomes": [
                    {"name": "Knicks", "odds": knicks_odds},
                    {"name": "Cavaliers", "odds": cavaliers_odds},
                ],
            }
        ],
        provider="polymarket",
        start_time=START_ISO,
        home_team="Cavaliers",
        away_team="Knicks",
        league="NBA",
    )


def _poly_ml(session, canonical_id):
    return {
        o.outcome: o.odds
        for o in session.query(Odds).filter_by(event_id=canonical_id, provider_id="polymarket", market="moneyline")
    }


def test_polymarket_inverted_odds_are_corrected(session):
    """Polymarket odds whose favorite disagrees with Pinnacle's clear favorite
    are swapped to canonical home/away on store."""
    # Pinnacle: Knicks (home) clear favorite at 1.45.
    canonical_id = _seed_canonical(session, pinn_home_odds=1.45, pinn_away_odds=2.9)
    # Polymarket arrives with the favorite's price on the WRONG team —
    # Knicks priced 2.9, Cavaliers 1.45 (inverted vs Pinnacle).
    store_polymarket_event(
        session,
        _poly_event(knicks_odds=2.9, cavaliers_odds=1.45),
        "basketball",
        event_cache={},
        sharp_odds_cache={},
    )
    session.commit()

    poly = _poly_ml(session, canonical_id)
    # After correction, polymarket's home (Knicks) carries the favorite price.
    assert poly["home"] == 1.45
    assert poly["away"] == 2.9


def test_polymarket_aligned_odds_unchanged(session):
    """Control: polymarket odds that already agree with Pinnacle's favorite are
    stored as-is — the inversion guard must not over-correct."""
    canonical_id = _seed_canonical(session, pinn_home_odds=1.45, pinn_away_odds=2.9)
    # Polymarket agrees: Knicks favored at 1.5, Cavaliers 2.7.
    store_polymarket_event(
        session,
        _poly_event(knicks_odds=1.5, cavaliers_odds=2.7),
        "basketball",
        event_cache={},
        sharp_odds_cache={},
    )
    session.commit()

    poly = _poly_ml(session, canonical_id)
    assert poly["home"] == 1.5
    assert poly["away"] == 2.7
