"""M10a: Schedule Optimizer — predicts value bet yield per extraction run.

Activates at 50+ runs per tier in extraction_features table.
Uses LightGBM regression to predict how many value bets a run will produce
given current timing/health context. Recommends skipping low-yield runs.
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)

TIER_ENCODING = {"sharp": 0, "api_soft": 1, "browser_soft": 2}

FEATURE_NAMES = [
    "hour_of_day", "day_of_week", "tier_encoded",
    "minutes_since_last_sharp", "minutes_since_last_soft",
    "events_starting_next_2h", "events_starting_next_6h",
    "providers_attempted", "providers_succeeded", "providers_failed",
    "circuit_breakers_open",
    "total_events", "total_odds", "avg_match_rate",
    "value_bets_last_run", "value_bets_avg_last_5",
    "avg_edge_last_run", "providers_with_value_last_run",
]


class ScheduleOptimizer:
    activation_threshold = 50

    def __init__(self):
        self._model = None

    def check_and_train(self, session) -> dict | None:
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
        enriched = []
        past_yields = []
        for row in rows:
            vb_found = row[15] or 0
            avg_edge = row[16] or 0
            hist = {
                "value_bets_last_run": past_yields[-1] if past_yields else 0,
                "value_bets_avg_last_5": (
                    sum(past_yields[-5:]) / len(past_yields[-5:]) if past_yields else 0
                ),
                "avg_edge_last_run": avg_edge,
                "providers_with_value_last_run": row[9] or 0,
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
        filtered = [r for r in rows if r.get("trigger") == tier]
        return filtered if len(filtered) >= self.activation_threshold else None

    def _build_features_and_target(self, rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
        X = np.array([[row.get(f, 0) or 0 for f in FEATURE_NAMES] for row in rows], dtype=float)
        y = np.array([row.get("value_bets_found", 0) or 0 for row in rows], dtype=float)
        return X, y

    def predict_yield(self, features: dict) -> float:
        if self._model is None:
            return -1.0
        X = np.array([[features.get(f, 0) or 0 for f in FEATURE_NAMES]], dtype=float)
        pred = float(self._model.predict(X)[0])
        return max(0.0, pred)

    def should_skip_run(self, features: dict, min_yield: float = 2.0) -> bool:
        if self._model is None:
            return False
        return self.predict_yield(features) < min_yield
