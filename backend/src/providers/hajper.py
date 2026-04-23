"""
Hajper/Lyllo Retriever - Date-Based WebSocket extraction

ComeOn Group platform with RSocket WebSocket data delivery.
Shared platform with ComeOn — same sport IDs, WS format, and date navigation.

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

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..matching.normalizer import normalize_team_name
from .mixins import RSocketMixin

logger = logging.getLogger(__name__)


class HajperRetriever(BrowserRetriever, RSocketMixin):
    """
    Date-based Hajper/Lyllo retriever.

    Strategy: Navigate to sport page → dismiss cookies → click through date
    buttons → parse RSocket WS messages for events/markets/selections.
    """

    # Sport URL mapping: canonical sport key -> URL path (no /sv/ prefix)
    # Same sport IDs as ComeOn (shared ComeOn Group platform)
    SPORT_URL_MAP = {
        "football": "/sportsbook/sport/1-fotboll",
        "basketball": "/sportsbook/sport/2-basket",
        "american_football": "/sportsbook/sport/3-amerikansk-fotboll",
        "ice_hockey": "/sportsbook/sport/4-ishockey",
        "tennis": "/sportsbook/sport/6-tennis",
        "mma": "/sportsbook/sport/7-mma",
        "esports": "/sportsbook/sport/130-esport",
        "baseball": "/sportsbook/sport/12-baseboll",
        "handball": "/sportsbook/sport/10-handboll",
        "table_tennis": "/sportsbook/sport/26-bordtennis",
    }

    # Market type mapping: marketType.id -> standard type
    # Aligned with Snabbare's complete map (same SBTech/GAN platform)
    MARKET_TYPE_MAP = {
        # 1x2 (3-way)
        1: "1x2",
        # Moneyline (2-way)
        175: "moneyline",  # Winner
        206: "moneyline",  # Winner (incl. overtime)
        376: "moneyline",  # Winner (incl. overtime and penalties) — MMA, handball
        # Total (over/under)
        18: "total",  # Over/Under (generic — football, handball)
        202: "total",  # Total goals
        212: "total",  # Total (incl. overtime) — basketball
        225: "total",  # Total Points O/U — basketball
        1621: "total",  # Total Goals O/U (Regular Time) — ice hockey
        1622: "total",  # Total Goals O/U — ice hockey
        # Spread (handicap)
        16: "spread",  # Asian Handicap (generic — football)
        187: "spread",  # Handicap — basketball
        203: "spread",  # Handicap
        213: "spread",  # Handicap (incl. overtime)
        1619: "spread",  # Puck Line (Regular Time) — ice hockey
        1625: "spread",  # Puck Line — ice hockey
    }

    def __init__(self, config: dict[str, Any], transport: BrowserTransport | None = None):
        super().__init__(config, transport)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")

    def _normalize_market_type(self, market_type_id: int) -> str:
        """Map marketTypeId to standard market type."""
        return self.MARKET_TYPE_MAP.get(market_type_id, "other")

    def _build_outcome(
        self, selection: dict, market_type: str, home_team: str = "", away_team: str = ""
    ) -> dict | None:
        """Build normalized outcome dict from a selection.

        Args:
            home_team/away_team: Optional normalized team names for matching
                when outcomeType is empty (common on event detail pages).
        """
        odds = selection.get("trueOdds", 0.0)
        if not odds or odds <= 1.0:
            return None

        outcome_type = (selection.get("outcomeType") or "").lower()
        name = (selection.get("name") or "").lower()

        if market_type in ("1x2", "moneyline"):
            if outcome_type == "home":
                return {"name": "home", "odds": float(odds)}
            if outcome_type == "away":
                return {"name": "away", "odds": float(odds)}
            if outcome_type in ("tie", "draw"):
                return {"name": "draw", "odds": float(odds)}

        elif market_type == "total":
            points = selection.get("points")
            if points is None or points == 0.0:
                return None
            if outcome_type == "over" or "över" in name or "over" in name:
                return {"name": "over", "odds": float(odds), "point": float(points)}
            if outcome_type == "under" or "under" in name:
                return {"name": "under", "odds": float(odds), "point": float(points)}

        elif market_type == "spread":
            points = selection.get("points")
            if points is None:
                return None
            if outcome_type == "home":
                return {"name": "home", "odds": float(odds), "point": float(points)}
            if outcome_type == "away":
                return {"name": "away", "odds": float(odds), "point": float(points)}
            # Fallback: match selection name against team names (detail pages have empty outcomeType)
            if home_team and home_team in name:
                return {"name": "home", "odds": float(odds), "point": float(points)}
            if away_team and away_team in name:
                return {"name": "away", "odds": float(odds), "point": float(points)}

        return None

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        raise NotImplementedError("HajperRetriever uses extract() directly")

    async def _dismiss_cookie_overlay(self, page) -> None:
        """Dismiss cookie consent overlay.

        ComeOn Group SPAs may navigate after cookie accept, destroying the
        execution context. We wait for the page to settle before continuing.
        """
        # Try OneTrust (used by some ComeOn Group sites)
        try:
            btn = await page.query_selector("#onetrust-accept-btn-handler")
            if btn:
                await btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                await page.wait_for_timeout(1000)
                return
        except Exception:
            pass

        # Try generic accept buttons (Hajper/Lyllo may use different consent)
        for btn_text in ["Acceptera", "Accept", "Godkänn"]:
            try:
                await page.click(f'button:has-text("{btn_text}")', timeout=1500)
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                await page.wait_for_timeout(1000)
                return
            except Exception:
                pass

        # Force-remove overlay elements that intercept clicks
        with contextlib.suppress(Exception):
            await page.evaluate("""() => {
                const filter = document.querySelector('.onetrust-pc-dark-filter');
                if (filter) filter.remove();
                const sdk = document.querySelector('#onetrust-consent-sdk');
                if (sdk) sdk.remove();
            }""")

    async def extract(self, sport: str, limit: int | None = None, **kwargs) -> list[StandardEvent]:
        """Extract events via date-button navigation on the sport page."""
        sport_path = self.SPORT_URL_MAP.get(sport)
        if not sport_path:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        logger.debug(f"[{self.provider_id}] Starting extraction for {sport}")

        ws_messages = []
        all_events_data = {}

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
            sport_url = f"{self.site_url}/sv{sport_path}"
            logger.debug(f"[{self.provider_id}] Loading {sport_url}")
            await page.goto(sport_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)

            # Dismiss cookie overlay — may trigger SPA navigation
            await self._dismiss_cookie_overlay(page)

            # Check if cookie redirect moved us off the sport page
            current_url = page.url
            if sport_path not in current_url:
                logger.debug(f"[{self.provider_id}] Cookie redirect detected, navigating back to {sport_url}")
                await page.goto(sport_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1000)

            # Wait for WS data to arrive (5s to ensure INITIAL_STATE is delivered)
            await page.wait_for_timeout(5000)

            # Step 1: Collect today's events from initial WS messages
            self._collect_ws_events(ws_messages, all_events_data)
            logger.debug(f"[{self.provider_id}] Today: {len(all_events_data)} events from WS")

            # Step 2: Scroll date container to reveal all dates, then click through them
            # The date strip is horizontally scrollable — only ~7-10 buttons visible initially.
            # Scrolling right reveals 14+ additional dates (up to ~21 total).
            await page.evaluate(r"""() => {
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
            }""")
            # Wait for any lazy-loaded date buttons to render after scroll
            await page.wait_for_timeout(500)

            # Now discover ALL date buttons (including newly revealed ones)
            # Collect button text labels instead of indices — indices shift when
            # clicking dates renders new event buttons in the DOM.
            date_labels = await page.evaluate(r"""() => {
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
            }""")

            # Skip date scanning only if no events AND no date buttons
            if not all_events_data and not date_labels:
                logger.debug(f"[{self.provider_id}] No events and no date buttons for {sport}, skipping")
                return []

            if date_labels:
                logger.debug(f"[{self.provider_id}] Found {len(date_labels)} date buttons")
                for label in date_labels:
                    try:
                        ws_before = len(ws_messages)
                        # Find and click button by its exact text content (DOM-safe)
                        clicked = await page.evaluate(
                            """(targetLabel) => {
                            const btns = document.querySelectorAll('button');
                            for (const btn of btns) {
                                if (btn.textContent.trim() === targetLabel) {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }""",
                            label,
                        )

                        if clicked:
                            # Adaptive wait: min 0.5s, poll for WS data, max 4.0s
                            await asyncio.sleep(0.5)
                            elapsed = 0.5
                            while len(ws_messages) == ws_before and elapsed < 4.0:
                                await asyncio.sleep(0.1)
                                elapsed += 0.1
                            self._collect_ws_events(ws_messages, all_events_data)
                    except Exception as e:
                        logger.debug(f"[{self.provider_id}] Date button '{label}' failed: {e}")

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
                    payload = msg.get("payload", {})

                    for market in payload.get("markets", []):
                        mid = market.get("id")
                        if mid:
                            all_markets[mid] = market

                    for sel in payload.get("selections", []):
                        sid = sel.get("id")
                        if sid:
                            all_selections[sid] = sel

            logger.debug(
                f"[{self.provider_id}] WS totals: {len(all_events_data)} events, "
                f"{len(all_markets)} markets, {len(all_selections)} selections"
            )

            # Build event->markets and market->selections mappings
            event_markets_map: dict[int, list[int]] = {}
            for mid, mkt in all_markets.items():
                eid = mkt.get("eventId")
                if eid:
                    event_markets_map.setdefault(eid, []).append(mid)

            market_selections_map: dict[int, list[dict]] = {}
            for sid, sel in all_selections.items():
                mid = sel.get("marketId")
                if mid:
                    market_selections_map.setdefault(mid, []).append(sel)

            # Parse events
            events = []
            for eid, event_data in all_events_data.items():
                event = self._parse_event(event_data, sport, event_markets_map, all_markets, market_selections_map)
                if event:
                    events.append(event)

            logger.debug(f"[{self.provider_id}] Parsed {len(events)} events for {sport}")

            # Pass 2: Enrich with spread/total from event detail pages
            if events:
                event_urls = await page.evaluate(self.JS_DISCOVER_EVENT_URLS)
                if event_urls:
                    enriched_count = await self._enrich_with_detail_markets(page, events, event_urls)
                    logger.debug(
                        f"[{self.provider_id}] Enriched {enriched_count} markets from {len(event_urls)} event URLs"
                    )
                else:
                    logger.debug(f"[{self.provider_id}] No event URLs found in DOM for enrichment")

            return events[:limit] if limit else events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    # --- Pass 2: Event detail page enrichment for spread/total ---

    MAX_DETAIL_EVENTS = 100

    JS_DISCOVER_EVENT_URLS = """() => {
        const links = {};
        document.querySelectorAll('a[href*="/events/"]').forEach(a => {
            const href = a.getAttribute('href');
            const match = href.match(/\\/events\\/(\\d+)/);
            if (match) links[match[1]] = href;
        });
        return links;
    }"""

    async def _enrich_with_detail_markets(self, page, events: list[StandardEvent], event_urls: dict[str, str]) -> int:
        """Navigate to event detail pages to extract spread and total markets.

        Uses the main page sequentially — ComeOn Group SPA only establishes WS
        on the active page, so concurrent tabs don't receive WS data.
        Returns count of events enriched with additional markets.
        """
        event_by_id = {ev.id: ev for ev in events}

        todo = []
        for eid_str, ev in event_by_id.items():
            existing_types = {m["type"] for m in ev.markets}
            if "spread" not in existing_types or "total" not in existing_types:
                url = event_urls.get(eid_str)
                if url:
                    todo.append((ev, url))

        if not todo:
            logger.debug(f"[{self.provider_id}] No events need spread/total enrichment")
            return 0

        if len(todo) > self.MAX_DETAIL_EVENTS:
            logger.info(
                f"[{self.provider_id}] Capping detail enrichment from {len(todo)} to {self.MAX_DETAIL_EVENTS} events"
            )
            todo = todo[: self.MAX_DETAIL_EVENTS]

        logger.info(f"[{self.provider_id}] Enriching {len(todo)} events with spread/total from detail pages")

        enriched = 0
        errors = 0
        consecutive_errors = 0

        for event, href in todo:
            if consecutive_errors > 10:
                logger.warning(
                    f"[{self.provider_id}] Stopping enrichment after {consecutive_errors} consecutive errors"
                )
                break

            try:
                detail_ws_messages: list[list] = []

                def on_ws(ws, msgs=detail_ws_messages):
                    def on_frame(payload, m=msgs):
                        if isinstance(payload, bytes):
                            decoded = self._decode_rsocket_frame(payload)
                            if decoded:
                                m.append(decoded)

                    ws.on("framereceived", on_frame)

                page.on("websocket", on_ws)

                url = f"{self.site_url}{href}" if href.startswith("/") else href
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Detail {event.id}: navigation failed: {e}")
                    page.remove_listener("websocket", on_ws)
                    errors += 1
                    consecutive_errors += 1
                    continue

                # Adaptive wait for WS data — listener must stay active until WS connects
                await asyncio.sleep(1.5)
                elapsed = 1.5
                last_count = len(detail_ws_messages)
                while elapsed < 4.0:
                    await asyncio.sleep(0.3)
                    elapsed += 0.3
                    new_count = len(detail_ws_messages)
                    if new_count > last_count:
                        last_count = new_count
                    elif elapsed > 2.5:
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
                        payload = msg.get("payload", {})
                        for mkt in payload.get("markets", []):
                            mid = mkt.get("id")
                            if mid:
                                detail_markets[mid] = mkt
                        for sel in payload.get("selections", []):
                            sid = sel.get("id")
                            if sid:
                                detail_selections[sid] = sel

                mkt_sel_map: dict[int, list[dict]] = {}
                for sid, sel in detail_selections.items():
                    mid = sel.get("marketId")
                    if mid:
                        mkt_sel_map.setdefault(mid, []).append(sel)

                added = []
                for mid, mkt in detail_markets.items():
                    mt = mkt.get("marketType", {})
                    mt_id = mt.get("id", 0)
                    market_type = self._normalize_market_type(mt_id)

                    if market_type not in ("spread", "total"):
                        continue
                    if mkt.get("isSuspended"):
                        continue

                    sels = mkt_sel_map.get(mid, [])
                    outcomes = []
                    for sel in sels:
                        if sel.get("status") != "Active":
                            continue
                        outcome = self._build_outcome(
                            sel,
                            market_type,
                            home_team=event.home_team.lower() if event.home_team else "",
                            away_team=event.away_team.lower() if event.away_team else "",
                        )
                        if outcome:
                            outcomes.append(outcome)

                    if outcomes:
                        added.append({"type": market_type, "outcomes": outcomes})

                if added:
                    existing = set()
                    for m in event.markets:
                        key = m["type"]
                        for o in m.get("outcomes", []):
                            if "point" in o:
                                key = f"{m['type']}_{o['point']}"
                        existing.add(key)

                    for m in added:
                        key = m["type"]
                        for o in m.get("outcomes", []):
                            if "point" in o:
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
                payload = msg.get("payload", {})
                for event in payload.get("events", []):
                    eid = event.get("id")
                    if eid and eid not in all_events_data:
                        all_events_data[eid] = event

    def _parse_event(
        self,
        event_data: dict,
        sport: str,
        event_markets_map: dict[int, list[int]],
        all_markets: dict[int, dict],
        market_selections_map: dict[int, list[dict]],
    ) -> StandardEvent | None:
        """Parse a single event from WS data."""
        try:
            eid = event_data.get("id")
            if not eid:
                return None

            # Extract teams from primaryParticipants
            home_team = None
            away_team = None

            primary = event_data.get("primaryParticipants", {})
            if isinstance(primary, dict):
                for _pid, p in primary.items():
                    role = p.get("venueRole", "")
                    if role == "Home":
                        home_team = p.get("name")
                    elif role == "Away":
                        away_team = p.get("name")

            # Fallback: parse from eventName
            if not home_team or not away_team:
                event_name = event_data.get("eventName", "")
                if " - " in event_name:
                    parts = event_name.split(" - ", 1)
                    home_team = home_team or parts[0].strip()
                    away_team = away_team or parts[1].strip()

            if not home_team or not away_team:
                return None

            home_team = normalize_team_name(home_team)
            away_team = normalize_team_name(away_team)

            # Start time
            start_time_str = event_data.get("startingOn") or event_data.get("startTime")
            start_time = None
            if start_time_str:
                with contextlib.suppress(ValueError):
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))

            league = event_data.get("leagueName", "Unknown")

            # Build markets
            markets = []
            market_ids = event_markets_map.get(eid, [])

            for mid in market_ids:
                mkt = all_markets.get(mid)
                if not mkt:
                    continue

                mt = mkt.get("marketType", {})
                mt_id = mt.get("id", 0)
                market_type = self._normalize_market_type(mt_id)

                if market_type == "other":
                    mt_name = mt.get("originalName", mt.get("name", ""))
                    logger.debug(f"[{self.provider_id}] Unknown market typeId={mt_id} name='{mt_name}'")
                    continue

                if mkt.get("isSuspended"):
                    continue

                selections = market_selections_map.get(mid, [])
                outcomes = []
                for sel in selections:
                    if sel.get("status") != "Active":
                        continue
                    outcome = self._build_outcome(sel, market_type)
                    if outcome:
                        outcomes.append(outcome)

                if outcomes:
                    markets.append({"type": market_type, "outcomes": outcomes})

            return StandardEvent(
                id=str(eid),
                name=f"{home_team} vs {away_team}",
                provider=self.provider_id,
                sport=sport,
                league=league,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                markets=markets,
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")
            return None
