"""Playwright browser lifecycle — launch, manage tabs, intercept traffic."""
import asyncio
import json
import logging
from typing import Optional, Callable, Any

from playwright.async_api import (
    async_playwright, Browser, BrowserContext, Page, Playwright, Response,
)

logger = logging.getLogger(__name__)

# URL patterns for classifying intercepted responses
_BALANCE_KEYWORDS = (
    "account/balance", "/wallets", "mainbalance", "wallet/balance",
    "payment-stats", "/cashier/balance",
)
_HISTORY_KEYWORDS = (
    "bethistory", "bet-history", "betHistory", "mybets", "my-bets",
    "widgetBetHistory", "coupon-history",
)
_BET_PLACEMENT_KEYWORDS = (
    "placeWidget", "placeBet", "/coupons", "bets/straight",
    "bets/parlay", "bets/place", "clob.polymarket.com/order",
)

# Provider domain → provider_id mapping
_DOMAIN_TO_PROVIDER: dict[str, str] = {
    "betinia.se": "betinia", "quickcasino.com": "quickcasino",
    "campobet.se": "campobet", "comeon.com": "comeon",
    "unibet.se": "unibet", "leovegas.se": "leovegas",
    "expekt.se": "expekt", "spelklubben.com": "spelklubben",
    "betsson.se": "betsson", "nordicbet.com": "nordicbet",
    "betsafe.se": "betsafe", "pinnacle.se": "pinnacle",
    "interwetten.se": "interwetten", "coolbet.com": "coolbet",
    "vbet.com": "vbet", "10bet.com": "10bet",
    "polymarket.com": "polymarket", "tipwin.se": "tipwin",
    "mrgreen.com": "mrgreen", "888sport.com": "888sport",
    "hajper.com": "hajper", "x3000.se": "x3000",
    "speedybet.com": "speedybet", "goldenbull.se": "goldenbull",
}


class MirrorBrowser:
    """Manages a headed Chromium browser with network interception."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._running = False
        # Intercepted data per provider
        self.provider_data: dict[str, dict] = {}  # pid → {logged_in, balance, last_url, ...}
        # Callback for broadcasting events
        self._on_event: Optional[Callable[[str, dict], None]] = None

    def set_event_callback(self, callback: Callable[[str, dict], None]):
        """Set callback for intercepted events (e.g. broadcaster.publish)."""
        self._on_event = callback

    @property
    def running(self) -> bool:
        return self._running

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

    async def start(self) -> BrowserContext:
        if self._running:
            return self._context
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        self._context = await self._browser.new_context(
            viewport=None,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        # Attach interception to all new pages
        self._context.on("page", self._on_new_page)
        self._running = True
        logger.info("Mirror browser started")
        return self._context

    async def stop(self):
        if not self._running:
            return
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
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

    async def open_tab(self, url: str) -> Page:
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
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

    # ------------------------------------------------------------------
    # Interception
    # ------------------------------------------------------------------

    def _on_new_page(self, page: Page):
        """Attach response listener to every new tab."""
        page.on("response", lambda resp: asyncio.ensure_future(self._on_response(resp)))
        logger.info(f"[browser] Attached interceptor to new page")

    async def _on_response(self, response: Response):
        """Classify and process every HTTP response."""
        url = response.url
        status = response.status

        # Only process successful responses
        if status < 200 or status >= 400:
            return

        # Detect provider from page URL
        page = response.frame.page
        provider_id = self._detect_provider(page.url)
        if not provider_id:
            return

        url_lower = url.lower()

        # Balance / financial
        if any(kw in url_lower for kw in _BALANCE_KEYWORDS):
            try:
                body = await response.json()
                balance = self._extract_balance(body)
                if balance is not None and balance >= 0:
                    if provider_id not in self.provider_data:
                        self.provider_data[provider_id] = {}
                    self.provider_data[provider_id]["logged_in"] = True
                    self.provider_data[provider_id]["balance"] = balance
                    self.provider_data[provider_id]["last_balance_url"] = url
                    logger.info(f"[browser] {provider_id} balance: {balance}")
                    if self._on_event:
                        self._on_event("balance_intercepted", {
                            "provider_id": provider_id, "balance": balance, "url": url,
                        })
            except Exception:
                pass
            return

        # Bet history
        if any(kw in url_lower for kw in _HISTORY_KEYWORDS):
            try:
                body = await response.text()
                logger.info(f"[browser] {provider_id} history response: {url} ({len(body)} bytes)")
                if self._on_event:
                    self._on_event("history_intercepted", {
                        "provider_id": provider_id, "url": url, "size": len(body),
                    })
            except Exception:
                pass
            return

        # Bet placement
        if any(kw in url_lower for kw in _BET_PLACEMENT_KEYWORDS):
            try:
                body = await response.json()
                logger.info(f"[browser] {provider_id} bet placement: {url}")
                if self._on_event:
                    self._on_event("bet_intercepted", {
                        "provider_id": provider_id, "url": url, "body": body,
                    })
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
        # Altenar: {cash: {total: 942.04}}
        try:
            return float(body["cash"]["total"])
        except (KeyError, TypeError, ValueError):
            pass
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
