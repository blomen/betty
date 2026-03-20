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
