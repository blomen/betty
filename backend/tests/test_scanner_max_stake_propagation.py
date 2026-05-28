"""End-to-end: Odds.max_stake flows through scan_arb_for_provider into ArbOpportunity.legs[i]['max_stake']."""

from datetime import UTC, datetime, timedelta

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
    session.add_all(
        [
            Provider(id="pinnacle", name="Pinnacle", is_enabled=True),
            Provider(id="betinia", name="Betinia", is_enabled=True),
        ]
    )
    ev = Event(
        id="evt1",
        sport="basketball",
        home_team="A",
        away_team="B",
        start_time=datetime.now(UTC) + timedelta(hours=4),
        home_away_validated=True,
    )
    session.add(ev)
    session.flush()
    # Pinnacle: home 2.0 away 2.0 (fair = 2.0 each after devig, perfect book)
    # Betinia: home 2.20 (+EV vs fair), away 1.70 (-EV).
    # Platform-conflict resolution demotes betinia's away leg to Pinnacle (away
    # max_stake=800.0), so the resulting arb has one Pinnacle leg carrying cap.
    session.add_all(
        [
            Odds(
                event_id="evt1",
                provider_id="pinnacle",
                market="moneyline",
                outcome="home",
                odds=2.0,
                scope="ft",
                max_stake=1500.0,
                updated_at=datetime.now(UTC),
            ),
            Odds(
                event_id="evt1",
                provider_id="pinnacle",
                market="moneyline",
                outcome="away",
                odds=2.0,
                scope="ft",
                max_stake=800.0,
                updated_at=datetime.now(UTC),
            ),
            Odds(
                event_id="evt1",
                provider_id="betinia",
                market="moneyline",
                outcome="home",
                odds=2.20,
                scope="ft",
                updated_at=datetime.now(UTC),
            ),
            # Betinia away is below fair — anchor-mode keeps it, but
            # platform-conflict resolution demotes it to Pinnacle (single-book rule).
            Odds(
                event_id="evt1",
                provider_id="betinia",
                market="moneyline",
                outcome="away",
                odds=1.70,
                scope="ft",
                updated_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    yield session
    session.close()


def test_arb_opportunity_legs_carry_max_stake(db):
    scanner = OpportunityScanner(db)
    arbs = scanner.scan_arb_for_provider("betinia")
    assert arbs, "expected at least one arb opportunity"
    arb = arbs[0]
    pinnacle_legs = [leg for leg in arb.legs if leg["provider"] == "pinnacle"]
    assert pinnacle_legs, "expected at least one Pinnacle leg in the arb"
    # The away leg has max_stake=800.0 in the fixture; min across Pinnacle legs is 800.0.
    pinnacle_caps = [leg.get("max_stake") for leg in pinnacle_legs if leg.get("max_stake") is not None]
    assert pinnacle_caps, "expected populated max_stake on at least one Pinnacle leg"
    assert min(pinnacle_caps) == 800.0
