"""Model-agnostic prediction contract.

Signal — what a model says about a zone touch. Frozen for safety.
PositionState — current broker position summary.
ExecutionContext — Signal + state — what the execution policy consumes.
MultiTaskOutputs — raw multi-head output before calibration → packaged into Signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["CONTINUATION", "REVERSAL", "SKIP"]


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
            return "CONTINUATION"
        if m == self.p_rev:
            return "REVERSAL"
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
    history: tuple[TradeRecord, ...] = field(default_factory=tuple)


@dataclass
class MultiTaskOutputs:
    """Raw multi-head model output BEFORE calibration. Mutable on purpose —
    calibration is a separate step that produces the final immutable Signal."""

    direction_logits: list[float]  # length 3: [cont, rev, skip]
    magnitude_R: float

    def __post_init__(self) -> None:
        if len(self.direction_logits) != 3:
            raise ValueError(f"direction_logits must have 3 elements (CONT/REV/SKIP), got {len(self.direction_logits)}")

    win_probability: float
    duration_bars: float
    uncertainty: float
