"""Per-methodology-group MLP encoder.

Each group of obs dims (OF, VSA, PROFILE, AMT, DOW_STRUCTURE, MICRO,
ZONE_MEMORY, MACRO, EXECUTION) gets its own small MLP that learns the
joint representation of that family in isolation. This is the "synapse"
architecture — each methodology family wires together internally before
attending to others.

OF gets the biggest output_dim (128 default) so it dominates downstream
cross-group attention as Query. Others use 32.
"""

from __future__ import annotations

import torch
from torch import nn


class PerGroupEncoder(nn.Module):
    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        hidden_dim: int | None = None,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        if hidden_dim is None:
            hidden_dim = max(output_dim, input_dim // 2)
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, output_dim),
            nn.LayerNorm(output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)
