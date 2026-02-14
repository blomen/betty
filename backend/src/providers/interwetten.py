"""
Interwetten Retriever - Browser-based SSR extraction

Interwetten uses a proprietary platform (Sportsbook Software GmbH) with
server-side rendered HTML pages.

Extraction strategy (two-pass):
1. League pages: Navigate to each league, extract events with 1x2/moneyline odds
2. Event detail pages: Navigate to each event, extract spread + total markets

League page data-betting format:
  Market: [marketId, eventId, "Match Name", "Market Label", locked, " "]
  Outcome: [outcomeId, "1"/"X"/"2", displayName, teamName, "odds", locked]

Event detail page market labels:
  Football: "Asian Handicap" (spread), "How many goals" (total)
  Basketball: "Handicap" (spread), "Over/Under" (total)
  Ice Hockey: "Over/Under" (total only, no Asian Handicap)
  Tennis: "Handicap Games" (spread), "How many games" (total)
  Handball: "Handicap" (spread), "Over/Under" (total)

Spread outcome format: "Team Name (+1.5)" / "Team Name (-1.5)" with type "1"/"2"
Total outcome format: "Over 2.5" / "Under 2.5" with type " " (space)
"""

from typing import Dict, Any, List, Optional
import asyncio
import json
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
    Interwetten SSR HTML extractor.

    Navigates to league pages via Playwright and parses
    server-rendered event data from the DOM.
    """

    # League IDs per sport — discovered from /en/sportsbook navigation
    # League IDs discovered 2026-02-09 from /en/sportsbook navigation
    # Script: scripts/discover_interwetten_leagues.py
    SPORT_LEAGUES = {
        "football": [
            # European cups
            (10410, "champions-league"),
            (105379, "europa-league"),
            (411663, "conference-league"),
            (405615, "champions-league-women"),
            (405836, "concacaf-champions-league"),
            (105120, "copa-libertadores"),
            (105217, "copa-sudamericana"),
            (105353, "afc-champions-league-elite"),
            # Top leagues
            (1021, "england-premier-league"),
            (1022, "england-championship"),
            (10467, "england-league-one"),
            (10468, "england-league-two"),
            (1091, "england-fa-cup"),
            (10427, "england-efl-cup"),
            (1019, "germany-bundesliga"),
            (1020, "germany-second-league"),
            (10268, "germany-dfb-cup"),
            (1030, "spain-laliga"),
            (105034, "spain-laliga-2"),
            (10523, "spain-cup"),
            (1029, "italy-serie-a"),
            (405298, "italy-serie-b"),
            (407049, "italy-cup"),
            (1024, "france-ligue-1"),
            (10617, "france-ligue-2"),
            (1081, "france-cup"),
            # Nordic
            (10235, "sweden-allsvenskan"),
            (10208, "sweden-superettan"),
            (10251, "norway-eliteseries"),
            (10782, "norway-1st-division"),
            (1035, "denmark-superleague"),
            (105225, "denmark-first-division"),
            (10293, "finland-veikkausliiga"),
            # Rest of Europe
            (1023, "austria-bundesliga"),
            (10900, "austria-second-league"),
            (1025, "switzerland-super-league"),
            (105002, "switzerland-challenge-league"),
            (1026, "scotland-premiership"),
            (10605, "scotland-championship"),
            (1027, "netherlands-eredivisie"),
            (10448, "netherlands-eerste-divisie"),
            (10598, "portugal-primeira-liga"),
            (10269, "portugal-segunda-liga"),
            (1028, "belgium-pro-league"),
            (10265, "belgium-challenger-pro-league"),
            (1036, "turkiye-super-lig"),
            (405290, "turkiye-first-league"),
            (1060, "greece-super-league-1"),
            (1059, "poland-ekstraklasa"),
            (10420, "czech-republic-1st-league"),
            (10306, "hungary-nb-i"),
            (405364, "romania-league-1"),
            (406174, "serbia-superleague"),
            (405435, "cyprus-division-1"),
            (10435, "ireland-premier"),
            # Americas
            (10750, "usa-major-league-soccer"),
            (105121, "argentina-liga-profesional"),
            (405525, "brazil-serie-a"),
            (405526, "brazil-serie-b"),
            (405250, "mexico-liga-mx"),
            (405736, "colombia-primera-a"),
            (405415, "chile-primera-division"),
            (405440, "uruguay-primera-division"),
            (405417, "peru-1st-league"),
            (406296, "costa-rica-primera-division"),
            # Rest of world
            (405485, "australia-a-league"),
            (406183, "saudi-arabia-pro-league"),
            (405644, "egypt-premier-league"),
            (406147, "south-africa-premier-league"),
        ],
        "ice_hockey": [
            (4080, "nhl"),
            (40506, "sweden-shl"),
            (406409, "sweden-hockey-allsvenskan"),
            (40511, "finland-liiga"),
            (406404, "finland-mestis"),
            (40627, "germany-del"),
            (405088, "switzerland-national-league"),
            (4083, "austria-ihl"),
            (405257, "czech-republic-extraliga"),
            (405258, "slovakia-extraliga"),
            (405093, "norway-eliteserien"),
            (405307, "denmark-superisligaen"),
            (406024, "champions-hockey-league"),
        ],
        "basketball": [
            (15103, "nba"),
            (15, "wnba"),
            (405226, "euroleague"),
            (405358, "eurocup"),
            (408307, "fiba-cl"),
            (405577, "aba-league"),
            (412221, "bnxt-league"),
            (405446, "spain-primera-feb"),
            (405293, "italy-lega-a"),
            (405602, "greece-elite-league"),
            (408802, "turkiye-tbl"),
            (406521, "finland-korisliigan"),
            (405733, "korea-kbl"),
            (409333, "argentina-liga"),
        ],
        "tennis": [
            # ATP tour
            (11512, "atp-rotterdam"),
            (407303, "atp-buenos-aires"),
            (412162, "atp-dallas"),
            # ATP Challengers
            (407270, "atp-challenger-pau"),
            (414524, "atp-challenger-brisbane-2"),
            (411967, "atp-challenger-tenerife"),
            (415450, "atp-challenger-baton-rouge"),
            (407290, "atp-challenger-chennai"),
            # WTA tour
            (115072, "wta-doha"),
            (413769, "wta-oeiras-125"),
            # Grand Slams (for future reference)
            (407198, "australian-open-men"),
            (407199, "australian-open-ladies"),
            (115023, "wimbledon-men"),
            (115212, "wimbledon-ladies"),
            (115052, "french-open-men"),
            (115229, "french-open-ladies"),
            (407014, "us-open-men"),
            (407013, "us-open-ladies"),
        ],
        "handball": [
            (405361, "ehf-champions-league-men"),
            (405441, "ehf-el-men"),
            (405362, "sweden-elitserien"),
            (405225, "germany-bundesliga"),
            (405376, "spain-asobal"),
            (405454, "france-lnh-starligue"),
            (405616, "poland-superliga"),
            (405390, "denmark-haandboldligaen-men"),
        ],
        "volleyball": [
            (405581, "germany-bundesliga-men"),
            (405458, "italy-a1-men"),
            (406029, "turkiye-sultanlar-ligi-women"),
            (405460, "greece-a1-men"),
            (406062, "brazil-superleague-men"),
            (406117, "finland-sm-liiga-men"),
            (405764, "korea-v-league-men"),
            (405443, "champions-league-men"),
            (405515, "champions-league-women"),
        ],
        "rugby": [
            (405395, "australia-nrl"),
            (405396, "super-league"),
            (405302, "rugby-six-nations"),
            (405397, "super-rugby"),
            (405398, "european-champions-cup"),
            (405988, "united-rugby-championship"),
            (405453, "english-premiership"),
            (405414, "france-top-14"),
        ],
        "cricket": [
            (405870, "india-premier-league"),
            (407373, "twenty-20-world-cup"),
            (405607, "icc-world-cup"),
            (410274, "the-hundred"),
            (409140, "england-t20-blast"),
            (409141, "england-county-championship"),
            (406632, "australia-big-bash-league"),
        ],
        "darts": [
            (407283, "premier-league-darts"),
            (411495, "modus-super-series"),
            (405512, "championship-league"),
        ],
        "boxing": [
            (90, "boxing"),
        ],
        "american_football": [
            # NFL not in current navigation — may use different path
            # Keep old ID as fallback
            (13473, "ncaaf-college"),
            (13450, "canada-cfl"),
        ],
        "baseball": [
            (14233, "mlb"),
            (406200, "korea-kbo"),
        ],
    }

    # Outcome type mapping from data-betting to our standard
    OUTCOME_MAP = {
        "1": "home",
        "X": "draw",
        "2": "away",
    }

    # Sports where event detail pages have useful spread/total markets
    DETAIL_SPORTS = {
        "football", "basketball", "ice_hockey", "tennis",
        "handball", "volleyball", "american_football", "baseball", "rugby",
    }

    # Market label sets for spread/total detection across sports
    # Football: "Asian Handicap" (spread), "How many goals" (total)
    # Basketball/Ice Hockey: "Handicap" (spread), "Over/Under" (total)
    # Tennis: "Handicap Games" (spread), "How many games" (total)
    # Handball: "Handicap" (spread), "Over/Under" (total)
    SPREAD_LABELS = {"Asian Handicap", "Handicap", "Handicap Games"}
    TOTAL_LABELS = {"How many goals", "Over/Under", "How many games"}

    # JS to extract spread/total from event detail page data-betting attributes
    JS_EXTRACT_DETAIL_MARKETS = """() => {
        const SPREAD = new Set(["Asian Handicap", "Handicap", "Handicap Games"]);
        const TOTAL = new Set(["How many goals", "Over/Under", "How many games"]);
        const results = { spread: null, total: null };
        const allBetting = document.querySelectorAll('[data-betting]');

        for (const el of allBetting) {
            try {
                const raw = JSON.parse(el.getAttribute('data-betting'));
                if (!Array.isArray(raw)) continue;
                // Market-level: [marketId, eventId(number), matchName, marketLabel, locked, " "]
                if (typeof raw[1] !== 'number' || raw[1] < 100000) continue;
                const label = (raw[3] || '').trim();

                // Spread markets (first occurrence = main line)
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

                // Total markets (first occurrence = main line)
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

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        # Interwetten needs headed browser to bypass Cloudflare
        transport = transport or BrowserTransport(headless=False)
        super().__init__(config, transport=transport)
        self.base_url = config.get("site_url", "https://www.interwetten.se")

    CONCURRENT_LEAGUE_PAGES = 5  # Parallel league navigation tabs (Pass 1)

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events via two-pass strategy with concurrent navigation:
        1. League pages (concurrent): get events with 1x2/moneyline odds + event detail hrefs
        2. Event detail pages (concurrent): navigate to each event for spread + total markets
        """
        leagues = self.SPORT_LEAGUES.get(sport, [])
        if not leagues:
            logger.warning(f"[{self.provider_id}] No leagues configured for {sport}")
            return []

        logger.info(f"[{self.provider_id}] Starting extraction for {sport} ({len(leagues)} leagues)")

        # Initialize browser and get session cookies
        await self.transport._ensure_browser()
        page = self.transport.page

        # Navigate to main sportsbook first to establish session
        await self._ensure_init(f"{self.base_url}/en/sportsbook", "sportsbook")

        # --- Pass 1: League listing pages with CONCURRENT tabs ---
        all_events = []
        event_hrefs = {}
        seen_event_ids = set()

        context = page.context
        sem = asyncio.Semaphore(self.CONCURRENT_LEAGUE_PAGES)

        # Create extra pages for concurrent league navigation
        extra_pages = []
        for _ in range(self.CONCURRENT_LEAGUE_PAGES - 1):
            try:
                p = await context.new_page()
                extra_pages.append(p)
            except Exception:
                break
        all_pages = [page] + extra_pages
        page_pool = asyncio.Queue()
        for p in all_pages:
            await page_pool.put(p)

        errors = 0

        async def extract_league_concurrent(league_id, league_slug):
            nonlocal errors
            if errors > 30:
                return [], {}
            worker_page = await page_pool.get()
            try:
                async with sem:
                    return await self._extract_league(
                        worker_page, league_id, league_slug, sport
                    )
            except Exception as e:
                errors += 1
                logger.debug(f"[{self.provider_id}] League {league_slug} error: {e}")
                return [], {}
            finally:
                await page_pool.put(worker_page)

        # Process leagues in batches
        batch_size = 20
        for batch_start in range(0, len(leagues), batch_size):
            if limit and len(all_events) >= limit:
                break
            if errors > 30:
                logger.warning(f"[{self.provider_id}] Too many league errors ({errors}), stopping")
                break

            batch = leagues[batch_start:batch_start + batch_size]
            tasks = [extract_league_concurrent(lid, lslug) for lid, lslug in batch]
            results = await asyncio.gather(*tasks)

            for league_events, league_hrefs in results:
                if league_events:
                    for event in league_events:
                        if event.id not in seen_event_ids:
                            seen_event_ids.add(event.id)
                            all_events.append(event)
                    event_hrefs.update(league_hrefs)

        # Close extra pages from Pass 1
        for p in extra_pages:
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
                page, all_events, event_hrefs, sport
            )
            logger.info(
                f"[{self.provider_id}] {sport}: enriched {detail_count}/{len(all_events)} events with spread/total"
            )

        return all_events[:limit] if limit else all_events

    async def _extract_league(
        self,
        page,
        league_id: int,
        league_slug: str,
        sport: str,
    ) -> tuple[List[StandardEvent], Dict[str, str]]:
        """Extract events from a single league page.

        Returns:
            Tuple of (events, {event_id: detail_href})
        """
        url = f"{self.base_url}/en/sportsbook/l/{league_id}/{league_slug}"

        try:
            resp = await page.goto(url, wait_until="load", timeout=20000)
            if not resp or resp.status != 200:
                status = resp.status if resp else '?'
                if status != 404:  # 404 = league doesn't exist, not worth logging
                    logger.info(f"[{self.provider_id}] League {league_slug}: HTTP {status}")
                return [], {}
        except Exception as e:
            logger.info(f"[{self.provider_id}] League {league_slug} navigation: {e}")
            return [], {}

        # Wait for content to render — try to detect events quickly
        try:
            await page.wait_for_selector('.s-event', timeout=3000)
        except Exception:
            return [], {}

        title = await page.title()
        if title == "Error":
            logger.debug(f"[{self.provider_id}] League {league_slug}: page returned Error")
            return [], {}

        # Parse events from DOM using JavaScript evaluation
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

                    const timeEl = el.querySelector('[class*="gametime"] span');
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

                    const countEl = el.querySelector('[data-count]');
                    const marketCount = countEl ? parseInt(countEl.getAttribute('data-count')) : 0;

                    if (home && away && outcomes.length > 0) {
                        events.push({
                            id: eventId,
                            home: home,
                            away: away,
                            time: time,
                            href: href,
                            outcomes: outcomes,
                            marketCount: marketCount,
                        });
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

        logger.info(
            f"[{self.provider_id}] {league_slug}: {len(events)} events"
        )
        return events, hrefs

    CONCURRENT_DETAIL_PAGES = 5  # Reduced from 8 — headed mode more stable with lower concurrency

    async def _enrich_with_detail_markets(
        self,
        page,
        events: List[StandardEvent],
        event_hrefs: Dict[str, str],
        sport: str,
    ) -> int:
        """Navigate to event detail pages to extract spread and total markets.

        Uses concurrent tabs (CONCURRENT_DETAIL_PAGES) for parallelism.
        Returns count of events enriched with additional markets.
        """
        # Filter to events with hrefs
        todo = [(ev, event_hrefs[ev.id]) for ev in events if ev.id in event_hrefs]
        if not todo:
            return 0

        enriched = 0
        errors = 0
        sem = asyncio.Semaphore(self.CONCURRENT_DETAIL_PAGES)

        # Open extra pages for concurrency (reuse main page as one worker)
        context = page.context
        extra_pages = []
        for _ in range(self.CONCURRENT_DETAIL_PAGES - 1):
            try:
                p = await context.new_page()
                extra_pages.append(p)
            except Exception:
                break
        all_pages = [page] + extra_pages
        # Round-robin page assignment
        page_pool = asyncio.Queue()
        for p in all_pages:
            await page_pool.put(p)

        async def enrich_one(event: StandardEvent, href: str):
            nonlocal enriched, errors
            if errors > 20:
                return

            worker_page = await page_pool.get()
            try:
                async with sem:
                    url = f"{self.base_url}{href}"
                    resp = await worker_page.goto(url, wait_until="load", timeout=20000)
                    if not resp or resp.status != 200:
                        errors += 1
                        return

                    await worker_page.wait_for_timeout(150)
                    detail = await worker_page.evaluate(self.JS_EXTRACT_DETAIL_MARKETS)

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

        # Process in batches to allow early exit on too many errors
        batch_size = 20
        for i in range(0, len(todo), batch_size):
            if errors > 20:
                logger.warning(f"[{self.provider_id}] Too many detail page errors ({errors}), stopping enrichment")
                break
            batch = todo[i:i + batch_size]
            await asyncio.gather(*(enrich_one(ev, href) for ev, href in batch))

        # Close extra pages
        for p in extra_pages:
            try:
                await p.close()
            except Exception:
                pass

        return enriched

    def _parse_spread_market(
        self, raw_market: dict, event: StandardEvent
    ) -> Optional[dict]:
        """Parse Asian Handicap / Handicap market into spread format.

        Outcome name format: "Team Name (+1.5)" or "Team Name (-1.5)"
        Outcome type: "1" (home) or "2" (away)
        """
        outcomes = []
        point = None

        point_by_side = {}  # "home" -> point, "away" -> point

        for out in raw_market.get("outcomes", []):
            out_type = out.get("type", "")
            name = out.get("name", "")
            odds_str = out.get("odds", "")

            # Map type to home/away
            if out_type == "1":
                outcome_name = "home"
            elif out_type == "2":
                outcome_name = "away"
            else:
                continue

            # Parse odds
            try:
                odds = float(odds_str.replace(",", "."))
                if odds <= 1.0:
                    continue
            except (ValueError, TypeError):
                continue

            # Extract point value from display name: "Team (+1.5)" or "Team (-0.5)"
            match = re.search(r'\(([+-]?\d+\.?\d*)\)', name)
            if match:
                p = float(match.group(1))
                point_by_side[outcome_name] = p
                if outcome_name == "home":
                    point = p

            outcomes.append({"name": outcome_name, "odds": odds})

        # Add point to each outcome (storage pipeline expects point on each outcome)
        if len(outcomes) >= 2 and point is not None:
            for o in outcomes:
                side = o["name"]
                if side in point_by_side:
                    o["point"] = point_by_side[side]
                elif side == "away" and point is not None:
                    o["point"] = -point  # away point is negated home point
                else:
                    o["point"] = point
            return {
                "type": "spread",
                "outcomes": outcomes,
            }
        return None

    def _parse_total_market(self, raw_market: dict) -> Optional[dict]:
        """Parse How many goals / Over/Under market into total format.

        Football: "How many goals" with outcomes "Over 3.5" / "Under 3.5"
        Basketball/etc: "Over/Under" with outcomes "over 220.5" / "under 220.5"
        """
        outcomes = []
        point = None

        for out in raw_market.get("outcomes", []):
            name = out.get("name", "").strip()
            odds_str = out.get("odds", "")

            # Parse odds
            try:
                odds = float(odds_str.replace(",", "."))
                if odds <= 1.0:
                    continue
            except (ValueError, TypeError):
                continue

            # Determine over/under from display name
            name_lower = name.lower()
            if name_lower.startswith("over"):
                outcome_name = "over"
            elif name_lower.startswith("under"):
                outcome_name = "under"
            else:
                continue

            # Extract point value: "Over 2.5" → 2.5, "over 220.5" → 220.5
            match = re.search(r'(\d+\.?\d*)', name)
            outcome_point = None
            if match:
                outcome_point = float(match.group(1))
                if outcome_name == "over":
                    point = outcome_point

            outcomes.append({"name": outcome_name, "odds": odds, "point": outcome_point})

        if len(outcomes) >= 2 and point is not None:
            return {
                "type": "total",
                "outcomes": outcomes,
            }
        return None

    def _parse_raw_event(
        self,
        raw: dict,
        sport: str,
        league: str,
    ) -> Optional[StandardEvent]:
        """Parse a raw event dict from JavaScript extraction."""
        try:
            event_id = raw.get("id", "")
            home_raw = raw.get("home", "")
            away_raw = raw.get("away", "")
            time_str = raw.get("time", "")

            if not event_id or not home_raw or not away_raw:
                return None

            # Normalize team names
            home_team = normalize_team_name(home_raw)
            away_team = normalize_team_name(away_raw)

            # Parse start time (just time like "13:30" on the page)
            start_time = None
            if time_str:
                try:
                    hour, minute = time_str.split(":")
                    # Assume today or tomorrow
                    now = datetime.now(timezone.utc)
                    start_time = now.replace(
                        hour=int(hour), minute=int(minute), second=0, microsecond=0
                    )
                    # If time is in the past, it's tomorrow
                    if start_time < now:
                        start_time += timedelta(days=1)
                except (ValueError, TypeError):
                    pass

            # Parse outcomes
            outcomes = []
            for out in raw.get("outcomes", []):
                if out.get("locked"):
                    continue

                out_type = out.get("type", "")
                out_name = self.OUTCOME_MAP.get(out_type)
                if not out_name:
                    continue

                # Parse odds (displayed as "2.65" or "13.00")
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

            # Build market — Interwetten league pages only show 1x2
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
