"""Monitoring API routes (circuit breaker, cache, health checks, provider monitor)."""

from typing import Optional
from fastapi import APIRouter, HTTPException

from ..deps import get_pipeline

router = APIRouter(tags=["monitoring"])


# ============ Circuit Breaker ============

@router.get("/api/circuit-breaker/status")
async def get_circuit_breaker_status():
    """Get circuit breaker status for all providers."""
    pipeline = get_pipeline()

    if not pipeline.circuit_breaker:
        return {"error": "Circuit breaker not enabled", "statuses": {}}

    statuses = pipeline.circuit_breaker.get_all_statuses()

    return {
        "statuses": {
            pid: {
                "state": status.state.value,
                "failure_count": status.failure_count,
                "success_count": status.success_count,
                "last_failure_time": status.last_failure_time,
                "last_success_time": status.last_success_time,
                "opened_at": status.opened_at,
            }
            for pid, status in statuses.items()
        }
    }


@router.get("/api/circuit-breaker/status/{provider_id}")
async def get_provider_circuit_breaker_status(provider_id: str):
    """Get circuit breaker status for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.circuit_breaker:
        return {"error": "Circuit breaker not enabled"}

    status = pipeline.circuit_breaker.get_status(provider_id)

    return {
        "provider_id": provider_id,
        "state": status.state.value,
        "failure_count": status.failure_count,
        "success_count": status.success_count,
        "last_failure_time": status.last_failure_time,
        "last_success_time": status.last_success_time,
        "opened_at": status.opened_at,
    }


@router.post("/api/circuit-breaker/reset/{provider_id}")
async def reset_circuit_breaker(provider_id: str):
    """Manually reset circuit breaker for provider."""
    pipeline = get_pipeline()

    if not pipeline.circuit_breaker:
        raise HTTPException(400, "Circuit breaker not enabled")

    pipeline.circuit_breaker.reset(provider_id)

    return {
        "success": True,
        "provider_id": provider_id,
        "message": "Circuit breaker reset to CLOSED"
    }


# ============ Cache ============

@router.get("/api/cache/stats")
async def get_cache_stats():
    """Get cache statistics."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        return {"error": "Cache not enabled"}

    stats = pipeline.cache.get_stats()

    return stats


@router.get("/api/cache/stats/{provider_id}")
async def get_provider_cache_stats(provider_id: str):
    """Get cache statistics for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        return {"error": "Cache not enabled"}

    stats = pipeline.cache.get_provider_stats(provider_id)

    return {
        "provider_id": provider_id,
        **stats
    }


@router.post("/api/cache/clear")
async def clear_cache(provider_id: Optional[str] = None):
    """Clear cache (all or specific provider)."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        raise HTTPException(400, "Cache not enabled")

    pipeline.cache.clear(provider_id=provider_id)

    return {
        "success": True,
        "message": f"Cache cleared{' for ' + provider_id if provider_id else ' (all providers)'}"
    }


@router.post("/api/cache/evict-expired")
async def evict_expired_cache():
    """Manually evict expired cache entries."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        raise HTTPException(400, "Cache not enabled")

    pipeline.cache.evict_expired()

    return {
        "success": True,
        "message": "Expired cache entries evicted"
    }


# ============ Health Checks ============

@router.get("/api/health-check/status")
async def get_health_check_status():
    """Get cached health check status for all providers."""
    pipeline = get_pipeline()

    if not pipeline.health_checker:
        return {"error": "Health checker not enabled", "statuses": {}}

    statuses = pipeline.health_checker.get_all_statuses()

    return {
        "statuses": {
            pid: {
                "healthy": status.healthy,
                "response_time_ms": status.response_time_ms,
                "error": status.error,
                "checked_at": status.checked_at,
            }
            for pid, status in statuses.items()
        }
    }


@router.post("/api/health-check/run/{provider_id}")
async def run_health_check(provider_id: str, force: bool = False):
    """Run health check for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.health_checker:
        raise HTTPException(400, "Health checker not enabled")

    # Get extractor
    extractor = pipeline.engine.get_extractor(provider_id)
    if not extractor:
        raise HTTPException(404, f"Provider {provider_id} not found")

    # Run check
    status = await pipeline.health_checker.check_provider(
        provider_id, extractor, force=force
    )

    return {
        "provider_id": provider_id,
        "healthy": status.healthy,
        "response_time_ms": status.response_time_ms,
        "error": status.error,
        "checked_at": status.checked_at,
    }


@router.post("/api/health-check/clear-cache")
async def clear_health_check_cache(provider_id: Optional[str] = None):
    """Clear health check cache."""
    pipeline = get_pipeline()

    if not pipeline.health_checker:
        raise HTTPException(400, "Health checker not enabled")

    pipeline.health_checker.clear_cache(provider_id=provider_id)

    return {
        "success": True,
        "message": f"Health check cache cleared{' for ' + provider_id if provider_id else ' (all)'}"
    }


# ============ Provider Monitoring ============

@router.get("/api/monitor/providers")
async def monitor_all_providers(limit: int = 20):
    """Get health assessment for all providers."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    # Get metrics history
    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        return {"error": "No metrics history available", "providers": {}}

    # Get circuit breaker and health check statuses
    cb_statuses = {}
    if pipeline.circuit_breaker:
        statuses = pipeline.circuit_breaker.get_all_statuses()
        cb_statuses = {
            pid: {
                "state": status.state.value,
                "failure_count": status.failure_count,
                "success_count": status.success_count,
            }
            for pid, status in statuses.items()
        }

    hc_statuses = {}
    if pipeline.health_checker:
        statuses = pipeline.health_checker.get_all_statuses()
        hc_statuses = {
            pid: {
                "healthy": status.healthy,
                "error": status.error,
            }
            for pid, status in statuses.items()
        }

    # Assess providers
    from ...pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()
    assessments = monitor.assess_all_providers(history, cb_statuses, hc_statuses)

    return {
        "providers": {
            pid: {
                "health_score": health.health_score.value,
                "score_value": health.score_value,
                "is_healthy": health.is_healthy,
                "has_critical_issues": health.has_critical_issues,
                "avg_events_per_run": health.avg_events_per_run,
                "avg_response_time_ms": health.avg_response_time_ms,
                "success_rate": health.success_rate,
                "trend_direction": health.trend_direction,
                "issues": [
                    {
                        "type": issue.issue_type.value,
                        "severity": issue.severity,
                        "message": issue.message,
                        "metric_value": issue.metric_value,
                    }
                    for issue in health.issues
                ],
            }
            for pid, health in assessments.items()
        },
        "summary": {
            "total_providers": len(assessments),
            "healthy": sum(1 for h in assessments.values() if h.is_healthy),
            "unhealthy": sum(1 for h in assessments.values() if not h.is_healthy),
            "critical": sum(1 for h in assessments.values() if h.has_critical_issues),
        }
    }


@router.get("/api/monitor/providers/{provider_id}")
async def monitor_provider(provider_id: str, limit: int = 20):
    """Get detailed health assessment for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        raise HTTPException(404, "No metrics history available")

    # Get statuses
    cb_status = None
    if pipeline.circuit_breaker:
        status = pipeline.circuit_breaker.get_status(provider_id)
        cb_status = {
            "state": status.state.value,
            "failure_count": status.failure_count,
            "success_count": status.success_count,
        }

    hc_status = None
    if pipeline.health_checker:
        status = pipeline.health_checker.get_cached_status(provider_id)
        if status:
            hc_status = {
                "healthy": status.healthy,
                "error": status.error,
                "response_time_ms": status.response_time_ms,
            }

    # Assess provider
    from ...pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()
    health = monitor.assess_provider(provider_id, history, cb_status, hc_status)

    return {
        "provider_id": provider_id,
        "health_score": health.health_score.value,
        "score_value": health.score_value,
        "is_healthy": health.is_healthy,
        "has_critical_issues": health.has_critical_issues,
        "metrics": {
            "avg_events_per_run": health.avg_events_per_run,
            "avg_response_time_ms": health.avg_response_time_ms,
            "success_rate": health.success_rate,
            "uptime_pct": health.uptime_pct,
            "avg_odds_per_event": health.avg_odds_per_event,
        },
        "trend": {
            "direction": health.trend_direction,
            "is_degrading": health.is_degrading,
        },
        "issues": [
            {
                "type": issue.issue_type.value,
                "severity": issue.severity,
                "message": issue.message,
                "metric_value": issue.metric_value,
                "threshold_value": issue.threshold_value,
                "detected_at": issue.detected_at,
            }
            for issue in health.issues
        ],
        "assessed_at": health.assessed_at,
    }


@router.get("/api/monitor/unhealthy")
async def get_unhealthy_providers(limit: int = 20):
    """Get list of unhealthy providers."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        return {"unhealthy_providers": [], "count": 0}

    # Assess all providers
    from ...pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()

    cb_statuses = {}
    if pipeline.circuit_breaker:
        statuses = pipeline.circuit_breaker.get_all_statuses()
        cb_statuses = {
            pid: {"state": s.state.value, "failure_count": s.failure_count}
            for pid, s in statuses.items()
        }

    assessments = monitor.assess_all_providers(history, cb_statuses)
    unhealthy = monitor.get_unhealthy_providers(assessments)

    return {
        "unhealthy_providers": [
            {
                "provider_id": pid,
                "health_score": assessments[pid].health_score.value,
                "score_value": assessments[pid].score_value,
                "issue_count": len(assessments[pid].issues),
                "critical_issues": sum(1 for i in assessments[pid].issues if i.severity == "critical"),
            }
            for pid in unhealthy
        ],
        "count": len(unhealthy)
    }


@router.get("/api/monitor/critical")
async def get_critical_providers(limit: int = 20):
    """Get list of providers with critical issues."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        return {"critical_providers": [], "count": 0}

    # Assess all providers
    from ...pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()

    cb_statuses = {}
    if pipeline.circuit_breaker:
        statuses = pipeline.circuit_breaker.get_all_statuses()
        cb_statuses = {
            pid: {"state": s.state.value, "failure_count": s.failure_count}
            for pid, s in statuses.items()
        }

    assessments = monitor.assess_all_providers(history, cb_statuses)
    critical = monitor.get_critical_providers(assessments)

    return {
        "critical_providers": [
            {
                "provider_id": pid,
                "health_score": assessments[pid].health_score.value,
                "score_value": assessments[pid].score_value,
                "critical_issues": [
                    {
                        "type": i.issue_type.value,
                        "message": i.message,
                    }
                    for i in assessments[pid].issues
                    if i.severity == "critical"
                ],
            }
            for pid in critical
        ],
        "count": len(critical)
    }
