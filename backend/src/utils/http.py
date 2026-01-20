"""
HTTP Client for Oddopp

Simplified async HTTP client with rate limiting.
Based on oddsview HTTPClient but without proxy/ETag complexity.
"""

import aiohttp
import asyncio
import time
import logging
from typing import Any

logger = logging.getLogger(__name__)


class HTTPClient:
    """Async HTTP client with rate limiting and retry logic."""
    
    def __init__(
        self, 
        rate_limits: dict[str, Any] | None = None, 
        headers: dict[str, str] | None = None
    ):
        self.rate_limits = rate_limits or {}
        self.headers = headers or {}
        self.session: aiohttp.ClientSession | None = None
        self.last_request_time = 0.0
    
    async def __aenter__(self):
        # Parse cookies from headers if present
        cookies_dict = {}
        headers_clean = dict(self.headers)
        
        if 'Cookie' in headers_clean:
            for pair in headers_clean.pop('Cookie').split('; '):
                if '=' in pair:
                    name, value = pair.split('=', 1)
                    cookies_dict[name.strip()] = value.strip()
        
        self.session = aiohttp.ClientSession(
            headers=headers_clean,
            cookies=cookies_dict
        )
        return self
    
    async def __aexit__(self, *args):
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def get(
        self, 
        url: str, 
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
        timeout: float = 30.0
    ) -> dict[str, Any]:
        """GET request with rate limiting and retry logic."""
        await self._apply_rate_limit()
        
        if not self.session:
            raise RuntimeError("Use 'async with' context manager")
        
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        
        for attempt in range(max_retries):
            try:
                async with self.session.get(
                    url, 
                    params=params, 
                    timeout=client_timeout
                ) as response:
                    
                    if response.status == 404:
                        return {}
                    
                    # Rate limit - exponential backoff
                    if response.status == 429:
                        if attempt < max_retries - 1:
                            wait = 2 ** (attempt + 1)
                            logger.warning(f"429 for {url}, waiting {wait}s")
                            await asyncio.sleep(wait)
                            continue
                        else:
                            logger.error(f"Rate limited: {url}")
                            return {}
                    
                    response.raise_for_status()
                    return await response.json()
                    
            except aiohttp.ClientError as e:
                logger.debug(f"HTTP error: {e}")
                return {}
            except Exception as e:
                logger.debug(f"Request error: {e}")
                return {}
        
        return {}
    
    async def _apply_rate_limit(self):
        """Apply rate limiting based on rpm config."""
        rpm = self.rate_limits.get("rpm", 0)
        if rpm > 0:
            min_delay = 60.0 / rpm
            elapsed = time.time() - self.last_request_time
            if elapsed < min_delay:
                await asyncio.sleep(min_delay - elapsed)
        
        self.last_request_time = time.time()
