"""Per-sport blended-vs-Pinnacle comparison section in /api/opp-snapshots/stats."""

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

    for i in range(4):
        s.add(
            OppSnapshot(
                event_id="e1",
                type="value",
                market="1x2",
                outcome1=f"outcome_{i}",  # unique per row to satisfy UQ constraint
                scope="ft",
                provider1_id="betsson",
                odds1_at_detection=2.1,
                first_detected_at=now - timedelta(hours=2),
                last_detected_at=now - timedelta(hours=2),
                clv_computed_at=now,
                pinnacle_clv_pct=1.0,
                blended_clv_pct=3.0,
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


def test_stats_includes_sport_blend_comparison(client):
    resp = client.get("/api/opp-snapshots/stats?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert "sport_blend_comparison" in data
    rows = data["sport_blend_comparison"]
    assert len(rows) == 1
    row = rows[0]
    assert row["sport"] == "soccer_epl"
    assert row["n"] == 4
    assert row["mean_pinnacle_clv_pct"] == pytest.approx(1.0)
    assert row["mean_blended_clv_pct"] == pytest.approx(3.0)
    assert row["delta"] == pytest.approx(2.0)
