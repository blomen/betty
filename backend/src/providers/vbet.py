"""
Vbet Retriever - BetConstruct Swarm WebSocket-based extraction

Vbet uses the BetConstruct platform with the Swarm WebSocket API.
Events are fetched via direct WebSocket commands to the Swarm server.

Protocol:
1. Connect to wss://eu-swarm-newm.vbet.se/
2. Send request_session with site_id=1088
3. Send "get" commands with source="betting", what/where filters
4. Parse nested response: sport > region > competition > game > market > event

Market type mapping:
- P1XP2 → 1x2 (3-way)
- P1P2 → moneyline (2-way)
- OverUnder → total
- Handicap / AsianHandicap → spread

Event outcome types:
- P1 → home, X → draw, P2 → away
- Over → over, Under → under
"""

from typing import Dict, Any, List, Optional
import json
import logging
from datetime import datetime, timezone
import asyncio

import websockets

from ..core import Retriever, StandardEvent
from ..core.exceptions import RetryableError
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class VbetRetriever(Retriever):
    """
    BetConstruct Swarm WebSocket retriever for Vbet.

    Uses direct WebSocket connection (no browser needed).
    Sends structured JSON commands to query prematch events with odds.
    """

    # BetConstruct sport alias → our sport key
    SPORT_ALIAS_MAP = {
        "Soccer": "football",
        "Basketball": "basketball",
        "IceHockey": "ice_hockey",
        "Tennis": "tennis",
        "Baseball": "baseball",
        "AmericanFootball": "american_football",
        "Handball": "handball",
        "Volleyball": "volleyball",
        "Rugby": "rugby",
        "TableTennis": "table_tennis",
        "MMA": "mma",
        "Boxing": "boxing",
        "Darts": "darts",
        "Snooker": "snooker",
        "Cricket": "cricket",
        "Esports": "esports",
        "CyberFootball": "esports",
        "Floorball": "floorball",
        "Futsal": "futsal",
    }

    # Reverse map: our sport key → BetConstruct alias
    SPORT_KEY_TO_ALIAS = {v: k for k, v in SPORT_ALIAS_MAP.items()}

    # BetConstruct market types we care about
    MARKET_TYPE_MAP = {
        "P1XP2": "1x2",
        "P1P2": "moneyline",
        "OverUnder": "total",
        "Handicap": "spread",
        "AsianHandicap": "spread",
    }

    # BetConstruct event outcome types → our outcome names
    OUTCOME_MAP = {
        "P1": "home",
        "X": "draw",
        "P2": "away",
        "Over": "over",
        "Under": "under",
        "W1": "home",
        "W2": "away",
        "1": "home",
        "2": "away",
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.ws_url = config.get("ws_url", "wss://eu-swarm-newm.vbet.se/")
        self.site_id = config.get("site_id", 1088)
        self._rid_counter = 1000

    def _next_rid(self) -> int:
        """Generate unique request ID."""
        self._rid_counter += 1
        return self._rid_counter

    def _get_sport_url(self, sport: str) -> str:
        """Not used — WebSocket-based extraction."""
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — we override extract() completely."""
        return []

    async def _ws_request(self, ws, command: dict) -> dict:
        """Send a command and receive the response."""
        await ws.send(json.dumps(command))
        resp = await ws.recv()
        return json.loads(resp)

    def _parse_games(
        self,
        data: dict,
        sport: str,
        market_types: List[str],
    ) -> List[StandardEvent]:
        """
        Parse nested Swarm response into StandardEvents.

        Response structure:
        data.data.sport.{id}.region.{id}.competition.{id}.game.{id}.market.{id}.event.{id}
        """
        events = []
        inner = data.get("data", {}).get("data", data.get("data", {}))

        sport_data = inner.get("sport", inner)
        if not isinstance(sport_data, dict):
            return events

        for sport_id, sport_obj in sport_data.items():
            regions = sport_obj.get("region", {})
            if not isinstance(regions, dict):
                continue

            for reg_id, region in regions.items():
                region_name = region.get("name", "")
                competitions = region.get("competition", {})
                if not isinstance(competitions, dict):
                    continue

                for comp_id, comp in competitions.items():
                    comp_name = comp.get("name", "")
                    league = f"{region_name} - {comp_name}" if region_name else comp_name
                    games = comp.get("game", {})
                    if not isinstance(games, dict):
                        continue

                    for game_id, game in games.items():
                        event = self._parse_single_game(
                            game, game_id, sport, league, market_types
                        )
                        if event:
                            events.append(event)

        return events

    def _parse_single_game(
        self,
        game: dict,
        game_id: str,
        sport: str,
        league: str,
        market_types: List[str],
    ) -> Optional[StandardEvent]:
        """Parse a single game object into a StandardEvent."""
        try:
            team1 = game.get("team1_name", "")
            team2 = game.get("team2_name", "")

            if not team1 or not team2:
                return None

            # Skip live games
            if game.get("is_live"):
                return None

            # Parse start time (Unix timestamp)
            start_ts = game.get("start_ts")
            start_time = None
            if start_ts:
                try:
                    start_time = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    pass

            # Normalize team names
            home_team = normalize_team_name(team1)
            away_team = normalize_team_name(team2)

            # Parse markets
            markets = []
            game_markets = game.get("market", {})
            if not isinstance(game_markets, dict):
                return None

            # Collect all markets, then deduplicate spread/total to main line only
            # BetConstruct returns many alternate lines; keep lowest order (main line)
            spread_candidates = []  # [(order, mkt_base, market_dict)]
            total_candidates = []

            for mkt_id, market in game_markets.items():
                mkt_type_raw = market.get("type", "")
                mkt_type = self.MARKET_TYPE_MAP.get(mkt_type_raw)

                if not mkt_type:
                    continue  # Skip unsupported market types

                # Get point/base for spread and total
                mkt_base = market.get("base")
                point = None
                if mkt_type in ("spread", "total") and mkt_base is not None:
                    try:
                        point = float(mkt_base)
                    except (ValueError, TypeError):
                        pass

                # Parse event outcomes
                outcomes = []
                market_events = market.get("event", {})
                if not isinstance(market_events, dict):
                    continue

                # Determine market order (lowest order = main line)
                min_order = 999
                for ev_id, ev in market_events.items():
                    order = ev.get("order", 999)
                    if order < min_order:
                        min_order = order

                for ev_id, ev in market_events.items():
                    price = ev.get("price")
                    if not price or price <= 1.0:
                        continue

                    ev_type = ev.get("type", "")
                    outcome_name = self.OUTCOME_MAP.get(ev_type)

                    if not outcome_name:
                        # Try matching by name against team names
                        ev_name = ev.get("name", "")
                        if ev_name.lower() == team1.lower() or normalize_team_name(ev_name) == home_team:
                            outcome_name = "home"
                        elif ev_name.lower() == team2.lower() or normalize_team_name(ev_name) == away_team:
                            outcome_name = "away"
                        elif ev_name.lower() in ("draw", "tie", "x"):
                            outcome_name = "draw"
                        else:
                            continue  # Can't map this outcome

                    outcome_dict = {"name": outcome_name, "odds": float(price)}
                    if point is not None:
                        # For spread, home gets negative point, away gets positive
                        if mkt_type == "spread":
                            if outcome_name == "home":
                                outcome_dict["point"] = -abs(point)
                            elif outcome_name == "away":
                                outcome_dict["point"] = abs(point)
                        else:
                            outcome_dict["point"] = point

                    outcomes.append(outcome_dict)

                if outcomes:
                    mkt_dict = {"type": mkt_type, "outcomes": outcomes}
                    if mkt_type == "spread":
                        spread_candidates.append((min_order, mkt_dict))
                    elif mkt_type == "total":
                        total_candidates.append((min_order, mkt_dict))
                    else:
                        markets.append(mkt_dict)

            # Keep only main line for spread/total (lowest order value)
            if spread_candidates:
                spread_candidates.sort(key=lambda x: x[0])
                markets.append(spread_candidates[0][1])
            if total_candidates:
                total_candidates.sort(key=lambda x: x[0])
                markets.append(total_candidates[0][1])

            if not markets:
                return None

            return StandardEvent(
                id=f"vbet_{game_id}",
                name=f"{team1} vs {team2}",
                provider=self.provider_id,
                sport=sport,
                league=league,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                markets=markets,
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse game {game_id}: {e}")
            return None

    WS_MAX_RETRIES = 3
    WS_BACKOFF_BASE = 2  # seconds: 2, 4, 8

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events via BetConstruct Swarm WebSocket.

        Connects to Swarm with retry+backoff, requests session, then fetches
        prematch events for the given sport with 1x2/moneyline + spread/total markets.
        """
        # Map our sport key to BetConstruct alias
        bc_alias = self.SPORT_KEY_TO_ALIAS.get(sport)
        if not bc_alias:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not mapped to BetConstruct alias")
            return []

        logger.debug(f"[{self.provider_id}] Starting extraction for {sport} (alias={bc_alias})")

        all_events = []
        last_err = None

        for attempt in range(self.WS_MAX_RETRIES):
            try:
                async with websockets.connect(
                    self.ws_url,
                    additional_headers={
                        "Origin": "https://www.vbet.se",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    },
                    max_size=10 * 1024 * 1024,
                    close_timeout=10,
                    open_timeout=15,
                ) as ws:
                    if attempt > 0:
                        logger.info(f"[{self.provider_id}] WebSocket connected on attempt {attempt + 1}")
                    return await self._fetch_sport(ws, sport, bc_alias, limit)
            except websockets.exceptions.ConnectionClosedError as e:
                logger.warning(f"[{self.provider_id}] WebSocket closed during {sport}: {e}")
                return all_events  # Return whatever was collected
            except Exception as e:
                last_err = e
                if attempt < self.WS_MAX_RETRIES - 1:
                    delay = self.WS_BACKOFF_BASE * (2 ** attempt)
                    logger.debug(
                        f"[{self.provider_id}] WebSocket attempt {attempt + 1}/{self.WS_MAX_RETRIES} "
                        f"for {sport} failed: {e}, retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted — raise so orchestrator sees this as a real failure
        error_msg = f"WebSocket failed for {sport} after {self.WS_MAX_RETRIES} attempts: {last_err}"
        logger.error(f"[{self.provider_id}] {error_msg}")
        raise RetryableError(error_msg)

    async def _fetch_sport(
        self, ws, sport: str, bc_alias: str, limit: int
    ) -> List[StandardEvent]:
        """Fetch events for a sport over an established WebSocket connection."""
        all_events = []

        # 1. Request session
        session_resp = await self._ws_request(ws, {
            "command": "request_session",
            "params": {
                "source": 42,
                "language": "eng",
                "site_id": self.site_id,
            },
        })

        if session_resp.get("code") != 0:
            logger.error(f"[{self.provider_id}] Session request failed: {session_resp}")
            return []

        logger.debug(f"[{self.provider_id}] Session established")

        # 2. Fetch 1x2/moneyline markets
        match_winner_resp = await self._ws_request(ws, {
            "command": "get",
            "params": {
                "source": "betting",
                "what": {
                    "sport": ["id", "name", "alias"],
                    "region": ["id", "name", "alias"],
                    "competition": ["id", "name"],
                    "game": [
                        "id", "team1_name", "team2_name", "start_ts",
                        "is_live", "type",
                    ],
                    "market": ["id", "type", "name", "base"],
                    "event": ["id", "name", "price", "type", "base", "order"],
                },
                "where": {
                    "sport": {"alias": bc_alias},
                    "game": {"type": {"@in": [0, 2]}},
                    "market": {"type": {"@in": ["P1XP2", "P1P2"]}},
                },
                "subscribe": False,
            },
            "rid": self._next_rid(),
        })

        if match_winner_resp.get("code") == 0:
            winner_events = self._parse_games(
                match_winner_resp, sport, ["P1XP2", "P1P2"]
            )
            if not winner_events:
                # WS returned success but no parseable events — log response structure
                inner = match_winner_resp.get("data", {})
                sport_keys = list(inner.get("data", inner).get("sport", {}).keys()) if isinstance(inner, dict) else []
                logger.warning(
                    f"[{self.provider_id}] {sport}: WS code=0 but 0 ML events parsed "
                    f"(sport_keys={sport_keys[:3]}, resp_keys={list(match_winner_resp.keys())[:5]})"
                )
            else:
                logger.debug(
                    f"[{self.provider_id}] {sport}: {len(winner_events)} events with 1x2/moneyline"
                )
            all_events.extend(winner_events)
        else:
            logger.warning(
                f"[{self.provider_id}] Match winner request failed: code={match_winner_resp.get('code')}"
            )

        # 3. Fetch spread/total markets
        spread_total_resp = await self._ws_request(ws, {
            "command": "get",
            "params": {
                "source": "betting",
                "what": {
                    "sport": ["id", "name", "alias"],
                    "region": ["id", "name"],
                    "competition": ["id", "name"],
                    "game": [
                        "id", "team1_name", "team2_name", "start_ts",
                        "is_live", "type",
                    ],
                    "market": ["id", "type", "name", "base"],
                    "event": ["id", "name", "price", "type", "base", "order"],
                },
                "where": {
                    "sport": {"alias": bc_alias},
                    "game": {
                        "type": {"@in": [0, 2]},
                    },
                    "market": {
                        "type": {"@in": ["OverUnder", "Handicap", "AsianHandicap"]},
                    },
                },
                "subscribe": False,
            },
            "rid": self._next_rid(),
        })

        if spread_total_resp.get("code") == 0:
            st_events = self._parse_games(
                spread_total_resp, sport, ["OverUnder", "Handicap", "AsianHandicap"]
            )
            logger.debug(
                f"[{self.provider_id}] {sport}: {len(st_events)} events with spread/total"
            )

            if not st_events and all_events:
                logger.info(
                    f"[{self.provider_id}] {sport}: 0 spread/total events but "
                    f"{len(all_events)} ML events — platform may not offer these markets"
                )

            # Merge spread/total markets into existing events
            event_map = {e.id: e for e in all_events}
            for st_event in st_events:
                if st_event.id in event_map:
                    event_map[st_event.id].markets.extend(st_event.markets)
                else:
                    all_events.append(st_event)
        else:
            logger.warning(
                f"[{self.provider_id}] Spread/total request failed: code={spread_total_resp.get('code')}"
            )

        # Apply limit
        if limit and len(all_events) > limit:
            all_events = all_events[:limit]

        logger.debug(f"[{self.provider_id}] {sport}: {len(all_events)} total events extracted")
        return all_events

    async def close(self):
        """No persistent resources to clean up for WebSocket-based extractor."""
        pass
