"""Cross-attention layer: OF embedding as Query, other-group embeddings as Key/Value.

This architecturally weights OF as the dominant methodology — other
groups (VSA, PROFILE, etc.) are 'looked up' to support or refute the
OF signal. Matches the methodology priority discussed in the
2026-05-17 architecture verdict.
"""

from __future__ import annotations

import torch
from torch import nn


class CrossGroupAttention(nn.Module):
    def __init__(
        self,
        query_dim: int,
        kv_dim: int,
        num_heads: int = 4,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        # Project K, V to query_dim so MultiheadAttention can use embed_dim=query_dim
        self.k_proj = nn.Linear(kv_dim, query_dim)
        self.v_proj = nn.Linear(kv_dim, query_dim)
        self.attn = nn.MultiheadAttention(
            embed_dim=query_dim,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.norm = nn.LayerNorm(query_dim)

    def forward(self, query: torch.Tensor, kv: torch.Tensor) -> torch.Tensor:
        """query: (B, 1, query_dim), kv: (B, N, kv_dim) → (B, 1, query_dim)"""
        k = self.k_proj(kv)
        v = self.v_proj(kv)
        attended, _ = self.attn(query, k, v)
        return self.norm(query + attended)
