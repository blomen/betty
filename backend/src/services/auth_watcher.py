"""
AuthWatcher — background task that monitors CDP Chrome tabs for provider logins.

Polls open Chrome tabs every 15 seconds. When a provider tab transitions from
"not logged in" to "logged in", auto-triggers AccountSyncService to settle
bets and update balance.

Supports:
  - Swedish sportsbooks (BankID login detection)
  - Polymarket (wallet-connected detection + auto wallet address extraction)

Usage:
    watcher = get_auth_watcher()
    asyncio.create_task(watcher.start())  # in app lifespan
    ...
    watcher.stop()  # on shutdown
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright

from ..constants import PLATFORM_MAP
from ..recorder.chrome_launcher import get_chrome_launcher
from ..recorder.domain_detector import detect_provider
from ..placement.slip_filler import check_logged_in

logger = logging.getLogger(__name__)

POLL_INTERVAL = 15      # Seconds between tab checks
SYNC_COOLDOWN = 300     # Don't re-sync same provider within 5 minutes


async def _check_polymarket_logged_in(page) -> bool:
    """Check if user is logged into Polymarket (wallet connected)."""
    try:
        # Polymarket shows portfolio/profile links when logged in
        html = await page.content()
        # Logged in indicators: profile avatar, portfolio link, wallet address display
        logged_in_indicators = [
            "/portfolio",           # Portfolio link visible
            "data-testid=\"profile",  # Profile element
            "0x",                    # Wallet address displayed
        ]
        logged_out_indicators = [
            "Log In",
            "Sign Up",
            "Connect Wallet",
        ]
        for indicator in logged_out_indicators:
            if indicator in html:
                return False
        for indicator in logged_in_indicators:
            if indicator in html:
                return True
        return False
    except Exception:
        return False


async def _extract_wallet_address(page) -> str | None:
    """Try to extract the Polymarket wallet address from the page."""
    try:
        # Method 1: Check URL for wallet address pattern
        url = page.url
        wallet_match = re.search(r'0x[a-fA-F0-9]{40}', url)
        if wallet_match:
            return wallet_match.group(0)

        # Method 2: Look in page content for wallet address
        html = await page.content()
        # Find 0x addresses that look like wallet addresses
        addresses = re.findall(r'0x[a-fA-F0-9]{40}', html)
        if addresses:
            # Return the first one found (usually the user's address)
            return addresses[0]

        # Method 3: Try JS evaluation to get connected wallet
        try:
            addr = await page.evaluate("() => window.ethereum?.selectedAddress || null")
            if addr and re.match(r'^0x[a-fA-F0-9]{40}$', addr):
                return addr
        except Exception:
            pass

        return None
    except Exception:
        return None


class AuthWatcher:
    """Watches CDP Chrome tabs for login state changes, auto-triggers sync."""

    def __init__(self):
        self._auth_state: dict[str, bool] = {}       # provider_id → logged_in
        self._last_sync: dict[str, datetime] = {}     # provider_id → last sync time
        self._sync_results: dict[str, dict] = {}      # provider_id → last sync result
        self._running = False
        self._syncing = False

    async def start(self, poll_interval: int = POLL_INTERVAL):
        """Start the auth watcher loop."""
        self._running = True
        logger.info(f"[AuthWatcher] Started (polling every {poll_interval}s)")

        while self._running:
            try:
                await self._poll_tabs()
            except Exception as e:
                logger.debug(f"[AuthWatcher] Poll error: {e}")

            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break

        logger.info("[AuthWatcher] Stopped")

    def stop(self):
        """Stop the auth watcher."""
        self._running = False

    def get_status(self) -> dict:
        """Get current watcher status for API."""
        return {
            "watching": self._running,
            "syncing": self._syncing,
            "auth_state": dict(self._auth_state),
            "last_syncs": {
                k: v.isoformat() for k, v in self._last_sync.items()
            },
            "sync_results": dict(self._sync_results),
        }

    async def _poll_tabs(self):
        """Check CDP tabs for provider login state changes."""
        chrome = get_chrome_launcher()

        if not await chrome._is_cdp_available():
            return

        # Get tab list via CDP HTTP endpoint (lightweight, no Playwright needed)
        tabs = await chrome.list_tabs()
        if not tabs:
            return

        # Find provider tabs
        provider_tabs: dict[str, str] = {}  # provider_id → tab URL
        for tab in tabs:
            if tab.get("type") != "page":
                continue
            url = tab.get("url", "")
            provider_id = detect_provider(url)
            if provider_id:
                provider_tabs[provider_id] = url

        if not provider_tabs:
            return

        # Connect Playwright to check login state on detected provider pages
        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(chrome.cdp_url)

            context = browser.contexts[0] if browser.contexts else None
            if not context:
                return

            # Build URL → page mapping from existing pages
            pages_by_domain: dict[str, object] = {}
            for page in context.pages:
                pid = detect_provider(page.url)
                if pid and pid in provider_tabs:
                    pages_by_domain[pid] = page

            # Check login state for each provider page
            for provider_id, page in pages_by_domain.items():
                try:
                    platform = PLATFORM_MAP.get(provider_id, "")

                    # Use platform-specific login detection
                    if platform == "polymarket":
                        logged_in = await _check_polymarket_logged_in(page)
                    else:
                        logged_in = await check_logged_in(page)

                    prev_state = self._auth_state.get(provider_id, False)
                    self._auth_state[provider_id] = logged_in

                    if logged_in and not prev_state:
                        # State transition: not logged in → logged in
                        logger.info(f"[AuthWatcher] Login detected for {provider_id}")

                        # For Polymarket: auto-extract wallet address
                        if platform == "polymarket":
                            await self._handle_polymarket_login(page)

                        if not self._is_on_cooldown(provider_id):
                            await self._trigger_sync(provider_id)
                except Exception as e:
                    logger.debug(f"[AuthWatcher] Failed to check {provider_id}: {e}")

            # Clear auth state for providers no longer visible
            for pid in list(self._auth_state.keys()):
                if pid not in pages_by_domain:
                    self._auth_state.pop(pid, None)

        finally:
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

    async def _handle_polymarket_login(self, page):
        """Extract and store wallet address when Polymarket login is detected."""
        wallet = await _extract_wallet_address(page)
        if not wallet:
            logger.info("[AuthWatcher] Polymarket login detected but could not extract wallet")
            return

        logger.info(f"[AuthWatcher] Extracted Polymarket wallet: {wallet[:6]}...{wallet[-4:]}")

        # Store wallet address in DB
        try:
            from ..db.models import get_session, ProfileProviderBalance
            from ..repositories import ProfileRepo

            session = get_session()
            try:
                profile_repo = ProfileRepo(session)
                profile = profile_repo.get_active()

                # Get or create balance row for polymarket
                balance_row = session.query(ProfileProviderBalance).filter(
                    ProfileProviderBalance.profile_id == profile.id,
                    ProfileProviderBalance.provider_id == "polymarket",
                ).first()

                if balance_row:
                    if balance_row.wallet_address != wallet:
                        balance_row.wallet_address = wallet
                        session.commit()
                        logger.info(f"[AuthWatcher] Updated Polymarket wallet address")
                else:
                    balance_row = ProfileProviderBalance(
                        profile_id=profile.id,
                        provider_id="polymarket",
                        balance=0.0,
                        wallet_address=wallet,
                    )
                    session.add(balance_row)
                    session.commit()
                    logger.info(f"[AuthWatcher] Stored new Polymarket wallet address")
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"[AuthWatcher] Failed to store wallet: {e}")

    def _is_on_cooldown(self, provider_id: str) -> bool:
        """Check if provider was recently synced."""
        last = self._last_sync.get(provider_id)
        if not last:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < SYNC_COOLDOWN

    def _get_wallet(self, session, provider_id: str) -> str | None:
        """Get stored wallet address for a provider."""
        from ..db.models import ProfileProviderBalance
        from ..repositories import ProfileRepo

        profile_repo = ProfileRepo(session)
        profile = profile_repo.get_active()

        row = session.query(ProfileProviderBalance).filter(
            ProfileProviderBalance.profile_id == profile.id,
            ProfileProviderBalance.provider_id == provider_id,
        ).first()

        return row.wallet_address if row else None

    async def _trigger_sync(self, provider_id: str):
        """Trigger account sync for a provider."""
        if self._syncing:
            logger.info(f"[AuthWatcher] Skipping sync for {provider_id} — another sync in progress")
            return

        self._syncing = True
        try:
            # Import here to avoid circular imports
            from ..db.models import get_session
            from .account_sync_service import AccountSyncService
            from ..placement.strategies.kambi_account import KambiAccountStrategy

            platform = PLATFORM_MAP.get(provider_id, "")
            if not platform:
                logger.warning(f"[AuthWatcher] No platform for {provider_id}")
                return

            session = get_session()
            try:
                service = AccountSyncService(session)

                # Register strategies based on platform
                service.register_strategy("kambi", KambiAccountStrategy())

                # Polymarket: API-based, needs wallet address
                if platform == "polymarket":
                    wallet = self._get_wallet(session, provider_id)
                    if wallet:
                        from ..placement.strategies.polymarket_account import PolymarketAccountStrategy
                        service.register_strategy("polymarket", PolymarketAccountStrategy(wallet))
                    else:
                        logger.warning(f"[AuthWatcher] No wallet for polymarket — skipping sync")
                        return

                logger.info(f"[AuthWatcher] Syncing {provider_id}...")
                result = await service.sync_provider(provider_id)

                self._last_sync[provider_id] = datetime.now(timezone.utc)
                self._sync_results[provider_id] = {
                    "settled_count": result.get("settled_count", 0),
                    "balance": result.get("balance"),
                    "balance_updated": result.get("balance_updated", False),
                    "pending_remaining": result.get("pending_remaining", 0),
                    "error": result.get("error"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                settled = result.get("settled_count", 0)
                balance = result.get("balance")
                logger.info(
                    f"[AuthWatcher] Synced {provider_id}: "
                    f"{settled} bets settled, balance={'%.2f' % balance if balance else '?'}"
                )
            except Exception as e:
                logger.error(f"[AuthWatcher] Sync failed for {provider_id}: {e}", exc_info=True)
                self._sync_results[provider_id] = {
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            finally:
                session.close()
        finally:
            self._syncing = False


# Singleton
_watcher: AuthWatcher | None = None


def get_auth_watcher() -> AuthWatcher:
    global _watcher
    if _watcher is None:
        _watcher = AuthWatcher()
    return _watcher
