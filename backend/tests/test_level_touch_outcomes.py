"""Tests for level touch outcome classifier and OutcomeTracker."""

from src.ml.level_touch.outcomes import classify_outcome, OutcomeTracker


# ---------------------------------------------------------------------------
# Task 4: classify_outcome (pure function)
# ---------------------------------------------------------------------------

def test_strong_reversal_from_below():
    result = classify_outcome(20000.0, "from_below",
        [20002.0, 20001.0, 19999.0, 19996.0, 19994.0],
        [19999.0, 19998.0, 19996.0, 19993.0, 19993.75])
    assert result["outcome"] == "strong_reversal"
    assert result["max_reversal_ticks"] >= 20


def test_strong_continuation_from_below():
    result = classify_outcome(20000.0, "from_below",
        [20002.0, 20003.0, 20005.0, 20006.0, 20006.25],
        [20000.0, 20001.0, 20003.0, 20004.0, 20005.0])
    assert result["outcome"] == "strong_continuation"
    assert result["max_continuation_ticks"] >= 20


def test_chop():
    result = classify_outcome(20000.0, "from_below",
        [20001.0, 20001.25, 20000.75, 20001.0, 20000.50],
        [19999.5, 19999.75, 19999.25, 19999.0, 19999.50])
    assert result["outcome"] == "chop"


def test_weak_reversal_from_above():
    result = classify_outcome(20000.0, "from_above",
        [20003.0, 20003.0, 20003.0, 20003.0, 20003.0],
        [20000.0, 20000.25, 20000.50, 20001.0, 20001.0])
    assert result["outcome"] == "weak_reversal"
    assert 8 <= result["max_reversal_ticks"] < 20


def test_weak_continuation_from_above():
    result = classify_outcome(20000.0, "from_above",
        [20000.0, 19999.5, 19999.0, 19998.5, 19998.0],
        [19999.0, 19998.5, 19997.75, 19997.50, 19997.50])
    assert result["outcome"] == "weak_continuation"
    assert 8 <= result["max_continuation_ticks"] < 20


def test_empty_candles():
    result = classify_outcome(20000.0, "from_below", [], [])
    assert result["outcome"] is None


# ---------------------------------------------------------------------------
# Task 5: OutcomeTracker
# ---------------------------------------------------------------------------

def test_outcome_tracker_registers_touch():
    tracker = OutcomeTracker()
    tracker.register_touch(
        symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1000.0, session_date="2026-03-18", features={"delta": 500})
    assert len(tracker._pending) == 1


def test_outcome_tracker_deduplication():
    tracker = OutcomeTracker()
    tracker.register_touch(symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1000.0, session_date="2026-03-18", features={"delta": 500})
    tracker.register_touch(symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1500.0, session_date="2026-03-18", features={"delta": 600})
    assert len(tracker._pending) == 1  # deduped


def test_outcome_tracker_different_levels():
    tracker = OutcomeTracker()
    tracker.register_touch(symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1000.0, session_date="2026-03-18", features={"delta": 500})
    tracker.register_touch(symbol="NQ", level_name="POC", level_type="poc",
        level_price=19950.0, approach_direction="from_above",
        touch_ts=1000.0, session_date="2026-03-18", features={"delta": -200})
    assert len(tracker._pending) == 2
