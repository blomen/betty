# Opp Snapshots — CLV Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every scanner-detected opportunity into a new `opp_snapshots` table, then backfill same-provider CLV, vs-Pinnacle CLV, and (for arbs) closing prob sum once each event starts.

**Architecture:** New `opp_snapshots` table with detection-time fields frozen on first sighting and CLV fields backfilled by a job mirroring `BetService.snapshot_closing_odds()`. Snapshot upsert is wired inline into the three existing `OpportunityRepo` upsert methods so it runs atomically with the live opp write. Backfill runs in the existing scheduler settlement hook after the bet-CLV pass.

**Tech Stack:** Python 3.12, SQLAlchemy ORM, Alembic migrations, PostgreSQL 16 (production) / SQLite in-memory (tests), pytest.

**Design spec:** [`docs/superpowers/specs/2026-05-29-opp-snapshots-clv-tracking-design.md`](../specs/2026-05-29-opp-snapshots-clv-tracking-design.md)

---

## File Structure

| File | Purpose | Created/Modified |
|------|---------|------------------|
| `backend/alembic/versions/005_add_opp_snapshots.py` | Migration: create `opp_snapshots` table + indexes | Create |
| `backend/src/db/models.py` | New `OppSnapshot` ORM model | Modify (append) |
| `backend/src/services/opp_snapshot_service.py` | `OppSnapshotService` with `upsert_from_opportunity()` and `compute_closing_clv()` | Create |
| `backend/src/repositories/opportunity_repo.py` | Add inline snapshot calls in `upsert_value`, `upsert_arb`, `upsert_reverse_value` | Modify |
| `backend/src/pipeline/scheduler.py` | Call `OppSnapshotService.compute_closing_clv()` in `_run_settlement` | Modify (~10 lines) |
| `backend/tests/services/test_opp_snapshot_service.py` | Unit tests for service: upsert + backfill | Create |
| `backend/tests/test_opp_repo_snapshots.py` | Integration test: repo upserts also produce snapshots | Create |
| `backend/tests/pipeline/test_scheduler_settlement.py` | Test scheduler hook invokes opp-CLV pass | Create or modify (exists?) |

**Boundary rationale:** `OppSnapshotService` owns all snapshot logic (write + backfill). The repo layer calls `service.upsert_from_opportunity(opp)` after its own opp upsert — the repo doesn't know about CLV mechanics; the service doesn't know about scanner internals. The scheduler doesn't know about CLV math at all; it just calls the service.

---

## Task 1: Alembic migration — create `opp_snapshots` table

**Files:**
- Create: `backend/alembic/versions/005_add_opp_snapshots.py`
- Test: `backend/tests/test_opp_snapshots_migration.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_opp_snapshots_migration.py`:

```python
"""Verify the opp_snapshots migration creates the expected schema."""

from sqlalchemy import create_engine, inspect


def test_opp_snapshots_table_has_required_columns():
    """After Base.metadata.create_all, opp_snapshots exists with all design columns."""
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "opp_snapshots" in inspector.get_table_names()

    cols = {c["name"] for c in inspector.get_columns("opp_snapshots")}
    required = {
        "id", "event_id", "type", "market", "outcome1", "point", "scope",
        "provider1_id", "odds1_at_detection", "fair_odds1_at_detection", "edge_pct_at_detection",
        "provider2_id", "outcome2", "odds2_at_detection",
        "first_detected_at", "last_detected_at", "detection_count",
        "time_to_start_minutes_at_detection",
        "provider1_closing_odds", "provider1_closing_age_minutes",
        "provider2_closing_odds", "provider2_closing_age_minutes",
        "pinnacle_closing_fair", "pinnacle_closing_age_minutes",
        "provider_clv_pct", "pinnacle_clv_pct",
        "closing_prob_sum", "was_arb_at_close",
        "clv_computed_at",
    }
    missing = required - cols
    assert not missing, f"Missing columns: {missing}"


def test_opp_snapshots_unique_constraint():
    """Unique on (event_id, market, outcome1, provider1_id, type, scope)."""
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    uniques = inspector.get_unique_constraints("opp_snapshots")
    expected_cols = {"event_id", "market", "outcome1", "provider1_id", "type", "scope"}
    assert any(set(u["column_names"]) == expected_cols for u in uniques), (
        f"Missing unique constraint on {expected_cols}; got {uniques}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_opp_snapshots_migration.py -v`
Expected: FAIL with "opp_snapshots not in table_names" (model doesn't exist yet).

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/005_add_opp_snapshots.py`:

```python
"""Add opp_snapshots table for CLV tracking on all detected opportunities.

Revision ID: 005
Revises: 004
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'opp_snapshots',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('event_id', sa.String, sa.ForeignKey('events.id'), nullable=False),
        sa.Column('type', sa.String, nullable=False),
        sa.Column('market', sa.String, nullable=False),
        sa.Column('outcome1', sa.String, nullable=False),
        sa.Column('point', sa.Float, nullable=True),
        sa.Column('scope', sa.String(16), nullable=False, server_default='ft'),

        sa.Column('provider1_id', sa.String, sa.ForeignKey('providers.id'), nullable=False),
        sa.Column('odds1_at_detection', sa.Float, nullable=False),
        sa.Column('fair_odds1_at_detection', sa.Float, nullable=True),
        sa.Column('edge_pct_at_detection', sa.Float, nullable=True),

        sa.Column('provider2_id', sa.String, sa.ForeignKey('providers.id'), nullable=True),
        sa.Column('outcome2', sa.String, nullable=True),
        sa.Column('odds2_at_detection', sa.Float, nullable=True),

        sa.Column('first_detected_at', sa.DateTime, nullable=False),
        sa.Column('last_detected_at', sa.DateTime, nullable=False),
        sa.Column('detection_count', sa.Integer, nullable=False, server_default='1'),
        sa.Column('time_to_start_minutes_at_detection', sa.Float, nullable=True),

        sa.Column('provider1_closing_odds', sa.Float, nullable=True),
        sa.Column('provider1_closing_age_minutes', sa.Float, nullable=True),
        sa.Column('provider2_closing_odds', sa.Float, nullable=True),
        sa.Column('provider2_closing_age_minutes', sa.Float, nullable=True),
        sa.Column('pinnacle_closing_fair', sa.Float, nullable=True),
        sa.Column('pinnacle_closing_age_minutes', sa.Float, nullable=True),
        sa.Column('provider_clv_pct', sa.Float, nullable=True),
        sa.Column('pinnacle_clv_pct', sa.Float, nullable=True),
        sa.Column('closing_prob_sum', sa.Float, nullable=True),
        sa.Column('was_arb_at_close', sa.Boolean, nullable=True),
        sa.Column('clv_computed_at', sa.DateTime, nullable=True),

        sa.UniqueConstraint(
            'event_id', 'market', 'outcome1', 'provider1_id', 'type', 'scope',
            name='uq_opp_snapshot',
        ),
    )
    op.create_index(
        'ix_opp_snap_provider_type_first',
        'opp_snapshots',
        ['provider1_id', 'type', 'first_detected_at'],
    )
    op.create_index(
        'ix_opp_snap_first_detected_at',
        'opp_snapshots',
        ['first_detected_at'],
    )
    op.create_index(
        'ix_opp_snap_clv_pending',
        'opp_snapshots',
        ['event_id'],
        postgresql_where=sa.text('clv_computed_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_opp_snap_clv_pending', table_name='opp_snapshots')
    op.drop_index('ix_opp_snap_first_detected_at', table_name='opp_snapshots')
    op.drop_index('ix_opp_snap_provider_type_first', table_name='opp_snapshots')
    op.drop_table('opp_snapshots')
```

The test depends on `Base.metadata.create_all` knowing about this table — which requires the ORM model in Task 2. So the test still fails after this step; that's expected.

- [ ] **Step 4: Verify migration syntax**

Run: `cd backend && python -c "import alembic.versions; from alembic.script import ScriptDirectory; from alembic.config import Config; cfg = Config('alembic.ini'); sd = ScriptDirectory.from_config(cfg); print([s.revision for s in sd.walk_revisions()])"`
Expected: `['005', '004', '003', '002', '001']` (newest first, no errors).

- [ ] **Step 5: Commit**

```bash
cd c:/Users/rasmu/betty
git add backend/alembic/versions/005_add_opp_snapshots.py backend/tests/test_opp_snapshots_migration.py
git commit -m "feat(db): add 005 opp_snapshots migration (table + indexes)"
```

---

## Task 2: Add `OppSnapshot` ORM model

**Files:**
- Modify: `backend/src/db/models.py` (append after `Opportunity` class, ~line 890)
- Reuse test: `backend/tests/test_opp_snapshots_migration.py` (from Task 1)

- [ ] **Step 1: Confirm test still fails for the right reason**

Run: `cd backend && pytest tests/test_opp_snapshots_migration.py -v`
Expected: FAIL with `"opp_snapshots" not in inspector.get_table_names()` — model doesn't exist so `Base.metadata.create_all` doesn't create it.

- [ ] **Step 2: Add the ORM model**

Find the end of the `Opportunity` class in `backend/src/db/models.py` (it ends around line 884 with `event = relationship("Event")`). After it, before the next class, add:

```python
class OppSnapshot(Base):
    """
    Frozen detection-time record of every opportunity surfaced by the scanner,
    with closing-line value backfilled once the event starts.

    Sister table to `opportunities`: the live `opportunities` table is ephemeral
    (wiped on each scan cycle); this table persists one row per logical opp
    instance (uniqueness mirrors `opportunities`) for retrospective CLV analysis.

    Detection-time fields are frozen on first sighting; re-detections only bump
    `last_detected_at` and `detection_count`. CLV fields are NULL until the
    backfill job runs after event start_time.
    """

    __tablename__ = "opp_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "market", "outcome1", "provider1_id", "type", "scope",
            name="uq_opp_snapshot",
        ),
        Index("ix_opp_snap_provider_type_first", "provider1_id", "type", "first_detected_at"),
        Index("ix_opp_snap_first_detected_at", "first_detected_at"),
        # Partial index for backfill job (Postgres only — SQLite ignores the
        # postgresql_where kwarg and creates a plain index, which is fine).
        Index("ix_opp_snap_clv_pending", "event_id", postgresql_where=text("clv_computed_at IS NULL")),
    )

    id = Column(Integer, primary_key=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    type = Column(String, nullable=False)  # value | arb | reverse_value
    market = Column(String, nullable=False)
    outcome1 = Column(String, nullable=False)
    point = Column(Float, nullable=True)
    scope = Column(String(16), nullable=False, server_default="ft", default="ft")

    # Leg 1 (always present)
    provider1_id = Column(String, ForeignKey("providers.id"), nullable=False)
    odds1_at_detection = Column(Float, nullable=False)
    fair_odds1_at_detection = Column(Float, nullable=True)
    edge_pct_at_detection = Column(Float, nullable=True)

    # Leg 2 (arb-only; NULL for value/reverse_value)
    provider2_id = Column(String, ForeignKey("providers.id"), nullable=True)
    outcome2 = Column(String, nullable=True)
    odds2_at_detection = Column(Float, nullable=True)

    # Lifecycle
    first_detected_at = Column(DateTime, nullable=False, default=_utcnow)
    last_detected_at = Column(DateTime, nullable=False, default=_utcnow)
    detection_count = Column(Integer, nullable=False, default=1, server_default="1")
    time_to_start_minutes_at_detection = Column(Float, nullable=True)

    # Backfilled at event start (NULL until then)
    provider1_closing_odds = Column(Float, nullable=True)
    provider1_closing_age_minutes = Column(Float, nullable=True)
    provider2_closing_odds = Column(Float, nullable=True)
    provider2_closing_age_minutes = Column(Float, nullable=True)
    pinnacle_closing_fair = Column(Float, nullable=True)
    pinnacle_closing_age_minutes = Column(Float, nullable=True)
    provider_clv_pct = Column(Float, nullable=True)
    pinnacle_clv_pct = Column(Float, nullable=True)
    closing_prob_sum = Column(Float, nullable=True)  # arbs only
    was_arb_at_close = Column(Boolean, nullable=True)  # arbs only
    clv_computed_at = Column(DateTime, nullable=True)

    event = relationship("Event")
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_opp_snapshots_migration.py -v`
Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
cd c:/Users/rasmu/betty
git add backend/src/db/models.py
git commit -m "feat(db): add OppSnapshot ORM model"
```

---

## Task 3: `OppSnapshotService.upsert_from_opportunity()`

**Files:**
- Create: `backend/src/services/opp_snapshot_service.py`
- Create: `backend/tests/services/test_opp_snapshot_service.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/test_opp_snapshot_service.py`:

```python
"""Unit tests for OppSnapshotService."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Event, OppSnapshot, Opportunity, Provider


@pytest.fixture
def basic_setup(db_session):
    """Seed an event + two providers so FK constraints succeed."""
    db_session.add_all([
        Provider(id="pinnacle", name="Pinnacle"),
        Provider(id="unibet", name="Unibet"),
    ])
    db_session.add(Event(
        id="evt-1",
        sport="soccer",
        home_team="A",
        away_team="B",
        start_time=datetime.now(UTC) + timedelta(hours=2),
    ))
    db_session.commit()
    return db_session


def _make_value_opp(provider="unibet", outcome="A", odds=2.10, fair=2.00, edge=5.0):
    return Opportunity(
        type="value",
        event_id="evt-1",
        market="moneyline",
        outcome1=outcome,
        provider1_id=provider,
        odds1=odds,
        provider2_id="pinnacle",
        odds2=fair,
        edge_pct=edge,
        scope="ft",
        is_active=True,
        detected_at=datetime.now(UTC),
    )


def test_first_sighting_inserts_snapshot_with_frozen_fields(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()

    snap = svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    assert snap.id is not None
    assert snap.odds1_at_detection == 2.10
    assert snap.fair_odds1_at_detection == 2.00
    assert snap.edge_pct_at_detection == 5.0
    assert snap.detection_count == 1
    assert snap.first_detected_at == snap.last_detected_at
    assert snap.time_to_start_minutes_at_detection is not None
    assert 115 < snap.time_to_start_minutes_at_detection < 125  # ~120 min


def test_redetection_bumps_count_and_last_seen_only(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp(odds=2.10, fair=2.00, edge=5.0)
    basic_setup.add(opp)
    basic_setup.flush()
    snap1 = svc.upsert_from_opportunity(opp)
    basic_setup.commit()
    original_first = snap1.first_detected_at
    original_odds = snap1.odds1_at_detection
    original_edge = snap1.edge_pct_at_detection

    # Re-detect — same opp, drifted odds (scanner saw it again, edge changed)
    opp.odds1 = 2.05
    opp.edge_pct = 2.5
    snap2 = svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    assert snap2.id == snap1.id  # same row
    assert snap2.detection_count == 2
    assert snap2.last_detected_at >= original_first  # >= because Windows clock can repeat within a tick
    assert snap2.first_detected_at == original_first  # frozen
    assert snap2.odds1_at_detection == original_odds  # frozen
    assert snap2.edge_pct_at_detection == original_edge  # frozen


def test_arb_opp_snapshots_both_legs(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    basic_setup.add(Provider(id="betinia", name="Betinia"))
    basic_setup.commit()

    arb = Opportunity(
        type="arb",
        event_id="evt-1",
        market="moneyline",
        outcome1="A",
        outcome2="B",
        provider1_id="unibet",
        provider2_id="betinia",
        odds1=2.10,
        odds2=2.05,
        edge_pct=1.5,
        scope="ft",
        is_active=True,
        detected_at=datetime.now(UTC),
    )
    basic_setup.add(arb)
    basic_setup.flush()

    snap = svc.upsert_from_opportunity(arb)
    basic_setup.commit()

    assert snap.type == "arb"
    assert snap.provider2_id == "betinia"
    assert snap.outcome2 == "B"
    assert snap.odds2_at_detection == 2.05


def test_value_opp_leg2_fields_are_null(basic_setup):
    """Value opps have leg2 NULL (spec: leg-2 is arb-only)."""
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    snap = svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    assert snap.provider2_id is None
    assert snap.outcome2 is None
    assert snap.odds2_at_detection is None


def test_reverse_value_uses_pinnacle_odds_as_leg1(basic_setup):
    """For reverse_value, leg1 IS Pinnacle (raw), benchmark IS consensus.
    fair_odds1_at_detection captures the consensus number."""
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    rv = Opportunity(
        type="reverse_value",
        event_id="evt-1",
        market="moneyline",
        outcome1="A",
        provider1_id="pinnacle",
        odds1=5.50,  # Pinnacle's raw price
        provider2_id="consensus",
        odds2=5.00,  # consensus fair
        edge_pct=10.0,
        scope="ft",
        is_active=True,
        detected_at=datetime.now(UTC),
    )
    basic_setup.add(rv)
    basic_setup.flush()
    snap = svc.upsert_from_opportunity(rv)
    basic_setup.commit()

    assert snap.type == "reverse_value"
    assert snap.provider1_id == "pinnacle"
    assert snap.odds1_at_detection == 5.50
    assert snap.fair_odds1_at_detection == 5.00  # consensus benchmark
    assert snap.provider2_id is None  # consensus is not a real provider
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/services/test_opp_snapshot_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.opp_snapshot_service'`.

- [ ] **Step 3: Implement the service**

Create `backend/src/services/opp_snapshot_service.py`:

```python
"""OppSnapshotService — persist scanner-detected opportunities and backfill CLV.

Sister to BetService.snapshot_closing_odds() (backend/src/services/bet_service.py:568).
Same closing-time definition: latest odds row at-or-before event.start_time.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.models import Event, OppSnapshot, Opportunity


class OppSnapshotService:
    """Persist opp detections and backfill closing-line value."""

    def __init__(self, db: Session):
        self.db = db

    def upsert_from_opportunity(self, opp: Opportunity) -> OppSnapshot:
        """
        Insert a snapshot on first sighting, or bump last_detected_at +
        detection_count on re-detection. Detection-time fields are frozen
        on first sighting and never overwritten.

        Returns the OppSnapshot row (newly inserted or updated in-place).
        """
        now = datetime.now(UTC)

        existing = (
            self.db.query(OppSnapshot)
            .filter(
                OppSnapshot.event_id == opp.event_id,
                OppSnapshot.market == opp.market,
                OppSnapshot.outcome1 == opp.outcome1,
                OppSnapshot.provider1_id == opp.provider1_id,
                OppSnapshot.type == opp.type,
                OppSnapshot.scope == opp.scope,
            )
            .first()
        )

        if existing is not None:
            existing.last_detected_at = now
            existing.detection_count = (existing.detection_count or 0) + 1
            return existing

        # First sighting — freeze detection-time state.
        # Compute time-to-start if we know event start_time.
        ttk = None
        event = self.db.query(Event).filter(Event.id == opp.event_id).first()
        if event is not None and event.start_time is not None:
            ttk = (event.start_time - now).total_seconds() / 60.0

        # Leg 1 fair-odds benchmark:
        # - value: odds2 is Pinnacle fair (devigged at detect)
        # - arb: leg-1 fair is leg-1's own fair_odds (carried in outcomes JSON);
        #        for the snapshot we leave fair_odds1 as opp.odds2 too — repo
        #        callers can override via _fair_odds1_override if needed
        # - reverse_value: leg-1 is Pinnacle raw, opp.odds2 is consensus fair
        fair_odds1 = opp.odds2 if opp.odds2 and opp.odds2 > 1.0 else None

        # Leg 2 is arb-only per the design spec.
        is_arb = opp.type == "arb"
        provider2_id = opp.provider2_id if is_arb else None
        outcome2 = opp.outcome2 if is_arb else None
        odds2_at_detection = opp.odds2 if is_arb else None

        snap = OppSnapshot(
            event_id=opp.event_id,
            type=opp.type,
            market=opp.market,
            outcome1=opp.outcome1,
            point=opp.point,
            scope=opp.scope,
            provider1_id=opp.provider1_id,
            odds1_at_detection=opp.odds1,
            fair_odds1_at_detection=fair_odds1,
            edge_pct_at_detection=opp.edge_pct,
            provider2_id=provider2_id,
            outcome2=outcome2,
            odds2_at_detection=odds2_at_detection,
            first_detected_at=now,
            last_detected_at=now,
            detection_count=1,
            time_to_start_minutes_at_detection=ttk,
        )
        self.db.add(snap)
        self.db.flush()  # populate PK so caller has it
        return snap
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/services/test_opp_snapshot_service.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/rasmu/betty
git add backend/src/services/opp_snapshot_service.py backend/tests/services/test_opp_snapshot_service.py
git commit -m "feat(services): OppSnapshotService.upsert_from_opportunity"
```

---

## Task 4: Wire snapshot upsert into `OpportunityRepo`

**Files:**
- Modify: `backend/src/repositories/opportunity_repo.py` (three upsert methods)
- Create: `backend/tests/test_opp_repo_snapshots.py`

- [ ] **Step 1: Write the failing integration test**

Create `backend/tests/test_opp_repo_snapshots.py`:

```python
"""Verify each OpportunityRepo upsert also produces an opp_snapshots row."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Event, OppSnapshot, Provider
from src.repositories.opportunity_repo import OpportunityRepo


@pytest.fixture
def repo_setup(db_session):
    db_session.add_all([
        Provider(id="pinnacle", name="Pinnacle"),
        Provider(id="unibet", name="Unibet"),
        Provider(id="betinia", name="Betinia"),
        Provider(id="consensus", name="Consensus"),
    ])
    db_session.add(Event(
        id="evt-1",
        sport="soccer",
        home_team="A",
        away_team="B",
        start_time=datetime.now(UTC) + timedelta(hours=2),
    ))
    db_session.commit()
    return db_session, OpportunityRepo(db_session)


def test_upsert_value_creates_snapshot(repo_setup):
    db, repo = repo_setup
    is_new, opp = repo.upsert_value(
        event_id="evt-1",
        market="moneyline",
        outcome="A",
        provider_id="unibet",
        provider_odds=2.10,
        fair_odds=2.00,
        edge_pct=5.0,
        outcomes_json=[],
    )
    db.commit()

    assert is_new
    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.type == "value"
    assert s.provider1_id == "unibet"
    assert s.odds1_at_detection == 2.10
    assert s.fair_odds1_at_detection == 2.00


def test_upsert_value_redetection_does_not_duplicate_snapshot(repo_setup):
    db, repo = repo_setup
    for _ in range(3):
        repo.upsert_value(
            event_id="evt-1",
            market="moneyline",
            outcome="A",
            provider_id="unibet",
            provider_odds=2.10,
            fair_odds=2.00,
            edge_pct=5.0,
            outcomes_json=[],
        )
    db.commit()

    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    assert snaps[0].detection_count == 3


def test_upsert_arb_creates_snapshot_with_both_legs(repo_setup):
    db, repo = repo_setup
    legs = [
        {"outcome": "A", "provider": "unibet", "odds": 2.10, "edge_pct": 5.0,
         "fair_odds": 2.00, "stake_pct": 50.0},
        {"outcome": "B", "provider": "betinia", "odds": 2.05, "edge_pct": 3.0,
         "fair_odds": 1.99, "stake_pct": 50.0},
    ]
    is_new, opp = repo.upsert_arb(
        event_id="evt-1",
        market="moneyline",
        legs=legs,
        combined_edge_pct=4.0,
        guaranteed_profit_pct=1.5,
    )
    db.commit()

    assert is_new
    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.type == "arb"
    assert s.provider1_id == "unibet"
    assert s.provider2_id == "betinia"
    assert s.odds1_at_detection == 2.10
    assert s.odds2_at_detection == 2.05


def test_upsert_reverse_value_creates_snapshot(repo_setup):
    db, repo = repo_setup
    is_new, opp = repo.upsert_reverse_value(
        event_id="evt-1",
        market="moneyline",
        outcome="A",
        pinnacle_odds=5.50,
        consensus_fair_odds=5.00,
        edge_pct=10.0,
        outcomes_json=[],
    )
    db.commit()

    assert is_new
    snaps = db.query(OppSnapshot).all()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.type == "reverse_value"
    assert s.provider1_id == "pinnacle"
    assert s.odds1_at_detection == 5.50
    assert s.fair_odds1_at_detection == 5.00
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_opp_repo_snapshots.py -v`
Expected: 4 FAILs — all asserting `len(snaps) == 1` but actual is `0` (repo does not snapshot yet).

- [ ] **Step 3: Add snapshot call to `OpportunityRepo.upsert_value`**

In `backend/src/repositories/opportunity_repo.py`, find `upsert_value` (line ~69). Just before each `return` (both the `existing` branch at ~line 117 and the new-row branch at ~line 137), add a snapshot call. To keep things DRY, refactor the method to capture the opp and call snapshot once at the end.

Replace the method body (lines ~88-137) — the final `return` lines specifically — by adding the snapshot call before each return:

Find:
```python
            return False, existing
        else:
            opp = Opportunity(
```

Change to capture the opp and snapshot in one place. The cleanest edit is to assign instead of returning, then snapshot, then return. Replace the entire `upsert_value` method body after the `now = datetime.now(UTC)` line with:

```python
        now = datetime.now(UTC)

        if existing:
            existing.is_active = True
            existing.edge_pct = edge_pct
            existing.provider1_id = provider_id
            existing.odds1 = provider_odds
            existing.provider2_id = "pinnacle"
            existing.odds2 = fair_odds
            existing.outcomes = outcomes_json
            existing.point = point
            existing.detected_at = now
            existing.annotations = annotations
            flag_modified(existing, "outcomes")
            if annotations is not None:
                flag_modified(existing, "annotations")
            opp_obj = existing
            is_new = False
        else:
            opp_obj = Opportunity(
                type="value",
                event_id=event_id,
                market=market,
                outcome1=outcome,
                edge_pct=edge_pct,
                provider1_id=provider_id,
                odds1=provider_odds,
                provider2_id="pinnacle",
                odds2=fair_odds,
                outcomes=outcomes_json,
                point=point,
                is_active=True,
                detected_at=now,
                annotations=annotations,
                scope=scope,
            )
            self.db.add(opp_obj)
            self.db.flush()  # snapshot service needs opp_obj fields populated
            is_new = True

        # Snapshot for CLV tracking (atomic with the opp upsert above).
        from ..services.opp_snapshot_service import OppSnapshotService
        OppSnapshotService(self.db).upsert_from_opportunity(opp_obj)

        return is_new, opp_obj
```

- [ ] **Step 4: Apply the same pattern to `upsert_arb`**

In `backend/src/repositories/opportunity_repo.py`, find `upsert_arb` (line ~139). Apply the same refactor: capture the opp, snapshot, then return. Replace the post-`now = datetime.now(UTC)` body with:

```python
        now = datetime.now(UTC)

        legs_list = [
            {
                "outcome": leg["outcome"],
                "provider": leg["provider"],
                "odds": leg["odds"],
                "edge_pct": leg["edge_pct"],
                "fair_odds": leg["fair_odds"],
                "stake_pct": leg["stake_pct"],
                "is_sharp": leg.get("is_sharp", False),
            }
            for leg in sorted_legs
        ]
        outcomes_json = {
            "legs": legs_list,
            "arb_profit_pct": arb_profit_pct,
            "arb_legs": arb_legs,
        }

        if existing:
            existing.is_active = True
            existing.provider1_id = primary["provider"]
            existing.provider2_id = secondary["provider"]
            existing.odds1 = primary["odds"]
            existing.odds2 = secondary["odds"]
            existing.outcome1 = primary["outcome"]
            existing.outcome2 = secondary["outcome"]
            existing.profit_pct = guaranteed_profit_pct
            existing.edge_pct = combined_edge_pct
            existing.outcomes = outcomes_json
            existing.point = point
            existing.detected_at = now
            flag_modified(existing, "outcomes")
            opp_obj = existing
            is_new = False
        else:
            opp_obj = Opportunity(
                type="arb",
                event_id=event_id,
                market=market,
                outcome1=primary["outcome"],
                outcome2=secondary["outcome"],
                provider1_id=primary["provider"],
                provider2_id=secondary["provider"],
                odds1=primary["odds"],
                odds2=secondary["odds"],
                profit_pct=guaranteed_profit_pct,
                edge_pct=combined_edge_pct,
                outcomes=outcomes_json,
                point=point,
                is_active=True,
                detected_at=now,
                scope=scope,
            )
            self.db.add(opp_obj)
            self.db.flush()
            is_new = True

        from ..services.opp_snapshot_service import OppSnapshotService
        OppSnapshotService(self.db).upsert_from_opportunity(opp_obj)

        return is_new, opp_obj
```

- [ ] **Step 5: Apply the same pattern to `upsert_reverse_value`**

In `backend/src/repositories/opportunity_repo.py`, find `upsert_reverse_value` (line ~303). Replace the post-`now = datetime.now(UTC)` body with:

```python
        now = datetime.now(UTC)

        if existing:
            existing.is_active = True
            existing.edge_pct = edge_pct
            existing.provider1_id = "pinnacle"
            existing.odds1 = pinnacle_odds
            existing.provider2_id = "consensus"
            existing.odds2 = consensus_fair_odds
            existing.outcomes = outcomes_json
            existing.point = point
            existing.detected_at = now
            flag_modified(existing, "outcomes")
            opp_obj = existing
            is_new = False
        else:
            opp_obj = Opportunity(
                type="reverse_value",
                event_id=event_id,
                market=market,
                outcome1=outcome,
                edge_pct=edge_pct,
                provider1_id="pinnacle",
                odds1=pinnacle_odds,
                provider2_id="consensus",
                odds2=consensus_fair_odds,
                outcomes=outcomes_json,
                point=point,
                is_active=True,
                detected_at=now,
                scope=scope,
            )
            self.db.add(opp_obj)
            self.db.flush()
            is_new = True

        from ..services.opp_snapshot_service import OppSnapshotService
        OppSnapshotService(self.db).upsert_from_opportunity(opp_obj)

        return is_new, opp_obj
```

Note: we intentionally do NOT modify `upsert_reverse` (the older type-`reverse` arb variant) — the design spec restricts snapshots to `value`, `arb`, `reverse_value`. Leaving `upsert_reverse` untouched is correct.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_opp_repo_snapshots.py tests/services/test_opp_snapshot_service.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 7: Run the broader opp/scanner test suite to catch regressions**

Run: `cd backend && pytest tests/ -k "opp or scanner or analyzer" -v --no-header 2>&1 | tail -40`
Expected: all selected tests PASS. If any pre-existing test fails, it's a regression from the upsert-method refactor — investigate before continuing.

- [ ] **Step 8: Commit**

```bash
cd c:/Users/rasmu/betty
git add backend/src/repositories/opportunity_repo.py backend/tests/test_opp_repo_snapshots.py
git commit -m "feat(repo): inline opp_snapshot upsert in OpportunityRepo (value/arb/reverse_value)"
```

---

## Task 5: `OppSnapshotService.compute_closing_clv()` backfill

**Files:**
- Modify: `backend/src/services/opp_snapshot_service.py`
- Modify: `backend/tests/services/test_opp_snapshot_service.py` (append tests)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/services/test_opp_snapshot_service.py`:

```python
def test_backfill_skips_events_that_have_not_started(basic_setup):
    from src.services.opp_snapshot_service import OppSnapshotService

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    stats = svc.compute_closing_clv()
    assert stats["processed"] == 0  # event start_time is +2h


def test_backfill_populates_clv_for_started_events(basic_setup):
    from src.db.models import Event, Odds, OppSnapshot
    from src.services.opp_snapshot_service import OppSnapshotService

    # Event has already started
    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = datetime.now(UTC) - timedelta(minutes=5)

    # Seed Pinnacle closing odds for the outcome and its complement
    basic_setup.add_all([
        Odds(event_id="evt-1", provider_id="pinnacle", market="moneyline",
             outcome="A", odds=2.00, scope="ft",
             updated_at=datetime.now(UTC) - timedelta(minutes=6)),
        Odds(event_id="evt-1", provider_id="pinnacle", market="moneyline",
             outcome="B", odds=2.00, scope="ft",
             updated_at=datetime.now(UTC) - timedelta(minutes=6)),
        # Unibet's own closing price (slightly worse than detection)
        Odds(event_id="evt-1", provider_id="unibet", market="moneyline",
             outcome="A", odds=2.05, scope="ft",
             updated_at=datetime.now(UTC) - timedelta(minutes=6)),
    ])
    basic_setup.commit()

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp(odds=2.10, fair=2.00, edge=5.0)
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    stats = svc.compute_closing_clv()
    assert stats["processed"] == 1
    assert stats["updated"] == 1

    snap = basic_setup.query(OppSnapshot).first()
    assert snap.clv_computed_at is not None
    assert snap.provider1_closing_odds == 2.05
    # CLV vs same-provider = (2.10/2.05 - 1)*100 = 2.439...
    assert abs(snap.provider_clv_pct - 2.44) < 0.01
    # Pinnacle close devigged: 2.00 with sibling 2.00 → prob_sum=1.0, fair stays 2.00
    assert snap.pinnacle_closing_fair == 2.00
    # vs-Pinnacle CLV = (2.10/2.00 - 1)*100 = 5.0
    assert abs(snap.pinnacle_clv_pct - 5.0) < 0.01


def test_backfill_marks_done_even_with_no_closing_data(basic_setup):
    """If neither Pinnacle nor provider had closing odds, still mark
    clv_computed_at so the row isn't reprocessed every cycle."""
    from src.db.models import Event, OppSnapshot
    from src.services.opp_snapshot_service import OppSnapshotService

    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = datetime.now(UTC) - timedelta(minutes=5)
    basic_setup.commit()

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    stats = svc.compute_closing_clv()
    snap = basic_setup.query(OppSnapshot).first()
    assert snap.clv_computed_at is not None  # marked done
    assert snap.provider_clv_pct is None  # no data to compute
    assert snap.pinnacle_clv_pct is None
    assert stats["processed"] == 1
    assert stats["updated"] == 0  # nothing was actually computed


def test_backfill_arb_computes_closing_prob_sum(basic_setup):
    from src.db.models import Event, Odds, OppSnapshot, Provider
    from src.services.opp_snapshot_service import OppSnapshotService

    basic_setup.add(Provider(id="betinia", name="Betinia"))
    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = datetime.now(UTC) - timedelta(minutes=5)
    basic_setup.add_all([
        Odds(event_id="evt-1", provider_id="unibet", market="moneyline",
             outcome="A", odds=2.08, scope="ft",
             updated_at=datetime.now(UTC) - timedelta(minutes=6)),
        Odds(event_id="evt-1", provider_id="betinia", market="moneyline",
             outcome="B", odds=2.02, scope="ft",
             updated_at=datetime.now(UTC) - timedelta(minutes=6)),
    ])
    basic_setup.commit()

    from src.db.models import Opportunity
    arb = Opportunity(
        type="arb", event_id="evt-1", market="moneyline",
        outcome1="A", outcome2="B",
        provider1_id="unibet", provider2_id="betinia",
        odds1=2.10, odds2=2.05, edge_pct=1.5, scope="ft",
        is_active=True, detected_at=datetime.now(UTC),
    )
    basic_setup.add(arb)
    basic_setup.flush()
    svc = OppSnapshotService(basic_setup)
    svc.upsert_from_opportunity(arb)
    basic_setup.commit()

    svc.compute_closing_clv()
    snap = basic_setup.query(OppSnapshot).first()
    # prob_sum = 1/2.08 + 1/2.02 ≈ 0.9759
    assert abs(snap.closing_prob_sum - (1/2.08 + 1/2.02)) < 1e-6
    assert snap.was_arb_at_close is True  # < 1.0


def test_backfill_records_closing_age_minutes(basic_setup):
    from src.db.models import Event, Odds, OppSnapshot
    from src.services.opp_snapshot_service import OppSnapshotService

    now = datetime.now(UTC)
    evt = basic_setup.query(Event).filter(Event.id == "evt-1").first()
    evt.start_time = now - timedelta(minutes=1)
    basic_setup.add(Odds(
        event_id="evt-1", provider_id="unibet", market="moneyline",
        outcome="A", odds=2.05, scope="ft",
        updated_at=now - timedelta(minutes=11),  # 10 min before start
    ))
    basic_setup.commit()

    svc = OppSnapshotService(basic_setup)
    opp = _make_value_opp()
    basic_setup.add(opp)
    basic_setup.flush()
    svc.upsert_from_opportunity(opp)
    basic_setup.commit()

    svc.compute_closing_clv()
    snap = basic_setup.query(OppSnapshot).first()
    # provider1_closing_age_minutes = start_time - updated_at = 10 min
    assert abs(snap.provider1_closing_age_minutes - 10.0) < 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/services/test_opp_snapshot_service.py -v -k "backfill"`
Expected: 5 FAILs — `AttributeError: 'OppSnapshotService' object has no attribute 'compute_closing_clv'`.

- [ ] **Step 3: Add `compute_closing_clv()` to the service**

Append to `backend/src/services/opp_snapshot_service.py`:

```python
    def compute_closing_clv(self, batch_size: int = 500) -> dict:
        """
        For every opp_snapshots row where clv_computed_at IS NULL and the
        event has started, backfill provider/pinnacle closing odds + CLV +
        (for arbs) closing prob sum. Mark clv_computed_at = now() even if
        no closing data was available, to avoid reprocessing.

        Mirrors BetService.snapshot_closing_odds() (bet_service.py:568).

        Returns: {"processed": int, "updated": int}
                 - processed: rows where we ran the backfill (incl. data-less)
                 - updated:   rows where we wrote at least one CLV value
        """
        from ..db.models import Odds  # local import to keep top of file lean

        now = datetime.now(UTC)

        rows = (
            self.db.query(OppSnapshot)
            .join(Event, Event.id == OppSnapshot.event_id)
            .filter(
                OppSnapshot.clv_computed_at.is_(None),
                Event.start_time.isnot(None),
                Event.start_time <= now,
            )
            .limit(batch_size)
            .all()
        )

        processed = 0
        updated = 0

        for snap in rows:
            processed += 1
            did_update = False
            event = self.db.query(Event).filter(Event.id == snap.event_id).first()
            start_time = event.start_time if event else None

            # ---- Leg 1: same-provider closing odds ----
            p1_odds = self._latest_odds(
                event_id=snap.event_id,
                provider_id=snap.provider1_id,
                market=snap.market,
                outcome=snap.outcome1,
                point=snap.point,
                scope=snap.scope,
            )
            if p1_odds is not None and p1_odds.odds > 1.0:
                snap.provider1_closing_odds = p1_odds.odds
                if start_time and p1_odds.updated_at:
                    snap.provider1_closing_age_minutes = (
                        (start_time - p1_odds.updated_at).total_seconds() / 60.0
                    )
                snap.provider_clv_pct = round(
                    (snap.odds1_at_detection / p1_odds.odds - 1) * 100, 2
                )
                did_update = True

            # ---- Pinnacle closing fair odds (devigged) ----
            pinnacle_fair, pinnacle_age = self._pinnacle_closing_fair(
                event_id=snap.event_id,
                market=snap.market,
                outcome=snap.outcome1,
                point=snap.point,
                scope=snap.scope,
                start_time=start_time,
            )
            if pinnacle_fair is not None:
                snap.pinnacle_closing_fair = pinnacle_fair
                snap.pinnacle_closing_age_minutes = pinnacle_age
                snap.pinnacle_clv_pct = round(
                    (snap.odds1_at_detection / pinnacle_fair - 1) * 100, 2
                )
                did_update = True

            # ---- Arb leg 2 + closing prob sum ----
            if snap.type == "arb" and snap.provider2_id and snap.outcome2:
                p2_odds = self._latest_odds(
                    event_id=snap.event_id,
                    provider_id=snap.provider2_id,
                    market=snap.market,
                    outcome=snap.outcome2,
                    point=snap.point,
                    scope=snap.scope,
                )
                if p2_odds is not None and p2_odds.odds > 1.0:
                    snap.provider2_closing_odds = p2_odds.odds
                    if start_time and p2_odds.updated_at:
                        snap.provider2_closing_age_minutes = (
                            (start_time - p2_odds.updated_at).total_seconds() / 60.0
                        )
                    did_update = True

                if (snap.provider1_closing_odds and snap.provider2_closing_odds):
                    prob_sum = (1.0 / snap.provider1_closing_odds
                                + 1.0 / snap.provider2_closing_odds)
                    snap.closing_prob_sum = prob_sum
                    snap.was_arb_at_close = prob_sum < 1.0

            # Always mark done — even when no closing data was available —
            # so the row isn't reprocessed every cycle.
            snap.clv_computed_at = now
            if did_update:
                updated += 1

        return {"processed": processed, "updated": updated}

    def _latest_odds(
        self,
        event_id: str,
        provider_id: str,
        market: str,
        outcome: str,
        point: float | None,
        scope: str,
    ):
        """Latest odds row for the given key. Returns Odds or None."""
        from ..db.models import Odds

        q = self.db.query(Odds).filter(
            Odds.event_id == event_id,
            Odds.provider_id == provider_id,
            Odds.market == market,
            Odds.outcome == outcome,
            Odds.scope == scope,
        )
        if market in ("spread", "total") and point is not None:
            q = q.filter(Odds.point == point)
        return q.order_by(Odds.updated_at.desc().nullslast()).first()

    def _pinnacle_closing_fair(
        self,
        event_id: str,
        market: str,
        outcome: str,
        point: float | None,
        scope: str,
        start_time,
    ):
        """Pinnacle's odds devigged against sibling outcomes for the same
        (market, point, scope). Returns (fair_odds, age_minutes) or (None, None).
        """
        from ..db.models import Odds

        q = self.db.query(Odds).filter(
            Odds.event_id == event_id,
            Odds.provider_id == "pinnacle",
            Odds.market == market,
            Odds.scope == scope,
        )
        if market in ("spread", "total") and point is not None:
            q = q.filter(Odds.point == point)
        siblings = q.all()
        if not siblings:
            return None, None

        # Devig: prob_i = (1/odds_i) / sum(1/odds_j) → fair_i = 1/prob_i
        inv_sum = sum(1.0 / o.odds for o in siblings if o.odds > 1.0)
        if inv_sum <= 0:
            return None, None

        target = next((o for o in siblings if o.outcome == outcome), None)
        if target is None or target.odds <= 1.0:
            return None, None

        fair = 1.0 / ((1.0 / target.odds) / inv_sum)
        age = None
        if start_time and target.updated_at:
            age = (start_time - target.updated_at).total_seconds() / 60.0
        return fair, age
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/services/test_opp_snapshot_service.py -v`
Expected: all 10 tests PASS (5 from Task 3 + 5 from Task 5).

- [ ] **Step 5: Commit**

```bash
cd c:/Users/rasmu/betty
git add backend/src/services/opp_snapshot_service.py backend/tests/services/test_opp_snapshot_service.py
git commit -m "feat(services): OppSnapshotService.compute_closing_clv backfill"
```

---

## Task 6: Wire CLV backfill into scheduler settlement hook

**Files:**
- Modify: `backend/src/pipeline/scheduler.py:1114-1135`
- Create: `backend/tests/pipeline/test_scheduler_opp_clv.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/pipeline/test_scheduler_opp_clv.py`:

```python
"""Verify the scheduler settlement tick invokes the opp-CLV backfill."""

from unittest.mock import MagicMock, patch

import pytest


def test_run_settlement_calls_opp_clv_backfill(db_session):
    """_run_settlement should call OppSnapshotService.compute_closing_clv
    after BetService.snapshot_closing_odds."""
    from src.pipeline.scheduler import Scheduler

    sched = Scheduler.__new__(Scheduler)  # bypass __init__; we only need the method

    with patch("src.pipeline.scheduler.get_session", return_value=db_session), \
         patch("src.services.bet_service.BetService.snapshot_closing_odds",
               return_value={"processed": 0, "updated": 0}) as bet_mock, \
         patch("src.services.opp_snapshot_service.OppSnapshotService.compute_closing_clv",
               return_value={"processed": 0, "updated": 0}) as opp_mock:
        result = sched._run_settlement()

    assert bet_mock.called, "bet CLV must still be invoked"
    assert opp_mock.called, "opp CLV backfill must be invoked"
    # Bet CLV first, then opp CLV (so opp CLV can use any odds bet CLV touched)
    assert bet_mock.call_count == 1
    assert opp_mock.call_count == 1
    assert "bet_clv" in result
    assert "opp_clv" in result
```

Note: this test imports `get_session` from `src.pipeline.scheduler`. Check that import path matches the actual line `from src.db.models import get_session` inside `_run_settlement` — patching needs to match where the name is *used*, not where it's defined. If the import is local inside the method, patch `src.db.models.get_session` instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/pipeline/test_scheduler_opp_clv.py -v`
Expected: FAIL — either `opp_mock.called == False` or `"opp_clv" not in result`.

- [ ] **Step 3: Modify `_run_settlement`**

In `backend/src/pipeline/scheduler.py` around line 1114, replace the entire `_run_settlement` method with:

```python
    def _run_settlement(self) -> dict:
        """Snapshot closing odds for CLV tracking — bets first, then unplayed opps."""
        from src.db.models import get_session
        from src.services.bet_service import BetService
        from src.services.opp_snapshot_service import OppSnapshotService

        session = get_session()
        try:
            bet_service = BetService(session)
            bet_clv = bet_service.snapshot_closing_odds()

            opp_service = OppSnapshotService(session)
            opp_clv = opp_service.compute_closing_clv()

            session.commit()

            if bet_clv.get("updated", 0) > 0:
                logger.info(
                    f"[Scheduler:settlement] Bet CLV: {bet_clv['updated']}/{bet_clv['processed']} bets updated"
                )
            if opp_clv.get("updated", 0) > 0:
                logger.info(
                    f"[Scheduler:settlement] Opp CLV: {opp_clv['updated']}/{opp_clv['processed']} snapshots updated"
                )

            return {"bet_clv": bet_clv, "opp_clv": opp_clv}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

Note: the return type changes from `dict` of bet-CLV fields to a dict-of-dicts (`{"bet_clv": ..., "opp_clv": ...}`). Check that no caller relies on the old flat shape. Grep:

```bash
grep -rn "_run_settlement\|run_settlement" c:/Users/rasmu/betty/backend/src/
```

If any caller reads `clv_stats["updated"]` directly off the return, update it to `clv_stats["bet_clv"]["updated"]`. Most likely the only caller is `start_settlement_loop` which just exists for side effects — no shape dependency.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/pipeline/test_scheduler_opp_clv.py -v`
Expected: PASS.

- [ ] **Step 5: Run scheduler-related regression suite**

Run: `cd backend && pytest tests/pipeline/ tests/test_*scheduler* -v --no-header 2>&1 | tail -30`
Expected: all PASS. Any regression points at a caller of `_run_settlement` that depended on the flat-dict return shape.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/rasmu/betty
git add backend/src/pipeline/scheduler.py backend/tests/pipeline/test_scheduler_opp_clv.py
git commit -m "feat(scheduler): run opp-CLV backfill after bet-CLV snapshot"
```

---

## Task 7: Full test suite + deploy + smoke verification

**Files:**
- None (deploy + verify only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest tests/ --no-header 2>&1 | tail -20`
Expected: all tests PASS (or the same baseline-failing count as before this work — investigate any new failure).

- [ ] **Step 2: Push to main**

```bash
cd c:/Users/rasmu/betty
git push origin main
```

- [ ] **Step 3: Check for concurrent deploy lock**

```bash
ssh root@148.251.40.251 "pgrep -fa 'server-deploy.sh' && lsof /opt/betty/.deploy.lock 2>/dev/null"
```
Expected: no `pgrep` matches, no lsof output → lock is free. If held, wait and retry.

- [ ] **Step 4: Deploy**

```bash
ssh root@148.251.40.251 "bash /opt/betty/backend/scripts/server-deploy.sh rebuild backend"
```
Expected: build completes, health endpoint returns 200, exit 0. If it pulls the migration: `005_add_opp_snapshots` is in alembic head.

- [ ] **Step 5: Verify migration ran**

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose exec -T postgres psql -U betty -d betty -c '\d opp_snapshots'"
```
Expected: table exists with all 27 columns from the spec.

- [ ] **Step 6: Wait one extraction cycle (~5 min), then verify snapshots are being written**

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose exec -T postgres psql -U betty -d betty -c 'SELECT type, COUNT(*), MIN(first_detected_at), MAX(last_detected_at), SUM(detection_count) FROM opp_snapshots GROUP BY type'"
```
Expected: rows present for `value`, `arb`, `reverse_value`. `detection_count > COUNT(*)` confirms re-detection-bump logic is active (not just inserts).

- [ ] **Step 7: Wait for an event to start, then verify CLV backfill**

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose exec -T postgres psql -U betty -d betty -c \"SELECT COUNT(*) AS total, COUNT(clv_computed_at) AS clv_done, COUNT(provider_clv_pct) AS prov_clv, COUNT(pinnacle_clv_pct) AS pin_clv FROM opp_snapshots WHERE event_id IN (SELECT id FROM events WHERE start_time <= now() - interval '15 minutes')\""
```
Expected: `total > 0`, `clv_done = total` (all started-event rows backfilled), `prov_clv` and `pin_clv` both non-zero (most rows got at least one CLV).

- [ ] **Step 8: Sanity-check CLV distribution**

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose exec -T postgres psql -U betty -d betty -c \"SELECT type, COUNT(*) FILTER (WHERE pinnacle_clv_pct IS NOT NULL) AS n, ROUND(AVG(pinnacle_clv_pct)::numeric, 2) AS mean_clv, ROUND(STDDEV(pinnacle_clv_pct)::numeric, 2) AS sd FROM opp_snapshots GROUP BY type\""
```
Expected: at least 10+ rows per type after a few hours. Sanity: `value` opps should trend slightly positive `mean_clv` (scanner is finding edge); `arb` legs slightly positive or near zero; `reverse_value` highly variable. If mean_clv is wildly negative (-20% or worse) across types, the scanner is mis-calling edge — flag for investigation, do not consider this task complete.

- [ ] **Step 9: Final commit (if any post-deploy fixes were needed)**

If everything verified clean, no further commits. If verification surfaced an issue, fix → test → commit → redeploy → re-verify.
