"""Tests for extraction health detector."""

from unittest.mock import mock_open, patch

import yaml

from src.pipeline.health import get_provider_intervals

SAMPLE_YAML = yaml.dump(
    {
        "extraction_scheduling": {
            "sharp": {"providers": ["pinnacle"], "interval_minutes": 1},
            "api_soft": {"providers": ["unibet", "betinia"], "interval_minutes": 2},
            "browser_soft": {"providers": ["888sport"], "interval_minutes": 10},
        },
        "active": ["pinnacle", "unibet", "betinia", "888sport", "cloudbet"],
    }
)


def test_get_provider_intervals_maps_active_providers():
    with patch("src.pipeline.health.get_config_path", return_value="fake.yaml"):
        with patch("builtins.open", mock_open(read_data=SAMPLE_YAML)):
            result = get_provider_intervals()

    assert result["pinnacle"] == 1
    assert result["unibet"] == 2
    assert result["betinia"] == 2
    assert result["888sport"] == 10
    # cloudbet is active but not in any tier — should not appear
    assert "cloudbet" not in result


def test_get_provider_intervals_excludes_inactive():
    cfg = yaml.dump(
        {
            "extraction_scheduling": {
                "sharp": {"providers": ["pinnacle"], "interval_minutes": 1},
                "api_soft": {"providers": ["unibet", "betinia"], "interval_minutes": 2},
            },
            "active": ["pinnacle"],  # only pinnacle active
        }
    )
    with patch("src.pipeline.health.get_config_path", return_value="fake.yaml"):
        with patch("builtins.open", mock_open(read_data=cfg)):
            result = get_provider_intervals()

    assert result == {"pinnacle": 1}


# ── Tests for assess_extraction_health ──────────────────────────────────────

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.pipeline.health import assess_extraction_health


def _utcnow():
    return datetime.now(timezone.utc)


def _row(provider_id, status="success", start_time=None, error_message=None):
    """Create a tuple matching the SQL SELECT column order."""
    return (provider_id, status, start_time or _utcnow(), error_message)


def _mock_db(metric_rows, opp_current=100, opp_previous=100):
    """Mock DB session that returns metric rows and opportunity counts."""
    db = MagicMock()
    call_count = [0]

    def fake_execute(stmt, params=None):
        result = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: provider_run_metrics query
            result.fetchall.return_value = metric_rows
        else:
            # Second call: opportunities query
            result.fetchone.return_value = (opp_current, opp_previous)
        return result

    db.execute = fake_execute
    return db


# --- Check 1: Sharp source down ---


def test_sharp_source_down_detected():
    intervals = {"pinnacle": 1, "unibet": 2}
    metrics = [
        _row("pinnacle", "success", _utcnow() - timedelta(minutes=20)),
        _row("unibet", "success", _utcnow() - timedelta(minutes=1)),
    ]
    status, issues = assess_extraction_health(_mock_db(metrics), intervals)
    assert status == "critical"
    assert any("pinnacle" in i.lower() for i in issues)


def test_sharp_source_healthy():
    intervals = {"pinnacle": 1, "unibet": 2}
    metrics = [
        _row("pinnacle", "success", _utcnow() - timedelta(minutes=1)),
        _row("unibet", "success", _utcnow() - timedelta(minutes=1)),
    ]
    status, issues = assess_extraction_health(_mock_db(metrics), intervals)
    assert status == "ok"


# --- Check 2: Consecutive failures ---


def test_consecutive_failures_critical():
    intervals = {"polymarket": 5}
    now = _utcnow()
    metrics = [_row("polymarket", "failed", now - timedelta(minutes=i), "UniqueViolation") for i in range(5)]
    status, issues = assess_extraction_health(_mock_db(metrics), intervals)
    assert status == "critical"
    assert any("consecutive" in i.lower() and "polymarket" in i.lower() for i in issues)


def test_consecutive_failures_warning():
    intervals = {"polymarket": 5}
    now = _utcnow()
    metrics = [
        _row("polymarket", "failed", now - timedelta(minutes=1), "timeout"),
        _row("polymarket", "failed", now - timedelta(minutes=2), "timeout"),
        _row("polymarket", "success", now - timedelta(minutes=3)),
    ]
    status, issues = assess_extraction_health(_mock_db(metrics), intervals)
    assert status == "warning"
    assert any("2 consecutive" in i.lower() for i in issues)


# --- Check 3: Provider staleness ---


def test_provider_staleness_detected():
    intervals = {"pinnacle": 1, "unibet": 2}
    metrics = [
        _row("pinnacle", "success", _utcnow() - timedelta(minutes=1)),
        _row("unibet", "success", _utcnow() - timedelta(minutes=30)),
    ]
    status, issues = assess_extraction_health(_mock_db(metrics), intervals)
    assert any("unibet" in i.lower() and "stale" in i.lower() for i in issues)


def test_provider_no_runs_flagged():
    intervals = {"pinnacle": 1, "unibet": 2}
    metrics = [
        _row("pinnacle", "success", _utcnow() - timedelta(minutes=1)),
        # unibet has no runs at all
    ]
    status, issues = assess_extraction_health(_mock_db(metrics), intervals)
    assert any("unibet" in i.lower() and "no successful run" in i.lower() for i in issues)


# --- Check 4: DB integrity errors ---


def test_db_integrity_errors_detected():
    intervals = {"unibet": 2}
    metrics = [
        _row(
            "unibet",
            "failed",
            _utcnow() - timedelta(minutes=2),
            "UniqueViolation: duplicate key value violates unique constraint",
        ),
    ]
    status, issues = assess_extraction_health(_mock_db(metrics), intervals)
    assert status == "critical"
    assert any("integrity" in i.lower() for i in issues)


# --- Check 5: Opportunity volume drop ---


def test_opportunity_volume_drop_detected():
    intervals = {"pinnacle": 1}
    metrics = [_row("pinnacle", "success", _utcnow())]
    status, issues = assess_extraction_health(_mock_db(metrics, opp_current=40, opp_previous=100), intervals)
    assert any("dropped" in i.lower() for i in issues)


def test_opportunity_volume_stable():
    intervals = {"pinnacle": 1}
    metrics = [_row("pinnacle", "success", _utcnow())]
    status, issues = assess_extraction_health(_mock_db(metrics, opp_current=95, opp_previous=100), intervals)
    assert not any("dropped" in i.lower() for i in issues)


def test_opportunity_volume_low_baseline_skipped():
    intervals = {"pinnacle": 1}
    metrics = [_row("pinnacle", "success", _utcnow())]
    # Previous hour only had 10 opps — too few to be meaningful
    status, issues = assess_extraction_health(_mock_db(metrics, opp_current=2, opp_previous=10), intervals)
    assert not any("dropped" in i.lower() for i in issues)
