"""Tests for the DQN neural network."""
import numpy as np
import pytest
import torch

from src.rl.config import NUM_ACTIONS
from src.rl.agent.network import DQNetwork

# Arbitrary observation dimension used throughout the tests
INPUT_DIM = 40


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def net() -> DQNetwork:
    model = DQNetwork(INPUT_DIM)
    model.eval()
    return model


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_single_input_output_shape(net: DQNetwork) -> None:
    """Single observation produces (1, NUM_ACTIONS) output."""
    x = torch.zeros(1, INPUT_DIM)
    out = net(x)
    assert out.shape == (1, NUM_ACTIONS)


def test_batch_output_shape(net: DQNetwork) -> None:
    """Batch of 64 observations produces (64, NUM_ACTIONS) output."""
    x = torch.zeros(64, INPUT_DIM)
    out = net(x)
    assert out.shape == (64, NUM_ACTIONS)


def test_deterministic_in_eval_mode(net: DQNetwork) -> None:
    """Two forward passes with the same input return identical results in eval mode."""
    x = torch.randn(1, INPUT_DIM)
    out1 = net(x)
    out2 = net(x)
    assert torch.allclose(out1, out2)


def test_parameter_count_in_range() -> None:
    """Network has between 20k and 50k trainable parameters."""
    model = DQNetwork(INPUT_DIM)
    total = sum(p.numel() for p in model.parameters() if p.requires_grad)
    assert 20_000 <= total <= 50_000, f"Parameter count {total} out of range [20k, 50k]"


def test_predict_from_numpy(net: DQNetwork) -> None:
    """predict() accepts a numpy array and returns a numpy array of correct shape."""
    obs = np.random.rand(INPUT_DIM).astype(np.float32)
    result = net.predict(obs)
    assert isinstance(result, np.ndarray)
    assert result.shape == (1, NUM_ACTIONS)


def test_predict_no_gradient(net: DQNetwork) -> None:
    """predict() runs without building a computation graph."""
    obs = np.random.rand(INPUT_DIM).astype(np.float32)
    result = net.predict(obs)
    # Result should be plain numpy with no grad info
    assert not isinstance(result, torch.Tensor)
