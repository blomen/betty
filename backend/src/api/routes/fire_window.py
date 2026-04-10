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
    liquid_amount: float | None = None  # Triggers deposit phase with allocation


@router.post("/open")
async def open_fire_window(request: OpenRequest):
    """Build fire window, then open tabs for providers that need action.

    Opens tabs for providers with:
    - Balance > 0 (can place bets), OR
    - Pending bets where start_time has passed (can settle → free cash)

    If liquid_amount is provided, runs the allocator and includes deposit
    recommendations in the response (deposit phase before betting).
    """
    if not request.batch:
        raise HTTPException(400, "Empty batch")
    result = fw.open_window(request.batch, request.provider_order, request.liquid_amount)

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


@router.post("/deposit-phase/complete")
def complete_deposit_phase():
    """Mark deposit phase as done; window is ready for betting."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    window.deposit_phase_complete = True
    return {"status": "ready_for_betting"}


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
async def get_next_bet():
    """Get the next unfired bet and navigate the provider tab to the event."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider")

    result = fw.get_next_bet()

    # Auto-navigate the provider's tab to this event
    # For clusters, provider_id is the actual provider (not cluster ID)
    if not result.get("done") and result.get("bet_id"):
        mirror = _get_active_mirror()
        if mirror:
            nav_pid = result.get("provider_id") or window.current_provider
            try:
                from ...mirror.workflows import get_workflow
                workflow = get_workflow(nav_pid)
                context = getattr(mirror, 'interceptor', None)
                context = getattr(context, 'context', None) if context else None
                if context:
                    page = await workflow.find_tab(context)
                    if page:
                        class BetNav:
                            pass
                        bet = BetNav()
                        bet.bet_id = result["bet_id"]
                        bet.market_slug = result.get("market_slug")
                        bet.matchup_id = result.get("matchup_id")
                        bet.display_home = result.get("display_home", "")
                        bet.display_away = result.get("display_away", "")
                        bet.outcome = result.get("outcome", "")
                        bet.original_outcome = result.get("original_outcome", "")
                        bet.poly_outcome = result.get("poly_outcome", "")
                        bet.market = result.get("market", "")
                        bet.odds = result.get("odds", 0)
                        bet.stake = result.get("stake", 0)
                        bet.point = result.get("point")
                        await workflow.navigate_to_event(page, bet)
                        result["navigated"] = True
            except Exception as e:
                logger.warning(f"[next-bet] Navigation failed: {e}")

    return result


@router.post("/check-bet/{bet_id}")
async def check_bet(bet_id: int):
    """Check live price for a specific bet."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    mirror = _get_active_mirror()
    return await fw.check_bet(bet_id, mirror)


class PlaceBetRequest(BaseModel):
    target_provider: str | None = None


@router.post("/place-bet/{bet_id}")
async def place_bet(bet_id: int, request: PlaceBetRequest | None = None):
    """Place a single confirmed bet. For clusters, optionally specify target_provider."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    mirror = _get_active_mirror()
    target = request.target_provider if request else None
    return await fw.place_bet(bet_id, mirror, target_provider=target)


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


@router.post("/pinnacle/scan")
async def pinnacle_scan():
    """Read-only preview of Pinnacle account: balance, pending bets, settled bets, DB diff."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    context = getattr(mirror, 'interceptor', None)
    context = getattr(context, 'context', None) if context else None
    if not context:
        raise HTTPException(400, "No browser context")

    from ...mirror.workflows import get_workflow
    workflow = get_workflow("pinnacle")
    page = await workflow.find_tab(context)
    if not page:
        raise HTTPException(400, "No Pinnacle tab open")

    return await workflow.scan(page)


@router.post("/pinnacle/settle-all")
async def pinnacle_settle_all():
    """Full automated Pinnacle settlement: scrape pending bets + auto-settle + sync balance."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    context = getattr(mirror, 'interceptor', None)
    context = getattr(context, 'context', None) if context else None
    if not context:
        raise HTTPException(400, "No browser context")

    from ...mirror.workflows import get_workflow
    workflow = get_workflow("pinnacle")
    page = await workflow.find_tab(context)
    if not page:
        raise HTTPException(400, "No Pinnacle tab open")

    return await workflow.settle_all(page)


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


@router.post("/polymarket/scan")
async def polymarket_scan():
    """Scan Polymarket portfolio — preview what will be claimed/redeemed/settled.

    Returns a breakdown WITHOUT clicking anything. User reviews this
    before confirming via /polymarket/settle-all.
    """
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
        page = await context.new_page()
        await page.goto(
            "https://polymarket.com/portfolio?tab=positions",
            wait_until="domcontentloaded", timeout=15000,
        )

    return await workflow.scan_portfolio_settlements(page)


@router.post("/polymarket/settle-all")
async def polymarket_settle_all():
    """Execute Polymarket settlement: claim → redeem → settle DB.

    Call /polymarket/scan first to preview, then this to execute.
    """
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
        page = await context.new_page()
        await page.goto(
            "https://polymarket.com/portfolio?tab=positions",
            wait_until="domcontentloaded", timeout=15000,
        )

    return await workflow.settle_all(page)
