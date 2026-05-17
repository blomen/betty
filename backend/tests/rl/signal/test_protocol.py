import numpy as np
import pytest

from src.rl.signal.protocol import ModelProtocol
from src.rl.signal.types import MultiTaskOutputs, Signal


def test_modelprotocol_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        ModelProtocol()  # type: ignore


def test_modelprotocol_subclass_must_implement_predict_raw():
    class IncompleteModel(ModelProtocol):
        pass

    with pytest.raises(TypeError):
        IncompleteModel()  # type: ignore


def test_minimal_modelprotocol_subclass_works():
    class StubModel(ModelProtocol):
        def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
            return MultiTaskOutputs(
                direction_logits=[0.6, 0.3, 0.1],
                magnitude_R=1.0,
                win_probability=0.65,
                duration_bars=5.0,
                uncertainty=0.1,
            )

    m = StubModel()
    obs = np.zeros(313, dtype=np.float32)
    raw = m.predict_raw(obs)
    assert isinstance(raw, MultiTaskOutputs)


def test_modelprotocol_predict_composes_raw_calibration_and_signal_packaging():
    """predict() = predict_raw() → calibrate() → Signal(...). Default
    calibrate is identity if no calibrator attached."""

    class StubModel(ModelProtocol):
        def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
            return MultiTaskOutputs(
                direction_logits=[0.5, 0.4, 0.1],
                magnitude_R=2.0,
                win_probability=0.7,
                duration_bars=8.0,
                uncertainty=0.05,
            )

    m = StubModel()
    obs = np.zeros(313, dtype=np.float32)
    sig = m.predict(obs, zone_id=42, timestamp=123.0)
    assert isinstance(sig, Signal)
    assert sig.p_cont == 0.5
    assert sig.p_rev == 0.4
    assert sig.p_skip == 0.1
    assert sig.expected_R == 2.0
    assert sig.win_probability == 0.7
    assert sig.zone_id == 42
    assert sig.timestamp == 123.0
