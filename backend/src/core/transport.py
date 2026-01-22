from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import aiohttp
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

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
    """
    def __init__(self, headers: Optional[Dict] = None):
        self.session = None
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    async def _ensure_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(headers=self.headers)

    async def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        await self._ensure_session()
        # Merge headers if provided
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)
            
        async with self.session.get(url, params=params, headers=req_headers) as response:
            if response.status != 200:
                logger.warning(f"HTTP GET {url} returned status {response.status}")
                return None
            
            # Auto-detect JSON vs Text
            if "application/json" in response.headers.get("Content-Type", ""):
                return await response.json()
            return await response.text()

    async def post(self, url: str, data: Optional[Dict] = None, json: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        await self._ensure_session()
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)

        async with self.session.post(url, data=data, json=json, headers=req_headers) as response:
             if response.status not in (200, 201):
                 logger.warning(f"HTTP POST {url} returned status {response.status}")
                 return None
             if "application/json" in response.headers.get("Content-Type", ""):
                 return await response.json()
             return await response.text()

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

class BrowserTransport(Transport):
    """
    Heavy transport using Playwright.
    Best for protected sites or DOM scraping.
    """
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def _ensure_browser(self):
        if self.page: return

        self.playwright = await async_playwright().start()

        # Launch browser with stealth options
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-web-security'
            ]
        )

        # Create context with simple settings (like the working debug script)
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        self.page = await self.context.new_page()

        # Inject scripts to hide automation
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            window.chrome = {
                runtime: {}
            };

            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            Object.defineProperty(navigator, 'languages', {
                get: () => ['sv-SE', 'sv', 'en']
            });
        """)

    async def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        await self._ensure_browser()
        
        # Mode 1: Hybrid - Use Fetch API from context (faster, no page load if not needed)
        try:
            response = await self.context.request.get(url, params=params, headers=headers)
            if response.status == 200:
                try:
                    return await response.json()
                except:
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
                except:
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
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
