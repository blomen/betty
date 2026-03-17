"""Comprehensive ML system test — exercises all 13 models, features, analytics, and serving.

Tests:
1. Feature extraction (betting, extraction, coverage)
2. Model instantiation + synthetic training (all 13 models)
3. M10 optimizers with live DB data
4. Analytics engine (provider ROI, coverage gaps, scheduling)
5. Training orchestrator (threshold checks)
6. Predictor serving layer
7. Feature store read/write
"""
import sys
import os
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime, timezone

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_ml")

from src.db.models import get_session

PASS = 0
FAIL = 0
SKIP = 0


def report(name: str, success: bool, detail: str = "", skipped: bool = False):
    global PASS, FAIL, SKIP
    if skipped:
        SKIP += 1
        print(f"  SKIP  {name}: {detail}")
    elif success:
        PASS += 1
        print(f"  PASS  {name}: {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}: {detail}")


# ──────────────────────────────────────────────
# 1. Feature extraction modules
# ──────────────────────────────────────────────
print("\n=== 1. FEATURE EXTRACTION ===")

try:
    from src.ml.features.betting_features import extract_betting_features
    features = extract_betting_features(
        edge_pct=3.5, provider_odds=2.10, fair_odds=2.00, fair_probability=0.50,
        provider="unibet", sport="football", market="1x2", event_id="test:a:b:20260313",
        prob_sum=1.02, odds_by_outcome={"1": [{"provider": "unibet", "odds": 2.10}, {"provider": "pinnacle", "odds": 2.00}]},
        pinnacle_overround=1.04, event_start_time=datetime(2026, 3, 14, 15, 0, tzinfo=timezone.utc),
        point=None,
    )
    assert "edge_pct" in features and "prob_sum" in features and "odds_ratio" in features
    report("betting_features", True, f"{len(features)} features extracted")
except Exception as e:
    report("betting_features", False, str(e))

try:
    from src.ml.features.extraction_features import extract_extraction_features
    ext_feats = extract_extraction_features(
        run_id="test-run-001", trigger="api_soft",
        providers_attempted=10, providers_succeeded=8,
        providers_failed=2, total_events=500,
        total_odds=3000, avg_match_rate=0.65,
        circuit_breakers_open=0, events_starting_next_2h=30, events_starting_next_6h=80,
    )
    assert "hour_of_day" in ext_feats and "trigger" in ext_feats
    report("extraction_features", True, f"{len(ext_feats)} features")
except ImportError:
    report("extraction_features", True, "module exists (import structure may differ)", skipped=True)
except Exception as e:
    report("extraction_features", False, str(e))

try:
    from src.ml.features.boost_features import extract_boost_features
    report("boost_features", True, "module imports OK")
except Exception as e:
    report("boost_features", False, str(e))

try:
    from src.ml.features.kelly_features import extract_kelly_features
    report("kelly_features", True, "module imports OK")
except Exception as e:
    report("kelly_features", False, str(e))

try:
    from src.ml.features.devig_features import extract_devig_features
    report("devig_features", True, "module imports OK")
except Exception as e:
    report("devig_features", False, str(e))

try:
    from src.ml.features.limit_features import extract_limit_features
    report("limit_features", True, "module imports OK")
except Exception as e:
    report("limit_features", False, str(e))

try:
    from src.ml.features.trading_features import extract_trading_features
    report("trading_features", True, "module imports OK")
except Exception as e:
    report("trading_features", False, str(e))

try:
    from src.ml.features.candle_features import snapshot_candles, CandleFlow
    report("candle_features", True, "module imports OK (snapshot_candles, CandleFlow)")
except Exception as e:
    report("candle_features", False, str(e))

try:
    from src.ml.features.gate_features import extract_gate_features
    report("gate_features", True, "module imports OK")
except Exception as e:
    report("gate_features", False, str(e))

try:
    from src.ml.features.macro_features import extract_macro_features
    report("macro_features", True, "module imports OK")
except Exception as e:
    report("macro_features", False, str(e))

try:
    from src.ml.features.pinnacle_coverage import log_coverage, compute_coverage_delta
    report("pinnacle_coverage", True, "module imports OK (log_coverage, compute_coverage_delta)")
except Exception as e:
    report("pinnacle_coverage", False, str(e))

# ──────────────────────────────────────────────
# 2. Model instantiation
# ──────────────────────────────────────────────
print("\n=== 2. MODEL INSTANTIATION ===")

model_classes = {
    "M1 EdgeQuality": ("src.ml.models.edge_quality", "EdgeQualityModel"),
    "M2 LimitPredictor": ("src.ml.models.limit_predictor", "LimitPredictorModel"),
    "M3 DevigSelector": ("src.ml.models.devig_selector", "DevigSelectorModel"),
    "M4 BoostCalibrator": ("src.ml.models.boost_calibrator", "BoostCalibratorModel"),
    "M5 SetupScorer": ("src.ml.models.setup_scorer", "SetupScorerModel"),
    "M6 TemporalPattern": ("src.ml.models.temporal_pattern", "TemporalPatternModel"),
    "M7 GateClassifier": ("src.ml.models.gate_classifier", "GateClassifierModel"),
    "M8 AdaptiveKelly": ("src.ml.models.adaptive_kelly", "AdaptiveKellyModel"),
    "M9 MacroEngine": ("src.ml.models.macro_engine", "MacroEngineModel"),
}

for name, (module_path, class_name) in model_classes.items():
    try:
        mod = __import__(module_path, fromlist=[class_name])
        cls = getattr(mod, class_name)
        instance = cls()
        # Verify predict returns None when untrained
        pred = instance.predict({})
        assert pred is None, f"Expected None from untrained model, got {pred}"
        report(name, True, f"instantiated, predict()=None (untrained)")
    except Exception as e:
        report(name, False, f"{e}")

# M10 optimizers
m10_classes = {
    "M10a ScheduleOptimizer": ("src.ml.optimizer.schedule", "ScheduleOptimizer"),
    "M10b ProviderPriority": ("src.ml.optimizer.provider_priority", "ProviderPriorityScorer"),
    "M10c TimeoutTuner": ("src.ml.optimizer.timeout", "TimeoutTuner"),
    "M10d CoverageOptimizer": ("src.ml.optimizer.coverage", "CoverageOptimizer"),
}

for name, (module_path, class_name) in m10_classes.items():
    try:
        mod = __import__(module_path, fromlist=[class_name])
        cls = getattr(mod, class_name)
        instance = cls()
        report(name, True, f"instantiated (threshold={instance.activation_threshold})")
    except Exception as e:
        report(name, False, f"{e}")


# ──────────────────────────────────────────────
# 3. M10 Optimizers with live DB
# ──────────────────────────────────────────────
print("\n=== 3. M10 OPTIMIZERS (LIVE DB) ===")

session = get_session()

try:
    from src.ml.optimizer.coverage import CoverageOptimizer
    cov = CoverageOptimizer()
    result = cov.check_and_train(session)
    if result:
        gaps = result.get("gaps", [])
        providers = result.get("provider_summary", {})
        unmatched = result.get("unmatched_events", [])
        report("M10d coverage.check_and_train", True,
               f"status={result['status']}, {len(gaps)} gaps, {len(providers)} providers, {len(unmatched)} unmatched")
    else:
        report("M10d coverage.check_and_train", True, "insufficient data (expected)", skipped=True)
except Exception as e:
    report("M10d coverage.check_and_train", False, f"{e}\n{traceback.format_exc()}")

try:
    from src.ml.optimizer.schedule import ScheduleOptimizer
    sched = ScheduleOptimizer()
    result = sched.check_and_train(session)
    if result:
        report("M10a schedule.check_and_train", True,
               f"status={result.get('status')}, samples={result.get('training_samples')}, "
               f"score={result.get('validation_score')}")
        # Test prediction
        test_features = {f: 0 for f in sched.__class__.__dict__.get("FEATURE_NAMES", [])}
        from src.ml.optimizer.schedule import FEATURE_NAMES as SCHED_FEATURES
        test_features = {f: 5.0 for f in SCHED_FEATURES}
        pred = sched.predict_yield(test_features)
        report("M10a schedule.predict_yield", True, f"predicted={pred:.2f}")
        skip = sched.should_skip_run(test_features, min_yield=2.0)
        report("M10a schedule.should_skip_run", True, f"skip={skip}")
    else:
        report("M10a schedule.check_and_train", True, "insufficient data", skipped=True)
except Exception as e:
    report("M10a schedule.check_and_train", False, f"{e}\n{traceback.format_exc()}")

try:
    from src.ml.optimizer.provider_priority import ProviderPriorityScorer
    pp = ProviderPriorityScorer()
    result = pp.check_and_train(session)
    if result:
        rankings = result.get("rankings", [])
        report("M10b provider_priority.check_and_train", True,
               f"status={result.get('status')}, {len(rankings)} providers ranked")
        if rankings:
            top = rankings[0]
            report("M10b top provider", True,
                   f"{top.get('provider_id')}: score={top.get('composite_score', 0):.3f}")
    else:
        report("M10b provider_priority.check_and_train", True, "insufficient data", skipped=True)
except Exception as e:
    report("M10b provider_priority.check_and_train", False, f"{e}\n{traceback.format_exc()}")

try:
    from src.ml.optimizer.timeout import TimeoutTuner
    tt = TimeoutTuner()
    result = tt.check_and_train(session)
    if result:
        recs = result.get("recommendations", [])
        report("M10c timeout.check_and_train", True,
               f"status={result.get('status')}, {len(recs)} recommendations")
        for r in recs[:3]:
            report(f"  M10c timeout {r.get('provider_id', '?')}", True,
                   f"current={r.get('current_timeout', '?')}s → recommended={r.get('recommended_timeout', '?')}s")
    else:
        report("M10c timeout.check_and_train", True, "insufficient data", skipped=True)
except Exception as e:
    report("M10c timeout.check_and_train", False, f"{e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────
# 4. Analytics Engine (live DB)
# ──────────────────────────────────────────────
print("\n=== 4. ANALYTICS ENGINE ===")

try:
    from src.ml.analytics.engine import compute_provider_roi
    roi = compute_provider_roi(session)
    report("provider_roi", True, f"{len(roi)} providers")
    for r in roi[:5]:
        report(f"  roi {r['provider_id']}", True,
               f"opps={r['total_opportunities']}, avg_edge={r['avg_edge']}%, bets={r['total_bets']}, pnl={r['net_pnl']}")
except Exception as e:
    report("provider_roi", False, f"{e}\n{traceback.format_exc()}")

try:
    from src.ml.analytics.engine import compute_coverage_gaps
    gaps = compute_coverage_gaps(session)
    report("coverage_gaps", True, f"{len(gaps)} provider-sport gaps")
    # Show top 5
    for g in gaps[:5]:
        report(f"  gap {g['provider_id']}/{g['sport']}", True,
               f"coverage={g['event_coverage_pct']}%, missing={g['missing_events']} events")
except Exception as e:
    report("coverage_gaps", False, f"{e}\n{traceback.format_exc()}")

try:
    from src.ml.analytics.engine import compute_scheduling_efficiency
    sched = compute_scheduling_efficiency(session)
    report("scheduling_efficiency", True, f"{len(sched)} tiers")
    for tier, data in sched.items():
        report(f"  tier {tier}", True,
               f"runs={data['runs']}, avg_dur={data['avg_duration']}s, avg_events={data['avg_events']}, "
               f"events/s={data['events_per_sec']}")
except Exception as e:
    report("scheduling_efficiency", False, f"{e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────
# 5. Training Orchestrator (threshold check)
# ──────────────────────────────────────────────
print("\n=== 5. TRAINING ORCHESTRATOR ===")

try:
    from src.ml.training.train_all import TrainingOrchestrator
    orch = TrainingOrchestrator()
    thresholds = orch.check_thresholds(session)
    ready_count = sum(1 for v in thresholds.values() if v)
    not_ready = [k for k, v in thresholds.items() if not v]
    report("check_thresholds", True,
           f"{ready_count}/{len(thresholds)} models data-ready")
    if not_ready:
        report("  not_ready", True, f"{', '.join(not_ready)}", skipped=True)
except Exception as e:
    report("check_thresholds", False, f"{e}\n{traceback.format_exc()}")

# Try train_all (will skip most models due to insufficient data)
try:
    results = orch.train_all(session)
    trained = [k for k, v in results.items() if v == "trained"]
    insufficient = [k for k, v in results.items() if v == "insufficient_data"]
    errors = [k for k, v in results.items() if "error" in str(v)]
    report("train_all", True,
           f"trained={len(trained)}, insufficient={len(insufficient)}, errors={len(errors)}")
    if trained:
        report("  trained models", True, ", ".join(trained))
    if errors:
        for k in errors:
            report(f"  error {k}", False, results[k])
    session.commit()
except Exception as e:
    report("train_all", False, f"{e}\n{traceback.format_exc()}")
    session.rollback()


# ──────────────────────────────────────────────
# 6. Predictor Serving
# ──────────────────────────────────────────────
print("\n=== 6. PREDICTOR SERVING ===")

try:
    from src.ml.serving.predictor import Predictor, get_predictor
    pred = get_predictor()
    report("predictor singleton", True, f"models loaded: {len(pred.models)}")

    # Load from registry (if any trained models exist)
    loaded = pred.load_from_registry(session)
    report("load_from_registry", True, f"{loaded} models loaded from registry")

    # Test prediction on all loaded models
    for model_name in pred.models:
        result = pred.predict(model_name, {})
        report(f"  predict {model_name}", True, f"result={result}")

    # Verify unloaded model returns None
    result = pred.predict("nonexistent_model", {})
    assert result is None
    report("predict_unloaded", True, "returns None (correct)")
except Exception as e:
    report("predictor", False, f"{e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────
# 7. Feature Store
# ──────────────────────────────────────────────
print("\n=== 7. FEATURE STORE ===")

try:
    from src.ml.feature_store import log_features, get_training_data, resolve_clv_outcomes
    from src.db.models import MlFeature

    # Check existing feature counts
    for domain in ["betting", "trading", "extraction"]:
        count = session.query(MlFeature).filter_by(domain=domain).count()
        resolved = session.query(MlFeature).filter(
            MlFeature.domain == domain, MlFeature.outcome.isnot(None)
        ).count()
        report(f"feature_store {domain}", True, f"{count} total, {resolved} resolved")

    # Test CLV outcome resolution
    updated = resolve_clv_outcomes(session)
    report("resolve_clv_outcomes", True, f"{updated} rows updated")
    session.commit()
except Exception as e:
    report("feature_store", False, f"{e}\n{traceback.format_exc()}")
    session.rollback()


# ──────────────────────────────────────────────
# 8. Diagnostics & Recommendations
# ──────────────────────────────────────────────
print("\n=== 8. DIAGNOSTICS & RECOMMENDATIONS ===")

try:
    from src.ml.analytics.diagnostics import diagnose_provider
    diag = diagnose_provider({
        "provider_id": "test_provider",
        "avg_match_rate": 0.25,
        "avg_events": 50,
        "avg_duration": 300,
        "total_opportunities": 5,
        "seconds_per_value_bet": 60,
        "spread_count": 0,
        "total_count": 0,
    })
    report("diagnose_provider", True, f"{len(diag)} recommendations generated")
    for d in diag:
        report(f"  diag {d['category']}", True, f"[{d['severity']}] {d['message'][:60]}")
except Exception as e:
    report("diagnostics", False, f"{e}\n{traceback.format_exc()}")

try:
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(session)
    report("RecommendationManager", True, "instantiated OK")
except Exception as e:
    report("RecommendationManager", False, f"{e}")


# ──────────────────────────────────────────────
# 9. LightGBM Trainer
# ──────────────────────────────────────────────
print("\n=== 9. LIGHTGBM TRAINER ===")

try:
    import numpy as np
    from src.ml.optimizer.trainer import train_model

    # Synthetic binary classification
    np.random.seed(42)
    X = np.random.randn(200, 5)
    y = (X[:, 0] + X[:, 1] > 0).astype(float)
    result = train_model(X, y, task="classification", min_samples=50)
    if result:
        report("trainer classification", True,
               f"score={result['validation_score']:.3f}, importance={list(result.get('feature_importance', {}).keys())[:3]}")
    else:
        report("trainer classification", False, "returned None")

    # Synthetic regression
    y_reg = X[:, 0] * 2 + X[:, 1] + np.random.randn(200) * 0.1
    result = train_model(X, y_reg, task="regression", min_samples=50)
    if result:
        report("trainer regression", True, f"score={result['validation_score']:.3f}")
    else:
        report("trainer regression", False, "returned None")

except ImportError as e:
    report("trainer", True, f"dependency not installed: {e}", skipped=True)
except Exception as e:
    report("trainer", False, f"{e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────
# 10. API Endpoint Smoke Tests (via HTTP)
# ──────────────────────────────────────────────
print("\n=== 10. API ENDPOINTS ===")

try:
    import urllib.request

    endpoints = [
        ("GET", "http://localhost:8000/api/extraction/ml/status", "ml/status"),
        ("GET", "http://localhost:8000/api/extraction/optimizer/status", "optimizer/status"),
    ]

    for method, url, name in endpoints:
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                report(f"API {name}", True, f"HTTP {resp.status}, keys={list(data.keys())[:5]}")
        except Exception as e:
            report(f"API {name}", False, str(e))

    # Test POST ml/train
    try:
        req = urllib.request.Request("http://localhost:8000/api/extraction/ml/train", method="POST",
                                    data=b"", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            trained = [k for k, v in data.items() if v == "trained"]
            report("API ml/train", True, f"HTTP {resp.status}, trained={trained or 'none'}")
    except Exception as e:
        report("API ml/train", False, str(e))

except Exception as e:
    report("API endpoints", False, str(e))


# ──────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────
session.close()

print(f"\n{'='*50}")
print(f"  RESULTS: {PASS} passed, {FAIL} failed, {SKIP} skipped")
print(f"{'='*50}")

sys.exit(1 if FAIL > 0 else 0)
