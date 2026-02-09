"""
Snabbare Retriever - WebSocket-based sportsbook extraction

Snabbare (Sportradar MTS platform) uses WebSocket for real-time event data.
Binary frames contain JSON payloads with events, markets, and selections arrays.

Strategy:
1. REST API to discover leagues per sport (/v2/leagues?filter.sportId=N)
2. Navigate to each league page to trigger WS data
3. Parse events/markets/selections from WS payloads

Data structure (from WS frames):
- payload.events[]: {id, eventName, startingOn, sportId, leagueId, leagueName, status, ...}
- payload.markets[]: {id, eventId, marketType: {id, originalName}, ...}
- payload.selections[]: {eventId, marketId, outcomeType, name, trueOdds, points}

URL structure: /sv/sportsbook/sport/{sportId}-{slug}/leagues/{leagueId}-{slug}

Notes:
- Sport overview page only shows ~16 featured events with 1x2 markets
- League detail pages return all events for that league with 1x2 + goal markets
- Spread/total markets only available on individual event pages (not extracted)
- Event separator is " - " (dash), not " vs "
"""

from typing import Dict, Any, List, Optional
import json
import logging
import re
from datetime import datetime
import asyncio

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..matching.normalizer import normalize_team_name
from .mixins import RSocketMixin

logger = logging.getLogger(__name__)


class SnabbareRetriever(BrowserRetriever, RSocketMixin):
    """
    WebSocket-based retriever for Snabbare sportsbook.

    On first call, discovers all leagues via REST API, navigates to each
    to collect WS data, then caches results and returns filtered by sport.
    """

    # Snabbare sport ID → (canonical name, URL slug)
    SPORT_MAP: Dict[int, tuple] = {
        1:   ("football",          "fotboll"),
        2:   ("basketball",        "basket"),
        3:   ("american_football", "amerikansk-fotboll"),
        4:   ("ice_hockey",        "ishockey"),
        5:   ("rugby",             "rugby"),
        6:   ("tennis",            "tennis"),
        7:   ("handball",          "handboll"),
        8:   ("volleyball",        "volleyboll"),
        10:  ("table_tennis",      "bordtennis"),
        12:  ("baseball",          "baseboll"),
        17:  ("cricket",           "cricket"),
        31:  ("boxing",            "boxning"),
        37:  ("mma",               "mma"),
        130: ("esports",           "esport"),
        48:  ("darts",             "dart"),
    }

    # Reverse: canonical name → sport ID
    SPORT_ID_BY_NAME = {v[0]: k for k, v in SPORT_MAP.items()}

    # Outcome type → standard name
    OUTCOME_MAP = {
        "Home":  "home",
        "Away":  "away",
        "Draw":  "draw",
        "Over":  "over",
        "Under": "under",
    }

    # Market type IDs we want
    # typeId=1 is "1x2", typeId=8 is goal scorer (skip)
    MARKET_TYPE_MAP = {
        1: "1x2",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.snabbare.com")
        self.api_base = "https://www.snabbare.com/sportsbook-api/api"
        self.default_params = {
            "franchiseCode": "SWEDEN_SNABBARE",
            "locale": "sv",
        }
        # Cache all events on first extraction, then filter by sport
        self._all_events: Optional[Dict[str, List[StandardEvent]]] = None

    async def extract(self, sport: str, limit: int = 1000, **kwargs) -> List[StandardEvent]:
        """
        Extract events for a given sport.

        On first call, collects all events via WS interception across sports.
        Subsequent calls return cached results filtered by sport.
        """
        # Health check — return quickly without full extraction
        if limit <= 1 and self._all_events is None:
            return await self._quick_health_check()

        # Extract all sports on first call
        if self._all_events is None:
            self._all_events = await self._extract_all()

        events = self._all_events.get(sport, [])
        logger.info(f"[{self.provider_id}] {sport}: {len(events)} events")
        return events[:limit]

    async def _quick_health_check(self) -> List[StandardEvent]:
        """Quick health check: verify site is accessible."""
        try:
            if not isinstance(self.transport, BrowserTransport):
                return []
            await self.transport._ensure_browser()
            page = self.transport.page

            if not self._session_ready:
                await page.goto(self.site_url, wait_until="load", timeout=30000)
                await self._handle_cookie_consent(page)
                await asyncio.sleep(2)
                self._session_ready = True

            return []
        except Exception as e:
            logger.error(f"[{self.provider_id}] Health check failed: {e}")
            raise

    CONCURRENT_TABS = 3
    LEAGUE_SETTLE_TIME = 2.0  # seconds to wait for WS data after navigation

    async def _extract_all(self) -> Dict[str, List[StandardEvent]]:
        """
        Discover all leagues via REST API, then navigate per-league
        using concurrent tabs to collect WS event data across all sports.
        """
        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] Requires BrowserTransport")
                return {}

            await self.transport._ensure_browser()
            page = self.transport.page

            # Shared WS message store — all tabs feed into this list
            ws_messages: List = []

            # Setup WS interception on main page
            self._setup_ws_interception_into(page, ws_messages)

            # Initial page load to establish session + cookies
            if not self._session_ready:
                await page.goto(
                    f"{self.site_url}/sv/sportsbook",
                    wait_until="load", timeout=30000
                )
                await self._handle_cookie_consent(page)
                await asyncio.sleep(2)
                self._session_ready = True

            # Discover leagues per sport via REST API
            all_leagues: List[Dict] = []
            for sport_id, (canonical, slug) in self.SPORT_MAP.items():
                leagues = await self._fetch_leagues(page, sport_id)
                for league in leagues:
                    league["_sport_id"] = sport_id
                    league["_canonical"] = canonical
                    league["_slug"] = slug
                all_leagues.extend(leagues)

            # Filter to leagues with events
            active_leagues = [l for l in all_leagues if l.get("eventCount", 0) > 0]
            total_events = sum(l.get("eventCount", 0) for l in active_leagues)
            logger.info(
                f"[{self.provider_id}] Discovered {len(active_leagues)} active leagues "
                f"with {total_events} total events"
            )

            # Sort by event count descending (biggest leagues first)
            active_leagues.sort(key=lambda x: x.get("eventCount", 0), reverse=True)

            # Open extra tabs for concurrent navigation
            context = page.context
            extra_pages = []
            for _ in range(self.CONCURRENT_TABS - 1):
                try:
                    p = await context.new_page()
                    self._setup_ws_interception_into(p, ws_messages)
                    extra_pages.append(p)
                except Exception:
                    break

            all_pages = [page] + extra_pages
            page_pool = asyncio.Queue()
            for p in all_pages:
                await page_pool.put(p)

            leagues_processed = 0
            errors = 0

            async def visit_league(league: Dict):
                nonlocal leagues_processed, errors
                worker = await page_pool.get()
                try:
                    lid = league.get("id", "")
                    lname = league.get("name", "")
                    sport_slug = league["_slug"]
                    sport_id = league["_sport_id"]

                    name_slug = re.sub(r'[^a-z0-9]+', '-', lname.lower()).strip('-')
                    league_url = (
                        f"{self.site_url}/sv/sportsbook/sport/"
                        f"{sport_id}-{sport_slug}/leagues/{lid}-{name_slug}"
                    )

                    await worker.goto(
                        league_url,
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )
                    await asyncio.sleep(self.LEAGUE_SETTLE_TIME)
                    leagues_processed += 1

                except Exception as e:
                    logger.debug(f"[{self.provider_id}] League {league.get('name', '?')} error: {e}")
                    errors += 1
                finally:
                    await page_pool.put(worker)

            # Process in batches matching concurrency
            batch_size = self.CONCURRENT_TABS * 5
            for i in range(0, len(active_leagues), batch_size):
                batch = active_leagues[i:i + batch_size]
                await asyncio.gather(*(visit_league(l) for l in batch))

                if (i + batch_size) % 60 < batch_size:
                    logger.info(
                        f"[{self.provider_id}] Processed {leagues_processed} leagues, "
                        f"{len(ws_messages)} WS messages"
                    )

            logger.info(
                f"[{self.provider_id}] Processed {leagues_processed} leagues, "
                f"collected {len(ws_messages)} WS messages ({errors} errors)"
            )

            # Close extra pages
            for p in extra_pages:
                try:
                    await p.close()
                except Exception:
                    pass

            # Parse all WS messages into events
            events_by_sport = self._parse_ws_data(ws_messages)

            total = sum(len(v) for v in events_by_sport.values())
            sport_summary = ", ".join(
                f"{k}: {len(v)}" for k, v in sorted(events_by_sport.items())
            )
            logger.info(f"[{self.provider_id}] Total: {total} events ({sport_summary})")

            return events_by_sport

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting: {e}", exc_info=True)
            return {}

    async def _fetch_leagues(self, page, sport_id: int) -> List[Dict]:
        """Fetch all leagues for a sport via REST API using browser context."""
        try:
            url = (
                f"{self.api_base}/v2/leagues?"
                f"franchiseCode=SWEDEN_SNABBARE&locale=sv"
                f"&filter.sportId={sport_id}"
                f"&page=1&pageSize=200"
            )
            data = await page.evaluate(
                f"""async () => {{
                    const r = await fetch('{url}');
                    if (!r.ok) return [];
                    return await r.json();
                }}"""
            )
            if isinstance(data, list):
                active = [l for l in data if l.get("eventCount", 0) > 0]
                canonical = self.SPORT_MAP.get(sport_id, ("unknown", ""))[0]
                if active:
                    total = sum(l.get("eventCount", 0) for l in active)
                    logger.info(
                        f"[{self.provider_id}] {canonical}: "
                        f"{len(active)} active leagues, {total} events"
                    )
                return data
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to fetch leagues for sport {sport_id}: {e}")
        return []

    def _parse_ws_data(self, ws_messages: List) -> Dict[str, List[StandardEvent]]:
        """
        Parse WebSocket messages into StandardEvents grouped by sport.

        WS messages from RSocketMixin are already decoded to JSON lists.
        Each message is a list of dicts with 'payload' containing events/markets/selections.
        """
        # Collect all raw data
        all_events: Dict[str, Dict] = {}
        all_markets: Dict[str, List[Dict]] = {}
        all_selections: Dict[str, List[Dict]] = {}

        for msg_list in ws_messages:
            if not isinstance(msg_list, list):
                continue
            for item in msg_list:
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload", {})
                if not isinstance(payload, dict):
                    continue

                for ev in payload.get("events", []):
                    eid = str(ev.get("id", ""))
                    if eid:
                        all_events[eid] = ev

                for mkt in payload.get("markets", []):
                    eid = str(mkt.get("eventId", ""))
                    if eid:
                        all_markets.setdefault(eid, []).append(mkt)

                for sel in payload.get("selections", []):
                    mid = str(sel.get("marketId", ""))
                    if mid:
                        all_selections.setdefault(mid, []).append(sel)

        logger.info(
            f"[{self.provider_id}] WS data: "
            f"{len(all_events)} events, "
            f"{sum(len(v) for v in all_markets.values())} markets, "
            f"{sum(len(v) for v in all_selections.values())} selections"
        )

        # Build StandardEvents
        events_by_sport: Dict[str, List[StandardEvent]] = {}
        seen: set = set()

        for eid, ev in all_events.items():
            std_event = self._build_event(eid, ev, all_markets.get(eid, []), all_selections)
            if not std_event:
                continue

            key = f"{std_event.home_team}:{std_event.away_team}:{std_event.start_time}"
            if key in seen:
                continue
            seen.add(key)

            events_by_sport.setdefault(std_event.sport, []).append(std_event)

        return events_by_sport

    def _build_event(
        self,
        event_id: str,
        ev: Dict,
        markets_raw: List[Dict],
        all_selections: Dict[str, List[Dict]],
    ) -> Optional[StandardEvent]:
        """Build a StandardEvent from WS event + markets + selections data."""
        event_name = ev.get("eventName", "")
        if not event_name:
            return None

        # Snabbare uses " - " as separator
        parts = re.split(r'\s+-\s+', event_name, maxsplit=1)
        if len(parts) != 2:
            # Fallback: try other separators
            parts = re.split(r'\s+(?:vs\.?|–|—)\s+', event_name, maxsplit=1)
            if len(parts) != 2:
                return None

        home_raw, away_raw = parts[0].strip(), parts[1].strip()
        if not home_raw or not away_raw:
            return None

        # Skip live events
        if ev.get("isLive", False):
            return None
        status = ev.get("status", "")
        if status and status.lower() in ("live", "started", "inprogress"):
            return None

        # Determine sport from sportId
        sport_id = ev.get("sportId")
        sport_info = self.SPORT_MAP.get(sport_id)
        if not sport_info:
            return None
        canonical_sport = sport_info[0]

        # Parse start time
        start_time = self._parse_start_time(ev.get("startingOn"))

        # Parse markets
        markets = self._parse_markets(markets_raw, all_selections, home_raw, away_raw)
        if not markets:
            return None

        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)
        league = ev.get("leagueName", "")

        return StandardEvent(
            id=f"snabbare_{event_id}",
            name=f"{home_raw} vs {away_raw}",
            provider=self.provider_id,
            sport=canonical_sport,
            league=league,
            home_team=home_team,
            away_team=away_team,
            start_time=start_time.isoformat() if start_time else "",
            markets=markets,
        )

    def _parse_markets(
        self,
        markets_raw: List[Dict],
        all_selections: Dict[str, List[Dict]],
        home_raw: str,
        away_raw: str,
    ) -> List[Dict]:
        """Parse markets and their selections into standardized format."""
        markets: List[Dict] = []
        seen_types: set = set()

        for mkt in markets_raw:
            mt_info = mkt.get("marketType", {})
            mt_id = mt_info.get("id")

            market_type = self.MARKET_TYPE_MAP.get(mt_id)
            if not market_type:
                # Fallback: check name patterns
                mt_name = (mt_info.get("originalName", "") or mt_info.get("name", "")).lower()
                market_type = self._classify_market_by_name(mt_name)

            if not market_type or market_type in seen_types:
                continue

            market_id = str(mkt.get("id", ""))
            selections = all_selections.get(market_id, [])
            if not selections:
                continue

            outcomes = []
            point_value = None

            for sel in selections:
                odds = sel.get("trueOdds")
                if odds is None:
                    continue
                try:
                    odds_val = float(odds)
                except (ValueError, TypeError):
                    continue
                if odds_val <= 1.0:
                    continue

                # Skip suspended selections
                if sel.get("status", "").lower() == "suspended":
                    continue

                outcome_name = self.OUTCOME_MAP.get(sel.get("outcomeType", ""))
                if not outcome_name:
                    # Try matching by selection name
                    outcome_name = self._match_outcome_by_name(
                        sel.get("name", ""), home_raw, away_raw
                    )
                if not outcome_name:
                    continue

                # Extract point value for spread/total
                points = sel.get("points")
                if points is not None and points != 0.0:
                    try:
                        point_value = float(points)
                    except (ValueError, TypeError):
                        pass

                outcome_dict: Dict[str, Any] = {"name": outcome_name, "odds": odds_val}
                if point_value is not None and market_type in ("spread", "total"):
                    outcome_dict["point"] = point_value
                outcomes.append(outcome_dict)

            if outcomes:
                markets.append({"type": market_type, "outcomes": outcomes})
                seen_types.add(market_type)

        # Dedup: prefer 1x2 over moneyline
        types = {m["type"] for m in markets}
        if "1x2" in types and "moneyline" in types:
            markets = [m for m in markets if m["type"] != "moneyline"]

        return markets

    def _classify_market_by_name(self, name: str) -> Optional[str]:
        """Fallback: classify market type from name string."""
        if not name:
            return None
        if "1x2" in name:
            return "1x2"
        if any(w in name for w in ("vinnare", "matchvinnare", "winner")):
            return "moneyline"
        if any(w in name for w in ("över/under", "over/under", "totalt")):
            return "total"
        if any(w in name for w in ("handikapp", "handicap", "spread")):
            return "spread"
        return None

    def _match_outcome_by_name(
        self, sel_name: str, home_raw: str, away_raw: str
    ) -> Optional[str]:
        """Match selection name to outcome when outcomeType isn't standard."""
        sel_lower = sel_name.lower().strip()
        home_lower = home_raw.lower().strip()
        away_lower = away_raw.lower().strip()

        if sel_lower == home_lower or sel_lower.startswith(home_lower):
            return "home"
        if sel_lower == away_lower or sel_lower.startswith(away_lower):
            return "away"
        if sel_lower in ("draw", "oavgjort", "x"):
            return "draw"
        if "over" in sel_lower or "över" in sel_lower:
            return "over"
        if "under" in sel_lower:
            return "under"
        return None

    def _parse_start_time(self, dt_val: Any) -> Optional[datetime]:
        """Parse ISO datetime string from WS data."""
        if not dt_val:
            return None
        try:
            if isinstance(dt_val, str):
                return datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
            elif isinstance(dt_val, (int, float)):
                ts = dt_val / 1000 if dt_val > 10**10 else dt_val
                return datetime.fromtimestamp(ts)
        except Exception:
            pass
        return None

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        selectors = [
            'button:has-text("Acceptera")',
            'button:has-text("Accept")',
            'button:has-text("Godkänn")',
            'button:has-text("OK")',
            '[class*="cookie"] button',
        ]
        for sel in selectors:
            try:
                await page.click(sel, timeout=3000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent")
                await asyncio.sleep(1)
                return
            except Exception:
                continue

    def parse(self, events_data: List[Dict], sport: str) -> List[StandardEvent]:
        """Not used - extract() is overridden."""
        raise NotImplementedError("SnabbareRetriever uses extract() directly")
