"""Mirror router — browser control and bet placement endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from .browser import MirrorBrowser
from .pending_loop import _AUTH_HEADER, _AUTH_VALUE, PendingLoop
from .play_loop import PlayLoop
from .sse import MirrorBroadcaster
from .workflows import get_workflow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _user_picked_opp persistence
# ---------------------------------------------------------------------------
# In-memory _user_picked_opp[provider_id] is set when the user clicks "play"
# in the UI (arb or value). It carries event_id/market/outcome/start_time so
# downstream bet-recording paths (play_loop._record_manual_bet, pending_loop.
# _record_unknown_open_bets) can populate those fields when the provider's
# placement response doesn't expose them. Without persistence, ANY bet placed
# in session A and recorded via reactive sync in session B loses event_id ->
# the row ends up in the "unknown" sport bucket with no edge/CLV analysis.
#
# JSON file in arnold/data/ alongside the rest of the local state. 24h TTL
# discards stale picks (anything older is irrelevant; a placement that hasn't
# settled in 24h is a separate problem).
_PICKED_OPP_TTL_SEC = 24 * 3600
_PICKED_OPP_PATH = Path(__file__).resolve().parent.parent / "data" / "picked_opps.json"


def _load_picked_opps() -> dict[str, dict]:
    """Load persisted picks, dropping any past TTL. Safe on missing/corrupt file."""
    if not _PICKED_OPP_PATH.exists():
        return {}
    try:
        raw = json.loads(_PICKED_OPP_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[picked_opps] failed to load {_PICKED_OPP_PATH}: {e}")
        return {}
    now = time.time()
    fresh: dict[str, dict] = {}
    for pid, entry in (raw or {}).items():
        if not isinstance(entry, dict):
            continue
        ts = entry.get("_picked_ts", 0)
        if now - ts > _PICKED_OPP_TTL_SEC:
            continue
        fresh[pid] = entry
    return fresh


def _persist_picked_opps(picked: dict[str, dict]) -> None:
    """Write picks to disk. Best-effort; logs but doesn't raise."""
    try:
        _PICKED_OPP_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PICKED_OPP_PATH.write_text(json.dumps(picked, default=str))
    except OSError as e:
        logger.warning(f"[picked_opps] failed to write {_PICKED_OPP_PATH}: {e}")


def _set_picked_opp(browser: Any, provider_id: str, payload: dict) -> None:
    """Set the picked-opp context for a provider and persist to disk.

    Used by both /arb/navigate-opp (arb anchor click) and /navigate (single
    value-bet click) so downstream recording can fill event_id/market/outcome.
    """
    if not hasattr(browser, "_user_picked_opp"):
        browser._user_picked_opp = {}
    payload = dict(payload)
    payload["_picked_ts"] = time.time()
    browser._user_picked_opp[provider_id] = payload
    _persist_picked_opps(browser._user_picked_opp)


def _restore_picked_opps(browser: Any) -> None:
    """Restore picks from disk on first browser-use after startup."""
    if hasattr(browser, "_user_picked_opp"):
        return
    browser._user_picked_opp = _load_picked_opps()
    if browser._user_picked_opp:
        logger.info(
            f"[picked_opps] restored {len(browser._user_picked_opp)} picks from disk: "
            f"{list(browser._user_picked_opp.keys())}"
        )


# ---------------------------------------------------------------------------
# Live-odds → DB sync
# ---------------------------------------------------------------------------
# In-memory cache of last value pushed per leg, used to debounce: only POST
# when the odds actually change (avoids hammering /api/odds/live-update with
# every 1s poll tick when the price hasn't moved). Bounded growth not a
# concern — the user has at most ~10 active legs at once.
_LAST_PUSHED_ODDS: dict[tuple[str, str, str, str, float | None], float] = {}

# Same idea for DOM-scraped balances. Keyed by provider_id → last value we
# POSTed to /api/bankroll/set. Debouncing on provider_data's cached balance
# was wrong: that cache can already hold the live value (from a poll that
# predates this process) while the server DB is still stale, so a delta
# check against it blocks the initial persist forever. Tracking what we
# actually pushed is the only correct gate.
_LAST_PUSHED_BALANCE: dict[str, float] = {}


def _persist_live_odds(
    provider_id: str,
    event_id: str | None,
    market: str,
    outcome: str,
    point: float | None,
    odds: float,
) -> None:
    """Fire-and-forget POST to /api/odds/live-update so the next /arb-workflow
    scan returns the live-updated value. Without this, the mirror's live
    observations live only in the frontend in-memory overlay — on refresh
    or other devices the user sees stale extraction-time odds.

    Skipped when (provider_id, event_id, market, outcome, point) hasn't
    changed value since last push — keeps the tunnel quiet during quiet
    markets.
    """
    if (
        not event_id
        or not market
        or not outcome
        or not isinstance(odds, (int, float))
        or odds <= 1
    ):
        return
    key = (provider_id, event_id, market, outcome, point)
    last = _LAST_PUSHED_ODDS.get(key)
    if last is not None and abs(last - odds) < 0.005:
        return
    _LAST_PUSHED_ODDS[key] = float(odds)
    import asyncio as _asyncio

    async def _do_push():
        try:
            from local.http_client import tunnel_client

            await tunnel_client().post(
                "/api/odds/live-update",
                json={
                    "provider_id": provider_id,
                    "event_id": event_id,
                    "market": market,
                    "outcome": outcome,
                    "point": point,
                    "odds": float(odds),
                    "source": "mirror",
                },
                timeout=5.0,
            )
        except Exception as e:
            logger.debug(
                f"[live-odds DB push] {provider_id} {event_id} {market}/{outcome}: {e!r}"
            )

    try:
        _asyncio.ensure_future(_do_push())
    except RuntimeError:
        pass


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
    # Provider-specific metadata (event_slug, matchup_id, token_id, etc.).
    # Forwarded into the bet object so workflows whose URL template requires
    # a slug — Polymarket "/event/{event_slug}", Pinnacle matchup_id — can
    # resolve the event page. Without this, /mirror/navigate lands on the
    # provider's lobby (the value-bet click nav bug, 2026-05-15).
    provider_meta: dict | None = None


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


def create_mirror_router(
    browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str
) -> APIRouter:
    """Return an APIRouter with mirror browser control and placement endpoints."""

    router = APIRouter(prefix="/mirror", tags=["mirror"])

    play_loop = PlayLoop(browser, broadcaster, proxy_url)
    # PendingLoop is intentionally NOT started. Per the auto-nav invariant
    # the mirror is hands-off on everything except arb event-clicks — the
    # user manually navigates to provider history pages and the browser
    # interceptor catches the response. The interceptor → history_synced
    # SSE → reactive_sync helper below records any unknown pending bets +
    # reconciles settlements. Kept the instance so we can still reuse its
    # helpers (_record_unknown_open_bets / reconcile) from the reactive path.
    pending_loop = PendingLoop(browser, broadcaster, proxy_url)

    # Wire browser bet interception → play loop auto-record
    # Chain with existing callback (broadcaster.publish set in server.py)
    _prev_callback = browser._on_event

    async def _post_balance_async(provider_id: str, balance: float):
        from local.http_client import tunnel_client

        try:
            client = tunnel_client()
            await client.post(
                f"/api/bankroll/set/{provider_id}",
                json={"balance": balance},
                timeout=10.0,
            )
        except Exception as e:
            print(f"[balance-sync] {provider_id} POST failed: {e!r}", flush=True)

    # Strong refs for fire-and-forget balance POSTs. asyncio.ensure_future
    # returns a Task the event loop only weakly tracks — with no reference
    # held, it can be GC'd before it runs (the silent-drop bug class). Keep
    # each task in this set until it completes.
    _pending_balance_tasks: set = set()

    def _fire_balance_post(provider_id: str, balance: float) -> None:
        import asyncio

        try:
            task = asyncio.ensure_future(_post_balance_async(provider_id, balance))
        except RuntimeError:
            return
        _pending_balance_tasks.add(task)
        task.add_done_callback(_pending_balance_tasks.discard)

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
        if event_type == "odds_states_intercepted":
            pid = data.get("provider_id", "")
            body = data.get("body")
            if pid and body:
                from .workflows import get_workflow

                wf = get_workflow(pid)
                if hasattr(wf, "update_odds_states"):
                    try:
                        touched = wf.update_odds_states(body)
                        if touched:
                            print(
                                f"[odds_states] {pid} merged {touched} odd updates",
                                flush=True,
                            )
                    except Exception as e:
                        print(f"[odds_states] {pid} merge raised: {e!r}", flush=True)
                # Extract {outcome_id: price} flat map and broadcast so the
                # betty UI can update displayed leg odds in real time without
                # waiting for a server-side re-scan. Match by leg.provider_meta.
                # outcome_id (Altenar's odd id) which the scanner stamps into
                # every leg it emits.
                try:
                    updates: dict[str, float] = {}
                    states = (
                        body.get("oddStates")
                        or body.get("OddStates")
                        or body.get("odds")
                        or []
                    )
                    if isinstance(body, list):
                        states = body
                    if isinstance(states, list):
                        for s in states:
                            if not isinstance(s, dict):
                                continue
                            oid = (
                                s.get("id")
                                or s.get("Id")
                                or s.get("oddId")
                                or s.get("OddId")
                            )
                            price = s.get("price") or s.get("Price")
                            if oid is None or price is None:
                                continue
                            try:
                                updates[str(oid)] = float(price)
                            except (TypeError, ValueError):
                                continue
                    if updates:
                        broadcaster.publish(
                            "live_provider_odds",
                            {
                                "provider_id": pid,
                                "updates": updates,
                            },
                        )
                except Exception as e:
                    print(f"[odds_states] broadcast extract failed: {e!r}", flush=True)
        if event_type == "balance_intercepted":
            pid = data.get("provider_id", "")
            bal = data.get("balance")
            if pid and bal is not None:
                _fire_balance_post(pid, bal)
        # Reactive history sync — fires when the user manually navigates the
        # provider tab to its history/positions page. Replaces the deleted
        # PendingLoop 60s tick. We grab whatever sync_history returns (workflow
        # decides: cache read for Gecko, authed-fetch for Altenar, DOM scrape
        # for Polymarket) and pipe it through reconcile + _record_unknown_open_bets.
        #
        # Debounce here SYNCHRONOUSLY (before spawning the async task) — pre-
        # 2026-05-11 the debounce check lived inside _reactive_history_sync,
        # which meant 3 history_intercepted events in <1 s spawned 3 tasks
        # that all read the same (stale) timestamp before any wrote, all
        # passed the gate, and each inserted a duplicate bet (the Polymarket
        # × 4 dup we just cleaned up). Doing the check in this sync handler
        # before `ensure_future` makes the check-and-set atomic from the
        # event-loop's POV: the SSE callbacks fire serially on the loop.
        if event_type == "history_intercepted":
            pid = data.get("provider_id", "")
            print(f"[history_intercepted] pid={pid}", flush=True)
            if pid:
                import asyncio
                import time as _time

                debouncer = getattr(browser, "_reactive_sync_debouncer", None) or {}
                now = _time.monotonic()
                last = debouncer.get(pid, 0.0)
                if now - last < 5.0:
                    print(
                        f"[history_intercepted] {pid} debounced (last={now - last:.2f}s ago)",
                        flush=True,
                    )
                    return
                debouncer[pid] = now
                browser._reactive_sync_debouncer = debouncer
                try:
                    asyncio.ensure_future(_reactive_history_sync(pid))
                    print(f"[history_intercepted] {pid} sync task spawned", flush=True)
                except RuntimeError as exc:
                    print(
                        f"[history_intercepted] {pid} ensure_future RuntimeError: {exc!r}",
                        flush=True,
                    )

    # Per-provider lock so concurrent intercepts can't double-record even
    # if they slip past the 5s callback debounce (network glitch, manual
    # /mirror/start, etc.). The debounce makes "duplicate fires within 5s"
    # impossible; this lock handles the rare > 5s scenario where two syncs
    # for the same provider would otherwise read db_pending in parallel and
    # both decide "this position isn't in DB yet".
    _reactive_sync_locks: dict[str, asyncio.Lock] = {}

    async def _reactive_history_sync(provider_id: str) -> None:
        """Fired by the browser interceptor whenever a history response is cached.

        Pulls the workflow's sync_history (passive read of cached data), runs
        reconcile against DB pending, and inserts unknown pending entries.
        Replaces the polling PendingLoop — recovery only happens when the user
        chooses to look at their history page. Debounce lives in the sync
        callback above so concurrent intercepts can't race past the gate.

        Broadcasts settling_pending → settling_done so the UI shows a
        transient "scanning pending..." badge for the active sync, then
        clears it. Without this the badge stays stuck on whatever the play
        runner last reported (which can be stale for hours).
        """
        import asyncio as _asyncio

        from .workflows import get_workflow

        print(f"[reactive_sync] {provider_id} start", flush=True)
        lock = _reactive_sync_locks.get(provider_id)
        if lock is None:
            lock = _asyncio.Lock()
            _reactive_sync_locks[provider_id] = lock
        if lock.locked():
            print(
                f"[reactive_sync] {provider_id} another sync in flight — skipping",
                flush=True,
            )
            return

        # Only fire the "scanning pending..." badge when the user is actually
        # looking at a history/positions page. Bookmaker widgets often hit
        # their bet-history endpoint to render a pending-count chip in the
        # header (Betinia's `widgetbethistory`, Polymarket's positions API)
        # on EVERY page — without this gate the badge would flash up every
        # few seconds while the user is just browsing the lobby. The sync
        # itself still runs in the background; only the UI signal is gated.
        recorded = 0
        reconciled = 0
        page_url = ""
        try:
            async with lock:
                try:
                    workflow = get_workflow(provider_id)
                    page = await workflow.find_tab(browser.context)
                    if page is None:
                        print(
                            f"[reactive_sync] {provider_id} no tab found — skipping",
                            flush=True,
                        )
                        return
                    page_url = (page.url or "").lower()
                    on_history_page = any(
                        kw in page_url
                        for kw in (
                            "history",
                            "portfolio",
                            "spelhistorik",
                            "betting/history",
                            "mybets",
                            "journal/bets",
                            "minaspel",
                            "mina-spel",
                            "positions",
                        )
                    )
                    if on_history_page:
                        broadcaster.publish(
                            "settling_pending",
                            {"provider_id": provider_id, "source": "reactive"},
                        )
                    print(
                        f"[reactive_sync] {provider_id} calling sync_history on {page_url[:60]}",
                        flush=True,
                    )
                    history = await workflow.sync_history(page)
                    print(
                        f"[reactive_sync] {provider_id} sync_history returned {len(history) if history else 0} entries",
                        flush=True,
                    )
                    if not history:
                        return
                    history_dicts = [
                        {
                            "odds": e.odds,
                            "stake": e.stake,
                            "status": e.status,
                            "payout": e.payout,
                            "provider_bet_id": e.provider_bet_id,
                            "event_name": e.event_name,
                        }
                        for e in history
                    ]
                    # Fetch current DB pending for reconcile. None = fetch
                    # failed — fail-closed: skip BOTH reconcile and record.
                    # Recording against an unknown DB state re-inserts every
                    # open bet as a duplicate (BETINIA ×3 dup bug, 2026-05-12).
                    db_pending = await pending_loop._fetch_pending_for_provider(
                        provider_id
                    )
                    if db_pending is None:
                        print(
                            f"[reactive_sync] {provider_id} db_pending fetch failed — "
                            "skipping reconcile+record this cycle",
                            flush=True,
                        )
                        return
                    # Reconcile (matches DB pending against settled history entries)
                    from .reconcile import reconcile_and_publish

                    reconciled = await reconcile_and_publish(
                        pending_loop._proxy_url,
                        _AUTH_HEADER,
                        _AUTH_VALUE,
                        provider_id,
                        db_pending,
                        history_dicts,
                        broadcaster,
                        page=page,
                        workflow=workflow,
                    )
                    # Insert pending entries that aren't in the DB yet
                    await pending_loop._record_unknown_open_bets(
                        provider_id, history_dicts, db_pending
                    )
                except Exception as exc:
                    print(f"[reactive_sync] {provider_id} raised: {exc!r}", flush=True)
        finally:
            broadcaster.publish(
                "settling_done",
                {
                    "provider_id": provider_id,
                    "source": "reactive",
                    "reconciled": reconciled or 0,
                    "recorded": recorded,
                },
            )

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
        """Open a provider's site in a tab (idempotent).

        Checks every page in the context for a live, http(s) tab whose host
        matches the workflow's domain. If found, just brings it to front and
        returns 'already_open'. Otherwise spawns a new tab. Skips closed pages
        and non-http URLs (about:blank, chrome:// etc.) to avoid the silent
        "tab exists but actually died" trap.
        """
        body = await request.json()
        pid = body.get("provider_id", "")
        if not pid:
            raise HTTPException(400, "provider_id required")
        if not browser.running:
            await browser.start()
        from ._urls import hostname_matches

        workflow = get_workflow(pid)
        if browser.context and workflow.domain:
            for page in browser.context.pages:
                try:
                    if page.is_closed():
                        continue
                    url = (page.url or "").lower()
                    if not url.startswith(("http://", "https://")):
                        continue
                    if hostname_matches(workflow.domain, url):
                        try:
                            await page.bring_to_front()
                        except Exception:
                            pass
                        return {
                            "status": "already_open",
                            "url": page.url,
                            "provider_id": pid,
                        }
                except Exception:
                    continue
        domain = workflow.domain
        if not domain:
            raise HTTPException(400, f"No domain for provider {pid}")
        page = await browser.open_tab(workflow.home_url)
        try:
            await page.bring_to_front()
        except Exception:
            pass
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

    # Per-provider portfolio URL — pages whose response triggers the
    # history/positions interceptor → reactive sync cascade. For sites
    # whose primary placement path bypasses HTTP intercept (Polymarket CLOB
    # via WebSocket), this is the only reliable way to surface a freshly-
    # placed bet without the user manually clicking "Portfolio".
    _PORTFOLIO_URL = {
        "polymarket": "https://polymarket.com/portfolio?tab=positions",
        "kalshi": "https://kalshi.com/portfolio",
        "cloudbet": "https://www.cloudbet.com/en/my-bets",
        "pinnacle": "https://www.pinnacle.se/sv/account/bets",
    }

    @router.post("/sync-positions/{provider_id}")
    async def sync_positions(provider_id: str):
        """API-based bet recorder for kalshi + cookie-based pinnacle/cloudbet.

        - kalshi:            authenticated trade-api.kalshi.com/portfolio/positions
                             (requires KALSHI_API_KEY + KALSHI_PRIVATE_KEY env vars)
        - pinnacle/cloudbet: reuse the browser session via page.evaluate(fetch)

        Polymarket is recorded server-side 24/7 (backend/src/recorders/
        server_poller.py) — it is no longer handled here.

        Idempotent — dedup against existing DB rows via provider_bet_id +
        (event_id, outcome). Safe to call repeatedly.
        """
        from local.http_client import tunnel_client

        async def api_post(payload: dict):
            return await tunnel_client().post("/api/bets", json=payload, timeout=10.0)

        async def fetch_events() -> list[dict]:
            try:
                # upcoming_only=true — without it the endpoint returns oldest
                # events first and limit=2000 still cuts today's matches.
                r = await tunnel_client().get(
                    "/api/events?limit=2000&upcoming_only=true",
                    timeout=10.0,
                )
                if r.status_code == 200:
                    return r.json().get("events", []) or []
            except Exception as exc:
                print(f"[sync-positions] fetch_events raised: {exc!r}", flush=True)
            return []

        async def fetch_db_pending() -> list[dict]:
            return await pending_loop._fetch_pending_for_provider(provider_id) or []

        async def fetch_known_ids() -> list[str] | None:
            """All provider_bet_id values ever recorded for this provider
            (any result) — the dedup source for the position recorders.
            Returns None on failure so the recorder fails closed instead of
            re-inserting every open position against an unknown dedup state."""
            try:
                r = await tunnel_client().get(
                    "/api/bets/recorded-ids",
                    params={"provider_id": provider_id},
                    timeout=30.0,
                )
                r.raise_for_status()
                return r.json().get("provider_bet_ids", []) or []
            except Exception as exc:
                print(f"[sync-positions] fetch_known_ids raised: {exc!r}", flush=True)
                return None

        settle_summary: dict | None = None

        async def api_settle(bet_id: int, res: str, payout: float):
            return await tunnel_client().put(
                f"/api/bets/{bet_id}",
                json={"result": res, "payout": payout},
                timeout=10.0,
            )

        if provider_id == "kalshi":
            from .recorders import kalshi_api

            result = await kalshi_api.sync(
                api_post,
                fetch_events,
                fetch_db_pending,
                fetch_known_ids=fetch_known_ids,
            )
            try:
                settle_summary = await kalshi_api.settle(api_settle, fetch_db_pending)
            except Exception as exc:
                print(f"[sync-positions] kalshi settle raised: {exc!r}", flush=True)
                settle_summary = {"won": 0, "lost": 0, "errors": [repr(exc)]}
        elif provider_id in ("pinnacle", "cloudbet"):
            # Pinnacle + Cloudbet have authenticated REST APIs but their session
            # cookies live in the Playwright browser context. We reuse the
            # strategy's sync_history (which calls page.evaluate(fetch) → the
            # browser sends cookies automatically) WITHOUT navigating the tab.
            # That way the auto-poller doesn't disrupt anything the user is
            # actively doing on the provider's site.
            from .reconcile import reconcile_and_publish

            result = type("R", (), {})()
            result.provider_id = provider_id
            result.fetched = 0
            result.inserted = 0
            result.skipped_dup = 0
            result.skipped_unmatched = 0
            result.errors = []

            if not browser.running or not browser.context:
                settle_summary = {
                    "won": 0,
                    "lost": 0,
                    "errors": [
                        "browser not running — cookie-based providers need an active context"
                    ],
                }
            else:
                workflow = get_workflow(provider_id)
                page = await workflow.find_tab(browser.context)
                if page is None:
                    settle_summary = {
                        "won": 0,
                        "lost": 0,
                        "errors": [
                            f"no open tab for {provider_id} — open the provider once to authenticate"
                        ],
                    }
                else:
                    try:
                        raw_history = await workflow.sync_history(page) or []
                        history_dicts = [
                            {
                                "odds": e.odds,
                                "stake": e.stake,
                                "status": e.status,
                                "payout": e.payout,
                                "provider_bet_id": e.provider_bet_id,
                                "event_name": e.event_name,
                            }
                            for e in raw_history
                        ]
                        result.fetched = len(history_dicts)

                        db_pending = await pending_loop._fetch_pending_for_provider(
                            provider_id
                        )
                        if db_pending is not None:
                            synced = await reconcile_and_publish(
                                pending_loop._proxy_url,
                                _AUTH_HEADER,
                                _AUTH_VALUE,
                                provider_id,
                                db_pending,
                                history_dicts,
                                broadcaster,
                                page=page,
                                workflow=workflow,
                            )
                            pre_count = len(db_pending)
                            await pending_loop._record_unknown_open_bets(
                                provider_id, history_dicts, db_pending
                            )
                            post = await pending_loop._fetch_pending_for_provider(
                                provider_id
                            )
                            if post is not None:
                                result.inserted = max(0, len(post) - pre_count)
                            # reconcile_and_publish returns settled count via SSE deltas
                            settle_summary = {
                                "won": 0,
                                "lost": 0,
                                "reconciled": synced,
                                "errors": [],
                            }
                    except Exception as exc:
                        print(
                            f"[sync-positions] {provider_id} sync raised: {exc!r}",
                            flush=True,
                        )
                        settle_summary = {"won": 0, "lost": 0, "errors": [repr(exc)]}
        else:
            raise HTTPException(400, f"sync-positions not supported for {provider_id}")

        return {
            "provider_id": result.provider_id,
            "fetched": result.fetched,
            "inserted": result.inserted,
            "skipped_dup": result.skipped_dup,
            "skipped_unmatched": result.skipped_unmatched,
            "settle": settle_summary,
            "errors": result.errors[:10],
        }

    # Providers whose settlement state lives on a SECOND URL (activity / history
    # tab). Polymarket: open positions on ?tab=positions, settled rows on
    # ?tab=history. Without scraping both, anything that resolved and aged off
    # the positions list stays "pending" in our DB indefinitely.
    _SECONDARY_PORTFOLIO_URL = {
        "polymarket": "https://polymarket.com/portfolio?tab=history",
    }

    @router.post("/poll-portfolio/{provider_id}")
    async def poll_portfolio(provider_id: str):
        """Sync a provider's pending bets to the DB. Strategy:

        1. Navigate the tab to the provider's portfolio/bet-history URL so the
           positions/history XHR fires and the interceptor → reactive_sync
           chain records any new bets (works for polymarket / kalshi /
           cloudbet whose pages drive the XHR on render).
        2. ALSO directly invoke workflow.sync_history(page) → reconcile +
           record_unknown_open_bets. Required for pinnacle: its bet-history
           page redirects to /sv/ and never fires the XHR, but the workflow's
           sync_history makes the bets-API call independently via the page's
           request context.

        Idempotent; safe to call repeatedly. Step 2's record path
        deduplicates against existing DB rows via (odds, stake) signature.
        """
        url = _PORTFOLIO_URL.get(provider_id)
        if not url:
            raise HTTPException(400, f"no portfolio URL configured for {provider_id}")
        if not browser.running or not browser.context:
            raise HTTPException(400, "browser not running")
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if page is None:
            raise HTTPException(404, f"no open tab for {provider_id}")

        # Step 1: navigate (best-effort — some providers redirect).
        nav_error: str | None = None
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:
            nav_error = repr(exc)

        # Step 2: sync_history at primary URL (positions for polymarket,
        # bet-history for kalshi/cloudbet/pinnacle).
        raw_history_primary: list = []
        try:
            raw_history_primary = await workflow.sync_history(page) or []
        except Exception as exc:
            print(
                f"[poll-portfolio] {provider_id} primary sync raised: {exc!r}",
                flush=True,
            )

        # Step 3: providers whose settlement state lives on a SECOND URL
        # (polymarket: activity/history tab) — navigate there and merge.
        # Without this, anything that resolved + aged off the positions list
        # stays "pending" in our DB indefinitely.
        raw_history_secondary: list = []
        secondary_url = _SECONDARY_PORTFOLIO_URL.get(provider_id)
        if secondary_url:
            try:
                await page.goto(
                    secondary_url, wait_until="domcontentloaded", timeout=20000
                )
                raw_history_secondary = await workflow.sync_history(page) or []
            except Exception as exc:
                print(
                    f"[poll-portfolio] {provider_id} secondary sync raised: {exc!r}",
                    flush=True,
                )

        raw_history = list(raw_history_primary) + list(raw_history_secondary)

        synced = 0
        recorded = 0
        try:
            history_dicts = [
                {
                    "odds": e.odds,
                    "stake": e.stake,
                    "status": e.status,
                    "payout": e.payout,
                    "provider_bet_id": e.provider_bet_id,
                    "event_name": e.event_name,
                }
                for e in raw_history
            ]
            db_pending = await pending_loop._fetch_pending_for_provider(provider_id)
            if db_pending is not None:
                from .reconcile import reconcile_and_publish

                synced = await reconcile_and_publish(
                    pending_loop._proxy_url,
                    _AUTH_HEADER,
                    _AUTH_VALUE,
                    provider_id,
                    db_pending,
                    history_dicts,
                    broadcaster,
                    page=page,
                    workflow=workflow,
                )
                # Snapshot pending count before insert so we can report new rows.
                pre_count = len(db_pending)
                await pending_loop._record_unknown_open_bets(
                    provider_id, history_dicts, db_pending
                )
                post = await pending_loop._fetch_pending_for_provider(provider_id)
                if post is not None:
                    recorded = max(0, len(post) - pre_count)
        except Exception as exc:
            print(
                f"[poll-portfolio] {provider_id} reconcile raised: {exc!r}", flush=True
            )

        return {
            "navigated": nav_error is None,
            "nav_error": nav_error,
            "url": page.url,
            "history_entries": len(raw_history),
            "history_primary": len(raw_history_primary),
            "history_secondary": len(raw_history_secondary),
            "reconciled": synced,
            "recorded": recorded,
        }

    @router.post("/browser/provider/{provider_id}/clear-cache")
    async def clear_provider_cache(provider_id: str):
        """Clear cached provider_data for a provider — forces the next
        check_login to actually run instead of trusting a stale cache.
        Mostly a debugging tool: once the cache says logged_in=True we
        never re-verify until the cache gets cleared (e.g. by tab close
        or restart). For a long-lived session whose token expired
        server-side this can leave the UI stuck on "green" indefinitely."""
        browser.provider_data.pop(provider_id, None)
        return {"cleared": provider_id}

    @router.get("/browser/provider/{provider_id}")
    async def browser_provider_state(provider_id: str):
        """Live state of a provider — from intercepted/cached data only. Never opens tabs."""
        if not browser.running or not browser.context:
            return {
                "found": False,
                "logged_in": False,
                "balance": None,
                "reason": "browser_not_started",
            }
        # Check if we have a tab for this provider
        workflow = get_workflow(provider_id)
        tab_url = None
        for page in browser.context.pages:
            if workflow.domain and workflow.domain in page.url:
                tab_url = page.url
                break
        if not tab_url:
            return {
                "found": False,
                "logged_in": False,
                "balance": None,
                "domain": workflow.domain,
            }
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
                    # DOM-scraped balances (polymarket "Cash $X", pinnacle
                    # localStorage, etc.) never produce a balance_intercepted
                    # network event, so they were never persisted to the
                    # server DB — /api/bankroll stayed stale forever while the
                    # mirror knew the live value. Push when the value differs
                    # from what we last POSTed (not from provider_data, which
                    # may already hold the live value while the DB is stale).
                    last_pushed = _LAST_PUSHED_BALANCE.get(provider_id)
                    if (
                        last_pushed is None
                        or abs(float(last_pushed) - float(bal)) > 0.01
                    ):
                        _LAST_PUSHED_BALANCE[provider_id] = float(bal)
                        _fire_balance_post(provider_id, bal)
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

    @router.get("/browser/diag/{provider_id}")
    async def diag(provider_id: str):
        """Debug: surface workflow internals so we can tell stale-code from init-failure."""
        workflow = get_workflow(provider_id)
        # Snapshot the keys of provider_data so we can tell which interceptor
        # branches have fired (e.g. is coupon_history_raw populated?). Body
        # values may be huge so we only report keys + sizes here.
        pdata = browser.provider_data.get(provider_id, {}) or {}
        pdata_summary = {}
        for k, v in pdata.items():
            if k == "coupon_history_by_url" and isinstance(v, dict):
                pdata_summary[k] = {
                    "urls": list(v.keys()),
                    "coupons_per_url": {
                        u: len((b.get("data") or {}).get("coupons", []) or [])
                        if isinstance(b, dict)
                        else "?"
                        for u, b in v.items()
                    },
                }
            elif k == "coupon_history_raw" and isinstance(v, dict):
                coupons = (v.get("data") or {}).get("coupons", []) or []
                first = coupons[0] if coupons else {}
                # Capture a few diagnostic fields verbatim — we need to know
                # which key carries settlement status and what shape it has.
                first_sample = {
                    fk: first.get(fk)
                    for fk in (
                        "id",
                        "couponId",
                        "couponStatus",
                        "status",
                        "settlementStatus",
                        "betsStatus",
                        "totalOdds",
                        "stake",
                        "totalPayout",
                        "eventNames",
                        "fullCouponSettlementDate",
                    )
                }
                pdata_summary[k] = {
                    "top_keys": list(v.keys())[:20],
                    "data_keys": list((v.get("data") or {}).keys())[:20]
                    if isinstance(v.get("data"), dict)
                    else None,
                    "coupons_len": len(coupons),
                    "first_coupon_keys": list(first.keys())[:40] if first else None,
                    "first_coupon_sample": first_sample,
                }
            elif isinstance(v, (dict, list)):
                pdata_summary[k] = f"<{type(v).__name__} len={len(v)}>"
            elif isinstance(v, str):
                pdata_summary[k] = v[:80]
            else:
                pdata_summary[k] = v
        out: dict[str, Any] = {
            "class": type(workflow).__name__,
            "module": type(workflow).__module__,
            "autonomous_placement": getattr(workflow, "autonomous_placement", None),
            "provider_data": pdata_summary,
        }
        # Find a live page for this provider; check_login needs one (no SDK fallback any more).
        page = None
        if browser.running and browser.context:
            try:
                page = await workflow.find_tab(browser.context)
            except Exception as e:
                out["find_tab_error"] = str(e)
        out["page_url"] = page.url if page else None
        if page is not None:
            try:
                out["check_login"] = await workflow.check_login(page)
            except Exception as e:
                out["check_login_error"] = str(e)
            # Pinnacle exposes its raw signal dict — surface it so we can
            # see exactly which signal fired (or failed) without re-running
            # the page eval ourselves.
            try:
                from .workflows.strategies.pinnacle import _check_login_signals

                if provider_id == "pinnacle":
                    out["signals"] = await _check_login_signals(page)
            except Exception as e:
                out["signals_error"] = str(e)
        return out

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
                    {
                        "event": e.event_name,
                        "status": e.status,
                        "odds": e.odds,
                        "stake": e.stake,
                        "payout": e.payout,
                    }
                    for e in history[:10]
                ],
            }
        except Exception as e:
            return {"error": str(e)}

    @router.get("/browser/api-probe/{provider_id}")
    async def api_probe(provider_id: str, url: str):
        """Hit an arbitrary URL via the provider tab's request context.

        Uses Playwright's `page.request` API which carries the tab's auth
        cookies and bypasses CORS — so we can call APIs the SPA itself
        uses (api.arcadia.pinnacle.se etc.) which JS-side fetches can't.
        Debug-only — keeps response body short."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"error": "no tab"}
        try:
            resp = await page.request.get(url, timeout=10_000)
            text = await resp.text()
            return {
                "status": resp.status,
                "ok": resp.ok,
                "url": resp.url,
                "body": text[:2000],
                "headers": dict(resp.headers),
            }
        except Exception as e:
            return {"error": str(e)}

    @router.get("/pinnacle/refresh-matchup/{matchup_id}")
    async def refresh_pinnacle_matchup(matchup_id: int):
        """Targeted live-odds refresh for one Pinnacle matchup.

        Hits api.arcadia.pinnacle.se with the public X-API-Key (extracted
        from the SPA's appConfig) via Playwright's page.request — bypasses
        CORS, uses the tab's session cookies. Returns all markets for the
        matchup with American prices converted to decimal so the frontend
        can apply them as liveLegOdds overrides without a per-leg click.

        Auto-follows the pre-match → live successor chain: if the
        requested matchup has gone live, returns markets for the live
        matchup_id (frontend keys overrides on the original matchup_id
        that's stored in betty's DB, so it doesn't care).

        Returns shape:
          {
            "matchup_id": int,            # the ID we read markets from
            "requested_id": int,          # what frontend asked for
            "league": str | None,
            "sport": str | None,
            "participants": [str, str],
            "markets": [
              {
                "key": "s;0;m" | "s;6;m" | "s;0;s;-1.5" | "s;0;ou;2.5" | ...,
                "period": int,
                "prices": [
                  {"designation": "home"|"away"|"draw"|"over"|"under",
                   "american": int, "decimal": float, "points": float|null}
                ]
              }, ...
            ]
          }
        """
        from .workflows.strategies.pinnacle import (
            _PINNACLE_API_BASE,
            _PINNACLE_FRONTEND_API_KEY,
            _american_to_decimal,
        )

        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        workflow = get_workflow("pinnacle")
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"error": "no pinnacle tab"}
        headers = {"X-API-Key": _PINNACLE_FRONTEND_API_KEY}

        async def _fetch_json(url: str):
            try:
                resp = await page.request.get(url, headers=headers, timeout=8_000)
                if not resp.ok:
                    return None
                return await resp.json()
            except Exception as e:
                logger.debug(f"[refresh-matchup] {url} failed: {e!r}")
                return None

        # Step 1: matchup info — also tells us if there's a live successor.
        m = await _fetch_json(f"{_PINNACLE_API_BASE}/matchups/{matchup_id}")
        if not isinstance(m, dict) or not m.get("league"):
            return {"error": "matchup_not_found", "requested_id": matchup_id}

        target_id = matchup_id
        # If pre-match has gone live, fetch markets from the live successor.
        if m.get("hasLive") and m.get("status") == "pending":
            league_id = m["league"]["id"]
            league_matchups = await _fetch_json(
                f"{_PINNACLE_API_BASE}/leagues/{league_id}/matchups"
            )
            if isinstance(league_matchups, list):
                live = next(
                    (
                        x
                        for x in league_matchups
                        if x.get("parentId") == int(matchup_id)
                        and x.get("type") == "matchup"
                        and x.get("isLive") is True
                        and x.get("status") == "started"
                        and (x.get("league") or {}).get("id") == league_id
                        and any(p.get("period") == 0 for p in (x.get("periods") or []))
                    ),
                    None,
                )
                if live:
                    target_id = live["id"]
                    m = live  # use the live matchup's metadata too

        # Step 2: markets/straight for the (live) matchup.
        markets_raw = await _fetch_json(
            f"{_PINNACLE_API_BASE}/matchups/{target_id}/markets/straight"
        )
        if not isinstance(markets_raw, list):
            return {"error": "markets_not_found", "matchup_id": target_id}

        markets = []
        for mk in markets_raw:
            if mk.get("isAlternate"):
                continue
            prices_out = []
            for p in mk.get("prices") or []:
                price = p.get("price")
                if price is None:
                    continue
                try:
                    decimal = _american_to_decimal(float(price))
                except Exception:
                    continue
                prices_out.append(
                    {
                        "designation": p.get("designation"),
                        "american": price,
                        "decimal": round(decimal, 4),
                        "points": p.get("points"),
                    }
                )
            if prices_out:
                markets.append(
                    {
                        "key": mk.get("key"),
                        "period": mk.get("period"),
                        "prices": prices_out,
                    }
                )

        parts = m.get("participants") or []
        return {
            "matchup_id": target_id,
            "requested_id": matchup_id,
            "league": (m.get("league") or {}).get("name"),
            "sport": ((m.get("league") or {}).get("sport") or {}).get("name"),
            "participants": [
                p.get("name") if isinstance(p, dict) else p for p in parts
            ],
            "is_live": bool(m.get("isLive")),
            "status": m.get("status"),
            "markets": markets,
        }

    @router.get("/browser/eval-on-tab")
    async def eval_on_tab(url_contains: str, js: str = "document.title"):
        """Debug: evaluate JS on the first tab whose URL contains `url_contains`.
        Lets us inspect non-provider tabs (e.g. the local betty UI at
        127.0.0.1:8000) without needing a registered workflow."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        target = None
        for p in browser.context.pages:
            if url_contains in (p.url or ""):
                target = p
                break
        if not target:
            return {"error": f"no tab with url containing {url_contains!r}"}
        try:
            return {"url": target.url, "result": await target.evaluate(js)}
        except Exception as e:
            return {"error": str(e), "url": target.url}

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
        return {
            "url": page.url,
            "balance_text": balance_text,
            "screenshot": "debug_screenshot.png",
        }

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
        domain (the 5 unlimited + tradingview + about:blank) gets closed. Catches
        the case where Chromium async-restored a stray tab (dbet, etc.) past the
        browser's startup grace window.
        """
        await browser.start()
        # Restore the user_picked_opp cache from disk so reactive-sync bet
        # recording (after a placement that intercept missed) can still fill
        # event_id/market/outcome on bets placed in a previous betty session.
        _restore_picked_opps(browser)
        # Defensive sweep BEFORE re-opening unlimited tabs so we don't end up
        # with both a stray dbet AND a fresh cloudbet (Chromium can refuse to
        # open a duplicate tab if it already has one for the same provider).
        #
        # Build the allowlist from every domain we have a workflow for. Pre-
        # 2026-05-11 this was hardcoded to the 5 unlimited counters + TV +
        # localhost, which silently killed any soft provider tab (BETINIA,
        # quickcasino, etc.) the user had open — including funded ones with
        # an active arb session. Deriving from `_DOMAIN_TO_PROVIDER` keeps
        # the allowlist in sync with whatever providers we support.
        from .browser import _DOMAIN_TO_PROVIDER

        _ALLOWED = (
            *_DOMAIN_TO_PROVIDER.keys(),
            "tradingview.com",
            "127.0.0.1",
            "localhost",
        )
        from ._urls import hostname_matches as _hm

        if browser.context:
            for page in list(browser.context.pages):
                try:
                    url = (page.url or "").lower()
                    if not url or url == "about:blank" or url.startswith("chrome:"):
                        continue
                    if any(_hm(d, url) for d in _ALLOWED):
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
                if not browser.context:
                    continue
                # An "already open" tab must be (a) not closed, (b) on a real
                # http(s) URL containing the domain — not about:blank, not a
                # chrome:// page, not a closed page object lingering in
                # context.pages. Without these guards a half-loaded stale tab
                # silently blocks the re-open and the provider never appears
                # (the cloudbet symptom we kept hitting).
                from ._urls import hostname_matches

                already = False
                for p in browser.context.pages:
                    try:
                        if p.is_closed():
                            continue
                        url = (p.url or "").lower()
                        if not url.startswith(("http://", "https://")):
                            continue
                        if workflow.domain and hostname_matches(workflow.domain, url):
                            already = True
                            break
                    except Exception:
                        continue
                if already:
                    continue
                try:
                    await browser.open_tab(workflow.home_url)
                except Exception as exc:
                    # Surface the failure in cmd output instead of swallowing
                    # — silent failure here is what makes "cloudbet didn't
                    # open" invisible until the user notices the missing tab.
                    print(
                        f"[mirror/start] open_tab({pid}) failed: {type(exc).__name__}: {exc}",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    f"[mirror/start] provider {pid} setup failed: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue
        # Close the boot keeper (and any other lingering about:blank tab) now
        # that real tabs exist. The keeper served its purpose holding the
        # context alive during startup cleanup; leaving it visible just
        # clutters the tab strip. Only close if there's at least one real
        # http(s) tab so we never end up with zero pages (which can let
        # Chromium decide to quit).
        if browser.context:
            real_tabs = sum(
                1
                for p in browser.context.pages
                if not p.is_closed()
                and (p.url or "").startswith(("http://", "https://"))
            )
            if real_tabs >= 1:
                for p in list(browser.context.pages):
                    try:
                        if p.is_closed():
                            continue
                        url = p.url or ""
                        if url == "about:blank" or url == "chrome://newtab/":
                            await p.close()
                            print(
                                f"[mirror/start] Closed keeper tab: {url}", flush=True
                            )
                    except Exception:
                        pass
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

        # Stash the picked-opp context per-provider so the subsequent placement
        # (intercepted via play_loop._record_manual_bet) or the reactive history
        # sync (pending_loop._record_unknown_open_bets) can fill event_id /
        # market / outcome. Persisted to disk so this survives betty restarts.
        _set_picked_opp(
            browser,
            req.provider_id,
            {
                "event_id": req.event_id,
                "market": req.market,
                "outcome": req.outcome,
                "point": req.point,
                "planned_odds": req.odds,
                "planned_stake": req.stake,
                "fair_odds": req.fair_odds,
                "home_team": req.display_home,
                "away_team": req.display_away,
            },
        )

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

    @router.post("/play/record-placed")
    async def play_record_placed(body: dict[str, Any]):
        """Record a bet placed manually in the user's real Chrome (for
        providers whose login the mirror can't complete).

        Requires that the user FIRST clicked the arb/value-bet row to set
        the picked-opp context via `/mirror/arb/navigate-opp`. Then they
        place the bet in real Chrome, come back, and POST here with
        {provider_id, stake, odds, provider_bet_id?}.
        """
        pid = body.get("provider_id")
        stake = body.get("stake")
        odds = body.get("odds")
        if not pid or stake is None or odds is None:
            raise HTTPException(400, "provider_id, stake, odds required")
        try:
            stake_f = float(stake)
            odds_f = float(odds)
        except (TypeError, ValueError):
            raise HTTPException(400, "stake and odds must be numeric")
        if stake_f <= 0 or odds_f <= 1.0:
            raise HTTPException(400, "stake must be >0, odds must be >1.0")
        try:
            return await play_loop.record_user_placed_bet(
                provider_id=pid,
                stake=stake_f,
                odds=odds_f,
                provider_bet_id=body.get("provider_bet_id"),
            )
        except ValueError as e:
            raise HTTPException(409, str(e))
        except Exception as e:
            raise HTTPException(500, f"record failed: {type(e).__name__}: {e}")

    @router.post("/play/run/{provider_id}")
    async def play_run(provider_id: str):
        """Release the Run gate for a provider runner: yellow → green.
        409 if no runner exists or gate already open."""
        ok = play_loop.set_run(provider_id, True)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=f"No runner for {provider_id} or gate already open",
            )
        return play_loop.get_status()

    @router.post("/play/pause/{provider_id}")
    async def play_pause(provider_id: str):
        """Clear the Run gate for a provider runner: green → yellow.
        409 if no runner exists or gate already closed."""
        ok = play_loop.set_run(provider_id, False)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=f"No runner for {provider_id} or gate already closed",
            )
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

    @router.post("/arb/navigate-opp")
    async def arb_navigate_opp(body: dict[str, Any]):
        """Drive a single provider tab to a leg of a user-picked arb opp.

        Bypasses the runner's queue. Body shapes:
          { provider_id, opp, leg? }
            - provider_id: the tab/workflow to drive (e.g. betinia, pinnacle)
            - opp: full opp dict (used for context — sport/market/event_id)
            - leg (optional): the specific leg to use for nav meta. If omitted,
              we resolve by matching leg.provider == provider_id, then by
              cluster (sibling fallback — opp anchored on a cluster rep is also
              valid for any sibling of that rep).
            { provider_id, leg }   — leg-only shape for direct calls

        For Altenar siblings (betinia/quickcasino/campobet/...), the anchor
        leg's provider may not match the clicked provider_id; the events are
        shared across the cluster, so we use the leg's meta to navigate the
        sibling's own tab.

        Soft anchor → nav + prep + SlipOddsStream (drift detection).
        Counter legs (autonomous_placement=False) → nav-only per F17.
        """
        from .arb_runner import ArbRunner
        from .play_loop import _PROVIDER_TO_CLUSTER, UNLIMITED_PROVIDERS, _bet_ns
        from .workflows import get_workflow

        provider_id = body.get("provider_id")
        opp = body.get("opp") or {}
        explicit_leg = body.get("leg")
        if not provider_id:
            raise HTTPException(status_code=400, detail="provider_id required")
        if not explicit_leg and not isinstance(opp, dict):
            raise HTTPException(
                status_code=400, detail="opp dict required when leg is omitted"
            )

        # Resolve which leg's meta to use for navigation. Frontend may inline
        # the picked leg as `opp._picked_leg` (per-leg click) or pass `leg` at
        # top level (programmatic call). Both win over auto-resolution.
        legs = (
            opp.get("arb_legs") or opp.get("legs", []) if isinstance(opp, dict) else []
        )
        anchor_leg = (
            explicit_leg
            or (opp.get("_picked_leg") if isinstance(opp, dict) else None)
            or next((l for l in legs if l.get("provider") == provider_id), None)
        )
        if not anchor_leg:
            # Sibling fallback: if any leg's provider is in the same cluster
            # as the requested provider_id, reuse its meta. Altenar siblings
            # (betinia/quickcasino/...) share event_ids, so navigation works.
            target_cluster = _PROVIDER_TO_CLUSTER.get(provider_id)
            if target_cluster:
                anchor_leg = next(
                    (
                        l
                        for l in legs
                        if _PROVIDER_TO_CLUSTER.get(l.get("provider")) == target_cluster
                    ),
                    None,
                )
        if not anchor_leg:
            raise HTTPException(
                status_code=400,
                detail=f"opp has no leg for {provider_id} or its cluster siblings",
            )

        # Manual pick takes precedence over the auto-runner. Stop any runner
        # for this provider AND remove it from the coordinator's provider_ids
        # so subsequent _add_new_runners cycles don't re-spawn it. The user is
        # in manual mode (cell-click drives navigation); the auto-runner's
        # top-opp watcher would race and look haywire.
        existing_runner = play_loop._runners.get(provider_id)
        if existing_runner is not None and getattr(existing_runner, "running", False):
            try:
                existing_runner.stop()
                logger.info(
                    f"[/arb/navigate-opp] stopped existing runner for {provider_id}"
                )
            except Exception as e:
                logger.warning(
                    f"[/arb/navigate-opp] failed to stop runner for {provider_id}: {e!r}"
                )
        try:
            if provider_id in getattr(play_loop, "_provider_ids", []):
                play_loop._provider_ids = [
                    p for p in play_loop._provider_ids if p != provider_id
                ]
                logger.info(
                    f"[/arb/navigate-opp] removed {provider_id} from coordinator provider_ids"
                )
            play_loop._runners.pop(provider_id, None)
        except Exception as e:
            logger.debug(f"[/arb/navigate-opp] cleanup raised: {e!r}")

        wf = get_workflow(provider_id)
        if not browser.context:
            await browser.start()
        page = await wf.find_tab(browser.context)
        if not page:
            page = await browser.open_tab(wf.home_url)

        balance = browser.provider_data.get(provider_id, {}).get("balance") or 0.0
        bet = ArbRunner._opp_to_bet(opp, anchor_leg)
        # Frontend can override the anchor stake (manual stake input on the
        # DUTCH ARB widget). Clamp to [0, balance] so we can't request a bet
        # larger than what's available — the bookmaker would reject it
        # anyway, but failing here keeps the slip prep from getting into
        # an inconsistent state.
        override_stake = opp.get("_override_stake") if isinstance(opp, dict) else None
        if isinstance(override_stake, (int, float)) and override_stake > 0:
            bet["stake"] = round(min(float(override_stake), balance), 2)
        else:
            bet["stake"] = round(balance, 2)
        bet_ns = _bet_ns(bet)

        leg_event_id = (anchor_leg.get("provider_meta") or {}).get(
            "event_id"
        ) or opp.get("event_id")
        # Stash the picked-opp context per-provider (persisted to disk so the
        # context survives a betty restart and reactive-sync bet recording
        # in a later session still preserves event_id). Without this any bet
        # recorded via reactive sync after a restart loses event_id and lands
        # in the "unknown" sport analytics bucket with no edge/CLV breakdown.
        _set_picked_opp(
            browser,
            provider_id,
            {
                "event_id": opp.get("event_id"),
                "market": anchor_leg.get("market") or opp.get("market"),
                "outcome": anchor_leg.get("outcome"),
                "point": anchor_leg.get("point")
                if anchor_leg.get("point") is not None
                else opp.get("point"),
                "planned_odds": anchor_leg.get("odds"),
                "planned_stake": bet["stake"],
                "start_time": opp.get("starts_at") or opp.get("start_time"),
                "home_team": opp.get("display_home") or opp.get("home_team"),
                "away_team": opp.get("display_away") or opp.get("away_team"),
                "sport": opp.get("sport"),
            },
        )
        broadcaster.publish(
            "arb_leg_started",
            {
                "provider_id": provider_id,
                "role": "anchor",
                "planned_odds": anchor_leg.get("odds"),
                "planned_stake": bet["stake"],
                "user_picked": True,
                "event_id": opp.get("event_id"),
            },
        )

        nav_ok = await wf.navigate_to_event(page, bet_ns)
        if not nav_ok:
            # For Pinnacle: navigate_to_event already follows the live-successor
            # chain and returns False only when the matchup is genuinely dead
            # (no live ID can render content). Treat this as event_closed so
            # the frontend drains the row, instead of leaving the user with a
            # "nav failed" status they can't act on.
            if provider_id == "pinnacle":
                event_id_pn = (anchor_leg.get("provider_meta") or {}).get(
                    "event_id"
                ) or opp.get("event_id")
                broadcaster.publish(
                    "arb_leg_event_closed",
                    {
                        "provider_id": provider_id,
                        "event_id": event_id_pn,
                        "url": page.url,
                        "user_picked": True,
                        "reason": "no_live_matchup",
                    },
                )
                return {
                    "status": "event_closed",
                    "url": page.url,
                    "event_id": event_id_pn,
                }
            broadcaster.publish(
                "arb_leg_failed",
                {
                    "provider_id": provider_id,
                    "stage": "navigate",
                    "reason": "navigate_failed",
                },
            )
            raise HTTPException(status_code=502, detail="navigate_to_event failed")
        broadcaster.publish(
            "arb_leg_navigated",
            {
                "provider_id": provider_id,
                "url": page.url,
                "user_picked": True,
                "event_id": opp.get("event_id"),
            },
        )

        # Event-closed detection: bookmaker shows "Detta evenemang är avslutat"
        # (or English equivalent) for finished/suspended events. We don't want
        # to prep the slip on a dead page — return early so the frontend can
        # mark this opp drained and auto-pop the next.
        from .provider_runner import ProviderRunner

        # Pinnacle uses a different fingerprint: expired matchups serve 502
        # from the API and leave the page on the home shell with no event
        # card. The Altenar-shaped check (`_is_event_closed`) only looks
        # inside `stb-sportsbook` shadow root and won't catch this.
        pinnacle_empty = False
        if provider_id == "pinnacle":
            try:
                from .workflows.strategies.pinnacle import _is_matchup_empty

                pinnacle_empty = await _is_matchup_empty(page)
            except Exception as e:
                logger.debug(f"[/arb/navigate-opp] pinnacle empty-check raised: {e!r}")
        if pinnacle_empty or await ProviderRunner._is_event_closed(page):
            event_id = (anchor_leg.get("provider_meta") or {}).get(
                "event_id"
            ) or opp.get("event_id")
            broadcaster.publish(
                "arb_leg_event_closed",
                {
                    "provider_id": provider_id,
                    "event_id": event_id,
                    "url": page.url,
                    "user_picked": True,
                },
            )
            return {
                "status": "event_closed",
                "url": page.url,
                "event_id": event_id,
            }

        # Guided counter (autonomous_placement=False AND not the soft anchor's
        # cluster) — nav-only per F17. Pinnacle/Kambi/etc. counter tabs just
        # land on the event page; the user clicks outcome + Place themselves.
        # Soft anchor (Altenar/Gecko/etc.) AND autonomous (Polymarket SDK) get
        # the full prep + stream chain so we have drift detection.
        is_unlimited = provider_id in UNLIMITED_PROVIDERS
        is_autonomous = bool(getattr(wf, "autonomous_placement", False))
        is_soft_anchor = (not is_unlimited) and _PROVIDER_TO_CLUSTER.get(
            provider_id
        ) is not None
        if not (is_soft_anchor or is_autonomous):
            # Guided counters STILL get a live-odds poll task so the frontend
            # sees the counter's odds drift in real time. Without this, only
            # the soft anchor streams updates and the user can't tell when
            # the sharp side moved against them after locking BETINIA.
            captured_event_id_g = opp.get("event_id")
            captured_market_g = anchor_leg.get("market") or opp.get("market") or ""
            captured_outcome_g = anchor_leg.get("outcome") or ""
            captured_point_g = anchor_leg.get("point")

            def _on_odds_g(o):
                broadcaster.publish(
                    "arb_leg_odds",
                    {
                        "provider_id": provider_id,
                        "live_odds": o,
                        "planned_odds": anchor_leg.get("odds"),
                        "user_picked": True,
                        "event_id": captured_event_id_g,
                    },
                )
                # ALSO publish live_price so Section B's value-bet rows
                # update livePrices. arb_leg_odds only feeds the arb
                # section's overlay; without this the value-bet display
                # would keep showing extraction-time odds while the slip
                # stream knows the live value (the 56-vs-58 mismatch).
                fair_g = anchor_leg.get("fair_odds")
                live_edge_g = (
                    ((o / fair_g - 1) * 100) if fair_g and fair_g > 0 else None
                )
                if captured_market_g and captured_outcome_g:
                    broadcaster.publish(
                        "live_price",
                        {
                            "provider_id": provider_id,
                            "event_id": captured_event_id_g,
                            "market": captured_market_g,
                            "outcome": captured_outcome_g,
                            "point": captured_point_g,
                            "live_odds": o,
                            "live_edge": live_edge_g,
                        },
                    )
                # Persist to DB so the next /arb-workflow scan returns the
                # live value — frontend stays correct across refresh / other
                # devices / browser data clear. Fire-and-forget so the poll
                # cadence isn't blocked on tunnel latency.
                _persist_live_odds(
                    provider_id,
                    captured_event_id_g,
                    captured_market_g,
                    captured_outcome_g,
                    captured_point_g,
                    o,
                )

            async def _poll_guided_live_price():
                last: float | None = None
                ticks = 0
                print(f"[POLL {provider_id}] START (guided)", flush=True)
                try:
                    while True:
                        await asyncio.sleep(1.0)
                        ticks += 1
                        live: float | None = None
                        src = ""
                        # Slip-first: when the user clicks an outcome on the
                        # bookmaker tab, the slip carries the exact odd id +
                        # price they selected. Unambiguous even when the
                        # page shows multiple moneyline-shaped markets
                        # stacked (Winner-Enhanced / Vinnare / First-Set).
                        if hasattr(wf, "read_slip_odds"):
                            slip_event_id_g = getattr(
                                bet_ns, "altenar_event_id", None
                            ) or (getattr(bet_ns, "provider_meta", None) or {}).get(
                                "event_id"
                            )
                            try:
                                live = (
                                    await wf.read_slip_odds(
                                        page, expected_event_id=slip_event_id_g
                                    )
                                    if slip_event_id_g
                                    else await wf.read_slip_odds(page)
                                )
                                if live is not None:
                                    src = "slip"
                            except Exception as e:
                                print(
                                    f"[POLL {provider_id}] read_slip_odds raised: {e!r}",
                                    flush=True,
                                )
                        if live is None and hasattr(wf, "read_outcome_odds_dom"):
                            try:
                                live = await wf.read_outcome_odds_dom(page, bet_ns)
                                if live is not None:
                                    src = "dom"
                            except Exception as e:
                                print(
                                    f"[POLL {provider_id}] read_outcome_odds_dom raised: {e!r}",
                                    flush=True,
                                )
                        if live is None and hasattr(wf, "check_live_price"):
                            try:
                                live, _ = await wf.check_live_price(page, bet_ns)
                                if live is not None:
                                    src = "live_price"
                            except Exception:
                                pass
                        if ticks % 5 == 0:
                            print(
                                f"[POLL {provider_id}] tick={ticks} live={live} src={src!r} last={last}",
                                flush=True,
                            )
                        if live is not None and live != last:
                            last = live
                            try:
                                _on_odds_g(live)
                            except Exception as e:
                                print(
                                    f"[POLL {provider_id}] _on_odds raised: {e!r}",
                                    flush=True,
                                )
                except asyncio.CancelledError:
                    print(
                        f"[POLL {provider_id}] CANCELLED after {ticks} ticks",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[POLL {provider_id}] crashed: {e!r}", flush=True)

            existing_task_g = getattr(browser, "_user_picked_tasks", {}).get(
                provider_id
            )
            if existing_task_g and not existing_task_g.done():
                existing_task_g.cancel()
            task_g = asyncio.create_task(
                _poll_guided_live_price(), name=f"live_price_{provider_id}"
            )
            if not hasattr(browser, "_user_picked_tasks"):
                browser._user_picked_tasks = {}
            browser._user_picked_tasks[provider_id] = task_g

            broadcaster.publish(
                "arb_leg_synced",
                {
                    "provider_id": provider_id,
                    "planned_odds": anchor_leg.get("odds"),
                    "url": page.url,
                    "user_picked": True,
                    "guided": True,
                    "event_id": opp.get("event_id"),
                },
            )
            return {
                "status": "nav_only",
                "url": page.url,
                "planned_odds": anchor_leg.get("odds"),
                "guided": True,
                "event_id": opp.get("event_id"),
            }

        prep = await wf.prep_betslip(page, bet_ns, bet["stake"])
        if prep.status not in ("prepped", "placed"):
            broadcaster.publish(
                "arb_leg_failed",
                {
                    "provider_id": provider_id,
                    "stage": "prep",
                    "reason": getattr(prep, "reason", None) or prep.status,
                },
            )
            return {
                "status": "prep_failed",
                "url": page.url,
                "reason": getattr(prep, "reason", None) or prep.status,
            }

        # Start slip-odds stream so the row shows live drift. Replace any
        # existing stream for this provider.
        existing = getattr(browser, "_user_picked_streams", {}).get(provider_id)
        if existing:
            try:
                existing.stop()
            except Exception:
                pass

        captured_event_id = opp.get("event_id")
        captured_market = anchor_leg.get("market") or opp.get("market") or ""
        captured_outcome = anchor_leg.get("outcome") or ""
        captured_point = anchor_leg.get("point")

        def _on_odds(o):
            broadcaster.publish(
                "arb_leg_odds",
                {
                    "provider_id": provider_id,
                    "live_odds": o,
                    "planned_odds": anchor_leg.get("odds"),
                    "user_picked": True,
                    "event_id": captured_event_id,
                },
            )
            # Parallel live_price for Section B's value-bet row display
            # (livePrices map). See _on_odds_g for the rationale.
            fair = anchor_leg.get("fair_odds")
            live_edge = ((o / fair - 1) * 100) if fair and fair > 0 else None
            if captured_market and captured_outcome:
                broadcaster.publish(
                    "live_price",
                    {
                        "provider_id": provider_id,
                        "event_id": captured_event_id,
                        "market": captured_market,
                        "outcome": captured_outcome,
                        "point": captured_point,
                        "live_odds": o,
                        "live_edge": live_edge,
                    },
                )
            # Persist to DB. Same rationale as the guided counter — make
            # mirror updates the canonical source on the server side, not
            # just a frontend overlay that disappears on refresh.
            _persist_live_odds(
                provider_id,
                captured_event_id,
                captured_market,
                captured_outcome,
                captured_point,
                o,
            )

        # Live-odds polling. Order matters:
        # 1. read_slip_odds — when the user has clicked an outcome the slip
        #    carries the EXACT odd id + price they selected. Unambiguous —
        #    no need to guess between Winner-Enhanced @ 1.56 vs Vinnare @
        #    1.53 vs First-Set @ 1.61. The user telling us which market is
        #    canonical via their click is the cleanest signal we have.
        # 2. DOM scrape — when slip is empty, read the rendered OddValue
        #    from the widget's shadow DOM. Falls back to index/team-name
        #    based wrapper selection (ambiguous on tennis pages that show
        #    multiple moneyline-shaped markets stacked).
        # 3. check_live_price — cached GetEventDetails fallback when both
        #    above fail.
        async def _poll_live_price():
            last: float | None = None
            ticks = 0
            print(f"[POLL {provider_id}] START", flush=True)
            try:
                while True:
                    await asyncio.sleep(1.0)
                    ticks += 1
                    live: float | None = None
                    src: str = ""
                    # 1. Slip-first — user click is the unambiguous source
                    if hasattr(wf, "read_slip_odds"):
                        slip_event_id = getattr(bet_ns, "altenar_event_id", None) or (
                            getattr(bet_ns, "provider_meta", None) or {}
                        ).get("event_id")
                        try:
                            live = (
                                await wf.read_slip_odds(
                                    page, expected_event_id=slip_event_id
                                )
                                if slip_event_id
                                else await wf.read_slip_odds(page)
                            )
                            if live is not None:
                                src = "slip"
                        except Exception as e:
                            print(
                                f"[POLL {provider_id}] read_slip_odds raised: {e!r}",
                                flush=True,
                            )
                    # 2. DOM scrape — read the rendered OddValue directly from
                    # the bookmaker widget's shadow DOM. This is what the user
                    # SEES on the tab, kept in sync by the widget itself with
                    # whatever transport (HTTP poll, WebSocket, SSE) it uses
                    # internally. Most reliable continuous-stream source.
                    if live is None and hasattr(wf, "read_outcome_odds_dom"):
                        try:
                            live = await wf.read_outcome_odds_dom(page, bet_ns)
                            if live is not None:
                                src = "dom"
                        except Exception as e:
                            print(
                                f"[POLL {provider_id}] read_outcome_odds_dom raised: {e!r}",
                                flush=True,
                            )
                    # 3. check_live_price — cached GetEventDetails. Falls
                    # behind on bookmakers that don't re-fetch on drift.
                    if live is None and hasattr(wf, "check_live_price"):
                        try:
                            live, _ = await wf.check_live_price(page, bet_ns)
                            if live is not None:
                                src = "live_price"
                        except Exception as e:
                            print(
                                f"[POLL {provider_id}] check_live_price raised: {e!r}",
                                flush=True,
                            )
                    if False and live is None and hasattr(wf, "read_slip_odds"):
                        slip_event_id = getattr(bet_ns, "altenar_event_id", None) or (
                            getattr(bet_ns, "provider_meta", None) or {}
                        ).get("event_id")
                        try:
                            try:
                                live = await wf.read_slip_odds(
                                    page, expected_event_id=slip_event_id
                                )
                            except TypeError:
                                live = await wf.read_slip_odds(page)
                            if live is not None:
                                src = "slip"
                        except Exception as e:
                            print(
                                f"[POLL {provider_id}] read_slip_odds raised: {e!r}",
                                flush=True,
                            )
                    if ticks % 5 == 0:
                        print(
                            f"[POLL {provider_id}] tick={ticks} live={live} src={src!r} last={last}",
                            flush=True,
                        )
                    if live is not None and live != last:
                        last = live
                        print(
                            f"[POLL {provider_id}] FIRE odds={live} src={src!r}",
                            flush=True,
                        )
                        try:
                            _on_odds(live)
                        except Exception as e:
                            print(
                                f"[POLL {provider_id}] _on_odds raised: {e!r}",
                                flush=True,
                            )
            except asyncio.CancelledError:
                print(f"[POLL {provider_id}] CANCELLED after {ticks} ticks", flush=True)
            except Exception as e:
                print(f"[POLL {provider_id}] crashed: {e!r}", flush=True)
                import traceback

                traceback.print_exc()

        existing_task = getattr(browser, "_user_picked_tasks", {}).get(provider_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()
        task = asyncio.create_task(_poll_live_price(), name=f"live_price_{provider_id}")
        if not hasattr(browser, "_user_picked_tasks"):
            browser._user_picked_tasks = {}
        browser._user_picked_tasks[provider_id] = task

        broadcaster.publish(
            "arb_leg_synced",
            {
                "provider_id": provider_id,
                "planned_odds": anchor_leg.get("odds"),
                "planned_stake": bet["stake"],
                "url": page.url,
                "user_picked": True,
                "event_id": captured_event_id,
            },
        )

        return {
            "status": "synced",
            "url": page.url,
            "planned_odds": anchor_leg.get("odds"),
            "planned_stake": bet["stake"],
        }

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
            raise HTTPException(
                status_code=404, detail=f"No open tab for {provider_id}"
            )

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
                "items": [
                    {"event": p.event_name, "odds": p.odds, "stake": p.stake}
                    for p in pos[:5]
                ],
            }
        except Exception as e:
            results["positions"] = {"error": str(e)}

        # Test history
        try:
            hist = await wf.sync_history(page)
            results["history"] = {
                "count": len(hist),
                "items": [
                    {
                        "event": h.event_name,
                        "status": h.status,
                        "odds": h.odds,
                        "stake": h.stake,
                    }
                    for h in hist[:5]
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
                await page.locator("#STB_SPORTSBOOK > div").click(
                    position={"x": ox, "y": oy}, timeout=5000
                )
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
