import numpy as np
import torch

from src.rl.signal.ft_predictor import FTTransformerNet, FTTransformerPredictor
from src.rl.signal.types import MultiTaskOutputs, Signal


def test_ft_transformer_net_forward_shape():
    """End-to-end forward: 313-dim obs → multi-task outputs."""
    from src.rl.features.observation_index import _CATEGORY_SEGMENTS

    net = FTTransformerNet(category_segments=_CATEGORY_SEGMENTS)
    obs = torch.randn(2, 313)
    out = net(obs)
    assert out["direction_logits"].shape == (2, 3)
    assert out["magnitude_R"].shape == (2,)
    assert out["win_probability"].shape == (2,)
    assert out["duration_bars"].shape == (2,)


def test_ft_transformer_predictor_implements_modelprotocol():
    pred = FTTransformerPredictor()
    obs = np.random.randn(313).astype(np.float32)
    raw = pred.predict_raw(obs)
    assert isinstance(raw, MultiTaskOutputs)


def test_ft_transformer_predictor_returns_valid_signal():
    pred = FTTransformerPredictor()
    obs = np.zeros(313, dtype=np.float32)
    sig = pred.predict(obs, zone_id=42, timestamp=99.0)
    assert isinstance(sig, Signal)
    assert sig.zone_id == 42
    assert sig.action in ("CONTINUATION", "REVERSAL", "SKIP")


def test_ft_transformer_save_and_load(tmp_path):
    pred = FTTransformerPredictor()
    obs = np.zeros(313, dtype=np.float32)
    sig1 = pred.predict(obs, zone_id=1, timestamp=1.0)

    path = tmp_path / "ft.pt"
    pred.save(path)

    pred2 = FTTransformerPredictor.load(path)
    sig2 = pred2.predict(obs, zone_id=1, timestamp=1.0)
    assert sig1.p_cont == sig2.p_cont
