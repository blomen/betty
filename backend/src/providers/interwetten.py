"""
Interwetten Retriever - Browser-based SSR extraction

Interwetten uses a proprietary platform (Sportsbook Software GmbH) with
server-side rendered HTML pages. Each league page contains all events
with 1x2 odds embedded in the DOM.

Architecture:
1. Navigate to league page via Playwright (headed mode needed for Cloudflare)
2. Parse DOM elements: .s-event containers
3. Extract team names, odds, and event IDs from data-betting attributes

HTML Structure per event:
<li class="s-event">
  <div class="s-event-data">
    <a href="/en/sportsbook/e/{eventId}/slug">
      <div class="s-event-name">
        <strong class="s-event-player">Home Team</strong>
        <strong class="s-event-player">Away Team</strong>
      </div>
      <div class="js-gametime-{eventId}"><span>13:30</span></div>
    </a>
  </div>
  <div class="s-market" data-betting="[marketId, eventId, ...]">
    <div class="s-outcome" data-betting="[outcomeId, '1', 'Home', 'Home', '2,65', false]">
      <span class="s-outcome-odd">2.65</span>
    </div>
    <div class="s-outcome" data-betting="[outcomeId, 'X', 'X', 'X', '3,5', false]">
      <span class="s-outcome-odd">3.50</span>
    </div>
    <div class="s-outcome" data-betting="[outcomeId, '2', 'Away', 'Away', '2,2', false]">
      <span class="s-outcome-odd">2.20</span>
    </div>
  </div>
</li>

League pages: /en/sportsbook/l/{leagueId}/league-slug
Sport page (all): /en/sportsbook/e/football (doesn't work — blocked)
Main sportsbook: /en/sportsbook (works — lists all navigation)
"""

from typing import Dict, Any, List, Optional
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

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        # Interwetten needs headed browser to bypass Cloudflare
        transport = transport or BrowserTransport(headless=False)
        super().__init__(config, transport=transport)
        self.base_url = config.get("site_url", "https://www.interwetten.se")

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events by navigating to league pages and parsing SSR HTML.

        Strategy:
        1. Navigate to /en/sportsbook first (cookies + session)
        2. Then visit each league page for the sport
        3. Parse DOM to extract events with 1x2 odds
        """
        leagues = self.SPORT_LEAGUES.get(sport, [])
        if not leagues:
            logger.warning(f"[{self.provider_id}] No leagues configured for {sport}")
            return []

        logger.info(f"[{self.provider_id}] Starting extraction for {sport} ({len(leagues)} leagues)")

        # Initialize browser and get session cookies
        await self.transport._ensure_browser()
        page = self.transport.page

        # Apply stealth
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            window.chrome = {runtime: {}};
        """)

        # Navigate to main sportsbook first to establish session
        await self._ensure_init(f"{self.base_url}/en/sportsbook", "sportsbook")

        all_events = []
        seen_event_ids = set()
        empty_count = 0

        for league_id, league_slug in leagues:
            if limit and len(all_events) >= limit:
                break

            try:
                league_events = await self._extract_league(
                    page, league_id, league_slug, sport
                )

                if league_events:
                    empty_count = 0
                    # Deduplicate
                    new_count = 0
                    for event in league_events:
                        if event.id not in seen_event_ids:
                            seen_event_ids.add(event.id)
                            all_events.append(event)
                            new_count += 1
                    if new_count > 0:
                        logger.debug(
                            f"[{self.provider_id}] {league_slug}: {new_count} events"
                        )
                else:
                    empty_count += 1

            except Exception as e:
                logger.warning(
                    f"[{self.provider_id}] Error extracting league {league_slug}: {e}"
                )
                empty_count += 1

        logger.info(
            f"[{self.provider_id}] {sport}: {len(all_events)} events from {len(leagues)} leagues"
        )
        return all_events[:limit] if limit else all_events

    async def _extract_league(
        self,
        page,
        league_id: int,
        league_slug: str,
        sport: str,
    ) -> List[StandardEvent]:
        """Extract events from a single league page."""
        url = f"{self.base_url}/en/sportsbook/l/{league_id}/{league_slug}"

        try:
            resp = await page.goto(url, wait_until="load", timeout=30000)
            if not resp or resp.status != 200:
                logger.debug(f"[{self.provider_id}] League {league_slug}: status {resp.status if resp else '?'}")
                return []
        except Exception as e:
            logger.debug(f"[{self.provider_id}] League {league_slug} navigation error: {e}")
            return []

        # Wait for content to render — try to detect events quickly
        try:
            await page.wait_for_selector('.s-event', timeout=3000)
        except Exception:
            # No events on this page — skip quickly
            return []

        title = await page.title()
        if title == "Error":
            logger.debug(f"[{self.provider_id}] League {league_slug}: page returned Error")
            return []

        # Parse events from DOM using JavaScript evaluation
        raw_events = await page.evaluate("""() => {
            const events = [];
            const eventEls = document.querySelectorAll('.s-event');

            for (const el of eventEls) {
                try {
                    // Get team names
                    const players = el.querySelectorAll('.s-event-player');
                    if (players.length < 2) continue;
                    const home = players[0].textContent.trim();
                    const away = players[1].textContent.trim();

                    // Get event link/ID
                    const link = el.querySelector('a[href*="/e/"]');
                    const href = link ? link.getAttribute('href') : '';
                    const idMatch = href.match(/\\/e\\/(\\d+)\\//);
                    const eventId = idMatch ? idMatch[1] : '';

                    // Get time
                    const timeEl = el.querySelector('[class*="gametime"] span');
                    const time = timeEl ? timeEl.textContent.trim() : '';

                    // Get outcomes from data-betting attributes
                    const outcomes = [];
                    const outcomeEls = el.querySelectorAll('.s-outcome');
                    for (const oe of outcomeEls) {
                        const dataBetting = oe.getAttribute('data-betting');
                        const oddSpan = oe.querySelector('.s-outcome-odd');
                        const oddText = oddSpan ? oddSpan.textContent.trim() : '';

                        if (dataBetting) {
                            try {
                                const parsed = JSON.parse(dataBetting);
                                // Format: [outcomeId, "1"/"X"/"2", displayName, teamName, "odds,value", isLocked]
                                outcomes.push({
                                    type: parsed[1],  // "1", "X", or "2"
                                    name: parsed[2],
                                    odds: oddText,
                                    locked: parsed[5] || false,
                                });
                            } catch(e) {}
                        }
                    }

                    // Get market count
                    const countEl = el.querySelector('[data-count]');
                    const marketCount = countEl ? parseInt(countEl.getAttribute('data-count')) : 0;

                    if (home && away && outcomes.length > 0) {
                        events.push({
                            id: eventId,
                            home: home,
                            away: away,
                            time: time,
                            outcomes: outcomes,
                            marketCount: marketCount,
                        });
                    }
                } catch(e) {}
            }

            return events;
        }""")

        events = []
        for raw in raw_events:
            event = self._parse_raw_event(raw, sport, league_slug)
            if event:
                events.append(event)

        logger.info(
            f"[{self.provider_id}] {league_slug}: {len(events)} events"
        )
        return events

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
