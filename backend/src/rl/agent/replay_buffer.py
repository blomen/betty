"""Prioritized Experience Replay buffer for the DQN trading agent.

Uses a SumTree for O(log n) proportional sampling and O(log n) priority updates.
Falls back to uniform sampling when all priorities are zero.
"""
from __future__ import annotations

import random

import numpy as np


class _SumTree:
    """Binary tree where each parent is the sum of its children.

    Leaf nodes store transition priorities. Internal nodes store partial sums
    for O(log n) proportional sampling.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._tree = np.zeros(2 * capacity - 1, dtype=np.float64)
        self._write_idx = 0

    def add(self, priority: float) -> int:
        """Add a leaf with the given priority. Returns the leaf index."""
        leaf_idx = self._write_idx
        tree_idx = leaf_idx + self.capacity - 1
        self._update_tree(tree_idx, priority)
        self._write_idx = (self._write_idx + 1) % self.capacity
        return leaf_idx

    def update(self, leaf_idx: int, priority: float) -> None:
        """Update the priority of an existing leaf."""
        tree_idx = leaf_idx + self.capacity - 1
        self._update_tree(tree_idx, priority)

    def sample(self, value: float) -> int:
        """Sample a leaf index proportional to priorities using a random value in [0, total)."""
        idx = 0  # root
        while idx < self.capacity - 1:
            left = 2 * idx + 1
            right = left + 1
            if value <= self._tree[left]:
                idx = left
            else:
                value -= self._tree[left]
                idx = right
        return idx - (self.capacity - 1)

    @property
    def total(self) -> float:
        return float(self._tree[0])

    def priority(self, leaf_idx: int) -> float:
        return float(self._tree[leaf_idx + self.capacity - 1])

    def _update_tree(self, tree_idx: int, priority: float) -> None:
        delta = priority - self._tree[tree_idx]
        self._tree[tree_idx] = priority
        while tree_idx > 0:
            tree_idx = (tree_idx - 1) // 2
            self._tree[tree_idx] += delta


class ReplayBuffer:
    """Prioritized Experience Replay buffer.

    Stores (observation, action, reward) transitions with TD-error-based
    priorities. Supports proportional sampling for mini-batch training,
    with importance-sampling weights for bias correction.

    Args:
        capacity: Maximum number of transitions to store.
        alpha: Priority exponent (0 = uniform, 1 = full prioritisation).
        beta_start: Initial importance-sampling exponent (annealed to 1.0).
        beta_frames: Number of samples over which beta is annealed to 1.0.
        epsilon: Small constant added to TD errors to prevent zero priority.
    """

    def __init__(
        self,
        capacity: int,
        alpha: float = 0.6,
        beta_start: float = 0.4,
        beta_frames: int = 100_000,
        epsilon: float = 1e-5,
    ) -> None:
        self._capacity = capacity
        self._alpha = alpha
        self._beta_start = beta_start
        self._beta_frames = beta_frames
        self._epsilon = epsilon
        self._sample_count = 0

        self._tree = _SumTree(capacity)

        # Data storage (pre-allocated lazily on first add)
        self._observations: list[np.ndarray | None] = [None] * capacity
        self._actions: np.ndarray = np.zeros(capacity, dtype=np.int64)
        self._rewards: np.ndarray = np.zeros(capacity, dtype=np.float32)
        self._stop_targets: np.ndarray = np.zeros(capacity, dtype=np.float32)
        self._size = 0
        self._write_idx = 0
        self._max_priority: float = 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, observation: np.ndarray, action: int, reward: float, stop_target: float = 10.0) -> None:
        """Store a transition with max priority (will be corrected on first sample)."""
        idx = self._write_idx
        self._observations[idx] = np.array(observation, dtype=np.float32)
        self._actions[idx] = int(action)
        self._rewards[idx] = float(reward)
        self._stop_targets[idx] = float(stop_target)

        # New transitions get max priority so they're sampled at least once
        priority = self._max_priority ** self._alpha
        self._tree.add(priority)

        self._write_idx = (self._write_idx + 1) % self._capacity
        self._size = min(self._size + 1, self._capacity)

    def sample(self, batch_size: int) -> dict[str, np.ndarray]:
        """Sample a prioritized mini-batch with importance-sampling weights.

        Returns:
            dict with keys:
                - "observations": float32 array (batch_size, obs_dim)
                - "actions":      int64 array  (batch_size,)
                - "rewards":      float32 array (batch_size,)
                - "weights":      float32 array (batch_size,) — IS weights
                - "indices":      int64 array  (batch_size,) — leaf indices for priority update

        Raises:
            ValueError: if buffer contains fewer than batch_size items.
        """
        if self._size < batch_size:
            raise ValueError(
                f"Not enough samples: requested {batch_size}, "
                f"buffer has {self._size}."
            )

        indices = np.empty(batch_size, dtype=np.int64)
        priorities = np.empty(batch_size, dtype=np.float64)

        total = self._tree.total
        if total <= 0:
            # Fallback to uniform if all priorities are zero
            indices = np.array(random.sample(range(self._size), batch_size), dtype=np.int64)
            priorities[:] = 1.0 / self._size
        else:
            segment = total / batch_size
            for i in range(batch_size):
                lo = segment * i
                hi = segment * (i + 1)
                value = random.uniform(lo, hi)
                leaf_idx = self._tree.sample(value)
                # Guard against sampling beyond current size
                leaf_idx = min(leaf_idx, self._size - 1)
                indices[i] = leaf_idx
                priorities[i] = max(self._tree.priority(leaf_idx), self._epsilon)

        # Importance-sampling weights
        beta = min(1.0, self._beta_start + self._sample_count * (1.0 - self._beta_start) / max(self._beta_frames, 1))
        self._sample_count += batch_size

        probs = priorities / max(total, self._epsilon)
        weights = (self._size * probs) ** (-beta)
        weights /= max(weights.max(), self._epsilon)  # Normalise so max weight = 1

        observations = np.stack([self._observations[i] for i in indices])

        return {
            "observations": observations,
            "actions": self._actions[indices].copy(),
            "rewards": self._rewards[indices].copy(),
            "stop_targets": self._stop_targets[indices].copy(),
            "weights": weights.astype(np.float32),
            "indices": indices,
        }

    def update_priorities(self, indices: np.ndarray, td_errors: np.ndarray) -> None:
        """Update priorities based on TD errors from the last training step."""
        for idx, td_err in zip(indices, td_errors):
            priority = (abs(float(td_err)) + self._epsilon) ** self._alpha
            self._tree.update(int(idx), priority)
            self._max_priority = max(self._max_priority, abs(float(td_err)) + self._epsilon)

    @property
    def size(self) -> int:
        return self._size

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return self._size
