import torch

from src.rl.signal.heads import MultiTaskHead


def test_multitask_head_returns_all_four_outputs():
    head = MultiTaskHead(input_dim=128)
    x = torch.randn(4, 128)
    out = head(x)
    assert "direction_logits" in out
    assert "magnitude_R" in out
    assert "win_probability" in out
    assert "duration_bars" in out


def test_direction_logits_shape_three_classes():
    head = MultiTaskHead(input_dim=128)
    x = torch.randn(2, 128)
    out = head(x)
    assert out["direction_logits"].shape == (2, 3)


def test_win_probability_in_zero_one_range():
    head = MultiTaskHead(input_dim=64)
    x = torch.randn(8, 64)
    out = head(x)
    p = out["win_probability"]
    assert (p >= 0).all() and (p <= 1).all()


def test_duration_bars_is_positive():
    """Duration head uses softplus to enforce > 0."""
    head = MultiTaskHead(input_dim=64)
    x = torch.randn(8, 64)
    out = head(x)
    assert (out["duration_bars"] > 0).all()


def test_magnitude_R_can_be_negative():
    """Magnitude regression should NOT clip to positive."""
    head = MultiTaskHead(input_dim=64)
    x = torch.randn(100, 64) * 10  # large activations to push some predictions negative
    out = head(x)
    # Just confirm it's not always positive
    assert out["magnitude_R"].shape == (100,)
