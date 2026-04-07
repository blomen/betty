"""
Interwetten Retriever - Browser DOM extraction with dynamic league discovery

Interwetten uses a proprietary platform (Sportsbook Software GmbH) with
server-side rendered HTML pages. Cloudflare blocks programmatic API/XHR
requests, so all extraction uses browser DOM navigation.

Extraction strategy (two-pass):
1. Discover leagues from sport overview page (dynamic, no hardcoded IDs)
2. League pages (concurrent tabs): Extract events with 1x2/moneyline odds
3. Event detail pages (concurrent tabs): Extract spread + total markets

League page data-betting format:
  Market: [marketId, eventId, "Match Name", "Market Label", locked, " "]
  Outcome: [outcomeId, "1"/"X"/"2", displayName, teamName, "odds", locked]

Event detail page market labels:
  Football: "Asian Handicap" (spread), "How many goals" (total)
  Basketball: "Handicap" (spread), "Over/Under" (total)
  Ice Hockey: "Over/Under" (total only, no Asian Handicap)
  Tennis: "Handicap Games" (spread), "How many games" (total)
  Handball: "Handicap" (spread), "Over/Under" (total)
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta

from ..core import StandardEvent
from ..core.browser_retriever import BrowserRetriever
from ..core.transport import BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class InterwettenRetriever(BrowserRetriever):
    """
    Interwetten SSR HTML extractor with dynamic league discovery.

    Navigates to league pages via Playwright and parses
    server-rendered event data from the DOM.
    """

    SPORT_OVERVIEW_MAP = {
        "football": (10, "football"),
        "ice_hockey": (40, "ice-hockey"),
        "basketball": (15, "basketball"),
        "tennis": (11, "tennis"),
        "handball": (1002, "handball"),
        "volleyball": (1012, "volleyball"),
        "rugby": (16, "rugby"),
        "cricket": (1027, "cricket"),
        "american_football": (13, "american-football"),
        "baseball": (14, "baseball"),
        "boxing": (90, "boxing"),
        "darts": (42, "darts"),
    }

    OUTCOME_MAP = {"1": "home", "X": "draw", "2": "away"}

    DETAIL_SPORTS = {
        "football", "basketball", "ice_hockey", "tennis",
        "handball", "volleyball", "american_football", "baseball", "rugby",
    }

    SPREAD_LABELS = {"Asian Handicap", "Handicap", "Handicap Games"}
    TOTAL_LABELS = {"How many goals", "Over/Under", "How many games"}

    JS_EXTRACT_DETAIL_MARKETS = """() => {
        const SPREAD = new Set(["Asian Handicap", "Handicap", "Handicap Games"]);
        const TOTAL = new Set(["How many goals", "Over/Under", "How many games"]);
        const results = { spread: null, total: null, datetime: null };

        const timeEl = document.querySelector('[class*="gametime"]');
        if (timeEl) results.datetime = timeEl.textContent.trim();

        const allBetting = document.querySelectorAll('[data-betting]');

        for (const el of allBetting) {
            try {
                const raw = JSON.parse(el.getAttribute('data-betting'));
                if (!Array.isArray(raw)) continue;
                if (typeof raw[1] !== 'number' || raw[1] < 100000) continue;
                const label = (raw[3] || '').trim();

                if (SPREAD.has(label) && !results.spread) {
                    const outcomes = [];
                    for (const oel of el.querySelectorAll('[data-betting]')) {
                        try {
                            const od = JSON.parse(oel.getAttribute('data-betting'));
                            if (typeof od[1] === 'string')
                                outcomes.push({ type: od[1], name: od[2], odds: od[4] });
                        } catch(e) {}
                    }
                    if (outcomes.length >= 2) results.spread = { label, outcomes };
                }

                if (TOTAL.has(label) && !results.total) {
                    const outcomes = [];
                    for (const oel of el.querySelectorAll('[data-betting]')) {
                        try {
                            const od = JSON.parse(oel.getAttribute('data-betting'));
                            if (typeof od[1] === 'string')
                                outcomes.push({ type: od[1], name: od[2], odds: od[4] });
                        } catch(e) {}
                    }
                    if (outcomes.length >= 2) results.total = { label, outcomes };
                }

                if (results.spread && results.total) break;
            } catch(e) {}
        }
        return results;
    }"""

    CONCURRENT_LEAGUE_PAGES = 16
    CONCURRENT_DETAIL_PAGES = 20
    MAX_DETAIL_EVENTS = 250

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        transport = transport or BrowserTransport(headless=True)
        super().__init__(config, transport=transport)
        self.base_url = config.get("site_url", "https://www.interwetten.se")
        # Interwetten only reads data-betting attributes — no CSS needed
        self.transport._BLOCK_STYLESHEETS = True

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events via two-pass DOM strategy with dynamic league discovery:
        1. Discover leagues from sport overview page
        2. League pages (concurrent): get events with 1x2/moneyline + detail hrefs
        3. Event detail pages (concurrent): get spread + total markets
        """
        import time as _time
        extract_start = _time.time()
        sport_timeout = self.config.get("sport_timeout", 300)

        await self.transport._ensure_browser()
        page = self.transport.page
        await self._ensure_init(f"{self.base_url}/en/sportsbook", "sportsbook")

        # Dismiss cookie consent banner (blocks DOM visibility)
        await self._dismiss_cookie_banner(page)

        # Discover leagues dynamically from sport overview page
        leagues = await self._discover_leagues_from_overview(page, sport)

        # Filter to target_leagues if provided
        target_leagues = kwargs.get("target_leagues")
        if target_leagues:
            leagues = self._filter_leagues(leagues, target_leagues)

        logger.info(f"[{self.provider_id}] {sport}: discovered {len(leagues)} leagues")

        if not leagues:
            return []

        # --- Pass 1: League pages (concurrent tabs) ---
        all_events = []
        event_hrefs = {}
        seen_event_ids = set()
        context = page.context
        league_sem = asyncio.Semaphore(self.CONCURRENT_LEAGUE_PAGES)

        league_pages = []
        for _ in range(self.CONCURRENT_LEAGUE_PAGES - 1):
            try:
                p = await context.new_page()
                league_pages.append(p)
            except Exception:
                break
        all_league_pages = [page] + league_pages
        league_page_pool = asyncio.Queue()
        for p in all_league_pages:
            await league_page_pool.put(p)

        errors = 0

        async def extract_league_concurrent(league):
            nonlocal errors
            if errors > 30:
                return [], {}
            worker_page = await league_page_pool.get()
            try:
                async with league_sem:
                    return await self._extract_league(
                        worker_page, league["id"], league["slug"], sport
                    )
            except Exception as e:
                errors += 1
                logger.debug(f"[{self.provider_id}] League {league.get('slug')} error: {e}")
                return [], {}
            finally:
                await league_page_pool.put(worker_page)

        # Scrape leagues in batches
        batch_size = 40
        for batch_start in range(0, len(leagues), batch_size):

            batch = leagues[batch_start:batch_start + batch_size]
            tasks = [extract_league_concurrent(lg) for lg in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for league_events, league_hrefs in results:
                if league_events:
                    for event in league_events:
                        if event.id not in seen_event_ids:
                            seen_event_ids.add(event.id)
                            all_events.append(event)
                    event_hrefs.update(league_hrefs)

        for p in league_pages:
            try:
                await p.close()
            except Exception:
                pass

        logger.info(
            f"[{self.provider_id}] {sport}: {len(all_events)} events from {len(leagues)} leagues ({errors} errors)"
        )

        # --- Pass 2: Event detail pages (spread + total) ---
        if all_events and event_hrefs and sport in self.DETAIL_SPORTS:
            detail_count = await self._enrich_with_detail_markets(
                page, all_events, event_hrefs, sport,
                extract_start=extract_start, sport_timeout=None,
            )
            logger.info(
                f"[{self.provider_id}] {sport}: enriched {detail_count}/{len(all_events)} "
                f"events with spread/total"
            )

        total_elapsed = _time.time() - extract_start
        logger.info(
            f"[{self.provider_id}] {sport}: completed in {total_elapsed:.0f}s — "
            f"{len(all_events)} events"
        )

        return all_events[:limit] if limit else all_events

    async def _dismiss_cookie_banner(self, page):
        """Dismiss Truendo cookie consent banner if present."""
        try:
            for selector in [
                'button:has-text("ACCEPT ALL")',
                'button:has-text("NECESSARY ONLY")',
                'button:has-text("Acceptera alla")',
                '.tru_overlay button',
            ]:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(500)
                    logger.debug(f"[{self.provider_id}] Cookie banner dismissed via {selector}")
                    return
        except Exception:
            pass

    async def _discover_leagues_from_overview(self, page, sport: str) -> List[dict]:
        """Navigate to sport overview page and extract league links from DOM."""
        sport_info = self.SPORT_OVERVIEW_MAP.get(sport)
        if not sport_info:
            logger.warning(f"[{self.provider_id}] No sport overview mapping for {sport}")
            return []
        sport_id, slug = sport_info
        url = f"{self.base_url}/en/sportsbook/o/{sport_id}/{slug}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Use state='attached' to bypass cookie banner overlay
            try:
                await page.wait_for_selector('a[href*="/l/"]', timeout=5000, state="attached")
            except Exception:
                logger.debug(f"[{self.provider_id}] No league links found for {sport}")
                return []
            # Click "Show more" if present
            try:
                for text in ["Show more", "Visa fler", "Show all"]:
                    show_more = await page.query_selector(f'button:has-text("{text}"), a:has-text("{text}")')
                    if show_more:
                        await show_more.click()
                        await page.wait_for_timeout(500)
                        break
            except Exception:
                pass
            leagues = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href*="/l/"]'))
                    .map(a => {
                        const m = a.href.match(/\\/l\\/(\\d+)\\/(.+?)(?:\\/|$|\\?)/);
                        return m ? {id: parseInt(m[1]), slug: m[2], name: a.textContent.trim()} : null;
                    })
                    .filter(Boolean)
                    .filter((v, i, a) => a.findIndex(x => x.id === v.id) === i);
            }""")
            return leagues or []
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to discover leagues for {sport}: {e}")
            return []

    def _filter_leagues(self, leagues: List[dict], target_leagues: set) -> List[dict]:
        """Filter discovered leagues to match target_leagues names."""
        target_lower = {t.lower() for t in target_leagues}
        matched = []
        for league in leagues:
            league_name = league.get("name", "").lower()
            if any(target in league_name or league_name in target for target in target_lower):
                matched.append(league)
        if not matched:
            logger.info(f"[{self.provider_id}] No leagues matched target_leagues, using top {min(20, len(leagues))}")
            return leagues[:20]
        return matched

    async def _extract_league(
        self, page, league_id: int, league_slug: str, sport: str,
    ) -> tuple[List[StandardEvent], Dict[str, str]]:
        """Extract events from a single league page."""
        url = f"{self.base_url}/en/sportsbook/l/{league_id}/{league_slug}"
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if not resp or resp.status != 200:
                status = resp.status if resp else '?'
                if status != 404:
                    logger.debug(f"[{self.provider_id}] League {league_slug}: HTTP {status}")
                return [], {}
        except Exception as e:
            logger.debug(f"[{self.provider_id}] League {league_slug} navigation: {e}")
            return [], {}

        try:
            await page.wait_for_selector('.s-event', timeout=3000)
        except Exception:
            return [], {}

        title = await page.title()
        if title == "Error":
            return [], {}

        raw_events = await page.evaluate("""() => {
            const events = [];
            const eventEls = document.querySelectorAll('.s-event');
            for (const el of eventEls) {
                try {
                    const players = el.querySelectorAll('.s-event-player');
                    if (players.length < 2) continue;
                    const home = players[0].textContent.trim();
                    const away = players[1].textContent.trim();
                    const link = el.querySelector('a[href*="/e/"]');
                    const href = link ? link.getAttribute('href') : '';
                    const idMatch = href.match(/\\/e\\/(\\d+)\\//);
                    const eventId = idMatch ? idMatch[1] : '';
                    const timeEl = el.querySelector('[class*="gametime"]');
                    const time = timeEl ? timeEl.textContent.trim() : '';
                    const outcomes = [];
                    const outcomeEls = el.querySelectorAll('.s-outcome');
                    for (const oe of outcomeEls) {
                        const dataBetting = oe.getAttribute('data-betting');
                        const oddSpan = oe.querySelector('.s-outcome-odd');
                        const oddText = oddSpan ? oddSpan.textContent.trim() : '';
                        if (dataBetting) {
                            try {
                                const parsed = JSON.parse(dataBetting);
                                outcomes.push({
                                    type: parsed[1],
                                    name: parsed[2],
                                    odds: oddText,
                                    locked: parsed[5] || false,
                                });
                            } catch(e) {}
                        }
                    }
                    if (home && away && outcomes.length > 0) {
                        events.push({ id: eventId, home, away, time, href, outcomes });
                    }
                } catch(e) {}
            }
            return events;
        }""")

        events = []
        hrefs = {}
        for raw in raw_events:
            event = self._parse_raw_event(raw, sport, league_slug)
            if event:
                events.append(event)
                href = raw.get("href", "")
                if href:
                    hrefs[event.id] = href

        logger.debug(f"[{self.provider_id}] {league_slug}: {len(events)} events")
        return events, hrefs

    async def _enrich_with_detail_markets(
        self, page, events: List[StandardEvent],
        event_hrefs: Dict[str, str], sport: str,
        extract_start: float = 0, sport_timeout: float = 300,
    ) -> int:
        """Navigate to event detail pages to extract spread and total markets."""
        todo = [(ev, event_hrefs[ev.id]) for ev in events if ev.id in event_hrefs]
        if not todo:
            return 0

        if len(todo) > self.MAX_DETAIL_EVENTS:
            logger.info(
                f"[{self.provider_id}] {sport}: capping detail enrichment from "
                f"{len(todo)} to {self.MAX_DETAIL_EVENTS} events"
            )
            todo = todo[:self.MAX_DETAIL_EVENTS]

        enriched = 0
        errors = 0
        sem = asyncio.Semaphore(self.CONCURRENT_DETAIL_PAGES)

        context = page.context
        extra_pages = []
        for _ in range(self.CONCURRENT_DETAIL_PAGES - 1):
            try:
                p = await context.new_page()
                extra_pages.append(p)
            except Exception:
                break
        all_pages = [page] + extra_pages
        page_pool = asyncio.Queue()
        for p in all_pages:
            await page_pool.put(p)

        async def enrich_one(event: StandardEvent, href: str):
            nonlocal enriched, errors
            if errors > 20:
                return

            # Time-budget check: stop if approaching sport timeout (if set)
            import time as _time
            if sport_timeout and extract_start and _time.time() - extract_start > sport_timeout * 0.90:
                return

            worker_page = await page_pool.get()
            try:
                async with sem:
                    url = f"{self.base_url}{href}"
                    resp = await worker_page.goto(url, wait_until="domcontentloaded", timeout=8000)
                    if not resp or resp.status != 200:
                        errors += 1
                        return

                    detail = await worker_page.evaluate(self.JS_EXTRACT_DETAIL_MARKETS)

                    dt_str = detail.get("datetime", "")
                    if dt_str:
                        parsed_dt = self._parse_datetime_str(dt_str)
                        if parsed_dt:
                            event.start_time = parsed_dt

                    added_markets = []
                    if detail.get("spread"):
                        spread_market = self._parse_spread_market(detail["spread"], event)
                        if spread_market:
                            added_markets.append(spread_market)
                    if detail.get("total"):
                        total_market = self._parse_total_market(detail["total"])
                        if total_market:
                            added_markets.append(total_market)

                    if added_markets:
                        event.markets.extend(added_markets)
                        enriched += 1

            except Exception as e:
                logger.debug(f"[{self.provider_id}] Detail page error for {event.id}: {e}")
                errors += 1
            finally:
                await page_pool.put(worker_page)

        await asyncio.gather(*(enrich_one(ev, href) for ev, href in todo), return_exceptions=True)

        for p in extra_pages:
            try:
                await p.close()
            except Exception:
                pass

        return enriched

    def _parse_datetime_str(self, dt_str: str) -> Optional[datetime]:
        """Parse interwetten datetime string like '15.03. - 15:00' into UTC datetime."""
        from zoneinfo import ZoneInfo
        m = re.search(r'(\d{1,2})\.(\d{1,2})\.\s*-\s*(\d{1,2}):(\d{2})', dt_str)
        if not m:
            return None
        try:
            day, month = int(m.group(1)), int(m.group(2))
            hour, minute = int(m.group(3)), int(m.group(4))
            now = datetime.now(timezone.utc)
            year = now.year
            if now.month >= 11 and month <= 2:
                year += 1
            return datetime(
                year, month, day, hour, minute, 0,
                tzinfo=ZoneInfo("Europe/Vienna"),  # CET/CEST — DST-aware
            ).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    def _parse_spread_market(
        self, raw_market: dict, event: StandardEvent
    ) -> Optional[dict]:
        """Parse Asian Handicap / Handicap market into spread format."""
        outcomes = []
        point = None
        point_by_side = {}

        for out in raw_market.get("outcomes", []):
            out_type = out.get("type", "")
            name = out.get("name", "")
            odds_str = out.get("odds", "")

            if out_type == "1":
                outcome_name = "home"
            elif out_type == "2":
                outcome_name = "away"
            else:
                continue

            try:
                odds = float(odds_str.replace(",", "."))
                if odds <= 1.0:
                    continue
            except (ValueError, TypeError):
                continue

            match = re.search(r'\(([+-]?\d+\.?\d*)\)', name)
            if match:
                p = float(match.group(1))
                point_by_side[outcome_name] = p
                if outcome_name == "home":
                    point = p

            outcomes.append({"name": outcome_name, "odds": odds})

        if len(outcomes) >= 2 and point is not None:
            for o in outcomes:
                side = o["name"]
                if side in point_by_side:
                    o["point"] = point_by_side[side]
                elif side == "away" and point is not None:
                    o["point"] = -point
                else:
                    o["point"] = point
            return {"type": "spread", "outcomes": outcomes}
        return None

    def _parse_total_market(self, raw_market: dict) -> Optional[dict]:
        """Parse How many goals / Over/Under market into total format."""
        outcomes = []
        point = None

        for out in raw_market.get("outcomes", []):
            name = out.get("name", "").strip()
            odds_str = out.get("odds", "")

            try:
                odds = float(odds_str.replace(",", "."))
                if odds <= 1.0:
                    continue
            except (ValueError, TypeError):
                continue

            name_lower = name.lower()
            if name_lower.startswith("over"):
                outcome_name = "over"
            elif name_lower.startswith("under"):
                outcome_name = "under"
            else:
                continue

            match = re.search(r'(\d+\.?\d*)', name)
            outcome_point = None
            if match:
                outcome_point = float(match.group(1))
                if outcome_name == "over":
                    point = outcome_point

            outcomes.append({"name": outcome_name, "odds": odds, "point": outcome_point})

        if len(outcomes) >= 2 and point is not None:
            return {"type": "total", "outcomes": outcomes}
        return None

    def _parse_raw_event(
        self, raw: dict, sport: str, league: str,
    ) -> Optional[StandardEvent]:
        """Parse a raw event dict from JavaScript extraction."""
        try:
            event_id = raw.get("id", "")
            home_raw = raw.get("home", "")
            away_raw = raw.get("away", "")
            time_str = raw.get("time", "")

            if not event_id or not home_raw or not away_raw:
                return None

            home_team = normalize_team_name(home_raw)
            away_team = normalize_team_name(away_raw)

            start_time = self._parse_datetime_str(time_str) if time_str else None
            if not start_time and time_str:
                try:
                    hour, minute = time_str.split(":")
                    now = datetime.now(timezone.utc)
                    start_time = now.replace(
                        hour=int(hour), minute=int(minute), second=0, microsecond=0
                    )
                    if start_time < now:
                        start_time += timedelta(days=1)
                except (ValueError, TypeError):
                    pass

            outcomes = []
            for out in raw.get("outcomes", []):
                if out.get("locked"):
                    continue
                out_type = out.get("type", "")
                out_name = self.OUTCOME_MAP.get(out_type)
                if not out_name:
                    continue
                odds_str = out.get("odds", "")
                try:
                    odds = float(odds_str.replace(",", "."))
                    if odds <= 1.0:
                        continue
                except (ValueError, TypeError):
                    continue
                outcomes.append({"name": out_name, "odds": odds})

            if not outcomes:
                return None

            has_draw = any(o["name"] == "draw" for o in outcomes)
            market_type = "1x2" if has_draw else "moneyline"
            markets = [{"type": market_type, "outcomes": outcomes}]

            return StandardEvent(
                id=f"interwetten_{event_id}",
                name=f"{home_raw} vs {away_raw}",
                provider=self.provider_id,
                sport=sport,
                league=league.replace("-", " ").title(),
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                markets=markets,
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")
            return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — browser-based extraction."""
        return []
