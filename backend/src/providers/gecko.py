from typing import List, Any, Optional, Dict, Set
import logging
import re
import json
import asyncio
from datetime import datetime, timedelta
from ..core import BrowserRetriever, StandardEvent, BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

class GeckoRetriever(BrowserRetriever):
    """
    Retriever for Betsson Group sites (Betsson, Betsafe, NordicBet, ComeOn).
    These sites use the Gecko sportsbook platform built on BetRadar/Sportradar.

    Uses HTML parsing from rendered DOM (API endpoints don't return fixture data).

    Subclasses can override SELECTORS dict to customize for different site structures.
    """

    SPORT_SLUGS: Dict[str, str] = {
        "football": "fotboll",
        "basketball": "basket",
        "tennis": "tennis",
        "ice_hockey": "ishockey",
        "american_football": "amerikansk-fotboll",
        "baseball": "baseboll",
        "mma": "mma",
        "esports": "esports",
        "rugby": "rugby",
        "cricket": "cricket",
        "boxing": "boxning",
        "handball": "handboll",
    }

    # CSS selectors for HTML parsing (can be overridden by subclasses)
    SELECTORS: Dict[str, str] = {
        # Main fixture containers
        "fixture_list": "article, [data-test-id*='event'], [class*='event'], [class*='fixture'], [class*='match']",
        "fixture_card": "article, .event-row, .fixture-card, .match-card",

        # Team names
        "home_team": "[class*='home'] [class*='team'], [data-test-id*='home'], .home-team, .team-home",
        "away_team": "[class*='away'] [class*='team'], [data-test-id*='away'], .away-team, .team-away",
        "team_name": "[class*='team-name'], [class*='participant'], .team, .participant",

        # Market containers
        "markets": "[class*='market'], [class*='odds'], [data-test-id*='market']",
        "market_buttons": "button, a, [class*='selection'], [class*='outcome']",

        # Odds values
        "odds_value": "[class*='odds'], [class*='price'], [data-odds]",

        # Time
        "start_time": "time, [class*='time'], [class*='date'], [datetime]",
    }

    # Gecko category IDs for sports
    CATEGORY_IDS: Dict[str, str] = {
        "football": "1",
        "basketball": "2",
        "tennis": "3",
        "ice_hockey": "4",
        "american_football": "5",
        "baseball": "6",
        "handball": "7",
    }

    # Market type mapping (Gecko -> Standard)
    MARKET_TYPE_MAP: Dict[str, str] = {
        # Moneyline/1X2 markets
        "FTCS": "1x2",  # Full Time Correct Score -> 1X2
        "FTR": "1x2",   # Full Time Result
        "ML": "moneyline",  # Moneyline (2-way)
        "MW": "moneyline",  # Match Winner

        # Totals (Over/Under)
        "OU": "over_under",  # Over/Under
        "TG": "over_under",  # Total Goals
        "TP": "over_under",  # Total Points

        # Spreads (Handicaps)
        "HC": "spread",  # Handicap
        "AH": "spread",  # Asian Handicap
        "PS": "spread",  # Point Spread
    }

    # Outcome mapping (Gecko -> Standard)
    OUTCOME_MAP: Dict[str, str] = {
        # 1X2 outcomes
        "1": "home",
        "X": "draw",
        "2": "away",
        "home": "home",
        "draw": "draw",
        "away": "away",

        # Moneyline outcomes
        "Home": "home",
        "Away": "away",

        # Over/Under outcomes
        "over": "over",
        "under": "under",
        "Over": "over",
        "Under": "under",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

        # Ensure site_url is clean (no trailing slash)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', 'betsson.com')}")
        self.site_url: str = raw_site_url.rstrip("/")

        # Gecko API endpoints
        self.api_base: str = f"{self.site_url}/api/sb/v1"
        self.fe_api_base: str = f"{self.site_url}/sb/fe-api"

        # Cache for API responses
        self._competitions_cache: Dict[str, Dict] = {}
        self._competitions_cache_time: Dict[str, datetime] = {}
        self._cache_ttl: int = 300  # 5 minutes

    async def _ensure_sport_init(self, sport: str) -> None:
        """No special initialization needed - handled by browser."""
        pass

    def _get_sport_url(self, sport: str) -> str:
        """Get the sportsbook URL for a given sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sv/odds/{sport_slug}"

    async def _handle_cookie_consent(self, page):
        """
        Handle cookie consent dialogs on Gecko platform sites.
        Tries multiple common button selectors for Swedish/English sites.
        """
        cookie_selectors = [
            # Swedish variations
            'button:has-text("Acceptera alla")',
            'button:has-text("Acceptera")',
            'button:has-text("Godkänn alla")',
            'button:has-text("Godkänn")',
            'button:has-text("Jag godkänner")',
            # English variations
            'button:has-text("Accept all")',
            'button:has-text("Accept")',
            'button:has-text("I accept")',
            'button:has-text("Agree")',
            # By ID/class (common patterns)
            '#accept-cookies',
            '#acceptCookies',
            '.accept-cookies',
            '[id*="cookie"][id*="accept"]',
            '[class*="cookie"][class*="accept"]',
            '[data-test-id*="cookie"][data-test-id*="accept"]',
        ]

        for selector in cookie_selectors:
            try:
                # Try to click the cookie consent button
                await page.click(selector, timeout=2000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent: {selector}")
                await asyncio.sleep(1)
                return  # Success, exit
            except Exception:
                continue  # Try next selector

        logger.debug(f"[{self.provider_id}] No cookie consent button found (may not be needed)")

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """
        Parse method (required by abstract base class).
        Not used since we override extract() completely.
        """
        return []

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """
        Extract events for a sport by parsing HTML from rendered page.

        Gecko platform sites don't expose fixture data via APIs,
        so we parse it directly from the DOM after page render.
        """
        if sport not in self.SPORT_SLUGS:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        # Check cache first
        cache_key = f"{sport}"
        if cache_key in self._competitions_cache_time:
            cache_age = (datetime.now() - self._competitions_cache_time[cache_key]).total_seconds()
            if cache_age < self._cache_ttl:
                logger.debug(f"[{self.provider_id}] Using cached data for {sport}")
                # Cache stores parsed events directly
                return self._competitions_cache[cache_key][:limit]

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] GeckoRetriever requires BrowserTransport")
                return []

            # Ensure browser is initialized
            await self.transport._ensure_browser()
            page = self.transport.page

            # Navigate to sport page
            sport_url = self._get_sport_url(sport)
            logger.info(f"[{self.provider_id}] Navigating to {sport_url}")
            await page.goto(sport_url, wait_until='networkidle', timeout=60000)

            # Handle cookie consent (try multiple Swedish/English variations)
            await self._handle_cookie_consent(page)

            # Wait for fixtures to load
            logger.info(f"[{self.provider_id}] Waiting for fixtures to render...")
            await asyncio.sleep(8)

            # Check page title
            page_title = await page.title()
            logger.info(f"[{self.provider_id}] Page title: {page_title}")

            # Try to wait for fixture elements
            try:
                await page.wait_for_selector('article, [class*="event"], button', timeout=15000)
                logger.info(f"[{self.provider_id}] Found elements on page")
            except Exception as e:
                logger.debug(f"[{self.provider_id}] Timeout waiting for elements: {e}")

            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
            await asyncio.sleep(2)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(2)

            # Parse fixtures from HTML
            events = await self._parse_html(page, sport)

            if not events:
                logger.warning(f"[{self.provider_id}] No events extracted from HTML")
                return []

            logger.info(f"[{self.provider_id}] Extracted {len(events)} events from HTML")

            # Cache the parsed events
            self._competitions_cache[cache_key] = events
            self._competitions_cache_time[cache_key] = datetime.now()

            return events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    async def _parse_html(self, page, sport: str) -> List[StandardEvent]:
        """
        Parse fixtures from rendered HTML DOM.

        Extracts team names, odds, markets, and start times using CSS selectors.
        Uses multiple fallback strategies for robustness.
        """
        events = []

        try:
            # First, check if page has loaded content
            article_count = await page.evaluate('() => document.querySelectorAll("article").length')
            logger.info(f"[{self.provider_id}] Page has {article_count} article elements")

            # Get all fixture elements using JavaScript
            fixtures_data = await page.evaluate("""
                () => {
                    // Try multiple selectors to find fixtures
                    const selectors = [
                        'article',
                        '[data-test-id*="event"]',
                        '[data-test-id*="fixture"]',
                        '[class*="EventRow"]',
                        '[class*="FixtureCard"]',
                        '[class*="MatchCard"]'
                    ];

                    let fixtures = [];
                    let usedSelector = '';
                    for (const selector of selectors) {
                        const elements = document.querySelectorAll(selector);
                        if (elements.length > 0) {
                            fixtures = Array.from(elements);
                            usedSelector = selector;
                            break;
                        }
                    }

                    console.log(`Found ${fixtures.length} fixtures using selector: ${usedSelector}`);

                    // Extract data from each fixture
                    return fixtures.slice(0, 100).map((fixture, index) => {
                        // Try to find team names
                        const allText = fixture.innerText || '';
                        const lines = allText.split('\\n').map(l => l.trim()).filter(l => l);

                        // Get all clickable elements (likely outcomes/odds)
                        const buttons = fixture.querySelectorAll('button, a, [role="button"], [class*="selection"], [class*="outcome"]');
                        const odds = Array.from(buttons).map(btn => {
                            const text = btn.innerText || btn.textContent || '';
                            const dataOdds = btn.getAttribute('data-odds') || btn.getAttribute('data-price');

                            // Try to extract odds value (decimal number)
                            const oddsMatch = text.match(/\\d+\\.\\d+/);
                            const oddsValue = dataOdds || (oddsMatch ? oddsMatch[0] : null);

                            return {
                                text: text.trim(),
                                odds: oddsValue,
                                label: btn.getAttribute('aria-label') || '',
                                dataTestId: btn.getAttribute('data-test-id') || ''
                            };
                        }).filter(o => o.odds);

                        // Get time element
                        const timeEl = fixture.querySelector('time');
                        const dateTime = timeEl ? timeEl.getAttribute('datetime') : null;

                        // Get competition/league
                        const headingEl = fixture.querySelector('h2, h3, h4, [class*="competition"], [class*="league"]');
                        const competition = headingEl ? headingEl.innerText.trim() : '';

                        return {
                            index: index,
                            html: fixture.outerHTML.substring(0, 1000),
                            text: allText.substring(0, 500),
                            lines: lines,
                            odds: odds,
                            dateTime: dateTime,
                            competition: competition
                        };
                    });
                }
            """)

            logger.info(f"[{self.provider_id}] JavaScript returned {len(fixtures_data) if fixtures_data else 0} fixtures")

            logger.info(f"[{self.provider_id}] Found {len(fixtures_data)} fixture elements")

            # DEBUG: Save first fixture for inspection
            if fixtures_data:
                try:
                    with open('C:\\Users\\rasmu\\oddopp\\scrap\\betsson_fixture_sample.json', 'w', encoding='utf-8') as f:
                        json.dump(fixtures_data[0], f, indent=2, ensure_ascii=False)
                    logger.debug(f"[{self.provider_id}] Saved sample fixture data")
                except Exception as e:
                    pass

            # Parse each fixture
            for i, fixture_data in enumerate(fixtures_data):
                try:
                    event = self._parse_html_fixture(fixture_data, sport)
                    if event:
                        events.append(event)
                        logger.debug(f"[{self.provider_id}] Parsed event {i+1}: {event.home_team} vs {event.away_team}")
                    else:
                        logger.debug(f"[{self.provider_id}] Fixture {i+1} returned None")
                except Exception as e:
                    logger.warning(f"[{self.provider_id}] Error parsing fixture {i+1}: {e}")
                    continue

            logger.info(f"[{self.provider_id}] Successfully parsed {len(events)} events from {len(fixtures_data)} fixtures")
            return events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error in _parse_html: {e}", exc_info=True)
            return []

    def _parse_html_fixture(self, fixture_data: Dict, sport: str) -> Optional[StandardEvent]:
        """
        Parse a single fixture from extracted HTML data.

        Attempts to identify teams, odds, and markets from text and structure.
        """
        try:
            lines = fixture_data.get('lines', [])
            odds_data = fixture_data.get('odds', [])

            logger.debug(f"[{self.provider_id}] Fixture has {len(lines)} lines and {len(odds_data)} odds")

            if not lines or len(lines) < 2:
                logger.debug(f"[{self.provider_id}] Skipping fixture: insufficient lines ({len(lines)})")
                return None

            # Strategy: Find team names
            # Teams are usually the first 2-3 meaningful text lines
            # Filter out numbers, odds values, time strings
            potential_teams = []
            for line in lines[:10]:  # Check first 10 lines
                # Skip if it's just a number, odds, or time
                if re.match(r'^[\d:\.]+$', line):
                    continue
                if re.match(r'^\d{1,2}:\d{2}$', line):  # Time like "19:00"
                    continue
                if len(line) < 3:  # Too short
                    continue
                # Skip if it looks like a competition name (has keywords)
                if any(kw in line.lower() for kw in ['liga', 'league', 'cup', 'championship', 'division']):
                    continue

                potential_teams.append(line)

                if len(potential_teams) >= 2:
                    break

            if len(potential_teams) < 2:
                logger.debug(f"[{self.provider_id}] Could not find 2 teams in fixture")
                return None

            home_team_raw = potential_teams[0]
            away_team_raw = potential_teams[1]

            # Normalize team names
            home_team = normalize_team_name(home_team_raw)
            away_team = normalize_team_name(away_team_raw)

            if not home_team or not away_team:
                return None

            # Parse start time
            start_time = None
            datetime_str = fixture_data.get('dateTime')
            if datetime_str:
                try:
                    start_time = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
                except Exception:
                    pass

            # Parse markets from odds
            markets_list = self._parse_html_markets(odds_data)

            if not markets_list:
                logger.debug(f"[{self.provider_id}] No valid markets for {home_team} vs {away_team}")
                return None

            # Get competition
            competition = fixture_data.get('competition', 'Unknown')

            return StandardEvent(
                provider_id=self.provider_id,
                sport=sport,
                league=competition,
                home_team=home_team,
                away_team=away_team,
                commence_time=start_time,
                start_time=start_time,
                event_id=f"{home_team}_{away_team}_{datetime.now().strftime('%Y%m%d')}",
                name=f"{home_team_raw} vs {away_team_raw}",
                id=f"{self.provider_id}_{home_team}_{away_team}",
                markets=markets_list,
                url="",
                provider=self.provider_id
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing HTML fixture: {e}")
            return None

    def _parse_html_markets(self, odds_data: List[Dict]) -> List[Dict]:
        """
        Parse markets from odds button data.

        Attempts to identify market types (1x2, over/under, etc.) from button labels.
        """
        markets_list = []

        if not odds_data:
            return markets_list

        # Try to identify 1X2 market (first 3 odds are usually home/draw/away)
        if len(odds_data) >= 3:
            # Check if first 3 odds look like 1X2
            first_three = odds_data[:3]
            labels = [o.get('text', '') for o in first_three]

            # Common 1X2 labels
            if any(re.match(r'^[12X]$', label) for label in labels):
                # This looks like 1X2
                outcomes = []
                outcome_map = {'1': 'home', 'X': 'draw', '2': 'away'}

                for odd in first_three:
                    label = odd.get('text', '').strip()
                    odds_value = odd.get('odds')

                    if label in outcome_map and odds_value:
                        try:
                            outcomes.append({
                                'name': outcome_map[label],
                                'odds': round(float(odds_value), 3)
                            })
                        except (ValueError, TypeError):
                            continue

                if len(outcomes) >= 2:
                    markets_list.append({
                        'type': '1x2',
                        'outcomes': outcomes
                    })

        # Try to find over/under markets
        for odd in odds_data:
            label = odd.get('text', '').lower()
            if 'över' in label or 'over' in label:
                # Extract line value
                line_match = re.search(r'(\d+\.?\d*)', label)
                if line_match:
                    line = float(line_match.group(1))
                    odds_value = odd.get('odds')
                    if odds_value:
                        # Find corresponding under
                        # ... simplified for now
                        pass

        return markets_list

    def _parse_events(self, widgets_response: Dict, sport: str, limit: int) -> List[StandardEvent]:
        """
        Parse events from Gecko widgets/view API response.

        Structure:
        data.widgets[].data.items[].fixtures{fixture_id: {...}}
        """
        events = []

        try:
            data = widgets_response.get('data', {})
            widgets = data.get('widgets', [])

            if not widgets:
                logger.warning(f"[{self.provider_id}] No widgets in response")
                return []

            logger.info(f"[{self.provider_id}] Found {len(widgets)} widgets")

            # Iterate through widgets to find event listings
            for widget in widgets:
                widget_data = widget.get('data', {})
                items = widget_data.get('items', [])

                if not items:
                    continue

                # Each item is typically a competition group
                for item in items:
                    comp_label = item.get('label', 'Unknown')
                    fixtures = item.get('fixtures', {})

                    if not fixtures:
                        continue

                    logger.debug(f"[{self.provider_id}] Competition '{comp_label}': {len(fixtures)} fixtures")

                    # Parse each fixture
                    for fixture_id, fixture_data in fixtures.items():
                        try:
                            event = self._parse_fixture(fixture_id, fixture_data, comp_label, sport)
                            if event:
                                events.append(event)

                                if len(events) >= limit:
                                    break
                        except Exception as e:
                            logger.warning(f"[{self.provider_id}] Error parsing fixture {fixture_id}: {e}")
                            continue

                    if len(events) >= limit:
                        break

                if len(events) >= limit:
                    break

            logger.info(f"[{self.provider_id}] Extracted {len(events)} events for {sport}")
            return events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error parsing events: {e}", exc_info=True)
            return []

    def _parse_fixture(self, fixture_id: str, fixture_data: Dict, competition: str, sport: str) -> Optional[StandardEvent]:
        """
        Parse a single fixture into a StandardEvent.

        Applies normalization, market mapping, and validation.
        """
        try:
            # Extract and normalize team names
            home_team_raw = fixture_data.get('homeTeam', {}).get('name', '')
            away_team_raw = fixture_data.get('awayTeam', {}).get('name', '')

            if not home_team_raw or not away_team_raw:
                logger.debug(f"[{self.provider_id}] Skipping fixture {fixture_id}: missing teams")
                return None

            home_team = normalize_team_name(home_team_raw)
            away_team = normalize_team_name(away_team_raw)

            # Parse start time
            start_time_str = fixture_data.get('startTime', '')
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Could not parse start time: {e}")

            # Extract and parse markets
            raw_markets = fixture_data.get('markets', {})
            markets_list = []

            for market_id, market_data in raw_markets.items():
                market = self._parse_market(market_data)
                if market:
                    markets_list.append(market)

            # Skip event if no valid markets
            if not markets_list:
                logger.debug(f"[{self.provider_id}] Skipping fixture {fixture_id}: no valid markets")
                return None

            return StandardEvent(
                provider_id=self.provider_id,
                sport=sport,
                league=competition,
                home_team=home_team,
                away_team=away_team,
                commence_time=start_time,
                start_time=start_time,
                event_id=fixture_id,
                markets=markets_list,
                raw_data=fixture_data
            )

        except Exception as e:
            logger.warning(f"[{self.provider_id}] Error parsing fixture {fixture_id}: {e}")
            return None

    def _parse_market(self, market_data: Dict) -> Optional[Dict]:
        """
        Parse a market and its selections into a market dict.

        Only extracts priority 1 & 2 markets (1x2, moneyline, over_under, spread).

        Returns dict: {"type": str, "outcomes": List[dict], "line": float|None}
        """
        try:
            # Get market type and map to standard name
            raw_market_type = market_data.get('type', '')
            market_type = self.MARKET_TYPE_MAP.get(raw_market_type)

            if not market_type:
                # Unknown market type - skip it
                logger.debug(f"[{self.provider_id}] Skipping unknown market type: {raw_market_type}")
                return None

            # Extract point value (for spreads/totals)
            point = market_data.get('line') or market_data.get('point') or market_data.get('handicap')

            # Parse selections (outcomes)
            raw_selections = market_data.get('selections', {})
            outcomes = []

            for selection_id, selection_data in raw_selections.items():
                outcome = self._parse_outcome(selection_data)
                if outcome:
                    outcomes.append(outcome)

            # Skip market if no valid outcomes
            if not outcomes:
                logger.debug(f"[{self.provider_id}] Skipping market {market_type}: no valid outcomes")
                return None

            market_dict = {
                "type": market_type,
                "outcomes": outcomes
            }

            # Add line/point if present
            if point is not None:
                market_dict["line"] = float(point)

            return market_dict

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing market: {e}")
            return None

    def _parse_outcome(self, selection_data: Dict) -> Optional[Dict]:
        """
        Parse a selection into an outcome dict.

        Returns dict: {"name": str, "odds": float}
        """
        try:
            # Get outcome label and map to standard name
            raw_label = selection_data.get('label', '')
            outcome_name = self.OUTCOME_MAP.get(raw_label, raw_label.lower())

            # Validate it's a standard outcome
            valid_outcomes = {'home', 'away', 'draw', 'over', 'under'}
            if outcome_name not in valid_outcomes:
                logger.debug(f"[{self.provider_id}] Skipping unknown outcome: {raw_label}")
                return None

            # Get odds value
            odds_value = selection_data.get('odds')
            if not odds_value:
                return None

            # Convert to float and validate
            odds_float = float(odds_value)
            if odds_float <= 1.0:
                logger.debug(f"[{self.provider_id}] Skipping invalid odds: {odds_float}")
                return None

            return {
                "name": outcome_name,
                "odds": round(odds_float, 3)
            }

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing outcome: {e}")
            return None
