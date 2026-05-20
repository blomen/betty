"""Playwright browser lifecycle — launch, manage tabs, intercept traffic.

Default is patchright (chromium-1217 bundle). Vanilla playwright's
chromium-1200 bundle was observed to crash on launch with this profile
(2026-05-18, STATUS_BREAKPOINT exit code, no useful stderr) — even a fresh
`playwright install chromium` reinstall produced the same crash. Chromium
1217 from patchright's bundle handles the same profile fine, so we default
there for stability.

Set env `ARNOLD_USE_VANILLA_PLAYWRIGHT=1` to force vanilla (useful for
testing whether the chromium-1200 issue has been resolved).
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path  # noqa: F401
from typing import Any

if os.getenv("ARNOLD_USE_VANILLA_PLAYWRIGHT") == "1":
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        Response,
        WebSocket,
        async_playwright,
    )
else:
    from patchright.async_api import (  # type: ignore[import-not-found]
        Browser,
        BrowserContext,
        Page,
        Playwright,
        Response,
        WebSocket,
        async_playwright,
    )

logger = logging.getLogger(__name__)

# URL patterns for classifying intercepted responses
_BALANCE_KEYWORDS = (
    "account/balance",
    "/wallets",
    "mainbalance",
    "wallet/balance",
    "payment-stats",
    "/cashier/balance",
    "iam-balances",  # Cloudbet POST /iam-balances
    "clob.polymarket.com/balance-allowance",  # Polymarket CLOB SDK (fires only at order time)
    # Kalshi web API: api.elections.kalshi.com/v1/users/<UUID>/balance (singular).
    # Subaccount /balances variant returns a different shape — _extract_balance
    # handles both via the {balance: int_cents} fallback path.
    "api.elections.kalshi.com/v1/users/",
    # NOTE: data-api.polymarket.com/value returns POSITION value, NOT cash. Don't add it.
    # NOTE: data-api.polymarket.com/positions also doesn't carry cash.
    # Polymarket cash USDC balance is currently DOM-scraped only (see strategies/polymarket.py).
    # The CLOB balance-allowance endpoint fires when SDK builds an order — we'll learn its
    # exact shape when the first real bet is placed.
)
_HISTORY_KEYWORDS = (
    "bethistory",
    "bet-history",
    "mybets",
    "my-bets",
    "widgetbethistory",
    "coupon-history",
    "data-api.polymarket.com/trades",  # Polymarket trade history
    "data-api.polymarket.com/positions",  # Polymarket open positions — fired by /portfolio?tab=positions
    "arcadia.pinnacle.se/0.1/bets",  # Pinnacle bet list (?status=settled|unsettled)
    "sports-betting/v4/bets/positions",  # Cloudbet positions (ACCEPTED + COMPLETED)
    "event_positions",  # Kalshi: /v1/users/<UUID>/event_positions?position_status=...
)
_BET_PLACEMENT_KEYWORDS = (
    "placewidget",
    "placebet",
    "/coupons",
    "/coupon.json",  # Kambi CDN placement (LeoVegas)
    "bets/straight",
    "bets/parlay",
    "bets/place",
    "clob.polymarket.com/order",
    # Kalshi: POST api.elections.kalshi.com/v1/users/<UUID>/orders
    # Both the bare /orders POST (placement) and /orders?status=resting GET
    # match this substring — the placement handler gates on
    # response.request.method == "POST" so reads aren't mistaken for placements.
    "api.elections.kalshi.com/v1/users/",
)
# Third-party tracker / analytics hosts. Their URLs often embed the provider's
# page URL (containing "bethistory" / "balance" / etc.) as a query param,
# which used to spuriously trigger our keyword interceptors. Skip them.
_TRACKER_HOST_SUFFIXES = (
    "facebook.com",
    "facebook.net",
    "google.com",
    "googleadservices.com",
    "googletagmanager.com",
    "google-analytics.com",
    "googlesyndication.com",
    "doubleclick.net",
    "criteo.com",
    "criteo.net",
    "bing.com",
    "branch.io",
    "segment.com",
    "segment.io",
    "hotjar.com",
    "mxpnl.com",
    "mixpanel.com",
    "snowplowanalytics.com",
    "tiktok.com",
)

# WebSocket URLs to monitor for bet placement frames (Kambi uses WS, not HTTP)
_WS_MONITOR_KEYWORDS = ("kambi", "push.aws")

# Keywords in WS frames that indicate a bet was placed (server → client)
_WS_BET_RECEIVED_KEYWORDS = (
    '"couponId"',
    '"placeBetResult"',
    '"couponStatus"',
    '"couponResponse"',
    '"betPlaced"',
    '"PLACED"',
)

# Keywords in WS frames for bet requests (client → server)
_WS_BET_SENT_KEYWORDS = ('"placeBet"', '"placeCoupon"', '"stake"')

# Provider domain → provider_id mapping
_DOMAIN_TO_PROVIDER: dict[str, str] = {
    "betinia.se": "betinia",
    "quickcasino.se": "quickcasino",
    "campobet.se": "campobet",
    "comeon.com": "comeon",
    "unibet.se": "unibet",
    "leovegas.se": "leovegas",
    "leovegas.com": "leovegas",
    "expekt.se": "expekt",
    "spelklubben.se": "spelklubben",
    "spelklubbenplayground.net": "spelklubben",
    "cloud-api.spelklubben.se": "spelklubben",
    "betsson.com": "betsson",
    "betssonplayground.net": "betsson",
    "nordicbet.com": "nordicbet",
    "nordicbetplayground.net": "nordicbet",
    "betsafe.com": "betsafe",
    "betsafeplayground.net": "betsafe",
    "bethard.com": "bethard",
    "bethardplayground.net": "bethard",
    "pinnacle.se": "pinnacle",
    "coolbet.com": "coolbet",
    "vbet.com": "vbet",
    "10bet.com": "10bet",
    "polymarket.com": "polymarket",
    "cloudbet.com": "cloudbet",
    # Kalshi: kalshi.com is the page host; api.elections.kalshi.com is the
    # cross-origin web API. Both classify as kalshi so interceptor + tab
    # detection work whether we look at page URL or request URL.
    "kalshi.com": "kalshi",
    "api.elections.kalshi.com": "kalshi",
    # Rainbet: signal-only Betby tenant. Tab auto-opens for browsing but no
    # play workflow — interceptor doesn't read balance or place bets.
    "rainbet.com": "rainbet",
    "tipwin.se": "tipwin",
    "mrgreen.com": "mrgreen",
    "888sport.com": "888sport",
    "hajper.com": "hajper",
    "x3000.se": "x3000",
    "speedybet.com": "speedybet",
    "goldenbull.se": "goldenbull",
}

# Altenar/Gecko/Kambi API domains use integration= param to identify the provider
# We detect provider from the page URL (which tab made the request), not the API domain
# But we also need to recognize these API domains as "belonging to a provider"
_API_DOMAINS = {
    "biahosted.com",
    "bfrndz.com",  # Altenar API
    "sbapi.sbtech.com",
    "sportsbook-api",  # SBTech
    "kambi.com",
    "push.aws",  # Kambi
    "clob.polymarket.com",  # Polymarket
}


_USER_DATA_DIR = Path(__file__).parent.parent / "data" / "browser_profile"


# Module-level singleton — assigned in MirrorBrowser.start(). Workflows (which
# only receive a `page`, not the browser) can `from .browser import
# get_active_browser` to reach `MirrorBrowser.provider_data`. Used by Gecko V2
# sync_history to read the interceptor-cached coupon-history body.
_ACTIVE_BROWSER: "MirrorBrowser | None" = None


def get_active_browser() -> "MirrorBrowser | None":
    """Return the currently-running MirrorBrowser, or None if not started."""
    return _ACTIVE_BROWSER


class MirrorBrowser:
    """Manages a headed Chromium browser with persistent profile.

    Uses launch_persistent_context with a real Chrome profile directory.
    Cookies, localStorage, and login sessions survive server restarts
    and even force-kills — no explicit save needed.
    """

    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None  # unused with persistent context
        self._context: BrowserContext | None = None
        self._running = False
        # Intercepted data per provider
        self.provider_data: dict[str, dict] = {}  # pid → {logged_in, balance, last_url, ...}
        # Callback for broadcasting events
        self._on_event: Callable[[str, dict], None] | None = None
        # Callback for stream dispatch (provider_id, event_type, data)
        self._on_stream_callback: Callable[[str, str, Any], None] | None = None
        # Domains the user/system intentionally opened — extends the static
        # allowlist used by the stray-tab watchdog. Populated by open_tab().
        self._dynamic_allowlist: set[str] = set()
        # Pages we explicitly opened via open_tab() (or the boot keeper).
        # The route filter trusts navigations originating from these pages —
        # so a kalshi login redirect to auth.magic.link survives even within
        # the ghost-block grace window.
        self._explicit_pages: set[Page] = set()
        # Monotonic time after which the route-level ghost-tab blocker stops
        # filtering. Set in start(); blocker passes everything through after.
        self._ghost_block_deadline: float = 0.0
        # Strong refs to background tasks so they don't get GC'd. asyncio
        # only keeps weak refs to tasks created by create_task — without an
        # explicit anchor here, the watchdog and per-tab close-tasks vanish
        # mid-execution. discrepancy was: dbet sat in /mirror/browser/tabs
        # for 100s with zero "Watchdog closed" log lines because the task
        # had been collected.
        self._background_tasks: set[asyncio.Task] = set()
        # Lock around the find-or-create + goto sequence in open_tab. Two
        # concurrent open_tab calls used to scan context.pages for an
        # about:blank tab simultaneously, both pick the same one, and the
        # second goto would preempt the first. Real consequence: TV auto-
        # open + /mirror/start cloudbet step ran in parallel, both took
        # the boot keeper, cloudbet's goto aborted TV's. Lock serializes
        # tab acquisition; the goto itself can still run concurrently after
        # the page is securely allocated.
        self._open_tab_lock: asyncio.Lock | None = None
        # Serialize start() so two concurrent callers (e.g. /mirror/start
        # endpoint + TV-overlay auto-open task) don't both pass the
        # `if self._running` guard, both run _kill_orphaned_chromium, and
        # silently kill each other's just-launched Chromium process. The
        # symptom is an infinite "[browser] Using profile" → TargetClosedError
        # → re-launch loop where Chromium never stabilizes.
        self._start_lock: asyncio.Lock | None = None

    def _spawn(self, coro, name: str | None = None) -> asyncio.Task:
        """Create a background task and keep a strong reference to it.

        asyncio.create_task() returns a Task that the event loop only weakly
        references. Without an external anchor the task can be garbage-
        collected mid-execution — observed empirically: the watchdog never
        ran, dbet ghost tabs never got closed by the periodic sweep,
        framenavigated close-tasks vanished. Use this helper for every
        background task spawned from this class.
        """
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            self._background_tasks.discard(t)
            # Surface unexpected errors so a silently-crashed task doesn't
            # leave the watchdog/guard dead without us noticing.
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    print(
                        f"[browser] background task {t.get_name()!r} crashed: {type(exc).__name__}: {exc}",
                        flush=True,
                    )

        task.add_done_callback(_on_done)
        return task

    def set_event_callback(self, callback: Callable[[str, dict], None]):
        """Set callback for intercepted events (e.g. broadcaster.publish)."""
        self._on_event = callback

    def set_stream_callback(self, callback: Callable[[str, str, Any], None]):
        """Set callback for stream-relevant events: (provider_id, event_type, data)."""
        self._on_stream_callback = callback

    @property
    def running(self) -> bool:
        return self._running

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    async def start(self) -> BrowserContext:
        # Thin lock wrapper. The actual launch is in `_start_impl`. Two
        # concurrent callers (e.g. /mirror/start endpoint + TV-overlay
        # auto-open task at boot) both used to pass an `if self._running`
        # guard and both run `_kill_orphaned_chromium`, silently killing
        # each other's just-launched Chromium → infinite re-launch loop
        # with no stable browser. The lock serializes; the re-check inside
        # short-circuits the second caller.
        if self._running:
            return self._context
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._running:
                return self._context
            return await self._start_impl()

    async def _start_impl(self) -> BrowserContext:
        # Kill any orphaned Chromium holding the profile lock
        await self._kill_orphaned_chromium()

        self._playwright = await async_playwright().start()

        # Persistent context = real Chrome profile on disk.
        # Cookies, localStorage, service workers all survive restarts.
        _USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Disable Chrome session restore — prevent old tabs from reopening.
        # Two layers: (1) Preferences flags Chromium reads at launch, (2) wipe
        # all on-disk snapshot/session state so the recovery code path has
        # nothing to read even if the flags get ignored.
        prefs_file = _USER_DATA_DIR / "Default" / "Preferences"
        if prefs_file.exists():
            try:
                prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
                prefs.setdefault("session", {})["restore_on_startup"] = 5  # 5 = open blank
                # Explicitly mark previous run as clean. Just popping exit_type
                # isn't enough — Chromium's session-crashed recovery checks both
                # exit_type and exited_cleanly. Force both into the "no crash"
                # state so the recovery path is bypassed even when our previous
                # process was killed via Stop-Process -Force (which leaves the
                # file in "Crashed" state).
                profile = prefs.setdefault("profile", {})
                profile["exit_type"] = "Normal"
                profile["exited_cleanly"] = True
                # Top-level `sessions.event_log` records each session's crash
                # state. Chromium's recovery path consults this independently
                # of profile.exit_type — clear it so the "previous run crashed"
                # signal can't survive across a forced kill.
                prefs.pop("sessions", None)
                # Saved tab groups can re-pin tabs even when Sessions/ is
                # wiped. Clear the metadata pointer; the on-disk Tab Groups
                # files are wiped below.
                prefs.pop("saved_tab_groups", None)
                # Also wipe pinned_tabs in case anything ever populated it.
                prefs["pinned_tabs"] = []
                prefs_file.write_text(json.dumps(prefs), encoding="utf-8")
            except Exception:
                pass

        # Wipe ALL session/snapshot directories — the actual restoration source.
        # Sessions/ is the live session store; Snapshots/<version>/ is Chromium's
        # crash-recovery snapshot which is consulted even with restore_on_startup=5
        # + --no-restore-state when exit_type=Crashed. With both gone there's
        # literally no data for Chromium to restore from.
        import shutil as _shutil

        for path in (
            _USER_DATA_DIR / "Default" / "Sessions",
            _USER_DATA_DIR / "Snapshots",
            # Modern Chromium can also resurrect tabs from saved tab groups —
            # the user might have an "Arnold" tab group from a prior run. The
            # data is in a few possible spots depending on Chromium build.
            _USER_DATA_DIR / "Default" / "Tab Groups",
            _USER_DATA_DIR / "Default" / "SavedTabGroups",
            _USER_DATA_DIR / "Default" / "Saved Tab Groups",
        ):
            if path.exists():
                try:
                    _shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    pass

        # Also remove legacy single-file session artifacts if present
        # (pre-Sessions/ Chromium versions). Belt-and-suspenders — these don't
        # exist on modern Chrome but cost nothing to check.
        for legacy in (
            "Last Tabs",
            "Current Tabs",
            "Last Session",
            "Current Session",
            "Tab Group Highlights",
        ):
            f = _USER_DATA_DIR / "Default" / legacy
            if f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass

        print(f"[browser] Using profile: {_USER_DATA_DIR}", flush=True)

        # Auto-load the Arnold TradingView Overlay extension so any TV tab
        # opened in the mirror gets zone/position drawing for free. The
        # extension's MV3 manifest only matches tradingview.com, so it's
        # inert on every other tab (sportsbook flows are unaffected).
        _tv_ext_dir = Path(__file__).resolve().parent.parent / "tv_overlay" / "extension"
        ext_args: list[str] = []
        if _tv_ext_dir.exists():
            ext_args = [
                f"--disable-extensions-except={_tv_ext_dir}",
                f"--load-extension={_tv_ext_dir}",
            ]
            print(f"[browser] Loading TV overlay extension: {_tv_ext_dir}", flush=True)
        else:
            print(f"[browser] TV overlay extension dir missing: {_tv_ext_dir}", flush=True)

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(_USER_DATA_DIR),
            headless=False,
            locale="en-GB",
            timezone_id="Europe/Stockholm",
            # No user_agent override: Playwright's bundled Chromium reports
            # its real version (currently 140) via both UA string and
            # Sec-CH-UA client hints. A hardcoded UA from an older Chrome
            # version mismatches Sec-CH-UA (which is derived from the actual
            # binary, not the override) — anti-fraud middleware on BankID-style
            # auth flows reads both and rejects the session when they disagree.
            no_viewport=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--disable-session-crashed-bubble",
                "--no-restore-state",
                *ext_args,
            ],
            ignore_default_args=["--enable-automation"],
        )
        # Module-level singleton so workflows (which only receive `page`) can
        # reach MirrorBrowser.provider_data. Used by Gecko V2 sync_history to
        # read the interceptor-cached coupon-history body.
        global _ACTIVE_BROWSER
        _ACTIVE_BROWSER = self

        # Network-level ghost-tab blocker. Set up FIRST — before any other
        # async work — so route handlers are registered with Chromium before
        # session-restore navigations get a chance to complete. Within a
        # bounded grace window, abort top-level document loads to non-
        # allowlisted hosts. Subresources and subframes always pass through;
        # in-tab login redirects (kalshi → magic.link, etc.) happen well after
        # the grace window expires. The aborted tab still exists as a Page
        # object but never paints — _on_page_created closes it on first
        # navigation event.
        import time as _time

        self._ghost_block_deadline = _time.monotonic() + 8.0
        await self._context.route("**/*", self._ghost_route_filter)

        # Close ALL tabs from previous sessions — only open what user clicks.
        # Persistent context restores the last session's tabs. Open a fresh
        # blank tab first so the context stays alive while we close every
        # restored tab (including the first — earlier code only navigated it
        # to about:blank, which silently failed leaving stale tabs visible).
        restored_pages = list(self._context.pages)
        keeper = await self._context.new_page()
        # Mark the keeper as explicit so its about:blank page can't be
        # mis-classified as a ghost. Cleared on close.
        self._explicit_pages.add(keeper)
        keeper.once("close", lambda _p=keeper: self._explicit_pages.discard(_p))
        try:
            await keeper.goto("about:blank")
        except Exception:
            pass
        for page in restored_pages:
            try:
                old_url = page.url[:60]
                await page.close()
                print(f"[browser] Closed restored tab: {old_url}", flush=True)
            except Exception:
                pass

        # Attach interception to ALL existing + future pages
        for page in self._context.pages:
            self._attach_page(page)
        self._context.on("page", lambda p: self._attach_page(p))

        # Stray-tab killer: even with Sessions/ wiped + --no-restore-state,
        # Chromium occasionally async-restores tabs (dbet etc.) AFTER our
        # synchronous cleanup has run. Two-layer defense:
        #   (a) Per-page event guard: catches tabs opened post-launch (within
        #       _STRAY_TAB_WINDOW_S grace window) — registered via context.on.
        #   (b) Delayed second-pass close: 3s after launch, sweep ALL existing
        #       tabs and close any non-allowlisted ones. Catches the race where
        #       Chromium async-restores tabs in a window where (a) wasn't yet
        #       registered AND restored_pages snapshot above already passed.
        # Allowlisted = the 4 unlimited counter providers + TradingView (chart)
        # + about:blank/chrome://. /mirror/start re-opens unlimited tabs
        # explicitly so this never closes a tab we wanted.
        self._context.on("page", lambda p: self._guard_stray_tab(p))
        # Initial guard for pages that already exist (the keeper, plus any
        # async-restored pages that got created between launch_persistent_context
        # returning and the context.on("page") registration above). Without
        # this, a Chromium-restored ghost tab that materialized during the
        # registration race never gets a framenavigated handler attached.
        for p in list(self._context.pages):
            self._guard_stray_tab(p)
        self._spawn(self._delayed_stray_sweep(), name="delayed_stray_sweep")

        self._running = True
        print("[browser] Mirror browser started (persistent profile)", flush=True)
        return self._context

    # ------------------------------------------------------------------
    # Stray-tab guard
    # ------------------------------------------------------------------

    _STRAY_TAB_WINDOW_S = 120.0
    # Static allowlist — domains we ALWAYS allow regardless of how the tab got
    # opened. The 4 unlimited counters are eagerly opened by /mirror/start;
    # TradingView is opened by the tv-overlay auto-open task.
    _ALLOWED_TAB_DOMAINS = (
        "polymarket.com",
        "pinnacle.se",
        "cloudbet.com",
        "kalshi.com",
        "tradingview.com",
        "127.0.0.1",
        "localhost",
    )

    def _is_tab_allowed(self, url: str) -> bool:
        """Allowlist check used by both startup guards and the permanent watchdog.

        A tab is allowed if its URL is empty/blank/internal, matches the static
        allowlist, OR is on a domain we intentionally opened via open_tab().
        Soft-provider tabs are added to _dynamic_allowlist when the user clicks
        a provider button — see open_tab().
        """
        from ._urls import hostname_matches

        u = (url or "").lower()
        if not u or u == "about:blank" or u.startswith("chrome:") or u.startswith("devtools:"):
            return True
        if any(hostname_matches(d, u) for d in self._ALLOWED_TAB_DOMAINS):
            return True
        dyn = getattr(self, "_dynamic_allowlist", None) or ()
        if any(hostname_matches(d, u) for d in dyn):
            return True
        return False

    # Hosts known to be Chromium ghost-restored despite our wipe. The route
    # filter aborts top-level document loads to these hosts during the
    # grace window. Default-allow everything else so cloudbet / TV / login
    # redirects always work, even within the grace window.
    _GHOST_DENYLIST: tuple[str, ...] = (
        "dbet.com",
        "dbet.se",
    )

    async def _ghost_route_filter(self, route) -> None:
        """Always abort top-level document loads to known-ghost hosts.
        Default-allow everything else.

        Originally we only blocked during a startup grace window, but
        Chromium's session-restore hijack actually fires tens of seconds
        after launch — observed: cloudbet's tab was alive at t=33s and
        hijacked-then-closed by t=63s. The denylist is precise (only
        `dbet.com` / `dbet.se` main-frame docs) so we leave the filter
        permanently armed without breaking anything legitimate. Aborting
        at the network layer prevents Chromium from completing the
        navigation, so the page that *would* have been hijacked stays on
        its previous URL — cloudbet.com survives.
        """
        try:
            request = route.request
            # Subresources always pass — only top-level document loads matter.
            if request.resource_type != "document":
                await route.continue_()
                return
            # Subframe document → parent already passed.
            frame = request.frame
            if frame is not None and frame.parent_frame is not None:
                await route.continue_()
                return
            url = request.url
            from ._urls import hostname_matches

            if not any(hostname_matches(d, url) for d in self._GHOST_DENYLIST):
                await route.continue_()
                return
            # Denylist hit: abort. Even when the source page is in
            # _explicit_pages — Chromium session restore navigates an
            # existing (explicit) tab to a saved URL, and we want to
            # stop that at the network layer so the original URL is
            # preserved.
            print(f"[browser] Blocked ghost navigation: {url[:120]}", flush=True)
            await route.abort("aborted")
        except Exception as e:
            print(f"[browser] _ghost_route_filter error ({type(e).__name__}): {e}", flush=True)
            try:
                await route.continue_()
            except Exception:
                pass

    def _trust_page_and_its_popups(self, page: Page) -> None:
        """Add `page` to _explicit_pages AND wire `popup` events so any window
        Cloudbet/etc. opens for SSO/OAuth/BankID inherits the same trust.

        Without this, the stray-tab watchdog kills OAuth popups the moment
        they navigate off the host site to accounts.google.com / appleid.apple.com /
        gateway.zignsec.com / etc., because the popup's URL isn't in the static
        allowlist and the popup itself was never opened via open_tab. The
        user sees "popup for SSO login has been closed before finalizing the
        operation" (Cloudbet) or a silent BankID failure.

        Recursive: a popup spawned by a popup is also trusted, so multi-hop
        OAuth flows (e.g. Google → site → callback popup) survive.
        """
        self._explicit_pages.add(page)
        page.once("close", lambda _p=page: self._explicit_pages.discard(_p))
        # When this page opens a popup (window.open / target=_blank / SSO),
        # the new Page is delivered synchronously here BEFORE its first
        # framenavigated fires — so adding it to _explicit_pages now is in
        # time to bypass _guard_stray_tab's close check.
        page.on("popup", lambda popup: self._trust_page_and_its_popups(popup))

    def _guard_stray_tab(self, page: Page) -> None:
        """Close `page` the moment it navigates to a non-allowlisted URL.

        Uses Playwright's `framenavigated` event instead of polling — fires
        as soon as Chromium decides the destination URL, before the document
        body has had a chance to paint. Combined with the network-level
        route filter (which aborts the document load entirely during the
        startup grace window), this makes the dbet ghost-tab path:
            (a) document request aborted by route filter → empty page
            (b) framenavigated emits with about:blank or chrome-error://
            (c) page.close() runs synchronously
        Net result: no visible flash, regardless of what URL Chromium tried
        to restore.
        """

        def _on_nav(frame) -> None:
            try:
                if frame.parent_frame is not None:
                    return  # subframe, not a top-level navigation
                url = (frame.url or "").lower()
                if not url:
                    return
                # Denylist trumps everything — even a page we explicitly opened
                # (e.g. cloudbet) can be hijacked by Chromium session restore
                # which navigates an existing tab to a saved URL instead of
                # creating a new one. Without this, cloudbet's Page object
                # stays in _explicit_pages while its URL silently became
                # https://www.dbet.com/... (observed empirically).
                from ._urls import hostname_matches as _hm

                if any(_hm(d, url) for d in self._GHOST_DENYLIST):
                    self._spawn(_close_safely(url), name="guard_stray_close")
                    return
                # Page was explicitly opened by us (open_tab / keeper) — trust
                # the navigation regardless of destination. Cloudbet does geo
                # redirects, TradingView redirects to chart subdomains, OAuth
                # flows redirect to magic.link / SSO providers; closing those
                # silently breaks every legitimate flow we have.
                try:
                    page_obj = frame.page
                except Exception:
                    page_obj = None
                if page_obj is not None and page_obj in self._explicit_pages:
                    return
                # chrome-error://chromewebdata/ shows up when the route filter
                # aborts a document — close the now-useless tab.
                is_chrome_error = url.startswith("chrome-error:")
                if not is_chrome_error and self._is_tab_allowed(url):
                    return
                self._spawn(_close_safely(url), name="guard_stray_close")
            except Exception:
                pass

        async def _close_safely(url: str) -> None:
            try:
                if page.is_closed():
                    return
                await page.close()
                print(f"[browser] Closed stray restored tab: {url[:80]}", flush=True)
            except Exception:
                pass

        try:
            page.on("framenavigated", _on_nav)
        except Exception:
            pass

    async def _delayed_stray_sweep(self) -> None:
        """Permanent background watchdog — runs forever while the browser is up.

        Sweeps every 10s and closes any tab that isn't allowlisted (static
        allowlist + dynamic allowlist populated by open_tab). Chromium has
        repeatedly demonstrated it can lazily materialize ghost tabs minutes
        after launch (e.g. dbet.com appearing despite no History entry, no
        bookmark, no startup URL, no explicit code path opening it). Bounded
        grace windows kept missing them. A permanent watchdog is the only
        defense that doesn't leak.

        Soft-provider tabs survive because PlayPage.startSkin → /mirror/open-
        provider-tab → open_tab() adds the domain to _dynamic_allowlist
        before the tab navigation completes.
        """
        try:
            print("[browser] watchdog: started", flush=True)
            await asyncio.sleep(3.0)  # initial settle
            tick = 0
            while True:
                if not self._context:
                    print("[browser] watchdog: context gone, exiting", flush=True)
                    return
                tick += 1
                pages = list(self._context.pages)
                non_explicit = [p for p in pages if p not in self._explicit_pages]
                if tick % 3 == 1:  # every ~30s, summarize with URLs
                    summary_lines = [
                        f"[browser] watchdog tick {tick}: {len(pages)} pages "
                        f"({len(self._explicit_pages)} in _explicit_pages, "
                        f"{len(non_explicit)} stray-candidates):"
                    ]
                    for p in pages:
                        try:
                            tag = "explicit" if p in self._explicit_pages else "STRAY"
                            url_short = (p.url or "")[:80]
                            summary_lines.append(f"    [{tag}] {url_short}")
                        except Exception:
                            summary_lines.append("    [???] <page url unreadable>")
                    print("\n".join(summary_lines), flush=True)
                from ._urls import hostname_matches as _hm

                for page in pages:
                    try:
                        url = (page.url or "").lower()
                        # Denylist trumps explicit_pages — same reason as in
                        # _guard_stray_tab: Chromium session restore can
                        # navigate an existing (explicit) tab to a saved
                        # ghost URL like dbet.com, leaving the Page object
                        # in our explicit set with a hijacked URL.
                        if any(_hm(d, url) for d in self._GHOST_DENYLIST):
                            await page.close()
                            print(
                                f"[browser] Watchdog closed denylisted tab: {url[:80]}",
                                flush=True,
                            )
                            continue
                        if page in self._explicit_pages:
                            continue
                        if self._is_tab_allowed(url):
                            continue
                        await page.close()
                        print(f"[browser] Watchdog closed stray tab: {url[:80]}", flush=True)
                    except Exception as e:
                        print(f"[browser] watchdog inner error: {type(e).__name__}: {e}", flush=True)
                await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            print("[browser] watchdog: cancelled", flush=True)
        except Exception as e:
            print(f"[browser] watchdog: died with {type(e).__name__}: {e}", flush=True)

    async def stop(self):
        if not self._running:
            return
        try:
            # Persistent context auto-saves to disk — no manual save needed
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            logger.exception("Error closing mirror browser")
        finally:
            self._running = False
            self._context = None
            self._browser = None
            self._playwright = None
            self.provider_data.clear()
            logger.info("Mirror browser stopped")

    @staticmethod
    async def _kill_orphaned_chromium():
        """Kill Chromium processes from previous sessions holding the profile lock.

        wmic + taskkill are blocking subprocesses (wmic alone takes 1-3s on
        Windows). Running them on the asyncio loop freezes every other task
        for the duration — including Playwright IPC, the dashboard WS server,
        and the SignalRelay sender. Offloaded to a thread.
        """
        import sys

        if sys.platform != "win32":
            return

        def _do_kill() -> int:
            import subprocess

            try:
                result = subprocess.run(
                    [
                        "wmic",
                        "process",
                        "where",
                        "name='chromium.exe' or name='chrome.exe'",
                        "get",
                        "processid,commandline",
                        "/format:csv",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                return 0
            profile_str = str(_USER_DATA_DIR).replace("\\", "/")
            profile_str_win = str(_USER_DATA_DIR)
            killed = 0
            for line in result.stdout.splitlines():
                if (profile_str in line or profile_str_win in line or "browser_profile" in line) and (
                    "disable-blink-features" in line
                ):
                    parts = line.strip().split(",")
                    if parts:
                        pid = parts[-1].strip()
                        if pid.isdigit():
                            try:
                                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
                                killed += 1
                            except Exception:
                                pass
            return killed

        try:
            killed = await asyncio.to_thread(_do_kill)
            if killed:
                print(f"[browser] Killed {killed} orphaned Chromium process(es)", flush=True)
                await asyncio.sleep(1)
        except Exception:
            pass

    async def open_tab(self, url: str) -> Page:
        if not self._context:
            raise RuntimeError("Browser not started")
        # Whitelist this domain BEFORE we navigate so the watchdog/guard don't
        # race-close the tab between new_page() and goto().
        try:
            from urllib.parse import urlparse

            host = (urlparse(url).hostname or "").lower()
            if host:
                # Strip leading "www." so the substring match in _is_tab_allowed
                # catches both apex and www variants.
                self._dynamic_allowlist.add(host[4:] if host.startswith("www.") else host)
        except Exception:
            pass
        # Lazy-init the lock here — __init__ runs without an event loop so we
        # can't construct asyncio.Lock there. Once created the same lock is
        # reused for the lifetime of the browser instance.
        if self._open_tab_lock is None:
            self._open_tab_lock = asyncio.Lock()
        # Atomic page acquisition: two concurrent open_tab calls used to scan
        # context.pages for an about:blank tab and pick the same one (TV's
        # auto-open + /mirror/start cloudbet step in parallel — cloudbet's
        # goto preempted TV's nav). Always create a fresh page so each caller
        # gets a distinct tab. The boot keeper persists harmlessly as one
        # extra about:blank.
        async with self._open_tab_lock:
            page = await self._context.new_page()
            # Mark explicit BEFORE the goto fires so the route filter and
            # framenavigated guard both see this page as user-opened.
            self._trust_page_and_its_popups(page)
            # Attach interceptor BEFORE navigating so we catch all responses.
            self._attach_page(page)
            print(f"[browser] Opening tab: {url}", flush=True)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"[browser] Navigation slow/failed ({e}), tab still usable", flush=True)
        try:
            from .state_writer import write_provider_state

            pid = self._detect_provider(url)
            if pid:
                write_provider_state(pid, tab_open=True, tab_url=url)
        except Exception as e:
            logger.debug(f"[browser] state_writer (open_tab) failed: {e!r}")
        return page

    def get_status(self) -> dict:
        pages = []
        if self._context:
            for page in self._context.pages:
                pages.append({"url": page.url, "title": page.url.split("/")[2] if "/" in page.url else ""})
        return {
            "running": self._running,
            "tabs": len(pages),
            "pages": pages,
            "providers": self.provider_data,
        }

    def is_logged_in(self, provider_id: str) -> bool:
        """Check if a provider is logged in based on intercepted data."""
        return self.provider_data.get(provider_id, {}).get("logged_in", False)

    def get_balance(self, provider_id: str) -> float | None:
        """Get last known balance for a provider."""
        return self.provider_data.get(provider_id, {}).get("balance")

    async def check_login_dom(self, provider_id: str) -> dict:
        """Check login by scraping balance from DOM — fallback when interception misses."""
        if not self._context:
            return {"logged_in": False}
        from ._urls import hostname_matches
        from .workflows import get_workflow

        workflow = get_workflow(provider_id)
        page = None
        for p in self._context.pages:
            if workflow.domain and hostname_matches(workflow.domain, p.url):
                page = p
                break
        if not page:
            return {"logged_in": False, "reason": "no_tab"}
        try:
            scrape = await page.evaluate(r"""() => {
                // 1. Balance — header/nav/toolbar area only, not promo banners.
                let balance = null;
                const navEls = document.querySelectorAll('header, nav, [class*="header"], [class*="toolbar"], [class*="balance"], [class*="user"], [class*="account"], [class*="wallet"]');
                for (const el of navEls) {
                    const text = el.innerText || '';
                    const m = text.match(/(\d+[,.\s]\d{2})\s*KR/i);
                    if (m) { balance = parseFloat(m[1].replace(/\s/g, '').replace(',', '.')); break; }
                }
                // Polymarket / USD providers: "Cash $XX.XX" in nav. Match even
                // when balance is $0 — a logged-in user with zero cash is still
                // logged in and we don't want to treat them as logged-out.
                if (balance === null) {
                    const allNav = document.querySelectorAll('nav, nav *, header, header *');
                    for (const el of allNav) {
                        const text = el.textContent || '';
                        const m2 = text.match(/Cash\s*\$\s*(\d+(?:[,.]\d+)?)/i);
                        if (m2) { balance = parseFloat(m2[1].replace(',', '.')); break; }
                    }
                }
                // 2. Login signals — independent of balance, so $0 users still register.
                const text = (document.body.innerText || '').slice(0, 5000);
                const hasLoginCTA = /\b(Log In|Sign Up|Connect Wallet|Logga in)\b/i.test(text);
                const hasDeposit = /\b(Deposit|Deponera|Insättning)\b/i.test(text);
                const hasProfileMenu = !!document.querySelector(
                    'a[href*="/profile"], a[href*="/portfolio"], a[href*="account" i], button[aria-label*="profile" i], [data-testid*="profile" i], [class*="avatar" i]'
                );
                const hasLogoutCTA = /\b(Log Out|Sign Out|Logga ut)\b/i.test(text);
                const loggedInSignals = [];
                if (balance !== null) loggedInSignals.push('balance');
                if (hasDeposit && !hasLoginCTA) loggedInSignals.push('deposit_no_login_cta');
                if (hasProfileMenu && !hasLoginCTA) loggedInSignals.push('profile_no_login_cta');
                if (hasLogoutCTA) loggedInSignals.push('logout_cta');
                return {
                    balance,
                    logged_in: loggedInSignals.length > 0,
                    signals: loggedInSignals,
                };
            }""")
            if scrape and scrape.get("logged_in"):
                balance = scrape.get("balance")
                if provider_id not in self.provider_data:
                    self.provider_data[provider_id] = {}
                self.provider_data[provider_id]["logged_in"] = True
                if balance is not None:
                    self.provider_data[provider_id]["balance"] = balance
                self.provider_data[provider_id]["source"] = "dom"
                try:
                    from .state_writer import write_provider_state

                    write_provider_state(provider_id, logged_in=True, balance=balance)
                except Exception as e:
                    logger.debug(f"[browser] state_writer failed: {e!r}")
                if self._on_event and balance is not None:
                    self._on_event(
                        "balance_intercepted",
                        {
                            "provider_id": provider_id,
                            "balance": balance,
                            "source": "dom",
                        },
                    )
                return {
                    "logged_in": True,
                    "balance": balance,
                    "signals": scrape.get("signals", []),
                }
        except Exception:
            pass
        return {"logged_in": False}

    # ------------------------------------------------------------------
    # Interception
    # ------------------------------------------------------------------

    def _attach_page(self, page: Page):
        """Attach response + WebSocket + frame-nav listeners to a page."""
        print(f"[browser] ATTACHING interceptor to page: {page.url[:80]}", flush=True)

        async def handle_response(resp):
            await self._safe_on_response(resp)

        page.on("response", lambda resp: asyncio.ensure_future(handle_response(resp)))
        page.on("websocket", lambda ws: self._on_websocket(ws, page))
        page.on("framenavigated", lambda frame: self._on_frame_navigated(frame, page))

    def _on_frame_navigated(self, frame, page: Page) -> None:
        """Detect the user manually browsing to a matchup page and emit a
        provider-specific nav event so the frontend can auto-pick the
        matching arb. Parses the URL only — no API calls — so it's cheap
        enough to run on every frame nav.
        """
        # Only main-frame navigations on the page itself (not iframes /
        # tracking pixels).
        try:
            if frame != page.main_frame:
                return
        except Exception:
            return
        url = frame.url or ""
        if not url:
            return
        provider_id = self._detect_provider(url)
        if not provider_id:
            return

        # Pinnacle: canonical URL pattern is
        #   /<lang>/<sport>/<league>/<home>-vs-<away>/<matchup_id>/
        # Where <lang> is sv|en. The bare /<lang>/matchup/<id>/ URL doesn't
        # render content so it's not interesting here.
        if provider_id == "pinnacle":
            import re

            m = re.search(
                r"/(?:sv|en)/(?P<sport>[a-z0-9-]+)/(?P<league>[a-z0-9-]+)/(?P<teams>[a-z0-9-]+-vs-[a-z0-9-]+)/(?P<matchup_id>\d+)/?",
                url,
            )
            if not m:
                return
            teams = m.group("teams")
            try:
                home_slug, away_slug = teams.split("-vs-", 1)
            except ValueError:
                return
            payload = {
                "provider_id": provider_id,
                "matchup_id": m.group("matchup_id"),
                "sport_slug": m.group("sport"),
                "league_slug": m.group("league"),
                "home_slug": home_slug,
                "away_slug": away_slug,
                "url": url,
            }
            if self._on_event:
                try:
                    self._on_event("provider_manual_nav", payload)
                except Exception as e:
                    logger.debug(f"[browser] provider_manual_nav publish failed: {e!r}")

    async def _safe_on_response(self, response: Response):
        """Wrapper to catch all errors in response handler."""
        try:
            await self._on_response(response)
        except Exception:
            pass  # Never let interceptor errors break browsing

    async def _on_response(self, response: Response):
        """Classify and process HTTP responses."""
        url = response.url
        status = response.status

        if status < 200 or status >= 400:
            return

        # Skip third-party trackers/analytics. Their URLs frequently embed the
        # provider's page URL as a query param (e.g. fb.com/tr?dl=...bethistory),
        # which would otherwise spuriously match our keyword interceptors.
        try:
            from urllib.parse import urlparse

            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        if host and any(host == suffix or host.endswith("." + suffix) for suffix in _TRACKER_HOST_SUFFIXES):
            return

        # Detect provider from PAGE URL (which tab made the request)
        # API requests go to third-party domains (biahosted.com, kambi.com)
        # but the page URL tells us which provider we're on
        try:
            page_url = response.frame.page.url
        except Exception:
            page_url = ""
        provider_id = self._detect_provider(page_url) or self._detect_provider(url)

        # Log API calls for debugging (skip static assets)
        if provider_id and not any(ext in url for ext in (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".ico")):
            if any(
                kw in url.lower()
                for kw in ("api", "balance", "wallet", "account", "relay", "graphql", "login", "auth", "session")
            ):
                print(f"[intercept] {provider_id} API: {url[:120]}", flush=True)

        if not provider_id:
            return

        url_lower = url.lower()

        # Balance / financial
        if any(kw in url_lower for kw in _BALANCE_KEYWORDS):
            try:
                body_text = await response.text()
                body = json.loads(body_text)
            except Exception:
                return
            balance = self._extract_balance(body)
            if balance is not None and balance >= 0:
                # Polymarket balance-allowance returns USDC in raw wei (6 decimals)
                if provider_id == "polymarket" and balance > 1_000_000:
                    balance = balance / 1e6
                # Kalshi /balance returns int cents — scale to dollars.
                # /balance only (we filter out /balances + /event_positions etc.
                # via the URL keyword check above; only the singular /balance
                # endpoint reaches here as a {balance: int} payload).
                if provider_id == "kalshi" and "/balance" in url_lower and not url_lower.endswith("/balances"):
                    balance = balance / 100.0
                if provider_id not in self.provider_data:
                    self.provider_data[provider_id] = {}
                self.provider_data[provider_id]["logged_in"] = True
                self.provider_data[provider_id]["balance"] = balance
                self.provider_data[provider_id]["last_balance_url"] = url
                logger.info(f"[browser] {provider_id} BALANCE: {balance} (from {url[:80]})")
                try:
                    from .state_writer import write_provider_state

                    write_provider_state(provider_id, logged_in=True, balance=balance)
                except Exception as e:
                    logger.debug(f"[browser] state_writer failed: {e!r}")
                if self._on_event:
                    self._on_event(
                        "balance_intercepted",
                        {
                            "provider_id": provider_id,
                            "balance": balance,
                            "url": url,
                        },
                    )
                if self._on_stream_callback:
                    self._on_stream_callback(provider_id, "balance_intercepted", {"balance": balance})
            return

        # GraphQL relay (LeoVegas etc.) — check body for balance data
        if "relay" in url_lower or "graphql" in url_lower:
            try:
                body_text = await response.text()
                if '"balance"' in body_text and ('"totalAmount"' in body_text or '"amount"' in body_text):
                    body = json.loads(body_text)
                    balance = self._extract_balance(body)
                    if balance is not None and balance >= 0:
                        if provider_id not in self.provider_data:
                            self.provider_data[provider_id] = {}
                        self.provider_data[provider_id]["logged_in"] = True
                        self.provider_data[provider_id]["balance"] = balance
                        logger.info(f"[browser] {provider_id} BALANCE (relay): {balance}")
                        try:
                            from .state_writer import write_provider_state

                            write_provider_state(provider_id, logged_in=True, balance=balance)
                        except Exception as e:
                            logger.debug(f"[browser] state_writer failed: {e!r}")
                        if self._on_event:
                            self._on_event(
                                "balance_intercepted",
                                {
                                    "provider_id": provider_id,
                                    "balance": balance,
                                    "url": url,
                                },
                            )
                        if self._on_stream_callback:
                            self._on_stream_callback(provider_id, "balance_intercepted", {"balance": balance})
            except Exception:
                pass
            return

        # Bet history
        if any(kw in url_lower for kw in _HISTORY_KEYWORDS):
            try:
                body = await response.text()
                logger.info(f"[browser] {provider_id} history: {url[:80]} ({len(body)}b)")
                # Cache the parsed body on provider_data so workflow.sync_history
                # can read it without making its own API call. Several Gecko V2
                # providers hijack window.fetch (Spelklubben's GTM tracker.js
                # poisons it cross-origin) — the page itself can fetch
                # coupon-history but our re-issued call gets blocked. Riding the
                # page's own response is the only path that actually works.
                #
                # Key by URL query so Open + Settled views (separate calls) are
                # both kept — sync_history needs BOTH (DB-pending bets often
                # match settled entries on the provider, that's reconciliation).
                # `coupon_history_raw` is also written for back-compat with
                # readers that only want "the latest".
                try:
                    parsed = json.loads(body) if body else None
                except Exception:
                    parsed = None
                if parsed is not None:
                    self.provider_data.setdefault(provider_id, {})["coupon_history_raw"] = parsed
                    by_url = self.provider_data[provider_id].setdefault("coupon_history_by_url", {})
                    by_url[url] = parsed
                if self._on_event:
                    self._on_event(
                        "history_intercepted",
                        {
                            "provider_id": provider_id,
                            "url": url,
                            "size": len(body),
                        },
                    )
            except Exception:
                pass
            return

        # Altenar GetEventDetails — live odds for price sync
        if "geteventdetails" in url_lower:
            try:
                body_text = await response.text()
                body_parsed = json.loads(body_text)
                # Extract eventId from query params
                from urllib.parse import parse_qs, urlparse

                qs = parse_qs(urlparse(url).query)
                event_id = qs.get("eventId", [None])[0]
                if event_id and body_parsed:
                    logger.info(f"[browser] {provider_id} EventDetails: event={event_id}")
                    if self._on_event:
                        self._on_event(
                            "event_details_intercepted",
                            {
                                "provider_id": provider_id,
                                "event_id": event_id,
                                "body": body_parsed,
                            },
                        )
            except Exception:
                pass
            return

        # Altenar GetOddsStates — periodic price-drift push for events visible
        # in the widget. Body shape: {oddStates: [{id, price, oddStatus, ...}]}
        # We use it to update the per-odd cache so check_live_price returns
        # fresh prices without waiting for a full GetEventDetails refresh.
        if "getoddsstates" in url_lower:
            try:
                body_text = await response.text()
                body_parsed = json.loads(body_text)
                preview = str(body_parsed)[:200]
                print(f"[OddsStates] {provider_id} body[:200]={preview}", flush=True)
                if body_parsed and self._on_event:
                    self._on_event(
                        "odds_states_intercepted",
                        {
                            "provider_id": provider_id,
                            "body": body_parsed,
                        },
                    )
            except Exception as e:
                print(f"[OddsStates] {provider_id} parse failed: {e!r}", flush=True)
            return

        # Bet placement
        if any(kw in url_lower for kw in _BET_PLACEMENT_KEYWORDS):
            # Method gate — only POST/PUT count as placement. Some keyword
            # patterns (e.g. Kalshi's /v1/users/<U>/orders) match GET reads
            # too (resting orders, fills) which would otherwise be misclassified
            # as placements and broadcast bet_intercepted.
            try:
                request_method = (response.request.method or "GET").upper()
            except Exception:
                request_method = "GET"
            if request_method not in ("POST", "PUT"):
                return
            try:
                # Capture request body — contains the actual stake submitted by the browser
                # (may differ from requested stake if WSDK/site capped it)
                request_body: dict | None = None
                request_headers: dict = {}
                try:
                    post_data = response.request.post_data
                    if post_data:
                        request_body = json.loads(post_data)
                except Exception:
                    pass
                try:
                    # Record request headers so we can replay auth on scripted calls
                    # (Pinnacle requires per-session JWT/fingerprint — capture once here).
                    request_headers = await response.request.all_headers()
                except Exception:
                    pass

                body_text = await response.text()
                body_parsed = json.loads(body_text)
                logger.warning(
                    f"[browser] {provider_id} BET PLACED\n  URL: {url}\n"
                    f"  HEADERS: {json.dumps(request_headers, indent=2)[:2000]}\n"
                    f"  BODY: {json.dumps(request_body, indent=2)[:1200] if request_body else '(none)'}"
                )
                if self._on_event:
                    self._on_event(
                        "bet_intercepted",
                        {
                            "provider_id": provider_id,
                            "url": url,
                            "body": body_parsed,
                            "request_body": request_body,
                        },
                    )
                if self._on_stream_callback:
                    self._on_stream_callback(provider_id, "bet_intercepted", {"body": body_parsed})
            except Exception:
                pass
            return

    def _on_websocket(self, ws: WebSocket, page: Page):
        """Monitor WebSocket connections for Kambi bet placement frames."""
        url = ws.url
        if not any(kw in url.lower() for kw in _WS_MONITOR_KEYWORDS):
            return

        provider_id = self._detect_provider(page.url)
        logger.info(f"[browser] WS connected: {url[:80]} (provider={provider_id})")

        def _on_frame_received(payload: str | bytes):
            try:
                if not isinstance(payload, str):
                    return
                if not any(kw in payload for kw in _WS_BET_RECEIVED_KEYWORDS):
                    return

                pid = provider_id or self._detect_provider(page.url)
                if not pid:
                    return

                logger.info(f"[browser] {pid} WS BET PLACED ({len(payload)} bytes)")
                body = json.loads(payload)

                if self._on_event:
                    self._on_event(
                        "bet_intercepted",
                        {
                            "provider_id": pid,
                            "url": url,
                            "body": body,
                            "request_body": None,
                            "source": "websocket",
                        },
                    )
                if self._on_stream_callback:
                    self._on_stream_callback(pid, "bet_intercepted", {"body": body})
            except Exception:
                pass

        def _on_frame_sent(payload: str | bytes):
            try:
                if not isinstance(payload, str):
                    return
                if not any(kw in payload for kw in _WS_BET_SENT_KEYWORDS):
                    return
                pid = provider_id or self._detect_provider(page.url)
                logger.info(f"[browser] {pid} WS bet request sent ({len(payload)} bytes)")
            except Exception:
                pass

        ws.on("framereceived", _on_frame_received)
        ws.on("framesent", _on_frame_sent)

    def _detect_provider(self, page_url: str) -> str | None:
        """Detect provider_id from a page URL."""
        for domain, pid in _DOMAIN_TO_PROVIDER.items():
            if domain in page_url:
                return pid
        return None

    def _extract_balance(self, body: Any) -> float | None:
        """Extract balance from various response shapes."""
        if not isinstance(body, dict):
            return None
        # Altenar: {result: {cash: {total: X}, bonus: {total: Y}}} — sum all wallets
        data = body.get("result", body) if "result" in body else body
        if isinstance(data, dict) and any(w in data for w in ("cash", "bonus", "sport")):
            total = 0.0
            for wallet in ("cash", "bonus", "sport"):
                try:
                    total += float(data[wallet]["total"])
                except (KeyError, TypeError, ValueError):
                    continue
            if total > 0:
                return total
            # If all wallets are 0, still return 0 (logged in with empty balance)
            if any(w in data for w in ("cash", "bonus", "sport")):
                return 0.0
        # Gecko: {Balances: {SEK: {Real: {Balance: 907.14}}}}
        try:
            return float(body["Balances"]["SEK"]["Real"]["Balance"])
        except (KeyError, TypeError, ValueError):
            pass
        # Kambi: {mainBalance: {amount: 515.3}}
        try:
            return float(body["mainBalance"]["amount"])
        except (KeyError, TypeError, ValueError):
            pass
        # Generic: {balance: 123.45} or {amount: 123.45}
        for key in ("balance", "amount", "availableBalance", "total"):
            if key in body:
                val = body[key]
                try:
                    return float(val) if not isinstance(val, dict) else float(val.get("amount", val.get("total", -1)))
                except (TypeError, ValueError):
                    pass
        # GraphQL relay: {data: {viewer: {user: {balance: {totalAmount: X}}}}}
        # Also handles list wrapper: [{data: {viewer: ...}}]
        relay = body
        if isinstance(body, list) and body:
            relay = body[0]
        if isinstance(relay, dict):
            try:
                bal = relay.get("data", {}).get("viewer", {}).get("user", {}).get("balance", {})
                if isinstance(bal, dict) and "totalAmount" in bal:
                    return float(bal["totalAmount"])
            except (TypeError, ValueError, AttributeError):
                pass
        # Wallets array: [{balance: 123}]
        if "wallets" in body and isinstance(body["wallets"], list) and body["wallets"]:
            try:
                return float(body["wallets"][0].get("balance", -1))
            except (TypeError, ValueError):
                pass
        return None
