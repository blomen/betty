"""
Tests for response caching.
"""

import time
import pytest
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.pipeline.cache import ResponseCache, CacheEntry


def test_cache_entry_expiration():
    """Test CacheEntry expiration check."""
    entry = CacheEntry(value="test", created_at=time.time(), ttl_seconds=1)

    assert not entry.is_expired()

    time.sleep(1.1)
    assert entry.is_expired()


def test_cache_basic_get_set():
    """Test basic cache get/set operations."""
    cache = ResponseCache(default_ttl_seconds=60, max_entries=100)

    # Miss on empty cache
    assert cache.get("http://example.com/api") is None

    # Set value
    cache.set("http://example.com/api", {"data": [1, 2, 3]})

    # Hit on existing key
    result = cache.get("http://example.com/api")
    assert result == {"data": [1, 2, 3]}


def test_cache_with_params():
    """Test cache with query parameters."""
    cache = ResponseCache()

    # Different params = different keys
    cache.set("http://example.com/api", "result1", params={"page": 1})
    cache.set("http://example.com/api", "result2", params={"page": 2})

    assert cache.get("http://example.com/api", params={"page": 1}) == "result1"
    assert cache.get("http://example.com/api", params={"page": 2}) == "result2"

    # Params order shouldn't matter
    assert cache.get("http://example.com/api", params={"page": 1, "sort": "asc"}) is None
    cache.set("http://example.com/api", "result3", params={"sort": "asc", "page": 1})
    assert cache.get("http://example.com/api", params={"page": 1, "sort": "asc"}) == "result3"


def test_cache_ttl_expiration():
    """Test TTL expiration."""
    cache = ResponseCache(default_ttl_seconds=1)

    cache.set("http://example.com/api", "data")
    assert cache.get("http://example.com/api") == "data"

    # Wait for expiration
    time.sleep(1.1)
    assert cache.get("http://example.com/api") is None


def test_cache_custom_ttl():
    """Test custom TTL override."""
    cache = ResponseCache(default_ttl_seconds=60)

    # Use custom short TTL
    cache.set("http://example.com/api", "data", ttl_seconds=1)

    assert cache.get("http://example.com/api") == "data"

    time.sleep(1.1)
    assert cache.get("http://example.com/api") is None


def test_cache_lru_eviction():
    """Test LRU eviction when max_entries exceeded."""
    cache = ResponseCache(max_entries=3)

    # Fill cache
    cache.set("http://example.com/1", "data1")
    cache.set("http://example.com/2", "data2")
    cache.set("http://example.com/3", "data3")

    # All should be present
    assert cache.get("http://example.com/1") == "data1"
    assert cache.get("http://example.com/2") == "data2"
    assert cache.get("http://example.com/3") == "data3"

    # Add 4th item - should evict oldest (1)
    cache.set("http://example.com/4", "data4")

    assert cache.get("http://example.com/1") is None  # Evicted
    assert cache.get("http://example.com/2") == "data2"
    assert cache.get("http://example.com/3") == "data3"
    assert cache.get("http://example.com/4") == "data4"


def test_cache_lru_access_order():
    """Test LRU eviction respects access order."""
    cache = ResponseCache(max_entries=3)

    cache.set("http://example.com/1", "data1")
    cache.set("http://example.com/2", "data2")
    cache.set("http://example.com/3", "data3")

    # Access 1 (moves to end)
    cache.get("http://example.com/1")

    # Add 4th item - should evict 2 (oldest)
    cache.set("http://example.com/4", "data4")

    assert cache.get("http://example.com/1") == "data1"  # Still present
    assert cache.get("http://example.com/2") is None  # Evicted
    assert cache.get("http://example.com/3") == "data3"
    assert cache.get("http://example.com/4") == "data4"


def test_cache_per_provider_isolation():
    """Test per-provider cache isolation."""
    cache = ResponseCache(per_provider=True)

    # Same URL, different providers
    cache.set("http://example.com/api", "provider1_data", provider_id="provider1")
    cache.set("http://example.com/api", "provider2_data", provider_id="provider2")

    assert cache.get("http://example.com/api", provider_id="provider1") == "provider1_data"
    assert cache.get("http://example.com/api", provider_id="provider2") == "provider2_data"


def test_cache_global_mode():
    """Test global cache mode (no provider isolation)."""
    cache = ResponseCache(per_provider=False)

    # Same URL, different providers - should share cache
    cache.set("http://example.com/api", "data1", provider_id="provider1")

    # Get with different provider should return same data
    assert cache.get("http://example.com/api", provider_id="provider2") == "data1"


def test_cache_clear_all():
    """Test clearing all caches."""
    cache = ResponseCache(per_provider=True)

    cache.set("http://example.com/1", "data1", provider_id="provider1")
    cache.set("http://example.com/2", "data2", provider_id="provider2")

    # Clear all
    cache.clear()

    assert cache.get("http://example.com/1", provider_id="provider1") is None
    assert cache.get("http://example.com/2", provider_id="provider2") is None


def test_cache_clear_provider():
    """Test clearing specific provider cache."""
    cache = ResponseCache(per_provider=True)

    cache.set("http://example.com/1", "data1", provider_id="provider1")
    cache.set("http://example.com/2", "data2", provider_id="provider2")

    # Clear provider1 only
    cache.clear(provider_id="provider1")

    assert cache.get("http://example.com/1", provider_id="provider1") is None
    assert cache.get("http://example.com/2", provider_id="provider2") == "data2"


def test_cache_get_stats():
    """Test cache statistics."""
    cache = ResponseCache()

    # Initial stats
    stats = cache.get_stats()
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["hit_rate"] == 0.0
    assert stats["total_entries"] == 0

    # Add data and track hits/misses
    cache.set("http://example.com/1", "data1")
    cache.get("http://example.com/1")  # Hit
    cache.get("http://example.com/2")  # Miss

    stats = cache.get_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["total_entries"] == 1


def test_cache_get_provider_stats():
    """Test provider-specific statistics."""
    cache = ResponseCache(per_provider=True)

    # Add entries
    cache.set("http://example.com/1", "data1", provider_id="provider1")
    cache.set("http://example.com/2", "data2", provider_id="provider1")
    cache.set("http://example.com/3", "data3", provider_id="provider2")

    stats1 = cache.get_provider_stats("provider1")
    assert stats1["entries"] == 2

    stats2 = cache.get_provider_stats("provider2")
    assert stats2["entries"] == 1


def test_cache_evict_expired():
    """Test manual eviction of expired entries."""
    cache = ResponseCache(default_ttl_seconds=1)

    # Add entries
    cache.set("http://example.com/1", "data1")
    cache.set("http://example.com/2", "data2", ttl_seconds=60)  # Won't expire

    # Wait for first to expire
    time.sleep(1.1)

    # Manual eviction
    cache.evict_expired()

    # Check stats
    stats = cache.get_stats()
    assert stats["total_entries"] == 1  # Only non-expired remains


def test_cache_update_existing():
    """Test updating existing cache entry."""
    cache = ResponseCache()

    cache.set("http://example.com/api", "data1")
    assert cache.get("http://example.com/api") == "data1"

    # Update with new value
    cache.set("http://example.com/api", "data2")
    assert cache.get("http://example.com/api") == "data2"


def test_cache_thread_safety():
    """Test cache thread safety (basic check)."""
    import threading

    cache = ResponseCache()

    def writer(n):
        for i in range(10):
            cache.set(f"http://example.com/{n}/{i}", f"data{n}_{i}")

    def reader(n):
        for i in range(10):
            cache.get(f"http://example.com/{n}/{i}")

    threads = []
    for i in range(5):
        threads.append(threading.Thread(target=writer, args=(i,)))
        threads.append(threading.Thread(target=reader, args=(i,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Should not crash and have some entries
    stats = cache.get_stats()
    assert stats["total_entries"] > 0
