from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import asyncio
import random
import aiohttp
import logging
try:
    from patchright.async_api import async_playwright
except ImportError:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        async_playwright = None

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

logger = logging.getLogger(__name__)


def get_proxy_url() -> str | None:
    """Get PROXY_URL from environment. Returns plain URL string for aiohttp."""
    import os
    return os.environ.get("PROXY_URL")


def get_proxy_dict() -> dict | None:
    """Parse PROXY_URL into Playwright/Camoufox proxy dict format.

    Handles format: http://user:pass@host:port → {server, username, password}
    """
    import os
    from urllib.parse import urlparse
    proxy_url = os.environ.get("PROXY_URL")
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


# Modern Chrome UA — updated periodically
_CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

class Transport(ABC):
    """
    Abstract Interface for Data Transport.
    Responsible for fetching raw data locally or remotely.
    """
    
    @abstractmethod
    async def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        pass

    @abstractmethod
    async def post(self, url: str, data: Optional[Dict] = None, json: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        pass
        
    @abstractmethod
    async def close(self):
        pass

class HttpTransport(Transport):
    """
    Lightweight HTTP Transport using aiohttp.
    Best for APIs.

    Supports async context manager for proper resource cleanup:
        async with HttpTransport() as transport:
            data = await transport.get(url)
    """
    def __init__(self, headers: Optional[Dict] = None, circuit_breaker: Any = None, rate_limit_config: Any = None, proxy: Optional[str] = None):
        self.session = None
        self._session_lock = asyncio.Lock()
        self._owns_session = True  # Track if we created the session
        self.headers = headers or {"User-Agent": _CHROME_UA}
        self.circuit_breaker = circuit_breaker
        self.rate_limit_config = rate_limit_config
        self.proxy = proxy  # e.g. "http://user:pass@host:port"
        # Track consecutive 429s per provider for circuit breaker notification
        self._consecutive_429s: Dict[str, int] = {}

    async def __aenter__(self):
        """Async context manager entry - ensures session is created."""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures session is closed."""
        await self.close()
        return False

    async def _ensure_session(self):
        if not self.session:
            async with self._session_lock:
                if not self.session:  # Double-check after lock
                    self.session = aiohttp.ClientSession(headers=self.headers)

    async def get(
        self,
        url: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        cache: Optional[Any] = None,
        provider_id: Optional[str] = None,
        max_retries: int = None
    ) -> Any:
        """
        GET request with optional caching and 429 rate limit handling.

        Args:
            url: Request URL
            params: Optional query parameters
            headers: Optional headers
            cache: Optional ResponseCache instance
            provider_id: Optional provider identifier for cache
            max_retries: Max retries on 429 rate limit (uses config default if None)

        Returns:
            Response data (JSON or text)
        """
        # Use config values or defaults
        if max_retries is None:
            max_retries = self.rate_limit_config.max_retries if self.rate_limit_config else 2
        default_wait = self.rate_limit_config.default_wait_seconds if self.rate_limit_config else 5
        max_wait = self.rate_limit_config.max_wait_seconds if self.rate_limit_config else 60
        cb_threshold = self.rate_limit_config.notify_circuit_breaker_after if self.rate_limit_config else 2

        # Check cache first
        if cache:
            cached = cache.get(url, params, provider_id)
            if cached is not None:
                logger.debug(f"Cache HIT: {url}")
                return cached

        await self._ensure_session()
        # Merge headers if provided
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)

        # Per-request timeout — prevents hanging connections without competing with sport timeout
        # (sport timeout handles the overall deadline; this just catches stuck TCP connections)
        req_timeout = aiohttp.ClientTimeout(total=90)

        # Retry loop for 429 handling
        for attempt in range(max_retries + 1):
            async with self.session.get(url, params=params, headers=req_headers, timeout=req_timeout, proxy=self.proxy) as response:
                # Handle 429 rate limit with exponential backoff
                if response.status == 429:
                    retry_after = response.headers.get('Retry-After', str(default_wait))
                    try:
                        wait_seconds = int(retry_after)
                    except ValueError:
                        wait_seconds = default_wait

                    # Cap wait time and apply exponential backoff
                    wait_seconds = min(wait_seconds * (2 ** attempt), max_wait)

                    provider_str = f"[{provider_id}] " if provider_id else ""
                    logger.warning(
                        f"{provider_str}429 Rate Limited - Retry-After: {wait_seconds}s "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )

                    # Track consecutive 429s and notify circuit breaker
                    if provider_id:
                        self._consecutive_429s[provider_id] = self._consecutive_429s.get(provider_id, 0) + 1
                        if self._consecutive_429s[provider_id] >= cb_threshold and self.circuit_breaker:
                            logger.warning(
                                f"{provider_str}Notifying circuit breaker after {cb_threshold} consecutive 429s"
                            )
                            self.circuit_breaker.record_failure(provider_id)

                    if attempt < max_retries:
                        await asyncio.sleep(wait_seconds)
                        continue
                    else:
                        logger.error(f"{provider_str}429 Rate limit exceeded after {max_retries + 1} attempts")
                        return None

                # Reset consecutive 429 counter on success
                if provider_id and provider_id in self._consecutive_429s:
                    self._consecutive_429s[provider_id] = 0

                if response.status != 200:
                    # 401/403 are expected for restricted resources (e.g., Pinnacle leagues)
                    # Log at DEBUG to avoid noisy warnings during normal operation
                    if response.status in (401, 403):
                        logger.debug(f"HTTP GET {url} returned status {response.status}")
                    else:
                        logger.warning(f"HTTP GET {url} returned status {response.status}")
                    return None

                # Auto-detect JSON vs Text
                if "application/json" in response.headers.get("Content-Type", ""):
                    data = await response.json()
                else:
                    data = await response.text()

                # Store in cache
                if cache and data:
                    cache.set(url, data, params, provider_id)

                return data

        return None

    async def post(
        self,
        url: str,
        data: Optional[Dict] = None,
        json: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        max_retries: int = None
    ) -> Any:
        if max_retries is None:
            max_retries = self.rate_limit_config.max_retries if self.rate_limit_config else 2
        default_wait = self.rate_limit_config.default_wait_seconds if self.rate_limit_config else 5
        max_wait = self.rate_limit_config.max_wait_seconds if self.rate_limit_config else 60

        await self._ensure_session()
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)

        req_timeout = aiohttp.ClientTimeout(total=90)

        for attempt in range(max_retries + 1):
            async with self.session.post(url, data=data, json=json, headers=req_headers, timeout=req_timeout, proxy=self.proxy) as response:
                if response.status == 429:
                    retry_after = response.headers.get('Retry-After', str(default_wait))
                    try:
                        wait_seconds = int(retry_after)
                    except ValueError:
                        wait_seconds = default_wait
                    wait_seconds = min(wait_seconds * (2 ** attempt), max_wait)
                    logger.warning(f"429 Rate Limited on POST {url} (attempt {attempt + 1}/{max_retries + 1})")
                    if attempt < max_retries:
                        await asyncio.sleep(wait_seconds)
                        continue
                    else:
                        return None

                if response.status not in (200, 201):
                    logger.warning(f"HTTP POST {url} returned status {response.status}")
                    return None
                if "application/json" in response.headers.get("Content-Type", ""):
                    return await response.json()
                return await response.text()

        return None

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

class BrowserTransport(Transport):
    """
    Heavy transport using Playwright.
    Best for protected sites or DOM scraping.

    Args:
        headless: Run browser in headless mode
        user_data_dir: Path for persistent browser profile (cookies/session survive restarts).
                       When set, uses launch_persistent_context() instead of launch() + new_context().
                       Useful for sites with aggressive bot protection (Imperva, Cloudflare).
        channel: Browser channel to use (e.g. 'chrome' for installed Chrome instead of Playwright Chromium).
                 Using 'chrome' bypasses Imperva/Incapsula bot detection that flags Playwright's bundled Chromium.
        cdp_url: Connect to an existing Chrome browser via CDP (e.g. 'http://localhost:9222').
                 Chrome must be running with --remote-debugging-port=9222.
                 This bypasses all bot detection since it attaches to a real human-controlled browser.
    """
    def __init__(self, headless: bool = True, user_data_dir: Optional[str] = None,
                 channel: Optional[str] = None, cdp_url: Optional[str] = None,
                 circuit_breaker: Any = None, use_proxy: bool = False):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.channel = channel
        self.cdp_url = cdp_url
        self.circuit_breaker = circuit_breaker
        self._proxy_dict = get_proxy_dict() if use_proxy else None
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        # Resource types always blocked during extraction (speeds up page loads)
        self._BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}

        # Opt-in: set True to also block stylesheets
        # Default False because Hajper uses getComputedStyle() for scroll detection
        self._BLOCK_STYLESHEETS = False

        # Tracking/analytics domains to block
        self._BLOCKED_URL_PATTERNS = [
            "google-analytics.com", "googletagmanager.com",
            "googlesyndication.com", "doubleclick.net",
            "bat.bing.com", "bat.bing.net",
            "facebook.net", "facebook.com/tr",
            "truendo.com", "braze.eu", "braze.com",
            "hotjar.com", "clarity.ms",
            "sportradar.com/widgets",
        ]

    async def _setup_resource_blocking(self):
        """Block images, fonts, and tracking scripts on all pages in this context."""
        if not self.context:
            return

        blocked_types = set(self._BLOCKED_RESOURCE_TYPES)
        if self._BLOCK_STYLESHEETS:
            blocked_types.add("stylesheet")

        async def _block_unnecessary(route):
            try:
                if route.request.resource_type in blocked_types:
                    await route.abort()
                    return
                url = route.request.url.lower()
                for pattern in self._BLOCKED_URL_PATTERNS:
                    if pattern in url:
                        await route.abort()
                        return
                await route.continue_()
            except Exception:
                pass  # Route already handled by another handler

        await self.context.route("**/*", _block_unnecessary)
        logger.debug("Resource blocking enabled for browser context (block_css=%s)", self._BLOCK_STYLESHEETS)

    async def _ensure_browser(self):
        if self.page: return

        if async_playwright is None:
            raise ImportError("Browser transport requires patchright or playwright. Install with: pip install patchright")

        try:
            self.playwright = await async_playwright().start()
        except Exception as e:
            logger.error(
                f"[BrowserTransport] async_playwright().start() FAILED: "
                f"{type(e).__name__}: {e!r}"
            )
            raise

        # CDP mode: attach to an already-running Chrome browser
        if self.cdp_url:
            self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)
            contexts = self.browser.contexts
            if contexts:
                self.context = contexts[0]
                self.page = await self.context.new_page()
            else:
                self.context = await self.browser.new_context()
                self.page = await self.context.new_page()
            await self._setup_resource_blocking()
            logger.info(f"Browser connected via CDP to {self.cdp_url}")
            return

        context_opts = dict(
            user_agent=_CHROME_UA,
            locale='sv-SE',
            geolocation={
                'latitude': 59.3293 + (random.random() - 0.5) * 0.01,
                'longitude': 18.0686 + (random.random() - 0.5) * 0.01,
            },  # Stockholm ±500m jitter
        )
        if self._proxy_dict:
            context_opts['proxy'] = self._proxy_dict
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--window-position=-2400,-2400',
        ]

        launch_kwargs = {}
        if self.channel:
            launch_kwargs['channel'] = self.channel

        if self.user_data_dir:
            # Persistent context — cookies/local storage survive between runs
            import os
            os.makedirs(self.user_data_dir, exist_ok=True)
            self.context = await self.playwright.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=self.headless,
                args=launch_args,
                **launch_kwargs,
                **context_opts,
            )
            self.browser = None  # persistent context has no separate browser handle
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        else:
            # Standard launch — fresh context each run
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
                **launch_kwargs,
            )
            self.context = await self.browser.new_context(**context_opts)
            self.page = await self.context.new_page()

        await self._setup_resource_blocking()

        # Patchright handles all stealth at CDP level (webdriver, plugins, WebGL, etc.)
        # No add_init_script() needed — it conflicts with patchright's internal patching
        # and causes net::ERR_NAME_NOT_RESOLVED on Windows

        proxy_msg = " + residential proxy" if self._proxy_dict else ""
        logger.info(f"Browser initialized with patchright stealth{proxy_msg}")

    async def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        await self._ensure_browser()
        
        # Mode 1: Hybrid - Use Fetch API from context (faster, no page load if not needed)
        try:
            response = await self.context.request.get(url, params=params, headers=headers)
            if response.status == 200:
                try:
                    return await response.json()
                except Exception:
                    return await response.text()
            else:
                 # Fallback to page navigation if API fails (maybe protected?)
                 pass
        except Exception:
            pass

        # Mode 2: Full Page Load
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            return await self.page.content()
        except Exception as e:
            logger.error(f"Browser GET failed: {e}")
            return None

    async def post(self, url: str, data: Optional[Dict] = None, json: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        await self._ensure_browser()
        
        # Mode 1: Hybrid context request
        try:
            # Playwright request.post supports 'data' (form) or 'data' (json if object? no `multipart` usually explicitly)
            # request.post(url, data=..., form=..., multipart=...)
            # We map generic 'data' to 'form' if dict and not json
            
            kwargs = {"headers": headers}
            if json:
                kwargs["data"] = json # Playwright treats dict in data as JSON automatically? No, requests does. 
                # Playwright: data (str|bytes|Serializable), form (Dict), multipart (Dict)
                # If we pass json dict to 'data', it serializes?
                # Best to use 'data' for json if content-type header is set, otherwise...
                pass
            
            # Simplified mapping:
            # If json arg is present -> assume JSON body
            # If data arg is present -> assume Form/Multipart
            
            if json:
                response = await self.context.request.post(url, data=json, headers=headers)
            elif isinstance(data, dict):
                 response = await self.context.request.post(url, form=data, headers=headers)
            elif data:
                 # Pass raw string/bytes to 'data'
                 response = await self.context.request.post(url, data=data, headers=headers)
            else:
                response = await self.context.request.post(url, headers=headers)

            if response.status in (200, 201):
                try:
                    return await response.json()
                except Exception:
                    return await response.text()
            else:
                logger.warning(f"Browser POST {url} returned {response.status} {response.status_text}")
                return None
                
        except Exception as e:
            logger.error(f"Browser POST failed: {e}")
            return None

    async def new_page(self):
        """Create a new page in the existing context (sharing cookies)."""
        await self._ensure_browser()
        return await self.context.new_page()

    async def smart_scroll(self, timeout: int = 10000, button_selector: Optional[str] = None, page: Any = None):
        """
        Robust smart scrolling mechanism adapted from modern scraping libraries.
        - Scrolls to bottom
        - Waits for network idle
        - Checks for height change
        - Clicks "Show More" buttons if selector provided
        """
        await self._ensure_browser()
        target_page = page or self.page
        if not target_page:
             raise ValueError("No page available for scrolling")
        
        js_script = """
        async (args) => {
            const { timeout, buttonSelector } = args;
            const startTime = Date.now();
            let lastHeight = 0;
            let sameHeightCount = 0;
            const maxRetries = 5;
            
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            
            while (Date.now() - startTime < timeout) {
                // 1. Scroll to bottom
                window.scrollTo(0, document.body.scrollHeight);
                await sleep(500);
                
                // 2. Click button if exists
                let clicked = false;
                if (buttonSelector) {
                    let btn = null;
                    
                    // Determine if XPath or CSS
                    if (buttonSelector.startsWith('/') || buttonSelector.startsWith('(') || buttonSelector.startsWith('xpath=')) {
                        const cleanXpath = buttonSelector.replace('xpath=', '');
                        try {
                            const result = document.evaluate(cleanXpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                            btn = result.singleNodeValue;
                        } catch (e) {
                            console.log("XPath error:", e);
                        }
                    } else {
                        try {
                            btn = document.querySelector(buttonSelector);
                        } catch (e) {
                            console.log("CSS Selector error:", e);
                        }
                    }
                    
                    if (btn && btn.offsetParent !== null) { // Check visibility
                        console.log("Clicking load more button");
                        btn.click();
                        await sleep(2000); // Wait for content load
                        clicked = true;
                        sameHeightCount = 0; // Reset counter on click
                    }
                }
                
                // 3. Check height if we didn't click (if we clicked, height likely changed or will change)
                if (!clicked) {
                    const currentHeight = document.body.scrollHeight;
                    if (currentHeight === lastHeight) {
                        sameHeightCount++;
                        if (sameHeightCount >= maxRetries) {
                            console.log("Reached end of page (height constant)");
                            break;
                        }
                    } else {
                        sameHeightCount = 0;
                        lastHeight = currentHeight;
                    }
                }
                
                await sleep(500);
            }
        }
        """
        
        try:
            await target_page.evaluate(js_script, {"timeout": timeout, "buttonSelector": button_selector})
            logger.info("Smart scroll completed.")
        except Exception as e:
            logger.error(f"Smart scroll failed: {e}")

    async def close(self):
        if self.cdp_url:
            # CDP mode — only close the page we created, leave the browser running
            if self.page:
                await self.page.close()
        elif self.user_data_dir and self.context:
            # Persistent context — close context directly (no separate browser)
            await self.context.close()
        elif self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
