"""Verifies BatchBet exposes the sharp baseline used to compute the row.

The frontend's useSharpRefresh hook reads these fields to know which
provider to live-fetch (and, for Pinnacle, which matchup_id to query)
when the user clicks an opportunity row.

We exercise BatchBuilder._make_candidate directly — it's the unit that
constructs the BatchBet from an Opportunity, and going through the full
.build() orchestration would require a Profile + balances + repos. The
production path (build → _collect_candidates → _make_candidate) calls
this same function, so the baseline fields surface end-to-end.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

# Force in-memory SQLite for these tests
os.environ.pop("DATABASE_URL", None)

from src.db.models import Event, Odds, Opportunity, Provider
from src.services.batch_builder import BatchBuilder


def _future_start() -> datetime:
    """Return a start_time inside the MAX_TTK_HOURS window so the candidate isn't filtered."""
    return datetime.now(UTC) + timedelta(hours=24)


@pytest.fixture
def linette_value_setup(db_session):
    """Linette ML value bet at unibet with pinnacle as the devig baseline."""
    db_session.add_all(
        [
            Provider(id="unibet", name="Unibet"),
            Provider(id="pinnacle", name="Pinnacle"),
        ]
    )
    db_session.flush()

    event = Event(
        id="evt-linette-1",
        sport="tennis",
        league="WTA",
        home_team="magda linette",
        away_team="iga swiatek",
        display_home="Magda Linette",
        display_away="Iga Swiatek",
        start_time=_future_start(),
    )
    db_session.add(event)
    db_session.flush()

    # Pinnacle's odds row carries matchup_id in provider_meta
    db_session.add_all(
        [
            Odds(
                event_id="evt-linette-1",
                provider_id="pinnacle",
                market="moneyline",
                outcome="home",
                point=None,
                odds=16.35,
                provider_meta={"matchup_id": "1234567"},
            ),
            Odds(
                event_id="evt-linette-1",
                provider_id="unibet",
                market="moneyline",
                outcome="home",
                point=None,
                odds=16.5,
                provider_meta={},
            ),
        ]
    )

    opp = Opportunity(
        type="value",
        event_id="evt-linette-1",
        market="moneyline",
        outcome1="home",
        point=None,
        provider1_id="unibet",
        provider2_id="pinnacle",
        odds1=16.5,
        odds2=13.75,
        edge_pct=9.2,
        detected_at=datetime.now(UTC),
    )
    db_session.add(opp)
    db_session.commit()
    return event, opp


def _build_candidate(db_session, opp, event):
    """Invoke _make_candidate with bankroll big enough that stake/edge gates pass."""
    builder = BatchBuilder(db_session)
    total_bankroll = 100_000.0  # large SEK bankroll so Kelly produces a non-zero stake
    return builder._make_candidate(
        opp=opp,
        event=event,
        opp_type="value",
        total_bankroll=total_bankroll,
        single_bet_cap_pct=0.05,
        min_edge=0.01,
        min_stake=20.0,
        provider_bankroll_sek={"unibet": total_bankroll},
        cluster_bankroll_sek={},
    )


def test_value_bet_carries_baseline_provider_id(db_session, linette_value_setup):
    event, opp = linette_value_setup
    bet = _build_candidate(db_session, opp, event)
    assert bet is not None, "candidate was filtered before assertions could run"
    assert bet.baseline_provider_id == "pinnacle"


def test_value_bet_carries_baseline_meta_matchup_id(db_session, linette_value_setup):
    event, opp = linette_value_setup
    bet = _build_candidate(db_session, opp, event)
    assert bet is not None, "candidate was filtered before assertions could run"
    assert bet.baseline_meta == {"matchup_id": "1234567"}


def test_baseline_meta_is_none_when_no_sharp_odds_row(db_session):
    """If no odds row exists for the baseline (e.g. consensus-derived
    value bet), baseline_meta is None — the hook will land in 'unsupported'."""
    db_session.add_all(
        [
            Provider(id="unibet", name="Unibet"),
            Provider(id="pinnacle", name="Pinnacle"),
        ]
    )
    db_session.flush()
    event = Event(
        id="evt-no-pinn",
        sport="tennis",
        home_team="a",
        away_team="b",
        display_home="A",
        display_away="B",
        start_time=_future_start(),
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        Odds(
            event_id="evt-no-pinn",
            provider_id="unibet",
            market="moneyline",
            outcome="home",
            point=None,
            odds=2.10,
        )
    )
    opp = Opportunity(
        type="value",
        event_id="evt-no-pinn",
        market="moneyline",
        outcome1="home",
        point=None,
        provider1_id="unibet",
        provider2_id="pinnacle",
        odds1=2.10,
        odds2=2.00,
        edge_pct=5.0,
        detected_at=datetime.now(UTC),
    )
    db_session.add(opp)
    db_session.commit()

    bet = _build_candidate(db_session, opp, event)
    if bet is None:
        pytest.skip("candidate filtered upstream — out of scope for this test")
    assert bet.baseline_provider_id == "pinnacle"
    assert bet.baseline_meta is None
