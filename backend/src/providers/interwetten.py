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
    SPORT_LEAGUES = {
        "football": [
            # Major leagues
            (1021, "england-premier-league"),
            (1022, "england-championship"),
            (1019, "germany-bundesliga"),
            (1020, "germany-second-league"),
            (1030, "spain-laliga"),
            (105034, "spain-laliga-2"),
            (1029, "italy-serie-a"),
            (1024, "france-ligue-1"),
            (10235, "sweden-allsvenskan"),
            (10208, "sweden-superettan"),
            (1023, "austria-bundesliga"),
            (1025, "switzerland-super-league"),
            (10251, "norway-eliteseries"),
            (1026, "scotland-premiership"),
            (1028, "netherlands-eredivisie"),
            (1027, "portugal-primeira-liga"),
            (1031, "belgium-jupiler-league"),
            (10148, "japan-j-league"),
            # European cups
            (10410, "champions-league"),
            (105379, "europa-league"),
            (411663, "conference-league"),
        ],
        "ice_hockey": [
            (1036, "sweden-shl"),
            (1098, "usa-nhl"),
            (1037, "finland-liiga"),
        ],
        "basketball": [
            (1099, "usa-nba"),
            (10296, "euroleague"),
        ],
        "tennis": [
            # Tennis IDs to be discovered
        ],
        "american_football": [
            (1087, "usa-nfl"),
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

        for league_id, league_slug in leagues:
            if limit and len(all_events) >= limit:
                break

            try:
                league_events = await self._extract_league(
                    page, league_id, league_slug, sport
                )

                # Deduplicate
                for event in league_events:
                    if event.id not in seen_event_ids:
                        seen_event_ids.add(event.id)
                        all_events.append(event)

            except Exception as e:
                logger.warning(
                    f"[{self.provider_id}] Error extracting league {league_slug}: {e}"
                )

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

        # Wait for content to render
        await page.wait_for_timeout(2000)

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
