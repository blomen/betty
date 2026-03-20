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

    def forward_with_activations(self, x: Tensor) -> dict[str, Tensor]:
        """Forward pass capturing all intermediate activations."""
        if x.ndim == 1:
            x = x.unsqueeze(0)
        inputs = x
        layer1 = self.net[1](self.net[0](x))
        layer2 = self.net[3](self.net[2](layer1))
        layer3 = self.net[5](self.net[4](layer2))
        q_values = self.net[6](layer3)
        return {
            "inputs": inputs,
            "layer1": layer1,
            "layer2": layer2,
            "layer3": layer3,
            "q_values": q_values,
        }

    @torch.no_grad()
    def extract_top_connections(
        self, activations: dict[str, Tensor], top_n: int = 100
    ) -> dict[str, list[dict]]:
        """Extract strongest connections per layer transition.

        Signal strength = |weight[j, i] * activation[i]| for each connection.
        Returns top_n connections per transition, sorted by strength descending.
        """
        layers = [
            ("input_l1", activations["inputs"], self.net[0]),
            ("l1_l2",    activations["layer1"], self.net[2]),
            ("l2_l3",    activations["layer2"], self.net[4]),
            ("l3_output", activations["layer3"], self.net[6]),
        ]
        result = {}
        for name, act, linear in layers:
            act_1d = act[0]
            w = linear.weight
            signal = (w * act_1d.unsqueeze(0)).abs()
            flat = signal.flatten()
            k = min(top_n, flat.numel())
            top_vals, top_idxs = flat.topk(k)
            conns = []
            for val, idx in zip(top_vals.tolist(), top_idxs.tolist()):
                j = idx // w.shape[1]
                i = idx % w.shape[1]
                conns.append({
                    "from_idx": i,
                    "to_idx": j,
                    "strength": round(val, 4),
                    "sign": 1 if w[j, i].item() >= 0 else -1,
                })
            result[name] = conns
        return result

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
