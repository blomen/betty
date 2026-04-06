"""
Coolbet Retriever - Proprietary GAN Sports platform

Coolbet uses a proprietary sportsbook (formerly GAN Sports) with Imperva/Incapsula
bot protection. Uses Camoufox (anti-detect Firefox) to bypass Imperva automatically.
Falls back to CDP connection if camoufox is unavailable.

API endpoints (proxied through coolbet.com):
- GET /s/sbgate/sports/fo-category/?categoryId={id}&offset=N — paginated category/league listing
- POST /s/sb-odds/odds/current/fo-line/ — odds values keyed by outcome ID

The category API returns 10 leagues per page. Must paginate with offset (starting at 1)
to get all leagues for a sport. offset=0 returns a validation error.

Sport category IDs (discovered via /s/sbgate/category/by-slug/sv/):
- Football: 62, Basketball: 77, Tennis: 72, Ice Hockey: 85
- American Football: 58, Baseball: 96, MMA: 20491, Esports: 65035, Handball: 68
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
import json
import time
from datetime import datetime

from ..core import StandardEvent
from ..core.browser_retriever import BrowserRetriever
from ..core.exceptions import RetryableError
from ..core.transport import BrowserTransport, get_proxy_dict
from ..matching.normalizer import normalize_team_name, normalize_outcome

logger = logging.getLogger(__name__)

# Page size for category API (fixed server-side)
CATEGORY_PAGE_SIZE = 10
MAX_OFFSET = 500

# Camoufox persistent profile directory (preserves cookies between runs)
CAMOUFOX_PROFILE_DIR = None  # Will use temp dir; set for cookie persistence


class CoolbetRetriever(BrowserRetriever):
    """Retriever for Coolbet sportsbook (GAN Sports platform).

    Uses Camoufox (anti-detect Firefox) to bypass Imperva bot detection.
    Camoufox patches fingerprints at C++ level, making it undetectable
    to Imperva's Reese84 challenge. Falls back to CDP if unavailable.
    """

    SPORT_CONFIG: Dict[str, Dict] = {
        "football":          {"slug": "fotboll",             "category_id": 62},
        "basketball":        {"slug": "basket",              "category_id": 77},
        "tennis":            {"slug": "tennis",              "category_id": 72},
        "ice_hockey":        {"slug": "ishockey",            "category_id": 85},
        "american_football": {"slug": "amerikansk-fotboll",  "category_id": 58},
        "baseball":          {"slug": "baseboll",            "category_id": 96},
        "mma":               {"slug": "mma",                 "category_id": 20491},
        "esports":           {"slug": "esports",             "category_id": 65035},
        "handball":          {"slug": "handboll",            "category_id": 68},
    }

    # Exact market name → standard type (all observed names from API)
    MARKET_MAP = {
        # 1x2 (3-way)
        "Match Result (1X2)":           "1x2",
        # Moneyline (2-way)
        "Match Winner":                 "moneyline",
        "Match Winner (2-way)":         "moneyline",
        "Moneyline":                    "moneyline",
        "Money Line":                   "moneyline",
        "Match Result":                 "moneyline",
        "Fight Result (Draw No Bet)":   "moneyline",
        # Total
        "Total Goals Over / Under":     "total",
        "Total Goals Over/Under":       "total",
        "Total Points Over/Under":      "total",
        "Total Points Over / Under":    "total",
        "Total Over / Under":           "total",
        "Total Over/Under":             "total",
        "Total Games Over/Under":       "total",
        "Total Maps Played":            "total",
        # Spread
        "Asian Handicap":               "spread",
        "Handicap (2 Way)":             "spread",
        "Handicap":                     "spread",
        "Spread":                       "spread",
        "Game Handicap":                "spread",
        "Match Handicap":               "spread",
    }

    # Market names to explicitly skip (3-way handicap not useful)
    SKIP_MARKETS = {"Handicap (3 Way)"}

    _camoufox_unavailable = False  # Class-level flag to avoid repeated ImportError

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.coolbet.com")
        self._camoufox_browser = None
        self._camoufox_page = None
        self._sports_on_page = 0  # Track usage to proactively recycle

    async def _recycle_page(self):
        """Close current page and create a fresh one from existing browser.

        Camoufox pages accumulate SPA state and memory after many navigations,
        eventually crashing. Proactive recycling prevents the crash.
        """
        if not self._camoufox_browser:
            return
        if self._camoufox_page:
            try:
                await self._camoufox_page.close()
            except Exception:
                pass
            self._camoufox_page = None
        try:
            self._camoufox_page = await self._camoufox_browser.new_page()
            self._session_ready = False
            self._sports_on_page = 0
            logger.debug(f"[{self.provider_id}] Recycled Camoufox page")
        except Exception:
            logger.warning(f"[{self.provider_id}] Page recycle failed, full relaunch needed")
            await self._cleanup_camoufox()

    async def _ensure_camoufox(self):
        """Launch Camoufox anti-detect browser if not already running."""
        if self._camoufox_page is not None:
            # Proactively recycle page after each sport to prevent crash
            if self._sports_on_page > 0:
                await self._recycle_page()
                if self._camoufox_page:
                    return self._camoufox_page
                # Fall through to launch if recycle failed

            # Validate cached page is still alive
            if self._camoufox_page:
                try:
                    await self._camoufox_page.evaluate("() => true", timeout=5000)
                    return self._camoufox_page
                except Exception:
                    logger.warning(f"[{self.provider_id}] Camoufox page died, recovering...")
                    if self._camoufox_browser:
                        try:
                            self._camoufox_page = await self._camoufox_browser.new_page()
                            self._session_ready = False
                            self._sports_on_page = 0
                            logger.info(f"[{self.provider_id}] Recovered with new page (browser alive)")
                            return self._camoufox_page
                        except Exception:
                            logger.warning(f"[{self.provider_id}] Browser also dead, full relaunch")
                    await self._cleanup_camoufox()
                    self._session_ready = False

        if CoolbetRetriever._camoufox_unavailable:
            return None

        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            CoolbetRetriever._camoufox_unavailable = True
            logger.error(
                f"[{self.provider_id}] camoufox not installed. "
                f"Install with: pip install camoufox[geoip] && python -m camoufox fetch"
            )
            return None

        logger.info(f"[{self.provider_id}] Launching Camoufox anti-detect browser...")
        t0 = time.time()
        try:
            proxy = get_proxy_dict()
            self._camoufox_browser = await AsyncCamoufox(
                headless=True,
                geoip=True,
                humanize=0.2,
                os="windows",
                proxy=proxy,
            ).__aenter__()
            if proxy:
                logger.info(f"[{self.provider_id}] Camoufox launched with residential proxy")

            self._camoufox_page = await self._camoufox_browser.new_page()
            logger.info(f"[{self.provider_id}] Camoufox browser ready in {time.time()-t0:.1f}s")
            return self._camoufox_page
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to launch Camoufox: {e}")
            self._camoufox_browser = None
            self._camoufox_page = None
            return None

    async def _cleanup_camoufox(self):
        """Close camoufox browser (suppresses pipe errors from subprocess cleanup)."""
        if self._camoufox_browser:
            try:
                await self._camoufox_browser.__aexit__(None, None, None)
            except (Exception, OSError, ValueError):
                # Camoufox subprocess may raise "I/O operation on closed pipe"
                # during shutdown — this is benign and expected
                pass
            finally:
                self._camoufox_browser = None
                self._camoufox_page = None

    async def _get_page(self) -> Optional[Any]:
        """Get a browser page — tries Camoufox first, falls back to CDP transport."""
        # Strategy 1: Camoufox (anti-detect Firefox, bypasses Imperva)
        page = await self._ensure_camoufox()
        if page:
            return page

        # Strategy 2: CDP fallback (requires manual Chrome with --remote-debugging-port=9222)
        if isinstance(self.transport, BrowserTransport):
            try:
                await self.transport._ensure_browser()
                return self.transport.page
            except Exception as e:
                logger.warning(f"[{self.provider_id}] CDP fallback failed: {e}")

        logger.error(
            f"[{self.provider_id}] No browser available. "
            f"Install camoufox: pip install camoufox[geoip] && python -m camoufox fetch"
        )
        return None

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """Extract events using Coolbet's internal API via browser context."""
        sport_conf = self.SPORT_CONFIG.get(sport)
        if not sport_conf:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            page = await self._get_page()
            if not page:
                raise RetryableError(
                    "No browser available (camoufox not installed?)",
                    provider_id=self.provider_id,
                )

            # Navigate to sport page to establish session (needed for API auth)
            if not self._session_ready:
                sport_url = f"{self.site_url}/sv/odds/{sport_conf['slug']}"
                logger.debug(f"[{self.provider_id}] Loading {sport_url}")

                await page.goto(sport_url, wait_until='load', timeout=60000)

                # Imperva sets session cookies asynchronously after page load.
                # The API returns 403 if called too early (cookies not yet valid).
                # 2s is sufficient for Imperva Reese84 challenge to complete with Camoufox.
                await asyncio.sleep(2)
                body_text = await page.evaluate(
                    'document.body ? document.body.innerText.substring(0, 500) : ""'
                )
                if 'Incapsula' in body_text or 'security check' in body_text.lower() or \
                   'Access denied' in body_text or 'Error 15' in body_text:
                    raise RetryableError(
                        "Imperva block detected even with Camoufox",
                        provider_id=self.provider_id,
                    )

                logger.info(f"[{self.provider_id}] Session established — Imperva bypassed")
                self._session_ready = True

            # Fetch ALL categories with pagination
            category_data = await self._fetch_all_categories(
                page, sport_conf['category_id']
            )

            if not category_data:
                logger.warning(f"[{self.provider_id}] No category data for {sport}")
                return []

            # Collect market IDs from prematch matches
            market_ids = []
            for cat in category_data:
                for match in cat.get("matches", []):
                    if match.get("inplay") or match.get("match_type") == "OUTRIGHT":
                        continue
                    for market in match.get("markets", []):
                        mid = market.get("id")
                        if mid:
                            market_ids.append(mid)

            # Fetch odds for all markets via POST
            odds_data = {}
            if market_ids:
                odds_data = await self._fetch_odds_api(page, market_ids)

            logger.debug(
                f"[{self.provider_id}] {sport}: {len(category_data)} categories, "
                f"{len(market_ids)} markets, {len(odds_data)} odds entries"
            )

            # Parse events
            events = self._parse_categories(category_data, odds_data, sport)
            logger.debug(f"[{self.provider_id}] {sport}: {len(events)} events extracted")
            self._sports_on_page += 1
            return events[:limit]

        except Exception as e:
            err_str = str(e)
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            # Proxy/network error — kill browser so next run gets a fresh one
            if "NS_ERROR" in err_str or "PROXY" in err_str or "net::" in err_str:
                logger.warning(f"[{self.provider_id}] Network error — restarting browser for next run")
                self._session_ready = False
                await self._cleanup_camoufox()
            return []

    CONCURRENT_CATEGORY_FETCHES = 8  # Parallel category page fetches (restored from 4 — I/O-bound, Camoufox handles fine)

    async def _fetch_all_categories(self, page, category_id: int) -> List[Dict]:
        """Fetch all categories with pagination (API returns 10 per page).

        Uses concurrent fetching for massive speedup on sports with many leagues
        (football: 92s sequential → ~20s with 5 concurrent).

        Strategy: fetch first page to probe, then fan out concurrent requests
        for remaining pages.
        """
        all_categories = []
        seen_cat_ids = set()

        # First page: no offset param (offset=0 returns validation error)
        first_page = await self._fetch_category_page(page, category_id, offset=None)
        if first_page:
            for cat in first_page:
                cid = cat.get("id")
                if cid not in seen_cat_ids:
                    seen_cat_ids.add(cid)
                    all_categories.append(cat)

        if not first_page:
            return all_categories

        # Fan out: fetch pages concurrently in batches
        # Each batch of CONCURRENT_CATEGORY_FETCHES offsets runs in parallel
        offset = CATEGORY_PAGE_SIZE  # Start after first page (0-indexed)
        consecutive_empty_batches = 0

        while offset < MAX_OFFSET and consecutive_empty_batches < 3:
            # Build batch of offsets to fetch concurrently
            batch_offsets = []
            for i in range(self.CONCURRENT_CATEGORY_FETCHES):
                o = offset + i * CATEGORY_PAGE_SIZE
                if o < MAX_OFFSET:
                    batch_offsets.append(o)

            # Fetch all pages in this batch concurrently
            tasks = [
                self._fetch_category_page(page, category_id, o)
                for o in batch_offsets
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_has_data = False
            for cats in results:
                if isinstance(cats, Exception):
                    logger.warning(f"[{self.provider_id}] Category fetch failed: {cats}")
                    continue
                if not cats:
                    continue
                for cat in cats:
                    cid = cat.get("id")
                    if cid not in seen_cat_ids:
                        seen_cat_ids.add(cid)
                        all_categories.append(cat)
                        batch_has_data = True

            if not batch_has_data:
                consecutive_empty_batches += 1
            else:
                consecutive_empty_batches = 0

            offset += len(batch_offsets) * CATEGORY_PAGE_SIZE

        logger.debug(
            f"[{self.provider_id}] Category API: {len(all_categories)} categories "
            f"(paginated to offset={offset})"
        )
        return all_categories

    async def _fetch_category_page(
        self, page, category_id: int, offset: Optional[int] = None
    ) -> List[Dict]:
        """Fetch a single page of categories with retry on 403 (Imperva not ready)."""
        url = (
            f"{self.site_url}/s/sbgate/sports/fo-category/"
            f"?categoryId={category_id}&country=SE&isMobile=0"
            f"&language=sv&layout=EUROPEAN&limit=500"
        )
        if offset is not None:
            url += f"&offset={offset}"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = await asyncio.wait_for(page.evaluate(f"""
                    (async () => {{
                        const resp = await fetch('{url}', {{credentials: 'include'}});
                        if (!resp.ok) return {{__status: resp.status, __ok: false}};
                        return await resp.json();
                    }})();
                """), timeout=30)

                # Check for HTTP error response
                if isinstance(resp, dict) and resp.get("__ok") is False:
                    status = resp.get("__status", "?")
                    if status == 403 and attempt < max_retries - 1:
                        wait = 3 * (attempt + 1)
                        logger.warning(
                            f"[{self.provider_id}] Category API returned 403 "
                            f"(Imperva not ready), retrying in {wait}s..."
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.warning(
                        f"[{self.provider_id}] Category API returned {status} "
                        f"for categoryId={category_id} offset={offset}"
                    )
                    return []

                if isinstance(resp, list):
                    return resp

                logger.warning(
                    f"[{self.provider_id}] Unexpected response type "
                    f"{type(resp).__name__} for categoryId={category_id}"
                )
                return []

            except Exception as e:
                logger.warning(
                    f"[{self.provider_id}] Category page "
                    f"categoryId={category_id} offset={offset} failed: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
        return []

    async def _fetch_odds_api(self, page, market_ids: List) -> Dict:
        """Fetch odds values for market IDs via the sb-odds fo-line endpoint."""
        if not market_ids:
            return {}
        try:
            unique_ids = list(set(market_ids))
            all_odds = {}
            chunk_size = 500
            for i in range(0, len(unique_ids), chunk_size):
                chunk = unique_ids[i:i + chunk_size]
                market_arrays = [[mid] for mid in chunk]
                body = json.dumps({"marketIds": market_arrays})
                resp = await asyncio.wait_for(page.evaluate(f"""
                    (async () => {{
                        const resp = await fetch('/s/sb-odds/odds/current/fo-line/', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            credentials: 'include',
                            body: '{body}'
                        }});
                        return await resp.json();
                    }})();
                """), timeout=30)
                if isinstance(resp, dict):
                    all_odds.update(resp)
            logger.debug(
                f"[{self.provider_id}] Odds API: {len(all_odds)} entries "
                f"for {len(unique_ids)} markets"
            )
            return all_odds
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Odds API failed: {e}")
        return {}

    def _parse_categories(
        self,
        categories: List[Dict],
        odds_data: Dict,
        sport: str,
    ) -> List[StandardEvent]:
        """Parse category API response into StandardEvents."""
        events = []
        seen_ids = set()

        for category in categories:
            league = category.get("name", "Unknown")
            matches = category.get("matches", [])

            for match in matches:
                try:
                    event = self._parse_match(match, odds_data, sport, league)
                    if event and event.id not in seen_ids:
                        seen_ids.add(event.id)
                        events.append(event)
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Failed to parse match: {e}")

        return events

    def _parse_match(
        self,
        match: Dict,
        odds_data: Dict,
        sport: str,
        league: str,
    ) -> Optional[StandardEvent]:
        """Parse a single match from Coolbet category API."""
        if match.get("inplay"):
            return None
        if match.get("match_type") == "OUTRIGHT":
            return None

        match_id = match.get("id")
        home_team_raw = match.get("home_team_name", "")
        away_team_raw = match.get("away_team_name", "")

        if not home_team_raw or not away_team_raw:
            return None

        home_team = normalize_team_name(home_team_raw)
        away_team = normalize_team_name(away_team_raw)

        start_time = None
        start_str = match.get("match_start")
        if start_str:
            try:
                start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                logger.debug(f"[{self.provider_id}] Invalid start_time: {start_str}")

        # Fallback: use current time so fuzzy matching has a valid date
        if not start_time:
            from datetime import timezone
            start_time = datetime.now(timezone.utc)
            logger.debug(f"[{self.provider_id}] No start_time for match {match.get('id')}, using now()")

        # Parse markets — store ALL spread/total lines (storage layer filters to Pinnacle's point)
        # Previously picked "most balanced" line which rarely matched Pinnacle → 0 spread/total stored
        markets = []
        seen_winner_type = None  # Track 1x2/moneyline dedup

        for raw_market in match.get("markets", []):
            market_name = raw_market.get("name", "")
            if market_name in self.SKIP_MARKETS:
                continue

            market_type = self._normalize_market_type(market_name)
            if not market_type:
                continue

            line = raw_market.get("line")
            point = None
            if line is not None:
                try:
                    point = float(line)
                except (ValueError, TypeError):
                    pass

            outcomes = self._parse_outcomes(
                raw_market.get("outcomes", []),
                odds_data, market_type, home_team_raw, away_team_raw
            )

            if not outcomes:
                continue

            market_dict = {"type": market_type, "outcomes": outcomes}
            if point is not None:
                for o in market_dict["outcomes"]:
                    o["point"] = point

            if market_type in ("total", "spread"):
                # Store ALL lines — storage pipeline will match against Pinnacle's point
                markets.append(market_dict)
            else:
                # 1x2/moneyline: take first of each type
                if market_type not in {m["type"] for m in markets}:
                    markets.append(market_dict)

        # Dedup: prefer 1x2 over moneyline
        market_types_present = {m["type"] for m in markets}
        if "1x2" in market_types_present and "moneyline" in market_types_present:
            markets = [m for m in markets if m["type"] != "moneyline"]

        if not markets:
            return None

        return StandardEvent(
            id=str(match_id),
            name=f"{home_team_raw} vs {away_team_raw}",
            provider=self.provider_id,
            sport=sport,
            league=league,
            home_team=home_team,
            away_team=away_team,
            start_time=start_time,
            markets=markets,
        )

    def _parse_outcomes(
        self,
        raw_outcomes: List[Dict],
        odds_data: Dict,
        market_type: str,
        home_raw: str,
        away_raw: str,
    ) -> List[Dict]:
        """Parse outcomes for a market, looking up odds from odds_data."""
        outcomes = []
        for raw_outcome in raw_outcomes:
            if raw_outcome.get("status") != "OPEN":
                continue

            outcome_id = str(raw_outcome.get("id", ""))
            result_key = raw_outcome.get("result_key", "")
            outcome_name_raw = raw_outcome.get("name", "")

            # Get odds value from odds_data (keyed by outcome ID)
            odds_entry = odds_data.get(outcome_id)
            if odds_entry is None:
                odds_entry = odds_data.get(raw_outcome.get("id"))
            if odds_entry is None:
                continue

            if isinstance(odds_entry, dict):
                if odds_entry.get("status") == "SUSPENDED":
                    continue
                odds_val = odds_entry.get("value")
            else:
                odds_val = odds_entry

            if odds_val is None or not isinstance(odds_val, (int, float)):
                continue

            # Coolbet uses milliodds for values > 100
            if odds_val > 100:
                odds_val = odds_val / 1000.0

            if odds_val <= 1.0:
                continue

            outcome_name = self._normalize_outcome(
                result_key, outcome_name_raw, market_type,
                home_raw, away_raw
            )
            if not outcome_name:
                continue

            outcome_dict = {"name": outcome_name, "odds": float(odds_val)}

            # Extract point from outcome-level line field (spreads often have line=null at market level)
            if market_type in ("spread", "total"):
                o_line = raw_outcome.get("line")
                if o_line is not None:
                    try:
                        outcome_dict["point"] = float(o_line)
                    except (ValueError, TypeError):
                        pass

            outcomes.append(outcome_dict)

        return outcomes

    def _normalize_market_type(self, market_name: str) -> Optional[str]:
        """Map Coolbet market name to standard type."""
        if market_name in self.MARKET_MAP:
            return self.MARKET_MAP[market_name]

        name_lower = market_name.lower()

        # Skip sequence/quarter/period markets
        if "[sequence]" in name_lower or "quarter" in name_lower or "period" in name_lower:
            return None
        # Skip 3-way handicap
        if "3 way" in name_lower or "3-way" in name_lower:
            return None

        if "1x2" in name_lower:
            return "1x2"
        if "match result" in name_lower:
            return "1x2"
        if "fight result" in name_lower and "3" not in name_lower:
            return "moneyline"
        if "winner" in name_lower or "moneyline" in name_lower or "money line" in name_lower:
            return "moneyline"
        if "over" in name_lower and "under" in name_lower:
            return "total"
        if "total" in name_lower and ("over" in name_lower or "under" in name_lower or "maps" in name_lower):
            return "total"
        if "handicap" in name_lower or "spread" in name_lower:
            return "spread"

        return None

    @staticmethod
    def _normalize_outcome(
        result_key: str,
        name: str,
        market_type: str,
        home_raw: str,
        away_raw: str,
    ) -> Optional[str]:
        """Normalize outcome name from Coolbet result_key/name."""
        rk = result_key.lower().strip("[]")

        if market_type in ("1x2", "moneyline", "spread"):
            if rk == "home":
                return "home"
            elif rk == "away":
                return "away"
            elif rk == "draw":
                return "draw"
            else:
                return normalize_outcome(name, home_raw, away_raw)

        elif market_type == "total":
            rk_check = rk.lower()
            if rk_check == "over":
                return "over"
            elif rk_check == "under":
                return "under"
            # Fallback to name
            name_lower = name.lower()
            if "över" in name_lower or "over" in name_lower:
                return "over"
            elif "under" in name_lower:
                return "under"

        return None

    async def close(self):
        """Close Camoufox browser and transport."""
        await self._cleanup_camoufox()
        await super().close()

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — browser-based extraction."""
        return []
