"""Tests for ReplayEngine precomputed level injection."""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from src.rl.data.replay_engine import ReplayEngine
from src.rl.config import LevelType

ET = ZoneInfo("US/Eastern")


def _make_tick(hour: int, minute: int, price: float, size: int = 10, side: str = "A") -> dict:
    ts = datetime(2025, 1, 15, hour, minute, 0, tzinfo=ET)
    return {"ts": ts, "price": price, "size": size, "side": side}


def _rth_ticks(price: float = 20000.0, count: int = 100) -> list[dict]:
    ticks = []
    for i in range(count):
        minute = 30 + (i // 4)
        if minute >= 60:
            break
        ticks.append(_make_tick(9, minute, price + (i % 5) * 0.25, 10))
    return ticks


class TestReplayEnginePrecomputedLevels:
    def test_precomputed_levels_appear_in_active_levels(self):
        engine = ReplayEngine()
        ticks = _rth_ticks(20000.0, 100)
        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)
        precomputed = {
            "naked_pocs": [{"date": "2025-01-14", "price": 20050.0}],
            "poc_daily": 19999.0,
            "poc_weekly": 19998.0,
            "poc_monthly": 19997.0,
            "poc_macro": 19996.0,
            "globex_high": 20010.0,
            "globex_low": 19990.0,
            "overnight_high": 20010.0,
            "overnight_low": 19990.0,
            "single_print_zones": [(19995.0, 19995.75)],
        }
        engine.replay_session(ticks, session_dt, precomputed_levels=precomputed)
        snapshot = engine.get_level_snapshot()
        level_types = {lv["type"] for lv in snapshot["active_levels"]}
        assert "naked_poc" in level_types
        assert "poc_daily" in level_types
        assert "poc_weekly" in level_types
        assert "poc_monthly" in level_types
        assert "poc_macro" in level_types
        assert "globex_hl" in level_types
        assert "overnight_hl" in level_types
        assert "single_print" in level_types

    def test_backward_compatible_without_precomputed(self):
        engine = ReplayEngine()
        ticks = _rth_ticks(20000.0, 100)
        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)
        episodes = engine.replay_session(ticks, session_dt)
        snapshot = engine.get_level_snapshot()
        assert len(snapshot["active_levels"]) > 0

    def test_globex_hl_only_active_after_rth_start(self):
        engine = ReplayEngine()
        pre_rth_ticks = [_make_tick(8, m, 20000.0 + m * 0.25, 10) for m in range(60)]
        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)
        precomputed = {
            "naked_pocs": [], "poc_daily": None, "poc_weekly": None,
            "poc_monthly": None, "poc_macro": None,
            "globex_high": 20020.0, "globex_low": 19980.0,
            "overnight_high": 20020.0, "overnight_low": 19980.0,
            "single_print_zones": [],
        }
        engine.replay_session(pre_rth_ticks, session_dt, precomputed_levels=precomputed)
        snapshot = engine.get_level_snapshot()
        level_types = {lv["type"] for lv in snapshot["active_levels"]}
        assert "globex_hl" not in level_types

    def test_naked_poc_invalidated_when_price_sweeps_through(self):
        engine = ReplayEngine()
        ticks = []
        for i in range(200):
            minute = 30 + (i // 8)
            if minute >= 60:
                break
            price = 19999.0 + (i % 5) * 1.0
            ticks.append(_make_tick(9, minute, price, 10))
        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)
        precomputed = {
            "naked_pocs": [{"date": "2025-01-10", "price": 20001.0}],
            "poc_daily": None, "poc_weekly": None, "poc_monthly": None,
            "poc_macro": None, "globex_high": None, "globex_low": None,
            "overnight_high": None, "overnight_low": None,
            "single_print_zones": [],
        }
        engine.replay_session(ticks, session_dt, precomputed_levels=precomputed)
        snapshot = engine.get_level_snapshot()
        naked_levels = [lv for lv in snapshot["active_levels"] if lv["type"] == "naked_poc"]
        assert len(naked_levels) == 0
