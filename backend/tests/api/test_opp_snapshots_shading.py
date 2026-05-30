"""shading_clv_breakdown section in /api/opp-snapshots/stats.

Groups realized CLV by (odds_bucket x shading_risk), only counts groups with n >= 3,
and excludes rows where shading_risk / odds_bucket / pinnacle_clv_pct is NULL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import app
from src.api.deps import get_db
from src.db.models import Base, Event, OppSnapshot, Provider


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    now = datetime.now(UTC)

    s.add(Provider(id="betsson", name="Betsson"))
    s.add(
        Event(
            id="e1",
            sport="soccer_epl",
            home_team="A",
            away_team="B",
            start_time=now - timedelta(hours=1),
        )
    )
    s.flush()

    # --- Group 1: odds_bucket="2.5-4.0", shading_risk="high" — 3 rows → SHOULD appear ---
    clv_values_high = [2.5, -1.0, 0.5]
    for i, clv in enumerate(clv_values_high):
        s.add(
            OppSnapshot(
                event_id="e1",
                type="value",
                market="1x2",
                outcome1=f"high_{i}",
                scope="ft",
                provider1_id="betsson",
                odds1_at_detection=3.0,
                first_detected_at=now - timedelta(hours=2),
                last_detected_at=now - timedelta(hours=2),
                clv_computed_at=now,
                pinnacle_clv_pct=clv,
                odds_bucket="2.5-4.0",
                shading_risk="high",
            )
        )

    # --- Group 2: odds_bucket="1.5-2.5", shading_risk="low" — 4 rows → SHOULD appear ---
    clv_values_low = [1.0, 2.0, 3.0, 4.0]
    for i, clv in enumerate(clv_values_low):
        s.add(
            OppSnapshot(
                event_id="e1",
                type="value",
                market="1x2",
                outcome1=f"low_{i}",
                scope="ft",
                provider1_id="betsson",
                odds1_at_detection=2.0,
                first_detected_at=now - timedelta(hours=2),
                last_detected_at=now - timedelta(hours=2),
                clv_computed_at=now,
                pinnacle_clv_pct=clv,
                odds_bucket="1.5-2.5",
                shading_risk="low",
            )
        )

    # --- Group 3: odds_bucket="4.0+", shading_risk="elevated" — only 2 rows → MUST NOT appear ---
    for i in range(2):
        s.add(
            OppSnapshot(
                event_id="e1",
                type="value",
                market="1x2",
                outcome1=f"elev_{i}",
                scope="ft",
                provider1_id="betsson",
                odds1_at_detection=5.0,
                first_detected_at=now - timedelta(hours=2),
                last_detected_at=now - timedelta(hours=2),
                clv_computed_at=now,
                pinnacle_clv_pct=1.0,
                odds_bucket="4.0+",
                shading_risk="elevated",
            )
        )

    # --- Excluded: shading_risk IS NULL — 3 rows → MUST NOT appear as a group ---
    for i in range(3):
        s.add(
            OppSnapshot(
                event_id="e1",
                type="value",
                market="1x2",
                outcome1=f"null_risk_{i}",
                scope="ft",
                provider1_id="betsson",
                odds1_at_detection=2.0,
                first_detected_at=now - timedelta(hours=2),
                last_detected_at=now - timedelta(hours=2),
                clv_computed_at=now,
                pinnacle_clv_pct=5.0,
                odds_bucket="1.5-2.5",
                shading_risk=None,
            )
        )

    # --- Excluded: odds_bucket IS NULL — 3 rows → MUST NOT appear ---
    for i in range(3):
        s.add(
            OppSnapshot(
                event_id="e1",
                type="value",
                market="1x2",
                outcome1=f"null_bucket_{i}",
                scope="ft",
                provider1_id="betsson",
                odds1_at_detection=2.0,
                first_detected_at=now - timedelta(hours=2),
                last_detected_at=now - timedelta(hours=2),
                clv_computed_at=now,
                pinnacle_clv_pct=5.0,
                odds_bucket=None,
                shading_risk="low",
            )
        )

    # --- Excluded: pinnacle_clv_pct IS NULL — 3 rows → MUST NOT appear ---
    for i in range(3):
        s.add(
            OppSnapshot(
                event_id="e1",
                type="value",
                market="1x2",
                outcome1=f"null_clv_{i}",
                scope="ft",
                provider1_id="betsson",
                odds1_at_detection=2.0,
                first_detected_at=now - timedelta(hours=2),
                last_detected_at=now - timedelta(hours=2),
                clv_computed_at=None,
                pinnacle_clv_pct=None,
                odds_bucket="1.5-2.5",
                shading_risk="low",
            )
        )

    s.commit()
    yield s
    s.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_stats_includes_shading_clv_breakdown(client):
    resp = client.get("/api/opp-snapshots/stats?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert "shading_clv_breakdown" in data, "shading_clv_breakdown key missing from response"


def test_shading_clv_breakdown_structure(client):
    resp = client.get("/api/opp-snapshots/stats?days=30")
    rows = resp.json()["shading_clv_breakdown"]
    assert isinstance(rows, list)
    for row in rows:
        assert set(row.keys()) >= {"odds_bucket", "shading_risk", "n", "mean_pinnacle_clv_pct"}


def test_shading_clv_breakdown_correct_grouping(client):
    resp = client.get("/api/opp-snapshots/stats?days=30")
    rows = resp.json()["shading_clv_breakdown"]

    # Build a lookup by (odds_bucket, shading_risk)
    by_key = {(r["odds_bucket"], r["shading_risk"]): r for r in rows}

    # Group 1: "2.5-4.0" / "high" — 3 rows, mean = (2.5 + -1.0 + 0.5) / 3 = 0.666...
    assert ("2.5-4.0", "high") in by_key
    row_high = by_key[("2.5-4.0", "high")]
    assert row_high["n"] == 3
    assert row_high["mean_pinnacle_clv_pct"] == pytest.approx((2.5 + -1.0 + 0.5) / 3, abs=0.01)

    # Group 2: "1.5-2.5" / "low" — 4 rows, mean = (1+2+3+4)/4 = 2.5
    assert ("1.5-2.5", "low") in by_key
    row_low = by_key[("1.5-2.5", "low")]
    assert row_low["n"] == 4
    assert row_low["mean_pinnacle_clv_pct"] == pytest.approx(2.5, abs=0.01)


def test_shading_clv_breakdown_min_count_gate(client):
    """Groups with fewer than 3 rows must be excluded."""
    resp = client.get("/api/opp-snapshots/stats?days=30")
    rows = resp.json()["shading_clv_breakdown"]
    by_key = {(r["odds_bucket"], r["shading_risk"]): r for r in rows}

    # 2-row group must not appear
    assert ("4.0+", "elevated") not in by_key


def test_shading_clv_breakdown_excludes_nulls(client):
    """NULL shading_risk / odds_bucket / pinnacle_clv_pct rows are excluded."""
    resp = client.get("/api/opp-snapshots/stats?days=30")
    rows = resp.json()["shading_clv_breakdown"]

    # No row should have None / null shading_risk or odds_bucket as a key
    for row in rows:
        assert row["shading_risk"] is not None
        assert row["odds_bucket"] is not None

    # The null_clv group (clv_computed_at=None) must not inflate counts in "1.5-2.5"/"low"
    by_key = {(r["odds_bucket"], r["shading_risk"]): r for r in rows}
    if ("1.5-2.5", "low") in by_key:
        # Only 4 valid rows seeded for this bucket/risk combo; nulls must not be counted
        assert by_key[("1.5-2.5", "low")]["n"] == 4
