"""Marathonbet HTML odds extractor.

Marathonbet serves pre-match odds as server-rendered HTML — no JavaScript needed.
A single HTTP GET to e.g. https://www.marathonbet.com/en/betting/Football/ returns
all events with odds embedded in data attributes.
"""
from typing import List, Optional, Any
import json
import logging
import os
import re

from ..core.retriever import Retriever, StandardEvent
from ..core.transport import HttpTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Sports with no draw outcome — use moneyline instead of 1x2
_NO_DRAW_SPORTS = {"basketball", "tennis", "ice-hockey", "american-football", "baseball", "mma", "boxing"}

# Sport slug → URL path segment
_SPORT_URL_MAP = {
    "football": "Football",
    "basketball": "Basketball",
    "tennis": "Tennis",
    "ice_hockey": "Ice+Hockey",
    "american_football": "American+Football",
    "baseball": "Baseball",
    "mma": "Mixed+Martial+Arts",
    "boxing": "Boxing",
}

_BASE_URL = "https://www.marathonbet.com/en/betting/"

# Regex: find coupon-row divs with their full content
_EVENT_BLOCK_RE = re.compile(
    r'<div[^>]+class="[^"]*coupon-row[^"]*"[^>]+'
    r'data-event-eventId="(\d+)"[^>]+'
    r'data-event-name="([^"]+)"[^>]+'
    r'data-live="(true|false)"[^>]*>',
    re.DOTALL,
)

# Match individual event block: from opening div to the next top-level coupon-row (greedy bounded)
_FULL_BLOCK_RE = re.compile(
    r'(<div[^>]+class="[^"]*coupon-row[^"]*"[^>]+data-event-eventId="(\d+)"[^>]+'
    r'data-event-name="([^"]+)"[^>]+data-live="(true|false)"[^>]*>)',
    re.DOTALL,
)

# Regex: extract data-sel JSON blobs from within an event block
_DATA_SEL_RE = re.compile(r'data-sel=\'({[^\']+})\'')

# Regex: extract data-event-start-time if present
_START_TIME_RE = re.compile(r'data-event-start-time="([^"]+)"')

# Regex: extract league/tree path from data-event-treeName or similar
_TREE_NAME_RE = re.compile(r'data-event-treeName="([^"]+)"')


def _parse_odds(epr: str) -> Optional[float]:
    """Parse decimal odds string to float. Returns None if invalid or <= 1.0."""
    try:
        v = float(epr)
        return v if v > 1.0 else None
    except (ValueError, TypeError):
        return None


def _split_events(html: str) -> List[tuple]:
    """Split HTML into (event_id, event_name, is_live, block_html) tuples.

    Since events are nested in a flat list, we split on the opening tag of each
    coupon-row and treat the text up to the next coupon-row as one block.
    """
    # Find all opening tags with their positions
    pattern = re.compile(
        r'<div[^>]+class="[^"]*(?:coupon-row)[^"]*"[^>]+'
        r'data-event-eventId="(\d+)"[^>]+'
        r'data-event-name="([^"]+)"[^>]+'
        r'data-live="(true|false)"[^>]*>',
        re.DOTALL,
    )
    matches = list(pattern.finditer(html))
    results = []
    for i, m in enumerate(matches):
        event_id = m.group(1)
        event_name = m.group(2)
        is_live = m.group(3) == "true"
        # Slice from start of this match to start of next (or end)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        block = html[start:end]
        results.append((event_id, event_name, is_live, block))
    return results


def parse_event_html(html_block: str, sport: str, event_id: str, event_name: str) -> Optional[StandardEvent]:
    """Parse one event block into a StandardEvent.

    Args:
        html_block: Raw HTML for a single event coupon row.
        sport: Internal sport key (e.g. "football").
        event_id: Numeric event ID string from data-event-eventId.
        event_name: Raw event name from data-event-name (e.g. "Atletico Madrid vs Barcelona").

    Returns:
        StandardEvent or None if event cannot be parsed.
    """
    # Extract all data-sel JSON blobs
    sel_blobs = _DATA_SEL_RE.findall(html_block)
    if not sel_blobs:
        return None

    selections = []
    for blob in sel_blobs:
        try:
            data = json.loads(blob)
            epr = data.get("epr", "")
            odds = _parse_odds(epr)
            if odds is None:
                continue
            selections.append(odds)
        except (json.JSONDecodeError, ValueError):
            continue

    if not selections:
        return None

    # Determine market type for match-winner market
    is_no_draw = sport in _NO_DRAW_SPORTS
    ml_count = 2 if is_no_draw else 3

    # Need at least the match-winner selections
    if len(selections) < ml_count:
        return None

    # Build markets list
    markets = []

    # Match winner (1x2 or moneyline) — first ml_count selections
    mw_odds = selections[:ml_count]
    if is_no_draw:
        market_type = "moneyline"
        mw_outcomes = [
            {"name": "home", "odds": mw_odds[0]},
            {"name": "away", "odds": mw_odds[1]},
        ]
    else:
        market_type = "1x2"
        mw_outcomes = [
            {"name": "home", "odds": mw_odds[0]},
            {"name": "draw", "odds": mw_odds[1]},
            {"name": "away", "odds": mw_odds[2]},
        ]
    markets.append({"type": market_type, "outcomes": mw_outcomes})

    # Total (Over/Under) — next 2 selections after match winner
    offset = ml_count
    if len(selections) >= offset + 2:
        over_odds = selections[offset]
        under_odds = selections[offset + 1]
        markets.append({
            "type": "total",
            "outcomes": [
                {"name": "over", "odds": over_odds},
                {"name": "under", "odds": under_odds},
            ],
        })

    # Handicap (Spread) — next 2 after total
    offset += 2
    if len(selections) >= offset + 2:
        home_odds = selections[offset]
        away_odds = selections[offset + 1]
        markets.append({
            "type": "spread",
            "outcomes": [
                {"name": "home", "odds": home_odds},
                {"name": "away", "odds": away_odds},
            ],
        })

    if not markets:
        return None

    # Parse team names from "Team A vs Team B"
    sep = " vs "
    if sep in event_name:
        home_raw, away_raw = event_name.split(sep, 1)
    else:
        home_raw = event_name
        away_raw = ""

    home_team = normalize_team_name(home_raw)
    away_team = normalize_team_name(away_raw) if away_raw else ""

    # Extract start time if present
    start_match = _START_TIME_RE.search(html_block)
    start_time = start_match.group(1) if start_match else ""

    # Extract league from data-event-treeName if present
    tree_match = _TREE_NAME_RE.search(html_block)
    league = tree_match.group(1) if tree_match else ""

    return StandardEvent(
        id=f"marathon_{event_id}",
        name=event_name,
        sport=sport,
        markets=markets,
        provider="marathon",
        url=f"https://www.marathonbet.com/en/betting/{event_id}",
        start_time=start_time,
        home_team=home_team,
        away_team=away_team,
        league=league,
    )


def parse_page(html: str, sport: str, provider_id: str = "marathon") -> List[StandardEvent]:
    """Parse a full Marathonbet sport page into a list of StandardEvents.

    Args:
        html: Full page HTML.
        sport: Internal sport key (e.g. "football").
        provider_id: Provider identifier string.

    Returns:
        List of StandardEvent (pre-match only, skips live events).
    """
    blocks = _split_events(html)
    events = []
    for event_id, event_name, is_live, block in blocks:
        if is_live:
            continue
        event = parse_event_html(block, sport, event_id, event_name)
        if event:
            event.provider = provider_id
            events.append(event)
    logger.debug(
        f"[{provider_id}] Parsed {len(events)} events from {len(blocks)} blocks for sport '{sport}'"
    )
    return events


class MarathonRetriever(Retriever):
    """Retriever for Marathonbet pre-match odds via server-rendered HTML."""

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            transport = HttpTransport(
                headers=_HEADERS,
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config,
                proxy=os.environ.get("PROXY_URL"),
            )
        super().__init__(config, transport)

    def _get_sport_url(self, sport: str) -> str:
        """Not used — we override extract() directly."""
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — parsing is done inside extract()."""
        return []

    async def extract(self, sport: str, limit: int = 200, **kwargs) -> List[StandardEvent]:
        """Extract pre-match events for a sport from Marathonbet."""
        url_segment = _SPORT_URL_MAP.get(sport)
        if not url_segment:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not mapped for Marathon")
            return []

        url = f"{_BASE_URL}{url_segment}/"
        html = await self.transport.get(url, headers=_HEADERS)

        if not html:
            logger.warning(f"[{self.provider_id}] No HTML returned for sport '{sport}' at {url}")
            return []

        # transport.get returns JSON by default; for HTML we get a string
        if isinstance(html, dict):
            logger.warning(f"[{self.provider_id}] Expected HTML string but got dict for sport '{sport}'")
            return []

        events = parse_page(html, sport, self.provider_id)

        if limit and len(events) > limit:
            events = events[:limit]

        logger.info(f"[{self.provider_id}] Extracted {len(events)} events for sport '{sport}'")
        return events
