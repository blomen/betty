"""Tests for Phase 1 / Phase 2 entry gates and state-machine behavior."""


def test_conf_floor_is_zero_in_reckless_mode(monkeypatch):
    """Reckless mode (paper-phase) must accept any non-zero confidence."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "1")
    from src.market_data.level_monitor import _conf_floor

    assert _conf_floor() == 0.0, "Paper-phase floor must be 0"


def test_conf_floor_is_strict_when_reckless_disabled(monkeypatch):
    """Strict mode keeps the 0.15 floor for live-money runs."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "0")
    from src.market_data.level_monitor import _conf_floor

    assert _conf_floor() == 0.15


def test_of_floor_is_zero_in_reckless_mode(monkeypatch):
    """Reckless mode keeps OF floor at 0 in BOTH gate-display and broker dispatch."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "1")
    from src.market_data.level_monitor import _of_floor

    assert _of_floor() == 0.0


def test_of_floor_strict(monkeypatch):
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "0")
    from src.market_data.level_monitor import _of_floor

    assert _of_floor() == 0.30
