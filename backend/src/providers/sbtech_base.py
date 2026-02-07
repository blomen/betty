"""
SBTech Base Retriever - Shared logic for SBTech-powered operators

SBTech (acquired by DraftKings 2020) powers multiple white-label sportsbooks
including Bethard, ComeOn, and Hajper. This base class provides common
API interception and parsing logic.

Flow:
1. Load sportsbook page with Playwright
2. Intercept SBTech API calls
3. Parse JSON responses
4. Convert to StandardEvent format
"""

from typing import List, Any, Optional, Dict
import logging
import asyncio
import re
from datetime import datetime
from ..core import BrowserRetriever, StandardEvent, BrowserTransport
from ..matching.normalizer import normalize_team_name, normalize_outcome

logger = logging.getLogger(__name__)


class SBTechRetriever(BrowserRetriever):
    """
    Base retriever for SBTech-powered operators.

    Subclasses must define:
    - site_url: Base URL for the operator
    - SPORT_SLUGS: Mapping of sport names to URL slugs
    """

    # Sports where participants are individuals (name order: "lastname firstname" in SBTech)
    INDIVIDUAL_SPORTS = {"tennis", "mma", "boxing", "darts", "snooker", "table_tennis"}

    # Default sport slugs (can be overridden by subclasses)
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

    # API endpoint patterns to intercept (can be extended by subclasses)
    API_PATTERNS: List[str] = [
        '/api/sportsbook/',
        '/api/odds/',
        '/api/sb/',
        '/sportcontent/',
        '/EventMarket/',
        '/sportsbook-api/',  # ComeOn Group custom API (ComeOn, Hajper)
    ]

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")

        # Cache for captured API responses
        self._api_responses: List[Dict] = []

    async def _ensure_sport_init(self, sport: str) -> None:
        """No special initialization needed."""
        pass

    def _get_sport_url(self, sport: str) -> str:
        """
        Get the sportsbook URL for a given sport.

        Override this in subclasses if URL structure differs.
        """
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sports/{sport_slug}"

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        cookie_selectors = [
            'button:has-text("Accept")',
            'button:has-text("Acceptera")',
            'button:has-text("Godkänn")',
            '#accept-cookies',
            '.cookie-accept',
        ]

        for selector in cookie_selectors:
            try:
                await page.click(selector, timeout=2000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent")
                await asyncio.sleep(1)
                return
            except Exception:
                continue

        logger.debug(f"[{self.provider_id}] No cookie consent needed")

    async def _process_response(self, response):
        """Process an API response asynchronously."""
        try:
            # Try to get JSON directly
            try:
                data = await response.json()
            except:
                # Fallback to text parsing
                text = await response.text()
                import json
                data = json.loads(text)

            # Check for errors
            if isinstance(data, dict) and ('error' in data or 'Error' in data):
                logger.warning(f"[{self.provider_id}] API returned error: {data}")
            else:
                self._api_responses.append(data)
                logger.info(f"[{self.provider_id}] Captured SBTech API response (total: {len(self._api_responses)})")

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse response: {e}")

    def _should_intercept(self, url: str) -> bool:
        """
        Determine if a URL should be intercepted.

        Override this in subclasses for custom filtering logic.
        """
        return any(pattern in url for pattern in self.API_PATTERNS)

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - extract() is overridden."""
        raise NotImplementedError("SBTechRetriever uses extract() directly")

    # Team sports where spread/total markets are expected
    DETAIL_SPORTS = {"football", "basketball", "ice_hockey", "american_football", "baseball"}

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events by intercepting SBTech API calls.

        1. Load sport page with Playwright
        2. Intercept SBTech API responses
        3. Parse JSON directly
        4. For team sports, navigate to event detail pages for spread/total markets
        """
        if sport not in self.SPORT_SLUGS:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] SBTechRetriever requires BrowserTransport")
                return []

            # Clear previous responses
            self._api_responses = []

            # Setup response interceptor
            await self.transport._ensure_browser()
            page = self.transport.page

            # Intercept API responses
            pending_tasks = []
            all_responses_count = 0

            def intercept_response(response):
                """Synchronous handler that schedules async processing."""
                nonlocal all_responses_count
                all_responses_count += 1
                url = response.url

                # Log API calls for debugging
                if self._should_intercept(url):
                    logger.debug(f"[{self.provider_id}] Found SBTech API call: {url[:100]}")
                    task = asyncio.create_task(self._process_response(response))
                    pending_tasks.append(task)

            page.on('response', intercept_response)

            # Load the sport page
            sport_url = self._get_sport_url(sport)
            logger.info(f"[{self.provider_id}] Loading {sport_url}")
            await page.goto(sport_url, wait_until='load', timeout=60000)

            # Handle cookie consent
            await self._handle_cookie_consent(page)

            # Wait for page to fully render and make API calls
            logger.info(f"[{self.provider_id}] Waiting for API calls...")
            await asyncio.sleep(8)
            logger.debug(f"[{self.provider_id}] Total HTTP responses: {all_responses_count}")

            # Scroll loop to trigger infinite-scroll pagination
            prev_response_count = len(self._api_responses)
            for scroll_i in range(8):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                current_count = len(self._api_responses)
                logger.debug(
                    f"[{self.provider_id}] Scroll {scroll_i + 1}: "
                    f"{current_count} API responses (was {prev_response_count})"
                )
                if current_count == prev_response_count:
                    break  # No new data loaded
                prev_response_count = current_count

            # Remove interceptor
            page.remove_listener('response', intercept_response)

            # Wait for all pending response processing tasks
            if pending_tasks:
                logger.debug(f"[{self.provider_id}] Waiting for {len(pending_tasks)} pending tasks...")
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            # Aggregate all responses then parse (SBTech splits events/markets/selections across responses)
            logger.info(f"[{self.provider_id}] Captured {len(self._api_responses)} API responses")
            slug_map: Dict[str, str] = {}
            events = self._parse_aggregated_responses(self._api_responses, sport, slug_map)

            # Deduplicate by event ID
            seen_ids = set()
            unique_events = []
            for event in events:
                event_key = f"{event.home_team}:{event.away_team}:{event.start_time}"
                if event_key not in seen_ids:
                    seen_ids.add(event_key)
                    unique_events.append(event)

            # For team sports, fetch detail pages to get spread/total markets
            if sport in self.DETAIL_SPORTS and slug_map:
                events_with_markets = [e for e in unique_events if e.markets]
                if events_with_markets:
                    logger.info(
                        f"[{self.provider_id}] Fetching detail pages for "
                        f"{len(events_with_markets)} {sport} events"
                    )
                    await self._extract_event_details(events_with_markets, slug_map, sport)

            logger.info(f"[{self.provider_id}] Extracted {len(unique_events)} unique events")
            return unique_events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    def _get_event_detail_url(self, slug: str) -> str:
        """
        Get the URL for an event's detail page.

        Override in subclasses for locale-specific URL patterns.
        """
        return f"{self.site_url}/sports/{slug}"

    async def _extract_event_details(self, events: List[StandardEvent],
                                      slug_map: Dict[str, str], sport: str) -> None:
        """
        Navigate to individual event detail pages to capture spread/total markets.

        Listing pages only return 1x2/moneyline. Detail pages include all markets.
        Modifies events in-place by merging additional markets.
        """
        page = self.transport.page
        detail_count = 0
        markets_added = 0

        for event in events:
            event_key = f"{event.home_team}:{event.away_team}:{event.start_time}"
            slug = slug_map.get(event_key)
            if not slug:
                continue

            # Skip if event already has spread and total
            existing_types = {m["type"] for m in event.markets}
            if "spread" in existing_types and "total" in existing_types:
                continue

            try:
                # Clear responses for this detail page
                self._api_responses = []
                pending_tasks = []

                def intercept_detail(response):
                    url = response.url
                    if self._should_intercept(url):
                        task = asyncio.create_task(self._process_response(response))
                        pending_tasks.append(task)

                page.on('response', intercept_detail)

                detail_url = self._get_event_detail_url(slug)
                logger.debug(f"[{self.provider_id}] Detail page: {detail_url}")
                await page.goto(detail_url, wait_until='load', timeout=30000)
                await asyncio.sleep(3)

                page.remove_listener('response', intercept_detail)

                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)

                if not self._api_responses:
                    continue

                # Parse detail page responses
                detail_events = self._parse_aggregated_responses(self._api_responses, sport)

                # Find matching event and merge new markets
                for de in detail_events:
                    de_key = f"{de.home_team}:{de.away_team}:{de.start_time}"
                    if de_key == event_key:
                        for market in de.markets:
                            if market["type"] not in existing_types:
                                event.markets.append(market)
                                existing_types.add(market["type"])
                                markets_added += 1
                        break

                detail_count += 1
                # Small delay between navigations to avoid rate limiting
                await asyncio.sleep(1)

            except Exception as e:
                logger.debug(f"[{self.provider_id}] Detail page failed for {event.name}: {e}")
                continue

        logger.info(
            f"[{self.provider_id}] Detail extraction: {detail_count} pages visited, "
            f"{markets_added} markets added"
        )

    def _parse_aggregated_responses(self, responses: List[Dict], sport: str,
                                     slug_map: Optional[Dict[str, str]] = None) -> List[StandardEvent]:
        """
        Aggregate all captured API responses then parse.

        SBTech splits events, markets, and selections across separate responses.
        We merge them all before parsing to avoid missing data.

        If slug_map is provided, it will be populated with event_key -> slug mappings
        for use in detail page extraction.
        """
        # Aggregate all events, markets, selections across responses
        all_events = {}  # id -> event data (dedup by id)
        all_markets = {}  # id -> market data
        all_selections = {}  # id -> selection data

        for api_data in responses:
            if not isinstance(api_data, dict):
                continue
            if 'data' not in api_data or not isinstance(api_data['data'], dict):
                continue

            data = api_data['data']
            for ev in data.get('events', []):
                eid = ev.get('id')
                if eid:
                    all_events[eid] = ev
            for mk in data.get('markets', []):
                mid = mk.get('id')
                if mid:
                    all_markets[mid] = mk
            for sel in data.get('selections', []):
                sid = sel.get('id')
                if sid:
                    all_selections[sid] = sel
            # Also parse marketSelections (SBTech returns selections under both keys)
            ms = data.get('marketSelections')
            if isinstance(ms, list):
                for sel_item in ms:
                    if isinstance(sel_item, dict):
                        sid = sel_item.get('id')
                        if sid:
                            all_selections[sid] = sel_item
            elif isinstance(ms, dict):
                for sid, sel_item in ms.items():
                    if isinstance(sel_item, dict):
                        all_selections[sid] = sel_item

        logger.info(
            f"[{self.provider_id}] Aggregated {len(all_events)} events, "
            f"{len(all_markets)} markets, {len(all_selections)} selections"
        )

        if not all_events:
            return []

        # Build market lookup: eventId -> list of markets
        markets_by_event = {}
        for market in all_markets.values():
            event_id = market.get('eventId')
            if event_id not in markets_by_event:
                markets_by_event[event_id] = []
            markets_by_event[event_id].append(market)

        # Build selection lookup: marketId -> list of selections
        selections_by_market = {}
        for selection in all_selections.values():
            market_id = selection.get('marketId')
            if market_id not in selections_by_market:
                selections_by_market[market_id] = []
            selections_by_market[market_id].append(selection)

        # Parse each event
        events = []
        for event_data in all_events.values():
            try:
                event_id = event_data.get('id')
                event_markets = markets_by_event.get(event_id, [])
                event = self._parse_event(event_data, sport, event_markets, selections_by_market)
                if event:
                    events.append(event)
                    # Track slug for detail page extraction
                    if slug_map is not None:
                        slug = event_data.get('slug')
                        if slug:
                            event_key = f"{event.home_team}:{event.away_team}:{event.start_time}"
                            slug_map[event_key] = slug
            except Exception as e:
                logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")

        return events

    def _parse_event(self, event_data: Dict, sport: str, event_markets: List[Dict],
                     selections_by_market: Dict[str, List[Dict]]) -> Optional[StandardEvent]:
        """
        Parse a single event from SBTech API data.

        SBTech event structure:
        - participants: [{label, side: 1}, {label, side: 2}]  # 1=home, 2=away
        - startDate: ISO datetime
        - competitionName: League name
        - label: "Home Team - Away Team"
        """
        try:
            # Extract teams from participants (side 1 = home, side 2 = away)
            participants = event_data.get('participants', [])
            home_team = None
            away_team = None

            for p in participants:
                if p.get('side') == 1:
                    home_team = p.get('label')
                elif p.get('side') == 2:
                    away_team = p.get('label')

            if not home_team or not away_team:
                return None

            # SBTech returns individual sport names as "lastname firstname"
            # Pinnacle uses "firstname lastname" — reverse 2-word names to match
            if sport in self.INDIVIDUAL_SPORTS:
                home_team = self._reverse_player_name(home_team)
                away_team = self._reverse_player_name(away_team)

            # Normalize team names
            home_team = normalize_team_name(home_team)
            away_team = normalize_team_name(away_team)

            # Parse start time
            start_time_raw = event_data.get('startDate')
            start_time = self._parse_datetime(start_time_raw) if start_time_raw else None

            # Extract league
            league = event_data.get('competitionName', 'Unknown')

            # Parse markets
            markets = []
            seen_market_types = set()
            for market in event_markets:
                market_id = market.get('id')
                market_label = market.get('marketFriendlyName', market.get('label', ''))
                template_id = market.get('marketTemplateId', '')

                # Get selections for this market
                market_selections = selections_by_market.get(market_id, [])

                # Normalize market type (prefer template ID over label)
                market_type = self._normalize_market_type(market_label, template_id)
                if market_type == "other":
                    continue

                # Only keep first market per type (avoid duplicate 1x2, spread, total)
                if market_type in seen_market_types:
                    continue

                # Extract point value from market-level lineValueRaw
                line_raw = market.get('lineValueRaw')
                market_point = None
                if line_raw is not None and line_raw != 0:
                    try:
                        market_point = float(line_raw)
                    except (ValueError, TypeError):
                        pass
                # Fallback to lineValue string or other fields
                if market_point is None:
                    for field in ('lineValue', 'line', 'handicap', 'points'):
                        val = market.get(field)
                        if val is not None and val != '' and val != 0:
                            try:
                                market_point = float(val)
                                break
                            except (ValueError, TypeError):
                                continue

                # Build outcomes
                outcomes = []
                for selection in market_selections:
                    if selection.get('status') != 'Open':
                        continue

                    raw_label = selection.get('label', '')
                    odds_val = selection.get('odds', 0.0)
                    if odds_val <= 1:
                        continue

                    # Use selectionTemplateId for reliable outcome mapping
                    sel_template = selection.get('selectionTemplateId', '')

                    # Determine point value
                    point = market_point

                    # Normalize outcome name based on market type
                    if market_type == "total":
                        if sel_template == 'OVER' or raw_label.lower().strip().startswith('over'):
                            outcome_name = "over"
                        elif sel_template == 'UNDER' or raw_label.lower().strip().startswith('under'):
                            outcome_name = "under"
                        else:
                            continue
                        # Try extracting point from label if not from market
                        if point is None:
                            m = re.search(r'[\d]+\.?\d*', raw_label)
                            if m:
                                point = float(m.group())
                    elif market_type == "spread":
                        # Use selectionTemplateId (HOME/AWAY) or normalize from label
                        if sel_template == 'HOME':
                            outcome_name = "home"
                        elif sel_template == 'AWAY':
                            outcome_name = "away"
                        else:
                            outcome_name = normalize_outcome(raw_label, home_team, away_team)
                        # Try extracting point from label
                        if point is None:
                            m = re.search(r'[+-]?[\d]+\.?\d*', raw_label)
                            if m:
                                point = float(m.group())
                    else:
                        # 1x2/moneyline — use selectionTemplateId if available
                        if sel_template == 'HOME':
                            outcome_name = "home"
                        elif sel_template == 'AWAY':
                            outcome_name = "away"
                        elif sel_template == 'DRAW':
                            outcome_name = "draw"
                        else:
                            outcome_name = normalize_outcome(raw_label, home_team, away_team)

                    outcome_dict = {
                        "name": outcome_name,
                        "odds": odds_val,
                    }
                    if point is not None:
                        outcome_dict["point"] = point
                    outcomes.append(outcome_dict)

                if outcomes:
                    markets.append({
                        "type": market_type,
                        "outcomes": outcomes
                    })
                    seen_market_types.add(market_type)

            # Dedup: prefer 1x2 over moneyline when both exist (e.g., ice hockey)
            market_types_present = {m["type"] for m in markets}
            if "1x2" in market_types_present and "moneyline" in market_types_present:
                markets = [m for m in markets if m["type"] != "moneyline"]

            # Generate event ID and name
            event_id = event_data.get('id', event_data.get('globalId', ''))
            event_name = f"{home_team} vs {away_team}"

            return StandardEvent(
                id=event_id,
                name=event_name,
                provider=self.provider_id,
                sport=sport,
                league=str(league),
                home_team=str(home_team),
                away_team=str(away_team),
                start_time=start_time,
                markets=markets
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")
            return None

    # SBTech marketTemplateId → market type mapping
    # MW3W = 3-way match result, MW2W = 2-way winner (moneyline)
    # Various handicap/total templates across sports
    TEMPLATE_MAP: Dict[str, str] = {
        "MW3W": "1x2",
        "MW2W": "moneyline",
        "ESNMOWINNER2W": "moneyline",
        # Handicap (spread) templates
        "2WHCPROLMID": "spread",
        "ESNMOHANDICAP": "spread",
        "M2WHCPIO": "spread",
        # Over/Under (total) templates
        "MROU": "total",
        "ESNMOOU": "total",
    }

    def _normalize_market_type(self, market_label: str, template_id: str = "") -> str:
        """Normalize market type from template ID or label."""
        # Prefer template ID (most reliable)
        if template_id and template_id in self.TEMPLATE_MAP:
            return self.TEMPLATE_MAP[template_id]

        label_lower = market_label.lower()

        # 1x2/Match result patterns (Swedish + English)
        if any(kw in label_lower for kw in ['matchresultat', 'match result', '1x2', 'full time result',
                                             'vinnare', 'winner', 'slutresultat', 'matchodds', 'moneyline']):
            return "1x2"

        # Total (over/under)
        if any(kw in label_lower for kw in ['over/under', 'total', 'över/under']):
            return "total"

        # Spread (handicap)
        if any(kw in label_lower for kw in ['handicap', 'spread', 'handikapp']):
            return "spread"

        return "other"

    @staticmethod
    def _reverse_player_name(name: str) -> str:
        """
        Reverse 2-word player names from "lastname firstname" to "firstname lastname".

        SBTech returns individual sport participants as "cocciaretto elisabetta"
        while Pinnacle uses "elisabetta cocciaretto". For 3+ word names, leave
        as-is since token_set_ratio fuzzy matching handles those.
        """
        parts = name.strip().split()
        if len(parts) == 2:
            return f"{parts[1]} {parts[0]}"
        return name

    def _parse_datetime(self, dt_str: Any) -> Optional[datetime]:
        """Parse datetime from various formats."""
        if not dt_str:
            return None

        try:
            # Try ISO format
            if isinstance(dt_str, str):
                return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            # Try timestamp
            elif isinstance(dt_str, (int, float)):
                return datetime.fromtimestamp(dt_str / 1000 if dt_str > 10**10 else dt_str)
        except Exception as e:
            logger.debug(f"Failed to parse datetime '{dt_str}': {e}")

        return None
