# Period-Scope Canonical Odds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `scope` dimension to canonical odds so the opportunity scanner can never surface a "value bet" or "arb" that pairs regulation-only odds with OT-inclusive odds (the bug that produced the false Slovenia v Italy IIHF arb on 2026-05-25).

**Architecture:** New `scope` VARCHAR(16) column on the `odds` table (default `'ft'`), populated per-row by each provider extractor from its native scope identifier (Pinnacle `period`, Altenar `typeId`, Gecko V2 `market_template`). The scanner's `group_odds` filters to the canonical scope for each sport, refusing to group cross-scope rows together. Single Postgres migration backfills the existing 5,656 Pinnacle hockey `period=6` rows to `scope='reg'` and rebuilds the unique index.

**Tech Stack:** Python 3.10+, SQLAlchemy ORM, PostgreSQL 16, pytest. Tests run with `cd backend && pytest tests/`. Deploys via `bash /opt/arnold/scripts/server-deploy.sh rebuild backend` per CLAUDE.md.

**Spec:** [docs/superpowers/specs/2026-05-25-period-scope-canonical-odds-design.md](../specs/2026-05-25-period-scope-canonical-odds-design.md)

---

## File Structure

```
backend/src/
├── constants.py                       MODIFY: add SPORT_CANONICAL_SCOPE + scope vocabulary
├── db/models.py                       MODIFY: Odds.scope column + migration block in _run_pg_migrations
├── pipeline/storage.py                MODIFY: OddsBatchProcessor.add(scope=...) + flush + 4 storage callsites
├── providers/
│   ├── pinnacle.py                    MODIFY: emit scope='ft'/'reg' per market period
│   ├── altenar.py                     MODIFY: emit scope from typeId; stop skipping typeId=18
│   └── gecko_v2.py                    MODIFY: emit scope from market_template
├── analysis/scanner.py                MODIFY: group_odds() filters by SPORT_CANONICAL_SCOPE[sport]
└── api/routes/extraction.py           MODIFY: /health/extraction reports unscannable_markets

backend/tests/
├── analysis/test_scope_enforcement.py    NEW: scanner refuses cross-scope grouping
├── providers/test_pinnacle_scope.py      NEW: period→scope mapping
├── providers/test_altenar_scope.py       NEW: typeId→scope mapping
├── providers/test_gecko_v2_scope.py      NEW: market_template→scope mapping
├── pipeline/test_storage_scope.py        NEW: scope plumbed through OddsBatchProcessor
└── integration/test_iihf_worlds_scope.py NEW: 2026-05-25 fixture → zero opps
```

---

## Task 1: SPORT_CANONICAL_SCOPE constant

**Files:**
- Modify: `backend/src/constants.py`

**Why:** A single source of truth for "what scope does the scanner trust for each sport." Referenced by scanner; potentially by storage validation later. Defined first so subsequent tasks can import it.

- [ ] **Step 1: Read current constants.py**

Read `backend/src/constants.py` so the next edit lands at the correct point.

- [ ] **Step 2: Append `SPORT_CANONICAL_SCOPE` after `SHARP_PROVIDERS`**

Edit `backend/src/constants.py`. Locate the line `SHARP_PROVIDERS = frozenset({"pinnacle"})` (currently line 28). Insert AFTER that line, AFTER the existing trailing comment block:

```python

# ============ Period / Scope Dimension ============
#
# Each row in the `odds` table carries a `scope` value identifying the
# temporal/structural scope of the market. The scanner only joins odds
# at matching scope, refusing to compare e.g. "Over 4.5 goals regulation"
# against "Under 4.5 goals incl. OT".
#
# Canonical vocabulary:
#   ft         — full time as the sport/book conventionally settles it
#   reg        — regulation time only (no OT/SO/extra innings)
#   1h, 2h     — halves (football, basketball, AF)
#   q1..q4     — quarters (basketball, AF)
#   p1..p3     — periods (hockey)
#   set_1..5   — sets (tennis, volleyball)
#   map_1..5   — maps (esports)
#
# `ft` per sport means:
#   football          — 90 min + stoppage (NO extra time, NO penalties)
#   ice_hockey        — including OT + shootout
#   basketball        — including OT
#   american_football — including OT
#   baseball          — including extra innings
#   tennis            — final match winner
#   esports           — series outcome (map markets are explicit scope)

VALID_SCOPES = frozenset({
    "ft", "reg",
    "1h", "2h",
    "q1", "q2", "q3", "q4",
    "p1", "p2", "p3",
    "set_1", "set_2", "set_3", "set_4", "set_5",
    "map_1", "map_2", "map_3", "map_4", "map_5",
})

# Default scope for new odds rows when an extractor doesn't set one.
DEFAULT_SCOPE = "ft"

# Per-sport canonical scope. The scanner only surfaces opportunities at
# this scope for each sport. Sports without an entry fall through to "ft".
SPORT_CANONICAL_SCOPE: dict[str, str] = {
    "football": "ft",
    "ice_hockey": "ft",
    "basketball": "ft",
    "american_football": "ft",
    "baseball": "ft",
    "tennis": "ft",
    "volleyball": "ft",
    "handball": "ft",
    "mma": "ft",
    "boxing": "ft",
    "rugby": "ft",
    "esports": "ft",
}


def canonical_scope_for(sport: str | None) -> str:
    """Return the canonical scope for a sport, falling back to DEFAULT_SCOPE."""
    if sport is None:
        return DEFAULT_SCOPE
    return SPORT_CANONICAL_SCOPE.get(sport, DEFAULT_SCOPE)
```

- [ ] **Step 3: Run import smoke test**

Run: `cd backend && python -c "from src.constants import SPORT_CANONICAL_SCOPE, VALID_SCOPES, DEFAULT_SCOPE, canonical_scope_for; assert canonical_scope_for('ice_hockey') == 'ft'; assert canonical_scope_for('unknown_sport') == 'ft'; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/src/constants.py
git commit -m "feat(scanner): add SPORT_CANONICAL_SCOPE vocabulary"
```

---

## Task 2: `Odds.scope` column + migration

**Files:**
- Modify: `backend/src/db/models.py` (the `Odds` class around line 164; `_run_pg_migrations` around line 1680)
- Test: `backend/tests/db/test_odds_scope_migration.py` (NEW)

**Why:** The schema change is the foundation; every downstream task assumes the column exists. Migration runs at backend startup, must be idempotent.

- [ ] **Step 1: Write failing migration test**

Create `backend/tests/db/test_odds_scope_migration.py`:

```python
"""Tests for the odds.scope column added 2026-05-25."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Odds, Event, Provider, _run_pg_migrations


@pytest.fixture
def pg_engine():
    """Real Postgres engine — required since the migration uses Postgres-only SQL.
    Skips if DATABASE_URL not set to a Postgres URL.
    """
    import os
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL not set to a Postgres URL")
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


def test_odds_scope_column_exists_after_migration(pg_engine):
    _run_pg_migrations(pg_engine)
    insp = inspect(pg_engine)
    cols = {c["name"]: c for c in insp.get_columns("odds")}
    assert "scope" in cols, "odds.scope column missing after migration"
    assert cols["scope"]["nullable"] is False
    assert cols["scope"]["default"] is not None  # has a server default


def test_odds_scope_defaults_to_ft(pg_engine):
    _run_pg_migrations(pg_engine)
    Session = sessionmaker(bind=pg_engine)
    with Session() as s:
        s.add(Provider(id="testprovider", name="Test"))
        s.add(Event(id="evt:test", sport="ice_hockey", home_team="A", away_team="B"))
        s.flush()
        # Insert without specifying scope — should default to 'ft'
        s.execute(text(
            "INSERT INTO odds (event_id, provider_id, market, outcome, odds, point) "
            "VALUES ('evt:test', 'testprovider', 'total', 'over', 1.85, 4.5)"
        ))
        s.commit()
        row = s.execute(text("SELECT scope FROM odds WHERE event_id = 'evt:test'")).first()
        assert row.scope == "ft"


def test_pinnacle_period_6_hockey_backfills_to_reg(pg_engine):
    """The migration backfills existing Pinnacle period=6 hockey rows to scope='reg'."""
    Session = sessionmaker(bind=pg_engine)
    with Session() as s:
        s.add(Provider(id="pinnacle", name="Pinnacle"))
        s.add(Event(id="evt:hockey", sport="ice_hockey", home_team="A", away_team="B"))
        s.add(Event(id="evt:football", sport="football", home_team="C", away_team="D"))
        s.flush()
        # Seed pre-migration rows: pinnacle period 6 hockey + period 6 football
        # (the football one should NOT backfill — only hockey gets reg)
        s.execute(text(
            "INSERT INTO odds (event_id, provider_id, market, outcome, odds, point, provider_meta) "
            "VALUES "
            "('evt:hockey',   'pinnacle', 'total', 'over',  1.85, 4.5, '{\"period\": 6}'::json), "
            "('evt:football', 'pinnacle', 'total', 'over',  1.85, 2.5, '{\"period\": 6}'::json)"
        ))
        s.commit()
    # Re-run migration (idempotency check — re-runs against now-populated DB)
    _run_pg_migrations(pg_engine)
    with Session() as s:
        hockey = s.execute(text("SELECT scope FROM odds WHERE event_id='evt:hockey'")).first()
        football = s.execute(text("SELECT scope FROM odds WHERE event_id='evt:football'")).first()
        assert hockey.scope == "reg", "hockey period=6 must backfill to 'reg'"
        assert football.scope == "ft", "non-hockey rows must stay 'ft'"


def test_migration_is_idempotent(pg_engine):
    _run_pg_migrations(pg_engine)
    _run_pg_migrations(pg_engine)  # Second run must not error
    insp = inspect(pg_engine)
    cols = {c["name"]: c for c in insp.get_columns("odds")}
    assert "scope" in cols
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://arnold:$DB_PASSWORD@localhost:5432/arnold_test pytest tests/db/test_odds_scope_migration.py -v`
Expected: SKIP if no Postgres available, otherwise FAIL with "odds.scope column missing" (column doesn't exist yet).

If no local Postgres test DB, this test will skip — verify it skips cleanly rather than erroring. Proceed regardless; the integration test in Task 9 covers end-to-end with the real DB during smoke.

- [ ] **Step 3: Add `scope` column to the `Odds` model**

Edit `backend/src/db/models.py`. Locate the `Odds` class (line 164). Find the line `provider_meta = Column(` (line 183) and insert AFTER the closing `)` of the `provider_meta` block (after line 185), BEFORE the `bid = Column(...)` line:

```python
    # Period/structural scope of this market (e.g. "ft", "reg", "1h", "set_1").
    # Set by each extractor from its native scope identifier (Pinnacle period,
    # Altenar typeId, Gecko market_template). Default 'ft' for backward compat.
    # The scanner only joins odds at matching scope — see SPORT_CANONICAL_SCOPE.
    scope = Column(String(16), nullable=False, server_default="ft", default="ft")
```

- [ ] **Step 4: Update the unique constraint to include `scope`**

In the same file, locate the `__table_args__` block (line 194). Replace the entire `UniqueConstraint(...)` block (lines 196-204) with:

```python
        # NULLS NOT DISTINCT so (event_id, provider_id, market, outcome, NULL, scope) is unique
        UniqueConstraint(
            "event_id",
            "provider_id",
            "market",
            "outcome",
            "point",
            "scope",
            name="uq_odds_with_point_scope",
            postgresql_nulls_not_distinct=True,
        ),
```

Then locate line 215 (`Index("ix_odds_composite_key", ...)`) and replace with:

```python
        # Composite key for OddsBatchProcessor flush lookups (now includes scope)
        Index("ix_odds_composite_key", "event_id", "provider_id", "market", "outcome", "point", "scope"),
        # Scanner join index — finds canonical-scope rows for an event/market/line fast
        Index("ix_odds_event_market_point_scope", "event_id", "market", "point", "scope"),
```

- [ ] **Step 5: Add migration block to `_run_pg_migrations`**

In the same file, locate `_run_pg_migrations` (line 1680). In the `additions` list (line 1691), add a new entry at the END (after line 1701, before the closing `]`):

```python
        # 2026-05-25 — period/structural scope dimension for canonical odds.
        # Added so the scanner can refuse to pair regulation-only with OT-inclusive
        # odds (the IIHF Slovenia v Italy false-arb bug).
        ("odds", "scope", "VARCHAR(16) NOT NULL DEFAULT 'ft'"),
```

Then, AFTER the `for table, col, col_type in additions:` block (after line 1764), insert this backfill + index rebuild block. Place it BEFORE the `# Index for provider_bet_id lookups...` block (line 1766):

```python
        # 2026-05-25 — backfill scope on existing Pinnacle hockey period=6 rows.
        # All other rows keep the column default 'ft'. Idempotent: re-running
        # is a no-op because period=6 hockey rows will already have scope='reg'.
        sp = conn.begin_nested()
        try:
            conn.execute(text("""
                UPDATE odds
                SET scope = 'reg'
                WHERE provider_id = 'pinnacle'
                  AND scope = 'ft'
                  AND provider_meta::jsonb->>'period' = '6'
                  AND event_id IN (SELECT id FROM events WHERE sport = 'ice_hockey')
            """))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: odds.scope backfill failed", exc_info=True)

        # 2026-05-25 — rebuild unique constraint to include scope.
        # Drop old then add new; both wrapped in SAVEPOINT for safety.
        sp = conn.begin_nested()
        try:
            conn.execute(text("ALTER TABLE odds DROP CONSTRAINT IF EXISTS uq_odds_with_point_nd"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: drop uq_odds_with_point_nd failed", exc_info=True)

        sp = conn.begin_nested()
        try:
            conn.execute(text("""
                ALTER TABLE odds
                ADD CONSTRAINT uq_odds_with_point_scope
                UNIQUE NULLS NOT DISTINCT (event_id, provider_id, market, outcome, point, scope)
            """))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: add uq_odds_with_point_scope failed", exc_info=True)

        # 2026-05-25 — scanner-side join index for canonical-scope lookups.
        sp = conn.begin_nested()
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_odds_event_market_point_scope "
                "ON odds (event_id, market, point, scope)"
            ))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: ix_odds_event_market_point_scope failed", exc_info=True)
```

- [ ] **Step 6: Run migration tests**

Run: `cd backend && TEST_DATABASE_URL=postgresql://arnold:$DB_PASSWORD@localhost:5432/arnold_test pytest tests/db/test_odds_scope_migration.py -v`
Expected: PASS (or SKIP if no Postgres test DB — proceed; smoke test in Task 9 covers it).

- [ ] **Step 7: Commit**

```bash
git add backend/src/db/models.py backend/tests/db/test_odds_scope_migration.py
git commit -m "feat(db): add odds.scope column with migration + backfill"
```

---

## Task 3: Plumb `scope` through `OddsBatchProcessor`

**Files:**
- Modify: `backend/src/pipeline/storage.py` (`OddsBatchProcessor.add` around line 1351; `_flush_inner` around line 1423; storage callsites around lines 634, 648, 1236, 1250)
- Test: `backend/tests/pipeline/test_storage_scope.py` (NEW)

**Why:** Each extractor will set scope on its emitted market dicts. The pipeline must carry that value from market dict → OddsBatchProcessor.add → DB row. Without this, scope stays at the DB default ('ft') and Pinnacle period=6 rows would be silently mis-labeled.

- [ ] **Step 1: Write failing test**

Create `backend/tests/pipeline/test_storage_scope.py`:

```python
"""Scope flows from market dict through OddsBatchProcessor to DB rows."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Provider, _run_pg_migrations
from src.pipeline.storage import OddsBatchProcessor


@pytest.fixture
def session():
    import os
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL not set to a Postgres URL")
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    _run_pg_migrations(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    s.add(Provider(id="pinnacle", name="Pinnacle"))
    s.add(Event(id="evt:t1", sport="ice_hockey", home_team="A", away_team="B"))
    s.commit()
    yield s
    s.rollback()
    s.close()
    Base.metadata.drop_all(eng)


def test_batch_processor_persists_scope(session):
    batch = OddsBatchProcessor(session)
    batch.add(
        event_id="evt:t1", provider="pinnacle", market="total", outcome="over",
        odds=1.85, point=4.5, scope="reg",
    )
    batch.flush()
    row = session.execute(text(
        "SELECT scope FROM odds WHERE event_id='evt:t1' AND outcome='over'"
    )).first()
    assert row.scope == "reg"


def test_batch_processor_defaults_scope_to_ft(session):
    batch = OddsBatchProcessor(session)
    batch.add(
        event_id="evt:t1", provider="pinnacle", market="total", outcome="over",
        odds=1.85, point=4.5,  # scope intentionally omitted
    )
    batch.flush()
    row = session.execute(text(
        "SELECT scope FROM odds WHERE event_id='evt:t1' AND outcome='over'"
    )).first()
    assert row.scope == "ft"


def test_same_event_different_scopes_coexist(session):
    """Two rows with same (event, provider, market, outcome, point) but
    different scope must coexist (unique constraint includes scope)."""
    batch = OddsBatchProcessor(session)
    batch.add(
        event_id="evt:t1", provider="pinnacle", market="total", outcome="over",
        odds=1.85, point=4.5, scope="reg",
    )
    batch.add(
        event_id="evt:t1", provider="pinnacle", market="total", outcome="over",
        odds=1.90, point=4.5, scope="ft",
    )
    batch.flush()
    rows = session.execute(text(
        "SELECT scope, odds FROM odds WHERE event_id='evt:t1' ORDER BY scope"
    )).fetchall()
    assert len(rows) == 2
    assert {r.scope for r in rows} == {"ft", "reg"}
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://arnold:$DB_PASSWORD@localhost:5432/arnold_test pytest tests/pipeline/test_storage_scope.py -v`
Expected: FAIL with `TypeError: add() got an unexpected keyword argument 'scope'` (or SKIP if no Postgres test DB).

- [ ] **Step 3: Add `scope` param to `OddsBatchProcessor.add` + dedup key**

Edit `backend/src/pipeline/storage.py`. Locate `OddsBatchProcessor.add` (line 1351). Replace the entire method (lines 1351-1379) with:

```python
    def add(
        self,
        event_id: str,
        provider: str,
        market: str,
        outcome: str,
        odds: float,
        point: float = None,
        provider_meta: dict = None,
        bid: float = None,
        ask: float = None,
        depth_usd: float = None,
        scope: str = "ft",
    ):
        """Add odds record to batch (will be processed on flush).

        `scope` identifies the temporal/structural market scope (e.g. 'ft',
        'reg', '1h'). See backend/src/constants.py:VALID_SCOPES. Defaults to
        'ft' so existing callers continue to work; extractors with scope
        ambiguity (Pinnacle period, Altenar typeId) must pass it explicitly.
        """
        # Use tuple key to deduplicate (scope included — two rows at different
        # scopes are physically different markets, must not dedupe each other).
        key = (event_id, provider, market, outcome, point, scope)
        self._pending[key] = {
            "event_id": event_id,
            "provider_id": provider,
            "market": market,
            "outcome": outcome,
            "odds": odds,
            "point": point,
            "provider_meta": provider_meta,
            "bid": bid,
            "ask": ask,
            "depth_usd": depth_usd,
            "scope": scope,
        }
        self._market_counts[market] = self._market_counts.get(market, 0) + 1

        if len(self._pending) >= self.batch_size:
            self.flush()
```

- [ ] **Step 4: Carry `scope` through `_flush_inner` rows + ON CONFLICT**

In the same file, locate `_flush_inner` (line 1423). Find the row-dict comprehension (lines 1437-1452). Replace it with:

```python
            rows = [
                {
                    "event_id": r["event_id"],
                    "provider_id": r["provider_id"],
                    "market": r["market"],
                    "outcome": r["outcome"],
                    "odds": r["odds"],
                    "point": r.get("point"),
                    "provider_meta": r.get("provider_meta"),
                    "bid": r.get("bid"),
                    "ask": r.get("ask"),
                    "depth_usd": r.get("depth_usd"),
                    "scope": r.get("scope", "ft"),
                    "updated_at": now,
                }
                for r in batch
            ]
```

Then, in the `stmt.on_conflict_do_update(...)` call just below (around line 1455), change the `constraint` argument from `"uq_odds_with_point_nd"` to `"uq_odds_with_point_scope"`:

```python
            stmt = stmt.on_conflict_do_update(
                constraint="uq_odds_with_point_scope",
                set_={
                    "odds": stmt.excluded.odds,
                    "updated_at": stmt.excluded.updated_at,
                    "provider_meta": stmt.excluded.provider_meta,
                    "bid": stmt.excluded.bid,
                    "ask": stmt.excluded.ask,
                    "depth_usd": stmt.excluded.depth_usd,
                },
            ).returning(
```

(Note: `scope` is NOT in the `set_` clause — it's part of the conflict key, so it never updates. A scope change for the same (event, provider, market, outcome, point) means a new row, which is correct.)

- [ ] **Step 5: Carry `scope` through the legacy direct-Odds-insert path**

In the same file, locate `_store_event_odds_with_inversion_check` around line 1269. Find the `Odds(...)` constructor call (around line 1316). Replace the constructor block with the `scope` propagation:

```python
            Odds(
                event_id=event_id,
                provider_id=provider,
                market=market,
                outcome=outcome,
                odds=odds_value,
                point=point,
                provider_meta=provider_meta,
                scope=scope,
            )
```

Then, update the function signature to accept `scope` (around line 1269 — find the `def _store_event_odds_with_inversion_check` line). Add `scope: str = "ft",` to the parameter list. Add scope-propagation in the `existing.provider_meta = provider_meta` block (line 1308) — extend to also set `existing.scope = scope`.

(If `_store_event_odds_with_inversion_check` is no longer called — check via grep — skip this step.)

Run: `cd backend && python -c "from src.pipeline.storage import _store_event_odds_with_inversion_check; import inspect; print(inspect.signature(_store_event_odds_with_inversion_check))"` and confirm `scope` is in the signature, then `cd backend && grep -n '_store_event_odds_with_inversion_check' src/ -r` to find callers.

- [ ] **Step 6: Carry `scope` from market dicts to OddsBatchProcessor.add at the 4 callsites**

In the same file, locate the 4 callsites where `odds_batch.add(...)` or equivalent dispatch happens. From the earlier grep, these are roughly at lines 634, 648, 1236, 1250.

For each callsite, find the surrounding `for market in ...` loop. The market dict will be available. Read `scope = market.get("scope", "ft")` BEFORE the `odds_batch.add(...)` call, then pass `scope=scope` to `add`.

Example pattern (apply to all 4):

```python
        for market in event_data.get("markets", []):
            scope = market.get("scope", "ft")  # NEW: extractor sets this
            # ... existing outcome loop ...
            odds_batch.add(
                event_id=event_id,
                provider=provider,
                market=normalized_market,
                outcome=normalized_outcome,
                odds=odds_value,
                point=point,
                provider_meta=provider_meta,
                scope=scope,  # NEW
            )
```

Run: `cd backend && grep -n 'odds_batch.add(' src/pipeline/storage.py` to verify all 4 sites get the `scope=scope` argument.

- [ ] **Step 7: Run storage tests**

Run: `cd backend && TEST_DATABASE_URL=postgresql://arnold:$DB_PASSWORD@localhost:5432/arnold_test pytest tests/pipeline/test_storage_scope.py -v`
Expected: PASS (or SKIP if no Postgres test DB).

- [ ] **Step 8: Run existing storage tests to confirm no regression**

Run: `cd backend && pytest tests/pipeline/ -v --tb=short`
Expected: All previously-passing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add backend/src/pipeline/storage.py backend/tests/pipeline/test_storage_scope.py
git commit -m "feat(storage): plumb scope through OddsBatchProcessor"
```

---

## Task 4: Pinnacle extractor emits `scope`

**Files:**
- Modify: `backend/src/providers/pinnacle.py` (the `parse_markets` method around line 400)
- Test: `backend/tests/providers/test_pinnacle_scope.py` (NEW)

**Why:** Pinnacle is the source of the bug. Period 0 hockey is OT-inclusive (`ft`); period 6 is regulation only (`reg`). This is the only extractor where the mapping is non-trivial today.

- [ ] **Step 1: Write failing test**

Create `backend/tests/providers/test_pinnacle_scope.py`:

```python
"""Pinnacle extractor sets scope='ft' on period 0 and 'reg' on period 6 hockey."""
from __future__ import annotations

from src.providers.pinnacle import PinnacleProvider


def _make_market(period: int, market_type: str = "total"):
    return {
        "status": "open",
        "type": market_type,
        "period": period,
        "isAlternate": False,
        "lineId": 1,
        "matchupId": 1,
        "prices": [
            {"designation": "over", "price": -110, "points": 4.5},
            {"designation": "under", "price": -110, "points": 4.5},
        ],
    }


def test_period_0_emits_scope_ft():
    p = PinnacleProvider({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=0)], sport="ice_hockey")
    assert all(m.get("scope") == "ft" for m in parsed), \
        f"expected all scope='ft', got {[m.get('scope') for m in parsed]}"


def test_period_6_hockey_emits_scope_reg():
    p = PinnacleProvider({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=6)], sport="ice_hockey")
    assert parsed, "no markets parsed"
    assert all(m.get("scope") == "reg" for m in parsed), \
        f"expected scope='reg', got {[m.get('scope') for m in parsed]}"


def test_period_6_hockey_1x2_also_emits_scope_reg():
    p = PinnacleProvider({"id": "pinnacle"})
    parsed = p._parse_markets([_make_market(period=6, market_type="moneyline")], sport="ice_hockey")
    assert parsed
    assert all(m.get("scope") == "reg" for m in parsed)
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && pytest tests/providers/test_pinnacle_scope.py -v`
Expected: FAIL — `scope` key missing on parsed market dicts.

(Note: the exact method name `_parse_markets` and signature should be confirmed before running. If different, adapt the test. Use `grep -n 'def _parse_markets\|def parse_markets' backend/src/providers/pinnacle.py` to locate.)

- [ ] **Step 3: Add scope to emitted market dicts**

Edit `backend/src/providers/pinnacle.py`. Locate the `_parse_markets` method (around line 400). Find the period-0 emission block (around lines 444-460, "Period 0 (full game / OT-included)") and the period-6 emission block (lines 462+, "Period 6 (regulation time) — ice hockey").

For each block where a parsed market dict is appended to `parsed`, add `"scope": "ft"` (for period 0) or `"scope": "reg"` (for period 6) to the dict. The exact shape will look like:

```python
            # ── Period 0 (full game / OT-included) ──
            if period == 0:
                if market_type in self._CORE_TYPES:
                    # ... existing code that builds the market dict ...
                    parsed.append({
                        "type": market_type,
                        "outcomes": outcomes,
                        "provider_meta": market_meta,
                        "scope": "ft",  # NEW
                    })

            # ── Period 6 (regulation time) — ice hockey ──
            elif period == 6:
                if market_type in self._CORE_TYPES:
                    # ... existing code ...
                    parsed.append({
                        "type": market_type,
                        "outcomes": outcomes,
                        "provider_meta": market_meta,
                        "scope": "reg",  # NEW — regulation only
                    })
```

Read the actual surrounding code first (`grep -nA 30 'Period 0 (full game' backend/src/providers/pinnacle.py | head -60`) and adapt the exact insertion point — the emission block may use a helper rather than literal dict construction.

Esports map periods (1-5) and any other branches: emit `"scope": f"map_{period}"` for esports map markets, else `"scope": "ft"`.

- [ ] **Step 4: Run Pinnacle scope tests**

Run: `cd backend && pytest tests/providers/test_pinnacle_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Run existing Pinnacle tests to confirm no regression**

Run: `cd backend && pytest tests/providers/ -v -k pinnacle --tb=short`
Expected: All previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/providers/pinnacle.py backend/tests/providers/test_pinnacle_scope.py
git commit -m "feat(pinnacle): emit scope='ft'/'reg' per market period"
```

---

## Task 5: Altenar extractor emits `scope`

**Files:**
- Modify: `backend/src/providers/altenar.py` (typeId mapping around line 70-90; market emission)
- Test: `backend/tests/providers/test_altenar_scope.py` (NEW)

**Why:** Altenar exposes both OT-inclusive and regulation-only via `typeId` (412 vs 18 for hockey, 225 vs 18 for basket/AF, 258 for baseball extras). Currently the extractor SKIPS typeId=18 for hockey ([altenar.py:313-316](backend/src/providers/altenar.py#L313-L316)). With the scope dimension, both can be stored — the scanner will only surface the canonical one. This makes the data richer for future use.

- [ ] **Step 1: Write failing test**

Create `backend/tests/providers/test_altenar_scope.py`:

```python
"""Altenar extractor sets scope based on typeId."""
from __future__ import annotations

import pytest

from src.providers.altenar import AltenarProvider


# Sanity: the typeId map is the source of truth for which scope each market emits.
# This test catches the case where someone adds a new typeId but forgets to
# tag it with a scope.
def test_altenar_typeid_scope_map_is_complete():
    """Every typeId in MARKET_TYPE_MAPPING must have a known scope."""
    # The MARKET_TYPE_MAPPING is in the provider source.
    from src.providers.altenar import AltenarProvider
    p = AltenarProvider({"id": "betinia"})
    mapping = getattr(p, "MARKET_TYPE_MAPPING", None) or getattr(p.__class__, "MARKET_TYPE_MAPPING", None)
    assert mapping is not None, "MARKET_TYPE_MAPPING must be discoverable"
    # Every entry should have a corresponding scope mapping
    from src.providers.altenar import TYPEID_SCOPE
    for type_id in mapping:
        assert type_id in TYPEID_SCOPE, f"typeId {type_id} ({mapping[type_id]}) has no scope mapping"


@pytest.mark.parametrize("type_id,expected_scope", [
    (412, "ft"),   # hockey total incl. OT+pens
    (18, "reg"),   # hockey/football regulation total
    (225, "ft"),   # basketball/AF total incl. OT
    (258, "ft"),   # baseball total incl. extras
    (406, "ft"),   # hockey moneyline incl. OT
    (1, "ft"),     # football 1x2 (90+stoppage is "ft")
])
def test_typeid_scope_mapping(type_id, expected_scope):
    from src.providers.altenar import TYPEID_SCOPE
    assert TYPEID_SCOPE[type_id] == expected_scope
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && pytest tests/providers/test_altenar_scope.py -v`
Expected: FAIL — `TYPEID_SCOPE` does not exist yet.

- [ ] **Step 3: Add `TYPEID_SCOPE` dict and stop skipping typeId=18 for hockey**

Edit `backend/src/providers/altenar.py`. Locate the `MARKET_TYPE_MAPPING` dict (around line 70). Immediately AFTER it, add:

```python
# Period/structural scope per Altenar typeId. Each entry MUST be present
# for every typeId in MARKET_TYPE_MAPPING — test asserts completeness.
TYPEID_SCOPE: dict[int, str] = {
    # Moneyline / 1x2
    1: "ft",       # Match result (football) — 90+stoppage is canonical "ft"
    186: "ft",     # Winner (tennis, volleyball, MMA)
    219: "ft",     # Winner incl. OT (basketball, AF)
    251: "ft",     # Winner incl. extra innings (baseball)
    406: "ft",     # Winner incl. OT+penalties (ice hockey)
    30001: "ft",   # Esports match winner
    # Total
    18: "reg",     # Total — regulation only (football's "ft" is also 90+stoppage; this is the regulation-only line for sports that have OT)
    189: "ft",     # Total games (tennis)
    225: "ft",     # Total incl. OT (basketball, AF)
    238: "ft",     # Total points (volleyball, table tennis)
    258: "ft",     # Total incl. extra innings (baseball)
    412: "ft",     # Total incl. OT+penalties (ice hockey)
    # Spread
    16: "ft",      # Handicap (handball, rugby)
    187: "ft",     # Game handicap (tennis)
    223: "ft",     # Spread incl. OT (basketball, AF)
    237: "ft",     # Point handicap (volleyball, table tennis)
    256: "ft",     # Handicap incl. extra innings (baseball)
    410: "ft",     # Handicap incl. OT+penalties (ice hockey)
}
```

> **Note on football typeId 18:** for football the regulation-only distinction is meaningless — "Full Time" is 90+stoppage and `typeId=18` is exactly that. So for football, typeId 18 = "ft" semantically. But for hockey, typeId 18 = "reg" (and is currently skipped). The cleanest mapping is to keep typeId 18 = "reg" globally and let the per-sport scope filter at the scanner sort it out. Football totals at scope='reg' won't be surfaced for football (canonical is 'ft'), which would drop football totals. Fix: split the mapping per (typeId, sport) OR keep typeId 18 = "ft" and add a special-case in the parser for ice hockey.

> **Pick the simpler path:** the parser already knows the sport when emitting. Change `TYPEID_SCOPE` to a function:

Replace the `TYPEID_SCOPE` dict above with a function — delete the dict and add instead:

```python
# Period/structural scope per Altenar typeId. Sport-aware because typeId 18
# means "regulation only" for hockey (OT-inclusive lives at 412) but means
# "Full Time" for football (which doesn't have OT in standard markets).
def scope_for(type_id: int, sport: str | None) -> str:
    # Hockey regulation-only total/spread
    if type_id == 18 and sport == "ice_hockey":
        return "reg"
    # Everything else maps to 'ft' by default. New typeIds with scope ambiguity
    # must be added here explicitly.
    _FT_TYPEIDS = {
        1, 186, 219, 251, 406, 30001,    # moneyline / 1x2
        18, 189, 225, 238, 258, 412,     # total
        16, 187, 223, 237, 256, 410,     # spread
    }
    if type_id in _FT_TYPEIDS:
        return "ft"
    # Unknown typeId — caller decides whether to emit. Default 'ft' to keep
    # backward compatibility; a unit test guards completeness.
    return "ft"


# Sentinel exported for completeness-check tests
TYPEID_SCOPE = {
    tid: scope_for(tid, None) for tid in (
        1, 186, 219, 251, 406, 30001,
        18, 189, 225, 238, 258, 412,
        16, 187, 223, 237, 256, 410,
    )
}
```

Update the test to match — `TYPEID_SCOPE[18]` is `"ft"` (football default), and a separate parametrized test confirms `scope_for(18, "ice_hockey") == "reg"`:

Edit `backend/tests/providers/test_altenar_scope.py` — replace the parametrized test with:

```python
@pytest.mark.parametrize("type_id,sport,expected_scope", [
    (412, "ice_hockey", "ft"),       # hockey total incl. OT+pens
    (18, "ice_hockey", "reg"),       # hockey regulation total
    (18, "football", "ft"),          # football typeId 18 = Full Time (no OT)
    (225, "basketball", "ft"),       # basketball total incl. OT
    (258, "baseball", "ft"),         # baseball total incl. extras
    (406, "ice_hockey", "ft"),       # hockey moneyline incl. OT
    (1, "football", "ft"),           # football 1x2
])
def test_typeid_scope_mapping(type_id, sport, expected_scope):
    from src.providers.altenar import scope_for
    assert scope_for(type_id, sport) == expected_scope
```

- [ ] **Step 4: Remove the typeId=18 skip for hockey**

In the same file (`backend/src/providers/altenar.py`), locate lines 313-316:

```python
                # Ice hockey: skip regulation-only total (typeId 18) — OT-inclusive
                # variant (412) preferred. Pinnacle sharp odds include OT.
                if sport == "ice_hockey" and market_type_id == 18:
                    continue
```

Replace with:

```python
                # Note: regulation-only hockey total (typeId 18) is now stored
                # with scope='reg' instead of being skipped — see scope_for().
                # The scanner refuses to compare across scopes, so this won't
                # produce false arbs against Pinnacle's period-0 OT-inclusive odds.
```

(I.e., delete the `continue` line + the surrounding 2 comment lines. The replacement is a documentation-only comment block.)

- [ ] **Step 5: Set `scope` on each emitted market**

In the same file, find where markets are emitted (look for `parsed.append(` or similar around line 320+, after the market_type lookup). Where the market dict is built, add:

```python
                market_dict["scope"] = scope_for(market_type_id, sport)
```

OR if the market is built inline:

```python
                parsed.append({
                    "type": market_type,
                    "outcomes": outcomes,
                    "provider_meta": {...},
                    "scope": scope_for(market_type_id, sport),
                })
```

Grep first to find the exact emission shape: `grep -nA 5 'market_type = self.MARKET_TYPE_MAPPING' backend/src/providers/altenar.py`.

- [ ] **Step 6: Run Altenar scope tests**

Run: `cd backend && pytest tests/providers/test_altenar_scope.py -v`
Expected: PASS.

- [ ] **Step 7: Run existing Altenar tests**

Run: `cd backend && pytest tests/providers/ -v -k altenar --tb=short`
Expected: All previously-passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add backend/src/providers/altenar.py backend/tests/providers/test_altenar_scope.py
git commit -m "feat(altenar): emit scope from typeId; stop skipping hockey typeId=18"
```

---

## Task 6: Gecko V2 extractor emits `scope`

**Files:**
- Modify: `backend/src/providers/gecko_v2.py` (market_template handling around line 100-130; emission around line 460+)
- Test: `backend/tests/providers/test_gecko_v2_scope.py` (NEW)

**Why:** Gecko V2 distinguishes `TGOUOT` (incl. OT) from `TGOU` (regulation) for hockey; `MHCPNOT` is hockey regulation handicap. Mirror Altenar's approach.

- [ ] **Step 1: Write failing test**

Create `backend/tests/providers/test_gecko_v2_scope.py`:

```python
"""Gecko V2 extractor sets scope from market_template."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("template,sport,expected", [
    ("TGOUOT", "ice_hockey", "ft"),    # hockey total incl. OT
    ("TGOU", "ice_hockey", "reg"),     # hockey regulation total
    ("MHCPNOT", "ice_hockey", "reg"),  # hockey regulation handicap
    ("MTG2W", "football", "ft"),       # football total (90+stoppage)
    ("MW3W", "football", "ft"),        # football 1x2
    ("MW2W", "tennis", "ft"),          # tennis 2-way moneyline
])
def test_template_scope_mapping(template, sport, expected):
    from src.providers.gecko_v2 import scope_for_template
    assert scope_for_template(template, sport) == expected
```

- [ ] **Step 2: Verify test fails**

Run: `cd backend && pytest tests/providers/test_gecko_v2_scope.py -v`
Expected: FAIL — `scope_for_template` does not exist.

- [ ] **Step 3: Add `scope_for_template` and emit scope on each market**

Edit `backend/src/providers/gecko_v2.py`. After the `MARKET_TEMPLATE_MAPPING` dict (around line 100-130), add:

```python
def scope_for_template(template: str, sport: str | None) -> str:
    """Map Gecko V2 market_template + sport to canonical scope.

    Hockey regulation-only variants must be tagged 'reg' so the scanner
    doesn't compare them against OT-inclusive odds from other books.
    """
    # Hockey regulation-only variants
    if sport == "ice_hockey" and template in ("TGOU", "MHCPNOT"):
        return "reg"
    return "ft"
```

In the market-emission loop (find with `grep -nA 5 'market_template' backend/src/providers/gecko_v2.py | head -30` — likely around line 460+), add the scope field:

```python
                parsed.append({
                    "type": market_type,
                    "outcomes": outcomes,
                    "provider_meta": {...},
                    "scope": scope_for_template(template, sport),
                })
```

- [ ] **Step 4: Run Gecko V2 scope tests**

Run: `cd backend && pytest tests/providers/test_gecko_v2_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Run existing Gecko V2 tests**

Run: `cd backend && pytest tests/providers/ -v -k gecko --tb=short`
Expected: All previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/providers/gecko_v2.py backend/tests/providers/test_gecko_v2_scope.py
git commit -m "feat(gecko_v2): emit scope from market_template"
```

---

## Task 7: Scanner refuses cross-scope grouping

**Files:**
- Modify: `backend/src/analysis/scanner.py` (`group_odds` around line 1110)
- Test: `backend/tests/analysis/test_scope_enforcement.py` (NEW)

**Why:** This is the safety gate. Even if an extractor forgets to set scope correctly, the scanner refusing to surface non-canonical-scope opportunities means a wrong row produces silence (no opportunity), not a phantom arb.

- [ ] **Step 1: Write failing test**

Create `backend/tests/analysis/test_scope_enforcement.py`:

```python
"""Scanner.group_odds refuses to bucket cross-scope odds together."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.analysis.scanner import OpportunityScanner


def _odds(provider, market, outcome, value, point=None, scope="ft"):
    return SimpleNamespace(
        provider_id=provider, market=market, outcome=outcome, odds=value,
        point=point, scope=scope,
        updated_at=datetime.now(timezone.utc), bid=None, ask=None,
    )


def _event(sport, odds_list):
    return SimpleNamespace(id="evt:t1", sport=sport, odds=odds_list)


def test_canonical_scope_rows_grouped():
    scanner = OpportunityScanner(session=None)
    ev = _event("ice_hockey", [
        _odds("pinnacle", "total", "over", 1.85, 4.5, "ft"),
        _odds("betinia", "total", "under", 2.35, 4.5, "ft"),
    ])
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    assert "over" in bucket and "under" in bucket
    assert len(bucket["over"]) == 1
    assert len(bucket["under"]) == 1


def test_non_canonical_scope_rows_filtered_out():
    """Pinnacle 'reg' hockey + Betinia 'ft' hockey → only the 'ft' row makes it through."""
    scanner = OpportunityScanner(session=None)
    ev = _event("ice_hockey", [
        _odds("pinnacle", "total", "over", 1.85, 4.5, "reg"),   # WRONG scope
        _odds("betinia", "total", "under", 2.35, 4.5, "ft"),    # canonical
    ])
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    # Pinnacle 'reg' row must NOT appear in the canonical-scope bucket.
    assert "over" not in bucket or len(bucket["over"]) == 0, \
        "scope='reg' Pinnacle row leaked into canonical bucket"
    assert "under" in bucket
    assert len(bucket["under"]) == 1


def test_iihf_worlds_false_arb_no_longer_groups():
    """The exact 2026-05-25 Slovenia v Italy bug: no cross-scope grouping."""
    scanner = OpportunityScanner(session=None)
    ev = _event("ice_hockey", [
        _odds("pinnacle", "total", "over", 1.85, 4.5, "reg"),
        _odds("pinnacle", "total", "under", 2.00, 4.5, "reg"),
        _odds("betinia", "total", "over", 1.6061, 4.5, "ft"),
        _odds("betinia", "total", "under", 2.35, 4.5, "ft"),
    ])
    grouped = scanner.group_odds(ev, check_staleness=False)
    bucket = grouped.get("total_4.5", {})
    # Only Betinia 'ft' rows should remain — no Pinnacle 'reg' rows.
    providers = {row["provider"] for outcome_rows in bucket.values() for row in outcome_rows}
    assert "pinnacle" not in providers, \
        "Pinnacle 'reg' row reached the canonical-scope bucket — false arb possible"
    assert "betinia" in providers
```

- [ ] **Step 2: Verify test fails**

Run: `cd backend && pytest tests/analysis/test_scope_enforcement.py -v`
Expected: FAIL — current `group_odds` ignores scope, so all 4 IIHF rows end up in the same bucket.

- [ ] **Step 3: Add scope filter at the top of `group_odds`**

Edit `backend/src/analysis/scanner.py`. Locate `group_odds` (line 1110). Find the `for odds in event.odds:` loop (line 1144). Insert this filter BEFORE the `# Skip excluded providers` check (line 1146):

```python
        # Scope filter: only canonical-scope rows for this sport participate in
        # opportunity scanning. Cross-scope comparisons (e.g. regulation vs
        # OT-inclusive hockey totals) are structurally invalid — refusing to
        # group them prevents false arbs like the 2026-05-25 IIHF bug.
        from ..constants import canonical_scope_for
        canonical = canonical_scope_for(getattr(event, "sport", None))
        row_scope = getattr(odds, "scope", "ft")
        if row_scope != canonical:
            logger.debug(
                "scope_filter: drop %s/%s scope=%s (canonical=%s for sport=%s)",
                event.id, odds.provider_id, row_scope, canonical, getattr(event, "sport", None),
            )
            continue
```

The `from ..constants import canonical_scope_for` should ideally be hoisted to the top of the file with the other imports — do that as a separate cleanup edit. To avoid the import on every iteration:

Find the existing imports at the top of `scanner.py` (around line 20). Add to the existing constants import line:

```python
from ..constants import PLATFORM_MAP, PREDICTION_MARKETS, SHARP_PROVIDERS, SIGNAL_ONLY_PROVIDERS, canonical_scope_for
```

Then in `group_odds`, BEFORE the `for odds in event.odds:` loop (line 1144), compute `canonical` once:

```python
        canonical = canonical_scope_for(getattr(event, "sport", None))
```

And use it inside the loop (removing the redundant computation):

```python
        for odds in event.odds:
            # Scope filter — see Task 7 plan + spec
            row_scope = getattr(odds, "scope", "ft")
            if row_scope != canonical:
                logger.debug(
                    "scope_filter: drop %s/%s scope=%s (canonical=%s for sport=%s)",
                    event.id, odds.provider_id, row_scope, canonical, getattr(event, "sport", None),
                )
                continue
            # ... rest of the existing loop body ...
```

- [ ] **Step 4: Run scope enforcement tests**

Run: `cd backend && pytest tests/analysis/test_scope_enforcement.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 5: Run existing scanner tests to confirm no regression**

Run: `cd backend && pytest tests/analysis/ -v --tb=short`
Expected: All previously-passing tests still pass. If any existing test creates an Odds/SimpleNamespace without a `scope` attribute, it'll work (defaults to `"ft"` via `getattr`).

- [ ] **Step 6: Commit**

```bash
git add backend/src/analysis/scanner.py backend/tests/analysis/test_scope_enforcement.py
git commit -m "feat(scanner): refuse cross-scope grouping in group_odds"
```

---

## Task 8: Integration test — IIHF Worlds fixture

**Files:**
- Test: `backend/tests/integration/test_iihf_worlds_scope.py` (NEW)

**Why:** End-to-end regression test that pins the original bug. If any future refactor reintroduces cross-scope matching, this test fires.

- [ ] **Step 1: Write the integration test**

Create `backend/tests/integration/test_iihf_worlds_scope.py`:

```python
"""End-to-end regression: the 2026-05-25 Slovenia v Italy IIHF false-arb bug.

Seeds the DB with the exact odds set that produced the false +3.66% arb,
runs the value/arb scanner, and asserts zero opportunities for that event.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Odds, Provider, _run_pg_migrations
from src.analysis.scanner import OpportunityScanner


@pytest.fixture
def db_session():
    import os
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL not set to a Postgres URL")
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    _run_pg_migrations(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    s.add(Provider(id="pinnacle", name="Pinnacle"))
    s.add(Provider(id="betinia", name="Betinia"))
    s.commit()
    yield s
    s.rollback()
    s.close()
    Base.metadata.drop_all(eng)


def test_iihf_slovenia_italy_false_arb_does_not_surface(db_session):
    """Replays the 2026-05-25 fixture. Pinnacle period=6 reg vs Betinia ft must
    NOT produce a total/spread opportunity for this event."""
    now = datetime.now(timezone.utc)
    s = db_session
    s.add(Event(
        id="evt:iihf:slov:ita:20260525",
        sport="ice_hockey",
        league="IIHF World Championship",
        home_team="slovenia",
        away_team="italy",
        start_time=now + timedelta(hours=1),
    ))
    s.flush()

    # Pinnacle period=6 (regulation only) — what we actually had in production
    s.add(Odds(
        event_id="evt:iihf:slov:ita:20260525",
        provider_id="pinnacle", market="total", outcome="over",
        odds=1.8547, point=4.5, scope="reg",
        provider_meta={"period": 6}, updated_at=now,
    ))
    s.add(Odds(
        event_id="evt:iihf:slov:ita:20260525",
        provider_id="pinnacle", market="total", outcome="under",
        odds=2.00, point=4.5, scope="reg",
        provider_meta={"period": 6}, updated_at=now,
    ))
    # Betinia (Altenar typeId 412) — incl. OT+penalties
    s.add(Odds(
        event_id="evt:iihf:slov:ita:20260525",
        provider_id="betinia", market="total", outcome="over",
        odds=1.6061, point=4.5, scope="ft",
        updated_at=now,
    ))
    s.add(Odds(
        event_id="evt:iihf:slov:ita:20260525",
        provider_id="betinia", market="total", outcome="under",
        odds=2.35, point=4.5, scope="ft",
        updated_at=now,
    ))
    s.commit()

    scanner = OpportunityScanner(session=s)
    event = s.query(Event).filter_by(id="evt:iihf:slov:ita:20260525").one()

    # Pre-fix this would yield a value bet or arb. Post-fix: empty.
    value_bets = scanner.scan_value([event])
    arbs = scanner.scan_arbitrage([event]) if hasattr(scanner, "scan_arbitrage") else []

    total_market_value = [vb for vb in value_bets if vb.market == "total"]
    total_market_arbs = [a for a in arbs if a.market == "total"]

    assert not total_market_value, (
        f"BUG REGRESSION: value bet surfaced for cross-scope hockey total: {total_market_value}"
    )
    assert not total_market_arbs, (
        f"BUG REGRESSION: arb surfaced for cross-scope hockey total: {total_market_arbs}"
    )
```

> **If `scan_arbitrage` doesn't exist on `OpportunityScanner`:** find the arb-detection method via `grep -n 'def scan_\|ArbOpportunity' backend/src/analysis/scanner.py` and call whatever the actual public arb method is. The test body is adaptable — what matters is asserting no `total` opportunity surfaces.

- [ ] **Step 2: Run the integration test**

Run: `cd backend && TEST_DATABASE_URL=postgresql://arnold:$DB_PASSWORD@localhost:5432/arnold_test pytest tests/integration/test_iihf_worlds_scope.py -v`
Expected: PASS (or SKIP if no Postgres test DB).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_iihf_worlds_scope.py
git commit -m "test(scanner): integration regression for IIHF Worlds scope bug"
```

---

## Task 9: Health metric — `unscannable_markets`

**Files:**
- Modify: `backend/src/api/routes/extraction.py` (`/health/extraction` handler)
- Test: `backend/tests/api/test_health_unscannable_markets.py` (NEW)

**Why:** Operational visibility. If a soft provider quietly starts shipping non-canonical-scope rows (or stops shipping canonical), we want a warning rather than silent opportunity loss.

- [ ] **Step 1: Locate the /health/extraction handler**

Run: `grep -nA 5 'def.*health.*extraction\|@router.*health/extraction' backend/src/api/routes/extraction.py | head -30`

Identify the handler function name and where the response dict is built.

- [ ] **Step 2: Write failing test**

Create `backend/tests/api/test_health_unscannable_markets.py`:

```python
"""/health/extraction surfaces an unscannable_markets count for visibility."""
from __future__ import annotations

import pytest

# Implementation note: this test asserts the presence + shape of the metric.
# Full integration with the FastAPI test client lives in the broader API tests;
# here we just ensure the metric exists in the response payload structure.


def test_unscannable_markets_in_health_payload():
    """The unscannable_markets metric counts (event_id, market, point) triples
    where Pinnacle has a non-canonical scope row and no soft book has the
    canonical-scope row for that market."""
    from src.api.routes.extraction import _compute_unscannable_markets
    # Call with empty DB-equivalent input; expect 0.
    assert _compute_unscannable_markets(odds_rows=[]) == 0

    # Two rows: Pinnacle reg hockey total + no soft canonical → unscannable.
    rows = [
        {"event_id": "e1", "provider_id": "pinnacle", "sport": "ice_hockey",
         "market": "total", "point": 4.5, "scope": "reg"},
    ]
    assert _compute_unscannable_markets(odds_rows=rows) == 1

    # Add a soft 'ft' row for the same market — now scannable.
    rows.append(
        {"event_id": "e1", "provider_id": "betinia", "sport": "ice_hockey",
         "market": "total", "point": 4.5, "scope": "ft"},
    )
    assert _compute_unscannable_markets(odds_rows=rows) == 0
```

- [ ] **Step 3: Verify test fails**

Run: `cd backend && pytest tests/api/test_health_unscannable_markets.py -v`
Expected: FAIL — `_compute_unscannable_markets` does not exist.

- [ ] **Step 4: Implement `_compute_unscannable_markets`**

Edit `backend/src/api/routes/extraction.py`. Add this helper (near the top of the file, after imports):

```python
def _compute_unscannable_markets(odds_rows: list[dict]) -> int:
    """Count (event_id, market, point) triples where Pinnacle has a
    non-canonical-scope row AND no other provider has the canonical-scope row.

    These are markets where we have a sharp baseline at the wrong scope for
    the sport — they silently drop out of opportunity scanning. Surface them
    here so a creeping data-quality regression is visible.
    """
    from collections import defaultdict
    from ...constants import canonical_scope_for

    # Group by (event_id, market, point)
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in odds_rows:
        by_key[(r["event_id"], r["market"], r.get("point"))].append(r)

    count = 0
    for (event_id, market, point), rows in by_key.items():
        # Find the sport from any row in the group (all share the event)
        sport = rows[0].get("sport")
        canonical = canonical_scope_for(sport)

        # Does Pinnacle have a non-canonical scope row?
        has_pinnacle_noncanonical = any(
            r["provider_id"] == "pinnacle" and r.get("scope", "ft") != canonical
            for r in rows
        )
        # Does anyone have a canonical-scope row?
        has_canonical = any(r.get("scope", "ft") == canonical for r in rows)

        if has_pinnacle_noncanonical and not has_canonical:
            count += 1

    return count
```

Then integrate the metric into the health endpoint. Find the response-building code (typically a dict literal) and add a new key:

```python
        # Compute unscannable_markets — see _compute_unscannable_markets docstring.
        odds_rows = session.execute(text(
            "SELECT o.event_id, o.provider_id, e.sport, o.market, o.point, o.scope "
            "FROM odds o JOIN events e ON e.id = o.event_id "
            "WHERE e.start_time > NOW() AND e.start_time < NOW() + INTERVAL '24 hours'"
        )).mappings().all()
        unscannable = _compute_unscannable_markets(list(odds_rows))

        response = {
            # ... existing keys ...
            "unscannable_markets": unscannable,
            "unscannable_markets_status": "WARNING" if unscannable > 10 else "OK",
        }
```

(Adapt to the actual handler shape — read the surrounding code first.)

- [ ] **Step 5: Run the test**

Run: `cd backend && pytest tests/api/test_health_unscannable_markets.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes/extraction.py backend/tests/api/test_health_unscannable_markets.py
git commit -m "feat(health): report unscannable_markets for scope-mismatched coverage"
```

---

## Task 10: Pre-deploy validation + deploy

**Files:** none (operational)

**Why:** Multi-agent coordination per CLAUDE.md — deploy via `server-deploy.sh`, verify rollout.

- [ ] **Step 1: Run the full backend test suite locally**

Run: `cd backend && pytest tests/ -v --tb=short`
Expected: All tests pass (or skip cleanly when Postgres test DB is missing).

- [ ] **Step 2: Lint check**

Run: `cd backend && ruff check src/ && ruff format --check src/`
Expected: No errors. Fix any reported issues.

- [ ] **Step 3: Check no other agent holds the deploy lock**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh status && pgrep -fa 'server-deploy.sh' && lsof /opt/arnold/.deploy.lock 2>/dev/null"`
Expected: Status shows no active deploy. `pgrep` returns nothing. `lsof` empty.

- [ ] **Step 4: Push branch + open PR**

```bash
git push -u origin HEAD
gh pr create --title "feat(scanner): period-scope dimension on canonical odds" --body "$(cat <<'EOF'
## Summary
- Add `odds.scope` VARCHAR(16) column with backfill for Pinnacle hockey period=6 rows
- Pinnacle/Altenar/Gecko V2 extractors emit `scope` from their native scope identifiers
- Scanner's `group_odds` filters by `SPORT_CANONICAL_SCOPE[sport]`, refusing cross-scope grouping
- `/health/extraction` surfaces `unscannable_markets` count
- Regression test pins the 2026-05-25 Slovenia v Italy IIHF false-arb fix

## Why
Manual audit caught the scanner surfacing a +3.66% "arb" on Slovenia v Italy IIHF Worlds where Lodur Under 4.5 was full-match incl. OT and Pinnacle Over 4.5 was regulation only — both legs could lose. Root cause: canonical odds schema didn't capture market scope, so the scanner joined across scope boundaries.

## Test plan
- [ ] `cd backend && pytest tests/` all green
- [ ] Local Postgres test DB: scope-tagged tests pass
- [ ] Post-deploy: `SELECT scope, COUNT(*) FROM odds GROUP BY scope` → ~`ft` 395k, `reg` 5,656
- [ ] Post-deploy: zero opportunities surface for the live IIHF Worlds event(s)
- [ ] `/health/extraction` returns `unscannable_markets` key

Spec: docs/superpowers/specs/2026-05-25-period-scope-canonical-odds-design.md
EOF
)"
```

- [ ] **Step 5: Deploy after PR review/merge**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"`
Expected: Deploy completes; health endpoint responds within 2 min.

- [ ] **Step 6: Verify migration ran**

Run: `ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"SELECT scope, COUNT(*) FROM odds GROUP BY scope ORDER BY scope;\""`
Expected: Two rows — `ft` ~395k, `reg` ~5,656 (numbers will drift with new extractions but ratios should hold).

- [ ] **Step 7: Verify scanner is scope-aware in production**

Run: `ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"SELECT op.id, op.market, op.point FROM opportunities op JOIN events e ON e.id = op.event_id WHERE e.sport = 'ice_hockey' AND op.market IN ('total','spread') AND e.league = 'IIHF World Championship';\""`
Expected: Zero rows (IIHF hockey total/spread opportunities now structurally impossible).

- [ ] **Step 8: Verify health metric**

Run: `ssh root@148.251.40.251 "curl -sf -u arnold:\$PASSWORD https://148.251.40.251/health/extraction | jq .unscannable_markets"`
Expected: Some integer (likely 5-15 in the IIHF/KHL/SHL active-window). Confirm key is present.

- [ ] **Step 9: Tail logs for unexpected scope-filter drops**

Run: `ssh root@148.251.40.251 "cd /opt/arnold && docker compose logs backend --tail=200 | grep scope_filter | head -20"`
Expected: Some `scope_filter: drop` debug lines for hockey period=6 — expected. No errors.

---

## Self-Review (post-write)

**Spec coverage:**
- ✅ Schema change (Task 2)
- ✅ Backfill migration (Task 2 step 5)
- ✅ Per-extractor scope mapping: Pinnacle (Task 4), Altenar (Task 5), Gecko V2 (Task 6)
- ✅ Scanner scope enforcement (Task 7)
- ✅ Regression test for the original bug (Task 8)
- ✅ `unscannable_markets` health metric (Task 9)
- ✅ Deploy + verification (Task 10)
- ✅ Kambi/Spectate/etc. → spec said "default 'ft', explicit mapping deferred to implementation discovery" → handled by column default; no task needed unless audit during implementation surfaces a non-canonical typeId

**Placeholder scan:**
- One soft TBD in Task 9 — the response dict shape is "(adapt to actual handler shape — read the surrounding code first)". This is acceptable for handler integration where the existing shape isn't fully knowable from the spec; the helper function `_compute_unscannable_markets` is fully specified and tested.
- Task 5 step 3 contains a meta-discussion of the typeId 18 mapping choice — this is intentional design documentation that the implementing engineer needs to make the right call. The final decision (scope_for function) is fully specified.

**Type consistency:**
- `scope` is `str` everywhere (column type `VARCHAR(16)`, function arg `str`, dict value `str`)
- `canonical_scope_for(sport: str | None) -> str` matches its callers in Task 7
- `OddsBatchProcessor.add(scope: str = "ft")` matches the column default

**Scope check:** single implementation plan covering one cohesive change. No subsystem splits needed.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-25-period-scope-canonical-odds.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
