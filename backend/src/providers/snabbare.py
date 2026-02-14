"""
Snabbare Retriever - WebSocket-based sportsbook extraction

Snabbare (Sportradar MTS platform / Komigen) uses WebSocket for real-time event data.
Binary frames contain JSON payloads with events, markets, and selections arrays.

Strategy:
1. page.goto() to each sport page (React Router SPA)
2. DOM sidebar contains league <a> links (React Router <Link> components)
3. Click each link via el.click() → React Router navigates → WS delivers league data
4. history.back() → back to sport page → click next league

CRITICAL: Only DOM link clicks trigger WS data delivery. pushState/popstate
does NOT work — React Router updates the route but component lifecycle hooks
don't fire → no WS subscription → no data.

Event data (odds/markets) is exclusively delivered via WebSocket.
REST API (/sportsbook-api/api/) only provides metadata (sports, leagues, config).

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

    # Market type IDs we want (discovered via diag_snabbare_markets.py)
    MARKET_TYPE_MAP = {
        # 1x2 (3-way)
        1: "1x2",
        # Moneyline (2-way)
        175: "moneyline",   # Winner
        206: "moneyline",   # Winner (incl. overtime)
        376: "moneyline",   # Winner (incl. overtime and penalties)
        # Total (over/under)
        212: "total",        # Total (incl. overtime) — basketball
        1621: "total",       # Total Goals Over/Under (Regular Time) — ice hockey
        1622: "total",       # Total Goals Over/Under — ice hockey
        # Spread (handicap)
        1619: "spread",      # Puck Line (Regular Time) — ice hockey
        1625: "spread",      # Puck Line — ice hockey
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.snabbare.com")
        self.api_base = "https://www.snabbare.com/sportsbook-api/api"
        self.default_params = {
            "franchiseCode": "SWEDEN_SNABBARE",
            "locale": "sv",
        }

    async def extract(self, sport: str, limit: int = 1000, **kwargs) -> List[StandardEvent]:
        """
        Extract events for a given sport via SPA league-link clicking.

        Each sport call navigates to that sport's page, clicks league links
        via React Router (SPA, no page reload), and collects WS data.
        Fits within the orchestrator's per-sport timeout (~120s).
        """
        # Health check — return quickly without full extraction
        if limit <= 1:
            return await self._quick_health_check()

        events = await self._extract_sport(sport)
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

    LEAGUE_SETTLE_TIME = 0.05  # min seconds to wait for WS data after SPA link click
    MAX_LEAGUE_SETTLE_TIME = 0.4  # max seconds to wait (if WS data still arriving)

    async def _extract_sport(self, sport: str) -> List[StandardEvent]:
        """
        Extract events for one sport by navigating to the sport page and
        clicking each league link via React Router (SPA, no page reload).

        Strategy:
        1. page.goto() to the sport page (1 full navigation per sport)
        2. DOM sidebar contains league <a> links (React Router <Link> components)
        3. Click each link via JS el.click() → React Router navigates → same WS delivers data
        4. history.back() → back to sport page → click next league

        IMPORTANT: Only DOM link clicks trigger WS data delivery. pushState/popstate
        does NOT trigger React component lifecycle → no WS subscription → no data.

        Each sport fits within the orchestrator's per-sport timeout (~120s).
        """
        sport_id = self.SPORT_ID_BY_NAME.get(sport)
        if sport_id is None:
            return []

        sport_info = self.SPORT_MAP.get(sport_id)
        if not sport_info:
            return []
        canonical, slug = sport_info

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] Requires BrowserTransport")
                return []

            await self.transport._ensure_browser()
            page = self.transport.page

            # WS message store for this sport
            ws_messages: List = []

            # Setup WS interception (handles binary RSocket + text JSON)
            self._setup_snabbare_ws(page, ws_messages)

            # Initial session setup (cookie consent, etc.) — only once
            if not self._session_ready:
                await page.goto(
                    f"{self.site_url}/sv/sportsbook",
                    wait_until="load", timeout=30000
                )
                await self._handle_cookie_consent(page)
                await self._remove_overlays(page)
                await asyncio.sleep(1)
                self._session_ready = True

            # Navigate to sport page (retry once on connection error)
            sport_url = f"{self.site_url}/sv/sportsbook/sport/{sport_id}-{slug}"
            try:
                await page.goto(sport_url, wait_until="load", timeout=30000)
            except Exception as nav_err:
                if "Connection closed" in str(nav_err) or "closed" in str(nav_err).lower():
                    logger.warning(
                        f"[{self.provider_id}] Browser connection lost for {sport}, reconnecting..."
                    )
                    page = await self._reconnect_browser()
                    await page.goto(sport_url, wait_until="load", timeout=30000)
                else:
                    raise
            # Re-register WS after goto (goto destroys page context)
            self._setup_snabbare_ws(page, ws_messages)
            await self._remove_overlays(page)

            # Wait for league links to appear in the sidebar (React renders async)
            try:
                await page.wait_for_selector(
                    'a[href*="/leagues/"]', timeout=8000
                )
            except Exception:
                logger.debug(f"[{self.provider_id}] {canonical}: no league links after 8s wait")
            await asyncio.sleep(0.3)

            # Discover league links from DOM sidebar
            # These are React Router <Link> components — clicking them triggers
            # SPA navigation and WS subscription for that league's events.
            # IMPORTANT: Filter OUT event-level links (/events/) — we only want
            # league-level links like /leagues/123-premier-league (not /events/456-team-a-team-b)
            league_links = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/leagues/"]');
                return Array.from(links)
                    .filter(l => !l.getAttribute('href').includes('/events/'))
                    .map(l => ({
                        href: l.getAttribute('href'),
                        text: l.textContent.trim().substring(0, 60)
                    }));
            }""")
            league_links = league_links or []

            # Dedup by league ID (sidebar has duplicate links: e.g.
            # "898-fa-cup" AND "898-england-fa-cup" are the same league)
            seen_league_ids = set()
            unique_links = []
            for link in league_links:
                href = link.get("href", "")
                if not href:
                    continue
                lid = self._extract_league_id(href)
                if lid and lid not in seen_league_ids:
                    seen_league_ids.add(lid)
                    unique_links.append(link)
                elif not lid and href not in seen_league_ids:
                    # Fallback: dedup by full href if no ID extractable
                    seen_league_ids.add(href)
                    unique_links.append(link)
            league_links = unique_links

            if not league_links:
                logger.debug(f"[{self.provider_id}] {canonical}: no league links in DOM")
                return []

            # Cap leagues per sport to stay within sport_timeout (~240s)
            # Top leagues appear first in DOM (ordered by popularity/event count)
            # 40 leagues × ~3s/league = ~120s, well within timeout
            MAX_LEAGUES_PER_SPORT = 40
            if len(league_links) > MAX_LEAGUES_PER_SPORT:
                logger.info(
                    f"[{self.provider_id}] {canonical}: capping {len(league_links)} leagues "
                    f"to top {MAX_LEAGUES_PER_SPORT}"
                )
                league_links = league_links[:MAX_LEAGUES_PER_SPORT]

            logger.info(
                f"[{self.provider_id}] {canonical}: {len(league_links)} league links to process"
            )

            # Click each league link via SPA router
            leagues_processed = 0
            leagues_with_data = 0
            errors = 0

            for j, link in enumerate(league_links):
                ws_before = len(ws_messages)
                try:
                    href = link["href"]
                    # Click the DOM link — React Router intercepts → component mount → WS subscription
                    clicked = await page.evaluate(
                        f"""() => {{
                            const el = document.querySelector('a[href="{href}"]');
                            if (el) {{ el.click(); return true; }}
                            return false;
                        }}"""
                    )
                    if not clicked:
                        errors += 1
                        continue

                    # Adaptive wait: wait minimum time, then check for WS data
                    await asyncio.sleep(self.LEAGUE_SETTLE_TIME)
                    elapsed = self.LEAGUE_SETTLE_TIME
                    # If no data yet, wait a bit more (up to MAX)
                    while len(ws_messages) == ws_before and elapsed < self.MAX_LEAGUE_SETTLE_TIME:
                        await asyncio.sleep(0.05)
                        elapsed += 0.05

                    leagues_processed += 1
                    ws_delta = len(ws_messages) - ws_before
                    if ws_delta > 0:
                        leagues_with_data += 1

                    # Navigate back to sport page for next league
                    await page.evaluate("window.history.back()")
                    await asyncio.sleep(0.05)

                except Exception as e:
                    err_str = str(e)
                    logger.debug(f"[{self.provider_id}] {link['text']} error: {err_str}")
                    errors += 1
                    # Browser connection lost — reconnect and retry remaining leagues
                    if "Connection closed" in err_str or "closed" in err_str.lower() or "Target crashed" in err_str:
                        logger.warning(
                            f"[{self.provider_id}] {canonical}: browser lost at league {j+1}/{len(league_links)}, reconnecting..."
                        )
                        try:
                            page = await self._reconnect_browser()
                            self._setup_snabbare_ws(page, ws_messages)
                            await page.goto(sport_url, wait_until="load", timeout=30000)
                            self._setup_snabbare_ws(page, ws_messages)
                            await self._remove_overlays(page)
                            await asyncio.sleep(1)
                            continue  # Retry remaining leagues
                        except Exception as reconn_err:
                            logger.error(f"[{self.provider_id}] Reconnection failed: {reconn_err}")
                            break
                    # Non-connection error — try to recover to sport page
                    try:
                        await page.goto(sport_url, wait_until="load", timeout=15000)
                        self._setup_snabbare_ws(page, ws_messages)
                        await asyncio.sleep(1)
                    except Exception:
                        break

            # Parse WS messages into events
            events_by_sport = self._parse_ws_data(ws_messages)
            events = events_by_sport.get(canonical, [])

            events_with_markets = sum(1 for e in events if e.markets)
            logger.info(
                f"[{self.provider_id}] {canonical}: {leagues_processed}/{len(league_links)} leagues, "
                f"{leagues_with_data} with WS data, "
                f"{len(ws_messages)} msgs -> {len(events)} events "
                f"({events_with_markets} with markets, {errors} errors)"
            )

            return events

        except Exception as e:
            logger.error(
                f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True
            )
            return []

    @staticmethod
    def _extract_league_id(href: str) -> str:
        """Extract league ID from a URL like /sv/sportsbook/sport/1-fotboll/leagues/123-premier-league."""
        import re as _re
        match = _re.search(r'/leagues/(\d+)', href)
        return match.group(1) if match else ""

    async def _remove_overlays(self, page) -> None:
        """Remove OneTrust cookie overlay and other blocking elements."""
        try:
            await page.evaluate("""() => {
                document.querySelectorAll(
                    '.onetrust-pc-dark-filter, #onetrust-consent-sdk, .ot-fade-in'
                ).forEach(e => e.remove());
            }""")
        except Exception:
            pass

    def _parse_ws_data(self, ws_messages: List) -> Dict[str, List[StandardEvent]]:
        """
        Parse WebSocket messages into StandardEvents grouped by sport.

        WS messages from RSocketMixin are already decoded to JSON lists.
        Each message is a list of dicts with 'payload' containing events/markets/selections.
        """
        # Collect all raw data (dedup by ID to avoid duplicates from repeated WS messages)
        all_events: Dict[str, Dict] = {}
        all_markets_by_id: Dict[str, Dict] = {}  # market_id -> market dict
        all_selections_by_id: Dict[str, Dict] = {}  # selection unique key -> selection dict

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
                    mid = str(mkt.get("id", ""))
                    if mid:
                        all_markets_by_id[mid] = mkt

                for sel in payload.get("selections", []):
                    # Dedup selections by (marketId, outcomeType) or (marketId, name)
                    mid = str(sel.get("marketId", ""))
                    sel_key = f"{mid}:{sel.get('outcomeType', '')}:{sel.get('name', '')}"
                    if mid:
                        all_selections_by_id[sel_key] = sel

        # Rebuild grouped structures from deduped data
        all_markets: Dict[str, List[Dict]] = {}
        for mkt in all_markets_by_id.values():
            eid = str(mkt.get("eventId", ""))
            if eid:
                all_markets.setdefault(eid, []).append(mkt)

        all_selections: Dict[str, List[Dict]] = {}
        for sel in all_selections_by_id.values():
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
                if not market_type:
                    logger.debug(
                        f"[{self.provider_id}] Unknown market typeId={mt_id} "
                        f"name='{mt_info.get('originalName', mt_info.get('name', ''))}'")

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

    def _setup_snabbare_ws(self, page, messages: list) -> None:
        """Setup WS interception that handles both binary and text frames.

        Snabbare may send data as binary (RSocket) or text (JSON) frames.
        The standard RSocketMixin only handles binary — this also captures text.
        """
        ws_count = [0]

        def on_websocket(ws):
            ws_count[0] += 1
            ws_url = ws.url if hasattr(ws, 'url') else 'unknown'
            logger.info(
                f"[{self.provider_id}] WS #{ws_count[0]} connected: "
                f"{ws_url[:80]}"
            )

            def on_frame_received(payload):
                if isinstance(payload, bytes):
                    decoded = self._decode_rsocket_frame(payload)
                    if decoded:
                        messages.append(decoded)
                elif isinstance(payload, str):
                    # Text frame — try direct JSON parse
                    try:
                        if payload.startswith('[{') or payload.startswith('{"'):
                            data = json.loads(payload)
                            if isinstance(data, list):
                                messages.append(data)
                            elif isinstance(data, dict):
                                messages.append([data])
                    except (json.JSONDecodeError, ValueError):
                        pass

            ws.on("framereceived", on_frame_received)

        page.on("websocket", on_websocket)

    async def _reconnect_browser(self):
        """Kill dead browser and start a fresh one after a crash.

        _ensure_browser() checks `self.page` and returns early if set,
        so we must clear all references first to force a full restart.
        """
        # Clear dead references so _ensure_browser() actually restarts
        try:
            if self.transport.browser:
                await self.transport.browser.close()
        except Exception:
            pass
        self.transport.page = None
        self.transport.context = None
        self.transport.browser = None
        if self.transport.playwright:
            try:
                await self.transport.playwright.stop()
            except Exception:
                pass
            self.transport.playwright = None

        # Start fresh browser
        await self.transport._ensure_browser()
        page = self.transport.page
        self._session_ready = False
        await page.goto(f"{self.site_url}/sv/sportsbook", wait_until="load", timeout=30000)
        await self._handle_cookie_consent(page)
        await self._remove_overlays(page)
        self._session_ready = True
        return page

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
