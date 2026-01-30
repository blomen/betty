"""Metrics API routes."""

from fastapi import APIRouter

from ..deps import get_pipeline

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/history")
async def get_metrics_history(limit: int = 10):
    """Get historical metrics from pipeline runs."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        return {"error": "Metrics not enabled", "history": []}

    history = pipeline.metrics.get_history(limit=limit)

    return {
        "history": [run.to_dict() for run in history],
        "count": len(history)
    }


@router.get("/provider/{provider_id}")
async def get_provider_metrics(provider_id: str, limit: int = 10):
    """Get aggregate metrics for a specific provider."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        return {"error": "Metrics not enabled"}

    agg = pipeline.metrics.get_provider_aggregate(provider_id, limit=limit)

    return agg


@router.get("/current")
async def get_current_metrics():
    """Get metrics for current/latest run."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        return {"error": "Metrics not enabled"}

    current = pipeline.metrics.get_current_run()
    if current:
        return current.to_dict()

    # Get latest from history
    history = pipeline.metrics.get_history(limit=1)
    if history:
        return history[0].to_dict()

    return {"error": "No metrics available"}
