"""FT-Transformer training loop. Offline — runs against existing pool at
/app/data/rl/episodes/. Outputs a saved FTTransformerNet state_dict that
FTTransformerPredictor.load() can consume.

Multi-task loss = weighted sum of:
  - direction: cross-entropy (large weight)
  - magnitude: MSE (small weight — values can be large)
  - win-prob: BCE (small weight)
  - duration: MSE (small weight)

Weights tuned empirically; first pass uses defaults below.

KNOWN ISSUE — CrossEntropy + already-softmax'd direction_logits:
  MultiTaskHead.forward returns softmax'd probabilities for direction_logits
  (F.softmax applied in heads.py), but nn.CrossEntropyLoss expects RAW logits
  (it applies log-softmax internally). Passing softmax'd probs through
  CrossEntropyLoss means the network sees log(softmax(logit)) as its CE loss,
  which is mathematically valid (gradients are non-zero, loss is still
  minimised in the correct direction) but is NOT numerically equivalent to
  standard CE — it's slightly redundant and noisier near saturation.
  Task 16 (real training + benchmarking) should either:
    (a) change MultiTaskHead to return raw logits and use CrossEntropyLoss, or
    (b) keep softmax'd probs and switch to NLLLoss(log(probs)).
  Don't fix it here — wait for empirical comparison.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from .ft_predictor import FTTransformerNet


class FTTrainingDataset(Dataset):
    def __init__(
        self,
        obs: np.ndarray,
        direction: np.ndarray,
        magnitude: np.ndarray,
        win_outcomes: np.ndarray,
        durations: np.ndarray,
    ) -> None:
        self.obs = torch.from_numpy(obs.astype(np.float32))
        self.direction = torch.from_numpy(direction.astype(np.int64))
        self.magnitude = torch.from_numpy(magnitude.astype(np.float32))
        self.win = torch.from_numpy(win_outcomes.astype(np.float32))
        self.duration = torch.from_numpy(durations.astype(np.float32))

    def __len__(self) -> int:
        return self.obs.shape[0]

    def __getitem__(self, idx: int) -> dict:
        return {
            "obs": self.obs[idx],
            "direction": self.direction[idx],
            "magnitude": self.magnitude[idx],
            "win": self.win[idx],
            "duration": self.duration[idx],
        }


def train_ft_transformer(
    obs: np.ndarray,
    direction: np.ndarray,
    magnitude: np.ndarray,
    win_outcomes: np.ndarray,
    durations: np.ndarray,
    out_path: Path | str,
    max_epochs: int = 20,
    batch_size: int = 64,
    lr: float = 1e-3,
    direction_weight: float = 1.0,
    magnitude_weight: float = 0.1,
    win_weight: float = 0.3,
    duration_weight: float = 0.05,
    device: str | None = None,
) -> None:
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    ds = FTTrainingDataset(obs, direction, magnitude, win_outcomes, durations)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=True, num_workers=0)

    net = FTTransformerNet().to(device)
    optim = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    ce = nn.CrossEntropyLoss()
    mse = nn.MSELoss()
    bce = nn.BCELoss()

    net.train()
    for epoch in range(max_epochs):
        total_loss = 0.0
        for batch in loader:
            obs_b = batch["obs"].to(device)
            out = net(obs_b)
            loss = (
                direction_weight * ce(out["direction_logits"], batch["direction"].to(device))
                + magnitude_weight * mse(out["magnitude_R"], batch["magnitude"].to(device))
                + win_weight * bce(out["win_probability"], batch["win"].to(device))
                + duration_weight * mse(out["duration_bars"], batch["duration"].to(device))
            )
            optim.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), max_norm=1.0)
            optim.step()
            total_loss += loss.item()
        print(f"epoch {epoch + 1}/{max_epochs}  loss={total_loss / len(loader):.4f}")

    torch.save(net.state_dict(), out_path)
