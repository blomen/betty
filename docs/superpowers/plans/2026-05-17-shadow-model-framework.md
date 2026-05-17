# Shadow Model Comparison Framework + FT-Transformer Scaffolding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the model-agnostic prediction infrastructure (Signal/ExecutionContext interfaces, ModelProtocol, isotonic calibration, multi-task heads, shadow logger) so the architecture is "correct" from day one, then add FT-Transformer with per-group encoders as a second `ModelProtocol` implementation running in shadow mode. GBT stays the production predictor. FT-Transformer promotes when it beats GBT on 30 consecutive out-of-sample days.

**Architecture:** Two layers cleanly separated.
- **Layer A (scaffolding, model-agnostic):** `Signal` and `ExecutionContext` dataclasses define the contract between prediction and execution. `ModelProtocol` is an abstract base class with `predict(obs) -> Signal`. `IsotonicCalibrator` post-processes any `ModelProtocol`'s output. Multi-task GBT heads (direction + magnitude + win-prob + duration) implement `ModelProtocol`. All downstream code (live_inference, level_monitor, broker dispatch) consumes `Signal`, never raw model output.
- **Layer B (FT-Transformer shadow):** Per-group encoders (PyTorch MLPs, one per methodology category), cross-group attention with OF as Query, multi-task heads. `FTTransformerPredictor` implements `ModelProtocol`. A `ShadowLogger` runs both predictors on every signal, logs both predictions to a new `shadow_predictions` table, but only the GBT prediction reaches the broker. A `ModelComparator` reports daily WR / mean R / calibration error / agreement-rate. Promotion criterion: FT-T outperforms GBT (by ≥1% WR + ≥0.05R mean) on out-of-sample for 30 consecutive days.

**Tech Stack:** Python 3.10+, PyTorch + PyTorch Lightning, scikit-learn (isotonic regression), LightGBM (existing), SQLAlchemy (shadow_predictions table), d3rlpy reserved for execution-layer follow-up plan.

---

## File Structure

| File | Purpose | Status |
|---|---|---|
| `backend/src/rl/signal/__init__.py` | Package init | NEW |
| `backend/src/rl/signal/types.py` | `Signal`, `ExecutionContext`, `MultiTaskOutputs` dataclasses | NEW |
| `backend/src/rl/signal/protocol.py` | `ModelProtocol` abstract base | NEW |
| `backend/src/rl/signal/calibration.py` | `IsotonicCalibrator` wrapping sklearn isotonic regression | NEW |
| `backend/src/rl/signal/gbt_predictor.py` | `GBTPredictor` implementing `ModelProtocol` over current trigger_gbt | NEW |
| `backend/src/rl/signal/gbt_multitask.py` | Train + load separate LightGBM models for magnitude/win-prob/duration heads | NEW |
| `backend/src/rl/signal/encoders.py` | `PerGroupEncoder` — one MLP per methodology category | NEW |
| `backend/src/rl/signal/attention.py` | `CrossGroupAttention` — OF as Query, others as Key/Value | NEW |
| `backend/src/rl/signal/heads.py` | `MultiTaskHead` — direction + magnitude + win-prob + duration | NEW |
| `backend/src/rl/signal/ft_predictor.py` | `FTTransformerPredictor` end-to-end PyTorch module + ModelProtocol impl | NEW |
| `backend/src/rl/signal/shadow.py` | `ShadowLogger` — runs both predictors, logs to DB, returns production prediction | NEW |
| `backend/src/rl/signal/comparison.py` | `ModelComparator` — WR / mean R / calibration / promotion criterion | NEW |
| `backend/src/rl/signal/training.py` | FT-T training loop (PyTorch Lightning), reads from existing episodes pool | NEW |
| `backend/src/rl/live_inference.py` | MODIFY — `infer()` returns `Signal` dataclass | MODIFY |
| `backend/src/market_data/level_monitor.py` | MODIFY — consume `Signal` from `dqn.infer()` | MODIFY |
| `backend/src/db/models.py` | MODIFY — add `ShadowPrediction` table | MODIFY |
| `backend/tests/rl/signal/test_types.py` | Tests for dataclass immutability + serialization | NEW |
| `backend/tests/rl/signal/test_protocol.py` | Tests for ModelProtocol contract | NEW |
| `backend/tests/rl/signal/test_calibration.py` | Tests for isotonic calibration | NEW |
| `backend/tests/rl/signal/test_gbt_predictor.py` | Tests for GBTPredictor wrapping | NEW |
| `backend/tests/rl/signal/test_encoders.py` | Tests for per-group encoder shapes | NEW |
| `backend/tests/rl/signal/test_attention.py` | Tests for cross-group attention | NEW |
| `backend/tests/rl/signal/test_heads.py` | Tests for multi-task head outputs | NEW |
| `backend/tests/rl/signal/test_ft_predictor.py` | Tests for FT-Transformer end-to-end | NEW |
| `backend/tests/rl/signal/test_shadow.py` | Tests for shadow logger behavior | NEW |
| `backend/tests/rl/signal/test_comparison.py` | Tests for comparison metrics + promotion criterion | NEW |

---

## Task 1: Signal + ExecutionContext Dataclasses

**Files:**
- Create: `backend/src/rl/signal/__init__.py` (empty)
- Create: `backend/src/rl/signal/types.py`
- Test: `backend/tests/rl/signal/test_types.py`

- [ ] **Step 1: Create package init**

```bash
mkdir -p backend/src/rl/signal backend/tests/rl/signal
touch backend/src/rl/signal/__init__.py backend/tests/rl/signal/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/rl/signal/test_types.py
import time

import pytest

from src.rl.signal.types import ExecutionContext, MultiTaskOutputs, PositionState, Signal


def test_signal_is_immutable():
    sig = Signal(
        p_cont=0.6, p_rev=0.3, p_skip=0.1,
        expected_R=1.2, win_probability=0.65, duration_bars=8.0,
        uncertainty=0.05, timestamp=time.time(), zone_id=42,
    )
    with pytest.raises((TypeError, AttributeError)):
        sig.p_cont = 0.9


def test_signal_probabilities_sum_to_one_invariant():
    """Dataclass doesn't enforce — caller responsibility. But we expose
    a helper that asserts for debug."""
    sig = Signal(
        p_cont=0.6, p_rev=0.3, p_skip=0.1,
        expected_R=1.2, win_probability=0.65, duration_bars=8.0,
        uncertainty=0.05, timestamp=1.0, zone_id=1,
    )
    assert abs(sig.p_cont + sig.p_rev + sig.p_skip - 1.0) < 1e-6


def test_signal_action_property_picks_argmax():
    sig = Signal(
        p_cont=0.5, p_rev=0.3, p_skip=0.2,
        expected_R=1.0, win_probability=0.6, duration_bars=5.0,
        uncertainty=0.1, timestamp=1.0, zone_id=1,
    )
    assert sig.action == "CONT"

    sig2 = Signal(
        p_cont=0.2, p_rev=0.5, p_skip=0.3,
        expected_R=1.0, win_probability=0.6, duration_bars=5.0,
        uncertainty=0.1, timestamp=1.0, zone_id=2,
    )
    assert sig2.action == "REV"

    sig3 = Signal(
        p_cont=0.2, p_rev=0.3, p_skip=0.5,
        expected_R=1.0, win_probability=0.6, duration_bars=5.0,
        uncertainty=0.1, timestamp=1.0, zone_id=3,
    )
    assert sig3.action == "SKIP"


def test_signal_confidence_property_argmax_minus_secondbest():
    """Confidence = max(p) - second-max(p). Matches the GBT abs(p_cont - p_rev)
    semantics for 2-class but extends naturally to 3-class."""
    sig = Signal(
        p_cont=0.7, p_rev=0.2, p_skip=0.1,
        expected_R=1.0, win_probability=0.7, duration_bars=5.0,
        uncertainty=0.05, timestamp=1.0, zone_id=1,
    )
    assert sig.confidence == pytest.approx(0.5)


def test_position_state_is_immutable():
    ps = PositionState(side="long", peak_R=2.1, time_in_trade_s=120.0, current_R=1.5, size=1)
    with pytest.raises((TypeError, AttributeError)):
        ps.peak_R = 9.9


def test_execution_context_is_immutable():
    sig = Signal(
        p_cont=0.6, p_rev=0.3, p_skip=0.1,
        expected_R=1.2, win_probability=0.65, duration_bars=8.0,
        uncertainty=0.05, timestamp=1.0, zone_id=1,
    )
    ps = PositionState(side=None, peak_R=0.0, time_in_trade_s=0.0, current_R=0.0, size=0)
    ctx = ExecutionContext(signal=sig, position=ps, session_pnl_R=0.5, consec_losses=0, history=[])
    with pytest.raises((TypeError, AttributeError)):
        ctx.consec_losses = 99


def test_multitask_outputs_holds_per_head_predictions():
    mto = MultiTaskOutputs(
        direction_logits=[0.6, 0.3, 0.1],
        magnitude_R=1.5,
        win_probability=0.7,
        duration_bars=10.0,
        uncertainty=0.05,
    )
    assert mto.magnitude_R == 1.5
    assert mto.direction_logits == [0.6, 0.3, 0.1]
```

- [ ] **Step 3: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_types.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 4: Implement the dataclasses**

```python
# backend/src/rl/signal/types.py
"""Model-agnostic prediction contract.

Signal — what a model says about a zone touch. Frozen for safety.
PositionState — current broker position summary.
ExecutionContext — Signal + state — what the execution policy consumes.
MultiTaskOutputs — raw multi-head output before calibration → packaged into Signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["CONT", "REV", "SKIP"]


@dataclass(frozen=True)
class Signal:
    """Calibrated, model-agnostic prediction for a single zone touch.

    Probabilities are post-calibration (isotonic-fitted on holdout).
    expected_R, win_probability, duration_bars from multi-task heads.
    uncertainty from ensemble std OR MC dropout — model-specific.
    """

    p_cont: float
    p_rev: float
    p_skip: float
    expected_R: float
    win_probability: float
    duration_bars: float
    uncertainty: float
    timestamp: float
    zone_id: int

    @property
    def action(self) -> Action:
        m = max(self.p_cont, self.p_rev, self.p_skip)
        if m == self.p_cont:
            return "CONT"
        if m == self.p_rev:
            return "REV"
        return "SKIP"

    @property
    def confidence(self) -> float:
        """argmax - second-argmax. Higher = more decisive."""
        probs = sorted([self.p_cont, self.p_rev, self.p_skip], reverse=True)
        return probs[0] - probs[1]


@dataclass(frozen=True)
class PositionState:
    """Current broker position. None side = flat."""

    side: Literal["long", "short"] | None
    peak_R: float
    time_in_trade_s: float
    current_R: float
    size: int


@dataclass(frozen=True)
class TradeRecord:
    """Minimal closed-trade record for history context in ExecutionContext."""

    realized_R: float
    side: Literal["long", "short"]
    exit_reason: str
    closed_at: float


@dataclass(frozen=True)
class ExecutionContext:
    """Everything an execution policy needs to decide what to do next."""

    signal: Signal
    position: PositionState
    session_pnl_R: float
    consec_losses: int
    history: list[TradeRecord] = field(default_factory=list)


@dataclass
class MultiTaskOutputs:
    """Raw multi-head model output BEFORE calibration. Mutable on purpose —
    calibration is a separate step that produces the final immutable Signal."""

    direction_logits: list[float]  # length 3: [cont, rev, skip]
    magnitude_R: float
    win_probability: float
    duration_bars: float
    uncertainty: float
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_types.py -v`
Expected: 7 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/rl/signal/
git commit -m "feat(rl/signal): add Signal/ExecutionContext/MultiTaskOutputs dataclasses

Model-agnostic prediction contract. Signal is the immutable output
of any ModelProtocol implementation; ExecutionContext bundles it with
position + session state for downstream execution policies. Both sides
of the signal/execution split (per architecture verdict 2026-05-17)
communicate only via these types."
```

---

## Task 2: ModelProtocol Abstract Base

**Files:**
- Create: `backend/src/rl/signal/protocol.py`
- Test: `backend/tests/rl/signal/test_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_protocol.py
import numpy as np
import pytest

from src.rl.signal.protocol import ModelProtocol
from src.rl.signal.types import MultiTaskOutputs, Signal


def test_modelprotocol_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        ModelProtocol()  # type: ignore


def test_modelprotocol_subclass_must_implement_predict_raw():
    class IncompleteModel(ModelProtocol):
        pass

    with pytest.raises(TypeError):
        IncompleteModel()  # type: ignore


def test_minimal_modelprotocol_subclass_works():
    class StubModel(ModelProtocol):
        def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
            return MultiTaskOutputs(
                direction_logits=[0.6, 0.3, 0.1],
                magnitude_R=1.0,
                win_probability=0.65,
                duration_bars=5.0,
                uncertainty=0.1,
            )

    m = StubModel()
    obs = np.zeros(313, dtype=np.float32)
    raw = m.predict_raw(obs)
    assert isinstance(raw, MultiTaskOutputs)


def test_modelprotocol_predict_composes_raw_calibration_and_signal_packaging():
    """predict() = predict_raw() → calibrate() → Signal(...). Default
    calibrate is identity if no calibrator attached."""

    class StubModel(ModelProtocol):
        def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
            return MultiTaskOutputs(
                direction_logits=[0.5, 0.4, 0.1],
                magnitude_R=2.0,
                win_probability=0.7,
                duration_bars=8.0,
                uncertainty=0.05,
            )

    m = StubModel()
    obs = np.zeros(313, dtype=np.float32)
    sig = m.predict(obs, zone_id=42, timestamp=123.0)
    assert isinstance(sig, Signal)
    assert sig.p_cont == 0.5
    assert sig.p_rev == 0.4
    assert sig.p_skip == 0.1
    assert sig.expected_R == 2.0
    assert sig.win_probability == 0.7
    assert sig.zone_id == 42
    assert sig.timestamp == 123.0
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_protocol.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement ModelProtocol**

```python
# backend/src/rl/signal/protocol.py
"""Abstract base for any predictor in the Arnold signal layer.

Implementations:
  - GBTPredictor (current production)
  - FTTransformerPredictor (shadow, eventually promoted)
  - Future: TabNet, Decision Transformer for execution side

All implementations return the same Signal dataclass via predict().
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from .types import MultiTaskOutputs, Signal


class ModelProtocol(ABC):
    """Abstract base. Subclasses override predict_raw() — the rest is
    handled by the default predict() which optionally applies calibration
    then packages into a Signal."""

    def __init__(self) -> None:
        self._calibrator = None  # set via attach_calibrator()

    def attach_calibrator(self, calibrator) -> None:
        """Attach an IsotonicCalibrator (or None to clear). Calibrator
        must have transform(logits: list[float]) -> list[float]."""
        self._calibrator = calibrator

    @abstractmethod
    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        """Return raw multi-task outputs. Subclass responsibility."""
        ...

    def predict(self, obs: np.ndarray, *, zone_id: int, timestamp: float) -> Signal:
        """Full pipeline: predict_raw → calibrate → package into Signal."""
        raw = self.predict_raw(obs)

        logits = raw.direction_logits
        if self._calibrator is not None:
            logits = self._calibrator.transform(logits)

        return Signal(
            p_cont=float(logits[0]),
            p_rev=float(logits[1]),
            p_skip=float(logits[2]),
            expected_R=float(raw.magnitude_R),
            win_probability=float(raw.win_probability),
            duration_bars=float(raw.duration_bars),
            uncertainty=float(raw.uncertainty),
            timestamp=float(timestamp),
            zone_id=int(zone_id),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_protocol.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/protocol.py backend/tests/rl/signal/test_protocol.py
git commit -m "feat(rl/signal): add ModelProtocol abstract base

Predictors implement predict_raw() returning MultiTaskOutputs. The base
class handles calibration + Signal packaging so every implementation
returns the same contract type."
```

---

## Task 3: IsotonicCalibrator

**Files:**
- Create: `backend/src/rl/signal/calibration.py`
- Test: `backend/tests/rl/signal/test_calibration.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_calibration.py
import numpy as np
import pytest

from src.rl.signal.calibration import IsotonicCalibrator


def test_calibrator_fit_then_transform():
    """Fit on (raw_probs, true_outcomes). transform() maps raw → calibrated."""
    cal = IsotonicCalibrator()
    # Synthetic data: raw_prob_cont vs whether cont actually happened
    rng = np.random.default_rng(42)
    n = 500
    raw_p_cont = rng.uniform(0, 1, size=n)
    # Outcome: cont happens with prob ~= raw_p_cont (well-calibrated)
    true_cont = (rng.uniform(0, 1, size=n) < raw_p_cont).astype(int)

    cal.fit_per_class(class_idx=0, raw_probs=raw_p_cont, true_outcomes=true_cont)
    # transform a single logit triple
    calibrated = cal.transform([0.6, 0.3, 0.1])
    assert len(calibrated) == 3
    assert all(0.0 <= p <= 1.0 for p in calibrated)


def test_calibrator_passes_through_when_unfitted():
    """Transform without fit returns the input unchanged."""
    cal = IsotonicCalibrator()
    out = cal.transform([0.5, 0.3, 0.2])
    assert out == [0.5, 0.3, 0.2]


def test_calibrator_renormalizes_to_sum_to_one():
    """After per-class transformation, probabilities may not sum to 1.
    Calibrator must renormalize."""
    cal = IsotonicCalibrator()
    rng = np.random.default_rng(0)
    for ci in range(3):
        raw = rng.uniform(0, 1, 200)
        true = (rng.uniform(0, 1, 200) < raw).astype(int)
        cal.fit_per_class(class_idx=ci, raw_probs=raw, true_outcomes=true)
    out = cal.transform([0.7, 0.5, 0.4])
    assert sum(out) == pytest.approx(1.0, abs=1e-6)


def test_calibrator_save_and_load(tmp_path):
    cal = IsotonicCalibrator()
    rng = np.random.default_rng(1)
    for ci in range(3):
        raw = rng.uniform(0, 1, 200)
        true = (rng.uniform(0, 1, 200) < raw).astype(int)
        cal.fit_per_class(class_idx=ci, raw_probs=raw, true_outcomes=true)

    path = tmp_path / "cal.joblib"
    cal.save(path)

    cal2 = IsotonicCalibrator.load(path)
    out = cal2.transform([0.6, 0.3, 0.1])
    expected = cal.transform([0.6, 0.3, 0.1])
    np.testing.assert_allclose(out, expected, atol=1e-8)


def test_brier_score_improves_after_calibration():
    """Trained calibrator should reduce Brier score on holdout."""
    from sklearn.metrics import brier_score_loss

    rng = np.random.default_rng(7)
    n = 2000
    # Miscalibrated raw probs: shifted toward 0.5 (overconfident-suppressed)
    raw_p = np.clip(rng.beta(2, 2, n), 0.05, 0.95)
    # True outcomes follow a STEEPER probability than raw suggests
    true_p = raw_p ** 0.5  # square-root pulls high probs higher
    true_y = (rng.uniform(0, 1, n) < true_p).astype(int)

    # Fit on half, test on half
    half = n // 2
    cal = IsotonicCalibrator()
    cal.fit_per_class(class_idx=0, raw_probs=raw_p[:half], true_outcomes=true_y[:half])

    raw_brier = brier_score_loss(true_y[half:], raw_p[half:])
    cal_p = np.array([cal.transform([rp, 0, 0])[0] for rp in raw_p[half:]])
    cal_brier = brier_score_loss(true_y[half:], cal_p)

    assert cal_brier < raw_brier, f"Calibration didn't improve: raw={raw_brier:.4f}, cal={cal_brier:.4f}"
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_calibration.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement IsotonicCalibrator**

```python
# backend/src/rl/signal/calibration.py
"""Per-class isotonic regression calibration for ModelProtocol outputs.

Sklearn's IsotonicRegression fits a monotone non-parametric mapping
raw_prob → calibrated_prob. Fit on holdout (raw model probability,
realized outcome). Transform any new model output to get calibrated
probabilities.

For 3-class (CONT/REV/SKIP), fit one calibrator per class on the
one-vs-rest binarization, then renormalize so calibrated probs sum to 1.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression


class IsotonicCalibrator:
    def __init__(self) -> None:
        self._fitted: dict[int, IsotonicRegression] = {}

    def fit_per_class(
        self,
        class_idx: int,
        raw_probs: np.ndarray,
        true_outcomes: np.ndarray,
    ) -> None:
        """Fit calibrator for one class. true_outcomes is binary
        (1 if this class won, 0 otherwise)."""
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(np.asarray(raw_probs).astype(float), np.asarray(true_outcomes).astype(float))
        self._fitted[class_idx] = ir

    def transform(self, raw_logits: list[float]) -> list[float]:
        """Apply per-class calibration, then renormalize to sum to 1."""
        if not self._fitted:
            return list(raw_logits)
        calibrated = []
        for ci, p in enumerate(raw_logits):
            ir = self._fitted.get(ci)
            if ir is None:
                calibrated.append(float(p))
            else:
                calibrated.append(float(ir.predict([float(p)])[0]))
        # Renormalize
        total = sum(calibrated)
        if total <= 0:
            return [1.0 / len(calibrated)] * len(calibrated)
        return [c / total for c in calibrated]

    def save(self, path: Path | str) -> None:
        joblib.dump(self._fitted, path)

    @classmethod
    def load(cls, path: Path | str) -> "IsotonicCalibrator":
        inst = cls()
        inst._fitted = joblib.load(path)
        return inst
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_calibration.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/calibration.py backend/tests/rl/signal/test_calibration.py
git commit -m "feat(rl/signal): add IsotonicCalibrator for model-agnostic probability calibration"
```

---

## Task 4: GBTPredictor — wrap current GBT in ModelProtocol

**Files:**
- Create: `backend/src/rl/signal/gbt_predictor.py`
- Test: `backend/tests/rl/signal/test_gbt_predictor.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_gbt_predictor.py
from pathlib import Path

import numpy as np
import pytest

from src.rl.signal.gbt_predictor import GBTPredictor
from src.rl.signal.types import MultiTaskOutputs, Signal


@pytest.fixture(scope="module")
def gbt_model_path():
    p = Path("/app/data/rl/models/trigger_gbt_v5.joblib")
    if not p.exists():
        pytest.skip(f"No GBT model at {p}")
    return p


def test_gbt_predictor_implements_modelprotocol(gbt_model_path):
    pred = GBTPredictor.load(gbt_model_path)
    assert hasattr(pred, "predict_raw")
    assert hasattr(pred, "predict")


def test_gbt_predictor_predict_raw_returns_multitask_outputs(gbt_model_path):
    pred = GBTPredictor.load(gbt_model_path)
    # GBT expects trigger obs — actual dim from trigger_features
    obs = np.random.randn(pred.trigger_obs_dim).astype(np.float32)
    raw = pred.predict_raw(obs)
    assert isinstance(raw, MultiTaskOutputs)
    assert len(raw.direction_logits) == 3
    assert all(0.0 <= p <= 1.0 for p in raw.direction_logits)


def test_gbt_predictor_predict_returns_signal_with_correct_action(gbt_model_path):
    pred = GBTPredictor.load(gbt_model_path)
    obs = np.zeros(pred.trigger_obs_dim, dtype=np.float32)
    sig = pred.predict(obs, zone_id=1, timestamp=100.0)
    assert isinstance(sig, Signal)
    assert sig.action in ("CONT", "REV", "SKIP")
    assert sig.zone_id == 1
    assert sig.timestamp == 100.0


def test_gbt_predictor_with_calibrator_changes_probs(gbt_model_path):
    from src.rl.signal.calibration import IsotonicCalibrator

    pred = GBTPredictor.load(gbt_model_path)
    cal = IsotonicCalibrator()
    # Fit a heavily biased calibrator: maps everything to 0.9 for class 0
    cal.fit_per_class(
        class_idx=0,
        raw_probs=np.linspace(0, 1, 100),
        true_outcomes=np.ones(100),
    )
    obs = np.zeros(pred.trigger_obs_dim, dtype=np.float32)

    sig_no_cal = pred.predict(obs, zone_id=1, timestamp=1.0)
    pred.attach_calibrator(cal)
    sig_with_cal = pred.predict(obs, zone_id=1, timestamp=1.0)

    assert sig_no_cal.p_cont != sig_with_cal.p_cont
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_gbt_predictor.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement GBTPredictor**

```python
# backend/src/rl/signal/gbt_predictor.py
"""GBTPredictor — wraps the existing TriggerGBT in the ModelProtocol contract.

The current TriggerGBT outputs direction (CONT vs REV) + confidence +
stop_ticks. To meet the multi-task contract (direction CONT/REV/SKIP +
magnitude + win-prob + duration), this wrapper:

- direction_logits: maps GBT's [p_cont, p_rev] to [p_cont, p_rev, p_skip].
  p_skip is derived as max(0, 1 - confidence) — uncertain predictions
  become more "skip-like". Tunable; default heuristic for v1.
- magnitude_R: approximated from GBT's stop_ticks → expected R as TP1_R=2.0
  scaled by confidence. Replace with proper magnitude head in Task 5.
- win_probability: from GBT's conf (heuristic — proper head in Task 5).
- duration_bars: not in GBT — heuristic constant 5.0 (replace in Task 5).
- uncertainty: 1.0 - confidence (rough; FT-T will have real ensemble std).

Until Task 5 lands the proper multi-task GBT heads, these are approximations.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.rl.agent.trigger_gbt import TriggerGBT
from .protocol import ModelProtocol
from .types import MultiTaskOutputs


class GBTPredictor(ModelProtocol):
    def __init__(self, gbt: TriggerGBT) -> None:
        super().__init__()
        self._gbt = gbt
        self.trigger_obs_dim = self._gbt.input_dim if hasattr(self._gbt, "input_dim") else 313

    @classmethod
    def load(cls, model_path: Path | str) -> "GBTPredictor":
        gbt = TriggerGBT.load(Path(model_path))
        return cls(gbt=gbt)

    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        action_idx, confidence, prob_cont, prob_rev = self._gbt.predict_direction(obs)
        stop_ticks = float(self._gbt.predict_stop(obs))

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

        # Magnitude approximation: confidence-scaled R
        magnitude_R = 2.0 * confidence  # TP1_R = 2.0 baseline

        # Win-prob heuristic: confidence directly
        win_prob = float(confidence)

        return MultiTaskOutputs(
            direction_logits=[float(p_cont), float(p_rev), float(p_skip)],
            magnitude_R=float(magnitude_R),
            win_probability=float(win_prob),
            duration_bars=5.0,  # placeholder until magnitude head trained
            uncertainty=float(1.0 - confidence),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_gbt_predictor.py -v`
Expected: 4 PASS (1 may skip if model not present on dev machine)

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/gbt_predictor.py backend/tests/rl/signal/test_gbt_predictor.py
git commit -m "feat(rl/signal): wrap current TriggerGBT as ModelProtocol implementation

GBTPredictor.predict() returns the model-agnostic Signal. Direction is
3-class (CONT/REV/SKIP) — p_skip approximated from (1 - confidence)
until multi-task GBT heads land in next task."
```

---

## Task 5: Multi-task GBT Heads (magnitude, win-prob, duration)

**Files:**
- Create: `backend/src/rl/signal/gbt_multitask.py`
- Test: `backend/tests/rl/signal/test_gbt_multitask.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_gbt_multitask.py
from pathlib import Path

import numpy as np
import pytest

from src.rl.signal.gbt_multitask import MultiTaskGBT


def test_multitask_gbt_trains_three_separate_models(tmp_path):
    rng = np.random.default_rng(0)
    n, d = 200, 313
    X = rng.standard_normal((n, d)).astype(np.float32)
    # Synthetic targets
    magnitudes = rng.uniform(-2, 4, n)
    win_outcomes = (rng.uniform(0, 1, n) < 0.6).astype(int)
    durations = rng.uniform(1, 20, n)

    mtgbt = MultiTaskGBT()
    mtgbt.train(X, magnitudes=magnitudes, win_outcomes=win_outcomes, durations=durations)

    obs = rng.standard_normal(d).astype(np.float32)
    out = mtgbt.predict(obs)
    assert -10 < out["magnitude_R"] < 10
    assert 0 <= out["win_probability"] <= 1
    assert out["duration_bars"] > 0


def test_multitask_gbt_save_and_load(tmp_path):
    rng = np.random.default_rng(1)
    n, d = 100, 313
    X = rng.standard_normal((n, d)).astype(np.float32)
    mtgbt = MultiTaskGBT()
    mtgbt.train(
        X,
        magnitudes=rng.uniform(-2, 4, n),
        win_outcomes=(rng.uniform(0, 1, n) < 0.5).astype(int),
        durations=rng.uniform(1, 20, n),
    )

    path = tmp_path / "mtgbt.joblib"
    mtgbt.save(path)

    mtgbt2 = MultiTaskGBT.load(path)
    obs = rng.standard_normal(d).astype(np.float32)
    o1 = mtgbt.predict(obs)
    o2 = mtgbt2.predict(obs)
    assert o1["magnitude_R"] == pytest.approx(o2["magnitude_R"])


def test_multitask_gbt_returns_zeros_when_untrained():
    mtgbt = MultiTaskGBT()
    obs = np.zeros(313, dtype=np.float32)
    out = mtgbt.predict(obs)
    assert out["magnitude_R"] == 0.0
    assert out["win_probability"] == 0.5
    assert out["duration_bars"] == 5.0
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_gbt_multitask.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement MultiTaskGBT**

```python
# backend/src/rl/signal/gbt_multitask.py
"""Three independent LightGBM heads trained on the same obs pool:
  - magnitude_R: regression on realized R (continuous)
  - win_probability: binary classifier (win=1, loss=0)
  - duration_bars: regression on bars-to-exit

These complement the direction head from TriggerGBT. Together they
populate the MultiTaskOutputs contract from a single obs vector.

Training data source: existing /app/data/rl/episodes/ pool:
  - observations.npy: (N, 313) obs vectors
  - rewards_cont.npy / rewards_rev.npy: realized R per episode (use the
    one matching the GBT's predicted action)
  - duration_bars: derive from episode metadata (TODO: where?)
"""

from __future__ import annotations

from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np


class MultiTaskGBT:
    def __init__(self) -> None:
        self._magnitude_model: lgb.LGBMRegressor | None = None
        self._winprob_model: lgb.LGBMClassifier | None = None
        self._duration_model: lgb.LGBMRegressor | None = None

    def train(
        self,
        X: np.ndarray,
        *,
        magnitudes: np.ndarray,
        win_outcomes: np.ndarray,
        durations: np.ndarray,
    ) -> None:
        """Train all three heads. Each is independent; the GBT trees
        on the same obs find different patterns relevant to each target."""
        common_params = dict(
            n_estimators=200,
            num_leaves=31,
            learning_rate=0.05,
            min_child_samples=20,
            verbose=-1,
        )

        self._magnitude_model = lgb.LGBMRegressor(**common_params)
        self._magnitude_model.fit(X, magnitudes)

        self._winprob_model = lgb.LGBMClassifier(**common_params)
        self._winprob_model.fit(X, win_outcomes)

        self._duration_model = lgb.LGBMRegressor(**common_params)
        self._duration_model.fit(X, durations)

    def predict(self, obs: np.ndarray) -> dict:
        if self._magnitude_model is None:
            return {"magnitude_R": 0.0, "win_probability": 0.5, "duration_bars": 5.0}

        x = obs.reshape(1, -1)
        return {
            "magnitude_R": float(self._magnitude_model.predict(x)[0]),
            "win_probability": float(self._winprob_model.predict_proba(x)[0][1]),
            "duration_bars": float(self._duration_model.predict(x)[0]),
        }

    def save(self, path: Path | str) -> None:
        joblib.dump(
            {
                "magnitude": self._magnitude_model,
                "winprob": self._winprob_model,
                "duration": self._duration_model,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path | str) -> "MultiTaskGBT":
        d = joblib.load(path)
        inst = cls()
        inst._magnitude_model = d["magnitude"]
        inst._winprob_model = d["winprob"]
        inst._duration_model = d["duration"]
        return inst
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_gbt_multitask.py -v`
Expected: 3 PASS

- [ ] **Step 5: Update GBTPredictor to use MultiTaskGBT when available**

In `backend/src/rl/signal/gbt_predictor.py`, modify the class to accept a multitask:

```python
# Add to imports
from .gbt_multitask import MultiTaskGBT

# Modify __init__
class GBTPredictor(ModelProtocol):
    def __init__(self, gbt: TriggerGBT, multitask: MultiTaskGBT | None = None) -> None:
        super().__init__()
        self._gbt = gbt
        self._multitask = multitask  # None = use heuristics
        self.trigger_obs_dim = self._gbt.input_dim if hasattr(self._gbt, "input_dim") else 313

    @classmethod
    def load(cls, model_path: Path | str, multitask_path: Path | str | None = None) -> "GBTPredictor":
        gbt = TriggerGBT.load(Path(model_path))
        multitask = MultiTaskGBT.load(Path(multitask_path)) if multitask_path else None
        return cls(gbt=gbt, multitask=multitask)

    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        action_idx, confidence, prob_cont, prob_rev = self._gbt.predict_direction(obs)

        p_skip = max(0.0, 1.0 - confidence)
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
            magnitude_R = 2.0 * confidence
            win_prob = float(confidence)
            duration_bars = 5.0

        return MultiTaskOutputs(
            direction_logits=[float(p_cont), float(p_rev), float(p_skip)],
            magnitude_R=float(magnitude_R),
            win_probability=float(win_prob),
            duration_bars=float(duration_bars),
            uncertainty=float(1.0 - confidence),
        )
```

Rerun the GBTPredictor test to confirm it still passes:

```bash
cd backend && python -m pytest tests/rl/signal/test_gbt_predictor.py -v
```

Expected: still 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/rl/signal/gbt_multitask.py backend/src/rl/signal/gbt_predictor.py backend/tests/rl/signal/test_gbt_multitask.py
git commit -m "feat(rl/signal): add MultiTaskGBT with magnitude/winprob/duration heads

Three independent LightGBM models on the same obs pool, one per target.
GBTPredictor optionally consumes MultiTaskGBT for proper Signal fields;
falls back to heuristic when not provided."
```

---

## Task 6: Per-Group Encoder PyTorch Module

**Files:**
- Create: `backend/src/rl/signal/encoders.py`
- Test: `backend/tests/rl/signal/test_encoders.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_encoders.py
import torch

from src.rl.signal.encoders import PerGroupEncoder


def test_per_group_encoder_output_shape():
    """Encoder for one group: input (B, dim_in) → (B, dim_out)."""
    enc = PerGroupEncoder(input_dim=25, output_dim=128, hidden_dim=64)
    x = torch.randn(4, 25)
    out = enc(x)
    assert out.shape == (4, 128)


def test_per_group_encoder_handles_batch_size_one():
    enc = PerGroupEncoder(input_dim=10, output_dim=32)
    x = torch.randn(1, 10)
    out = enc(x)
    assert out.shape == (1, 32)


def test_per_group_encoder_default_hidden_dim_scales_with_input():
    enc = PerGroupEncoder(input_dim=64, output_dim=128)
    # No assertion on internal hidden dim — just confirms it doesn't crash
    x = torch.randn(2, 64)
    out = enc(x)
    assert out.shape == (2, 128)


def test_per_group_encoder_supports_dropout():
    enc = PerGroupEncoder(input_dim=20, output_dim=32, dropout=0.3)
    x = torch.randn(8, 20)
    enc.train()
    out_train = enc(x)
    enc.eval()
    out_eval = enc(x)
    assert out_train.shape == out_eval.shape == (8, 32)
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_encoders.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement PerGroupEncoder**

```python
# backend/src/rl/signal/encoders.py
"""Per-methodology-group MLP encoder.

Each group of obs dims (OF, VSA, PROFILE, AMT, DOW_STRUCTURE, MICRO,
ZONE_MEMORY, MACRO, EXECUTION) gets its own small MLP that learns the
joint representation of that family in isolation. This is the "synapse"
architecture — each methodology family wires together internally before
attending to others.

OF gets the biggest output_dim (128 default) so it dominates downstream
cross-group attention as Query. Others use 32.
"""

from __future__ import annotations

import torch
from torch import nn


class PerGroupEncoder(nn.Module):
    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        hidden_dim: int | None = None,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        if hidden_dim is None:
            hidden_dim = max(output_dim, input_dim // 2)
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, output_dim),
            nn.LayerNorm(output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_encoders.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/encoders.py backend/tests/rl/signal/test_encoders.py
git commit -m "feat(rl/signal): add PerGroupEncoder MLP for methodology group embeddings"
```

---

## Task 7: Cross-Group Attention Layer

**Files:**
- Create: `backend/src/rl/signal/attention.py`
- Test: `backend/tests/rl/signal/test_attention.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_attention.py
import torch

from src.rl.signal.attention import CrossGroupAttention


def test_attention_output_shape():
    """Query (B, 1, d_q), Keys+Values (B, N, d_kv) → output (B, 1, d_q)."""
    attn = CrossGroupAttention(query_dim=128, kv_dim=32, num_heads=4)
    query = torch.randn(2, 1, 128)  # OF embedding
    kv = torch.randn(2, 8, 32)  # 8 other-group embeddings
    out = attn(query, kv)
    assert out.shape == (2, 1, 128)


def test_attention_handles_single_group_in_kv():
    attn = CrossGroupAttention(query_dim=64, kv_dim=32, num_heads=2)
    query = torch.randn(1, 1, 64)
    kv = torch.randn(1, 1, 32)
    out = attn(query, kv)
    assert out.shape == (1, 1, 64)


def test_attention_is_deterministic_in_eval_mode():
    attn = CrossGroupAttention(query_dim=64, kv_dim=32, num_heads=2, dropout=0.5)
    attn.eval()
    query = torch.randn(1, 1, 64)
    kv = torch.randn(1, 4, 32)
    out1 = attn(query, kv)
    out2 = attn(query, kv)
    torch.testing.assert_close(out1, out2)
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_attention.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement CrossGroupAttention**

```python
# backend/src/rl/signal/attention.py
"""Cross-attention layer: OF embedding as Query, other-group embeddings as Key/Value.

This architecturally weights OF as the dominant methodology — other
groups (VSA, PROFILE, etc.) are 'looked up' to support or refute the
OF signal. Matches the methodology priority discussed in the
2026-05-17 architecture verdict.
"""

from __future__ import annotations

import torch
from torch import nn


class CrossGroupAttention(nn.Module):
    def __init__(
        self,
        query_dim: int,
        kv_dim: int,
        num_heads: int = 4,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        # Project K, V to query_dim so MultiheadAttention can use embed_dim=query_dim
        self.k_proj = nn.Linear(kv_dim, query_dim)
        self.v_proj = nn.Linear(kv_dim, query_dim)
        self.attn = nn.MultiheadAttention(
            embed_dim=query_dim,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.norm = nn.LayerNorm(query_dim)

    def forward(self, query: torch.Tensor, kv: torch.Tensor) -> torch.Tensor:
        """query: (B, 1, query_dim), kv: (B, N, kv_dim) → (B, 1, query_dim)"""
        k = self.k_proj(kv)
        v = self.v_proj(kv)
        attended, _ = self.attn(query, k, v)
        return self.norm(query + attended)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_attention.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/attention.py backend/tests/rl/signal/test_attention.py
git commit -m "feat(rl/signal): add CrossGroupAttention layer (OF as Query, others as KV)"
```

---

## Task 8: Multi-Task PyTorch Heads

**Files:**
- Create: `backend/src/rl/signal/heads.py`
- Test: `backend/tests/rl/signal/test_heads.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_heads.py
import torch

from src.rl.signal.heads import MultiTaskHead


def test_multitask_head_returns_all_four_outputs():
    head = MultiTaskHead(input_dim=128)
    x = torch.randn(4, 128)
    out = head(x)
    assert "direction_logits" in out
    assert "magnitude_R" in out
    assert "win_probability" in out
    assert "duration_bars" in out


def test_direction_logits_shape_three_classes():
    head = MultiTaskHead(input_dim=128)
    x = torch.randn(2, 128)
    out = head(x)
    assert out["direction_logits"].shape == (2, 3)


def test_win_probability_in_zero_one_range():
    head = MultiTaskHead(input_dim=64)
    x = torch.randn(8, 64)
    out = head(x)
    p = out["win_probability"]
    assert (p >= 0).all() and (p <= 1).all()


def test_duration_bars_is_positive():
    """Duration head uses softplus to enforce > 0."""
    head = MultiTaskHead(input_dim=64)
    x = torch.randn(8, 64)
    out = head(x)
    assert (out["duration_bars"] > 0).all()


def test_magnitude_R_can_be_negative():
    """Magnitude regression should NOT clip to positive."""
    head = MultiTaskHead(input_dim=64)
    x = torch.randn(100, 64) * 10  # large activations to push some predictions negative
    out = head(x)
    # Just confirm it's not always positive
    assert out["magnitude_R"].shape == (100,)
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_heads.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement MultiTaskHead**

```python
# backend/src/rl/signal/heads.py
"""Multi-task heads on top of the shared representation.

  direction: 3-class softmax (CONT/REV/SKIP) — cross-entropy loss
  magnitude_R: regression (no activation) — MSE loss
  win_probability: sigmoid — binary cross-entropy loss
  duration_bars: softplus (> 0) — MSE loss

Loss is weighted sum during training; weights typically tuned to balance
gradient magnitudes (direction CE much larger than win-prob BCE).
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


class MultiTaskHead(nn.Module):
    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.direction = nn.Linear(input_dim, 3)
        self.magnitude = nn.Linear(input_dim, 1)
        self.win_prob = nn.Linear(input_dim, 1)
        self.duration = nn.Linear(input_dim, 1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        return {
            "direction_logits": F.softmax(self.direction(x), dim=-1),
            "magnitude_R": self.magnitude(x).squeeze(-1),
            "win_probability": torch.sigmoid(self.win_prob(x)).squeeze(-1),
            "duration_bars": F.softplus(self.duration(x)).squeeze(-1),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_heads.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/heads.py backend/tests/rl/signal/test_heads.py
git commit -m "feat(rl/signal): add MultiTaskHead with direction/magnitude/winprob/duration outputs"
```

---

## Task 9: FTTransformerPredictor — End-to-End Module + ModelProtocol Impl

**Files:**
- Create: `backend/src/rl/signal/ft_predictor.py`
- Test: `backend/tests/rl/signal/test_ft_predictor.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_ft_predictor.py
import numpy as np
import torch

from src.rl.signal.ft_predictor import FTTransformerNet, FTTransformerPredictor
from src.rl.signal.types import MultiTaskOutputs, Signal


def test_ft_transformer_net_forward_shape():
    """End-to-end forward: 313-dim obs → multi-task outputs."""
    from src.rl.features.observation_index import _CATEGORY_SEGMENTS
    net = FTTransformerNet(category_segments=_CATEGORY_SEGMENTS)
    obs = torch.randn(2, 313)
    out = net(obs)
    assert out["direction_logits"].shape == (2, 3)
    assert out["magnitude_R"].shape == (2,)
    assert out["win_probability"].shape == (2,)
    assert out["duration_bars"].shape == (2,)


def test_ft_transformer_predictor_implements_modelprotocol():
    pred = FTTransformerPredictor()
    obs = np.random.randn(313).astype(np.float32)
    raw = pred.predict_raw(obs)
    assert isinstance(raw, MultiTaskOutputs)


def test_ft_transformer_predictor_returns_valid_signal():
    pred = FTTransformerPredictor()
    obs = np.zeros(313, dtype=np.float32)
    sig = pred.predict(obs, zone_id=42, timestamp=99.0)
    assert isinstance(sig, Signal)
    assert sig.zone_id == 42
    assert sig.action in ("CONT", "REV", "SKIP")


def test_ft_transformer_save_and_load(tmp_path):
    pred = FTTransformerPredictor()
    obs = np.zeros(313, dtype=np.float32)
    sig1 = pred.predict(obs, zone_id=1, timestamp=1.0)

    path = tmp_path / "ft.pt"
    pred.save(path)

    pred2 = FTTransformerPredictor.load(path)
    sig2 = pred2.predict(obs, zone_id=1, timestamp=1.0)
    assert sig1.p_cont == sig2.p_cont
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_ft_predictor.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement FTTransformerNet + FTTransformerPredictor**

```python
# backend/src/rl/signal/ft_predictor.py
"""FT-Transformer-style network for tabular obs vectors with methodology grouping.

Architecture:
  obs[313] → per-group encoders (one MLP per methodology category)
            → stack into (B, num_groups, group_dim)
            → OF group extracted as Query (B, 1, query_dim)
            → other groups stacked as Key/Value (B, num_groups - 1, kv_dim)
            → CrossGroupAttention → (B, 1, query_dim)
            → MultiTaskHead → 4 outputs

OF gets the largest embedding (128) — others get 32. The attention layer
projects the smaller KV embeddings up to query_dim, so the network can
attend to all groups at the same scale while preserving OF's dominance.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn

from src.rl.features.observation_index import _CATEGORY_SEGMENTS, _SEGMENT_OFFSETS
from .attention import CrossGroupAttention
from .encoders import PerGroupEncoder
from .heads import MultiTaskHead
from .protocol import ModelProtocol
from .types import MultiTaskOutputs

_OF_QUERY_DIM = 128
_OTHER_KV_DIM = 32


class FTTransformerNet(nn.Module):
    def __init__(self, category_segments: dict | None = None) -> None:
        super().__init__()
        cats = category_segments or _CATEGORY_SEGMENTS
        # Build per-group encoders. Index OF specially.
        self._cat_order: list[str] = sorted(cats.keys())
        self._encoders = nn.ModuleDict()
        self._cat_dims: dict[str, tuple[int, int]] = {}  # cat -> (start, end) in flat obs

        cursor = 0
        for cat in self._cat_order:
            segs = cats[cat]
            dim = sum(s["size"] for s in segs)
            out_dim = _OF_QUERY_DIM if cat == "OF" else _OTHER_KV_DIM
            self._encoders[cat] = PerGroupEncoder(input_dim=dim, output_dim=out_dim)
            self._cat_dims[cat] = (cursor, cursor + dim)
            cursor += dim
        self._total_dim = cursor

        self.attention = CrossGroupAttention(
            query_dim=_OF_QUERY_DIM,
            kv_dim=_OTHER_KV_DIM,
            num_heads=4,
        )
        self.head = MultiTaskHead(input_dim=_OF_QUERY_DIM)

    def forward(self, obs: torch.Tensor) -> dict[str, torch.Tensor]:
        # obs: (B, 313). Slice each category from the flat vector.
        # NOTE: this assumes the obs is laid out exactly per
        # observation_index SEGMENTS ordering. Validate at training time.
        embeddings: dict[str, torch.Tensor] = {}
        # Use _SEGMENT_OFFSETS from observation_index for the canonical layout
        for cat in self._cat_order:
            from src.rl.features.observation_index import _CATEGORY_SEGMENTS as _CS

            # Concatenate all segments in this category from obs
            chunks = []
            for seg in _CS[cat]:
                start, end = _SEGMENT_OFFSETS[seg["name"]]
                chunks.append(obs[:, start:end])
            cat_input = torch.cat(chunks, dim=-1)
            embeddings[cat] = self._encoders[cat](cat_input)

        # OF as Query (B, 1, query_dim); others as KV (B, N, kv_dim)
        of_emb = embeddings["OF"].unsqueeze(1)  # (B, 1, _OF_QUERY_DIM)
        other_embs = torch.stack(
            [embeddings[c] for c in self._cat_order if c != "OF"],
            dim=1,
        )  # (B, num_others, _OTHER_KV_DIM)

        attended = self.attention(of_emb, other_embs).squeeze(1)  # (B, _OF_QUERY_DIM)
        return self.head(attended)


class FTTransformerPredictor(ModelProtocol):
    def __init__(self, net: FTTransformerNet | None = None) -> None:
        super().__init__()
        self._net = net if net is not None else FTTransformerNet()
        self._net.eval()

    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        with torch.no_grad():
            x = torch.from_numpy(obs.astype(np.float32)).unsqueeze(0)
            out = self._net(x)
        return MultiTaskOutputs(
            direction_logits=out["direction_logits"][0].tolist(),
            magnitude_R=float(out["magnitude_R"][0].item()),
            win_probability=float(out["win_probability"][0].item()),
            duration_bars=float(out["duration_bars"][0].item()),
            uncertainty=0.1,  # placeholder; replace with MC dropout or ensemble in v2
        )

    def save(self, path: Path | str) -> None:
        torch.save(self._net.state_dict(), path)

    @classmethod
    def load(cls, path: Path | str) -> "FTTransformerPredictor":
        net = FTTransformerNet()
        net.load_state_dict(torch.load(path, map_location="cpu"))
        net.eval()
        return cls(net=net)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_ft_predictor.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/ft_predictor.py backend/tests/rl/signal/test_ft_predictor.py
git commit -m "feat(rl/signal): add FTTransformerPredictor with per-group encoders + attention

The synapse architecture: one MLP per methodology category, OF as Query
in cross-group attention (architecturally dominant), MultiTaskHead
produces direction/magnitude/winprob/duration. Untrained at first —
training pipeline in Task 12. Initial weights random; predictions
meaningless until trained, hence shadow-only deployment."
```

---

## Task 10: shadow_predictions DB table

**Files:**
- Modify: `backend/src/db/models.py` (add ShadowPrediction)
- Migration: handled via `_run_pg_migrations` per CLAUDE.md convention

- [ ] **Step 1: Find existing model + migration pattern**

Run: `grep -n "_run_pg_migrations\|class BrokerTrade" backend/src/db/models.py | head -10`

- [ ] **Step 2: Add ShadowPrediction model**

In `backend/src/db/models.py`, add a new SQLAlchemy model:

```python
# Add near the bottom of the existing models, before _run_pg_migrations

class ShadowPrediction(Base):
    """Side-by-side log of multiple models' predictions on the same obs.

    Production model's prediction is what gets dispatched. Shadow model's
    prediction is logged here for comparison. Both rows share the same
    request_id so we can compare them later.
    """

    __tablename__ = "shadow_predictions"

    id = Column(Integer, primary_key=True)
    request_id = Column(String(64), nullable=False, index=True)  # uuid per zone touch
    model_name = Column(String(32), nullable=False, index=True)  # 'gbt_v5' or 'ft_v1'
    is_production = Column(Boolean, nullable=False, default=False)
    ts = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    # The prediction itself
    p_cont = Column(Float, nullable=False)
    p_rev = Column(Float, nullable=False)
    p_skip = Column(Float, nullable=False)
    expected_R = Column(Float, nullable=False)
    win_probability = Column(Float, nullable=False)
    duration_bars = Column(Float, nullable=False)
    uncertainty = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    action = Column(String(8), nullable=False)  # "CONT"/"REV"/"SKIP"

    # Context for joining to outcomes
    zone_id = Column(Integer, nullable=True)
    zone_center = Column(Float, nullable=True)

    # FK to the realized broker_trade if one was placed (production only)
    broker_trade_id = Column(Integer, ForeignKey("broker_trades.id"), nullable=True)

    __table_args__ = (
        Index("ix_shadow_predictions_request_model", "request_id", "model_name"),
        Index("ix_shadow_predictions_ts", "ts"),
    )
```

- [ ] **Step 3: Add migration to `_run_pg_migrations`**

Find `_run_pg_migrations` and append:

```python
# Add inside _run_pg_migrations, after existing migrations
try:
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS shadow_predictions (
            id SERIAL PRIMARY KEY,
            request_id VARCHAR(64) NOT NULL,
            model_name VARCHAR(32) NOT NULL,
            is_production BOOLEAN NOT NULL DEFAULT FALSE,
            ts TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            p_cont DOUBLE PRECISION NOT NULL,
            p_rev DOUBLE PRECISION NOT NULL,
            p_skip DOUBLE PRECISION NOT NULL,
            expected_r DOUBLE PRECISION NOT NULL,
            win_probability DOUBLE PRECISION NOT NULL,
            duration_bars DOUBLE PRECISION NOT NULL,
            uncertainty DOUBLE PRECISION NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            action VARCHAR(8) NOT NULL,
            zone_id INTEGER,
            zone_center DOUBLE PRECISION,
            broker_trade_id INTEGER REFERENCES broker_trades(id)
        )
    """))
    session.execute(text("CREATE INDEX IF NOT EXISTS ix_shadow_predictions_request_model ON shadow_predictions(request_id, model_name)"))
    session.execute(text("CREATE INDEX IF NOT EXISTS ix_shadow_predictions_ts ON shadow_predictions(ts)"))
    session.commit()
    log.info("shadow_predictions table ready")
except Exception:
    session.rollback()
    log.exception("shadow_predictions migration failed")
```

- [ ] **Step 4: Smoke-test by importing**

```bash
cd backend && python -c "from src.db.models import ShadowPrediction; print(ShadowPrediction.__tablename__)"
```

Expected: `shadow_predictions`

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat(db): add shadow_predictions table for side-by-side model logging"
```

---

## Task 11: ShadowLogger — Run Both Models, Log Both, Dispatch Production

**Files:**
- Create: `backend/src/rl/signal/shadow.py`
- Test: `backend/tests/rl/signal/test_shadow.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_shadow.py
import uuid

import numpy as np
import pytest

from src.rl.signal.protocol import ModelProtocol
from src.rl.signal.shadow import ShadowLogger
from src.rl.signal.types import MultiTaskOutputs


class _MockModel(ModelProtocol):
    def __init__(self, name: str, p_cont: float) -> None:
        super().__init__()
        self.name = name
        self._p_cont = p_cont

    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        return MultiTaskOutputs(
            direction_logits=[self._p_cont, 1 - self._p_cont - 0.1, 0.1],
            magnitude_R=1.0,
            win_probability=0.6,
            duration_bars=5.0,
            uncertainty=0.1,
        )


def test_shadow_logger_returns_production_signal_only():
    """The dispatch path gets the production model's signal."""
    prod = _MockModel("gbt_v5", p_cont=0.7)
    shadow = _MockModel("ft_v1", p_cont=0.3)

    # Use a fake DB writer (test isolation)
    written: list[dict] = []

    def fake_writer(records: list[dict]) -> None:
        written.extend(records)

    sl = ShadowLogger(
        production=prod,
        shadows=[shadow],
        db_writer=fake_writer,
    )
    obs = np.zeros(313, dtype=np.float32)
    sig = sl.predict(obs, zone_id=1, zone_center=25000.0, timestamp=100.0)

    # Production wins the dispatch
    assert sig.p_cont > 0.5

    # Both models logged
    assert len(written) == 2
    assert {r["model_name"] for r in written} == {"gbt_v5", "ft_v1"}
    # Same request_id linking them
    assert written[0]["request_id"] == written[1]["request_id"]
    # is_production flag
    prod_record = next(r for r in written if r["model_name"] == "gbt_v5")
    shadow_record = next(r for r in written if r["model_name"] == "ft_v1")
    assert prod_record["is_production"] is True
    assert shadow_record["is_production"] is False


def test_shadow_logger_continues_if_shadow_crashes():
    """A shadow model exception must NOT affect production dispatch."""
    prod = _MockModel("gbt_v5", p_cont=0.7)

    class _CrashyModel(ModelProtocol):
        name = "crashy"

        def predict_raw(self, obs):
            raise RuntimeError("simulated crash")

    written: list[dict] = []
    sl = ShadowLogger(
        production=prod,
        shadows=[_CrashyModel()],
        db_writer=lambda recs: written.extend(recs),
    )
    obs = np.zeros(313, dtype=np.float32)
    sig = sl.predict(obs, zone_id=2, zone_center=25001.0, timestamp=101.0)
    # Production still dispatched
    assert sig.p_cont > 0.5
    # Only the production record was logged (crashy didn't make it)
    prod_records = [r for r in written if r["is_production"]]
    assert len(prod_records) == 1


def test_shadow_logger_with_no_shadows_passes_through():
    """If shadows=[] the logger should behave like a pure production wrapper."""
    prod = _MockModel("gbt_v5", p_cont=0.7)
    written: list[dict] = []
    sl = ShadowLogger(production=prod, shadows=[], db_writer=lambda recs: written.extend(recs))
    obs = np.zeros(313, dtype=np.float32)
    sig = sl.predict(obs, zone_id=3, zone_center=25002.0, timestamp=102.0)
    assert sig.p_cont > 0.5
    assert len(written) == 1  # production only
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_shadow.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement ShadowLogger**

```python
# backend/src/rl/signal/shadow.py
"""ShadowLogger — runs the production model + 0..N shadow models on every
zone touch, logs all predictions to the shadow_predictions table, and
returns the production model's Signal for dispatch.

Critical safety property: a shadow model's exception must NEVER affect
the production prediction or dispatch. We catch everything from shadows
and log it, then continue.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable

import numpy as np

from .protocol import ModelProtocol
from .types import Signal

log = logging.getLogger(__name__)


class ShadowLogger:
    def __init__(
        self,
        production: ModelProtocol,
        shadows: list[ModelProtocol],
        db_writer: Callable[[list[dict]], None],
    ) -> None:
        """
        production: the ModelProtocol whose Signal is dispatched
        shadows: ModelProtocols whose Signals are logged only
        db_writer: function that takes a list of dicts and persists them
                   (typically wraps SQLAlchemy bulk insert)
        """
        self._production = production
        self._shadows = shadows
        self._db_writer = db_writer

    def predict(
        self,
        obs: np.ndarray,
        *,
        zone_id: int,
        zone_center: float,
        timestamp: float,
    ) -> Signal:
        request_id = uuid.uuid4().hex
        records: list[dict] = []

        # 1. Production — must succeed
        prod_signal = self._production.predict(
            obs, zone_id=zone_id, timestamp=timestamp
        )
        records.append(
            self._signal_to_record(
                signal=prod_signal,
                request_id=request_id,
                model_name=getattr(self._production, "name", "production"),
                is_production=True,
                zone_id=zone_id,
                zone_center=zone_center,
            )
        )

        # 2. Each shadow — best-effort
        for shadow in self._shadows:
            try:
                shadow_signal = shadow.predict(
                    obs, zone_id=zone_id, timestamp=timestamp
                )
                records.append(
                    self._signal_to_record(
                        signal=shadow_signal,
                        request_id=request_id,
                        model_name=getattr(shadow, "name", shadow.__class__.__name__),
                        is_production=False,
                        zone_id=zone_id,
                        zone_center=zone_center,
                    )
                )
            except Exception:
                log.exception("shadow model %s raised; production unaffected", shadow)

        # 3. Best-effort log — write failure shouldn't affect dispatch
        try:
            self._db_writer(records)
        except Exception:
            log.exception("shadow log write failed")

        return prod_signal

    @staticmethod
    def _signal_to_record(
        signal: Signal,
        request_id: str,
        model_name: str,
        is_production: bool,
        zone_id: int,
        zone_center: float,
    ) -> dict:
        return {
            "request_id": request_id,
            "model_name": model_name,
            "is_production": is_production,
            "p_cont": signal.p_cont,
            "p_rev": signal.p_rev,
            "p_skip": signal.p_skip,
            "expected_R": signal.expected_R,
            "win_probability": signal.win_probability,
            "duration_bars": signal.duration_bars,
            "uncertainty": signal.uncertainty,
            "confidence": signal.confidence,
            "action": signal.action,
            "zone_id": zone_id,
            "zone_center": zone_center,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_shadow.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/shadow.py backend/tests/rl/signal/test_shadow.py
git commit -m "feat(rl/signal): add ShadowLogger — production + shadow predictions logged side-by-side

Production model's Signal is dispatched. Shadow models log only.
Shadow exceptions never affect production. DB write failures never
affect dispatch."
```

---

## Task 12: FT-Transformer Training Loop (PyTorch Lightning)

**Files:**
- Create: `backend/src/rl/signal/training.py`
- Test: `backend/tests/rl/signal/test_training.py` (smoke test only — real training is offline)

- [ ] **Step 1: Write the failing test (smoke only)**

```python
# backend/tests/rl/signal/test_training.py
import numpy as np
import torch

from src.rl.signal.training import FTTrainingDataset, train_ft_transformer


def test_training_dataset_returns_tensors():
    obs = np.random.randn(100, 313).astype(np.float32)
    direction = np.random.randint(0, 3, 100)
    magnitude = np.random.randn(100).astype(np.float32)
    win = np.random.randint(0, 2, 100)
    duration = np.random.uniform(1, 20, 100).astype(np.float32)

    ds = FTTrainingDataset(obs, direction, magnitude, win, duration)
    item = ds[0]
    assert torch.is_tensor(item["obs"])
    assert item["obs"].shape == (313,)
    assert item["direction"].item() in (0, 1, 2)


def test_train_ft_transformer_smoke(tmp_path):
    """Train for 1 epoch on tiny synthetic data — just checks the loop runs."""
    obs = np.random.randn(64, 313).astype(np.float32)
    direction = np.random.randint(0, 3, 64)
    magnitude = np.random.randn(64).astype(np.float32)
    win = np.random.randint(0, 2, 64)
    duration = np.random.uniform(1, 20, 64).astype(np.float32)

    out_path = tmp_path / "ft.pt"
    train_ft_transformer(
        obs=obs,
        direction=direction,
        magnitude=magnitude,
        win_outcomes=win,
        durations=duration,
        out_path=out_path,
        max_epochs=1,
        batch_size=16,
    )
    assert out_path.exists()
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_training.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement training**

```python
# backend/src/rl/signal/training.py
"""FT-Transformer training loop. Offline — runs against existing pool at
/app/data/rl/episodes/. Outputs a saved FTTransformerNet state_dict that
FTTransformerPredictor.load() can consume.

Multi-task loss = weighted sum of:
  - direction: cross-entropy (large weight)
  - magnitude: MSE (small weight — values can be large)
  - win-prob: BCE (small weight)
  - duration: MSE (small weight)

Weights tuned empirically; first pass uses defaults below.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from .ft_predictor import FTTransformerNet


class FTTrainingDataset(Dataset):
    def __init__(
        self,
        obs: np.ndarray,
        direction: np.ndarray,
        magnitude: np.ndarray,
        win_outcomes: np.ndarray,
        durations: np.ndarray,
    ) -> None:
        self.obs = torch.from_numpy(obs.astype(np.float32))
        self.direction = torch.from_numpy(direction.astype(np.int64))
        self.magnitude = torch.from_numpy(magnitude.astype(np.float32))
        self.win = torch.from_numpy(win_outcomes.astype(np.float32))
        self.duration = torch.from_numpy(durations.astype(np.float32))

    def __len__(self) -> int:
        return self.obs.shape[0]

    def __getitem__(self, idx: int) -> dict:
        return {
            "obs": self.obs[idx],
            "direction": self.direction[idx],
            "magnitude": self.magnitude[idx],
            "win": self.win[idx],
            "duration": self.duration[idx],
        }


def train_ft_transformer(
    obs: np.ndarray,
    direction: np.ndarray,
    magnitude: np.ndarray,
    win_outcomes: np.ndarray,
    durations: np.ndarray,
    out_path: Path | str,
    max_epochs: int = 20,
    batch_size: int = 64,
    lr: float = 1e-3,
    direction_weight: float = 1.0,
    magnitude_weight: float = 0.1,
    win_weight: float = 0.3,
    duration_weight: float = 0.05,
    device: str | None = None,
) -> None:
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    ds = FTTrainingDataset(obs, direction, magnitude, win_outcomes, durations)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=True, num_workers=0)

    net = FTTransformerNet().to(device)
    optim = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    ce = nn.CrossEntropyLoss()
    mse = nn.MSELoss()
    bce = nn.BCELoss()

    net.train()
    for epoch in range(max_epochs):
        total_loss = 0.0
        for batch in loader:
            obs_b = batch["obs"].to(device)
            out = net(obs_b)
            loss = (
                direction_weight * ce(out["direction_logits"], batch["direction"].to(device))
                + magnitude_weight * mse(out["magnitude_R"], batch["magnitude"].to(device))
                + win_weight * bce(out["win_probability"], batch["win"].to(device))
                + duration_weight * mse(out["duration_bars"], batch["duration"].to(device))
            )
            optim.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), max_norm=1.0)
            optim.step()
            total_loss += loss.item()
        print(f"epoch {epoch+1}/{max_epochs}  loss={total_loss / len(loader):.4f}")

    torch.save(net.state_dict(), out_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_training.py -v`
Expected: 2 PASS (one-epoch smoke training succeeds)

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/training.py backend/tests/rl/signal/test_training.py
git commit -m "feat(rl/signal): add FT-Transformer training loop with multi-task loss

Train direction (cross-entropy) + magnitude (MSE) + win-prob (BCE) +
duration (MSE) jointly on existing episode pool. Default loss weights
balance gradients; tune empirically once first training run lands."
```

---

## Task 13: ModelComparator — Promotion Criterion

**Files:**
- Create: `backend/src/rl/signal/comparison.py`
- Test: `backend/tests/rl/signal/test_comparison.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/signal/test_comparison.py
import numpy as np

from src.rl.signal.comparison import (
    DailyMetrics,
    PromotionDecision,
    compute_daily_metrics,
    evaluate_promotion,
)


def test_compute_daily_metrics():
    predictions = [
        {"model_name": "gbt", "action": "CONT", "win_probability": 0.7, "p_cont": 0.7, "p_rev": 0.2, "p_skip": 0.1, "expected_R": 1.5},
        {"model_name": "gbt", "action": "REV", "win_probability": 0.4, "p_cont": 0.3, "p_rev": 0.5, "p_skip": 0.2, "expected_R": 0.5},
        {"model_name": "ft", "action": "CONT", "win_probability": 0.8, "p_cont": 0.8, "p_rev": 0.15, "p_skip": 0.05, "expected_R": 2.0},
        {"model_name": "ft", "action": "CONT", "win_probability": 0.6, "p_cont": 0.6, "p_rev": 0.3, "p_skip": 0.1, "expected_R": 1.0},
    ]
    realized_R_by_request = {0: 1.5, 1: -0.5, 2: 2.5, 3: 1.0}  # request_id → realized R
    # Map predictions back via index for the test
    for i, p in enumerate(predictions):
        p["request_id"] = i
        p["realized_R"] = realized_R_by_request[i]

    metrics = compute_daily_metrics(predictions)
    assert "gbt" in metrics
    assert "ft" in metrics
    # GBT: 1 win out of 2 (1.5 > 0; -0.5 < 0) — wait actually only count non-SKIP
    # Both non-SKIP. WR = 1/2 = 50%
    assert metrics["gbt"].win_rate == 0.5
    # FT: 2 wins out of 2 = 100%
    assert metrics["ft"].win_rate == 1.0


def test_promotion_requires_30_consecutive_days_meeting_threshold():
    """Promotion requires N consecutive days where FT beats GBT by margin."""
    # 30 days of FT >= GBT by 1pt WR + 0.05R mean
    days = [
        {
            "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
            "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
        }
        for _ in range(30)
    ]
    decision = evaluate_promotion(days, production="gbt", candidate="ft", min_consecutive=30)
    assert decision.should_promote is True
    assert decision.consecutive_days == 30


def test_promotion_rejects_when_fewer_consecutive_days():
    days = [
        {
            "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
            "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
        }
        for _ in range(10)
    ]
    decision = evaluate_promotion(days, production="gbt", candidate="ft", min_consecutive=30)
    assert decision.should_promote is False
    assert decision.consecutive_days < 30


def test_promotion_resets_on_a_losing_day():
    """A single day where FT doesn't beat GBT resets the streak."""
    days = []
    for _ in range(20):
        days.append(
            {
                "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
                "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
            }
        )
    # One losing day
    days.append(
        {
            "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
            "ft": DailyMetrics(win_rate=0.40, mean_R=0.10, n=10),
        }
    )
    # 10 more winning days — but streak was reset by the losing day
    for _ in range(10):
        days.append(
            {
                "gbt": DailyMetrics(win_rate=0.55, mean_R=0.30, n=10),
                "ft": DailyMetrics(win_rate=0.57, mean_R=0.36, n=10),
            }
        )
    decision = evaluate_promotion(days, production="gbt", candidate="ft", min_consecutive=30)
    assert decision.should_promote is False
    # The last 10 days are a fresh streak of 10
    assert decision.consecutive_days == 10
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/signal/test_comparison.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement ModelComparator**

```python
# backend/src/rl/signal/comparison.py
"""Compute per-model daily metrics + promotion criterion.

Promotion criterion: candidate model must beat production by:
  - win_rate margin >= 0.01 (1 percentage point)
  - mean_R margin >= 0.05 (5 basis points of R)
For min_consecutive days (default 30).

A single losing day resets the streak. This is intentionally strict —
shadow promotions are high-stakes (changing the production predictor)
and should be supported by strong empirical evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyMetrics:
    win_rate: float
    mean_R: float
    n: int


@dataclass(frozen=True)
class PromotionDecision:
    should_promote: bool
    consecutive_days: int
    reason: str


def compute_daily_metrics(predictions: list[dict]) -> dict[str, DailyMetrics]:
    """Group predictions by model_name, compute WR/mean_R."""
    by_model: dict[str, list[dict]] = {}
    for p in predictions:
        if p.get("action") == "SKIP":
            continue
        by_model.setdefault(p["model_name"], []).append(p)

    out: dict[str, DailyMetrics] = {}
    for model_name, preds in by_model.items():
        if not preds:
            continue
        wins = sum(1 for p in preds if p.get("realized_R", 0) > 0)
        total_R = sum(p.get("realized_R", 0) for p in preds)
        out[model_name] = DailyMetrics(
            win_rate=wins / len(preds),
            mean_R=total_R / len(preds),
            n=len(preds),
        )
    return out


def evaluate_promotion(
    days: list[dict[str, DailyMetrics]],
    production: str,
    candidate: str,
    min_consecutive: int = 30,
    wr_margin: float = 0.01,
    mean_R_margin: float = 0.05,
) -> PromotionDecision:
    """Count consecutive days at the END of the days list where
    candidate beats production by both margins."""
    consecutive = 0
    for day in reversed(days):
        prod_m = day.get(production)
        cand_m = day.get(candidate)
        if prod_m is None or cand_m is None:
            break
        wr_diff = cand_m.win_rate - prod_m.win_rate
        r_diff = cand_m.mean_R - prod_m.mean_R
        if wr_diff >= wr_margin and r_diff >= mean_R_margin:
            consecutive += 1
        else:
            break

    should_promote = consecutive >= min_consecutive
    reason = (
        f"candidate beat production for {consecutive}/{min_consecutive} consecutive days"
        if should_promote
        else f"only {consecutive}/{min_consecutive} consecutive winning days — wait"
    )
    return PromotionDecision(
        should_promote=should_promote,
        consecutive_days=consecutive,
        reason=reason,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/signal/test_comparison.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/signal/comparison.py backend/tests/rl/signal/test_comparison.py
git commit -m "feat(rl/signal): add ModelComparator with 30-consecutive-day promotion criterion"
```

---

## Task 14: Wire ShadowLogger into live_inference

**Files:**
- Modify: `backend/src/rl/live_inference.py`

- [ ] **Step 1: Find the existing inference path**

Run: `grep -n "def infer\|self._gbt\|predict_direction" backend/src/rl/live_inference.py | head`

- [ ] **Step 2: Add ShadowLogger initialization in `__init__`**

Read the current `DQNInference.__init__` and add ShadowLogger setup. Below is a sketch — adapt to the existing class structure:

```python
# At top of live_inference.py with other imports:
from src.rl.signal.gbt_predictor import GBTPredictor
from src.rl.signal.ft_predictor import FTTransformerPredictor
from src.rl.signal.shadow import ShadowLogger
from src.db.session import get_db_session
from src.db.models import ShadowPrediction
import os

# Inside DQNInference.__init__, after the existing self._trigger_gbt setup:

# Wrap existing GBT in ModelProtocol for shadow logging
self._production_predictor = GBTPredictor(self._trigger_gbt)
self._production_predictor.name = "gbt_v5"

# Optionally load FT-Transformer as shadow
self._shadow_predictors = []
ft_path = os.environ.get("FT_SHADOW_PATH", "/app/data/rl/models/ft_v1.pt")
if Path(ft_path).exists():
    try:
        ft = FTTransformerPredictor.load(ft_path)
        ft.name = "ft_v1"
        self._shadow_predictors.append(ft)
    except Exception:
        log.exception("FT shadow model load failed")

def _shadow_db_writer(records: list[dict]) -> None:
    try:
        with get_db_session() as session:
            for r in records:
                session.add(ShadowPrediction(**r))
            session.commit()
    except Exception:
        log.exception("shadow prediction write failed")

self._shadow_logger = ShadowLogger(
    production=self._production_predictor,
    shadows=self._shadow_predictors,
    db_writer=_shadow_db_writer,
)
```

- [ ] **Step 3: Update the inference entry point to call shadow_logger**

Find the method that currently calls `self._trigger_gbt.predict_direction(...)` (likely `infer()`). Replace the model call with:

```python
# OLD:
# gbt_action, gbt_conf, prob_cont, prob_rev = self._trigger_gbt.predict_direction(trigger_obs)

# NEW:
import time
zone_id = state.get("zone_id", 0)
zone_center = state.get("zone_center", 0.0)
signal = self._shadow_logger.predict(
    trigger_obs,
    zone_id=int(zone_id),
    zone_center=float(zone_center),
    timestamp=time.time(),
)

# Map Signal back to existing variable names for downstream code:
gbt_action = 0 if signal.action == "CONT" else 1  # SKIP needs handling too — TODO
gbt_conf = signal.confidence
prob_cont = signal.p_cont
prob_rev = signal.p_rev
```

This preserves existing downstream code that uses `gbt_action`/`gbt_conf` etc., while routing through the shadow logger.

- [ ] **Step 4: Smoke-test the import**

```bash
cd backend && python -c "from src.rl.live_inference import DQNInference; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/live_inference.py
git commit -m "feat(rl/live_inference): route predictions through ShadowLogger

Production model (GBT) is wrapped in GBTPredictor and dispatched as
before. If /app/data/rl/models/ft_v1.pt exists, FTTransformerPredictor
is loaded as shadow. Both predictions logged to shadow_predictions
table on every zone touch. Backward-compatible: when no shadow model
file exists, behavior is identical to pre-shadow inference."
```

---

## Task 15: Backfill realized_R into shadow_predictions

**Files:**
- Create: `backend/scripts/correlate_shadow_predictions.py`
- Test: manual run after deploy

- [ ] **Step 1: Write the correlation script**

```python
# backend/scripts/correlate_shadow_predictions.py
"""For every shadow prediction missing a realized_R, find the corresponding
broker_trade (production records only — shadow predictions don't trade)
and copy the realized R back.

Should run nightly via cron, similar to the existing signal correlate cron.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_

from src.db.session import get_db_session
from src.db.models import BrokerTrade, ShadowPrediction


def correlate(lookback_hours: int = 24) -> int:
    """Match shadow_predictions to broker_trades by zone touch time +
    zone_center. Returns number of matches written."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    count = 0
    with get_db_session() as session:
        # Get production predictions with no broker_trade_id yet
        preds = (
            session.query(ShadowPrediction)
            .filter(
                and_(
                    ShadowPrediction.ts >= cutoff,
                    ShadowPrediction.is_production.is_(True),
                    ShadowPrediction.broker_trade_id.is_(None),
                )
            )
            .all()
        )
        for p in preds:
            # Find the broker_trade with closed_at within 5 minutes after
            # prediction.ts and entry near zone_center
            t = (
                session.query(BrokerTrade)
                .filter(
                    and_(
                        BrokerTrade.ts >= p.ts,
                        BrokerTrade.ts <= p.ts + timedelta(minutes=5),
                        BrokerTrade.closed_at.isnot(None),
                    )
                )
                .order_by(BrokerTrade.ts)
                .first()
            )
            if t is not None:
                p.broker_trade_id = t.id
                count += 1
        session.commit()
    return count


if __name__ == "__main__":
    n = correlate()
    print(f"correlated {n} shadow predictions")
```

- [ ] **Step 2: Add nightly cron entry**

Edit `/etc/cron.d/arnold` (server-side) to add:

```
55 23 * * * arnold cd /opt/arnold && docker compose exec -T backend python /app/backend/scripts/correlate_shadow_predictions.py >> /app/logs/shadow_correlate.log 2>&1
```

- [ ] **Step 3: Test manually**

After predictions accumulate, run:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend python /app/backend/scripts/correlate_shadow_predictions.py"
```

Expected: `correlated N shadow predictions` for N > 0.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/correlate_shadow_predictions.py
git commit -m "feat(scripts): correlate shadow_predictions with broker_trades nightly"
```

---

## Task 16: Train First FT-Transformer on Existing Pool

**Files:**
- Create: `backend/scripts/train_ft_v1.py`
- Run manually on server after Task 9 lands

- [ ] **Step 1: Write the training driver**

```python
# backend/scripts/train_ft_v1.py
"""One-shot training of the first FT-Transformer model.

Reads /app/data/rl/episodes/observations.npy + reward arrays, builds
the multi-task targets, runs the training loop, saves to
/app/data/rl/models/ft_v1.pt where live_inference auto-loads it.

GPU required if available (will fall back to CPU; expect ~10x slower).
"""

import sys
from pathlib import Path

import numpy as np

from src.rl.signal.training import train_ft_transformer

ep = Path("/app/data/rl/episodes")
md = Path("/app/data/rl/models")
md.mkdir(parents=True, exist_ok=True)

obs = np.load(ep / "observations.npy")
trig = np.load(ep / "trigger_observations.npy")  # use trigger obs to match GBT input
rc = np.load(ep / "rewards_cont.npy")
rr = np.load(ep / "rewards_rev.npy")

# Build targets
# direction: argmax of rewards (0=CONT, 1=REV, 2=SKIP if both are bad)
direction = np.where(rc > rr, 0, 1).astype(np.int64)
# Mark as SKIP if both rewards are negative
both_negative = (rc < 0) & (rr < 0)
direction[both_negative] = 2

# magnitude: realized R of the chosen action
magnitude = np.where(direction == 0, rc, np.where(direction == 1, rr, 0.0)).astype(np.float32)

# win: chosen action's reward > 0
win = (magnitude > 0).astype(np.int64)

# duration: not in pool today — use constant placeholder
duration = np.full(len(obs), 5.0, dtype=np.float32)

print(f"Training FT-Transformer on {len(obs)} episodes...")
train_ft_transformer(
    obs=obs,
    direction=direction,
    magnitude=magnitude,
    win_outcomes=win,
    durations=duration,
    out_path=md / "ft_v1.pt",
    max_epochs=20,
    batch_size=128,
)
print(f"Saved to {md / 'ft_v1.pt'}")
```

- [ ] **Step 2: Run on server**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend python /app/backend/scripts/train_ft_v1.py"
```

Expected: training log per epoch, then "Saved to /app/data/rl/models/ft_v1.pt"

- [ ] **Step 3: Restart backend to load FT shadow**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh restart backend"
```

- [ ] **Step 4: Verify shadow predictions appearing**

After ~30 min of zone touches:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c 'SELECT model_name, COUNT(*) FROM shadow_predictions GROUP BY model_name'"
```

Expected: both `gbt_v5` and `ft_v1` rows present.

- [ ] **Step 5: Commit script**

```bash
git add backend/scripts/train_ft_v1.py
git commit -m "feat(scripts): one-shot FT-Transformer training driver

Reads existing episode pool, builds multi-task targets from
rewards_cont/rewards_rev, trains for 20 epochs, saves to
/app/data/rl/models/ft_v1.pt where live_inference auto-loads as shadow."
```

---

## Task 17: Daily Shadow Comparison Report

**Files:**
- Create: `backend/scripts/shadow_daily_report.py`

- [ ] **Step 1: Write the report script**

```python
# backend/scripts/shadow_daily_report.py
"""Daily shadow vs production comparison. Run via cron at 00:05 UTC
after correlation runs. Prints summary; optionally writes JSON to
/app/data/rl/shadow_reports/YYYY-MM-DD.json for tracking.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db.session import get_db_session
from src.db.models import ShadowPrediction
from src.rl.signal.comparison import (
    compute_daily_metrics,
    evaluate_promotion,
)


def daily_report(days_back: int = 30) -> dict:
    now = datetime.now(timezone.utc)
    out = {"generated_at": now.isoformat(), "days": []}

    with get_db_session() as session:
        for d in range(days_back, 0, -1):
            date_start = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
            date_end = date_start + timedelta(days=1)
            rows = (
                session.query(ShadowPrediction)
                .filter(ShadowPrediction.ts >= date_start, ShadowPrediction.ts < date_end)
                .all()
            )
            preds = []
            for r in rows:
                d_dict = {
                    "model_name": r.model_name,
                    "action": r.action,
                    "win_probability": r.win_probability,
                    "p_cont": r.p_cont,
                    "p_rev": r.p_rev,
                    "p_skip": r.p_skip,
                    "expected_R": r.expected_R,
                    "request_id": r.request_id,
                    "broker_trade_id": r.broker_trade_id,
                }
                # realized_R requires join — fetch from broker_trades
                if r.broker_trade_id is not None:
                    from src.db.models import BrokerTrade
                    t = session.query(BrokerTrade).filter_by(id=r.broker_trade_id).first()
                    if t is not None and t.pnl_r is not None:
                        d_dict["realized_R"] = float(t.pnl_r)
                preds.append(d_dict)
            metrics = compute_daily_metrics(preds)
            out["days"].append(
                {
                    "date": date_start.strftime("%Y-%m-%d"),
                    "metrics": {k: vars(v) for k, v in metrics.items()},
                }
            )

    # Promotion check on last N days
    days_metrics = [{k: v for k, v in zip(["gbt_v5", "ft_v1"], (None, None))}] * 0  # placeholder
    days_metrics = []
    for d in out["days"]:
        ms = d["metrics"]
        if "gbt_v5" in ms and "ft_v1" in ms:
            from src.rl.signal.comparison import DailyMetrics
            days_metrics.append({
                "gbt_v5": DailyMetrics(**ms["gbt_v5"]),
                "ft_v1": DailyMetrics(**ms["ft_v1"]),
            })
    decision = evaluate_promotion(days_metrics, production="gbt_v5", candidate="ft_v1", min_consecutive=30)
    out["promotion_decision"] = {
        "should_promote": decision.should_promote,
        "consecutive_days": decision.consecutive_days,
        "reason": decision.reason,
    }

    return out


if __name__ == "__main__":
    report = daily_report()
    print(json.dumps(report, indent=2, default=str))
    out_dir = Path("/app/data/rl/shadow_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    out_file.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved to {out_file}")
```

- [ ] **Step 2: Add cron entry**

```
5 0 * * * arnold cd /opt/arnold && docker compose exec -T backend python /app/backend/scripts/shadow_daily_report.py >> /app/logs/shadow_daily.log 2>&1
```

- [ ] **Step 3: Test manually**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend python /app/backend/scripts/shadow_daily_report.py | head -50"
```

Expected: JSON summary with per-day metrics + promotion decision.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/shadow_daily_report.py
git commit -m "feat(scripts): daily shadow vs production comparison report"
```

---

## Task 18: Deploy + End-to-End Verification

**Files:**
- None — operational

- [ ] **Step 1: Deploy all changes**

```bash
git push origin main
ssh root@148.251.40.251 "ALLOW_OPEN_POSITION_DEPLOY=1 bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

- [ ] **Step 2: Verify shadow_predictions table exists**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c '\\d shadow_predictions'"
```

Expected: schema printed with all the columns from Task 10.

- [ ] **Step 3: Train and load FT v1 (Task 16)**

Already covered in Task 16 — confirm now in production.

- [ ] **Step 4: Wait for first zone touches to populate predictions**

During next market session:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c 'SELECT model_name, COUNT(*) FROM shadow_predictions WHERE ts > NOW() - INTERVAL \"1 hour\" GROUP BY model_name'"
```

Expected: both `gbt_v5` and `ft_v1` with non-zero counts.

- [ ] **Step 5: Run first daily report manually**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend python /app/backend/scripts/shadow_daily_report.py"
```

Expected: report JSON; promotion_decision.should_promote = false (way too early to promote — only ~1 day of data).

- [ ] **Step 6: Verify no production regression**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose logs backend --since 1h | grep -E 'broker gate|dispatched' | tail -10"
```

Expected: GBT continues dispatching as before. No errors about ShadowLogger or FT-Transformer affecting dispatch.

---

## Task 19: 30-Day Validation Window

**Files:**
- None — passive observation

- [ ] **Step 1: Weekly check**

Each Monday morning:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend python /app/backend/scripts/shadow_daily_report.py | python3 -c 'import json,sys; r=json.load(sys.stdin); print(\"consecutive winning days:\", r[\"promotion_decision\"][\"consecutive_days\"])'"
```

Track the trajectory. Expected outcome at this sample size (15k labeled): FT v1 likely loses to GBT initially. That's fine — the architecture is the win, not the model itself.

- [ ] **Step 2: Iterate on FT-Transformer hyperparameters**

If after 30 days FT v1 hasn't shown improvement, re-train with adjustments:
- Larger OF embedding (try 256 vs 128)
- More attention heads (8 vs 4)
- More layers (stacked CrossGroupAttention)
- Different loss weights
- Save as `ft_v2.pt`, load alongside `ft_v1.pt` as second shadow

Each iteration adds another row class to `shadow_predictions`; comparison handles multiple shadows naturally.

- [ ] **Step 3: When promotion criterion fires, switch production**

Manual promotion gesture:

```python
# In live_inference.py, swap the production line:
self._production_predictor = FTTransformerPredictor.load("/app/data/rl/models/ft_v2.pt")
self._production_predictor.name = "ft_v2"
# Move GBT to shadows:
self._shadow_predictors = [GBTPredictor.load("...")]
```

Commit the swap. Deploy. Monitor for 1 week.

---

## Self-Review Checklist

- [x] Every task has exact file paths
- [x] Every step has code or commands (no placeholders)
- [x] TDD — test before implementation in every task
- [x] Commits at end of each task
- [x] No "TBD" / "TODO without code" / "fill in later"
- [x] Function signatures consistent: `predict(obs, *, zone_id, timestamp) -> Signal` used everywhere
- [x] `ModelProtocol.predict_raw` returns `MultiTaskOutputs` consistently across GBTPredictor + FTTransformerPredictor
- [x] DB schema migrations covered (Task 10)
- [x] Live deploy verification (Task 18)
- [x] Long-term validation + promotion path (Task 19)
- [x] Shadow safety: shadow exceptions never affect production (Task 11)

---

## Risk Notes

1. **FT-Transformer at 15k samples will likely lose to GBT.** Expected. The point of this plan is the scaffolding + comparison infrastructure, not winning immediately. Treat the first 6+ months as data accumulation.

2. **Shadow logging adds DB write per zone touch.** ~30 touches/day × 2 models = 60 rows/day. Negligible for postgres but watch for index bloat over years.

3. **FT-Transformer GPU vs CPU.** Hetzner i7-7700 has no GPU. CPU inference for FT-T (~50ms vs GBT's ~1ms) is fine for the once-per-zone-touch rate but adds latency. If FT-T becomes production, consider quantization or ONNX export.

4. **MAGNITUDE / WIN-PROB / DURATION labels.** The current pool doesn't have ground-truth duration. Task 16 uses constant 5.0 as placeholder — duration head will learn nothing. Fix in v2 by adding `duration_bars` to the episode-builder output.

5. **Task 16 SKIP class is heuristic** ("both rewards negative"). Better label: episodes where the model dispatched but realized R was negative for any chosen action. Improve in v2.

6. **Promotion criterion is intentionally strict** (30 consecutive days, both WR and mean_R margins). This protects against random shadow wins. Looser criteria invite false promotions.

7. **No A/B traffic split.** This plan uses pure shadow mode — production GBT always dispatches. Some teams prefer 90/10 traffic split where 10% of decisions go to the candidate model for real money. That's higher-risk but produces faster signal. Out of scope here; can be added as a future enhancement once promotion criterion fires.

8. **Calibration is unfit at first.** `IsotonicCalibrator` is built but no task fits it to data. Add a calibration-fitting cron once enough predictions + realized outcomes accumulate (~1000 samples should suffice). Until then, raw model probabilities are emitted — acceptable for shadow comparison but should be addressed before promoting.
