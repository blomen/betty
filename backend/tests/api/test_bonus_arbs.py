"""Tests for GET /api/bets/bonus-arbs.

Validates window bucketing (Europe/Stockholm calendar), arb-pair construction
from arb_group_id linkage, SEK conversion across mixed-currency legs, and
edge cases: unpaired anchors, bonus anchors, voided legs, non-soft anchors.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api import app
from src.api.deps import get_db
from src.db.models import Base, Bet, Event, Profile

STK = ZoneInfo("Europe/Stockholm")


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Profile(id=1, name="test", is_active=True))
    s.add(
        Event(
            id="soccer:laliga:rma-vs-bar:2026-05-27",
            sport="soccer",
            league="laliga",
            home_team="Real Madrid",
            away_team="Barcelona",
            start_time=datetime(2026, 5, 27, 19, 0, tzinfo=UTC),
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


def _bet(s, **kw):
    base = dict(
        profile_id=1,
        odds=2.0,
        stake=500.0,
        currency="SEK",
        result="pending",
        is_bonus=False,
        placed_at=datetime(2026, 5, 27, 11, 42, tzinfo=UTC).replace(tzinfo=None),
    )
    base.update(kw)
    b = Bet(**base)
    s.add(b)
    s.commit()
    return b


def test_empty_db_returns_zeroed_summary(client):
    r = client.get("/api/bets/bonus-arbs?window=week")
    assert r.status_code == 200
    body = r.json()
    assert body["groups"] == []
    assert body["summary"]["today"]["arbs"] == 0
    assert body["summary"]["week"]["arbs"] == 0
    assert body["summary"]["thirty"]["arbs"] == 0
    assert body["summary"]["today"]["pnl_sek"] == 0.0
    assert len(body["daily"]) == 30
    assert all(d["arbs"] == 0 and d["pnl_sek"] == 0.0 for d in body["daily"])


def test_settled_arb_pair_realized_yield(client, db_session):
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    anchor = _bet(
        db_session,
        provider_id="betinia",
        event_id=eid,
        market="1x2",
        outcome="home",
        odds=2.10,
        stake=500.0,
        currency="SEK",
        payout=1050.0,
        result="won",
        arb_group_id="grp1",
        bet_type="arb_anchor",
        fair_odds_at_placement=2.05,
        clv_pct=1.4,
    )
    counter = _bet(
        db_session,
        provider_id="pinnacle",
        event_id=eid,
        market="1x2",
        outcome="away",
        odds=2.05,
        stake=512.0,
        currency="SEK",
        payout=0.0,
        result="lost",
        arb_group_id="grp1",
        bet_type="arb_counter",
        clv_pct=-0.3,
    )
    r = client.get("/api/bets/bonus-arbs?window=30d")
    body = r.json()
    assert len(body["groups"]) == 1
    g = body["groups"][0]
    assert g["status"] == "settled"
    assert g["arb_group_id"] == "grp1"
    # PnL = (1050 - 500) + (0 - 512) = 38 SEK
    assert g["pnl_sek"] == pytest.approx(38.0)
    # Total stake = 500 + 512 = 1012
    assert g["total_stake_sek"] == pytest.approx(1012.0)
    # Realized yield = 38/1012 * 100 ≈ 3.755%
    assert g["realized_yield_pct"] == pytest.approx(3.755, abs=0.01)
    # Displayed = (1 / (1/2.10 + 1/2.05) - 1) * 100 ≈ 1.32%
    assert g["displayed_yield_pct"] == pytest.approx(1.325, abs=0.01)


def test_mixed_currency_sek_conversion(client, db_session):
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    # Lodur anchor in SEK, Polymarket counter in USDC
    anchor = _bet(
        db_session,
        provider_id="lodur",
        event_id=eid,
        market="1x2",
        outcome="home",
        odds=2.10,
        stake=500.0,
        currency="SEK",
        payout=1050.0,
        result="won",
        arb_group_id="grp2",
        bet_type="arb_anchor",
    )
    counter = _bet(
        db_session,
        provider_id="polymarket",
        event_id=eid,
        market="1x2",
        outcome="away",
        odds=2.05,
        stake=48.76,
        currency="USDC",
        payout=0.0,
        result="lost",
        arb_group_id="grp2",
        bet_type="arb_counter",
    )
    r = client.get("/api/bets/bonus-arbs?window=30d")
    g = r.json()["groups"][0]
    # 48.76 USDC * 10.50 = 511.98 SEK counter stake; total = 500 + 511.98
    assert g["total_stake_sek"] == pytest.approx(1011.98, abs=0.1)
    # PnL = +550 SEK anchor + (-511.98) SEK counter = 38.02 SEK
    assert g["pnl_sek"] == pytest.approx(38.02, abs=0.1)


def test_unpaired_anchor_renders_as_partial(client, db_session):
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    _bet(
        db_session,
        provider_id="swiper",
        event_id=eid,
        market="1x2",
        outcome="home",
        odds=2.10,
        stake=500.0,
        currency="SEK",
        result="pending",
        arb_group_id=None,
        bet_type="arb_anchor",
    )
    r = client.get("/api/bets/bonus-arbs?window=30d")
    g = r.json()["groups"][0]
    assert g["status"] == "partial"
    assert g["counter"] is None
    assert g["displayed_yield_pct"] is None
    assert g["realized_yield_pct"] is None
    assert g["total_stake_sek"] == pytest.approx(500.0)


def test_bonus_anchor_displayed_yield_is_null(client, db_session):
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    _bet(
        db_session,
        provider_id="betinia",
        event_id=eid,
        market="1x2",
        outcome="home",
        odds=2.10,
        stake=500.0,
        currency="SEK",
        payout=1050.0,
        result="won",
        is_bonus=True,
        arb_group_id="grp3",
        bet_type="arb_anchor",
    )
    _bet(
        db_session,
        provider_id="pinnacle",
        event_id=eid,
        market="1x2",
        outcome="away",
        odds=2.05,
        stake=512.0,
        currency="SEK",
        payout=0.0,
        result="lost",
        arb_group_id="grp3",
        bet_type="arb_counter",
    )
    r = client.get("/api/bets/bonus-arbs?window=30d")
    g = r.json()["groups"][0]
    assert g["anchor"]["is_bonus"] is True
    assert g["displayed_yield_pct"] is None
    # Realized still computed (bonus profit = full payout since stake was free)
    # anchor.profit = 1050 (full payout, no stake deduction), counter.profit = -512
    # = +538 / 1012 ≈ +53.16%
    assert g["realized_yield_pct"] == pytest.approx(53.16, abs=0.5)


def test_non_soft_anchor_excluded(client, db_session):
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    _bet(
        db_session,
        provider_id="spelklubben",
        event_id=eid,
        market="1x2",
        outcome="home",
        odds=2.10,
        stake=500.0,
        currency="SEK",
        result="pending",
        arb_group_id="grp4",
        bet_type="arb_anchor",
    )
    r = client.get("/api/bets/bonus-arbs?window=30d")
    assert r.json()["groups"] == []


def test_stockholm_day_boundary_buckets(client, db_session):
    """An anchor placed at 23:59 Stockholm vs 00:01 the next day must land
    in separate daily[] buckets."""
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    # 2026-05-26 21:59 UTC = 2026-05-26 23:59 Stockholm (CEST is UTC+2 in May)
    late = datetime(2026, 5, 26, 21, 59, tzinfo=UTC).replace(tzinfo=None)
    # 2026-05-26 22:01 UTC = 2026-05-27 00:01 Stockholm
    early = datetime(2026, 5, 26, 22, 1, tzinfo=UTC).replace(tzinfo=None)
    _bet(
        db_session,
        provider_id="betinia",
        event_id=eid,
        market="1x2",
        outcome="home",
        odds=2.10,
        stake=500.0,
        payout=1050.0,
        result="won",
        arb_group_id="late",
        bet_type="arb_anchor",
        placed_at=late,
    )
    _bet(
        db_session,
        provider_id="pinnacle",
        event_id=eid,
        market="1x2",
        outcome="away",
        odds=2.05,
        stake=512.0,
        payout=0.0,
        result="lost",
        arb_group_id="late",
        bet_type="arb_counter",
        placed_at=late,
    )
    _bet(
        db_session,
        provider_id="lodur",
        event_id=eid,
        market="1x2",
        outcome="home",
        odds=2.10,
        stake=500.0,
        payout=0.0,
        result="lost",
        arb_group_id="early",
        bet_type="arb_anchor",
        placed_at=early,
    )
    _bet(
        db_session,
        provider_id="pinnacle",
        event_id=eid,
        market="1x2",
        outcome="away",
        odds=2.05,
        stake=512.0,
        payout=1050.0,
        result="won",
        arb_group_id="early",
        bet_type="arb_counter",
        placed_at=early,
    )
    r = client.get("/api/bets/bonus-arbs?window=30d")
    daily = {d["date"]: d for d in r.json()["daily"]}
    assert daily["2026-05-26"]["arbs"] == 1
    assert daily["2026-05-27"]["arbs"] == 1


def test_window_today_vs_week_vs_30d(client, db_session, monkeypatch):
    """?window= controls groups[] selection: today < week < 30d."""
    import src.api.routes.bonus_arbs as mod

    # Freeze "now" to a known Stockholm time so the test is deterministic.
    fixed_now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(mod, "_now_utc", lambda: fixed_now)

    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    today = datetime(2026, 5, 27, 10, 0, tzinfo=UTC).replace(tzinfo=None)
    this_week = datetime(2026, 5, 25, 10, 0, tzinfo=UTC).replace(tzinfo=None)  # Monday
    last_month = datetime(2026, 5, 5, 10, 0, tzinfo=UTC).replace(tzinfo=None)
    for at, gid in [(today, "g_today"), (this_week, "g_week"), (last_month, "g_month")]:
        _bet(
            db_session,
            provider_id="betinia",
            event_id=eid,
            market="1x2",
            outcome="home",
            odds=2.10,
            stake=500.0,
            result="pending",
            arb_group_id=gid,
            bet_type="arb_anchor",
            placed_at=at,
        )

    assert len(client.get("/api/bets/bonus-arbs?window=today").json()["groups"]) == 1
    assert len(client.get("/api/bets/bonus-arbs?window=week").json()["groups"]) == 2
    assert len(client.get("/api/bets/bonus-arbs?window=30d").json()["groups"]) == 3
