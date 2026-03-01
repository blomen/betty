"""
SlipFiller — CDP-based bet slip automation.

Connects to the user's Chrome via CDP, navigates to the event page,
clicks the correct odds button, and fills the stake amount.
Does NOT submit the bet — the user manually confirms on the provider site.

Usage:
    filler = SlipFillerService()
    filler.register_strategy("kambi", KambiSlipStrategy())
    result = await filler.fill_slip(request)
    # result.status == SlipStatus.READY → bet slip filled, user confirms
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

try:
    from patchright.async_api import async_playwright, Page
except ImportError:
    from playwright.async_api import async_playwright, Page

from .url_builder import build_match_url, PROVIDER_LANDING_URLS
from ..constants import PLATFORM_MAP
from ..recorder.chrome_launcher import get_chrome_launcher

logger = logging.getLogger(__name__)


class SlipStatus(str, Enum):
    """Outcome of a bet slip fill attempt."""
    READY = "ready"                      # Odds clicked, stake filled — user confirms
    NAVIGATED_ONLY = "navigated_only"    # Event page open, user fills manually
    ERROR = "error"                      # CDP/navigation failure


@dataclass
class SlipRequest:
    """What we want to fill in the bet slip."""
    provider_id: str
    event_id: str
    market: str             # "1x2", "moneyline", "spread", "total"
    outcome: str            # "home", "away", "draw", "over", "under"
    point: Optional[float]  # For spread/total markets
    stake: float
    expected_odds: float
    provider_meta: Optional[dict] = None
    home_team: str = ""
    away_team: str = ""


@dataclass
class SlipResult:
    """What happened when we tried to fill the slip."""
    status: SlipStatus
    message: str = ""
    provider_id: str = ""
    url: str = ""
    actual_odds: Optional[float] = None


async def dismiss_cookie_banner(page: Page) -> None:
    """Dismiss cookie banner if present (Altenar/Soft2Bet sites)."""
    try:
        btn = page.get_by_test_id("btnAcceptNecessaryCookies")
        if await btn.is_visible(timeout=2000):
            await btn.click()
            logger.info("Cookie banner dismissed")
    except Exception:
        pass  # No banner or already dismissed


async def check_logged_in(page: Page) -> bool:
    """Check if the user is logged in by looking for login indicators."""
    try:
        content = await page.content()
        # Logged OUT indicators
        if "Spela Här" in content or "SPELA HÄR" in content:
            return False
        # Logged IN indicators (balance display, account menu)
        if "account" in content.lower() and "saldo" in content.lower():
            return True
        # Check for the place bet button text
        if "PLACERA SPEL" in content:
            return True
        if "LOGGA IN" in content.upper():
            return False
        # Default: assume logged in if no clear logout indicators
        return True
    except Exception:
        return False


async def ensure_logged_in(page: Page, base_url: str) -> bool:
    """
    Navigate to site and ensure logged in. Returns True if logged in.
    If not logged in, navigates to login page and waits for BankID auth.
    The CDP Chrome profile persists cookies, so login should stick across sessions.
    """
    await dismiss_cookie_banner(page)

    if await check_logged_in(page):
        return True

    logger.warning("Not logged in — waiting for BankID authentication...")
    # Navigate to trigger login modal
    try:
        # Click "SPELA HÄR" button if visible
        btn = page.locator("button:has-text('Spela Här')")
        if await btn.is_visible(timeout=3000):
            await btn.click()
            # Wait up to 120s for user to complete BankID login
            for _ in range(60):
                await page.wait_for_timeout(2000)
                if await check_logged_in(page):
                    logger.info("BankID login completed")
                    return True
    except Exception as e:
        logger.error(f"Login flow error: {e}")

    return False


class SlipStrategy:
    """Base strategy — navigate to event page only (no auto-fill)."""

    async def fill(self, page: Page, request: SlipRequest, url: str) -> SlipResult:
        """Navigate to the event page. Subclasses add odds clicking + stake fill."""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)  # Wait for widget to render
        except Exception as e:
            return SlipResult(
                status=SlipStatus.ERROR,
                message=f"Navigation failed: {e}",
                provider_id=request.provider_id,
                url=url,
            )

        # Dismiss cookie banner if present
        await dismiss_cookie_banner(page)

        return SlipResult(
            status=SlipStatus.NAVIGATED_ONLY,
            message="Navigated to event page. Click odds and enter stake manually.",
            provider_id=request.provider_id,
            url=url,
        )


class SlipFillerService:
    """Orchestrates bet slip filling via CDP Chrome."""

    def __init__(self):
        self._strategies: dict[str, SlipStrategy] = {}
        self._fallback = SlipStrategy()

    def register_strategy(self, platform: str, strategy: SlipStrategy):
        """Register a platform-specific fill strategy."""
        self._strategies[platform] = strategy

    async def fill_slip(self, request: SlipRequest) -> SlipResult:
        """Connect to CDP Chrome, navigate, and fill the bet slip."""
        chrome = get_chrome_launcher()

        # 1. Build the event URL
        url = await build_match_url(
            provider_id=request.provider_id,
            provider_meta=request.provider_meta,
            home_team=request.home_team,
            away_team=request.away_team,
            event_id=request.event_id,
        )
        if not url:
            return SlipResult(
                status=SlipStatus.ERROR,
                message=f"No URL configured for {request.provider_id}",
                provider_id=request.provider_id,
            )

        # 2. Auto-start CDP Chrome if not running
        if not await chrome._is_cdp_available():
            logger.info("CDP Chrome not running — auto-starting...")
            started = await chrome.start()
            if not started:
                return SlipResult(
                    status=SlipStatus.ERROR,
                    message="Failed to auto-start CDP Chrome",
                    provider_id=request.provider_id,
                    url=url,
                )

        # 3. Connect to CDP Chrome via Playwright
        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(chrome.cdp_url)
        except Exception as e:
            if playwright:
                try:
                    await playwright.stop()
                except Exception:
                    pass
            return SlipResult(
                status=SlipStatus.ERROR,
                message=f"CDP connect failed: {e}",
                provider_id=request.provider_id,
                url=url,
            )

        try:
            # 4. Open a new tab
            context = browser.contexts[0]
            page = await context.new_page()

            # 5. Navigate to site and ensure logged in
            # Extract base_url from the full URL (e.g. https://betinia.se)
            from urllib.parse import urlparse
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            landing = PROVIDER_LANDING_URLS.get(request.provider_id, base_url)
            await page.goto(landing, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            logged_in = await ensure_logged_in(page, base_url)
            if not logged_in:
                return SlipResult(
                    status=SlipStatus.ERROR,
                    message="Not logged in — BankID authentication required. Open Chrome and log in manually.",
                    provider_id=request.provider_id,
                    url=url,
                )

            # 6. Select platform strategy
            platform = PLATFORM_MAP.get(request.provider_id, "")
            strategy = self._strategies.get(platform, self._fallback)

            logger.info(
                f"Filling slip: {request.provider_id} ({platform}) "
                f"market={request.market} outcome={request.outcome} "
                f"stake={request.stake} odds={request.expected_odds}"
            )

            # 7. Execute the strategy
            result = await strategy.fill(page, request, url)
            result.provider_id = request.provider_id
            result.url = url

            logger.info(f"Slip fill result: {result.status.value} — {result.message}")
            return result

        except Exception as e:
            logger.error(f"SlipFiller error: {e}", exc_info=True)
            return SlipResult(
                status=SlipStatus.ERROR,
                message=f"Fill failed: {e}",
                provider_id=request.provider_id,
                url=url,
            )
        finally:
            # Disconnect Playwright handle — Chrome tab stays open for the user
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
            try:
                if playwright:
                    await playwright.stop()
            except Exception:
                pass
