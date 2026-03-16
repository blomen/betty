from __future__ import annotations

"""
ComeOn DOM Parser
=================
Utilities for parsing data from ComeOn's website DOM elements:
- Odds button aria-labels
- Swedish datetime text
- Market outcomes from parsed aria-labels
- Market tab pill selection (sport-aware)
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ..matching.normalizer import normalize_team_name
from ..core import StandardEvent
from . import comeon_dom_js as JS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")

SWEDISH_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
    "mars": 3, "juni": 6, "juli": 7, "oktober": 10,
}

DRAW_NAMES: set[str] = {"oavgjort", "draw", "x"}

OT_SPORTS: set[str] = {"ice_hockey", "basketball"}
OT_KEYWORDS: set[str] = {"inkl", "övertid", "overtime"}

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# Matches: "Lägg till val: {name}, Odds: {value}" (Swedish: "Add to selection")
# Also handles "Lag till val" as fallback
_ARIA_RE = re.compile(r"L[aä]g+\s+till\s+val:\s*(.+?),\s*Odds:\s*([\d.]+)")

# Matches spread point in parentheses: "Team (+0.5)" or "Team (-1)"
# Only matches numeric content (not letters like "Dam")
_SPREAD_RE = re.compile(r"^(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)$")

# Matches HH:MM at end of string
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})$")

# Matches "DD MonthName" within a string
_DATE_RE = re.compile(r"(\d{1,2})\s+(\w+)")


# ---------------------------------------------------------------------------
# Task 1: Aria-Label Parser
# ---------------------------------------------------------------------------

def parse_aria_label(text: str) -> Optional[dict]:
    """Parse a ComeOn odds button aria-label into a structured dict.

    Input format: "Lag till val: {name}, Odds: {value}"

    Returns dict with keys:
        - name (str): outcome name
        - odds (float): decimal odds
        - point (float, optional): handicap/total line if detected

    Returns None if the format is unrecognised or odds are missing/invalid.
    """
    m = _ARIA_RE.match(text)
    if not m:
        return None

    name = m.group(1).strip()
    try:
        odds = float(m.group(2))
    except ValueError:
        return None
    if odds <= 0:
        return None

    result: dict = {"name": name, "odds": odds}

    # Check for spread point: "Team (+0.5)" or "Team (-1)"
    # _SPREAD_RE only matches numeric content, so "AIK (Dam)" won't match
    spread_m = _SPREAD_RE.match(name)
    if spread_m:
        result["name"] = spread_m.group(1).strip()
        result["point"] = float(spread_m.group(2))
    # Check for total: "Over 2.5", "Under 2.5", "Över 2.5"
    elif name.startswith("Over ") or name.startswith("Under ") or name.startswith("Över "):
        parts = name.split(" ", 1)
        if len(parts) == 2:
            try:
                result["point"] = float(parts[1])
            except ValueError:
                pass

    return result


# ---------------------------------------------------------------------------
# Task 2: Swedish DateTime Parser
# ---------------------------------------------------------------------------

def parse_swedish_datetime(text: str) -> Optional[datetime]:
    """Parse Swedish date/time text from ComeOn event listings.

    Supported formats:
        - "Idag16:00"        → today at 16:00 Stockholm time
        - "Imorgon15:00"     → tomorrow at 15:00 Stockholm time
        - "Fre 20 Mars21:00" → 20th March at 21:00 Stockholm time
        - "Idag 14 Mars"     → today's date (14th March) at 00:00

    Year rollover: if parsed date is more than 60 days in the past,
    assumes next calendar year.

    Returns a timezone-aware datetime in Europe/Stockholm, or None on failure.
    """
    text = text.strip()

    # Extract time from end of string (HH:MM)
    time_m = _TIME_RE.search(text)
    hour, minute = 0, 0
    if time_m:
        hour = int(time_m.group(1))
        minute = int(time_m.group(2))
        date_part = text[: time_m.start()].strip()
    else:
        date_part = text.strip()

    today_stockholm = datetime.now(tz=STOCKHOLM_TZ).date()

    lower = date_part.lower()

    if lower.startswith("idag"):
        target_date = today_stockholm
    elif lower.startswith("imorgon"):
        target_date = today_stockholm + timedelta(days=1)
    else:
        # Expect a pattern like "Fre 20 Mars" — find DD MonthName
        date_m = _DATE_RE.search(date_part)
        if not date_m:
            return None
        day = int(date_m.group(1))
        month_str = date_m.group(2).lower()
        month = SWEDISH_MONTHS.get(month_str)
        if month is None:
            return None

        year = today_stockholm.year
        try:
            import datetime as _dt
            candidate = _dt.date(year, month, day)
        except ValueError:
            return None

        # Year rollover: if date is more than 60 days in the past, use next year
        delta = (candidate - today_stockholm).days
        if delta < -60:
            try:
                import datetime as _dt
                candidate = _dt.date(year + 1, month, day)
            except ValueError:
                return None

        target_date = candidate

    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        minute,
        tzinfo=STOCKHOLM_TZ,
    )


# ---------------------------------------------------------------------------
# Task 3: Outcome Builder from Parsed Aria-Labels
# ---------------------------------------------------------------------------

def _fuzzy_team_match(name: str, team: str) -> bool:
    """Return True if name loosely matches team (after normalization)."""
    if not team:
        return False
    norm_name = normalize_team_name(name).lower()
    norm_team = normalize_team_name(team).lower()
    return norm_name == norm_team or norm_name in norm_team or norm_team in norm_name


def build_outcomes_from_labels(
    labels: list[dict],
    market_type: str,
    home_team: str,
    away_team: str,
) -> Optional[dict]:
    """Convert parsed aria-label dicts into a StandardEvent market dict.

    Args:
        labels:      List of dicts from parse_aria_label()
        market_type: "1x2", "spread", or "total"
        home_team:   Home team name for matching
        away_team:   Away team name for matching

    Returns a dict with keys "type" and "outcomes", or None if labels is empty
    or outcomes cannot be resolved.
    """
    if not labels:
        return None

    if market_type == "total":
        outcomes = []
        for label in labels:
            name_lower = label["name"].lower()
            if name_lower.startswith("over") or name_lower.startswith("över"):
                side = "over"
            elif name_lower.startswith("under"):
                side = "under"
            else:
                continue
            outcome: dict = {"name": side, "odds": label["odds"]}
            if "point" in label:
                outcome["point"] = label["point"]
            outcomes.append(outcome)
        if not outcomes:
            return None
        return {"type": "total", "outcomes": outcomes}

    if market_type == "spread":
        outcomes = []
        for label in labels:
            if _fuzzy_team_match(label["name"], home_team):
                side = "home"
            elif _fuzzy_team_match(label["name"], away_team):
                side = "away"
            else:
                # Fall back to position: first=home, second=away
                side = "home" if len(outcomes) == 0 else "away"
            outcome = {"name": side, "odds": label["odds"]}
            if "point" in label:
                outcome["point"] = label["point"]
            outcomes.append(outcome)
        if not outcomes:
            return None
        return {"type": "spread", "outcomes": outcomes}

    # 1x2 / moneyline
    has_draw = any(label["name"].lower() in DRAW_NAMES for label in labels)

    # Auto-detect: 2 outcomes with no draw → moneyline
    resolved_type = "1x2" if has_draw or len(labels) != 2 else "moneyline"

    outcomes = []
    for label in labels:
        name_lower = label["name"].lower()
        if name_lower in DRAW_NAMES:
            side = "draw"
        elif _fuzzy_team_match(label["name"], home_team):
            side = "home"
        elif _fuzzy_team_match(label["name"], away_team):
            side = "away"
        else:
            # Fall back to position order
            assigned = {o["name"] for o in outcomes}
            if "home" not in assigned:
                side = "home"
            elif "away" not in assigned:
                side = "away"
            else:
                side = label["name"]
        outcome = {"name": side, "odds": label["odds"]}
        if "point" in label:
            outcome["point"] = label["point"]
        outcomes.append(outcome)

    return {"type": resolved_type, "outcomes": outcomes}


# ---------------------------------------------------------------------------
# Task 4: Market Tab Pill Selector (Sport-Aware)
# ---------------------------------------------------------------------------

_SPREAD_KEYWORDS = {"handikapp", "handicap", "spread"}
_TOTAL_KEYWORDS = {"over/under", "over / under", "över/under", "total"}


def _pill_has_ot(pill: str) -> bool:
    """Return True if pill text contains OT-inclusive keywords."""
    lower = pill.lower()
    return any(kw in lower for kw in OT_KEYWORDS)


def _is_spread_pill(pill: str) -> bool:
    lower = pill.lower()
    return any(kw in lower for kw in _SPREAD_KEYWORDS)


def _is_total_pill(pill: str) -> bool:
    lower = pill.lower()
    return any(kw in lower for kw in _TOTAL_KEYWORDS)


def select_market_pills(
    pill_texts: list[str],
    sport: str,
) -> tuple[Optional[str], Optional[str]]:
    """Select the best spread and total tab pills for the given sport.

    For OT sports (ice_hockey, basketball): prefer OT-inclusive pills
    (containing "inkl", "övertid", or "overtime"). Falls back to any
    spread/total pill if no OT variant exists.

    For other sports: takes the first matching spread/total pill, avoiding
    second-half / half-time variants.

    Returns:
        (spread_pill, total_pill) — either may be None if not found.
    """
    prefer_ot = sport in OT_SPORTS

    spread_candidates: list[str] = []
    total_candidates: list[str] = []

    for pill in pill_texts:
        if _is_spread_pill(pill):
            spread_candidates.append(pill)
        elif _is_total_pill(pill):
            total_candidates.append(pill)

    def pick_pill(candidates: list[str]) -> Optional[str]:
        if not candidates:
            return None
        if prefer_ot:
            ot_pills = [p for p in candidates if _pill_has_ot(p)]
            if ot_pills:
                return ot_pills[0]
        # Return first non-half-time candidate
        for p in candidates:
            lower = p.lower()
            if "halvlek" not in lower and "halv" not in lower and "half" not in lower:
                return p
        return candidates[0]

    return pick_pill(spread_candidates), pick_pill(total_candidates)


# ---------------------------------------------------------------------------
# League Page Scraper
# ---------------------------------------------------------------------------

async def scrape_league_page(
    page,
    league_href: str,
    site_url: str,
    sport: str,
    league_name: str,
    provider_id: str,
) -> list[StandardEvent]:
    """Scrape a single league page for all events and markets.

    Navigates to the league page, then runs a single JS evaluation that
    parses 1x2, clicks spread/total tabs, and collects all markets.
    """
    url = f"{site_url}{league_href}" if league_href.startswith("/") else league_href

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        logger.debug(f"[{provider_id}] League {league_name}: navigation failed: {e}")
        return []

    # Wait for game cards to render (SPA hydration)
    try:
        await page.wait_for_selector('[data-at="game-card"]', timeout=10000)
    except Exception:
        logger.debug(f"[{provider_id}] League {league_name}: no game cards (empty or timeout)")
        return []

    # Single JS call: parse 1x2 + discover pills + click spread/total + collect all odds
    try:
        result = await page.evaluate(JS.JS_SCRAPE_ALL_MARKETS, {
            "spreadKeywords": list(_SPREAD_KEYWORDS),
            "totalKeywords": list(_TOTAL_KEYWORDS),
            "otKeywords": list(OT_KEYWORDS),
            "otSports": list(OT_SPORTS),
            "sport": sport,
        })
    except Exception as e:
        logger.debug(f"[{provider_id}] League {league_name}: combined scrape failed: {e}")
        return []

    card_data = result.get("events", [])
    if not card_data:
        return []

    spread_odds = result.get("spreadOdds", {})
    total_odds = result.get("totalOdds", {})

    # Build events with all markets from the single JS result
    events: dict[str, StandardEvent] = {}
    for card in card_data:
        home = normalize_team_name(card["home"])
        away = normalize_team_name(card["away"])
        start_time = parse_swedish_datetime(card["timeText"])
        event_id = card["eventId"]

        # 1x2 market from default tab
        labels = [parse_aria_label(l) for l in card["odds"]]
        labels = [l for l in labels if l is not None]
        market_1x2 = build_outcomes_from_labels(labels, "1x2", home, away)

        markets = [market_1x2] if market_1x2 else []

        # Spread market
        if event_id in spread_odds:
            s_labels = [parse_aria_label(l) for l in spread_odds[event_id]]
            s_labels = [l for l in s_labels if l is not None]
            spread_market = build_outcomes_from_labels(s_labels, "spread", home, away)
            if spread_market:
                markets.append(spread_market)

        # Total market
        if event_id in total_odds:
            t_labels = [parse_aria_label(l) for l in total_odds[event_id]]
            t_labels = [l for l in t_labels if l is not None]
            total_market = build_outcomes_from_labels(t_labels, "total", home, away)
            if total_market:
                markets.append(total_market)

        events[event_id] = StandardEvent(
            id=event_id,
            name=f"{home} vs {away}",
            sport=sport,
            provider=provider_id,
            markets=markets,
            league=league_name,
            home_team=home,
            away_team=away,
            start_time=start_time.isoformat() if start_time else "",
        )

    pills = result.get("pills", [])
    logger.debug(
        f"[{provider_id}] League {league_name}: {len(events)} events, "
        f"pills=[{', '.join(pills[:4])}...]"
    )

    return list(events.values())


