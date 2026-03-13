"""M10b: Provider Priority Scorer — ranks providers by value per extraction second.

Activates at 100+ provider_value_log rows per provider (~3 months).
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
