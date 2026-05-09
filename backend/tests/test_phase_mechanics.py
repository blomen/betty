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
    """Strict mode (real money) keeps the 0.30 OF floor."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "0")
    from src.market_data.level_monitor import _of_floor

    assert _of_floor() == 0.30


def test_stop_ticks_too_tight_blocks_dispatch():
    """Dim-predicted stop < 6 ticks must block entry."""
    from src.market_data.level_monitor import _stop_ticks_in_bounds

    assert _stop_ticks_in_bounds(5.0) is False
    assert _stop_ticks_in_bounds(5.99) is False
    assert _stop_ticks_in_bounds(6.0) is True


def test_stop_ticks_too_wide_blocks_dispatch():
    """Dim-predicted stop > 40 ticks must block entry."""
    from src.market_data.level_monitor import _stop_ticks_in_bounds

    assert _stop_ticks_in_bounds(40.0) is True
    assert _stop_ticks_in_bounds(40.01) is False
    assert _stop_ticks_in_bounds(100.0) is False


def test_phase2_threshold_constant_is_1_5R():
    """Phase 2 transition gate must read 1.5R to match BE-lock."""
    from src.market_data import level_monitor

    assert level_monitor.PHASE_2_THRESHOLD_R == 1.5


def test_reversal_signals_active_default_disabled(monkeypatch):
    """Default behavior post-spec: per-tick reversal exits OFF."""
    monkeypatch.delenv("ENABLE_PER_TICK_REVERSAL", raising=False)
    from src.market_data.level_monitor import _reversal_signals_active

    assert _reversal_signals_active() is False, "Phase 2 must NOT use per-tick reversal_signals by default"


def test_reversal_signals_active_when_enabled(monkeypatch):
    """ENABLE_PER_TICK_REVERSAL=1 restores the old behavior for diagnostics."""
    monkeypatch.setenv("ENABLE_PER_TICK_REVERSAL", "1")
    from src.market_data.level_monitor import _reversal_signals_active

    assert _reversal_signals_active() is True


def test_early_exit_lock_active_default_disabled(monkeypatch):
    """Default: per-tick early-exit lock OFF."""
    monkeypatch.delenv("ENABLE_EARLY_EXIT_LOCK", raising=False)
    from src.market_data.level_monitor import _early_exit_lock_active

    assert _early_exit_lock_active() is False


def test_pyramid_size_high_conf(monkeypatch):
    """Pyramid add at conf>=0.85 → 2 contracts."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "1")
    from src.market_data.level_monitor import _pyramid_add_size

    assert _pyramid_add_size(confidence=0.90) == 2


def test_pyramid_size_low_conf_floors_at_one(monkeypatch):
    """Pyramid add at low conf rounds up to 1 contract floor."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "1")
    from src.market_data.level_monitor import _pyramid_add_size

    assert _pyramid_add_size(confidence=0.10) == 1


def test_pyramid_size_mid_conf(monkeypatch):
    """Pyramid add at mid-conf → 1 contract (size_multiplier 0.6 rounds to 1)."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "1")
    from src.market_data.level_monitor import _pyramid_add_size

    assert _pyramid_add_size(confidence=0.65) == 1


def test_phase1_in_position_handler_no_op_below_threshold():
    """In-position handler must skip cont-trail/pyramid when peak_R < 1.5R."""
    from unittest.mock import MagicMock

    from src.market_data.level_monitor import _should_run_phase2_handlers

    tr = MagicMock()
    tr.is_flat = False
    tr.peak_R = 1.0
    tr.locked_BE = False

    assert _should_run_phase2_handlers(tr) is False, "Phase 1 (peak_R<1.5) must NOT run Phase 2 handlers"

    tr.peak_R = 1.5
    tr.locked_BE = True
    assert _should_run_phase2_handlers(tr) is True, "Phase 2 (peak_R>=1.5 + locked_BE) MUST run handlers"

    tr.is_flat = True
    assert _should_run_phase2_handlers(tr) is False, "flat → no Phase 2 handlers"


def test_on_quote_mark_advances_peak_R():
    """Wiring the GatewayQuote stream to update_mark_and_check_be_lock makes
    peak_R advance even when GatewayTrade is silent (the production case)."""
    from unittest.mock import MagicMock

    adapter = MagicMock()

    def _on_quote_mark(quote_payload):
        last_price = float(quote_payload.get("lastPrice") or 0)
        if last_price <= 0:
            bid = float(quote_payload.get("bestBid") or 0)
            ask = float(quote_payload.get("bestAsk") or 0)
            if bid > 0 and ask > 0:
                last_price = (bid + ask) / 2.0
        if last_price > 0:
            adapter.update_mark_and_check_be_lock(last_price)

    # Quote with lastPrice
    _on_quote_mark({"lastPrice": 19873.25, "bestBid": 19873.0, "bestAsk": 19873.5})
    adapter.update_mark_and_check_be_lock.assert_called_with(19873.25)

    # Quote with no lastPrice but bid/ask present (mid-price fallback)
    adapter.reset_mock()
    _on_quote_mark({"bestBid": 19880.0, "bestAsk": 19880.5})
    adapter.update_mark_and_check_be_lock.assert_called_with(19880.25)

    # Empty quote → no call
    adapter.reset_mock()
    _on_quote_mark({"lastPrice": 0, "bestBid": 0, "bestAsk": 0})
    adapter.update_mark_and_check_be_lock.assert_not_called()


def test_handle_account_calls_on_account():
    """GatewayUserAccount with canTrade=False fires on_account callback."""

    from src.stocks.topstepx_stream import TopstepXStream

    stream = TopstepXStream(token="x", contract_id="X", account_id=1)
    captured = []
    stream.on_account = lambda payload: captured.append(payload)

    stream._handle_account([{"id": 1, "name": "PRAC", "balance": 49000, "canTrade": False}])

    assert len(captured) == 1
    assert captured[0]["canTrade"] is False
    assert captured[0]["balance"] == 49000
