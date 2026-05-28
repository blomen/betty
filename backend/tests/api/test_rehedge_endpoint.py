"""Test the /api/opportunities/rehedge endpoint."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import app
from src.api.deps import get_db
from src.db.models import Base, Bet, Event, Opportunity, Provider


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
    yield s
    s.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def rehedge_opp(db_session):
    """Insert a Provider / Event / Bet / Opportunity tuple for the endpoint to return."""
    for pid in ["unibet", "betsson"]:
        db_session.add(Provider(id=pid, name=pid.title()))
    event = Event(
        id="evt-rehedge-1",
        sport="americanfootball_nfl",
        home_team="Pats",
        away_team="Jets",
        start_time=datetime.now(UTC) + timedelta(hours=12),
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        Bet(
            id=99,
            event_id="evt-rehedge-1",
            provider_id="unibet",
            market="spread",
            outcome="home",
            point=-2.5,
            odds=1.91,
            stake=100.0,
            currency="SEK",
            result="pending",
            bet_type="value",
            start_time=event.start_time,
        )
    )
    db_session.add(
        Opportunity(
            type="rehedge",
            event_id="evt-rehedge-1",
            market="spread",
            scope="ft",
            provider1_id="betsson",
            odds1=1.91,
            outcome1="away",
            point=3.5,
            total_stake=95.0,
            is_active=True,
            annotations={
                "case": "post_placement_middle",
                "bet_id": 99,
                "key_number": 3,
                "wing_loss_pct": 0.01,
                "base_currency": "SEK",
                "recommended_stake_base": 95.0,
                "on_arb_leg": False,
            },
        )
    )
    db_session.commit()


class TestRehedgeEndpoint:
    def test_returns_active_rehedge_opportunities(self, client, rehedge_opp):
        resp = client.get("/api/opportunities/rehedge")
        assert resp.status_code == 200
        data = resp.json()
        assert "opportunities" in data
        assert len(data["opportunities"]) == 1
        opp = data["opportunities"][0]
        assert opp["case"] == "post_placement_middle"
        assert opp["original_bet_id"] == 99
        assert opp["hedge_provider"] == "betsson"
        assert opp["hedge_outcome"] == "away"
        assert opp["hedge_point"] == 3.5
        assert opp["hedge_odds"] == 1.91
        assert opp["recommended_stake_sek"] == 95.0
        assert opp["key_number"] == 3
        assert opp["on_arb_leg"] is False
        assert opp["event"]["home_team"] == "Pats"

    def test_excludes_inactive(self, client, rehedge_opp, db_session):
        # Mark the opportunity inactive
        db_session.query(Opportunity).filter(Opportunity.type == "rehedge").update({"is_active": False})
        db_session.commit()

        resp = client.get("/api/opportunities/rehedge")
        assert resp.status_code == 200
        assert resp.json()["opportunities"] == []
