"""Verify each OpportunityRepo upsert also produces an opp_snapshots row."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Event, OppSnapshot, Provider
from src.repositories.opportunity_repo import OpportunityRepo


@pytest.fixture
def repo_setup(db_session):
    db_session.add_all(
        [
            Provider(id="pinnacle", name="Pinnacle"),
            Provider(id="unibet", name="Unibet"),
            Provider(id="betinia", name="Betinia"),
            Provider(id="consensus", name="Consensus"),
        ]
    )
    db_session.add(
        Event(
            id="evt-1",
            sport="soccer",
            home_team="A",
            away_team="B",
            start_time=datetime.now(UTC) + timedelta(hours=2),
        )
    )
    db_session.commit()
    return db_session, OpportunityRepo(db_session)


def test_upsert_value_creates_snapshot(repo_setup):
    db, repo = repo_setup
    is_new, opp = repo.upsert_value(
        event_id="evt-1",
        market="moneyline",
        outcome="A",
        provider_id="unibet",
        provider_odds=2.10,
        fair_odds=2.00,
        edge_pct=5.0,
        outcomes_json=[],
    )
    db.commit()

    assert is_new
    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.type == "value"
    assert s.provider1_id == "unibet"
    assert s.odds1_at_detection == 2.10
    assert s.fair_odds1_at_detection == 2.00


def test_upsert_value_redetection_does_not_duplicate_snapshot(repo_setup):
    db, repo = repo_setup
    for _ in range(3):
        repo.upsert_value(
            event_id="evt-1",
            market="moneyline",
            outcome="A",
            provider_id="unibet",
            provider_odds=2.10,
            fair_odds=2.00,
            edge_pct=5.0,
            outcomes_json=[],
        )
    db.commit()

    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    assert snaps[0].detection_count == 3


def test_upsert_arb_creates_snapshot_with_both_legs(repo_setup):
    db, repo = repo_setup
    legs = [
        {"outcome": "A", "provider": "unibet", "odds": 2.10, "edge_pct": 5.0, "fair_odds": 2.00, "stake_pct": 50.0},
        {"outcome": "B", "provider": "betinia", "odds": 2.05, "edge_pct": 3.0, "fair_odds": 1.99, "stake_pct": 50.0},
    ]
    is_new, opp = repo.upsert_arb(
        event_id="evt-1",
        market="moneyline",
        legs=legs,
        combined_edge_pct=4.0,
        guaranteed_profit_pct=1.5,
    )
    db.commit()

    assert is_new
    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.type == "arb"
    assert s.provider1_id == "unibet"
    assert s.provider2_id == "betinia"
    assert s.odds1_at_detection == 2.10
    assert s.odds2_at_detection == 2.05


def test_upsert_reverse_value_creates_snapshot(repo_setup):
    db, repo = repo_setup
    is_new, opp = repo.upsert_reverse_value(
        event_id="evt-1",
        market="moneyline",
        outcome="A",
        pinnacle_odds=5.50,
        consensus_fair_odds=5.00,
        edge_pct=10.0,
        outcomes_json=[],
    )
    db.commit()

    assert is_new
    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.type == "reverse_value"
    assert s.provider1_id == "pinnacle"
    assert s.odds1_at_detection == 5.50
    assert s.fair_odds1_at_detection == 5.00
