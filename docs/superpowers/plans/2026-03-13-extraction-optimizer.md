# M10 Extraction Pipeline Optimizer — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an analytics engine that computes provider ROI, coverage gaps, and scheduling efficiency from existing extraction data, surfaces insights in the CLI report and API, and generates actionable diagnostic recommendations.

**Architecture:** Two-layer system. Layer 1 (this plan) queries existing tables (`provider_run_metrics`, `sport_run_metrics`, `opportunities`, `bets`) to compute analytics on demand. Layer 2 (future plan) adds LightGBM models that train when Phase 1 ML tables accumulate enough data. Layer 1 is useful forever — even after ML activates, it's the validation layer.

**Tech Stack:** Python 3.10+ / SQLAlchemy / FastAPI / React 19 / TypeScript

**Spec:** `docs/superpowers/specs/2026-03-13-extraction-optimizer-design.md`

---

## Chunk 1: Database + Analytics Engine Core

### Task 1: ProviderRecommendation ORM Model + Migration

**Files:**
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/ml/migrations.py`
- Create: `backend/tests/test_analytics.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_analytics.py
"""Tests for extraction analytics engine."""
from sqlalchemy import inspect


def test_provider_recommendations_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "provider_recommendations" in inspector.get_table_names()


def test_recommendation_insert_and_query(db_session):
    from src.db.models import ProviderRecommendation
    rec = ProviderRecommendation(
        provider_id="betsson",
        category="match_rate",
        severity="warning",
        message="Match rate dropped from 82% to 62% over last 5 runs",
        diagnostic_data={"before": 0.82, "after": 0.62, "trend": "declining"},
        status="open",
        before_metric=0.82,
        source="rules",
    )
    db_session.add(rec)
    db_session.commit()
    result = db_session.query(ProviderRecommendation).first()
    assert result.provider_id == "betsson"
    assert result.category == "match_rate"
    assert result.status == "open"
    assert result.before_metric == 0.82
    assert result.after_metric is None


def test_recommendation_status_update(db_session):
    from src.db.models import ProviderRecommendation
    from datetime import datetime, timezone
    rec = ProviderRecommendation(
        provider_id="comeon",
        category="timing",
        severity="critical",
        message="SPA stalls on tennis",
        status="open",
        before_metric=52.1,
        source="rules",
    )
    db_session.add(rec)
    db_session.commit()

    result = db_session.query(ProviderRecommendation).first()
    result.status = "acted_on"
    result.acted_on_at = datetime.now(timezone.utc)
    db_session.commit()

    updated = db_session.query(ProviderRecommendation).first()
    assert updated.status == "acted_on"
    assert updated.acted_on_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: FAIL — ProviderRecommendation not defined

- [ ] **Step 3: Add ORM model to models.py**

Add before `def init_db()` in `backend/src/db/models.py`:

```python
class ProviderRecommendation(Base):
    """Diagnostic recommendation for a provider with lifecycle tracking."""
    __tablename__ = "provider_recommendations"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String, nullable=False)
    category = Column(String, nullable=False)      # match_rate, coverage, timing, roi, market_gap
    severity = Column(String, nullable=False)       # critical, warning, info
    message = Column(String, nullable=False)
    diagnostic_data = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="open")  # open, acted_on, resolved, wont_fix
    acted_on_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    before_metric = Column(Float, nullable=True)
    after_metric = Column(Float, nullable=True)
    source = Column(String, default="rules")        # rules or ml
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_recommendations_provider", "provider_id"),
        Index("idx_recommendations_status", "status"),
    )
```

- [ ] **Step 4: Add migration function to migrations.py**

Add to `backend/src/ml/migrations.py`:

```python
def _create_provider_recommendations(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "provider_recommendations"):
        return
    conn.execute("""
        CREATE TABLE provider_recommendations (
            id INTEGER PRIMARY KEY,
            provider_id TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            diagnostic_data JSON,
            status TEXT NOT NULL DEFAULT 'open',
            acted_on_at DATETIME,
            resolved_at DATETIME,
            before_metric REAL,
            after_metric REAL,
            source TEXT DEFAULT 'rules',
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_recommendations_provider ON provider_recommendations(provider_id)")
    conn.execute("CREATE INDEX idx_recommendations_status ON provider_recommendations(status)")
```

And add the call to `run_migrations()`:
```python
def run_migrations(conn: sqlite3.Connection) -> None:
    # ... existing calls ...
    _create_provider_recommendations(conn)
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: All PASS

- [ ] **Step 6: Run migration on production DB**

```bash
cd backend && python -c "
import sqlite3
from src.ml.migrations import run_migrations
conn = sqlite3.connect('data/bankrollbbq.db')
run_migrations(conn)
conn.close()
print('Migration applied')
"
```

- [ ] **Step 7: Commit**

```bash
git add backend/src/db/models.py backend/src/ml/migrations.py backend/tests/test_analytics.py
git commit -m "feat(analytics): add ProviderRecommendation model and migration"
```

---

### Task 2: Provider Value Attribution (Analytics Engine Core)

**Files:**
- Create: `backend/src/ml/analytics/__init__.py`
- Create: `backend/src/ml/analytics/engine.py`
- Modify: `backend/tests/test_analytics.py`

This is the core analytics computation. It queries `provider_run_metrics`, `opportunities`, and `bets` to compute per-provider ROI metrics.

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_analytics.py`:

```python
def test_compute_provider_roi_basic(db_session):
    """Test provider ROI computation with seeded data."""
    from src.db.models import Opportunity, Bet, Event
    from src.ml.analytics.engine import compute_provider_roi

    # Seed an event
    evt = Event(
        id="football:team_a:team_b:2026-03-13",
        sport="football", league="Test League",
        home_team="team_a", away_team="team_b",
    )
    db_session.add(evt)

    # Seed opportunities for betsson (canonical)
    for i in range(5):
        db_session.add(Opportunity(
            event_id=evt.id,
            type="value",
            market="1x2",
            provider1_id="betsson",
            odds1=2.5,
            edge_pct=5.0 + i,
            is_active=True,
        ))

    # Seed 2 bets for betsson
    db_session.add(Bet(
        event_id=evt.id, provider_id="betsson", market="1x2",
        outcome="home", odds=2.5, stake=100, result="won",
        payout=250, bet_type="value",
    ))
    db_session.add(Bet(
        event_id=evt.id, provider_id="betsson", market="1x2",
        outcome="away", odds=2.5, stake=100, result="lost",
        payout=0, bet_type="value",
    ))
    db_session.commit()

    roi = compute_provider_roi(db_session)
    # Should have betsson in results
    betsson = next((r for r in roi if r["provider_id"] == "betsson"), None)
    assert betsson is not None
    assert betsson["total_opportunities"] == 5
    assert betsson["avg_edge"] == 7.0  # (5+6+7+8+9) / 5
    assert betsson["total_bets"] == 2
    assert betsson["win_rate"] == 0.5
    assert betsson["net_pnl"] == 50.0  # (250 - 100) + (0 - 100) = 50


def test_compute_provider_roi_canonical_grouping(db_session):
    """Test that alias providers group under canonical."""
    from src.db.models import Opportunity, Event
    from src.ml.analytics.engine import compute_provider_roi

    evt = Event(
        id="football:team_c:team_d:2026-03-13",
        sport="football", league="Test",
        home_team="team_c", away_team="team_d",
    )
    db_session.add(evt)

    # leovegas is a Kambi alias for unibet
    for pid in ["unibet", "leovegas", "expekt"]:
        db_session.add(Opportunity(
            event_id=evt.id, type="value", market="1x2",
            provider1_id=pid, odds1=2.0, edge_pct=4.0, is_active=True,
        ))
    db_session.commit()

    roi = compute_provider_roi(db_session)
    # All 3 should be grouped under unibet (canonical)
    unibet = next((r for r in roi if r["provider_id"] == "unibet"), None)
    assert unibet is not None
    assert unibet["total_opportunities"] == 3


def test_compute_provider_roi_empty_db(db_session):
    """No data should return empty list."""
    from src.ml.analytics.engine import compute_provider_roi
    roi = compute_provider_roi(db_session)
    assert roi == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_analytics.py::test_compute_provider_roi_basic -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create analytics engine**

```python
# backend/src/ml/analytics/__init__.py
```

```python
# backend/src/ml/analytics/engine.py
"""Extraction analytics engine — computes provider ROI, coverage gaps, scheduling efficiency.

Queries existing tables (provider_run_metrics, sport_run_metrics, opportunities, bets)
directly. No dependency on Phase 1 ML tables.
"""
import logging
from sqlalchemy import func, case, distinct

from src.constants import PROVIDER_CANONICAL, CANONICAL_MEMBERS

logger = logging.getLogger(__name__)


def _canonical(provider_id: str) -> str:
    """Map provider to canonical (e.g., leovegas -> unibet)."""
    return PROVIDER_CANONICAL.get(provider_id, provider_id)


def compute_provider_roi(session, limit_runs: int = 10) -> list[dict]:
    """Compute per-provider ROI from opportunities and bets.

    Groups alias providers under their canonical provider.
    Returns list of dicts sorted by total_opportunities descending.
    """
    from src.db.models import Opportunity, Bet

    # Get all value opportunities grouped by provider
    opp_rows = (
        session.query(
            Opportunity.provider1_id,
            func.count().label("cnt"),
            func.avg(Opportunity.edge_pct).label("avg_edge"),
        )
        .filter(Opportunity.type == "value")
        .group_by(Opportunity.provider1_id)
        .all()
    )

    if not opp_rows:
        return []

    # Aggregate under canonical providers
    canonical_opps = {}  # {canonical: {total_opportunities, sum_edge, count}}
    for provider_id, cnt, avg_edge in opp_rows:
        canon = _canonical(provider_id)
        if canon not in canonical_opps:
            canonical_opps[canon] = {"total_opportunities": 0, "sum_edge": 0.0, "count": 0}
        canonical_opps[canon]["total_opportunities"] += cnt
        canonical_opps[canon]["sum_edge"] += (avg_edge or 0) * cnt
        canonical_opps[canon]["count"] += cnt

    # Get bet results grouped by provider
    bet_rows = (
        session.query(
            Bet.provider_id,
            func.count().label("total_bets"),
            func.sum(case((Bet.result == "won", 1), else_=0)).label("wins"),
            func.sum(case((Bet.result == "lost", 1), else_=0)).label("losses"),
            func.sum(case(
                (Bet.result == "won", Bet.payout - Bet.stake),
                (Bet.result == "lost", -Bet.stake),
                else_=0,
            )).label("net_pnl"),
        )
        .filter(Bet.result.in_(["won", "lost"]))
        .group_by(Bet.provider_id)
        .all()
    )

    canonical_bets = {}
    for provider_id, total, wins, losses, pnl in bet_rows:
        canon = _canonical(provider_id)
        if canon not in canonical_bets:
            canonical_bets[canon] = {"total_bets": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        canonical_bets[canon]["total_bets"] += total
        canonical_bets[canon]["wins"] += wins
        canonical_bets[canon]["losses"] += losses
        canonical_bets[canon]["net_pnl"] += float(pnl or 0)

    # Build result list
    results = []
    for canon, opp_data in canonical_opps.items():
        bet_data = canonical_bets.get(canon, {"total_bets": 0, "wins": 0, "losses": 0, "net_pnl": 0.0})
        resolved = bet_data["wins"] + bet_data["losses"]
        results.append({
            "provider_id": canon,
            "total_opportunities": opp_data["total_opportunities"],
            "avg_edge": round(opp_data["sum_edge"] / opp_data["count"], 2) if opp_data["count"] > 0 else 0.0,
            "total_bets": bet_data["total_bets"],
            "win_rate": round(bet_data["wins"] / resolved, 3) if resolved > 0 else None,
            "net_pnl": round(bet_data["net_pnl"], 2),
        })

    results.sort(key=lambda x: x["total_opportunities"], reverse=True)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/analytics/ backend/tests/test_analytics.py
git commit -m "feat(analytics): implement provider value attribution engine"
```

---

### Task 3: Coverage Gap Analysis

**Files:**
- Modify: `backend/src/ml/analytics/engine.py`
- Modify: `backend/tests/test_analytics.py`

Computes per-provider per-sport coverage gaps from `sport_run_metrics`.

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_analytics.py`:

```python
def test_compute_coverage_gaps(db_session):
    """Test coverage gap computation from sport_run_metrics."""
    from src.ml.analytics.engine import compute_coverage_gaps

    # We need to seed sport_run_metrics directly since it's not an ORM model
    # but a raw table populated by metrics.persist_to_db()
    # Use raw SQL to insert test data
    from sqlalchemy import text
    db_session.execute(text("""
        INSERT INTO sport_run_metrics (run_id, provider_id, sport, duration_seconds,
            events_processed, events_new, events_matched, events_unmatched,
            odds_processed, odds_new, ml_count, spread_count, total_count, success)
        VALUES
            ('run1', 'betsson', 'football', 10.0, 80, 0, 65, 15, 500, 0, 65, 30, 40, 1),
            ('run1', 'betsson', 'tennis', 5.0, 40, 0, 20, 20, 200, 0, 20, 0, 0, 1),
            ('run1', 'pinnacle', 'football', 5.0, 100, 0, 100, 0, 800, 0, 100, 90, 95, 1),
            ('run1', 'pinnacle', 'tennis', 3.0, 60, 0, 60, 0, 400, 0, 60, 50, 55, 1)
    """))
    db_session.commit()

    gaps = compute_coverage_gaps(db_session)
    # Should have betsson football and betsson tennis
    fb = next((g for g in gaps if g["provider_id"] == "betsson" and g["sport"] == "football"), None)
    assert fb is not None
    assert fb["pinnacle_events"] == 100
    assert fb["matched_events"] == 65
    assert fb["event_coverage_pct"] == 65.0

    tn = next((g for g in gaps if g["provider_id"] == "betsson" and g["sport"] == "tennis"), None)
    assert tn is not None
    assert tn["spread_count"] == 0
    assert tn["pinnacle_spread_count"] == 50


def test_compute_coverage_gaps_empty(db_session):
    from src.ml.analytics.engine import compute_coverage_gaps
    gaps = compute_coverage_gaps(db_session)
    assert gaps == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_analytics.py::test_compute_coverage_gaps -v`
Expected: FAIL — function not defined

- [ ] **Step 3: Implement coverage gap computation**

Add to `backend/src/ml/analytics/engine.py`:

```python
def compute_coverage_gaps(session) -> list[dict]:
    """Compute per-provider per-sport coverage vs Pinnacle from sport_run_metrics.

    Uses the latest run's data per provider per sport. Compares each soft
    provider's event/market counts against Pinnacle's baseline.

    Returns list of dicts sorted by missing_events descending (biggest gaps first).
    """
    from sqlalchemy import text

    # Get Pinnacle baseline per sport (latest run)
    pin_rows = session.execute(text("""
        SELECT sport, events_processed, ml_count, spread_count, total_count
        FROM sport_run_metrics
        WHERE provider_id = 'pinnacle'
        AND run_id = (SELECT run_id FROM sport_run_metrics WHERE provider_id = 'pinnacle' ORDER BY rowid DESC LIMIT 1)
    """)).fetchall()

    if not pin_rows:
        return []

    pinnacle_baseline = {}
    for sport, events, ml, spread, total in pin_rows:
        pinnacle_baseline[sport] = {
            "events": events, "ml": ml, "spread": spread, "total": total,
        }

    # Get soft provider data (latest run per provider)
    soft_rows = session.execute(text("""
        SELECT provider_id, sport, events_matched, ml_count, spread_count, total_count
        FROM sport_run_metrics
        WHERE provider_id NOT IN ('pinnacle', 'polymarket')
        AND run_id IN (
            SELECT DISTINCT run_id FROM sport_run_metrics
            WHERE provider_id NOT IN ('pinnacle', 'polymarket')
            ORDER BY rowid DESC
            LIMIT 1
        )
    """)).fetchall()

    results = []
    for provider_id, sport, matched, ml, spread, total in soft_rows:
        pin = pinnacle_baseline.get(sport)
        if not pin:
            continue

        pin_events = pin["events"]
        coverage_pct = round(100 * matched / pin_events, 1) if pin_events > 0 else 0.0

        results.append({
            "provider_id": provider_id,
            "sport": sport,
            "pinnacle_events": pin_events,
            "matched_events": matched,
            "event_coverage_pct": coverage_pct,
            "missing_events": pin_events - matched,
            "ml_count": ml,
            "spread_count": spread,
            "total_count": total,
            "pinnacle_ml_count": pin["ml"],
            "pinnacle_spread_count": pin["spread"],
            "pinnacle_total_count": pin["total"],
            "missing_spread": pin["spread"] - spread,
            "missing_total": pin["total"] - total,
        })

    results.sort(key=lambda x: x["missing_events"], reverse=True)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/analytics/engine.py backend/tests/test_analytics.py
git commit -m "feat(analytics): add coverage gap analysis"
```

---

### Task 4: Scheduling Efficiency

**Files:**
- Modify: `backend/src/ml/analytics/engine.py`
- Modify: `backend/tests/test_analytics.py`

Computes per-tier scheduling metrics from `extraction_runs`.

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_analytics.py`:

```python
def test_compute_scheduling_efficiency(db_session):
    """Test scheduling efficiency from extraction_runs."""
    from src.ml.analytics.engine import compute_scheduling_efficiency
    from sqlalchemy import text

    # Seed extraction_runs
    db_session.execute(text("""
        INSERT INTO extraction_runs (id, start_time, end_time, duration_seconds,
            providers_attempted, providers_succeeded, providers_failed,
            total_events, total_odds, trigger)
        VALUES
            ('run1', '2026-03-13 10:00:00', '2026-03-13 10:02:30', 150.0, 8, 7, 1, 9000, 35000, 'api_soft'),
            ('run2', '2026-03-13 14:00:00', '2026-03-13 14:02:00', 120.0, 8, 8, 0, 10000, 40000, 'api_soft'),
            ('run3', '2026-03-13 10:00:00', '2026-03-13 10:00:50', 50.0, 2, 2, 0, 2000, 20000, 'sharp')
    """))
    db_session.commit()

    sched = compute_scheduling_efficiency(db_session)
    assert "api_soft" in sched
    assert sched["api_soft"]["runs"] == 2
    assert sched["api_soft"]["avg_duration"] == 135.0
    assert sched["api_soft"]["avg_events"] == 9500.0
    assert "sharp" in sched
    assert sched["sharp"]["runs"] == 1


def test_compute_scheduling_efficiency_empty(db_session):
    from src.ml.analytics.engine import compute_scheduling_efficiency
    sched = compute_scheduling_efficiency(db_session)
    assert sched == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_analytics.py::test_compute_scheduling_efficiency -v`
Expected: FAIL — function not defined

- [ ] **Step 3: Implement scheduling efficiency**

Add to `backend/src/ml/analytics/engine.py`:

```python
def compute_scheduling_efficiency(session) -> dict:
    """Compute per-tier scheduling metrics from extraction_runs.

    Returns dict keyed by trigger name with avg duration, events, odds, and events/sec.
    """
    from sqlalchemy import text

    rows = session.execute(text("""
        SELECT trigger,
            COUNT(*) as runs,
            AVG(duration_seconds) as avg_duration,
            AVG(total_events) as avg_events,
            AVG(total_odds) as avg_odds
        FROM extraction_runs
        GROUP BY trigger
    """)).fetchall()

    results = {}
    for trigger, runs, avg_dur, avg_events, avg_odds in rows:
        events_per_sec = round(avg_events / avg_dur, 1) if avg_dur > 0 else 0.0
        results[trigger] = {
            "runs": runs,
            "avg_duration": round(avg_dur, 1),
            "avg_events": round(avg_events, 1),
            "avg_odds": round(avg_odds, 1),
            "events_per_sec": events_per_sec,
        }

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/analytics/engine.py backend/tests/test_analytics.py
git commit -m "feat(analytics): add scheduling efficiency analysis"
```

---

### Task 5: Diagnostic Rules Engine

**Files:**
- Create: `backend/src/ml/analytics/diagnostics.py`
- Modify: `backend/tests/test_analytics.py`

Rule-based diagnostic engine that generates recommendations. Implements "diagnose before deprioritize" philosophy.

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_analytics.py`:

```python
def test_diagnose_match_rate_drop():
    """Test match rate drop detection."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "dbet",
        "avg_match_rate": 0.55,
        "prev_match_rate": 0.82,
        "avg_events": 166,
        "avg_duration": 42.5,
        "total_opportunities": 20,
        "seconds_per_value_bet": 8.3,
    }

    recommendations = diagnose_provider(provider_data)
    assert len(recommendations) >= 1
    match_rec = next((r for r in recommendations if r["category"] == "match_rate"), None)
    assert match_rec is not None
    assert match_rec["severity"] in ("warning", "critical")
    assert "match rate" in match_rec["message"].lower()


def test_diagnose_zero_spreads():
    """Test missing market detection."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "betinia",
        "avg_match_rate": 0.85,
        "spread_count": 0,
        "total_count": 45,
        "avg_events": 67,
        "avg_duration": 16.0,
        "total_opportunities": 15,
    }

    recommendations = diagnose_provider(provider_data)
    market_rec = next((r for r in recommendations if r["category"] == "market_gap"), None)
    assert market_rec is not None
    assert "spread" in market_rec["message"].lower()


def test_diagnose_slow_provider():
    """Test slow extraction detection."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "comeon",
        "avg_match_rate": 0.70,
        "avg_events": 42,
        "avg_duration": 180.0,
        "total_opportunities": 2,
        "seconds_per_value_bet": 90.0,
    }

    recommendations = diagnose_provider(provider_data)
    timing_rec = next((r for r in recommendations if r["category"] == "timing"), None)
    assert timing_rec is not None


def test_diagnose_healthy_provider():
    """Healthy provider should get no recommendations."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "unibet",
        "avg_match_rate": 0.85,
        "spread_count": 30,
        "total_count": 45,
        "avg_events": 284,
        "avg_duration": 25.0,
        "total_opportunities": 50,
        "seconds_per_value_bet": 2.1,
    }

    recommendations = diagnose_provider(provider_data)
    assert len(recommendations) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_analytics.py::test_diagnose_match_rate_drop -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement diagnostic rules**

```python
# backend/src/ml/analytics/diagnostics.py
"""Rule-based diagnostic engine for provider health.

Philosophy: diagnose -> recommend fix -> track -> deprioritize only as last resort.

Each rule checks a specific condition and produces a recommendation
with category, severity, message, and diagnostic data.
"""
import logging

logger = logging.getLogger(__name__)

# Thresholds for diagnostics
MATCH_RATE_WARNING = 0.65
MATCH_RATE_CRITICAL = 0.40
MATCH_RATE_DROP_THRESHOLD = 0.15  # 15% drop triggers warning
SLOW_SECONDS_PER_VB = 30.0  # >30s per value bet is slow
SLOW_DURATION = 120.0  # >120s extraction is slow


def diagnose_provider(provider_data: dict) -> list[dict]:
    """Run all diagnostic rules against a provider's metrics.

    Args:
        provider_data: dict with keys like avg_match_rate, avg_events,
            avg_duration, total_opportunities, seconds_per_value_bet,
            spread_count, total_count, prev_match_rate, etc.

    Returns:
        List of recommendation dicts with: category, severity, message, diagnostic_data
    """
    recommendations = []

    # Rule 1: Match rate drop
    match_rate = provider_data.get("avg_match_rate", 1.0)
    prev_rate = provider_data.get("prev_match_rate")
    provider_id = provider_data.get("provider_id", "unknown")

    if match_rate < MATCH_RATE_CRITICAL:
        recommendations.append({
            "category": "match_rate",
            "severity": "critical",
            "message": f"{provider_id}: match rate is {match_rate:.0%} — check sports.yaml aliases and API changes",
            "diagnostic_data": {"current": match_rate, "threshold": MATCH_RATE_CRITICAL},
        })
    elif prev_rate is not None and (prev_rate - match_rate) > MATCH_RATE_DROP_THRESHOLD:
        recommendations.append({
            "category": "match_rate",
            "severity": "warning",
            "message": f"{provider_id}: match rate dropped from {prev_rate:.0%} to {match_rate:.0%} — check API changes or team name normalization",
            "diagnostic_data": {"current": match_rate, "previous": prev_rate, "drop": prev_rate - match_rate},
        })
    elif match_rate < MATCH_RATE_WARNING:
        recommendations.append({
            "category": "match_rate",
            "severity": "warning",
            "message": f"{provider_id}: match rate is {match_rate:.0%} — review sports.yaml aliases",
            "diagnostic_data": {"current": match_rate, "threshold": MATCH_RATE_WARNING},
        })

    # Rule 2: Missing markets (spread or total = 0)
    spread = provider_data.get("spread_count")
    total = provider_data.get("total_count")
    if spread is not None and spread == 0 and provider_data.get("avg_events", 0) > 20:
        recommendations.append({
            "category": "market_gap",
            "severity": "warning",
            "message": f"{provider_id}: 0 spread markets — needs Pass 2 enrichment or API endpoint check",
            "diagnostic_data": {"spread_count": 0, "total_count": total},
        })
    if total is not None and total == 0 and provider_data.get("avg_events", 0) > 20:
        recommendations.append({
            "category": "market_gap",
            "severity": "warning",
            "message": f"{provider_id}: 0 total markets — needs enrichment or API endpoint check",
            "diagnostic_data": {"spread_count": spread, "total_count": 0},
        })

    # Rule 3: Slow extraction / poor ROI
    sec_per_vb = provider_data.get("seconds_per_value_bet")
    duration = provider_data.get("avg_duration", 0)

    if sec_per_vb is not None and sec_per_vb > SLOW_SECONDS_PER_VB:
        recommendations.append({
            "category": "timing",
            "severity": "warning",
            "message": f"{provider_id}: {sec_per_vb:.1f}s per value bet (threshold: {SLOW_SECONDS_PER_VB}s) — investigate extraction bottleneck before deprioritizing",
            "diagnostic_data": {"seconds_per_value_bet": sec_per_vb, "avg_duration": duration},
        })
    elif duration > SLOW_DURATION and (provider_data.get("total_opportunities", 0) < 5):
        recommendations.append({
            "category": "timing",
            "severity": "info",
            "message": f"{provider_id}: {duration:.0f}s extraction for {provider_data.get('total_opportunities', 0)} opportunities — low yield",
            "diagnostic_data": {"avg_duration": duration, "total_opportunities": provider_data.get("total_opportunities", 0)},
        })

    return recommendations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/analytics/diagnostics.py backend/tests/test_analytics.py
git commit -m "feat(analytics): add diagnostic rules engine"
```

---

### Task 6: Recommendation Manager

**Files:**
- Create: `backend/src/ml/analytics/recommendations.py`
- Modify: `backend/tests/test_analytics.py`

Manages recommendation lifecycle: create (dedup), update status, get active.

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_analytics.py`:

```python
def test_recommendation_manager_create(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec = mgr.create(
        provider_id="dbet",
        category="match_rate",
        severity="warning",
        message="Match rate dropped to 55%",
        before_metric=0.55,
    )
    assert rec.id is not None
    assert rec.status == "open"


def test_recommendation_manager_dedup(db_session):
    """Creating same category+provider should not duplicate."""
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec1 = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                       message="First message", before_metric=0.55)
    rec2 = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                       message="Updated message", before_metric=0.50)
    # Should return the existing one (updated message)
    assert rec1.id == rec2.id
    assert rec2.message == "Updated message"
    assert rec2.before_metric == 0.50


def test_recommendation_manager_get_active(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    mgr.create(provider_id="dbet", category="match_rate", severity="warning",
               message="Test", before_metric=0.55)
    mgr.create(provider_id="comeon", category="timing", severity="critical",
               message="Slow", before_metric=90.0)

    active = mgr.get_active()
    assert len(active) == 2


def test_recommendation_manager_update_status(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                     message="Test", before_metric=0.55)

    updated = mgr.update_status(rec.id, "acted_on")
    assert updated.status == "acted_on"
    assert updated.acted_on_at is not None


def test_recommendation_manager_resolve(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                     message="Test", before_metric=0.55)

    resolved = mgr.update_status(rec.id, "resolved", after_metric=0.82)
    assert resolved.status == "resolved"
    assert resolved.after_metric == 0.82
    assert resolved.resolved_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_analytics.py::test_recommendation_manager_create -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement recommendation manager**

```python
# backend/src/ml/analytics/recommendations.py
"""Manages provider recommendation lifecycle.

Deduplicates by (provider_id, category, status='open') — only one open
recommendation per provider per category at a time. When a new recommendation
for the same provider+category arrives, the existing one is updated.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RecommendationManager:
    def __init__(self, session):
        self.session = session

    def create(
        self,
        provider_id: str,
        category: str,
        severity: str,
        message: str,
        before_metric: float | None = None,
        diagnostic_data: dict | None = None,
        source: str = "rules",
    ):
        """Create or update a recommendation. Deduplicates by provider+category for open recs."""
        from src.db.models import ProviderRecommendation

        # Check for existing open recommendation
        existing = (
            self.session.query(ProviderRecommendation)
            .filter_by(provider_id=provider_id, category=category, status="open")
            .first()
        )

        if existing:
            existing.message = message
            existing.severity = severity
            existing.before_metric = before_metric
            existing.diagnostic_data = diagnostic_data
            existing.source = source
            self.session.flush()
            return existing

        rec = ProviderRecommendation(
            provider_id=provider_id,
            category=category,
            severity=severity,
            message=message,
            diagnostic_data=diagnostic_data,
            status="open",
            before_metric=before_metric,
            source=source,
        )
        self.session.add(rec)
        self.session.flush()
        return rec

    def get_active(self, provider_id: str | None = None) -> list:
        """Get all open/acted_on recommendations, optionally filtered by provider."""
        from src.db.models import ProviderRecommendation

        q = self.session.query(ProviderRecommendation).filter(
            ProviderRecommendation.status.in_(["open", "acted_on"])
        )
        if provider_id:
            q = q.filter_by(provider_id=provider_id)
        return q.order_by(ProviderRecommendation.created_at.desc()).all()

    def update_status(self, rec_id: int, status: str, after_metric: float | None = None):
        """Update recommendation status."""
        from src.db.models import ProviderRecommendation

        rec = self.session.query(ProviderRecommendation).get(rec_id)
        if not rec:
            return None

        rec.status = status
        now = datetime.now(timezone.utc)

        if status == "acted_on":
            rec.acted_on_at = now
        elif status == "resolved":
            rec.resolved_at = now
            if after_metric is not None:
                rec.after_metric = after_metric

        self.session.flush()
        return rec

    def get_all(self, limit: int = 50) -> list:
        """Get all recommendations ordered by created_at desc."""
        from src.db.models import ProviderRecommendation
        return (
            self.session.query(ProviderRecommendation)
            .order_by(ProviderRecommendation.created_at.desc())
            .limit(limit)
            .all()
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/analytics/recommendations.py backend/tests/test_analytics.py
git commit -m "feat(analytics): add recommendation manager with dedup and lifecycle"
```

---

### Task 7: AnalyticsEngine.refresh() — The Glue

**Files:**
- Modify: `backend/src/ml/analytics/engine.py`
- Modify: `backend/tests/test_analytics.py`

The `refresh()` method ties everything together: computes analytics, runs diagnostics, creates/updates recommendations. Called after each extraction run.

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_analytics.py`:

```python
def test_analytics_refresh(db_session):
    """Test full refresh cycle: compute analytics + generate recommendations."""
    from src.ml.analytics.engine import AnalyticsEngine
    from src.db.models import ProviderRecommendation, Event, Opportunity
    from sqlalchemy import text

    # Seed minimal data
    evt = Event(
        id="football:x:y:2026-03-13", sport="football", league="Test",
        home_team="x", away_team="y",
    )
    db_session.add(evt)

    # Provider with poor match rate
    db_session.execute(text("""
        INSERT INTO provider_run_metrics (run_id, provider_id, start_time, end_time,
            duration_seconds, events_processed, events_new, odds_processed, odds_new,
            sports_attempted, sports_succeeded, events_matched, events_unmatched,
            ml_count, spread_count, total_count, status)
        VALUES ('run1', 'comeon', '2026-03-13', '2026-03-13', 180.0, 42, 0, 200, 0,
            5, 5, 15, 27, 0, 0, 0, 'success')
    """))

    db_session.add(Opportunity(
        event_id=evt.id, type="value", market="1x2",
        provider1_id="comeon", odds1=2.0, edge_pct=3.0, is_active=True,
    ))
    db_session.commit()

    engine = AnalyticsEngine()
    result = engine.refresh(db_session, "run1")

    assert "provider_roi" in result
    assert "recommendations" in result

    # comeon should have match_rate recommendation (15/42 = 36%)
    recs = db_session.query(ProviderRecommendation).filter_by(provider_id="comeon").all()
    assert len(recs) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_analytics.py::test_analytics_refresh -v`
Expected: FAIL — AnalyticsEngine not defined

- [ ] **Step 3: Implement AnalyticsEngine.refresh()**

Add to `backend/src/ml/analytics/engine.py`:

```python
class AnalyticsEngine:
    """Orchestrates analytics computation and recommendation generation."""

    def refresh(self, session, run_id: str) -> dict:
        """Run full analytics refresh after an extraction run.

        1. Compute provider ROI
        2. Compute coverage gaps
        3. Compute scheduling efficiency
        4. Run diagnostics on provider metrics
        5. Create/update recommendations

        Returns dict with all analytics results.
        """
        from .diagnostics import diagnose_provider
        from .recommendations import RecommendationManager
        from sqlalchemy import text

        provider_roi = compute_provider_roi(session)
        coverage_gaps = compute_coverage_gaps(session)
        scheduling = compute_scheduling_efficiency(session)

        # Build per-provider diagnostic data from provider_run_metrics
        provider_metrics = session.execute(text("""
            SELECT provider_id,
                AVG(duration_seconds) as avg_duration,
                AVG(events_processed) as avg_events,
                AVG(CASE WHEN events_processed > 0
                    THEN CAST(events_matched AS REAL) / events_processed
                    ELSE 0 END) as avg_match_rate,
                SUM(spread_count) as spread_count,
                SUM(total_count) as total_count
            FROM provider_run_metrics
            WHERE provider_id NOT IN ('pinnacle', 'polymarket')
            GROUP BY provider_id
        """)).fetchall()

        mgr = RecommendationManager(session)
        all_recs = []

        for pid, avg_dur, avg_events, avg_mr, spr_cnt, tot_cnt in provider_metrics:
            # Find matching ROI data
            roi_data = next((r for r in provider_roi if r["provider_id"] == pid), {})
            total_opps = roi_data.get("total_opportunities", 0)
            sec_per_vb = round(avg_dur / max(total_opps / 10, 1), 1) if avg_dur and total_opps else None

            diag_data = {
                "provider_id": pid,
                "avg_match_rate": avg_mr or 0,
                "avg_events": avg_events or 0,
                "avg_duration": avg_dur or 0,
                "total_opportunities": total_opps,
                "seconds_per_value_bet": sec_per_vb,
                "spread_count": spr_cnt or 0,
                "total_count": tot_cnt or 0,
            }

            recommendations = diagnose_provider(diag_data)
            for rec in recommendations:
                created = mgr.create(
                    provider_id=pid,
                    category=rec["category"],
                    severity=rec["severity"],
                    message=rec["message"],
                    before_metric=rec.get("diagnostic_data", {}).get("current"),
                    diagnostic_data=rec.get("diagnostic_data"),
                )
                all_recs.append(created)

        session.flush()

        return {
            "provider_roi": provider_roi,
            "coverage_gaps": coverage_gaps,
            "scheduling": scheduling,
            "recommendations": [
                {"id": r.id, "provider_id": r.provider_id, "category": r.category,
                 "severity": r.severity, "message": r.message, "status": r.status}
                for r in all_recs
            ],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_analytics.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/analytics/engine.py backend/tests/test_analytics.py
git commit -m "feat(analytics): implement AnalyticsEngine.refresh() orchestration"
```

---

## Chunk 2: Integration — CLI Report, API, Frontend

### Task 8: Extend Extraction Report with Provider ROI + Recommendations

**Files:**
- Modify: `backend/src/pipeline/extraction_report.py`

Add two new sections to the extraction report: Provider ROI table and Recommendations.

- [ ] **Step 1: Read extraction_report.py generate() method**

Read `backend/src/pipeline/extraction_report.py` to find where to insert new sections. The `generate()` method builds a list of `lines` and returns them joined. New sections go before the final separator at line ~197.

- [ ] **Step 2: Add _build_provider_roi() method**

Add a new method to `ExtractionReport`:

```python
def _build_provider_roi(self, session) -> list[str]:
    """Build Provider ROI section from analytics engine."""
    try:
        from src.ml.analytics.engine import compute_provider_roi
    except ImportError:
        return []

    roi = compute_provider_roi(session)
    if not roi:
        return []

    lines = []
    lines.append("")
    lines.append("PROVIDER ROI (all time)")
    lines.append("-" * 90)
    lines.append(f"{'Provider':<20s} {'Opps':>6s} {'Edge%':>6s} {'Bets':>5s} {'Win%':>5s} {'P&L':>8s}")
    lines.append("-" * 90)

    for r in roi[:15]:  # Top 15
        win_str = f"{r['win_rate']:.0%}" if r['win_rate'] is not None else "-"
        pnl_str = f"{r['net_pnl']:+.0f}" if r['net_pnl'] else "0"
        lines.append(
            f"{r['provider_id']:<20s} {r['total_opportunities']:>6d} "
            f"{r['avg_edge']:>5.1f}% {r['total_bets']:>5d} "
            f"{win_str:>5s} {pnl_str:>8s}"
        )

    lines.append("-" * 90)
    return lines
```

- [ ] **Step 3: Add _build_recommendations() method**

```python
def _build_recommendations(self, session) -> list[str]:
    """Build Recommendations section from analytics engine."""
    try:
        from src.ml.analytics.recommendations import RecommendationManager
    except ImportError:
        return []

    mgr = RecommendationManager(session)
    active = mgr.get_active()
    if not active:
        return []

    severity_icon = {"critical": "!", "warning": "~", "info": "+"}
    lines = []
    lines.append("")
    lines.append("RECOMMENDATIONS")
    lines.append("-" * 90)
    for rec in active[:10]:
        icon = severity_icon.get(rec.severity, " ")
        lines.append(f"{icon} {rec.message}")
    lines.append("-" * 90)
    return lines
```

- [ ] **Step 4: Wire into generate()**

In the `generate()` method, add calls to the new methods before the final separator. Find the existing pattern where `_build_boost_health()` is called and add after it:

```python
        # Provider ROI section
        if db_session:
            try:
                roi_lines = self._build_provider_roi(db_session)
                lines.extend(roi_lines)
                rec_lines = self._build_recommendations(db_session)
                lines.extend(rec_lines)
            except Exception:
                pass
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/extraction_report.py
git commit -m "feat(analytics): add Provider ROI and Recommendations to extraction report"
```

---

### Task 9: Hook AnalyticsEngine into Orchestrator

**Files:**
- Modify: `backend/src/pipeline/orchestrator.py`

Add analytics refresh after the existing Phase 1 ML hooks (around line 1007).

- [ ] **Step 1: Read orchestrator.py to find exact insertion point**

Read `backend/src/pipeline/orchestrator.py` around lines 985-1010 where Phase 1 ML hooks are. The analytics refresh goes after `log_extraction_run()` and before `self.session.commit()`.

- [ ] **Step 2: Add analytics refresh hook**

Insert after the existing ML feature logging block:

```python
            # Run extraction analytics (best-effort, never blocks extraction)
            try:
                from src.ml.analytics.engine import AnalyticsEngine
                analytics = AnalyticsEngine()
                analytics.refresh(self.session, run_id)
                self.session.commit()
            except Exception as e:
                logger.debug(f"Extraction analytics skipped: {e}")
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/pipeline/orchestrator.py
git commit -m "feat(analytics): hook AnalyticsEngine.refresh() into orchestrator"
```

---

### Task 10: API Endpoints

**Files:**
- Modify: `backend/src/api/routes/extraction.py`

Add 3 endpoints for analytics and recommendations.

- [ ] **Step 1: Add GET /analytics endpoint**

Add to `backend/src/api/routes/extraction.py`:

```python
@router.get("/analytics")
async def get_extraction_analytics():
    """Get extraction analytics: provider ROI, coverage gaps, scheduling efficiency."""
    from src.db.models import get_session
    from src.ml.analytics.engine import compute_provider_roi, compute_coverage_gaps, compute_scheduling_efficiency

    session = get_session()
    try:
        return {
            "provider_roi": compute_provider_roi(session),
            "coverage_gaps": compute_coverage_gaps(session),
            "scheduling": compute_scheduling_efficiency(session),
        }
    finally:
        session.close()
```

- [ ] **Step 2: Add GET /recommendations endpoint**

```python
@router.get("/recommendations")
async def get_extraction_recommendations():
    """Get active extraction recommendations."""
    from src.db.models import get_session
    from src.ml.analytics.recommendations import RecommendationManager

    session = get_session()
    try:
        mgr = RecommendationManager(session)
        active = mgr.get_active()
        return [
            {
                "id": r.id,
                "provider_id": r.provider_id,
                "category": r.category,
                "severity": r.severity,
                "message": r.message,
                "status": r.status,
                "before_metric": r.before_metric,
                "after_metric": r.after_metric,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in active
        ]
    finally:
        session.close()
```

- [ ] **Step 3: Add PATCH /recommendations/{id} endpoint**

```python
@router.patch("/recommendations/{rec_id}")
async def update_recommendation(rec_id: int, status: str, after_metric: float = None):
    """Update recommendation status (acted_on, resolved, wont_fix)."""
    from src.db.models import get_session
    from src.ml.analytics.recommendations import RecommendationManager

    if status not in ("acted_on", "resolved", "wont_fix"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    session = get_session()
    try:
        mgr = RecommendationManager(session)
        rec = mgr.update_status(rec_id, status, after_metric=after_metric)
        session.commit()
        if not rec:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Recommendation not found")
        return {"id": rec.id, "status": rec.status}
    finally:
        session.close()
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/extraction.py
git commit -m "feat(analytics): add extraction analytics and recommendations API endpoints"
```

---

### Task 11: Frontend API Service

**Files:**
- Modify: `frontend/src/services/api.ts`

Add API methods for the new endpoints.

- [ ] **Step 1: Add API methods**

Add to the `api` object in `frontend/src/services/api.ts`:

```typescript
  async getExtractionAnalytics() {
    return fetchWithRetry<{
      provider_roi: Array<{
        provider_id: string;
        total_opportunities: number;
        avg_edge: number;
        total_bets: number;
        win_rate: number | null;
        net_pnl: number;
      }>;
      coverage_gaps: Array<{
        provider_id: string;
        sport: string;
        pinnacle_events: number;
        matched_events: number;
        event_coverage_pct: number;
        missing_events: number;
        spread_count: number;
        total_count: number;
        pinnacle_spread_count: number;
        pinnacle_total_count: number;
      }>;
      scheduling: Record<string, {
        runs: number;
        avg_duration: number;
        avg_events: number;
        events_per_sec: number;
      }>;
    }>(`${API_BASE}/extraction/analytics`);
  },

  async getExtractionRecommendations() {
    return fetchWithRetry<Array<{
      id: number;
      provider_id: string;
      category: string;
      severity: string;
      message: string;
      status: string;
      before_metric: number | null;
      after_metric: number | null;
      source: string;
      created_at: string | null;
    }>>(`${API_BASE}/extraction/recommendations`);
  },

  async updateRecommendation(id: number, status: string, afterMetric?: number) {
    const params = new URLSearchParams({ status });
    if (afterMetric !== undefined) params.set("after_metric", String(afterMetric));
    return fetchWithRetry<{ id: number; status: string }>(
      `${API_BASE}/extraction/recommendations/${id}?${params}`,
      { method: "PATCH" }
    );
  },
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/services/api.ts
git commit -m "feat(analytics): add extraction analytics API methods to frontend"
```

---

### Task 12: StatsPage Extraction Section

**Files:**
- Modify: `frontend/src/components/Terminal/pages/StatsPage.tsx`

Add an "Extraction Analytics" section with provider ROI table and recommendations.

- [ ] **Step 1: Read StatsPage.tsx current structure**

Read `frontend/src/components/Terminal/pages/StatsPage.tsx` to understand the existing sections and data-fetching pattern. The component uses `useCallback` for data fetching and `useRefreshOnExtraction` for auto-refresh.

- [ ] **Step 2: Add extraction analytics data fetching**

Add to the state declarations:

```typescript
const [extractionData, setExtractionData] = useState<any>(null);
const [recommendations, setRecommendations] = useState<any[]>([]);
```

Add to `fetchData`:

```typescript
try {
  const [analyticsData, recsData] = await Promise.all([
    api.getExtractionAnalytics(),
    api.getExtractionRecommendations(),
  ]);
  setExtractionData(analyticsData);
  setRecommendations(recsData);
} catch (e) {
  // Analytics endpoints may not exist yet — ignore
}
```

- [ ] **Step 3: Add Provider ROI table**

Add a new section after the existing provider stats table. Follow the existing `sq` table class pattern:

```tsx
{extractionData?.provider_roi?.length > 0 && (
  <>
    <h3 className="text-sm font-bold mt-4 mb-2 text-[var(--text-primary)]">
      Extraction Provider ROI
    </h3>
    <table className="sq w-full">
      <thead>
        <tr>
          <th>Provider</th>
          <th className="text-right">Opps</th>
          <th className="text-right">Edge%</th>
          <th className="text-right">Bets</th>
          <th className="text-right">Win%</th>
          <th className="text-right">P&L</th>
        </tr>
      </thead>
      <tbody>
        {extractionData.provider_roi.map((r: any) => (
          <tr key={r.provider_id}>
            <td>{r.provider_id}</td>
            <td className="text-right">{r.total_opportunities}</td>
            <td className="text-right">{r.avg_edge.toFixed(1)}%</td>
            <td className="text-right">{r.total_bets}</td>
            <td className="text-right">
              {r.win_rate != null ? `${(r.win_rate * 100).toFixed(0)}%` : '-'}
            </td>
            <td className={`text-right ${r.net_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {r.net_pnl >= 0 ? '+' : ''}{r.net_pnl.toFixed(0)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </>
)}
```

- [ ] **Step 4: Add Recommendations section**

```tsx
{recommendations.length > 0 && (
  <>
    <h3 className="text-sm font-bold mt-4 mb-2 text-[var(--text-primary)]">
      Recommendations
    </h3>
    <div className="space-y-1">
      {recommendations.map((r: any) => (
        <div key={r.id} className={`text-xs px-2 py-1 rounded ${
          r.severity === 'critical' ? 'bg-red-900/30 text-red-300' :
          r.severity === 'warning' ? 'bg-yellow-900/30 text-yellow-300' :
          'bg-blue-900/30 text-blue-300'
        }`}>
          <span className="font-mono mr-2">
            {r.severity === 'critical' ? '!' : r.severity === 'warning' ? '~' : '+'}
          </span>
          {r.message}
        </div>
      ))}
    </div>
  </>
)}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/StatsPage.tsx
git commit -m "feat(analytics): add extraction analytics section to StatsPage"
```

---

### Task 13: Full Integration Test

**Files:**
- Modify: `backend/tests/test_analytics.py`

Run all tests and verify the full pipeline works.

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests pass (previous 44 + new analytics tests)

- [ ] **Step 2: Commit if any test fixes needed**

```bash
git add -A
git commit -m "test(analytics): ensure full test suite passes with analytics integration"
```

---

## Chunk 3: ML Auto-Optimizer (Layer 2)

> **Note:** Layer 2 depends on Phase 1 ML tables accumulating data. The models below cannot be trained until activation thresholds are met (50+ runs per tier). This chunk sets up the training infrastructure and model stubs — they activate automatically when data is ready.

### Task 14: LightGBM Trainer Infrastructure

**Files:**
- Create: `backend/src/ml/optimizer/__init__.py`
- Create: `backend/src/ml/optimizer/trainer.py`
- Create: `backend/tests/test_optimizer.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_optimizer.py
"""Tests for ML optimizer training infrastructure."""
import numpy as np


def test_walk_forward_split():
    """Test walk-forward cross-validation with embargo."""
    from src.ml.optimizer.trainer import walk_forward_splits

    n_samples = 100
    splits = list(walk_forward_splits(n_samples, n_splits=5, embargo=5))
    assert len(splits) == 5

    for train_idx, test_idx in splits:
        # Train comes before test
        assert max(train_idx) < min(test_idx) - 5  # embargo gap
        # No overlap
        assert len(set(train_idx) & set(test_idx)) == 0


def test_train_model_basic():
    """Test basic model training with synthetic data."""
    from src.ml.optimizer.trainer import train_model

    np.random.seed(42)
    X = np.random.randn(100, 5)
    y = X[:, 0] * 2 + X[:, 1] + np.random.randn(100) * 0.1

    result = train_model(X, y, task="regression")
    assert "model" in result
    assert "validation_score" in result
    assert result["validation_score"] is not None


def test_train_model_too_few_samples():
    """Too few samples should return None."""
    from src.ml.optimizer.trainer import train_model

    X = np.random.randn(10, 5)
    y = np.random.randn(10)

    result = train_model(X, y, task="regression", min_samples=20)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_optimizer.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement trainer**

```python
# backend/src/ml/optimizer/__init__.py
```

```python
# backend/src/ml/optimizer/trainer.py
"""LightGBM training infrastructure with walk-forward validation.

Walk-forward: train on [0..t], test on [t+embargo..t+embargo+window].
Prevents temporal leakage by ensuring train data always precedes test data
with a purge/embargo gap.
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)

MIN_SAMPLES_DEFAULT = 30


def walk_forward_splits(n_samples: int, n_splits: int = 5, embargo: int = 5):
    """Generate walk-forward cross-validation splits with embargo gap.

    Yields (train_indices, test_indices) tuples where train always
    precedes test with an embargo gap between them.
    """
    test_size = n_samples // (n_splits + 1)

    for i in range(n_splits):
        train_end = test_size * (i + 1)
        test_start = train_end + embargo
        test_end = min(test_start + test_size, n_samples)

        if test_start >= n_samples or test_end <= test_start:
            continue

        train_idx = list(range(train_end))
        test_idx = list(range(test_start, test_end))
        yield train_idx, test_idx


def train_model(
    X: np.ndarray,
    y: np.ndarray,
    task: str = "regression",
    min_samples: int = MIN_SAMPLES_DEFAULT,
    n_splits: int = 3,
    embargo: int = 5,
) -> dict | None:
    """Train a LightGBM model with walk-forward validation.

    Args:
        X: Feature matrix (n_samples, n_features)
        y: Target vector
        task: 'regression' or 'classification'
        min_samples: Minimum samples to proceed
        n_splits: Number of CV splits
        embargo: Gap between train/test

    Returns:
        Dict with 'model', 'validation_score', 'feature_importance'
        or None if insufficient data.
    """
    if len(X) < min_samples:
        logger.info(f"Insufficient data: {len(X)} < {min_samples} min_samples")
        return None

    try:
        import lightgbm as lgb
    except ImportError:
        logger.warning("lightgbm not installed — ML optimizer disabled")
        return None

    objective = "regression" if task == "regression" else "binary"
    metric = "rmse" if task == "regression" else "binary_logloss"

    params = {
        "objective": objective,
        "metric": metric,
        "num_leaves": 15,
        "learning_rate": 0.05,
        "n_estimators": 100,
        "verbose": -1,
        "min_child_samples": 5,
    }

    # Walk-forward validation
    scores = []
    for train_idx, test_idx in walk_forward_splits(len(X), n_splits=n_splits, embargo=embargo):
        if len(train_idx) < 10 or len(test_idx) < 5:
            continue

        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = lgb.LGBMRegressor(**params) if task == "regression" else lgb.LGBMClassifier(**params)
        model.fit(X_train, y_train)
        score = model.score(X_test, y_test)
        scores.append(score)

    # Train final model on all data
    final_model = lgb.LGBMRegressor(**params) if task == "regression" else lgb.LGBMClassifier(**params)
    final_model.fit(X, y)

    return {
        "model": final_model,
        "validation_score": float(np.mean(scores)) if scores else None,
        "feature_importance": dict(zip(
            [f"f{i}" for i in range(X.shape[1])],
            final_model.feature_importances_.tolist(),
        )),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_optimizer.py -v`
Expected: All PASS (if lightgbm installed; if not, test_train_model_basic will skip)

**Important:** If `lightgbm` is not installed:
```bash
pip install lightgbm
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/optimizer/ backend/tests/test_optimizer.py
git commit -m "feat(optimizer): add LightGBM training infrastructure with walk-forward validation"
```

---

### Task 15: M10a Schedule Optimizer Stub

**Files:**
- Create: `backend/src/ml/optimizer/schedule.py`
- Modify: `backend/tests/test_optimizer.py`

Stub that checks activation threshold and trains when ready.

- [ ] **Step 1: Write test**

Append to `backend/tests/test_optimizer.py`:

```python
def test_schedule_optimizer_not_ready(db_session):
    """Should return None when insufficient data."""
    from src.ml.optimizer.schedule import ScheduleOptimizer
    opt = ScheduleOptimizer()
    result = opt.check_and_train(db_session)
    assert result is None


def test_schedule_optimizer_threshold():
    from src.ml.optimizer.schedule import ScheduleOptimizer
    opt = ScheduleOptimizer()
    assert opt.activation_threshold == 50
```

- [ ] **Step 2: Implement schedule optimizer**

```python
# backend/src/ml/optimizer/schedule.py
"""M10a: Schedule Optimizer — predicts optimal extraction intervals per tier.

Activates at 50+ runs per tier in extraction_features table.
Until then, returns None and the rule-based analytics engine handles scheduling.
"""
import logging

logger = logging.getLogger(__name__)


class ScheduleOptimizer:
    activation_threshold = 50

    def check_and_train(self, session) -> dict | None:
        """Check if enough data exists, train if so.

        Returns recommendations dict or None if not ready.
        """
        from sqlalchemy import text

        # Check data volume per tier
        rows = session.execute(text("""
            SELECT trigger, COUNT(*) as cnt
            FROM extraction_features
            GROUP BY trigger
        """)).fetchall()

        tier_counts = {trigger: cnt for trigger, cnt in rows}
        ready_tiers = [t for t, c in tier_counts.items() if c >= self.activation_threshold]

        if not ready_tiers:
            logger.debug(f"Schedule optimizer: not enough data. Counts: {tier_counts}")
            return None

        logger.info(f"Schedule optimizer: ready for tiers {ready_tiers}")
        # TODO: Phase 2 — build feature matrix from extraction_features,
        # train LightGBM regressor targeting value_bets_found + avg_edge_pct
        return {"ready_tiers": ready_tiers, "status": "threshold_met"}
```

- [ ] **Step 3: Run tests, commit**

```bash
cd backend && python -m pytest tests/test_optimizer.py -v
git add backend/src/ml/optimizer/schedule.py backend/tests/test_optimizer.py
git commit -m "feat(optimizer): add M10a schedule optimizer stub with activation threshold"
```

---

### Task 16: M10b-d Optimizer Stubs

**Files:**
- Create: `backend/src/ml/optimizer/provider_priority.py`
- Create: `backend/src/ml/optimizer/timeout.py`
- Create: `backend/src/ml/optimizer/coverage.py`
- Modify: `backend/tests/test_optimizer.py`

Stub implementations for the remaining 3 sub-models. Same pattern as M10a.

- [ ] **Step 1: Write tests**

Append to `backend/tests/test_optimizer.py`:

```python
def test_provider_priority_not_ready(db_session):
    from src.ml.optimizer.provider_priority import ProviderPriorityScorer
    opt = ProviderPriorityScorer()
    assert opt.check_and_train(db_session) is None
    assert opt.activation_threshold == 100


def test_timeout_tuner_not_ready(db_session):
    from src.ml.optimizer.timeout import TimeoutTuner
    opt = TimeoutTuner()
    assert opt.check_and_train(db_session) is None
    assert opt.activation_threshold == 50


def test_coverage_optimizer_not_ready(db_session):
    from src.ml.optimizer.coverage import CoverageOptimizer
    opt = CoverageOptimizer()
    assert opt.check_and_train(db_session) is None
    assert opt.activation_threshold == 20
```

- [ ] **Step 2: Implement stubs**

```python
# backend/src/ml/optimizer/provider_priority.py
"""M10b: Provider Priority Scorer — ranks providers by value per extraction second.

Activates at 100+ provider_value_log rows per provider (~3 months).
Until then, rule-based diagnostics handle provider scoring.
"""
import logging
logger = logging.getLogger(__name__)


class ProviderPriorityScorer:
    activation_threshold = 100

    def check_and_train(self, session) -> dict | None:
        from sqlalchemy import text
        rows = session.execute(text("""
            SELECT provider_id, COUNT(*) as cnt
            FROM provider_value_log
            GROUP BY provider_id
        """)).fetchall()
        ready = [pid for pid, cnt in rows if cnt >= self.activation_threshold]
        if not ready:
            return None
        return {"ready_providers": ready, "status": "threshold_met"}
```

```python
# backend/src/ml/optimizer/timeout.py
"""M10c: Timeout Tuner — recommends per-provider extraction timeouts.

Activates at 50+ runs per provider in provider_run_metrics.
"""
import logging
logger = logging.getLogger(__name__)


class TimeoutTuner:
    activation_threshold = 50

    def check_and_train(self, session) -> dict | None:
        from sqlalchemy import text
        rows = session.execute(text("""
            SELECT provider_id, COUNT(*) as cnt
            FROM provider_run_metrics
            GROUP BY provider_id
        """)).fetchall()
        ready = [pid for pid, cnt in rows if cnt >= self.activation_threshold]
        if not ready:
            return None
        return {"ready_providers": ready, "status": "threshold_met"}
```

```python
# backend/src/ml/optimizer/coverage.py
"""M10d: Coverage Optimizer — identifies and prioritizes Pinnacle coverage gaps.

Activates at 20+ pinnacle_coverage_log rows per provider.
"""
import logging
logger = logging.getLogger(__name__)


class CoverageOptimizer:
    activation_threshold = 20

    def check_and_train(self, session) -> dict | None:
        from sqlalchemy import text
        rows = session.execute(text("""
            SELECT provider_id, COUNT(*) as cnt
            FROM pinnacle_coverage_log
            GROUP BY provider_id
        """)).fetchall()
        ready = [pid for pid, cnt in rows if cnt >= self.activation_threshold]
        if not ready:
            return None
        return {"ready_providers": ready, "status": "threshold_met"}
```

- [ ] **Step 3: Run tests, commit**

```bash
cd backend && python -m pytest tests/test_optimizer.py -v
git add backend/src/ml/optimizer/ backend/tests/test_optimizer.py
git commit -m "feat(optimizer): add M10b-d optimizer stubs with activation thresholds"
```

---

### Task 17: Final Full Test Suite

**Files:** None new.

- [ ] **Step 1: Run complete test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Verify extraction report works with analytics**

This is a manual check — run an extraction and confirm the report includes the new sections:

```bash
cd backend && python -m src.app extract pinnacle
```

Look for "PROVIDER ROI" and "RECOMMENDATIONS" sections in the output.

---

## Summary

**What this plan delivers:**

**Layer 1 (Immediate Value):**
- Provider Value Attribution — per-provider ROI from opportunities + bets, grouped by canonical platform
- Coverage Gap Analysis — per-provider per-sport gaps vs Pinnacle from sport_run_metrics
- Scheduling Efficiency — per-tier duration/events/efficiency from extraction_runs
- Diagnostic Rules Engine — diagnose match rate drops, missing markets, slow extraction
- Recommendation Manager — lifecycle tracking (open -> acted_on -> resolved)
- CLI Report Extension — Provider ROI table + Recommendations in extraction report
- API Endpoints — GET /analytics, GET /recommendations, PATCH /recommendations/{id}
- StatsPage — Extraction analytics section with provider ROI and recommendations

**Layer 2 (Activates When Data Ready):**
- LightGBM training infrastructure with walk-forward validation
- M10a Schedule Optimizer stub (50+ runs/tier)
- M10b Provider Priority Scorer stub (100+ rows/provider)
- M10c Timeout Tuner stub (50+ runs/provider)
- M10d Coverage Optimizer stub (20+ rows/provider)

**1 new table:** provider_recommendations
**17 tasks, ~60 tests**
