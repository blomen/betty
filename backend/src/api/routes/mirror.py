"""Mirror API routes — start/stop bet interception browser."""

import asyncio
import logging
import yaml
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...mirror.service import MirrorService
from ...pipeline.broadcast import odds_broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mirror", tags=["mirror"])

# Multi-provider mirror state (used by lifespan auto-start AND manual start/stop)
_mirrors: dict[str, MirrorService] = {}
_start_lock = asyncio.Lock()

# Default provider when none specified
_DEFAULT_PROVIDER = "spelklubben"


def _load_all_providers() -> dict[str, dict]:
    """Load provider configs from providers.yaml for mirror auto-start."""
    config_path = Path(__file__).resolve().parents[2] / "config" / "providers.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("providers", {})


def _any_running() -> bool:
    """Check if any mirror instance is running."""
    return any(m.get_status()["running"] for m in _mirrors.values())


@router.post("/start")
async def start_mirror():
    """Launch the mirror browser."""
    if _any_running():
        raise HTTPException(400, "Mirror already running")

    mirror = MirrorService(broadcaster=odds_broadcaster, provider_id=_DEFAULT_PROVIDER)
    await mirror.start()
    _mirrors[_DEFAULT_PROVIDER] = mirror
    return mirror.get_status()


@router.post("/ensure-started")
async def ensure_mirror_started():
    """Idempotent: start mirror if not already running, otherwise return status."""
    async with _start_lock:
        if _any_running():
            for m in _mirrors.values():
                status = m.get_status()
                if status["running"]:
                    return status
            return {"running": True, "status": "running"}

        mirror = MirrorService(broadcaster=odds_broadcaster, provider_id=_DEFAULT_PROVIDER)
        await mirror.start()
        _mirrors[_DEFAULT_PROVIDER] = mirror
        return mirror.get_status()


@router.post("/stop")
async def stop_mirror():
    """Stop all mirror browsers."""
    if not _mirrors:
        raise HTTPException(400, "No mirror running")

    for pid in list(_mirrors.keys()):
        try:
            await _mirrors.pop(pid).stop()
        except Exception as e:
            logger.warning(f"Error stopping mirror {pid}: {e}")

    return {"running": False, "status": "stopped"}


@router.post("/open-settle-tabs")
async def open_settle_tabs():
    """Open browser tabs for providers that need action: unsettled bets OR have balance."""
    mirror = _get_active_mirror()
    if not mirror or not mirror.interceptor.context:
        raise HTTPException(400, "No mirror running")

    from ...db.models import Bet, get_session
    from ...repositories.profile_repo import ProfileRepo
    from ...mirror.workflows import get_workflow
    from ...config.loader import load_config

    db = get_session()
    try:
        profile = ProfileRepo(db).get_active()
        # Providers with any pending bets
        pending = db.query(Bet).filter(
            Bet.profile_id == profile.id,
            Bet.result == "pending",
        ).all()
        pending_pids = {b.provider_id for b in pending}
        # Providers with balance (can place bets)
        balances = ProfileRepo(db).get_all_balances(profile.id)
        balance_pids = {pid for pid, bal in balances.items() if bal >= 10}
        provider_ids = sorted(pending_pids | balance_pids)
    finally:
        db.close()

    if not provider_ids:
        return {"opened": [], "count": 0}

    context = mirror.interceptor.context
    cfg = load_config()
    opened = []

    for pid in provider_ids:
        workflow = get_workflow(pid)
        # Skip if tab already exists
        existing = await workflow.find_tab(context)
        if existing:
            opened.append(pid)
            continue

        # Build URL from workflow domain or config
        url = None
        if workflow.domain:
            url = f"https://www.{workflow.domain}"
        else:
            pconfig = cfg.get_provider(pid)
            if pconfig:
                url = pconfig.site_url or (f"https://www.{pconfig.domain}" if pconfig.domain else None)
        if not url:
            continue

        try:
            # Reuse blank tab or open new
            blank = next((p for p in context.pages if (p.url or "").startswith("about:")), None)
            if blank:
                await blank.goto(url, wait_until="domcontentloaded", timeout=15000)
            else:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            opened.append(pid)
            logger.info(f"[mirror] Opened settle tab: {pid} → {url}")
        except Exception as e:
            logger.warning(f"[mirror] Failed to open tab for {pid}: {e}")

    return {"opened": opened, "count": len(opened)}


@router.post("/scrape-poly-portfolio")
async def scrape_poly_portfolio():
    """Scrape Polymarket portfolio page and stage settlements for pending bets."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    staged = await mirror.scrape_polymarket_settlements()
    return {"staged": len(staged), "settlements": staged}


@router.post("/settle/{provider_id}")
async def settle_provider(provider_id: str):
    """Trigger settlement sync for a provider using its workflow API."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    await mirror._settle_via_workflow(provider_id)
    return {"staged": len(mirror._pending_settlements), "settlements": mirror._pending_settlements}


@router.get("/debug-history/{provider_id}")
async def debug_history(provider_id: str):
    """Debug: fetch raw history entries from a provider workflow."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    from ...mirror.workflows import get_workflow
    workflow = get_workflow(provider_id)
    context = mirror.interceptor.context
    if not context:
        raise HTTPException(400, "No browser context")

    page = await workflow.find_tab(context)
    if not page:
        return {"error": f"No {provider_id} tab found", "pages": [p.url[:80] for p in context.pages]}

    entries = await workflow.sync_history(page)
    return {
        "provider": provider_id,
        "page_url": page.url[:100],
        "entries": len(entries),
        "settled": [
            {"event": e.event_name, "odds": e.odds, "stake": e.stake, "status": e.status, "payout": e.payout}
            for e in entries if e.status in ("won", "lost", "void")
        ],
        "pending": [
            {"event": e.event_name, "odds": e.odds, "stake": e.stake, "status": e.status}
            for e in entries if e.status == "pending"
        ],
    }


@router.get("/status")
def mirror_status():
    """Get mirror status — returns running if any mirror instance is active."""
    for m in _mirrors.values():
        status = m.get_status()
        if status["running"]:
            return status
    return {"running": False, "status": "stopped"}


def _get_active_mirror() -> MirrorService | None:
    """Get the first running mirror instance."""
    for m in _mirrors.values():
        if m.get_status()["running"]:
            return m
    return None


@router.get("/settlements")
def get_pending_settlements():
    """Get staged settlements awaiting confirmation."""
    mirror = _get_active_mirror()
    if not mirror:
        return {"settlements": []}
    return {"settlements": mirror.get_pending_settlements()}


@router.post("/settlements/confirm")
def confirm_settlements():
    """Apply all pending settlements to the database."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    return mirror.confirm_settlements()


@router.post("/settlements/reject")
def reject_settlements():
    """Discard all pending settlements."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    return mirror.reject_settlements()


@router.get("/scrape-page-bets")
async def scrape_page_bets():
    """Scrape bet history from the currently visible page DOM (Kambi/Unibet)."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    page = mirror.interceptor.context.pages[0]
    url = page.url

    # First try: check if Kambi coupon API is available via the page's auth context
    import re

    # Note: Kambi (unibet etc.) bet history is fully server-side rendered.
    # No JSON API available — DOM scraping is the only option.

    # Fallback: get raw text from page and parse
    raw_text = await page.evaluate("() => document.body.innerText")

    # Parse bets: "Singel @ ODDS Result DATE • TIME Kupong-Id: ID ... Insats: STAKE kr Utbetalning: PAYOUT kr"
    bet_pattern = re.compile(
        r'Singel\s*@\s*([\d.]+)\s+'
        r'(Vinst|F.rlust|Oavgjord|Cashout)\s+'
        r'(\d+ \w+ \d{4})\s*.\s*([\d:]+)\s+'
        r'Kupong-Id:\s*(\d+)\s+'
        r'(.*?)'
        r'Insats:\s*([\d.,]+)\s*kr'
        r'(?:\s*Utbetalning:\s*([\d.,]+)\s*kr)?',
        re.DOTALL
    )

    bets = []
    seen = set()
    for m in bet_pattern.finditer(raw_text):
        cid = m.group(5)
        if cid in seen:
            continue
        seen.add(cid)
        result_raw = m.group(2)
        if "rlust" in result_raw:
            result = "lost"
        elif result_raw == "Vinst":
            result = "won"
        elif result_raw == "Oavgjord":
            result = "void"
        else:
            result = "cashout"
        bets.append({
            "odds": float(m.group(1)),
            "result": result,
            "date": m.group(3),
            "time": m.group(4),
            "coupon_id": cid,
            "market_event": m.group(6).strip().replace("\n", " ")[:80],
            "stake": float(m.group(7).replace(",", ".")),
            "payout": float(m.group(8).replace(",", ".")) if m.group(8) else 0,
        })

    if not bets:
        return {"url": url, "data": {"bets": [], "count": 0, "staged": 0}}

    # Match scraped bets against pending DB bets and stage settlements
    from ..deps import get_db as _get_db
    from ...db.models import Bet
    from ...repositories import ProfileRepo

    db = next(_get_db())
    try:
        profile = ProfileRepo(db).get_active()
        provider_id = "unibet"  # TODO: detect from URL

        pending = db.query(Bet).filter(
            Bet.profile_id == profile.id,
            Bet.result == "pending",
            Bet.provider_id == provider_id,
        ).all()

        staged = []
        for pb in pending:
            for sb in bets:
                if abs(sb["odds"] - pb.odds) < 0.02 and abs(sb["stake"] - pb.stake) < 0.02:
                    staged.append({
                        "bet_id": pb.id,
                        "provider": provider_id,
                        "event": sb["market_event"],
                        "odds": sb["odds"],
                        "stake": sb["stake"],
                        "result": sb["result"],
                        "payout": sb["payout"],
                    })
                    break

        # Stage in mirror service and notify via SSE
        if staged:
            mirror._pending_settlements = staged
            mirror._notify("settlements_pending", {
                "provider": provider_id,
                "count": len(staged),
                "wins": len([s for s in staged if s["result"] == "won"]),
                "losses": len([s for s in staged if s["result"] == "lost"]),
                "total_staked": sum(s["stake"] for s in staged),
                "total_payout": sum(s["payout"] for s in staged),
                "net": sum(s["payout"] for s in staged) - sum(s["stake"] for s in staged),
                "settlements": staged,
            })

    finally:
        db.close()

    return {"url": url, "data": {"bets": bets, "count": len(bets), "staged": len(staged)}}


class PolymarketBetRequest(BaseModel):
    bet_id: int
    market_slug: str
    token_id: str = ""
    outcome: str
    amount_usdc: float
    expected_price: float
    max_slippage_pct: float = 2.0


class PlaceBetsRequest(BaseModel):
    bets: list[PolymarketBetRequest]


@router.post("/place-bets")
async def place_polymarket_bets(request: PlaceBetsRequest):
    """Place a batch of bets on Polymarket via mirror browser automation."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    page = mirror.interceptor.context.pages[0]
    if "polymarket.com" not in (page.url or ""):
        raise HTTPException(400, f"Mirror browser is not on Polymarket (current: {page.url})")

    bets = [b.model_dump() for b in request.bets]
    result = await mirror.place_polymarket_bets(bets)
    return result


class FireBatchBet(BaseModel):
    event_id: str
    market: str
    outcome: str
    odds: float
    stake: float  # in USDC (batch builder already converts from SEK)


class FireBatchRequest(BaseModel):
    bets: list[FireBatchBet]
    max_slippage_pct: float = 3.0


def _resolve_batch_bets(request: FireBatchRequest) -> dict:
    """Resolve batch bets to Polymarket slugs and outcomes from DB.

    Returns {"bets": [...resolved...], "errors": [...]}
    """
    from ..deps import get_db as _get_db
    from ...db.models import Odds

    db = next(_get_db())
    resolved = []
    errors = []
    try:
        for i, bet in enumerate(request.bets):
            odds_row = db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id == "polymarket",
                Odds.market == bet.market,
                Odds.outcome == bet.outcome,
            ).first()

            if not odds_row or not odds_row.provider_meta:
                errors.append({"event_id": bet.event_id, "reason": "No Polymarket odds found"})
                continue

            meta = odds_row.provider_meta if isinstance(odds_row.provider_meta, dict) else {}
            slug = meta.get("event_slug", "")
            if not slug:
                errors.append({"event_id": bet.event_id, "reason": "No event_slug in provider_meta"})
                continue

            amount_usdc = round(bet.stake, 2)
            expected_price = round(1 / bet.odds, 4) if bet.odds > 1 else 0.5
            poly_outcome = _resolve_poly_outcome(bet.outcome, meta)

            resolved.append({
                "bet_id": i,
                "market_slug": slug,
                "token_id": "",
                "outcome": poly_outcome,
                "amount_usdc": amount_usdc,
                "expected_price": expected_price,
                "max_slippage_pct": request.max_slippage_pct,
                "event_id": bet.event_id,
                "original_odds": bet.odds,
                "_original_outcome": bet.outcome,
                "_market_type": bet.market,
            })
    finally:
        db.close()
    return {"bets": resolved, "errors": errors}


@router.post("/live-edge")
async def get_live_edge(request: FireBatchRequest):
    """Get live Polymarket odds compared against Pinnacle fair odds.

    Auto-ensures mirror is started. Opens tabs to market pages automatically.
    Returns per-bet: live_odds, fair_odds, edge_pct, status.
    """
    mirror = _get_active_mirror()
    if not mirror:
        # Auto-start mirror
        async with _start_lock:
            if not _any_running():
                mirror = MirrorService(broadcaster=odds_broadcaster, provider_id=_DEFAULT_PROVIDER)
                await mirror.start()
                _mirrors[_DEFAULT_PROVIDER] = mirror
            else:
                mirror = _get_active_mirror()
    if not mirror or not mirror.interceptor.context:
        raise HTTPException(400, "Could not start mirror browser")

    resolved = await asyncio.to_thread(_resolve_batch_bets, request)
    if not resolved["bets"]:
        return {"bets": [], "resolve_errors": resolved["errors"]}

    result = await mirror.get_live_edge(resolved["bets"])
    result["resolve_errors"] = resolved["errors"]
    return result


@router.post("/fire-live")
async def fire_live(request: FireBatchRequest):
    """Scan live Polymarket prices and auto-fire bets with positive edge.

    Auto-ensures mirror is started. Opens tabs to market pages automatically.
    Only places bets where edge_pct > 0 after Polymarket's 2% fee.
    """
    mirror = _get_active_mirror()
    if not mirror:
        async with _start_lock:
            if not _any_running():
                mirror = MirrorService(broadcaster=odds_broadcaster, provider_id=_DEFAULT_PROVIDER)
                await mirror.start()
                _mirrors[_DEFAULT_PROVIDER] = mirror
            else:
                mirror = _get_active_mirror()
    if not mirror or not mirror.interceptor.context:
        raise HTTPException(400, "Could not start mirror browser")

    resolved = await asyncio.to_thread(_resolve_batch_bets, request)
    if not resolved["bets"]:
        return {"placed": [], "skipped": [], "negative": [], "errors": resolved["errors"], "total": 0}

    result = await mirror.fire_with_live_edge(resolved["bets"])
    result["resolve_errors"] = resolved["errors"]
    return result


@router.post("/close-poly-tabs")
async def close_poly_tabs():
    """Close all Polymarket tabs and any extra pages beyond the main one."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    # Close tracked poly tabs
    await mirror.close_poly_tabs()

    # Close any untracked extra pages (leftover from previous spam)
    context = mirror.interceptor.context
    if context:
        pages = context.pages
        if len(pages) > 1:
            for page in pages[1:]:
                try:
                    await page.close()
                except Exception:
                    pass

    remaining = len(context.pages) if context else 0
    return {"closed": True, "remaining_pages": remaining}


@router.post("/fire-batch")
async def fire_polymarket_batch(request: FireBatchRequest):
    """Deprecated — use /fire-live instead."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    page = mirror.interceptor.context.pages[0]
    if "polymarket.com" not in (page.url or ""):
        raise HTTPException(400, f"Mirror browser is not on Polymarket (current: {page.url})")

    resolved = await asyncio.to_thread(_resolve_batch_bets, request)
    if not resolved["bets"]:
        return {"placed": [], "skipped": [], "failed": resolved["errors"], "total": 0, "resolve_errors": resolved["errors"]}

    result = await mirror.place_polymarket_bets(resolved["bets"])
    result["resolve_errors"] = resolved["errors"]
    return result


def _resolve_poly_outcome(outcome: str, meta: dict) -> str:
    """Map internal outcome (home/away/draw) to Polymarket display outcome.

    Polymarket uses team names or Yes/No for outcomes.
    provider_meta has poly_home/poly_away for the mapping.
    """
    poly_home = meta.get("poly_home", "")
    poly_away = meta.get("poly_away", "")

    if outcome == "home" and poly_home:
        return poly_home
    if outcome == "away" and poly_away:
        return poly_away
    if outcome == "draw":
        return "Draw"
    if outcome == "over":
        return "Over"
    if outcome == "under":
        return "Under"
    # Fallback: return as-is (might be "Yes"/"No" already)
    return outcome


@router.get("/page-eval")
async def page_eval(js: str = "() => document.body.innerText"):
    """Evaluate JS on the active mirror page and return result."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    page = mirror.interceptor.context.pages[0]
    result = await page.evaluate(js)
    return {"url": page.url, "result": result}


@router.get("/notification-recipes")
def get_notification_recipes():
    """List all stored notification mute recipes."""
    mirror = _get_active_mirror()
    if not mirror:
        return {"recipes": []}
    return {"recipes": mirror.get_notification_recipes()}


@router.delete("/notification-recipes/{provider_id}")
def delete_notification_recipe(provider_id: str):
    """Delete a notification mute recipe for a provider."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(404, "No mirror running")
    deleted = mirror.delete_notification_recipe(provider_id)
    if not deleted:
        raise HTTPException(404, f"No recipe found for {provider_id}")
    return {"deleted": True, "provider_id": provider_id}
