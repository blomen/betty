# Arb Recording Dedup + Phantom Cleanup + Leg Linkage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Polymarket `arb_counter` duplication death spiral, clean the 42 phantom rows it produced, and link the two legs of each arb via a new `arb_group_id`.

**Architecture:** The position recorders (`polymarket_api`, `kalshi_api`) dedup against pending-only bets, so a settled position lingering in the provider feed re-inserts every 5-minute sync. Fix: dedup against *all* recorded conditionIds. Separately, add a `bets.arb_group_id` column and a correlation pass that pairs ungrouped soft-anchor / Polymarket-counter legs by event + complementary outcome + time window.

**Tech Stack:** Python, FastAPI, SQLAlchemy, PostgreSQL (prod) / SQLite (tests), pytest.

**Spec:** [docs/superpowers/specs/2026-05-20-arb-recording-dedup-linkage-design.md](../specs/2026-05-20-arb-recording-dedup-linkage-design.md)

---

## File Structure

**Backend (server — needs rebuild deploy):**
- `backend/src/db/models.py` — add `arb_group_id` column to `Bet` + migration entry
- `backend/src/repositories/bet_repo.py` — add `recorded_provider_bet_ids()` + `recorded_signatures()` (Part 4)
- `backend/src/services/arb_correlation.py` — **new** — `correlate_arbs()` pass
- `backend/src/api/routes/bets.py` — add `GET /recorded-ids` + `GET /recorded-signatures` (Part 4) + `POST /correlate-arbs`

**Local client (arnold — needs `arnold.bat` restart):**
- `arnold/mirror/recorders/polymarket_api.py` — `sync()` dedup against all recorded ids
- `arnold/mirror/recorders/kalshi_api.py` — same
- `arnold/mirror/router.py` — wire a fail-closed `fetch_known_ids` callable
- `arnold/mirror/recorders/auto_poller.py` — call `correlate-arbs` after each cycle
- `arnold/mirror/pending_loop.py` — `_record_unknown_open_bets` dedup against all recorded bets (Part 4)
- `arnold/mirror/provider_runner.py` — same (sibling copy) (Part 4)

**Tests:**
- `backend/tests/test_bet_repo.py` — extend
- `backend/tests/test_arb_correlation.py` — **new**
- `arnold/tests/test_recorder_dedup.py` — **new**

---

## Task 1: Schema — add `bets.arb_group_id`

**Files:**
- Modify: `backend/src/db/models.py` (Bet class ~line 290; `_run_pg_migrations` additions list ~line 2389)

- [ ] **Step 1: Add the column to the `Bet` ORM model**

In `backend/src/db/models.py`, in `class Bet`, immediately after the `provider_bet_id` column (line 290) add:

```python
    # arb_group_id: shared id across the two+ legs of one arbitrage position
    # (soft-book anchor + Polymarket/Kalshi counter). NULL until the
    # arb_correlation pass pairs the legs. See 2026-05-20 dedup+linkage spec.
    arb_group_id = Column(String, nullable=True, index=True)
```

- [ ] **Step 2: Add the Postgres migration entry**

In `_run_pg_migrations`, in the `additions` list, after the `("broker_trades", "final_stop_price", ...)` entry add:

```python
        # 2026-05-20 — arb leg linkage. Pairs the soft anchor + Polymarket
        # counter of one arbitrage so per-arb guaranteed profit is verifiable.
        ("bets", "arb_group_id", "VARCHAR"),
```

- [ ] **Step 3: Verify the model imports cleanly**

Run: `cd backend && python -c "from src.db.models import Bet; print(Bet.arb_group_id)"`
Expected: prints a Column object, no error.

- [ ] **Step 4: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat(bets): add arb_group_id column for arb leg linkage"
```

---

## Task 2: `BetRepo.recorded_provider_bet_ids()`

**Files:**
- Modify: `backend/src/repositories/bet_repo.py`
- Test: `backend/tests/test_bet_repo.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_bet_repo.py`:

```python
def test_recorded_provider_bet_ids_includes_settled(db):
    """Dedup must see settled bets, not just pending — otherwise a settled
    Polymarket position re-inserts every sync (the duplication death spiral)."""
    repo = BetRepo(db)
    db.add(Bet(profile_id=1, provider_id="polymarket", odds=2.0, stake=10.0,
               result="lost", provider_bet_id="0xCID_SETTLED"))
    db.add(Bet(profile_id=1, provider_id="polymarket", odds=2.0, stake=10.0,
               result="pending", provider_bet_id="0xCID_PENDING"))
    db.add(Bet(profile_id=1, provider_id="polymarket", odds=2.0, stake=10.0,
               result="lost", provider_bet_id=None))
    db.add(Bet(profile_id=1, provider_id="betinia", odds=2.0, stake=10.0,
               result="lost", provider_bet_id="OTHER_PROVIDER"))
    db.commit()

    ids = repo.recorded_provider_bet_ids(profile_id=1, provider_id="polymarket")
    assert ids == {"0xCID_SETTLED", "0xCID_PENDING"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_bet_repo.py::test_recorded_provider_bet_ids_includes_settled -v`
Expected: FAIL with `AttributeError: 'BetRepo' object has no attribute 'recorded_provider_bet_ids'`

- [ ] **Step 3: Implement the method**

In `backend/src/repositories/bet_repo.py`, add to `class BetRepo` (after `get_pending_for_provider`):

```python
    def recorded_provider_bet_ids(self, profile_id: int, provider_id: str) -> set[str]:
        """All non-null provider_bet_id values for a provider, ANY result.

        The position-based recorders (polymarket/kalshi) dedup against this so
        a settled-and-lingering position is never re-inserted. Deduping on
        pending-only rows re-inserts a position every sync once it settles.
        """
        rows = (
            self.db.query(Bet.provider_bet_id)
            .filter(
                Bet.profile_id == profile_id,
                Bet.provider_id == provider_id,
                Bet.provider_bet_id.isnot(None),
            )
            .all()
        )
        return {r[0] for r in rows if r[0]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_bet_repo.py::test_recorded_provider_bet_ids_includes_settled -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/bet_repo.py backend/tests/test_bet_repo.py
git commit -m "feat(bets): BetRepo.recorded_provider_bet_ids for all-status dedup"
```

---

## Task 3: `GET /api/bets/recorded-ids` endpoint

**Files:**
- Modify: `backend/src/api/routes/bets.py` (add route near `@router.get("/analytics")`, ~line 386)

- [ ] **Step 1: Add the endpoint**

In `backend/src/api/routes/bets.py`, immediately before `@router.get("/analytics")` add:

```python
@router.get("/recorded-ids")
def recorded_ids(provider_id: str, db: Session = Depends(get_db)):
    """All provider_bet_id values ever recorded for a provider (any result).

    The position recorders (polymarket/kalshi) dedup against this set so a
    settled position still lingering in the provider feed is never re-inserted.
    """
    profile = ProfileRepo(db).get_active()
    ids = BetRepo(db).recorded_provider_bet_ids(profile.id, provider_id)
    return {"provider_bet_ids": sorted(ids)}
```

`ProfileRepo`, `BetRepo`, `get_db`, and `Session` are already imported in this file (used by `list_bets`).

- [ ] **Step 2: Verify the route registers**

Run: `cd backend && python -c "from src.api.routes.bets import router; print([r.path for r in router.routes if 'recorded' in r.path])"`
Expected: prints `['/recorded-ids']`

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/bets.py
git commit -m "feat(bets): GET /api/bets/recorded-ids endpoint"
```

(End-to-end verification of this endpoint happens in Task 12 via curl after deploy.)

---

## Task 4: `polymarket_api.sync()` — dedup against all recorded ids

**Files:**
- Modify: `arnold/mirror/recorders/polymarket_api.py` (`sync()` ~lines 191-222)
- Test: `arnold/tests/test_recorder_dedup.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `arnold/tests/test_recorder_dedup.py`:

```python
"""Recorder dedup — a settled position lingering in the provider feed must
NOT be re-inserted (the duplication death spiral, 2026-05-20 audit)."""

from __future__ import annotations

import asyncio

from arnold.mirror.recorders import polymarket_api


class _Resp:
    status_code = 201
    text = ""


def _position(cid: str):
    return polymarket_api.RecoveredPosition(
        provider_id="polymarket",
        provider_bet_id=cid,
        event_name="Team A vs Team B",
        outcome_name="Team A",
        odds=2.0,
        stake=10.0,
        currency="USDC",
        raw={},
    )


def test_poly_sync_skips_settled_position(monkeypatch):
    """conditionId is in fetch_known_ids (recorded) but NOT in db_pending
    (it settled) — must be skipped, not re-inserted."""
    cid = "0x" + "a" * 64
    monkeypatch.setattr(polymarket_api, "fetch_open_positions",
                        lambda wallet: _async([_position(cid)]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(polymarket_api.sync(
        "0xwallet", api_post,
        fetch_events=lambda: _async([]),
        fetch_db_pending=lambda: _async([]),
        fetch_known_ids=lambda: _async([cid]),
    ))
    assert result.inserted == 0
    assert result.skipped_dup == 1
    assert posted == []


def test_poly_sync_inserts_new_position(monkeypatch):
    """A conditionId not in fetch_known_ids IS inserted."""
    cid = "0x" + "b" * 64
    monkeypatch.setattr(polymarket_api, "fetch_open_positions",
                        lambda wallet: _async([_position(cid)]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(polymarket_api.sync(
        "0xwallet", api_post,
        fetch_events=lambda: _async([]),
        fetch_db_pending=lambda: _async([]),
        fetch_known_ids=lambda: _async([]),
    ))
    assert result.inserted == 1
    assert posted[0]["provider_bet_id"] == cid


def test_poly_sync_fails_closed_when_known_ids_unavailable(monkeypatch):
    """fetch_known_ids returning None = fetch failed → insert pass skipped
    entirely (never insert against an unknown dedup state)."""
    cid = "0x" + "c" * 64
    monkeypatch.setattr(polymarket_api, "fetch_open_positions",
                        lambda wallet: _async([_position(cid)]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(polymarket_api.sync(
        "0xwallet", api_post,
        fetch_events=lambda: _async([]),
        fetch_db_pending=lambda: _async([]),
        fetch_known_ids=lambda: _async(None),
    ))
    assert result.inserted == 0
    assert posted == []


def _async(value):
    async def _coro():
        return value
    return _coro()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest arnold/tests/test_recorder_dedup.py -v`
Expected: FAIL — `sync()` does not accept `fetch_known_ids`.

- [ ] **Step 3: Change the `sync()` signature**

In `arnold/mirror/recorders/polymarket_api.py`, change the `sync` signature (line ~191):

```python
async def sync(
    wallet: str,
    api_post,  # async callable(payload: dict) -> response
    fetch_events,  # async callable() -> list[{id, home_team, away_team}]
    fetch_db_pending,  # async callable() -> list[{provider_bet_id, event_id, outcome, odds, stake}]
    api_patch=None,  # async callable(bet_id: int, payload: dict) -> response — backfills conditionId
    fetch_known_ids=None,  # async callable() -> list[str] | None — ALL recorded conditionIds
    #                        (any result). None return = fetch failed → skip insert (fail-closed).
) -> RecorderResult:
```

- [ ] **Step 4: Replace the `known_ids` construction**

In `sync()`, replace the `known_ids` / `known_sigs` block (lines ~216-222):

```python
    # Index pending bets by normalized conditionId so a truncated 60-char DB
    # cid matches a 66-char position cid. Slugs (`athletics-vs-...`) are
    # filtered out by _is_condition_id — they must never collide.
    known_ids = {_cid_key(b.get("provider_bet_id")) for b in db_pending if _is_condition_id(b.get("provider_bet_id"))}
    known_sigs = {
        (b.get("event_id"), b.get("outcome")): b for b in db_pending if b.get("event_id") and b.get("outcome")
    }
```

with:

```python
    # Dedup against ALL recorded conditionIds (any result), not just pending.
    # A losing Polymarket position lingers in the /positions feed forever; if
    # dedup only knows pending bets, a settled position re-inserts every sync
    # → the duplication death spiral (70 rows for 28 real positions, audit
    # 2026-05-20). fetch_known_ids returns every recorded cid; None = the
    # lookup failed and we must NOT insert against an unknown dedup state.
    if fetch_known_ids is not None:
        recorded = await fetch_known_ids()
        if recorded is None:
            logger.warning("[polymarket_api] fetch_known_ids failed — skipping insert pass (fail-closed)")
            result.errors.append("fetch_known_ids unavailable — insert skipped")
            return result
        known_ids = {_cid_key(c) for c in recorded if _is_condition_id(c)}
    else:
        known_ids = {_cid_key(b.get("provider_bet_id")) for b in db_pending if _is_condition_id(b.get("provider_bet_id"))}
    known_sigs = {
        (b.get("event_id"), b.get("outcome")): b for b in db_pending if b.get("event_id") and b.get("outcome")
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest arnold/tests/test_recorder_dedup.py -v`
Expected: 3 PASS (the kalshi tests are added in Task 5).

- [ ] **Step 6: Commit**

```bash
git add arnold/mirror/recorders/polymarket_api.py arnold/tests/test_recorder_dedup.py
git commit -m "fix(polymarket): dedup sync against all recorded conditionIds

Stops the duplication death spiral — a settled position lingering in
the /positions feed was re-inserted every 5-min sync because dedup only
checked pending bets. Fail-closed when the recorded-ids lookup fails."
```

---

## Task 5: `kalshi_api.sync()` — same dedup fix

**Files:**
- Modify: `arnold/mirror/recorders/kalshi_api.py` (`sync()` ~lines 310-325)
- Test: `arnold/tests/test_recorder_dedup.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `arnold/tests/test_recorder_dedup.py`:

```python
def test_kalshi_sync_skips_settled_position(monkeypatch):
    """Same fix for the Kalshi recorder — dedup against all recorded tickers."""
    from arnold.mirror.recorders import kalshi_api

    ticker = "KXNQ-26-T1"
    pos = kalshi_api.RecoveredPosition(
        provider_id="kalshi",
        provider_bet_id=ticker,
        event_name="Team A vs Team B",
        outcome_name="Team A",
        odds=2.0,
        stake=10.0,
        currency="USD",
        raw={},
    )
    monkeypatch.setattr(kalshi_api, "fetch_open_positions", lambda: _async([pos]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(kalshi_api.sync(
        api_post,
        fetch_events=lambda: _async([]),
        fetch_db_pending=lambda: _async([]),
        fetch_known_ids=lambda: _async([ticker]),
    ))
    assert result.inserted == 0
    assert result.skipped_dup == 1
    assert posted == []


def test_kalshi_sync_fails_closed(monkeypatch):
    """fetch_known_ids None → skip insert."""
    from arnold.mirror.recorders import kalshi_api

    pos = kalshi_api.RecoveredPosition(
        provider_id="kalshi", provider_bet_id="KXNQ-26-T2",
        event_name="A vs B", outcome_name="A", odds=2.0, stake=5.0,
        currency="USD", raw={},
    )
    monkeypatch.setattr(kalshi_api, "fetch_open_positions", lambda: _async([pos]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(kalshi_api.sync(
        api_post,
        fetch_events=lambda: _async([]),
        fetch_db_pending=lambda: _async([]),
        fetch_known_ids=lambda: _async(None),
    ))
    assert result.inserted == 0
    assert posted == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest arnold/tests/test_recorder_dedup.py -k kalshi -v`
Expected: FAIL — `kalshi_api.sync()` does not accept `fetch_known_ids`.

- [ ] **Step 3: Change the `sync()` signature and `known_ids` block**

In `arnold/mirror/recorders/kalshi_api.py`, change the `sync` signature (line ~310):

```python
async def sync(
    api_post,
    fetch_events,
    fetch_db_pending,
    fetch_known_ids=None,  # async callable() -> list[str] | None — ALL recorded tickers
) -> RecorderResult:
```

Then replace line ~325:

```python
    known_ids = {b.get("provider_bet_id") for b in db_pending if b.get("provider_bet_id")}
```

with:

```python
    # Dedup against ALL recorded tickers (any result), not just pending —
    # mirror of the polymarket fix. None = lookup failed → skip insert.
    if fetch_known_ids is not None:
        recorded = await fetch_known_ids()
        if recorded is None:
            logger.warning("[kalshi_api] fetch_known_ids failed — skipping insert pass (fail-closed)")
            result.errors.append("fetch_known_ids unavailable — insert skipped")
            return result
        known_ids = {c for c in recorded if c}
    else:
        known_ids = {b.get("provider_bet_id") for b in db_pending if b.get("provider_bet_id")}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest arnold/tests/test_recorder_dedup.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/recorders/kalshi_api.py arnold/tests/test_recorder_dedup.py
git commit -m "fix(kalshi): dedup sync against all recorded tickers"
```

---

## Task 6: Wire `fetch_known_ids` into `router.py:sync_positions`

**Files:**
- Modify: `arnold/mirror/router.py` (`sync_positions`, ~lines 634-666)

- [ ] **Step 1: Add the `fetch_known_ids` callable**

In `arnold/mirror/router.py`, inside `sync_positions`, immediately after the `fetch_db_pending` definition (line ~634-635) add:

```python
        async def fetch_known_ids() -> list[str] | None:
            """All provider_bet_id values ever recorded for this provider
            (any result) — the dedup source for the position recorders.
            Returns None on failure so the recorder fails closed instead of
            re-inserting every open position against an unknown dedup state."""
            from arnold.http_client import tunnel_client

            try:
                r = await tunnel_client().get(
                    "/api/bets/recorded-ids",
                    params={"provider_id": provider_id},
                    timeout=30.0,
                )
                r.raise_for_status()
                return r.json().get("provider_bet_ids", []) or []
            except Exception as exc:
                print(f"[sync-positions] fetch_known_ids raised: {exc!r}", flush=True)
                return None
```

- [ ] **Step 2: Pass it to the polymarket recorder**

In `sync_positions`, change the polymarket `sync` call (line ~652) from:

```python
            result = await polymarket_api.sync(wallet, api_post, fetch_events, fetch_db_pending, api_patch=api_patch)
```

to:

```python
            result = await polymarket_api.sync(
                wallet, api_post, fetch_events, fetch_db_pending,
                api_patch=api_patch, fetch_known_ids=fetch_known_ids,
            )
```

- [ ] **Step 3: Pass it to the kalshi recorder**

Change the kalshi `sync` call (line ~661) from:

```python
            result = await kalshi_api.sync(api_post, fetch_events, fetch_db_pending)
```

to:

```python
            result = await kalshi_api.sync(
                api_post, fetch_events, fetch_db_pending, fetch_known_ids=fetch_known_ids,
            )
```

- [ ] **Step 4: Verify the module imports cleanly**

Run: `python -c "import arnold.mirror.router"`
Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/router.py
git commit -m "fix(mirror): wire fail-closed fetch_known_ids into sync-positions"
```

---

## Task 7: `correlate_arbs()` service

**Files:**
- Create: `backend/src/services/arb_correlation.py`
- Test: `backend/tests/test_arb_correlation.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_arb_correlation.py`:

```python
"""Tests for arb leg correlation."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, Event, Profile
from src.services.arb_correlation import correlate_arbs


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Profile(id=1, name="t", is_active=True))
    s.add(Event(id="evt1", sport="tennis", home_team="ruud", away_team="brooksby"))
    s.commit()
    yield s
    s.close()


def _bet(s, **kw):
    base = dict(profile_id=1, odds=2.0, stake=10.0, result="pending",
                placed_at=datetime.utcnow())
    base.update(kw)
    b = Bet(**base)
    s.add(b)
    s.commit()
    return b


def test_high_confidence_pairs_same_event_complementary(db):
    anchor = _bet(db, provider_id="betinia", event_id="evt1", outcome="home")
    counter = _bet(db, provider_id="polymarket", event_id="evt1", outcome="away",
                   bet_type="arb_counter", provider_bet_id="0xCID")
    out = correlate_arbs(db)
    assert out["linked"] == 1
    db.refresh(anchor)
    db.refresh(counter)
    assert anchor.arb_group_id is not None
    assert anchor.arb_group_id == counter.arb_group_id
    assert anchor.bet_type == "arb_anchor"


def test_medium_confidence_pairs_by_title(db):
    anchor = _bet(db, provider_id="betinia", event_id="evt1", outcome="home")
    counter = _bet(db, provider_id="polymarket", event_id=None, outcome="",
                   bet_type="arb_counter", provider_bet_id="0xCID2",
                   boost_event="Geneva Open: Jenson Brooksby vs Casper Ruud")
    out = correlate_arbs(db)
    assert out["linked"] == 1
    db.refresh(anchor)
    db.refresh(counter)
    assert counter.arb_group_id == anchor.arb_group_id


def test_no_link_outside_time_window(db):
    now = datetime.utcnow()
    _bet(db, provider_id="betinia", event_id="evt1", outcome="home", placed_at=now)
    _bet(db, provider_id="polymarket", event_id="evt1", outcome="away",
         bet_type="arb_counter", provider_bet_id="0xCID3",
         placed_at=now + timedelta(hours=6))
    out = correlate_arbs(db)
    assert out["linked"] == 0


def test_ambiguous_high_matches_left_unlinked(db):
    _bet(db, provider_id="betinia", event_id="evt1", outcome="home")
    _bet(db, provider_id="bethard", event_id="evt1", outcome="home")
    _bet(db, provider_id="polymarket", event_id="evt1", outcome="away",
         bet_type="arb_counter", provider_bet_id="0xCID4")
    out = correlate_arbs(db)
    assert out["linked"] == 0


def test_already_grouped_legs_skipped(db):
    a = _bet(db, provider_id="betinia", event_id="evt1", outcome="home",
             arb_group_id="existing")
    c = _bet(db, provider_id="polymarket", event_id="evt1", outcome="away",
             bet_type="arb_counter", provider_bet_id="0xCID5", arb_group_id="existing")
    out = correlate_arbs(db)
    assert out["linked"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_arb_correlation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.arb_correlation'`

- [ ] **Step 3: Implement the service**

Create `backend/src/services/arb_correlation.py`:

```python
"""Arb leg correlation — pairs unlinked anchor/counter bets into arb groups.

An arb is a two-leg position: a soft-book anchor + a Polymarket/Kalshi counter
on the same event, opposite sides, placed close in time. The legs are recorded
by different paths and arrive with no shared id. This pass infers the pairing
and stamps a shared bets.arb_group_id. Ambiguous matches are left unlinked — a
wrong pair corrupts the analytics this is meant to fix.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..db.models import Bet, Event

COUNTER_PROVIDERS = {"polymarket", "kalshi"}
PAIR_WINDOW_SECONDS = 2 * 3600.0
LOOKBACK_DAYS = 30

_STOP = {"vs", "v", "the", "fc", "cf", "sc", "fk", "ec", "esports"}
_COMPLEMENT = {"home": "away", "away": "home", "over": "under", "under": "over"}


def _tokens(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return {t for t in s.split() if len(t) >= 3 and t not in _STOP}


def _match_confidence(counter: Bet, anchor: Bet, events: dict) -> str | None:
    """'high'  = same event_id + complementary (or blank) counter outcome.
    'medium' = counter has no event_id but both of the anchor event's team
               names appear in the counter's boost_event title.
    None     = no confident match.
    """
    # HIGH — exact event match
    if counter.event_id and anchor.event_id and counter.event_id == anchor.event_id:
        c_out = (counter.outcome or "").lower()
        a_out = (anchor.outcome or "").lower()
        if not c_out:
            return "high"  # unmatched counter — same event is enough
        if a_out and _COMPLEMENT.get(a_out) == c_out:
            return "high"
        return None
    # MEDIUM — title contains both anchor team names
    if not counter.event_id:
        title = (counter.boost_event or "").lower()
        ev = events.get(anchor.event_id) if anchor.event_id else None
        if ev is None:
            return None
        home = (ev.home_team or "").lower()
        away = (ev.away_team or "").lower()
        if not home or not away:
            return None
        if home in title and away in title:
            return "medium"
        t = _tokens(title)
        if _tokens(home) & t and _tokens(away) & t:
            return "medium"
    return None


def _best_anchor(counter: Bet, anchors: list[Bet], events: dict) -> Bet | None:
    """Single best anchor for this counter, or None if ambiguous / no match."""
    highs: list[Bet] = []
    mediums: list[Bet] = []
    for a in anchors:
        if a is counter or a.provider_id == counter.provider_id:
            continue
        if counter.placed_at is None or a.placed_at is None:
            continue
        if abs((a.placed_at - counter.placed_at).total_seconds()) > PAIR_WINDOW_SECONDS:
            continue
        conf = _match_confidence(counter, a, events)
        if conf == "high":
            highs.append(a)
        elif conf == "medium":
            mediums.append(a)
    if len(highs) == 1:
        return highs[0]
    if highs:
        return None  # ambiguous — don't guess
    if len(mediums) == 1:
        return mediums[0]
    return None


def correlate_arbs(session: Session) -> dict:
    """Link ungrouped arb legs. Returns {"linked": n, "groups": n}."""
    cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
    legs = (
        session.query(Bet)
        .filter(Bet.arb_group_id.is_(None), Bet.placed_at >= cutoff)
        .all()
    )
    counters = [
        b for b in legs
        if b.provider_id in COUNTER_PROVIDERS and b.bet_type == "arb_counter"
    ]
    anchors = [b for b in legs if b.provider_id not in COUNTER_PROVIDERS]

    event_ids = {b.event_id for b in (anchors + counters) if b.event_id}
    events: dict = {}
    if event_ids:
        for e in session.query(Event).filter(Event.id.in_(event_ids)).all():
            events[e.id] = e

    linked = 0
    groups: set[str] = set()
    for counter in counters:
        anchor = _best_anchor(counter, anchors, events)
        if anchor is None:
            continue
        gid = anchor.arb_group_id or counter.arb_group_id or uuid.uuid4().hex[:12]
        counter.arb_group_id = gid
        anchor.arb_group_id = gid
        if not anchor.bet_type:
            anchor.bet_type = "arb_anchor"
        linked += 1
        groups.add(gid)

    if linked:
        session.commit()
    return {"linked": linked, "groups": len(groups)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_arb_correlation.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/arb_correlation.py backend/tests/test_arb_correlation.py
git commit -m "feat(arb): correlate_arbs service to pair anchor/counter legs"
```

---

## Task 8: `POST /api/bets/correlate-arbs` endpoint

**Files:**
- Modify: `backend/src/api/routes/bets.py`

- [ ] **Step 1: Add the endpoint**

In `backend/src/api/routes/bets.py`, immediately after the `recorded_ids` route added in Task 3, add:

```python
@router.post("/correlate-arbs")
def correlate_arbs_endpoint(db: Session = Depends(get_db_writer)):
    """Link ungrouped arb legs (soft anchor <-> Polymarket/Kalshi counter)."""
    from src.services.arb_correlation import correlate_arbs

    return correlate_arbs(db)
```

`get_db_writer` is already imported in this file (used by `create_bet`). `correlate_arbs` commits internally.

- [ ] **Step 2: Verify the route registers**

Run: `cd backend && python -c "from src.api.routes.bets import router; print([r.path for r in router.routes if 'correlate' in r.path])"`
Expected: prints `['/correlate-arbs']`

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/bets.py
git commit -m "feat(bets): POST /api/bets/correlate-arbs endpoint"
```

---

## Task 9: Call `correlate-arbs` from the auto-poller

**Files:**
- Modify: `arnold/mirror/recorders/auto_poller.py` (`run_auto_poller`, ~lines 62-72)

- [ ] **Step 1: Add the correlation call after each cycle**

In `arnold/mirror/recorders/auto_poller.py`, in `run_auto_poller`, change the loop body so that after the `for pid in PROVIDERS` loop it also calls correlate. Replace:

```python
        while True:
            try:
                for pid in PROVIDERS:
                    await _tick_one(client, local_url, pid)
            except asyncio.CancelledError:
                logger.info("[auto_poller] cancelled — shutting down")
                raise
            except Exception as e:
                logger.warning(f"[auto_poller] tick crashed: {type(e).__name__}: {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
```

with:

```python
        while True:
            try:
                for pid in PROVIDERS:
                    await _tick_one(client, local_url, pid)
                await _correlate_arbs(client, local_url)
            except asyncio.CancelledError:
                logger.info("[auto_poller] cancelled — shutting down")
                raise
            except Exception as e:
                logger.warning(f"[auto_poller] tick crashed: {type(e).__name__}: {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
```

- [ ] **Step 2: Add the `_correlate_arbs` helper**

In `arnold/mirror/recorders/auto_poller.py`, add this function immediately before `run_auto_poller`:

```python
async def _correlate_arbs(client: httpx.AsyncClient, local_url: str) -> None:
    """Link any newly recorded arb legs. Forwarded to the server by the
    local proxy's /api/* route."""
    try:
        r = await client.post(f"{local_url}/api/bets/correlate-arbs", timeout=60.0)
        if r.status_code != 200:
            logger.warning(f"[auto_poller] correlate-arbs → {r.status_code}: {(r.text or '')[:200]}")
            return
        body = r.json() or {}
        if body.get("linked"):
            logger.info(f"[auto_poller] arb correlation: linked={body['linked']} groups={body['groups']}")
    except Exception as e:
        logger.warning(f"[auto_poller] correlate-arbs raised: {type(e).__name__}: {e}")
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `python -c "import arnold.mirror.recorders.auto_poller"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add arnold/mirror/recorders/auto_poller.py
git commit -m "feat(auto_poller): run arb correlation after each sync cycle"
```

---

## Task 10: Deploy backend + restart local client

> **CHECKPOINT — confirm with the user before this task.** This is a production backend rebuild. Per CLAUDE.md it severs the TopstepX session briefly and is gated if a stocks position is open.

**Files:** none (operational)

- [ ] **Step 1: Push the branch and fast-forward `main`**

```bash
git fetch origin
git log origin/main..HEAD --oneline   # confirm only our commits
git checkout main && git merge --ff-only fix/arb-recording-dedup-linkage && git push origin main
```

If `main` has advanced and `--ff-only` fails, rebase `fix/arb-recording-dedup-linkage` onto `origin/main` first, then retry.

- [ ] **Step 2: Check the deploy lock is free**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh status && pgrep -fa 'server-deploy.sh'"`
Expected: status prints; `pgrep` prints nothing (slot free).

- [ ] **Step 3: Rebuild the backend**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"`
Expected: exits 0; health check passes.

- [ ] **Step 4: Verify the migration applied and the endpoint is live**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c '\d bets' | grep arb_group_id"
ssh root@148.251.40.251 "curl -sf 'http://localhost:8000/api/bets/recorded-ids?provider_id=polymarket' | head -c 200"
```
Expected: `arb_group_id` column listed; the curl returns a JSON object with `provider_bet_ids`.

- [ ] **Step 5: Restart the local client**

The local-client files (`polymarket_api.py`, `kalshi_api.py`, `router.py`, `auto_poller.py`) ship via `arnold.bat`. Tell the user to close and re-run `arnold.bat` so the new recorder + auto-poller code loads.

- [ ] **Step 6: No commit** (operational task)

---

## Task 11: Clean the 42 phantom rows

> **CHECKPOINT — show the keep/delete list to the user and get sign-off before any DELETE.** Run only AFTER Task 10 (so the dedup fix is live and no new duplicates appear).

**Files:** none (DB operation against `arnold-postgres-1`)

- [ ] **Step 1: Show the duplicate rows that would be deleted**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
WITH ranked AS (
  SELECT id, provider_bet_id, result, settlement_source, placed_at,
         row_number() OVER (PARTITION BY provider_bet_id
           ORDER BY (settlement_source IS NOT NULL) DESC, placed_at ASC, id ASC) AS rn
  FROM bets WHERE bet_type='arb_counter' AND provider_id='polymarket')
SELECT id, provider_bet_id, result, placed_at FROM ranked WHERE rn > 1 ORDER BY provider_bet_id, placed_at;\""
```
Expected: ~42 rows. Present this list to the user for sign-off.

- [ ] **Step 2: Check FK children referencing the doomed rows**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
WITH doomed AS (
  SELECT id FROM (
    SELECT id, row_number() OVER (PARTITION BY provider_bet_id
      ORDER BY (settlement_source IS NOT NULL) DESC, placed_at ASC, id ASC) rn
    FROM bets WHERE bet_type='arb_counter' AND provider_id='polymarket') x WHERE rn>1)
SELECT 'bet_traces' tbl, count(*) FROM bet_traces WHERE bet_id IN (SELECT id FROM doomed)
UNION ALL SELECT 'bet_postmortems', count(*) FROM bet_postmortems WHERE bet_id IN (SELECT id FROM doomed)
UNION ALL SELECT 'settlement_queue', count(*) FROM settlement_queue WHERE bet_id IN (SELECT id FROM doomed);\""
```
Expected: counts per child table (likely 0 for phantom rows).

- [ ] **Step 3: Delete children (if any) then the phantom rows, in one transaction**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
BEGIN;
WITH doomed AS (
  SELECT id FROM (
    SELECT id, row_number() OVER (PARTITION BY provider_bet_id
      ORDER BY (settlement_source IS NOT NULL) DESC, placed_at ASC, id ASC) rn
    FROM bets WHERE bet_type='arb_counter' AND provider_id='polymarket') x WHERE rn>1)
DELETE FROM bet_traces WHERE bet_id IN (SELECT id FROM doomed);
WITH doomed AS (
  SELECT id FROM (
    SELECT id, row_number() OVER (PARTITION BY provider_bet_id
      ORDER BY (settlement_source IS NOT NULL) DESC, placed_at ASC, id ASC) rn
    FROM bets WHERE bet_type='arb_counter' AND provider_id='polymarket') x WHERE rn>1)
DELETE FROM bet_postmortems WHERE bet_id IN (SELECT id FROM doomed);
WITH doomed AS (
  SELECT id FROM (
    SELECT id, row_number() OVER (PARTITION BY provider_bet_id
      ORDER BY (settlement_source IS NOT NULL) DESC, placed_at ASC, id ASC) rn
    FROM bets WHERE bet_type='arb_counter' AND provider_id='polymarket') x WHERE rn>1)
DELETE FROM settlement_queue WHERE bet_id IN (SELECT id FROM doomed);
WITH doomed AS (
  SELECT id FROM (
    SELECT id, row_number() OVER (PARTITION BY provider_bet_id
      ORDER BY (settlement_source IS NOT NULL) DESC, placed_at ASC, id ASC) rn
    FROM bets WHERE bet_type='arb_counter' AND provider_id='polymarket') x WHERE rn>1)
DELETE FROM bets WHERE id IN (SELECT id FROM doomed);
COMMIT;\""
```
Expected: final `DELETE` reports ~42 rows.

- [ ] **Step 4: Verify no duplicates remain**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
SELECT provider_bet_id, count(*) FROM bets WHERE bet_type='arb_counter'
GROUP BY 1 HAVING count(*) > 1;\""
```
Expected: 0 rows.

- [ ] **Step 5: No commit** (operational task)

---

## Task 12: Backfill correlation + final verification

**Files:** none (operational)

- [ ] **Step 1: Run the correlation backfill**

```bash
ssh root@148.251.40.251 "curl -sf -X POST http://localhost:8000/api/bets/correlate-arbs"
```
Expected: JSON `{"linked": <n>, "groups": <n>}`.

- [ ] **Step 2: Verify dedup holds — watch two auto-poller cycles**

After the local client has run for ~12 minutes, check its console / logs for `[auto_poller] polymarket`. Confirm a known settled conditionId shows under `skipped_dup`, not `inserted`. Re-run the duplicate query from Task 11 Step 4 — still 0 rows.

- [ ] **Step 3: Verify arb groups satisfy the guaranteed-profit invariant**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
SELECT arb_group_id, count(*) legs,
       round(sum(stake)::numeric,2) staked,
       round(sum(coalesce(payout,0))::numeric,2) returned,
       string_agg(DISTINCT result, ',') results
FROM bets WHERE arb_group_id IS NOT NULL
GROUP BY arb_group_id ORDER BY arb_group_id;\""
```
Expected: each group has ≥2 legs; for fully-settled groups, `returned > staked` (the guaranteed-profit invariant). Flag any settled group where `returned < staked` to the user — it indicates a bad pair or a real losing arb.

- [ ] **Step 4: Report the corrected profitability**

Re-run the value-bet and arb P&L aggregates from the original audit and report the corrected figures to the user (aggregate P&L is now free of phantom-row contamination).

- [ ] **Step 5: No commit** (operational task)

---

## Task 13: `BetRepo.recorded_signatures()`  *(Part 4 — added 2026-05-22)*

> **Part 4 ordering:** do Tasks 13–15 **before** Task 10 so one backend rebuild +
> `arnold.bat` restart ships Parts 1–4a together. Task 16 (cleanup) runs after the
> deploy, alongside Task 11. If Parts 1–3 already deployed, Tasks 13–15 need their
> own `server-deploy.sh rebuild backend` + local restart.

**Files:**
- Modify: `backend/src/repositories/bet_repo.py`
- Test: `backend/tests/test_bet_repo.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_bet_repo.py`:

```python
def test_recorded_signatures_spans_all_results_and_types(db):
    """The reactive-sync dedup must see settled bets AND arb_counter rows —
    a settled polymarket arb counter must not re-insert as a value row."""
    repo = BetRepo(db)
    db.add(Bet(profile_id=1, provider_id="polymarket", odds=6.6667, stake=3.83,
               result="won", bet_type="arb_counter", provider_bet_id="0xAAA"))
    db.add(Bet(profile_id=1, provider_id="polymarket", odds=2.05, stake=20.0,
               result="lost", bet_type=None, provider_bet_id=None))
    db.add(Bet(profile_id=1, provider_id="betinia", odds=2.0, stake=10.0,
               result="won", bet_type="arb_anchor", provider_bet_id=None))
    db.commit()

    sigs = repo.recorded_signatures(profile_id=1, provider_id="polymarket")
    assert (6.67, 3.8) in sigs        # settled arb_counter — must be visible
    assert (2.05, 20.0) in sigs
    assert (2.0, 10.0) not in sigs    # other provider excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_bet_repo.py::test_recorded_signatures_spans_all_results_and_types -v`
Expected: FAIL with `AttributeError: 'BetRepo' object has no attribute 'recorded_signatures'`

- [ ] **Step 3: Implement the method**

In `backend/src/repositories/bet_repo.py`, add to `class BetRepo` (after `recorded_provider_bet_ids`):

```python
    def recorded_signatures(self, profile_id: int, provider_id: str) -> set[tuple[float, float]]:
        """(odds, stake) signatures of EVERY recorded bet for a provider —
        any result, any bet_type. The reactive history sync dedups against
        this so a settled arb_counter position is never re-inserted as a
        NULL-typed value row.

        Rounding matches pending_loop._sig: odds 2dp, stake 1dp (the 1dp on
        stake absorbs CLOB-fill cent drift, e.g. 3.82 vs 3.83 -> 3.8).
        """
        rows = (
            self.db.query(Bet.odds, Bet.stake)
            .filter(Bet.profile_id == profile_id, Bet.provider_id == provider_id)
            .all()
        )
        return {(round(float(o or 0), 2), round(float(s or 0), 1)) for o, s in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_bet_repo.py::test_recorded_signatures_spans_all_results_and_types -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/bet_repo.py backend/tests/test_bet_repo.py
git commit -m "feat(bets): BetRepo.recorded_signatures for cross-path dedup"
```

---

## Task 14: `GET /api/bets/recorded-signatures` endpoint  *(Part 4)*

**Files:**
- Modify: `backend/src/api/routes/bets.py` (next to `/recorded-ids` from Task 3)

- [ ] **Step 1: Add the endpoint**

In `backend/src/api/routes/bets.py`, immediately after the `recorded_ids` route (Task 3), add:

```python
@router.get("/recorded-signatures")
def recorded_signatures(provider_id: str, db: Session = Depends(get_db)):
    """(odds, stake) signatures of every recorded bet for a provider — any
    result, any bet_type. The reactive history sync dedups against this so a
    settled arb_counter position is not re-inserted as a NULL-typed value row.
    """
    profile = ProfileRepo(db).get_active()
    sigs = BetRepo(db).recorded_signatures(profile.id, provider_id)
    return {"signatures": sorted([list(s) for s in sigs])}
```

- [ ] **Step 2: Verify the route registers**

Run: `cd backend && python -c "from src.api.routes.bets import router; print(sorted(r.path for r in router.routes if 'recorded' in r.path))"`
Expected: prints `['/recorded-ids', '/recorded-signatures']`

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/bets.py
git commit -m "feat(bets): GET /api/bets/recorded-signatures endpoint"
```

(End-to-end verification happens in Task 16 after deploy.)

---

## Task 15: Dedup `_record_unknown_open_bets` against all recorded bets  *(Part 4)*

**Files:**
- Modify: `arnold/mirror/pending_loop.py`
- Modify: `arnold/mirror/provider_runner.py` (near-verbatim sibling copy)

`_record_unknown_open_bets` seeds its dedup sets from `db_pending` (pending only).
A settled `arb_counter` position is invisible to it, so the reactive sync
re-inserts it as a `bet_type=NULL` value row. Fix: also seed from the all-status
recorded sets, and fail closed if they cannot be fetched.

- [ ] **Step 1: Add a recorded-sets fetch helper to `PendingLoop`**

In `arnold/mirror/pending_loop.py`, add this method next to `_post_balance`:

```python
    async def _fetch_recorded(self, pid: str):
        """Fetch the all-status dedup sets for a provider. Returns
        (recorded_ids, recorded_sigs), or (None, None) on any failure — the
        caller fails closed and skips insertion."""
        from arnold.http_client import tunnel_client

        try:
            client = tunnel_client()
            r1 = await client.get("/api/bets/recorded-ids", params={"provider_id": pid}, timeout=15.0)
            r1.raise_for_status()
            r2 = await client.get("/api/bets/recorded-signatures", params={"provider_id": pid}, timeout=15.0)
            r2.raise_for_status()
            ids = {str(x) for x in (r1.json().get("provider_bet_ids") or [])}
            sigs = {(round(float(o), 2), round(float(s), 1)) for o, s in (r2.json().get("signatures") or [])}
            return ids, sigs
        except Exception:
            logger.warning(f"[PendingLoop] _fetch_recorded({pid}) failed — reactive insert will skip")
            return None, None
```

- [ ] **Step 2: Pass the recorded sets at the call site**

In `_sync_provider`, replace the line:

```python
        await self._record_unknown_open_bets(pid, history, db_bets)
```

with:

```python
        recorded_ids, recorded_sigs = await self._fetch_recorded(pid)
        await self._record_unknown_open_bets(pid, history, db_bets, recorded_ids, recorded_sigs)
```

- [ ] **Step 3: Widen the `_record_unknown_open_bets` signature**

Change:

```python
    async def _record_unknown_open_bets(
        self, provider_id: str, history: list[dict], db_pending: list[dict] | None
    ) -> None:
```

to:

```python
    async def _record_unknown_open_bets(
        self, provider_id: str, history: list[dict], db_pending: list[dict] | None,
        recorded_ids: set[str] | None = None,
        recorded_sigs: set[tuple[float, float]] | None = None,
    ) -> None:
```

- [ ] **Step 4: Add the fail-closed guard**

Immediately after the existing `if db_pending is None:` abort block, add:

```python
        if recorded_ids is None or recorded_sigs is None:
            logger.warning(
                f"[PendingLoop] _record_unknown_open_bets({provider_id}) aborted — "
                "recorded ids/signatures unavailable; refusing to insert (would re-create phantoms)"
            )
            return
```

- [ ] **Step 5: Seed the dedup sets from the recorded sets**

Replace the `db_pending` seeding loop:

```python
        for b in db_pending:
            pid_id = str(b.get("provider_bet_id") or "")
            if pid_id:
                known_pids.add(pid_id)
            known_sigs[_sig(b)] += 1
```

with:

```python
        # Seed from the all-status recorded sets — they span EVERY recorded bet
        # for this provider (any result, any bet_type), so a settled arb_counter
        # position is recognized and never re-inserted as a value row.
        # recorded_sigs already includes the pending rows, so db_pending is NOT
        # re-seeded into known_sigs (that would double-count and let a genuine
        # duplicate slip past the count-based dedup).
        known_pids |= {p for p in recorded_ids if p}
        for sig in recorded_sigs:
            known_sigs[sig] += 1
        for b in db_pending:
            pid_id = str(b.get("provider_bet_id") or "")
            if pid_id:
                known_pids.add(pid_id)
```

- [ ] **Step 6: Apply the identical change to `provider_runner.py`**

`arnold/mirror/provider_runner.py` has a near-verbatim `_record_unknown_open_bets`
(the `pending_loop` docstring says "Mirrors provider_runner._record_unknown_open_bets").
Apply Steps 1–5 there: add the same `_fetch_recorded` helper, the two-line call-site
change, the widened signature, the fail-closed guard, and the recorded-set seeding.

- [ ] **Step 7: Smoke-check syntax + lint**

```bash
python -c "import ast; [ast.parse(open(f).read()) for f in ('arnold/mirror/pending_loop.py','arnold/mirror/provider_runner.py')]; print('ok')"
python -m ruff check arnold/mirror/pending_loop.py arnold/mirror/provider_runner.py
```
Expected: prints `ok`; ruff clean.

- [ ] **Step 8: Commit**

```bash
git add arnold/mirror/pending_loop.py arnold/mirror/provider_runner.py
git commit -m "fix(mirror): dedup reactive sync against all recorded bets

_record_unknown_open_bets seeded its dedup set from pending bets only,
so a settled polymarket arb_counter position re-inserted as a NULL-typed
value row. Seed from the all-status recorded-ids / recorded-signatures
endpoints; fail closed if they are unavailable."
```

(End-to-end verification happens in Task 16 after deploy.)

---

## Task 16: Clean the value-row phantoms  *(Part 4b)*

> **CHECKPOINT — show the keep/delete list to the user and get sign-off before any DELETE.** Run only AFTER the deploy (Task 10) so Task 15's fix is live and no new phantoms appear.

**Files:** none (DB operation against `arnold-postgres-1`)

- [ ] **Step 1: Show the value-row phantoms that would be deleted**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
SELECT v.id, v.odds, v.stake, v.result, v.event_id, left(v.boost_event,40) AS boost
FROM bets v
WHERE v.provider_id='polymarket' AND v.bet_type IS NULL AND v.provider_bet_id IS NULL
  AND EXISTS (SELECT 1 FROM bets c
    WHERE c.provider_id='polymarket' AND c.bet_type='arb_counter'
      AND round(c.odds::numeric,2)=round(v.odds::numeric,2)
      AND round(c.stake::numeric,1)=round(v.stake::numeric,1))
ORDER BY v.id;\""
```
Expected: the value-row phantoms (the 2026-05-22 audit found ids 613, 614, 760, 761, 774 — verify the live set). Present this list to the user for sign-off.

- [ ] **Step 2: Check FK children referencing the doomed rows**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
WITH doomed AS (
  SELECT v.id FROM bets v
  WHERE v.provider_id='polymarket' AND v.bet_type IS NULL AND v.provider_bet_id IS NULL
    AND EXISTS (SELECT 1 FROM bets c WHERE c.provider_id='polymarket' AND c.bet_type='arb_counter'
      AND round(c.odds::numeric,2)=round(v.odds::numeric,2)
      AND round(c.stake::numeric,1)=round(v.stake::numeric,1)))
SELECT 'bet_traces' tbl, count(*) FROM bet_traces WHERE bet_id IN (SELECT id FROM doomed)
UNION ALL SELECT 'bet_postmortems', count(*) FROM bet_postmortems WHERE bet_id IN (SELECT id FROM doomed)
UNION ALL SELECT 'settlement_queue', count(*) FROM settlement_queue WHERE bet_id IN (SELECT id FROM doomed);\""
```
Expected: counts per child table (likely 0 for phantom rows).

- [ ] **Step 3: Delete children (if any) then the phantom rows, in one transaction**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
BEGIN;
WITH doomed AS (
  SELECT v.id FROM bets v WHERE v.provider_id='polymarket' AND v.bet_type IS NULL AND v.provider_bet_id IS NULL
    AND EXISTS (SELECT 1 FROM bets c WHERE c.provider_id='polymarket' AND c.bet_type='arb_counter'
      AND round(c.odds::numeric,2)=round(v.odds::numeric,2) AND round(c.stake::numeric,1)=round(v.stake::numeric,1)))
DELETE FROM bet_traces WHERE bet_id IN (SELECT id FROM doomed);
WITH doomed AS (
  SELECT v.id FROM bets v WHERE v.provider_id='polymarket' AND v.bet_type IS NULL AND v.provider_bet_id IS NULL
    AND EXISTS (SELECT 1 FROM bets c WHERE c.provider_id='polymarket' AND c.bet_type='arb_counter'
      AND round(c.odds::numeric,2)=round(v.odds::numeric,2) AND round(c.stake::numeric,1)=round(v.stake::numeric,1)))
DELETE FROM bet_postmortems WHERE bet_id IN (SELECT id FROM doomed);
WITH doomed AS (
  SELECT v.id FROM bets v WHERE v.provider_id='polymarket' AND v.bet_type IS NULL AND v.provider_bet_id IS NULL
    AND EXISTS (SELECT 1 FROM bets c WHERE c.provider_id='polymarket' AND c.bet_type='arb_counter'
      AND round(c.odds::numeric,2)=round(v.odds::numeric,2) AND round(c.stake::numeric,1)=round(v.stake::numeric,1)))
DELETE FROM settlement_queue WHERE bet_id IN (SELECT id FROM doomed);
WITH doomed AS (
  SELECT v.id FROM bets v WHERE v.provider_id='polymarket' AND v.bet_type IS NULL AND v.provider_bet_id IS NULL
    AND EXISTS (SELECT 1 FROM bets c WHERE c.provider_id='polymarket' AND c.bet_type='arb_counter'
      AND round(c.odds::numeric,2)=round(v.odds::numeric,2) AND round(c.stake::numeric,1)=round(v.stake::numeric,1)))
DELETE FROM bets WHERE id IN (SELECT id FROM doomed);
COMMIT;\""
```
Expected: the final `DELETE` reports the phantom-row count from Step 1.

- [ ] **Step 4: Verify no value-row phantoms remain**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
SELECT count(*) FROM bets v WHERE v.provider_id='polymarket' AND v.bet_type IS NULL AND v.provider_bet_id IS NULL
  AND EXISTS (SELECT 1 FROM bets c WHERE c.provider_id='polymarket' AND c.bet_type='arb_counter'
    AND round(c.odds::numeric,2)=round(v.odds::numeric,2) AND round(c.stake::numeric,1)=round(v.stake::numeric,1));\""
```
Expected: `0`.

- [ ] **Step 5: No commit** (operational task)

---

## Notes

- **Settlement logic is intentionally unchanged** — it only marks `lost` on genuine on-chain resolution. The "instant lost" symptom was the duplicate being re-settled; Task 4 removes the duplication.
- **Honest limitation:** Polymarket legs with a null `event_id` and an obscure title (ITF / minor esports) with no single clear anchor are left unlinked by Task 7. Aggregate P&L is still correct after Tasks 4–11; only per-arb grouping is incomplete for those.
- The `arb_runner._record_bet` `notes` field (dropped by `BetCreate`) is left as-is — the user places arbs manually, so that path is not exercised.
- **Part 4** closes a cross-path duplicate Parts 1–3 miss: the reactive history sync re-records a settled Polymarket `arb_counter` position as a separate `bet_type=NULL` value row. The wrong-`event_id` fuzzy match those phantom rows also carry is a *separate* defect (the matcher) — out of scope here; tracked separately.
