"""
Provider Health Checking and Extraction Health Assessment.

On-demand health checks with caching to avoid redundant checks.
Extraction health: detects sharp source outage, consecutive failures,
provider staleness, DB integrity errors, opportunity volume drops.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

import yaml
from sqlalchemy import text

from ..paths import get_config_path

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health check result."""

    healthy: bool
    response_time_ms: float
    error: str | None = None
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

    def __init__(self, timeout_seconds: float = 10.0, cache_ttl_seconds: int = 60):
        """
        Initialize health checker.

        Args:
            timeout_seconds: Timeout for health checks
            cache_ttl_seconds: Cache TTL for health results
        """
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds

        self._lock = Lock()
        self._cache: dict[str, HealthStatus] = {}

    def _get_cached_status(self, provider_id: str) -> HealthStatus | None:
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

    async def check_provider(self, provider_id: str, extractor, force: bool = False) -> HealthStatus:
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
            events = await asyncio.wait_for(extractor.extract(test_sport, limit=1), timeout=self.timeout_seconds)

            response_time_ms = (time.time() - start_time) * 1000

            # Success if we got any data (even empty list is success)
            status = HealthStatus(healthy=True, response_time_ms=response_time_ms, error=None)

            logger.info(f"[HealthCheck] {provider_id}: HEALTHY ({response_time_ms:.0f}ms, {len(events)} events)")

        except asyncio.TimeoutError:
            response_time_ms = (time.time() - start_time) * 1000
            status = HealthStatus(
                healthy=False, response_time_ms=response_time_ms, error=f"Timeout after {self.timeout_seconds}s"
            )
            logger.warning(f"[HealthCheck] {provider_id}: TIMEOUT")

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            status = HealthStatus(healthy=False, response_time_ms=response_time_ms, error=str(e))
            logger.warning(f"[HealthCheck] {provider_id}: FAILED - {e}")

        # Cache result
        with self._lock:
            self._cache[provider_id] = status

        return status

    def get_cached_status(self, provider_id: str) -> HealthStatus | None:
        """
        Get cached health status (public method).

        Args:
            provider_id: Provider identifier

        Returns:
            HealthStatus or None if not cached/expired
        """
        with self._lock:
            return self._get_cached_status(provider_id)

    def clear_cache(self, provider_id: str | None = None):
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

    def get_all_statuses(self) -> dict[str, HealthStatus]:
        """
        Get all cached health statuses.

        Returns:
            Dictionary of provider_id -> HealthStatus
        """
        with self._lock:
            return dict(self._cache)


def get_provider_intervals() -> dict[str, int]:
    """Load provider → interval_minutes mapping from providers.yaml.

    Only includes providers that are both in a scheduling tier AND in the active list.
    """
    config_path = get_config_path("providers.yaml")
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    active = set(config.get("active", []))
    tiers = config.get("extraction_scheduling", {})

    intervals: dict[str, int] = {}
    for tier_cfg in tiers.values():
        interval = tier_cfg.get("interval_minutes", 10)
        for provider in tier_cfg.get("providers", []):
            if provider in active:
                intervals[provider] = interval

    return intervals


# ── Extraction Health Assessment ─────────────────────────────────────────────

# Thresholds applied to each provider's expected interval (minutes). Multipliers
# alone don't work for fast tiers — api_soft providers run on a 2-min interval
# but the actual orchestrator cycle (extract + storage + analysis) takes 5-10
# minutes on a contended box, so 3x interval = 6m would false-warn on every
# successful run. Floor the thresholds at a realistic minimum cycle time.
WARN_MULTIPLIER = 3
CRIT_MULTIPLIER = 6
WARN_FLOOR_MINUTES = 10
CRIT_FLOOR_MINUTES = 25
# Pinnacle is the only sharp source — without it, no fair odds, no arbs. Tighter floor.
SHARP_WARN_MINUTES = 5
SHARP_CRIT_MINUTES = 15


def assess_extraction_health(db, intervals: dict[str, int]) -> tuple[str, list[str], list[dict]]:
    """Per-provider freshness check using odds.updated_at as ground truth.

    Why odds.updated_at and not extraction_runs / provider_run_metrics:
    those tables aggregate per-tier and lag wildly behind real writes — during
    the 2026-04-25 incident we observed 1-2 rows for the last hour despite
    dozens of completed runs, so the prior /health/extraction endpoint reported
    everything-stale even when fresh odds were landing every minute. The odds
    table cannot lie: a row's updated_at is set at insert/update time, so a
    fresh value proves the provider is actually producing data.

    Returns (status, issues, providers) where status is "ok"/"warning"/"critical",
    issues is the human-readable list (kept for back-compat with the existing
    response body), and providers is the structured per-provider list the UI
    consumes for the banner.
    """
    now = datetime.now(timezone.utc)
    issues: list[str] = []
    providers: list[dict] = []

    # Map each requested provider to the canonical provider whose rows actually
    # land in `odds` — non-canonical platform members (e.g. spelklubben →
    # betsson, expekt → unibet) have their odds stored under the canonical
    # provider_id by storage.store_provider_event. Reading by raw provider_id
    # would see only stale pre-consolidation rows and report 'CRITICAL stale'
    # for providers that are actually writing fresh data.
    from ..constants import PROVIDER_CANONICAL

    canonical_for: dict[str, str] = {p: PROVIDER_CANONICAL.get(p, p) for p in intervals}
    canonical_set = set(canonical_for.values())

    rows = db.execute(
        text(
            "SELECT provider_id, MAX(updated_at) AS last_update "
            "FROM odds "
            "WHERE provider_id = ANY(:provs) "
            "GROUP BY provider_id"
        ),
        {"provs": list(canonical_set)},
    ).fetchall()
    last_update_by_canonical: dict[str, datetime] = {r[0]: r[1] for r in rows}

    for provider_id, expected_interval in intervals.items():
        is_sharp = provider_id == "pinnacle"
        # Resolve via canonical so platform members share their canonical's freshness.
        last_update = last_update_by_canonical.get(canonical_for[provider_id])
        if last_update is None:
            age_min = None
        else:
            # odds.updated_at is timestamp-without-tz from PG; treat as UTC.
            age_min = (now - last_update.replace(tzinfo=timezone.utc)).total_seconds() / 60

        if is_sharp:
            warn_min, crit_min = SHARP_WARN_MINUTES, SHARP_CRIT_MINUTES
        else:
            warn_min = max(expected_interval * WARN_MULTIPLIER, WARN_FLOOR_MINUTES)
            crit_min = max(expected_interval * CRIT_MULTIPLIER, CRIT_FLOOR_MINUTES)

        if age_min is None:
            provider_status = "down"
            issues.append(f"{'CRITICAL' if is_sharp else 'WARNING'}: {provider_id} has never written odds (no rows)")
        elif age_min > crit_min:
            provider_status = "critical"
            issues.append(f"CRITICAL: {provider_id} stale {int(age_min)}m (threshold: {int(crit_min)}m)")
        elif age_min > warn_min:
            provider_status = "warning"
            issues.append(f"WARNING: {provider_id} stale {int(age_min)}m (threshold: {int(warn_min)}m)")
        else:
            provider_status = "ok"

        providers.append(
            {
                "provider_id": provider_id,
                "status": provider_status,
                "age_minutes": round(age_min, 1) if age_min is not None else None,
                "warn_minutes": warn_min,
                "crit_minutes": crit_min,
                "interval_minutes": expected_interval,
                "is_sharp": is_sharp,
            }
        )

    providers.sort(key=lambda p: (p["age_minutes"] is None, -(p["age_minutes"] or 0)))

    # Roll up to overall status: any critical → critical, any warning → warning.
    status = "ok"
    for p in providers:
        if p["status"] in ("critical", "down"):
            status = "critical"
            break
        if p["status"] == "warning":
            status = "warning"

    return status, issues, providers
