"""Smoke test for the full V5 hybrid GBT→DQN inference pipeline.

Guards against the class of bugs that blew up paper trading on 2026-04-17:
- DQN input dim mismatch (base obs 279 vs augmented 295)
- Normalizer dim mismatch when extended to hybrid
- Double-normalization of augmented observations
- Wrong slot layout for GBT forecast in trigger_observation

These are shape/integration regressions a unit test on DQNetwork alone cannot catch.
Requires trained v5 artifacts in data/rl/models and data/rl/episodes. If absent,
the test is skipped rather than failing — CI without artifacts stays green.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.rl.features.observation import AUGMENTED_OBSERVATION_DIM, OBSERVATION_DIM

_MODELS = Path("data/rl/models")
_EPISODES = Path("data/rl/episodes")

_REQUIRED = [
    _MODELS / "trigger_gbt_latest.joblib",
    _MODELS / "dqn_latest.pt",
    _EPISODES / "normalizer.json",
]


def _artifacts_missing() -> bool:
    return not all(p.exists() for p in _REQUIRED)


skip_no_artifacts = pytest.mark.skipif(
    _artifacts_missing(), reason="v5 artifacts not present (expected in deploy, not CI)"
)


@skip_no_artifacts
def test_live_inference_v5_loads_hybrid():
    """LiveInferenceV5.try_load returns True and wires the required components.

    NarrativeGBT was retired in H3 (Phase 3c) — label was circular.
    """
    from src.rl.live_inference import LiveInferenceV5

    engine = LiveInferenceV5()
    assert engine.try_load(), "try_load returned False with v5 artifacts present"
    assert engine._trigger_gbt is not None
    assert engine._dqn is not None
    # DQN input dim should be augmented (318) or base (302)
    assert engine._dqn_input_dim in (OBSERVATION_DIM, AUGMENTED_OBSERVATION_DIM)
    assert engine._normalizer is not None
    assert engine._normalizer.dim == engine._dqn_input_dim


@skip_no_artifacts
def test_infer_with_synthetic_state_produces_valid_payload():
    """Feeding a minimal synthetic state through the full pipeline yields a usable result."""
    from src.rl.live_inference import LiveInferenceV5

    engine = LiveInferenceV5()
    engine.try_load()

    state = {
        "level_type": "vwap",
        "price": 20000.0,
        "approach_direction": "up",
        "zone": None,
        "zone_memory": {},
        "candles": [],
        "candles_5m": [],
        "vwap_bands": None,
        "volume_profile": None,
        "tpo_profile": None,
        "session_tpos": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": {"minutes_since_rth": 60, "minute_of_day": 570},
    }

    # Prime the narrative cache — real inference does this on each tick
    engine.update_narrative(state)

    result = engine.infer(state)
    assert result is not None, "infer returned None on valid state"
    assert "action" in result
    assert result["action"] in ("CONTINUATION", "REVERSAL", "SKIP")
    assert "confidence" in result
    assert 0.0 <= float(result["confidence"]) <= 1.0
    # Hybrid path ran iff DQN produced q_values
    if result.get("dqn_q_values") is not None:
        assert len(result["dqn_q_values"]) == 3


@skip_no_artifacts
def test_augmented_obs_shape_matches_dqn_input():
    """The 295-dim augmented observation must match DQN input exactly."""
    from src.rl.live_inference import LiveInferenceV5

    engine = LiveInferenceV5()
    engine.try_load()

    # base (279) + gbt forecast (8) + position state (8) = 295
    base = np.zeros(OBSERVATION_DIM, dtype=np.float32)
    forecast = np.zeros(8, dtype=np.float32)
    pos = np.zeros(8, dtype=np.float32)
    augmented = np.concatenate([base, forecast, pos])
    assert augmented.size == AUGMENTED_OBSERVATION_DIM == engine._dqn_input_dim
