"""Experience replay buffer for the DQN trading agent."""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class _Transition:
    observation: np.ndarray
    action: int
    reward: float


class ReplayBuffer:
    """Fixed-capacity circular replay buffer.

    Stores (observation, action, reward) transitions and supports random
    sampling for mini-batch training.  Oldest transitions are silently
    dropped once capacity is reached.
    """

    def __init__(self, capacity: int) -> None:
        self._buffer: deque[_Transition] = deque(maxlen=capacity)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, observation: np.ndarray, action: int, reward: float) -> None:
        """Store a single transition.

        A copy of *observation* is made so that the caller is free to reuse
        the underlying array.
        """
        self._buffer.append(
            _Transition(
                observation=np.array(observation, dtype=np.float32),
                action=int(action),
                reward=float(reward),
            )
        )

    def sample(self, batch_size: int) -> dict[str, np.ndarray]:
        """Draw *batch_size* transitions without replacement.

        Returns:
            dict with keys:
                - "observations": float32 array (batch_size, obs_dim)
                - "actions":      int64 array  (batch_size,)
                - "rewards":      float32 array (batch_size,)

        Raises:
            ValueError: if the buffer contains fewer than *batch_size* items.
        """
        if len(self._buffer) < batch_size:
            raise ValueError(
                f"Not enough samples: requested {batch_size}, "
                f"buffer has {len(self._buffer)}."
            )
        transitions = random.sample(self._buffer, batch_size)
        return {
            "observations": np.stack([t.observation for t in transitions]),
            "actions": np.array([t.action for t in transitions], dtype=np.int64),
            "rewards": np.array([t.reward for t in transitions], dtype=np.float32),
        }

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._buffer)
