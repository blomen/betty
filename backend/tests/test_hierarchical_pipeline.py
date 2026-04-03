"""Integration smoke tests for the hierarchical observation pipeline.

Verifies the full pipeline from empty state through narrative → trigger,
plus backward compatibility with the legacy build_observation (276-dim).
"""
from __future__ import annotations

import numpy as np
import pytest

from src.rl.features.narrative_features import extract_narrative_features, NARRATIVE_DIM
from src.rl.features.trigger_features import build_trigger_observation, TRIGGER_DIM
from src.rl.features.passthrough_features import extract_passthrough, PASSTHROUGH_DIM
from src.rl.features.observation import (
    build_observation,
    build_narrative,
    build_trigger,
    TRIGGER_OBSERVATION_DIM,
)
from src.rl.labeling.setup_types import SetupType, NUM_SETUP_TYPES
from src.rl.labeling.setup_labeler import label_episode


# ---------------------------------------------------------------------------
# Shared minimal state used across tests
# ---------------------------------------------------------------------------

def _minimal_state() -> dict:
    """Minimal valid state dict — all optional fields absent or empty."""
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
        "macro": None,
        "swing_structure": None,
        "session_levels": None,
        "volume_profile": None,
        "vwap_bands": None,
        "session_tpos": None,
        "amt_dynamics": None,
        "all_levels": [],
        "trades_today": 0,
        "time_to_close": 0.0,
        "session_pnl": 0.0,
    }


def _make_narrative_arr() -> np.ndarray:
    return np.zeros(NARRATIVE_DIM, dtype=np.float32)


def _make_setup_probs() -> np.ndarray:
    return np.zeros(8, dtype=np.float32)


# ---------------------------------------------------------------------------
# 1. test_full_pipeline_dimensions
# ---------------------------------------------------------------------------

class TestFullPipelineDimensions:
    """Narrative, passthrough, and trigger all produce correct shapes from empty state."""

    def test_narrative_shape(self):
        state = _minimal_state()
        obs = extract_narrative_features(state)
        assert obs.shape == (NARRATIVE_DIM,), f"Expected ({NARRATIVE_DIM},), got {obs.shape}"
        assert obs.dtype == np.float32

    def test_narrative_dim_is_15(self):
        assert NARRATIVE_DIM == 15, f"NARRATIVE_DIM should be 15, got {NARRATIVE_DIM}"

    def test_passthrough_shape(self):
        # passthrough takes a 276-dim base observation
        base = np.zeros(276, dtype=np.float32)
        out = extract_passthrough(base)
        assert out.shape == (PASSTHROUGH_DIM,), f"Expected ({PASSTHROUGH_DIM},), got {out.shape}"
        assert out.dtype == np.float32

    def test_passthrough_dim_is_10(self):
        assert PASSTHROUGH_DIM == 10, f"PASSTHROUGH_DIM should be 10, got {PASSTHROUGH_DIM}"

    def test_trigger_shape(self):
        state = _minimal_state()
        narrative = _make_narrative_arr()
        setup_probs = _make_setup_probs()
        base_obs = build_observation(state)
        obs = build_trigger_observation(narrative, setup_probs, state, base_obs)
        assert obs.shape == (TRIGGER_DIM,), f"Expected ({TRIGGER_DIM},), got {obs.shape}"
        assert obs.dtype == np.float32

    def test_trigger_dim_is_141(self):
        assert TRIGGER_DIM == 141, f"TRIGGER_DIM should be 141, got {TRIGGER_DIM}"

    def test_all_outputs_finite_from_empty_state(self):
        state = _minimal_state()
        narrative = extract_narrative_features(state)
        setup_probs = _make_setup_probs()
        base_obs = build_observation(state)
        trigger = build_trigger_observation(narrative, setup_probs, state, base_obs)

        assert np.all(np.isfinite(narrative)), "Narrative has non-finite values"
        assert np.all(np.isfinite(trigger)), "Trigger has non-finite values"


# ---------------------------------------------------------------------------
# 2. test_narrative_inside_trigger
# ---------------------------------------------------------------------------

class TestNarrativeInsideTrigger:
    """Narrative signals appear at start of trigger; setup probs follow immediately after."""

    def test_narrative_at_start_of_trigger(self):
        """The first NARRATIVE_DIM elements of trigger == narrative input."""
        state = _minimal_state()
        narrative = np.linspace(-0.9, 0.9, NARRATIVE_DIM, dtype=np.float32)
        setup_probs = np.array([0.5, 0.2, 0.1, 0.08, 0.05, 0.04, 0.02, 0.01], dtype=np.float32)
        base_obs = build_observation(state)
        trigger = build_trigger_observation(narrative, setup_probs, state, base_obs)

        np.testing.assert_array_equal(
            trigger[:NARRATIVE_DIM],
            narrative,
            err_msg="First 15 elements of trigger must equal the narrative input",
        )

    def test_setup_probs_after_narrative(self):
        """Elements [15:23] of trigger == setup_probs input."""
        state = _minimal_state()
        narrative = _make_narrative_arr()
        setup_probs = np.array([0.1, 0.2, 0.3, 0.15, 0.1, 0.07, 0.05, 0.03], dtype=np.float32)
        base_obs = build_observation(state)
        trigger = build_trigger_observation(narrative, setup_probs, state, base_obs)

        SETUP_PROB_DIM = 8
        np.testing.assert_array_equal(
            trigger[NARRATIVE_DIM: NARRATIVE_DIM + SETUP_PROB_DIM],
            setup_probs,
            err_msg="Elements [15:23] of trigger must equal setup_probs input",
        )

    def test_narrative_and_setup_probs_independent(self):
        """Different narrative values produce different trigger outputs at the correct offsets."""
        state = _minimal_state()
        setup_probs = _make_setup_probs()
        base_obs = build_observation(state)

        narrative_a = np.full(NARRATIVE_DIM, 0.3, dtype=np.float32)
        narrative_b = np.full(NARRATIVE_DIM, -0.3, dtype=np.float32)

        trigger_a = build_trigger_observation(narrative_a, setup_probs, state, base_obs)
        trigger_b = build_trigger_observation(narrative_b, setup_probs, state, base_obs)

        # First 15 elements should differ
        assert not np.array_equal(trigger_a[:NARRATIVE_DIM], trigger_b[:NARRATIVE_DIM])
        # Remaining elements (from passthrough onwards) should be identical
        assert np.array_equal(trigger_a[NARRATIVE_DIM:], trigger_b[NARRATIVE_DIM:])


# ---------------------------------------------------------------------------
# 3. test_setup_labeler_returns_valid_types
# ---------------------------------------------------------------------------

class TestSetupLabelerReturnsValidTypes:
    """label_episode returns a valid SetupType for any input."""

    def _base_ep(self, **kwargs) -> dict:
        ep = {
            "zone_types": ["vwap"],
            "reward_rev": 1.0,
            "reward_cont": 0.5,
            "forward_reversal_speed": 0.0,
            "price_vs_value": 0.0,
            "ib_closed": False,
            "touch_time_et": None,
            "has_gap": False,
            "has_single_print": False,
            "delta_ratio": 0.0,
        }
        ep.update(kwargs)
        return ep

    def test_returns_setup_type_instance(self):
        ep = self._base_ep()
        result = label_episode(ep)
        assert isinstance(result, SetupType), f"Expected SetupType, got {type(result)}"

    def test_returns_unknown_for_generic_episode(self):
        ep = self._base_ep()
        result = label_episode(ep)
        assert result == SetupType.UNKNOWN

    def test_failed_auction_labeled_correctly(self):
        ep = self._base_ep(
            zone_types=["pdh"],
            reward_rev=2.0,
            reward_cont=0.5,
            forward_reversal_speed=10.0,
        )
        result = label_episode(ep)
        assert result == SetupType.FAILED_AUCTION

    def test_single_print_fill_labeled_correctly(self):
        ep = self._base_ep(has_single_print=True)
        result = label_episode(ep)
        assert result == SetupType.SINGLE_PRINT_FILL

    def test_gap_fill_labeled_correctly(self):
        ep = self._base_ep(has_gap=True)
        result = label_episode(ep)
        assert result == SetupType.GAP_FILL

    def test_all_valid_setup_type_values_are_setup_type(self):
        """Every value in the SetupType enum must be an instance of SetupType."""
        for st in SetupType:
            assert isinstance(st, SetupType)

    def test_num_setup_types_excludes_unknown(self):
        """NUM_SETUP_TYPES should equal len(SetupType) - 1 (excludes UNKNOWN)."""
        assert NUM_SETUP_TYPES == len(SetupType) - 1, (
            f"NUM_SETUP_TYPES={NUM_SETUP_TYPES}, len(SetupType)-1={len(SetupType)-1}"
        )


# ---------------------------------------------------------------------------
# 4. test_backward_compatible_observation
# ---------------------------------------------------------------------------

class TestBackwardCompatibleObservation:
    """Legacy build_observation still returns 276-dim (zone mode) or 275-dim (legacy mode).

    The docstring in observation.py documents both:
      - Zone mode (state["zone"] present):  276 dims
      - Legacy mode (no zone):              275 dims

    OBSERVATION_DIM is computed at import using zone mode → 276.
    The task requirement "returns 276-dim" refers to zone mode.
    """

    def test_returns_276_dims_in_zone_mode(self):
        """Zone-mode state (zone present) must return 276 dims."""
        from src.rl.zone_builder import Zone, ZoneMember
        from src.rl.config import LevelType

        member = ZoneMember(name="vwap", level_type=LevelType.VWAP, price=19000.0)
        zone = Zone(
            center_price=19000.0,
            upper_bound=19001.0,
            lower_bound=18999.0,
            members=[member],
            composition=[1.0 if lt == LevelType.VWAP else 0.0 for lt in LevelType],
            width_ticks=8.0,
            member_count=1,
            hierarchy_score=0.5,
        )
        state = _minimal_state()
        state["zone"] = zone
        state["all_zones"] = [zone]
        obs = build_observation(state)
        assert obs.shape == (276,), f"Expected (276,), got {obs.shape}"

    def test_dtype_is_float32(self):
        state = _minimal_state()
        obs = build_observation(state)
        assert obs.dtype == np.float32

    def test_all_finite(self):
        state = _minimal_state()
        obs = build_observation(state)
        assert np.all(np.isfinite(obs)), f"Non-finite values at indices: {np.where(~np.isfinite(obs))[0]}"

    def test_observation_dim_constant_is_276(self):
        """OBSERVATION_DIM constant in observation.py must be 276 (zone mode)."""
        from src.rl.features.observation import OBSERVATION_DIM
        assert OBSERVATION_DIM == 276, f"OBSERVATION_DIM should be 276, got {OBSERVATION_DIM}"

    def test_legacy_mode_returns_275(self):
        """Legacy mode (no zone) returns 275 dims — one less than zone mode."""
        state = _minimal_state()
        obs = build_observation(state)
        assert obs.shape == (275,), f"Legacy mode: expected (275,), got {obs.shape}"


# ---------------------------------------------------------------------------
# 5. test_v5_exports_available
# ---------------------------------------------------------------------------

class TestV5ExportsAvailable:
    """build_narrative, build_trigger, TRIGGER_OBSERVATION_DIM are importable from observation.py."""

    def test_build_narrative_importable(self):
        from src.rl.features.observation import build_narrative
        assert callable(build_narrative)

    def test_build_trigger_importable(self):
        from src.rl.features.observation import build_trigger
        assert callable(build_trigger)

    def test_trigger_observation_dim_importable(self):
        from src.rl.features.observation import TRIGGER_OBSERVATION_DIM
        assert isinstance(TRIGGER_OBSERVATION_DIM, int)

    def test_trigger_observation_dim_is_141(self):
        assert TRIGGER_OBSERVATION_DIM == 141, (
            f"TRIGGER_OBSERVATION_DIM should be 141, got {TRIGGER_OBSERVATION_DIM}"
        )

    def test_build_narrative_returns_correct_shape(self):
        state = _minimal_state()
        obs = build_narrative(state)
        assert obs.shape == (NARRATIVE_DIM,), f"build_narrative: expected ({NARRATIVE_DIM},), got {obs.shape}"
        assert obs.dtype == np.float32

    def test_build_trigger_returns_correct_shape(self):
        state = _minimal_state()
        narrative = build_narrative(state)
        setup_probs = _make_setup_probs()
        obs = build_trigger(narrative, setup_probs, state)
        assert obs.shape == (TRIGGER_OBSERVATION_DIM,), (
            f"build_trigger: expected ({TRIGGER_OBSERVATION_DIM},), got {obs.shape}"
        )
        assert obs.dtype == np.float32

    def test_build_trigger_matches_direct_call(self):
        """build_trigger (from observation.py) should produce identical output to build_trigger_observation."""
        state = _minimal_state()
        narrative = build_narrative(state)
        setup_probs = _make_setup_probs()

        from src.rl.features.observation import build_trigger
        result_wrapper = build_trigger(narrative, setup_probs, state)

        base_obs = build_observation(state)
        result_direct = build_trigger_observation(narrative, setup_probs, state, base_obs)

        np.testing.assert_array_equal(
            result_wrapper,
            result_direct,
            err_msg="build_trigger wrapper and direct call should produce identical outputs",
        )
