import numpy as np
from unittest.mock import patch
from src.rl.live_inference import DQNLiveInference


def test_infer_returns_none_when_no_model():
    """Without a model file, infer() returns None."""
    service = DQNLiveInference()
    assert not service.is_loaded
    assert service.infer({}) is None


def test_infer_returns_full_payload():
    """With a model loaded, infer() returns complete payload."""
    service = DQNLiveInference()
    from src.rl.agent.network import DQNetwork
    service._network = DQNetwork(input_dim=107)
    service._loaded = True

    state = {
        "level_type": "vwap",
        "price": 24500.0,
        "candles": [],
        "vwap_bands": None,
        "volume_profile": None,
        "tpo_profile": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": None,
    }
    result = service.infer(state)

    assert result is not None
    assert len(result["inputs"]) == 107
    assert len(result["activations"]["layer1"]) == 128
    assert len(result["activations"]["layer2"]) == 128
    assert len(result["activations"]["layer3"]) == 64
    assert len(result["q_values"]) == 3
    assert result["action"] in ("LONG", "SHORT", "SKIP")
    assert set(result["connections"].keys()) == {"input_l1", "l1_l2", "l2_l3", "l3_output"}
