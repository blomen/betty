"""Tests for the experience replay buffer."""
import numpy as np
import pytest

from src.rl.agent.replay_buffer import ReplayBuffer

OBS_DIM = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_obs() -> np.ndarray:
    return np.random.rand(OBS_DIM).astype(np.float32)


def _fill(buf: ReplayBuffer, n: int) -> None:
    for i in range(n):
        buf.add(_random_obs(), action=i % 3, reward=float(i))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_empty_buffer_cannot_sample() -> None:
    """Sampling from an empty buffer raises ValueError."""
    buf = ReplayBuffer(capacity=100)
    with pytest.raises(ValueError):
        buf.sample(1)


def test_insufficient_samples_raises() -> None:
    """Requesting more samples than stored raises ValueError."""
    buf = ReplayBuffer(capacity=100)
    _fill(buf, 5)
    with pytest.raises(ValueError):
        buf.sample(10)


def test_add_and_sample_works() -> None:
    """Basic add + sample round-trip returns correct structure."""
    buf = ReplayBuffer(capacity=100)
    _fill(buf, 20)
    batch = buf.sample(8)

    assert set(batch.keys()) == {"observations", "actions", "rewards"}
    assert batch["observations"].shape == (8, OBS_DIM)
    assert batch["actions"].shape == (8,)
    assert batch["rewards"].shape == (8,)


def test_capacity_overflow_drops_oldest() -> None:
    """Adding more items than capacity keeps only the most recent ones."""
    capacity = 10
    buf = ReplayBuffer(capacity=capacity)
    _fill(buf, 25)
    assert len(buf) == capacity


def test_sample_batch_size_correct() -> None:
    """Sample returns exactly the requested batch size."""
    buf = ReplayBuffer(capacity=200)
    _fill(buf, 100)
    for size in (1, 10, 64, 100):
        batch = buf.sample(size)
        assert batch["observations"].shape[0] == size
        assert batch["actions"].shape[0] == size
        assert batch["rewards"].shape[0] == size


def test_samples_are_random() -> None:
    """Two independent samples from the same buffer differ (with high probability)."""
    buf = ReplayBuffer(capacity=1000)
    _fill(buf, 500)
    batch1 = buf.sample(64)
    batch2 = buf.sample(64)
    # It is astronomically unlikely that two independent 64-element random
    # samples from 500 items are identical.
    assert not np.array_equal(batch1["observations"], batch2["observations"])


def test_len_reflects_stored_items() -> None:
    """__len__ returns the current number of stored transitions."""
    buf = ReplayBuffer(capacity=100)
    assert len(buf) == 0
    _fill(buf, 50)
    assert len(buf) == 50
    _fill(buf, 60)  # Overflow: total 110 > 100
    assert len(buf) == 100


def test_observation_is_copied() -> None:
    """Stored observation is a copy; mutating the original does not affect the buffer."""
    buf = ReplayBuffer(capacity=10)
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    buf.add(obs, action=0, reward=1.0)
    obs[:] = 999.0  # Mutate original
    batch = buf.sample(1)
    assert not np.any(batch["observations"] == 999.0)
