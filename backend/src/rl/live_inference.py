"""Live inference service — singleton for real-time level touch inference.

Supports both GBT (preferred) and DQN models. GBT is loaded first if available.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch

from .agent.gbt_model import GBTModel
from .agent.network import DQNetwork
from .config import Action, LevelType
from .data.normalization import RunningNormalizer
from .features.observation import OBSERVATION_DIM, build_observation

log = logging.getLogger(__name__)

_MODEL_SEARCH_DIRS = [
    Path("data/rl/models"),
    Path("backend/data/rl/models"),
    Path("data/rl"),
    Path("backend/data/rl"),
]


class LiveInference:
    """Loads a trained model (GBT or DQN) and runs inference."""

    def __init__(self) -> None:
        self._gbt: GBTModel | None = None
        self._dqn: DQNetwork | None = None
        self._normalizer: RunningNormalizer | None = None
        self._loaded = False
        self._model_type: str = "none"

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model_type(self) -> str:
        return self._model_type

    def try_load(self) -> bool:
        """Load both GBT (for decisions) and DQN (for visualization) if available."""
        gbt_loaded = False
        dqn_loaded = False

        for search_dir in _MODEL_SEARCH_DIRS:
            if not search_dir.exists():
                continue
            # Load GBT for decisions
            if not gbt_loaded:
                gbt_candidates = sorted(search_dir.glob("gbt_*.joblib"), key=lambda p: p.stat().st_mtime, reverse=True)
                if gbt_candidates:
                    gbt_loaded = self._load_gbt(gbt_candidates[0])
            # Load DQN for visualization (activations + connections)
            if not dqn_loaded:
                dqn_latest = search_dir / "dqn_latest.pt"
                if dqn_latest.exists():
                    dqn_loaded = self._load_dqn(dqn_latest)
                if not dqn_loaded:
                    dqn_candidates = sorted(search_dir.glob("dqn_*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if dqn_candidates:
                        dqn_loaded = self._load_dqn(dqn_candidates[0])

        if gbt_loaded:
            self._model_type = "gbt"
            self._loaded = True
        elif dqn_loaded:
            self._model_type = "dqn"
            self._loaded = True

        if not self._loaded:
            log.info("No model checkpoint found — live visualization will show empty architecture")
        else:
            log.info("Models loaded: GBT=%s DQN=%s (decisions=%s)", gbt_loaded, dqn_loaded, self._model_type)
        return self._loaded

    def _load_normalizer(self, model_path: Path) -> None:
        """Load normalizer from episodes dir (sibling to models dir)."""
        episodes_dir = model_path.parent.parent / "episodes"
        norm_path = episodes_dir / "normalizer.json"
        if norm_path.exists():
            self._normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
            self._normalizer.load(norm_path)
            log.info("Normalizer loaded from %s (count=%d)", norm_path, self._normalizer.count)

    def _load_gbt(self, path: Path) -> bool:
        try:
            self._gbt = GBTModel.load(path)
            self._load_normalizer(path)
            self._loaded = True
            self._model_type = "gbt"
            log.info("GBT model loaded from %s", path)
            return True
        except Exception:
            log.exception("Failed to load GBT from %s", path)
            self._gbt = None
            return False

    def _load_dqn(self, path: Path) -> bool:
        try:
            checkpoint = torch.load(path, weights_only=False, map_location="cpu")
            # Infer input dim from saved weights (handles both 276 base and 292 augmented)
            first_weight = checkpoint["q_network"]["feature_net.0.weight"]
            obs_dim = first_weight.shape[1]
            self._dqn = DQNetwork(input_dim=obs_dim)
            self._dqn.load_state_dict(checkpoint["q_network"])
            self._dqn.eval()
            self._load_normalizer(path)
            self._loaded = True
            self._model_type = "dqn"
            log.info("DQN model loaded from %s", path)
            return True
        except Exception:
            log.exception("Failed to load DQN checkpoint from %s", path)
            self._dqn = None
            return False

    def get_model(self) -> GBTModel | DQNetwork | None:
        """Return the loaded model for use with SessionManager."""
        if self._gbt is not None:
            return self._gbt
        return self._dqn

    def infer(self, state: dict) -> dict | None:
        """Run inference on a market state dict.

        When both GBT and DQN are loaded:
        - GBT provides the trading decision (action, confidence, stop)
        - DQN provides the visualization (activations, connections)
        The frontend gets both: accurate decisions + neural network visualization.
        """
        if not self._loaded:
            return None

        # Zone mode: no level_type conversion needed
        lt = state.get("level_type")
        if lt is not None and isinstance(lt, str):
            try:
                state["level_type"] = LevelType(lt)
            except ValueError:
                state["level_type"] = LevelType.VWAP

        obs = build_observation(state)
        if self._normalizer is not None:
            obs = self._normalizer.normalize(obs)

        result: dict = {"inputs": obs.tolist()}

        # DQN: always run for visualization if available
        if self._dqn is not None:
            obs_tensor = torch.from_numpy(obs).unsqueeze(0)
            with torch.no_grad():
                activations = self._dqn.forward_with_activations(obs_tensor)
                connections = self._dqn.extract_top_connections(activations, top_n=100)
            dqn_q = activations["q_values"][0].tolist()
            result["activations"] = {
                "layer1": activations["layer1"][0].tolist(),
                "layer2": activations["layer2"][0].tolist(),
                "layer3": activations["layer3"][0].tolist(),
                "layer4": activations["features"][0].tolist(),
            }
            result["connections"] = connections
            # Use DQN Q-values and action as defaults
            result["q_values"] = dqn_q
            result["action"] = Action(int(np.argmax(dqn_q))).name
            result["model_type"] = "dqn"

        # GBT: override decision if available (better accuracy)
        if self._gbt is not None:
            action_idx, confidence, prob_cont, prob_rev = self._gbt.predict_direction(obs)
            stop_ticks = self._gbt.predict_stop(obs)
            result["q_values"] = [prob_cont, prob_rev, 0.0]
            result["action"] = Action(action_idx).name
            result["confidence"] = confidence
            result["stop_ticks"] = stop_ticks
            result["model_type"] = "gbt+dqn" if self._dqn is not None else "gbt"
            # Fill visualization defaults if no DQN
            if "activations" not in result:
                result["activations"] = {}
                result["connections"] = []

        if "action" not in result:
            return None

        return result


class LiveInferenceV5:
    """Two-stage inference: narrative (slow) -> trigger (fast)."""

    def __init__(self) -> None:
        self._narrative_gbt = None
        self._trigger_gbt = None
        self._normalizer: RunningNormalizer | None = None
        self._narrative_cache: np.ndarray | None = None

    def try_load(self) -> bool:
        """Load v5 models (narrative + trigger GBTs)."""
        from .agent.narrative_gbt import NarrativeGBT
        from .agent.trigger_gbt import TriggerGBT

        narrative_loaded = False
        trigger_loaded = False

        for search_dir in _MODEL_SEARCH_DIRS:
            if not search_dir.exists():
                continue

            if not narrative_loaded:
                narrative_path = search_dir / "narrative_gbt_latest.joblib"
                if narrative_path.exists():
                    try:
                        self._narrative_gbt = NarrativeGBT.load(narrative_path)
                        narrative_loaded = True
                        log.info("NarrativeGBT loaded from %s", narrative_path)
                    except Exception:
                        log.exception("Failed to load NarrativeGBT from %s", narrative_path)

            if not trigger_loaded:
                trigger_path = search_dir / "trigger_gbt_latest.joblib"
                if trigger_path.exists():
                    try:
                        self._trigger_gbt = TriggerGBT.load(trigger_path)
                        trigger_loaded = True
                        log.info("TriggerGBT loaded from %s", trigger_path)
                    except Exception:
                        log.exception("Failed to load TriggerGBT from %s", trigger_path)

            # Also try to load normalizer from first search dir that has models
            if narrative_loaded and self._normalizer is None:
                episodes_dir = search_dir / "episodes"
                norm_path = episodes_dir / "normalizer.json"
                if norm_path.exists():
                    self._normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
                    self._normalizer.load(norm_path)
                    log.info(
                        "Normalizer loaded from %s (count=%d)",
                        norm_path,
                        self._normalizer.count,
                    )

        if narrative_loaded and trigger_loaded:
            log.info("LiveInferenceV5: both models loaded successfully")
        else:
            log.info(
                "LiveInferenceV5: narrative=%s trigger=%s",
                narrative_loaded,
                trigger_loaded,
            )
        return narrative_loaded and trigger_loaded

    def update_narrative(self, state: dict) -> None:
        """Update narrative signals. Call every 30min or on structural events."""
        from .features.narrative_features import extract_narrative_features

        self._narrative_cache = extract_narrative_features(state)

    def infer(self, state: dict) -> dict | None:
        """Run two-stage inference at a zone touch."""
        if self._narrative_gbt is None or self._trigger_gbt is None:
            return None

        # 1. Ensure narrative is up to date
        if self._narrative_cache is None:
            self.update_narrative(state)

        # 2. Build base observation (for passthrough extraction)
        lt = state.get("level_type")
        if lt is not None and isinstance(lt, str):
            try:
                state["level_type"] = LevelType(lt)
            except ValueError:
                state["level_type"] = LevelType.VWAP

        base_obs = build_observation(state)
        if self._normalizer is not None:
            base_obs = self._normalizer.normalize(base_obs)

        # 3. Get narrative features for setup prob prediction
        narrative = self._narrative_cache

        # 4. Get setup_probs from narrative GBT
        setup_probs_arr = self._narrative_gbt.predict_setup_probs(narrative)

        # 5. Build trigger observation without GBT forecast
        from .features.trigger_features import build_trigger_observation

        trigger_obs_no_gbt = build_trigger_observation(
            narrative=narrative,
            setup_probs=setup_probs_arr,
            state=state,
            base_observation=base_obs,
            trigger_gbt_forecast=None,
        )

        # 6. Get trigger GBT forecast
        gbt_forecast = self._trigger_gbt.predict_full(trigger_obs_no_gbt)

        # 7. Rebuild trigger observation WITH GBT forecast
        trigger_obs = build_trigger_observation(
            narrative=narrative,
            setup_probs=setup_probs_arr,
            state=state,
            base_observation=base_obs,
            trigger_gbt_forecast=gbt_forecast,
        )

        # 8. Final direction prediction from trigger GBT
        action_idx, confidence, prob_cont, prob_rev = self._trigger_gbt.predict_direction(trigger_obs)
        stop_ticks = self._trigger_gbt.predict_stop(trigger_obs)

        # Build setup_probs dict
        from .labeling.setup_types import SetupType

        setup_names = [s.value for s in SetupType if s != SetupType.UNKNOWN]
        setup_probs_dict = {name: float(setup_probs_arr[i]) for i, name in enumerate(setup_names)}

        # Build narrative dict
        from .features.narrative_features import NARRATIVE_NAMES

        narrative_dict = {name: float(narrative[i]) for i, name in enumerate(NARRATIVE_NAMES)}

        # Composite confidence scoring
        from .confidence import compute_composite_confidence, size_multiplier
        from .features.micro_features import extract_micro_features

        # Trade direction: +1 long, -1 short, 0 skip (action_idx 0=cont, 1=rev, 2=skip)
        approach_dir = state.get("approach_direction", "up")
        if action_idx == 2:  # skip
            trade_direction = 0
        elif action_idx == 0:  # continuation
            trade_direction = 1 if approach_dir == "up" else -1
        else:  # reversal
            trade_direction = -1 if approach_dir == "up" else 1

        # Zone quality features
        zone = state.get("zone")
        zone_confluence_weight = float(zone.hierarchy_score) if zone is not None else 0.5
        zone_member_count = int(zone.member_count) if zone is not None else 1

        # Micro features from recent ticks
        recent_ticks = state.get("recent_ticks", [])
        price = float(state.get("price", 0.0))
        micro_features = extract_micro_features(recent_ticks, price)

        # Q-spread: use directional conviction from trigger GBT (|prob_cont - prob_rev|)
        q_spread = abs(prob_cont - prob_rev)

        composite = compute_composite_confidence(
            setup_probs=setup_probs_arr,
            narrative=narrative,
            trigger_forecast=gbt_forecast,
            q_spread=q_spread,
            zone_confluence_weight=zone_confluence_weight,
            zone_member_count=zone_member_count,
            micro_features=micro_features,
            trade_direction=trade_direction,
        )

        return {
            "action": Action(action_idx).name,
            "confidence": float(confidence),
            "stop_ticks": float(stop_ticks),
            "setup_probs": setup_probs_dict,
            "narrative": narrative_dict,
            "composite_confidence": composite,
            "size_multiplier": size_multiplier(composite),
            "model_type": "v5_hierarchical",
        }


class LiveInferenceSpecialists:
    """Specialist ensemble inference — CONT and REV experts.

    Preferred over single-model approaches. Each specialist answers
    its own question independently, then the ensemble picks the
    higher-EV action.
    """

    def __init__(self) -> None:
        self._ensemble = None
        self._normalizer: RunningNormalizer | None = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def try_load(self) -> bool:
        from .agent.specialists import SpecialistEnsemble

        for search_dir in _MODEL_SEARCH_DIRS:
            if not search_dir.exists():
                continue
            for name in ["specialists_latest.joblib", "specialists_v5.joblib"]:
                path = search_dir / name
                if path.exists():
                    try:
                        self._ensemble = SpecialistEnsemble.load(path)
                        log.info("Specialists loaded from %s", path)

                        # Load normalizer
                        for ep_dir in [search_dir.parent / "episodes", search_dir / "episodes"]:
                            norm_path = ep_dir / "normalizer.json"
                            if norm_path.exists():
                                self._normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
                                self._normalizer.load(norm_path)
                                log.info("Normalizer loaded (count=%d)", self._normalizer.count)
                                break

                        self._loaded = True
                        return True
                    except Exception:
                        log.exception("Failed to load specialists from %s", path)
        return False

    def infer(self, state: dict) -> dict | None:
        """Run specialist inference at a zone touch."""
        if self._ensemble is None:
            return None

        obs = build_observation(state)
        if self._normalizer is not None:
            obs = self._normalizer.normalize(obs)

        decision = self._ensemble.decide(obs)

        # Map action string to Action enum for compatibility
        action_map = {"continuation": Action.CONTINUATION, "reversal": Action.REVERSAL, "skip": Action.SKIP}
        action = action_map.get(decision["action"], Action.SKIP)

        return {
            "action": action.name,
            "confidence": decision["confidence"],
            "cont_p": decision["cont_p"],
            "rev_p": decision["rev_p"],
            "cont_ev": decision["cont_ev"],
            "rev_ev": decision["rev_ev"],
            "sizing_signal": decision["sizing_signal"],
            "model_type": "specialists",
        }


# Keep backward-compatible alias
DQNLiveInference = LiveInference

_instance = None


def get_dqn_inference():
    """Get the global inference singleton. Prefers specialists > GBT > DQN."""
    global _instance
    if _instance is None:
        # Try specialists first (best model)
        spec = LiveInferenceSpecialists()
        if spec.try_load():
            _instance = spec
            log.info("Using specialist ensemble for live inference")
            return _instance

        # Fallback to GBT/DQN
        _instance = LiveInference()
        _instance.try_load()
    return _instance
