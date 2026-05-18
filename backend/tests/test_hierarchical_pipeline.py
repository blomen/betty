"""Integration smoke tests for the hierarchical observation pipeline (Phase 3b).

Verifies the pipeline from empty state through narrative → trigger. Phase 3b:
trigger obs no longer contains narrative or setup_probs; those signals feed
other downstream consumers (risk heads, composite confidence).
"""

from __future__ import annotations

import numpy as np

from src.rl.features.narrative_features import NARRATIVE_DIM, extract_narrative_features
from src.rl.features.observation import (
    TRIGGER_OBSERVATION_DIM,
    build_narrative,
    build_observation,
    build_trigger,
)
from src.rl.features.passthrough_features import PASSTHROUGH_DIM, extract_passthrough
from src.rl.features.trigger_features import TRIGGER_DIM, build_trigger_observation
from src.rl.labeling.setup_labeler import label_episode
from src.rl.labeling.setup_types import NUM_SETUP_TYPES, SetupType


def _minimal_state() -> dict:
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


class TestFullPipelineDimensions:
    def test_narrative_shape(self):
        obs = extract_narrative_features(_minimal_state())
        assert obs.shape == (NARRATIVE_DIM,)
        assert obs.dtype == np.float32

    def test_narrative_dim_is_18(self):
        assert NARRATIVE_DIM == 18

    def test_passthrough_shape(self):
        base = np.zeros(302, dtype=np.float32)
        out = extract_passthrough(base)
        assert out.shape == (PASSTHROUGH_DIM,)

    def test_passthrough_dim_is_10(self):
        assert PASSTHROUGH_DIM == 10

    def test_trigger_shape(self):
        state = _minimal_state()
        base_obs = build_observation(state)
        obs = build_trigger_observation(state, base_obs)
        assert obs.shape == (TRIGGER_DIM,)

    def test_trigger_dim_is_122(self):
        assert TRIGGER_DIM == 122

    def test_all_outputs_finite_from_empty_state(self):
        state = _minimal_state()
        narrative = extract_narrative_features(state)
        base_obs = build_observation(state)
        trigger = build_trigger_observation(state, base_obs)
        assert np.all(np.isfinite(narrative))
        assert np.all(np.isfinite(trigger))


class TestSetupLabelerReturnsValidTypes:
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
        assert isinstance(label_episode(self._base_ep()), SetupType)

    def test_returns_unknown_for_generic_episode(self):
        assert label_episode(self._base_ep()) == SetupType.UNKNOWN

    def test_failed_auction_labeled_correctly(self):
        ep = self._base_ep(
            zone_types=["pdh"],
            reward_rev=2.0,
            reward_cont=0.5,
            forward_reversal_speed=10.0,
        )
        assert label_episode(ep) == SetupType.FAILED_AUCTION

    def test_single_print_fill_labeled_correctly(self):
        assert label_episode(self._base_ep(has_single_print=True)) == SetupType.SINGLE_PRINT_FILL

    def test_gap_fill_labeled_correctly(self):
        assert label_episode(self._base_ep(has_gap=True)) == SetupType.GAP_FILL

    def test_num_setup_types_excludes_unknown(self):
        assert len(SetupType) - 1 == NUM_SETUP_TYPES


class TestV5ExportsAvailable:
    def test_build_narrative_importable(self):
        assert callable(build_narrative)

    def test_build_trigger_importable(self):
        assert callable(build_trigger)

    def test_trigger_observation_dim_is_122(self):
        assert TRIGGER_OBSERVATION_DIM == 122

    def test_build_narrative_returns_correct_shape(self):
        obs = build_narrative(_minimal_state())
        assert obs.shape == (NARRATIVE_DIM,)

    def test_build_trigger_returns_correct_shape(self):
        state = _minimal_state()
        obs = build_trigger(state)
        assert obs.shape == (TRIGGER_OBSERVATION_DIM,)

    def test_build_trigger_matches_direct_call(self):
        state = _minimal_state()
        result_wrapper = build_trigger(state)
        base_obs = build_observation(state)
        result_direct = build_trigger_observation(state, base_obs)
        np.testing.assert_array_equal(result_wrapper, result_direct)
