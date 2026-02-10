"""
ComeOn Date-Based Retriever

Extracts events by clicking through date buttons on the sport page.
ComeOn Group platform with RSocket WebSocket data delivery.

URL structure: /sv/sportsbook/sport/{id}-{slug}
Sport page shows today's events initially. Clicking date buttons (11 feb, 12 feb, ...)
triggers new WS INITIAL_STATE messages with events for that date.

Markets: 1x2 (id=1), moneyline (id=175,206), total (id=212) via WS.

Note: League page navigation in new tabs does NOT work — the WS connection
only delivers data to the page that initiated it. Date-based extraction on
the main page is the correct approach.
"""

from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..matching.normalizer import normalize_team_name
from .mixins import RSocketMixin

logger = logging.getLogger(__name__)


class ComeOnMultiLeagueRetriever(BrowserRetriever, RSocketMixin):
    """
    Date-based ComeOn retriever.

    Strategy: Navigate to sport page → dismiss cookies → click through date
    buttons → parse RSocket WS messages for events/markets/selections.
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

    async def _dismiss_cookie_overlay(self, page) -> None:
        """Dismiss OneTrust cookie consent overlay.

        ComeOn's SPA may navigate after cookie accept, destroying the
        execution context. We wait for the page to settle before removing
        overlay elements.
        """
        try:
            btn = await page.query_selector('#onetrust-accept-btn-handler')
            if btn:
                await btn.click()
                # Wait for potential SPA navigation to complete
                await page.wait_for_load_state('domcontentloaded', timeout=5000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass
        # Force-remove overlay elements that intercept clicks
        try:
            await page.evaluate('''() => {
                const filter = document.querySelector('.onetrust-pc-dark-filter');
                if (filter) filter.remove();
                const sdk = document.querySelector('#onetrust-consent-sdk');
                if (sdk) sdk.remove();
            }''')
        except Exception:
            pass  # Context may have been destroyed — overlay is likely gone anyway

    async def _extract_single_sport(self, sport: str, limit: Optional[int] = None) -> List[StandardEvent]:
        """Extract events from a single sport via date-button navigation.

        ComeOn shows today's events by default. Clicking date buttons
        (11 feb, 12 feb, ...) triggers new WS INITIAL_STATE messages
        with events for that date. We click through all available dates
        to capture all upcoming events.
        """
        sport_normalized = sport.split('/')[0] if '/' in sport else sport

        sport_path = self.SPORT_URL_MAP.get(sport_normalized)
        if not sport_path:
            logger.warning(f"[{self.provider_id}] Sport '{sport_normalized}' not supported")
            return []

        logger.info(f"[{self.provider_id}] Starting extraction for {sport_normalized}")

        ws_messages = []
        all_events_data = {}  # event_id -> event_data dict

        try:
            await self.transport._ensure_browser()
            page = self.transport.page

            # Setup WS interception — persists across SPA navigations
            def on_websocket(ws):
                def on_frame_received(payload):
                    if isinstance(payload, bytes):
                        decoded = self._decode_rsocket_frame(payload)
                        if decoded:
                            ws_messages.append(decoded)
                ws.on("framereceived", on_frame_received)
            page.on("websocket", on_websocket)

            # Load sport page
            main_url = f"{self.site_url}/sv{sport_path}"
            logger.info(f"[{self.provider_id}] Loading {main_url}")
            await page.goto(main_url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(1500)

            # Dismiss cookie overlay — may trigger SPA navigation
            await self._dismiss_cookie_overlay(page)

            # Check if we're still on the sport page, if not navigate back
            current_url = page.url
            if sport_path not in current_url:
                logger.info(f"[{self.provider_id}] Cookie redirect detected, navigating back to {main_url}")
                await page.goto(main_url, wait_until='domcontentloaded', timeout=30000)

            # Wait for WS data to arrive (RSocket needs time to establish + send INITIAL_STATE)
            await page.wait_for_timeout(3000)

            # Step 1: Collect today's events from initial WS messages
            self._collect_ws_events(ws_messages, all_events_data)
            logger.info(f"[{self.provider_id}] Today: {len(all_events_data)} events from WS")

            # Step 2: Find all date buttons and click through them
            date_buttons = await page.evaluate(r'''() => {
                const btns = [];
                document.querySelectorAll('button').forEach((btn, idx) => {
                    const text = btn.textContent.trim().toLowerCase();
                    // Match date patterns like "11 feb.", "ons11 feb."
                    if (/\d+\s+\w{3}\.?$/.test(text) && !text.startsWith('idag')) {
                        btns.push(idx);
                    }
                });
                return btns;
            }''')

            if date_buttons:
                logger.info(f"[{self.provider_id}] Found {len(date_buttons)} date buttons")
                for btn_idx in date_buttons:
                    before_count = len(all_events_data)
                    try:
                        await page.evaluate(f'document.querySelectorAll("button")[{btn_idx}].click()')
                        await page.wait_for_timeout(2000)

                        # Collect new events from WS
                        self._collect_ws_events(ws_messages, all_events_data)
                    except Exception as e:
                        logger.debug(f"[{self.provider_id}] Date button {btn_idx} failed: {e}")

            logger.info(f"[{self.provider_id}] Total events after date scan: {len(all_events_data)}")

            # Step 3: Parse WS data into structured events
            all_markets = {}
            all_selections = {}

            for msg_data in ws_messages:
                if not isinstance(msg_data, list):
                    continue
                for msg in msg_data:
                    if not isinstance(msg, dict):
                        continue
                    payload = msg.get('payload', {})

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

    def _collect_ws_events(self, ws_messages: list, all_events_data: dict) -> None:
        """Collect events from WS messages into all_events_data dict."""
        for msg_data in ws_messages:
            if not isinstance(msg_data, list):
                continue
            for msg in msg_data:
                if not isinstance(msg, dict):
                    continue
                payload = msg.get('payload', {})
                for event in payload.get('events', []):
                    eid = event.get('id')
                    if eid and eid not in all_events_data:
                        all_events_data[eid] = event

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
