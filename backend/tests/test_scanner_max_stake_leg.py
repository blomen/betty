"""Scanner.group_odds propagates Odds.max_stake into leg dicts."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analysis.scanner import OpportunityScanner
from src.db.models import Base, Event, Odds, Provider


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Provider(id="pinnacle", name="Pinnacle", is_enabled=True))
    ev = Event(
        id="evt1",
        sport="basketball",
        home_team="A",
        away_team="B",
        start_time=datetime.now(UTC),
        home_away_validated=True,
    )
    session.add(ev)
    session.flush()
    session.add(
        Odds(
            event_id="evt1",
            provider_id="pinnacle",
            market="moneyline",
            outcome="home",
            odds=2.10,
            scope="ft",
            max_stake=1500.0,
        )
    )
    session.commit()
    yield session
    session.close()


def test_group_odds_includes_max_stake_in_leg_dict(db):
    scanner = OpportunityScanner(db)
    ev = db.query(Event).one()
    grouped = scanner.group_odds(ev, check_staleness=False)
    assert grouped, "no markets grouped"
    leg = grouped["moneyline"]["home"][0]
    assert leg["max_stake"] == 1500.0
