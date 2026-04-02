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
from .features.observation import build_observation, OBSERVATION_DIM

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
            self._dqn = DQNetwork(input_dim=OBSERVATION_DIM)
            checkpoint = torch.load(path, weights_only=False, map_location="cpu")
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


# Keep backward-compatible alias
DQNLiveInference = LiveInference

_instance: LiveInference | None = None


def get_dqn_inference() -> LiveInference:
    """Get the global inference singleton."""
    global _instance
    if _instance is None:
        _instance = LiveInference()
        _instance.try_load()
    return _instance
