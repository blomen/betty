# M10 Extraction Pipeline Optimizer Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the 4 M10 sub-models (Schedule Optimizer, Provider Priority Scorer, Timeout Tuner, Coverage Optimizer) that use accumulated extraction data to optimize the pipeline.

**Architecture:** Each sub-model reads from existing tables (`extraction_features`, `provider_value_log`, `pinnacle_coverage_log`, `provider_run_metrics`), trains a LightGBM model via the shared `optimizer/trainer.py` infrastructure, and returns actionable recommendations (optimal intervals, provider rankings, timeouts, coverage gaps). All models use `check_and_train()` for threshold checking + training, and `predict()` / `recommend()` for inference. They integrate into the existing `TrainingOrchestrator` and the analytics API.

**Tech Stack:** Python 3.10+, LightGBM, SQLAlchemy, NumPy, existing `optimizer/trainer.py` walk-forward validation

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/src/ml/optimizer/schedule.py` | M10a: Predict value bet yield per run, recommend tier intervals |
| `backend/src/ml/optimizer/provider_priority.py` | M10b: Rank providers by value-per-second, recommend extraction order |
| `backend/src/ml/optimizer/timeout.py` | M10c: Compute optimal per-provider timeouts from duration distributions |
| `backend/src/ml/optimizer/coverage.py` | M10d: Identify coverage gaps, suggest sports.yaml alias fixes |
| `backend/src/ml/training/train_all.py` | Wire M10 models into training orchestrator |
| `backend/src/api/routes/extraction.py` | Add `/extraction/optimizer/status` endpoint |
| `backend/tests/test_m10_optimizer.py` | Tests for all 4 sub-models |

---

## Chunk 1: M10a Schedule Optimizer + M10c Timeout Tuner

### Task 1: M10a Schedule Optimizer

**Files:**
- Modify: `backend/src/ml/optimizer/schedule.py`
- Test: `backend/tests/test_m10_optimizer.py`

- [ ] **Step 1: Write tests for ScheduleOptimizer**

Create `backend/tests/test_m10_optimizer.py`:

```python
"""Tests for M10 extraction pipeline optimizer models."""
import numpy as np
import pytest


class TestScheduleOptimizer:
    def test_check_threshold_insufficient(self):
        """Returns None when fewer than 50 runs per tier."""
        from src.ml.optimizer.schedule import ScheduleOptimizer
        opt = ScheduleOptimizer()
        # With no data, _load_tier_data returns empty
        result = opt._load_tier_data([], "api_soft")
        assert result is None

    def test_build_features_and_target(self):
        """Builds correct feature matrix from extraction feature rows."""
        from src.ml.optimizer.schedule import ScheduleOptimizer, FEATURE_NAMES
        opt = ScheduleOptimizer()
        # Simulate 60 rows of extraction feature dicts
        rows = []
        for i in range(60):
            rows.append({
                "hour_of_day": i % 24,
                "day_of_week": i % 7,
                "tier_encoded": 1,
                "minutes_since_last_sharp": 30.0 + i,
                "minutes_since_last_soft": 60.0 + i,
                "events_starting_next_2h": 10 + i,
                "events_starting_next_6h": 30 + i,
                "providers_attempted": 12,
                "providers_succeeded": 10,
                "providers_failed": 2,
                "circuit_breakers_open": 0,
                "total_events": 200 + i,
                "total_odds": 1000 + i * 5,
                "avg_match_rate": 0.65,
                "value_bets_last_run": max(0, 4 + (i % 8) - 2),
                "value_bets_avg_last_5": 4.5,
                "avg_edge_last_run": 2.5,
                "providers_with_value_last_run": 8,
                "value_bets_found": max(0, 5 + (i % 10) - 3),
            })
        X, y = opt._build_features_and_target(rows)
        assert X.shape == (60, len(FEATURE_NAMES))
        assert y.shape == (60,)
        assert all(v >= 0 for v in y)  # value_bets_found >= 0

    def test_predict_yield(self):
        """Predict returns a non-negative yield estimate."""
        from src.ml.optimizer.schedule import ScheduleOptimizer, FEATURE_NAMES
        opt = ScheduleOptimizer()
        # Train on synthetic data
        np.random.seed(42)
        n = 60
        X = np.random.rand(n, len(FEATURE_NAMES))
        y = np.random.poisson(5, n).astype(float)
        from src.ml.optimizer.trainer import train_model
        result = train_model(X, y, task="regression", min_samples=30)
        assert result is not None
        opt._model = result["model"]
        # Predict
        features = {name: 0.5 for name in FEATURE_NAMES}
        pred = opt.predict_yield(features)
        assert isinstance(pred, float)
        assert pred >= 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_m10_optimizer.py::TestScheduleOptimizer -v`
Expected: FAIL — `_load_tier_data`, `_build_features_and_target`, `predict_yield` not defined

- [ ] **Step 3: Implement ScheduleOptimizer**

Replace `backend/src/ml/optimizer/schedule.py` with:

```python
"""M10a: Schedule Optimizer — predicts value bet yield per extraction run.

Activates at 50+ runs per tier in extraction_features table.
Uses LightGBM regression to predict how many value bets a run will produce
given current timing/health context. Recommends skipping low-yield runs.
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)

# Tier encoding: sharp=0, api_soft=1, browser_soft=2
TIER_ENCODING = {"sharp": 0, "api_soft": 1, "browser_soft": 2}

FEATURE_NAMES = [
    "hour_of_day", "day_of_week", "tier_encoded",
    "minutes_since_last_sharp", "minutes_since_last_soft",
    "events_starting_next_2h", "events_starting_next_6h",
    "providers_attempted", "providers_succeeded", "providers_failed",
    "circuit_breakers_open",
    "total_events", "total_odds", "avg_match_rate",
    # Historical yield context (computed from previous rows)
    "value_bets_last_run", "value_bets_avg_last_5",
    "avg_edge_last_run", "providers_with_value_last_run",
]


class ScheduleOptimizer:
    activation_threshold = 50

    def __init__(self):
        self._model = None

    def check_and_train(self, session) -> dict | None:
        """Check data threshold and train if sufficient."""
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT trigger, COUNT(*) as cnt FROM extraction_features "
            "WHERE value_bets_found IS NOT NULL GROUP BY trigger"
        )).fetchall()
        tier_counts = {trigger: cnt for trigger, cnt in rows}
        ready_tiers = [t for t, c in tier_counts.items() if c >= self.activation_threshold]
        if not ready_tiers:
            logger.debug(f"Schedule optimizer: not enough data. Counts: {tier_counts}")
            return None

        # Load all resolved runs ordered by time
        all_rows = session.execute(text(
            "SELECT id, trigger, hour_of_day, day_of_week, "
            "minutes_since_last_sharp, minutes_since_last_soft, "
            "events_starting_next_2h, events_starting_next_6h, "
            "providers_attempted, providers_succeeded, providers_failed, "
            "circuit_breakers_open, total_events, total_odds, avg_match_rate, "
            "value_bets_found, avg_edge_pct "
            "FROM extraction_features WHERE value_bets_found IS NOT NULL "
            "ORDER BY id ASC"
        )).fetchall()

        if len(all_rows) < self.activation_threshold:
            return None

        # Enrich with historical yield features (computed from prior rows)
        feature_dicts = self._enrich_with_history(all_rows)
        X, y = self._build_features_and_target(feature_dicts)

        from src.ml.optimizer.trainer import train_model
        result = train_model(X, y, task="regression", min_samples=self.activation_threshold)
        if result is None:
            return None

        self._model = result["model"]
        logger.info(f"Schedule optimizer trained on {len(X)} runs, score={result['validation_score']}")
        return {
            "status": "trained",
            "ready_tiers": ready_tiers,
            "training_samples": len(X),
            "validation_score": result["validation_score"],
            "model": result["model"],
        }

    def _enrich_with_history(self, rows: list) -> list[dict]:
        """Add rolling historical yield features to each row."""
        enriched = []
        past_yields = []
        for row in rows:
            # row: id, trigger, hour, dow, min_sharp, min_soft, ev_2h, ev_6h,
            #       prov_att, prov_succ, prov_fail, cb_open, tot_ev, tot_odds,
            #       avg_mr, vb_found, avg_edge
            vb_found = row[15] or 0
            avg_edge = row[16] or 0

            hist = {
                "value_bets_last_run": past_yields[-1] if past_yields else 0,
                "value_bets_avg_last_5": (
                    sum(past_yields[-5:]) / len(past_yields[-5:]) if past_yields else 0
                ),
                "avg_edge_last_run": avg_edge,
                "providers_with_value_last_run": row[9] or 0,  # providers_succeeded as proxy
            }

            enriched.append({
                "hour_of_day": row[2] or 0,
                "day_of_week": row[3] or 0,
                "tier_encoded": TIER_ENCODING.get(row[1], 1),
                "minutes_since_last_sharp": row[4] or 0,
                "minutes_since_last_soft": row[5] or 0,
                "events_starting_next_2h": row[6] or 0,
                "events_starting_next_6h": row[7] or 0,
                "providers_attempted": row[8] or 0,
                "providers_succeeded": row[9] or 0,
                "providers_failed": row[10] or 0,
                "circuit_breakers_open": row[11] or 0,
                "total_events": row[12] or 0,
                "total_odds": row[13] or 0,
                "avg_match_rate": row[14] or 0,
                **hist,
                "value_bets_found": vb_found,
            })
            past_yields.append(vb_found)

        return enriched

    def _load_tier_data(self, rows: list, tier: str) -> list | None:
        """Filter rows for a specific tier. Returns None if insufficient."""
        filtered = [r for r in rows if r.get("trigger") == tier]
        return filtered if len(filtered) >= self.activation_threshold else None

    def _build_features_and_target(self, rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
        """Convert row dicts to feature matrix X and target vector y."""
        X = np.array([[row.get(f, 0) or 0 for f in FEATURE_NAMES] for row in rows], dtype=float)
        y = np.array([row.get("value_bets_found", 0) or 0 for row in rows], dtype=float)
        return X, y

    def predict_yield(self, features: dict) -> float:
        """Predict number of value bets for given extraction context."""
        if self._model is None:
            return -1.0
        X = np.array([[features.get(f, 0) or 0 for f in FEATURE_NAMES]], dtype=float)
        pred = float(self._model.predict(X)[0])
        return max(0.0, pred)

    def should_skip_run(self, features: dict, min_yield: float = 2.0) -> bool:
        """Returns True if predicted yield is below threshold."""
        if self._model is None:
            return False  # No model = always run
        return self.predict_yield(features) < min_yield
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_m10_optimizer.py::TestScheduleOptimizer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/optimizer/schedule.py backend/tests/test_m10_optimizer.py
git commit -m "feat(ml): implement M10a Schedule Optimizer with yield prediction"
```

---

### Task 2: M10c Timeout Tuner

**Files:**
- Modify: `backend/src/ml/optimizer/timeout.py`
- Test: `backend/tests/test_m10_optimizer.py`

- [ ] **Step 1: Write tests for TimeoutTuner**

Append to `backend/tests/test_m10_optimizer.py`:

```python
class TestTimeoutTuner:
    def test_check_threshold_insufficient(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        result = tuner._compute_percentiles([])
        assert result == {}

    def test_compute_percentiles(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        durations = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        pcts = tuner._compute_percentiles(durations)
        assert "p50" in pcts
        assert "p90" in pcts
        assert "p95" in pcts
        assert pcts["p50"] == pytest.approx(55.0, abs=5)
        assert pcts["p95"] > pcts["p90"] > pcts["p50"]

    def test_recommend_timeout(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        durations = list(range(10, 200, 3))  # 10, 13, 16, ..., 199
        timeout = tuner.recommend_timeout(durations, buffer_pct=0.2)
        assert isinstance(timeout, float)
        assert timeout > 0
        # Should be p95 + 20% buffer
        p95 = float(np.percentile(durations, 95))
        assert timeout == pytest.approx(p95 * 1.2, rel=0.01)

    def test_recommend_all_providers(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        provider_durations = {
            "unibet": [50.0, 55.0, 60.0, 65.0, 70.0] * 12,
            "betsson": [30.0, 35.0, 40.0, 45.0, 50.0] * 12,
        }
        recs = tuner.recommend_all(provider_durations)
        assert "unibet" in recs
        assert "betsson" in recs
        assert recs["unibet"]["timeout"] > recs["betsson"]["timeout"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_m10_optimizer.py::TestTimeoutTuner -v`
Expected: FAIL

- [ ] **Step 3: Implement TimeoutTuner**

Replace `backend/src/ml/optimizer/timeout.py` with:

```python
"""M10c: Timeout Tuner — recommends per-provider extraction timeouts.

Activates at 50+ runs per provider in provider_run_metrics.
Uses duration percentiles (P95 + buffer) rather than ML — statistical
approach is more interpretable and robust for this use case.
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)


class TimeoutTuner:
    activation_threshold = 50

    def check_and_train(self, session) -> dict | None:
        """Check data threshold and compute timeout recommendations."""
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT provider_id, COUNT(*) as cnt FROM provider_run_metrics "
            "WHERE duration_seconds IS NOT NULL AND status = 'success' "
            "GROUP BY provider_id"
        )).fetchall()
        ready = {pid: cnt for pid, cnt in rows if cnt >= self.activation_threshold}
        if not ready:
            return None

        # Load durations for ready providers
        provider_durations = {}
        for pid in ready:
            dur_rows = session.execute(text(
                "SELECT duration_seconds FROM provider_run_metrics "
                "WHERE provider_id = :pid AND duration_seconds IS NOT NULL "
                "AND status = 'success' ORDER BY start_time DESC LIMIT 200"
            ), {"pid": pid}).fetchall()
            provider_durations[pid] = [r[0] for r in dur_rows]

        recs = self.recommend_all(provider_durations)
        logger.info(f"Timeout tuner computed recommendations for {len(recs)} providers")
        return {"status": "computed", "recommendations": recs}

    def _compute_percentiles(self, durations: list[float]) -> dict:
        """Compute p50, p90, p95, p99 from duration list."""
        if not durations:
            return {}
        arr = np.array(durations)
        return {
            "p50": float(np.percentile(arr, 50)),
            "p90": float(np.percentile(arr, 90)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "count": len(durations),
        }

    def recommend_timeout(self, durations: list[float], buffer_pct: float = 0.2) -> float:
        """Recommend timeout as P95 + buffer."""
        if not durations:
            return 120.0  # default fallback
        p95 = float(np.percentile(durations, 95))
        return round(p95 * (1.0 + buffer_pct), 1)

    def recommend_all(self, provider_durations: dict[str, list[float]]) -> dict:
        """Compute timeout recommendations for all providers."""
        recs = {}
        for pid, durations in provider_durations.items():
            if len(durations) < self.activation_threshold:
                continue
            pcts = self._compute_percentiles(durations)
            timeout = self.recommend_timeout(durations)
            recs[pid] = {
                "timeout": timeout,
                "percentiles": pcts,
                "events_lost_estimate": self._estimate_events_lost(durations, timeout),
            }
        return recs

    def _estimate_events_lost(self, durations: list[float], timeout: float) -> float:
        """Estimate % of runs that would be cut off by this timeout."""
        if not durations:
            return 0.0
        exceeded = sum(1 for d in durations if d > timeout)
        return round(100.0 * exceeded / len(durations), 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_m10_optimizer.py::TestTimeoutTuner -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/optimizer/timeout.py backend/tests/test_m10_optimizer.py
git commit -m "feat(ml): implement M10c Timeout Tuner with percentile-based recommendations"
```

---

## Chunk 2: M10b Provider Priority + M10d Coverage Optimizer

### Task 3: M10b Provider Priority Scorer

**Files:**
- Modify: `backend/src/ml/optimizer/provider_priority.py`
- Test: `backend/tests/test_m10_optimizer.py`

- [ ] **Step 1: Write tests for ProviderPriorityScorer**

Append to `backend/tests/test_m10_optimizer.py`:

```python
class TestProviderPriorityScorer:
    def test_compute_value_score(self):
        from src.ml.optimizer.provider_priority import ProviderPriorityScorer
        scorer = ProviderPriorityScorer()
        score = scorer._compute_value_score(
            value_bets=10, avg_edge=3.5, avg_clv=1.2, duration=60.0
        )
        assert isinstance(score, float)
        assert score > 0

    def test_compute_value_score_zero_duration(self):
        from src.ml.optimizer.provider_priority import ProviderPriorityScorer
        scorer = ProviderPriorityScorer()
        score = scorer._compute_value_score(
            value_bets=5, avg_edge=2.0, avg_clv=0.5, duration=0.0
        )
        assert score == 0.0

    def test_rank_providers(self):
        from src.ml.optimizer.provider_priority import ProviderPriorityScorer
        scorer = ProviderPriorityScorer()
        provider_stats = {
            "unibet": {"value_bets": 20, "avg_edge": 4.0, "avg_clv": 1.5, "duration": 45.0, "is_browser": False},
            "betsson": {"value_bets": 15, "avg_edge": 3.0, "avg_clv": 1.0, "duration": 45.0, "is_browser": False},
            "comeon": {"value_bets": 5, "avg_edge": 2.0, "avg_clv": 0.5, "duration": 200.0, "is_browser": True},
        }
        ranked = scorer.rank_providers(provider_stats)
        assert len(ranked) == 3
        # unibet should rank higher (more value bets, higher edge, same duration)
        assert ranked[0]["provider_id"] == "unibet"
        # All have value_score
        assert all("value_score" in r for r in ranked)

    def test_suggest_deactivation(self):
        from src.ml.optimizer.provider_priority import ProviderPriorityScorer
        scorer = ProviderPriorityScorer()
        provider_stats = {
            "deadprovider": {"value_bets": 0, "avg_edge": 0.0, "avg_clv": 0.0, "duration": 300.0, "is_browser": True},
        }
        ranked = scorer.rank_providers(provider_stats)
        assert ranked[0]["suggest_deactivate"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_m10_optimizer.py::TestProviderPriorityScorer -v`
Expected: FAIL

- [ ] **Step 3: Implement ProviderPriorityScorer**

Replace `backend/src/ml/optimizer/provider_priority.py` with:

```python
"""M10b: Provider Priority Scorer — ranks providers by value per extraction second.

Activates at 100+ provider_value_log rows per provider.
Computes a composite value score: (value_bets × avg_clv) / duration.
Higher score = extract this provider first.
"""
import logging

logger = logging.getLogger(__name__)

# Providers with 0 value bets for this many consecutive runs get flagged
DEACTIVATION_THRESHOLD_RUNS = 10


class ProviderPriorityScorer:
    activation_threshold = 100

    def check_and_train(self, session) -> dict | None:
        """Load provider value + health data and compute rankings."""
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT provider_id, COUNT(*) as cnt FROM provider_value_log "
            "GROUP BY provider_id"
        )).fetchall()
        ready = {pid: cnt for pid, cnt in rows if cnt >= self.activation_threshold}
        if not ready:
            return None

        provider_stats = {}
        for pid in ready:
            # Value metrics from provider_value_log
            stats = session.execute(text(
                "SELECT "
                "  COALESCE(SUM(value_bets_from_provider), 0) as total_vb, "
                "  COALESCE(AVG(NULLIF(avg_edge_from_provider, 0)), 0) as avg_edge, "
                "  COALESCE(AVG(NULLIF(clv_avg_from_provider, 0)), 0) as avg_clv, "
                "  COALESCE(AVG(duration_seconds), 120) as avg_dur "
                "FROM provider_value_log WHERE provider_id = :pid"
            ), {"pid": pid}).fetchone()

            # Health metrics from provider_run_metrics (last 10 runs)
            health = session.execute(text(
                "SELECT "
                "  COALESCE(SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END), 0) as failures, "
                "  COALESCE(SUM(CASE WHEN circuit_breaker_tripped = 1 THEN 1 ELSE 0 END), 0) as cb_trips, "
                "  COUNT(*) as total, "
                "  COALESCE(AVG(spread_count), 0) as avg_spread, "
                "  COALESCE(AVG(total_count), 0) as avg_total "
                "FROM (SELECT * FROM provider_run_metrics WHERE provider_id = :pid "
                "ORDER BY start_time DESC LIMIT 10)"
            ), {"pid": pid}).fetchone()

            total_runs = ready[pid]
            failure_rate = (health[0] / health[2]) if health[2] > 0 else 0
            is_browser = session.execute(text(
                "SELECT COUNT(*) FROM provider_value_log "
                "WHERE provider_id = :pid AND duration_seconds > 100"
            ), {"pid": pid}).scalar() or 0

            provider_stats[pid] = {
                "value_bets": stats[0] / total_runs if total_runs else 0,
                "avg_edge": float(stats[1]),
                "avg_clv": float(stats[2]),
                "duration": float(stats[3]),
                "is_browser": is_browser > total_runs * 0.5,
                "failure_rate": round(failure_rate, 2),
                "cb_trips_last_10": int(health[1]),
                "avg_spread_count": float(health[3]),
                "avg_total_count": float(health[4]),
            }

        ranked = self.rank_providers(provider_stats)
        return {"status": "computed", "rankings": ranked}

    def _compute_value_score(
        self, value_bets: float, avg_edge: float, avg_clv: float, duration: float,
        failure_rate: float = 0.0,
    ) -> float:
        """Compute value-per-second score for a provider, penalized by failure rate."""
        if duration <= 0:
            return 0.0
        clv_weight = max(avg_clv, 0.1)
        raw_score = value_bets * clv_weight / duration
        # Penalize unreliable providers (50% failure rate = 50% score reduction)
        reliability = 1.0 - failure_rate
        return round(raw_score * reliability, 6)

    def rank_providers(self, provider_stats: dict) -> list[dict]:
        """Rank providers by value score, flag zero-value providers for deactivation."""
        scored = []
        for pid, stats in provider_stats.items():
            score = self._compute_value_score(
                stats["value_bets"], stats["avg_edge"], stats["avg_clv"],
                stats["duration"], stats.get("failure_rate", 0),
            )
            scored.append({
                "provider_id": pid,
                "value_score": score,
                "value_bets_per_run": stats["value_bets"],
                "avg_edge": stats["avg_edge"],
                "avg_clv": stats["avg_clv"],
                "avg_duration": stats["duration"],
                "is_browser": stats["is_browser"],
                "failure_rate": stats.get("failure_rate", 0),
                "cb_trips_last_10": stats.get("cb_trips_last_10", 0),
                "avg_spread_count": stats.get("avg_spread_count", 0),
                "avg_total_count": stats.get("avg_total_count", 0),
                "suggest_deactivate": stats["value_bets"] == 0 and stats["is_browser"],
            })
        scored.sort(key=lambda x: x["value_score"], reverse=True)
        return scored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_m10_optimizer.py::TestProviderPriorityScorer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/optimizer/provider_priority.py backend/tests/test_m10_optimizer.py
git commit -m "feat(ml): implement M10b Provider Priority Scorer with value-per-second ranking"
```

---

### Task 4: M10d Coverage Optimizer

**Files:**
- Modify: `backend/src/ml/optimizer/coverage.py`
- Test: `backend/tests/test_m10_optimizer.py`

- [ ] **Step 1: Write tests for CoverageOptimizer**

Append to `backend/tests/test_m10_optimizer.py`:

```python
class TestCoverageOptimizer:
    def test_identify_gaps(self):
        from src.ml.optimizer.coverage import CoverageOptimizer
        opt = CoverageOptimizer()
        coverage_rows = [
            {"provider_id": "unibet", "sport": "football", "event_coverage_pct": 80.0,
             "spread_coverage_pct": 45.0, "total_coverage_pct": 50.0,
             "missing_events": 20, "missing_spread": 30, "missing_total": 25},
            {"provider_id": "unibet", "sport": "tennis", "event_coverage_pct": 90.0,
             "spread_coverage_pct": 85.0, "total_coverage_pct": 80.0,
             "missing_events": 5, "missing_spread": 3, "missing_total": 5},
            {"provider_id": "betsson", "sport": "football", "event_coverage_pct": 75.0,
             "spread_coverage_pct": 40.0, "total_coverage_pct": 55.0,
             "missing_events": 25, "missing_spread": 35, "missing_total": 20},
        ]
        gaps = opt.identify_gaps(coverage_rows)
        assert len(gaps) > 0
        # Football spread should be the biggest gap
        assert gaps[0]["sport"] == "football"
        assert gaps[0]["market"] == "spread"

    def test_provider_coverage_summary(self):
        from src.ml.optimizer.coverage import CoverageOptimizer
        opt = CoverageOptimizer()
        coverage_rows = [
            {"provider_id": "unibet", "sport": "football", "event_coverage_pct": 80.0,
             "spread_coverage_pct": 45.0, "total_coverage_pct": 50.0,
             "missing_events": 20, "missing_spread": 30, "missing_total": 25},
            {"provider_id": "unibet", "sport": "tennis", "event_coverage_pct": 90.0,
             "spread_coverage_pct": 85.0, "total_coverage_pct": 80.0,
             "missing_events": 5, "missing_spread": 3, "missing_total": 5},
        ]
        summary = opt.provider_coverage_summary(coverage_rows)
        assert "unibet" in summary
        assert "avg_event_coverage" in summary["unibet"]

    def test_aggregate_coverage(self):
        from src.ml.optimizer.coverage import CoverageOptimizer
        opt = CoverageOptimizer()
        coverage_rows = [
            {"provider_id": "unibet", "sport": "football", "event_coverage_pct": 80.0,
             "spread_coverage_pct": 45.0, "total_coverage_pct": 50.0,
             "missing_events": 20, "missing_spread": 30, "missing_total": 25},
            {"provider_id": "betsson", "sport": "football", "event_coverage_pct": 75.0,
             "spread_coverage_pct": 60.0, "total_coverage_pct": 55.0,
             "missing_events": 25, "missing_spread": 20, "missing_total": 20},
        ]
        agg = opt.aggregate_coverage(coverage_rows)
        assert "football" in agg
        # Best coverage across providers
        assert agg["football"]["best_event_coverage"] == 80.0
        assert agg["football"]["best_spread_coverage"] == 60.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_m10_optimizer.py::TestCoverageOptimizer -v`
Expected: FAIL

- [ ] **Step 3: Implement CoverageOptimizer**

Replace `backend/src/ml/optimizer/coverage.py` with:

```python
"""M10d: Coverage Optimizer — identifies and prioritizes Pinnacle coverage gaps.

Activates at 20+ pinnacle_coverage_log rows per provider.
Analyzes coverage gaps by sport/market type and ranks them by impact
(missing_count × market_value_weight).
"""
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Spread and total markets produce more value bets per event than ML-only
MARKET_WEIGHTS = {"spread": 1.5, "total": 1.3, "event": 1.0}


class CoverageOptimizer:
    activation_threshold = 20

    def check_and_train(self, session) -> dict | None:
        """Load latest coverage data and compute gap analysis."""
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT provider_id, COUNT(*) as cnt "
            "FROM pinnacle_coverage_log GROUP BY provider_id"
        )).fetchall()
        ready = [pid for pid, cnt in rows if cnt >= self.activation_threshold]
        if not ready:
            return None

        # Get most recent run's coverage data
        last_run = session.execute(text(
            "SELECT run_id FROM pinnacle_coverage_log ORDER BY created_at DESC LIMIT 1"
        )).scalar()
        if not last_run:
            return None

        cov_rows = session.execute(text(
            "SELECT provider_id, sport, event_coverage_pct, spread_coverage_pct, "
            "total_coverage_pct, missing_events, missing_spread, missing_total "
            "FROM pinnacle_coverage_log WHERE run_id = :rid"
        ), {"rid": last_run}).fetchall()

        coverage_dicts = [
            {
                "provider_id": r[0], "sport": r[1],
                "event_coverage_pct": r[2] or 0, "spread_coverage_pct": r[3] or 0,
                "total_coverage_pct": r[4] or 0, "missing_events": r[5] or 0,
                "missing_spread": r[6] or 0, "missing_total": r[7] or 0,
            }
            for r in cov_rows
        ]

        gaps = self.identify_gaps(coverage_dicts)
        summary = self.provider_coverage_summary(coverage_dicts)
        agg = self.aggregate_coverage(coverage_dicts)
        unmatched = self.find_unmatched_events(session)

        return {
            "status": "computed",
            "gaps": gaps[:20],  # top 20 gaps
            "provider_summary": summary,
            "sport_aggregate": agg,
            "unmatched_events": unmatched,
        }

    def identify_gaps(self, coverage_rows: list[dict]) -> list[dict]:
        """Identify and rank coverage gaps by impact score."""
        gaps = []
        sport_gaps = defaultdict(lambda: {"spread": 0, "total": 0, "event": 0})

        for row in coverage_rows:
            sport = row["sport"]
            sport_gaps[sport]["spread"] += row.get("missing_spread", 0)
            sport_gaps[sport]["total"] += row.get("missing_total", 0)
            sport_gaps[sport]["event"] += row.get("missing_events", 0)

        for sport, markets in sport_gaps.items():
            for market, missing in markets.items():
                if missing <= 0:
                    continue
                weight = MARKET_WEIGHTS.get(market, 1.0)
                gaps.append({
                    "sport": sport,
                    "market": market,
                    "missing_count": missing,
                    "impact_score": round(missing * weight, 1),
                })

        gaps.sort(key=lambda x: x["impact_score"], reverse=True)
        return gaps

    def provider_coverage_summary(self, coverage_rows: list[dict]) -> dict:
        """Compute per-provider average coverage across sports."""
        provider_data = defaultdict(list)
        for row in coverage_rows:
            provider_data[row["provider_id"]].append(row)

        summary = {}
        for pid, rows in provider_data.items():
            summary[pid] = {
                "avg_event_coverage": round(
                    sum(r["event_coverage_pct"] for r in rows) / len(rows), 1
                ),
                "avg_spread_coverage": round(
                    sum(r["spread_coverage_pct"] for r in rows) / len(rows), 1
                ),
                "avg_total_coverage": round(
                    sum(r["total_coverage_pct"] for r in rows) / len(rows), 1
                ),
                "sports_covered": len(rows),
                "total_missing_events": sum(r["missing_events"] for r in rows),
            }
        return summary

    def aggregate_coverage(self, coverage_rows: list[dict]) -> dict:
        """Compute per-sport best coverage across all providers."""
        sport_data = defaultdict(list)
        for row in coverage_rows:
            sport_data[row["sport"]].append(row)

        agg = {}
        for sport, rows in sport_data.items():
            agg[sport] = {
                "best_event_coverage": max(r["event_coverage_pct"] for r in rows),
                "best_spread_coverage": max(r["spread_coverage_pct"] for r in rows),
                "best_total_coverage": max(r["total_coverage_pct"] for r in rows),
                "providers_count": len(set(r["provider_id"] for r in rows)),
                "total_missing_events": min(r["missing_events"] for r in rows),
            }
        return agg

    def find_unmatched_events(self, session, limit: int = 10) -> list[dict]:
        """Surface top unmatched Pinnacle events with diagnostic info.

        Queries Events that have Pinnacle odds but zero soft provider odds.
        Returns event details + closest fuzzy match score hints.
        """
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT e.id, e.sport, e.league, e.home_team, e.away_team, e.start_time "
            "FROM events e "
            "JOIN odds o ON o.event_id = e.id AND o.provider_id = 'pinnacle' "
            "WHERE e.id NOT IN ("
            "  SELECT DISTINCT event_id FROM odds "
            "  WHERE provider_id NOT IN ('pinnacle', 'polymarket')"
            ") "
            "ORDER BY e.start_time ASC LIMIT :lim"
        ), {"lim": limit}).fetchall()

        unmatched = []
        for r in rows:
            home = r[3] or ""
            away = r[4] or ""
            unmatched.append({
                "event_id": r[0],
                "sport": r[1],
                "league": r[2],
                "home_team": home,
                "away_team": away,
                "start_time": str(r[5]),
                "team_name_length": len(home) + len(away),
                "has_special_chars": any(ord(c) > 127 for c in home + away),
            })
        return unmatched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_m10_optimizer.py::TestCoverageOptimizer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/optimizer/coverage.py backend/tests/test_m10_optimizer.py
git commit -m "feat(ml): implement M10d Coverage Optimizer with gap analysis and ranking"
```

---

## Chunk 3: Integration + API + Final Tests

### Task 5: Wire M10 into TrainingOrchestrator

**Files:**
- Modify: `backend/src/ml/training/train_all.py`

- [ ] **Step 1: Add M10 configs to MODEL_CONFIGS and _get_trainer**

Add to the `MODEL_CONFIGS` dict in `train_all.py`:

```python
# M10 optimizer sub-models
"schedule_optimizer": {
    "min_samples": 50, "domain": "extraction",
    "source_type": "extraction_run", "task": "regression",
},
"provider_priority": {
    "min_samples": 100, "domain": "extraction",
    "source_type": "provider_value", "task": "ranking",
},
"timeout_tuner": {
    "min_samples": 50, "domain": "extraction",
    "source_type": "provider_metrics", "task": "statistical",
},
"coverage_optimizer": {
    "min_samples": 20, "domain": "extraction",
    "source_type": "pinnacle_coverage", "task": "analysis",
},
```

Add trainer functions:

```python
def _train_schedule_optimizer(data, session):
    from src.ml.optimizer.schedule import ScheduleOptimizer
    return ScheduleOptimizer().check_and_train(session)

def _train_provider_priority(data, session):
    from src.ml.optimizer.provider_priority import ProviderPriorityScorer
    return ProviderPriorityScorer().check_and_train(session)

def _train_timeout_tuner(data, session):
    from src.ml.optimizer.timeout import TimeoutTuner
    return TimeoutTuner().check_and_train(session)

def _train_coverage_optimizer(data, session):
    from src.ml.optimizer.coverage import CoverageOptimizer
    return CoverageOptimizer().check_and_train(session)
```

Add to `_get_trainer` dict:

```python
"schedule_optimizer": lambda data, s: _train_schedule_optimizer(data, s),
"provider_priority": lambda data, s: _train_provider_priority(data, s),
"timeout_tuner": lambda data, s: _train_timeout_tuner(data, s),
"coverage_optimizer": lambda data, s: _train_coverage_optimizer(data, s),
```

- [ ] **Step 2: Run all tests**

Run: `pytest backend/tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add backend/src/ml/training/train_all.py
git commit -m "feat(ml): wire M10 optimizer models into TrainingOrchestrator"
```

---

### Task 6: Add optimizer status to API

**Files:**
- Modify: `backend/src/api/routes/extraction.py`

- [ ] **Step 1: Add GET /extraction/optimizer/status endpoint**

Add endpoint that returns the latest optimizer results:

```python
@router.get("/extraction/optimizer/status")
def get_optimizer_status(session=Depends(get_session)):
    """Return latest M10 optimizer analysis results."""
    from src.ml.optimizer.schedule import ScheduleOptimizer
    from src.ml.optimizer.provider_priority import ProviderPriorityScorer
    from src.ml.optimizer.timeout import TimeoutTuner
    from src.ml.optimizer.coverage import CoverageOptimizer

    results = {}
    try:
        results["schedule"] = ScheduleOptimizer().check_and_train(session) or {"status": "insufficient_data"}
    except Exception as e:
        results["schedule"] = {"status": "error", "error": str(e)}
    try:
        results["provider_priority"] = ProviderPriorityScorer().check_and_train(session) or {"status": "insufficient_data"}
    except Exception as e:
        results["provider_priority"] = {"status": "error", "error": str(e)}
    try:
        results["timeout"] = TimeoutTuner().check_and_train(session) or {"status": "insufficient_data"}
    except Exception as e:
        results["timeout"] = {"status": "error", "error": str(e)}
    try:
        results["coverage"] = CoverageOptimizer().check_and_train(session) or {"status": "insufficient_data"}
    except Exception as e:
        results["coverage"] = {"status": "error", "error": str(e)}

    # Remove non-serializable model objects
    if "schedule" in results and "model" in results.get("schedule", {}):
        del results["schedule"]["model"]

    return results
```

- [ ] **Step 2: Run all tests**

Run: `pytest backend/tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/extraction.py
git commit -m "feat(api): add /extraction/optimizer/status endpoint for M10 results"
```

---

### Task 7: Final test suite verification

- [ ] **Step 1: Run full test suite**

Run: `pytest backend/tests/ -v --tb=short`
Expected: All tests pass (116 existing + new M10 tests)

- [ ] **Step 2: Verify imports work**

```bash
python -c "from src.ml.optimizer.schedule import ScheduleOptimizer; print('M10a OK')"
python -c "from src.ml.optimizer.provider_priority import ProviderPriorityScorer; print('M10b OK')"
python -c "from src.ml.optimizer.timeout import TimeoutTuner; print('M10c OK')"
python -c "from src.ml.optimizer.coverage import CoverageOptimizer; print('M10d OK')"
```

- [ ] **Step 3: Final commit if needed**

```bash
git add -A
git commit -m "chore: finalize M10 optimizer test suite"
```
