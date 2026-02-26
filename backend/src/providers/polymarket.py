from typing import List, Any, Optional
import logging
import json
from ..core import Retriever, StandardEvent

logger = logging.getLogger(__name__)

# Map Polymarket series slugs to our canonical sport names
# Note: Many leagues use year-suffixed slugs (e.g., 'nhl-2026')
# The _get_sport_league method handles these via prefix matching
SERIES_TO_SPORT = {
    # Football (Soccer)
    'premier-league': 'football',
    'la-liga': 'football',
    'bundesliga': 'football',
    'serie-a': 'football',
    'ligue-1': 'football',
    'eredivisie': 'football',
    'mls': 'football',
    'efl-championship': 'football',
    'fa-cup': 'football',
    'dfb-pokal': 'football',
    'copa-del-rey': 'football',
    'coppa-italia': 'football',
    'coupe-de-france': 'football',
    'champions-league': 'football',
    'europa-league': 'football',
    'conference-league': 'football',
    'saudi-professional-league': 'football',
    'primera-divisin-argentina': 'football',
    'brazil-serie-a': 'football',
    'mex-2025': 'football',
    'liga-mx': 'football',
    'a-league-soccer': 'football',
    'denmark-superliga': 'football',
    'scottish-premiership': 'football',
    'ligue-2': 'football',
    'bundesliga-2': 'football',
    'la-liga-2': 'football',
    'serie-b': 'football',
    'copa-libertadores': 'football',
    'efl-cup': 'football',
    'ere-2025': 'football',  # Eredivisie 2025
    # Basketball
    'nba': 'basketball',
    'nba-2026': 'basketball',
    'ncaa-cbb': 'basketball',
    'ncaa-cbb-2026': 'basketball',
    'cwbb': 'basketball',
    'euroleague': 'basketball',
    'euroleague-basketball': 'basketball',
    # Ice Hockey
    'nhl': 'ice_hockey',
    'nhl-2026': 'ice_hockey',
    'khl': 'ice_hockey',
    'khl-2026': 'ice_hockey',
    'shl': 'ice_hockey',
    'shl-2026': 'ice_hockey',
    'snhl': 'ice_hockey',       # Swiss National League
    'snhl-2026': 'ice_hockey',
    'ahl': 'ice_hockey',
    'ahl-2026': 'ice_hockey',
    'cehl': 'ice_hockey',       # Czech Extraliga
    'cehl-2026': 'ice_hockey',
    'del': 'ice_hockey',        # German DEL
    'del-2026': 'ice_hockey',
    'liiga': 'ice_hockey',      # Finnish Liiga
    'liiga-2026': 'ice_hockey',
    # American Football
    'nfl': 'american_football',
    'nfl-2026': 'american_football',
    'ncaa-football': 'american_football',
    'ncaa-football-2026': 'american_football',
    # Tennis
    'atp': 'tennis',
    'wta': 'tennis',
    'australian-open': 'tennis',
    # MMA
    'ufc': 'mma',
    # Esports
    'league-of-legends': 'esports',
    'valorant': 'esports',
    'counter-strike': 'esports',
    'cs2': 'esports',
    'dota-2': 'esports',
    # Rugby
    'rugby-top-14': 'rugby',
    'united-rugby-championship': 'rugby',
    'rugby-six-nations': 'rugby',
    'super-rugby-pacific': 'rugby',
    # Cricket
    'ipl': 'cricket',
    'big-bash': 'cricket',
    't20': 'cricket',
}


class PolymarketRetriever(Retriever):
    """
    Polymarket Retriever - fetches ALL game events using tag_id.

    Uses tag_id=100639 (Game Bets) to get all sports events in one call,
    instead of per-league series_id fetching which misses events due to
    incorrect/missing series ID mappings.

    Filters:
    - Volume: Skip markets with < $100 volume (92% of zero-volume are untraded 50/50)
    - Exact 50/50: Skip markets where all prices are exactly 0.50 (no trading yet)
    - Derivative: Skip "More Markets" events (only spreads/totals/props)
    - Non-match: Skip events that can't be parsed into home vs away

    Optimization:
    - Caches extract_all() results and filters by sport on subsequent calls
    - Prevents redundant API calls when orchestrator iterates through sports
    """

    # Minimum market volume in USD to filter untraded markets
    # Analysis shows: $0 = 92% untraded, $1-100 = 41% untraded, $100+ = 20% untraded
    MIN_VOLUME = 100

    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.base_url = config.get("base_url", "https://gamma-api.polymarket.com")
        self.clob_url = config.get("clob_url", "https://clob.polymarket.com")
        self.game_bets_tag_id = config.get("params", {}).get("game_bets_tag_id", 100639)
        # Spread buffer: cents added to midpoint to estimate executable buy price
        self.spread_buffer = config.get("params", {}).get("spread_buffer_cents", 2) / 100.0
        self.use_clob_midpoint = config.get("params", {}).get("use_clob_midpoint", True)
        self._cached_events: list = None  # Cache all events to avoid re-fetching
        self._events_by_sport: dict = None  # Pre-indexed by sport for O(1) lookup
        self._clob_midpoints: dict = {}  # token_id -> midpoint price (populated during extraction)

    def _get_sport_url(self, sport: str) -> str:
        return ""

    def _price_to_odds(self, price: float) -> float:
        """Convert a probability price to decimal odds with spread adjustment.

        Adds spread_buffer to approximate the executable buy price (ask),
        since Gamma API / CLOB midpoints understate the actual cost.
        """
        adjusted = min(price + self.spread_buffer, 0.99)
        return round(1 / adjusted, 3) if adjusted > 0.01 else 100.0

    def _get_clob_price(self, token_id: str, gamma_price: float) -> float:
        """Get best price for a token: CLOB midpoint if available, else Gamma."""
        return self._clob_midpoints.get(token_id, gamma_price)

    async def _fetch_clob_midpoints(self, token_ids: list[str]):
        """Batch-fetch CLOB midpoints for all tokens using concurrent requests.

        Populates self._clob_midpoints dict. Falls back to Gamma price on failure.
        """
        import aiohttp
        import asyncio

        if not token_ids or not self.use_clob_midpoint:
            return

        semaphore = asyncio.Semaphore(20)  # Limit concurrency to avoid rate limits
        unique_tokens = list(set(token_ids))

        async def fetch_one(session: aiohttp.ClientSession, token_id: str):
            async with semaphore:
                try:
                    url = f"{self.clob_url}/midpoint"
                    async with session.get(url, params={"token_id": token_id}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            mid = float(data.get("mid", 0))
                            if 0.01 < mid < 0.99:
                                self._clob_midpoints[token_id] = mid
                except Exception:
                    pass  # Fallback to Gamma price

        try:
            async with aiohttp.ClientSession() as session:
                await asyncio.gather(*[fetch_one(session, tid) for tid in unique_tokens])
            logger.debug(
                f"[{self.provider_id}] CLOB midpoints: {len(self._clob_midpoints)}/{len(unique_tokens)} tokens"
            )
        except Exception as e:
            logger.warning(f"[{self.provider_id}] CLOB midpoint fetch failed: {e}")

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Parse API response - delegates to _parse_all."""
        return self._parse_all(data) if data else []

    async def extract_all(self, limit: int = 500) -> List[StandardEvent]:
        """
        Fetch ALL game events from Polymarket using tag_id with pagination.

        Polymarket API returns max 500 events per request, so we paginate
        through all results using offset parameter.

        Args:
            limit: Events per page (capped at API_MAX_LIMIT=500)

        Returns all events with sport/league determined from series info.
        """
        API_MAX_LIMIT = 500  # Polymarket API caps at 500 events per request

        # Phase 1: Fetch all raw event data from Gamma API
        all_raw = []
        offset = 0
        page = 1
        page_limit = min(limit, API_MAX_LIMIT)

        while True:
            params = {
                "active": "true",
                "closed": "false",
                "tag_id": self.game_bets_tag_id,
                "order": "startTime",
                "ascending": "true",
                "limit": page_limit,
                "offset": offset
            }

            url = f"{self.base_url}/events"
            data = await self.transport.get(url, params=params)

            if not data:
                break

            logger.debug(f"[{self.provider_id}] Page {page}: fetched {len(data)} events (offset={offset})")
            all_raw.extend(data)

            if len(data) < page_limit:
                break

            offset += page_limit
            page += 1

        # Phase 2: Collect all CLOB token IDs and fetch midpoints
        if self.use_clob_midpoint and all_raw:
            all_token_ids = []
            for item in all_raw:
                for m in item.get("markets", []):
                    raw_ids = m.get("clobTokenIds", "[]")
                    try:
                        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else (raw_ids or [])
                        all_token_ids.extend(str(t) for t in ids if t)
                    except (json.JSONDecodeError, TypeError):
                        pass
            await self._fetch_clob_midpoints(all_token_ids)

        # Phase 3: Parse events (using CLOB midpoints + spread buffer)
        all_events = self._parse_all(all_raw)

        logger.debug(
            f"[{self.provider_id}] Fetched {len(all_events)} events total from Polymarket "
            f"({page} pages, spread={self.spread_buffer*100:.0f}¢, "
            f"clob_midpoints={len(self._clob_midpoints)})"
        )
        return all_events

    async def extract(self, sport: str, limit: int = 50, **kwargs) -> List[StandardEvent]:
        """
        Extract events for a specific sport/league.

        Optimization: Caches extract_all() results and filters by sport.
        This prevents redundant API calls when orchestrator iterates through sports.

        Args:
            sport: Sport to filter by (e.g., 'football', 'basketball')
            limit: Events per page for initial fetch (capped at 500)

        Returns:
            List of StandardEvent for the requested sport only
        """
        # Populate cache on first call
        if self._cached_events is None:
            self._cached_events = await self.extract_all(limit)
            # Pre-index by sport for O(1) lookup
            self._events_by_sport = {}
            for event in self._cached_events:
                event_sport = event.sport or "unknown"
                if event_sport not in self._events_by_sport:
                    self._events_by_sport[event_sport] = []
                self._events_by_sport[event_sport].append(event)
            logger.info(
                f"[{self.provider_id}] Cached {len(self._cached_events)} events across "
                f"{len(self._events_by_sport)} sports"
            )

        # Return only events for requested sport
        return self._events_by_sport.get(sport, [])

    def _parse_all(self, data: List[dict]) -> List[StandardEvent]:
        """Parse all events, determining sport from series info."""
        events = []

        for item in data:
            try:
                event = self._parse_event(item)
                if event:
                    events.append(event)
            except Exception as e:
                logger.debug(f"Failed to parse Polymarket event: {e}")

        return events

    def _parse_event(self, item: dict) -> Optional[StandardEvent]:
        """Parse a single Polymarket event."""
        title = item.get("title", "")
        event_id = str(item.get("id", ""))
        start_time = item.get("startTime")

        # Normalize startTime: Gamma API may return epoch timestamp (int/float)
        # instead of ISO string. Convert to ISO so canonical ID date matching works.
        if isinstance(start_time, (int, float)):
            from datetime import datetime, timezone
            # Handle millisecond vs second timestamps
            ts = start_time / 1000 if start_time > 1e10 else start_time
            start_time = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        # Skip "More Markets" events - they only have spreads/totals/props, no 1x2
        if " - More Markets" in title:
            return None

        # Parse teams from title
        home, away = self._parse_teams(title)
        if not home or not away:
            return None  # Skip non-match events

        # Determine sport and league from series
        sport, league = self._get_sport_league(item)

        # Sports with possible draws use separate binary sub-markets on Polymarket
        # ("Will X win?", "Draw?", "Will Y win?") — combine into single 1x2
        draw_sports = {"football", "rugby"}
        if sport in draw_sports:
            markets = self._combine_football_markets(item.get("markets", []), home, away)
        else:
            # Track moneyline candidates with volume — keep only the highest-volume one
            # to avoid sub-markets (e.g., "Map 1 Winner") overwriting the real moneyline
            ml_candidates = []
            markets = []
            for m_data in item.get("markets", []):
                m = self._parse_market(m_data, home, away)
                if m:
                    vol = float(m_data.get("volume", 0) or 0)
                    if m["type"] in ("moneyline", "1x2"):
                        ml_candidates.append((m, vol))
                    else:
                        markets.append(m)
            # Keep only the highest-volume moneyline market
            if ml_candidates:
                ml_candidates.sort(key=lambda x: x[1], reverse=True)
                markets.insert(0, ml_candidates[0][0])
                if len(ml_candidates) > 1:
                    logger.debug(
                        f"[polymarket] {title}: picked moneyline with vol=${ml_candidates[0][1]:.0f}, "
                        f"skipped {len(ml_candidates)-1} lower-volume moneyline markets"
                    )

        # Collect spread/total markets (both football and non-football)
        spread_candidates = []
        total_candidates = []
        for m_data in item.get("markets", []):
            s = self._parse_spread_market(m_data, home, away)
            if s:
                spread_candidates.append((s, float(m_data.get("volume", 0) or 0)))
            t = self._parse_total_market(m_data)
            if t:
                total_candidates.append((t, float(m_data.get("volume", 0) or 0)))

        # Keep ALL spread/total lines — storage filters to Pinnacle's point
        for s, _ in spread_candidates:
            markets.append(s)
        for t, _ in total_candidates:
            markets.append(t)

        if not markets:
            return None  # Skip events without valid markets

        return StandardEvent(
            id=event_id,
            name=title,
            home_team=home,
            away_team=away,
            sport=sport,
            league=league,
            start_time=start_time,
            markets=markets,
            provider=self.provider_id,
        )

    def _parse_teams(self, title: str) -> tuple[str, str]:
        """Extract home and away teams from event title."""
        import re
        clean_title = title

        # Strip common suffixes
        for suffix in [" - More Markets", " - Winner", " (Game 1)", " (Game 2)", " (Game 3)"]:
            if suffix in clean_title:
                clean_title = clean_title.split(suffix)[0]

        # Strip common prefixes (tournament/game names)
        # Order matters - check longer prefixes first
        prefixes_to_strip = [
            # Esports - full names and abbreviations
            "Counter-Strike: ", "Counter-Strike:", "CS2: ", "CS2:", "CS: ", "CS:",
            "League of Legends: ", "League of Legends:", "LoL: ", "LoL:",
            "Valorant: ", "Valorant:", "Dota 2: ", "Dota 2:", "Dota2: ", "Dota2:",
            "Call of Duty: ", "Call of Duty:", "CoD: ", "CoD:",
            "Rainbow Six: ", "Rainbow Six:", "R6: ", "R6:",
            "Overwatch: ", "Overwatch:", "OW: ", "OW:",
            "Rocket League: ", "Rocket League:", "RL: ", "RL:",
            "StarCraft II: ", "StarCraft II:", "StarCraft: ", "StarCraft:", "SC2: ", "SC2:",
            "Fortnite: ", "Fortnite:", "PUBG: ", "PUBG:",
            # Tennis - tournaments
            "Australian Open Men's: ", "Australian Open Men's:",
            "Australian Open Women's: ", "Australian Open Women's:",
            "US Open Men's: ", "US Open Men's:",
            "US Open Women's: ", "US Open Women's:",
            "Wimbledon Men's: ", "Wimbledon Men's:",
            "Wimbledon Women's: ", "Wimbledon Women's:",
            "French Open Men's: ", "French Open Men's:",
            "French Open Women's: ", "French Open Women's:",
            "ATP: ", "ATP:", "WTA: ", "WTA:",
            # Cricket
            "International T20 Series: ", "Sheffield Shield: ",
            "BPL: ", "Ranji Trophy: ", "WNCL: ", "WNCL:",
            "IPL: ", "IPL:", "BBL: ", "BBL:", "CPL: ", "CPL:",
            # General
            "Men's: ", "Women's: ",
        ]
        for prefix in prefixes_to_strip:
            if clean_title.startswith(prefix):
                clean_title = clean_title[len(prefix):]
                break  # Only strip one prefix

        # MMA: Strip "UFC Fight Night: ", "UFC 315: ", "Bellator 300: ", etc.
        clean_title = re.sub(r'^(?:UFC|Bellator|PFL|ONE)(?:\s+[\w\'\-]+)*\s*:\s*', '', clean_title)

        # Strip match format indicators: (BO1), (BO3), (BO5), etc.
        # and trailing parenthetical metadata (weight class, card position, etc.)
        clean_title = re.sub(r'\s*\([^)]+\)\s*', '', clean_title)

        # Strip tournament/league info after " - " (but preserve "vs" split)
        # Do this AFTER splitting on "vs" to avoid removing team names
        # First, try to split on vs
        for sep in [" vs. ", " vs ", " @ "]:
            if sep in clean_title:
                parts = clean_title.split(sep)
                if len(parts) == 2:
                    home = parts[0].strip()
                    away = parts[1].strip()

                    # Strip tournament info from away team (appears after " - " or "- ")
                    if " - " in away:
                        away = away.split(" - ")[0].strip()
                    elif "- " in away:
                        # Handle "TeamName- Tournament" (no space before dash)
                        # Use rsplit to preserve team names with dashes (e.g., "ex-RUBY")
                        parts = away.rsplit("- ", 1)
                        if len(parts) == 2 and len(parts[1].split()) >= 2:
                            away = parts[0].strip()

                    return home, away

        return "", ""

    def _get_sport_league(self, item: dict) -> tuple[str, str]:
        """Determine sport and league from event's series info."""
        series_list = item.get("series", [])
        series_slug = item.get("seriesSlug", "")

        # Get series title for league name
        league = "Unknown"
        if series_list:
            league = series_list[0].get("title", "Unknown")

        # Map slug to sport - try exact match first
        sport = SERIES_TO_SPORT.get(series_slug)

        # If no exact match, try without year suffix (e.g., "nhl-2026" -> "nhl")
        # This handles future year variants automatically
        if not sport and '-20' in series_slug:
            base_slug = series_slug.rsplit('-20', 1)[0]
            sport = SERIES_TO_SPORT.get(base_slug)
            if sport:
                logger.debug(f"[{self.provider_id}] Matched '{series_slug}' via base slug '{base_slug}'")

        # Fallback: try to infer from tags
        if not sport:
            sport = self._infer_sport_from_tags(item)

        return sport or "unknown", league

    def _infer_sport_from_tags(self, item: dict) -> Optional[str]:
        """Infer sport from event tags as a fallback."""
        tags = [t.get("slug", "") for t in item.get("tags", [])]

        if "basketball" in tags or "nba" in tags:
            return "basketball"
        elif "football" in tags or "soccer" in tags:
            return "football"
        elif "nfl" in tags:
            return "american_football"
        elif "hockey" in tags or "nhl" in tags:
            return "ice_hockey"
        elif "tennis" in tags:
            return "tennis"
        elif "mma" in tags or "ufc" in tags:
            return "mma"
        elif "esports" in tags:
            return "esports"
        elif "cricket" in tags:
            return "cricket"
        elif "rugby" in tags:
            return "rugby"

        return None

    def _parse_clob_token_ids(self, data: dict) -> list:
        """Extract clobTokenIds from a Gamma API market object."""
        raw = data.get("clobTokenIds", "[]")
        try:
            ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
            return [str(t) for t in ids] if ids else []
        except (json.JSONDecodeError, TypeError):
            return []

    def _parse_market(self, data: dict, home: str = "", away: str = "") -> Optional[dict]:
        """Parse a single market (moneyline only - skips totals/spreads)."""
        try:
            import re

            # Skip non-moneyline markets based on question text
            question = data.get("question", "")
            question_lower = question.lower()
            if any(kw in question_lower for kw in [
                "over", "under", "total", "spread", "handicap",
                "points", "goals scored", "combined",
                # Esports sub-markets (map/game-level lines are NOT match moneyline)
                "map 1", "map 2", "map 3", "map 4", "map 5",
                "game 1", "game 2", "game 3", "game 4", "game 5",
                "first map", "second map", "third map",
                "first game", "second game", "third game",
                "map winner", "game winner",
                "1st map", "2nd map", "3rd map",
                "pistol round", "first blood",
                # Esports exotic prop markets (e.g., "Series: Most drakes?")
                "most kills", "most towers", "most drakes", "most nashors",
                "most inhibitors", "most barons",
                # Cross-sport sub-markets (halves, quarters, periods, sets, rounds)
                "1st half", "2nd half", "first half", "second half",
                "1st quarter", "2nd quarter", "3rd quarter", "4th quarter",
                "1st period", "2nd period", "3rd period",
                "1st set", "2nd set", "3rd set", "set 1", "set 2", "set 3",
                # UFC sub-markets
                "method of victory", "by ko", "by tko", "by submission",
                "by decision", "round betting",
            ]):
                return None

            # Volume filter: Skip low-volume markets (no real trading activity)
            volume = data.get("volume", 0)
            try:
                volume = float(volume) if volume else 0
            except (ValueError, TypeError):
                volume = 0

            if volume < self.MIN_VOLUME:
                return None

            # Parse outcome prices
            prices_raw = data.get("outcomePrices", "[]")
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
            prices = [float(p) for p in prices]

            outcomes_raw = data.get("outcomes", [])
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])

            if not outcomes or not prices:
                return None

            # Skip exact 50/50 markets (no trading activity yet)
            if all(p == 0.5 for p in prices if p > 0):
                return None

            # Check active (liquidity check)
            if not any(0.02 < p < 0.98 for p in prices):
                return None

            # Handle binary "Yes/No" markets - convert to team names if we can parse the question
            # Pattern: "Will [Team] win on [date]?" or "Will [Team] win?"
            outcome_names_lower = [o.lower() for o in outcomes]
            if set(outcome_names_lower) == {"yes", "no"} and home and away:
                # Try to extract team name from "Will X win" pattern
                match = re.search(r"will\s+(.+?)\s+win", question_lower)
                if match:
                    team_in_question = match.group(1).strip()
                    home_lower = home.lower()
                    away_lower = away.lower()

                    # Check if team in question matches home or away
                    # Use substring matching for flexibility (e.g., "Lakers" vs "Los Angeles Lakers")
                    matched_team = None
                    other_team = None

                    if team_in_question in home_lower or home_lower in team_in_question:
                        matched_team = home
                        other_team = away
                    elif team_in_question in away_lower or away_lower in team_in_question:
                        matched_team = away
                        other_team = home

                    if matched_team:
                        # Map Yes → matched_team, No → other_team
                        yes_idx = outcome_names_lower.index("yes")
                        no_idx = outcome_names_lower.index("no")
                        clob_ids = self._parse_clob_token_ids(data)

                        if prices[yes_idx] > 0.02 and prices[no_idx] > 0.02:
                            yes_token = clob_ids[yes_idx] if yes_idx < len(clob_ids) else None
                            no_token = clob_ids[no_idx] if no_idx < len(clob_ids) else None
                            yes_price = self._get_clob_price(yes_token, prices[yes_idx]) if yes_token else prices[yes_idx]
                            no_price = self._get_clob_price(no_token, prices[no_idx]) if no_token else prices[no_idx]
                            return {
                                "type": "moneyline",
                                "outcomes": [
                                    {"name": matched_team, "odds": self._price_to_odds(yes_price),
                                     "clob_token_id": yes_token},
                                    {"name": other_team, "odds": self._price_to_odds(no_price),
                                     "clob_token_id": no_token},
                                ]
                            }

                # Couldn't parse team from question - skip this Yes/No market
                return None

            # Convert to odds (skip over/under outcomes - these are totals markets)
            clob_ids = self._parse_clob_token_ids(data)
            formatted_outcomes = []
            for i, (name, p) in enumerate(zip(outcomes, prices)):
                if p > 0.02:
                    name_lower = name.lower()
                    # Skip over/under outcomes (totals markets that slipped through)
                    if name_lower in ("over", "under"):
                        continue
                    token_id = clob_ids[i] if i < len(clob_ids) else None
                    price = self._get_clob_price(token_id, p) if token_id else p
                    formatted_outcomes.append({
                        "name": name,
                        "odds": self._price_to_odds(price),
                        "clob_token_id": token_id,
                    })

            if not formatted_outcomes:
                return None

            # Moneyline = exactly 2 outcomes (home/away).
            # More than 2 = prop market (TD scorer, MVP, etc.) — skip.
            if len(formatted_outcomes) != 2:
                return None

            return {
                "type": "moneyline",
                "outcomes": formatted_outcomes
            }
        except Exception:
            return None

    def _parse_spread_market(self, data: dict, home: str, away: str) -> Optional[dict]:
        """Parse a spread/handicap market.

        Detection: question starts with "Spread:" but NOT "1H Spread".
        Example: "Spread: Pistons (-2.5)" → spread market with point=-2.5 for Pistons.
        """
        import re
        try:
            question = data.get("question", "")

            # Must start with "Spread:" but not "1H Spread"
            if not question.startswith("Spread:"):
                return None
            if question.startswith("1H Spread"):
                return None

            # Volume filter
            volume = float(data.get("volume", 0) or 0)
            if volume < self.MIN_VOLUME:
                return None

            # Extract point value from question: "Spread: TeamName (-2.5)"
            point_match = re.search(r'\(([+-]?\d+\.?\d*)\)', question)
            if not point_match:
                return None
            favored_point = float(point_match.group(1))

            # Extract favored team name from question: "Spread: TeamName (-2.5)"
            team_match = re.search(r'Spread:\s*(.+?)\s*\(', question)
            if not team_match:
                return None
            favored_team = team_match.group(1).strip()

            # Parse outcomes and prices
            outcomes_raw = data.get("outcomes", "[]")
            prices_raw = data.get("outcomePrices", "[]")
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])

            if len(outcomes) != 2 or len(prices) != 2:
                return None

            prices = [float(p) for p in prices]

            # Skip exact 50/50 (no trading) and illiquid markets
            if all(p == 0.5 for p in prices):
                return None
            if not any(0.02 < p < 0.98 for p in prices):
                return None

            # Determine which outcome is the favored team
            from ..matching import normalize_outcome
            favored_norm = normalize_outcome(favored_team, home, away)

            clob_ids = self._parse_clob_token_ids(data)
            result_outcomes = []
            for i, (name, p) in enumerate(zip(outcomes, prices)):
                if p <= 0.02:
                    continue
                norm = normalize_outcome(name, home, away)
                if norm not in ('home', 'away'):
                    continue
                # Favored team gets the point from the question, other gets opposite
                if norm == favored_norm:
                    point = favored_point
                else:
                    point = -favored_point
                token_id = clob_ids[i] if i < len(clob_ids) else None
                price = self._get_clob_price(token_id, p) if token_id else p
                result_outcomes.append({
                    "name": norm,
                    "odds": self._price_to_odds(price),
                    "point": point,
                    "clob_token_id": token_id,
                })

            if len(result_outcomes) != 2:
                return None

            return {"type": "spread", "outcomes": result_outcomes}
        except Exception:
            return None

    def _parse_total_market(self, data: dict) -> Optional[dict]:
        """Parse a total (over/under) market.

        Detection: question contains " O/U " but NOT "1H O/U" and NOT player props.
        Example: "Knicks vs. Pistons: O/U 222.5" → total market with point=222.5.
        """
        import re
        try:
            question = data.get("question", "")

            # Must contain " O/U " pattern
            if " O/U " not in question:
                return None

            # Skip 1st half totals
            if "1H O/U" in question:
                return None

            # Skip player props: pattern like "Player Name: Points O/U" or "Name: Rebounds O/U"
            # Match events have pattern "Team vs Team: O/U NUM" — the colon-prefixed part is just "O/U"
            # Player props have "Stat O/U" (e.g., "Points O/U", "Rebounds O/U")
            colon_idx = question.find(":")
            if colon_idx >= 0:
                after_colon = question[colon_idx + 1:].strip()
                # Event totals start directly with "O/U", player props have "stat O/U"
                if not after_colon.startswith("O/U"):
                    return None

            # Volume filter
            volume = float(data.get("volume", 0) or 0)
            if volume < self.MIN_VOLUME:
                return None

            # Extract point value: "O/U 222.5"
            point_match = re.search(r'O/U\s+(\d+\.?\d*)', question)
            if not point_match:
                return None
            point = float(point_match.group(1))

            # Parse outcomes and prices
            outcomes_raw = data.get("outcomes", "[]")
            prices_raw = data.get("outcomePrices", "[]")
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])

            if len(outcomes) != 2 or len(prices) != 2:
                return None

            prices = [float(p) for p in prices]

            # Skip exact 50/50 (no trading) and illiquid markets
            if all(p == 0.5 for p in prices):
                return None
            if not any(0.02 < p < 0.98 for p in prices):
                return None

            clob_ids = self._parse_clob_token_ids(data)
            result_outcomes = []
            for i, (name, p) in enumerate(zip(outcomes, prices)):
                if p <= 0.02:
                    continue
                name_lower = name.lower().strip()
                token_id = clob_ids[i] if i < len(clob_ids) else None
                if name_lower == "over":
                    price = self._get_clob_price(token_id, p) if token_id else p
                    result_outcomes.append({
                        "name": "over",
                        "odds": self._price_to_odds(price),
                        "point": point,
                        "clob_token_id": token_id,
                    })
                elif name_lower == "under":
                    price = self._get_clob_price(token_id, p) if token_id else p
                    result_outcomes.append({
                        "name": "under",
                        "odds": self._price_to_odds(price),
                        "point": point,
                        "clob_token_id": token_id,
                    })

            if len(result_outcomes) != 2:
                return None

            return {"type": "total", "outcomes": result_outcomes}
        except Exception:
            return None

    def _combine_football_markets(self, raw_markets: list, home: str, away: str) -> list:
        """
        Combine Polymarket's separate football markets into a single 1x2 market.

        Polymarket has 3 separate markets per football event:
        - "Will Team A win?" (home)
        - "Will ... end in a draw?" (draw)
        - "Will Team B win?" (away)

        This combines them into one 1x2 market with home/draw/away outcomes.

        Data Quality: Only includes markets with sufficient volume and price discovery.
        """
        home_odds = None
        draw_odds = None
        away_odds = None
        home_token_id = None
        draw_token_id = None
        away_token_id = None

        home_lower = home.lower()
        away_lower = away.lower()

        for m in raw_markets:
            question = m.get("question", "").lower()

            # Volume filter: Skip low-volume markets
            volume = m.get("volume", 0)
            try:
                volume = float(volume) if volume else 0
            except (ValueError, TypeError):
                volume = 0

            if volume < self.MIN_VOLUME:
                continue

            # Parse outcomes and prices
            outcomes_raw = m.get("outcomes", "[]")
            prices_raw = m.get("outcomePrices", "[]")

            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])

            if not outcomes or not prices:
                continue

            # Skip exact 50/50 markets (no trading activity yet)
            float_prices = [float(p) for p in prices if p]
            if all(p == 0.5 for p in float_prices if p > 0):
                continue

            # Get "Yes" price (probability of the event happening)
            yes_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "yes"), 0)
            if yes_idx >= len(prices):
                continue

            yes_price = float(prices[yes_idx])

            if yes_price < 0.02:  # Skip illiquid markets
                continue

            # Extract Yes token ID for this sub-market
            clob_ids = self._parse_clob_token_ids(m)
            token_id = clob_ids[yes_idx] if yes_idx < len(clob_ids) else None

            # Use CLOB midpoint if available, apply spread buffer
            price = self._get_clob_price(token_id, yes_price) if token_id else yes_price
            odds = self._price_to_odds(price)

            # Identify market type from question
            # Only match specific patterns to avoid BTTS, spreads, totals
            if "end in a draw" in question:
                draw_odds = odds
                draw_token_id = token_id
            elif question.startswith("will ") and " win" in question:
                # This is a "Will X win?" market - match team name
                if home_lower in question:
                    home_odds = odds
                    home_token_id = token_id
                elif away_lower in question:
                    away_odds = odds
                    away_token_id = token_id

        # Build combined 1x2 market
        if home_odds and away_odds:
            outcomes = [
                {"name": home, "odds": home_odds, "clob_token_id": home_token_id},
            ]
            if draw_odds:
                outcomes.append({"name": "Draw", "odds": draw_odds, "clob_token_id": draw_token_id})
            outcomes.append({"name": away, "odds": away_odds, "clob_token_id": away_token_id})

            return [{
                "type": "1x2",
                "outcomes": outcomes
            }]

        return []
