"""M5: Setup Score Predictor -- predicts R-multiple for trading signals.

Replaces fixed +10/+8/+5 scoring weights with learned interactions.
Phase 1: 15-20 high-prior features. Phase 2: full 70+ features.
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 200
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

# Phase 1: highest-prior features (start with these at 200 trades)
FEATURE_NAMES_PHASE1 = [
    "base_score", "delta_pct", "cvd_slope_5bar",
    "volume_ratio_vs_20bar", "volume_ratio_vs_session",
    "distance_to_level_ticks", "distance_to_poc_ticks",
    "price_position_in_va", "ib_range_vs_avg",
    "minutes_since_rth_open", "aspr_percentile",
    "passive_active_ratio", "absorption_bar_count",
    "vix_level", "gex",
]

# Phase 2: add at 500+ trades
FEATURE_NAMES_PHASE2 = FEATURE_NAMES_PHASE1 + [
    "delta_divergence_bars", "delta_unwind_speed_bars",
    "cvd_slope_10bar", "cvd_acceleration",
    "body_ratio_last", "body_ratio_avg_3bar",
    "spread_ticks", "spread_ratio_vs_avg",
    "trapped_magnitude", "tick_count_ratio",
    "imbalance_ratio_max", "stacked_imbalance_count",
    "big_trades_count", "big_trades_net_delta",
    "stop_run_magnitude_ticks", "stop_run_volume_ratio",
    "distance_to_vwap_ticks", "price_vs_vwap_sd",
    "va_width_ticks", "va_width_vs_yesterday",
    "single_print_count_above", "single_print_count_below",
    "num_levels_within_20_ticks",
    "rotation_factor", "aspr",
    "session_volume_total", "session_volume_acceleration",
    "news_event_minutes_away", "news_event_importance",
    "unfinished_auction_count_above", "unfinished_auction_count_below",
]

# Categorical features encoded as integers
SETUP_TYPE_MAP = {
    "spring": 0, "sfp": 1, "poor_extreme": 2, "ib_break": 3,
    "rule_80": 4, "fakeout": 5, "break_from_balance": 6,
    "double_distribution": 7, "news_directional": 8,
}
DIRECTION_MAP = {"long": 0, "short": 1}
MARKET_TYPE_MAP = {"balanced": 0, "trending_up": 1, "trending_down": 2, "unknown": 3}
OPENING_TYPE_MAP = {"OD": 0, "OTD": 1, "ORR": 2, "OA": 3, "unknown": 4}


class SetupScorerModel:
    def train(self, data) -> dict | None:
        try:
            import lightgbm as lgb  # noqa: F401
        except ImportError:
            logger.warning("lightgbm not installed")
            return None

        use_phase2 = len(data) >= 500
        feature_names = FEATURE_NAMES_PHASE2 if use_phase2 else FEATURE_NAMES_PHASE1

        X, y = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            vec = _encode_features(features, feature_names)
            if vec is not None and row.outcome is not None:
                X.append(vec)
                y.append(row.outcome)

        X = np.array(X, dtype=np.float32)
        y = np.array(y, dtype=np.float32)

        if len(X) < MIN_SAMPLES:
            return None

        from src.ml.optimizer.trainer import train_model
        result = train_model(X, y, task="regression", min_samples=MIN_SAMPLES)
        if result is None:
            return None

        import joblib
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "setup_scorer_latest.joblib"
        joblib.dump({
            "model": result["model"],
            "feature_names": feature_names,
            "task": "regression",
        }, path)

        return {
            "file_path": str(path),
            "training_data_count": len(X),
            "validation_score": result["validation_score"],
            "baseline_metric": None,
        }

    def predict(self, features: dict) -> float | None:
        """Predict R-multiple for a trading setup."""
        use_phase2 = len(features) > 20
        feature_names = FEATURE_NAMES_PHASE2 if use_phase2 else FEATURE_NAMES_PHASE1
        vec = _encode_features(features, feature_names)
        if vec is None:
            return None
        return vec  # Actual prediction done by Predictor


def _encode_features(features: dict, feature_names: list) -> np.ndarray | None:
    """Encode feature dict to numeric array, handling categoricals."""
    vec = []
    for name in feature_names:
        val = features.get(name)
        if name == "setup_type":
            val = SETUP_TYPE_MAP.get(val, -1)
        elif name == "direction":
            val = DIRECTION_MAP.get(val, -1)
        elif name == "market_type":
            val = MARKET_TYPE_MAP.get(val, 3)
        elif name == "opening_type":
            val = OPENING_TYPE_MAP.get(val, 4)
        elif isinstance(val, bool):
            val = int(val)
        vec.append(float(val) if val is not None else 0.0)
    return np.array(vec, dtype=np.float32)
