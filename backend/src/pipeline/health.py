"""
Provider Health Checking and Extraction Health Assessment.

On-demand health checks with caching to avoid redundant checks.
Extraction health: detects sharp source outage, consecutive failures,
provider staleness, DB integrity errors, opportunity volume drops.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

SHARP_STALE_MINUTES = 10
CONSECUTIVE_FAILURE_CRITICAL = 3
CONSECUTIVE_FAILURE_WARNING = 2
STALENESS_MULTIPLIER = 3
VOLUME_DROP_THRESHOLD = 0.50
VOLUME_MIN_BASELINE = 50

INTEGRITY_PATTERNS = re.compile(r"UniqueViolation|IntegrityError|duplicate key|sequence", re.IGNORECASE)


def assess_extraction_health(db, intervals: dict[str, int]) -> tuple[str, list[str]]:
    """Run 5 deep health checks on extraction state.

    Returns (status, issues) where status is "ok"/"warning"/"critical".
    """
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    issues: list[str] = []

    # ── Fetch recent provider run metrics (last hour) ──
    rows = db.execute(
        text(
            "SELECT provider_id, status, start_time, error_message "
            "FROM provider_run_metrics "
            "WHERE start_time > :since "
            "ORDER BY start_time DESC"
        ),
        {"since": one_hour_ago},
    ).fetchall()

    by_provider: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_provider[r[0]].append((r[1], r[2], r[3]))

    # ── Check 1: Sharp source down ──
    if "pinnacle" in intervals:
        pinnacle_runs = by_provider.get("pinnacle", [])
        last_pinnacle_success = None
        for status, start_time, _ in pinnacle_runs:
            if status == "success":
                last_pinnacle_success = start_time
                break

        if last_pinnacle_success is None:
            issues.append("CRITICAL: pinnacle has not completed successfully in 60+ minutes")
        else:
            age_min = (now - last_pinnacle_success.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age_min > SHARP_STALE_MINUTES:
                issues.append(f"CRITICAL: pinnacle has not completed successfully in {int(age_min)} minutes")

    # ── Check 2: Consecutive provider failures ──
    for provider_id, runs in by_provider.items():
        consecutive = 0
        last_error = ""
        for status, _, error_msg in runs:
            if status != "success":
                consecutive += 1
                if not last_error and error_msg:
                    last_error = error_msg[:100]
            else:
                break
        if consecutive >= CONSECUTIVE_FAILURE_CRITICAL:
            issues.append(
                f"CRITICAL: {provider_id} has failed {consecutive} consecutive runs"
                + (f" — {last_error}" if last_error else "")
            )
        elif consecutive >= CONSECUTIVE_FAILURE_WARNING:
            issues.append(
                f"WARNING: {provider_id} has failed {consecutive} consecutive runs"
                + (f" — {last_error}" if last_error else "")
            )

    # ── Check 3: Provider staleness ──
    for provider_id, expected_interval in intervals.items():
        if provider_id == "pinnacle":
            continue  # covered by check 1
        threshold_min = expected_interval * STALENESS_MULTIPLIER
        runs = by_provider.get(provider_id, [])
        last_success = None
        for status, start_time, _ in runs:
            if status == "success":
                last_success = start_time
                break
        if last_success is None:
            issues.append(
                f"WARNING: {provider_id} has no successful run in the last hour (threshold: {threshold_min} min)"
            )
        else:
            age_min = (now - last_success.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age_min > threshold_min:
                issues.append(
                    f"WARNING: {provider_id} is stale — last succeeded {int(age_min)} min ago "
                    f"(threshold: {threshold_min} min)"
                )

    # ── Check 4: Database integrity errors ──
    integrity_providers: set[str] = set()
    for provider_id, runs in by_provider.items():
        for _, _, error_msg in runs:
            if error_msg and INTEGRITY_PATTERNS.search(error_msg):
                integrity_providers.add(provider_id)
                break
    if integrity_providers:
        issues.append("CRITICAL: database integrity errors detected in: " + ", ".join(sorted(integrity_providers)))

    # ── Check 5: Opportunity volume drop ──
    result = db.execute(
        text(
            "SELECT "
            "COUNT(*) FILTER (WHERE detected_at > :one_h_ago) AS opp_current, "
            "COUNT(*) FILTER (WHERE detected_at > :two_h_ago AND detected_at <= :one_h_ago) AS opp_previous "
            "FROM opportunities "
            "WHERE detected_at > :two_h_ago"
        ),
        {"one_h_ago": one_hour_ago, "two_h_ago": now - timedelta(hours=2)},
    ).fetchone()
    opp_current, opp_previous = result[0], result[1]
    if opp_previous >= VOLUME_MIN_BASELINE and opp_current < opp_previous * (1 - VOLUME_DROP_THRESHOLD):
        drop_pct = int((1 - opp_current / opp_previous) * 100)
        issues.append(f"WARNING: opportunity volume dropped {drop_pct}% ({opp_previous} → {opp_current}) in last hour")

    # ── Determine overall status ──
    status = "ok"
    for issue in issues:
        if issue.startswith("CRITICAL:"):
            status = "critical"
            break
        if issue.startswith("WARNING:"):
            status = "warning"

    return status, issues
