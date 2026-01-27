"""
Provider Health Checking

On-demand health checks with caching to avoid redundant checks.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health check result."""
    healthy: bool
    response_time_ms: float
    error: Optional[str] = None
    checked_at: float = None

    def __post_init__(self):
        if self.checked_at is None:
            self.checked_at = time.time()


class HealthChecker:
    """
    Provider health checker with caching.

    Strategy:
    - Test extraction with minimal data (limit=1)
    - 60s cache to avoid redundant checks
    - Configurable timeout per check
    """

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 60
    ):
        """
        Initialize health checker.

        Args:
            timeout_seconds: Timeout for health checks
            cache_ttl_seconds: Cache TTL for health results
        """
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds

        self._lock = Lock()
        self._cache: Dict[str, HealthStatus] = {}

    def _get_cached_status(self, provider_id: str) -> Optional[HealthStatus]:
        """
        Get cached health status if still valid.

        Args:
            provider_id: Provider identifier

        Returns:
            HealthStatus or None if cache miss/expired
        """
        if provider_id not in self._cache:
            return None

        status = self._cache[provider_id]
        age = time.time() - status.checked_at

        if age > self.cache_ttl_seconds:
            # Expired
            del self._cache[provider_id]
            return None

        return status

    async def check_provider(
        self,
        provider_id: str,
        extractor,
        force: bool = False
    ) -> HealthStatus:
        """
        Check provider health.

        Args:
            provider_id: Provider identifier
            extractor: Extractor instance to test
            force: Force check even if cached

        Returns:
            HealthStatus with result
        """
        # Check cache first (unless forced)
        if not force:
            with self._lock:
                cached = self._get_cached_status(provider_id)
                if cached:
                    logger.debug(f"[HealthCheck] {provider_id}: Using cached result")
                    return cached

        # Perform health check
        start_time = time.time()

        try:
            # Test with minimal extraction (limit=1)
            # Use first available sport
            test_sport = "football"  # Most providers support football

            # Run with timeout
            events = await asyncio.wait_for(
                extractor.extract(test_sport, limit=1),
                timeout=self.timeout_seconds
            )

            response_time_ms = (time.time() - start_time) * 1000

            # Success if we got any data (even empty list is success)
            status = HealthStatus(
                healthy=True,
                response_time_ms=response_time_ms,
                error=None
            )

            logger.info(
                f"[HealthCheck] {provider_id}: HEALTHY "
                f"({response_time_ms:.0f}ms, {len(events)} events)"
            )

        except asyncio.TimeoutError:
            response_time_ms = (time.time() - start_time) * 1000
            status = HealthStatus(
                healthy=False,
                response_time_ms=response_time_ms,
                error=f"Timeout after {self.timeout_seconds}s"
            )
            logger.warning(f"[HealthCheck] {provider_id}: TIMEOUT")

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            status = HealthStatus(
                healthy=False,
                response_time_ms=response_time_ms,
                error=str(e)
            )
            logger.warning(f"[HealthCheck] {provider_id}: FAILED - {e}")

        # Cache result
        with self._lock:
            self._cache[provider_id] = status

        return status

    def get_cached_status(self, provider_id: str) -> Optional[HealthStatus]:
        """
        Get cached health status (public method).

        Args:
            provider_id: Provider identifier

        Returns:
            HealthStatus or None if not cached/expired
        """
        with self._lock:
            return self._get_cached_status(provider_id)

    def clear_cache(self, provider_id: Optional[str] = None):
        """
        Clear health check cache.

        Args:
            provider_id: Provider to clear (None = clear all)
        """
        with self._lock:
            if provider_id is None:
                self._cache.clear()
                logger.info("Health check cache cleared (all)")
            else:
                if provider_id in self._cache:
                    del self._cache[provider_id]
                    logger.info(f"Health check cache cleared: {provider_id}")

    def get_all_statuses(self) -> Dict[str, HealthStatus]:
        """
        Get all cached health statuses.

        Returns:
            Dictionary of provider_id -> HealthStatus
        """
        with self._lock:
            return dict(self._cache)
