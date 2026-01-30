"""Extraction API routes."""

import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect

from ..state import (
    extraction_state,
    update_extraction_state,
    get_extraction_state,
    ws_manager,
)

router = APIRouter(prefix="/api/extraction", tags=["extraction"])
logger = logging.getLogger(__name__)


async def poll_metrics_and_update_state(pipeline, stop_event):
    """
    Polls metrics every 500ms and updates extraction_state.
    Runs in background while extraction is active.
    """
    while not stop_event.is_set():
        if not pipeline.metrics:
            await asyncio.sleep(0.5)
            continue

        current_run = pipeline.metrics.get_current_run()
        if not current_run:
            await asyncio.sleep(0.5)
            continue

        # Build provider states from metrics
        providers_state = {}
        completed_count = 0
        current_provider = None

        # Polymarket
        if current_run.polymarket_events > 0:
            providers_state["polymarket"] = {
                "status": "completed",
                "events": current_run.polymarket_events,
                "odds": current_run.polymarket_odds,
                "duration_seconds": 0,
                "error": None,
                "sports_completed": 0,
                "sports_total": 0,
            }
            completed_count += 1

        # Each provider
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

        # Update global state
        update_extraction_state(
            total_events=current_run.total_events,
            total_odds=current_run.total_odds,
            providers=providers_state,
            current_provider=current_provider,
            completed_providers=completed_count,
        )

        await asyncio.sleep(0.5)


async def run_extraction_task(providers: list[str] | None):
    """Background task to run extraction from ALL sports."""
    from ..deps import get_pipeline
    from ...pipeline.orchestrator import ExtractionPipeline

    pipeline = ExtractionPipeline()
    provider_list = providers if providers else pipeline.engine.get_enabled_providers()
    total_providers = len(provider_list) + 1  # +1 for Polymarket

    # Initialize state
    update_extraction_state(
        running=True,
        start_time=datetime.utcnow(),
        total_events=0,
        total_odds=0,
        providers={},
        current_provider=None,
        completed_providers=0,
        total_providers=total_providers,
    )

    try:
        # Start metrics polling task
        stop_event = asyncio.Event()
        polling_task = asyncio.create_task(
            poll_metrics_and_update_state(pipeline, stop_event)
        )

        try:
            # Extract ALL sports, filtered providers
            results = await pipeline.run(
                polymarket=True,
                providers=provider_list if provider_list else None,
            )

            update_extraction_state(
                total_events=results.get("total_events", 0),
                total_odds=results.get("total_odds", 0),
                last_run=datetime.utcnow().isoformat(),
            )

        finally:
            stop_event.set()
            await polling_task

    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)
        update_extraction_state(error=str(e))

    finally:
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
    elapsed_seconds = 0
    if state["running"] and state["start_time"]:
        elapsed_seconds = (datetime.utcnow() - state["start_time"]).total_seconds()

    # Calculate progress percentage
    progress_pct = 0
    if state["total_providers"] > 0:
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


@router.post("/run")
async def run_extraction(
    background_tasks: BackgroundTasks,
    providers: str | None = None,  # Optional: "unibet,leovegas" or None for all
):
    """
    Trigger extraction from all configured sports and providers.

    Always extracts:
    - ALL sports/leagues from sports.json (113 leagues)
    - ALL enabled providers (or filtered subset if providers specified)
    - Polymarket as truth source
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
