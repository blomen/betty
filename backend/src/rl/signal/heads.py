"""Multi-task heads on top of the shared representation.

  direction: 3-class softmax (CONTINUATION/REVERSAL/SKIP) — cross-entropy loss
  magnitude_R: regression (no activation) — MSE loss
  win_probability: sigmoid — binary cross-entropy loss
  duration_bars: softplus (> 0) — MSE loss

Loss is weighted sum during training; weights typically tuned to balance
gradient magnitudes (direction CE much larger than win-prob BCE).
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


class MultiTaskHead(nn.Module):
    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.direction = nn.Linear(input_dim, 3)
        self.magnitude = nn.Linear(input_dim, 1)
        self.win_prob = nn.Linear(input_dim, 1)
        self.duration = nn.Linear(input_dim, 1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        return {
            "direction_logits": F.softmax(self.direction(x), dim=-1),
            "magnitude_R": self.magnitude(x).squeeze(-1),
            "win_probability": torch.sigmoid(self.win_prob(x)).squeeze(-1),
            "duration_bars": F.softplus(self.duration(x)).squeeze(-1),
        }
