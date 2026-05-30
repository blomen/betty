"""Tests for Stats page per-profile account styles."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import app
from src.api.deps import get_db
from src.db.models import Base, Bet, Profile  # noqa: F401


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
    s.add(Profile(id=1, name="test", is_active=True))
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


def test_profile_has_style_default_personal(db_session):
    p = Profile(name="t_style_default")
    db_session.add(p)
    db_session.commit()
    assert p.style == "personal"


def test_profile_style_settable(db_session):
    p = Profile(name="t_style_bonus", style="bonus_extraction")
    db_session.add(p)
    db_session.commit()
    assert p.style == "bonus_extraction"


def test_profile_to_dict_includes_style(db_session):
    from src.api.routes.profiles import profile_to_dict
    from src.repositories import ProfileRepo

    p = Profile(name="t_dict_style", style="bonus_extraction")
    db_session.add(p)
    db_session.commit()
    d = profile_to_dict(p, ProfileRepo(db_session))
    assert d["style"] == "bonus_extraction"


def test_profile_repo_get_returns_by_id(db_session):
    from src.repositories import ProfileRepo

    p = Profile(name="t_repo_get")
    db_session.add(p)
    db_session.commit()
    assert ProfileRepo(db_session).get(p.id).id == p.id


def test_profile_repo_get_none_returns_active(db_session):
    from src.repositories import ProfileRepo

    repo = ProfileRepo(db_session)
    assert repo.get(None).id == repo.get_active().id


def test_get_stats_profile_id_matches_active_when_omitted(db_session):
    from src.services import BankrollService

    svc = BankrollService(db_session)
    active_id = svc.profile_repo.get_active().id
    assert svc.get_stats()["profile_id"] == active_id
    assert svc.get_stats(active_id)["profile_id"] == active_id


def test_analytics_by_strategy_lanes(client, db_session):
    from src.repositories import ProfileRepo

    pid = ProfileRepo(db_session).get_active().id
    db_session.add_all(
        [
            Bet(
                profile_id=pid,
                provider_id="betsson",
                market="1x2",
                outcome="home",
                odds=2.0,
                stake=100.0,
                currency="SEK",
                bet_type="value",
                result="won",
                payout=200.0,
                clv_pct=3.0,
            ),
            Bet(
                profile_id=pid,
                provider_id="betsson",
                market="1x2",
                outcome="home",
                odds=2.0,
                stake=100.0,
                currency="SEK",
                bet_type="value",
                result="lost",
                payout=0.0,
                clv_pct=-1.0,
            ),
            Bet(
                profile_id=pid,
                provider_id="pinnacle",
                market="1x2",
                outcome="home",
                odds=2.0,
                stake=100.0,
                currency="SEK",
                bet_type="arb",
                result="won",
                payout=200.0,
            ),
        ]
    )
    db_session.commit()
    r = client.get(f"/api/bets/analytics?days=3650&profile_id={pid}").json()
    v = r["by_strategy"]["Value"]
    for k in ("n", "win_pct", "staked", "profit", "roi_pct", "avg_clv_pct", "clv_positive_pct"):
        assert k in v, f"missing key {k}"
    assert v["n"] == 2
    assert v["profit"] == 0.0
    assert v["clv_positive_pct"] == 50.0
    assert r["by_strategy"]["Arb"]["n"] == 1


def test_analytics_by_provider_currency_correct(client, db_session):
    from src.db.models import Bet
    from src.repositories import ProfileRepo

    pid = ProfileRepo(db_session).get_active().id
    db_session.add_all(
        [
            Bet(
                profile_id=pid,
                provider_id="betsson",
                market="1x2",
                outcome="home",
                odds=2.0,
                stake=100.0,
                currency="SEK",
                bet_type="value",
                result="won",
                payout=200.0,
            ),
            Bet(
                profile_id=pid,
                provider_id="polymarket",
                market="moneyline",
                outcome="home",
                odds=2.0,
                stake=10.0,
                currency="USDC",
                bet_type="value",
                result="won",
                payout=20.0,
            ),
        ]
    )
    db_session.commit()
    r = client.get(f"/api/bets/analytics?days=3650&profile_id={pid}").json()
    assert r["by_provider"]["betsson"]["profit"] == 100.0
    assert r["by_provider"]["polymarket"]["profit"] > 100.0  # +10 USDC ~105 SEK


def test_equity_curve_cumulative_and_baseline(client, db_session):
    from datetime import UTC, datetime, timedelta

    from src.db.models import Bet
    from src.repositories import ProfileRepo

    pid = ProfileRepo(db_session).get_active().id
    t0 = datetime.now(UTC) - timedelta(days=2)
    db_session.add_all(
        [
            Bet(
                profile_id=pid,
                provider_id="betsson",
                market="1x2",
                outcome="home",
                odds=2.0,
                stake=100.0,
                currency="SEK",
                bet_type="value",
                result="won",
                payout=200.0,
                placed_at=t0,
            ),
            Bet(
                profile_id=pid,
                provider_id="betsson",
                market="1x2",
                outcome="home",
                odds=2.0,
                stake=100.0,
                currency="SEK",
                bet_type="value",
                result="lost",
                payout=0.0,
                placed_at=t0 + timedelta(hours=1),
            ),
        ]
    )
    db_session.commit()
    r = client.get(f"/api/bets/equity-curve?days=3650&profile_id={pid}").json()
    assert r["total_profit_sek"] == 0.0
    assert [round(p["cum_profit_sek"]) for p in r["points"]] == [100, 0]
    assert "current_bankroll_sek" in r
    assert "total_staked_sek" in r


def test_equity_curve_total_matches_get_stats_with_bonus(client, db_session):
    """A winning bonus bet must NOT inflate the curve vs the KPI Net Profit."""
    from src.db.models import Bet
    from src.repositories import ProfileRepo
    from src.services import BankrollService

    pid = ProfileRepo(db_session).get_active().id
    db_session.add_all(
        [
            Bet(
                profile_id=pid,
                provider_id="betsson",
                market="1x2",
                outcome="home",
                odds=2.0,
                stake=100.0,
                currency="SEK",
                bet_type="value",
                result="won",
                payout=200.0,
            ),
            Bet(
                profile_id=pid,
                provider_id="betsson",
                market="1x2",
                outcome="home",
                odds=3.0,
                stake=50.0,
                currency="SEK",
                bet_type="value",
                is_bonus=True,
                result="won",
                payout=150.0,
            ),
        ]
    )
    db_session.commit()
    curve = client.get(f"/api/bets/equity-curve?profile_id={pid}").json()
    stats_profit = BankrollService(db_session).get_stats(pid)["total_profit"]
    assert curve["total_profit_sek"] == stats_profit  # both exclude the bonus bet


def test_profile_bonus_statuses_endpoint(client, db_session):
    from src.repositories import ProfileRepo

    pid = ProfileRepo(db_session).get_active().id
    r = client.get(f"/api/profiles/{pid}/bonus-statuses")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
