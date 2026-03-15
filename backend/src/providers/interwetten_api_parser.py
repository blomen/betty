"""
Interwetten JSON API Response Parser

Parses event detail JSON returned by interwetten when called with
X-Requested-With: XMLHttpRequest header. Extracts 1x2/moneyline,
spread (Asian Handicap), and total (How many goals / Over/Under).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..core import StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

SPREAD_TEMPLATE_NAMES = {"Asian Handicap", "Handicap", "Handicap Games"}
TOTAL_TEMPLATE_NAMES = {"How many goals", "Over/Under", "How many games"}

_POINT_RE = re.compile(r"\(([+-]?\d+\.?\d*)\)")
_TOTAL_POINT_RE = re.compile(r"(\d+\.?\d*)")


def parse_main_market(
    main_market: dict, home_team: str, away_team: str
) -> Optional[dict]:
    outcomes_raw = main_market.get("outcomes", [])
    outcomes = []
    has_draw = False

    for o in outcomes_raw:
        odds = o.get("odd", 0)
        if not odds or odds <= 1.0:
            continue
        tip = o.get("tip", "")
        if tip == "1":
            outcomes.append({"name": "home", "odds": odds})
        elif tip == "X":
            outcomes.append({"name": "draw", "odds": odds})
            has_draw = True
        elif tip == "2":
            outcomes.append({"name": "away", "odds": odds})

    if len(outcomes) < 2:
        return None

    return {
        "type": "1x2" if has_draw else "moneyline",
        "outcomes": outcomes,
    }


def parse_spread_from_template(template: dict) -> Optional[dict]:
    markets = template.get("markets", [])
    if not markets:
        return None

    first = markets[0]
    outcomes_raw = first.get("outcomes", [])
    outcomes = []
    has_point = False

    for o in outcomes_raw:
        odds = o.get("odd", 0)
        if not odds or odds <= 1.0:
            continue
        tip = o.get("tip", "")
        name = o.get("name", "")

        side = "home" if tip == "1" else "away" if tip == "2" else None
        if side is None:
            continue

        m = _POINT_RE.search(name)
        if m:
            point = float(m.group(1))
            outcomes.append({"name": side, "odds": odds, "point": point})
            has_point = True
        else:
            outcomes.append({"name": side, "odds": odds})

    if len(outcomes) < 2 or not has_point:
        return None

    return {"type": "spread", "outcomes": outcomes}


def parse_total_from_template(template: dict) -> Optional[dict]:
    markets = template.get("markets", [])
    if not markets:
        return None

    first = markets[0]
    outcomes_raw = first.get("outcomes", [])
    outcomes = []

    for o in outcomes_raw:
        odds = o.get("odd", 0)
        if not odds or odds <= 1.0:
            continue
        name = o.get("name", "").strip()
        name_lower = name.lower()

        if name_lower.startswith("over") or name_lower.startswith("över"):
            side = "over"
        elif name_lower.startswith("under"):
            side = "under"
        else:
            continue

        m = _TOTAL_POINT_RE.search(name)
        point = float(m.group(1)) if m else None
        outcome: dict = {"name": side, "odds": odds}
        if point is not None:
            outcome["point"] = point
        outcomes.append(outcome)

    if len(outcomes) < 2:
        return None

    return {"type": "total", "outcomes": outcomes}


def parse_top_leagues_response(data: dict) -> list[dict]:
    """Parse top-leagues JSON into list of event info dicts."""
    results = []
    for league in data.get("leagues", []):
        league_name = league.get("name", "")
        for ev in league.get("events", []):
            results.append({
                "id": ev.get("id"),
                "href": ev.get("href", ""),
                "league": league_name,
                "name": ev.get("name", ""),
            })
    return results


def parse_event_json(
    data: dict, provider_id: str = "interwetten"
) -> Optional[StandardEvent]:
    event_data = data.get("event")
    if not event_data:
        return None

    event_id = event_data.get("id")
    event_name = event_data.get("name", "")
    start_time = event_data.get("startTime", "")

    parts = event_name.split(" - ", 1)
    if len(parts) != 2:
        return None

    home_raw, away_raw = parts[0].strip(), parts[1].strip()
    home = normalize_team_name(home_raw)
    away = normalize_team_name(away_raw)

    league_data = data.get("league", {})
    league_name = league_data.get("name", "")

    sport_data = data.get("sport", {})
    sport_name = sport_data.get("name", "").lower()

    sport_map = {
        "football": "football", "basketball": "basketball",
        "ice hockey": "ice_hockey", "tennis": "tennis",
        "handball": "handball", "volleyball": "volleyball",
        "american football": "american_football",
        "baseball": "baseball", "rugby": "rugby",
        "cricket": "cricket", "darts": "darts", "boxing": "boxing",
    }
    sport = sport_map.get(sport_name, sport_name)

    markets = []

    main = event_data.get("mainMarket")
    if main:
        mm = parse_main_market(main, home, away)
        if mm:
            markets.append(mm)

    for group in event_data.get("templateGroups", []):
        for template in group.get("templates", []):
            tname = template.get("name", "")
            if tname in SPREAD_TEMPLATE_NAMES:
                spread = parse_spread_from_template(template)
                if spread:
                    markets.append(spread)
                    break
        for template in group.get("templates", []):
            tname = template.get("name", "")
            if tname in TOTAL_TEMPLATE_NAMES:
                total = parse_total_from_template(template)
                if total:
                    markets.append(total)
                    break

    if not markets:
        return None

    return StandardEvent(
        id=f"interwetten_{event_id}",
        name=f"{home_raw} vs {away_raw}",
        provider=provider_id,
        sport=sport,
        league=league_name,
        home_team=home,
        away_team=away,
        start_time=start_time,
        markets=markets,
    )
