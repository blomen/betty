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

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.10bet.se")

    def _get_sport_url(self, sport: str) -> str:
        """Get 10Bet competitions listing URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sports/{sport_slug}/competitions"

    async def extract(self, sport: str, limit: int = 1000, **kwargs) -> List[StandardEvent]:
        """Extract events for a sport by discovering and scraping competitions."""
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

        logger.info(f"[{self.provider_id}] Found {len(competitions)} competitions for {sport}")

        # Scrape competitions in batches to allow early exit
        all_events = []
        unique_ids = set()
        sem = asyncio.Semaphore(5)  # 5 parallel tabs (tested: SPA handles concurrency well)
        batch_size = 10

        async def process_competition(comp):
            async with sem:
                return await self._scrape_competition(comp, sport)

        for batch_start in range(0, len(competitions), batch_size):
            if len(all_events) >= limit:
                break

            batch = competitions[batch_start:batch_start + batch_size]
            tasks = [process_competition(c) for c in batch]
            results = await asyncio.gather(*tasks)

            for res in results:
                for ev in res:
                    if len(all_events) >= limit:
                        break
                    if ev.id not in unique_ids:
                        all_events.append(ev)
                        unique_ids.add(ev.id)

        logger.info(f"[{self.provider_id}] {sport}: {len(all_events)} events extracted")
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
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Try to wait for competition links to appear
            try:
                await page.wait_for_selector(
                    'a[href*="competitions/"]', timeout=8000
                )
            except Exception:
                # SPA might need more time in headed mode
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

            # If no competitions found, retry once with longer wait
            if not competitions:
                logger.debug(f"[{self.provider_id}] No competitions on first try for {sport_slug}, retrying...")
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

            return competitions

        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to discover competitions for {sport_slug}: {e}")
            return []

    async def _scrape_competition(self, comp: Dict, sport: str) -> List[StandardEvent]:
        """Scrape all events from a single competition's matches page."""
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

        try:
            logger.debug(f"[{self.provider_id}] Scraping {comp_name} ({url})")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for event items to render (SPA widget needs time — headed mode slower)
            try:
                await page.wait_for_selector('[class*="ta-EventListItem"]', timeout=15000)
            except Exception:
                # Check for empty state
                empty = await page.query_selector_all('text=/Inga matcher|Inga evenemang|No matches|No events/i')
                if empty:
                    logger.debug(f"[{self.provider_id}] No matches for {comp_name}")
                else:
                    logger.debug(f"[{self.provider_id}] No EventListItem found for {comp_name}")
                return []

            # Brief pause for remaining odds to load
            await page.wait_for_timeout(500)

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

                        // Point values (spread/total)
                        const infoTexts = Array.from(
                            m.querySelectorAll('[class*="ta-infoText"]')
                        ).map(t => t.textContent.trim());

                        markets.push({ type: marketType, prices, infoTexts });
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

        # Parse markets (dedup: only first market per type)
        all_markets = []
        seen_types = set()

        for market_data in item.get('markets', []):
            market_type = market_data.get('type', '')
            prices = market_data.get('prices', [])
            info_texts = market_data.get('infoTexts', [])

            parsed_market = self._parse_market(
                market_type, prices, info_texts, home_raw, away_raw
            )
            if parsed_market and parsed_market['type'] not in seen_types:
                all_markets.append(parsed_market)
                seen_types.add(parsed_market['type'])

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
            # Unknown/unsupported market type (e.g. "undefined")
            return None

        try:
            if canonical == "1x2":
                return self._parse_1x2(prices)
            elif canonical == "moneyline":
                return self._parse_moneyline(prices)
            elif canonical == "total":
                return self._parse_total(prices, info_texts)
            elif canonical == "spread":
                return self._parse_spread(prices, info_texts)
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
            return None

        return {
            "name": "total",
            "type": "total",
            "point": point,
            "outcomes": [
                {"name": f"Over {point}", "odds": parsed[0], "side": "over"},
                {"name": f"Under {point}", "odds": parsed[1], "side": "under"},
            ],
        }

    def _parse_spread(self, prices: List[str], info_texts: List[str]) -> Optional[Dict]:
        """Parse handicap/spread market (2-way or 3-way)."""
        parsed = self._parse_prices(prices)
        if not parsed or len(parsed) < 2:
            return None

        # Extract point value from info texts
        point = self._extract_point_value(info_texts)
        if point is None:
            return None

        # 3-way handicap (HCMR) has home/draw/away -- take first and last
        # 2-way handicap (HCOT, FHOT, TGHC) has home/away
        return {
            "name": "spread",
            "type": "spread",
            "point": point,
            "outcomes": [
                {"name": f"Home {point:+g}", "odds": parsed[0], "side": "home"},
                {"name": f"Away {-point:+g}", "odds": parsed[-1], "side": "away"},
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

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — extract() is overridden."""
        raise NotImplementedError("TenBetRetriever uses extract() directly")
