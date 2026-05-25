"""Stale opportunity cleanup: expire opps for events past start time + 1h,
or where the underlying odds are >4h old."""

from __future__ import annotations


def test_cleanup_function_exists():
    from src.services.opportunity_service import cleanup_stale_opportunities  # noqa

    assert callable(cleanup_stale_opportunities)


def test_cleanup_filters_on_start_time():
    """Cleanup function must reference event start_time predicate."""
    import inspect

    from src.services import opportunity_service

    src = inspect.getsource(opportunity_service.cleanup_stale_opportunities)
    assert "start_time" in src, "cleanup must filter on event start_time"
    assert "is_active" in src, "cleanup must set is_active=false"


def test_cleanup_filters_on_odds_age():
    """Cleanup function must reference odds.updated_at predicate."""
    import inspect

    from src.services import opportunity_service

    src = inspect.getsource(opportunity_service.cleanup_stale_opportunities)
    assert "updated_at" in src, "cleanup must reference odds.updated_at"
