"""Tests for the rehedge scheduler tier."""

import asyncio

import pytest

from src.pipeline.scheduler import ExtractionScheduler


class TestRehedgeTier:
    @pytest.mark.asyncio
    async def test_start_rehedge_tier_creates_task(self):
        sched = ExtractionScheduler()
        await sched.start_rehedge_tier(interval_seconds=300)
        try:
            assert sched._rehedge_task is not None
            assert not sched._rehedge_task.done()
        finally:
            if sched._rehedge_task:
                sched._rehedge_task.cancel()
                try:
                    await sched._rehedge_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_start_twice_warns_no_double_task(self):
        sched = ExtractionScheduler()
        await sched.start_rehedge_tier(interval_seconds=300)
        first = sched._rehedge_task
        try:
            await sched.start_rehedge_tier(interval_seconds=300)
            # Second call must NOT replace the running task
            assert sched._rehedge_task is first
        finally:
            if sched._rehedge_task:
                sched._rehedge_task.cancel()
                try:
                    await sched._rehedge_task
                except asyncio.CancelledError:
                    pass
