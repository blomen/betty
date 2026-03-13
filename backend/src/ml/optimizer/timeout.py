"""M10c: Timeout Tuner — recommends per-provider extraction timeouts.

Activates at 50+ runs per provider in provider_run_metrics.
Uses duration percentiles (P95 + buffer) — statistical approach is more
interpretable and robust for this use case than ML.
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)


class TimeoutTuner:
    activation_threshold = 50

    def check_and_train(self, session) -> dict | None:
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT provider_id, COUNT(*) as cnt FROM provider_run_metrics "
            "WHERE duration_seconds IS NOT NULL AND status = 'success' "
            "GROUP BY provider_id"
        )).fetchall()
        ready = {pid: cnt for pid, cnt in rows if cnt >= self.activation_threshold}
        if not ready:
            return None

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
        if not durations:
            return 120.0
        p95 = float(np.percentile(durations, 95))
        return round(p95 * (1.0 + buffer_pct), 1)

    def recommend_all(self, provider_durations: dict[str, list[float]]) -> dict:
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
        if not durations:
            return 0.0
        exceeded = sum(1 for d in durations if d > timeout)
        return round(100.0 * exceeded / len(durations), 1)
