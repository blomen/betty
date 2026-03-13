from src.market_data.levels import detect_naked_pocs


def test_naked_poc_not_revisited():
    """POC from prior session that was never touched should be detected."""
    prior_sessions = [
        {"date": "2026-03-10", "poc": 21450.0},
        {"date": "2026-03-11", "poc": 21680.0},
        {"date": "2026-03-12", "poc": 21820.0},
    ]
    bars_since = [
        {"high": 21600, "low": 21500},
        {"high": 21700, "low": 21550},
        {"high": 21900, "low": 21700},
        {"high": 21850, "low": 21750},
    ]
    result = detect_naked_pocs(prior_sessions, bars_since)
    naked_prices = [r["price"] for r in result]
    assert 21450.0 in naked_prices
    assert 21680.0 not in naked_prices
    assert 21820.0 not in naked_prices


def test_no_naked_pocs():
    """All POCs touched should return empty list."""
    prior_sessions = [{"date": "2026-03-10", "poc": 21600.0}]
    bars_since = [{"high": 21700, "low": 21500}]
    result = detect_naked_pocs(prior_sessions, bars_since)
    assert result == []


def test_empty_sessions():
    """No prior sessions should return empty list."""
    result = detect_naked_pocs([], [])
    assert result == []
