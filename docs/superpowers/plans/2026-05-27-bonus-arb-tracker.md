# Bonus-Arb Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Stats-page section that lets the user audit whether the arbs they place across lodur/betinia/swiper (anchor) + the unlimited pool (counter) actually deliver the displayed yield once both legs settle, with daily/weekly P&L and per-leg CLV.

**Architecture:** One new read-only FastAPI endpoint (`GET /api/bets/bonus-arbs`) reading the existing `bets.arb_group_id` linkage, plus one self-contained React component mounted on `StatsPage.tsx`. No DB migration. One incidental change: `arb_group_id` is added to the `/api/bets` response payload and the frontend `Bet` type.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / pytest (backend), React 19 / TypeScript / Vite / @tanstack/react-query / Tailwind (frontend). Time-zone math uses `zoneinfo.ZoneInfo("Europe/Stockholm")` (stdlib).

**Spec:** `docs/superpowers/specs/2026-05-27-bonus-arb-tracker-design.md`

---

## File Structure

**Backend (new + modified):**
- Create: `backend/src/api/routes/bonus_arbs.py` — endpoint + helpers (`_window_bounds`, `_to_sek`, `_build_group`, `_summarize`, `_daily_buckets`)
- Modify: `backend/src/api/routes/__init__.py` — export `bonus_arbs_router`
- Modify: `backend/src/api/__init__.py` — `app.include_router(bonus_arbs_router)`
- Modify: `backend/src/api/routes/bets.py:316` — add `"arb_group_id": b.arb_group_id` to the per-bet dict
- Create: `backend/tests/api/test_bonus_arbs.py` — endpoint tests (8 scenarios from spec §Testing)

**Frontend (new + modified):**
- Modify: `frontend/src/types/index.ts` — add `arb_group_id?: string | null` to `Bet`; add `BonusArbLeg`, `BonusArbGroupEvent`, `BonusArbGroup`, `BonusArbSummary`, `BonusArbDaily`, `BonusArbResponse`
- Modify: `frontend/src/services/api/bets.ts` — add `getBonusArbs(window)` method
- Create: `frontend/src/components/BonusArbTracker.tsx` — section component (window chips, summary tiles, 30d bar chart, group table with expand)
- Modify: `frontend/src/pages/StatsPage.tsx` — import and mount `<BonusArbTracker />` between the charts row (~line 679) and the Realized-ROI Analytics accordion (~line 682)

**No DB migration. No change to placement, arb_correlation, or any extraction code.**

---

## Task 1: Surface `arb_group_id` on the existing `/api/bets` response

**Files:**
- Modify: `backend/src/api/routes/bets.py:316`

- [ ] **Step 1: Read the existing bet dict to confirm context**

Read `backend/src/api/routes/bets.py` lines 267-317 and locate the dict-append in `list_bets`. The `arb_group_id` field is currently missing from the response despite existing on `Bet`.

- [ ] **Step 2: Add `arb_group_id` to the bet dict**

In `backend/src/api/routes/bets.py`, in `list_bets`, locate this block (near line 315):

```python
                "boost_title": b.boost_title or ((sp.llm_title or sp.title) if sp else None),
                "bet_type": b.bet_type,
            }
```

Change to:

```python
                "boost_title": b.boost_title or ((sp.llm_title or sp.title) if sp else None),
                "bet_type": b.bet_type,
                "arb_group_id": b.arb_group_id,
            }
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/bets.py
git commit -m "feat(api): surface arb_group_id on /api/bets response

Required by the new Bonus-Arb Tracker section to group anchor+counter
legs in the frontend. Field exists in DB but was not previously
serialized."
```

---

## Task 2: Write failing tests for the bonus-arbs endpoint

**Files:**
- Create: `backend/tests/api/test_bonus_arbs.py`

- [ ] **Step 1: Inspect existing test pattern**

Read `backend/tests/test_arb_correlation.py` to see how `Bet`/`Event`/`Profile` are constructed in tests. The fixture pattern (in-memory SQLite via `create_engine("sqlite:///:memory:")` + `Base.metadata.create_all`) is reusable here.

Read `backend/src/db/models.py` lines 280-410 for the full `Bet` column list (currency, payout, result, placed_at, settled_at, fair_odds_at_placement, clv_pct, provider_clv_pct, is_bonus).

- [ ] **Step 2: Write the test file**

Create `backend/tests/api/test_bonus_arbs.py`:

```python
"""Tests for GET /api/bets/bonus-arbs.

Validates window bucketing (Europe/Stockholm calendar), arb-pair construction
from arb_group_id linkage, SEK conversion across mixed-currency legs, and
edge cases: unpaired anchors, bonus anchors, voided legs, non-soft anchors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    s.add(Event(
        id="soccer:laliga:rma-vs-bar:2026-05-27",
        sport="soccer", league="laliga",
        home_team="Real Madrid", away_team="Barcelona",
        start_time=datetime(2026, 5, 27, 19, 0, tzinfo=timezone.utc),
    ))
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
        profile_id=1, odds=2.0, stake=500.0, currency="SEK",
        result="pending", is_bonus=False,
        placed_at=datetime(2026, 5, 27, 11, 42, tzinfo=timezone.utc).replace(tzinfo=None),
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
    anchor = _bet(db_session, provider_id="betinia", event_id=eid, market="1x2",
                  outcome="home", odds=2.10, stake=500.0, currency="SEK",
                  payout=1050.0, result="won", arb_group_id="grp1",
                  bet_type="arb_anchor", fair_odds_at_placement=2.05, clv_pct=1.4)
    counter = _bet(db_session, provider_id="pinnacle", event_id=eid, market="1x2",
                   outcome="away", odds=2.05, stake=512.0, currency="SEK",
                   payout=0.0, result="lost", arb_group_id="grp1",
                   bet_type="arb_counter", clv_pct=-0.3)
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
    anchor = _bet(db_session, provider_id="lodur", event_id=eid, market="1x2",
                  outcome="home", odds=2.10, stake=500.0, currency="SEK",
                  payout=1050.0, result="won", arb_group_id="grp2",
                  bet_type="arb_anchor")
    counter = _bet(db_session, provider_id="polymarket", event_id=eid, market="1x2",
                   outcome="away", odds=2.05, stake=48.76, currency="USDC",
                   payout=0.0, result="lost", arb_group_id="grp2",
                   bet_type="arb_counter")
    r = client.get("/api/bets/bonus-arbs?window=30d")
    g = r.json()["groups"][0]
    # 48.76 USDC * 10.50 = 511.98 SEK counter stake; total = 500 + 511.98
    assert g["total_stake_sek"] == pytest.approx(1011.98, abs=0.1)
    # PnL = +550 SEK anchor + (-511.98) SEK counter = 38.02 SEK
    assert g["pnl_sek"] == pytest.approx(38.02, abs=0.1)


def test_unpaired_anchor_renders_as_partial(client, db_session):
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    _bet(db_session, provider_id="swiper", event_id=eid, market="1x2",
         outcome="home", odds=2.10, stake=500.0, currency="SEK",
         result="pending", arb_group_id=None, bet_type="arb_anchor")
    r = client.get("/api/bets/bonus-arbs?window=30d")
    g = r.json()["groups"][0]
    assert g["status"] == "partial"
    assert g["counter"] is None
    assert g["displayed_yield_pct"] is None
    assert g["realized_yield_pct"] is None
    assert g["total_stake_sek"] == pytest.approx(500.0)


def test_bonus_anchor_displayed_yield_is_null(client, db_session):
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    _bet(db_session, provider_id="betinia", event_id=eid, market="1x2",
         outcome="home", odds=2.10, stake=500.0, currency="SEK",
         payout=1050.0, result="won", is_bonus=True,
         arb_group_id="grp3", bet_type="arb_anchor")
    _bet(db_session, provider_id="pinnacle", event_id=eid, market="1x2",
         outcome="away", odds=2.05, stake=512.0, currency="SEK",
         payout=0.0, result="lost", arb_group_id="grp3",
         bet_type="arb_counter")
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
    _bet(db_session, provider_id="spelklubben", event_id=eid, market="1x2",
         outcome="home", odds=2.10, stake=500.0, currency="SEK",
         result="pending", arb_group_id="grp4", bet_type="arb_anchor")
    r = client.get("/api/bets/bonus-arbs?window=30d")
    assert r.json()["groups"] == []


def test_stockholm_day_boundary_buckets(client, db_session):
    """An anchor placed at 23:59 Stockholm vs 00:01 the next day must land
    in separate daily[] buckets."""
    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    # 2026-05-26 21:59 UTC = 2026-05-26 23:59 Stockholm (CEST is UTC+2 in May)
    late = datetime(2026, 5, 26, 21, 59, tzinfo=timezone.utc).replace(tzinfo=None)
    # 2026-05-26 22:01 UTC = 2026-05-27 00:01 Stockholm
    early = datetime(2026, 5, 26, 22, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    _bet(db_session, provider_id="betinia", event_id=eid, market="1x2",
         outcome="home", odds=2.10, stake=500.0, payout=1050.0, result="won",
         arb_group_id="late", bet_type="arb_anchor", placed_at=late)
    _bet(db_session, provider_id="pinnacle", event_id=eid, market="1x2",
         outcome="away", odds=2.05, stake=512.0, payout=0.0, result="lost",
         arb_group_id="late", bet_type="arb_counter", placed_at=late)
    _bet(db_session, provider_id="lodur", event_id=eid, market="1x2",
         outcome="home", odds=2.10, stake=500.0, payout=0.0, result="lost",
         arb_group_id="early", bet_type="arb_anchor", placed_at=early)
    _bet(db_session, provider_id="pinnacle", event_id=eid, market="1x2",
         outcome="away", odds=2.05, stake=512.0, payout=1050.0, result="won",
         arb_group_id="early", bet_type="arb_counter", placed_at=early)
    r = client.get("/api/bets/bonus-arbs?window=30d")
    daily = {d["date"]: d for d in r.json()["daily"]}
    assert daily["2026-05-26"]["arbs"] == 1
    assert daily["2026-05-27"]["arbs"] == 1


def test_window_today_vs_week_vs_30d(client, db_session, monkeypatch):
    """?window= controls groups[] selection: today < week < 30d."""
    import src.api.routes.bonus_arbs as mod
    # Freeze "now" to a known Stockholm time so the test is deterministic.
    fixed_now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(mod, "_now_utc", lambda: fixed_now)

    eid = "soccer:laliga:rma-vs-bar:2026-05-27"
    today = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    this_week = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc).replace(tzinfo=None)  # Monday
    last_month = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    for at, gid in [(today, "g_today"), (this_week, "g_week"), (last_month, "g_month")]:
        _bet(db_session, provider_id="betinia", event_id=eid, market="1x2",
             outcome="home", odds=2.10, stake=500.0, result="pending",
             arb_group_id=gid, bet_type="arb_anchor", placed_at=at)

    assert len(client.get("/api/bets/bonus-arbs?window=today").json()["groups"]) == 1
    assert len(client.get("/api/bets/bonus-arbs?window=week").json()["groups"]) == 2
    assert len(client.get("/api/bets/bonus-arbs?window=30d").json()["groups"]) == 3
```

- [ ] **Step 3: Run tests to verify they all fail with ImportError or 404**

Run: `cd backend && pytest tests/api/test_bonus_arbs.py -v`

Expected: all tests fail. The first failure should be `ImportError` or a 404 (the endpoint doesn't exist yet). Do NOT proceed if tests pass — that means the endpoint is already implemented and the rest of this plan is redundant.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_bonus_arbs.py
git commit -m "test(api): failing tests for /api/bets/bonus-arbs

Covers window bucketing (Stockholm calendar), arb-pair construction,
SEK conversion across mixed-currency legs, unpaired anchors, bonus
anchors, and non-soft anchor exclusion."
```

---

## Task 3: Implement the `/api/bets/bonus-arbs` endpoint

**Files:**
- Create: `backend/src/api/routes/bonus_arbs.py`

- [ ] **Step 1: Create the route module**

Create `backend/src/api/routes/bonus_arbs.py`:

```python
"""Bonus-Arb Tracker endpoint.

Read-only audit view for the user's bonus-extraction experiment across
{lodur, betinia, swiper}. Groups anchor legs at those providers with their
matched sharp counter (via bets.arb_group_id) and returns realized-vs-displayed
yield per arb, summary aggregates for today / this week / last 30 days, and
30 calendar days of P&L for a bar chart.

Day/week boundaries computed in Europe/Stockholm to match how the user reads
"my Tuesday arbs". All monetary values returned in SEK (USD/USDC * 10.50).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db.models import Bet, Event
from ...repositories import ProfileRepo
from ..deps import get_db

router = APIRouter(prefix="/api/bets/bonus-arbs", tags=["bets"])

SOFT_PROVIDERS = {"lodur", "betinia", "swiper"}
SEK_PER = {"USD": 10.50, "USDC": 10.50, "SEK": 1.0}
STK = ZoneInfo("Europe/Stockholm")
DAILY_HISTORY_DAYS = 30


def _now_utc() -> datetime:
    """Indirection so tests can freeze 'now'."""
    return datetime.now(timezone.utc)


def _to_sek(amount: float | None, currency: str) -> float | None:
    if amount is None:
        return None
    return round(amount * SEK_PER.get(currency or "SEK", 1.0), 2)


def _window_bounds(window: str, now_utc: datetime) -> tuple[datetime, datetime]:
    """Return (since_utc, until_utc) bounds for the requested window.

    'today' = since 00:00 Stockholm today
    'week'  = since Monday 00:00 Stockholm of this week
    '30d'   = last 30 calendar days (since 00:00 Stockholm 29 days ago)
    """
    now_stk = now_utc.astimezone(STK)
    if window == "today":
        start_stk = now_stk.replace(hour=0, minute=0, second=0, microsecond=0)
    elif window == "week":
        start_stk = now_stk.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now_stk.weekday())
    else:  # "30d"
        start_stk = (now_stk.replace(hour=0, minute=0, second=0, microsecond=0)
                     - timedelta(days=DAILY_HISTORY_DAYS - 1))
    return start_stk.astimezone(timezone.utc), now_utc


def _placed_at_stk_date(b: Bet) -> date:
    """Calendar date in Europe/Stockholm of a bet's placed_at.

    Bet.placed_at is stored as naive UTC (TIMESTAMP WITHOUT TIME ZONE),
    so we re-attach UTC before converting.
    """
    return b.placed_at.replace(tzinfo=timezone.utc).astimezone(STK).date()


def _leg_dict(b: Bet) -> dict:
    return {
        "id": b.id,
        "provider_id": b.provider_id,
        "market": b.market,
        "outcome": b.outcome,
        "point": b.point,
        "odds": b.odds,
        "stake_sek": _to_sek(b.stake, b.currency or "SEK"),
        "stake_native": b.stake,
        "currency": b.currency or "SEK",
        "payout_sek": _to_sek(b.payout, b.currency or "SEK"),
        "profit_sek": _to_sek(b.profit, b.currency or "SEK"),
        "result": b.result,
        "is_bonus": bool(b.is_bonus),
        "fair_odds_at_placement": b.fair_odds_at_placement,
        "clv_pct": b.clv_pct,
        "provider_clv_pct": b.provider_clv_pct,
    }


def _event_dict(ev: Event | None) -> dict | None:
    if ev is None:
        return None
    return {
        "id": ev.id,
        "home_team": ev.home_team,
        "away_team": ev.away_team,
        "display_home": ev.display_home,
        "display_away": ev.display_away,
        "sport": ev.sport,
        "league": ev.league,
        "start_time": ev.start_time.isoformat() + "Z" if ev.start_time else None,
    }


def _arb_status(anchor: Bet, counter: Bet | None) -> str:
    if counter is None:
        return "partial"
    settled_states = {"won", "lost", "void"}
    a_settled = anchor.result in settled_states
    c_settled = counter.result in settled_states
    if a_settled and c_settled:
        return "settled"
    if not a_settled and not c_settled:
        return "pending"
    return "partial"


def _displayed_yield_pct(anchor: Bet, counter: Bet | None) -> float | None:
    """Theoretical arb yield at placement. None for bonus or unpaired anchor."""
    if counter is None or anchor.is_bonus:
        return None
    if anchor.odds <= 1.0 or counter.odds <= 1.0:
        return None
    inv_sum = (1.0 / anchor.odds) + (1.0 / counter.odds)
    if inv_sum <= 0:
        return None
    return round((1.0 / inv_sum - 1.0) * 100, 3)


def _build_group(anchor: Bet, counter: Bet | None, counter_share: float,
                 events: dict[str, Event]) -> dict:
    """Build one group dict. counter_share is 1.0 unless multiple anchors
    share the same counter (sister-skin replay), in which case the counter's
    stake/payout/profit are divided across anchors so aggregate totals match.
    """
    anchor_leg = _leg_dict(anchor)
    counter_leg = _leg_dict(counter) if counter is not None else None

    if counter_leg is not None and counter_share != 1.0:
        for k in ("stake_sek", "stake_native", "payout_sek", "profit_sek"):
            if counter_leg[k] is not None:
                counter_leg[k] = round(counter_leg[k] * counter_share, 2)

    status = _arb_status(anchor, counter)
    total_stake_sek = anchor_leg["stake_sek"] or 0.0
    if counter_leg is not None:
        total_stake_sek += counter_leg["stake_sek"] or 0.0
    total_stake_sek = round(total_stake_sek, 2)

    realized_yield_pct: float | None = None
    pnl_sek: float | None = None
    if status == "settled":
        pnl_sek = round((anchor_leg["profit_sek"] or 0.0)
                        + (counter_leg["profit_sek"] if counter_leg else 0.0), 2)
        if total_stake_sek > 0:
            realized_yield_pct = round(pnl_sek / total_stake_sek * 100, 3)

    ev = events.get(anchor.event_id) if anchor.event_id else None
    return {
        "arb_group_id": anchor.arb_group_id,
        "status": status,
        "placed_at": anchor.placed_at.replace(tzinfo=timezone.utc).astimezone(STK).isoformat(),
        "event": _event_dict(ev),
        "boost_event": anchor.boost_event,
        "anchor": anchor_leg,
        "counter": counter_leg,
        "total_stake_sek": total_stake_sek,
        "displayed_yield_pct": _displayed_yield_pct(anchor, counter),
        "realized_yield_pct": realized_yield_pct,
        "pnl_sek": pnl_sek,
    }


def _summarize(groups: list[dict]) -> dict:
    n = len(groups)
    settled = [g for g in groups if g["status"] == "settled"]
    disp = [g["displayed_yield_pct"] for g in groups if g["displayed_yield_pct"] is not None]
    real = [g["realized_yield_pct"] for g in settled if g["realized_yield_pct"] is not None]
    anchor_clv = [g["anchor"]["clv_pct"] for g in groups if g["anchor"]["clv_pct"] is not None]
    counter_clv = [g["counter"]["clv_pct"] for g in groups
                   if g["counter"] is not None and g["counter"]["clv_pct"] is not None]
    counter_prov_clv = [g["counter"]["provider_clv_pct"] for g in groups
                       if g["counter"] is not None and g["counter"]["provider_clv_pct"] is not None]
    return {
        "arbs": n,
        "settled": len(settled),
        "stake_sek": round(sum(g["total_stake_sek"] for g in groups), 2),
        "pnl_sek": round(sum(g["pnl_sek"] or 0.0 for g in settled), 2),
        "avg_displayed_pct": round(sum(disp) / len(disp), 3) if disp else None,
        "avg_realized_pct": round(sum(real) / len(real), 3) if real else None,
        "anchor_clv_avg": round(sum(anchor_clv) / len(anchor_clv), 2) if anchor_clv else None,
        "counter_clv_avg": round(sum(counter_clv) / len(counter_clv), 2) if counter_clv else None,
        "counter_provider_clv_avg": (round(sum(counter_prov_clv) / len(counter_prov_clv), 2)
                                     if counter_prov_clv else None),
    }


def _daily_buckets(groups_30d: list[dict], now_utc: datetime) -> list[dict]:
    """Bucket 30d of groups by Stockholm calendar date, oldest first.

    Zero-fills missing days so the bar chart has stable width.
    """
    today_stk = now_utc.astimezone(STK).date()
    dates = [today_stk - timedelta(days=i) for i in range(DAILY_HISTORY_DAYS - 1, -1, -1)]
    by_day: dict[date, list[dict]] = defaultdict(list)
    for g in groups_30d:
        # placed_at in groups is the Stockholm-tz isoformat string; parse back.
        d = datetime.fromisoformat(g["placed_at"]).date()
        by_day[d].append(g)
    out = []
    for d in dates:
        items = by_day.get(d, [])
        settled = [g for g in items if g["status"] == "settled"]
        disp = [g["displayed_yield_pct"] for g in items if g["displayed_yield_pct"] is not None]
        real = [g["realized_yield_pct"] for g in settled if g["realized_yield_pct"] is not None]
        out.append({
            "date": d.isoformat(),
            "arbs": len(items),
            "settled": len(settled),
            "stake_sek": round(sum(g["total_stake_sek"] for g in items), 2),
            "pnl_sek": round(sum(g["pnl_sek"] or 0.0 for g in settled), 2),
            "avg_displayed_pct": round(sum(disp) / len(disp), 3) if disp else None,
            "avg_realized_pct": round(sum(real) / len(real), 3) if real else None,
        })
    return out


@router.get("")
def get_bonus_arbs(
    window: Literal["today", "week", "30d"] = "week",
    db: Session = Depends(get_db),
):
    """Paired anchor+counter view for arbs placed at lodur/betinia/swiper."""
    profile = ProfileRepo(db).get_active()
    if profile is None:
        return {
            "window": window, "since": None, "until": None,
            "summary": {"today": _summarize([]), "week": _summarize([]),
                       "thirty": _summarize([])},
            "daily": _daily_buckets([], _now_utc()),
            "groups": [],
        }

    now = _now_utc()
    # Always fetch 30 days for daily buckets + the "thirty" summary; filter
    # for groups[] / window-summary in-memory afterwards.
    since_30d, until = _window_bounds("30d", now)
    since_30d_naive = since_30d.replace(tzinfo=None)
    until_naive = until.replace(tzinfo=None)

    anchors: list[Bet] = (
        db.query(Bet)
        .filter(
            Bet.profile_id == profile.id,
            Bet.provider_id.in_(SOFT_PROVIDERS),
            Bet.placed_at >= since_30d_naive,
            Bet.placed_at < until_naive,
        )
        .order_by(Bet.placed_at.desc())
        .all()
    )

    # Counter legs by arb_group_id. Exclude anchors themselves.
    arb_gids = {a.arb_group_id for a in anchors if a.arb_group_id}
    counters_by_gid: dict[str, list[Bet]] = defaultdict(list)
    anchors_by_gid: dict[str, list[Bet]] = defaultdict(list)
    for a in anchors:
        if a.arb_group_id:
            anchors_by_gid[a.arb_group_id].append(a)
    if arb_gids:
        rows = (
            db.query(Bet)
            .filter(
                Bet.profile_id == profile.id,
                Bet.arb_group_id.in_(arb_gids),
                Bet.provider_id.notin_(SOFT_PROVIDERS),
            )
            .all()
        )
        for c in rows:
            counters_by_gid[c.arb_group_id].append(c)

    # Hydrate events.
    event_ids = {a.event_id for a in anchors if a.event_id}
    events: dict[str, Event] = {}
    if event_ids:
        for ev in db.query(Event).filter(Event.id.in_(event_ids)).all():
            events[ev.id] = ev

    # Build groups. Sister-skin replay: if N anchors share a gid with 1 counter,
    # each anchor renders as its own group with counter stake/profit/payout
    # divided by N so aggregate totals match.
    groups_30d: list[dict] = []
    for a in anchors:
        counter = None
        share = 1.0
        if a.arb_group_id:
            cands = counters_by_gid.get(a.arb_group_id, [])
            if cands:
                counter = cands[0]  # one counter per arb_group_id is the norm
                sibling_anchors = anchors_by_gid.get(a.arb_group_id, [])
                if len(sibling_anchors) > 1:
                    share = 1.0 / len(sibling_anchors)
        groups_30d.append(_build_group(a, counter, share, events))

    # Window-filter for groups[].
    since_window, _ = _window_bounds(window, now)
    since_window_iso = since_window.astimezone(STK).isoformat()

    def in_window(g: dict, window_name: str) -> bool:
        ws_utc, _ = _window_bounds(window_name, now)
        return datetime.fromisoformat(g["placed_at"]) >= ws_utc.astimezone(STK)

    groups_in_window = [g for g in groups_30d if in_window(g, window)]

    return {
        "window": window,
        "since": since_window.isoformat(),
        "until": until.isoformat(),
        "summary": {
            "today": _summarize([g for g in groups_30d if in_window(g, "today")]),
            "week": _summarize([g for g in groups_30d if in_window(g, "week")]),
            "thirty": _summarize(groups_30d),
        },
        "daily": _daily_buckets(groups_30d, now),
        "groups": groups_in_window,
    }
```

- [ ] **Step 2: Register the router export**

Edit `backend/src/api/routes/__init__.py`. Add `bonus_arbs_router` import next to the other routers (alphabetical-ish):

```python
from .bets import router as bets_router
from .bonus_arbs import router as bonus_arbs_router
from .chat import router as chat_router
```

And add to `__all__`:

```python
__all__ = [
    ...
    "bets_router",
    "bonus_arbs_router",
    "chat_router",
    ...
]
```

- [ ] **Step 3: Mount the router on the FastAPI app**

Edit `backend/src/api/__init__.py`. In the `from .routes import (...)` block (~line 34), add `bonus_arbs_router` next to `bets_router`. Then in the `app.include_router(...)` section (~line 597), add:

```python
app.include_router(bets_router)
app.include_router(bonus_arbs_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/api/test_bonus_arbs.py -v`

Expected: all 8 tests pass.

If `test_window_today_vs_week_vs_30d` fails because the monkeypatch target doesn't exist, double-check `_now_utc` is defined at module scope in `bonus_arbs.py`. If `test_stockholm_day_boundary_buckets` fails, inspect whether `_placed_at_stk_date` correctly re-attaches UTC before converting.

- [ ] **Step 5: Run the broader API test suite to catch regressions**

Run: `cd backend && pytest tests/api/ -v`

Expected: all tests pass. No existing test should be broken — the only edit to existing code is `bets.py` adding one field to the response dict.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes/bonus_arbs.py backend/src/api/routes/__init__.py backend/src/api/__init__.py
git commit -m "feat(api): GET /api/bets/bonus-arbs endpoint

Paired anchor+counter view for arbs at lodur/betinia/swiper, with
today/week/30d summary, 30-day daily P&L buckets, and Europe/Stockholm
calendar-day bucketing. Read-only — no DB migration."
```

---

## Task 4: Extend frontend `Bet` type and add Bonus-Arb types

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Add `arb_group_id` to the `Bet` interface**

In `frontend/src/types/index.ts`, locate the `Bet` interface (line ~207). Add after `bet_type?: string | null;`:

```typescript
  bet_type?: string | null;  // "value", "arb", "reverse", "polymarket", "boost"
  arb_group_id?: string | null;  // Set by arb_runner / correlate_arbs to link anchor + counter legs
}
```

- [ ] **Step 2: Add Bonus-Arb interfaces**

In the same file, after the `Bet` interface, add:

```typescript
// Bonus-Arb Tracker
export interface BonusArbLeg {
  id: number;
  provider_id: string;
  market: string | null;
  outcome: string | null;
  point: number | null;
  odds: number;
  stake_sek: number;
  stake_native: number;
  currency: string;
  payout_sek: number | null;
  profit_sek: number | null;
  result: 'won' | 'lost' | 'void' | 'pending';
  is_bonus: boolean;
  fair_odds_at_placement: number | null;
  clv_pct: number | null;
  provider_clv_pct: number | null;
}

export interface BonusArbGroupEvent {
  id: string;
  home_team: string | null;
  away_team: string | null;
  display_home: string | null;
  display_away: string | null;
  sport: string | null;
  league: string | null;
  start_time: string | null;
}

export interface BonusArbGroup {
  arb_group_id: string | null;
  status: 'settled' | 'pending' | 'partial';
  placed_at: string;
  event: BonusArbGroupEvent | null;
  boost_event: string | null;
  anchor: BonusArbLeg;
  counter: BonusArbLeg | null;
  total_stake_sek: number;
  displayed_yield_pct: number | null;
  realized_yield_pct: number | null;
  pnl_sek: number | null;
}

export interface BonusArbSummary {
  arbs: number;
  settled: number;
  stake_sek: number;
  pnl_sek: number;
  avg_displayed_pct: number | null;
  avg_realized_pct: number | null;
  anchor_clv_avg: number | null;
  counter_clv_avg: number | null;
  counter_provider_clv_avg: number | null;
}

export interface BonusArbDaily {
  date: string;
  arbs: number;
  settled: number;
  stake_sek: number;
  pnl_sek: number;
  avg_displayed_pct: number | null;
  avg_realized_pct: number | null;
}

export interface BonusArbResponse {
  window: 'today' | 'week' | '30d';
  since: string | null;
  until: string | null;
  summary: {
    today: BonusArbSummary;
    week: BonusArbSummary;
    thirty: BonusArbSummary;
  };
  daily: BonusArbDaily[];
  groups: BonusArbGroup[];
}
```

- [ ] **Step 3: Type-check the frontend**

Run: `cd frontend && npm run lint`

Expected: zero new lint errors. If existing lint errors exist in unrelated files, that's fine — just confirm none in `types/index.ts`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat(types): BonusArb types + arb_group_id on Bet

Frontend type extensions for the Bonus-Arb Tracker section."
```

---

## Task 5: Add `getBonusArbs` API method

**Files:**
- Modify: `frontend/src/services/api/bets.ts`

- [ ] **Step 1: Add the method to `betsApi`**

In `frontend/src/services/api/bets.ts`, add the import for `BonusArbResponse`:

```typescript
import type { Bet, BonusArbResponse } from '@/types';
```

And add `getBonusArbs` after `getAnalytics` (before the closing `};` of `betsApi`):

```typescript
  async getBonusArbs(
    window: 'today' | 'week' | '30d' = 'week'
  ): Promise<BonusArbResponse> {
    const params = new URLSearchParams();
    params.set('window', window);
    return fetchJson(`/bets/bonus-arbs?${params}`);
  },
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run lint`

Expected: zero new lint errors in `bets.ts`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api/bets.ts
git commit -m "feat(api-client): getBonusArbs method"
```

---

## Task 6: Build the `BonusArbTracker` component

**Files:**
- Create: `frontend/src/components/BonusArbTracker.tsx`

- [ ] **Step 1: Inspect existing component patterns to match style**

Read `frontend/src/pages/StatsPage.tsx` lines 50-60 (CHART constants), lines 81-105 (polyChart helper), lines 504-655 (Stats summary tile layout) for visual consistency. The new component should match the Stats card grid pattern (4-tile rows, `bg-panel2`, `border border-border`, `text-success`/`text-error` for green/red).

Read `frontend/src/components/ProviderName.tsx` to confirm import signature.

- [ ] **Step 2: Create the component**

Create `frontend/src/components/BonusArbTracker.tsx`:

```typescript
import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { ProviderName } from '@/components/ProviderName';
import { displayTeamName } from '@/utils/formatters';
import type { BonusArbGroup, BonusArbSummary, BonusArbDaily } from '@/types';

type Window = 'today' | 'week' | '30d';

const SOFT_PROVIDERS = ['lodur', 'betinia', 'swiper'] as const;

function fmtSek(v: number | null | undefined, signed = false): string {
  if (v == null) return '-';
  const sign = signed && v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(0)} kr`;
}

function fmtPct(v: number | null | undefined, signed = true): string {
  if (v == null) return '-';
  const sign = signed && v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function statusColor(status: BonusArbGroup['status']): string {
  if (status === 'settled') return 'text-text';
  if (status === 'partial') return 'text-warning';
  return 'text-muted';
}

function resultPill(result: string): { text: string; cls: string } {
  switch (result) {
    case 'won': return { text: 'W', cls: 'bg-success/15 text-success' };
    case 'lost': return { text: 'L', cls: 'bg-error/15 text-error' };
    case 'void': return { text: 'V', cls: 'bg-muted/15 text-muted' };
    default: return { text: '…', cls: 'bg-accent/15 text-accent' };
  }
}

function SummaryTiles({ label, s }: { label: string; s: BonusArbSummary }) {
  const roi = s.settled > 0 && s.stake_sek > 0
    ? (s.pnl_sek / s.stake_sek) * 100
    : null;
  return (
    <div>
      <div className="text-[10px] text-muted uppercase tracking-wider mb-1">{label}</div>
      <div className="grid grid-cols-4 gap-px bg-border border border-border">
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Arbs</div>
          <div className="text-text text-lg font-semibold">{s.arbs}</div>
          <div className="text-[10px] text-muted">{s.settled} settled</div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Stake</div>
          <div className="text-text text-lg font-semibold">{fmtSek(s.stake_sek)}</div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">P&L</div>
          <div className={`text-lg font-semibold ${s.pnl_sek >= 0 ? 'text-success' : 'text-error'}`}>
            {fmtSek(s.pnl_sek, true)}
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">ROI</div>
          <div className={`text-lg font-semibold ${roi == null ? 'text-muted' : roi >= 0 ? 'text-success' : 'text-error'}`}>
            {fmtPct(roi)}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 px-3 py-1.5 bg-panel2 border border-border border-t-0 text-[10px] text-muted">
        <span>displayed <span className="text-text">{fmtPct(s.avg_displayed_pct)}</span></span>
        <span>realized <span className="text-text">{fmtPct(s.avg_realized_pct)}</span></span>
        <span>anchor CLV <span className="text-text">{fmtPct(s.anchor_clv_avg)}</span></span>
        <span>counter CLV <span className="text-text">{fmtPct(s.counter_clv_avg)}</span></span>
        {s.counter_provider_clv_avg != null && (
          <span>same-mkt CLV <span className="text-text">{fmtPct(s.counter_provider_clv_avg)}</span></span>
        )}
      </div>
    </div>
  );
}

function DailyBars({ daily }: { daily: BonusArbDaily[] }) {
  const maxAbs = useMemo(
    () => Math.max(1, ...daily.map(d => Math.abs(d.pnl_sek))),
    [daily],
  );
  const W = 600, H = 80, PL = 8, PR = 8, PT = 8, PB = 18;
  const barW = (W - PL - PR) / daily.length;
  const zeroY = PT + (H - PT - PB) / 2;
  const yScale = (H - PT - PB) / 2 / maxAbs;
  const totalPnl = daily.reduce((s, d) => s + d.pnl_sek, 0);

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">
          Daily P&L (last 30 days)
        </span>
        <span className={`text-sm font-semibold ${totalPnl >= 0 ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
          {fmtSek(totalPnl, true)} total
        </span>
      </div>
      <div className="relative" style={{ paddingBottom: '20%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#484f58" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
          {daily.map((d, i) => {
            const x = PL + i * barW + 0.5;
            const h = Math.abs(d.pnl_sek) * yScale;
            const y = d.pnl_sek >= 0 ? zeroY - h : zeroY;
            const color = d.pnl_sek > 0 ? '#3fb950' : d.pnl_sek < 0 ? '#f85149' : '#30363d';
            return (
              <rect key={d.date} x={x} y={y} width={Math.max(0.5, barW - 1)} height={Math.max(0.5, h)}
                    fill={color} vectorEffect="non-scaling-stroke">
                <title>{`${d.date}: ${d.arbs} arbs (${d.settled} settled), ${fmtSek(d.stake_sek)} staked, ${fmtSek(d.pnl_sek, true)} P&L`}</title>
              </rect>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function GroupRow({ g, isExpanded, onToggle }: { g: BonusArbGroup; isExpanded: boolean; onToggle: () => void }) {
  const time = new Date(g.placed_at).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  const eventName = g.event
    ? `${displayTeamName(g.event.home_team ?? '', g.event.display_home)} vs ${displayTeamName(g.event.away_team ?? '', g.event.display_away)}`
    : (g.boost_event ?? '-');
  const sport = g.event?.sport ?? '';

  const aPill = resultPill(g.anchor.result);
  const cPill = g.counter ? resultPill(g.counter.result) : null;
  const yieldColor = g.realized_yield_pct == null
    ? 'text-muted'
    : g.realized_yield_pct >= 0 ? 'text-success' : 'text-error';

  return (
    <>
      <tr className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`} onClick={onToggle}>
        <td className="text-muted text-[11px] whitespace-nowrap">{time}</td>
        <td className="text-text text-sm">
          <div>{eventName}</div>
          {sport && <div className="text-muted2 text-[10px]">{sport}</div>}
        </td>
        <td className="text-text text-sm">
          <div className="flex items-center gap-1.5">
            <ProviderName name={g.anchor.provider_id} />
            <span className="font-medium">{g.anchor.odds.toFixed(2)}</span>
            <span className={`text-[10px] px-1 ${aPill.cls}`}>{aPill.text}</span>
            {g.anchor.is_bonus && <span className="text-[9px] px-1 bg-warning/15 text-warning">BONUS</span>}
          </div>
          <div className={`text-[10px] ${g.anchor.profit_sek == null ? 'text-muted' : g.anchor.profit_sek >= 0 ? 'text-success' : 'text-error'}`}>
            {fmtSek(g.anchor.profit_sek, true)}
          </div>
        </td>
        <td className="text-text text-sm">
          {g.counter ? (
            <>
              <div className="flex items-center gap-1.5">
                <ProviderName name={g.counter.provider_id} />
                <span className="font-medium">{g.counter.odds.toFixed(2)}</span>
                <span className={`text-[10px] px-1 ${cPill!.cls}`}>{cPill!.text}</span>
              </div>
              <div className={`text-[10px] ${g.counter.profit_sek == null ? 'text-muted' : g.counter.profit_sek >= 0 ? 'text-success' : 'text-error'}`}>
                {fmtSek(g.counter.profit_sek, true)}
              </div>
            </>
          ) : (
            <span className="text-warning text-[11px]">unpaired</span>
          )}
        </td>
        <td className="text-right text-text text-sm">{fmtSek(g.total_stake_sek)}</td>
        <td className="text-right text-sm text-muted">{fmtPct(g.displayed_yield_pct)}</td>
        <td className={`text-right text-sm font-medium ${yieldColor}`}>{fmtPct(g.realized_yield_pct)}</td>
        <td className="text-right text-sm">
          <span className={(g.anchor.clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}>
            {fmtPct(g.anchor.clv_pct)}
          </span>
        </td>
        <td className="text-right text-sm">
          {g.counter && (
            <span className={(g.counter.clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}>
              {fmtPct(g.counter.clv_pct)}
            </span>
          )}
        </td>
        <td className={`text-right text-sm capitalize ${statusColor(g.status)}`}>{g.status}</td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={10} className="!p-0">
            <div className="px-3 py-2 bg-panel text-[11px] text-muted grid grid-cols-2 gap-4">
              <div>
                <div className="text-muted2 uppercase tracking-wider mb-1">Anchor</div>
                <div>Provider: <span className="text-text"><ProviderName name={g.anchor.provider_id} /></span></div>
                <div>Market: <span className="text-text">{g.anchor.market} / {g.anchor.outcome}{g.anchor.point != null ? ` ${g.anchor.point}` : ''}</span></div>
                <div>Stake: <span className="text-text">{g.anchor.stake_native.toFixed(2)} {g.anchor.currency} ({fmtSek(g.anchor.stake_sek)})</span></div>
                <div>Fair odds: <span className="text-text">{g.anchor.fair_odds_at_placement?.toFixed(3) ?? '-'}</span></div>
                <div>CLV: <span className="text-text">{fmtPct(g.anchor.clv_pct)}</span></div>
              </div>
              <div>
                <div className="text-muted2 uppercase tracking-wider mb-1">Counter</div>
                {g.counter ? (
                  <>
                    <div>Provider: <span className="text-text"><ProviderName name={g.counter.provider_id} /></span></div>
                    <div>Market: <span className="text-text">{g.counter.market} / {g.counter.outcome}{g.counter.point != null ? ` ${g.counter.point}` : ''}</span></div>
                    <div>Stake: <span className="text-text">{g.counter.stake_native.toFixed(2)} {g.counter.currency} ({fmtSek(g.counter.stake_sek)})</span></div>
                    <div>CLV (Pinnacle): <span className="text-text">{fmtPct(g.counter.clv_pct)}</span></div>
                    {g.counter.provider_clv_pct != null && (
                      <div>CLV (same-market): <span className="text-text">{fmtPct(g.counter.provider_clv_pct)}</span></div>
                    )}
                  </>
                ) : (
                  <div className="text-warning">No counter leg linked yet. Run correlate_arbs or check arb_runner placement.</div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function BonusArbTracker() {
  const [window, setWindow] = useState<Window>('week');
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['bonus-arbs', window],
    queryFn: () => api.getBonusArbs(window),
    staleTime: 30_000,
  });

  return (
    <div className="border-l-2 border-tabBets">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs text-muted uppercase tracking-wider font-semibold">
          Bonus-Arb Tracker
        </h3>
        <div className="flex items-center gap-3">
          <div className="text-[10px] text-muted2">
            providers: {SOFT_PROVIDERS.join(' · ')}
          </div>
          <div className="flex gap-1">
            {(['today', 'week', '30d'] as Window[]).map(w => (
              <button
                key={w}
                onClick={() => setWindow(w)}
                className={`px-2 py-0.5 text-[10px] rounded border ${
                  window === w
                    ? 'bg-tabBets/20 text-tabBets border-tabBets/40'
                    : 'bg-panel2 text-muted border-border hover:text-text'
                }`}
              >
                {w}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading && !data ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Loading...
        </div>
      ) : !data ? null : (
        <>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <SummaryTiles label="Today" s={data.summary.today} />
            <SummaryTiles label="This Week" s={data.summary.week} />
          </div>

          <div className="mb-3">
            <DailyBars daily={data.daily} />
          </div>

          {data.groups.length === 0 ? (
            <div className="text-muted text-sm py-6 text-center border border-border bg-panel">
              No arbs in this window yet.
            </div>
          ) : (
            <div className="border border-border">
              <table className="sq">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Event</th>
                    <th>Anchor</th>
                    <th>Counter</th>
                    <th className="text-right">Stake</th>
                    <th className="text-right">Displ.</th>
                    <th className="text-right">Realized</th>
                    <th className="text-right">Anchor CLV</th>
                    <th className="text-right">Counter CLV</th>
                    <th className="text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.groups.map(g => {
                    const key = `${g.arb_group_id ?? g.anchor.id}`;
                    return (
                      <GroupRow
                        key={key}
                        g={g}
                        isExpanded={expanded === key}
                        onToggle={() => setExpanded(expanded === key ? null : key)}
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Lint and type-check**

Run: `cd frontend && npm run lint`

Expected: zero new lint errors in `BonusArbTracker.tsx`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/BonusArbTracker.tsx
git commit -m "feat(stats): BonusArbTracker component

Window chips (today/week/30d), Today + This Week summary tiles, 30-day
daily P&L bar chart, paired-leg arb table with expandable per-leg detail.
Reads /api/bets/bonus-arbs."
```

---

## Task 7: Mount `BonusArbTracker` on StatsPage

**Files:**
- Modify: `frontend/src/pages/StatsPage.tsx`

- [ ] **Step 1: Add the import**

In `frontend/src/pages/StatsPage.tsx`, add to the import block near line 1-10:

```typescript
import { BonusArbTracker } from '@/components/BonusArbTracker';
```

- [ ] **Step 2: Mount the component between Charts and Realized-ROI Analytics**

Locate the closing `</div>` of the Charts row (around line 679, right after the Reverse-Value CLV conditional) and the opening of the Realized-ROI Analytics accordion (around line 682, the `{/* Realized-ROI Analytics ... */}` comment).

Between them, insert:

```tsx
      {/* Bonus-Arb Tracker — paired anchor+counter view for lodur/betinia/swiper */}
      <BonusArbTracker />
```

The final structure in that region should read:

```tsx
        {bets.some(b => b.provider === 'pinnacle' && !b.is_bonus) && (
          <div className="grid grid-cols-1 gap-[1px] bg-[#161b22] mt-[1px]">
            <CLVChart
              bets={bets.filter(b => b.provider === 'pinnacle' && !b.is_bonus)}
              title="Reverse-Value CLV (Pinnacle vs soft consensus)"
            />
          </div>
        )}
      </div>

      {/* Bonus-Arb Tracker — paired anchor+counter view for lodur/betinia/swiper */}
      <BonusArbTracker />

      {/* Realized-ROI Analytics — per-sport + per-edge-bucket breakdown */}
      <div>
```

- [ ] **Step 3: Lint**

Run: `cd frontend && npm run lint`

Expected: zero new lint errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/StatsPage.tsx
git commit -m "feat(stats): mount BonusArbTracker on StatsPage

Renders between charts row and Realized-ROI Analytics."
```

---

## Task 8: Verify end-to-end via local launcher

**Files:** none (manual verification)

- [ ] **Step 1: Start the local Betty client**

From the repo root:

```bash
.\betty.bat
```

This opens an SSH tunnel to the production API on port 18000, starts the local FastAPI on 8000, and opens the browser to the local UI.

**Note:** The backend changes are NOT live in production until deployed via `bash /opt/betty/backend/scripts/server-deploy.sh rebuild backend` from the server. Until then, the new endpoint will 404 through the SSH tunnel. To verify locally before deploying, run the backend in dev mode:

```bash
cd backend && python run_dev.py
```

…and edit `local/proxy.py` temporarily to point at `http://localhost:8000` instead of the tunnel, OR run pytest as the primary verification surface (already done in Task 3) and defer browser verification until after a coordinated deploy.

- [ ] **Step 2: Navigate to Stats tab and verify the section renders**

In the browser, click the Stats tab. Verify:
- New section appears between the charts row and the "Realized vs Displayed Edge" accordion.
- Window chips (today / week / 30d) are clickable; clicking changes which arbs render in the table.
- "Today" and "This Week" tiles populate (zeros are fine if no arbs placed yet).
- Daily bar chart renders 30 bars (may all be zero-height).
- If the user has any arbs at lodur/betinia/swiper in the last 30 days: a paired row should render. Expand it to see leg detail.

- [ ] **Step 3: Test edge cases live (if data available)**

Open the browser DevTools network tab and verify the request hits `/api/bets/bonus-arbs?window=week` and returns 200. If a row shows `unpaired`, expand it — confirm it explains the missing counter clearly. If the user has bonus arbs, confirm `BONUS` chip appears and `displayed_yield_pct` shows `-`.

- [ ] **Step 4: Final commit (if any manual fixes were needed)**

If verification surfaced any styling or copy fixes:

```bash
git add -p frontend/src/components/BonusArbTracker.tsx
git commit -m "fix(stats): BonusArbTracker polish from live verification"
```

Otherwise, no commit needed — proceed to deploy/PR.

---

## Self-Review Notes

**Spec coverage (each spec section → task):**
- §What we're building → Tasks 1, 3, 6, 7
- §Constants (SOFT_PROVIDERS, SEK_PER, TZ, DAILY_HISTORY_DAYS) → Task 3 step 1
- §Data model — endpoint response → Task 3 step 1 (`_build_group`, `_leg_dict`, `_event_dict`)
- §Calculations (displayed/realized yield, status, summary aggregates, daily buckets) → Task 3 step 1 (`_displayed_yield_pct`, `_arb_status`, `_summarize`, `_daily_buckets`)
- §Window selection rule → Task 3 step 1 (`get_bonus_arbs` always computes all three summaries; `groups[]` filtered by window)
- §Group-selection query (SQL pseudocode) → Task 3 step 1 (SQLAlchemy two-query approach: anchors then counters by gid)
- §File-level plan → Tasks 1, 3, 4, 5, 6, 7 (paths match spec; `BonusArbTracker.tsx` location adjusted from `pages/components/` to `components/` to match actual repo layout)
- §Edge cases (unpaired, multi-anchor, bonus, voided, mixed-currency, empty window, midnight boundary, DST) → Task 2 (tests) + Task 3 step 1 (implementation)
- §Testing (8 scenarios) → Task 2 (all 8 written as pytest cases)
- §Out of scope — naturally absent from plan ✓

**Type consistency check:**
- `BonusArbLeg.stake_sek` (Task 4) matches `_leg_dict` returning `stake_sek` (Task 3) ✓
- `BonusArbGroup.arb_group_id` is `string | null` (Task 4) matches backend returning `anchor.arb_group_id` which can be None (Task 3) ✓
- `summary.thirty` key (Task 3) matches `BonusArbResponse.summary.thirty` (Task 4) ✓
- `getBonusArbs(window)` signature (Task 5) matches component call (Task 6) ✓
- `BonusArbDaily.date` is ISO string `"2026-05-27"` (Task 4) matches `_daily_buckets` returning `d.isoformat()` (Task 3) ✓
- Result enum `'won' | 'lost' | 'void' | 'pending'` (Task 4 BonusArbLeg) matches the strings the backend returns and matches existing `Bet.result` enum ✓

**Placeholder scan:** no "TBD", "implement later", or empty step references. Every code step has the actual code. ✓

**One adjustment from spec:** Component path is `frontend/src/components/BonusArbTracker.tsx`, not `frontend/src/pages/components/BonusArbTracker.tsx` — that subdirectory doesn't exist in this repo. Documented in the File Structure section above.
