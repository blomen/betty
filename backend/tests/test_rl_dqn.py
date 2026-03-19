"""Tests for DQNAgent — network, epsilon-greedy, training, persistence."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.rl.agent.dqn import DQNAgent
from src.rl.config import BATCH_SIZE, EPSILON_END, EPSILON_START, NUM_ACTIONS

OBS_DIM = 16  # arbitrary small observation dimension for fast tests


def _make_obs() -> np.ndarray:
    return np.random.rand(OBS_DIM).astype(np.float32)


def _fill_buffer(agent: DQNAgent, n: int = BATCH_SIZE) -> None:
    """Populate the replay buffer with *n* random transitions."""
    for _ in range(n):
        obs = _make_obs()
        action = np.random.randint(NUM_ACTIONS)
        reward = float(np.random.randn())
        agent.store(obs, action, reward)


# ---------------------------------------------------------------------------
# 1. Random actions at epsilon=1.0
# ---------------------------------------------------------------------------

def test_random_actions_at_full_epsilon() -> None:
    """With epsilon=1.0 the agent should explore: we expect multiple distinct
    actions across a reasonable number of selections."""
    agent = DQNAgent(OBS_DIM, epsilon=1.0)
    obs = _make_obs()

    actions = {agent.select_action(obs) for _ in range(50)}

    # Probability of seeing only 1 unique action in 50 trials is (1/3)^49 ≈ 0
    assert len(actions) > 1, "Expected multiple distinct actions at epsilon=1.0"


# ---------------------------------------------------------------------------
# 2. Greedy at epsilon=0.0
# ---------------------------------------------------------------------------

def test_greedy_deterministic_at_zero_epsilon() -> None:
    """With epsilon=0.0 the same observation must always produce the same action."""
    agent = DQNAgent(OBS_DIM, epsilon=0.0)
    obs = _make_obs()

    actions = [agent.select_action(obs) for _ in range(20)]

    assert len(set(actions)) == 1, (
        f"Expected a single greedy action but got: {set(actions)}"
    )


# ---------------------------------------------------------------------------
# 3. Training does not diverge
# ---------------------------------------------------------------------------

def test_training_loss_does_not_diverge() -> None:
    """Run several train steps and verify the loss stays finite."""
    agent = DQNAgent(OBS_DIM, epsilon=EPSILON_START)
    _fill_buffer(agent, n=BATCH_SIZE * 4)

    losses = [agent.train_step() for _ in range(20)]

    assert all(np.isfinite(l) for l in losses), (
        f"Loss diverged: {losses}"
    )


# ---------------------------------------------------------------------------
# 4. Epsilon decays after training
# ---------------------------------------------------------------------------

def test_epsilon_decays_after_training() -> None:
    """Each train_step should reduce epsilon (until EPSILON_END is reached)."""
    agent = DQNAgent(OBS_DIM, epsilon=EPSILON_START)
    _fill_buffer(agent, n=BATCH_SIZE * 10)

    epsilon_before = agent.epsilon
    for _ in range(10):
        agent.train_step()
    epsilon_after = agent.epsilon

    assert epsilon_after < epsilon_before, (
        f"Epsilon did not decay: before={epsilon_before}, after={epsilon_after}"
    )


def test_epsilon_floored_at_epsilon_end() -> None:
    """Epsilon must never go below EPSILON_END."""
    # Start near the floor and run many steps
    agent = DQNAgent(OBS_DIM, epsilon=EPSILON_END + 1e-6)
    _fill_buffer(agent, n=BATCH_SIZE * 200)

    for _ in range(200):
        agent.train_step()

    assert agent.epsilon >= EPSILON_END - 1e-9, (
        f"Epsilon went below EPSILON_END: {agent.epsilon}"
    )


# ---------------------------------------------------------------------------
# 5. Save / load produces same Q-values
# ---------------------------------------------------------------------------

def test_save_load_preserves_q_values(tmp_path: Path) -> None:
    """After save → load the Q-values for a fixed observation must be identical."""
    agent = DQNAgent(OBS_DIM, epsilon=0.5)
    _fill_buffer(agent, n=BATCH_SIZE * 2)

    # Run a few train steps so the network has non-random weights
    for _ in range(5):
        agent.train_step()

    obs = _make_obs()
    q_before = agent.q_network.predict(obs).copy()

    checkpoint = tmp_path / "agent.pt"
    agent.save(checkpoint)

    # Create a fresh agent and restore
    agent2 = DQNAgent(OBS_DIM)
    agent2.load(checkpoint)

    q_after = agent2.q_network.predict(obs)

    np.testing.assert_allclose(
        q_before,
        q_after,
        atol=1e-6,
        err_msg="Q-values differ after save/load",
    )
    assert agent2.epsilon == agent.epsilon
    assert agent2.train_steps == agent.train_steps


# ---------------------------------------------------------------------------
# 6. Buffer integration — train_step raises when buffer is empty
# ---------------------------------------------------------------------------

def test_train_step_raises_on_empty_buffer() -> None:
    """train_step must raise ValueError when the buffer has too few samples."""
    agent = DQNAgent(OBS_DIM)  # empty buffer

    with pytest.raises(ValueError, match="Not enough samples"):
        agent.train_step()
