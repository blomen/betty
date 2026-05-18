"""Tests for the trigger feature assembler (122-dim, Phase 3b + Tier C OF)."""

from __future__ import annotations

import numpy as np
import pytest

from src.rl.features.trigger_features import (
    EXEC_PASSTHROUGH_DIM,
    TRIGGER_DIM,
    TRIGGER_GBT_DIM,
    TRIGGER_SEGMENTS,
    build_trigger_observation,
)


def _make_base_obs(dim: int = 302) -> np.ndarray:
    return np.random.default_rng(0).uniform(-1.0, 1.0, dim).astype(np.float32)


def _make_minimal_state() -> dict:
    return {
        "price": 19000.0,
        "candles": [],
        "recent_ticks": [],
        "orderflow_signals": None,
        "zone": None,
        "all_zones": [],
        "fvgs": [],
        "single_print_zones": [],
        "approach_direction": "up",
        "session_context": None,
        "trades_today": 2,
        "time_to_close": 90.0,
        "session_pnl": 1.5,
    }


class TestOutputShape:
    def test_shape_is_trigger_dim(self):
        obs = build_trigger_observation(_make_minimal_state(), _make_base_obs())
        assert obs.shape == (TRIGGER_DIM,)

    def test_dtype_is_float32(self):
        obs = build_trigger_observation(_make_minimal_state(), _make_base_obs())
        assert obs.dtype == np.float32

    def test_trigger_dim_is_124(self):
        assert TRIGGER_DIM == 124

    def test_no_nans_or_infs(self):
        obs = build_trigger_observation(_make_minimal_state(), _make_base_obs())
        assert np.all(np.isfinite(obs))


class TestSegmentSums:
    def test_segments_sum_to_trigger_dim(self):
        assert sum(TRIGGER_SEGMENTS.values()) == TRIGGER_DIM

    def test_segment_names_present(self):
        expected = {
            "structural_passthrough",
            "micro",
            "orderflow",
            "candles",
            "zone_features",
            "zone_confluence",
            "zone_composition",
            "approach_direction",
            "trigger_gbt_forecast",
            "exec_passthrough",
        }
        assert set(TRIGGER_SEGMENTS.keys()) == expected

    def test_no_narrative_or_setup_probs(self):
        """Phase 3b — narrative & setup_probs must NOT appear in trigger."""
        assert "narrative" not in TRIGGER_SEGMENTS
        assert "setup_probs" not in TRIGGER_SEGMENTS


class TestApproachDirection:
    def test_approach_up_is_positive_one(self):
        state = _make_minimal_state()
        state["approach_direction"] = "up"
        obs = build_trigger_observation(state, _make_base_obs())
        approach_idx = (
            TRIGGER_SEGMENTS["structural_passthrough"]
            + TRIGGER_SEGMENTS["micro"]
            + TRIGGER_SEGMENTS["orderflow"]
            + TRIGGER_SEGMENTS["candles"]
            + TRIGGER_SEGMENTS["zone_features"]
            + TRIGGER_SEGMENTS["zone_confluence"]
            + TRIGGER_SEGMENTS["zone_composition"]
        )
        assert obs[approach_idx] == pytest.approx(1.0)

    def test_approach_down_is_negative_one(self):
        state = _make_minimal_state()
        state["approach_direction"] = "down"
        obs = build_trigger_observation(state, _make_base_obs())
        approach_idx = (
            TRIGGER_SEGMENTS["structural_passthrough"]
            + TRIGGER_SEGMENTS["micro"]
            + TRIGGER_SEGMENTS["orderflow"]
            + TRIGGER_SEGMENTS["candles"]
            + TRIGGER_SEGMENTS["zone_features"]
            + TRIGGER_SEGMENTS["zone_confluence"]
            + TRIGGER_SEGMENTS["zone_composition"]
        )
        assert obs[approach_idx] == pytest.approx(-1.0)


class TestTriggerGBT:
    def test_gbt_zeros_when_not_provided(self):
        obs = build_trigger_observation(_make_minimal_state(), _make_base_obs())
        gbt_start = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
        np.testing.assert_array_equal(
            obs[gbt_start : gbt_start + TRIGGER_GBT_DIM],
            np.zeros(TRIGGER_GBT_DIM, dtype=np.float32),
        )

    def test_gbt_values_propagated(self):
        gbt = np.arange(TRIGGER_GBT_DIM, dtype=np.float32) * 0.1
        obs = build_trigger_observation(
            _make_minimal_state(),
            _make_base_obs(),
            trigger_gbt_forecast=gbt,
        )
        gbt_start = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
        np.testing.assert_array_almost_equal(obs[gbt_start : gbt_start + TRIGGER_GBT_DIM], gbt)


class TestExecPassthrough:
    def test_exec_at_end(self):
        state = _make_minimal_state()
        state["trades_today"] = 5
        state["time_to_close"] = 195.0
        state["session_pnl"] = 5.0
        obs = build_trigger_observation(state, _make_base_obs())
        exec_seg = obs[-EXEC_PASSTHROUGH_DIM:]
        assert exec_seg[0] == pytest.approx(0.5)
        assert exec_seg[1] == pytest.approx(0.5)
        assert exec_seg[2] == pytest.approx(0.5)


class TestDeterminism:
    def test_same_inputs_same_output(self):
        state = _make_minimal_state()
        base_obs = _make_base_obs()
        obs1 = build_trigger_observation(state, base_obs)
        obs2 = build_trigger_observation(state, base_obs)
        np.testing.assert_array_equal(obs1, obs2)
