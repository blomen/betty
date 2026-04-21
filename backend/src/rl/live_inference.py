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
            # Infer input dim from saved weights (handles both 276 base and 292+ augmented)
            first_weight = checkpoint["q_network"]["encoder.0.weight"]
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

        raw_obs = build_observation(state)
        # DQN needs normalized obs; GBT has its own internal StandardScaler —
        # passing normalized input to GBT would double-normalize.
        norm_obs = self._normalizer.normalize(raw_obs) if self._normalizer is not None else raw_obs

        result: dict = {"inputs": raw_obs.tolist()}

        # DQN: always run for visualization if available (uses normalized obs)
        if self._dqn is not None:
            obs_tensor = torch.from_numpy(norm_obs).unsqueeze(0)
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

        # GBT: override decision if available (uses raw obs — GBT has internal scaler)
        if self._gbt is not None:
            action_idx, confidence, prob_cont, prob_rev = self._gbt.predict_direction(raw_obs)
            stop_ticks = self._gbt.predict_stop(raw_obs)
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
    """Two-stage hybrid inference: trigger (fast) → DQN (decision).

    Phase 3b: narrative GBT is decoupled from the trigger path and used only
    for narrative-alignment scoring in composite confidence. Setup
    identification is done by the trigger GBT from orderflow + level
    alignment, not from narrative-derived setup probabilities.
    - TriggerGBT: 118-dim trigger obs → direction + expected R + stop
    - DQN: augmented obs (base + GBT forecast + position state) → Q-values
    """

    def __init__(self) -> None:
        self._narrative_gbt = None  # optional; used only if present for narrative alignment
        self._trigger_gbt = None
        self._dqn = None
        self._size_model = None  # Phase 3c: optional trained size tier head
        self._early_exit_model = None  # Phase 3c: optional pump-and-retrace detector
        self._normalizer: RunningNormalizer | None = None
        self._narrative_cache: np.ndarray | None = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def try_load(self) -> bool:
        """Load v5 models (trigger GBT mandatory; narrative GBT + size model optional)."""
        from .agent.narrative_gbt import NarrativeGBT
        from .agent.trigger_gbt import TriggerGBT

        narrative_loaded = False
        trigger_loaded = False
        size_loaded = False

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

            if not size_loaded:
                size_path = search_dir / "size_model_latest.joblib"
                if size_path.exists():
                    try:
                        from .agent.size_model import SizeModel

                        self._size_model = SizeModel.load(size_path)
                        size_loaded = True
                        log.info("SizeModel loaded from %s", size_path)
                    except Exception:
                        log.exception("Failed to load SizeModel from %s", size_path)

            if self._early_exit_model is None:
                ee_path = search_dir / "early_exit_model_latest.joblib"
                if ee_path.exists():
                    try:
                        from .agent.early_exit_model import EarlyExitModel

                        self._early_exit_model = EarlyExitModel.load(ee_path)
                        log.info("EarlyExitModel loaded from %s", ee_path)
                    except Exception:
                        log.exception("Failed to load EarlyExitModel from %s", ee_path)

            # Try to load DQN for hybrid decision layer (optional — falls back to pure GBT)
            if self._dqn is None:
                dqn_path = search_dir / "dqn_latest.pt"
                if dqn_path.exists():
                    try:
                        ckpt = torch.load(dqn_path, weights_only=False, map_location="cpu")
                        w = ckpt["q_network"]["encoder.0.weight"]
                        dqn_dim = w.shape[1]
                        # Schema compatibility check — warn if the model was
                        # trained on a different feature schema than current code.
                        schema_path = search_dir / "dqn_v5_schema.json"
                        if schema_path.exists():
                            try:
                                from src.rl.features.registry import check_compatibility

                                ok, msg = check_compatibility(schema_path)
                                if not ok:
                                    log.warning(
                                        "DQN feature schema drift detected: %s. Inference may degrade — retrain recommended.",
                                        msg,
                                    )
                            except Exception as exc:
                                log.debug("schema check skipped: %s", exc)
                        self._dqn = DQNetwork(input_dim=dqn_dim)
                        self._dqn.load_state_dict(ckpt["q_network"])
                        self._dqn.eval()
                        self._dqn_input_dim = dqn_dim
                        log.info("DQN loaded from %s (input_dim=%d)", dqn_path, dqn_dim)
                    except Exception:
                        log.exception("Failed to load DQN from %s", dqn_path)
                        self._dqn = None

            # Also try to load normalizer from first search dir that has models
            if narrative_loaded and self._normalizer is None:
                episodes_dir = search_dir / "episodes"
                norm_path = episodes_dir / "normalizer.json"
                if norm_path.exists():
                    # Normalizer dim should match DQN's input dim; if saved smaller
                    # (hybrid augmentation), extend with identity stats for extras.
                    import json as _json

                    saved = _json.loads(norm_path.read_text())
                    saved_dim = saved.get("dim", OBSERVATION_DIM)
                    norm_dim = getattr(self, "_dqn_input_dim", OBSERVATION_DIM)
                    self._normalizer = RunningNormalizer(dim=norm_dim)
                    if saved_dim == norm_dim:
                        self._normalizer.load(norm_path)
                    elif saved_dim < norm_dim:
                        base_norm = RunningNormalizer(dim=saved_dim)
                        base_norm.load(norm_path)
                        self._normalizer.count = base_norm.count
                        self._normalizer.ewm_mean[:saved_dim] = base_norm.ewm_mean
                        self._normalizer.ewm_var[:saved_dim] = base_norm.ewm_var
                        log.info("Extended normalizer %d→%d for hybrid obs", saved_dim, norm_dim)
                    else:
                        log.warning("Saved normalizer dim %d > expected %d", saved_dim, norm_dim)
                    log.info(
                        "Normalizer loaded from %s (count=%d, dim=%d)",
                        norm_path,
                        self._normalizer.count,
                        norm_dim,
                    )

        if trigger_loaded:
            self._loaded = True
            log.info(
                "LiveInferenceV5: loaded (trigger+narrative=%s+DQN=%s)",
                narrative_loaded,
                self._dqn is not None,
            )
        else:
            log.info("LiveInferenceV5: trigger_loaded=False")
        return self._loaded

    def update_narrative(self, state: dict) -> None:
        """Update narrative signals. Call every 30min or on structural events."""
        from .features.narrative_features import extract_narrative_features

        self._narrative_cache = extract_narrative_features(state)

    def infer(self, state: dict) -> dict | None:
        """Run two-stage inference at a zone touch (Phase 3b)."""
        if self._trigger_gbt is None:
            return None

        # 1. Ensure narrative cache is populated for confidence scoring
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
            if self._normalizer.dim == len(base_obs):
                base_obs = self._normalizer.normalize(base_obs)
            else:
                std = np.sqrt(np.maximum(self._normalizer.ewm_var[: len(base_obs)], 1e-8))
                base_obs = ((base_obs - self._normalizer.ewm_mean[: len(base_obs)]) / std).astype(np.float32)

        narrative = self._narrative_cache

        # 3. Build trigger observation (no narrative / no setup_probs in Phase 3b)
        from .features.trigger_features import build_trigger_observation

        trigger_obs_no_gbt = build_trigger_observation(
            state=state,
            base_observation=base_obs,
            trigger_gbt_forecast=None,
        )

        # 4. Get trigger GBT forecast
        gbt_forecast = self._trigger_gbt.predict_full(trigger_obs_no_gbt)

        # 5. Rebuild trigger observation WITH GBT forecast
        trigger_obs = build_trigger_observation(
            state=state,
            base_observation=base_obs,
            trigger_gbt_forecast=gbt_forecast,
        )

        # 8. GBT direction prediction (primary)
        gbt_action, gbt_conf, prob_cont, prob_rev = self._trigger_gbt.predict_direction(trigger_obs)
        stop_ticks = self._trigger_gbt.predict_stop(trigger_obs)

        # 9. DQN ensemble (optional) — hybrid decision layer
        dqn_q_values = None
        dqn_action = None
        dqn_agrees = True
        if self._dqn is not None:
            try:
                # Build augmented obs: base + GBT forecast + position state
                # Use caller-provided position_state if available, else flat zeros.
                ps_raw = state.get("position_state")
                if ps_raw is not None:
                    position_state = np.asarray(ps_raw, dtype=np.float32).flatten()
                    if position_state.size != 8:
                        position_state = np.zeros(8, dtype=np.float32)
                else:
                    position_state = np.zeros(8, dtype=np.float32)
                augmented_obs = np.concatenate(
                    [base_obs.astype(np.float32), gbt_forecast.astype(np.float32), position_state]
                )
                # Pad or truncate to match DQN's trained input dim
                if len(augmented_obs) != self._dqn_input_dim:
                    if len(augmented_obs) < self._dqn_input_dim:
                        pad = np.zeros(self._dqn_input_dim - len(augmented_obs), dtype=np.float32)
                        augmented_obs = np.concatenate([augmented_obs, pad])
                    else:
                        augmented_obs = augmented_obs[: self._dqn_input_dim]
                # base_obs is already normalized above; GBT forecast is probabilities
                # in [0,1] and position_state is zeros — both already in model-friendly
                # scale, so no second normalization pass.
                obs_tensor = torch.from_numpy(augmented_obs).unsqueeze(0)
                with torch.no_grad():
                    q_values = self._dqn(obs_tensor)[0].numpy()
                dqn_q_values = q_values.tolist()
                dqn_action = int(np.argmax(q_values))
                # 0=CONT, 1=REV, 2=SKIP
                dqn_agrees = (dqn_action == gbt_action) or (dqn_action == 2)
            except Exception:
                log.debug("DQN inference failed", exc_info=True)

        # Decision: GBT primary. DQN disagreement reduces confidence (veto mechanism).
        action_idx = gbt_action
        if self._dqn is not None and not dqn_agrees:
            # DQN disagrees strongly with GBT — reduce effective confidence
            confidence = gbt_conf * 0.5
            log.info(
                "DQN disagrees with GBT: gbt=%d dqn=%d — reducing confidence %.3f → %.3f",
                gbt_action,
                dqn_action,
                gbt_conf,
                confidence,
            )
        else:
            confidence = gbt_conf

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

        # Q-spread: directional conviction from trigger GBT
        q_spread = abs(prob_cont - prob_rev)

        composite = compute_composite_confidence(
            narrative=narrative,
            trigger_forecast=gbt_forecast,
            q_spread=q_spread,
            zone_confluence_weight=zone_confluence_weight,
            zone_member_count=zone_member_count,
            micro_features=micro_features,
            trade_direction=trade_direction,
        )

        # Phase 3c: prefer SizeModel (trained) over the composite-tier heuristic.
        # Builds the same 318-dim augmented obs shape SizeModel/EarlyExitModel
        # trained on. Reused by the early_exit head below.
        size_source = "heuristic"
        size_mult = size_multiplier(composite)
        augmented = None
        if action_idx != 2:
            try:
                ps_raw = state.get("position_state")
                if ps_raw is not None:
                    pos = np.asarray(ps_raw, dtype=np.float32).flatten()
                    if pos.size != 8:
                        pos = np.zeros(8, dtype=np.float32)
                else:
                    pos = np.zeros(8, dtype=np.float32)
                augmented = np.concatenate([base_obs.astype(np.float32), gbt_forecast.astype(np.float32), pos])
            except Exception:
                log.debug("Failed to build augmented obs for size/early-exit heads", exc_info=True)

        if self._size_model is not None and augmented is not None:
            try:
                size_mult = float(self._size_model.predict_size(augmented))
                size_source = "size_model"
            except Exception:
                log.debug("SizeModel predict failed; falling back to heuristic", exc_info=True)

        # Phase 3c: early_exit probability — downstream session manager
        # attaches a +0.5R locked exit when this exceeds its threshold.
        early_exit_prob = 0.0
        if self._early_exit_model is not None and augmented is not None:
            try:
                early_exit_prob = float(self._early_exit_model.predict_proba(augmented))
            except Exception:
                log.debug("EarlyExitModel predict failed", exc_info=True)

        return {
            "inputs": base_obs.tolist(),
            "action": Action(action_idx).name,
            "confidence": float(confidence),
            "q_values": dqn_q_values if dqn_q_values is not None else [prob_cont, prob_rev, 0.0],
            "stop_ticks": float(stop_ticks),
            "narrative": narrative_dict,
            "composite_confidence": composite,
            "size_multiplier": size_mult,
            "size_source": size_source,
            "early_exit_prob": early_exit_prob,
            "dqn_action": dqn_action,
            "dqn_agrees": dqn_agrees,
            "gbt_action": gbt_action,
            "activations": {},
            "connections": [],
            "model_type": "v5_hybrid_gbt_dqn" if self._dqn is not None else "v5_hierarchical",
        }


class LiveInferenceSpecialists:
    """Specialist ensemble inference — CONT and REV experts.

    Preferred over single-model approaches. Each specialist answers
    its own question independently, then the ensemble picks the
    higher-EV action. Also loads DQN for neural network visualization.
    """

    def __init__(self) -> None:
        self._ensemble = None
        self._dqn: DQNetwork | None = None
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
                    except Exception:
                        log.exception("Failed to load specialists from %s", path)

            # Also load DQN for visualization (activations + connections)
            if self._dqn is None:
                for dqn_path in [search_dir / "dqn_latest.pt"] + sorted(
                    search_dir.glob("dqn_*.pt"), key=lambda p: p.stat().st_mtime, reverse=True
                ):
                    if dqn_path.exists():
                        try:
                            checkpoint = torch.load(dqn_path, weights_only=False, map_location="cpu")
                            first_weight = checkpoint["q_network"]["encoder.0.weight"]
                            obs_dim = first_weight.shape[1]
                            self._dqn = DQNetwork(input_dim=obs_dim)
                            self._dqn.load_state_dict(checkpoint["q_network"])
                            self._dqn.eval()
                            log.info("DQN loaded for visualization from %s", dqn_path)
                            break
                        except Exception:
                            log.debug("Could not load DQN from %s", dqn_path)

            if self._loaded:
                return True
        return False

    def infer(self, state: dict) -> dict | None:
        """Run specialist inference at a zone touch + DQN for visualization."""
        if self._ensemble is None:
            return None

        lt = state.get("level_type")
        if lt is not None and isinstance(lt, str):
            try:
                state["level_type"] = LevelType(lt)
            except ValueError:
                state["level_type"] = LevelType.VWAP

        obs = build_observation(state)
        if self._normalizer is not None:
            obs = self._normalizer.normalize(obs)

        decision = self._ensemble.decide(obs)

        # Map action string to Action enum for compatibility
        action_map = {"continuation": Action.CONTINUATION, "reversal": Action.REVERSAL, "skip": Action.SKIP}
        action = action_map.get(decision["action"], Action.SKIP)

        result = {
            "inputs": obs.tolist(),
            "action": action.name,
            "confidence": decision["confidence"],
            "cont_p": decision["cont_p"],
            "rev_p": decision["rev_p"],
            "cont_ev": decision["cont_ev"],
            "rev_ev": decision["rev_ev"],
            "sizing_signal": decision["sizing_signal"],
            "model_type": "specialists",
        }

        # Run DQN forward pass for visualization (activations + connections)
        if self._dqn is not None:
            try:
                obs_tensor = torch.from_numpy(obs).unsqueeze(0)
                with torch.no_grad():
                    activations = self._dqn.forward_with_activations(obs_tensor)
                    connections = self._dqn.extract_top_connections(activations, top_n=100)
                result["activations"] = {
                    "layer1": activations["layer1"][0].tolist(),
                    "layer2": activations["layer2"][0].tolist(),
                    "layer3": activations["layer3"][0].tolist(),
                    "layer4": activations["features"][0].tolist(),
                }
                result["connections"] = connections
                result["q_values"] = activations["q_values"][0].tolist()
                result["model_type"] = "specialists+dqn"
            except Exception:
                log.debug("DQN visualization forward pass failed", exc_info=True)

        return result


# Keep backward-compatible alias
DQNLiveInference = LiveInference

_instance = None


def get_dqn_inference():
    """Get the global inference singleton. Prefers v5 > specialists > GBT > DQN."""
    global _instance
    if _instance is None:
        # Try V5 two-stage inference first (narrative + trigger GBTs)
        v5 = LiveInferenceV5()
        if v5.try_load():
            _instance = v5
            log.info("Using V5 two-stage inference for live inference")
            return _instance

        # Try specialists
        spec = LiveInferenceSpecialists()
        if spec.try_load():
            _instance = spec
            log.info("Using specialist ensemble for live inference")
            return _instance

        # Fallback to GBT/DQN
        _instance = LiveInference()
        _instance.try_load()
    return _instance
