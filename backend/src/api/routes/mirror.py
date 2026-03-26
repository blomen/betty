"""Mirror API routes — start/stop bet interception browser."""

import logging
import yaml
from pathlib import Path
from fastapi import APIRouter, HTTPException

from ...mirror.service import MirrorService
from ...pipeline.broadcast import odds_broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mirror", tags=["mirror"])

# Multi-provider mirror state (used by lifespan auto-start AND manual start/stop)
_mirrors: dict[str, MirrorService] = {}

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


@router.get("/status")
async def mirror_status():
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
async def get_pending_settlements():
    """Get staged settlements awaiting confirmation."""
    mirror = _get_active_mirror()
    if not mirror:
        return {"settlements": []}
    return {"settlements": mirror.get_pending_settlements()}


@router.post("/settlements/confirm")
async def confirm_settlements():
    """Apply all pending settlements to the database."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    return mirror.confirm_settlements()


@router.post("/settlements/reject")
async def reject_settlements():
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


@router.get("/notification-recipes")
async def get_notification_recipes():
    """List all stored notification mute recipes."""
    mirror = _get_active_mirror()
    if not mirror:
        return {"recipes": []}
    return {"recipes": mirror.get_notification_recipes()}


@router.delete("/notification-recipes/{provider_id}")
async def delete_notification_recipe(provider_id: str):
    """Delete a notification mute recipe for a provider."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(404, "No mirror running")
    deleted = mirror.delete_notification_recipe(provider_id)
    if not deleted:
        raise HTTPException(404, f"No recipe found for {provider_id}")
    return {"deleted": True, "provider_id": provider_id}
