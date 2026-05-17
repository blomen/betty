import time

import pytest

from src.rl.signal.types import ExecutionContext, MultiTaskOutputs, PositionState, Signal


def test_signal_is_immutable():
    sig = Signal(
        p_cont=0.6,
        p_rev=0.3,
        p_skip=0.1,
        expected_R=1.2,
        win_probability=0.65,
        duration_bars=8.0,
        uncertainty=0.05,
        timestamp=time.time(),
        zone_id=42,
    )
    with pytest.raises((TypeError, AttributeError)):
        sig.p_cont = 0.9


def test_signal_probabilities_sum_to_one_invariant():
    """Dataclass doesn't enforce — caller responsibility. But we expose
    a helper that asserts for debug."""
    sig = Signal(
        p_cont=0.6,
        p_rev=0.3,
        p_skip=0.1,
        expected_R=1.2,
        win_probability=0.65,
        duration_bars=8.0,
        uncertainty=0.05,
        timestamp=1.0,
        zone_id=1,
    )
    assert abs(sig.p_cont + sig.p_rev + sig.p_skip - 1.0) < 1e-6


def test_signal_action_property_picks_argmax():
    sig = Signal(
        p_cont=0.5,
        p_rev=0.3,
        p_skip=0.2,
        expected_R=1.0,
        win_probability=0.6,
        duration_bars=5.0,
        uncertainty=0.1,
        timestamp=1.0,
        zone_id=1,
    )
    assert sig.action == "CONTINUATION"

    sig2 = Signal(
        p_cont=0.2,
        p_rev=0.5,
        p_skip=0.3,
        expected_R=1.0,
        win_probability=0.6,
        duration_bars=5.0,
        uncertainty=0.1,
        timestamp=1.0,
        zone_id=2,
    )
    assert sig2.action == "REVERSAL"

    sig3 = Signal(
        p_cont=0.2,
        p_rev=0.3,
        p_skip=0.5,
        expected_R=1.0,
        win_probability=0.6,
        duration_bars=5.0,
        uncertainty=0.1,
        timestamp=1.0,
        zone_id=3,
    )
    assert sig3.action == "SKIP"


def test_signal_confidence_property_argmax_minus_secondbest():
    """Confidence = max(p) - second-max(p). Matches the GBT abs(p_cont - p_rev)
    semantics for 2-class but extends naturally to 3-class."""
    sig = Signal(
        p_cont=0.7,
        p_rev=0.2,
        p_skip=0.1,
        expected_R=1.0,
        win_probability=0.7,
        duration_bars=5.0,
        uncertainty=0.05,
        timestamp=1.0,
        zone_id=1,
    )
    assert sig.confidence == pytest.approx(0.5)


def test_position_state_is_immutable():
    ps = PositionState(side="long", peak_R=2.1, time_in_trade_s=120.0, current_R=1.5, size=1)
    with pytest.raises((TypeError, AttributeError)):
        ps.peak_R = 9.9


def test_execution_context_is_immutable():
    sig = Signal(
        p_cont=0.6,
        p_rev=0.3,
        p_skip=0.1,
        expected_R=1.2,
        win_probability=0.65,
        duration_bars=8.0,
        uncertainty=0.05,
        timestamp=1.0,
        zone_id=1,
    )
    ps = PositionState(side=None, peak_R=0.0, time_in_trade_s=0.0, current_R=0.0, size=0)
    ctx = ExecutionContext(signal=sig, position=ps, session_pnl_R=0.5, consec_losses=0, history=())
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
