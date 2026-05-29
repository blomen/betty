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


def test_backfill_skips_events_that_have_not_started(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    stats = svc.compute_closing_clv()
    assert stats["processed"] == 0  # event start_time is +2h


def test_backfill_populates_clv_for_started_events(basic_setup):
    from src.db.models import Event, Odds, OppSnapshot
    from src.services.opp_snapshot_service import OppSnapshotService

    # Event has already started
    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = datetime.now(UTC) - timedelta(minutes=5)

    # Seed Pinnacle closing odds for the outcome and its complement
    basic_setup.add_all(
        [
            Odds(
                event_id="evt-1",
                provider_id="pinnacle",
                market="moneyline",
                outcome="A",
                odds=2.00,
                scope="ft",
                updated_at=datetime.now(UTC) - timedelta(minutes=6),
            ),
            Odds(
                event_id="evt-1",
                provider_id="pinnacle",
                market="moneyline",
                outcome="B",
                odds=2.00,
                scope="ft",
                updated_at=datetime.now(UTC) - timedelta(minutes=6),
            ),
            # Unibet's own closing price (slightly worse than detection)
            Odds(
                event_id="evt-1",
                provider_id="unibet",
                market="moneyline",
                outcome="A",
                odds=2.05,
                scope="ft",
                updated_at=datetime.now(UTC) - timedelta(minutes=6),
            ),
        ]
    )
    basic_setup.commit()

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp(odds=2.10, fair=2.00, edge=5.0)
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    stats = svc.compute_closing_clv()
    assert stats["processed"] == 1
    assert stats["updated"] == 1

    snap = basic_setup.query(OppSnapshot).first()
    assert snap.clv_computed_at is not None
    assert snap.provider1_closing_odds == 2.05
    # CLV vs same-provider = (2.10/2.05 - 1)*100 = 2.439...
    assert abs(snap.provider_clv_pct - 2.44) < 0.01
    # Pinnacle close devigged: 2.00 with sibling 2.00 → prob_sum=1.0, fair stays 2.00
    assert snap.pinnacle_closing_fair == 2.00
    # vs-Pinnacle CLV = (2.10/2.00 - 1)*100 = 5.0
    assert abs(snap.pinnacle_clv_pct - 5.0) < 0.01


def test_backfill_marks_done_even_with_no_closing_data(basic_setup):
    """If neither Pinnacle nor provider had closing odds, still mark
    clv_computed_at so the row isn't reprocessed every cycle."""
    from src.db.models import Event, OppSnapshot
    from src.services.opp_snapshot_service import OppSnapshotService

    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = datetime.now(UTC) - timedelta(minutes=5)
    basic_setup.commit()

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    stats = svc.compute_closing_clv()
    snap = basic_setup.query(OppSnapshot).first()
    assert snap.clv_computed_at is not None  # marked done
    assert snap.provider_clv_pct is None  # no data to compute
    assert snap.pinnacle_clv_pct is None
    assert stats["processed"] == 1
    assert stats["updated"] == 0  # nothing was actually computed


def test_backfill_arb_computes_closing_prob_sum(basic_setup):
    from src.db.models import Event, Odds, OppSnapshot, Provider
    from src.services.opp_snapshot_service import OppSnapshotService

    basic_setup.add(Provider(id="betinia", name="Betinia"))
    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = datetime.now(UTC) - timedelta(minutes=5)
    basic_setup.add_all(
        [
            Odds(
                event_id="evt-1",
                provider_id="unibet",
                market="moneyline",
                outcome="A",
                odds=2.08,
                scope="ft",
                updated_at=datetime.now(UTC) - timedelta(minutes=6),
            ),
            Odds(
                event_id="evt-1",
                provider_id="betinia",
                market="moneyline",
                outcome="B",
                odds=2.02,
                scope="ft",
                updated_at=datetime.now(UTC) - timedelta(minutes=6),
            ),
        ]
    )
    basic_setup.commit()

    from src.db.models import Opportunity

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
    svc = OppSnapshotService(basic_setup)
    svc.upsert_from_opportunity(arb)
    basic_setup.commit()

    svc.compute_closing_clv()
    snap = basic_setup.query(OppSnapshot).first()
    # prob_sum = 1/2.08 + 1/2.02 ≈ 0.9759
    assert abs(snap.closing_prob_sum - (1 / 2.08 + 1 / 2.02)) < 1e-6
    assert snap.was_arb_at_close is True  # < 1.0


def test_backfill_records_closing_age_minutes(basic_setup):
    from src.db.models import Event, Odds, OppSnapshot
    from src.services.opp_snapshot_service import OppSnapshotService

    now = datetime.now(UTC)
    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = now - timedelta(minutes=1)
    basic_setup.add(
        Odds(
            event_id="evt-1",
            provider_id="unibet",
            market="moneyline",
            outcome="A",
            odds=2.05,
            scope="ft",
            updated_at=now - timedelta(minutes=11),  # 10 min before start
        )
    )
    basic_setup.commit()

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    svc.compute_closing_clv()
    snap = basic_setup.query(OppSnapshot).first()
    # provider1_closing_age_minutes = start_time - updated_at = 10 min
    assert abs(snap.provider1_closing_age_minutes - 10.0) < 0.5
