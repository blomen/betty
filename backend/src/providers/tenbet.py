"""
10Bet Retriever - DOM scraping approach

10Bet uses a Playtech/Mojito sportsbook widget that renders events into the DOM
with `ta-*` CSS class prefixes. No REST API or WebSocket endpoints are available
for event/odds data — DOM scraping is the only viable approach.

DOM selectors:
  - ta-EventListItem = event row container
  - ta-participantName = team names
  - ta-price_text = odds values
  - ta-MarketType-MRES = match result (1x2, 3-way) -- football, handball
  - ta-MarketType-HCTG = total goals (over/under) -- football, handball
  - ta-MarketType-HCMR = handicap match result (spread, 3-way) -- football
  - ta-MarketType-H2HT = head-to-head / moneyline (2-way) -- basketball, ice hockey, amfoot
  - ta-MarketType-HTOH = head-to-head (2-way) -- tennis, MMA
  - ta-MarketType-HCOT = handicap / spread (2-way) -- basketball, ice hockey
  - ta-MarketType-FHOT = full-game handicap / spread -- american football
  - ta-MarketType-TPOT = total points (over/under) -- basketball
  - ta-MarketType-OUTG = over/under total goals -- ice hockey
  - ta-MarketType-FTPO = full-game total points (over/under) -- american football
  - ta-MarketType-TGHC = total games handicap -- tennis
  - ta-infoText = spread/total point values
  - ta-EventTimingStatus = match date/time

URL structure:
  - /sports/{sport}/competitions = list of competitions with numeric IDs
  - /sports/{sport}/competitions/{id}/matches = matches for a competition
"""

from typing import List, Any, Optional, Dict
import logging
import asyncio
import re
from datetime import datetime, timedelta
from ..core import BrowserRetriever, StandardEvent, BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

# Market type code -> canonical market type mapping.
# Each sport uses different DOM class codes for the same market concepts.
MARKET_TYPE_MAP: Dict[str, str] = {
    # 1x2 / Moneyline (winner market)
    "MRES": "1x2",       # Match Result -- football, handball (3-way)
    "H2HT": "moneyline", # Head-to-Head -- basketball, ice hockey, american football (2-way)
    "HTOH": "moneyline", # Head-to-Head -- tennis, MMA (2-way)
    # Total (over/under)
    "HCTG": "total",     # Total Goals -- football, handball
    "TPOT": "total",     # Total Points -- basketball
    "OUTG": "total",     # Over/Under Total Goals -- ice hockey
    "FTPO": "total",     # Full-game Total Points -- american football
    # Spread (handicap)
    "HCMR": "spread",   # Handicap Match Result -- football (3-way)
    "HCOT": "spread",   # Handicap -- basketball, ice hockey (2-way)
    "FHOT": "spread",   # Full-game Handicap -- american football
    "TGHC": "spread",   # Total Games Handicap -- tennis
}


JS_EXTRACT_DETAIL_MARKETS = """() => {
    const result = {spread: null, total: null};

    // Find Asian Handicap market (spread) - look for market containers
    const allMarkets = document.querySelectorAll('[class*="ta-MarketType-"], [class*="ta-AggregatedMarket"]');
    for (const mkt of allMarkets) {
        const cls = Array.from(mkt.classList || []).join(' ');

        // Asian Handicap (2-way spread)
        if (!result.spread && (cls.includes('AHCP') || cls.includes('AsianHandicap'))) {
            const outcomes = [];
            const prices = Array.from(mkt.querySelectorAll('[class*="ta-price_text"]')).map(p => p.textContent.trim());
            const infos = Array.from(mkt.querySelectorAll('[class*="ta-infoText"]')).map(t => t.textContent.trim());
            const names = Array.from(mkt.querySelectorAll('[class*="ta-participantName"]')).map(n => n.textContent.trim());

            for (let i = 0; i < Math.min(prices.length, 2); i++) {
                outcomes.push({
                    name: names[i] || '',
                    point: infos[i] || '',
                    odds: prices[i]
                });
            }
            if (outcomes.length >= 2) result.spread = {outcomes};
        }

        // Asian Total / Over-Under (total)
        if (!result.total && (cls.includes('ATOT') || cls.includes('AsianTotal') || cls.includes('OverUnder') || cls.includes('ÖverUnder'))) {
            const outcomes = [];
            const prices = Array.from(mkt.querySelectorAll('[class*="ta-price_text"]')).map(p => p.textContent.trim());
            const labels = Array.from(mkt.querySelectorAll('[class*="ta-participantName"], [class*="ta-label"]')).map(l => l.textContent.trim());

            for (let i = 0; i < Math.min(prices.length, 2); i++) {
                outcomes.push({
                    name: labels[i] || (i === 0 ? 'Over' : 'Under'),
                    odds: prices[i]
                });
            }
            if (outcomes.length >= 2) result.total = {outcomes};
        }

        if (result.spread && result.total) break;
    }
    return result;
}"""


class TenBetRetriever(BrowserRetriever):
    """
    Retriever for 10Bet sportsbook via DOM scraping.

    Navigates to competition listing pages, discovers competition IDs,
    then scrapes each competition's matches page for events and odds.
    """

    SPORT_SLUGS: Dict[str, str] = {
        "football": "football",
        "basketball": "basketball",
        "tennis": "tennis",
        "ice_hockey": "ice_hockey",
        "american_football": "american_football",
        "baseball": "baseball",
        "mma": "martial_arts",
        "esports": "esports",
        "handball": "handball",
        "volleyball": "volleyball",
        "cricket": "cricket",
        "table_tennis": "table_tennis",
        "boxing": "boxing",
        "curling": "curling",
    }

    # Sports that benefit from detail-page enrichment for spread/total.
    # Basketball/ice_hockey/baseball already get spread/total from the DOM list
    # (HCOT/TPOT/OUTG codes). Football only gets 1x2 (MRES) on the list page.
    # Handball/tennis/volleyball may also miss spread/total on list pages.
    DETAIL_SPORTS = {"football", "handball", "tennis", "volleyball", "mma"}

    # Max competitions to scrape per sport (football can have 100+ but most are tiny leagues)
    MAX_COMPETITIONS_PER_SPORT = 60

    # Per-sport caps to keep total extraction under provider_timeout.
    # Each comp needs ~4-5s (page nav + DOM render + parse) with Semaphore(4).
    # Football was 60 but timed out at 600s in 4/5 runs — reduced to 30
    # (top competitions discovered first, covers ~90% of Pinnacle matches).
    SPORT_COMPETITION_CAPS: Dict[str, int] = {
        "football": 60,     # No provider timeout — extract all valuable competitions
        "basketball": 50,
        "ice_hockey": 40,
        "tennis": 35,
        "handball": 30,
        "mma": 15,
        "esports": 15,
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.10bet.se")

    def _get_sport_url(self, sport: str) -> str:
        """Get 10Bet competitions listing URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sports/{sport_slug}/competitions"

    async def extract(self, sport: str, limit: int = 1000, **kwargs) -> List[StandardEvent]:
        """Extract events for a sport by discovering and scraping competitions."""
        import time as _time
        extract_start = _time.time()

        # Initialize session
        await self._ensure_init(url=f"{self.site_url}/sports", page_key="sports_home")

        # Handle cookie consent on first visit
        await self._handle_cookie_consent()

        sport_slug = self.SPORT_SLUGS.get(sport)
        if not sport_slug:
            logger.warning(f"[{self.provider_id}] Unknown sport slug for {sport}")
            return []

        # Discover competition IDs
        competitions = await self._discover_competitions(sport_slug)
        if not competitions:
            logger.info(f"[{self.provider_id}] No competitions found for {sport}")
            return []

        # Cap competitions to avoid timeout (use per-sport cap if available)
        cap = self.SPORT_COMPETITION_CAPS.get(sport, self.MAX_COMPETITIONS_PER_SPORT)
        if len(competitions) > cap:
            logger.info(
                f"[{self.provider_id}] Capping {sport} from {len(competitions)} to "
                f"{cap} competitions"
            )
            competitions = competitions[:cap]

        logger.info(f"[{self.provider_id}] Found {len(competitions)} competitions for {sport}")

        # Scrape competitions in batches to allow early exit
        all_events = []
        unique_ids = set()
        sem = asyncio.Semaphore(4)  # 4 parallel tabs (6 caused browser pressure, slow DOM renders)
        batch_size = 15
        sport_timeout = self.config.get("sport_timeout", 600)

        async def process_competition(comp):
            async with sem:
                return await self._scrape_competition(comp, sport)

        for batch_start in range(0, len(competitions), batch_size):
            if len(all_events) >= limit:
                break

            # Time-budget check: stop if we've used 70% of sport timeout
            elapsed = _time.time() - extract_start
            if elapsed > sport_timeout * 0.70:
                logger.warning(
                    f"[{self.provider_id}] {sport}: time-budget exit at {elapsed:.0f}s "
                    f"({batch_start}/{len(competitions)} comps, {len(all_events)} events)"
                )
                break

            batch = competitions[batch_start:batch_start + batch_size]
            tasks = [process_competition(c) for c in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for res in results:
                for ev in res:
                    if len(all_events) >= limit:
                        break
                    if ev.id not in unique_ids:
                        all_events.append(ev)
                        unique_ids.add(ev.id)

        logger.info(f"[{self.provider_id}] {sport}: {len(all_events)} events extracted in {_time.time() - extract_start:.0f}s")

        # Pass 2: Enrich events with detail page spread/total
        # Skip if competition scraping already consumed most of the timeout
        elapsed = _time.time() - extract_start
        if all_events and sport in self.DETAIL_SPORTS and elapsed < sport_timeout * 0.80:
            detail_count = await self._enrich_events_with_details(all_events, sport)
            logger.info(
                f"[{self.provider_id}] {sport}: enriched {detail_count}/{len(all_events)} with spread/total"
            )
        elif all_events and sport in self.DETAIL_SPORTS:
            logger.warning(
                f"[{self.provider_id}] {sport}: skipping detail enrichment — "
                f"{elapsed:.0f}s already elapsed (budget: {sport_timeout}s)"
            )

        return all_events

    async def _handle_cookie_consent(self):
        """Dismiss cookie consent banner without navigating away."""
        if hasattr(self, '_cookies_handled') and self._cookies_handled:
            return

        try:
            page = self.transport.page
            # Try clicking accept buttons (NOT "OK" which navigates to cookie-settings)
            for selector in [
                'button:has-text("Tillat alla")',
                'button:has-text("Acceptera alla")',
                'button:has-text("Acceptera")',
                'button:has-text("Allow all")',
                '[class*="CookiesRegulation"] button:first-of-type',
            ]:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        await el.click()
                        logger.debug(f"[{self.provider_id}] Cookie consent dismissed via {selector}")
                        self._cookies_handled = True
                        await page.wait_for_timeout(1000)
                        return
                except Exception:
                    pass

            # Fallback: set cookie manually
            await self.transport.context.add_cookies([{
                "name": "cookie_consent",
                "value": "accepted",
                "domain": ".10bet.se",
                "path": "/",
            }])
            self._cookies_handled = True
            logger.debug(f"[{self.provider_id}] Cookie consent set via cookie injection")

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Cookie handling: {e}")
            self._cookies_handled = True  # Don't retry

    async def _discover_competitions(self, sport_slug: str) -> List[Dict]:
        """
        Discover competition IDs by navigating to the sport's competitions page.

        Returns list of {id, name} dicts.
        Uses retry with increasing wait times for headed mode SPA rendering.
        """
        url = f"{self.site_url}/sports/{sport_slug}/competitions"
        page = self.transport.page

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # Try to wait for competition links to appear
            try:
                await page.wait_for_selector(
                    'a[href*="competitions/"]', timeout=10000
                )
            except Exception:
                await page.wait_for_timeout(3000)

            competitions = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="competitions/"]');
                return Array.from(links)
                    .map(a => ({
                        text: a.textContent.trim(),
                        href: a.getAttribute('href')
                    }))
                    .filter(l => l.href && /\\/competitions\\/\\d+/.test(l.href))
                    .filter((v, i, a) => a.findIndex(x => x.href === v.href) === i)
                    .map(l => {
                        const match = l.href.match(/\\/competitions\\/(\\d+)/);
                        return {
                            id: match ? match[1] : null,
                            name: l.text
                        };
                    })
                    .filter(c => c.id);
            }""")

            # If no competitions found, retry once with shorter wait
            if not competitions:
                logger.debug(f"[{self.provider_id}] No competitions on first try for {sport_slug}, retrying...")
                await page.wait_for_timeout(1500)
                competitions = await page.evaluate("""() => {
                    const links = document.querySelectorAll('a[href*="competitions/"]');
                    return Array.from(links)
                        .map(a => ({
                            text: a.textContent.trim(),
                            href: a.getAttribute('href')
                        }))
                        .filter(l => l.href && /\\/competitions\\/\\d+/.test(l.href))
                        .filter((v, i, a) => a.findIndex(x => x.href === v.href) === i)
                        .map(l => {
                            const match = l.href.match(/\\/competitions\\/(\\d+)/);
                            return {
                                id: match ? match[1] : null,
                                name: l.text
                            };
                        })
                        .filter(c => c.id);
                }""")

            return competitions

        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to discover competitions for {sport_slug}: {e}")
            return []

    async def _scrape_competition(self, comp: Dict, sport: str) -> List[StandardEvent]:
        """Scrape all events from a single competition's matches page.

        Also captures XHR/fetch API requests made by the Playtech SPA for
        potential direct API extraction (logged at debug level for discovery).
        """
        comp_id = comp['id']
        comp_name = comp.get('name', f'competition-{comp_id}')
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        url = f"{self.site_url}/sports/{sport_slug}/competitions/{comp_id}/matches"
        events = []

        try:
            await self.transport._ensure_browser()
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to ensure browser for {comp_name}: {e}")
            return []

        page = await self.transport.new_page()

        # Capture API requests for potential direct extraction
        api_urls_seen = set()
        def _on_response(response):
            req_url = response.url
            if any(p in req_url for p in ('/api/', '/graphql', '/sportsbook', '/sb/', '/odds')):
                if req_url not in api_urls_seen:
                    api_urls_seen.add(req_url)
                    logger.debug(f"[{self.provider_id}] API intercept: {response.status} {req_url[:150]}")
        page.on("response", _on_response)

        try:
            logger.debug(f"[{self.provider_id}] Scraping {comp_name} ({url})")
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)

            # Wait for event items to render (single attempt, longer timeout)
            events_loaded = False
            try:
                await page.wait_for_selector('[class*="ta-EventListItem"]', timeout=15000)
                events_loaded = True
            except Exception:
                # Check for empty state
                empty = await page.query_selector_all('text=/Inga matcher|Inga evenemang|No matches|No events/i')
                if empty:
                    logger.debug(f"[{self.provider_id}] No matches for {comp_name}")
                    return []
                logger.debug(f"[{self.provider_id}] No EventListItem found for {comp_name}")

            if not events_loaded:
                return []

            # Wait for odds to render (selector-based, faster than fixed timeout)
            try:
                await page.wait_for_selector('[class*="ta-price_text"]', timeout=2000)
            except Exception:
                pass  # Some pages may not have odds yet — proceed with what's available

            # Scrape event data from DOM
            scraped = await page.evaluate("""() => {
                const items = document.querySelectorAll('[class*="ta-EventListItem"]');
                return Array.from(items).map(item => {
                    // Participants
                    const participants = Array.from(
                        item.querySelectorAll('[class*="ta-participantName"]')
                    ).map(p => p.textContent.trim());

                    // Timing — find div containing time pattern (HH:MM)
                    let timing = '';
                    const timingEl = item.querySelector(
                        '[class*="ta-EventTimingStatus"], [class*="Timing"]'
                    );
                    if (timingEl) {
                        timing = timingEl.textContent.trim();
                    } else {
                        // Fallback: search for small div with time pattern
                        const divs = item.querySelectorAll('div');
                        for (const d of divs) {
                            const t = d.textContent.trim();
                            if (t.length < 25 && /\\d{1,2}:\\d{2}/.test(t) && d.children.length === 0) {
                                timing = t;
                                break;
                            }
                        }
                    }

                    // Live indicator
                    const isLive = !!(
                        item.querySelector('[class*="live"]') ||
                        item.querySelector('[class*="Live"]') ||
                        /LIVE|\\d+'/.test(timing)
                    );

                    // Markets (by ta-MarketType-* class)
                    const markets = [];
                    item.querySelectorAll('[class*="ta-MarketType-"]').forEach(m => {
                        const cls = Array.from(m.classList).find(c => c.startsWith('ta-MarketType-'));
                        const marketType = cls ? cls.replace('ta-MarketType-', '') : 'unknown';

                        // Odds
                        const prices = Array.from(
                            m.querySelectorAll('[class*="ta-price_text"]')
                        ).map(p => p.textContent.trim());

                        // Point values (spread/total) from ta-infoText
                        const infoTexts = Array.from(
                            m.querySelectorAll('[class*="ta-infoText"]')
                        ).map(t => t.textContent.trim());

                        // Fallback: capture selection label text (e.g., "1 (0:1)" for HCMR)
                        // and market header text for embedded point values
                        const selLabels = Array.from(
                            m.querySelectorAll('[class*="ta-selection"], [class*="ta-label"], [class*="Label"]')
                        ).map(t => t.textContent.trim());

                        markets.push({ type: marketType, prices, infoTexts, selLabels });
                    });

                    // Event link (for unique ID)
                    const link = item.querySelector('a[href*="/events/"]');
                    const href = link ? link.getAttribute('href') : '';

                    return { participants, timing, isLive, markets, href };
                });
            }""")

            logger.debug(f"[{self.provider_id}] Scraped {len(scraped)} items from {comp_name}")

            for item in scraped:
                ev = self._parse_event(item, sport, comp_name, url, comp_id)
                if ev:
                    events.append(ev)

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error scraping {comp_name}: {e}")
        finally:
            await page.close()

        return events

    def _parse_event(
        self, item: Dict, sport: str, league: str, page_url: str, comp_id: str
    ) -> Optional[StandardEvent]:
        """Parse a single scraped DOM item into a StandardEvent."""
        participants = item.get('participants', [])
        if len(participants) != 2:
            # Exactly 2 participants expected; skip container elements with many
            # participants and items with fewer than 2
            return None

        # Skip live events
        if item.get('isLive'):
            return None

        home_raw = participants[0]
        away_raw = participants[1]
        home = normalize_team_name(home_raw)
        away = normalize_team_name(away_raw)

        if not home or not away:
            return None

        # Parse markets (dedup winner markets only; allow multiple spread/total lines)
        all_markets = []
        seen_winner_types = set()

        for market_data in item.get('markets', []):
            market_type = market_data.get('type', '')
            prices = market_data.get('prices', [])
            info_texts = market_data.get('infoTexts', [])
            sel_labels = market_data.get('selLabels', [])

            parsed_market = self._parse_market(
                market_type, prices, info_texts, home_raw, away_raw,
                sel_labels=sel_labels,
            )
            if not parsed_market:
                continue
            ptype = parsed_market['type']
            # Dedup only winner markets (1x2/moneyline); allow multiple spread/total lines
            if ptype in ("1x2", "moneyline"):
                if ptype in seen_winner_types:
                    continue
                seen_winner_types.add(ptype)
            all_markets.append(parsed_market)

        if not all_markets:
            return None

        # Build event ID from href or participants
        href = item.get('href', '')
        event_id_match = re.search(r'/events/(\d+)', href)
        if event_id_match:
            ev_id = f"10bet-{event_id_match.group(1)}"
        else:
            ev_id = f"10bet-{home}-{away}-{comp_id}"

        start_time = self._parse_time(item.get('timing', ''))

        return StandardEvent(
            id=ev_id,
            name=f"{home} vs {away}",
            sport=sport,
            league=league,
            markets=all_markets,
            provider="10bet",
            home_team=home,
            away_team=away,
            start_time=start_time.isoformat(),
            url=page_url,
        )

    def _parse_market(
        self,
        market_type: str,
        prices: List[str],
        info_texts: List[str],
        home_raw: str,
        away_raw: str,
        sel_labels: Optional[List[str]] = None,
    ) -> Optional[Dict]:
        """
        Parse a market from DOM data using the MARKET_TYPE_MAP lookup.

        Market type codes vary by sport:
          Football/Handball: MRES (1x2), HCTG (total), HCMR (spread)
          Basketball:        H2HT (moneyline), TPOT (total), HCOT (spread)
          Ice Hockey:        H2HT (moneyline), OUTG (total), HCOT (spread)
          American Football: H2HT (moneyline), FTPO (total), FHOT (spread)
          Tennis/MMA:        HTOH (moneyline), TGHC (spread)
        """
        if not prices:
            return None

        canonical = MARKET_TYPE_MAP.get(market_type)
        if not canonical:
            if market_type and market_type != "undefined":
                logger.debug(f"[{self.provider_id}] Unknown DOM market type: '{market_type}' prices={prices[:2]} info={info_texts[:2]}")
            return None

        # Merge info_texts + sel_labels for point extraction fallback
        all_info = list(info_texts)
        if sel_labels:
            all_info.extend(sel_labels)

        try:
            if canonical == "1x2":
                return self._parse_1x2(prices)
            elif canonical == "moneyline":
                return self._parse_moneyline(prices)
            elif canonical == "total":
                return self._parse_total(prices, all_info)
            elif canonical == "spread":
                return self._parse_spread(prices, all_info)
        except (ValueError, IndexError) as e:
            logger.debug(f"[{self.provider_id}] Failed to parse {market_type} ({canonical}): {e}")

        return None

    def _parse_1x2(self, prices: List[str]) -> Optional[Dict]:
        """Parse 1x2 market (3-way: home/draw/away)."""
        parsed = self._parse_prices(prices)
        if not parsed or len(parsed) < 3:
            return None

        return {
            "name": "1x2",
            "type": "1x2",
            "outcomes": [
                {"name": "1", "odds": parsed[0], "side": "home"},
                {"name": "X", "odds": parsed[1], "side": "draw"},
                {"name": "2", "odds": parsed[2], "side": "away"},
            ],
        }

    def _parse_moneyline(self, prices: List[str]) -> Optional[Dict]:
        """Parse moneyline market (2-way: home/away)."""
        parsed = self._parse_prices(prices)
        if not parsed or len(parsed) < 2:
            return None

        return {
            "name": "moneyline",
            "type": "moneyline",
            "outcomes": [
                {"name": "1", "odds": parsed[0], "side": "home"},
                {"name": "2", "odds": parsed[1], "side": "away"},
            ],
        }

    def _parse_total(self, prices: List[str], info_texts: List[str]) -> Optional[Dict]:
        """Parse over/under total market."""
        parsed = self._parse_prices(prices)
        if not parsed or len(parsed) < 2:
            return None

        # Extract point value from info texts
        point = self._extract_point_value(info_texts)
        if point is None:
            logger.debug(f"[{self.provider_id}] Total market missing point: prices={prices} info_texts={info_texts}")
            return None

        return {
            "name": "total",
            "type": "total",
            "point": point,
            "outcomes": [
                {"name": f"Over {point}", "odds": parsed[0], "side": "over", "point": point},
                {"name": f"Under {point}", "odds": parsed[1], "side": "under", "point": point},
            ],
        }

    def _parse_spread(self, prices: List[str], info_texts: List[str]) -> Optional[Dict]:
        """Parse handicap/spread market (2-way or 3-way)."""
        parsed = self._parse_prices(prices)
        if not parsed or len(parsed) < 2:
            return None

        # Extract point value from info texts (includes selLabels)
        # First try the structured "(0:1)" or "(+1.5)" format from labels
        point = self._extract_point_from_prices(info_texts)

        # Then try plain number extraction from info texts
        if point is None:
            point = self._extract_point_value(info_texts)

        # Fallback: try extracting point from price label text itself
        if point is None:
            point = self._extract_point_from_prices(prices)

        if point is None:
            logger.debug(f"[{self.provider_id}] Spread market missing point: prices={prices} info_texts={info_texts}")
            return None

        # 3-way handicap (HCMR) has home/draw/away -- take first and last
        # 2-way handicap (HCOT, FHOT, TGHC) has home/away
        return {
            "name": "spread",
            "type": "spread",
            "point": point,
            "outcomes": [
                {"name": f"Home {point:+g}", "odds": parsed[0], "side": "home", "point": point},
                {"name": f"Away {-point:+g}", "odds": parsed[-1], "side": "away", "point": -point},
            ],
        }

    def _parse_prices(self, prices: List[str]) -> Optional[List[float]]:
        """Parse price strings to floats, filtering invalid values."""
        result = []
        for p in prices:
            try:
                val = float(p.replace(',', '.').strip())
                if val <= 1.0:
                    return None  # Invalid odds
                result.append(val)
            except (ValueError, AttributeError):
                return None
        return result if result else None

    def _extract_point_value(self, info_texts: List[str]) -> Optional[float]:
        """Extract point value (spread/total) from info text elements."""
        for text in info_texts:
            # Try to extract a number like "2.5", "+1.5", "-0.5"
            match = re.search(r'([+-]?\d+\.?\d*)', text.replace(',', '.'))
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    pass
        return None

    def _extract_point_from_prices(self, prices: List[str]) -> Optional[float]:
        """Fallback: extract point value from price label text.

        Football HCMR embeds handicap in labels like "1 (0:1)" or "(+1.5)".
        """
        for p in prices:
            # Match "(0:1)" format → goal handicap (European notation)
            m = re.search(r'\((\d+):(\d+)\)', p)
            if m:
                home_goals, away_goals = int(m.group(1)), int(m.group(2))
                return float(home_goals - away_goals)
            # Match "(+1.5)" or "(-0.5)" embedded in label
            m = re.search(r'\(([+-]?\d+\.?\d*)\)', p)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
        return None

    def _parse_time(self, time_str: str) -> datetime:
        """Parse 10bet time formats (Swedish locale)."""
        now = datetime.now()
        if not time_str:
            return now

        ts = time_str.lower().strip()

        # "13:30" — today
        if re.match(r'^\d{1,2}:\d{2}$', ts):
            try:
                h, m = map(int, ts.split(':'))
                return now.replace(hour=h, minute=m, second=0, microsecond=0)
            except ValueError:
                return now

        # "idag 13:30"
        if 'idag' in ts:
            match = re.search(r'(\d{1,2}:\d{2})', ts)
            if match:
                try:
                    h, m = map(int, match.group(1).split(':'))
                    return now.replace(hour=h, minute=m, second=0, microsecond=0)
                except ValueError:
                    return now

        # "imorgon 13:30"
        if 'imorgon' in ts:
            match = re.search(r'(\d{1,2}:\d{2})', ts)
            if match:
                try:
                    tomorrow = now + timedelta(days=1)
                    h, m = map(int, match.group(1).split(':'))
                    return tomorrow.replace(hour=h, minute=m, second=0, microsecond=0)
                except ValueError:
                    return now

        # "lör 24 jan 13:30" or "24 jan. 13:30"
        months = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'maj': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'okt': 10, 'nov': 11, 'dec': 12,
        }
        match = re.search(r'(\d{1,2})\s+([a-zåäö]{3})', ts)
        if match:
            try:
                day = int(match.group(1))
                month = months.get(match.group(2), now.month)
                time_match = re.search(r'(\d{1,2}:\d{2})', ts)
                h, m = (0, 0)
                if time_match:
                    h, m = map(int, time_match.group(1).split(':'))
                year = now.year
                if month < now.month and now.month == 12:
                    year += 1
                return datetime(year, month, day, h, m)
            except (ValueError, TypeError):
                return now

        # "15/02 13:30" or "15/02/2026 13:30"
        match = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*(\d{1,2}:\d{2})?', ts)
        if match:
            try:
                day = int(match.group(1))
                month = int(match.group(2))
                year = int(match.group(3)) if match.group(3) else now.year
                if year < 100:
                    year += 2000
                h, m = (0, 0)
                if match.group(4):
                    h, m = map(int, match.group(4).split(':'))
                return datetime(year, month, day, h, m)
            except (ValueError, TypeError):
                return now

        return now

    def _parse_detail_spread(self, raw: dict) -> Optional[Dict]:
        """Parse Asian Handicap from event detail JS output."""
        outcomes = []
        has_point = False
        for o in raw.get("outcomes", []):
            odds_str = o.get("odds", "")
            point_str = o.get("point", "")
            try:
                odds = float(odds_str.replace(",", "."))
                if odds <= 1.0:
                    continue
            except (ValueError, TypeError):
                continue

            # Extract point from point field
            try:
                point = float(point_str.replace(",", "."))
                has_point = True
            except (ValueError, TypeError):
                continue

            # Determine side from position (first = home, second = away)
            side = "home" if len(outcomes) == 0 else "away"
            outcomes.append({"name": side, "odds": odds, "point": point})

        if len(outcomes) < 2 or not has_point:
            return None
        return {"type": "spread", "outcomes": outcomes}

    def _parse_detail_total(self, raw: dict) -> Optional[Dict]:
        """Parse Over/Under total from event detail JS output."""
        outcomes = []
        for o in raw.get("outcomes", []):
            odds_str = o.get("odds", "")
            name = o.get("name", "").strip()
            try:
                odds = float(odds_str.replace(",", "."))
                if odds <= 1.0:
                    continue
            except (ValueError, TypeError):
                continue

            name_lower = name.lower()
            if name_lower.startswith("over") or name_lower.startswith("över"):
                side = "over"
            elif name_lower.startswith("under"):
                side = "under"
            else:
                side = "over" if len(outcomes) == 0 else "under"

            # Extract point from name: "Over 2.5" -> 2.5
            m = re.search(r'(\d+\.?\d*)', name)
            point = float(m.group(1)) if m else None

            outcome = {"name": side, "odds": odds}
            if point is not None:
                outcome["point"] = point
            outcomes.append(outcome)

        if len(outcomes) < 2:
            return None
        return {"type": "total", "outcomes": outcomes}

    MAX_DETAIL_EVENTS = 300  # No provider timeout — enrich all events

    async def _enrich_events_with_details(
        self, events: List[StandardEvent], sport: str
    ) -> int:
        """Navigate to event detail pages to extract Asian Handicap + Asian Total."""
        # Only enrich events that have href-derived IDs
        todo = [(ev, ev.id) for ev in events if ev.id.startswith("10bet-")]
        if not todo:
            return 0

        if len(todo) > self.MAX_DETAIL_EVENTS:
            logger.info(f"[{self.provider_id}] Capping detail enrichment from {len(todo)} to {self.MAX_DETAIL_EVENTS}")
            todo = todo[:self.MAX_DETAIL_EVENTS]

        enriched = 0
        errors = 0
        sem = asyncio.Semaphore(4)

        # Create page pool
        await self.transport._ensure_browser()
        context = self.transport.page.context
        extra_pages = []
        for _ in range(3):  # 3 extra + main = 4 total
            try:
                p = await context.new_page()
                extra_pages.append(p)
            except Exception:
                break
        page_pool = asyncio.Queue()
        for p in [self.transport.page] + extra_pages:
            await page_pool.put(p)

        sport_slug = self.SPORT_SLUGS.get(sport, sport)

        async def enrich_one(event, event_id):
            nonlocal enriched, errors
            if errors > 30:
                return

            # Extract numeric ID from "10bet-{id}"
            numeric_id = event_id.replace("10bet-", "")
            if not numeric_id.isdigit():
                return

            worker_page = await page_pool.get()
            try:
                async with sem:
                    url = f"{self.site_url}/sports/{sport_slug}/events/{numeric_id}"
                    try:
                        await worker_page.goto(url, wait_until="domcontentloaded", timeout=10000)
                        # Wait briefly for markets to render
                        await worker_page.wait_for_timeout(1500)
                    except Exception as e:
                        errors += 1
                        return

                    detail = await worker_page.evaluate(JS_EXTRACT_DETAIL_MARKETS)

                    added = False
                    if detail.get("spread"):
                        spread = self._parse_detail_spread(detail["spread"])
                        if spread:
                            # Only add if event doesn't already have a spread
                            has_spread = any(m.get("type") == "spread" for m in event.markets)
                            if not has_spread:
                                event.markets.append(spread)
                                added = True

                    if detail.get("total"):
                        total = self._parse_detail_total(detail["total"])
                        if total:
                            has_total = any(m.get("type") == "total" for m in event.markets)
                            if not has_total:
                                event.markets.append(total)
                                added = True

                    if added:
                        enriched += 1
            except Exception as e:
                errors += 1
                logger.debug(f"[{self.provider_id}] Detail enrichment error for {event_id}: {e}")
            finally:
                await page_pool.put(worker_page)

        await asyncio.gather(*(enrich_one(ev, eid) for ev, eid in todo), return_exceptions=True)

        # Close extra pages
        for p in extra_pages:
            try:
                await p.close()
            except Exception:
                pass

        return enriched

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — extract() is overridden."""
        raise NotImplementedError("TenBetRetriever uses extract() directly")
