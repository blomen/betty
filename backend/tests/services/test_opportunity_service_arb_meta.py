"""OpportunityService.scan_arb_workflow — provider_meta plumbing.

Verifies that the legs returned to the API include each leg's
provider_meta (matchup_id etc.), which the mirror workflows need to
navigate to provider event pages without an extra lookup.
"""

from __future__ import annotations

import os

# Force in-memory SQLite for these tests — the conftest fixture honors a real
# DATABASE_URL if set, but we don't want the prod Postgres for unit tests.
os.environ.pop("DATABASE_URL", None)

from unittest.mock import MagicMock, patch

import pytest

from src.db.models import Event, Odds, Provider
from src.services.opportunity_service import OpportunityService


@pytest.fixture
def service_with_event(db_session):
    """Seed Event + Provider + Odds rows so _format_arb_workflow can find them."""
    db_session.add_all(
        [
            Provider(id="betinia", name="Betinia"),
            Provider(id="pinnacle", name="Pinnacle"),
            Event(
                id="evt-1",
                sport="football",
                league="Austria - Bundesliga",
                home_team="hartberg",
                away_team="lask linz",
                display_home="Hartberg",
                display_away="LASK Linz",
            ),
            Odds(
                event_id="evt-1",
                provider_id="betinia",
                market="1x2",
                outcome="away",
                odds=1.6154,
                provider_meta={"prov_home": "hartberg", "prov_away": "lask"},
            ),
            Odds(
                event_id="evt-1",
                provider_id="pinnacle",
                market="1x2",
                outcome="home",
                odds=20.0,
                provider_meta={
                    "matchup_id": "1629999",
                    "period": 0,
                    "prov_home": "hartberg",
                    "prov_away": "lask",
                },
            ),
        ]
    )
    db_session.commit()
    return OpportunityService(db_session)


def _stub_scanner_arb(service, evt_id="evt-1"):
    """Patch the OpportunityScanner to return a single fake arb-opp."""
    fake_opp = MagicMock()
    fake_opp.event_id = evt_id
    fake_opp.market = "1x2"
    fake_opp.sport = "football"
    fake_opp.league = "Austria - Bundesliga"
    fake_opp.home_team = "hartberg"
    fake_opp.away_team = "lask linz"
    fake_opp.start_time = None
    fake_opp.combined_edge_pct = 23.22
    fake_opp.guaranteed_profit_pct = 23.22
    fake_opp.arb_profit_pct = 23.22
    fake_opp.legs = [
        {"provider": "betinia", "outcome": "away", "odds": 1.6154, "is_sharp": False},
        {"provider": "pinnacle", "outcome": "home", "odds": 20.0, "is_sharp": True},
    ]
    fake_opp.arb_legs = list(fake_opp.legs)
    return fake_opp


def test_arb_workflow_attaches_provider_meta_to_legs(service_with_event):
    fake_opp = _stub_scanner_arb(service_with_event)
    with patch("src.services.opportunity_service.OpportunityScanner") as ScannerCls:
        ScannerCls.return_value.scan_arb_for_provider.return_value = [fake_opp]
        result = service_with_event.scan_arb_workflow(anchor_providers=["betinia"], limit=5)

    opps = result["opportunities"]
    assert len(opps) == 1
    legs = opps[0]["legs"]
    assert len(legs) == 2

    # Every leg must carry a provider_meta dict
    assert all("provider_meta" in leg for leg in legs)

    # Pinnacle leg must surface its matchup_id (the whole point of this fix)
    pinn = next(l for l in legs if l["provider"] == "pinnacle")
    assert pinn["provider_meta"].get("matchup_id") == "1629999"
    assert pinn["provider_meta"].get("period") == 0


def test_arb_workflow_legs_get_empty_meta_when_db_has_none(db_session):
    """If the DB row has no provider_meta, the leg's provider_meta is {}."""
    db_session.add_all(
        [
            Provider(id="betinia", name="Betinia"),
            Provider(id="other", name="Other"),
            Event(id="evt-2", sport="football", league="L", home_team="h", away_team="a"),
            Odds(event_id="evt-2", provider_id="betinia", market="1x2", outcome="home", odds=2.0),
            Odds(event_id="evt-2", provider_id="other", market="1x2", outcome="away", odds=2.0),
        ]
    )
    db_session.commit()
    service = OpportunityService(db_session)

    fake_opp = MagicMock()
    fake_opp.event_id = "evt-2"
    fake_opp.market = "1x2"
    fake_opp.sport = "football"
    fake_opp.league = "L"
    fake_opp.home_team = "h"
    fake_opp.away_team = "a"
    fake_opp.start_time = None
    fake_opp.combined_edge_pct = 5.0
    fake_opp.guaranteed_profit_pct = 5.0
    fake_opp.arb_profit_pct = None
    fake_opp.legs = [
        {"provider": "betinia", "outcome": "home", "odds": 2.0, "is_sharp": False},
        {"provider": "other", "outcome": "away", "odds": 2.0, "is_sharp": False},
    ]
    fake_opp.arb_legs = None

    with patch("src.services.opportunity_service.OpportunityScanner") as ScannerCls:
        ScannerCls.return_value.scan_arb_for_provider.return_value = [fake_opp]
        result = service.scan_arb_workflow(anchor_providers=["betinia"], limit=5)

    legs = result["opportunities"][0]["legs"]
    assert all(leg["provider_meta"] == {} for leg in legs)


def test_arb_workflow_arb_legs_also_enriched(service_with_event):
    """arb_legs (when populated) must get the same provider_meta enrichment."""
    fake_opp = _stub_scanner_arb(service_with_event)
    with patch("src.services.opportunity_service.OpportunityScanner") as ScannerCls:
        ScannerCls.return_value.scan_arb_for_provider.return_value = [fake_opp]
        result = service_with_event.scan_arb_workflow(anchor_providers=["betinia"], limit=5)

    arb_legs = result["opportunities"][0]["arb_legs"]
    assert arb_legs is not None
    assert all("provider_meta" in leg for leg in arb_legs)
    pinn = next(l for l in arb_legs if l["provider"] == "pinnacle")
    assert pinn["provider_meta"].get("matchup_id") == "1629999"
