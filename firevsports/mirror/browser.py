"""Playwright browser lifecycle — launch, manage tabs, cleanup."""
import asyncio
import logging
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

logger = logging.getLogger(__name__)


class MirrorBrowser:
    """Manages a single headed Chromium browser for bet placement."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._running = False

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
        self._running = True
        logger.info("Mirror browser started")
        return self._context

    async def stop(self):
        if not self._running:
            return
        try:
            if self._context: await self._context.close()
            if self._browser: await self._browser.close()
            if self._playwright: await self._playwright.stop()
        except Exception:
            logger.exception("Error closing mirror browser")
        finally:
            self._running = False
            self._context = None
            self._browser = None
            self._playwright = None
            logger.info("Mirror browser stopped")

    async def open_tab(self, url: str):
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
        return {"running": self._running, "tabs": len(pages), "pages": pages}
