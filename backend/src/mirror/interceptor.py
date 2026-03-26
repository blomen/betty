"""BetInterceptor — headed Playwright browser for bet interception + full traffic recording.

Launches a single visible browser. The user browses any betting site freely.
Listeners run simultaneously:

1. NetworkRecorder — captures ALL network traffic to JSONL (RL training data)
2. Event cache — intercepts events-table API responses to cache team names
3. Bet placement — intercepts confirmed bets across all platforms
4. Bet history — intercepts settlement/history pages
5. Financial — intercepts balance/deposit/withdraw data
"""

import logging
from typing import Callable, Awaitable

from .recorder import NetworkRecorder

logger = logging.getLogger(__name__)


class BetInterceptor:
    """Manages a headed Playwright browser that intercepts bet placements."""

    # Bet placement URL patterns — platform-agnostic (HTTP)
    # Each tuple: (url_contains, method) — method None means any
    _BET_PLACEMENT_PATTERNS = (
        # Gecko V2 (Betsson, Spelklubben, Betsafe, NordicBet, Hajper)
        ("/api/sb/v2/coupons", "POST"),
        # Altenar (QuickCasino, ComeOn, Campobet, Betinia, LodurBet)
        ("/api/widget/placeWidget", "POST"),
        ("/api/widget/placeBet", "POST"),
        # SBTech / Amelco
        ("/bets/place", "POST"),
        # Pinnacle
        ("/v1/bets/straight", "POST"),
        ("/v1/bets/parlay", "POST"),
    )

    # WebSocket URLs to monitor for bet placement frames (Kambi etc.)
    _WS_MONITOR_KEYWORDS = ("kambi", "push.aws")

    # Bet history / settlement patterns (Altenar + Gecko + generic)
    _BET_HISTORY_KEYWORDS = ("bethistory", "bet-history", "betHistory", "mybets", "my-bets",
                             "widgetBetHistory", "coupon-history")
    # Gecko V2 bet history — same URL as placement but GET method (exclude /count)
    _GECKO_COUPON_HISTORY_PATTERNS = ("/api/sb/v1/coupons", "/api/sb/v2/coupons")
    # Balance / deposit / withdraw patterns
    _FINANCIAL_KEYWORDS = ("account/balance", "/wallets", "payment-stats", "mainbalance")

    # Notification / preference settings patterns
    _NOTIFICATION_KEYWORDS = (
        "preferences", "notifications", "communication", "consent",
        "marketing", "subscriptions", "gdpr", "contact-settings",
    )
    _NOTIFICATION_METHODS = {"PUT", "POST", "PATCH"}

    # Known provider domains → provider ID
    _PROVIDER_DOMAINS = {
        "campobet.se": "campobet", "quickcasino.se": "quickcasino",
        "betinia.se": "betinia", "swiper.se": "swiper", "lodur.se": "lodur",
        "dbet.com": "dbet", "spelklubben.se": "spelklubben",
        "betsson.com": "betsson", "betsafe.com": "betsafe",
        "nordicbet.com": "nordicbet", "bethard.com": "bethard",
        "unibet.se": "unibet", "leovegas.com": "leovegas",
        "expekt.se": "expekt", "888sport.se": "888sport",
        "speedybet.com": "speedybet", "x3000.com": "x3000",
        "goldenbull.se": "goldenbull", "1x2.se": "1x2",
        "comeon.com": "comeon", "hajper.com": "hajper",
        "lyllocasino.com": "lyllo", "snabbare.com": "snabbare",
        "10bet.se": "10bet", "mrgreen.se": "mrgreen",
        "betmgm.se": "betmgm", "vbet.se": "vbet",
        "interwetten.se": "interwetten", "coolbet.com": "coolbet",
        "tipwin.se": "tipwin", "pinnacle.com": "pinnacle",
    }

    def __init__(
        self,
        on_bet_response: Callable[..., Awaitable[None]] | None = None,
        on_event_data: Callable[[str, str], Awaitable[None]] | None = None,
        on_bet_history: Callable[[str, str], Awaitable[None]] | None = None,
        on_financial_data: Callable[[str, str], Awaitable[None]] | None = None,
        on_provider_detected: Callable[[str], Awaitable[None]] | None = None,
        on_notification_settings: Callable[..., Awaitable[None]] | None = None,
    ):
        self.on_bet_response = on_bet_response
        self.on_event_data = on_event_data
        self.on_bet_history = on_bet_history
        self.on_financial_data = on_financial_data
        self.on_provider_detected = on_provider_detected
        self.on_notification_settings = on_notification_settings
        self._detected_providers: set[str] = set()  # Track already-detected to avoid spam
        self.status = "stopped"
        self.context = None
        self._playwright = None
        self._started_at = None
        self.recorder = NetworkRecorder("mirror")

        from ..paths import get_app_data_dir
        self.user_data_dir = get_app_data_dir() / "data" / "mirror_profiles" / "default"

    async def start(self):
        """Launch headed browser — opens to a blank page, user navigates freely."""
        if self.status == "listening":
            logger.warning("[mirror] Already running")
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
            channel="chrome",
            headless=False,
            no_viewport=True,
            locale="sv-SE",
            timezone_id="Europe/Stockholm",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

        # Start recording
        self.recorder.start()

        # Attach HTTP + WebSocket + navigation listeners to all current and future pages
        def _attach_page(page):
            page.on("response", self._on_response)
            page.on("websocket", self._on_websocket)
            if self.on_provider_detected:
                page.on("framenavigated", lambda frame: self._check_provider_navigation(frame))

        for page in self.context.pages:
            _attach_page(page)
        self.context.on("page", _attach_page)

        self.status = "listening"
        self._started_at = datetime.now(timezone.utc)
        logger.info("[mirror] Started — recording all traffic + listening for bets")

    def _is_bet_placement(self, url: str, method: str) -> bool:
        """Check if this request is a bet placement across any platform."""
        url_lower = url.lower()
        for pattern, required_method in self._BET_PLACEMENT_PATTERNS:
            if pattern.lower() in url_lower:
                if required_method is None or method == required_method:
                    return True
        return False

    def _is_notification_settings(self, url: str, method: str) -> bool:
        """Check if this request is a notification/preference settings update."""
        if method not in self._NOTIFICATION_METHODS:
            return False
        url_lower = url.lower()
        return any(kw in url_lower for kw in self._NOTIFICATION_KEYWORDS)

    async def _on_response(self, response):
        """Response listener — records everything + filters for bet placements and event data."""
        try:
            # Always record to JSONL (RL training data)
            await self.recorder.record_response(response)

            url = response.url
            method = response.request.method



            # Cache event data from events-table API responses (Gecko V2)
            if self.on_event_data and "events-table" in url and method == "GET":
                try:
                    body_text = await response.text()
                    await self.on_event_data(url, body_text)
                except Exception as e:
                    logger.debug(f"[mirror] Could not read events-table response: {e}")

            # Intercept bet history / settlement responses
            _is_bet_history = any(kw in url for kw in self._BET_HISTORY_KEYWORDS)
            # Gecko V2: GET to coupons endpoint = bet history (POST = placement)
            if not _is_bet_history and method == "GET" and "/count" not in url and any(kw in url for kw in self._GECKO_COUPON_HISTORY_PATTERNS):
                _is_bet_history = True
            if self.on_bet_history and _is_bet_history:
                try:
                    # Try text() first, fall back to body() + decode for compressed responses
                    try:
                        body_text = await response.text()
                    except Exception:
                        raw = await response.body()
                        body_text = raw.decode("utf-8", errors="replace")
                    req_body = None
                    try:
                        req_body = response.request.post_data
                    except Exception:
                        pass
                    await self.on_bet_history(url, body_text, req_body)
                except Exception as e:
                    logger.debug(f"[mirror] Could not read bet history response: {e}")

            # Intercept balance / deposit / withdraw data
            if self.on_financial_data and any(kw in url for kw in self._FINANCIAL_KEYWORDS):
                try:
                    body_text = await response.text()
                    await self.on_financial_data(url, body_text)
                except Exception as e:
                    logger.debug(f"[mirror] Could not read financial data response: {e}")

            # Intercept notification settings updates
            if self.on_notification_settings and self._is_notification_settings(url, method):
                if response.status < 400:
                    try:
                        body_text = await response.text()
                        request_body = response.request.post_data
                        content_type = response.request.headers.get("content-type", "")
                        await self.on_notification_settings(url, method, request_body, body_text, content_type)
                    except Exception as e:
                        logger.debug(f"[mirror] Could not read notification settings response: {e}")

            # Intercept bet placements across all platforms
            if not self._is_bet_placement(url, method):
                return

            if response.status >= 400:
                return

            try:
                body_text = await response.text()
            except Exception as e:
                logger.debug(f"[mirror] Could not read response body: {e}")
                return

            request_body = None
            try:
                request_body = response.request.post_data
            except Exception:
                pass

            # Capture the page URL the user is viewing
            page_url = None
            try:
                page = response.request.frame.page
                if page:
                    page_url = page.url
            except Exception:
                pass

            logger.info(f"[mirror] Intercepted bet placement: {url} (page: {page_url})")

            if self.on_bet_response:
                await self.on_bet_response(url, request_body, body_text, page_url)

        except Exception as e:
            logger.error(f"[mirror] Error in response listener: {e}", exc_info=True)

    def _on_websocket(self, ws):
        """WebSocket listener — monitors Kambi and similar WS-based platforms."""
        url = ws.url
        if not any(kw in url.lower() for kw in self._WS_MONITOR_KEYWORDS):
            return

        logger.info(f"[mirror] WebSocket connected: {url}")

        def _on_frame_received(payload):
            """Handle incoming WebSocket frame (server → client)."""
            try:
                if not isinstance(payload, str):
                    return
                # Kambi WS frames are JSON — look for coupon/bet placement responses
                if not any(kw in payload for kw in ('"couponId"', '"placeBetResult"', '"couponStatus"',
                                                      '"couponResponse"', '"betPlaced"', '"PLACED"')):
                    return

                logger.info(f"[mirror] WS bet frame received ({len(payload)} bytes)")

                if self.on_bet_response:
                    import asyncio
                    asyncio.ensure_future(
                        self.on_bet_response(url, None, payload, None)
                    )
            except Exception as e:
                logger.debug(f"[mirror] WS frame error: {e}")

        def _on_frame_sent(payload):
            """Handle outgoing WebSocket frame (client → server) — captures bet requests."""
            try:
                if not isinstance(payload, str):
                    return
                if not any(kw in payload for kw in ('"placeBet"', '"placeCoupon"', '"stake"')):
                    return

                logger.info(f"[mirror] WS bet request sent ({len(payload)} bytes)")

                # Store the sent frame as a trace via on_bet_response
                # The response frame handler above will capture the confirmation
                if self.on_bet_response:
                    import asyncio
                    asyncio.ensure_future(
                        self.on_bet_response(url, payload, "{}", None)
                    )
            except Exception as e:
                logger.debug(f"[mirror] WS frame send error: {e}")

        ws.on("framereceived", _on_frame_received)
        ws.on("framesent", _on_frame_sent)
        ws.on("close", lambda: logger.debug(f"[mirror] WebSocket closed: {url}"))

    def _check_provider_navigation(self, frame):
        """Detect when user navigates to a known provider site."""
        try:
            if frame.parent_frame:
                return  # Only care about top-level navigation
            url = frame.url
            if not url or url.startswith("about:") or url.startswith("chrome:"):
                return
            from urllib.parse import urlparse
            hostname = urlparse(url).hostname or ""
            # Match against known domains (strip www.)
            clean = hostname.removeprefix("www.").removeprefix("d-cf.").removeprefix("cloud-api.")
            for domain, provider_id in self._PROVIDER_DOMAINS.items():
                if clean == domain or clean.endswith("." + domain):
                    if provider_id not in self._detected_providers:
                        self._detected_providers.add(provider_id)
                        logger.info(f"[mirror] Provider detected: {provider_id} ({url[:80]})")
                        if self.on_provider_detected:
                            import asyncio
                            asyncio.ensure_future(self.on_provider_detected(provider_id))
                    return
        except Exception as e:
            logger.debug(f"[mirror] Navigation check error: {e}")

    def reset_detected_providers(self):
        """Clear detected providers — allows re-detection after sync."""
        self._detected_providers.clear()

    async def stop(self):
        """Close browser and stop recording."""
        if self.status != "listening":
            return

        self.status = "stopped"
        self._started_at = None
        self.recorder.stop()

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

        logger.info("[mirror] Stopped")

    def get_status(self) -> dict:
        """Return current status info."""
        return {
            "running": self.status == "listening",
            "status": self.status,
            "since": self._started_at.isoformat() if self._started_at else None,
        }
