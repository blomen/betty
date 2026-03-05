"""Extraction API routes."""

import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect

from ..state import (
    extraction_state,
    update_extraction_state,
    get_extraction_state,
    get_tier_states,
    ws_manager,
)
from ...db.models import Event, Odds, get_session

router = APIRouter(prefix="/api/extraction", tags=["extraction"])
logger = logging.getLogger(__name__)


async def poll_metrics_and_update_state(pipeline, stop_event):
    """
    Polls metrics every 500ms and updates extraction_state.
    Runs in background while extraction is active.

    Uses actual DB counts for both totals and per-provider numbers
    so they are always consistent.
    """
    from sqlalchemy import func

    while not stop_event.is_set():
        if not pipeline.metrics:
            await asyncio.sleep(0.5)
            continue

        current_run = pipeline.metrics.get_current_run()
        if not current_run:
            await asyncio.sleep(0.5)
            continue

        # Build provider states from metrics (status, duration, errors)
        providers_state = {}
        completed_count = 0
        current_provider = None

        for pid, pm in current_run.providers.items():
            status = "pending"
            if pm.is_complete:
                status = "completed" if pm.success else "failed"
                completed_count += 1
            elif pm.start_time and not pm.is_complete:
                status = "running"
                current_provider = pid

            providers_state[pid] = {
                "status": status,
                "events": pm.total_events,
                "odds": pm.total_odds,
                "duration_seconds": pm.duration_seconds,
                "error": pm.error,
                "sports_completed": pm.sports_succeeded,
                "sports_total": pm.sports_attempted,
            }

        # Query DB for actual unique counts (totals + per-provider)
        db = get_session()
        try:
            total_events = db.query(Event).count()
            total_odds = db.query(Odds).count()

            # Override per-provider counts with actual DB numbers
            # for completed providers (running ones keep live metrics)
            provider_counts = {
                row[0]: {"events": row[1], "odds": row[2]}
                for row in db.query(
                    Odds.provider_id,
                    func.count(func.distinct(Odds.event_id)),
                    func.count(Odds.id),
                ).group_by(Odds.provider_id).all()
            }
            for pid, counts in provider_counts.items():
                if pid in providers_state and providers_state[pid]["status"] == "completed":
                    providers_state[pid]["events"] = counts["events"]
                    providers_state[pid]["odds"] = counts["odds"]
        finally:
            db.close()

        # Update global state
        update_extraction_state(
            total_events=total_events,
            total_odds=total_odds,
            providers=providers_state,
            current_provider=current_provider,
            completed_providers=completed_count,
        )

        await asyncio.sleep(0.5)


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
        # Start metrics polling task
        stop_event = asyncio.Event()
        polling_task = asyncio.create_task(
            poll_metrics_and_update_state(pipeline, stop_event)
        )

        try:
            _results = await pipeline.run(
                providers=provider_list if provider_list else None,
            )
        finally:
            stop_event.set()
            try:
                await polling_task
            except Exception:
                pass

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
    """
    Get detailed extraction progress with provider breakdown.
    Returns real-time progress during extraction.
    """
    state = get_extraction_state()

    # Calculate elapsed time
    if state["running"] and state["start_time"]:
        elapsed_seconds = (datetime.now(timezone.utc) - state["start_time"]).total_seconds()
    else:
        elapsed_seconds = state.get("elapsed_seconds", 0)

    # Calculate progress percentage from sport-level granularity
    progress_pct = 0
    providers_dict = state["providers"]
    if providers_dict:
        total_sports_all = 0
        completed_sports_all = 0
        for prov in providers_dict.values():
            total_sports_all += prov.get("sports_total", 0)
            completed_sports_all += prov.get("sports_completed", 0)
        if total_sports_all > 0:
            progress_pct = (completed_sports_all / total_sports_all) * 100
    elif state["total_providers"] > 0:
        progress_pct = (state["completed_providers"] / state["total_providers"]) * 100

    return {
        "running": state["running"],
        "last_run": state["last_run"],
        "start_time": state["start_time"].isoformat() if state["start_time"] else None,
        "elapsed_seconds": elapsed_seconds,
        "progress_pct": progress_pct,
        "total_events": state["total_events"],
        "total_odds": state["total_odds"],
        "current_provider": state["current_provider"],
        "completed_providers": state["completed_providers"],
        "total_providers": state["total_providers"],
        "providers": state["providers"],
    }


@router.get("/tiers/progress")
async def get_tier_progress():
    """
    Get per-tier extraction progress.

    Returns progress for each scheduler tier (sharp, api_soft, browser_soft)
    independently. Unlike /progress which shows one global state,
    this shows each tier separately so the UI can render individual bars.
    """
    tier_states_raw = get_tier_states()

    tiers = {}
    for tier_name, state in tier_states_raw.items():
        # Calculate elapsed time
        if state.get("running") and state.get("start_time"):
            elapsed = (datetime.now(timezone.utc) - state["start_time"]).total_seconds()
        else:
            elapsed = state.get("elapsed_seconds", 0)

        # Calculate progress percentage from sport-level granularity
        # Each provider contributes its completed sports / total sports
        # This gives smooth progress across the entire tier
        total_p = state.get("total_providers", 0)
        completed_p = state.get("completed_providers", 0)
        progress_pct = 0
        providers_dict = state.get("providers", {})
        if providers_dict:
            total_sports_all = 0
            completed_sports_all = 0
            for prov in providers_dict.values():
                total_sports_all += prov.get("sports_total", 0)
                completed_sports_all += prov.get("sports_completed", 0)
            if total_sports_all > 0:
                progress_pct = (completed_sports_all / total_sports_all) * 100
        elif total_p > 0:
            # Fallback to provider-level if no provider detail available
            progress_pct = (completed_p / total_p) * 100

        tiers[tier_name] = {
            "running": state.get("running", False),
            "last_run": state.get("last_run"),
            "elapsed_seconds": elapsed,
            "progress_pct": progress_pct,
            "total_events": state.get("total_events", 0),
            "total_odds": state.get("total_odds", 0),
            "current_provider": state.get("current_provider"),
            "completed_providers": completed_p,
            "total_providers": total_p,
            "providers": state.get("providers", {}),
        }

    # Check if ANY tier is running
    any_running = any(t["running"] for t in tiers.values())

    return {
        "any_running": any_running,
        "tiers": tiers,
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


# WebSocket endpoint
@router.websocket("/ws")
async def websocket_extraction_progress(websocket: WebSocket):
    """WebSocket endpoint for real-time extraction progress."""
    await ws_manager.connect(websocket)

    try:
        # Keep connection alive
        while True:
            # Wait for client message (ping)
            data = await websocket.receive_text()

            # Echo back to confirm connection
            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


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
    """Start a specific extraction tier.

    Valid tier names: sharp, api_soft, browser_soft
    """
    from ...pipeline.scheduler import get_scheduler

    scheduler = get_scheduler()

    tier_configs = {
        "sharp": {
            "providers": ["polymarket", "pinnacle"],
            "interval_seconds": 300,
        },
        "api_soft": {
            "providers": [
                "unibet", "leovegas", "betmgm",
                "speedybet", "x3000", "goldenbull", "1x2",
                "betinia", "campobet", "swiper", "lodur", "dbet", "quickcasino",
                "betsson", "nordicbet", "spelklubben", "bethard",
                "vbet",
            ],
            "interval_seconds": 3600,
        },
        "browser_soft": {
            "providers": [
                "mrgreen", "888sport",
                "comeon", "hajper", "lyllo",
                "snabbare", "10bet", "interwetten",
                "coolbet", "tipwin",
            ],
            "interval_seconds": 7200,
        },
    }

    if tier_name not in tier_configs:
        raise HTTPException(400, f"Unknown tier: {tier_name}. Use: sharp, api_soft, browser_soft")

    config = tier_configs[tier_name]
    await scheduler.start_tier(
        name=tier_name,
        providers=config["providers"],
        interval_seconds=config["interval_seconds"],
    )

    return {
        "status": "started",
        "tier": tier_name,
        "providers": config["providers"],
        "interval_seconds": config["interval_seconds"],
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

        # Boost freshness from boost_extraction_logs (latest scraped_at)
        boost_latest = session.query(func.max(BoostExtractionLog.scraped_at)).scalar()
        boosts_ts = boost_latest.isoformat() + "Z" if boost_latest else None

        return {
            "soft": result.get("soft"),
            "sharp": result.get("sharp"),
            "poly": result.get("poly"),
            "boosts": boosts_ts,
        }
    finally:
        session.close()
