"""DQN live inference service — singleton for real-time level touch inference."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch

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


class DQNLiveInference:
    """Loads a trained DQN and runs inference with full activation capture."""

    def __init__(self) -> None:
        self._network: DQNetwork | None = None
        self._normalizer: RunningNormalizer | None = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def try_load(self) -> bool:
        """Attempt to find and load the newest DQN checkpoint."""
        for search_dir in _MODEL_SEARCH_DIRS:
            if not search_dir.exists():
                continue
            latest = search_dir / "dqn_latest.pt"
            if latest.exists():
                return self._load_checkpoint(latest)
            candidates = sorted(search_dir.glob("dqn_*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                return self._load_checkpoint(candidates[0])
        log.info("No DQN checkpoint found — live visualization will show empty architecture")
        return False

    def _load_checkpoint(self, path: Path) -> bool:
        try:
            self._network = DQNetwork(input_dim=OBSERVATION_DIM)
            checkpoint = torch.load(path, weights_only=False, map_location="cpu")
            self._network.load_state_dict(checkpoint["q_network"])
            self._network.eval()
            self._loaded = True
            log.info("DQN model loaded from %s", path)

            # Load normalizer from episodes dir (sibling to models dir)
            episodes_dir = path.parent.parent / "episodes"
            norm_path = episodes_dir / "normalizer.json"
            if norm_path.exists():
                self._normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
                self._normalizer.load(norm_path)
                log.info("Normalizer loaded from %s (count=%d)", norm_path, self._normalizer.count)

            return True
        except Exception:
            log.exception("Failed to load DQN checkpoint from %s", path)
            self._network = None
            self._loaded = False
            return False

    def infer(self, state: dict) -> dict | None:
        """Run inference on a market state dict.

        Returns None if no model is loaded.
        Returns full payload with inputs, activations, q_values, action, connections.
        """
        if not self._loaded or self._network is None:
            return None

        # Zone mode: no level_type conversion needed (zone object already present)
        # Legacy mode: convert string level_type to LevelType enum
        lt = state.get("level_type")
        if lt is not None and isinstance(lt, str):
            try:
                state["level_type"] = LevelType(lt)
            except ValueError:
                state["level_type"] = LevelType.VWAP

        obs = build_observation(state)
        if self._normalizer is not None:
            obs = self._normalizer.normalize(obs)
        obs_tensor = torch.from_numpy(obs).unsqueeze(0)

        with torch.no_grad():
            activations = self._network.forward_with_activations(obs_tensor)
            connections = self._network.extract_top_connections(activations, top_n=100)

        q_vals = activations["q_values"][0].tolist()
        action_idx = int(np.argmax(q_vals))
        action_name = Action(action_idx).name

        return {
            "inputs": obs.tolist(),
            "activations": {
                "layer1": activations["layer1"][0].tolist(),
                "layer2": activations["layer2"][0].tolist(),
                "layer3": activations["layer3"][0].tolist(),
                "layer4": activations["features"][0].tolist(),
            },
            "q_values": q_vals,
            "action": action_name,
            "connections": connections,
        }


_instance: DQNLiveInference | None = None


def get_dqn_inference() -> DQNLiveInference:
    """Get the global DQN inference singleton."""
    global _instance
    if _instance is None:
        _instance = DQNLiveInference()
        _instance.try_load()
    return _instance
