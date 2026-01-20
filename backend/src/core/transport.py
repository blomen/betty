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
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
             user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        self.page = await self.context.new_page()

    async def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> Any:
        await self._ensure_browser()
        
        # Playwright doesn't handle params automatically in goto, so we might need to append them
        # For simple usage, we assume url is full or simple. 
        # Ideal: use urllib.parse or context.request (API style)
        
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
            # Return page content or evaluate? 
            # For "Transport", we usually want the data.
            # If the user wants DOM, they should access page directly.
            # But here we're implementing a generic "get".
            # Let's return the content.
            return await self.page.content()
        except Exception as e:
            logger.error(f"Browser GET failed: {e}")
            return None

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
