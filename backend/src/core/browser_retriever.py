"""
Browser Retriever Base Class

Base class for retrievers that use browser automation (Playwright).
Provides common initialization and session management.
"""

import logging
from typing import Any

from .retriever import Retriever
from .transport import BrowserTransport

logger = logging.getLogger(__name__)


class BrowserRetriever(Retriever):
    """
    Base class for retrievers using BrowserTransport.

    Provides:
    - Automatic BrowserTransport initialization
    - Session initialization pattern
    - Common browser utilities
    """

    def __init__(self, config: dict[str, Any], transport: BrowserTransport | None = None):
        """
        Initialize browser retriever.

        Args:
            config: Provider configuration
            transport: Optional BrowserTransport instance (creates new if not provided)
        """
        # Enforce BrowserTransport
        transport = transport or BrowserTransport(headless=True)
        super().__init__(config, transport)

        # Track initialization state
        self._initialized_pages: set[str] = set()
        self._session_ready = False

    async def _ensure_init(self, url: str = None, page_key: str = None) -> None:
        """
        Ensure browser session is initialized.

        Visits the specified URL to establish cookies/session.
        Tracks visited pages to avoid redundant initialization.

        Args:
            url: URL to visit for initialization (default: site root)
            page_key: Key to track this page (default: url)
        """
        if page_key is None:
            page_key = url or "root"

        # Skip if already initialized
        if page_key in self._initialized_pages:
            return

        if url is None:
            # Default: mark as ready without visiting
            self._session_ready = True
            self._initialized_pages.add(page_key)
            return

        logger.info(f"[{self.provider_id}] Initializing session via {url}...")

        try:
            if isinstance(self.transport, BrowserTransport):
                await self.transport._ensure_browser()
                # Use 'load' instead of 'networkidle' to avoid timeouts on pages with continuous activity
                await self.transport.page.goto(url, wait_until="load", timeout=30000)

                # Wait for cookies/session to settle and JS to execute
                await self.transport.page.wait_for_timeout(2000)

                self._initialized_pages.add(page_key)
                self._session_ready = True
                logger.info(f"[{self.provider_id}] Initialized session for {page_key}")

        except Exception as e:
            logger.error(f"[{self.provider_id}] Initialization failed for {url}: {e}")
            # Don't raise - existing cookies might be sufficient

    def _get_sport_url(self, sport: str) -> str:
        """
        Get URL for a specific sport.

        Browser retrievers typically build URLs differently,
        so this often returns empty string.
        """
        return ""

    async def close(self):
        """Close browser transport."""
        if isinstance(self.transport, BrowserTransport):
            await self.transport.close()
