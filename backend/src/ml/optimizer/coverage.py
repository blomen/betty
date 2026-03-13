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
