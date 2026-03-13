from src.market_data.levels import detect_swing_points


def test_uptrend_structure():
    """Bars that make HH/HL should classify as uptrend."""
    # Pattern: rise to swing high 1 (~110), dip to swing low 1 (~99),
    # rise to swing high 2 (~120, HH), dip to swing low 2 (~109, HL)
    bars = [
        {"high": 100, "low": 95, "close": 98},
        {"high": 103, "low": 98, "close": 101},
        {"high": 106, "low": 101, "close": 104},
        {"high": 109, "low": 104, "close": 107},
        {"high": 110, "low": 105, "close": 108},  # swing high 1
        {"high": 108, "low": 103, "close": 105},
        {"high": 106, "low": 101, "close": 103},
        {"high": 104, "low": 99, "close": 101},   # swing low 1
        {"high": 106, "low": 101, "close": 104},
        {"high": 108, "low": 103, "close": 106},
        {"high": 112, "low": 107, "close": 110},
        {"high": 116, "low": 111, "close": 114},
        {"high": 120, "low": 115, "close": 118},  # swing high 2 (HH)
        {"high": 118, "low": 113, "close": 116},
        {"high": 116, "low": 111, "close": 114},
        {"high": 114, "low": 109, "close": 112},  # swing low 2 (HL)
        {"high": 116, "low": 111, "close": 114},
        {"high": 118, "low": 113, "close": 116},
        {"high": 120, "low": 115, "close": 118},
        {"high": 122, "low": 117, "close": 120},
    ]
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "uptrend"
    assert result["swing_high"] is not None
    assert result["swing_low"] is not None
    assert result["last_hh"] is not None
    assert result["last_hl"] is not None


def test_downtrend_structure():
    """Bars that make LH/LL should classify as downtrend."""
    # ZigZag downtrend: SH1(105) → SL1(75) → SH2(95, LH) → SL2(65, LL)
    bars = [
        # Rise to swing high 1 at index 4
        {"high": 97, "low": 92, "close": 95},
        {"high": 99, "low": 94, "close": 97},
        {"high": 102, "low": 97, "close": 100},
        {"high": 104, "low": 99, "close": 102},
        {"high": 105, "low": 100, "close": 103},  # swing high 1 (index 4)
        # Fall to swing low 1 at index 9
        {"high": 103, "low": 98, "close": 101},
        {"high": 100, "low": 95, "close": 98},
        {"high": 90, "low": 85, "close": 88},
        {"high": 80, "low": 75, "close": 78},
        {"high": 78, "low": 73, "close": 76},    # swing low 1 (index 9)
        # Bounce to swing high 2 at index 14 (LH: 95 < 105)
        {"high": 80, "low": 75, "close": 78},
        {"high": 83, "low": 78, "close": 81},
        {"high": 88, "low": 83, "close": 86},
        {"high": 92, "low": 87, "close": 90},
        {"high": 95, "low": 90, "close": 93},    # swing high 2 (index 14, LH)
        # Fall to swing low 2 at index 19 (LL: 60 < 73)
        {"high": 93, "low": 88, "close": 91},
        {"high": 88, "low": 83, "close": 86},
        {"high": 78, "low": 73, "close": 76},
        {"high": 70, "low": 65, "close": 68},
        {"high": 68, "low": 63, "close": 66},    # swing low 2 (index 19, LL)
        # Trailing bars needed for lookback=3
        {"high": 70, "low": 65, "close": 68},
        {"high": 72, "low": 67, "close": 70},
        {"high": 74, "low": 69, "close": 72},
    ]
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "downtrend"
    assert result["last_lh"] is not None
    assert result["last_ll"] is not None


def test_ranging_structure():
    """Bars that oscillate within range should classify as ranging."""
    bars = []
    for i in range(20):
        offset = 5 if i % 4 < 2 else -5
        bars.append({
            "high": 120 + offset,
            "low": 110 + offset,
            "close": 115 + offset,
        })
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "ranging"


def test_insufficient_bars():
    """Fewer bars than 2*lookback+1 should return ranging with no swings."""
    bars = [{"high": 100, "low": 95, "close": 97}] * 3
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "ranging"
    assert result["swing_high"] is None
    assert result["swing_low"] is None
