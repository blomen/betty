"""
ComeOn Multi-League Retriever

Extracts events by navigating to individual league pages.
ComeOn Group platform with RSocket WebSocket data delivery.

URL structure: /sv/sportsbook/sport/{id}-{slug}/leagues/{id}-{slug}
League pages deliver 1x2 (id=1), moneyline (id=175,206), total (id=212) via WS.
"""

from typing import Dict, Any, List, Optional
import json
import logging
from datetime import datetime
import asyncio

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..matching.normalizer import normalize_team_name
from .mixins import RSocketMixin

logger = logging.getLogger(__name__)


class ComeOnMultiLeagueRetriever(BrowserRetriever, RSocketMixin):
    """
    Multi-league ComeOn retriever.

    Strategy: Navigate to sport page → extract league links → visit each league
    page in parallel → parse RSocket WS messages for events/markets/selections.
    """

    # Sport URL mapping: canonical sport key -> ComeOn URL path (no /sv/ prefix)
    SPORT_URL_MAP = {
        'football': '/sportsbook/sport/1-fotboll',
        'basketball': '/sportsbook/sport/2-basket',
        'american_football': '/sportsbook/sport/3-amerikansk-fotboll',
        'ice_hockey': '/sportsbook/sport/4-ishockey',
        'tennis': '/sportsbook/sport/6-tennis',
        'mma': '/sportsbook/sport/7-mma',
        'esports': '/sportsbook/sport/130-esport',
        'baseball': '/sportsbook/sport/12-baseboll',
        'handball': '/sportsbook/sport/10-handboll',
        'rugby': '/sportsbook/sport/16-rugby',
        'cricket': '/sportsbook/sport/17-cricket',
        'table_tennis': '/sportsbook/sport/26-bordtennis',
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")
        self.max_leagues = config.get("max_leagues", 100)
        self._league_cache: Dict[str, List[Dict[str, str]]] = {}

    async def _extract_league_links(self, page) -> List[Dict[str, str]]:
        """Extract league links from sport page DOM."""
        league_links = await page.evaluate('''() => {
            const links = [];
            const seen = new Set();

            document.querySelectorAll('a[href*="/leagues/"]').forEach(link => {
                const href = link.getAttribute('href');
                const text = link.textContent.trim();

                if (href && text && !href.includes('/events/')) {
                    const cleanHref = href.split('?')[0];
                    // Deduplicate by league ID (extract number from path)
                    const match = cleanHref.match(/\\/leagues\\/(\\d+)/);
                    const key = match ? match[1] : cleanHref;

                    if (!seen.has(key)) {
                        seen.add(key);
                        links.push({ href: cleanHref, text });
                    }
                }
            });

            return links;
        }''')

        logger.info(f"[{self.provider_id}] Found {len(league_links)} league links")
        return league_links

    # Market type mapping: marketType.id -> standard type
    MARKET_TYPE_MAP = {
        1: '1x2',          # 1x2 (3-way match result)
        175: 'moneyline',   # Vinnare (Winner, 2-way)
        206: 'moneyline',   # Vinnare inkl. övertid (Winner incl. overtime)
        212: 'total',        # Totalt inkl. övertid (Total incl. overtime)
    }

    def _normalize_market_type(self, market_type_id: int) -> str:
        """Map marketTypeId to standard market type."""
        return self.MARKET_TYPE_MAP.get(market_type_id, 'other')

    def _build_outcome(self, selection: dict, market_type: str) -> Optional[dict]:
        """Build normalized outcome dict from a selection."""
        odds = selection.get('trueOdds', 0.0)
        if not odds or odds <= 1.0:
            return None

        outcome_type = (selection.get('outcomeType') or '').lower()
        name = (selection.get('name') or '').lower()

        if market_type in ('1x2', 'moneyline'):
            if outcome_type == 'home':
                return {'name': 'home', 'odds': float(odds)}
            if outcome_type == 'away':
                return {'name': 'away', 'odds': float(odds)}
            if outcome_type in ('tie', 'draw'):
                return {'name': 'draw', 'odds': float(odds)}

        elif market_type == 'total':
            points = selection.get('points')
            if points is None or points == 0.0:
                return None
            if outcome_type == 'over' or name.startswith('över') or name.startswith('over'):
                return {'name': 'over', 'odds': float(odds), 'point': float(points)}
            if outcome_type == 'under' or name.startswith('under'):
                return {'name': 'under', 'odds': float(odds), 'point': float(points)}

        return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        raise NotImplementedError("ComeOnMultiLeagueRetriever uses extract() directly")

    async def extract(self, sport: str | List[str], limit: Optional[int] = None, **kwargs) -> List[StandardEvent]:
        """Extract events from one or more sports."""
        sports_to_extract = self._resolve_sports(sport)
        logger.info(f"[{self.provider_id}] Extracting {len(sports_to_extract)} sports: {', '.join(sports_to_extract)}")

        all_events = []
        for sport_key in sports_to_extract:
            try:
                sport_events = await self._extract_single_sport(sport_key, limit)
                logger.info(f"[{self.provider_id}] {sport_key}: {len(sport_events)} events")
                all_events.extend(sport_events)
            except Exception as e:
                logger.error(f"[{self.provider_id}] Failed to extract {sport_key}: {e}")

        return all_events

    def _resolve_sports(self, sport: str | List[str]) -> List[str]:
        if isinstance(sport, list):
            return sport
        if sport == "all":
            return list(self.SPORT_URL_MAP.keys())
        return [sport.split('/')[0] if '/' in sport else sport]

    async def _extract_single_sport(self, sport: str, limit: Optional[int] = None) -> List[StandardEvent]:
        """Extract events from a single sport via multi-league approach."""
        sport_normalized = sport.split('/')[0] if '/' in sport else sport

        sport_path = self.SPORT_URL_MAP.get(sport_normalized)
        if not sport_path:
            logger.warning(f"[{self.provider_id}] Sport '{sport_normalized}' not supported")
            return []

        logger.info(f"[{self.provider_id}] Starting extraction for {sport_normalized}")

        # Shared WS message storage across all league pages
        ws_messages = []
        all_events_data = {}  # event_id -> event_data dict

        try:
            await self.transport._ensure_browser()
            page = self.transport.page

            # Setup WS interception on main page
            def on_websocket(ws):
                def on_frame_received(payload):
                    if isinstance(payload, bytes):
                        decoded = self._decode_rsocket_frame(payload)
                        if decoded:
                            ws_messages.append(decoded)
                ws.on("framereceived", on_frame_received)
            page.on("websocket", on_websocket)

            # Load sport page with /sv/ prefix
            main_url = f"{self.site_url}/sv{sport_path}"
            logger.info(f"[{self.provider_id}] Loading {main_url}")
            await page.goto(main_url, wait_until='networkidle', timeout=45000)
            await page.wait_for_timeout(3000)

            # Cookie consent (first load only)
            for btn_text in ['Acceptera', 'Accept']:
                try:
                    await page.click(f'button:has-text("{btn_text}")', timeout=1500)
                    await page.wait_for_timeout(500)
                except:
                    pass

            # Extract league links (SPA may need extra wait for rendering)
            cache_key = sport_normalized
            if cache_key in self._league_cache:
                league_links = self._league_cache[cache_key]
                logger.info(f"[{self.provider_id}] Using cached leagues ({len(league_links)})")
            else:
                league_links = await self._extract_league_links(page)
                if not league_links:
                    # SPA rendering delay — wait and retry
                    await page.wait_for_timeout(5000)
                    league_links = await self._extract_league_links(page)
                if league_links:
                    self._league_cache[cache_key] = league_links

            if not league_links:
                logger.warning(f"[{self.provider_id}] No league links found for {sport_normalized}")
                return []

            leagues_to_process = league_links[:self.max_leagues]
            logger.info(f"[{self.provider_id}] Processing {len(leagues_to_process)}/{len(league_links)} leagues")

            # Step 2: Visit leagues in parallel
            concurrent_limit = self.config.get('concurrent_leagues', 8)
            sem = asyncio.Semaphore(concurrent_limit)

            async def extract_league(idx: int, league: dict) -> int:
                """Extract events from a single league page."""
                async with sem:
                    league_url = league['href']
                    if not league_url.startswith('http'):
                        league_url = f"{self.site_url}{league_url}"

                    league_page = await self.transport.new_page()
                    try:
                        # Setup WS on league page feeding into shared messages
                        def on_ws(ws):
                            def on_frame(payload):
                                if isinstance(payload, bytes):
                                    decoded = self._decode_rsocket_frame(payload)
                                    if decoded:
                                        ws_messages.append(decoded)
                            ws.on("framereceived", on_frame)
                        league_page.on("websocket", on_ws)

                        await league_page.goto(league_url, wait_until='networkidle', timeout=30000)
                        await league_page.wait_for_timeout(2000)
                        return 1
                    except Exception as e:
                        logger.debug(f"[{self.provider_id}] Failed league {league['text']}: {e}")
                        return 0
                    finally:
                        await league_page.close()

            tasks = [extract_league(i, lg) for i, lg in enumerate(leagues_to_process, 1)]
            logger.info(f"[{self.provider_id}] Extracting {len(tasks)} leagues (max {concurrent_limit} concurrent)")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successful = sum(1 for r in results if isinstance(r, int) and r > 0)
            logger.info(f"[{self.provider_id}] {successful}/{len(leagues_to_process)} leagues extracted")

            # Step 3: Parse WS data into events
            all_markets = {}
            all_selections = {}

            for msg_data in ws_messages:
                if not isinstance(msg_data, list):
                    continue
                for msg in msg_data:
                    if msg.get('type') != 'INITIAL_STATE':
                        continue
                    payload = msg.get('payload', {})

                    for event in payload.get('events', []):
                        eid = event.get('id')
                        if eid and eid not in all_events_data:
                            all_events_data[eid] = event

                    for market in payload.get('markets', []):
                        mid = market.get('id')
                        if mid:
                            all_markets[mid] = market

                    for sel in payload.get('selections', []):
                        sid = sel.get('id')
                        if sid:
                            all_selections[sid] = sel

            logger.info(f"[{self.provider_id}] WS totals: {len(all_events_data)} events, "
                        f"{len(all_markets)} markets, {len(all_selections)} selections")

            # Build event->markets and market->selections mappings
            event_markets_map: Dict[int, List[int]] = {}
            for mid, mkt in all_markets.items():
                eid = mkt.get('eventId')
                if eid:
                    event_markets_map.setdefault(eid, []).append(mid)

            market_selections_map: Dict[int, List[dict]] = {}
            for sid, sel in all_selections.items():
                mid = sel.get('marketId')
                if mid:
                    market_selections_map.setdefault(mid, []).append(sel)

            # Parse events
            parsed_events = []
            for eid, event_data in all_events_data.items():
                event = self._parse_event(event_data, sport_normalized,
                                          event_markets_map, all_markets, market_selections_map)
                if event:
                    parsed_events.append(event)

            logger.info(f"[{self.provider_id}] Parsed {len(parsed_events)} events for {sport_normalized}")
            return parsed_events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Extraction failed for {sport_normalized}: {e}", exc_info=True)
            return []

    def _parse_event(self, event_data: dict, sport: str,
                     event_markets_map: Dict[int, List[int]],
                     all_markets: Dict[int, dict],
                     market_selections_map: Dict[int, List[dict]]) -> Optional[StandardEvent]:
        """Parse a single event from WS data."""
        try:
            eid = event_data.get('id')
            if not eid:
                return None

            # Extract teams
            home_team = None
            away_team = None

            # Method 1: primaryParticipants dict with venueRole
            primary = event_data.get('primaryParticipants', {})
            if isinstance(primary, dict):
                for pid, p in primary.items():
                    role = p.get('venueRole', '')
                    if role == 'Home':
                        home_team = p.get('name')
                    elif role == 'Away':
                        away_team = p.get('name')

            # Method 2: Parse from eventName
            if not home_team or not away_team:
                event_name = event_data.get('eventName', '')
                if ' - ' in event_name:
                    parts = event_name.split(' - ', 1)
                    home_team = home_team or parts[0].strip()
                    away_team = away_team or parts[1].strip()

            if not home_team or not away_team:
                return None

            home_team = normalize_team_name(home_team)
            away_team = normalize_team_name(away_team)

            # Start time
            start_time_str = event_data.get('startingOn') or event_data.get('startTime')
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except ValueError:
                    pass

            # League
            league = event_data.get('leagueName', 'Unknown')

            # Build markets
            markets = []
            market_ids = event_markets_map.get(eid, [])

            for mid in market_ids:
                mkt = all_markets.get(mid)
                if not mkt:
                    continue

                # Get marketType from nested marketType dict
                mt = mkt.get('marketType', {})
                mt_id = mt.get('id', 0)
                market_type = self._normalize_market_type(mt_id)

                if market_type == 'other':
                    continue

                if mkt.get('isSuspended'):
                    continue

                selections = market_selections_map.get(mid, [])
                outcomes = []
                for sel in selections:
                    if sel.get('status') != 'Active':
                        continue
                    outcome = self._build_outcome(sel, market_type)
                    if outcome:
                        outcomes.append(outcome)

                if outcomes:
                    markets.append({
                        'type': market_type,
                        'outcomes': outcomes
                    })

            return StandardEvent(
                id=str(eid),
                name=f"{home_team} vs {away_team}",
                sport=sport,
                provider=self.provider_id,
                markets=markets,
                league=league,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time.isoformat() if start_time else ""
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event {event_data.get('id')}: {e}")
            return None
