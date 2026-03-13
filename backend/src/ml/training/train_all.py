"""Weekly training orchestrator for all ML models.

Checks data thresholds, trains models that have sufficient data,
evaluates against baseline, and registers to ml_model_registry.
"""
import logging
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MODEL_CONFIGS = {
    "edge_quality": {
        "min_samples": 200, "domain": "betting",
        "source_type": "opportunity", "task": "classification",
    },
    "limit_predictor": {
        "min_samples": 20, "domain": "betting",
        "source_type": "limit_event", "task": "classification",
    },
    "devig_selector": {
        "min_samples": 500, "domain": "betting",
        "source_type": "devig_comparison", "task": "multiclass",
    },
    "boost_calibrator": {
        "min_samples": 100, "domain": "betting",
        "source_type": "boost", "task": "calibration",
    },
    "adaptive_kelly": {
        "min_samples": 300, "domain": "betting",
        "source_type": "bet_outcome", "task": "regression",
    },
    # Trading models (M5-M7, M9)
    "setup_scorer": {
        "min_samples": 200, "domain": "trading",
        "source_type": "trading_signal", "task": "regression",
    },
    "temporal_pattern": {
        "min_samples": 500, "domain": "trading",
        "source_type": "trading_signal", "task": "classification",
    },
    "gate_classifier": {
        "min_samples": 100, "domain": "trading",
        "source_type": "market_session", "task": "multiclass",
    },
    "macro_engine": {
        "min_samples": 50, "domain": "trading",
        "source_type": "news_event", "task": "regression",
    },
}

MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class TrainingOrchestrator:
    def __init__(self):
        self.model_configs = MODEL_CONFIGS

    def check_thresholds(self, session: Session) -> dict[str, bool]:
        from src.ml.feature_store import get_training_data
        ready = {}
        for name, config in self.model_configs.items():
            data = get_training_data(session, config["domain"], config["source_type"])
            ready[name] = len(data) >= config["min_samples"]
        return ready

    def train_model(self, session: Session, model_name: str) -> dict | None:
        config = self.model_configs.get(model_name)
        if not config:
            return None
        from src.ml.feature_store import get_training_data
        data = get_training_data(session, config["domain"], config["source_type"])
        if len(data) < config["min_samples"]:
            return None
        trainer_fn = _get_trainer(model_name)
        if trainer_fn is None:
            return None
        return trainer_fn(data, session)

    def train_all(self, session: Session) -> dict[str, str]:
        results = {}
        ready = self.check_thresholds(session)
        for name, is_ready in ready.items():
            if not is_ready:
                results[name] = "insufficient_data"
                continue
            try:
                result = self.train_model(session, name)
                if result:
                    self._register_model(session, name, result)
                    results[name] = "trained"
                else:
                    results[name] = "train_failed"
            except Exception as e:
                logger.error(f"Training {name} failed: {e}")
                results[name] = f"error: {e}"
        return results

    def _register_model(self, session: Session, model_name: str, result: dict) -> None:
        from src.db.models import MlModelRegistry
        session.query(MlModelRegistry).filter_by(
            model_name=model_name, is_active=1
        ).update({"is_active": 0})
        last = (
            session.query(MlModelRegistry)
            .filter_by(model_name=model_name)
            .order_by(MlModelRegistry.version.desc())
            .first()
        )
        version = (last.version + 1) if last else 1
        entry = MlModelRegistry(
            model_name=model_name, version=version,
            file_path=result.get("file_path", ""),
            training_data_count=result.get("training_data_count", 0),
            validation_metric=result.get("validation_score"),
            baseline_metric=result.get("baseline_metric"),
            is_active=1,
        )
        session.add(entry)
        session.flush()


def _get_trainer(model_name: str):
    trainers = {
        "edge_quality": lambda data, s: _train_edge_quality(data, s),
        "limit_predictor": lambda data, s: _train_limit_predictor(data, s),
        "devig_selector": lambda data, s: _train_devig_selector(data, s),
        "boost_calibrator": lambda data, s: _train_boost_calibrator(data, s),
        "adaptive_kelly": lambda data, s: _train_adaptive_kelly(data, s),
        "setup_scorer": lambda data, s: _train_setup_scorer(data, s),
        "temporal_pattern": lambda data, s: _train_temporal_pattern(data, s),
        "gate_classifier": lambda data, s: _train_gate_classifier(data, s),
        "macro_engine": lambda data, s: _train_macro_engine(data, s),
    }
    return trainers.get(model_name)


def _train_edge_quality(data, session):
    from src.ml.models.edge_quality import EdgeQualityModel
    return EdgeQualityModel().train(data)


def _train_limit_predictor(data, session):
    from src.ml.models.limit_predictor import LimitPredictorModel
    return LimitPredictorModel().train(data)


def _train_devig_selector(data, session):
    from src.ml.models.devig_selector import DevigSelectorModel
    return DevigSelectorModel().train(data)


def _train_boost_calibrator(data, session):
    from src.ml.models.boost_calibrator import BoostCalibratorModel
    return BoostCalibratorModel().train(data)


def _train_adaptive_kelly(data, session):
    from src.ml.models.adaptive_kelly import AdaptiveKellyModel
    return AdaptiveKellyModel().train(data)


def _train_setup_scorer(data, session):
    from src.ml.models.setup_scorer import SetupScorerModel
    return SetupScorerModel().train(data)


def _train_temporal_pattern(data, session):
    from src.ml.models.temporal_pattern import TemporalPatternModel
    return TemporalPatternModel().train(data)


def _train_gate_classifier(data, session):
    from src.ml.models.gate_classifier import GateClassifierModel
    return GateClassifierModel().train(data)


def _train_macro_engine(data, session):
    from src.ml.models.macro_engine import MacroEngineModel
    return MacroEngineModel().train(data)
