"""M10a: Schedule Optimizer — predicts optimal extraction intervals per tier.

Activates at 50+ runs per tier in extraction_features table.
Until then, returns None and the rule-based analytics engine handles scheduling.
"""
import logging

logger = logging.getLogger(__name__)


class ScheduleOptimizer:
    activation_threshold = 50

    def check_and_train(self, session) -> dict | None:
        from sqlalchemy import text
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
        return {"ready_tiers": ready_tiers, "status": "threshold_met"}
