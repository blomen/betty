# Extraction Health Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shallow `/health/extraction` endpoint with a deep health assessment that detects sharp source outages, consecutive failures, provider staleness, DB integrity errors, and opportunity volume drops.

**Architecture:** Single function replacement in the existing endpoint. Queries `provider_run_metrics` and `opportunities` tables for the last hour. Reads tier intervals from `providers.yaml`. No new files — all logic in `backend/src/api/__init__.py`.

**Tech Stack:** Python / FastAPI / SQLAlchemy / PostgreSQL

---

### Task 1: Extract provider interval config loader

**Files:**
- Create: `backend/src/pipeline/health.py`
- Test: `backend/tests/test_health_detector.py`

The health assessment needs to know each provider's expected interval. Extract a small helper that loads the tier config and builds a `provider_id → interval_minutes` mapping. This is a pure function with no DB dependency.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_health_detector.py`:

```python
"""Tests for extraction health detector."""

from unittest.mock import patch, mock_open
import yaml

from src.pipeline.health import get_provider_intervals


SAMPLE_YAML = yaml.dump({
    "extraction_scheduling": {
        "sharp": {"providers": ["pinnacle"], "interval_minutes": 1},
        "api_soft": {"providers": ["unibet", "betinia"], "interval_minutes": 2},
        "browser_soft": {"providers": ["888sport"], "interval_minutes": 10},
    },
    "active": ["pinnacle", "unibet", "betinia", "888sport", "cloudbet"],
})


def test_get_provider_intervals_maps_active_providers():
    with patch("src.pipeline.health.get_config_path", return_value="fake.yaml"):
        with patch("builtins.open", mock_open(read_data=SAMPLE_YAML)):
            result = get_provider_intervals()

    assert result["pinnacle"] == 1
    assert result["unibet"] == 2
    assert result["betinia"] == 2
    assert result["888sport"] == 10
    # cloudbet is active but not in any tier — should not appear
    assert "cloudbet" not in result


def test_get_provider_intervals_excludes_inactive():
    cfg = yaml.dump({
        "extraction_scheduling": {
            "sharp": {"providers": ["pinnacle"], "interval_minutes": 1},
            "api_soft": {"providers": ["unibet", "betinia"], "interval_minutes": 2},
        },
        "active": ["pinnacle"],  # only pinnacle active
    })
    with patch("src.pipeline.health.get_config_path", return_value="fake.yaml"):
        with patch("builtins.open", mock_open(read_data=cfg)):
            result = get_provider_intervals()

    assert result == {"pinnacle": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_health_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.pipeline.health'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/pipeline/health.py`:

```python
"""Extraction health assessment.

Deep health checks for the /health/extraction endpoint.
Detects: sharp source outage, consecutive failures, provider staleness,
DB integrity errors, opportunity volume drops.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import yaml

from ..paths import get_config_path


def get_provider_intervals() -> dict[str, int]:
    """Load provider → interval_minutes mapping from providers.yaml.

    Only includes providers that are both in a scheduling tier AND in the active list.
    """
    config_path = get_config_path("providers.yaml")
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    active = set(config.get("active", []))
    tiers = config.get("extraction_scheduling", {})

    intervals: dict[str, int] = {}
    for tier_cfg in tiers.values():
        interval = tier_cfg.get("interval_minutes", 10)
        for provider in tier_cfg.get("providers", []):
            if provider in active:
                intervals[provider] = interval

    return intervals
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_health_detector.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/health.py backend/tests/test_health_detector.py
git commit -m "feat(health): add provider interval config loader"
```

---

### Task 2: Implement the 5 health checks

**Files:**
- Modify: `backend/src/pipeline/health.py`
- Test: `backend/tests/test_health_detector.py`

Add the `assess_extraction_health(db)` function that runs all 5 checks and returns `(status, issues)`. This takes a SQLAlchemy session and queries `provider_run_metrics` and `opportunities`.

- [ ] **Step 1: Write failing tests for all 5 checks**

Append to `backend/tests/test_health_detector.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.pipeline.health import assess_extraction_health


def _utcnow():
    return datetime.now(timezone.utc)


def _make_metric(provider_id, status="success", start_time=None, error_message=None):
    """Create a mock ProviderRunMetrics row."""
    m = MagicMock()
    m.provider_id = provider_id
    m.status = status
    m.start_time = start_time or _utcnow()
    m.error_message = error_message
    return m


def _mock_db(metrics_rows, opp_current=100, opp_previous=100):
    """Create a mock DB session that returns given metrics and opportunity counts."""
    db = MagicMock()

    def fake_execute(stmt):
        result = MagicMock()
        sql_str = str(stmt)
        if "provider_run_metrics" in sql_str:
            result.fetchall.return_value = metrics_rows
        elif "opp_current" in sql_str:
            result.fetchone.return_value = (opp_current, opp_previous)
        return result

    db.execute = fake_execute
    return db


# --- Check 1: Sharp source down ---

def test_sharp_source_down_detected():
    intervals = {"pinnacle": 1, "unibet": 2}
    # Pinnacle last succeeded 20 minutes ago
    metrics = [
        _make_metric("pinnacle", "success", _utcnow() - timedelta(minutes=20)),
        _make_metric("unibet", "success", _utcnow() - timedelta(minutes=1)),
    ]
    db = _mock_db(metrics)
    status, issues = assess_extraction_health(db, intervals)
    assert status == "critical"
    assert any("pinnacle" in i.lower() for i in issues)


def test_sharp_source_healthy():
    intervals = {"pinnacle": 1, "unibet": 2}
    metrics = [
        _make_metric("pinnacle", "success", _utcnow() - timedelta(minutes=1)),
        _make_metric("unibet", "success", _utcnow() - timedelta(minutes=1)),
    ]
    db = _mock_db(metrics)
    status, issues = assess_extraction_health(db, intervals)
    assert status == "ok"


# --- Check 2: Consecutive failures ---

def test_consecutive_failures_detected():
    intervals = {"polymarket": 5}
    now = _utcnow()
    metrics = [
        _make_metric("polymarket", "failed", now - timedelta(minutes=i), "UniqueViolation")
        for i in range(5)
    ]
    db = _mock_db(metrics)
    status, issues = assess_extraction_health(db, intervals)
    assert status == "critical"
    assert any("consecutive" in i.lower() and "polymarket" in i.lower() for i in issues)


# --- Check 3: Provider staleness ---

def test_provider_staleness_detected():
    intervals = {"pinnacle": 1, "unibet": 2}
    metrics = [
        _make_metric("pinnacle", "success", _utcnow() - timedelta(minutes=1)),
        # unibet last succeeded 30 min ago — threshold is 3*2=6 min
        _make_metric("unibet", "success", _utcnow() - timedelta(minutes=30)),
    ]
    db = _mock_db(metrics)
    status, issues = assess_extraction_health(db, intervals)
    assert any("unibet" in i.lower() and "stale" in i.lower() for i in issues)


# --- Check 4: DB integrity errors ---

def test_db_integrity_errors_detected():
    intervals = {"unibet": 2}
    metrics = [
        _make_metric("unibet", "failed", _utcnow() - timedelta(minutes=2),
                      "UniqueViolation: duplicate key value violates unique constraint"),
    ]
    db = _mock_db(metrics)
    status, issues = assess_extraction_health(db, intervals)
    assert status == "critical"
    assert any("integrity" in i.lower() for i in issues)


# --- Check 5: Opportunity volume drop ---

def test_opportunity_volume_drop_detected():
    intervals = {"pinnacle": 1}
    metrics = [_make_metric("pinnacle", "success", _utcnow())]
    db = _mock_db(metrics, opp_current=40, opp_previous=100)
    status, issues = assess_extraction_health(db, intervals)
    assert any("volume" in i.lower() or "dropped" in i.lower() for i in issues)


def test_opportunity_volume_stable():
    intervals = {"pinnacle": 1}
    metrics = [_make_metric("pinnacle", "success", _utcnow())]
    db = _mock_db(metrics, opp_current=95, opp_previous=100)
    status, issues = assess_extraction_health(db, intervals)
    assert not any("volume" in i.lower() or "dropped" in i.lower() for i in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_health_detector.py -v`
Expected: FAIL — `ImportError: cannot import name 'assess_extraction_health'`

- [ ] **Step 3: Implement assess_extraction_health**

Add to `backend/src/pipeline/health.py`:

```python
from sqlalchemy import text


# Thresholds
SHARP_STALE_MINUTES = 10
CONSECUTIVE_FAILURE_CRITICAL = 3
CONSECUTIVE_FAILURE_WARNING = 2
STALENESS_MULTIPLIER = 3
VOLUME_DROP_THRESHOLD = 0.50  # 50% drop
VOLUME_MIN_BASELINE = 50

INTEGRITY_PATTERNS = re.compile(
    r"UniqueViolation|IntegrityError|duplicate key|sequence", re.IGNORECASE
)


def assess_extraction_health(
    db, intervals: dict[str, int]
) -> tuple[str, list[str]]:
    """Run 5 deep health checks on extraction state.

    Args:
        db: SQLAlchemy session
        intervals: provider_id → expected interval_minutes (from get_provider_intervals)

    Returns:
        (status, issues) where status is "ok", "warning", or "critical"
        and issues is a list of human-readable strings.
    """
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    issues: list[str] = []

    # ── Fetch recent provider run metrics (last hour) ──
    rows = db.execute(
        text("""
            SELECT provider_id, status, start_time, error_message
            FROM provider_run_metrics
            WHERE start_time > :since
            ORDER BY start_time DESC
        """),
        {"since": one_hour_ago},
    ).fetchall()

    # Group by provider: list of (status, start_time, error_message) ordered newest-first
    from collections import defaultdict
    by_provider: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_provider[r[0]].append((r[1], r[2], r[3]))

    # ── Check 1: Sharp source down ──
    pinnacle_runs = by_provider.get("pinnacle", [])
    last_pinnacle_success = None
    for status, start_time, _ in pinnacle_runs:
        if status == "success":
            last_pinnacle_success = start_time
            break

    if "pinnacle" in intervals:
        if last_pinnacle_success is None:
            age = "60+"
            issues.append(f"CRITICAL: pinnacle has not completed successfully in {age} minutes")
        else:
            age_min = (now - last_pinnacle_success.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age_min > SHARP_STALE_MINUTES:
                issues.append(
                    f"CRITICAL: pinnacle has not completed successfully in {int(age_min)} minutes"
                )

    # ── Check 2: Consecutive provider failures ──
    for provider_id, runs in by_provider.items():
        consecutive = 0
        last_error = ""
        for status, _, error_msg in runs:
            if status != "success":
                consecutive += 1
                if not last_error and error_msg:
                    last_error = error_msg[:100]
            else:
                break
        if consecutive >= CONSECUTIVE_FAILURE_CRITICAL:
            issues.append(
                f"CRITICAL: {provider_id} has failed {consecutive} consecutive runs"
                + (f" — {last_error}" if last_error else "")
            )
        elif consecutive >= CONSECUTIVE_FAILURE_WARNING:
            issues.append(
                f"WARNING: {provider_id} has failed {consecutive} consecutive runs"
                + (f" — {last_error}" if last_error else "")
            )

    # ── Check 3: Provider staleness ──
    for provider_id, expected_interval in intervals.items():
        if provider_id == "pinnacle":
            continue  # covered by check 1
        threshold_min = expected_interval * STALENESS_MULTIPLIER
        runs = by_provider.get(provider_id, [])
        last_success = None
        for status, start_time, _ in runs:
            if status == "success":
                last_success = start_time
                break
        if last_success is None:
            # No successful run in the last hour
            issues.append(
                f"WARNING: {provider_id} has no successful run in the last hour "
                f"(threshold: {threshold_min} min)"
            )
        else:
            age_min = (now - last_success.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age_min > threshold_min:
                issues.append(
                    f"WARNING: {provider_id} is stale — last succeeded {int(age_min)} min ago "
                    f"(threshold: {threshold_min} min)"
                )

    # ── Check 4: Database integrity errors ──
    integrity_providers = set()
    for provider_id, runs in by_provider.items():
        for status, _, error_msg in runs:
            if error_msg and INTEGRITY_PATTERNS.search(error_msg):
                integrity_providers.add(provider_id)
                break
    if integrity_providers:
        issues.append(
            f"CRITICAL: database integrity errors detected in: "
            f"{', '.join(sorted(integrity_providers))}"
        )

    # ── Check 5: Opportunity volume drop ──
    result = db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE detected_at > :one_h_ago) AS opp_current,
                COUNT(*) FILTER (WHERE detected_at > :two_h_ago AND detected_at <= :one_h_ago) AS opp_previous
            FROM opportunities
            WHERE detected_at > :two_h_ago
        """),
        {"one_h_ago": one_hour_ago, "two_h_ago": now - timedelta(hours=2)},
    ).fetchone()
    opp_current, opp_previous = result[0], result[1]
    if opp_previous >= VOLUME_MIN_BASELINE:
        if opp_current < opp_previous * (1 - VOLUME_DROP_THRESHOLD):
            drop_pct = int((1 - opp_current / opp_previous) * 100)
            issues.append(
                f"WARNING: opportunity volume dropped {drop_pct}% "
                f"({opp_previous} → {opp_current}) in last hour"
            )

    # ── Determine overall status ──
    status = "ok"
    for issue in issues:
        if issue.startswith("CRITICAL:"):
            status = "critical"
            break
        if issue.startswith("WARNING:"):
            status = "warning"

    return status, issues
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_health_detector.py -v`
Expected: all 8 tests PASS.

Note: The mock-based tests use a simplified mock DB. The `assess_extraction_health` function uses raw SQL via `db.execute(text(...))`, so the mocks need to route based on the SQL text content. If the mocks don't match cleanly, adjust `_mock_db` to inspect the compiled SQL string. The key behavior under test is the logic, not the SQL syntax.

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/health.py backend/tests/test_health_detector.py
git commit -m "feat(health): implement 5 deep extraction health checks"
```

---

### Task 3: Wire into /health/extraction endpoint

**Files:**
- Modify: `backend/src/api/__init__.py:1200-1303`

Replace the existing shallow `health_extraction()` with the deep assessment. Keep the same response shape (`status`, `issues`, `runs`, `checked_at`) for backward compatibility.

- [ ] **Step 1: Replace the endpoint implementation**

In `backend/src/api/__init__.py`, replace the entire `health_extraction` function (lines 1200-1303) with:

```python
@app.get("/health/extraction")
async def health_extraction():
    """Public extraction health endpoint — no auth required.

    Deep health assessment: checks sharp source freshness, consecutive
    provider failures, staleness vs expected intervals, DB integrity
    errors, and opportunity volume drops.
    """
    from ..db.models import ExtractionRun, ProviderRunMetrics
    from ..pipeline.health import assess_extraction_health, get_provider_intervals
    from .deps import get_db

    def _query():
        db = None
        try:
            db = next(get_db())

            # ── Deep health assessment ──
            intervals = get_provider_intervals()
            health_status, issues = assess_extraction_health(db, intervals)

            # ── Existing: last 3 runs for the response body ──
            runs = db.query(ExtractionRun).order_by(ExtractionRun.start_time.desc()).limit(3).all()
            run_data = []
            for run in runs:
                providers = db.query(ProviderRunMetrics).filter(ProviderRunMetrics.run_id == run.id).all()
                failed = [
                    {"provider": p.provider_id, "error": (p.error_message or "")[:200], "status": p.status}
                    for p in providers
                    if p.status in ("failed", "timeout")
                ]
                low_match = [
                    {
                        "provider": p.provider_id,
                        "matched": p.events_matched or 0,
                        "unmatched": p.events_unmatched or 0,
                        "match_rate": round(
                            (p.events_matched or 0)
                            / max((p.events_matched or 0) + (p.events_unmatched or 0), 1)
                            * 100
                        ),
                    }
                    for p in providers
                    if (p.events_matched or 0) + (p.events_unmatched or 0) > 0
                    and (p.events_matched or 0) / max((p.events_matched or 0) + (p.events_unmatched or 0), 1)
                    < 0.3
                ]
                run_data.append(
                    {
                        "id": run.id,
                        "start_time": run.start_time.isoformat() if run.start_time else None,
                        "duration_seconds": run.duration_seconds,
                        "trigger": run.trigger,
                        "providers_attempted": run.providers_attempted,
                        "providers_succeeded": run.providers_succeeded,
                        "providers_failed": run.providers_failed,
                        "total_events": run.total_events,
                        "total_odds": run.total_odds,
                        "failed_providers": failed,
                        "low_match_rate": low_match,
                    }
                )

            return {"status": health_status, "issues": issues, "runs": run_data}
        except Exception as e:
            return {"error": str(e)}
        finally:
            if db:
                db.close()

    try:
        data = await asyncio.wait_for(asyncio.to_thread(_query), timeout=10.0)
    except asyncio.TimeoutError:
        return {"status": "error", "message": "Database query timed out"}

    if isinstance(data, dict) and "error" in data:
        return {"status": "error", "message": data["error"]}

    data["checked_at"] = datetime.now(timezone.utc).isoformat()
    return data
```

- [ ] **Step 2: Verify locally that the module imports cleanly**

Run: `cd backend && python -c "from src.pipeline.health import assess_extraction_health, get_provider_intervals; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run existing tests to check no regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/__init__.py
git commit -m "feat(health): wire deep health assessment into /health/extraction"
```

---

### Task 4: Deploy and verify on production

**Files:** None (deployment only)

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

- [ ] **Step 2: Deploy**

```bash
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"
```

- [ ] **Step 3: Verify the endpoint returns deep health data**

```bash
ssh root@148.251.40.251 "curl -s http://localhost:8000/health/extraction | python3 -m json.tool"
```

Expected: response includes `status`, `issues` (with specific check results), `runs`, `checked_at`. If all providers are healthy, `status` should be `"ok"` with empty `issues`. If any provider is stale or failing, it should report the specific issue.

- [ ] **Step 4: Verify a known-good state**

Wait for 2-3 extraction cycles (~5 min), then re-check. All providers should be succeeding after the earlier sequence fix. Confirm `status: "ok"`.

---

### Task 5: Tighten scheduled monitoring agent

**Files:** Scheduled agent / trigger configuration

- [ ] **Step 1: List existing scheduled triggers**

```bash
# Check existing extraction monitoring triggers
```

Use the CronList tool or equivalent to find the existing extraction monitoring trigger.

- [ ] **Step 2: Update interval from 3h to 30min**

Update or recreate the scheduled extraction monitoring trigger with a 30-minute interval. The trigger should:
- Hit `https://148.251.40.251/health/extraction`
- If `status` is `"critical"` or `"warning"`, create a GitHub issue or commit an alert

- [ ] **Step 3: Verify the trigger is active**

List triggers again to confirm the new interval.

- [ ] **Step 4: Commit any config changes**

If there are file-based trigger configs, commit them.
