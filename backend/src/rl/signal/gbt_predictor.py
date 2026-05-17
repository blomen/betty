"""GBTPredictor — wraps the existing TriggerGBT in the ModelProtocol contract.

The current TriggerGBT outputs direction (CONT vs REV) + confidence +
stop_ticks. To meet the multi-task contract (direction CONTINUATION/REVERSAL/SKIP
+ magnitude + win-prob + duration), this wrapper:

- direction_logits: maps GBT's [p_cont, p_rev] to [p_cont, p_rev, p_skip].
  p_skip is derived as max(0, 1 - confidence) — uncertain predictions
  become more "skip-like". Tunable; default heuristic for v1.
- magnitude_R: approximated from GBT's stop_ticks → expected R as TP1_R=2.0
  scaled by confidence. Replaced by MultiTaskGBT in Task 5 when wired.
- win_probability: from GBT's conf (heuristic — proper head in Task 5).
- duration_bars: not in GBT — heuristic constant 5.0 (replaced in Task 5).
- uncertainty: 1.0 - confidence (rough; FT-T will have real ensemble std).

Until Task 5 lands the proper multi-task GBT heads, these are approximations.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.rl.agent.trigger_gbt import TriggerGBT

from .gbt_multitask import MultiTaskGBT
from .protocol import ModelProtocol
from .types import MultiTaskOutputs


class GBTPredictor(ModelProtocol):
    def __init__(self, gbt: TriggerGBT, multitask: MultiTaskGBT | None = None) -> None:
        super().__init__()
        self._gbt = gbt
        self._multitask = multitask  # None = use heuristics
        self.trigger_obs_dim = self._gbt.input_dim if hasattr(self._gbt, "input_dim") else 313

    @classmethod
    def load(
        cls,
        model_path: Path | str,
        multitask_path: Path | str | None = None,
    ) -> GBTPredictor:
        gbt = TriggerGBT.load(Path(model_path))
        multitask = MultiTaskGBT.load(Path(multitask_path)) if multitask_path else None
        return cls(gbt=gbt, multitask=multitask)

    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        action_idx, confidence, prob_cont, prob_rev = self._gbt.predict_direction(obs)

        # 3-class direction from 2-class GBT
        # p_skip heuristic: low conf → higher skip probability
        p_skip = max(0.0, 1.0 - confidence)
        # Renormalize CONT + REV to absorb the remaining (1 - p_skip)
        cont_rev_total = prob_cont + prob_rev
        if cont_rev_total > 0:
            p_cont = prob_cont / cont_rev_total * (1.0 - p_skip)
            p_rev = prob_rev / cont_rev_total * (1.0 - p_skip)
        else:
            p_cont = p_rev = (1.0 - p_skip) / 2.0

        # Multi-task heads if available, else heuristic
        if self._multitask is not None:
            mt = self._multitask.predict(obs)
            magnitude_R = mt["magnitude_R"]
            win_prob = mt["win_probability"]
            duration_bars = mt["duration_bars"]
        else:
            magnitude_R = 2.0 * confidence  # TP1_R = 2.0 baseline
            win_prob = float(confidence)
            duration_bars = 5.0

        return MultiTaskOutputs(
            direction_logits=[float(p_cont), float(p_rev), float(p_skip)],
            magnitude_R=float(magnitude_R),
            win_probability=float(win_prob),
            duration_bars=float(duration_bars),
            uncertainty=float(1.0 - confidence),
        )
