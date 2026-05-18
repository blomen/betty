import numpy as np
import torch

from src.rl.signal.training import FTTrainingDataset, train_ft_transformer


def test_training_dataset_returns_tensors():
    obs = np.random.randn(100, 313).astype(np.float32)
    direction = np.random.randint(0, 3, 100)
    magnitude = np.random.randn(100).astype(np.float32)
    win = np.random.randint(0, 2, 100)
    duration = np.random.uniform(1, 20, 100).astype(np.float32)

    ds = FTTrainingDataset(obs, direction, magnitude, win, duration)
    item = ds[0]
    assert torch.is_tensor(item["obs"])
    assert item["obs"].shape == (313,)
    assert item["direction"].item() in (0, 1, 2)


def test_train_ft_transformer_smoke(tmp_path):
    """Train for 1 epoch on tiny synthetic data — just checks the loop runs."""
    obs = np.random.randn(64, 313).astype(np.float32)
    direction = np.random.randint(0, 3, 64)
    magnitude = np.random.randn(64).astype(np.float32)
    win = np.random.randint(0, 2, 64)
    duration = np.random.uniform(1, 20, 64).astype(np.float32)

    out_path = tmp_path / "ft.pt"
    train_ft_transformer(
        obs=obs,
        direction=direction,
        magnitude=magnitude,
        win_outcomes=win,
        durations=duration,
        out_path=out_path,
        max_epochs=1,
        batch_size=16,
    )
    assert out_path.exists()
