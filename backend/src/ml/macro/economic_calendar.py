"""Economic calendar fetcher and parser.

Fetches high/medium/low impact economic events from ForexFactory JSON feed.
Parses forecast, actual, and previous values; computes surprise delta.
"""

import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_IMPORTANCE_MAP = {
    "High": 3,
    "Medium": 2,
    "Low": 1,
    "Holiday": 0,
}


def _parse_importance(impact: str | None) -> int:
    """Map impact string to numeric level (0-3)."""
    if not impact:
        return 0
    return _IMPORTANCE_MAP.get(impact, 0)


def _parse_numeric(value: str | None) -> float | None:
    """Parse a numeric string like '0.3%', '-1.2%', '250K', '1.5M' to float.

    Returns None for empty/None input or unparseable strings.
    """
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    # Strip trailing % sign
    v = v.rstrip("%")
    # Handle K/M suffixes (e.g. 250K → 250.0, 1.5M → 1.5)
    if v.upper().endswith("K"):
        try:
            return float(v[:-1])
        except ValueError:
            return None
    if v.upper().endswith("M"):
        try:
            return float(v[:-1])
        except ValueError:
            return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_event(raw: dict) -> dict:
    """Parse a raw ForexFactory event dict into a normalised feature dict.

    Fields returned:
        event_name, event_date, importance, currency,
        forecast, actual, previous, surprise
    """
    forecast = _parse_numeric(raw.get("forecast"))
    actual = _parse_numeric(raw.get("actual"))
    previous = _parse_numeric(raw.get("previous"))

    surprise = None
    if actual is not None and forecast is not None:
        surprise = round(actual - forecast, 6)

    # Parse ISO-8601 date string (may include timezone offset)
    raw_date = raw.get("date", "")
    event_date = None
    if raw_date:
        try:
            event_date = datetime.fromisoformat(raw_date)
        except ValueError:
            event_date = None

    return {
        "event_name": raw.get("title", ""),
        "event_date": event_date,
        "importance": _parse_importance(raw.get("impact")),
        "currency": raw.get("country", ""),
        "forecast": forecast,
        "actual": actual,
        "previous": previous,
        "surprise": surprise,
    }


async def fetch_events() -> list[dict]:
    """Fetch this week's economic events from ForexFactory JSON feed.

    Returns a list of parsed event dicts (see parse_event).
    Returns empty list on any error.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(CALENDAR_URL)
            resp.raise_for_status()
            rows = resp.json()
        return [parse_event(row) for row in rows]
    except Exception as e:
        logger.error("Economic calendar fetch failed: %s", e)
        return []
