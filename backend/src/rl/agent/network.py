"""Dueling DQN with deeper static architecture.

All signal comes from hand-crafted features (orderflow, micro, setup detection,
TPO, structure, confluence, macro). No raw tick sequence — the orderflow features
already encode absorption, initiative, delta dynamics that a CNN+GRU couldn't
learn from 12 months of data.

Architecture:
  Input (obs_dim) → 256 (LayerNorm, ReLU) → 256 (LayerNorm, ReLU)
                  → 128 (LayerNorm, ReLU) → 64 (ReLU)
  Dueling split:
    Value:     64 → 32 → 1
    Advantage: 64 → 32 → NUM_ACTIONS
    Q = V + (A - mean(A))
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from torch import Tensor

from src.rl.config import NUM_ACTIONS


class DQNetwork(nn.Module):
    """Deeper static Dueling DQN — all features are hand-crafted."""

    def __init__(self, input_dim: int, **_kwargs) -> None:
        super().__init__()
        self._input_dim = input_dim

        # Shared feature extractor — deeper than before (4 layers vs 3)
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.LayerNorm(256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.LayerNorm(256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.LayerNorm(128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
        )

        # Dueling heads
        self.value_stream = nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )
        self.advantage_stream = nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, NUM_ACTIONS),
        )

        # Stop distance head: predicts optimal stop in ticks (8-30 range)
        # Output is raw, sigmoid-scaled to [8, 30] at inference time
        self.stop_head = nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x: Tensor, **_kwargs) -> Tensor:
        """Forward pass: observation → Q-values (stop via forward_full)."""
        features = self.encoder(x)
        return self._dueling(features)

    def forward_full(self, x: Tensor) -> tuple[Tensor, Tensor]:
        """Forward pass returning both Q-values and stop distance.

        Returns:
            q_values: (B, NUM_ACTIONS)
            stop_ticks: (B, 1) — predicted stop distance in ticks [8, 30]
        """
        features = self.encoder(x)
        q = self._dueling(features)
        raw_stop = self.stop_head(features)
        # Scale to [8, 30] ticks via sigmoid
        stop_ticks = 8.0 + 22.0 * torch.sigmoid(raw_stop)
        return q, stop_ticks

    def _dueling(self, features: Tensor) -> Tensor:
        """Dueling: Q = V + (A - mean(A))."""
        value = self.value_stream(features)
        advantage = self.advantage_stream(features)
        return value + advantage - advantage.mean(dim=1, keepdim=True)

    def forward_with_activations(self, x: Tensor, **_kwargs) -> dict[str, Tensor]:
        """Forward pass with intermediate activations for visualization."""
        if x.ndim == 1:
            x = x.unsqueeze(0)

        # Step through encoder layers manually
        h = x
        layer1 = self.encoder[2](self.encoder[1](self.encoder[0](h)))   # 256
        layer2 = self.encoder[5](self.encoder[4](self.encoder[3](layer1)))  # 256
        layer3 = self.encoder[8](self.encoder[7](self.encoder[6](layer2)))  # 128
        features = self.encoder[10](self.encoder[9](layer3))  # 64

        value = self.value_stream(features)
        advantage = self.advantage_stream(features)
        q_values = value + advantage - advantage.mean(dim=1, keepdim=True)

        return {
            "inputs": x,
            "layer1": layer1,
            "layer2": layer2,
            "layer3": layer3,
            "features": features,
            "value": value,
            "advantage": advantage,
            "q_values": q_values,
        }

    @torch.no_grad()
    def extract_top_connections(
        self, activations: dict[str, Tensor], top_n: int = 100,
    ) -> dict[str, list[dict]]:
        """Extract strongest connections per layer for visualization."""
        transitions = [
            ("input_l1", activations["inputs"], self.encoder[0]),
            ("l1_l2", activations["layer1"], self.encoder[3]),
            ("l2_l3", activations["layer2"], self.encoder[6]),
            ("l3_output", activations["layer3"], self.encoder[9]),
        ]
        result: dict[str, list[dict]] = {}
        for name, act, linear in transitions:
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
        """Convenience: numpy observation -> numpy Q-values (no gradient)."""
        obs = np.asarray(observation, dtype=np.float32)
        if obs.ndim == 1:
            obs = obs[np.newaxis, :]
        tensor = torch.from_numpy(obs)
        with torch.no_grad():
            q_values = self.forward(tensor)
        return q_values.numpy()
