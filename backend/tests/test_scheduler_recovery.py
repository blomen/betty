"""Tests for scheduler auto-recovery and FIFO browser lock."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.pipeline.scheduler import ProviderSchedule
from datetime import datetime, timezone, timedelta


def test_provider_schedule_has_revival_fields():
    """ProviderSchedule should have revival_attempts and reviving fields."""
    schedule = ProviderSchedule(
        provider_id="test", category="browser_soft", interval_seconds=3600
    )
    assert schedule.revival_attempts == 0
    assert schedule.reviving is False


@pytest.mark.asyncio
async def test_browser_lock_is_fifo():
    """asyncio.Lock guarantees FIFO ordering of waiters."""
    lock = asyncio.Lock()
    order = []

    async def acquire(label: str, delay: float = 0):
        await asyncio.sleep(delay)
        async with lock:
            order.append(label)
            await asyncio.sleep(0.01)  # Hold lock briefly

    # Lock is held first, then A and B enqueue in order
    async with lock:
        task_a = asyncio.create_task(acquire("A", 0.01))
        task_b = asyncio.create_task(acquire("B", 0.02))
        await asyncio.sleep(0.05)  # Let both enqueue

    await asyncio.gather(task_a, task_b)
    assert order == ["A", "B"], f"Expected FIFO order, got {order}"


@pytest.mark.asyncio
async def test_revival_scheduled_after_permanent_failure():
    """Watchdog should schedule revival for permanently failed providers."""
    from src.pipeline.scheduler import ExtractionScheduler

    scheduler = ExtractionScheduler()
    schedule = ProviderSchedule(
        provider_id="test_provider",
        category="browser_soft",
        interval_seconds=3600,
        consecutive_failures=3,
        running=False,  # Watchdog already killed it
    )
    scheduler._schedules["test_provider"] = schedule

    # Verify revival gets triggered
    with patch.object(scheduler, '_attempt_revival', new_callable=AsyncMock) as mock_revival:
        # Simulate one watchdog tick
        await scheduler._check_schedules_once()

        assert schedule.reviving is True
        # _attempt_revival should have been scheduled (via create_task)


@pytest.mark.asyncio
async def test_starvation_detection_logs_critical(caplog):
    """Watchdog should log CRITICAL when a browser provider hasn't run in 2x its interval."""
    from src.pipeline.scheduler import ExtractionScheduler
    import logging

    scheduler = ExtractionScheduler()
    schedule = ProviderSchedule(
        provider_id="slow_provider",
        category="browser_soft",
        interval_seconds=3600,
        running=True,
        last_completed=datetime.now(timezone.utc) - timedelta(seconds=8000),  # > 2x interval
    )
    # Give it a mock task that looks alive
    schedule.task = MagicMock()
    schedule.task.done.return_value = False
    scheduler._schedules["slow_provider"] = schedule

    with caplog.at_level(logging.CRITICAL):
        await scheduler._check_schedules_once()

    assert any("starving" in r.message for r in caplog.records), \
        f"Expected CRITICAL starvation log, got: {[r.message for r in caplog.records]}"
