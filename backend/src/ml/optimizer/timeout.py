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
