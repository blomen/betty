"""FT-Transformer-style network for tabular obs vectors with methodology grouping.

Architecture:
  obs[313] → per-group encoders (one MLP per methodology category)
            → stack into (B, num_groups, group_dim)
            → OF group extracted as Query (B, 1, query_dim)
            → other groups stacked as Key/Value (B, num_groups - 1, kv_dim)
            → CrossGroupAttention → (B, 1, query_dim)
            → MultiTaskHead → 4 outputs

OF gets the largest embedding (128) — others get 32. The attention layer
projects the smaller KV embeddings up to query_dim, so the network can
attend to all groups at the same scale while preserving OF's dominance.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn

from src.rl.features.observation_index import _CATEGORY_SEGMENTS, _SEGMENT_OFFSETS

from .attention import CrossGroupAttention
from .encoders import PerGroupEncoder
from .heads import MultiTaskHead
from .protocol import ModelProtocol
from .types import MultiTaskOutputs

_OF_QUERY_DIM = 128
_OTHER_KV_DIM = 32


class FTTransformerNet(nn.Module):
    def __init__(self, category_segments: dict | None = None) -> None:
        super().__init__()
        cats = category_segments or _CATEGORY_SEGMENTS
        # Build per-group encoders. Index OF specially.
        self._cat_order: list[str] = sorted(cats.keys())
        self._encoders = nn.ModuleDict()
        self._cat_dims: dict[str, tuple[int, int]] = {}  # cat -> (start, end) in flat obs

        cursor = 0
        for cat in self._cat_order:
            segs = cats[cat]
            dim = sum(s["size"] for s in segs)
            out_dim = _OF_QUERY_DIM if cat == "OF" else _OTHER_KV_DIM
            self._encoders[cat] = PerGroupEncoder(input_dim=dim, output_dim=out_dim)
            self._cat_dims[cat] = (cursor, cursor + dim)
            cursor += dim
        self._total_dim = cursor

        self.attention = CrossGroupAttention(
            query_dim=_OF_QUERY_DIM,
            kv_dim=_OTHER_KV_DIM,
            num_heads=4,
        )
        self.head = MultiTaskHead(input_dim=_OF_QUERY_DIM)

    def forward(self, obs: torch.Tensor) -> dict[str, torch.Tensor]:
        # obs: (B, 313). Slice each category from the flat vector.
        # NOTE: this assumes the obs is laid out exactly per
        # observation_index SEGMENTS ordering. Validate at training time.
        embeddings: dict[str, torch.Tensor] = {}
        # Use _SEGMENT_OFFSETS from observation_index for the canonical layout
        for cat in self._cat_order:
            from src.rl.features.observation_index import _CATEGORY_SEGMENTS as _CS

            # Concatenate all segments in this category from obs
            chunks = []
            for seg in _CS[cat]:
                start, end = _SEGMENT_OFFSETS[seg["name"]]
                chunks.append(obs[:, start:end])
            cat_input = torch.cat(chunks, dim=-1)
            embeddings[cat] = self._encoders[cat](cat_input)

        # OF as Query (B, 1, query_dim); others as KV (B, N, kv_dim)
        of_emb = embeddings["OF"].unsqueeze(1)  # (B, 1, _OF_QUERY_DIM)
        other_embs = torch.stack(
            [embeddings[c] for c in self._cat_order if c != "OF"],
            dim=1,
        )  # (B, num_others, _OTHER_KV_DIM)

        attended = self.attention(of_emb, other_embs).squeeze(1)  # (B, _OF_QUERY_DIM)
        return self.head(attended)


class FTTransformerPredictor(ModelProtocol):
    def __init__(self, net: FTTransformerNet | None = None) -> None:
        super().__init__()
        self._net = net if net is not None else FTTransformerNet()
        self._net.eval()

    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        with torch.no_grad():
            x = torch.from_numpy(obs.astype(np.float32)).unsqueeze(0)
            out = self._net(x)
        return MultiTaskOutputs(
            direction_logits=out["direction_logits"][0].tolist(),
            magnitude_R=float(out["magnitude_R"][0].item()),
            win_probability=float(out["win_probability"][0].item()),
            duration_bars=float(out["duration_bars"][0].item()),
            uncertainty=0.1,  # placeholder; replace with MC dropout or ensemble in v2
        )

    def save(self, path: Path | str) -> None:
        torch.save(self._net.state_dict(), path)

    @classmethod
    def load(cls, path: Path | str) -> "FTTransformerPredictor":
        net = FTTransformerNet()
        net.load_state_dict(torch.load(path, map_location="cpu"))
        net.eval()
        return cls(net=net)
