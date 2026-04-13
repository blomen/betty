"""Playwright browser lifecycle — launch, manage tabs, intercept traffic."""

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path  # noqa: F401
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
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
)
_HISTORY_KEYWORDS = (
    "bethistory",
    "bet-history",
    "mybets",
    "my-bets",
    "widgetbethistory",
    "coupon-history",
)
_BET_PLACEMENT_KEYWORDS = (
    "placewidget",
    "placebet",
    "/coupons",
    "bets/straight",
    "bets/parlay",
    "bets/place",
    "clob.polymarket.com/order",
)

# Provider domain → provider_id mapping
_DOMAIN_TO_PROVIDER: dict[str, str] = {
    "betinia.se": "betinia",
    "quickcasino.se": "quickcasino",
    "campobet.se": "campobet",
    "comeon.com": "comeon",
    "unibet.se": "unibet",
    "leovegas.se": "leovegas",
    "expekt.se": "expekt",
    "spelklubben.com": "spelklubben",
    "betsson.se": "betsson",
    "nordicbet.com": "nordicbet",
    "betsafe.se": "betsafe",
    "pinnacle.se": "pinnacle",
    "interwetten.se": "interwetten",
    "coolbet.com": "coolbet",
    "vbet.com": "vbet",
    "10bet.com": "10bet",
    "polymarket.com": "polymarket",
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
        if self._running:
            return self._context

        # Kill any orphaned Chromium holding the profile lock
        await self._kill_orphaned_chromium()

        self._playwright = await async_playwright().start()

        # Persistent context = real Chrome profile on disk.
        # Cookies, localStorage, service workers all survive restarts.
        _USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[browser] Using profile: {_USER_DATA_DIR}", flush=True)

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(_USER_DATA_DIR),
            headless=False,
            locale="en-GB",
            timezone_id="Europe/Stockholm",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            no_viewport=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

        # Close dead tabs from previous sessions (chrome-error, about:blank)
        # Keep at least one page so the context stays alive
        dead = [p for p in self._context.pages if "chrome-error" in (p.url or "") or p.url == "about:blank"]
        alive = [p for p in self._context.pages if p not in dead]
        if not alive and dead:
            dead = dead[1:]  # keep one so context doesn't die
        for page in dead:
            try:
                await page.close()
                print(f"[browser] Closed dead tab: {page.url[:60]}", flush=True)
            except Exception:
                pass

        # Attach interception to ALL existing + future pages
        for page in self._context.pages:
            self._attach_page(page)
        self._context.on("page", lambda p: self._attach_page(p))

        self._running = True
        print("[browser] Mirror browser started (persistent profile)", flush=True)
        return self._context

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
        """Kill Chromium processes from previous sessions holding the profile lock."""
        import subprocess
        import sys

        if sys.platform != "win32":
            return
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
                            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
                            killed += 1
            if killed:
                print(f"[browser] Killed {killed} orphaned Chromium process(es)", flush=True)
                await asyncio.sleep(1)
        except Exception:
            pass

    async def open_tab(self, url: str) -> Page:
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        # Attach interceptor BEFORE navigating so we catch all responses
        self._attach_page(page)
        print(f"[browser] Opening tab: {url}", flush=True)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[browser] Navigation slow/failed ({e}), tab still usable", flush=True)
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
        from .workflows import get_workflow

        workflow = get_workflow(provider_id)
        page = None
        for p in self._context.pages:
            if workflow.domain in p.url:
                page = p
                break
        if not page:
            return {"logged_in": False, "reason": "no_tab"}
        try:
            balance_text = await page.evaluate(r"""() => {
                // Look for balance in header/nav/toolbar area only — not promo banners
                const navEls = document.querySelectorAll('header, nav, [class*="header"], [class*="toolbar"], [class*="balance"], [class*="user"], [class*="account"], [class*="wallet"]');
                for (const el of navEls) {
                    const text = el.innerText || '';
                    const m = text.match(/(\d+[,.\s]\d{2})\s*KR/i);
                    if (m) return m[1].replace(/\s/g, '').replace(',', '.');
                }
                // Polymarket: look for Cash $XX.XX in nav
                const allNav = document.querySelectorAll('nav, nav *');
                for (const el of allNav) {
                    const text = el.textContent || '';
                    const m2 = text.match(/Cash\s*\$\s*(\d+[,.]\d+)/);
                    if (m2) return m2[1].replace(',', '.');
                }
                return null;
            }""")
            if balance_text:
                balance = float(balance_text)
                if provider_id not in self.provider_data:
                    self.provider_data[provider_id] = {}
                self.provider_data[provider_id]["logged_in"] = True
                self.provider_data[provider_id]["balance"] = balance
                self.provider_data[provider_id]["source"] = "dom"
                if self._on_event:
                    self._on_event(
                        "balance_intercepted",
                        {
                            "provider_id": provider_id,
                            "balance": balance,
                            "source": "dom",
                        },
                    )
                return {"logged_in": True, "balance": balance}
        except Exception:
            pass
        return {"logged_in": False}

    # ------------------------------------------------------------------
    # Interception
    # ------------------------------------------------------------------

    def _attach_page(self, page: Page):
        """Attach response listener to a page."""
        print(f"[browser] ATTACHING interceptor to page: {page.url[:80]}", flush=True)

        async def handle_response(resp):
            await self._safe_on_response(resp)

        page.on("response", lambda resp: asyncio.ensure_future(handle_response(resp)))

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
                if provider_id not in self.provider_data:
                    self.provider_data[provider_id] = {}
                self.provider_data[provider_id]["logged_in"] = True
                self.provider_data[provider_id]["balance"] = balance
                self.provider_data[provider_id]["last_balance_url"] = url
                logger.info(f"[browser] {provider_id} BALANCE: {balance} (from {url[:80]})")
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

        # Bet placement
        if any(kw in url_lower for kw in _BET_PLACEMENT_KEYWORDS):
            try:
                body_text = await response.text()
                body_parsed = json.loads(body_text)
                logger.info(f"[browser] {provider_id} BET PLACED: {url[:80]}")
                if self._on_event:
                    self._on_event(
                        "bet_intercepted",
                        {
                            "provider_id": provider_id,
                            "url": url,
                            "body": body_parsed,
                        },
                    )
                if self._on_stream_callback:
                    self._on_stream_callback(provider_id, "bet_intercepted", {"body": body_parsed})
            except Exception:
                pass
            return

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
        # Wallets array: [{balance: 123}]
        if "wallets" in body and isinstance(body["wallets"], list) and body["wallets"]:
            try:
                return float(body["wallets"][0].get("balance", -1))
            except (TypeError, ValueError):
                pass
        return None
