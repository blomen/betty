# Market Depth + Deferred Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase opportunity yield by debugging underperforming spread/total enrichment in existing providers and adding deferred matching to recover timing-gap events.

**Architecture:** Two independent workstreams. Part B debugs/fixes existing Pass 2 enrichment code in Altenar, VBet, and Betsson — these providers already have spread/total extraction but show gaps in metrics. Part C adds a `deferred_events` buffer table so soft provider events that arrive before Pinnacle are stored and retried after each Pinnacle extraction.

**Tech Stack:** Python 3.10+ / SQLAlchemy / FastAPI / WebSocket (VBet)

**Spec:** `docs/superpowers/specs/2026-03-23-market-depth-deferred-matching-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/db/models.py` | Modify | Add `DeferredEvent` ORM model |
| `backend/src/pipeline/storage.py` | Modify | Add `_store_deferred_event()`, modify require_match skip to defer |
| `backend/src/pipeline/orchestrator.py` | Modify | Add `resolve_deferred_events()` method on `ExtractionPipeline`, call after sharp caches warm |
| `backend/src/pipeline/metrics.py` | Modify | Add deferred tracking fields |
| `backend/src/providers/altenar.py` | Modify | Investigate spread gap by sport |
| `backend/src/providers/vbet.py` | Modify | Investigate spread/total gap by sport |
| `backend/tests/test_deferred_matching.py` | Create | Tests for deferred event lifecycle |

---

## Task 1: Diagnose Provider Spread/Total Gaps

**Purpose:** Before fixing code, determine which gaps are platform limitations vs bugs.

**Files:**
- Read: `backend/src/providers/altenar.py:498-500` (football exclusion)
- Read: `backend/src/providers/vbet.py:432-486` (Pass 2 WS)

- [ ] **Step 1: Query Betinia spread/total coverage by sport**

Use sqlite MCP:
```sql
SELECT o.provider_id, e.sport,
    COUNT(DISTINCT e.id) as events,
    SUM(CASE WHEN o.market = 'spread' THEN 1 ELSE 0 END) as spread_odds,
    SUM(CASE WHEN o.market = 'total' THEN 1 ELSE 0 END) as total_odds,
    SUM(CASE WHEN o.market IN ('1x2','moneyline') THEN 1 ELSE 0 END) as ml_odds
FROM odds o JOIN events e ON o.event_id = e.id
WHERE o.provider_id = 'betinia' AND o.updated_at > datetime('now', '-2 hours')
GROUP BY e.sport ORDER BY events DESC;
```

Expected: Football will show 0 spreads (platform limitation). Other sports should show spreads from enrichment. If non-football sports also show low spread coverage, the enrichment has a bug.

- [ ] **Step 2: Query VBet spread/total coverage by sport**

Same query with `provider_id = 'vbet'`. Compare spread/total counts across sports. Identify which sports return 0%.

- [ ] **Step 4: Query Betsson total coverage by sport**

Same query with `provider_id = 'betsson'`. The 64% total gap may be sport-specific.

- [ ] **Step 5: Document findings**

Record which gaps are platform limitations (no action) vs bugs (fix in subsequent tasks). Update the spec with findings. This determines the scope of Tasks 2-4.

- [ ] **Step 6: Commit diagnostic findings**

```bash
git add docs/superpowers/specs/2026-03-23-market-depth-deferred-matching-design.md
git commit -m "docs: add per-sport spread/total diagnostic findings"
```

---

## Task 3: Investigate and Fix VBet Spread/Total Gaps

**Purpose:** VBet has 52% spread / 43% total. The Pass 2 WebSocket code exists but may fail for certain sports.

**Files:**
- Modify: `backend/src/providers/vbet.py:432-486` (Pass 2 WS request)

- [ ] **Step 1: Analyze diagnostic query results from Task 1**

Review the per-sport breakdown. Identify which sports have 0% spread/total.

- [ ] **Step 2: Add DEBUG logging to Pass 2 WebSocket response**

At `vbet.py:462`, after the spread/total WebSocket response:

```python
logger.info(f"[vbet] Pass 2 response for {sport}: code={spread_total_resp.get('code')}, events_parsed={len(st_events) if spread_total_resp.get('code') == 0 else 'N/A'}")
```

- [ ] **Step 3: Run extraction and check logs**

```bash
cd backend
python -m src.app extract vbet 2>&1 | grep -i "vbet.*pass 2"
```

- [ ] **Step 4: Fix based on findings**

Common issues:
- WebSocket alias not mapped for some sports → check `_SPORT_ALIAS_MAP`
- Response parsing fails for certain market structures → fix `_parse_games()`
- Market merging misses events due to ID mismatch → fix merge logic at lines 477-482

- [ ] **Step 5: Verify fix**

```bash
cd backend
python -m src.app extract vbet
```

Check spread/total counts improved in extraction report.

- [ ] **Step 6: Commit fix**

```bash
git add backend/src/providers/vbet.py
git commit -m "fix(vbet): improve spread/total extraction coverage"
```

---

## Task 4: Investigate Altenar and Betsson Gaps

**Purpose:** Altenar has 33% spread (likely football = 0, rest OK). Betsson 64% total may be sport-specific.

**Files:**
- Read: `backend/src/providers/altenar.py:498-527`
- Read: `backend/src/providers/gecko_v2.py` (Betsson)

- [ ] **Step 1: Analyze Betinia diagnostic results**

If all non-football sports show good spread coverage, the 33% gap is entirely the football platform limitation. Document and close.

If non-football sports also have gaps, investigate `_enrich_missing_spreads()`:
- Are events exceeding the 200-cap?
- Are some sport_ids failing the `GetEventDetails` call?

- [ ] **Step 2: Analyze Betsson diagnostic results**

If specific sports show 0% total coverage, check if Gecko V2's `events-table` API returns total markets for those sports.

- [ ] **Step 3: Fix any bugs found**

Apply provider-specific fixes based on analysis.

- [ ] **Step 4: Commit findings/fixes**

```bash
git add backend/src/providers/altenar.py backend/src/providers/gecko_v2.py
git commit -m "fix(providers): improve altenar/betsson spread/total coverage"
```

If no code changes needed (all gaps are platform limitations), commit updated docs instead:
```bash
git add docs/superpowers/specs/2026-03-23-market-depth-deferred-matching-design.md
git commit -m "docs: document altenar/betsson spread/total platform limitations"
```

---

## Task 5: Add DeferredEvent Model

**Purpose:** Create the ORM model and DB table for buffering unmatched soft provider events.

**Files:**
- Modify: `backend/src/db/models.py:587+` (add after ProviderRunMetrics)
- Create: `backend/tests/test_deferred_matching.py`

- [ ] **Step 1: Write test for DeferredEvent model**

```python
# backend/tests/test_deferred_matching.py
import json
from datetime import datetime, timedelta
from src.db.models import DeferredEvent


def test_deferred_event_to_standard_event():
    """DeferredEvent.to_standard_event() reconstructs a valid StandardEvent."""
    markets = [
        {"type": "moneyline", "outcomes": [
            {"name": "home", "odds": 1.85},
            {"name": "away", "odds": 2.05},
        ]}
    ]
    de = DeferredEvent(
        provider_id="betsson",
        sport="football",
        league="Premier League",
        home_team="Arsenal",
        away_team="Chelsea",
        normalized_home="arsenal",
        normalized_away="chelsea",
        start_time=datetime(2026, 3, 25, 15, 0),
        markets_json=json.dumps(markets),
    )
    event = de.to_standard_event()
    assert event.sport == "football"
    assert event.home_team == "Arsenal"
    assert event.away_team == "Chelsea"
    assert event.markets == markets
    assert event.provider == "betsson"


def test_deferred_event_unique_constraint(db_session):
    """Duplicate provider+sport+teams+time is rejected."""
    de1 = DeferredEvent(
        provider_id="betsson", sport="football", league="PL",
        home_team="Arsenal", away_team="Chelsea",
        normalized_home="arsenal", normalized_away="chelsea",
        start_time=datetime(2026, 3, 25, 15, 0),
        markets_json="[]",
    )
    de2 = DeferredEvent(
        provider_id="betsson", sport="football", league="PL",
        home_team="Arsenal", away_team="Chelsea",
        normalized_home="arsenal", normalized_away="chelsea",
        start_time=datetime(2026, 3, 25, 15, 0),
        markets_json='[{"type": "moneyline"}]',
    )
    db_session.add(de1)
    db_session.commit()
    # Second insert with same key should fail or be handled
    db_session.merge(de2)  # upsert behavior
    db_session.commit()
    count = db_session.query(DeferredEvent).count()
    assert count == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend
pytest tests/test_deferred_matching.py -v
```

Expected: FAIL with `ImportError: cannot import name 'DeferredEvent'`

- [ ] **Step 3: Add DeferredEvent model to models.py**

Add after the existing `ProviderRunMetrics` class in `backend/src/db/models.py`:

```python
class DeferredEvent(Base):
    """Buffer for soft provider events that couldn't match Pinnacle on first attempt."""
    __tablename__ = "deferred_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Text, nullable=False)
    sport = Column(Text, nullable=False)
    league = Column(Text)
    home_team = Column(Text, nullable=False)
    away_team = Column(Text, nullable=False)
    normalized_home = Column(Text, nullable=False)
    normalized_away = Column(Text, nullable=False)
    start_time = Column(DateTime, nullable=False)
    markets_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    attempt_count = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint(
            "provider_id", "sport", "normalized_home", "normalized_away", "start_time",
            name="uq_deferred_provider_event",
        ),
        Index("idx_deferred_start", "start_time"),
        Index("idx_deferred_sport", "sport"),
    )

    def to_standard_event(self):
        """Reconstruct StandardEvent from deferred data. Sets _from_deferred flag to prevent re-deferral loops."""
        import json
        from src.core.retriever import StandardEvent
        event = StandardEvent(
            id="",
            name=f"{self.home_team} vs {self.away_team}",
            sport=self.sport,
            markets=json.loads(self.markets_json),
            provider=self.provider_id,
            start_time=self.start_time.isoformat() if self.start_time else "",
            home_team=self.home_team,
            away_team=self.away_team,
            league=self.league or "",
        )
        event._from_deferred = True  # Prevent re-deferral in store_provider_event
        return event
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend
pytest tests/test_deferred_matching.py -v
```

Expected: PASS. The `deferred_events` table is auto-created via `Base.metadata.create_all()` at startup (models.py:1383), so no migration is needed. Check existing test fixtures in `backend/tests/conftest.py` for the `db_session` fixture pattern.

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_deferred_matching.py
git commit -m "feat(db): add DeferredEvent model for timing-gap recovery"
```

---

## Task 6: Implement _store_deferred_event()

**Purpose:** When `require_match=True` and no match found, buffer the event instead of discarding it.

**Files:**
- Modify: `backend/src/pipeline/storage.py:747-748` (matched_id is None block in store_provider_event)
- Modify: `backend/tests/test_deferred_matching.py`

- [ ] **Step 1: Write test for _store_deferred_event**

```python
# Add to backend/tests/test_deferred_matching.py
from src.pipeline.storage import _store_deferred_event
from src.core.retriever import StandardEvent


def test_store_deferred_event_creates_record(db_session):
    """Unmatched event is stored in deferred_events table."""
    event = StandardEvent(
        id="", name="Arsenal vs Chelsea", sport="football",
        markets=[{"type": "moneyline", "outcomes": [{"name": "home", "odds": 1.85}]}],
        provider="betsson",
        start_time="2026-03-25T15:00:00",
        home_team="Arsenal", away_team="Chelsea", league="PL",
    )
    _store_deferred_event(db_session, event, "betsson")
    db_session.commit()

    from src.db.models import DeferredEvent
    result = db_session.query(DeferredEvent).one()
    assert result.provider_id == "betsson"
    assert result.sport == "football"
    assert result.normalized_home == "arsenal"  # normalized
    assert result.normalized_away == "chelsea"
    assert "moneyline" in result.markets_json


def test_store_deferred_event_upserts_on_duplicate(db_session):
    """Re-extracting same event updates odds, doesn't duplicate."""
    event1 = StandardEvent(
        id="", name="Arsenal vs Chelsea", sport="football",
        markets=[{"type": "moneyline", "outcomes": [{"name": "home", "odds": 1.85}]}],
        provider="betsson", start_time="2026-03-25T15:00:00",
        home_team="Arsenal", away_team="Chelsea", league="PL",
    )
    event2 = StandardEvent(
        id="", name="Arsenal vs Chelsea", sport="football",
        markets=[{"type": "moneyline", "outcomes": [{"name": "home", "odds": 1.90}]}],
        provider="betsson", start_time="2026-03-25T15:00:00",
        home_team="Arsenal", away_team="Chelsea", league="PL",
    )
    _store_deferred_event(db_session, event1, "betsson")
    db_session.commit()
    _store_deferred_event(db_session, event2, "betsson")
    db_session.commit()

    from src.db.models import DeferredEvent
    assert db_session.query(DeferredEvent).count() == 1
    result = db_session.query(DeferredEvent).one()
    assert "1.90" in result.markets_json  # Updated to latest odds
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
pytest tests/test_deferred_matching.py::test_store_deferred_event_creates_record -v
pytest tests/test_deferred_matching.py::test_store_deferred_event_upserts_on_duplicate -v
```

Expected: FAIL with `ImportError: cannot import name '_store_deferred_event'`

- [ ] **Step 3: Implement _store_deferred_event in storage.py**

Add to `backend/src/pipeline/storage.py`:

Note: Add these imports to the top of storage.py: `from src.db.models import DeferredEvent` and `from src.matching.normalizer import normalize_team_name` (if not already present).

```python
import json
from datetime import datetime
from src.db.models import DeferredEvent
from src.matching.normalizer import normalize_team_name


def _store_deferred_event(session, event: StandardEvent, provider: str):
    """Buffer an unmatched soft event for later Pinnacle matching."""
    normalized_home = normalize_team_name(event.home_team)
    normalized_away = normalize_team_name(event.away_team)

    # Parse start_time from ISO string (same pattern as storage.py:758-762)
    try:
        start_time = datetime.fromisoformat(event.start_time) if isinstance(event.start_time, str) else event.start_time
    except (ValueError, TypeError):
        return

    if not start_time:
        return

    existing = session.query(DeferredEvent).filter_by(
        provider_id=provider,
        sport=event.sport,
        normalized_home=normalized_home,
        normalized_away=normalized_away,
        start_time=start_time,
    ).first()

    if existing:
        existing.markets_json = json.dumps(event.markets)
        existing.attempt_count = 0  # Reset on fresh data
    else:
        session.add(DeferredEvent(
            provider_id=provider,
            sport=event.sport,
            league=event.league,
            home_team=event.home_team,
            away_team=event.away_team,
            normalized_home=normalized_home,
            normalized_away=normalized_away,
            start_time=start_time,
            markets_json=json.dumps(event.markets),
        ))
```

- [ ] **Step 4: Modify store_provider_event to defer instead of silently discard**

In `backend/src/pipeline/storage.py`, at lines 747-748 (in `store_provider_event`, where `matched_id is None` is checked), change:

```python
# Old (storage.py:747-748):
if matched_id is None:
    return (False, 0, 0)

# New:
if matched_id is None:
    if require_match and not getattr(event, '_from_deferred', False):
        _store_deferred_event(session, event, provider)
    return (False, 0, 0)
```

**Important:** The `_from_deferred` flag prevents re-deferral loops. When `resolve_deferred_events` reconstructs a StandardEvent from the buffer, it sets `event._from_deferred = True` so that if the event still doesn't match, it won't be re-inserted into the buffer (it just increments `attempt_count` in the resolution loop instead).

Do NOT modify `_resolve_event_id` (lines 690-694) — keep it as a pure resolution function.

- [ ] **Step 5: Run tests**

```bash
cd backend
pytest tests/test_deferred_matching.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/pipeline/storage.py backend/tests/test_deferred_matching.py
git commit -m "feat(storage): buffer unmatched soft events instead of discarding"
```

---

## Task 7: Implement resolve_deferred_events()

**Purpose:** After each Pinnacle extraction, sweep the buffer and try matching deferred events.

**Files:**
- Modify: `backend/src/pipeline/orchestrator.py:586+`
- Modify: `backend/tests/test_deferred_matching.py`

- [ ] **Step 1: Write test for resolve_deferred_events**

```python
# Add to backend/tests/test_deferred_matching.py
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import json

from src.db.models import DeferredEvent


def test_resolve_deferred_recovers_matched_event(db_session):
    """Deferred event is recovered when Pinnacle event now exists."""
    # Insert a deferred event
    de = DeferredEvent(
        provider_id="betsson", sport="football", league="PL",
        home_team="Arsenal", away_team="Chelsea",
        normalized_home="arsenal", normalized_away="chelsea",
        start_time=datetime.utcnow() + timedelta(days=2),
        markets_json=json.dumps([{"type": "moneyline", "outcomes": [{"name": "home", "odds": 1.85}]}]),
    )
    db_session.add(de)
    db_session.commit()

    # Mock store_provider_event to return success (matched)
    with patch("backend.src.pipeline.orchestrator.store_provider_event") as mock_store:
        mock_store.return_value = (True, 1, 1)
        recovered, expired = resolve_deferred_events_standalone(
            db_session,
            sharp_sports={"football"},
            event_cache={},
            date_index={},
            sharp_odds_cache={},
            fuzzy_config=MagicMock(threshold=85, min_individual_score=75, prefix_filter_length=3, max_asymmetry_diff=25, min_for_asymmetry_check=80),
        )

    assert recovered == 1
    assert db_session.query(DeferredEvent).count() == 0


def test_resolve_deferred_expires_past_events(db_session):
    """Deferred events with past start_time are cleaned up."""
    de = DeferredEvent(
        provider_id="betsson", sport="football", league="PL",
        home_team="Arsenal", away_team="Chelsea",
        normalized_home="arsenal", normalized_away="chelsea",
        start_time=datetime.utcnow() - timedelta(hours=1),  # Already started
        markets_json="[]",
    )
    db_session.add(de)
    db_session.commit()

    recovered, expired = resolve_deferred_events_standalone(
        db_session,
        sharp_sports={"football"},
        event_cache={}, date_index={}, sharp_odds_cache={},
        fuzzy_config=MagicMock(threshold=85, min_individual_score=75, prefix_filter_length=3, max_asymmetry_diff=25, min_for_asymmetry_check=80),
    )

    assert expired >= 1
    assert db_session.query(DeferredEvent).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend
pytest tests/test_deferred_matching.py::test_resolve_deferred_recovers_matched_event -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement resolve_deferred_events in orchestrator.py**

Add a standalone function for testability, plus a wrapper method on the `ExtractionPipeline` class (the main orchestrator class in orchestrator.py).

In `backend/src/pipeline/orchestrator.py`:

```python
from datetime import timedelta
from src.db.models import DeferredEvent


def resolve_deferred_events_standalone(
    session,
    sharp_sports: set,
    event_cache: dict,
    date_index: dict,
    sharp_odds_cache: dict,
    fuzzy_config,
):
    """Attempt to match deferred events against current Pinnacle data.

    Standalone function for testability. Orchestrator method wraps this.
    """
    now = datetime.utcnow()

    deferred = session.query(DeferredEvent).filter(
        DeferredEvent.start_time > now,
        DeferredEvent.sport.in_(sharp_sports),
    ).all()

    recovered = 0

    for de in deferred:
        event = de.to_standard_event()  # Sets _from_deferred=True to prevent re-deferral
        is_new, odds_processed, _ = store_provider_event(
            session,
            event,
            de.provider_id,
            event_cache=event_cache,
            fuzzy_threshold=fuzzy_config.threshold,
            min_individual_score=fuzzy_config.min_individual_score,
            prefix_filter_length=fuzzy_config.prefix_filter_length,
            require_match=True,
            sharp_odds_cache=sharp_odds_cache,
            max_asymmetry_diff=fuzzy_config.max_asymmetry_diff,
            min_for_asymmetry_check=fuzzy_config.min_for_asymmetry_check,
            date_index=date_index,
        )

        if is_new or odds_processed > 0:
            session.delete(de)
            recovered += 1
        else:
            de.attempt_count += 1

    # Cleanup: expired events or stale (>6 hours)
    six_hours_ago = now - timedelta(hours=6)
    expired_count = session.query(DeferredEvent).filter(
        (DeferredEvent.start_time <= now) | (DeferredEvent.created_at < six_hours_ago)
    ).delete()

    session.commit()
    logger.info(
        f"Deferred resolution: {recovered} recovered, "
        f"{expired_count} expired, {len(deferred) - recovered} still pending"
    )
    return recovered, expired_count
```

- [ ] **Step 4: Add ExtractionPipeline method that wraps the standalone function**

In the `ExtractionPipeline` class (the main class in orchestrator.py), add:

```python
async def resolve_deferred(self):
    """Resolve deferred events after Pinnacle extraction."""
    sharp_sports = await self.get_cached_sports()
    if not sharp_sports:
        return 0, 0
    return resolve_deferred_events_standalone(
        self.session,
        sharp_sports=sharp_sports,
        event_cache=self.event_cache,
        date_index=self.event_cache_by_date,
        sharp_odds_cache=getattr(self, 'sharp_odds_cache', {}),
        fuzzy_config=self.orchestrator_config.fuzzy_match,
    )
```

- [ ] **Step 5: Call resolve_deferred after sharp caches are warm**

In `orchestrator.py`, after `_pre_warm_pinnacle_caches()` completes (~line 595, NOT after the commit at line 586 — the sharp_odds_cache must be populated first):

```python
# After _pre_warm_pinnacle_caches() (existing code ~line 595)
recovered, expired = await self.resolve_deferred()
if recovered:
    logger.info(f"Recovered {recovered} deferred events after Pinnacle refresh")
```

- [ ] **Step 6: Run tests**

```bash
cd backend
pytest tests/test_deferred_matching.py -v
```

Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/pipeline/orchestrator.py backend/tests/test_deferred_matching.py
git commit -m "feat(orchestrator): resolve deferred events after each Pinnacle extraction"
```

---

## Task 8: Add Deferred Metrics to Extraction Report

**Purpose:** Track deferred/recovered/expired counts in the extraction report so we can measure impact.

**Files:**
- Modify: `backend/src/pipeline/metrics.py:49-163`
- Modify: `backend/src/pipeline/orchestrator.py` (where deferred counts are emitted)

- [ ] **Step 1: Add deferred tracking fields to ProviderMetrics**

In `backend/src/pipeline/metrics.py`, add to the `ProviderMetrics` class:

```python
events_deferred: int = 0
events_recovered: int = 0
events_expired: int = 0
```

- [ ] **Step 2: Wire up deferred count in storage.py**

The `_store_deferred_event` function needs to increment a counter. The simplest approach: return a boolean from `_store_deferred_event` and let the caller (orchestrator) increment the metric.

In `_resolve_event_id` (storage.py:690-694), after calling `_store_deferred_event`, return a signal. The orchestrator already tracks `events_unmatched` — deferred events ARE unmatched events, just now buffered.

- [ ] **Step 3: Log deferred stats in extraction report**

After `resolve_deferred_events_standalone` returns, store recovered/expired counts in the run metrics.

- [ ] **Step 4: Commit**

```bash
git add backend/src/pipeline/metrics.py backend/src/pipeline/orchestrator.py
git commit -m "feat(metrics): track deferred/recovered/expired event counts"
```

---

## Task 9: Integration Test — Full Extraction Cycle

**Purpose:** Verify the complete flow works end-to-end: extraction → deferred → Pinnacle update → resolution.

- [ ] **Step 1: Run a full extraction cycle**

```bash
cd backend
python -m src.app extract pinnacle
python -m src.app extract betsson
```

- [ ] **Step 2: Check for deferred events in DB**

```sql
SELECT COUNT(*) as deferred_count,
    provider_id, sport
FROM deferred_events
GROUP BY provider_id, sport;
```

Expected: Low or zero count (match rates are already ~100%). The feature primarily helps during timing gaps.

- [ ] **Step 3: Check extraction report for deferred metrics**

Verify the report includes deferred/recovered/expired counts.

- [ ] **Step 4: Verify no regression in match rates**

```sql
SELECT pr.provider_id, pr.events_matched, pr.events_unmatched,
    ROUND(100.0 * pr.events_matched / NULLIF(pr.events_matched + pr.events_unmatched, 0), 1) as match_rate
FROM provider_run_metrics pr
WHERE pr.run_id = (SELECT id FROM extraction_runs ORDER BY start_time DESC LIMIT 1);
```

Expected: Match rates still ~100%.

- [ ] **Step 5: Commit any final adjustments**

```bash
git add -A
git commit -m "test: verify deferred matching integration"
```
