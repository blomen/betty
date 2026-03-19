"""DQN neural network for the RL trading agent."""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from torch import Tensor

from src.rl.config import HIDDEN_LAYERS, NUM_ACTIONS


class DQNetwork(nn.Module):
    """Deep Q-Network: maps observations to Q-values for each action.

    Architecture: input → 128 (ReLU) → 128 (ReLU) → 64 (ReLU) → NUM_ACTIONS
    """

    def __init__(self, input_dim: int) -> None:
        super().__init__()
        h1, h2, h3 = HIDDEN_LAYERS  # [128, 128, 64]
        self.net = nn.Sequential(
            nn.Linear(input_dim, h1),
            nn.ReLU(),
            nn.Linear(h1, h2),
            nn.ReLU(),
            nn.Linear(h2, h3),
            nn.ReLU(),
            nn.Linear(h3, NUM_ACTIONS),
        )

    def forward(self, x: Tensor) -> Tensor:
        """Return Q-values for each action.

        Args:
            x: Tensor of shape (batch, input_dim) or (input_dim,).

        Returns:
            Tensor of shape (batch, NUM_ACTIONS).
        """
        return self.net(x)

    def predict(self, observation: np.ndarray) -> np.ndarray:
        """Convenience method: numpy observation → numpy Q-values (no gradient).

        Args:
            observation: 1-D float array of shape (input_dim,) or
                         2-D array of shape (batch, input_dim).

        Returns:
            numpy array of shape (1, NUM_ACTIONS) for a 1-D input, or
            (batch, NUM_ACTIONS) for a 2-D input.
        """
        obs = np.asarray(observation, dtype=np.float32)
        squeezed = obs.ndim == 1
        if squeezed:
            obs = obs[np.newaxis, :]  # (1, input_dim)
        tensor = torch.from_numpy(obs)
        with torch.no_grad():
            q_values = self.forward(tensor)
        return q_values.numpy()
