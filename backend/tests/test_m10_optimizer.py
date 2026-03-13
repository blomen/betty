"""Tests for M10 extraction pipeline optimizer models."""
import numpy as np
import pytest


class TestScheduleOptimizer:
    def test_check_threshold_insufficient(self):
        from src.ml.optimizer.schedule import ScheduleOptimizer
        opt = ScheduleOptimizer()
        result = opt._load_tier_data([], "api_soft")
        assert result is None

    def test_build_features_and_target(self):
        from src.ml.optimizer.schedule import ScheduleOptimizer, FEATURE_NAMES
        opt = ScheduleOptimizer()
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
        assert all(v >= 0 for v in y)

    def test_predict_yield(self):
        from src.ml.optimizer.schedule import ScheduleOptimizer, FEATURE_NAMES
        opt = ScheduleOptimizer()
        np.random.seed(42)
        n = 60
        X = np.random.rand(n, len(FEATURE_NAMES))
        y = np.random.poisson(5, n).astype(float)
        from src.ml.optimizer.trainer import train_model
        result = train_model(X, y, task="regression", min_samples=30)
        assert result is not None
        opt._model = result["model"]
        features = {name: 0.5 for name in FEATURE_NAMES}
        pred = opt.predict_yield(features)
        assert isinstance(pred, float)
        assert pred >= 0

    def test_should_skip_no_model(self):
        from src.ml.optimizer.schedule import ScheduleOptimizer
        opt = ScheduleOptimizer()
        assert opt.should_skip_run({}) is False

    def test_enrich_with_history(self):
        from src.ml.optimizer.schedule import ScheduleOptimizer
        opt = ScheduleOptimizer()
        # Simulate raw DB rows: id, trigger, hour, dow, min_sharp, min_soft, ev_2h, ev_6h,
        #   prov_att, prov_succ, prov_fail, cb_open, tot_ev, tot_odds, avg_mr, vb_found, avg_edge
        rows = [
            (1, "api_soft", 10, 2, 30.0, 60.0, 15, 40, 12, 10, 2, 0, 200, 1000, 0.65, 5, 2.5),
            (2, "api_soft", 11, 2, 35.0, 65.0, 16, 42, 12, 10, 2, 0, 210, 1050, 0.66, 8, 3.0),
            (3, "api_soft", 12, 2, 40.0, 70.0, 17, 44, 12, 10, 2, 0, 220, 1100, 0.67, 3, 1.5),
        ]
        enriched = opt._enrich_with_history(rows)
        assert len(enriched) == 3
        assert enriched[0]["value_bets_last_run"] == 0  # first row has no history
        assert enriched[1]["value_bets_last_run"] == 5  # second row sees first row's yield
        assert enriched[2]["value_bets_avg_last_5"] == pytest.approx((5 + 8) / 2)


class TestTimeoutTuner:
    def test_compute_percentiles_empty(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        assert tuner._compute_percentiles([]) == {}

    def test_compute_percentiles(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        durations = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        pcts = tuner._compute_percentiles(durations)
        assert "p50" in pcts
        assert "p90" in pcts
        assert "p95" in pcts
        assert pcts["p95"] > pcts["p90"] > pcts["p50"]

    def test_recommend_timeout(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        durations = list(range(10, 200, 3))
        timeout = tuner.recommend_timeout(durations, buffer_pct=0.2)
        assert isinstance(timeout, float)
        assert timeout > 0
        p95 = float(np.percentile(durations, 95))
        assert timeout == pytest.approx(p95 * 1.2, rel=0.01)

    def test_recommend_timeout_empty(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        assert tuner.recommend_timeout([]) == 120.0

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

    def test_estimate_events_lost(self):
        from src.ml.optimizer.timeout import TimeoutTuner
        tuner = TimeoutTuner()
        durations = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        lost = tuner._estimate_events_lost(durations, 85.0)
        assert lost == pytest.approx(20.0)  # 2 out of 10 exceed 85


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

    def test_compute_value_score_with_failures(self):
        from src.ml.optimizer.provider_priority import ProviderPriorityScorer
        scorer = ProviderPriorityScorer()
        clean = scorer._compute_value_score(value_bets=10, avg_edge=3.0, avg_clv=1.0, duration=60.0)
        failing = scorer._compute_value_score(value_bets=10, avg_edge=3.0, avg_clv=1.0, duration=60.0, failure_rate=0.5)
        assert failing < clean
        assert failing == pytest.approx(clean * 0.5, rel=0.01)

    def test_rank_providers(self):
        from src.ml.optimizer.provider_priority import ProviderPriorityScorer
        scorer = ProviderPriorityScorer()
        provider_stats = {
            "unibet": {"value_bets": 20, "avg_edge": 4.0, "avg_clv": 1.5, "duration": 45.0, "is_browser": False, "failure_rate": 0.0},
            "betsson": {"value_bets": 15, "avg_edge": 3.0, "avg_clv": 1.0, "duration": 45.0, "is_browser": False, "failure_rate": 0.0},
            "comeon": {"value_bets": 5, "avg_edge": 2.0, "avg_clv": 0.5, "duration": 200.0, "is_browser": True, "failure_rate": 0.1},
        }
        ranked = scorer.rank_providers(provider_stats)
        assert len(ranked) == 3
        assert ranked[0]["provider_id"] == "unibet"
        assert all("value_score" in r for r in ranked)

    def test_suggest_deactivation(self):
        from src.ml.optimizer.provider_priority import ProviderPriorityScorer
        scorer = ProviderPriorityScorer()
        provider_stats = {
            "deadprovider": {"value_bets": 0, "avg_edge": 0.0, "avg_clv": 0.0, "duration": 300.0, "is_browser": True, "failure_rate": 0.8},
        }
        ranked = scorer.rank_providers(provider_stats)
        assert ranked[0]["suggest_deactivate"] is True


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
        assert gaps[0]["sport"] == "football"
        assert gaps[0]["market"] == "spread"

    def test_identify_gaps_no_missing(self):
        from src.ml.optimizer.coverage import CoverageOptimizer
        opt = CoverageOptimizer()
        coverage_rows = [
            {"provider_id": "unibet", "sport": "football", "event_coverage_pct": 100.0,
             "spread_coverage_pct": 100.0, "total_coverage_pct": 100.0,
             "missing_events": 0, "missing_spread": 0, "missing_total": 0},
        ]
        gaps = opt.identify_gaps(coverage_rows)
        assert len(gaps) == 0

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
        assert summary["unibet"]["avg_event_coverage"] == 85.0

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
        assert agg["football"]["best_event_coverage"] == 80.0
        assert agg["football"]["best_spread_coverage"] == 60.0
