"""
Coolbet Retriever - Proprietary GAN Sports platform

Coolbet uses a proprietary sportsbook (formerly GAN Sports) with Imperva/Incapsula
bot protection. Uses browser-based API interception via Playwright.

API endpoints (proxied through coolbet.com):
- GET /s/sbgate/sports/fo-category/?categoryId={id} — category/league listing with matches and markets
- POST /s/sb-odds/odds/current/fo — odds values keyed by outcome ID
- POST /s/sb-odds/odds/current/fo-line/ — line (spread/total) odds

The category API returns match structure including:
- match.home_team_name, match.away_team_name, match.match_start
- match.markets[].name, match.markets[].outcomes[].id, outcome.result_key
- Odds decimal values come from the sb-odds POST (keyed by outcome ID)

Sport category IDs (discovered via /s/sbgate/category/by-slug/sv/):
- Football: 62
- Basketball: 77
- Tennis: 72
- Ice Hockey: 85
- American Football: 58
- Baseball: 96
- MMA: 20491
- Esports: 65035
- Handball: 68

URL structure: /sv/odds/{sport-slug}
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
import json
from datetime import datetime

from ..core import StandardEvent
from ..core.browser_retriever import BrowserRetriever
from ..core.transport import BrowserTransport
from ..matching.normalizer import normalize_team_name, normalize_outcome

logger = logging.getLogger(__name__)


class CoolbetRetriever(BrowserRetriever):
    """Retriever for Coolbet sportsbook (GAN Sports platform)."""

    # Sport slug → categoryId mapping (discovered via /s/sbgate/category/by-slug/sv/)
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

    # Markets we extract (Coolbet naming)
    MARKET_MAP = {
        "Match Result (1X2)":       "1x2",
        "Match Winner":             "moneyline",
        "Moneyline":                "moneyline",
        "Total Goals Over / Under": "total",
        "Total Points Over / Under": "total",
        "Total Over / Under":       "total",
        "Asian Handicap":           "spread",
        "Handicap":                 "spread",
        "Spread":                   "spread",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.coolbet.com")

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events using Coolbet's internal API (via browser context for auth).

        Flow:
        1. Navigate to sport page (establishes session/cookies)
        2. Fetch category data via direct API call (gets all leagues + matches)
        3. Collect outcome IDs from prematch matches
        4. Fetch odds via POST to sb-odds endpoint
        5. Merge and parse into StandardEvents
        """
        sport_conf = self.SPORT_CONFIG.get(sport)
        if not sport_conf:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] CoolbetRetriever requires BrowserTransport")
                return []

            try:
                await self.transport._ensure_browser()
            except Exception as e:
                logger.error(
                    f"[{self.provider_id}] Failed to connect browser. "
                    f"Coolbet requires CDP connection to bypass Imperva. "
                    f"Start Chrome with: chrome --remote-debugging-port=9222  |  Error: {e}"
                )
                return []
            page = self.transport.page

            # Navigate to sport page to establish session (needed for API auth)
            if not self._session_ready:
                sport_url = f"{self.site_url}/sv/odds/{sport_conf['slug']}"
                logger.info(f"[{self.provider_id}] Loading {sport_url}")

                # Intercept odds responses during initial page load
                odds_data: Dict[str, Any] = {}
                pending_tasks = []

                async def capture_odds(response):
                    try:
                        if '/s/sb-odds/odds/current/fo' in response.url:
                            data = await response.json()
                            if isinstance(data, dict):
                                odds_data.update(data)
                    except Exception:
                        pass

                def on_response(response):
                    if '/s/sb-odds/' in response.url:
                        pending_tasks.append(asyncio.create_task(capture_odds(response)))

                page.on('response', on_response)
                await page.goto(sport_url, wait_until='load', timeout=60000)

                # Check for Imperva block
                await asyncio.sleep(3)
                body_text = await page.evaluate(
                    'document.body ? document.body.innerText.substring(0, 500) : ""'
                )
                if 'Incapsula' in body_text or 'security check' in body_text.lower() or \
                   'Access denied' in body_text or 'Error 15' in body_text:
                    logger.error(
                        f"[{self.provider_id}] Imperva block detected. "
                        f"Start Chrome with: chrome --remote-debugging-port=9222"
                    )
                    page.remove_listener('response', on_response)
                    return []

                await asyncio.sleep(5)
                page.remove_listener('response', on_response)
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)

                self._session_ready = True
            else:
                odds_data = {}

            # Fetch category data via direct API call
            category_data = await self._fetch_category_api(
                page, sport_conf['category_id']
            )

            if not category_data:
                logger.warning(f"[{self.provider_id}] No category data for {sport}")
                return []

            # Collect market IDs from prematch matches to fetch odds
            market_ids = []
            for cat in category_data:
                for match in cat.get("matches", []):
                    if match.get("inplay"):
                        continue
                    for market in match.get("markets", []):
                        mid = market.get("id")
                        if mid:
                            market_ids.append(mid)

            # Fetch odds for all markets via POST
            if market_ids:
                fetched_odds = await self._fetch_odds_api(page, market_ids)
                odds_data.update(fetched_odds)

            logger.info(
                f"[{self.provider_id}] {sport}: {len(category_data)} categories, "
                f"{len(odds_data)} odds entries"
            )

            # Parse events
            events = self._parse_categories(category_data, odds_data, sport)
            logger.info(f"[{self.provider_id}] {sport}: {len(events)} events extracted")
            return events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    async def _fetch_category_api(self, page, category_id: int) -> List[Dict]:
        """Fetch category data via browser context (shares cookies/auth)."""
        try:
            url = (
                f"{self.site_url}/s/sbgate/sports/fo-category/"
                f"?categoryId={category_id}&country=SE&isMobile=0"
                f"&language=sv&layout=EUROPEAN&limit=500"
            )
            resp = await page.evaluate(f"""
                (async () => {{
                    const resp = await fetch('{url}', {{credentials: 'include'}});
                    return await resp.json();
                }})();
            """)
            if isinstance(resp, list):
                logger.info(
                    f"[{self.provider_id}] Category API: {len(resp)} categories"
                )
                return resp
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Direct category API failed: {e}")
        return []

    async def _fetch_odds_api(self, page, market_ids: List) -> Dict:
        """Fetch odds values for market IDs via the sb-odds fo-line endpoint.

        The Coolbet odds API accepts {"marketIds": [[id1], [id2], ...]}
        and returns odds keyed by outcome_id.
        """
        if not market_ids:
            return {}
        try:
            # Deduplicate and format as nested arrays
            unique_ids = list(set(market_ids))
            all_odds = {}
            chunk_size = 200
            for i in range(0, len(unique_ids), chunk_size):
                chunk = unique_ids[i:i + chunk_size]
                # Each market ID is wrapped in its own array
                market_arrays = [[mid] for mid in chunk]
                body = json.dumps({"marketIds": market_arrays})
                resp = await page.evaluate(f"""
                    (async () => {{
                        const resp = await fetch('/s/sb-odds/odds/current/fo-line/', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            credentials: 'include',
                            body: '{body}'
                        }});
                        return await resp.json();
                    }})();
                """)
                if isinstance(resp, dict):
                    all_odds.update(resp)
            logger.info(
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
        # Skip live events
        if match.get("inplay"):
            return None

        # Skip outrights/season bets
        match_type = match.get("match_type")
        if match_type == "OUTRIGHT":
            return None

        match_id = match.get("id")
        home_team_raw = match.get("home_team_name", "")
        away_team_raw = match.get("away_team_name", "")

        if not home_team_raw or not away_team_raw:
            return None

        home_team = normalize_team_name(home_team_raw)
        away_team = normalize_team_name(away_team_raw)

        # Parse start time
        start_time = None
        start_str = match.get("match_start")
        if start_str:
            try:
                start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                pass

        # Parse markets
        markets = []
        seen_market_types = set()

        for raw_market in match.get("markets", []):
            market_name = raw_market.get("name", "")
            market_type = self._normalize_market_type(market_name)
            if not market_type or market_type in seen_market_types:
                continue

            line = raw_market.get("line")
            point = None
            if line is not None and line != 0:
                try:
                    point = float(line)
                except (ValueError, TypeError):
                    pass

            outcomes = []
            for raw_outcome in raw_market.get("outcomes", []):
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

                # odds_entry can be a dict {value: ...} or a plain number
                if isinstance(odds_entry, dict):
                    odds_val = odds_entry.get("value")
                    # Skip suspended outcomes
                    if odds_entry.get("status") == "SUSPENDED":
                        continue
                else:
                    odds_val = odds_entry

                if odds_val is None or not isinstance(odds_val, (int, float)):
                    continue

                # Coolbet uses milliodds for values > 100 (e.g. 4501 = 4.501)
                if odds_val > 100:
                    odds_val = odds_val / 1000.0

                if odds_val <= 1.0:
                    continue

                # Normalize outcome name
                outcome_name = self._normalize_outcome(
                    result_key, outcome_name_raw, market_type,
                    home_team_raw, away_team_raw
                )
                if not outcome_name:
                    continue

                outcome_dict = {"name": outcome_name, "odds": float(odds_val)}
                if point is not None:
                    outcome_dict["point"] = point
                outcomes.append(outcome_dict)

            if outcomes:
                markets.append({"type": market_type, "outcomes": outcomes})
                seen_market_types.add(market_type)

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

    def _normalize_market_type(self, market_name: str) -> Optional[str]:
        """Map Coolbet market name to standard type."""
        # Exact match first
        if market_name in self.MARKET_MAP:
            return self.MARKET_MAP[market_name]

        # Fuzzy match
        name_lower = market_name.lower()
        if "1x2" in name_lower or "match result" in name_lower:
            return "1x2"
        if "winner" in name_lower or "moneyline" in name_lower:
            return "moneyline"
        if "over / under" in name_lower or "over/under" in name_lower or "total" in name_lower:
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
            name_lower = name.lower()
            if "över" in name_lower or "over" in name_lower:
                return "over"
            elif "under" in name_lower:
                return "under"

        return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — browser-based extraction."""
        return []
