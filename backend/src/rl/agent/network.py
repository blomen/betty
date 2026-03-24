"""Dual-stream Dueling DQN for the RL trading agent.

Architecture:
  Stream A — Temporal (processes raw tick/candle sequences):
    Tick stream:   (B, 50, 4) → Conv1D(16,k=3) → Conv1D(32,k=3) → GRU(64) → 64
    1m candles:    (B, 10, 3) → Conv1D(16,k=3) → 16
    5m candles:    (B, 6, 3)  → Conv1D(16,k=3) → 16

  Stream B — Static (processes market context):
    Context: (B, context_dim) → Linear(128) → LayerNorm → ReLU → Linear(64) → 64

  Fusion → Dueling:
    Concat(64 + 16 + 16 + 64 = 160)
    Value stream:     Linear(160→64→1)
    Advantage stream: Linear(160→64→3)
    Q = V + (A - mean(A))
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from torch import Tensor

from src.rl.config import NUM_ACTIONS

# Sequence dimensions (must match what replay_engine passes)
TICK_SEQ_LEN = 50
TICK_FEATURES = 4    # price_norm, size_norm, side(±1), dt_norm
CANDLE_1M_LEN = 10
CANDLE_5M_LEN = 6
CANDLE_FEATURES = 3  # delta_norm, volume_norm, body_ratio


class TemporalStream(nn.Module):
    """CNN + GRU for tick sequences, CNN for candle sequences."""

    def __init__(self) -> None:
        super().__init__()

        # Tick stream: Conv1D → Conv1D → GRU
        self.tick_conv = nn.Sequential(
            nn.Conv1d(TICK_FEATURES, 16, kernel_size=3, padding=1),
            nn.LayerNorm([16, TICK_SEQ_LEN]),
            nn.ReLU(),
            nn.Conv1d(16, 32, kernel_size=3, padding=1),
            nn.LayerNorm([32, TICK_SEQ_LEN]),
            nn.ReLU(),
        )
        self.tick_gru = nn.GRU(32, 64, batch_first=True)
        self.tick_attn = nn.Linear(64, 1)  # single-head attention over GRU outputs

        # 1m candle stream: Conv1D → pool
        self.candle_1m_conv = nn.Sequential(
            nn.Conv1d(CANDLE_FEATURES, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )

        # 5m candle stream: Conv1D → pool
        self.candle_5m_conv = nn.Sequential(
            nn.Conv1d(CANDLE_FEATURES, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )

    def forward(
        self,
        ticks: Tensor,       # (B, 50, 4)
        candles_1m: Tensor,  # (B, 10, 3)
        candles_5m: Tensor,  # (B, 6, 3)
    ) -> Tensor:
        """Returns (B, 96) — concatenated temporal features."""
        # Tick stream
        t = ticks.transpose(1, 2)  # (B, 4, 50) for Conv1d
        t = self.tick_conv(t)      # (B, 32, 50)
        t = t.transpose(1, 2)      # (B, 50, 32) for GRU
        gru_out, _ = self.tick_gru(t)  # (B, 50, 64)

        # Attention: weighted sum of GRU outputs
        attn_weights = torch.softmax(self.tick_attn(gru_out), dim=1)  # (B, 50, 1)
        tick_feat = (gru_out * attn_weights).sum(dim=1)  # (B, 64)

        # 1m candle stream
        c1 = candles_1m.transpose(1, 2)  # (B, 3, 10)
        c1 = self.candle_1m_conv(c1).squeeze(-1)  # (B, 16)

        # 5m candle stream
        c5 = candles_5m.transpose(1, 2)  # (B, 3, 6)
        c5 = self.candle_5m_conv(c5).squeeze(-1)  # (B, 16)

        return torch.cat([tick_feat, c1, c5], dim=1)  # (B, 96)


class StaticStream(nn.Module):
    """MLP for static market context features."""

    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.LayerNorm(128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.LayerNorm(64),
            nn.ReLU(),
        )

    def forward(self, x: Tensor) -> Tensor:
        return self.net(x)  # (B, 64)


class DQNetwork(nn.Module):
    """Dual-stream Dueling DQN.

    Accepts either:
      - A flat observation vector (legacy mode, for backward compat)
      - Structured input dict with tick_seq, candle_1m, candle_5m, context
    """

    def __init__(self, input_dim: int, context_dim: int | None = None) -> None:
        super().__init__()
        self._input_dim = input_dim

        if context_dim is not None:
            # New dual-stream mode
            self._dual_stream = True
            self._context_dim = context_dim
            self.temporal = TemporalStream()
            self.static = StaticStream(context_dim)

            fusion_dim = 96 + 64  # temporal(96) + static(64)
        else:
            # Legacy flat MLP mode (for loading old checkpoints)
            self._dual_stream = False
            self._context_dim = 0
            self.flat_encoder = nn.Sequential(
                nn.Linear(input_dim, 128),
                nn.ReLU(),
                nn.Linear(128, 128),
                nn.ReLU(),
                nn.Linear(128, 64),
                nn.ReLU(),
            )
            fusion_dim = 64

        # Dueling heads
        self.value_stream = nn.Sequential(
            nn.Linear(fusion_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )
        self.advantage_stream = nn.Sequential(
            nn.Linear(fusion_dim, 64),
            nn.ReLU(),
            nn.Linear(64, NUM_ACTIONS),
        )

    def forward(self, x: Tensor, **kwargs) -> Tensor:
        """Forward pass. Accepts flat tensor or structured kwargs.

        For dual-stream mode, pass:
            tick_seq:   (B, 50, 4)
            candle_1m:  (B, 10, 3)
            candle_5m:  (B, 6, 3)
            context:    (B, context_dim)

        For legacy mode, pass flat observation tensor.
        """
        if self._dual_stream and "tick_seq" in kwargs:
            temporal_feat = self.temporal(
                kwargs["tick_seq"], kwargs["candle_1m"], kwargs["candle_5m"]
            )
            static_feat = self.static(kwargs["context"])
            features = torch.cat([temporal_feat, static_feat], dim=1)
        elif self._dual_stream and x.shape[-1] == self._input_dim:
            # Structured input packed into single tensor — split it
            features = self._split_and_forward(x)
        else:
            features = self.flat_encoder(x)

        return self._dueling(features)

    def _dueling(self, features: Tensor) -> Tensor:
        """Dueling: Q = V + (A - mean(A))."""
        value = self.value_stream(features)           # (B, 1)
        advantage = self.advantage_stream(features)   # (B, NUM_ACTIONS)
        return value + advantage - advantage.mean(dim=1, keepdim=True)

    def _split_and_forward(self, x: Tensor) -> Tensor:
        """Split a packed flat tensor into temporal + static components."""
        B = x.shape[0]
        # Layout: [tick_seq(50*4), candle_1m(10*3), candle_5m(6*3), context(rest)]
        tick_end = TICK_SEQ_LEN * TICK_FEATURES       # 200
        c1m_end = tick_end + CANDLE_1M_LEN * CANDLE_FEATURES  # 230
        c5m_end = c1m_end + CANDLE_5M_LEN * CANDLE_FEATURES   # 248

        tick_seq = x[:, :tick_end].reshape(B, TICK_SEQ_LEN, TICK_FEATURES)
        candle_1m = x[:, tick_end:c1m_end].reshape(B, CANDLE_1M_LEN, CANDLE_FEATURES)
        candle_5m = x[:, c1m_end:c5m_end].reshape(B, CANDLE_5M_LEN, CANDLE_FEATURES)
        context = x[:, c5m_end:]

        temporal_feat = self.temporal(tick_seq, candle_1m, candle_5m)
        static_feat = self.static(context)
        return torch.cat([temporal_feat, static_feat], dim=1)

    def forward_with_activations(self, x: Tensor, **kwargs) -> dict[str, Tensor]:
        """Forward pass capturing intermediate activations for visualization."""
        if x.ndim == 1:
            x = x.unsqueeze(0)

        if self._dual_stream and "tick_seq" in kwargs:
            temporal_feat = self.temporal(
                kwargs["tick_seq"], kwargs["candle_1m"], kwargs["candle_5m"]
            )
            static_feat = self.static(kwargs["context"])
            features = torch.cat([temporal_feat, static_feat], dim=1)
        elif self._dual_stream:
            features = self._split_and_forward(x)
        else:
            features = self.flat_encoder(x)

        value = self.value_stream(features)
        advantage = self.advantage_stream(features)
        q_values = value + advantage - advantage.mean(dim=1, keepdim=True)

        return {
            "inputs": x,
            "features": features,         # fused representation
            "value": value,
            "advantage": advantage,
            "q_values": q_values,
            # For visualization compatibility — map to "layer" keys
            "layer1": features[:, :64] if features.shape[1] >= 64 else features,
            "layer2": features[:, 64:128] if features.shape[1] >= 128 else features,
            "layer3": features[:, 128:] if features.shape[1] > 128 else features[:, :min(features.shape[1], 64)],
        }

    @torch.no_grad()
    def extract_top_connections(
        self, activations: dict[str, Tensor], top_n: int = 100
    ) -> dict[str, list[dict]]:
        """Extract strongest connections for visualization.

        For dual-stream, shows value/advantage stream weights.
        """
        result: dict[str, list[dict]] = {}

        # Value stream connections
        for name, stream in [("to_value", self.value_stream), ("to_advantage", self.advantage_stream)]:
            feat = activations["features"][0]
            linear = stream[0]  # first Linear layer
            w = linear.weight
            signal = (w * feat.unsqueeze(0)).abs()
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

        # For frontend compat — map to expected keys
        result["input_l1"] = result.get("to_value", [])
        result["l1_l2"] = result.get("to_advantage", [])
        result["l2_l3"] = []
        result["l3_output"] = []

        return result

    def predict(self, observation: np.ndarray) -> np.ndarray:
        """Convenience: numpy observation → numpy Q-values (no gradient)."""
        obs = np.asarray(observation, dtype=np.float32)
        if obs.ndim == 1:
            obs = obs[np.newaxis, :]
        tensor = torch.from_numpy(obs)
        with torch.no_grad():
            q_values = self.forward(tensor)
        return q_values.numpy()
