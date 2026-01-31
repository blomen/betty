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

    Data Quality Filters:
    - MIN_VOLUME: Skip markets with < $1000 volume (no real trading activity)
    - 50/50 Filter: Skip markets where prices are 0.45-0.55 (no price discovery)
    """

    # Minimum volume in USD for a market to be considered valid
    # Markets below this threshold likely have no real price discovery
    MIN_VOLUME = 1000

    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.base_url = config.get("base_url", "https://gamma-api.polymarket.com")
        self.game_bets_tag_id = config.get("params", {}).get("game_bets_tag_id", 100639)
        self._cached_events = None  # Cache to avoid re-fetching

    def _get_sport_url(self, sport: str) -> str:
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Required by base class."""
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

        all_events = []
        offset = 0
        page = 1

        # Cap limit at API maximum to ensure pagination works correctly
        # If caller requests 1000 but API returns 500, we'd incorrectly think it's the last page
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
            all_events.extend(self._parse_all(data))

            # Stop if we got fewer than page_limit (last page)
            if len(data) < page_limit:
                break

            offset += page_limit
            page += 1

        logger.info(f"[{self.provider_id}] Fetched {len(all_events)} events total from Polymarket ({page} pages)")
        return all_events

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """
        Extract events for a specific sport/league.

        For backwards compatibility with orchestrator's per-sport iteration.
        Delegates to extract_all() since we use tag_id for all games.
        """
        # Always use extract_all - tag_id fetches all sports at once
        return await self.extract_all(limit)

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

        # Skip "More Markets" events - they only have spreads/totals/props, no 1x2
        if " - More Markets" in title:
            return None

        # Parse teams from title
        home, away = self._parse_teams(title)
        if not home or not away:
            return None  # Skip non-match events

        # Determine sport and league from series
        sport, league = self._get_sport_league(item)

        # Parse markets - for football, combine home/draw/away into single 1x2
        if sport == "football":
            markets = self._combine_football_markets(item.get("markets", []), home, away)
        else:
            markets = []
            for m_data in item.get("markets", []):
                m = self._parse_market(m_data)
                if m:
                    markets.append(m)

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

        # Strip match format indicators: (BO1), (BO3), (BO5), etc.
        clean_title = re.sub(r'\s*\(BO\d+\)', '', clean_title)

        # Strip tournament/league info after " - " (but preserve "vs" split)
        # Do this AFTER splitting on "vs" to avoid removing team names
        # First, try to split on vs
        for sep in [" vs. ", " vs ", " @ "]:
            if sep in clean_title:
                parts = clean_title.split(sep)
                if len(parts) == 2:
                    home = parts[0].strip()
                    away = parts[1].strip()

                    # Strip tournament info from away team (appears after " - ")
                    if " - " in away:
                        away = away.split(" - ")[0].strip()

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

    def _parse_market(self, data: dict) -> Optional[dict]:
        """Parse a single market (moneyline only - skips totals/spreads)."""
        try:
            # Skip non-moneyline markets based on question text
            question = data.get("question", "").lower()
            if any(kw in question for kw in [
                "over", "under", "total", "spread", "handicap",
                "points", "goals scored", "combined"
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

            # Skip 50/50 markets (no price discovery - prices between 0.45-0.55)
            if all(0.45 < p < 0.55 for p in prices if p > 0):
                return None

            # Check active (liquidity check)
            if not any(0.02 < p < 0.98 for p in prices):
                return None

            # Convert to odds (skip over/under outcomes - these are totals markets)
            formatted_outcomes = []
            for name, p in zip(outcomes, prices):
                if p > 0.02:
                    name_lower = name.lower()
                    # Skip over/under outcomes (totals markets that slipped through)
                    if name_lower in ("over", "under"):
                        continue
                    formatted_outcomes.append({
                        "name": name,
                        "odds": round(1 / p, 3)
                    })

            if not formatted_outcomes:
                return None

            # For non-football markets (parsed via _parse_market), it's always moneyline
            # Football markets go through _combine_football_markets which sets type='1x2'
            return {
                "type": "moneyline",
                "outcomes": formatted_outcomes
            }
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

            # Skip 50/50 markets (no price discovery)
            float_prices = [float(p) for p in prices if p]
            if all(0.45 < p < 0.55 for p in float_prices if p > 0):
                continue

            # Get "Yes" price (probability of the event happening)
            yes_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "yes"), 0)
            if yes_idx >= len(prices):
                continue

            yes_price = float(prices[yes_idx])

            if yes_price < 0.02:  # Skip illiquid markets
                continue

            odds = round(1 / yes_price, 3)

            # Identify market type from question
            # Only match specific patterns to avoid BTTS, spreads, totals
            if "end in a draw" in question:
                draw_odds = odds
            elif question.startswith("will ") and " win" in question:
                # This is a "Will X win?" market - match team name
                if home_lower in question:
                    home_odds = odds
                elif away_lower in question:
                    away_odds = odds

        # Build combined 1x2 market
        if home_odds and away_odds:
            outcomes = [
                {"name": home, "odds": home_odds},
            ]
            if draw_odds:
                outcomes.append({"name": "Draw", "odds": draw_odds})
            outcomes.append({"name": away, "odds": away_odds})

            return [{
                "type": "1x2",
                "outcomes": outcomes
            }]

        return []
