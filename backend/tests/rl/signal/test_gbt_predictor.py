from pathlib import Path

import numpy as np
import pytest

from src.rl.signal.gbt_predictor import GBTPredictor
from src.rl.signal.types import MultiTaskOutputs, Signal


@pytest.fixture(scope="module")
def gbt_model_path():
    p = Path("/app/data/rl/models/trigger_gbt_v5.joblib")
    if not p.exists():
        pytest.skip(f"No GBT model at {p}")
    return p


def test_gbt_predictor_implements_modelprotocol(gbt_model_path):
    pred = GBTPredictor.load(gbt_model_path)
    assert hasattr(pred, "predict_raw")
    assert hasattr(pred, "predict")


def test_gbt_predictor_predict_raw_returns_multitask_outputs(gbt_model_path):
    pred = GBTPredictor.load(gbt_model_path)
    obs = np.random.randn(pred.trigger_obs_dim).astype(np.float32)
    raw = pred.predict_raw(obs)
    assert isinstance(raw, MultiTaskOutputs)
    assert len(raw.direction_logits) == 3
    assert all(0.0 <= p <= 1.0 for p in raw.direction_logits)


def test_gbt_predictor_predict_returns_signal_with_correct_action(gbt_model_path):
    pred = GBTPredictor.load(gbt_model_path)
    obs = np.zeros(pred.trigger_obs_dim, dtype=np.float32)
    sig = pred.predict(obs, zone_id=1, timestamp=100.0)
    assert isinstance(sig, Signal)
    assert sig.action in ("CONTINUATION", "REVERSAL", "SKIP")
    assert sig.zone_id == 1
    assert sig.timestamp == 100.0


def test_gbt_predictor_with_calibrator_changes_probs(gbt_model_path):
    from src.rl.signal.calibration import IsotonicCalibrator

    pred = GBTPredictor.load(gbt_model_path)
    cal = IsotonicCalibrator()
    # Fit a heavily biased calibrator: maps everything to ~1.0 for class 0
    cal.fit_per_class(
        class_idx=0,
        raw_probs=np.linspace(0, 1, 100),
        true_outcomes=np.ones(100),
    )
    obs = np.zeros(pred.trigger_obs_dim, dtype=np.float32)

    sig_no_cal = pred.predict(obs, zone_id=1, timestamp=1.0)
    pred.attach_calibrator(cal)
    sig_with_cal = pred.predict(obs, zone_id=1, timestamp=1.0)

    assert sig_no_cal.p_cont != sig_with_cal.p_cont
