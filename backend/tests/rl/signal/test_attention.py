import torch

from src.rl.signal.attention import CrossGroupAttention


def test_attention_output_shape():
    """Query (B, 1, d_q), Keys+Values (B, N, d_kv) → output (B, 1, d_q)."""
    attn = CrossGroupAttention(query_dim=128, kv_dim=32, num_heads=4)
    query = torch.randn(2, 1, 128)  # OF embedding
    kv = torch.randn(2, 8, 32)  # 8 other-group embeddings
    out = attn(query, kv)
    assert out.shape == (2, 1, 128)


def test_attention_handles_single_group_in_kv():
    attn = CrossGroupAttention(query_dim=64, kv_dim=32, num_heads=2)
    query = torch.randn(1, 1, 64)
    kv = torch.randn(1, 1, 32)
    out = attn(query, kv)
    assert out.shape == (1, 1, 64)


def test_attention_is_deterministic_in_eval_mode():
    attn = CrossGroupAttention(query_dim=64, kv_dim=32, num_heads=2, dropout=0.5)
    attn.eval()
    query = torch.randn(1, 1, 64)
    kv = torch.randn(1, 4, 32)
    out1 = attn(query, kv)
    out2 = attn(query, kv)
    torch.testing.assert_close(out1, out2)
