"""BetInterceptor — headed Playwright browser for bet interception.

Launches a visible browser with persistent context. The user browses
and bets normally. A response listener intercepts bet placement API
calls and forwards them to a callback for processing.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class BetInterceptor:
    """Manages a headed Playwright browser that intercepts bet placements."""

    def __init__(
        self,
        provider_id: str,
        on_bet_response: Callable[[str, str | None, str], Awaitable[None]] | None = None,
        discovery: bool = False,
    ):
        self.provider_id = provider_id
        self.on_bet_response = on_bet_response
        self.discovery = discovery
        self.status = "stopped"
        self.browser = None
        self.context = None
        self._playwright = None
        self._started_at = None

        # Persistent context dir — separate from extraction browsers
        from ..paths import get_app_data_dir
        self.user_data_dir = get_app_data_dir() / "data" / "mirror_profiles" / provider_id

    async def start(self, site_url: str = "https://www.spelklubben.se/sv/odds"):
        """Launch headed browser and register response listener."""
        if self.status == "listening":
            logger.warning(f"[mirror:{self.provider_id}] Already running")
            return

        try:
            from patchright.async_api import async_playwright
        except ImportError:
            from playwright.async_api import async_playwright
        from datetime import datetime, timezone

        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self.context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=False,
            viewport={"width": 1440, "height": 900},
            locale="sv-SE",
            timezone_id="Europe/Stockholm",
            args=["--disable-blink-features=AutomationControlled"],
        )

        # Attach listener to all current and future pages
        for page in self.context.pages:
            page.on("response", self._on_response)
        self.context.on("page", lambda page: page.on("response", self._on_response))

        # Navigate first page to the site
        page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await page.goto(site_url, wait_until="load", timeout=60000)

        self.status = "listening"
        self._started_at = datetime.now(timezone.utc)
        logger.info(f"[mirror:{self.provider_id}] Started — listening for bet placements")

    async def _on_response(self, response):
        """Response listener — filters for bet placement endpoints."""
        try:
            url = response.url
            # Only POST requests to the Gecko bet API
            if response.request.method != "POST":
                return
            if "/api/sb/" not in url.lower():
                return

            # Discovery mode: log ALL POST requests to /api/sb/
            if self.discovery:
                logger.info(f"[mirror:{self.provider_id}] [DISCOVERY] POST {url}")
                try:
                    body_text = await response.text()
                    logger.info(f"[mirror:{self.provider_id}] [DISCOVERY] Body preview: {body_text[:500]}")
                except Exception:
                    pass
                if self.on_bet_response:
                    request_body = response.request.post_data
                    await self.on_bet_response(url, request_body, body_text)
                return

            # Check if this looks like a bet placement URL
            from .parsers.gecko import GeckoBetParser
            parser = GeckoBetParser()
            if not parser.is_bet_placement_url(url):
                return

            # Read response body
            try:
                body_text = await response.text()
            except Exception as e:
                logger.debug(f"[mirror:{self.provider_id}] Could not read response body: {e}")
                return

            # Read request body (POST data)
            request_body = None
            try:
                request_body = response.request.post_data
            except Exception:
                pass

            logger.info(f"[mirror:{self.provider_id}] Intercepted bet placement: {url}")

            if self.on_bet_response:
                await self.on_bet_response(url, request_body, body_text)

        except Exception as e:
            logger.error(f"[mirror:{self.provider_id}] Error in response listener: {e}", exc_info=True)

    async def stop(self):
        """Detach listener and close browser."""
        if self.status != "listening":
            return

        self.status = "stopped"
        self._started_at = None

        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        logger.info(f"[mirror:{self.provider_id}] Stopped")

    def get_status(self) -> dict[str, Any]:
        """Return current status info."""
        return {
            "running": self.status == "listening",
            "provider": self.provider_id,
            "status": self.status,
            "since": self._started_at.isoformat() if self._started_at else None,
        }
