"""OpportunityRepo upserts tag rows with scope; ft and f5 rows coexist."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Opportunity, Provider
from src.repositories.opportunity_repo import OpportunityRepo


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()
    # Seed providers and an MLB event so the FKs resolve.
    session.add(Provider(id="pinnacle", name="Pinnacle"))
    session.add(Provider(id="kambi_mlb", name="Kambi MLB"))
    session.add(
        Event(
            id="mlb:test1",
            sport="baseball",
            home_team="A",
            away_team="B",
        )
    )
    session.commit()
    yield session
    session.close()


def test_upsert_value_defaults_to_ft_scope(db):
    repo = OpportunityRepo(db)
    is_new, opp = repo.upsert_value(
        event_id="mlb:test1",
        market="total",
        outcome="over",
        provider_id="kambi_mlb",
        provider_odds=1.95,
        fair_odds=1.85,
        edge_pct=5.4,
        outcomes_json=[],
        point=8.5,
    )
    db.commit()
    assert is_new is True
    assert opp.scope == "ft"


def test_upsert_value_tags_f5_scope(db):
    repo = OpportunityRepo(db)
    is_new, opp = repo.upsert_value(
        event_id="mlb:test1",
        market="total",
        outcome="over",
        provider_id="kambi_mlb",
        provider_odds=2.05,
        fair_odds=1.95,
        edge_pct=5.1,
        outcomes_json=[],
        point=4.5,
        scope="f5",
    )
    db.commit()
    assert is_new is True
    assert opp.scope == "f5"


def test_ft_and_f5_rows_coexist_on_same_event_market_provider(db):
    """The unique-upsert index includes scope, so ft and f5 don't collide."""
    repo = OpportunityRepo(db)
    _, ft_opp = repo.upsert_value(
        event_id="mlb:test1",
        market="total",
        outcome="over",
        provider_id="kambi_mlb",
        provider_odds=1.95,
        fair_odds=1.85,
        edge_pct=5.4,
        outcomes_json=[],
        point=8.5,
        scope="ft",
    )
    _, f5_opp = repo.upsert_value(
        event_id="mlb:test1",
        market="total",
        outcome="over",
        provider_id="kambi_mlb",
        provider_odds=2.05,
        fair_odds=1.95,
        edge_pct=5.1,
        outcomes_json=[],
        point=4.5,
        scope="f5",
    )
    db.commit()

    rows = (
        db.query(Opportunity)
        .filter(
            Opportunity.event_id == "mlb:test1",
            Opportunity.market == "total",
            Opportunity.outcome1 == "over",
            Opportunity.provider1_id == "kambi_mlb",
            Opportunity.type == "value",
        )
        .all()
    )
    scopes = sorted(r.scope for r in rows)
    assert scopes == ["f5", "ft"], f"expected both ft and f5 rows to coexist, got scopes={scopes}"
    assert ft_opp.id != f5_opp.id, "ft and f5 must be distinct rows"


def test_upsert_value_idempotent_within_same_scope(db):
    """Re-upserting at the same scope updates the existing row, not creates a new one."""
    repo = OpportunityRepo(db)
    is_new_1, opp_1 = repo.upsert_value(
        event_id="mlb:test1",
        market="total",
        outcome="over",
        provider_id="kambi_mlb",
        provider_odds=1.95,
        fair_odds=1.85,
        edge_pct=5.4,
        outcomes_json=[],
        point=8.5,
        scope="f5",
    )
    db.commit()
    is_new_2, opp_2 = repo.upsert_value(
        event_id="mlb:test1",
        market="total",
        outcome="over",
        provider_id="kambi_mlb",
        provider_odds=2.00,  # updated price
        fair_odds=1.85,
        edge_pct=8.1,  # updated edge
        outcomes_json=[],
        point=8.5,
        scope="f5",
    )
    db.commit()
    assert is_new_1 is True
    assert is_new_2 is False
    assert opp_1.id == opp_2.id
    assert opp_2.odds1 == 2.00
    assert opp_2.edge_pct == 8.1
