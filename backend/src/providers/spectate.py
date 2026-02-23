from typing import List, Any, Optional, Dict, Set
import logging
import re
import json
import asyncio
from datetime import datetime, timedelta
from ..core import BrowserRetriever, StandardEvent, BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

class SpectateRetriever(BrowserRetriever):
    """
    Retriever for 888sport / Spectate based sites.
    Uses BrowserTransport to bypass protections.
    """
    # Track unknown market names for discovery (class-level to avoid noise)
    _logged_unknown_markets: set = set()

    SPORT_SLUGS: Dict[str, str] = {
        "football": "football", "basketball": "basketball", "tennis": "tennis",
        "ice_hockey": "ice-hockey", "american_football": "american-football",
        "baseball": "baseball", "mma": "mma", "esports": "esports",
        "rugby": "rugby-union", "cricket": "cricket", "boxing": "boxing",
        "motorsports": "motor-racing",
        "handball": "handball", "volleyball": "volleyball",
        "darts": "darts", "table_tennis": "table-tennis",
    }

    SITE_SLUGS: Dict[str, str] = {
        "football": "fotboll", "basketball": "basket", "tennis": "tennis",
        "ice_hockey": "ishockey", "american_football": "amerikansk-fotboll",
        "baseball": "baseboll", "mma": "mma", "esports": "esports",
        "rugby": "rugby-union", "cricket": "cricket", "boxing": "boxning",
        "motorsports": "motorsport",
        "handball": "handboll", "volleyball": "volleyboll",
        "darts": "dart", "table_tennis": "bordtennis",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

        self.api_base: str = config.get("api_base", "https://spectate-web.888sport.se/spectate")
        # Ensure site_url is clean (no trailing slash)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', '888sport.se')}")
        self.site_url: str = raw_site_url.rstrip("/")

        # ✅ OPTIMIZATION: Digest cache (TTL: 5 minutes)
        self._digest_cache: Dict[str, Dict] = {}
        self._digest_cache_time: Dict[str, datetime] = {}
        self._digest_cache_ttl: int = 300  # 5 minutes in seconds

        # ✅ OPTIMIZATION: Bucket response cache (TTL: 2 minutes)
        self._bucket_cache: Dict[str, List[StandardEvent]] = {}
        self._bucket_cache_time: Dict[str, datetime] = {}
        self._bucket_cache_ttl: int = 120  # 2 minutes (shorter for event data)

    async def _ensure_sport_init(self, sport: str) -> None:
        """Initialize session for a specific sport."""
        # Only initialize once for all sports
        if not self._session_ready:
            slug = self.SITE_SLUGS.get(sport, sport)
            www_url = f"{self.site_url}/sport/{slug}/"
            logger.debug(f"[{self.provider_id}] Initializing session via {www_url}")

            # Initialize browser and visit page
            await self.transport._ensure_browser()
            try:
                # Use 'load' for reliable initialization
                await self.transport.page.goto(www_url, wait_until="load", timeout=20000)
                # Wait for page JS and cookies to initialize
                await self.transport.page.wait_for_timeout(3000)
            except Exception as e:
                logger.error(f"[{self.provider_id}] Page load error: {e}")

            self._session_ready = True
            self._initialized_pages.add("spectate_session")
            logger.debug(f"[{self.provider_id}] Session initialized")

    async def extract(self, sport: str, limit: int = 1000, **kwargs) -> List[StandardEvent]:
        # 1. Ensure session is initialized for this sport
        await self._ensure_sport_init(sport)

        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        all_events: List[StandardEvent] = []

        # ✅ OPTIMIZATION 1: Check digest cache first
        digest = None
        if sport in self._digest_cache:
            cache_time = self._digest_cache_time.get(sport)
            if cache_time and (datetime.now() - cache_time).total_seconds() < self._digest_cache_ttl:
                digest = self._digest_cache[sport]
                logger.debug(f"[{self.provider_id}] Using cached digest for {sport}")

        # 2. Fetch Digest if not cached (Discovery)
        if digest is None:
            digest_url = f"/eventsrequest/getEventsDigest/{sport_slug}"
            digest = await self._fetch_api(digest_url)

            # Cache the digest
            if digest:
                self._digest_cache[sport] = digest
                self._digest_cache_time[sport] = datetime.now()

        # ✅ OPTIMIZATION 2: Better bucket filtering to avoid 400 errors
        buckets_to_fetch: List[str] = []

        if isinstance(digest, dict):
            # Prioritize near-term buckets (only if count > 0)
            for key in ["today", "tomorrow", "starting_soon"]:
                count = digest.get(key, 0)
                if isinstance(count, (int, float)) and count > 0:
                    buckets_to_fetch.append(key)

            # Check specific dates if upcoming has counts
            upcoming_counts = digest.get("upcoming", {})
            if isinstance(upcoming_counts, dict):
                for date_key, count in upcoming_counts.items():
                    # Only add dates with count > 0
                    if isinstance(count, (int, float)) and count > 0 and date_key not in buckets_to_fetch:
                        buckets_to_fetch.append(date_key)

        # Default fallback (only if no buckets found)
        if not buckets_to_fetch:
            buckets_to_fetch = ["upcoming"]

        # Deduplicate buckets
        unique_buckets: List[str] = []
        seen_buckets: Set[str] = set()
        for b in buckets_to_fetch:
            if b not in seen_buckets:
                unique_buckets.append(b)
                seen_buckets.add(b)

        logger.debug(f"[{self.provider_id}] {sport}: Crawling {len(unique_buckets)} buckets with events")

        # ✅ OPTIMIZATION 3: Fetch buckets in parallel instead of sequentially
        seen_events: Set[str] = set()

        async def fetch_bucket(bucket: str) -> List[StandardEvent]:
            """Fetch events from a single bucket with caching."""
            cache_key = f"{sport}:{bucket}"

            # ✅ OPTIMIZATION 4: Check bucket cache first
            if cache_key in self._bucket_cache:
                cache_time = self._bucket_cache_time.get(cache_key)
                if cache_time and (datetime.now() - cache_time).total_seconds() < self._bucket_cache_ttl:
                    logger.debug(f"[{self.provider_id}] Using cached bucket: {cache_key}")
                    return self._bucket_cache[cache_key]

            # Fetch from API if not cached or expired
            endpoint = f"/sportsbook-req/getUpcomingEvents/{sport_slug}/{bucket}"
            resp_data = await self._fetch_api(endpoint, method="POST")
            events = self.parse(resp_data, sport)

            # Cache the result
            self._bucket_cache[cache_key] = events
            self._bucket_cache_time[cache_key] = datetime.now()

            return events

        # Fetch all buckets concurrently
        tasks = [fetch_bucket(bucket) for bucket in unique_buckets]
        bucket_results = await asyncio.gather(*tasks)

        # Combine results and deduplicate
        for events in bucket_results:
            for ev in events:
                if ev.id not in seen_events:
                    all_events.append(ev)
                    seen_events.add(ev.id)

                    if limit and len(all_events) >= limit:
                        break

            if limit and len(all_events) >= limit:
                break

        return all_events

    async def _fetch_api(self, endpoint: str, method: str = "GET", data: Any = None, headers: Optional[Dict[str, str]] = None) -> Any:
        url = f"{self.api_base}{endpoint}"

        base_headers = {
             "accept": "application/json",
             "origin": self.site_url,
             "referer": f"{self.site_url}/",
        }
        if headers:
            base_headers.update(headers)

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] SpectateRetriever requires BrowserTransport")
                return {}

            # Ensure browser is initialized before accessing context
            await self.transport._ensure_browser()

            # Validate browser context is ready
            if not self.transport.context:
                logger.error(f"[{self.provider_id}] Browser context not available")
                return {}

            # Use context.request like the working debug script
            if method.upper() == "POST":
                response = await self.transport.context.request.post(url, headers=base_headers)
            else:
                response = await self.transport.context.request.get(url, headers=base_headers)

            if response.status == 400:
                logger.debug(f"[{self.provider_id}] 400 Bad Request for {url} (endpoint: {endpoint})")
                return {}
            elif response.status == 403:
                logger.warning(f"[{self.provider_id}] 403 Forbidden for {url}. Origin/Headers might be rejected.")
                return {}
            elif response.status == 429:
                logger.warning(f"[{self.provider_id}] 429 Rate Limited. Backing off.")
                await asyncio.sleep(2) # Simple backoff
                return {}
            elif response.status not in (200, 201):
                logger.warning(f"[{self.provider_id}] {method} {url} returned {response.status}")
                return {}

            try:
                return await response.json()
            except Exception:
                # Fallback for text/html responses that might be JSON
                text = await response.text()
                try:
                    return json.loads(text)
                except Exception:
                    logger.debug(f"[{self.provider_id}] Failed to parse JSON response from {url}")
                    return {}
        except Exception as e:
            logger.error(f"[{self.provider_id}] API fetch failed: {e}")
            return {}

    def parse(self, data: Any, sport: str, league: str = "") -> List[StandardEvent]:
        """Parse API response data into StandardEvents."""
        events: List[StandardEvent] = []
        if not data: return events

        # unexpected types
        if not isinstance(data, (dict, list)):
            return events

        # Handle list-wrapped responses
        if isinstance(data, list):
            for item in data:
                events.extend(self.parse(item, sport, league))
            return events

        # Handle Dict with 'events' key
        events_data = data.get("events")
        if events_data:
            items = events_data.values() if isinstance(events_data, dict) else events_data if isinstance(events_data, list) else []
            for ev_data in items:
                if isinstance(ev_data, dict):
                    ev = self._parse_event(ev_data, sport, league)
                    if ev: events.append(ev)
        
        # Handle single event dict structure (Top-level)
        # Note: Spectate usually wraps in 'events', but sometimes for single event requests it's direct
        if not events and isinstance(data, dict) and data.get("name") and data.get("id") and data.get("markets"):
            ev = self._parse_event(data, sport, league)
            if ev: events.append(ev)
            
        return events

    def _parse_event(self, event_data: dict, sport: str, league: str) -> Optional[StandardEvent]:
        try:
            if event_data.get("inplay"): return None

            ev_id = str(event_data.get("id", ""))
            name = event_data.get("name", "")
            start_time = event_data.get("start_time", "")
            
            # Competitors Logic
            competitors = event_data.get("competitors", {})
            home, away = "", ""
            comps = competitors.values() if isinstance(competitors, dict) else competitors if isinstance(competitors, list) else []
            
            for c in comps:
                if not isinstance(c, dict): continue
                if c.get("home") or c.get("is_home_team"): 
                    home = c.get("name", "")
                else: 
                    away = c.get("name", "")
            
            # Fallback name split
            if (not home or not away):
                if " v " in name:
                    parts = name.split(" v ", 1)
                    home, away = parts[0].strip(), parts[1].strip()
                elif " vs " in name:
                    parts = name.split(" vs ", 1)
                    home, away = parts[0].strip(), parts[1].strip()

            if not home or not away or not ev_id:
                return None

            # Save raw names for market parsing (spread outcome resolution)
            raw_home, raw_away = home, away

            # Normalize team names
            home = normalize_team_name(home)
            away = normalize_team_name(away)

            markets = self._parse_markets(event_data, home_raw=raw_home, away_raw=raw_away)
            if not markets:
                return None

            return StandardEvent(
                id=ev_id,
                name=name,
                home_team=home,
                away_team=away, 
                sport=sport,
                league=event_data.get("tournament_name") or event_data.get("tournament", {}).get("name") or league,
                start_time=start_time, 
                markets=markets, 
                provider=self.provider_id
            )
        except Exception as e:
            # logger.debug(f"Event parsing error: {e}")
            return None

    def _parse_markets(self, event_data: dict, home_raw: str = "", away_raw: str = "") -> List[dict]:
        markets: List[dict] = []
        m_data = event_data.get("markets", {})
        items = m_data.values() if isinstance(m_data, dict) else m_data if isinstance(m_data, list) else []
        
        # Standardized Mappings
        MARKET_MAP = {
            # 1x2 / moneyline
            "match winner": "moneyline",
            "vinnare": "moneyline",
            "utdelningsrader": "moneyline",
            "1x2": "moneyline",
            "fulltid": "1x2",
            "matchresultat": "moneyline",
            "matchresultat (2-vägs)": "moneyline",
            "matchresultat (3-vägs)": "1x2",
            "matchvinnare": "moneyline",
            "matchvinnare tvåvägs": "moneyline",
            "matchvinnare (3-vägs)": "1x2",
            "fightodds": "moneyline",
            "matchodds": "1x2",
            # Spread / handicap
            "pucklinje": "spread",
            "poänghandikapp": "spread",
            "handicap": "spread",
            "asiatisk handicap": "spread",
            "run line": "spread",
            "setshandicap": "spread",
            "gamehandicap": "spread",
            # Total (over/under)
            "totalt antal mål i match, över/under": "total",
            "totalt antal poäng, över/under": "total",
            "totalt antal poäng": "total",
            "over/under": "total",
            "över/under": "total",
            "totalt antal mål": "total",
            "total": "total",
            "totalt antal set, över/under": "total",
            "totalt antal games, över/under": "total",
            # Darts / snooker / misc
            "matchresultat tvåvägs": "moneyline",
            "legs handicap": "spread",
            "totalt antal legs, över/under": "total",
            "frames handicap": "spread",
            "totalt antal frames, över/under": "total",
            "sets handicap": "spread",
        }

        for m in items:
            if not isinstance(m, dict): continue
            raw_name = m.get("name", "").lower().strip()

            m_type = MARKET_MAP.get(raw_name)
            if not m_type:
                # Try fuzzy matching for spread/total keywords
                if raw_name:
                    m_type = self._fuzzy_market_match(raw_name)
                if not m_type:
                    if raw_name and raw_name not in self._logged_unknown_markets:
                        self._logged_unknown_markets.add(raw_name)
                        logger.debug(f"[{self.provider_id}] Unknown market: '{raw_name}'")
                    continue

            s_data = m.get("selections", {})
            s_items = s_data.values() if isinstance(s_data, dict) else s_data if isinstance(s_data, list) else []

            outcomes: List[dict] = []
            for s in s_items:
                if not isinstance(s, dict) or not s.get("active", True): continue
                try:
                    price = s.get("decimal_price") or s.get("price")
                    if not price:
                        continue
                    sel_name = s.get("name", "")
                    outcome: Dict[str, Any] = {"odds": round(float(price), 3)}

                    if m_type == "total":
                        # "Over (5.5)" / "Under (5.5)" → name=over/under, point=5.5
                        match = re.match(r'(over|under)\s*\(([0-9.]+)\)', sel_name, re.IGNORECASE)
                        if match:
                            outcome["name"] = match.group(1).lower()
                            outcome["point"] = float(match.group(2))
                        else:
                            continue
                    elif m_type == "spread":
                        # "Team (+2.5)" / "Team (-2.5)" → name=home/away, point=±2.5
                        match = re.search(r'\(([+-]?[0-9.]+)\)', sel_name)
                        if match:
                            point_val = float(match.group(1))
                            # Strip point from name for team matching
                            team_part = re.sub(r'\s*\([+-]?[0-9.]+\)\s*', '', sel_name).strip().lower()
                            if home_raw and team_part == home_raw.lower():
                                outcome["name"] = "home"
                            elif away_raw and team_part == away_raw.lower():
                                outcome["name"] = "away"
                            elif home_raw and home_raw.lower() in team_part:
                                outcome["name"] = "home"
                            elif away_raw and away_raw.lower() in team_part:
                                outcome["name"] = "away"
                            else:
                                continue
                            outcome["point"] = point_val
                        else:
                            continue
                    else:
                        outcome["name"] = sel_name

                    outcomes.append(outcome)
                except Exception:
                    continue

            if outcomes:
                markets.append({"type": m_type, "outcomes": outcomes})

        return markets

    @staticmethod
    def _fuzzy_market_match(name: str) -> str | None:
        """Catch spread/total markets not in the static MARKET_MAP via keyword matching."""
        # Spread keywords (Swedish + English)
        if any(kw in name for kw in (
            "handicap", "handikapp", "pucklinje", "run line",
            "spread", "asian handicap",
        )):
            return "spread"
        # Total keywords (Swedish + English)
        if any(kw in name for kw in (
            "över/under", "over/under", "totalt antal",
            "total goals", "total points", "total mål",
        )):
            return "total"
        return None
