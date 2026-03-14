# ComeOn DOM-Based League Scraper Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ComeOn's flaky WS-based extraction with reliable DOM scraping via league pages, increasing coverage from ~15 to ~200-300 football events and achieving ~95%+ reliability.

**Architecture:** Rewrite `comeon_multileague.py` to navigate league pages instead of sport pages. Each league page shows all upcoming events with market tabs for 1x2/spread/total. Use 8 concurrent browser tabs via `asyncio.Semaphore`. Filter leagues to Pinnacle-matched ones using `target_leagues` from the orchestrator. Scrape boosts from `/sport/85-odds-boost` in the same session.

**Tech Stack:** Python 3.10+, Playwright (via BrowserTransport), asyncio, rapidfuzz, zoneinfo, freezegun (test)

**Spec:** `docs/superpowers/specs/2026-03-14-comeon-dom-scraper-design.md`

**Prerequisites:**
- `pip install freezegun` (for datetime tests)
- Create `backend/tests/providers/` directory if it doesn't exist (and add `__init__.py`)

**Design decisions diverging from spec:**
- **No `sports.yaml` league ID mapping** — uses runtime fuzzy matching via orchestrator's `target_leagues` (Pinnacle league name set) instead of static ComeOn league IDs. This avoids a one-time manual mapping effort and automatically adapts when Pinnacle adds/removes leagues.
- **Boost scraping unchanged** — the existing WS-based boost scraper (`scrape_specials.py::_scrape_comeon_boosts`) already works reliably (~10s, 100% success rate). Only the providers.yaml config changes for Lyllo independence.

---

## Chunk 1: Core DOM Parsing Utilities

### Task 1: Aria-Label Parser

**Files:**
- Create: `backend/src/providers/comeon_dom_parser.py`
- Create: `backend/tests/providers/test_comeon_dom_parser.py`

- [ ] **Step 1: Write failing tests for aria-label parsing**

```python
# backend/tests/providers/test_comeon_dom_parser.py
import pytest
from src.providers.comeon_dom_parser import parse_aria_label, parse_swedish_datetime


class TestParseAriaLabel:
    """Parse ComeOn odds button aria-label text into structured data."""

    def test_1x2_home(self):
        result = parse_aria_label("Lag till val: Burnley FC, Odds: 4.18")
        assert result == {"name": "Burnley FC", "odds": 4.18}

    def test_1x2_draw(self):
        result = parse_aria_label("Lag till val: Oavgjort, Odds: 3.92")
        assert result == {"name": "Oavgjort", "odds": 3.92}

    def test_spread_positive(self):
        result = parse_aria_label("Lag till val: Burnley FC (+0.5), Odds: 1.97")
        assert result == {"name": "Burnley FC", "odds": 1.97, "point": 0.5}

    def test_spread_negative(self):
        result = parse_aria_label("Lag till val: Bournemouth (-0.5), Odds: 1.81")
        assert result == {"name": "Bournemouth", "odds": 1.81, "point": -0.5}

    def test_total_over(self):
        result = parse_aria_label("Lag till val: Over 2.5, Odds: 1.71")
        assert result == {"name": "Over 2.5", "odds": 1.71, "point": 2.5}

    def test_total_under(self):
        result = parse_aria_label("Lag till val: Under 2.5, Odds: 2.16")
        assert result == {"name": "Under 2.5", "odds": 2.16, "point": 2.5}

    def test_invalid_format(self):
        result = parse_aria_label("some random text")
        assert result is None

    def test_suspended_no_odds(self):
        result = parse_aria_label("Lag till val: Burnley FC, Odds: ")
        assert result is None

    def test_team_name_with_parentheses_non_spread(self):
        # Team name that naturally has parens, e.g. "FC (Women)"
        result = parse_aria_label("Lag till val: AIK (Dam), Odds: 2.50")
        assert result == {"name": "AIK (Dam)", "odds": 2.5}

    def test_spread_integer_point(self):
        result = parse_aria_label("Lag till val: Real Madrid (+1), Odds: 1.45")
        assert result == {"name": "Real Madrid", "odds": 1.45, "point": 1.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.providers.comeon_dom_parser'`

- [ ] **Step 3: Implement parse_aria_label**

```python
# backend/src/providers/comeon_dom_parser.py
"""
ComeOn DOM parsing utilities.

Parses aria-label text from ComeOn odds buttons and Swedish datetime strings
into structured data for the DOM-based league scraper.
"""

import re
from datetime import datetime, timedelta, date
from typing import Optional
from zoneinfo import ZoneInfo

# Regex: "Lag till val: {name}, Odds: {value}"
_ARIA_RE = re.compile(r"Lag till val:\s*(.+?),\s*Odds:\s*([\d.]+)")
# Spread point suffix: "(+0.5)" or "(-1.5)" at end of name
_SPREAD_RE = re.compile(r"^(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)$")

STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")

SWEDISH_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
    # Full names
    "mars": 3, "juni": 6, "juli": 7, "oktober": 10,
}


def parse_aria_label(text: str) -> Optional[dict]:
    """Parse ComeOn aria-label into {name, odds, point?}.

    Examples:
        "Lag till val: Burnley FC, Odds: 4.18" -> {"name": "Burnley FC", "odds": 4.18}
        "Lag till val: Burnley FC (+0.5), Odds: 1.97" -> {"name": "Burnley FC", "odds": 1.97, "point": 0.5}
        "Lag till val: Over 2.5, Odds: 1.71" -> {"name": "Over 2.5", "odds": 1.71, "point": 2.5}
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

    result = {"name": name, "odds": odds}

    # Check for spread point: "Team (+0.5)"
    spread_m = _SPREAD_RE.match(name)
    if spread_m:
        result["name"] = spread_m.group(1).strip()
        result["point"] = float(spread_m.group(2))
    # Check for total: "Over 2.5" or "Under 2.5"
    elif name.startswith("Over ") or name.startswith("Under ") or name.startswith("Över "):
        parts = name.split(" ", 1)
        if len(parts) == 2:
            try:
                result["point"] = float(parts[1])
            except ValueError:
                pass

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py::TestParseAriaLabel -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/comeon_dom_parser.py backend/tests/providers/test_comeon_dom_parser.py
git commit -m "feat(comeon): add aria-label parser for DOM-based extraction"
```

### Task 2: Swedish DateTime Parser

**Files:**
- Modify: `backend/src/providers/comeon_dom_parser.py`
- Modify: `backend/tests/providers/test_comeon_dom_parser.py`

- [ ] **Step 1: Write failing tests for Swedish datetime parsing**

Add to `backend/tests/providers/test_comeon_dom_parser.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from freezegun import freeze_time


class TestParseSwedishDatetime:
    """Parse ComeOn's Swedish datetime text into UTC datetime."""

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)  # CET
    def test_idag(self):
        result = parse_swedish_datetime("Idag16:00")
        expected = datetime(2026, 3, 14, 16, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        assert result == expected

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_imorgon(self):
        result = parse_swedish_datetime("Imorgon15:00")
        expected = datetime(2026, 3, 15, 15, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        assert result == expected

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_named_day_with_date(self):
        result = parse_swedish_datetime("Fre 20 Mars21:00")
        expected = datetime(2026, 3, 20, 21, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        assert result == expected

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_short_month(self):
        result = parse_swedish_datetime("Lör 21 Mar21:00")
        expected = datetime(2026, 3, 21, 21, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        assert result == expected

    @freeze_time("2026-12-28 12:00:00", tz_offset=1)
    def test_year_rollover(self):
        # December 28 looking at January date → next year
        result = parse_swedish_datetime("Fre 3 Jan18:00")
        expected = datetime(2027, 1, 3, 18, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        assert result == expected

    def test_invalid_text(self):
        result = parse_swedish_datetime("random text")
        assert result is None

    @freeze_time("2026-03-14 12:00:00", tz_offset=1)
    def test_date_header_format(self):
        # Date group headers: "Idag 14 Mars"
        result = parse_swedish_datetime("Idag 14 Mars")
        expected = datetime(2026, 3, 14, 0, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        assert result == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py::TestParseSwedishDatetime -v`
Expected: FAIL — `parse_swedish_datetime` not yet implemented

- [ ] **Step 3: Implement parse_swedish_datetime**

Add to `backend/src/providers/comeon_dom_parser.py`:

```python
# Regex for time portion at end of string: "16:00"
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})$")
# Regex for date portion: "20 Mars" or "3 Jan"
_DATE_RE = re.compile(r"(\d{1,2})\s+(\w+)")


def parse_swedish_datetime(text: str) -> Optional[datetime]:
    """Parse ComeOn Swedish datetime text into timezone-aware datetime.

    Formats:
        "Idag16:00" -> today at 16:00 Stockholm time
        "Imorgon15:00" -> tomorrow at 15:00
        "Fre 20 Mars21:00" -> March 20 at 21:00
        "Idag 14 Mars" -> today at 00:00 (date header, no time)

    Returns datetime with Europe/Stockholm timezone, or None if unparseable.
    """
    if not text or not isinstance(text, str):
        return None

    text = text.strip()
    today = datetime.now(STOCKHOLM_TZ).date()

    # Extract time if present
    time_match = _TIME_RE.search(text)
    hour, minute = 0, 0
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        text_before_time = text[:time_match.start()]
    else:
        text_before_time = text

    text_lower = text_before_time.strip().lower()

    # "Idag" / "Idag 14 Mars"
    if text_lower.startswith("idag"):
        return datetime(today.year, today.month, today.day, hour, minute, tzinfo=STOCKHOLM_TZ)

    # "Imorgon"
    if text_lower.startswith("imorgon"):
        tomorrow = today + timedelta(days=1)
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=STOCKHOLM_TZ)

    # "Fre 20 Mars" / "20 Mars" / "Lör 21 Mar"
    date_match = _DATE_RE.search(text_before_time)
    if date_match:
        day = int(date_match.group(1))
        month_str = date_match.group(2).lower().rstrip(".")
        month = SWEDISH_MONTHS.get(month_str)
        if month:
            year = today.year
            # Year rollover: if the date is more than 2 months in the past, assume next year
            candidate = date(year, month, day)
            if (today - candidate).days > 60:
                year += 1
            return datetime(year, month, day, hour, minute, tzinfo=STOCKHOLM_TZ)

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/comeon_dom_parser.py backend/tests/providers/test_comeon_dom_parser.py
git commit -m "feat(comeon): add Swedish datetime parser for DOM text"
```

### Task 3: Outcome Builder from Parsed Aria-Labels

**Files:**
- Modify: `backend/src/providers/comeon_dom_parser.py`
- Modify: `backend/tests/providers/test_comeon_dom_parser.py`

- [ ] **Step 1: Write failing tests for outcome building**

Add to test file:

```python
from src.providers.comeon_dom_parser import build_outcomes_from_labels


class TestBuildOutcomesFromLabels:
    """Convert parsed aria-labels per game card into StandardEvent market format."""

    def test_1x2_three_way(self):
        labels = [
            {"name": "Burnley FC", "odds": 4.18},
            {"name": "Oavgjort", "odds": 3.92},
            {"name": "Bournemouth", "odds": 1.88},
        ]
        market = build_outcomes_from_labels(labels, "1x2", "Burnley FC", "Bournemouth")
        assert market["type"] == "1x2"
        assert len(market["outcomes"]) == 3
        assert market["outcomes"][0] == {"name": "home", "odds": 4.18}
        assert market["outcomes"][1] == {"name": "draw", "odds": 3.92}
        assert market["outcomes"][2] == {"name": "away", "odds": 1.88}

    def test_moneyline_two_way(self):
        labels = [
            {"name": "LA Lakers", "odds": 1.55},
            {"name": "Boston Celtics", "odds": 2.40},
        ]
        market = build_outcomes_from_labels(labels, "1x2", "LA Lakers", "Boston Celtics")
        assert market["type"] == "moneyline"
        assert len(market["outcomes"]) == 2

    def test_spread(self):
        labels = [
            {"name": "Burnley FC", "odds": 1.97, "point": 0.5},
            {"name": "Bournemouth", "odds": 1.81, "point": -0.5},
        ]
        market = build_outcomes_from_labels(labels, "spread", "Burnley FC", "Bournemouth")
        assert market["type"] == "spread"
        assert market["outcomes"][0] == {"name": "home", "odds": 1.97, "point": 0.5}
        assert market["outcomes"][1] == {"name": "away", "odds": 1.81, "point": -0.5}

    def test_total(self):
        labels = [
            {"name": "Over 2.5", "odds": 1.71, "point": 2.5},
            {"name": "Under 2.5", "odds": 2.16, "point": 2.5},
        ]
        market = build_outcomes_from_labels(labels, "total", "", "")
        assert market["type"] == "total"
        assert market["outcomes"][0] == {"name": "over", "odds": 1.71, "point": 2.5}
        assert market["outcomes"][1] == {"name": "under", "odds": 2.16, "point": 2.5}

    def test_empty_labels(self):
        market = build_outcomes_from_labels([], "1x2", "A", "B")
        assert market is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py::TestBuildOutcomesFromLabels -v`
Expected: FAIL

- [ ] **Step 3: Implement build_outcomes_from_labels**

Add to `backend/src/providers/comeon_dom_parser.py`:

```python
from ..matching.normalizer import normalize_team_name

DRAW_NAMES = {"oavgjort", "draw", "x"}


def build_outcomes_from_labels(
    labels: list[dict],
    market_type: str,
    home_team: str,
    away_team: str,
) -> Optional[dict]:
    """Build a market dict from parsed aria-label data.

    Args:
        labels: List of parsed aria-labels [{name, odds, point?}, ...]
        market_type: "1x2", "spread", or "total"
        home_team: Normalized home team name (for home/away assignment)
        away_team: Normalized away team name

    Returns:
        {"type": market_type, "outcomes": [...]} or None
    """
    if not labels:
        return None

    outcomes = []
    home_lower = home_team.lower() if home_team else ""
    away_lower = away_team.lower() if away_team else ""

    if market_type in ("1x2", "moneyline"):
        for label in labels:
            name_lower = label["name"].lower()
            if name_lower in DRAW_NAMES:
                outcomes.append({"name": "draw", "odds": label["odds"]})
            elif _fuzzy_team_match(name_lower, home_lower):
                outcomes.append({"name": "home", "odds": label["odds"]})
            elif _fuzzy_team_match(name_lower, away_lower):
                outcomes.append({"name": "away", "odds": label["odds"]})

        # 2-way = moneyline, 3-way = 1x2
        actual_type = "moneyline" if len(outcomes) == 2 and not any(o["name"] == "draw" for o in outcomes) else "1x2"
        if not outcomes:
            return None
        return {"type": actual_type, "outcomes": outcomes}

    elif market_type == "spread":
        for label in labels:
            if "point" not in label:
                continue
            name_lower = label["name"].lower()
            if _fuzzy_team_match(name_lower, home_lower):
                outcomes.append({"name": "home", "odds": label["odds"], "point": label["point"]})
            elif _fuzzy_team_match(name_lower, away_lower):
                outcomes.append({"name": "away", "odds": label["odds"], "point": label["point"]})
        if not outcomes:
            return None
        return {"type": "spread", "outcomes": outcomes}

    elif market_type == "total":
        for label in labels:
            if "point" not in label:
                continue
            name_lower = label["name"].lower()
            if name_lower.startswith("over") or name_lower.startswith("över"):
                outcomes.append({"name": "over", "odds": label["odds"], "point": label["point"]})
            elif name_lower.startswith("under"):
                outcomes.append({"name": "under", "odds": label["odds"], "point": label["point"]})
        if not outcomes:
            return None
        return {"type": "total", "outcomes": outcomes}

    return None


def _fuzzy_team_match(label_name: str, team_name: str) -> bool:
    """Check if a label name matches a team name (substring match)."""
    if not label_name or not team_name:
        return False
    # Normalize both for comparison
    label_norm = normalize_team_name(label_name).lower()
    team_norm = team_name.lower()
    return label_norm in team_norm or team_norm in label_norm
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/comeon_dom_parser.py backend/tests/providers/test_comeon_dom_parser.py
git commit -m "feat(comeon): add outcome builder from parsed aria-labels"
```

### Task 4: Market Tab Pill Selector (Sport-Aware)

**Files:**
- Modify: `backend/src/providers/comeon_dom_parser.py`
- Modify: `backend/tests/providers/test_comeon_dom_parser.py`

- [ ] **Step 1: Write failing tests for pill selection**

Add to test file:

```python
from src.providers.comeon_dom_parser import select_market_pills


class TestSelectMarketPills:
    """Select correct spread/total pills based on sport (OT-aware)."""

    def test_football_pills(self):
        pills = [
            "Populara",
            "Båda lagen gör mål",
            "Over/Under mål",
            "Handikapp",
            "Dubbelchans",
            "Over/Under mål i 1a halvlek",
        ]
        spread, total = select_market_pills(pills, "football")
        assert spread == "Handikapp"
        assert total == "Over/Under mål"

    def test_ice_hockey_ot_pills(self):
        pills = [
            "Populara",
            "Vinnare (Inkl. övertid)",
            "Handikapp (Inkl. övertid)",
            "Over/Under mål (Inkl. övertid)",
        ]
        spread, total = select_market_pills(pills, "ice_hockey")
        assert spread == "Handikapp (Inkl. övertid)"
        assert total == "Over/Under mål (Inkl. övertid)"

    def test_basketball_ot_pills(self):
        pills = [
            "Populara",
            "Over/Under poäng (Inkl övertid)",
            "Handikapp (Inkl övertid)",
        ]
        spread, total = select_market_pills(pills, "basketball")
        assert spread == "Handikapp (Inkl övertid)"
        assert total == "Over/Under poäng (Inkl övertid)"

    def test_no_spread_pill(self):
        pills = ["Populara", "Over/Under mål"]
        spread, total = select_market_pills(pills, "football")
        assert spread is None
        assert total == "Over/Under mål"

    def test_no_pills(self):
        spread, total = select_market_pills([], "football")
        assert spread is None
        assert total is None

    def test_ice_hockey_fallback_no_ot(self):
        # If only non-OT pills exist, fall back to them
        pills = ["Populara", "Handikapp", "Over/Under mål"]
        spread, total = select_market_pills(pills, "ice_hockey")
        assert spread == "Handikapp"
        assert total == "Over/Under mål"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py::TestSelectMarketPills -v`
Expected: FAIL

- [ ] **Step 3: Implement select_market_pills**

Add to `backend/src/providers/comeon_dom_parser.py`:

```python
OT_SPORTS = {"ice_hockey", "basketball"}
OT_KEYWORDS = {"inkl", "övertid", "overtime"}


def select_market_pills(pill_texts: list[str], sport: str) -> tuple[Optional[str], Optional[str]]:
    """Select the correct spread and total pill texts for a sport.

    For ice_hockey/basketball, prefers OT-inclusive pills ("inkl. övertid").
    For other sports, takes the first match.

    Returns:
        (spread_pill_text, total_pill_text) — None if not found
    """
    spread_pill = None
    total_pill = None
    spread_fallback = None
    total_fallback = None

    prefer_ot = sport in OT_SPORTS

    for pill in pill_texts:
        pill_lower = pill.lower()
        has_ot = any(kw in pill_lower for kw in OT_KEYWORDS)

        if "handikapp" in pill_lower:
            if prefer_ot and has_ot:
                spread_pill = pill
            elif not spread_fallback:
                spread_fallback = pill
                if not prefer_ot:
                    spread_pill = pill

        if "over/under" in pill_lower:
            if prefer_ot and has_ot:
                total_pill = pill
            elif not total_fallback:
                total_fallback = pill
                if not prefer_ot:
                    total_pill = pill

    # Fallback for OT sports when no OT pill exists
    if prefer_ot:
        if not spread_pill:
            spread_pill = spread_fallback
        if not total_pill:
            total_pill = total_fallback

    return spread_pill, total_pill
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/comeon_dom_parser.py backend/tests/providers/test_comeon_dom_parser.py
git commit -m "feat(comeon): add sport-aware market pill selector (OT-inclusive)"
```

---

## Chunk 2: League Discovery and DOM Scraping Engine

### Task 5: JavaScript Evaluation Snippets for League Discovery

**Files:**
- Create: `backend/src/providers/comeon_dom_js.py`

This module contains all the `page.evaluate()` JavaScript snippets as Python string constants.
Keeping JS separate from Python logic makes both easier to read and test.

- [ ] **Step 1: Create JS snippets module**

```python
# backend/src/providers/comeon_dom_js.py
"""
JavaScript evaluation snippets for ComeOn DOM scraping.

All page.evaluate() strings used by the ComeOn DOM scraper.
Kept in one module to separate JS from Python logic.
"""

# Expand all country accordions on the /leagues directory page
# Clicks each collapsed country button to reveal league links
JS_EXPAND_ALL_COUNTRIES = """() => {
    const wrappers = document.querySelectorAll('li[data-expanded="false"]');
    let clicked = 0;
    for (const wrapper of wrappers) {
        const btn = wrapper.querySelector('button');
        if (btn) {
            btn.click();
            clicked++;
        }
    }
    return clicked;
}"""

# Collect all league URLs from the expanded league directory
# Returns [{id, name, href}, ...]
JS_COLLECT_LEAGUE_URLS = """() => {
    const leagues = [];
    const seen = new Set();
    document.querySelectorAll('a[href*="/leagues/"]').forEach(a => {
        const href = a.getAttribute('href');
        // Match: /leagues/{id}-{name} but NOT /leagues (directory itself)
        const match = href.match(/\\/leagues\\/(\\d+)-(.+?)(?:\\/|$|\\?)/);
        if (match && !seen.has(match[1])) {
            seen.add(match[1]);
            leagues.push({
                id: parseInt(match[1]),
                name: a.textContent.trim(),
                href: href.split('?')[0]
            });
        }
    });
    return leagues;
}"""

# Parse all game cards on a league page into structured data
# Returns [{eventId, home, away, timeText, isLive, odds: [{ariaLabel}]}, ...]
JS_PARSE_GAME_CARDS = """() => {
    const cards = document.querySelectorAll('[data-at="game-card"]');
    const events = [];

    for (const card of cards) {
        // Check if live (has score row)
        const scoreRow = card.querySelector('[class*="ScoreRow"]');
        if (scoreRow) continue;  // Skip live events

        // Event link + ID
        const link = card.querySelector('a[data-at="link-to-event"]');
        if (!link) continue;
        const href = link.getAttribute('href') || '';
        const idMatch = href.match(/\\/events\\/(\\d+)/);
        if (!idMatch) continue;

        // Team names
        const participants = card.querySelectorAll('small[class*="Participant"]');
        const teams = [];
        const seenTeams = new Set();
        for (const p of participants) {
            const name = p.textContent.trim();
            if (name && !seenTeams.has(name)) {
                seenTeams.add(name);
                teams.push(name);
            }
        }
        if (teams.length < 2) continue;

        // Time text
        const timeEl = card.querySelector('[class*="game-card-time"]');
        const timeText = timeEl ? timeEl.textContent.trim() : '';

        // Odds buttons (aria-labels)
        const oddsBtns = card.querySelectorAll('button[data-at="sportsbook-selection-btn"]');
        const odds = [];
        for (const btn of oddsBtns) {
            const label = btn.getAttribute('aria-label');
            if (label) odds.push(label);
        }

        events.push({
            eventId: idMatch[1],
            home: teams[0],
            away: teams[1],
            timeText: timeText,
            odds: odds
        });
    }
    return events;
}"""

# Get all market pill texts on the current league page
JS_GET_MARKET_PILLS = """() => {
    const pills = [];
    document.querySelectorAll('[class*="pill__Wrapper"]').forEach(pill => {
        const text = pill.textContent.trim();
        if (text) pills.push(text);
    });
    return pills;
}"""

# Click a market pill by text content, returns true if found and clicked
JS_CLICK_PILL = """(targetText) => {
    const pills = document.querySelectorAll('[class*="pill__Wrapper"]');
    for (const pill of pills) {
        if (pill.textContent.trim() === targetText) {
            const btn = pill.closest('button') || pill;
            btn.click();
            return true;
        }
    }
    return false;
}"""

# Get only the odds aria-labels from game cards (after tab switch)
# Used after clicking a market tab — only need updated odds, not full re-parse
JS_GET_CARD_ODDS = """() => {
    const cards = document.querySelectorAll('[data-at="game-card"]');
    const result = {};
    for (const card of cards) {
        // Skip live
        if (card.querySelector('[class*="ScoreRow"]')) continue;
        const link = card.querySelector('a[data-at="link-to-event"]');
        if (!link) continue;
        const href = link.getAttribute('href') || '';
        const idMatch = href.match(/\\/events\\/(\\d+)/);
        if (!idMatch) continue;

        const oddsBtns = card.querySelectorAll('button[data-at="sportsbook-selection-btn"]');
        const odds = [];
        for (const btn of oddsBtns) {
            const label = btn.getAttribute('aria-label');
            if (label) odds.push(label);
        }
        result[idMatch[1]] = odds;
    }
    return result;
}"""
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/providers/comeon_dom_js.py
git commit -m "feat(comeon): add JS evaluation snippets for DOM scraping"
```

### Task 6: League Page Scraper — Single League Extraction

**Files:**
- Modify: `backend/src/providers/comeon_dom_parser.py`

This function takes a Playwright page, navigates to a league URL, scrapes all 3 market tabs, and returns a list of StandardEvent objects.

- [ ] **Step 1: Add new imports to top of comeon_dom_parser.py**

Add these imports at the top of `backend/src/providers/comeon_dom_parser.py` (alongside existing imports from Tasks 1-3):

```python
import asyncio
import logging
from ..core import StandardEvent
from . import comeon_dom_js as JS

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Implement scrape_league_page**

Add to `backend/src/providers/comeon_dom_parser.py`:

```python
import asyncio
import logging
from ..core import StandardEvent
from . import comeon_dom_js as JS

logger = logging.getLogger(__name__)


async def scrape_league_page(
    page,
    league_href: str,
    site_url: str,
    sport: str,
    league_name: str,
    provider_id: str,
) -> list[StandardEvent]:
    """Scrape a single league page for all events and markets.

    Navigates to the league page, parses 1x2 from default tab,
    then clicks spread and total tabs to get those markets.

    Args:
        page: Playwright page (from context.new_page())
        league_href: League URL path (e.g., /sv/sportsbook/sport/1-fotboll/leagues/134-...)
        site_url: Base site URL (e.g., https://www.comeon.com)
        sport: Canonical sport key
        league_name: League display name
        provider_id: Provider identifier

    Returns:
        List of StandardEvent objects
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

    # Small wait for all cards to finish rendering
    await asyncio.sleep(0.5)

    # Step 1: Parse default tab (1x2/moneyline)
    card_data = await page.evaluate(JS.JS_PARSE_GAME_CARDS)
    if not card_data:
        return []

    # Build event shells with 1x2 markets
    events: dict[str, StandardEvent] = {}
    for card in card_data:
        home = normalize_team_name(card["home"])
        away = normalize_team_name(card["away"])
        start_time = parse_swedish_datetime(card["timeText"])

        labels = [parse_aria_label(l) for l in card["odds"]]
        labels = [l for l in labels if l is not None]
        market = build_outcomes_from_labels(labels, "1x2", home, away)

        markets = [market] if market else []
        events[card["eventId"]] = StandardEvent(
            id=card["eventId"],
            name=f"{home} vs {away}",
            sport=sport,
            provider=provider_id,
            markets=markets,
            league=league_name,
            home_team=home,
            away_team=away,
            start_time=start_time.isoformat() if start_time else "",
        )

    # Step 2: Get market pills and select spread/total
    pills = await page.evaluate(JS.JS_GET_MARKET_PILLS)
    spread_pill, total_pill = select_market_pills(pills, sport)

    # Step 3: Click spread tab and parse
    if spread_pill:
        await _scrape_market_tab(page, spread_pill, "spread", events, provider_id, league_name)

    # Step 4: Click total tab and parse
    if total_pill:
        await _scrape_market_tab(page, total_pill, "total", events, provider_id, league_name)

    logger.debug(
        f"[{provider_id}] League {league_name}: {len(events)} events, "
        f"pills=[{', '.join(pills[:4])}...]"
    )

    return list(events.values())


async def _scrape_market_tab(
    page,
    pill_text: str,
    market_type: str,
    events: dict[str, StandardEvent],
    provider_id: str,
    league_name: str,
) -> None:
    """Click a market tab pill and parse the resulting odds into events."""
    try:
        clicked = await page.evaluate(JS.JS_CLICK_PILL, pill_text)
        if not clicked:
            return

        # Wait for DOM to update after tab click
        await asyncio.sleep(0.8)

        # Get odds per event
        card_odds = await page.evaluate(JS.JS_GET_CARD_ODDS)
        for event_id, aria_labels in card_odds.items():
            event = events.get(event_id)
            if not event:
                continue

            labels = [parse_aria_label(l) for l in aria_labels]
            labels = [l for l in labels if l is not None]
            market = build_outcomes_from_labels(
                labels, market_type, event.home_team, event.away_team
            )
            if market:
                event.markets.append(market)

    except Exception as e:
        logger.debug(f"[{provider_id}] {league_name}: {market_type} tab failed: {e}")
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/providers/comeon_dom_parser.py
git commit -m "feat(comeon): add single league page DOM scraper"
```

### Task 7: Rewrite ComeOn Extractor — League-Based DOM Scraping

**Files:**
- Modify: `backend/src/providers/comeon_multileague.py`

This is the main rewrite. Replace the WS-based extraction with league-based DOM scraping.
Keep the same class name and interface so the orchestrator doesn't need changes.

- [ ] **Step 1: Rewrite comeon_multileague.py**

Replace the entire file content. Key changes:
- Drop `RSocketMixin` from class inheritance
- Remove `MARKET_TYPE_MAP`, `_build_outcome`, `_collect_ws_events`, `_parse_event`, `_enrich_with_detail_markets`, all WS/date-button logic
- Add league discovery, filtering via `target_leagues`, concurrent page scraping
- Accept `target_leagues` kwarg from orchestrator

```python
# backend/src/providers/comeon_multileague.py
"""
ComeOn DOM-Based League Retriever

Extracts events by navigating individual league pages and scraping odds
from the rendered DOM. Market tabs (1x2, Handikapp, Over/Under) provide
all three market types without needing event detail page enrichment.

Replaces the previous WS-based approach which had ~70% reliability
and only captured ~3-5% of football events.
"""

from typing import Dict, Any, List, Optional, Set
import asyncio
import logging

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..core.exceptions import RetryableError
from ..matching.normalizer import normalize_team_name
from . import comeon_dom_js as JS
from .comeon_dom_parser import scrape_league_page

logger = logging.getLogger(__name__)


class ComeOnMultiLeagueRetriever(BrowserRetriever):
    """
    DOM-based ComeOn retriever.

    Strategy: Navigate to sport league directory → discover leagues →
    filter to Pinnacle-matched leagues → scrape each league page with
    concurrent tabs → parse 1x2/spread/total from market tabs.
    """

    # Sport URL mapping: canonical sport key -> ComeOn URL path (no /sv/ prefix)
    SPORT_URL_MAP = {
        'football': '/sportsbook/sport/1-fotboll',
        'basketball': '/sportsbook/sport/2-basket',
        'american_football': '/sportsbook/sport/3-amerikansk-fotboll',
        'ice_hockey': '/sportsbook/sport/4-ishockey',
        'tennis': '/sportsbook/sport/6-tennis',
        'mma': '/sportsbook/sport/7-mma',
        'esports': '/sportsbook/sport/130-esport',
        'baseball': '/sportsbook/sport/12-baseboll',
        'handball': '/sportsbook/sport/10-handboll',
        'rugby': '/sportsbook/sport/16-rugby',
        'cricket': '/sportsbook/sport/17-cricket',
        'table_tennis': '/sportsbook/sport/26-bordtennis',
    }

    # Max concurrent league pages (tabs in single browser context)
    MAX_CONCURRENT_LEAGUES = 8

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")
        self._concurrent_leagues = config.get("concurrent_leagues", self.MAX_CONCURRENT_LEAGUES)

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        raise NotImplementedError("ComeOnMultiLeagueRetriever uses extract() directly")

    async def extract(self, sport: str | List[str], limit: Optional[int] = None, **kwargs) -> List[StandardEvent]:
        """Extract events from one or more sports via league page DOM scraping."""
        target_leagues: Optional[Set[str]] = kwargs.get("target_leagues")
        sports_to_extract = self._resolve_sports(sport)
        logger.debug(f"[{self.provider_id}] Extracting {len(sports_to_extract)} sports: {', '.join(sports_to_extract)}")

        # Ensure browser once for entire extraction run
        await self.transport._ensure_browser()
        page = self.transport.page

        # Dismiss cookie overlay once
        await self._dismiss_cookie_overlay(page)
        self._cookie_dismissed = True

        all_events = []
        sports_attempted = 0

        for sport_key in sports_to_extract:
            try:
                sports_attempted += 1
                sport_events = await self._extract_single_sport(
                    sport_key, target_leagues=target_leagues, limit=limit
                )
                logger.debug(f"[{self.provider_id}] {sport_key}: {len(sport_events)} events")
                all_events.extend(sport_events)
            except Exception as e:
                logger.error(f"[{self.provider_id}] Failed to extract {sport_key}: {e}")

        if not all_events and sports_attempted >= 3:
            raise RetryableError(
                f"0 events from {sports_attempted} sports — possible page/SPA failure",
                provider_id=self.provider_id,
            )

        return all_events

    def _resolve_sports(self, sport: str | List[str]) -> List[str]:
        if isinstance(sport, list):
            return sport
        if sport == "all":
            return list(self.SPORT_URL_MAP.keys())
        return [sport.split('/')[0] if '/' in sport else sport]

    async def _dismiss_cookie_overlay(self, page) -> None:
        """Dismiss OneTrust cookie consent overlay."""
        try:
            btn = await page.query_selector('#onetrust-accept-btn-handler')
            if btn:
                await btn.click()
                await page.wait_for_load_state('domcontentloaded', timeout=5000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass
        try:
            await page.evaluate('''() => {
                const filter = document.querySelector('.onetrust-pc-dark-filter');
                if (filter) filter.remove();
                const sdk = document.querySelector('#onetrust-consent-sdk');
                if (sdk) sdk.remove();
            }''')
        except Exception:
            pass

    async def _extract_single_sport(
        self,
        sport: str,
        target_leagues: Optional[Set[str]] = None,
        limit: Optional[int] = None,
    ) -> List[StandardEvent]:
        """Extract events for a single sport via league page DOM scraping.

        1. Navigate to /sport/{id}/leagues directory
        2. Discover all available leagues
        3. Filter to Pinnacle-matched leagues (or popular leagues as fallback)
        4. Scrape each league page with concurrent tabs
        """
        sport_normalized = sport.split('/')[0] if '/' in sport else sport
        sport_path = self.SPORT_URL_MAP.get(sport_normalized)
        if not sport_path:
            logger.warning(f"[{self.provider_id}] Sport '{sport_normalized}' not supported")
            return []

        page = self.transport.page

        # Validate page is still alive
        try:
            await page.evaluate("() => true", timeout=5000)
        except Exception:
            logger.warning(f"[{self.provider_id}] Page context dead, creating new page")
            try:
                page = await self.transport.context.new_page()
                self.transport.page = page
            except Exception:
                logger.warning(f"[{self.provider_id}] Context dead, full browser reinit")
                await self.transport.close()
                await self.transport._ensure_browser()
                page = self.transport.page
                self._cookie_dismissed = False

        # Step 1: Navigate to league directory
        leagues_url = f"{self.site_url}/sv{sport_path}/leagues"
        try:
            await page.goto(leagues_url, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to load leagues directory for {sport_normalized}: {e}")
            return []

        # Dismiss cookie if needed
        if not getattr(self, '_cookie_dismissed', False):
            await self._dismiss_cookie_overlay(page)
            self._cookie_dismissed = True

        # Step 2: Click the "Alla ligor" tab if not already active
        try:
            await page.evaluate("""() => {
                const tabs = document.querySelectorAll('[role="tab"]');
                for (const tab of tabs) {
                    if (tab.textContent.trim().toLowerCase().includes('alla ligor')) {
                        tab.click();
                        return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # Step 3: Expand all country accordions
        try:
            expanded = await page.evaluate(JS.JS_EXPAND_ALL_COUNTRIES)
            if expanded > 0:
                await asyncio.sleep(0.5)
                logger.debug(f"[{self.provider_id}] {sport_normalized}: expanded {expanded} countries")
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Country expansion failed: {e}")

        # Step 4: Collect all league URLs
        all_leagues = await page.evaluate(JS.JS_COLLECT_LEAGUE_URLS)
        if not all_leagues:
            logger.warning(f"[{self.provider_id}] {sport_normalized}: no leagues found")
            return []

        logger.debug(f"[{self.provider_id}] {sport_normalized}: discovered {len(all_leagues)} leagues")

        # Step 5: Filter leagues
        filtered_leagues = self._filter_leagues(all_leagues, target_leagues, sport_normalized)
        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"scraping {len(filtered_leagues)}/{len(all_leagues)} leagues"
        )

        # Step 6: Scrape league pages concurrently
        semaphore = asyncio.Semaphore(self._concurrent_leagues)
        all_events = []

        async def scrape_with_semaphore(league_info):
            async with semaphore:
                league_page = await self.transport.context.new_page()
                try:
                    events = await scrape_league_page(
                        page=league_page,
                        league_href=league_info["href"],
                        site_url=self.site_url,
                        sport=sport_normalized,
                        league_name=league_info["name"],
                        provider_id=self.provider_id,
                    )
                    return events
                except Exception as e:
                    logger.debug(
                        f"[{self.provider_id}] {league_info['name']}: scrape failed: {e}"
                    )
                    return []
                finally:
                    try:
                        await league_page.close()
                    except Exception:
                        pass

        # Run all league scrapes concurrently (limited by semaphore)
        tasks = [scrape_with_semaphore(league) for league in filtered_leagues]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_events.extend(result)
            elif isinstance(result, Exception):
                logger.debug(f"[{self.provider_id}] League scrape exception: {result}")

        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"{len(all_events)} events from {len(filtered_leagues)} leagues"
        )
        return all_events

    def _filter_leagues(
        self,
        all_leagues: list[dict],
        target_leagues: Optional[Set[str]],
        sport: str,
    ) -> list[dict]:
        """Filter discovered leagues to those with Pinnacle coverage.

        Uses the same target_leagues set that Kambi uses — fuzzy substring
        matching of ComeOn's Swedish league names against Pinnacle league names.

        Falls back to popular leagues (first 10) if no target_leagues provided.
        """
        if not target_leagues:
            # Fallback: take first 10 leagues (popular leagues appear first)
            return all_leagues[:10]

        filtered = []
        for league in all_leagues:
            league_name = league["name"].lower().strip()
            # Strip common prefixes: "England Premier League" -> "premier league"
            # ComeOn uses "Country LeagueName" format
            for target in target_leagues:
                if target in league_name or league_name in target:
                    filtered.append(league)
                    break
                # Also try stripping the first word (country name)
                parts = league_name.split(" ", 1)
                if len(parts) > 1 and (target in parts[1] or parts[1] in target):
                    filtered.append(league)
                    break

        if not filtered:
            # No matches — fall back to first 10
            logger.debug(
                f"[{self.provider_id}] {sport}: league filter matched 0/{len(all_leagues)}, "
                f"using top 10"
            )
            return all_leagues[:10]

        return filtered
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/providers/comeon_multileague.py
git commit -m "refactor(comeon): rewrite extractor from WS to DOM-based league scraping

Replaces RSocket WebSocket extraction with DOM scraping via league pages.
- Navigates league pages instead of sport pages
- Parses odds from aria-labels on rendered DOM elements
- Clicks market tabs (1x2/Handikapp/Over/Under) for all 3 market types
- 8 concurrent league pages via asyncio.Semaphore
- Filters leagues to Pinnacle-matched via target_leagues from orchestrator
- Removes Pass 2 event detail enrichment (tabs replace this)
- Drops RSocketMixin dependency"
```

### Task 7b: Test League Filtering Logic

**Files:**
- Modify: `backend/tests/providers/test_comeon_dom_parser.py`

- [ ] **Step 1: Write tests for _filter_leagues**

Add to test file:

```python
from src.providers.comeon_multileague import ComeOnMultiLeagueRetriever


class TestFilterLeagues:
    """Test league filtering against Pinnacle target leagues."""

    def setup_method(self):
        config = {"provider_id": "comeon", "domain": "comeon.com"}
        self.retriever = ComeOnMultiLeagueRetriever.__new__(ComeOnMultiLeagueRetriever)
        self.retriever.provider_id = "comeon"

    def test_filters_to_matching_leagues(self):
        leagues = [
            {"id": 134, "name": "England Premier League", "href": "/leagues/134-..."},
            {"id": 999, "name": "Tonga Division 2", "href": "/leagues/999-..."},
            {"id": 171, "name": "Spanien La Liga", "href": "/leagues/171-..."},
        ]
        target = {"premier league", "la liga", "bundesliga"}
        result = self.retriever._filter_leagues(leagues, target, "football")
        assert len(result) == 2
        assert result[0]["id"] == 134
        assert result[1]["id"] == 171

    def test_fallback_to_top_10_when_no_targets(self):
        leagues = [{"id": i, "name": f"League {i}", "href": f"/leagues/{i}"} for i in range(20)]
        result = self.retriever._filter_leagues(leagues, None, "football")
        assert len(result) == 10

    def test_fallback_to_top_10_when_no_matches(self):
        leagues = [
            {"id": 1, "name": "Obscure League", "href": "/leagues/1-..."},
            {"id": 2, "name": "Another Obscure", "href": "/leagues/2-..."},
        ]
        target = {"premier league", "la liga"}
        result = self.retriever._filter_leagues(leagues, target, "football")
        # Falls back to first 10 (or all if fewer)
        assert len(result) == 2

    def test_strips_country_prefix(self):
        leagues = [
            {"id": 134, "name": "England Premier League", "href": "/leagues/134-..."},
        ]
        target = {"premier league"}
        result = self.retriever._filter_leagues(leagues, target, "football")
        assert len(result) == 1
```

- [ ] **Step 2: Run tests**

Run: `cd backend && python -m pytest tests/providers/test_comeon_dom_parser.py::TestFilterLeagues -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/providers/test_comeon_dom_parser.py
git commit -m "test(comeon): add league filtering tests"
```

### Task 8: Smoke Test — Run Extraction Against Live Site

**Files:** None (manual verification)

- [ ] **Step 1: Run extraction to verify DOM scraping works**

```bash
cd backend
python -m src.app extract comeon
```

Expected: Events extracted from league pages with 1x2 + spread + total markets.
Check logs for:
- League discovery count (e.g., "discovered 99 leagues")
- League filter count (e.g., "scraping 25/99 leagues")
- Events per league
- Market types per event
- Total events: 200+ (vs previous ~103)
- Duration: <600s

- [ ] **Step 2: Compare against Pinnacle match rate**

Query via sqlite MCP:
```sql
SELECT sport, events_processed, events_matched, events_unmatched,
       moneyline_count, spread_count, total_count
FROM sport_run_metrics
WHERE run_id = (SELECT id FROM extraction_runs ORDER BY started_at DESC LIMIT 1)
  AND provider_id = 'comeon'
ORDER BY events_processed DESC;
```

- [ ] **Step 3: Commit any hotfixes**

If the smoke test reveals issues, fix them and commit.

---

## Chunk 3: Boost Scraping and Config Changes

### Task 9: Update providers.yaml for Lyllo Independent Boost Scraping

**Files:**
- Modify: `backend/src/config/providers.yaml`

- [ ] **Step 1: Read current comeon/lyllo boost config**

Read the boost sections of providers.yaml to find exact lines to modify.

- [ ] **Step 2: Update providers.yaml**

Changes needed:
1. In comeon boost `shared_with`, remove `lyllo` (keep `hajper`, `snabbare`)
2. Add/enable lyllo boost entry: `enabled: true`, `type: comeon`, `url: https://www.lyllocasino.com`

- [ ] **Step 3: Commit**

```bash
git add backend/src/config/providers.yaml
git commit -m "config(comeon): split lyllo boost scraping from comeon shared_with

Lyllo has different boost odds (e.g., 2.47 vs 2.60 on comeon).
Scrape independently instead of assuming identical odds."
```

### Task 10: Verify Lyllo Boost Independence

**Files:** None (manual verification)

- [ ] **Step 1: Run boost scraping for comeon and lyllo**

```bash
cd backend
python -m src.app scrape-specials comeon lyllo
```

- [ ] **Step 2: Query DB to verify different odds**

```sql
SELECT provider, title, boosted_odds, shared_providers
FROM specials
WHERE provider IN ('comeon', 'lyllo')
ORDER BY title, provider;
```

Expected: Same boost titles but different `boosted_odds` values for lyllo vs comeon.

- [ ] **Step 3: Commit any fixes**

---

## Chunk 4: Cleanup and Final Verification

### Task 11: Remove Dead WS Code

**Files:**
- Delete or clean: `backend/src/providers/comeon_multileague.py` (verify no WS remnants)

- [ ] **Step 1: Verify no WS/RSocket imports remain in comeon_multileague.py**

Check the rewritten file has no references to:
- `RSocketMixin`
- `_decode_rsocket_frame`
- `ws_messages`
- `MARKET_TYPE_MAP`
- `_build_outcome` (old WS version)
- `_enrich_with_detail_markets`
- `_collect_ws_events`
- `JS_DISCOVER_EVENT_URLS`

- [ ] **Step 2: Commit cleanup if needed**

### Task 12: End-to-End Verification

- [ ] **Step 1: Run full extraction cycle (sharp + comeon)**

```bash
cd backend
python -m src.app extract pinnacle comeon
```

- [ ] **Step 2: Check extraction report**

Query:
```sql
SELECT p.provider_id, p.status, p.events_processed, p.events_matched,
       p.odds_processed, p.duration_seconds
FROM provider_run_metrics p
JOIN extraction_runs r ON p.run_id = r.id
WHERE r.id = (SELECT id FROM extraction_runs ORDER BY started_at DESC LIMIT 1)
ORDER BY p.provider_id;
```

Expected:
- comeon: 200+ events, high match rate, 3 market types
- Duration: <600s (within 900s budget)

- [ ] **Step 3: Verify opportunities generated**

```sql
SELECT COUNT(*) as total,
       SUM(CASE WHEN edge_pct > 0 THEN 1 ELSE 0 END) as positive_ev
FROM opportunities
WHERE provider1_id = 'comeon';
```

Expected: More opportunities than before (more events = more matches = more value bets).

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(comeon): complete DOM-based league scraper migration

Coverage: ~15 → 200+ football events
Reliability: ~70% → 95%+
Markets: inconsistent → all 3 (1x2/spread/total) per event
Pass 2 enrichment: removed (tabs replace it)
Lyllo boost: independent scraping (different odds confirmed)"
```
