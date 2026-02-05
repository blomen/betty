"""
Response Caching

TTL-based LRU cache for API responses.
Provides per-provider or global caching with automatic eviction.
"""

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with TTL and value."""
    value: Any
    created_at: float
    ttl_seconds: int

    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return time.time() - self.created_at > self.ttl_seconds


class ResponseCache:
    """
    TTL-based LRU cache for API responses.

    Features:
    - MD5 hash keys from URL + params
    - LRU eviction when max_entries exceeded
    - Per-provider or global cache isolation
    - Thread-safe operations
    - Hit/miss statistics
    """

    def __init__(
        self,
        default_ttl_seconds: int = 300,
        max_entries: int = 1000,
        per_provider: bool = True
    ):
        """
        Initialize response cache.

        Args:
            default_ttl_seconds: Default TTL for cached entries
            max_entries: Maximum entries before LRU eviction
            per_provider: Isolate cache per provider (True) or global (False)
        """
        self.default_ttl_seconds = default_ttl_seconds
        self.max_entries = max_entries
        self.per_provider = per_provider

        self._lock = Lock()

        # Cache structure: {provider_id: OrderedDict[key, CacheEntry]}
        # OrderedDict maintains insertion order for LRU
        self._caches: Dict[str, OrderedDict] = {}

        # Statistics
        self._hits = 0
        self._misses = 0

    def _get_cache_key(self, url: str, params: Optional[Dict] = None) -> str:
        """
        Generate cache key from URL and params.

        Args:
            url: Request URL
            params: Optional query parameters

        Returns:
            MD5 hash of URL + params
        """
        key_data = url
        if params:
            # Sort params for consistent hashing
            sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            key_data = f"{url}?{sorted_params}"

        return hashlib.md5(key_data.encode()).hexdigest()

    def _get_provider_cache(self, provider_id: Optional[str]) -> OrderedDict:
        """
        Get cache for provider (or global cache).

        Args:
            provider_id: Provider identifier (None for global)

        Returns:
            OrderedDict cache for the provider
        """
        cache_id = provider_id if self.per_provider else "global"

        if cache_id not in self._caches:
            self._caches[cache_id] = OrderedDict()

        return self._caches[cache_id]

    def get(
        self,
        url: str,
        params: Optional[Dict] = None,
        provider_id: Optional[str] = None
    ) -> Optional[Any]:
        """
        Get cached response.

        Args:
            url: Request URL
            params: Optional query parameters
            provider_id: Optional provider identifier

        Returns:
            Cached value or None if not found/expired
        """
        # Pre-compute key BEFORE lock to reduce lock contention
        key = self._get_cache_key(url, params)

        with self._lock:
            cache = self._get_provider_cache(provider_id)

            if key not in cache:
                self._misses += 1
                return None

            entry = cache[key]

            # Check if expired
            if entry.is_expired():
                # Remove expired entry
                del cache[key]
                self._misses += 1
                logger.debug(f"Cache EXPIRED: {url[:100]}")
                return None

            # Move to end (LRU: most recently used)
            cache.move_to_end(key)

            self._hits += 1
            logger.debug(f"Cache HIT: {url[:100]}")
            return entry.value

    def set(
        self,
        url: str,
        value: Any,
        params: Optional[Dict] = None,
        provider_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None
    ):
        """
        Set cached response.

        Args:
            url: Request URL
            value: Response value to cache
            params: Optional query parameters
            provider_id: Optional provider identifier
            ttl_seconds: Optional TTL override (uses default if None)
        """
        # Pre-compute key BEFORE lock to reduce lock contention
        key = self._get_cache_key(url, params)

        with self._lock:
            cache = self._get_provider_cache(provider_id)
            ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds

            # Create entry
            entry = CacheEntry(
                value=value,
                created_at=time.time(),
                ttl_seconds=ttl
            )

            # Add or update
            if key in cache:
                # Update existing (move to end)
                cache[key] = entry
                cache.move_to_end(key)
            else:
                # Add new
                cache[key] = entry

                # LRU eviction if over limit
                if len(cache) > self.max_entries:
                    # Remove oldest (first item)
                    oldest_key = next(iter(cache))
                    del cache[oldest_key]
                    logger.debug(f"Cache LRU eviction (size: {len(cache)})")

    def clear(self, provider_id: Optional[str] = None):
        """
        Clear cache.

        Args:
            provider_id: Provider to clear (None = clear all)
        """
        with self._lock:
            if provider_id is None:
                # Clear all caches
                self._caches.clear()
                logger.info("Cache cleared (all providers)")
            else:
                # Clear specific provider
                cache_id = provider_id if self.per_provider else "global"
                if cache_id in self._caches:
                    self._caches[cache_id].clear()
                    logger.info(f"Cache cleared: {provider_id}")

    def get_stats(self) -> Dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with hits, misses, hit_rate, total_entries
        """
        with self._lock:
            total_entries = sum(len(cache) for cache in self._caches.values())
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0.0

            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": hit_rate,
                "total_entries": total_entries,
                "max_entries": self.max_entries,
                "providers": len(self._caches) if self.per_provider else 1
            }

    def get_provider_stats(self, provider_id: str) -> Dict:
        """
        Get statistics for specific provider.

        Args:
            provider_id: Provider identifier

        Returns:
            Dictionary with entry count and oldest entry age
        """
        with self._lock:
            cache = self._get_provider_cache(provider_id)

            if not cache:
                return {
                    "entries": 0,
                    "oldest_age_seconds": 0
                }

            # Get oldest entry (first in OrderedDict)
            oldest_entry = next(iter(cache.values())) if cache else None
            oldest_age = time.time() - oldest_entry.created_at if oldest_entry else 0

            return {
                "entries": len(cache),
                "oldest_age_seconds": oldest_age
            }

    def evict_expired(self):
        """Manually evict all expired entries across all caches."""
        with self._lock:
            evicted_count = 0

            for cache_id, cache in self._caches.items():
                expired_keys = [
                    key for key, entry in cache.items()
                    if entry.is_expired()
                ]

                for key in expired_keys:
                    del cache[key]
                    evicted_count += 1

            if evicted_count > 0:
                logger.info(f"Evicted {evicted_count} expired cache entries")
