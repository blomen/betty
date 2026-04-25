"""
Gecko V2 Retriever - Events Table API Approach

Betsson Group sites (betsson, betsafe, nordicbet, spelklubben) use the OBG
sportsbook platform (Gecko V2). The events-table/v2 API returns paginated
event listings with markets, selections, and odds.

Flow:
1. Load sport page with Playwright to establish session
2. Capture required custom headers (x-sb-*, brandid, sessiontoken)
3. Call events-table/v2 API directly with context.request + pagination
4. Parse events/markets/selections from JSON response

API endpoint:
- GET /api/sb/v1/widgets/events-table/v2
- Required headers: brandid, sessiontoken, x-sb-* (16+ custom headers)
- Params: categoryIds, phase, marketTemplateIds, priceFormats, page
- Returns: data.events[], data.markets[], data.selections[], totalPages, page

Market template IDs:
- MW3W = 3-way 1x2 (football, ice hockey)
- MW2W = 2-way moneyline (tennis, basketball, etc.)
- MTG2W / TGOU = total (over/under)
- M3WHCP / M2WHCP / 2WHCPROLMID = spread/handicap
- MWOU = over/under total

Selection template IDs:
- HOME, AWAY, DRAW
- OVER, UNDER
- HANDICAPHOME, HANDICAPDRAW, HANDICAPAWAY
"""

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..core.exceptions import RetryableError
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class GeckoV2Retriever(BrowserRetriever):
    """
    Retriever for Betsson Group sites using events-table/v2 API.

    Strategy: Load page to establish session headers, then call
    events-table/v2 API directly with pagination.
    """

    # Sport slug for URL navigation (used for session init)
    SPORT_SLUGS: dict[str, str] = {
        "football": "fotboll",
        "ice_hockey": "ishockey",
        "handball": "handboll",
        "basketball": "basket",
        "rugby": "rugby",
        "volleyball": "volleyboll",
        "american_football": "amerikansk-fotboll",
        "tennis": "tennis",
        "curling": "curling",
        "cricket": "cricket",
        "boxing": "boxning",
        "darts": "dart",
        "esports": "esports",
        "mma": "mma",
        "baseball": "baseboll",
        "golf": "golf",
        "table_tennis": "bordtennis",
    }

    # OBG category IDs for each sport
    # Verified 2026-02-09 via events-table/v2 category scan (gecko_category_scan.py)
    SPORT_CATEGORY_IDS: dict[str, int] = {
        "football": 1,
        "ice_hockey": 2,
        "handball": 3,
        "basketball": 4,
        "rugby": 7,  # Rugby League (ID 8 = Rugby Union)
        "volleyball": 9,
        "american_football": 10,
        "tennis": 11,
        "curling": 20,
        "cricket": 26,
        "boxing": 30,
        "darts": 34,
        "mma": 53,
    }

    # Market template ID → our standard market type
    MARKET_TEMPLATE_MAP: dict[str, str] = {
        # 1x2 (3-way)
        "MW3W": "1x2",
        "ESNRTWINNER3W": "1x2",
        # Moneyline (2-way)
        "MW2W": "moneyline",
        "ESNMOWINNER2W": "moneyline",
        "ESMW2W": "moneyline",  # Esports moneyline
        # Total (over/under)
        "MTG2W": "total",
        "MTG2W25": "total",
        "TGOU": "total",
        "TGOUOT": "total",  # Ice hockey total (incl. overtime)
        "MWOU": "total",
        "MROU": "total",
        "ESNMOTOTAL": "total",
        "OUALT": "total",
        "PTSOUROLMID": "total",
        "MTG2WIO": "total",
        "MTG2WP": "total",  # Tennis total (games/sets)
        "MTP": "total",  # Volleyball total (points)
        # Spread (handicap)
        # M3WHCP is 3-way European handicap (home/draw/away on integer lines).
        # Excluded: draw absorbs probability, inflating home/away odds vs
        # Pinnacle's 2-way Asian handicap. Produces false spread edges.
        "M2WHCP": "spread",
        "MW2WHCP": "spread",
        "M2WHCPIO": "spread",
        "2WHCPROLMID": "spread",
        "MWHCPALT": "spread",
        "MHCPNOT": "spread",  # Ice hockey handicap (not overtime)
        "MAHCP": "spread",
        "AHC": "spread",
        "ESNMOHANDICAP": "spread",
        "MSH": "spread",  # Volleyball set handicap
        "ESHMTHANDICAP": "spread",  # Esports handicap (maps)
    }

    # Selection template ID → our standard outcome name
    SELECTION_TEMPLATE_MAP: dict[str, str] = {
        "HOME": "home",
        "AWAY": "away",
        "DRAW": "draw",
        "OVER": "over",
        "UNDER": "under",
        "HANDICAPHOME": "home",
        "HANDICAPAWAY": "away",
        "HANDICAPDRAW": "draw",
    }

    # Keywords in market name that indicate half-time / period-specific markets
    # These should be skipped — we only want full-match markets.
    _PERIOD_KEYWORDS = (
        "halvtid",
        "half time",
        "half-time",
        "halvlek",
        "1st half",
        "2nd half",
        "first half",
        "second half",
        "halvtid/fulltid",
        "ht/ft",
        "quarter",
        "period",
        "1st set",
        "2nd set",
        "3rd set",
    )

    def __init__(self, config: dict[str, Any], transport: BrowserTransport | None = None):
        super().__init__(config, transport)

        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', 'betsson.com')}")
        self.site_url: str = raw_site_url.rstrip("/")
        # Path to navigate for session init (must trigger OBG API calls)
        self._init_path: str = config.get("init_path") or "/sv/odds"

        # Cached custom headers from browser session
        self._api_headers: dict[str, str] | None = None
        # API base URL (may differ from site_url, e.g., bethard uses d-cf.bethardplayground.net)
        self._api_base: str | None = None
        self._last_run_id: str | None = None
        # Fail-fast: if session init fails once per run, skip remaining sports
        self._session_init_failed: bool = False
        # Serialize session init across concurrent sports. The orchestrator runs
        # up to 3 sports in parallel per provider; without this lock all three
        # coroutines race into page.route() + page.goto() on the same Page,
        # which Playwright doesn't support cleanly. Under CPU load (load avg
        # 5-7 on a 4-core box during peak extraction) the racing nav calls
        # deadlock past the 120s outer wait_for, breaking the provider for
        # an entire cycle.
        self._session_init_lock = asyncio.Lock()

    async def _ensure_session(self) -> bool:
        """
        Load the site and capture required API headers.
        Returns True if session is established.
        """
        if self._api_headers:
            return True

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] GeckoV2Retriever requires BrowserTransport")
                return False

            await self.transport._ensure_browser()
            page = self.transport.page

            # Capture headers and API base URL from the first API request
            captured = {}
            api_base_holder: list[str] = []

            async def capture_route(route, request):
                url = request.url
                if "/api/sb/" in url and not captured:
                    captured.update(dict(request.headers))
                    idx = url.find("/api/sb/")
                    api_base_holder.append(url[:idx])
                with contextlib.suppress(Exception):
                    await route.continue_()

            await page.route("**/api/sb/**", capture_route)

            # Navigate to site
            url = f"{self.site_url}{self._init_path}"
            logger.debug(f"[{self.provider_id}] Loading {url} for session init")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Handle cookie consent
            await self._handle_cookie_consent(page)

            # Wait for API headers to be captured (up to 30s after page load)
            # Increased from 15s — under concurrent browser load the SPA can be slow
            for _ in range(60):
                if captured:
                    break
                await asyncio.sleep(0.5)

            # Fallback: navigate to a sport page to force API calls
            if not captured:
                logger.warning(f"[{self.provider_id}] No headers after init page, trying sport page fallback...")
                try:
                    await page.goto(
                        f"{self.site_url}{self._init_path}/fotboll", wait_until="domcontentloaded", timeout=30000
                    )
                    for _ in range(40):
                        if captured:
                            break
                        await asyncio.sleep(0.5)
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Sport page fallback failed: {e}")

            await page.unroute("**/api/sb/**")

            if not captured:
                logger.error(f"[{self.provider_id}] No API headers captured after page load + fallback")
                return False

            # Extract only the custom headers needed for API calls
            headers = {}
            for k, v in captured.items():
                kl = k.lower()
                if kl.startswith(("x-sb-", "x-obg-")) or kl in (
                    "brandid",
                    "sessiontoken",
                    "marketcode",
                    "correlationid",
                ):
                    headers[k] = v
            headers["accept"] = "application/json"
            headers["content-type"] = "application/json"

            self._api_headers = headers
            self._api_base = api_base_holder[0] if api_base_holder else self.site_url
            self._session_ready = True
            logger.debug(
                f"[{self.provider_id}] Session established with {len(headers)} headers, API base: {self._api_base}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.provider_id}] Session init failed: {e}")
            return False

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        for selector in [
            'button:has-text("Acceptera")',
            'button:has-text("Accept")',
            "#accept-cookies",
        ]:
            try:
                await page.click(selector, timeout=3000)
                logger.debug(f"[{self.provider_id}] Clicked cookie consent")
                await asyncio.sleep(1)
                return
            except Exception:
                continue

    async def _lookup_category_id(self, sport: str) -> int | None:
        """
        Dynamically look up category ID via category-by-slug API.
        Falls back to slug lookup when a sport isn't in the hardcoded map.
        """
        slug = self.SPORT_SLUGS.get(sport)
        if not slug:
            return None

        try:
            page = self.transport.page
            context = page.context
            url = f"{self._api_base}/api/sb/v1/widgets/category-by-slug/sv/{slug}"
            resp = await asyncio.wait_for(
                context.request.get(url, headers=self._api_headers),
                timeout=15,
            )
            if resp.ok:
                data = (await resp.json()).get("data", {})
                cat_id = data.get("id")
                if cat_id:
                    logger.debug(f"[{self.provider_id}] Discovered category ID for {sport}: {cat_id}")
                    # Cache for future use in this session
                    self.SPORT_CATEGORY_IDS[sport] = cat_id
                    return cat_id
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Category slug lookup failed for {sport}: {e}")

        return None

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> list[StandardEvent]:
        """
        Extract events by calling events-table/v2 API with pagination.
        """
        # Invalidate cached session on new extraction run
        run_id = kwargs.get("run_id")
        if run_id and run_id != self._last_run_id:
            self._api_headers = None
            self._api_base = None
            self._session_ready = False
            self._session_init_failed = False  # Reset fail-fast flag for new run
            logger.debug(f"[{self.provider_id}] New run {run_id[:12]}... — clearing cached session")
        self._last_run_id = run_id

        # Fail-fast: if session init already failed this run, skip immediately
        if self._session_init_failed:
            raise RetryableError(
                "Session init already failed this run — skipping remaining sports",
                provider_id=self.provider_id,
            )

        # If the previous run's close() invalidated the browser (transport.close()
        # sets page = None), the cached _api_headers no longer correspond to a
        # live page. Clear them so the lock-protected init below recreates the
        # page AND re-captures fresh headers from the new browser.
        if self._api_headers is not None and getattr(self.transport, "page", None) is None:
            logger.debug(f"[{self.provider_id}] transport.page gone — invalidating cached api_headers")
            self._api_headers = None
            self._api_base = None
            self._session_ready = False

        # Serialize across the 3 concurrent sport coroutines so only one of
        # them actually drives page.route + page.goto. The others wait, then
        # see _api_headers is set and skip the init.
        async with self._session_init_lock:
            if self._api_headers:
                # Another sport coroutine completed init while we waited.
                pass
            else:
                # Retry session init up to 3 times (header capture is timing-sensitive on betsson).
                # Outer timeout is 180s — _ensure_session worst case is page.goto (60s) +
                # header wait (30s) + sport-page fallback navigation (30s) + 30s of header
                # wait on fallback = 150s. Under production proxy load (5+ concurrent
                # browsers competing for Bahnhof bandwidth), 120s was tight enough that
                # gecko brands timed out every attempt; 180s gives headroom without
                # changing sport-level scheduling assumptions.
                for attempt in range(3):
                    try:
                        session_ok = await asyncio.wait_for(self._ensure_session(), timeout=180)
                        if session_ok:
                            break
                        # First attempt failed — close browser and retry with fresh page
                        logger.warning(f"[{self.provider_id}] Session init attempt {attempt + 1} failed, retrying...")
                        self._api_headers = None
                        self._api_base = None
                        self._session_ready = False
                        await self.transport.close()
                        await self.transport._ensure_browser()
                    except asyncio.TimeoutError:
                        logger.warning(f"[{self.provider_id}] Session init attempt {attempt + 1} timed out")
                        self._api_headers = None
                        self._api_base = None
                        self._session_ready = False
                        if attempt < 2:
                            await self.transport.close()
                            await self.transport._ensure_browser()
                            continue
                        self._session_init_failed = True
                        raise RetryableError(
                            "Session init timed out after 2 attempts",
                            provider_id=self.provider_id,
                        )

                if not self._api_headers:
                    self._session_init_failed = True
                    raise RetryableError(
                        "Session init failed — no API headers captured",
                        provider_id=self.provider_id,
                    )

        # Get category ID from hardcoded map or dynamic slug lookup
        category_id = self.SPORT_CATEGORY_IDS.get(sport)
        if category_id is None:
            category_id = await self._lookup_category_id(sport)
            if category_id is None:
                slug = self.SPORT_SLUGS.get(sport)
                if slug:
                    logger.warning(f"[{self.provider_id}] Could not find category ID for '{sport}' (slug: {slug})")
                else:
                    logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported (no slug mapping)")
                return []

        try:
            page = self.transport.page
            context = page.context
            base_url = f"{self._api_base}/api/sb/v1/widgets/events-table/v2"

            # Request all main market types (must include sport-specific variants)
            market_templates = (
                "MW3W,MW2W,MTG2W,MTG2W25,TGOU,TGOUOT,MWOU,MROU,"
                "M3WHCP,M2WHCP,MW2WHCP,M2WHCPIO,2WHCPROLMID,MWHCPALT,MHCPNOT,"
                "ESNRTWINNER3W,ESNMOWINNER2W,ESMW2W,ESNMOTOTAL,ESNMOHANDICAP,"
                "OUALT,PTSOUROLMID,MTG2WIO,MTG2WP,MTP,MSH,ESHMTHANDICAP,MAHCP,AHC"
            )
            base_params = (
                f"categoryIds={category_id}&phase=4"
                f"&marketTemplateIds={market_templates}"
                f"&priceFormats=1&timezoneOffsetMinutes=60"
            )

            all_events = []
            seen_ids: set[str] = set()

            # Fetch page 1 to discover total pages
            url_p1 = f"{base_url}?{base_params}&pageNumber=1"
            try:
                resp = await asyncio.wait_for(
                    context.request.get(url_p1, headers=self._api_headers),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.error(f"[{self.provider_id}] API page 1 request timed out after 30s")
                return []
            except Exception as e:
                logger.error(f"[{self.provider_id}] API request failed: {e}")
                return []

            if not resp.ok:
                if resp.status == 400:
                    logger.warning(f"[{self.provider_id}] 400 error, re-initializing session")
                    self._api_headers = None
                    self._api_base = None
                    self._session_ready = False
                    if await asyncio.wait_for(self._ensure_session(), timeout=45):
                        try:
                            resp = await asyncio.wait_for(
                                context.request.get(url_p1, headers=self._api_headers),
                                timeout=30,
                            )
                        except asyncio.TimeoutError:
                            logger.error(f"[{self.provider_id}] Retry API request timed out after 30s")
                            return []
                        except Exception as e:
                            logger.error(f"[{self.provider_id}] Retry failed: {e}")
                            return []
                        if not resp.ok:
                            logger.error(f"[{self.provider_id}] API still returning {resp.status}")
                            return []
                    else:
                        return []
                else:
                    logger.error(f"[{self.provider_id}] API returned {resp.status}")
                    return []

            data = (await resp.json()).get("data", {})
            total_pages = data.get("totalPages", 1)
            total_items = data.get("totalItemCount", 0)
            logger.debug(f"[{self.provider_id}] {sport}: {total_items} events, {total_pages} pages")

            # Parse page 1
            events_raw = data.get("events", [])
            markets_raw = data.get("markets", [])
            selections_raw = data.get("selections", [])
            if events_raw:
                all_events.extend(self._parse_page(events_raw, markets_raw, selections_raw, sport, seen_ids))

            # Fetch remaining pages in parallel (cap at limit)
            if total_pages > 1 and len(all_events) < limit:
                max_page = min(total_pages, 1 + (limit // max(len(events_raw), 1)))

                async def _fetch_page(pg: int) -> dict | None:
                    url = f"{base_url}?{base_params}&pageNumber={pg}"
                    try:
                        r = await asyncio.wait_for(
                            context.request.get(url, headers=self._api_headers),
                            timeout=30,
                        )
                        if r.ok:
                            return (await r.json()).get("data", {})
                    except asyncio.TimeoutError:
                        logger.debug(f"[{self.provider_id}] Page {pg} timed out after 30s")
                    except Exception as exc:
                        logger.debug(f"[{self.provider_id}] Page {pg} failed: {exc}")
                    return None

                page_results = await asyncio.gather(
                    *[_fetch_page(pg) for pg in range(2, max_page + 1)],
                    return_exceptions=True,
                )

                for page_data in page_results:
                    if page_data is None or isinstance(page_data, Exception):
                        continue
                    ev = page_data.get("events", [])
                    mk = page_data.get("markets", [])
                    sl = page_data.get("selections", [])
                    if ev:
                        all_events.extend(self._parse_page(ev, mk, sl, sport, seen_ids))

            logger.debug(f"[{self.provider_id}] {sport}: {len(all_events)} events parsed")
            return all_events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    def _parse_page(
        self,
        events_raw: list[dict],
        markets_raw: list[dict],
        selections_raw: list[dict],
        sport: str,
        seen_ids: set[str],
    ) -> list[StandardEvent]:
        """Parse a page of events-table API data."""
        # Build lookup maps
        # markets by eventId
        markets_by_event: dict[str, list[dict]] = {}
        for m in markets_raw:
            eid = m.get("eventId", "")
            markets_by_event.setdefault(eid, []).append(m)

        # selections by marketId
        selections_by_market: dict[str, list[dict]] = {}
        for s in selections_raw:
            mid = s.get("marketId", "")
            selections_by_market.setdefault(mid, []).append(s)

        events = []
        for event_raw in events_raw:
            try:
                event = self._parse_event(event_raw, markets_by_event, selections_by_market, sport)
                if event and event.id not in seen_ids:
                    seen_ids.add(event.id)
                    events.append(event)
            except Exception as e:
                logger.debug(f"[{self.provider_id}] Error parsing event: {e}")

        return events

    def _parse_event(
        self,
        event_raw: dict,
        markets_by_event: dict[str, list[dict]],
        selections_by_market: dict[str, list[dict]],
        sport: str,
    ) -> StandardEvent | None:
        """Parse a single event from events-table API."""
        event_id = event_raw.get("id", "")
        if not event_id:
            return None

        # Skip non-fixture events (outrights, etc.)
        event_type = event_raw.get("eventType", "")
        if event_type == "Outright":
            return None

        # Skip live events
        phase = event_raw.get("phase", "")
        if phase != "Prematch":
            return None

        # Extract participants (home/away)
        participants = event_raw.get("participants", [])
        if len(participants) < 2:
            return None

        # Sort by side (1=home, 2=away)
        participants.sort(key=lambda p: p.get("side", 0))
        home_raw = participants[0].get("label", "")
        away_raw = participants[1].get("label", "")

        if not home_raw or not away_raw:
            return None

        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)

        # Parse start time
        start_time = None
        start_date_str = event_raw.get("startDate")
        if start_date_str:
            with contextlib.suppress(Exception):
                start_time = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))

        # League/competition
        league = event_raw.get("competitionName", "Unknown")

        # Parse markets
        event_markets = markets_by_event.get(event_id, [])
        markets = self._parse_markets(event_markets, selections_by_market, sport, event_id)
        if not markets:
            return None

        return StandardEvent(
            id=f"{self.provider_id}_{event_id}",
            name=f"{home_raw} vs {away_raw}",
            sport=sport,
            markets=markets,
            provider=self.provider_id,
            start_time=start_time,
            home_team=home_team,
            away_team=away_team,
            league=league,
        )

    # Ice hockey: regulation-only templates to skip (OT-inclusive variants preferred)
    # Pinnacle uses OT-inclusive totals/spreads, so soft providers must match.
    # TGOU = total excl OT (TGOUOT is OT-inclusive), MHCPNOT = spread excl OT
    _ICE_HOCKEY_REGULATION_ONLY = {"TGOU", "MHCPNOT"}

    def _parse_markets(
        self,
        markets_raw: list[dict],
        selections_by_market: dict[str, list[dict]],
        sport: str = "",
        event_id: str = "",
    ) -> list[dict]:
        """Parse markets and their selections."""
        markets = []
        seen_types: set[str] = set()

        for market in markets_raw:
            template_id = market.get("marketTemplateId", "")
            market_type = self.MARKET_TEMPLATE_MAP.get(template_id)
            if not market_type:
                continue

            # Ice hockey: skip regulation-only total/spread when OT-inclusive exists
            # Pinnacle sharp odds include OT, so we must compare like-for-like.
            if sport == "ice_hockey" and template_id in self._ICE_HOCKEY_REGULATION_ONLY:
                continue

            # Skip half-time / period-specific markets by label
            market_label = (market.get("label") or "").lower()
            if any(kw in market_label for kw in self._PERIOD_KEYWORDS):
                continue

            # Skip duplicate market types (keep first)
            if market_type in seen_types:
                continue

            # Skip suspended markets
            if market.get("status") != "Open":
                continue

            market_id = market.get("id", "")
            selections = selections_by_market.get(market_id, [])

            # Extract point value for spread/total
            point = None
            if market_type in ("spread", "total"):
                line_raw = market.get("lineValueRaw")
                if line_raw is not None and line_raw != 0.0:
                    point = float(line_raw)
                else:
                    # Try lineValue string
                    line_str = market.get("lineValue", "").strip()
                    if line_str:
                        try:
                            # Handle "0 - 1" format → -1.0
                            if " - " in line_str:
                                parts = line_str.split(" - ")
                                point = float(parts[0]) - float(parts[1])
                            else:
                                point = float(line_str)
                        except (ValueError, IndexError):
                            pass

            outcomes = []
            for sel in selections:
                if sel.get("status") != "Open":
                    continue

                odds = sel.get("odds")
                if not odds or odds <= 1.0:
                    continue

                # Map selection template to outcome name
                sel_template = sel.get("selectionTemplateId", "")
                outcome_name = self.SELECTION_TEMPLATE_MAP.get(sel_template)
                if not outcome_name:
                    continue

                outcome_dict: dict[str, Any] = {
                    "name": outcome_name,
                    "odds": round(float(odds), 3),
                    "provider_meta": {
                        "selection_id": str(sel.get("id", "")),
                    },
                }
                if point is not None:
                    outcome_dict["point"] = point
                outcomes.append(outcome_dict)

            if outcomes:
                markets.append(
                    {
                        "type": market_type,
                        "outcomes": outcomes,
                        "provider_meta": {
                            "event_id": event_id,
                            "market_template": template_id,
                        },
                    }
                )
                seen_types.add(market_type)

        # Dedup: prefer 1x2 over moneyline
        types = {m["type"] for m in markets}
        if "1x2" in types and "moneyline" in types:
            markets = [m for m in markets if m["type"] != "moneyline"]

        return markets

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        """Not used - extract() is overridden."""
        return []
