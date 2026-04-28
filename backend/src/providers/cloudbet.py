"""Cloudbet REST Feed API odds extractor.

Cloudbet exposes pre-match odds via a public affiliate REST API at
https://sports-api.cloudbet.com/pub/v2/odds.
Authentication uses an X-API-Key header with an affiliate API key.

Extraction flow:
  1. GET /sports/{sport_key} → list of competition keys
  2. For each competition: GET /competitions/{comp_key}?markets=... → events with odds
"""

import logging
import re
from typing import Any

from ..core.retriever import Retriever, StandardEvent
from ..core.transport import HttpTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

BASE_URL = "https://sports-api.cloudbet.com/pub/v2/odds"

# Statuses to skip (live, finished, cancelled, suspended)
_SKIP_STATUSES = {"TRADING_LIVE", "RESULTED", "CANCELLED", "SUSPENDED"}

# Market key suffix → normalized type
_MARKET_TYPE_MAP = {
    "match_odds": "1x2",
    "matchOdds": "1x2",
    "1x2": "1x2",
    "moneyline": "moneyline",
    "winner": "moneyline",
    "asian_handicap": "spread",
    "asianHandicap": "spread",
    "handicap": "spread",
    "set_handicap": "spread",
    "game_handicap": "spread",
    "total_goals": "total",
    "totalGoals": "total",
    "totals": "total",
    "total_sets": "total",
}

# Sport key mapping: internal → Cloudbet
_SPORT_MAP = {
    "football": "soccer",
    "basketball": "basketball",
    "ice_hockey": "ice-hockey",
    "american_football": "american-football",
    "tennis": "tennis",
    "baseball": "baseball",
    "mma": "mma",
    "boxing": "boxing",
    "esports": "esports",
}

# Markets to request per Cloudbet sport key
_SPORT_MARKETS = {
    "soccer": "soccer.match_odds,soccer.asian_handicap,soccer.total_goals",
    "basketball": "basketball.moneyline,basketball.handicap,basketball.totals",
    "ice-hockey": "ice_hockey.moneyline,ice_hockey.handicap,ice_hockey.totals",
    "american-football": "american_football.moneyline,american_football.handicap,american_football.totals",
    "tennis": "tennis.winner,tennis.set_handicap,tennis.total_sets",
    "baseball": "baseball.moneyline,baseball.handicap,baseball.totals",
    "mma": "mma.winner,mma.totals",
    "boxing": "boxing.winner,boxing.totals",
    "esports": "esports.moneyline",
}

_HANDICAP_RE = re.compile(r"handicap=(-?\d+(?:\.\d+)?)")
_TOTAL_RE = re.compile(r"total=(-?\d+(?:\.\d+)?)")


def _resolve_market_type(market_key: str) -> str | None:
    """Resolve a Cloudbet market key like 'soccer.asian_handicap' to a normalized type."""
    suffix = market_key.split(".")[-1]
    return _MARKET_TYPE_MAP.get(suffix)


def parse_selections_to_market(
    selections: list,
    market_key: str,
) -> dict | None:
    """Parse Cloudbet selections into a normalized market dict.

    Returns None if:
    - selections list is empty
    - any selection status is not SELECTION_ENABLED
    - market key is unrecognized
    """
    if not selections:
        return None

    # All selections must be enabled
    if not all(s.get("status") == "SELECTION_ENABLED" for s in selections):
        return None

    market_type = _resolve_market_type(market_key)
    if market_type is None:
        return None

    if market_type == "spread":
        return _parse_handicap(selections)
    elif market_type == "total":
        return _parse_totals(selections)
    else:
        return _parse_winner(selections, market_type)


def _parse_winner(selections: list, market_type: str) -> dict | None:
    """Parse match winner / 1x2 / moneyline selections."""
    outcomes = []
    for sel in selections:
        outcome_name = sel.get("outcome", "").lower()
        price = sel.get("price")
        if outcome_name in ("home", "away", "draw"):
            outcomes.append({"name": outcome_name, "odds": price})

    if not outcomes:
        return None

    return {"type": market_type, "outcomes": outcomes}


def _parse_handicap(selections: list) -> dict | None:
    """Parse Asian handicap selections, taking the main line (smallest absolute handicap)."""
    # Group by handicap value
    lines: dict[float, dict] = {}
    for sel in selections:
        params = sel.get("params", "")
        m = _HANDICAP_RE.search(params)
        if not m:
            continue
        hcp = float(m.group(1))
        outcome_name = sel.get("outcome", "").lower()
        price = sel.get("price")
        abs_hcp = abs(hcp)
        if abs_hcp not in lines:
            lines[abs_hcp] = {}
        lines[abs_hcp][outcome_name] = {"price": price, "raw_hcp": hcp}

    if not lines:
        return None

    # Take smallest absolute handicap as main line
    main_abs = min(lines.keys())
    line = lines[main_abs]

    if "home" not in line or "away" not in line:
        return None

    home_hcp = line["home"]["raw_hcp"]
    outcomes = [
        {"name": "home", "odds": line["home"]["price"], "point": home_hcp},
        {"name": "away", "odds": line["away"]["price"], "point": -home_hcp},
    ]
    return {"type": "spread", "outcomes": outcomes}


def _parse_totals(selections: list) -> dict | None:
    """Parse totals selections, taking the main line (smallest total value)."""
    # Group by total value
    lines: dict[float, dict] = {}
    for sel in selections:
        params = sel.get("params", "")
        m = _TOTAL_RE.search(params)
        if not m:
            continue
        total = float(m.group(1))
        outcome_name = sel.get("outcome", "").lower()
        price = sel.get("price")
        if total not in lines:
            lines[total] = {}
        lines[total][outcome_name] = price

    if not lines:
        return None

    # Take smallest total as main line
    main_total = min(lines.keys())
    line = lines[main_total]

    if "over" not in line or "under" not in line:
        return None

    outcomes = [
        {"name": "over", "odds": line["over"], "point": main_total},
        {"name": "under", "odds": line["under"], "point": main_total},
    ]
    return {"type": "total", "outcomes": outcomes}


def parse_event(
    event: dict,
    sport: str,
    provider_id: str,
) -> StandardEvent | None:
    """Parse a single Cloudbet event dict into a StandardEvent.

    Returns None for live/resulted/cancelled/suspended events, events
    without home/away teams, or events with no parseable markets.
    """
    status = event.get("status", "")
    if status in _SKIP_STATUSES:
        return None

    home_obj = event.get("home")
    away_obj = event.get("away")
    if not home_obj or not away_obj:
        return None

    home_raw = home_obj.get("name", "")
    away_raw = away_obj.get("name", "")
    if not home_raw or not away_raw:
        return None

    event_id = event.get("id", "")
    start_time = event.get("startTime") or event.get("cutoffTime") or ""
    home_team = normalize_team_name(home_raw)
    away_team = normalize_team_name(away_raw)
    event_name = f"{home_raw} vs {away_raw}"

    markets_data = event.get("markets") or {}
    markets = []
    for market_key, market_obj in markets_data.items():
        submarkets = (market_obj or {}).get("submarkets") or {}
        for _subkey, submarket in submarkets.items():
            selections = (submarket or {}).get("selections") or []
            market = parse_selections_to_market(selections, market_key)
            if market:
                markets.append(market)

    if not markets:
        return None

    return StandardEvent(
        id=f"{provider_id}_{event_id}",
        name=event_name,
        sport=sport,
        markets=markets,
        provider=provider_id,
        url=f"https://www.cloudbet.com/en/sports/{sport}/event/{event_id}",
        start_time=start_time,
        home_team=home_team,
        away_team=away_team,
    )


class CloudbetRetriever(Retriever):
    """Retriever for Cloudbet pre-match odds via REST Feed API.

    Requires an affiliate API key in config['api_key'] (or CLOUDBET_API_KEY env var).
    """

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            import os

            transport = HttpTransport(
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config,
                proxy=os.environ.get("PROXY_URL"),
            )
        super().__init__(config, transport)
        self._api_key = os.environ.get("CLOUDBET_API_KEY", config.get("api_key", ""))

    def _get_sport_url(self, sport: str) -> str:
        """Not used — extraction uses a two-step fetch inside extract()."""
        return ""

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        """Not used — parsing is done inside extract()."""
        return []

    def _headers(self) -> dict:
        return {"X-API-Key": self._api_key}

    async def extract(self, sport: str, limit: int = 0, **kwargs) -> list[StandardEvent]:
        """Extract pre-match events for a sport from Cloudbet REST API.

        Two-step process:
          1. Fetch competition list for the sport
          2. Fetch each competition's events with odds
        """
        sport_key = _SPORT_MAP.get(sport)
        if not sport_key:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not mapped for Cloudbet")
            return []

        # Step 1: get competitions
        sport_url = f"{BASE_URL}/sports/{sport_key}"
        sport_data = await self.transport.get(sport_url, headers=self._headers())
        if not sport_data:
            logger.warning(f"[{self.provider_id}] No sport data for '{sport}'")
            return []

        # Competitions are nested under categories
        competitions = []
        for category in sport_data.get("categories") or []:
            for comp in category.get("competitions") or []:
                if comp.get("eventCount", 0) > 0:
                    competitions.append(comp)
        if not competitions:
            logger.debug(f"[{self.provider_id}] No competitions found for sport '{sport}'")
            return []
        logger.info(f"[{self.provider_id}] {sport}: {len(competitions)} active competitions")

        events: list[StandardEvent] = []

        # Health probes call extract(sport, limit=1). Walking 200+ competitions
        # at ~1s each to find the first one with parseable events blew the
        # orchestrator's 60s health-check budget — Cloudbet was permanently
        # SKIPPED. Cap competitions tried for tiny limits so the probe stays
        # fast; full extraction (limit=0) keeps walking the whole list.
        if limit and limit <= 5 and len(competitions) > 10:
            competitions = competitions[:10]

        # Step 2: fetch each competition.
        #
        # Critical (verified live 2026-04-28): the `?markets=...` URL filter is
        # broken on Cloudbet's pub/v2 endpoint — it returns events with
        # `markets: {}` (empty) instead of the requested markets. Without the
        # filter, the API returns the full event payload (~20 markets per
        # event). Verified with EPL: 20 EVENT_TYPE_EVENT events, with-filter
        # = 0 with markets, without-filter = 20/20 with markets. Soccer was
        # producing only ~11 events per cycle vs an actual 1486-event catalog
        # because EVERY event came back with empty markets and parse_event
        # rejected them.
        # parse_event filters to the markets we want (soccer.match_odds,
        # asian_handicap, total_goals via parse_selections_to_market) so
        # downloading the full payload doesn't pollute the result set.
        for comp in competitions:
            comp_key = comp.get("key") or comp.get("id")
            if not comp_key:
                continue
            comp_url = f"{BASE_URL}/competitions/{comp_key}"
            comp_data = await self.transport.get(comp_url, headers=self._headers())
            if not comp_data:
                continue

            comp_name = comp_data.get("name", "")
            raw_events = comp_data.get("events") or []
            for raw_event in raw_events:
                event = parse_event(raw_event, sport, self.provider_id)
                if event:
                    event.league = comp_name
                    events.append(event)
                    if limit and len(events) >= limit:
                        break

            if limit and len(events) >= limit:
                break

        logger.debug(
            f"[{self.provider_id}] Parsed {len(events)} events for sport '{sport}' "
            f"from {len(competitions)} competitions"
        )
        return events
