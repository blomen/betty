from src.market_data.levels import compute_developing_poc


def test_poc_migrating_up():
    """POC should migrate up when recent volume concentrates higher."""
    bars = []
    for i in range(10):
        bars.append({"high": 102, "low": 98, "close": 100, "volume": 1000})
    for i in range(10):
        bars.append({"high": 107, "low": 103, "close": 105, "volume": 1500})

    result = compute_developing_poc(bars)
    assert result["developing_poc"] is not None
    assert result["direction"] == "up"


def test_poc_stable():
    """Stable POC should report flat direction."""
    bars = [{"high": 102, "low": 98, "close": 100, "volume": 1000}] * 20
    result = compute_developing_poc(bars)
    assert result["direction"] == "flat"


def test_empty_bars():
    """No bars should return None."""
    result = compute_developing_poc([])
    assert result["developing_poc"] is None
    assert result["direction"] == "flat"
