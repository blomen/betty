"""Unit tests for OppSnapshotService."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Event, Opportunity, Provider


@pytest.fixture
def basic_setup(db_session):
    """Seed an event + two providers so FK constraints succeed."""
    db_session.add_all(
        [
            Provider(id="pinnacle", name="Pinnacle"),
            Provider(id="unibet", name="Unibet"),
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
    return db_session


def _make_value_opp(provider="unibet", outcome="A", odds=2.10, fair=2.00, edge=5.0):
    return Opportunity(
        type="value",
        event_id="evt-1",
        market="moneyline",
        outcome1=outcome,
        provider1_id=provider,
        odds1=odds,
        provider2_id="pinnacle",
        odds2=fair,
        edge_pct=edge,
        scope="ft",
        is_active=True,
        detected_at=datetime.now(UTC),
    )


def test_first_sighting_inserts_snapshot_with_frozen_fields(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()

    snap = svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    assert snap.id is not None
    assert snap.odds1_at_detection == 2.10
    assert snap.fair_odds1_at_detection == 2.00
    assert snap.edge_pct_at_detection == 5.0
    assert snap.detection_count == 1
    assert snap.first_detected_at == snap.last_detected_at
    assert snap.time_to_start_minutes_at_detection is not None
    assert 115 < snap.time_to_start_minutes_at_detection < 125  # ~120 min


def test_redetection_bumps_count_and_last_seen_only(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp(odds=2.10, fair=2.00, edge=5.0)
    basic_setup.add(opp)
    basic_setup.flush()
    snap1 = svc.upsert_from_opportunity(opp)
    basic_setup.commit()
    original_first = snap1.first_detected_at
    original_odds = snap1.odds1_at_detection
    original_edge = snap1.edge_pct_at_detection

    # Re-detect — same opp, drifted odds (scanner saw it again, edge changed)
    opp.odds1 = 2.05
    opp.edge_pct = 2.5
    snap2 = svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    assert snap2.id == snap1.id  # same row
    assert snap2.detection_count == 2
    assert snap2.last_detected_at >= original_first  # >= because Windows clock can repeat within a tick
    assert snap2.first_detected_at == original_first  # frozen
    assert snap2.odds1_at_detection == original_odds  # frozen
    assert snap2.edge_pct_at_detection == original_edge  # frozen


def test_arb_opp_snapshots_both_legs(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    basic_setup.add(Provider(id="betinia", name="Betinia"))
    basic_setup.commit()

    arb = Opportunity(
        type="arb",
        event_id="evt-1",
        market="moneyline",
        outcome1="A",
        outcome2="B",
        provider1_id="unibet",
        provider2_id="betinia",
        odds1=2.10,
        odds2=2.05,
        edge_pct=1.5,
        scope="ft",
        is_active=True,
        detected_at=datetime.now(UTC),
    )
    basic_setup.add(arb)
    basic_setup.flush()

    snap = svc.upsert_from_opportunity(arb)
    basic_setup.commit()

    assert snap.type == "arb"
    assert snap.provider2_id == "betinia"
    assert snap.outcome2 == "B"
    assert snap.odds2_at_detection == 2.05


def test_value_opp_leg2_fields_are_null(basic_setup):
    """Value opps have leg2 NULL (spec: leg-2 is arb-only)."""
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    snap = svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    assert snap.provider2_id is None
    assert snap.outcome2 is None
    assert snap.odds2_at_detection is None


def test_reverse_value_uses_pinnacle_odds_as_leg1(basic_setup):
    """For reverse_value, leg1 IS Pinnacle (raw), benchmark IS consensus.
    fair_odds1_at_detection captures the consensus number."""
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    rv = Opportunity(
        type="reverse_value",
        event_id="evt-1",
        market="moneyline",
        outcome1="A",
        provider1_id="pinnacle",
        odds1=5.50,  # Pinnacle's raw price
        provider2_id="consensus",
        odds2=5.00,  # consensus fair
        edge_pct=10.0,
        scope="ft",
        is_active=True,
        detected_at=datetime.now(UTC),
    )
    basic_setup.add(rv)
    basic_setup.flush()
    snap = svc.upsert_from_opportunity(rv)
    basic_setup.commit()

    assert snap.type == "reverse_value"
    assert snap.provider1_id == "pinnacle"
    assert snap.odds1_at_detection == 5.50
    assert snap.fair_odds1_at_detection == 5.00  # consensus benchmark
    assert snap.provider2_id is None  # consensus is not a real provider
