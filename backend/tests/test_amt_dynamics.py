"""Tests for AMTDynamicsTracker and AMT dynamics feature extractor."""
from __future__ import annotations

import numpy as np
import pytest

from src.market_data.amt_dynamics import AMTDynamicsTracker
from src.rl.features.amt_dynamics_features import extract_amt_dynamics_features, _N_FEATURES

SNAPSHOT_KEYS = {
    "ib_ext_up_count",
    "ib_ext_down_count",
    "ib_max_extension",
    "ib_ext_net_direction",
    "developing_day_type",
    "day_type_confidence",
    "responsive_ratio",
    "initiative_ratio",
    "va_acceptance_high",
    "va_rejection_high",
    "va_acceptance_low",
    "va_rejection_low",
    "poc_migration_speed",
    "va_width_expansion_rate",
    "balance_duration",
    "balance_width",
    "single_print_proximity",
    "excess_high",
    "excess_low",
    "otf_activity",
}


def _make_tracker(**overrides) -> AMTDynamicsTracker:
    """Helper: create and initialize a tracker with sensible defaults."""
    session = {
        "ib_high": 20000.0,
        "ib_low": 19980.0,
        "vah": 19998.0,
        "val": 19982.0,
        "poc": 19990.0,
        "single_prints": [(19985.0, 19986.0)],
    }
    session.update(overrides)
    t = AMTDynamicsTracker()
    t.initialize(session)
    return t


class TestInitialize:
    def test_initialize_sets_session_data(self):
        t = _make_tracker()
        assert t.ib_high == 20000.0
        assert t.ib_low == 19980.0
        assert t.ib_range == 20.0
        assert t.vah == 19998.0
        assert t.val == 19982.0
        assert t.poc == 19990.0
        assert t.single_prints == [(19985.0, 19986.0)]
        assert t.session_high == 20000.0
        assert t.session_low == 19980.0

    def test_initialize_defaults(self):
        """Missing vah/val/poc should fall back to IB-derived values."""
        t = AMTDynamicsTracker()
        t.initialize({"ib_high": 100.0, "ib_low": 90.0})
        assert t.vah == 100.0
        assert t.val == 90.0
        assert t.poc == 95.0


class TestSnapshot:
    def test_snapshot_returns_all_keys(self):
        t = _make_tracker()
        snap = t.snapshot()
        assert set(snap.keys()) == SNAPSHOT_KEYS

    def test_snapshot_all_finite(self):
        t = _make_tracker()
        # Feed some data
        for i in range(100):
            t.update(19990.0 + i * 0.1, 5, "buy")
        snap = t.snapshot()
        import math
        for k, v in snap.items():
            assert math.isfinite(v), f"{k} is not finite: {v}"

    def test_snapshot_without_initialize_returns_zeros(self):
        """Calling snapshot on uninitialized tracker should still work."""
        t = AMTDynamicsTracker()
        snap = t.snapshot()
        assert set(snap.keys()) == SNAPSHOT_KEYS


class TestIBExtensions:
    def test_ib_extension_up(self):
        """Price goes above IB, back inside, above again = 2 up extensions."""
        t = _make_tracker()
        # Start inside IB
        t.update(19990.0, 10, "buy")
        # Cross above IB
        t.update(20001.0, 10, "buy")
        assert t._ib_ext_up_count == 1
        # Come back inside
        t.update(19995.0, 10, "sell")
        assert t._ib_ext_up_count == 1
        # Cross above again
        t.update(20002.0, 10, "buy")
        assert t._ib_ext_up_count == 2

        snap = t.snapshot()
        assert snap["ib_ext_up_count"] == 2.0
        assert snap["ib_ext_down_count"] == 0.0

    def test_ib_extension_both_sides(self):
        """Extensions both ways, net direction = 0."""
        t = _make_tracker()
        # One up extension
        t.update(19990.0, 10, "buy")
        t.update(20005.0, 10, "buy")
        # Back inside
        t.update(19990.0, 10, "sell")
        # One down extension
        t.update(19975.0, 10, "sell")

        snap = t.snapshot()
        assert snap["ib_ext_up_count"] == 1.0
        assert snap["ib_ext_down_count"] == 1.0
        assert snap["ib_ext_net_direction"] == 0.0

    def test_ib_max_extension_magnitude(self):
        t = _make_tracker()
        # Extension of 10 points above IB high (20000)
        t.update(20010.0, 10, "buy")
        # Extension of 5 points below IB low (19980)
        t.update(19975.0, 10, "sell")

        snap = t.snapshot()
        # Max extension = 10 / 20 (ib_range) = 0.5
        assert snap["ib_max_extension"] == 0.5


class TestResponsiveVsInitiative:
    def test_responsive_vs_initiative(self):
        """Volume inside VA is responsive, outside is initiative."""
        t = _make_tracker()
        # VA is 19982 - 19998
        # Responsive: inside VA
        t.update(19990.0, 100, "buy")
        t.update(19985.0, 50, "sell")
        # Initiative: outside VA
        t.update(20005.0, 30, "buy")
        t.update(19975.0, 20, "sell")

        snap = t.snapshot()
        total = 100 + 50 + 30 + 20  # 200
        assert abs(snap["responsive_ratio"] - 150 / total) < 1e-9
        assert abs(snap["initiative_ratio"] - 50 / total) < 1e-9

    def test_otf_activity(self):
        """OTF delta tracks directional volume outside VA."""
        t = _make_tracker()
        # All initiative buying
        t.update(20005.0, 100, "buy")
        snap = t.snapshot()
        assert snap["otf_activity"] > 0


class TestDayType:
    def test_developing_day_type_non_trend(self):
        """Session stays inside IB = non_trend (0.0)."""
        t = _make_tracker()
        # Trade within IB range, don't exceed it
        t.update(19990.0, 10, "buy")
        t.update(19995.0, 10, "buy")

        snap = t.snapshot()
        assert snap["developing_day_type"] == 0.0  # non_trend

    def test_developing_day_type_trend(self):
        """Massive one-sided extension = trend (0.8)."""
        t = _make_tracker()
        # Huge up extension: 50 points above IB high (20000), IB range = 20
        # daily_range = 20050 - 19980 = 70, ratio = 3.5 (> 2.0)
        # ext_up = 50, ext_down = 0 => one side > 3x other => trend
        t.update(20050.0, 100, "buy")

        snap = t.snapshot()
        assert snap["developing_day_type"] == 0.8  # trend

    def test_developing_day_type_normal(self):
        """Moderate extension = normal (0.2)."""
        t = _make_tracker()
        # IB range = 20, extend to make range_ratio ~ 1.3 (between 1.15 and 1.5)
        # daily_range needs to be ~26 => extend 6 points above IB
        t.update(20006.0, 10, "buy")

        snap = t.snapshot()
        assert snap["developing_day_type"] == 0.2  # normal

    def test_day_type_confidence_at_threshold(self):
        """Confidence should be low near thresholds."""
        t = _make_tracker()
        # ratio = 1.15 exactly => at threshold => low confidence
        # IB range = 20, need daily_range = 23 => extend 3 above IB high
        t.update(20003.0, 10, "buy")

        snap = t.snapshot()
        assert snap["day_type_confidence"] < 0.5


class TestPeriodClose:
    def test_on_period_close_va_rejection(self):
        """Price probes above VAH, snaps back within 2 periods = rejection."""
        t = _make_tracker()
        # Period 1: probes above VAH (19998)
        t.on_period_close(
            period_high=20005.0, period_low=19990.0,
            developing_poc=19990.0, developing_vah=19998.0, developing_val=19982.0,
        )
        # Period 2: still above (countdown ticking)
        # Actually for rejection the price must snap back (period_high <= VAH)
        # Period 1 set pending, countdown=2
        # Period 2: price back inside => countdown decrements to 1
        t.on_period_close(
            period_high=19996.0, period_low=19985.0,
            developing_poc=19990.0, developing_vah=19998.0, developing_val=19982.0,
        )
        # Period 3: still inside => countdown decrements to 0, rejection counted
        t.on_period_close(
            period_high=19995.0, period_low=19988.0,
            developing_poc=19990.0, developing_vah=19998.0, developing_val=19982.0,
        )

        snap = t.snapshot()
        assert snap["va_rejection_high"] >= 1.0

    def test_on_period_close_poc_migration(self):
        """POC moves significantly between periods."""
        t = _make_tracker()
        # Feed several periods with increasing POC
        for i in range(6):
            t.on_period_close(
                period_high=20000.0 + i * 5,
                period_low=19980.0 + i * 5,
                developing_poc=19990.0 + i * 5,  # +5 each period
                developing_vah=19998.0 + i * 5,
                developing_val=19982.0 + i * 5,
            )

        snap = t.snapshot()
        # POC moved 5 points per period, IB range = 20 => migration speed = 5/20 = 0.25
        assert snap["poc_migration_speed"] == pytest.approx(0.25, abs=0.01)

    def test_balance_area_detection(self):
        """Session range within 1.5x IB for 3+ periods = balance."""
        t = _make_tracker()
        # Keep session range tight (within 1.5 * 20 = 30 points)
        for _ in range(4):
            t.on_period_close(
                period_high=20005.0, period_low=19985.0,
                developing_poc=19990.0, developing_vah=19998.0, developing_val=19982.0,
            )

        snap = t.snapshot()
        assert snap["balance_duration"] > 0
        assert snap["balance_width"] > 0

    def test_va_width_expansion(self):
        """VA width expands over time."""
        t = _make_tracker()
        # Initial VA width = 19998 - 19982 = 16
        # Expanding VA
        t.on_period_close(
            period_high=20000.0, period_low=19980.0,
            developing_poc=19990.0, developing_vah=20002.0, developing_val=19978.0,
        )
        t.on_period_close(
            period_high=20005.0, period_low=19975.0,
            developing_poc=19990.0, developing_vah=20005.0, developing_val=19975.0,
        )

        snap = t.snapshot()
        # Latest width = 30, initial = 16 => expansion = (30-16)/16 = 0.875
        assert snap["va_width_expansion_rate"] > 0


class TestEdgeCases:
    def test_update_before_initialize_is_noop(self):
        """Calling update() before initialize() should not crash."""
        t = AMTDynamicsTracker()
        t.update(20000.0, 10, "buy")  # no crash
        snap = t.snapshot()
        assert set(snap.keys()) == SNAPSHOT_KEYS

    def test_high_volume_of_updates(self):
        """Tracker handles many updates without errors (performance sanity)."""
        t = _make_tracker()
        for i in range(10_000):
            price = 19990.0 + (i % 30) - 15
            t.update(price, 1, "buy" if i % 2 == 0 else "sell")
        snap = t.snapshot()
        import math
        for k, v in snap.items():
            assert math.isfinite(v), f"{k} is not finite"


class TestExtractAMTDynamicsFeatures:
    def test_extract_amt_dynamics_features_zeros_when_none(self):
        """None input returns zeros(20)."""
        result = extract_amt_dynamics_features(None)
        assert result.shape == (_N_FEATURES,)
        assert result.dtype == np.float32
        np.testing.assert_array_equal(result, np.zeros(_N_FEATURES, dtype=np.float32))

    def test_extract_amt_dynamics_features_shape_and_dtype(self):
        """Correct shape and dtype from a real snapshot."""
        t = _make_tracker()
        t.update(19990.0, 10, "buy")
        snap = t.snapshot()
        result = extract_amt_dynamics_features(snap)
        assert result.shape == (_N_FEATURES,)
        assert result.dtype == np.float32
        assert np.all(np.isfinite(result))

    def test_extract_amt_dynamics_features_from_tracker(self):
        """Build tracker, update with ticks, extract features, verify IB extension features are non-zero."""
        t = _make_tracker()
        # Start inside IB
        t.update(19990.0, 10, "buy")
        # Cross above IB high (20000) — triggers IB extension up
        t.update(20005.0, 20, "buy")
        # Come back inside
        t.update(19995.0, 10, "sell")
        # Cross below IB low (19980) — triggers IB extension down
        t.update(19970.0, 15, "sell")

        snap = t.snapshot()
        result = extract_amt_dynamics_features(snap)

        # Index 0: ib_ext_up_count should be 1/5 = 0.2
        assert result[0] == pytest.approx(0.2, abs=1e-6)
        # Index 1: ib_ext_down_count should be 1/5 = 0.2
        assert result[1] == pytest.approx(0.2, abs=1e-6)
        # Index 2: ib_max_extension should be > 0 (max ext = 10 ticks / 20 IB range = 0.5, / 300 norm)
        assert result[2] > 0.0
        # Index 3: ib_ext_net_direction = (1-1)/2 = 0 (equal extensions)
        assert result[3] == pytest.approx(0.0, abs=1e-6)
        # All values should be within [-1, 1]
        assert np.all(result >= -1.0)
        assert np.all(result <= 1.0)
