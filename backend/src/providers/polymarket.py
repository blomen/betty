from typing import List, Any, Optional
from datetime import datetime, timezone, timedelta
import logging
import json
import re
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
    # Baseball
    'mlb': 'baseball',
    'mlb-2026': 'baseball',
    'npb': 'baseball',
    'kbo': 'baseball',
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

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            from ..core import HttpTransport
            transport = HttpTransport(
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
        super().__init__(config, transport)
        self.base_url = config.get("base_url", "https://gamma-api.polymarket.com")
        self.clob_url = config.get("clob_url", "https://clob.polymarket.com")
        self.game_bets_tag_id = config.get("params", {}).get("game_bets_tag_id", 100639)
        # Spread buffer: cents added to price to account for slippage (0 when using ask prices)
        self.spread_buffer = config.get("params", {}).get("spread_buffer_cents", 0) / 100.0
        self.use_clob_prices = config.get("params", {}).get("use_clob_prices", True)
        # Fill size in USD for depth-adjusted VWAP pricing (walks the order book)
        self.fill_size_usd = config.get("params", {}).get("fill_size_usd", 25)
        # Minimum available depth (USD) to consider a market liquid enough
        self.min_depth_usd = config.get("params", {}).get("min_depth_usd", 10)
        self._cached_events: list = None  # Cache all events to avoid re-fetching
        self._events_by_sport: dict = None  # Pre-indexed by sport for O(1) lookup
        self._clob_prices: dict = {}  # token_id -> depth-adjusted VWAP (populated during extraction)
        self._clob_depth: dict = {}  # token_id -> available depth in USD on ask side
        self._clob_bids: dict = {}   # token_id -> best bid price (highest bid)
        self._clob_asks: dict = {}   # token_id -> best ask price (lowest ask)

    def _get_sport_url(self, sport: str) -> str:
        return ""

    # Polymarket charges ~2% fee on net winnings
    POLY_FEE_RATE = 0.02

    def _price_to_odds(self, price: float) -> float:
        """Convert a probability price to effective decimal odds after fees.

        Price is already depth-adjusted (VWAP from order book) when CLOB is enabled.
        Polymarket charges ~2% on net winnings, so effective payout is reduced:
        raw_odds = 1/price, net_profit = (raw_odds - 1) * (1 - fee),
        effective_odds = 1 + net_profit = 1 + (1/price - 1) * (1 - fee).
        """
        adjusted = min(price + self.spread_buffer, 0.99)
        if adjusted <= 0.01:
            return 100.0
        raw_odds = 1 / adjusted
        effective_odds = 1 + (raw_odds - 1) * (1 - self.POLY_FEE_RATE)
        return round(effective_odds, 3)

    def _is_liquid(self, token_id: str) -> bool:
        """Check if a token has sufficient order book depth."""
        if not token_id or not self.use_clob_prices:
            return True  # Can't check depth without CLOB, allow through
        depth = self._clob_depth.get(token_id, 0)
        return depth >= self.min_depth_usd

    def _get_clob_price(self, token_id: str, gamma_price: float) -> float:
        """Get depth-adjusted VWAP for a token: CLOB book price if available, else Gamma."""
        return self._clob_prices.get(token_id, gamma_price)

    def _get_clob_depth_usd(self, token_id: str) -> float:
        """Get total available depth in USD on the ask side for a token."""
        return self._clob_depth.get(token_id, 0)

    def _build_outcome(self, name: str, price: float, token_id: str = None, **extra) -> dict:
        """Build an outcome dict with odds and optional CLOB microstructure data."""
        outcome = {"name": name, "odds": self._price_to_odds(price)}
        if token_id:
            bid = self._clob_bids.get(token_id)
            ask = self._clob_asks.get(token_id)
            depth = self._clob_depth.get(token_id)
            if bid is not None:
                outcome["bid"] = bid
            if ask is not None:
                outcome["ask"] = ask
            if depth is not None:
                outcome["depth_usd"] = depth
        outcome.update(extra)
        return outcome

    @staticmethod
    def _calc_vwap_from_asks(asks: list[dict], fill_size_usd: float) -> tuple[float, float]:
        """Walk the ask side of the order book to calculate depth-adjusted VWAP.

        Args:
            asks: List of {"price": "0.46", "size": "150"} sorted ascending by price.
            fill_size_usd: Target fill amount in USD.

        Returns:
            (vwap_price, total_depth_usd): The volume-weighted average price to fill
            the target size, and the total available depth in USD.
        """
        total_cost = 0.0
        total_shares = 0.0
        total_depth_usd = 0.0

        for level in asks:
            try:
                price = float(level["price"])
                size = float(level["size"])
            except (ValueError, TypeError, KeyError):
                continue
            if price <= 0 or size <= 0:
                continue

            level_cost = price * size  # USD cost to fill this entire level
            total_depth_usd += level_cost

            if total_shares * (total_cost / total_shares if total_shares else price) < fill_size_usd:
                # How many more USD do we need?
                remaining_usd = fill_size_usd - (total_cost if total_cost else 0)
                fillable_cost = min(level_cost, remaining_usd)
                fillable_shares = fillable_cost / price
                total_cost += fillable_cost
                total_shares += fillable_shares

        if total_shares <= 0:
            return 0.0, total_depth_usd

        vwap = total_cost / total_shares
        return vwap, total_depth_usd

    async def _fetch_clob_books(self, token_ids: list[str]):
        """Fetch order book depth for each token and compute VWAP pricing.

        Uses GET /book?token_id=XXX to get the full order book, then walks the
        ask side to calculate the volume-weighted average price for fill_size_usd.
        This replaces the old POST /prices approach that only returned top-of-book.

        Stores results in:
        - self._clob_prices: token_id -> VWAP price (depth-adjusted)
        - self._clob_depth: token_id -> total ask-side depth in USD
        """
        import aiohttp
        import asyncio

        if not token_ids or not self.use_clob_prices:
            return

        unique_tokens = list(set(token_ids))
        semaphore = asyncio.Semaphore(20)  # CLOB allows 1500/10s
        fill_size = self.fill_size_usd

        async def fetch_book(session: aiohttp.ClientSession, token_id: str):
            async with semaphore:
                try:
                    url = f"{self.clob_url}/book"
                    params = {"token_id": token_id}
                    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            asks = data.get("asks", [])
                            bids = data.get("bids", [])
                            if asks:
                                vwap, depth_usd = self._calc_vwap_from_asks(asks, fill_size)
                                # Always store depth so _is_liquid works correctly
                                self._clob_depth[token_id] = depth_usd
                                # Only use CLOB VWAP when in valid range; otherwise
                                # _get_clob_price falls back to Gamma mid-price
                                if 0.01 < vwap < 0.99:
                                    self._clob_prices[token_id] = vwap
                                # Extract best ask (lowest price on ask side)
                                try:
                                    best_ask = min(float(a["price"]) for a in asks if float(a.get("price", 0)) > 0)
                                    if 0.01 < best_ask < 0.99:
                                        self._clob_asks[token_id] = best_ask
                                except (ValueError, TypeError, KeyError):
                                    pass
                            # Extract best bid (highest price on bid side)
                            if bids:
                                try:
                                    best_bid = max(float(b["price"]) for b in bids if float(b.get("price", 0)) > 0)
                                    if 0.01 < best_bid < 0.99:
                                        self._clob_bids[token_id] = best_bid
                                except (ValueError, TypeError, KeyError):
                                    pass
                        elif resp.status != 404:
                            logger.debug(f"[{self.provider_id}] CLOB /book returned {resp.status} for {token_id[:12]}...")
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] CLOB /book failed for {token_id[:12]}...: {e}")

        try:
            async with aiohttp.ClientSession() as session:
                # Process in chunks to avoid overwhelming the connection pool
                CHUNK_SIZE = 50
                for i in range(0, len(unique_tokens), CHUNK_SIZE):
                    chunk = unique_tokens[i:i + CHUNK_SIZE]
                    await asyncio.gather(*[fetch_book(session, tid) for tid in chunk], return_exceptions=True)

            thin_count = sum(1 for d in self._clob_depth.values() if d < self.min_depth_usd)
            logger.debug(
                f"[{self.provider_id}] CLOB book depth: {len(self._clob_prices)}/{len(unique_tokens)} tokens "
                f"(fill_size=${fill_size}, thin_markets={thin_count})"
            )
        except Exception as e:
            logger.warning(f"[{self.provider_id}] CLOB book fetch failed, falling back to Gamma prices: {e}")

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

        # Phase 1b: Catch-up — also fetch recently closed events (last 48h)
        # Prevents data loss when extraction gaps occur (e.g., scheduler downtime).
        # Polymarket events close immediately on game resolution, so any extraction
        # gap means permanently missed events unless we catch up here.
        seen_ids = {item.get("id") for item in all_raw}
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        closed_offset = 0
        closed_count = 0
        while True:
            closed_params = {
                "active": "true",
                "closed": "true",
                "tag_id": self.game_bets_tag_id,
                "end_date_min": cutoff,
                "order": "endDate",
                "ascending": "false",
                "limit": page_limit,
                "offset": closed_offset,
            }
            url = f"{self.base_url}/events"
            closed_data = await self.transport.get(url, params=closed_params)
            if not closed_data:
                break
            for item in closed_data:
                if item.get("id") not in seen_ids:
                    all_raw.append(item)
                    seen_ids.add(item.get("id"))
                    closed_count += 1
            if len(closed_data) < page_limit:
                break
            closed_offset += page_limit
        if closed_count:
            logger.info(
                f"[{self.provider_id}] Catch-up: added {closed_count} recently closed events"
            )

        # Phase 2: Collect CLOB token IDs from markets that pass basic filters
        # Pre-filtering avoids fetching prices for markets we'll discard anyway
        if self.use_clob_prices and all_raw:
            needed_token_ids = []
            for item in all_raw:
                for m in item.get("markets", []):
                    # Skip low-volume markets (same filter as _parse_market)
                    try:
                        vol = float(m.get("volume", 0) or 0)
                    except (ValueError, TypeError):
                        vol = 0
                    if vol < self.MIN_VOLUME:
                        continue
                    # Skip exact 50/50 (no trading activity)
                    try:
                        prices_raw = m.get("outcomePrices", "[]")
                        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                        prices = [float(p) for p in prices]
                        if all(p == 0.5 for p in prices if p > 0):
                            continue
                        if not any(0.02 < p < 0.98 for p in prices):
                            continue
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue
                    # Market passes basic filters — collect its token IDs
                    raw_ids = m.get("clobTokenIds", "[]")
                    try:
                        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else (raw_ids or [])
                        needed_token_ids.extend(str(t) for t in ids if t)
                    except (json.JSONDecodeError, TypeError):
                        pass
            logger.debug(
                f"[{self.provider_id}] Pre-filtered to {len(set(needed_token_ids))} tokens "
                f"(from {sum(len(m.get('markets', [])) for m in all_raw)} total markets)"
            )
            await self._fetch_clob_books(needed_token_ids)

        # Phase 3: Parse events (using CLOB ask prices)
        all_events = self._parse_all(all_raw)

        logger.debug(
            f"[{self.provider_id}] Fetched {len(all_events)} events total from Polymarket "
            f"({page} pages, clob_depth_prices={len(self._clob_prices)})"
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
        event_slug = item.get("slug", "")
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

        # Skip standalone game/map events (e.g., "Team A vs Team B (Game 1)")
        # These are individual game winners that would overwrite the series moneyline
        # if stored under the same canonical event. Map-level markets are handled
        # as child_moneyline sub-markets within the main series event instead.
        title_lower = title.lower()
        if re.search(r'[\(\-:]\s*(?:game|map)\s*\d', title_lower):
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

        # Collect esports map winner markets (child_moneyline → moneyline_m{N})
        # Keep highest-volume per map number
        map_winner_by_num: dict[int, tuple] = {}
        for m_data in item.get("markets", []):
            mw = self._parse_map_winner_market(m_data, home, away)
            if mw:
                vol = float(m_data.get("volume", 0) or 0)
                # Extract map number from type (moneyline_m1 → 1)
                map_num = int(mw["type"].split("_m")[1])
                if map_num not in map_winner_by_num or vol > map_winner_by_num[map_num][1]:
                    map_winner_by_num[map_num] = (mw, vol)
        for mw, _ in map_winner_by_num.values():
            markets.append(mw)

        # Collect spread/total markets (both football and non-football)
        # Also collect esports map_handicap as spread
        spread_candidates = []
        total_candidates = []
        for m_data in item.get("markets", []):
            s = self._parse_spread_market(m_data, home, away)
            if not s:
                s = self._parse_map_handicap_market(m_data, home, away)
            if s:
                spread_candidates.append((s, float(m_data.get("volume", 0) or 0)))
            t = self._parse_total_market(m_data)
            if t:
                total_candidates.append((t, float(m_data.get("volume", 0) or 0)))

        # Deduplicate spread markets per absolute point — Polymarket often has
        # two questions for the same spread (e.g. "Spread: Bruins (-1.5)" and
        # "Spread: Penguins (+1.5)") which map to identical DB keys but carry
        # different prices. Keep highest-volume per point.
        spread_by_point: dict[float, tuple] = {}
        for s, vol in spread_candidates:
            abs_pt = abs(s["outcomes"][0]["point"]) if s["outcomes"] else 0
            if abs_pt not in spread_by_point or vol > spread_by_point[abs_pt][1]:
                spread_by_point[abs_pt] = (s, vol)
        for s, _ in spread_by_point.values():
            markets.append(s)

        # Deduplicate total markets per point — same event can have multiple
        # O/U markets at the same line (e.g. one active, one dead 50/50).
        # Keep highest-volume per point to avoid stale prices overwriting real ones.
        total_by_point: dict[float, tuple] = {}
        for t, vol in total_candidates:
            pt = t["outcomes"][0]["point"] if t["outcomes"] else 0
            if pt not in total_by_point or vol > total_by_point[pt][1]:
                total_by_point[pt] = (t, vol)
        for t, _ in total_by_point.values():
            markets.append(t)

        if not markets:
            return None  # Skip events without valid markets

        # Inject event_slug + Polymarket display names into provider_meta for all markets
        for m in markets:
            meta = m.get("provider_meta", {})
            if event_slug:
                meta["event_slug"] = event_slug
            if home:
                meta["poly_home"] = home
            if away:
                meta["poly_away"] = away
            m["provider_meta"] = meta

        # Parse live score data from Polymarket's sports data feed
        live_state = self._parse_live_state(item)

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
            live_state=live_state,
        )

    def _parse_live_state(self, item: dict) -> dict:
        """Extract live score data from Polymarket event.

        Polymarket's sports data feed provides these fields on events with gameId:
        - score: "49-67" (basketball), "7-6(7-3), 6-7(5-7), 6-3" (tennis),
                 "000-000|1-2|Bo3" (esports)
        - period: "FT", "HT", "1H", "2H", etc.
        - live: true/false
        - ended: true when game is over
        - elapsed: minutes elapsed in current period
        """
        live_state = {}

        score_str = item.get("score")
        if score_str and isinstance(score_str, str):
            live_state["score_raw"] = score_str
            parsed = self._parse_score_string(score_str)
            if parsed:
                live_state["home_score"] = parsed[0]
                live_state["away_score"] = parsed[1]

        period = item.get("period")
        if period:
            try:
                live_state["match_period"] = int(period)
            except (ValueError, TypeError):
                live_state["match_status"] = str(period)  # "Bot 6th" etc.

        elapsed = item.get("elapsed")
        if elapsed:
            try:
                live_state["match_minute"] = int(elapsed)
            except (ValueError, TypeError):
                pass

        if item.get("ended") is True:
            live_state["match_status"] = "finished"
        elif item.get("live") is True:
            live_state["match_status"] = "started"

        return live_state

    @staticmethod
    def _parse_score_string(score_str: str) -> tuple[int, int] | None:
        """Parse score string into (home, away) integers.

        Handles multiple formats:
        - Simple: "49-67" → (49, 67)
        - Tennis: "7-6(7-3), 6-7(5-7), 6-3" → count sets won → (2, 1)
        - Esports BO: "000-000|2-1|Bo3" → (2, 1)  [middle segment is map/game score]
        """
        s = score_str.strip()
        if not s:
            return None

        # Format 1: Esports "000-000|2-1|Bo3" or "000-000|3-1|Bo5"
        if "|" in s:
            parts = s.split("|")
            for part in parts:
                # Find the segment that looks like a series score (small numbers)
                m = re.match(r"^(\d{1,2})-(\d{1,2})$", part.strip())
                if m and part.strip() != parts[0].strip():  # Skip the first "000-000" segment
                    return int(m.group(1)), int(m.group(2))
            return None

        # Format 2: Tennis "7-6(7-3), 6-7(5-7), 6-3" → count sets won
        if "," in s or "(" in s:
            # Split by comma for individual sets
            sets = [x.strip() for x in s.split(",")]
            home_sets = 0
            away_sets = 0
            for set_score in sets:
                # Extract main set score, ignoring tiebreak in parens
                m = re.match(r"(\d+)-(\d+)", set_score)
                if m:
                    h, a = int(m.group(1)), int(m.group(2))
                    if h > a:
                        home_sets += 1
                    elif a > h:
                        away_sets += 1
            if home_sets > 0 or away_sets > 0:
                return home_sets, away_sets
            return None

        # Format 3: Simple "49-67"
        parts = s.split("-")
        if len(parts) == 2:
            try:
                return int(parts[0].strip()), int(parts[1].strip())
            except ValueError:
                pass

        return None

    async def fetch_resolved(self, limit: int = 3000) -> list[dict]:
        """Fetch closed Polymarket game-bets events with scores and resolution.

        Paginates through all closed game-bets events (typically 2000-3000)
        to find settled events with final scores and winner resolution.

        Returns list of dicts with:
        - title, home_team, away_team, sport, league, slug
        - home_score, away_score, match_status
        - winner_team (from outcomePrices resolution)
        """
        PAGE_SIZE = 500
        all_raw = []
        offset = 0

        while len(all_raw) < limit:
            params = {
                "closed": "true",
                "tag_id": self.game_bets_tag_id,
                "order": "endDate",
                "ascending": "false",
                "limit": PAGE_SIZE,
                "offset": offset,
            }

            url = f"{self.base_url}/events"
            data = await self.transport.get(url, params=params)
            if not data:
                break
            all_raw.extend(data)
            if len(data) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        resolved = []
        for item in all_raw:
            title = item.get("title", "")
            home, away = self._parse_teams(title)
            if not home or not away:
                continue

            live_state = self._parse_live_state(item)
            if live_state.get("match_status") != "finished":
                # Only include definitively ended events
                # Check if all markets are resolved via outcomePrices
                has_resolved_market = False
                for m in item.get("markets", []):
                    prices_raw = m.get("outcomePrices", "[]")
                    try:
                        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                        prices = [float(p) for p in prices]
                        # Resolved if any price is >= 0.99 (winner)
                        if any(p >= 0.99 for p in prices):
                            has_resolved_market = True
                            break
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass
                if not has_resolved_market:
                    continue
                live_state["match_status"] = "finished"

            sport, league = self._get_sport_league(item)

            # Determine winner from outcomePrices on moneyline market
            winner_team = None
            for m in item.get("markets", []):
                q = m.get("question", "")
                # Skip spread/total markets — only use moneyline for winner
                if "O/U" in q or "Spread" in q or "Over" in q or "Under" in q:
                    continue
                prices_raw = m.get("outcomePrices", "[]")
                outcomes_raw = m.get("outcomes", "[]")
                try:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                    prices = [float(p) for p in prices]
                    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
                    if any(p >= 0.99 for p in prices):
                        winner_idx = next(i for i, p in enumerate(prices) if p >= 0.99)
                        if winner_idx < len(outcomes):
                            winner_team = outcomes[winner_idx]
                        break
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Extract resolved total/spread market outcomes from outcomePrices
            resolved_markets = {}
            for m in item.get("markets", []):
                q = m.get("question", "")
                prices_raw = m.get("outcomePrices", "[]")
                outcomes_raw = m.get("outcomes", "[]")
                try:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                    prices = [float(p) for p in prices]
                    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])

                    if len(prices) != 2 or not any(p >= 0.99 for p in prices):
                        continue
                    winner_idx = next(i for i, p in enumerate(prices) if p >= 0.99)
                    if winner_idx >= len(outcomes):
                        continue

                    # Total market: "Team vs Team: O/U 226.5"
                    if " O/U " in q and "1H O/U" not in q:
                        point_match = re.search(r'O/U\s+(\d+\.?\d*)', q)
                        if point_match:
                            pt = float(point_match.group(1))
                            winner_name = outcomes[winner_idx].lower().strip()
                            if winner_name in ("over", "under"):
                                # Format key to match bet.market (e.g., "total_226.5")
                                pt_str = str(int(pt)) if pt == int(pt) else str(pt)
                                resolved_markets[f"total_{pt_str}"] = winner_name

                    # Spread market: "Spread: TeamName (-2.5)"
                    elif q.startswith("Spread:") and not q.startswith("1H Spread"):
                        point_match = re.search(r'\(([+-]?\d+\.?\d*)\)', q)
                        team_match = re.search(r'Spread:\s*(.+?)\s*\(', q)
                        if point_match and team_match:
                            favored_point = float(point_match.group(1))
                            favored_team = team_match.group(1).strip()
                            from ..matching import normalize_outcome
                            favored_side = normalize_outcome(favored_team, home, away)
                            winner_outcome = outcomes[winner_idx]
                            winner_side = normalize_outcome(winner_outcome, home, away)
                            if winner_side in ("home", "away") and favored_side in ("home", "away"):
                                # Store with home-perspective point (matching DB convention)
                                if favored_side == "home":
                                    home_point = favored_point
                                else:
                                    home_point = -favored_point
                                pt_str = str(int(home_point)) if home_point == int(home_point) else str(home_point)
                                resolved_markets[f"spread_{pt_str}"] = winner_side
                except (json.JSONDecodeError, ValueError, TypeError, StopIteration):
                    pass

            resolved.append({
                "polymarket_id": str(item.get("id", "")),
                "slug": item.get("slug", ""),
                "title": title,
                "home_team": home,
                "away_team": away,
                "sport": sport,
                "league": league,
                "start_time": item.get("startTime"),
                "home_score": live_state.get("home_score"),
                "away_score": live_state.get("away_score"),
                "match_status": "finished",
                "winner_team": winner_team,
                "resolved_markets": resolved_markets or None,
            })

        logger.info(f"[{self.provider_id}] Fetched {len(resolved)} resolved events (from {len(all_raw)} closed)")
        return resolved

    def _parse_teams(self, title: str) -> tuple[str, str]:
        """Extract home and away teams from event title."""
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
        elif "baseball" in tags or "mlb" in tags:
            return "baseball"
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
        """Parse a single market (series/match moneyline only - skips totals/spreads/maps)."""
        try:
            # Skip child_moneyline markets — these are map/game-level winners
            # handled exclusively by _parse_map_winner_market()
            smt = data.get("sportsMarketType", "")
            if smt == "child_moneyline":
                return None

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
                            # Skip if either side has insufficient depth
                            if not self._is_liquid(yes_token) or not self._is_liquid(no_token):
                                return None
                            yes_price = self._get_clob_price(yes_token, prices[yes_idx]) if yes_token else prices[yes_idx]
                            no_price = self._get_clob_price(no_token, prices[no_idx]) if no_token else prices[no_idx]
                            # Re-check after CLOB — VWAP can push price outside valid range
                            if not (0.02 < yes_price < 0.98) or not (0.02 < no_price < 0.98):
                                return None
                            return {
                                "type": "moneyline",
                                "outcomes": [
                                    self._build_outcome(matched_team, yes_price, yes_token),
                                    self._build_outcome(other_team, no_price, no_token),
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
                    if not self._is_liquid(token_id):
                        return None  # Skip entire market if any outcome is illiquid
                    price = self._get_clob_price(token_id, p) if token_id else p
                    # Re-check after CLOB — VWAP can push price outside valid range
                    if not (0.02 < price < 0.98):
                        continue
                    formatted_outcomes.append(self._build_outcome(name, price, token_id))

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
            # Determine the other team's normalized label
            other_norm = "away" if favored_norm == "home" else "home"
            result_outcomes = []
            for i, (name, p) in enumerate(zip(outcomes, prices)):
                if p <= 0.02:
                    continue
                # "Yes" = favored team covers the spread, "No" = other team.
                # Do NOT use normalize_outcome on "Yes"/"No" — the keyword fast
                # path always maps Yes→home which is wrong when the question
                # names the away team.
                name_lower = name.strip().lower()
                if name_lower == "yes":
                    norm = favored_norm
                elif name_lower == "no":
                    norm = other_norm
                else:
                    norm = normalize_outcome(name, home, away)
                if norm not in ('home', 'away'):
                    continue
                # Favored team gets the point from the question, other gets opposite
                if norm == favored_norm:
                    point = favored_point
                else:
                    point = -favored_point
                token_id = clob_ids[i] if i < len(clob_ids) else None
                if not self._is_liquid(token_id):
                    return None
                price = self._get_clob_price(token_id, p) if token_id else p
                if not (0.02 < price < 0.98):
                    continue
                result_outcomes.append(self._build_outcome(norm, price, token_id, point=point))

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
                if not self._is_liquid(token_id):
                    return None
                if name_lower == "over":
                    price = self._get_clob_price(token_id, p) if token_id else p
                    if not (0.02 < price < 0.98):
                        continue
                    result_outcomes.append(self._build_outcome("over", price, token_id, point=point))
                elif name_lower == "under":
                    price = self._get_clob_price(token_id, p) if token_id else p
                    if not (0.02 < price < 0.98):
                        continue
                    result_outcomes.append(self._build_outcome("under", price, token_id, point=point))

            if len(result_outcomes) != 2:
                return None

            return {"type": "total", "outcomes": result_outcomes}
        except Exception:
            return None

    # Map number extraction from question text for child_moneyline
    _MAP_PATTERNS = {
        "map 1": 1, "map 2": 2, "map 3": 3, "map 4": 4, "map 5": 5,
        "game 1": 1, "game 2": 2, "game 3": 3, "game 4": 4, "game 5": 5,
    }

    def _parse_map_winner_market(self, data: dict, home: str, away: str) -> Optional[dict]:
        """Parse an esports map/game winner market (child_moneyline).

        Detection: sportsMarketType == 'child_moneyline' or question contains
        'Map N Winner' / 'Game N Winner' patterns.
        Returns moneyline_m{N} market type.
        """
        try:
            smt = data.get("sportsMarketType", "")
            if smt != "child_moneyline":
                return None

            question = data.get("question", "")
            question_lower = question.lower()

            # Determine map number from question
            map_num = None
            for pattern, num in self._MAP_PATTERNS.items():
                if pattern in question_lower:
                    map_num = num
                    break
            if not map_num:
                return None

            # Volume filter
            volume = float(data.get("volume", 0) or 0)
            if volume < self.MIN_VOLUME:
                return None

            # Parse outcome prices
            prices_raw = data.get("outcomePrices", "[]")
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
            prices = [float(p) for p in prices]

            outcomes_raw = data.get("outcomes", [])
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])

            if len(outcomes) != 2 or len(prices) != 2:
                return None

            # Skip dead/illiquid markets
            if all(p == 0.5 for p in prices):
                return None
            if not any(0.02 < p < 0.98 for p in prices):
                return None

            clob_ids = self._parse_clob_token_ids(data)
            from ..matching import normalize_outcome
            formatted_outcomes = []
            for i, (name, p) in enumerate(zip(outcomes, prices)):
                if p <= 0.02:
                    continue
                norm = normalize_outcome(name, home, away)
                if norm not in ('home', 'away'):
                    continue
                token_id = clob_ids[i] if i < len(clob_ids) else None
                if not self._is_liquid(token_id):
                    return None
                price = self._get_clob_price(token_id, p) if token_id else p
                if not (0.02 < price < 0.98):
                    continue
                formatted_outcomes.append(self._build_outcome(norm, price, token_id))

            if len(formatted_outcomes) != 2:
                return None

            return {"type": f"moneyline_m{map_num}", "outcomes": formatted_outcomes}
        except Exception:
            return None

    def _parse_map_handicap_market(self, data: dict, home: str, away: str) -> Optional[dict]:
        """Parse an esports map handicap market.

        Detection: sportsMarketType == 'map_handicap'.
        Format: "Map Handicap: TeamA (-1.5) vs TeamB (+1.5)"
        or "Game Handicap: AL (-1.5) vs Weibo Gaming (+1.5)"
        Outcomes are team names (not Yes/No).
        Maps to 'spread' market type (same as Pinnacle period 0 spread).
        """
        try:
            smt = data.get("sportsMarketType", "")
            if smt != "map_handicap":
                return None

            question = data.get("question", "")

            # Volume filter
            volume = float(data.get("volume", 0) or 0)
            if volume < self.MIN_VOLUME:
                return None

            # Extract the first point value (favored team's handicap)
            point_match = re.search(r'\(([+-]?\d+\.?\d*)\)', question)
            if not point_match:
                return None
            favored_point = float(point_match.group(1))

            # Parse outcomes and prices — outcomes are full team names
            outcomes_raw = data.get("outcomes", "[]")
            prices_raw = data.get("outcomePrices", "[]")
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])

            if len(outcomes) != 2 or len(prices) != 2:
                return None

            prices = [float(p) for p in prices]

            if all(p == 0.5 for p in prices):
                return None
            if not any(0.02 < p < 0.98 for p in prices):
                return None

            # Normalize outcome team names to home/away
            from ..matching import normalize_outcome
            clob_ids = self._parse_clob_token_ids(data)
            result_outcomes = []
            for i, (name, p) in enumerate(zip(outcomes, prices)):
                if p <= 0.02:
                    continue
                norm = normalize_outcome(name, home, away)
                if norm not in ('home', 'away'):
                    continue
                # First outcome in question gets favored_point, second gets opposite
                # Determine from position: outcome[0] = favored team (listed first in question)
                point = favored_point if i == 0 else -favored_point
                token_id = clob_ids[i] if i < len(clob_ids) else None
                if not self._is_liquid(token_id):
                    return None
                price = self._get_clob_price(token_id, p) if token_id else p
                if not (0.02 < price < 0.98):
                    continue
                result_outcomes.append(self._build_outcome(norm, price, token_id, point=point))

            if len(result_outcomes) != 2:
                return None

            return {"type": "spread", "outcomes": result_outcomes}
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
        home_price = None
        draw_price = None
        away_price = None
        home_token = None
        draw_token = None
        away_token = None

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

            if yes_price < 0.02 or yes_price > 0.98:  # Skip illiquid/resolved markets
                continue

            # Extract Yes token ID for this sub-market
            clob_ids = self._parse_clob_token_ids(m)
            token_id = clob_ids[yes_idx] if yes_idx < len(clob_ids) else None

            if not self._is_liquid(token_id):
                continue  # Skip illiquid sub-market

            # Use CLOB depth-adjusted VWAP if available
            price = self._get_clob_price(token_id, yes_price) if token_id else yes_price

            # Re-check after CLOB adjustment — VWAP can push price outside valid range
            if price < 0.02 or price > 0.98:
                continue

            odds = self._price_to_odds(price)

            # Identify market type from question
            # Only match specific patterns to avoid BTTS, spreads, totals
            if "end in a draw" in question:
                draw_odds = odds
                draw_price = price
                draw_token = token_id
            elif question.startswith("will ") and " win" in question:
                # This is a "Will X win?" market - match team name
                if home_lower in question:
                    home_odds = odds
                    home_price = price
                    home_token = token_id
                elif away_lower in question:
                    away_odds = odds
                    away_price = price
                    away_token = token_id

        # Build combined 1x2 market
        if home_odds and away_odds:
            outcomes = [
                self._build_outcome(home, home_price, home_token),
            ]
            if draw_odds:
                outcomes.append(self._build_outcome("Draw", draw_price, draw_token))
            outcomes.append(self._build_outcome(away, away_price, away_token))

            return [{
                "type": "1x2",
                "outcomes": outcomes
            }]

        return []
