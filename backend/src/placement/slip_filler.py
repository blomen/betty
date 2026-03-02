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

import re as _re

from .url_builder import build_match_url, PROVIDER_LANDING_URLS
from ..constants import PLATFORM_MAP
from ..recorder.chrome_launcher import get_chrome_launcher

# Platforms that use wallet/non-BankID login (skip Swedish login flow)
WALLET_PLATFORMS = {"polymarket"}

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
    # Post-login sync results (Polymarket wallet-based)
    balance: Optional[float] = None
    wallet_address: Optional[str] = None
    balance_updated: bool = False


async def dismiss_cookie_banner(page: Page) -> None:
    """Dismiss cookie banner if present."""
    # Altenar/Soft2Bet sites
    try:
        btn = page.get_by_test_id("btnAcceptNecessaryCookies")
        if await btn.is_visible(timeout=2000):
            await btn.click()
            logger.info("Cookie banner dismissed (Altenar)")
            return
    except Exception:
        pass
    # Unibet/Kindred — OneTrust banner with "Avvisa alla" (decline all)
    try:
        btn = page.get_by_role("button", name="Avvisa alla")
        if await btn.is_visible(timeout=2000):
            await btn.click()
            logger.info("Cookie banner dismissed (Unibet/OneTrust)")
            return
    except Exception:
        pass


async def check_logged_in(page: Page) -> bool:
    """Check if the user is logged in by looking for login indicators."""
    try:
        content = await page.content()
        content_upper = content.upper()
        # Logged OUT indicators
        if "SPELA HÄR" in content_upper:
            return False
        if "LOGGA IN" in content_upper:
            return False
        # Logged IN indicators
        # Unibet/Kambi: balance display ("Saldo X kr"), customerLoggedIn param
        if "saldo" in content.lower():
            return True
        if "customerLoggedIn=true" in content:
            return True
        # Kambi: place bet button
        if "PLACERA SPEL" in content_upper:
            return True
        # Unibet: post-login gambling limits modal
        if "SPELGRÄNSER" in content_upper:
            return True
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
        # Unibet/Kambi: "Spela här" button triggers BankID modal
        btn = page.get_by_role("button", name="Spela här")
        if await btn.is_visible(timeout=3000):
            await btn.click()
            await page.wait_for_timeout(1000)

            # Click "Starta BankID" if the BankID modal appeared
            bankid_btn = page.get_by_role("button", name="Starta BankID")
            try:
                if await bankid_btn.is_visible(timeout=3000):
                    await bankid_btn.click()
                    logger.info("BankID QR code displayed — waiting for user to scan...")
            except Exception:
                pass

            # Wait up to 120s for user to complete BankID login
            for _ in range(60):
                await page.wait_for_timeout(2000)
                if await check_logged_in(page):
                    logger.info("BankID login completed")
                    # Dismiss post-login gambling limits modal if present
                    try:
                        okej_btn = page.get_by_role("button", name="okej")
                        if await okej_btn.is_visible(timeout=3000):
                            await okej_btn.click()
                            logger.info("Post-login limits modal dismissed")
                    except Exception:
                        pass
                    return True
    except Exception as e:
        logger.error(f"Login flow error: {e}")

    return False


async def check_polymarket_logged_in(page: Page) -> bool:
    """Check if user is wallet-connected on Polymarket."""
    try:
        content = await page.content()
        content_lower = content.lower()
        # Logged IN indicators: portfolio link, wallet address, position data
        if "portfolio" in content_lower and "0x" in content:
            return True
        # Profile menu or wallet display
        if "deposit" in content_lower and "withdraw" in content_lower:
            return True
        # Check for connect wallet button (logged OUT)
        if "connect wallet" in content_lower or "log in" in content_lower:
            return False
        # Default: assume logged in if no clear logout indicators
        return True
    except Exception:
        return False


async def ensure_polymarket_logged_in(page: Page) -> bool:
    """
    Navigate Polymarket and check wallet connection.
    If not connected, page stays on Polymarket for user to connect manually.
    Returns True if wallet is connected.
    """
    await page.wait_for_timeout(2000)

    if await check_polymarket_logged_in(page):
        return True

    logger.warning("Polymarket wallet not connected — user needs to connect manually")
    # Wait a bit in case user connects quickly
    for _ in range(15):
        await page.wait_for_timeout(2000)
        if await check_polymarket_logged_in(page):
            logger.info("Polymarket wallet connected")
            return True

    # Not connected after 30s — still navigated, user can connect manually
    return False


async def _extract_wallet_from_page(page: Page) -> Optional[str]:
    """Extract wallet address from Polymarket page DOM."""
    try:
        html = await page.content()
        addresses = _re.findall(r'0x[a-fA-F0-9]{40}', html)
        if addresses:
            return addresses[0]
        # Try JS evaluation
        try:
            addr = await page.evaluate("() => window.ethereum?.selectedAddress || null")
            if addr and _re.match(r'^0x[a-fA-F0-9]{40}$', addr):
                return addr
        except Exception:
            pass
    except Exception:
        pass
    return None


async def _sync_polymarket_after_login(page: Page) -> tuple[Optional[str], Optional[float]]:
    """
    Post-login sync: extract wallet + fetch balance via Data API.

    Returns (wallet_address, balance_usdc) — either may be None on failure.
    Stores wallet + updates balance in DB as a side effect.
    """
    # 1. Extract wallet from page
    wallet = await _extract_wallet_from_page(page)
    if not wallet:
        logger.info("[SlipFiller] Could not extract wallet from Polymarket page")
        return None, None

    logger.info(f"[SlipFiller] Extracted wallet: {wallet[:6]}...{wallet[-4:]}")

    # 2. Store wallet in DB
    try:
        from ..db.models import get_session, ProfileProviderBalance
        from ..repositories import ProfileRepo

        session = get_session()
        try:
            profile_repo = ProfileRepo(session)
            profile = profile_repo.get_active()

            balance_row = session.query(ProfileProviderBalance).filter(
                ProfileProviderBalance.profile_id == profile.id,
                ProfileProviderBalance.provider_id == "polymarket",
            ).first()

            if balance_row:
                if balance_row.wallet_address != wallet:
                    balance_row.wallet_address = wallet
                    session.commit()
            else:
                balance_row = ProfileProviderBalance(
                    profile_id=profile.id,
                    provider_id="polymarket",
                    balance=0.0,
                    wallet_address=wallet,
                )
                session.add(balance_row)
                session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"[SlipFiller] Failed to store wallet: {e}")

    # 3. Quick balance fetch via Data API
    balance = None
    try:
        from ..services.polymarket_client import PolymarketDataClient
        client = PolymarketDataClient()
        portfolio = await client.get_portfolio(wallet)
        balance = portfolio.total_value_usdc
        logger.info(f"[SlipFiller] Polymarket balance: ${balance:.2f}")

        # Update balance in DB
        try:
            session = get_session()
            try:
                profile_repo = ProfileRepo(session)
                profile = profile_repo.get_active()
                profile_repo.set_balance(profile.id, "polymarket", balance)
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"[SlipFiller] Failed to update balance: {e}")
    except Exception as e:
        logger.warning(f"[SlipFiller] Data API balance fetch failed: {e}")

    return wallet, balance


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
            from urllib.parse import urlparse
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            platform = PLATFORM_MAP.get(request.provider_id, "")

            if platform in WALLET_PLATFORMS:
                # Polymarket: navigate to polymarket.com first to check login
                await page.goto("https://polymarket.com", wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(3000)
                logged_in = await ensure_polymarket_logged_in(page)

                wallet = None
                balance = None
                balance_updated = False

                if logged_in:
                    # Post-login: extract wallet + sync balance via Data API
                    wallet, balance = await _sync_polymarket_after_login(page)
                    balance_updated = balance is not None

                    # Navigate to the actual event page
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        await page.wait_for_timeout(2000)
                    except Exception as e:
                        logger.warning(f"[SlipFiller] Event navigation failed: {e}")

                    msg = "Synced balance and navigated to event."
                else:
                    msg = "Connect your wallet on Polymarket, then try again."

                return SlipResult(
                    status=SlipStatus.NAVIGATED_ONLY,
                    message=msg,
                    provider_id=request.provider_id,
                    url=url,
                    wallet_address=wallet,
                    balance=balance,
                    balance_updated=balance_updated,
                )
            else:
                # Swedish sportsbooks: navigate to landing, check BankID login
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

            # 6. Select platform strategy (platform already resolved above)
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
