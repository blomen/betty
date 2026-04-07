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
    """Open browser tabs for providers with pending bets OR balance available."""
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
        # Providers with real balance (from mirror login, not stale DB)
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
    """Scrape Polymarket portfolio page and stage settlements for pending bets.

    Uses the inline DOM parser directly (same as debug-poly-dom) to avoid
    code path differences with the service method.
    """
    import re
    from rapidfuzz import fuzz

    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    context = mirror.interceptor.context
    if not context:
        raise HTTPException(400, "No browser context")

    # Find polymarket portfolio page
    page = None
    for p in context.pages:
        url = p.url or ""
        if 'polymarket.com' in url and '/portfolio' in url:
            page = p
            break
    if not page:
        for p in context.pages:
            if 'polymarket.com' in (p.url or ''):
                page = p
                break
    if not page:
        return {"error": "No polymarket page", "staged": 0, "settlements": []}

    # Navigate to portfolio if needed
    if '/portfolio' not in (page.url or ''):
        await page.goto("https://polymarket.com/portfolio", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)

    # Click History tab if needed
    has_history = await page.evaluate("() => (document.body.innerText || '').includes('Claimed') || (document.body.innerText || '').includes('Lost')")
    if not has_history:
        await page.evaluate("""() => {
            for (const t of document.querySelectorAll('a, button, div[role="tab"]')) {
                if ((t.textContent || '').trim() === 'History') { t.click(); return true; }
            }
            return false;
        }""")
        await asyncio.sleep(4)

    # Read full DOM
    raw = await page.evaluate("() => document.body.innerText")
    lines = raw.split('\n')

    # Parse entries
    entries = []
    for i, line in enumerate(lines):
        a = line.strip()
        if a not in ('Lost', 'Claimed'):
            continue
        market = ''
        value = 0.0
        for j in range(i + 1, min(i + 8, len(lines))):
            l = lines[j].strip()
            if not l or l == '-':
                continue
            if re.match(r'\d+[hmd]\s*ago', l):
                break
            val_match = re.match(r'^[+-]?\$([\d,.]+)$', l)
            if val_match:
                value = float(val_match.group(1).replace(',', ''))
                if l.startswith('-'):
                    value = -value
                continue
            if re.search(r'([\d.]+)\s*shares', l):
                continue
            if re.search(r'\d+\s*[¢c\xc2]', l) and len(l) < 40:
                continue
            if not market and len(l) > 10:
                market = l
        if market:
            entries.append({'activity': a, 'market': market[:120], 'value': abs(value)})

    if not entries:
        return {"staged": 0, "settlements": [], "page_url": page.url, "note": "no Lost/Claimed entries found"}

    # Get pending poly bets
    pending = await asyncio.to_thread(mirror._get_pending_poly_bets_sync)
    if not pending:
        return {"staged": 0, "settlements": [], "entries_found": len(entries), "note": "no pending poly bets"}

    # Match
    staged = []
    for entry in entries:
        activity = entry['activity']
        market = entry['market']
        value = entry['value']

        if activity == 'Lost':
            result = 'lost'
            payout = 0.0
        elif activity == 'Claimed':
            result = 'won'
            payout = value
        else:
            continue

        best_match = None
        best_score = 0
        for pb in pending:
            event_name = pb.get('event_name', '')
            s1 = fuzz.partial_ratio(market.lower(), event_name.lower())
            s2 = fuzz.token_set_ratio(market.lower(), event_name.lower())
            home = event_name.split(' vs ')[0].strip() if ' vs ' in event_name else ''
            s3 = fuzz.partial_ratio(home.lower(), market.lower()) if home and len(home) > 3 else 0
            score = max(s1, s2, s3)
            if score > best_score and score >= 55:
                best_score = score
                best_match = pb

        if not best_match:
            continue

        if result == 'won' and best_match['stake'] > 0:
            if 0.85 <= payout / best_match['stake'] <= 1.15:
                result = 'void'
                payout = best_match['stake']

        staged.append({
            'bet_id': best_match['id'],
            'provider': 'polymarket',
            'event': market[:80],
            'odds': best_match['odds'],
            'stake': best_match['stake'],
            'result': result,
            'payout': round(payout, 2),
        })
        pending.remove(best_match)

    if staged:
        mirror._pending_settlements = staged
        wins = [s for s in staged if s['result'] == 'won']
        losses = [s for s in staged if s['result'] == 'lost']
        total_staked = sum(s['stake'] for s in staged)
        total_payout = sum(s['payout'] for s in staged)
        mirror._notify('settlements_pending', {
            'provider': 'polymarket',
            'count': len(staged),
            'wins': len(wins),
            'losses': len(losses),
            'total_staked': total_staked,
            'total_payout': total_payout,
            'net': total_payout - total_staked,
            'settlements': staged,
        })

    return {"staged": len(staged), "settlements": staged, "entries_found": len(entries), "pending_count": len(pending) + len(staged)}


@router.get("/debug-poly-dom")
async def debug_poly_dom():
    """Debug: read Polymarket portfolio DOM and show parsed entries."""
    import re
    mirror = _get_active_mirror()
    if not mirror or not mirror.interceptor.context:
        raise HTTPException(400, "No mirror running")

    # Find polymarket page
    page = None
    for p in mirror.interceptor.context.pages:
        url = p.url or ""
        if 'polymarket.com' in url and '/portfolio' in url:
            page = p
            break
    if not page:
        for p in mirror.interceptor.context.pages:
            if 'polymarket.com' in (p.url or ''):
                page = p
                break
    if not page:
        return {"error": "No polymarket page"}

    raw = await page.evaluate("() => document.body.innerText")
    lines = raw.split('\n')

    entries = []
    for i, line in enumerate(lines):
        a = line.strip()
        if a not in ('Lost', 'Claimed'):
            continue
        context_lines = []
        market = ''
        for j in range(i + 1, min(i + 8, len(lines))):
            l = lines[j].strip()
            context_lines.append(l)
            if not l or l == '-':
                continue
            if re.match(r'\d+[hmd]\s*ago', l):
                break
            if re.search(r'\d+\s*[¢c\xc2]', l) and len(l) < 40:
                continue
            if re.search(r'([\d.]+)\s*shares', l):
                continue
            if re.match(r'^[+-]?\$([\d,.]+)$', l):
                continue
            if not market and len(l) > 10:
                market = l
        entries.append({
            "activity": a,
            "market": market[:80],
            "context": context_lines[:6],
        })

    return {
        "page_url": page.url[:100],
        "total_lines": len(lines),
        "entries": entries,
    }


class NavigateBetRequest(BaseModel):
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None = None
    odds: float
    fair_odds: float
    stake: float
    display_home: str = ""
    display_away: str = ""


@router.post("/navigate-bet")
async def navigate_to_bet(req: NavigateBetRequest):
    """Navigate mirror browser to an event page and check live price.

    Generic for all providers — uses the workflow's navigate_to_event.
    Returns the live edge if available.
    """
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    from ...mirror.workflows import get_workflow
    from ...db.models import Odds, get_session

    workflow = get_workflow(req.provider_id)
    context = mirror.interceptor.context
    if not context:
        raise HTTPException(400, "No browser context")

    page = await workflow.find_tab(context)
    if not page:
        # Try to open a new tab
        url = f"https://www.{workflow.domain}" if workflow.domain else None
        if not url:
            raise HTTPException(400, f"No tab and no domain for {req.provider_id}")
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

    # Get provider-specific metadata from DB (event_slug for Poly, matchup_id for Pinnacle, event_id for Altenar)
    market_slug = None
    poly_outcome = None
    matchup_id = None
    provider_meta = {}
    db = get_session()
    try:
        odds_row = db.query(Odds).filter(
            Odds.event_id == req.event_id,
            Odds.provider_id == req.provider_id,
            Odds.market == req.market,
            Odds.outcome == req.outcome,
        ).first()
        if odds_row and odds_row.provider_meta:
            provider_meta = odds_row.provider_meta if isinstance(odds_row.provider_meta, dict) else {}
            market_slug = provider_meta.get("event_slug", "")
            matchup_id = provider_meta.get("matchup_id", "")
            if req.provider_id == "polymarket":
                poly_outcome = provider_meta.get("poly_home") if req.outcome == "home" else provider_meta.get("poly_away")
    finally:
        db.close()

    # Build a bet-like object for the workflow
    class BetProxy:
        pass
    bet = BetProxy()
    bet.bet_id = 0
    bet.event_id = req.event_id
    bet.market = req.market
    bet.outcome = req.outcome
    bet.original_outcome = req.outcome
    bet.point = req.point
    bet.odds = req.odds
    bet.fair_odds = req.fair_odds
    # Cap stake to available balance - $0.10 buffer
    actual_stake = req.stake
    try:
        from ...repositories.profile_repo import ProfileRepo
        from ..deps import get_db as _get_db
        db = next(_get_db())
        profile = ProfileRepo(db).get_active()
        bal = ProfileRepo(db).get_balance(profile.id, req.provider_id)
        if bal > 0 and actual_stake > bal - 0.10:
            actual_stake = round(max(0, bal - 0.10), 2)
        db.close()
    except Exception:
        pass
    bet.stake = actual_stake
    bet.display_home = req.display_home
    bet.display_away = req.display_away
    bet.market_slug = market_slug
    bet.poly_outcome = poly_outcome or req.outcome
    bet.matchup_id = matchup_id
    bet.altenar_event_id = provider_meta.get("event_id", "")
    bet.altenar_sport_id = provider_meta.get("sport_id", "")
    bet.altenar_category_id = provider_meta.get("category_id", "")
    bet.altenar_championship_id = provider_meta.get("championship_id", "")

    # Navigate
    navigated = await workflow.navigate_to_event(page, bet)

    # Check live price
    live_edge = None
    try:
        live_edge = await workflow.check_live_price(page, bet)
    except Exception:
        pass

    return {
        "navigated": navigated,
        "provider_id": req.provider_id,
        "event_id": req.event_id,
        "market_slug": market_slug,
        "live_edge": round(live_edge, 1) if live_edge is not None else None,
        "db_edge": round(req.odds / req.fair_odds * 100 - 100, 1) if req.fair_odds > 0 else None,
        "page_url": page.url[:100] if page else None,
    }


@router.get("/live-price/{provider_id}")
async def get_live_price(
    provider_id: str, event_id: str, market: str, outcome: str,
    fair_odds: float, point: float | None = None,
    display_home: str = "", display_away: str = "",
):
    """Read live price from the current mirror page DOM. No navigation."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    from ...mirror.workflows import get_workflow
    workflow = get_workflow(provider_id)
    context = mirror.interceptor.context
    if not context:
        return {"live_edge": None, "live_cents": None}

    page = await workflow.find_tab(context)
    if not page:
        return {"live_edge": None, "live_cents": None}

    class BetProxy:
        pass
    bet = BetProxy()
    bet.bet_id = 0
    bet.event_id = event_id
    bet.market = market
    bet.outcome = outcome
    bet.original_outcome = outcome
    bet.point = point
    bet.odds = 0
    bet.fair_odds = fair_odds
    bet.display_home = display_home
    bet.display_away = display_away

    try:
        live_edge = await workflow.check_live_price(page, bet)
        # Read the matched button price for display
        live_cents = None
        if provider_id == "polymarket":
            try:
                btn_data = await mirror._read_btn_prices(page)
                matched = mirror._find_btn_for_market(
                    btn_data, outcome, market,
                    home_name=display_home, away_name=display_away,
                )
                if matched and matched.get("price") is not None:
                    live_cents = round(matched["price"] * 100, 1)
            except Exception:
                pass
        return {
            "live_edge": round(live_edge, 1) if live_edge is not None else None,
            "live_cents": live_cents,
        }
    except Exception:
        return {"live_edge": None, "live_cents": None}


@router.get("/debug-buttons")
async def debug_buttons():
    """Debug: read all trading buttons from the current Polymarket page."""
    mirror = _get_active_mirror()
    if not mirror or not mirror.interceptor.context:
        return {"error": "no mirror"}
    from ...mirror.workflows import get_workflow
    wf = get_workflow("polymarket")
    page = await wf.find_tab(mirror.interceptor.context)
    if not page:
        return {"error": "no poly page", "pages": [p.url[:60] for p in mirror.interceptor.context.pages]}
    btns = await mirror._read_btn_prices(page)
    return {"url": page.url[:80], "buttons": btns}


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

    # Find tab directly — workflow.find_tab may miss due to domain mismatch
    page = None
    domain = workflow.domain
    all_urls = []
    for p in context.pages:
        u = p.url or ""
        all_urls.append(u[:80])
        if domain and domain in u:
            page = p
            break
        # Fallback: match provider_id in URL
        if provider_id in u.lower():
            page = p
            break

    if not page:
        return {"error": f"No {provider_id} tab found", "domain": domain, "pages": all_urls}

    # First try API
    entries = await workflow.sync_history(page)

    # Also get raw DOM text for debugging
    try:
        dom_text = await page.evaluate("() => document.body.innerText.slice(0, 3000)")
    except Exception:
        dom_text = "(could not read)"

    # Try API call directly to see error
    api_result = None
    try:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        end = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        url = f"https://api.arcadia.pinnacle.se/0.1/bets?status=settled&startDate={start}&endDate={end}"
        api_result = await page.evaluate(f"""async () => {{
            try {{
                const r = await fetch("{url}", {{credentials: "include"}});
                if (!r.ok) return {{error: r.status, text: await r.text()}};
                return await r.json();
            }} catch(e) {{ return {{error: e.message}}; }}
        }}""")
    except Exception as e:
        api_result = {"error": str(e)}

    return {
        "provider": provider_id,
        "page_url": page.url[:100],
        "domain": domain,
        "entries_from_sync_history": len(entries),
        "api_raw": str(api_result)[:500] if api_result else None,
        "dom_preview": dom_text[:500] if dom_text else None,
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


@router.get("/page-query")
async def page_query(selector: str, action: str = "count"):
    """Query elements via Playwright locator (pierces shadow DOM).

    action: count | texts | click | snapshot
    """
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    page = mirror.interceptor.context.pages[0]
    loc = page.locator(selector)

    if action == "count":
        n = await loc.count()
        return {"selector": selector, "count": n}
    elif action == "texts":
        texts = await loc.all_text_contents()
        return {"selector": selector, "count": len(texts), "texts": texts[:50]}
    elif action == "click":
        await loc.first.click()
        return {"selector": selector, "clicked": True}
    elif action == "snapshot":
        n = await loc.count()
        items = []
        for i in range(min(n, 20)):
            el = loc.nth(i)
            text = (await el.text_content() or "").strip()[:60]
            visible = await el.is_visible()
            box = await el.bounding_box()
            items.append({"text": text, "visible": visible, "box": box})
        return {"selector": selector, "count": n, "items": items}
    else:
        raise HTTPException(400, f"Unknown action: {action}")


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
