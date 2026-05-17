"""Confirm build_observation passes l1_snapshot + recent_trades from rl_state
into extract_orderflow_features, so the L1-derivable OF dims actually
reflect the live top-of-book instead of candle approximations.
"""

from __future__ import annotations

import pytest

from src.market_data.l1_quote_state import L1Snapshot
from src.rl.features.observation import build_observation
from src.rl.features.observation_index import _SEGMENT_OFFSETS


def _minimal_state(*, with_l1: bool) -> dict:
    """Minimal rl_state — build_observation has many optional inputs
    (vwap_bands, volume_profile, swing_structure, etc.) all of which
    default to None / zeros when absent. zone=None, candles=[] is enough
    to exercise the orderflow segment and propagate L1 through."""
    state: dict = {
        "candles": [],
        "orderflow_signals": None,
        "zone": None,
        "price": 25000.0,
    }
    if with_l1:
        state["l1_snapshot"] = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
        state["recent_trades"] = []
    return state


def test_l1_snapshot_in_state_overrides_spread_dim():
    """With L1 in state, OF dim 6 (spread_ticks) reads from L1 (1 tick → 0.02).
    Without L1 and no candles, it stays at the candle-derived default (0.0)."""
    of_start, _of_end = _SEGMENT_OFFSETS["orderflow"]
    SPREAD_OFFSET = 6  # spread_ticks is index 6 within the orderflow segment

    obs_no_l1 = build_observation(_minimal_state(with_l1=False))
    obs_with_l1 = build_observation(_minimal_state(with_l1=True))

    assert obs_no_l1[of_start + SPREAD_OFFSET] == 0.0
    assert obs_with_l1[of_start + SPREAD_OFFSET] == pytest.approx(0.02, abs=1e-4)


def test_l1_snapshot_absent_preserves_legacy_behavior():
    """rl_state without an l1_snapshot key behaves identically to the
    pre-Task-7 code path — build_observation must not crash and must
    produce a finite-length obs vector."""
    obs = build_observation(_minimal_state(with_l1=False))
    assert obs is not None
    assert len(obs) > 0


def test_recent_trades_propagates_into_passive_active_ratio():
    """When L1 + recent trades are supplied, OF dim 7 (passive_active_ratio)
    reflects Lee-Ready classification, not candle proxies.

    Snap: bid=25000.0 ask=25000.25 (1 tick wide).
    Trades: two at ask (buy aggressor, active=15) + one inside spread (passive=20).
    pa_ratio = 20/15 ≈ 1.333, normalised /5 ≈ 0.267.

    NOTE: the inside-spread trade requires a 2-tick wide spread to land
    strictly between bid and ask. We use bid=25000.0 ask=25000.50 here.
    """
    of_start, _of_end = _SEGMENT_OFFSETS["orderflow"]
    PA_OFFSET = 7  # passive_active_ratio is index 7

    state = {
        "candles": [],
        "orderflow_signals": None,
        "zone": None,
        "price": 25000.25,
        "l1_snapshot": L1Snapshot(bid=25000.0, ask=25000.50, bid_size=10, ask_size=10, ts=1.0),
        "recent_trades": [
            {"price": 25000.50, "size": 10},  # buy aggressor (active)
            {"price": 25000.50, "size": 5},  # buy aggressor (active)
            {"price": 25000.25, "size": 20},  # inside spread → passive
        ],
    }
    obs = build_observation(state)
    pa_dim = obs[of_start + PA_OFFSET]
    # passive=20, active=15 → ratio 1.333, normalised /5 → 0.267
    assert pa_dim == pytest.approx(0.267, abs=0.01)
