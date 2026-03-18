"""Tests for the level touch backfill pipeline."""

from src.ml.level_touch.backfill import detect_virtual_touches


def test_detect_crossing_from_below():
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},
        {"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5},
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1
    assert touches[0]["approach_direction"] == "from_below"
    assert touches[0]["candle_index"] == 1


def test_detect_crossing_from_above():
    candles = [
        {"o": 20002.0, "h": 20003.0, "l": 20001.0, "c": 20002.5},
        {"o": 20001.0, "h": 20002.0, "l": 19999.0, "c": 19999.5},
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1
    assert touches[0]["approach_direction"] == "from_above"


def test_deduplication_within_30_candles():
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},
        {"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5},
    ]
    for _ in range(10):
        candles.append({"o": 20000.0, "h": 20001.0, "l": 19999.0, "c": 20000.25})
    candles.append({"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5})
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1


def test_no_touch():
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},
        {"o": 19998.5, "h": 19999.5, "l": 19997.5, "c": 19999.0},
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 0


def test_multiple_levels_detected():
    """Two distinct levels can both be touched in the same session."""
    candles = [
        {"o": 19990.0, "h": 19992.0, "l": 19989.0, "c": 19991.0},
        {"o": 19991.0, "h": 19996.0, "l": 19990.0, "c": 19995.5},  # crosses VAL at 19995
        {"o": 19995.5, "h": 20001.0, "l": 19994.0, "c": 20000.5},  # crosses VAH at 20000
    ]
    levels = [
        {"name": "VAL", "price": 19995.0, "type": "val", "category": "session"},
        {"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"},
    ]
    touches = detect_virtual_touches(candles, levels)
    level_names = {t["level_name"] for t in touches}
    assert "VAL" in level_names
    assert "VAH" in level_names


def test_touch_dict_keys():
    """Returned touch dicts contain the expected keys."""
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},
        {"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5},
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1
    touch = touches[0]
    assert "level_name" in touch
    assert "level_type" in touch
    assert "level_category" in touch
    assert "level_price" in touch
    assert "approach_direction" in touch
    assert "candle_index" in touch


def test_exact_touch_from_below():
    """prev_close < level <= high: counts as from_below touch."""
    candles = [
        {"o": 19998.0, "h": 19999.75, "l": 19997.0, "c": 19999.5},
        {"o": 19999.5, "h": 20000.0, "l": 19999.0, "c": 19999.75},  # high == level_price
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1
    assert touches[0]["approach_direction"] == "from_below"


def test_exact_touch_from_above():
    """prev_close > level >= low: counts as from_above touch."""
    candles = [
        {"o": 20001.0, "h": 20002.0, "l": 20000.25, "c": 20000.5},
        {"o": 20000.5, "h": 20001.0, "l": 20000.0, "c": 20000.25},  # low == level_price
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1
    assert touches[0]["approach_direction"] == "from_above"


def test_dedup_resets_after_30_candles():
    """A second touch on the same level beyond 30 candles IS recorded."""
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},
        {"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5},  # first touch at index 1
    ]
    # 30 candles hovering near but not touching
    for _ in range(30):
        candles.append({"o": 20000.0, "h": 20000.5, "l": 19999.5, "c": 20000.25})
    # 31st candle: comes from below and crosses level again
    candles.append({"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5})

    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 2
