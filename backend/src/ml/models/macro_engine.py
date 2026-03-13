"""M9: Macro & News Context Engine -- multi-component macro regime model.

Components:
1. News impact predictor -- LightGBM regression: given event type + surprise -> predicted NQ impact
2. Macro regime enhancer -- enriches MacroSnapshot with ML-learned regime signals
3. Options flow integration -- stores daily options/macro data to options_flow table

This model enhances the existing rule-based classify_regime() in macro_provider.py.
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 50
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

NEWS_IMPACT_FEATURES = [
    "event_type_encoded", "importance", "surprise",
    "vix_at_event", "delta_1m_after", "volume_1m_after",
]

MACRO_REGIME_FEATURES = [
    "vix_level", "vix_change_1d", "vix_term_structure_encoded",
    "dxy_level", "dxy_change_1d", "us10y_level", "us10y_change_1d",
    "yield_curve_spread", "gex", "net_options_delta", "put_call_ratio",
    "es_nq_ratio_change", "cot_net_position", "cot_change_1w",
]


class MacroEngineModel:
    def train(self, data) -> dict | None:
        """Train news impact predictor on historical event data."""
        try:
            import lightgbm as lgb  # noqa: F401
        except ImportError:
            logger.warning("lightgbm not installed")
            return None

        X, y = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            vec = [float(features.get(f, 0) or 0) for f in NEWS_IMPACT_FEATURES]
            if row.outcome is not None:
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
        path = MODELS_DIR / "macro_engine_latest.joblib"
        joblib.dump({
            "model": result["model"],
            "feature_names": NEWS_IMPACT_FEATURES,
            "task": "regression",
        }, path)

        return {
            "file_path": str(path),
            "training_data_count": len(X),
            "validation_score": result["validation_score"],
            "baseline_metric": None,
        }

    def predict_news_impact(self, features: dict) -> float | None:
        """Predict NQ price impact of an economic event."""
        vec = np.array(
            [float(features.get(f, 0) or 0) for f in NEWS_IMPACT_FEATURES],
            dtype=np.float32,
        ).reshape(1, -1)
        return vec  # Actual prediction done by Predictor


async def store_daily_options_flow(session, macro_snapshot) -> None:
    """Store daily options/macro data to options_flow table.

    Called once per day after macro fetch.
    """
    from src.db.models import OptionsFlow
    from datetime import date

    today = date.today().isoformat()
    existing = session.query(OptionsFlow).filter_by(date=today, symbol="NQ").first()
    if existing:
        return

    row = OptionsFlow(
        date=today,
        symbol="NQ",
        vix_level=macro_snapshot.vix,
        vix_1d_change=macro_snapshot.vix_change_pct,
        dxy_level=macro_snapshot.dxy,
        dxy_1d_change=macro_snapshot.dxy_change_pct,
        us10y_level=macro_snapshot.us10y,
        us10y_1d_change=macro_snapshot.us10y_change_bps,
        us02y_level=macro_snapshot.us2y,
        yield_curve_spread=macro_snapshot.yield_curve_spread,
    )
    session.add(row)
    session.flush()
