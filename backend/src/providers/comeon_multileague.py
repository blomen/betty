"""
ComeOn Date-Based Retriever

Extracts events by clicking through date buttons on the sport page.
ComeOn Group platform with RSocket WebSocket data delivery.

URL structure: /sv/sportsbook/sport/{id}-{slug}
Sport page shows today's events initially. Clicking date buttons (11 feb, 12 feb, ...)
triggers new WS INITIAL_STATE messages with events for that date.

Markets: 1x2 (id=1), moneyline (id=175,206), total (id=212) via WS.
Pass 2 enrichment: navigates to individual event pages to extract spread/total
markets from per-event WS connections.

Note: League page navigation in new tabs does NOT work — the WS connection
only delivers data to the page that initiated it. Date-based extraction on
the main page is the correct approach.
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
from datetime import datetime

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..core.exceptions import RetryableError
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
    # Aligned with Snabbare's complete map (same SBTech/GAN platform)
    MARKET_TYPE_MAP = {
        # 1x2 (3-way)
        1: '1x2',
        # Moneyline (2-way)
        175: 'moneyline',   # Winner
        206: 'moneyline',   # Winner (incl. overtime)
        376: 'moneyline',   # Winner (incl. overtime and penalties) — MMA, handball
        # Total (over/under)
        18: 'total',         # Over/Under (generic — football, handball)
        202: 'total',        # Total goals
        212: 'total',        # Total (incl. overtime) — basketball
        225: 'total',        # Total Points O/U — basketball
        1621: 'total',       # Total Goals O/U (Regular Time) — ice hockey
        1622: 'total',       # Total Goals O/U — ice hockey
        # Spread (handicap)
        16: 'spread',        # Asian Handicap (generic — football)
        187: 'spread',       # Handicap — basketball
        203: 'spread',       # Handicap
        213: 'spread',       # Handicap (incl. overtime)
        1619: 'spread',      # Puck Line (Regular Time) — ice hockey
        1625: 'spread',      # Puck Line — ice hockey
    }

    def _normalize_market_type(self, market_type_id: int) -> str:
        """Map marketTypeId to standard market type."""
        return self.MARKET_TYPE_MAP.get(market_type_id, 'other')

    def _build_outcome(self, selection: dict, market_type: str,
                       home_team: str = '', away_team: str = '') -> Optional[dict]:
        """Build normalized outcome dict from a selection.

        Args:
            home_team/away_team: Optional normalized team names for matching
                when outcomeType is empty (common on event detail pages).
        """
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
            if outcome_type == 'over' or 'över' in name or 'over' in name:
                return {'name': 'over', 'odds': float(odds), 'point': float(points)}
            if outcome_type == 'under' or 'under' in name:
                return {'name': 'under', 'odds': float(odds), 'point': float(points)}

        elif market_type == 'spread':
            points = selection.get('points')
            if points is None:
                return None
            if outcome_type == 'home':
                return {'name': 'home', 'odds': float(odds), 'point': float(points)}
            if outcome_type == 'away':
                return {'name': 'away', 'odds': float(odds), 'point': float(points)}
            # Fallback: match selection name against team names (detail pages have empty outcomeType)
            if home_team and home_team in name:
                return {'name': 'home', 'odds': float(odds), 'point': float(points)}
            if away_team and away_team in name:
                return {'name': 'away', 'odds': float(odds), 'point': float(points)}

        return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        raise NotImplementedError("ComeOnMultiLeagueRetriever uses extract() directly")

    async def extract(self, sport: str | List[str], limit: Optional[int] = None, **kwargs) -> List[StandardEvent]:
        """Extract events from one or more sports."""
        sports_to_extract = self._resolve_sports(sport)
        logger.debug(f"[{self.provider_id}] Extracting {len(sports_to_extract)} sports: {', '.join(sports_to_extract)}")

        # Ensure browser once for entire extraction run (not per-sport)
        await self.transport._ensure_browser()
        page = self.transport.page

        # Dismiss cookie overlay once — persists across SPA navigations
        await self._dismiss_cookie_overlay(page)
        self._cookie_dismissed = True

        all_events = []
        sports_attempted = 0
        sports_with_events = 0
        for sport_key in sports_to_extract:
            try:
                sports_attempted += 1
                sport_events = await self._extract_single_sport(sport_key, limit)
                logger.debug(f"[{self.provider_id}] {sport_key}: {len(sport_events)} events")
                if sport_events:
                    sports_with_events += 1
                all_events.extend(sport_events)
            except Exception as e:
                logger.error(f"[{self.provider_id}] Failed to extract {sport_key}: {e}")

        if not all_events and sports_attempted >= 3:
            raise RetryableError(
                f"0 events from {sports_attempted} sports — possible WS/page failure",
                provider_id=self.provider_id,
            )

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

        logger.debug(f"[{self.provider_id}] Starting extraction for {sport_normalized}")

        ws_messages = []
        all_events_data = {}  # event_id -> event_data dict

        try:
            page = self.transport.page

            # Validate page is still alive — if dead, create new page from
            # existing browser context (avoids expensive full browser restart)
            try:
                await page.evaluate("() => true", timeout=5000)
            except Exception:
                logger.warning(f"[{self.provider_id}] Page context dead for {sport_normalized}, creating new page")
                try:
                    # Try creating a new page in existing context (fast — no browser restart)
                    page = await self.transport.context.new_page()
                    self.transport.page = page
                except Exception:
                    # Context also dead — full reinit as last resort
                    logger.warning(f"[{self.provider_id}] Context also dead, full browser reinit")
                    await self.transport.close()
                    await self.transport._ensure_browser()
                    page = self.transport.page
                    self._cookie_dismissed = False

            # Setup WS interception — persists across SPA navigations
            ws_connected = False

            def on_websocket(ws):
                nonlocal ws_connected
                ws_connected = True

                def on_frame_received(payload):
                    if isinstance(payload, bytes):
                        decoded = self._decode_rsocket_frame(payload)
                        if decoded:
                            ws_messages.append(decoded)
                ws.on("framereceived", on_frame_received)
            page.on("websocket", on_websocket)

            # Load sport page
            main_url = f"{self.site_url}/sv{sport_path}"
            logger.debug(f"[{self.provider_id}] Loading {main_url}")
            await page.goto(main_url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(1000)

            # Dismiss cookie overlay if not already done
            if not getattr(self, '_cookie_dismissed', False):
                await self._dismiss_cookie_overlay(page)
                self._cookie_dismissed = True

            # Check if we're still on the sport page, if not navigate back
            current_url = page.url
            if sport_path not in current_url:
                logger.debug(f"[{self.provider_id}] Cookie redirect detected, navigating back to {main_url}")
                await page.goto(main_url, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(1000)

            # Adaptive wait for WS data — poll until messages arrive or max wait reached
            # Saves 1-4s per sport vs fixed 3-5s wait
            max_ws_wait = 5.0 if sport_normalized == 'football' else 3.0
            elapsed_ws = 0.0
            await asyncio.sleep(1.0)  # Minimum wait for RSocket handshake
            elapsed_ws = 1.0
            while elapsed_ws < max_ws_wait:
                if ws_messages:
                    # Got data — wait a bit more for stragglers
                    await asyncio.sleep(0.5)
                    break
                await asyncio.sleep(0.3)
                elapsed_ws += 0.3

            # Verify WS actually connected and delivered data
            if not ws_connected:
                logger.warning(
                    f"[{self.provider_id}] WebSocket never connected for {sport_normalized} — "
                    f"SPA may not have loaded sporting content"
                )
            elif not ws_messages:
                logger.warning(
                    f"[{self.provider_id}] WebSocket connected but 0 frames decoded for {sport_normalized}"
                )

            # Step 1: Collect today's events from initial WS messages
            self._collect_ws_events(ws_messages, all_events_data)
            logger.debug(f"[{self.provider_id}] Today: {len(all_events_data)} events from WS")

            # Step 2: Scroll date container to reveal all dates, then click through them
            # The date strip is horizontally scrollable — only ~7-10 buttons visible initially.
            # Scrolling right reveals 14+ additional dates (up to ~21 total).
            await page.evaluate(r'''() => {
                // Find the scrollable container holding date buttons
                // Strategy: find a date button, walk up to find its scrollable parent
                const allBtns = document.querySelectorAll('button');
                let dateBtn = null;
                for (const btn of allBtns) {
                    const text = btn.textContent.trim().toLowerCase();
                    if (/\d+\s+\w{3}\.?$/.test(text)) {
                        dateBtn = btn;
                        break;
                    }
                }
                if (!dateBtn) return;

                // Walk up to find scrollable container (overflow-x: auto/scroll)
                let container = dateBtn.parentElement;
                for (let i = 0; i < 5 && container; i++) {
                    const style = window.getComputedStyle(container);
                    if (style.overflowX === 'auto' || style.overflowX === 'scroll' ||
                        container.scrollWidth > container.clientWidth) {
                        // Scroll to the far right to load all date buttons
                        container.scrollLeft = container.scrollWidth;
                        return;
                    }
                    container = container.parentElement;
                }

                // Fallback: if no scrollable parent found, try scrolling the date button's
                // immediate parent to the right
                if (dateBtn.parentElement) {
                    dateBtn.parentElement.scrollLeft = dateBtn.parentElement.scrollWidth;
                }
            }''')
            # Wait for any lazy-loaded date buttons to render after scroll
            await page.wait_for_timeout(300)

            # Now discover ALL date buttons (including newly revealed ones)
            # Collect button text labels instead of indices — indices shift when
            # clicking dates renders new event buttons in the DOM.
            date_labels = await page.evaluate(r'''() => {
                const labels = [];
                document.querySelectorAll('button').forEach(btn => {
                    const text = btn.textContent.trim();
                    const lower = text.toLowerCase();
                    // Match date patterns like "11 feb.", "ons11 feb."
                    if (/\d+\s+\w{3}\.?$/.test(lower) && !lower.startsWith('idag')) {
                        labels.push(text);
                    }
                });
                return labels;
            }''')

            # Skip date scanning only if no events AND no date buttons
            if not all_events_data and not date_labels:
                # Retry once for major sports — large payloads may need more time
                if sport_normalized in ('football', 'ice_hockey', 'basketball', 'tennis'):
                    logger.info(f"[{self.provider_id}] 0 events + 0 date buttons for {sport_normalized}, retrying with extended wait")
                    await page.wait_for_timeout(3000)
                    self._collect_ws_events(ws_messages, all_events_data)
                    date_labels = await page.evaluate(r'''() => {
                        const labels = [];
                        document.querySelectorAll('button').forEach(btn => {
                            const text = btn.textContent.trim();
                            const lower = text.toLowerCase();
                            if (/\d+\s+\w{3}\.?$/.test(lower) && !lower.startsWith('idag')) {
                                labels.push(text);
                            }
                        });
                        return labels;
                    }''')
                    if not all_events_data and not date_labels:
                        logger.warning(f"[{self.provider_id}] Still 0 events for {sport_normalized} after retry")
                        return []
                else:
                    logger.warning(f"[{self.provider_id}] No events and no date buttons for {sport_normalized}, skipping")
                    return []

            if date_labels:
                logger.debug(f"[{self.provider_id}] Found {len(date_labels)} date buttons")
                # Batch click: click each date quickly with minimal gap,
                # then do a single wait for all WS responses to arrive.
                ws_before = len(ws_messages)
                clicked_count = 0
                for label in date_labels:
                    try:
                        clicked = await page.evaluate('''(targetLabel) => {
                            const btns = document.querySelectorAll('button');
                            for (const btn of btns) {
                                if (btn.textContent.trim() === targetLabel) {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }''', label)
                        if clicked:
                            clicked_count += 1
                            await asyncio.sleep(0.05)  # Tiny gap between clicks
                    except Exception as e:
                        logger.debug(f"[{self.provider_id}] Date button '{label}' failed: {e}")

                if clicked_count > 0:
                    # Single adaptive wait for all WS data to arrive
                    await asyncio.sleep(1.0)
                    elapsed = 1.0
                    last_count = len(ws_messages)
                    while elapsed < 4.0:
                        await asyncio.sleep(0.3)
                        elapsed += 0.3
                        new_count = len(ws_messages)
                        if new_count > last_count:
                            last_count = new_count
                        elif elapsed > 2.0:
                            break  # No new data for 0.3s after 2s — done
                    self._collect_ws_events(ws_messages, all_events_data)
                    logger.debug(f"[{self.provider_id}] Batch clicked {clicked_count} dates, got {len(ws_messages) - ws_before} new WS messages")

            logger.debug(f"[{self.provider_id}] Total events after date scan: {len(all_events_data)}")

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

            logger.debug(f"[{self.provider_id}] WS totals: {len(all_events_data)} events, "
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

            logger.debug(f"[{self.provider_id}] Parsed {len(parsed_events)} events for {sport_normalized}")

            # Pass 2: Enrich with spread/total from event detail pages
            if parsed_events:
                try:
                    event_urls = await page.evaluate(self.JS_DISCOVER_EVENT_URLS)
                    logger.info(f"[{self.provider_id}] Pass 2: found {len(event_urls)} event URLs in DOM")
                    if event_urls:
                        enriched_count = await self._enrich_with_detail_markets(page, parsed_events, event_urls)
                        logger.info(f"[{self.provider_id}] Enriched {enriched_count} markets from {len(event_urls)} event URLs")
                    else:
                        logger.info(f"[{self.provider_id}] No event URLs found in DOM for enrichment")
                except Exception as e:
                    logger.error(f"[{self.provider_id}] Pass 2 enrichment failed: {e}")

            return parsed_events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Extraction failed for {sport_normalized}: {e}", exc_info=True)
            return []

    # --- Pass 2: Event detail page enrichment for spread/total ---

    MAX_DETAIL_EVENTS = 50         # Reduced from 100 — diminishing returns after 50

    JS_DISCOVER_EVENT_URLS = """() => {
        const links = {};
        document.querySelectorAll('a[href*="/events/"]').forEach(a => {
            const href = a.getAttribute('href');
            const match = href.match(/\\/events\\/(\\d+)/);
            if (match) links[match[1]] = href;
        });
        return links;
    }"""

    async def _enrich_with_detail_markets(
        self, page, events: List[StandardEvent], event_urls: Dict[str, str]
    ) -> int:
        """Navigate to event detail pages to extract spread and total markets.

        Uses the main page sequentially — ComeOn's SPA only establishes WS
        on the active page, so concurrent tabs don't receive WS data.
        Returns count of events enriched with additional markets.
        """
        event_by_id = {ev.id: ev for ev in events}

        # Filter to events missing spread or total AND having a URL
        todo = []
        for eid_str, ev in event_by_id.items():
            existing_types = {m['type'] for m in ev.markets}
            if 'spread' not in existing_types or 'total' not in existing_types:
                url = event_urls.get(eid_str)
                if url:
                    todo.append((ev, url))

        if not todo:
            logger.debug(f"[{self.provider_id}] No events need spread/total enrichment")
            return 0

        if len(todo) > self.MAX_DETAIL_EVENTS:
            logger.info(
                f"[{self.provider_id}] Capping detail enrichment from "
                f"{len(todo)} to {self.MAX_DETAIL_EVENTS} events"
            )
            todo = todo[:self.MAX_DETAIL_EVENTS]

        logger.info(f"[{self.provider_id}] Enriching {len(todo)} events with spread/total from detail pages")

        enriched = 0
        errors = 0
        consecutive_errors = 0

        for idx, (event, href) in enumerate(todo):
            if consecutive_errors > 10:
                logger.warning(f"[{self.provider_id}] Stopping enrichment after {consecutive_errors} consecutive errors")
                break

            try:
                detail_ws_messages: List[list] = []

                def on_ws(ws, msgs=detail_ws_messages):
                    def on_frame(payload, m=msgs):
                        if isinstance(payload, bytes):
                            decoded = self._decode_rsocket_frame(payload)
                            if decoded:
                                m.append(decoded)
                    ws.on("framereceived", on_frame)
                page.on("websocket", on_ws)

                url = f"{self.site_url}{href}" if href.startswith('/') else href
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=10000)
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Detail {event.id}: navigation failed: {e}")
                    page.remove_listener("websocket", on_ws)
                    errors += 1
                    consecutive_errors += 1
                    continue

                # Adaptive wait for WS data — listener must stay active until WS connects
                await asyncio.sleep(1.0)
                elapsed = 1.0
                last_count = len(detail_ws_messages)
                while elapsed < 3.0:
                    await asyncio.sleep(0.3)
                    elapsed += 0.3
                    new_count = len(detail_ws_messages)
                    if new_count > last_count:
                        last_count = new_count
                    elif elapsed > 1.8:
                        break

                page.remove_listener("websocket", on_ws)

                if not detail_ws_messages:
                    consecutive_errors += 1
                    continue
                consecutive_errors = 0

                # Parse markets and selections from WS frames
                detail_markets = {}
                detail_selections = {}
                for msg_data in detail_ws_messages:
                    if not isinstance(msg_data, list):
                        continue
                    for msg in msg_data:
                        if not isinstance(msg, dict):
                            continue
                        payload = msg.get('payload', {})
                        for mkt in payload.get('markets', []):
                            mid = mkt.get('id')
                            if mid:
                                detail_markets[mid] = mkt
                        for sel in payload.get('selections', []):
                            sid = sel.get('id')
                            if sid:
                                detail_selections[sid] = sel

                mkt_sel_map: Dict[int, List[dict]] = {}
                for sid, sel in detail_selections.items():
                    mid = sel.get('marketId')
                    if mid:
                        mkt_sel_map.setdefault(mid, []).append(sel)

                # Extract only spread/total markets
                added = []
                for mid, mkt in detail_markets.items():
                    mt = mkt.get('marketType', {})
                    mt_id = mt.get('id', 0)
                    market_type = self._normalize_market_type(mt_id)

                    if market_type not in ('spread', 'total'):
                        continue
                    if mkt.get('isSuspended'):
                        continue

                    sels = mkt_sel_map.get(mid, [])
                    outcomes = []
                    for sel in sels:
                        if sel.get('status') != 'Active':
                            continue
                        outcome = self._build_outcome(
                            sel, market_type,
                            home_team=event.home_team.lower() if event.home_team else '',
                            away_team=event.away_team.lower() if event.away_team else ''
                        )
                        if outcome:
                            outcomes.append(outcome)

                    if outcomes:
                        added.append({'type': market_type, 'outcomes': outcomes})

                if added:
                    # Don't duplicate: check existing market types+points
                    existing = set()
                    for m in event.markets:
                        key = m['type']
                        for o in m.get('outcomes', []):
                            if 'point' in o:
                                key = f"{m['type']}_{o['point']}"
                        existing.add(key)

                    for m in added:
                        key = m['type']
                        for o in m.get('outcomes', []):
                            if 'point' in o:
                                key = f"{m['type']}_{o['point']}"
                        if key not in existing:
                            event.markets.append(m)
                            enriched += 1

            except Exception as e:
                logger.debug(f"[{self.provider_id}] Detail enrichment error for {event.id}: {e}")
                errors += 1
                consecutive_errors += 1

        if errors > 0:
            logger.debug(f"[{self.provider_id}] Detail enrichment had {errors} errors")

        return enriched

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
                    mt_name = mt.get('originalName', mt.get('name', ''))
                    logger.debug(
                        f"[{self.provider_id}] Unknown market typeId={mt_id} "
                        f"name='{mt_name}'")
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
