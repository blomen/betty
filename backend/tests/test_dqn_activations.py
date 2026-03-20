"""Tests for DQNetwork activation capture and top connection extraction."""
import numpy as np
import torch
from src.rl.agent.network import DQNetwork
from src.rl.config import NUM_ACTIONS


def test_forward_with_activations_shapes():
    """Verify activation capture returns correct shapes for each layer."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    result = net.forward_with_activations(obs)

    assert result["inputs"].shape == (1, 107)
    assert result["layer1"].shape == (1, 128)
    assert result["layer2"].shape == (1, 128)
    assert result["layer3"].shape == (1, 64)
    assert result["q_values"].shape == (1, NUM_ACTIONS)


def test_forward_with_activations_matches_forward():
    """Q-values from activation capture must match normal forward pass."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    normal_q = net.forward(obs)
    result = net.forward_with_activations(obs)
    torch.testing.assert_close(result["q_values"], normal_q)


def test_activations_are_non_negative():
    """Post-ReLU activations should be >= 0."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    result = net.forward_with_activations(obs)
    assert (result["layer1"] >= 0).all()
    assert (result["layer2"] >= 0).all()
    assert (result["layer3"] >= 0).all()
