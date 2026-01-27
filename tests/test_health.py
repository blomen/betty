"""
Tests for provider health checking.
"""

import asyncio
import time
import pytest
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.pipeline.health import HealthChecker, HealthStatus


# Mock extractor classes for testing
class HealthyExtractor:
    """Mock extractor that always succeeds."""
    async def extract(self, sport, limit=None):
        await asyncio.sleep(0.1)  # Simulate work
        return [{"event": "test"}]


class SlowExtractor:
    """Mock extractor that times out."""
    async def extract(self, sport, limit=None):
        await asyncio.sleep(10)  # Will timeout
        return []


class FailingExtractor:
    """Mock extractor that raises an error."""
    async def extract(self, sport, limit=None):
        raise Exception("Connection failed")


@pytest.mark.asyncio
async def test_health_status_dataclass():
    """Test HealthStatus dataclass."""
    status = HealthStatus(
        healthy=True,
        response_time_ms=150.0
    )

    assert status.healthy
    assert status.response_time_ms == 150.0
    assert status.error is None
    assert status.checked_at is not None


@pytest.mark.asyncio
async def test_health_checker_init():
    """Test HealthChecker initialization."""
    checker = HealthChecker(
        timeout_seconds=5.0,
        cache_ttl_seconds=30
    )

    assert checker.timeout_seconds == 5.0
    assert checker.cache_ttl_seconds == 30


@pytest.mark.asyncio
async def test_health_check_healthy_provider():
    """Test health check for healthy provider."""
    checker = HealthChecker(timeout_seconds=1.0)
    extractor = HealthyExtractor()

    status = await checker.check_provider("test_provider", extractor)

    assert status.healthy
    assert status.response_time_ms > 0
    assert status.error is None


@pytest.mark.asyncio
async def test_health_check_timeout():
    """Test health check timeout."""
    checker = HealthChecker(timeout_seconds=0.5)
    extractor = SlowExtractor()

    status = await checker.check_provider("test_provider", extractor)

    assert not status.healthy
    assert status.response_time_ms > 0
    assert "Timeout" in status.error


@pytest.mark.asyncio
async def test_health_check_failure():
    """Test health check for failing provider."""
    checker = HealthChecker(timeout_seconds=1.0)
    extractor = FailingExtractor()

    status = await checker.check_provider("test_provider", extractor)

    assert not status.healthy
    assert status.response_time_ms > 0
    assert "Connection failed" in status.error


@pytest.mark.asyncio
async def test_health_check_caching():
    """Test health check result caching."""
    checker = HealthChecker(cache_ttl_seconds=1)
    extractor = HealthyExtractor()

    # First check
    status1 = await checker.check_provider("test_provider", extractor)
    time1 = time.time()

    # Second check immediately (should use cache)
    status2 = await checker.check_provider("test_provider", extractor)
    time2 = time.time()

    # Should be instant (cached)
    assert time2 - time1 < 0.05

    # Should return same result
    assert status1.checked_at == status2.checked_at


@pytest.mark.asyncio
async def test_health_check_cache_expiration():
    """Test health check cache expiration."""
    checker = HealthChecker(cache_ttl_seconds=1)
    extractor = HealthyExtractor()

    # First check
    status1 = await checker.check_provider("test_provider", extractor)

    # Wait for cache to expire
    await asyncio.sleep(1.1)

    # Second check (cache expired, should recheck)
    status2 = await checker.check_provider("test_provider", extractor)

    # Should have different timestamps
    assert status1.checked_at != status2.checked_at


@pytest.mark.asyncio
async def test_health_check_force():
    """Test forcing health check bypass cache."""
    checker = HealthChecker(cache_ttl_seconds=60)
    extractor = HealthyExtractor()

    # First check
    status1 = await checker.check_provider("test_provider", extractor)

    # Forced check (should bypass cache)
    status2 = await checker.check_provider("test_provider", extractor, force=True)

    # Should have different timestamps
    assert status1.checked_at != status2.checked_at


@pytest.mark.asyncio
async def test_get_cached_status():
    """Test getting cached status."""
    checker = HealthChecker()
    extractor = HealthyExtractor()

    # No cache initially
    cached = checker.get_cached_status("test_provider")
    assert cached is None

    # Perform check
    await checker.check_provider("test_provider", extractor)

    # Should be cached now
    cached = checker.get_cached_status("test_provider")
    assert cached is not None
    assert cached.healthy


@pytest.mark.asyncio
async def test_clear_cache_all():
    """Test clearing all health check cache."""
    checker = HealthChecker()
    extractor = HealthyExtractor()

    # Check multiple providers
    await checker.check_provider("provider1", extractor)
    await checker.check_provider("provider2", extractor)

    # Clear all
    checker.clear_cache()

    assert checker.get_cached_status("provider1") is None
    assert checker.get_cached_status("provider2") is None


@pytest.mark.asyncio
async def test_clear_cache_specific_provider():
    """Test clearing specific provider cache."""
    checker = HealthChecker()
    extractor = HealthyExtractor()

    await checker.check_provider("provider1", extractor)
    await checker.check_provider("provider2", extractor)

    # Clear provider1 only
    checker.clear_cache(provider_id="provider1")

    assert checker.get_cached_status("provider1") is None
    assert checker.get_cached_status("provider2") is not None


@pytest.mark.asyncio
async def test_get_all_statuses():
    """Test getting all cached statuses."""
    checker = HealthChecker()
    healthy_extractor = HealthyExtractor()
    failing_extractor = FailingExtractor()

    await checker.check_provider("provider1", healthy_extractor)
    await checker.check_provider("provider2", failing_extractor)

    statuses = checker.get_all_statuses()

    assert len(statuses) == 2
    assert "provider1" in statuses
    assert "provider2" in statuses
    assert statuses["provider1"].healthy
    assert not statuses["provider2"].healthy


@pytest.mark.asyncio
async def test_concurrent_health_checks():
    """Test concurrent health checks."""
    checker = HealthChecker(timeout_seconds=1.0)
    extractor = HealthyExtractor()

    # Run multiple checks concurrently
    tasks = [
        checker.check_provider(f"provider{i}", extractor)
        for i in range(5)
    ]

    results = await asyncio.gather(*tasks)

    # All should succeed
    assert len(results) == 5
    assert all(r.healthy for r in results)
