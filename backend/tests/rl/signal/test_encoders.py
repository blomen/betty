import torch

from src.rl.signal.encoders import PerGroupEncoder


def test_per_group_encoder_output_shape():
    """Encoder for one group: input (B, dim_in) → (B, dim_out)."""
    enc = PerGroupEncoder(input_dim=25, output_dim=128, hidden_dim=64)
    x = torch.randn(4, 25)
    out = enc(x)
    assert out.shape == (4, 128)


def test_per_group_encoder_handles_batch_size_one():
    enc = PerGroupEncoder(input_dim=10, output_dim=32)
    x = torch.randn(1, 10)
    out = enc(x)
    assert out.shape == (1, 32)


def test_per_group_encoder_default_hidden_dim_scales_with_input():
    enc = PerGroupEncoder(input_dim=64, output_dim=128)
    # No assertion on internal hidden dim — just confirms it doesn't crash
    x = torch.randn(2, 64)
    out = enc(x)
    assert out.shape == (2, 128)


def test_per_group_encoder_supports_dropout():
    enc = PerGroupEncoder(input_dim=20, output_dim=32, dropout=0.3)
    x = torch.randn(8, 20)
    enc.train()
    out_train = enc(x)
    enc.eval()
    out_eval = enc(x)
    assert out_train.shape == out_eval.shape == (8, 32)
