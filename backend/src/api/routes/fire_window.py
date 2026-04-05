"""Fire Window API routes — provider-by-provider batch execution."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...services import fire_window as fw
from .mirror import _get_active_mirror

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fire-window", tags=["fire-window"])


class OpenRequest(BaseModel):
    batch: list[dict]
    provider_order: list[str] | None = None


@router.post("/open")
async def open_fire_window(request: OpenRequest):
    """Build fire window, then open tabs for providers that need action.

    Opens tabs for providers with:
    - Balance > 0 (can place bets), OR
    - Pending bets where start_time has passed (can settle → free cash)
    """
    if not request.batch:
        raise HTTPException(400, "Empty batch")
    result = fw.open_window(request.batch, request.provider_order)

    # Auto-open tabs for providers that need action
    mirror = _get_active_mirror()
    if mirror:
        try:
            tabs_result = await fw.open_needed_tabs(mirror)
            result["tabs"] = tabs_result
        except Exception as e:
            logger.exception(f"[open] open_needed_tabs failed: {e}")
            result["tabs"] = {"error": str(e)}

    return result



@router.post("/activate/{provider_id}")
async def activate_provider(provider_id: str):
    """Activate a provider: open tab, check login, sync history+balance, then ready for bets."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")

    if provider_id not in window.provider_bets:
        raise HTTPException(400, f"Provider '{provider_id}' not in queue")

    mirror = _get_active_mirror()
    result = fw.set_current_provider(provider_id)

    # Run the workflow setup sequence
    setup = await fw.activate_provider_workflow(provider_id, mirror)
    result["workflow"] = setup

    return result


@router.get("/state")
def get_state():
    """Get current provider's bet states + balance."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    return fw.get_live_state()


@router.post("/fire")
async def fire_current_provider():
    """Fire +EV bets for current provider (legacy batch fire)."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider to fire")
    mirror = _get_active_mirror()
    return await fw.fire_provider(mirror)


@router.get("/next-bet")
def get_next_bet():
    """Get the next unfired bet for the current provider."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider")
    return fw.get_next_bet()


@router.post("/check-bet/{bet_id}")
async def check_bet(bet_id: int):
    """Check live price for a specific bet."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    mirror = _get_active_mirror()
    return await fw.check_bet(bet_id, mirror)


@router.post("/place-bet/{bet_id}")
async def place_bet(bet_id: int):
    """Place a single confirmed bet."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    mirror = _get_active_mirror()
    return await fw.place_bet(bet_id, mirror)


@router.post("/skip-bet/{bet_id}")
def skip_single_bet(bet_id: int):
    """Skip a single bet without placing."""
    return fw.skip_bet(bet_id)


@router.post("/skip")
def skip_current_provider():
    """Skip current provider, advance to next."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider to skip")
    return fw.skip_provider()


@router.get("/queue")
def get_queue():
    """Get the provider queue with status."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    return fw._build_queue_response()


@router.post("/close")
async def close_fire_window():
    """Close fire window."""
    fw.close_window()
    return {"status": "closed"}


@router.get("/summary")
def get_summary():
    """Get summary of all fired providers."""
    return fw.get_fired_summary()


@router.post("/settle-check")
async def settle_check():
    """Check ALL providers for pending bets that need settlement.

    Returns a breakdown per provider with each bet's outcome and P&L.
    User reviews this before confirming settlements.
    """
    mirror = _get_active_mirror()
    return await fw.check_settlements(mirror)


@router.post("/settle-confirm")
async def settle_confirm():
    """Confirm and apply the staged settlements."""
    return fw.apply_settlements()


@router.post("/polymarket/portfolio")
async def polymarket_portfolio():
    """Scrape Polymarket portfolio positions from DOM."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    context = getattr(mirror, 'interceptor', None)
    context = getattr(context, 'context', None) if context else None
    if not context:
        raise HTTPException(400, "No browser context")

    from ...mirror.workflows.polymarket import PolymarketWorkflow
    workflow = PolymarketWorkflow(provider_id="polymarket", domain="polymarket.com")
    page = await workflow.find_tab(context)
    if not page:
        raise HTTPException(400, "No Polymarket tab open")

    page_url = page.url
    try:
        positions = await workflow.scrape_portfolio(page)
    except Exception as e:
        logger.exception(f"[polymarket] Portfolio scrape failed: {e}")
        return {"positions": [], "error": str(e), "page_url": page_url}

    # Match against pending bets in DB
    pending_count = 0
    pending_stake = 0.0
    try:
        from ...db.models import Bet, get_session
        from ...repositories.profile_repo import ProfileRepo
        db = get_session()
        try:
            repo = ProfileRepo(db)
            profile = repo.get_active()
            if profile:
                pending = db.query(Bet).filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "polymarket",
                    Bet.result == "pending",
                ).all()
                pending_count = len(pending)
                pending_stake = round(sum(b.stake for b in pending), 2)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[polymarket] DB query failed: {e}")

    return {
        "positions": positions,
        "pending_bets": pending_count,
        "pending_stake": pending_stake,
        "page_url": page_url,
    }


@router.post("/polymarket/redeem")
async def polymarket_redeem():
    """Click all Redeem buttons on Polymarket portfolio page."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    context = getattr(mirror, 'interceptor', None)
    context = getattr(context, 'context', None) if context else None
    if not context:
        raise HTTPException(400, "No browser context")

    from ...mirror.workflows.polymarket import PolymarketWorkflow
    workflow = PolymarketWorkflow(provider_id="polymarket", domain="polymarket.com")
    page = await workflow.find_tab(context)
    if not page:
        raise HTTPException(400, "No Polymarket tab open")

    return await workflow.redeem_all(page)
