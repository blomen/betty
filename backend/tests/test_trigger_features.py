"""Tests for the trigger feature assembler (141-dim fast observation)."""
from __future__ import annotations

import numpy as np
import pytest

from src.rl.features.trigger_features import (
    build_trigger_observation,
    TRIGGER_DIM,
    TRIGGER_SEGMENTS,
    SETUP_PROB_DIM,
    TRIGGER_GBT_DIM,
    EXEC_PASSTHROUGH_DIM,
)
from src.rl.features.narrative_features import NARRATIVE_DIM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_narrative() -> np.ndarray:
    """Return a dummy 15-dim narrative vector."""
    return np.linspace(-1.0, 1.0, NARRATIVE_DIM, dtype=np.float32)


def _make_setup_probs() -> np.ndarray:
    """Return a dummy 8-dim setup probability vector (sums to ~1, clamped 0-1)."""
    raw = np.array([0.5, 0.3, 0.1, 0.05, 0.02, 0.01, 0.01, 0.01], dtype=np.float32)
    return raw


def _make_base_obs(dim: int = 276) -> np.ndarray:
    """Return a dummy base observation of the expected size."""
    return np.random.default_rng(0).uniform(-1.0, 1.0, dim).astype(np.float32)


def _make_minimal_state() -> dict:
    """Return a minimal state dict with no real objects — features fall back to zeros."""
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


# ---------------------------------------------------------------------------
# Core contract tests
# ---------------------------------------------------------------------------

class TestOutputShape:
    def test_shape_is_trigger_dim(self):
        obs = build_trigger_observation(
            _make_narrative(),
            _make_setup_probs(),
            _make_minimal_state(),
            _make_base_obs(),
        )
        assert obs.shape == (TRIGGER_DIM,), f"Expected ({TRIGGER_DIM},), got {obs.shape}"

    def test_dtype_is_float32(self):
        obs = build_trigger_observation(
            _make_narrative(),
            _make_setup_probs(),
            _make_minimal_state(),
            _make_base_obs(),
        )
        assert obs.dtype == np.float32

    def test_trigger_dim_is_141(self):
        assert TRIGGER_DIM == 141

    def test_no_nans_or_infs(self):
        obs = build_trigger_observation(
            _make_narrative(),
            _make_setup_probs(),
            _make_minimal_state(),
            _make_base_obs(),
        )
        assert np.all(np.isfinite(obs)), "Output contains NaN or Inf"


class TestSegmentSums:
    def test_segments_sum_to_trigger_dim(self):
        total = sum(TRIGGER_SEGMENTS.values())
        assert total == TRIGGER_DIM, (
            f"TRIGGER_SEGMENTS sums to {total}, expected {TRIGGER_DIM}"
        )

    def test_segment_names_present(self):
        expected = {
            "narrative",
            "setup_probs",
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

    def test_segment_dims(self):
        assert TRIGGER_SEGMENTS["narrative"] == NARRATIVE_DIM == 15
        assert TRIGGER_SEGMENTS["setup_probs"] == SETUP_PROB_DIM == 8
        assert TRIGGER_SEGMENTS["structural_passthrough"] == 10
        assert TRIGGER_SEGMENTS["micro"] == 20
        assert TRIGGER_SEGMENTS["orderflow"] == 21
        assert TRIGGER_SEGMENTS["candles"] == 15
        assert TRIGGER_SEGMENTS["zone_features"] == 4
        assert TRIGGER_SEGMENTS["zone_confluence"] == 5
        assert TRIGGER_SEGMENTS["zone_composition"] == 31
        assert TRIGGER_SEGMENTS["approach_direction"] == 1
        assert TRIGGER_SEGMENTS["trigger_gbt_forecast"] == TRIGGER_GBT_DIM == 8
        assert TRIGGER_SEGMENTS["exec_passthrough"] == EXEC_PASSTHROUGH_DIM == 3


class TestNarrativeAtFront:
    def test_narrative_at_index_0(self):
        narrative = _make_narrative()
        obs = build_trigger_observation(
            narrative,
            _make_setup_probs(),
            _make_minimal_state(),
            _make_base_obs(),
        )
        np.testing.assert_array_equal(
            obs[:NARRATIVE_DIM],
            narrative,
            err_msg="Narrative signals must appear at obs[0:15]",
        )


class TestSetupProbsAfterNarrative:
    def test_setup_probs_at_index_15(self):
        setup_probs = _make_setup_probs()
        obs = build_trigger_observation(
            _make_narrative(),
            setup_probs,
            _make_minimal_state(),
            _make_base_obs(),
        )
        start = NARRATIVE_DIM
        end = start + SETUP_PROB_DIM
        np.testing.assert_array_equal(
            obs[start:end],
            setup_probs,
            err_msg=f"Setup probs must appear at obs[{start}:{end}]",
        )


class TestApproachDirection:
    def test_approach_up_is_positive_one(self):
        state = _make_minimal_state()
        state["approach_direction"] = "up"
        obs = build_trigger_observation(
            _make_narrative(), _make_setup_probs(), state, _make_base_obs()
        )
        approach_idx = (
            TRIGGER_SEGMENTS["narrative"]
            + TRIGGER_SEGMENTS["setup_probs"]
            + TRIGGER_SEGMENTS["structural_passthrough"]
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
        obs = build_trigger_observation(
            _make_narrative(), _make_setup_probs(), state, _make_base_obs()
        )
        approach_idx = (
            TRIGGER_SEGMENTS["narrative"]
            + TRIGGER_SEGMENTS["setup_probs"]
            + TRIGGER_SEGMENTS["structural_passthrough"]
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
        obs = build_trigger_observation(
            _make_narrative(), _make_setup_probs(), _make_minimal_state(), _make_base_obs()
        )
        gbt_start = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
        np.testing.assert_array_equal(
            obs[gbt_start : gbt_start + TRIGGER_GBT_DIM],
            np.zeros(TRIGGER_GBT_DIM, dtype=np.float32),
        )

    def test_gbt_values_propagated(self):
        gbt = np.arange(TRIGGER_GBT_DIM, dtype=np.float32) * 0.1
        obs = build_trigger_observation(
            _make_narrative(), _make_setup_probs(), _make_minimal_state(), _make_base_obs(),
            trigger_gbt_forecast=gbt,
        )
        gbt_start = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
        np.testing.assert_array_almost_equal(obs[gbt_start : gbt_start + TRIGGER_GBT_DIM], gbt)


class TestExecPassthrough:
    def test_exec_at_end(self):
        state = _make_minimal_state()
        state["trades_today"] = 5
        state["time_to_close"] = 195.0  # half session
        state["session_pnl"] = 5.0      # 0.5 after norm
        obs = build_trigger_observation(
            _make_narrative(), _make_setup_probs(), state, _make_base_obs()
        )
        exec_seg = obs[-EXEC_PASSTHROUGH_DIM:]
        assert exec_seg[0] == pytest.approx(0.5)   # 5/10
        assert exec_seg[1] == pytest.approx(0.5)   # 195/390
        assert exec_seg[2] == pytest.approx(0.5)   # 5/10


class TestInputValidation:
    def test_bad_narrative_shape_raises(self):
        with pytest.raises(ValueError, match="narrative must be shape"):
            build_trigger_observation(
                np.zeros(10, dtype=np.float32),
                _make_setup_probs(),
                _make_minimal_state(),
                _make_base_obs(),
            )

    def test_bad_setup_probs_shape_raises(self):
        with pytest.raises(ValueError, match="setup_probs must be shape"):
            build_trigger_observation(
                _make_narrative(),
                np.zeros(3, dtype=np.float32),
                _make_minimal_state(),
                _make_base_obs(),
            )

    def test_bad_gbt_forecast_shape_raises(self):
        with pytest.raises(ValueError, match="trigger_gbt_forecast must be shape"):
            build_trigger_observation(
                _make_narrative(),
                _make_setup_probs(),
                _make_minimal_state(),
                _make_base_obs(),
                trigger_gbt_forecast=np.zeros(5, dtype=np.float32),
            )


class TestDeterminism:
    def test_same_inputs_same_output(self):
        narrative = _make_narrative()
        probs = _make_setup_probs()
        state = _make_minimal_state()
        base_obs = _make_base_obs()
        obs1 = build_trigger_observation(narrative, probs, state, base_obs)
        obs2 = build_trigger_observation(narrative, probs, state, base_obs)
        np.testing.assert_array_equal(obs1, obs2)
