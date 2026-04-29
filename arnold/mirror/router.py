"""Mirror router — browser control and bet placement endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from .browser import MirrorBrowser
from .pending_loop import PendingLoop
from .play_loop import PlayLoop
from .sse import MirrorBroadcaster
from .workflows import get_workflow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class NavigateRequest(BaseModel):
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None = None
    odds: float
    fair_odds: float
    stake: float
    display_home: str
    display_away: str


class PlaceRequest(BaseModel):
    provider_id: str
    bet_id: int


class OpenTabRequest(BaseModel):
    url: str


class PlayStartRequest(BaseModel):
    batch: list[dict[str, Any]]
    balances: dict[str, Any]
    provider_id: str | None = None  # backward compat: single provider
    provider_ids: list[str] | None = None  # multi-provider: list of providers to start


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_mirror_router(browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str) -> APIRouter:
    """Return an APIRouter with mirror browser control and placement endpoints."""

    router = APIRouter(prefix="/mirror", tags=["mirror"])

    play_loop = PlayLoop(browser, broadcaster, proxy_url)
    pending_loop = PendingLoop(browser, broadcaster, proxy_url)
    pending_loop.start()

    # Wire browser bet interception → play loop auto-record
    # Chain with existing callback (broadcaster.publish set in server.py)
    _prev_callback = browser._on_event

    async def _post_balance_async(provider_id: str, balance: float):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{proxy_url}/api/bankroll/set/{provider_id}",
                    json={"balance": balance},
                    headers={"X-Nginx-Authenticated": "arnoldsports"},
                )
        except Exception:
            pass

    def _on_browser_event(event_type: str, data: dict):
        if _prev_callback:
            _prev_callback(event_type, data)
        if event_type == "bet_intercepted":
            play_loop.on_bet_intercepted(
                data.get("provider_id", ""),
                data.get("body", {}),
                data.get("request_body"),
            )
        if event_type == "event_details_intercepted":
            pid = data.get("provider_id", "")
            eid = data.get("event_id", "")
            body = data.get("body")
            if pid and eid and body:
                from .workflows import get_workflow

                wf = get_workflow(pid)
                if hasattr(wf, "cache_event_details"):
                    wf.cache_event_details(eid, body)
        if event_type == "balance_intercepted":
            pid = data.get("provider_id", "")
            bal = data.get("balance")
            if pid and bal is not None:
                import asyncio

                try:
                    asyncio.ensure_future(_post_balance_async(pid, bal))
                except RuntimeError:
                    pass

    browser.set_event_callback(_on_browser_event)

    @router.post("/close-all-tabs")
    async def close_all_tabs():
        """Close all browser tabs (keeps browser running). Leaves one blank tab."""
        if not browser.running or not browser.context:
            return {"closed": 0}
        pages = list(browser.context.pages)
        closed = 0
        for i, page in enumerate(pages):
            if i == 0:
                # Navigate first tab to blank instead of closing (keeps context alive)
                try:
                    await page.goto("about:blank")
                except Exception:
                    pass
                continue
            try:
                await page.close()
                closed += 1
            except Exception:
                pass
        browser.provider_data.clear()
        return {"closed": closed}

    @router.post("/open-provider-tab")
    async def open_provider_tab(request: Request):
        """Open a provider's site in a new tab (starts browser if needed)."""
        body = await request.json()
        pid = body.get("provider_id", "")
        if not pid:
            raise HTTPException(400, "provider_id required")
        # Start browser if not running
        if not browser.running:
            await browser.start()
        # Check if tab already open
        workflow = get_workflow(pid)
        if browser.context:
            for page in browser.context.pages:
                if workflow.domain and workflow.domain in page.url:
                    return {"status": "already_open", "url": page.url, "provider_id": pid}
        # Open new tab
        domain = workflow.domain
        if not domain:
            raise HTTPException(400, f"No domain for provider {pid}")
        page = await browser.open_tab(workflow.home_url)
        return {"status": "opened", "url": page.url, "provider_id": pid}

    @router.get("/browser/tabs")
    async def browser_tabs():
        """Live browser state — which tabs are open, URLs, provider detection."""
        if not browser.running or not browser.context:
            return {"tabs": []}
        tabs = []
        for page in browser.context.pages:
            url = page.url
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            tabs.append({"url": url, "title": title})
        return {"tabs": tabs}

    @router.get("/browser/provider/{provider_id}")
    async def browser_provider_state(provider_id: str):
        """Live state of a provider — from intercepted/cached data only. Never opens tabs."""
        if not browser.running or not browser.context:
            return {"found": False, "logged_in": False, "balance": None, "reason": "browser_not_started"}
        # Check if we have a tab for this provider
        workflow = get_workflow(provider_id)
        tab_url = None
        for page in browser.context.pages:
            if workflow.domain and workflow.domain in page.url:
                tab_url = page.url
                break
        if not tab_url:
            return {"found": False, "logged_in": False, "balance": None, "domain": workflow.domain}
        # Detection order: intercepted cache → workflow.check_login (intel JSON) → DOM scrape.
        # Balance: always attempt workflow.sync_balance when logged in (strategy/intel driven).
        intercepted = browser.provider_data.get(provider_id, {})
        logged_in = intercepted.get("logged_in", False)
        balance = intercepted.get("balance")
        page = None
        for p in browser.context.pages:
            if workflow.domain and workflow.domain in p.url:
                page = p
                break
        if not logged_in and page:
            try:
                if await workflow.check_login(page):
                    logged_in = True
                    browser.provider_data.setdefault(provider_id, {}).update(
                        {"logged_in": True, "source": "workflow_check_login"}
                    )
            except Exception:
                pass
        if not logged_in:
            dom = await browser.check_login_dom(provider_id)
            logged_in = dom.get("logged_in", False)
            balance = dom.get("balance") or balance
        if logged_in and page:
            try:
                bal = await workflow.sync_balance(page)
                if bal >= 0:
                    balance = bal
                    browser.provider_data.setdefault(provider_id, {}).update(
                        {"balance": bal, "source": "workflow_sync_balance"}
                    )
            except Exception:
                pass
        return {
            "found": True,
            "provider_id": provider_id,
            "url": tab_url,
            "logged_in": logged_in,
            "balance": balance,
            "domain": workflow.domain,
        }

    @router.get("/browser/test-settle/{provider_id}")
    async def test_settle(provider_id: str):
        """Debug: run sync_history and return results."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"error": "no tab"}
        try:
            history = await workflow.sync_history(page)
            return {
                "count": len(history),
                "entries": [
                    {"event": e.event_name, "status": e.status, "odds": e.odds, "stake": e.stake, "payout": e.payout}
                    for e in history[:10]
                ],
            }
        except Exception as e:
            return {"error": str(e)}

    @router.get("/browser/debug-eval/{provider_id}")
    async def debug_eval(provider_id: str, js: str = "document.title"):
        """Debug: evaluate JS on provider tab."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"error": "no tab"}
        try:
            result = await page.evaluate(js)
            return {"result": result}
        except Exception as e:
            return {"error": str(e)}

    @router.get("/browser/screenshot/{provider_id}")
    async def browser_screenshot(provider_id: str):
        """Take screenshot of provider tab and check for balance text."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"error": "no tab found"}
        # Check page content for balance
        balance_text = await page.evaluate("""() => {
            const text = document.body.innerText;
            const m = text.match(/(\\d+[,.]\\d+)\\s*KR/i);
            return m ? m[0] : null;
        }""")
        await page.screenshot(path="debug_screenshot.png")
        return {"url": page.url, "balance_text": balance_text, "screenshot": "debug_screenshot.png"}

    @router.post("/browser/eval/{provider_id}")
    async def browser_eval(provider_id: str, body: dict[str, Any]):
        """Evaluate JS in a provider's tab. Debug only."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"error": "no tab"}
        result = await page.evaluate(body["js"])
        return {"result": result}

    @router.post("/tv-open")
    async def tv_open():
        """Manual fallback for opening the TradingView NQ chart in the
        mirror — same logic as the auto-open task but callable any time.
        Idempotent: returns existing TV tab if one is already open.
        """
        url = "https://www.tradingview.com/chart/?symbol=CME_MINI%3ANQ1!"
        try:
            if not browser.running:
                await browser.start()
            if browser.context:
                for p in browser.context.pages:
                    try:
                        if "tradingview.com" in (p.url or ""):
                            return {"ok": True, "reused": True, "url": p.url}
                    except Exception:
                        continue
            page = await browser.open_tab(url)
            return {"ok": True, "reused": False, "url": page.url}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @router.post("/browser/tv-eval")
    async def tv_eval(body: dict[str, Any]):
        """Evaluate JS on the TradingView tab. TV isn't a 'provider' so it has
        no workflow — find the tab by URL substring instead. Useful for
        peeking at userscript console state from outside the browser.
        """
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        for page in browser.context.pages:
            try:
                if "tradingview.com" in (page.url or ""):
                    try:
                        result = await page.evaluate(body["js"])
                    except Exception as e:
                        return {"error": f"eval failed: {e}"}
                    return {"url": page.url, "result": result}
            except Exception:
                continue
        return {"error": "no tradingview tab"}

    @router.get("/status")
    async def get_status():
        """Return current browser status: running flag, tab count, open pages."""
        return browser.get_status()

    @router.post("/start")
    async def start_browser():
        """Launch the mirror browser. Idempotent — safe to call when already running.

        Eagerly opens tabs for the 4 unlimited counter providers (pinnacle, polymarket,
        cloudbet, kalshi) so the user can log into each in one pass. They stay open for
        the session and serve as on-demand counter legs for arb opps. Idempotent —
        re-calling /start is safe; existing tabs are reused.

        Also performs a defensive sweep: any tab whose URL doesn't match an allowed
        domain (the 4 unlimited + tradingview + about:blank) gets closed. Catches
        the case where Chromium async-restored a stray tab (dbet, etc.) past the
        browser's startup grace window.
        """
        await browser.start()
        # Defensive sweep BEFORE re-opening unlimited tabs so we don't end up
        # with both a stray dbet AND a fresh cloudbet (Chromium can refuse to
        # open a duplicate tab if it already has one for the same provider).
        _ALLOWED = (
            "polymarket.com",
            "pinnacle.se",
            "cloudbet.com",
            "kalshi.com",
            "tradingview.com",
            "127.0.0.1",
            "localhost",
        )
        if browser.context:
            for page in list(browser.context.pages):
                try:
                    url = (page.url or "").lower()
                    if not url or url == "about:blank" or url.startswith("chrome:"):
                        continue
                    if any(d in url for d in _ALLOWED):
                        continue
                    await page.close()
                    print(f"[mirror/start] Closed stray tab: {url[:80]}", flush=True)
                except Exception:
                    pass
        for pid in ("pinnacle", "polymarket", "cloudbet", "kalshi"):
            try:
                workflow = get_workflow(pid)
                if not workflow.domain:
                    continue
                if browser.context:
                    already = any(workflow.domain in (p.url or "") for p in browser.context.pages)
                    if already:
                        continue
                    await browser.open_tab(workflow.home_url)
            except Exception:
                continue
        return browser.get_status()

    @router.post("/stop")
    async def stop_browser():
        """Stop the mirror browser and close all tabs."""
        await browser.stop()
        return browser.get_status()

    @router.post("/navigate")
    async def navigate(req: NavigateRequest):
        """Navigate the provider's tab to the event for a pending bet."""
        if not browser.running:
            raise HTTPException(status_code=400, detail="Mirror browser is not running")

        workflow = get_workflow(req.provider_id)
        context = browser.context
        page = await workflow.find_tab(context)
        if page is None:
            raise HTTPException(
                status_code=404,
                detail=f"No open tab found for provider '{req.provider_id}' (domain: {workflow.domain})",
            )

        # Build a lightweight bet-like object the workflow can consume.
        class _Bet:
            pass

        bet = _Bet()
        for field, value in req.model_dump().items():
            setattr(bet, field, value)

        success = await workflow.navigate_to_event(page, bet)
        return {"success": success, "url": page.url}

    @router.post("/place")
    async def place_bet(req: PlaceRequest):
        """Place a pending bet by bet_id via the provider's workflow."""
        if not browser.running:
            raise HTTPException(status_code=400, detail="Mirror browser is not running")

        workflow = get_workflow(req.provider_id)
        context = browser.context
        page = await workflow.find_tab(context)
        if page is None:
            raise HTTPException(
                status_code=404,
                detail=f"No open tab found for provider '{req.provider_id}' (domain: {workflow.domain})",
            )

        # Fetch the bet from DB / service layer if available; fall back to a
        # minimal shim so the workflow still receives a typed object.
        try:
            from ...services.bet_service import BetService  # type: ignore

            bet = await BetService.get_by_id(req.bet_id)
        except Exception:

            class _Bet:
                pass

            bet = _Bet()
            bet.id = req.bet_id

        stake = getattr(bet, "stake", None)
        result = await workflow.place_bet(page, bet, stake)
        return {
            "status": result.status,
            "bet_id": result.bet_id,
            "actual_odds": result.actual_odds,
            "actual_stake": result.actual_stake,
            "reason": result.reason,
        }

    @router.post("/open-tab")
    async def open_tab(req: OpenTabRequest):
        """Open a new browser tab navigated to the given URL.

        Auto-starts the mirror Chromium if it isn't running yet. The
        boot-time auto-open task in server.py also tries to start it,
        but the user may hit this button before that fires (or after a
        Chromium crash). One source of truth: this endpoint always
        works regardless of which path got us here.
        """
        if not browser.running:
            try:
                await browser.start()
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to start mirror browser: {exc}",
                ) from exc
        page = await browser.open_tab(req.url)
        return {"url": page.url}

    # -----------------------------------------------------------------------
    # Play loop
    # -----------------------------------------------------------------------

    @router.post("/play/start")
    async def play_start(req: PlayStartRequest):
        """Load a batch of bets and start the play loop."""
        pids = req.provider_ids or ([req.provider_id] if req.provider_id else [])
        play_loop.load_batch(req.batch, req.balances, provider_ids=pids)
        play_loop.start()
        return play_loop.get_status()

    @router.post("/play/confirm-settlements")
    async def play_confirm_settlements(body: dict[str, Any] | None = None):
        """Confirm the settlement breakdown and proceed to bets.

        Body (optional): {confirmed: [{bet_id, result, payout}, ...]}
        If provided, only confirmed settlements are recorded to DB.
        """
        confirmed = (body or {}).get("confirmed")
        play_loop.confirm_settlements(confirmed)
        return play_loop.get_status()

    @router.post("/play/place")
    async def play_place(body: dict[str, Any] | None = None):
        """Confirm placement of the current bet in the play loop."""
        pid = (body or {}).get("provider_id")
        play_loop.place(provider_id=pid)
        return play_loop.get_status()

    @router.post("/play/skip")
    async def play_skip(body: dict[str, Any] | None = None):
        """Skip the current bet in the play loop."""
        pid = (body or {}).get("provider_id")
        play_loop.skip(provider_id=pid)
        return play_loop.get_status()

    @router.post("/play/stop")
    async def play_stop():
        """Stop the play loop."""
        play_loop.stop()
        return play_loop.get_status()

    @router.get("/play/status")
    async def play_status():
        """Return current play loop status."""
        return play_loop.get_status()

    # -----------------------------------------------------------------------
    # Data streams (per-provider continuous polling)
    # -----------------------------------------------------------------------

    @router.post("/data-stream/start/{provider_id}")
    async def start_data_stream(provider_id: str):
        """Start continuous data polling for a provider (balance, positions, history)."""
        from . import stream_registry
        from .data_stream import ProviderDataStream

        existing = stream_registry.get(provider_id)
        if existing and existing.running:
            return existing.get_status()

        if not browser.running or not browser.context:
            raise HTTPException(status_code=400, detail="Mirror browser is not running")

        wf = get_workflow(provider_id)
        page = await wf.find_tab(browser.context)
        if page is None:
            raise HTTPException(status_code=404, detail=f"No open tab for {provider_id}")

        stream = ProviderDataStream(provider_id, wf, page, broadcaster, proxy_url)
        stream.start()
        return stream.get_status()

    @router.post("/data-stream/stop/{provider_id}")
    async def stop_data_stream(provider_id: str):
        """Stop the data stream for a provider."""
        from . import stream_registry

        stream = stream_registry.get(provider_id)
        if not stream:
            return {"provider_id": provider_id, "running": False}
        stream.stop()
        return {"provider_id": provider_id, "running": False}

    @router.get("/pending/status")
    async def pending_status():
        """Return status of the background pending settlement loop."""
        return pending_loop.get_status()

    @router.get("/data-stream/status")
    async def data_stream_status():
        """Return status of all active data streams."""
        from . import stream_registry

        streams = stream_registry.get_all()
        return {
            "streams": {pid: s.get_status() for pid, s in streams.items()},
            "count": len(streams),
        }

    @router.get("/data-stream/debug/{provider_id}")
    async def debug_data_stream(provider_id: str):
        """Test each API call for a provider and return raw results."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        wf = get_workflow(provider_id)
        page = await wf.find_tab(browser.context)
        if not page:
            return {"error": "no tab found"}

        results: dict = {"provider_id": provider_id, "page_url": page.url}

        # Raw API test — see what the fetch actually returns
        bal_url = f"https://{wf.domain}/sv/api/v3/account/balance"
        raw_bal = await page.evaluate(
            f"""async () => {{
            try {{
                const r = await fetch("{bal_url}", {{credentials: "include"}});
                return {{status: r.status, ok: r.ok, body: r.ok ? await r.json() : await r.text()}};
            }} catch(e) {{ return {{error: e.message}}; }}
        }}"""
        )
        results["raw_balance_api"] = raw_bal

        hist_url = f"https://sb2frontend-altenar2.biahosted.com/api/widget/widgetBetHistory?integration={wf._integration}&status=settled&page=1&pageSize=5"
        raw_hist = await page.evaluate(
            f"""async () => {{
            try {{
                const r = await fetch("{hist_url}", {{credentials: "include"}});
                return {{status: r.status, ok: r.ok, body: r.ok ? await r.json() : await r.text()}};
            }} catch(e) {{ return {{error: e.message}}; }}
        }}"""
        )
        results["raw_history_api"] = {
            "status": raw_hist.get("status"),
            "ok": raw_hist.get("ok"),
            "error": raw_hist.get("error"),
            "keys": list(raw_hist.get("body", {}).keys())
            if isinstance(raw_hist.get("body"), dict)
            else str(type(raw_hist.get("body"))),
        }

        # Probe shadow DOM structure
        dom_probe = await page.evaluate("""() => {
            const stb = document.querySelector('STB-SPORTSBOOK');
            // Also look for iframes or other custom elements
            const iframes = document.querySelectorAll('iframe');
            const customs = document.querySelectorAll('*');
            const customEls = [];
            for (const el of customs) {
                if (el.tagName.includes('-') && !el.tagName.startsWith('FONT')) {
                    customEls.push(el.tagName);
                }
            }
            // Check for shadow roots on any element
            const shadowHosts = [];
            for (const el of customs) {
                if (el.shadowRoot) shadowHosts.push(el.tagName);
            }
            if (!stb) return {
                stb: false,
                url: location.href,
                iframes: Array.from(iframes).map(f => f.src?.substring(0, 100)),
                customElements: [...new Set(customEls)].slice(0, 20),
                shadowHosts: [...new Set(shadowHosts)].slice(0, 10),
                bodyText: document.body?.innerText?.substring(0, 300),
            };
            const fc = stb.firstElementChild;
            if (!fc) return {stb: true, firstChild: false};
            const sr = fc.shadowRoot;
            return {
                stb: true, firstChild: true,
                firstChildTag: fc.tagName, shadowRoot: !!sr,
                children: Array.from(stb.children).map(c => ({
                    tag: c.tagName, shadow: !!c.shadowRoot,
                })),
            };
        }""")
        results["dom_probe"] = dom_probe

        # Test balance
        try:
            bal = await wf.sync_balance(page)
            results["balance"] = {"value": bal, "ok": bal >= 0}
        except Exception as e:
            results["balance"] = {"error": str(e)}

        # Test positions
        try:
            pos = await wf.fetch_positions(page)
            results["positions"] = {
                "count": len(pos),
                "items": [{"event": p.event_name, "odds": p.odds, "stake": p.stake} for p in pos[:5]],
            }
        except Exception as e:
            results["positions"] = {"error": str(e)}

        # Test history
        try:
            hist = await wf.sync_history(page)
            results["history"] = {
                "count": len(hist),
                "items": [
                    {"event": h.event_name, "status": h.status, "odds": h.odds, "stake": h.stake} for h in hist[:5]
                ],
            }
        except Exception as e:
            results["history"] = {"error": str(e)}

        return results

    # -----------------------------------------------------------------------
    # Debug: Playwright-native introspection (accessibility tree, locators)
    # -----------------------------------------------------------------------

    @router.post("/browser/click/{provider_id}")
    async def browser_click(provider_id: str, body: dict[str, Any]):
        """Click at element-relative offset on a provider's WASM widget.

        body: {x, y} — offset relative to #STB_SPORTSBOOK > div
        Tries multiple click methods: locator, mouse, and CDP.
        """
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        wf = get_workflow(provider_id)
        page = await wf.find_tab(browser.context)
        if not page:
            return {"error": "no tab found"}

        ox, oy = body.get("x", 0), body.get("y", 0)

        # Get element position to convert offset to viewport coords
        rect = await page.evaluate("""() => {
            const el = document.querySelector('#STB_SPORTSBOOK > div');
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height};
        }""")
        if not rect:
            return {"error": "STB_SPORTSBOOK div not found"}

        vx = rect["x"] + ox
        vy = rect["y"] + oy

        method = body.get("method", "cdp")
        result = {"ox": ox, "oy": oy, "vx": vx, "vy": vy, "method": method}

        try:
            if method == "locator":
                await page.locator("#STB_SPORTSBOOK > div").click(position={"x": ox, "y": oy}, timeout=5000)
            elif method == "mouse":
                await page.mouse.click(vx, vy)
            else:
                # CDP: send raw input events at the browser level
                cdp = await page.context.new_cdp_session(page)
                for etype in ["mousePressed", "mouseReleased"]:
                    await cdp.send(
                        "Input.dispatchMouseEvent",
                        {
                            "type": etype,
                            "x": vx,
                            "y": vy,
                            "button": "left",
                            "clickCount": 1,
                        },
                    )
                await cdp.detach()
            result["clicked"] = True
        except Exception as e:
            result["error"] = str(e)[:200]

        return result

    # -----------------------------------------------------------------------
    # SSE stream
    # -----------------------------------------------------------------------

    @router.get("/stream")
    async def mirror_stream(request: Request):
        """Server-sent events stream for real-time mirror updates."""
        from sse_starlette.sse import EventSourceResponse

        client_id, queue = broadcaster.subscribe()

        async def generator():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                        yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                    except asyncio.TimeoutError:
                        yield {"event": "heartbeat", "data": ""}
            except asyncio.CancelledError:
                pass
            finally:
                broadcaster.unsubscribe(client_id)

        return EventSourceResponse(generator(), ping=15)

    return router
