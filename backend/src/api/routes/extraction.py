"""Extraction API routes."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from sse_starlette.sse import EventSourceResponse

from ...pipeline.broadcast import odds_broadcaster
from ..state import (
    extraction_state,
    update_extraction_state,
    get_extraction_state,
    get_provider_states,
)
from ...db.models import Event, Odds, get_session

router = APIRouter(prefix="/api/extraction", tags=["extraction"])
logger = logging.getLogger(__name__)



def _build_final_state(results: dict) -> dict:
    """Build final provider state dict from pipeline results for UI.

    Uses actual DB counts per provider so per-provider numbers
    sum to the totals (no double-counting from cross-provider matching).
    """
    from sqlalchemy import func

    # Query actual per-provider counts from DB
    db = get_session()
    try:
        provider_counts = {
            row[0]: {"events": row[1], "odds": row[2]}
            for row in db.query(
                Odds.provider_id,
                func.count(func.distinct(Odds.event_id)),
                func.count(Odds.id),
            ).group_by(Odds.provider_id).all()
        }
    finally:
        db.close()

    final_providers = {}
    completed = 0

    # Add Polymarket
    poly = results.get("polymarket", {})
    if poly.get("events_processed", 0) > 0:
        db_counts = provider_counts.get("polymarket", {"events": 0, "odds": 0})
        final_providers["polymarket"] = {
            "status": "completed",
            "events": db_counts["events"],
            "odds": db_counts["odds"],
            "duration_seconds": 0,
            "error": None,
            "sports_completed": 0,
            "sports_total": 0,
        }
        completed += 1

    # Add other providers
    for pid, presult in results.get("providers", {}).items():
        has_error = "error" in presult and isinstance(presult["error"], str)
        db_counts = provider_counts.get(pid, {"events": 0, "odds": 0})
        final_providers[pid] = {
            "status": "failed" if has_error else "completed",
            "events": db_counts["events"],
            "odds": db_counts["odds"],
            "duration_seconds": 0,
            "error": presult.get("error") if has_error else None,
            "sports_completed": presult.get("sports_succeeded", 0),
            "sports_total": presult.get("sports_attempted", 0),
        }
        if not has_error:
            completed += 1

    return {
        "providers": final_providers,
        "completed_providers": completed,
        "total_providers": len(final_providers),
    }


async def run_extraction_task(providers: list[str] | None):
    """Background task to run extraction from ALL sports.

    Polymarket is only extracted when explicitly included in providers list:
    - providers=["pinnacle", "leovegas"] -> Only bookmakers
    - providers=["polymarket"] -> Only Polymarket
    - providers=["polymarket", "pinnacle"] -> Both
    """
    from ..deps import get_pipeline

    pipeline = get_pipeline()
    provider_list = providers if providers else pipeline.engine.get_enabled_providers()

    # Count includes polymarket only if explicitly requested
    includes_polymarket = providers and "polymarket" in providers
    total_providers = len(provider_list)
    if includes_polymarket:
        total_providers = len(provider_list)  # polymarket counted as one provider

    # Initialize state
    update_extraction_state(
        running=True,
        start_time=datetime.now(timezone.utc),
        total_events=0,
        total_odds=0,
        providers={},
        current_provider=None,
        completed_providers=0,
        total_providers=total_providers,
    )

    _results = None
    try:
        _results = await pipeline.run(
            providers=provider_list if provider_list else None,
        )
    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)
        update_extraction_state(error=str(e))

    finally:
        # Final state update in finally block (guaranteed to run)
        if _results:
            try:
                final = _build_final_state(_results)
                update_extraction_state(
                    total_events=_results.get("total_events", 0),
                    total_odds=_results.get("total_odds", 0),
                    providers=final["providers"],
                    completed_providers=final["completed_providers"],
                    total_providers=final["total_providers"],
                    current_provider=None,
                    last_run=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                pass
        # Compute final elapsed time before clearing running flag
        start = extraction_state.get("start_time")
        if start:
            final_elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            update_extraction_state(elapsed_seconds=final_elapsed)
        update_extraction_state(running=False)


@router.get("/status")
async def get_extraction_status():
    """Get extraction status (legacy endpoint)."""
    state = get_extraction_state()
    return {
        "running": state["running"],
        "last_run": state["last_run"],
        "events": state["total_events"],
        "odds": state["total_odds"],
    }


@router.get("/progress")
async def get_extraction_progress():
    """Per-provider extraction status."""
    states = get_provider_states()
    return {
        "providers": states,
        "any_running": any(s.get("running", False) for s in states.values()),
    }


@router.post("/run")
async def run_extraction(
    background_tasks: BackgroundTasks,
    providers: str | None = None,  # Optional: "unibet,leovegas,polymarket" or None for all bookmakers
):
    """
    Trigger extraction from all configured sports and providers.

    Polymarket is a separate source and only extracted when explicitly requested:
    - providers=pinnacle,leovegas -> Only bookmakers
    - providers=polymarket -> Only Polymarket
    - providers=polymarket,pinnacle,leovegas -> All three

    Without providers parameter, extracts from all enabled bookmakers (not Polymarket).
    """
    if extraction_state["running"]:
        raise HTTPException(400, "Extraction already running")

    provider_list = [p.strip() for p in providers.split(",")] if providers else None
    background_tasks.add_task(run_extraction_task, provider_list)

    return {
        "status": "started",
        "providers": provider_list or "all",
    }


# SSE stream endpoint
@router.get("/stream")
async def extraction_stream(request: Request):
    """SSE endpoint streaming real-time odds and opportunity updates."""
    client_id, queue = odds_broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {
                        "event": msg["event"],
                        "data": json.dumps(msg["data"]),
                    }
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            odds_broadcaster.unsubscribe(client_id)

    return EventSourceResponse(event_generator(), ping=15)


# =============================================================================
# Continuous Extraction Endpoints
# =============================================================================

@router.post("/continuous/start")
async def start_continuous_extraction(
    interval_seconds: int = 300,
    providers: str = None,
):
    """
    Start continuous extraction loop for Polymarket + Pinnacle.

    Args:
        interval_seconds: Seconds between runs (default: 300 = 5 min)
        providers: Optional comma-separated providers (default: polymarket,pinnacle)

    Returns:
        Status and scheduler info
    """
    from ...pipeline.scheduler import get_scheduler

    scheduler = get_scheduler()

    if scheduler.running:
        raise HTTPException(400, "Continuous extraction already running")

    provider_list = (
        [p.strip() for p in providers.split(",")]
        if providers
        else ["polymarket", "pinnacle"]
    )

    # Start the scheduler in the current event loop
    # The scheduler creates its own task internally
    asyncio.create_task(
        scheduler.start_continuous(
            providers=provider_list,
            interval_seconds=interval_seconds,
        )
    )

    return {
        "status": "started",
        "providers": provider_list,
        "interval_seconds": interval_seconds,
    }


@router.post("/continuous/stop")
async def stop_continuous_extraction():
    """
    Stop continuous extraction loop.

    Returns:
        Status and stats from the run
    """
    from ...pipeline.scheduler import get_scheduler

    scheduler = get_scheduler()

    if not scheduler.running:
        raise HTTPException(400, "Continuous extraction not running")

    scheduler.stop()

    return {
        "status": "stopped",
        "run_count": scheduler.run_count,
        "last_run": scheduler.last_run.isoformat() if scheduler.last_run else None,
    }


@router.get("/continuous/status")
async def get_continuous_status():
    """
    Get status of continuous extraction scheduler.

    Returns:
        Running state, run count, last run timestamp
    """
    from ...pipeline.scheduler import get_scheduler

    scheduler = get_scheduler()
    return scheduler.get_status()


@router.post("/soft")
async def run_soft_extraction(
    background_tasks: BackgroundTasks,
    tier: str = "all",
):
    """
    Run manual soft book extraction.

    Args:
        tier: Which tier to run:
            - "all": All soft providers (default)
            - "api": API-based soft providers (Kambi, Altenar, Gecko V2, Vbet)
            - "browser": Browser-based soft providers (Spectate, ComeOn, etc.)
            - "kambi": Only Kambi providers (8)
            - "altenar": Only Altenar providers (6)
            - "spectate": Only Spectate providers (2)
            - "gecko": Only Gecko V2 providers (4)
            - "comeon": Only ComeOn group (3)
            - Or comma-separated provider names

    Returns:
        Status and provider list
    """
    if extraction_state["running"]:
        raise HTTPException(400, "Extraction already running")

    # Define tier mappings
    tier_providers = {
        "kambi": ["unibet", "leovegas", "betmgm", "speedybet", "x3000", "goldenbull", "1x2"],
        "altenar": ["betinia", "campobet", "swiper", "lodur", "dbet", "quickcasino"],
        "gecko": ["betsson", "nordicbet", "spelklubben", "bethard"],
        "spectate": ["mrgreen", "888sport"],
        "comeon": ["comeon", "hajper", "lyllo"],
        "snabbare": ["snabbare"],
        "vbet": ["vbet"],
        "10bet": ["10bet"],
        "interwetten": ["interwetten"],
        "coolbet": ["coolbet"],
        "tipwin": ["tipwin"],
    }

    # Composite tiers
    api_providers = (
        tier_providers["kambi"] + tier_providers["altenar"] +
        tier_providers["gecko"] + tier_providers["vbet"]
    )
    browser_providers = (
        tier_providers["spectate"] + tier_providers["comeon"] +
        tier_providers["snabbare"] + tier_providers["10bet"] +
        tier_providers["interwetten"] + tier_providers["coolbet"] +
        tier_providers["tipwin"]
    )

    # Resolve tier to provider list
    if tier == "all":
        provider_list = api_providers + browser_providers
    elif tier == "api":
        provider_list = api_providers
    elif tier == "browser":
        provider_list = browser_providers
    elif tier in tier_providers:
        provider_list = tier_providers[tier]
    elif "," in tier:
        provider_list = [p.strip() for p in tier.split(",")]
    else:
        raise HTTPException(
            400,
            f"Unknown tier: {tier}. Use: all, api, browser, kambi, altenar, "
            f"gecko, spectate, comeon, snabbare, vbet, 10bet, interwetten, "
            f"coolbet, tipwin, or comma-separated providers"
        )

    background_tasks.add_task(run_extraction_task, provider_list)

    return {
        "status": "started",
        "tier": tier,
        "providers": provider_list,
    }


# =============================================================================
# Tier Control Endpoints
# =============================================================================

@router.post("/tier/{tier_name}/start")
async def start_tier(tier_name: str):
    """Start a specific extraction tier/category.

    Reads config from providers.yaml extraction_scheduling section.
    Valid tier names: sharp, api_soft, browser_soft
    """
    from ...pipeline.scheduler import get_scheduler, ProviderSchedule

    scheduler = get_scheduler()

    # Load from YAML config (single source of truth)
    scheduling = scheduler._load_scheduling_config()
    if tier_name not in scheduling:
        raise HTTPException(400, f"Unknown tier: {tier_name}. Use: {', '.join(scheduling.keys())}")

    category_config = scheduling[tier_name]
    all_providers = category_config.get("providers", [])
    interval_minutes = category_config.get("interval_minutes", 60)
    interval_seconds = interval_minutes * 60
    grouped = category_config.get("grouped", False)

    # Filter out providers disabled in settings for active profile
    from ...db.models import Profile, ProviderExtractionSetting, get_session
    session = get_session()
    try:
        profile = session.query(Profile).filter(
            Profile.is_active == True  # noqa: E712
        ).first()
        disabled = set()
        if profile:
            disabled = {
                s.provider_id
                for s in session.query(ProviderExtractionSetting).filter(
                    ProviderExtractionSetting.profile_id == profile.id,
                    ProviderExtractionSetting.enabled == False,  # noqa: E712
                ).all()
            }
    finally:
        session.close()
    providers = [p for p in all_providers if p not in disabled]

    if grouped:
        schedule = ProviderSchedule(
            provider_id=tier_name,
            category=tier_name,
            interval_seconds=interval_seconds,
            providers=providers,
        )
        await scheduler._start_schedule(schedule)
    else:
        for provider_id in providers:
            schedule = ProviderSchedule(
                provider_id=provider_id,
                category=tier_name,
                interval_seconds=interval_seconds,
            )
            await scheduler._start_schedule(schedule)

    return {
        "status": "started",
        "tier": tier_name,
        "providers": providers,
        "interval_seconds": interval_seconds,
    }


@router.post("/tier/{tier_name}/stop")
async def stop_tier(tier_name: str):
    """Stop a specific extraction tier."""
    from ...pipeline.scheduler import get_scheduler

    scheduler = get_scheduler()
    scheduler.stop_tier(tier_name)

    return {"status": "stopped", "tier": tier_name}


@router.get("/freshness")
async def get_extraction_freshness():
    """Get the most recent odds update time per extraction tier (soft/sharp/poly/boosts)."""
    from sqlalchemy import func, case
    from ...db.models import BoostExtractionLog

    session = get_session()
    try:
        rows = (
            session.query(
                case(
                    (Odds.provider_id == "pinnacle", "sharp"),
                    (Odds.provider_id == "polymarket", "poly"),
                    else_="soft",
                ).label("tier"),
                func.max(Odds.updated_at).label("latest"),
            )
            .group_by("tier")
            .all()
        )
        # Append 'Z' to indicate UTC — naive isoformat() is interpreted as local time by JS
        result = {row.tier: row.latest.isoformat() + "Z" if row.latest else None for row in rows}

        # Boost freshness — DISABLED (boosts/specials turned off)
        return {
            "soft": result.get("soft"),
            "sharp": result.get("sharp"),
            "poly": result.get("poly"),
            "boosts": None,
        }
    finally:
        session.close()


# =============================================================================
# Analytics + Recommendations Endpoints
# =============================================================================

@router.get("/analytics")
async def get_extraction_analytics():
    """Get extraction analytics: provider ROI, coverage gaps, scheduling efficiency."""
    from src.ml.analytics.engine import compute_provider_roi, compute_coverage_gaps, compute_scheduling_efficiency

    session = get_session()
    try:
        return {
            "provider_roi": compute_provider_roi(session),
            "coverage_gaps": compute_coverage_gaps(session),
            "scheduling": compute_scheduling_efficiency(session),
        }
    finally:
        session.close()


@router.get("/recommendations")
async def get_extraction_recommendations():
    """Get active extraction recommendations."""
    from src.ml.analytics.recommendations import RecommendationManager

    session = get_session()
    try:
        mgr = RecommendationManager(session)
        active = mgr.get_active()
        return [
            {
                "id": r.id,
                "provider_id": r.provider_id,
                "category": r.category,
                "severity": r.severity,
                "message": r.message,
                "status": r.status,
                "before_metric": r.before_metric,
                "after_metric": r.after_metric,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in active
        ]
    finally:
        session.close()


@router.get("/ml/status")
async def get_ml_status():
    """Get status of all ML models."""
    from src.ml.serving.predictor import get_predictor
    from src.ml.training.train_all import TrainingOrchestrator

    session = get_session()
    try:
        predictor = get_predictor()
        orch = TrainingOrchestrator()
        thresholds = orch.check_thresholds(session)

        result = {}
        for name, config in orch.model_configs.items():
            result[name] = {
                "loaded": predictor.is_loaded(name),
                "data_ready": thresholds.get(name, False),
                "min_samples": config["min_samples"],
            }
        return result
    finally:
        session.close()


@router.post("/ml/train")
async def trigger_ml_training():
    """Manually trigger ML model training."""
    from src.ml.training.train_all import TrainingOrchestrator

    session = get_session()
    try:
        orch = TrainingOrchestrator()
        results = orch.train_all(session)
        session.commit()
        return results
    finally:
        session.close()


@router.get("/optimizer/status")
async def get_optimizer_status():
    """Return latest M10 optimizer analysis results."""
    from src.ml.optimizer.schedule import ScheduleOptimizer
    from src.ml.optimizer.provider_priority import ProviderPriorityScorer
    from src.ml.optimizer.timeout import TimeoutTuner
    from src.ml.optimizer.coverage import CoverageOptimizer

    session = get_session()
    try:
        results = {}
        for name, cls in [
            ("schedule", ScheduleOptimizer),
            ("provider_priority", ProviderPriorityScorer),
            ("timeout", TimeoutTuner),
            ("coverage", CoverageOptimizer),
        ]:
            try:
                result = cls().check_and_train(session) or {"status": "insufficient_data"}
                # Remove non-serializable model objects
                result.pop("model", None)
                results[name] = result
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}
        return results
    finally:
        session.close()


@router.patch("/recommendations/{rec_id}")
async def update_recommendation(rec_id: int, status: str, after_metric: float = None):
    """Update recommendation status (acted_on, resolved, wont_fix)."""
    from src.ml.analytics.recommendations import RecommendationManager

    if status not in ("acted_on", "resolved", "wont_fix"):
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    session = get_session()
    try:
        mgr = RecommendationManager(session)
        rec = mgr.update_status(rec_id, status, after_metric=after_metric)
        session.commit()
        if not rec:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        return {"id": rec.id, "status": rec.status}
    finally:
        session.close()
