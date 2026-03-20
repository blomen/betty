"""Tests for scheduler auto-recovery and FIFO browser lock."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.pipeline.scheduler import ProviderSchedule


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
