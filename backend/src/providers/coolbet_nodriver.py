"""
Coolbet Retriever - nodriver with DOM Extraction

Uses nodriver to bypass Incapsula protection and extracts data directly from DOM.
nodriver has 90-95% success rate against Incapsula/Imperva.
"""

from typing import List, Any, Optional, Dict
import logging
import asyncio
from datetime import datetime
import re

try:
    import nodriver as uc
    NODRIVER_AVAILABLE = True
except ImportError:
    NODRIVER_AVAILABLE = False
    uc = None

from ..core import Retriever, StandardEvent

logger = logging.getLogger(__name__)


class CoolbetNodriverRetriever(Retriever):
    """
    Retriever for Coolbet using nodriver for Incapsula bypass + DOM extraction.

    Strategy:
    1. Use nodriver to bypass Incapsula (undetected Chrome)
    2. Load sport page and wait for rendering
    3. Extract event data directly from DOM using JavaScript
    4. Parse and normalize to StandardEvent format
    """

    SPORT_SLUGS: Dict[str, str] = {
        "football": "football",
        "basketball": "basketball",
        "tennis": "tennis",
        "ice_hockey": "ice-hockey",
        "american_football": "american-football",
        "baseball": "baseball",
        "mma": "mma",
        "esports": "esports",
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        if not NODRIVER_AVAILABLE:
            raise ImportError(
                "nodriver is required for CoolbetNodriverRetriever. "
                "Install with: pip install nodriver"
            )

        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', 'coolbet.com')}")
        self.site_url: str = raw_site_url.rstrip("/")

    def _get_sport_url(self, sport: str) -> str:
        """Get the sportsbook URL for a given sport."""
        slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/en/sports/{slug}"

    async def extract(self, sport: str) -> List[StandardEvent]:
        """
        Extract events using nodriver + DOM parsing.
        """
        logger.info(f"Extracting {sport} from Coolbet using nodriver")

        url = self._get_sport_url(sport)
        browser = None

        try:
            # Launch undetected Chrome
            browser = await uc.start(
                headless=False,  # TODO: Change to True for production after debugging
                sandbox=False,
            )

            print(f"DEBUG: Loading URL: {url}")
            page = await browser.get(url, wait_load=True)
            print("DEBUG: browser.get() completed, page object received")

            # Wait for initial page load and check for challenge multiple times
            print("DEBUG: Waiting for page and checking for challenges...")
            challenge_found = False
            for attempt in range(10):
                await asyncio.sleep(1)
                try:
                    page_html = await page.evaluate("document.documentElement.outerHTML")
                    page_text = await page.evaluate("document.body ? document.body.innerText : ''")
                    print(f"DEBUG: Attempt {attempt+1} - HTML length: {len(page_html)}, Text length: {len(page_text)}")

                    # Check for Imperva/challenge indicators
                    if any(keyword in page_html.lower() for keyword in ['imperva', 'incapsula', 'click to verify', 'security check', 'additional security']):
                        challenge_found = True
                        print(f"DEBUG: Challenge detected in HTML!")
                        break

                    if len(page_text) > 1000:
                        print("DEBUG: Page has content, no challenge detected")
                        break
                except Exception as e:
                    print(f"DEBUG: Error checking page: {e}")

            if challenge_found:
                print("DEBUG: Imperva challenge detected! Waiting for challenge UI to render...")

                # Wait for challenge page to fully render
                await asyncio.sleep(3)

                # Try multiple times to find and click the challenge
                clicked = False
                for click_attempt in range(5):
                    print(f"DEBUG: Click attempt {click_attempt + 1}")
                    await asyncio.sleep(1)

                    # Check if there's an iframe (challenges often use iframes)
                    iframe_found = await page.evaluate("""
                        () => {
                            const iframes = document.querySelectorAll('iframe');
                            return iframes.length;
                        }
                    """)
                    print(f"DEBUG: Found {iframe_found} iframes")

                    # Try clicking in main document
                    try:
                        # First, check what elements are actually on the page
                        page_info = await page.evaluate("""
                            () => {
                                return {
                                    inputs: document.querySelectorAll('input').length,
                                    buttons: document.querySelectorAll('button').length,
                                    allElements: document.querySelectorAll('*').length,
                                    bodyHTML: document.body ? document.body.innerHTML.slice(0, 500) : ''
                                };
                            }
                        """)
                        print(f"DEBUG: Page elements: {page_info}")

                        # Try simple click attempt
                        click_result = await page.evaluate("""
                            () => {
                                try {
                                    // Try input checkbox
                                    const checkbox = document.querySelector('input[type="checkbox"]');
                                    if (checkbox) {
                                        checkbox.click();
                                        return 'clicked-checkbox';
                                    }

                                    // Try any button
                                    const button = document.querySelector('button');
                                    if (button) {
                                        button.click();
                                        return 'clicked-button';
                                    }

                                    return 'no-element-found';
                                } catch (e) {
                                    return 'error: ' + e.message;
                                }
                            }
                        """)
                    except Exception as e:
                        click_result = f"exception: {e}"

                    print(f"DEBUG: Click result: {click_result}")
                    if isinstance(click_result, str) and 'clicked' in click_result:
                        clicked = True
                        print(f"DEBUG: Successfully clicked! Result: {click_result}")
                        await asyncio.sleep(5)
                        break

                if not clicked:
                    print("DEBUG: Could not find/click challenge element after multiple attempts")
                    print("DEBUG: Challenge may require manual interaction or different approach")

            # Wait for page load with multiple checks
            print("DEBUG: Waiting for page content to load...")
            for i in range(20):
                await asyncio.sleep(1)
                try:
                    body_text = await page.evaluate("document.body ? document.body.innerText : ''")
                    print(f"DEBUG: Wait {i+1}s - Body length: {len(body_text)} chars")
                    if len(body_text) > 1000:
                        print("DEBUG: Page has substantial content, proceeding...")
                        break
                except Exception as e:
                    print(f"DEBUG: Wait {i+1}s - Error checking page: {e}")

            # Check if page loaded successfully
            title = await page.evaluate("document.title || ''")
            body_text = await page.evaluate("document.body ? document.body.innerText : ''")
            current_url = await page.evaluate("window.location.href")
            print(f"DEBUG: Current URL: {current_url}")
            print(f"DEBUG: Final page title: {title}")
            print(f"DEBUG: Final body text length: {len(body_text)} chars")
            if len(body_text) > 200:
                print(f"DEBUG: Body preview: {body_text[:500]}")

            # Take screenshot for debugging
            try:
                await page.save_screenshot('debug_coolbet_page.png')
                print("DEBUG: Screenshot saved to debug_coolbet_page.png")
            except Exception as e:
                print(f"DEBUG: Screenshot failed: {e}")

            # Check for Incapsula block
            if 'incapsula' in body_text.lower():
                print("DEBUG: Page contains 'incapsula' keyword - BLOCKED")
                return []
            elif len(body_text) < 100:
                print("DEBUG: Body text too short - page may not have loaded")
                return []
            else:
                print("DEBUG: Page appears to have loaded successfully")

            # Scroll to trigger lazy loading
            for i in range(3):
                await page.evaluate(f"window.scrollTo(0, {(i+1) * 500})")
                await asyncio.sleep(1)

            # Extract event data from DOM
            logger.info("Extracting events from DOM...")
            raw_events = await self._extract_events_from_dom(page)

            logger.info(f"Extracted {len(raw_events)} raw events")
            if len(raw_events) > 0:
                logger.info(f"First raw event: {raw_events[0]}")
            else:
                logger.warning("No raw events found - DOM extraction may need adjustment")

            # Parse into StandardEvent format
            events = []
            for raw_event in raw_events:
                try:
                    event = self._parse_event(raw_event, sport)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.warning(f"Failed to parse event: {e}")
                    continue

            logger.info(f"Successfully parsed {len(events)} events for {sport}")
            return events

        except Exception as e:
            logger.error(f"Failed to extract from Coolbet: {e}")
            return []

        finally:
            if browser:
                browser.stop()

    async def _extract_events_from_dom(self, page) -> List[Dict]:
        """
        Extract event data from DOM using JavaScript.
        """
        raw_events = await page.evaluate("""
            () => {
                const events = [];

                // Try multiple selectors to find event containers
                const selectors = [
                    '[data-testid*="event"]',
                    '[class*="event-row"]',
                    '[class*="EventRow"]',
                    '[class*="match"]',
                    '[class*="Match"]',
                    '[class*="game"]'
                ];

                let eventElements = [];
                for (const selector of selectors) {
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0) {
                        eventElements = Array.from(elements);
                        break;
                    }
                }

                // Fallback: find elements with team names and odds
                if (eventElements.length === 0) {
                    // Look for containers with multiple odds values
                    const allElements = document.querySelectorAll('div, article, section');
                    for (const el of allElements) {
                        const text = el.textContent;
                        const oddsMatches = text.match(/\\b[1-9]\\.[0-9]{2}\\b/g);
                        if (oddsMatches && oddsMatches.length >= 3 && text.length < 500) {
                            eventElements.push(el);
                        }
                    }
                }

                for (const el of eventElements) {
                    try {
                        const text = el.textContent;

                        // Extract teams (look for capitalized words)
                        const teamPattern = /[A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*/g;
                        const teamMatches = text.match(teamPattern);
                        const teams = teamMatches ? Array.from(new Set(teamMatches)).slice(0, 2) : [];

                        // Extract odds (decimal format like 1.50, 2.85, 3.60)
                        const oddsPattern = /\\b[1-9]\\.[0-9]{2}\\b/g;
                        const oddsMatches = text.match(oddsPattern);
                        const odds = oddsMatches ? oddsMatches.map(o => parseFloat(o)) : [];

                        // Extract date/time if available
                        const datePattern = /(\\d{1,2}\\s+[A-Za-z]{3}|\\d{1,2}\\/\\d{1,2}|Today|Tomorrow)/i;
                        const dateMatch = text.match(datePattern);
                        const dateStr = dateMatch ? dateMatch[0] : null;

                        const timePattern = /\\b\\d{1,2}:\\d{2}\\b/;
                        const timeMatch = text.match(timePattern);
                        const timeStr = timeMatch ? timeMatch[0] : null;

                        // Only include if we have teams and odds
                        if (teams.length >= 2 && odds.length >= 3) {
                            events.push({
                                home_team: teams[0],
                                away_team: teams[1],
                                odds: {
                                    home: odds[0] || null,
                                    draw: odds[1] || null,
                                    away: odds[2] || null,
                                    all_odds: odds
                                },
                                date_str: dateStr,
                                time_str: timeStr,
                                raw_text: text.slice(0, 300)
                            });
                        }
                    } catch (e) {
                        // Skip elements that fail
                        continue;
                    }
                }

                return events;
            }
        """)

        return raw_events if raw_events else []

    def _parse_event(self, raw: Dict, sport: str) -> Optional[StandardEvent]:
        """
        Parse raw event data into StandardEvent format.
        """
        try:
            home_team = raw.get("home_team", "").strip()
            away_team = raw.get("away_team", "").strip()

            if not home_team or not away_team:
                return None

            # Build markets
            markets = []

            odds_data = raw.get("odds", {})
            home_odds = odds_data.get("home")
            draw_odds = odds_data.get("draw")
            away_odds = odds_data.get("away")

            # Match Result (1X2 or moneyline)
            outcomes = []
            if home_odds:
                outcomes.append({"name": "1" if sport == "football" else home_team, "odds": home_odds})
            if draw_odds and sport in ["football", "ice_hockey"]:
                outcomes.append({"name": "X", "odds": draw_odds})
            if away_odds:
                outcomes.append({"name": "2" if sport == "football" else away_team, "odds": away_odds})

            if outcomes:
                markets.append({
                    "type": "match_result" if sport == "football" else "moneyline",
                    "outcomes": outcomes
                })

            # Parse date/time
            date_str = raw.get("date_str")
            time_str = raw.get("time_str")
            start_time = self._parse_datetime(date_str, time_str)

            return StandardEvent(
                provider=self.provider_id,
                sport=sport,
                league="Unknown",  # Coolbet doesn't clearly show league in DOM
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                markets=markets
            )

        except Exception as e:
            logger.warning(f"Failed to parse event: {e}")
            return None

    def _parse_datetime(self, date_str: Optional[str], time_str: Optional[str]) -> Optional[datetime]:
        """
        Parse date and time strings into datetime.
        """
        if not date_str or not time_str:
            return None

        try:
            # Handle common date formats
            today = datetime.now()

            if date_str.lower() == "today":
                date = today
            elif date_str.lower() == "tomorrow":
                date = datetime(today.year, today.month, today.day + 1)
            else:
                # Try parsing date formats like "28 Jan" or "28/01"
                # This is simplified - production would need robust date parsing
                return None

            # Parse time (format: HH:MM)
            if ':' in time_str:
                hour, minute = map(int, time_str.split(':'))
                return datetime(date.year, date.month, date.day, hour, minute)

        except Exception as e:
            logger.debug(f"Failed to parse datetime: {e}")

        return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - we override extract() completely"""
        return []
