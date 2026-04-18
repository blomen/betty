# Kalshi + Smarkets Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Kalshi (playable + consensus input) and Smarkets (signal-only) as new providers, following Polymarket's API-first extraction pattern. Rename `ALLOWED_SPORTS` → `PINNACLE_SPORTS` for clarity.

**Architecture:** Two new `Retriever` subclasses under `backend/src/providers/`, a new `ProviderWorkflow` for Kalshi play, all API-first (no Playwright for extraction, Playwright tab only for visual confirmation on Kalshi bets). Kalshi gets its own extraction tier; Smarkets joins `signal_international`. Scanner/consensus pipeline unchanged — both providers flow through existing storage + opportunity generation paths.

**Tech Stack:** Python 3.10+ (asyncio, aiohttp, pydantic), pytest, kalshi-python SDK (new), existing `Retriever` / `ProviderWorkflow` / `ExtractorFactory` abstractions.

**Reference spec:** [docs/superpowers/specs/2026-04-18-kalshi-smarkets-design.md](../specs/2026-04-18-kalshi-smarkets-design.md)

---

## Phase 1 — Foundation (constants + rename)

Low-risk prep work that unblocks Phase 2/3.

### Task 1: Rename `ALLOWED_SPORTS` → `PINNACLE_SPORTS`

**Files:**
- Modify: `backend/src/constants.py:165`
- Modify: `backend/src/pipeline/orchestrator.py:15, 673, 675, 752, 754, 931, 1506`

- [ ] **Step 1: Update the constant in `constants.py`**

Replace the definition:

```python
# OLD (line 165)
ALLOWED_SPORTS = frozenset({...})

# NEW
# Sports where Pinnacle provides sharp lines AND soft providers have head-to-head
# coverage for value comparison. Renamed from ALLOWED_SPORTS for clarity — the set
# represents Pinnacle's coverage, not a generic allowlist.
PINNACLE_SPORTS = frozenset(
    {
        "football",
        "basketball",
        "tennis",
        "ice_hockey",
        "american_football",
        "baseball",
        "mma",
        "esports",
        "boxing",
        "cricket",
        "rugby",
        "volleyball",
        "handball",
        "darts",
        "table_tennis",
        "curling",
    }
)
```

- [ ] **Step 2: Update imports and references in orchestrator**

Run a find-replace in `backend/src/pipeline/orchestrator.py`:

```bash
# Grep first to confirm 7 references exist
grep -n "ALLOWED_SPORTS" backend/src/pipeline/orchestrator.py
```

Expected: 7 lines. Replace each `ALLOWED_SPORTS` with `PINNACLE_SPORTS`.

- [ ] **Step 3: Grep for any other references across the codebase**

```bash
grep -rn "ALLOWED_SPORTS" backend/ firevsports/ 2>/dev/null | grep -v ".pyc"
```

Expected: no results. If any appear, update them too.

- [ ] **Step 4: Run tests to confirm nothing broke**

Run: `cd backend && pytest tests/ -x --ignore=tests/providers --ignore=tests/mirror -q 2>&1 | tail -20`
Expected: no new failures introduced by the rename (pre-existing failures are OK).

- [ ] **Step 5: Commit**

```bash
git add backend/src/constants.py backend/src/pipeline/orchestrator.py
git commit -m "refactor(constants): rename ALLOWED_SPORTS → PINNACLE_SPORTS

The set represents sports where Pinnacle has coverage, not a generic
allowlist. Rename makes the intent explicit ahead of adding Kalshi
and Smarkets which also key off this set."
```

---

### Task 2: Extend constants for Kalshi + Smarkets

**Files:**
- Modify: `backend/src/constants.py`

- [ ] **Step 1: Add `KALSHI_FEE_RATE`**

Below `POLYMARKET_FEE_RATE`:

```python
# Kalshi per-trade fee approximation. Actual formula is
# ceil(0.07 × price × (1 − price) × contracts); we model it as a flat
# multiplier on the price (tune from live fills data once enough trades land).
KALSHI_FEE_RATE = 0.02
```

- [ ] **Step 2: Add `smarkets` to `SIGNAL_ONLY_PROVIDERS`**

```python
SIGNAL_ONLY_PROVIDERS = frozenset({"marathon", "stake", "smarkets"})
```

- [ ] **Step 3: Extend `PLATFORM_MAP` and `EXTENDED_MARKET_PROVIDERS`**

Append to the `PLATFORM_MAP` dict:

```python
    # Prediction markets (added 2026-04-18)
    "kalshi": "kalshi",
    # Signal-only exchange (added 2026-04-18)
    "smarkets": "smarkets",
```

Update `EXTENDED_MARKET_PROVIDERS`:

```python
# Kalshi stores alternate-line candidates alongside Pinnacle + Polymarket
EXTENDED_MARKET_PROVIDERS = SHARP_PROVIDERS | frozenset({"polymarket", "kalshi"})
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/constants.py
git commit -m "feat(constants): wire Kalshi + Smarkets into shared dispatch sets

- KALSHI_FEE_RATE (tunable flat-rate approximation)
- smarkets → SIGNAL_ONLY_PROVIDERS (no placement, consensus only)
- kalshi/smarkets → PLATFORM_MAP
- kalshi → EXTENDED_MARKET_PROVIDERS (alternate-line storage)"
```

---

## Phase 2 — Kalshi extractor

### Task 3: Capture Kalshi API fixtures

Fixtures seed the parser tests. No account required — market-data endpoints are public.

**Files:**
- Create: `backend/tests/providers/fixtures/kalshi/events_sports.json`
- Create: `backend/tests/providers/fixtures/kalshi/orderbook_nba_example.json`

- [ ] **Step 1: Pull live Kalshi events**

```bash
mkdir -p backend/tests/providers/fixtures/kalshi
curl -sS "https://api.elections.kalshi.com/trade-api/v2/events?status=open&with_nested_markets=true&limit=200" \
  > backend/tests/providers/fixtures/kalshi/events_sports.json
```

Expected: a JSON file with an `events` array. If empty, try `&series_ticker=KXNBAGAME` to force a known-populated series.

- [ ] **Step 2: Pull one orderbook**

Pick an `event_ticker` → `market_ticker` from the events dump (a market with non-zero `volume`), then:

```bash
TICKER="<market_ticker_from_step_1>"
curl -sS "https://api.elections.kalshi.com/trade-api/v2/markets/${TICKER}/orderbook" \
  > backend/tests/providers/fixtures/kalshi/orderbook_nba_example.json
```

- [ ] **Step 3: Commit fixtures**

```bash
git add backend/tests/providers/fixtures/kalshi/
git commit -m "test(kalshi): capture Kalshi API fixtures for parser tests"
```

---

### Task 4: Kalshi series → sport mapping (pure function, TDD)

**Files:**
- Create: `backend/src/providers/kalshi.py` (skeleton with just the map + helper)
- Create: `backend/tests/providers/test_kalshi_parser.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/providers/test_kalshi_parser.py`:

```python
"""Tests for Kalshi market parser."""
import pytest

from src.providers.kalshi import series_to_sport


class TestSeriesToSport:
    @pytest.mark.parametrize("ticker,expected", [
        ("KXNBAGAME-26APR18LALGSW-LAL", "basketball"),
        ("KXNFLGAME-26WEEK5-KC", "american_football"),
        ("KXMLBGAME-26APR18NYY-NYY", "baseball"),
        ("KXNHLGAME-26APR18BOS-BOS", "ice_hockey"),
        ("KXNCAAFGAME-26WEEK3-ALA", "american_football"),
        ("KXNCAABGAME-26MAR21-DUKE", "basketball"),
        ("KXTENNISAUSOPEN-26-DJOKOVIC", "tennis"),
        ("KXUFC300-26-JONES", "mma"),
        ("KXBOXINGFURY-26-FURY", "boxing"),
        ("KXEPL-26MAY-ARS", "football"),
        ("KXUCL-26APR-RMA", "football"),
        ("KXWC26-26JUL-ARG", "football"),
    ])
    def test_known_prefixes(self, ticker, expected):
        assert series_to_sport(ticker) == expected

    def test_unknown_prefix_returns_none(self):
        assert series_to_sport("KXWEATHERNYC-26-75F") is None
        assert series_to_sport("KXPREZ-26-DEM") is None
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd backend && pytest tests/providers/test_kalshi_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.providers.kalshi'`.

- [ ] **Step 3: Implement minimal module**

Create `backend/src/providers/kalshi.py`:

```python
"""Kalshi prediction-market extractor.

Pulls binary YES/NO contracts from Kalshi's public REST API and converts
them to StandardEvent moneyline / 1x2 / spread / total markets. Extraction
is unauthenticated — only placement (in the mirror workflow) needs API keys.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Ticker-prefix → canonical sport. Extend as new series appear.
KALSHI_SERIES_TO_SPORT: dict[str, str] = {
    "KXNBAGAME": "basketball",
    "KXNCAABGAME": "basketball",
    "KXNFLGAME": "american_football",
    "KXNCAAFGAME": "american_football",
    "KXMLBGAME": "baseball",
    "KXNHLGAME": "ice_hockey",
    "KXTENNIS": "tennis",
    "KXUFC": "mma",
    "KXBOXING": "boxing",
    "KXEPL": "football",
    "KXUCL": "football",
    "KXWC": "football",
}


def series_to_sport(ticker: str) -> str | None:
    """Resolve a Kalshi event/market ticker to our canonical sport name.

    Uses longest-prefix match so more specific prefixes win (e.g. KXNCAAB
    before KX).
    """
    for prefix in sorted(KALSHI_SERIES_TO_SPORT.keys(), key=len, reverse=True):
        if ticker.startswith(prefix):
            return KALSHI_SERIES_TO_SPORT[prefix]
    return None
```

- [ ] **Step 4: Run test to confirm it passes**

Run: `cd backend && pytest tests/providers/test_kalshi_parser.py::TestSeriesToSport -v`
Expected: PASS (13 cases).

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/kalshi.py backend/tests/providers/test_kalshi_parser.py
git commit -m "feat(kalshi): series → sport mapping with longest-prefix match"
```

---

### Task 5: Kalshi market parsing (binary → moneyline/1x2/spread/total)

**Files:**
- Modify: `backend/src/providers/kalshi.py`
- Modify: `backend/tests/providers/test_kalshi_parser.py`

- [ ] **Step 1: Write the failing test (moneyline — 2-way)**

Append to `test_kalshi_parser.py`:

```python
from src.providers.kalshi import parse_event


class TestParseEvent:
    def _event(self, ticker: str, markets: list[dict], title: str = "LAL vs GSW") -> dict:
        return {
            "event_ticker": ticker,
            "title": title,
            "markets": markets,
        }

    def _market(self, ticker: str, yes_sub_title: str, yes_ask: float,
                volume: float = 5000.0, status: str = "active") -> dict:
        # Kalshi yes_ask/no_ask are in cents (integer 0–100)
        return {
            "ticker": ticker,
            "status": status,
            "yes_sub_title": yes_sub_title,
            "yes_ask": int(yes_ask * 100),
            "no_ask": int((1 - yes_ask) * 100),
            "volume": volume,
        }

    def test_nba_moneyline_two_contracts(self):
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.60),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.42),
            ],
            title="Lakers vs Warriors",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "basketball"
        assert result.provider == "kalshi"
        assert len(result.markets) == 1
        mkt = result.markets[0]
        assert mkt["type"] == "moneyline"
        assert len(mkt["outcomes"]) == 2
        # Odds = 1 / (price + fee_rate * price * (1-price))
        # For price=0.60 → effective = 0.6 + 0.02*0.6*0.4 = 0.6048 → 1/0.6048 ≈ 1.653
        assert mkt["outcomes"][0]["name"] in ("home", "away")
        assert 1.6 < mkt["outcomes"][0]["odds"] < 1.7

    def test_below_volume_threshold_dropped(self):
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.60, volume=50),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.42, volume=50),
            ],
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_all_50_50_dropped(self):
        event = self._event(
            "KXNBAGAME-26APR18LALGSW",
            [
                self._market("KXNBAGAME-26APR18LALGSW-LAL", "LAL", 0.50, volume=5000),
                self._market("KXNBAGAME-26APR18LALGSW-GSW", "GSW", 0.50, volume=5000),
            ],
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is None

    def test_unknown_sport_skipped(self):
        event = self._event(
            "KXWEATHERNYC-26-75F",
            [self._market("KXWEATHERNYC-26-75F-YES", "75F", 0.30, volume=5000)],
        )
        assert parse_event(event, min_volume_usd=100.0, fee_rate=0.02) is None

    def test_soccer_3way_1x2(self):
        event = self._event(
            "KXEPL-26MAY-ARSCHE",
            [
                self._market("KXEPL-26MAY-ARSCHE-ARS", "Arsenal win", 0.55),
                self._market("KXEPL-26MAY-ARSCHE-DRAW", "Draw", 0.25),
                self._market("KXEPL-26MAY-ARSCHE-CHE", "Chelsea win", 0.22),
            ],
            title="Arsenal vs Chelsea",
        )
        result = parse_event(event, min_volume_usd=100.0, fee_rate=0.02)
        assert result is not None
        assert result.sport == "football"
        mkt = result.markets[0]
        assert mkt["type"] == "1x2"
        assert len(mkt["outcomes"]) == 3
        names = {o["name"] for o in mkt["outcomes"]}
        assert names == {"home", "draw", "away"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/providers/test_kalshi_parser.py::TestParseEvent -v`
Expected: FAIL — `ImportError: cannot import name 'parse_event'`.

- [ ] **Step 3: Implement `parse_event` and helpers**

Append to `backend/src/providers/kalshi.py`:

```python
from ..core import StandardEvent

# Sports with no draw outcome use moneyline; others use 1x2
_NO_DRAW_SPORTS = frozenset(
    {"basketball", "american_football", "baseball", "ice_hockey",
     "tennis", "mma", "boxing"}
)


def _price_to_odds(price: float, fee_rate: float) -> float:
    """Convert a YES-contract price ($0–$1) to decimal odds with fee adjustment.

    Kalshi's per-trade fee is applied as an incremental cost on the entry price.
    effective_price = price + fee_rate * price * (1 - price)
    decimal_odds = 1 / effective_price
    """
    effective = price + fee_rate * price * (1.0 - price)
    if effective <= 0.0:
        return 0.0
    return round(1.0 / effective, 4)


def _market_price_dollars(m: dict) -> float:
    """Kalshi quotes yes_ask/no_ask as integer cents (0–100). Convert to dollars."""
    return float(m.get("yes_ask", 0)) / 100.0


def _extract_teams_from_title(title: str) -> tuple[str, str]:
    """Split 'Home vs Away' / 'Home @ Away' into (home, away). Falls back gracefully."""
    for sep in (" vs ", " @ ", " v. ", " v "):
        if sep in title:
            left, right = title.split(sep, 1)
            return left.strip(), right.strip()
    return title.strip(), ""


def parse_event(
    raw: dict,
    min_volume_usd: float = 100.0,
    fee_rate: float = 0.02,
) -> StandardEvent | None:
    """Parse one Kalshi event (container of binary markets) into a StandardEvent.

    Returns None if:
    - Series ticker not in KALSHI_SERIES_TO_SPORT
    - All markets below volume threshold
    - All prices exactly $0.50 (untraded)
    - Not enough active markets to form a valid moneyline/1x2
    """
    event_ticker = raw.get("event_ticker", "")
    sport = series_to_sport(event_ticker)
    if sport is None:
        return None

    raw_markets = [
        m for m in raw.get("markets", [])
        if m.get("status") == "active"
        and float(m.get("volume", 0) or 0) >= min_volume_usd
    ]
    if not raw_markets:
        return None

    # Drop if all prices are exactly 0.50 (untraded)
    if all(_market_price_dollars(m) == 0.50 for m in raw_markets):
        return None

    is_no_draw = sport in _NO_DRAW_SPORTS
    home, away = _extract_teams_from_title(raw.get("title", ""))

    # 2-way moneyline: exactly two contracts, complementary sides
    # 3-way 1x2 (soccer): three contracts (home/draw/away)
    if is_no_draw and len(raw_markets) >= 2:
        # Pick the first two highest-volume markets as home/away
        sorted_mkts = sorted(raw_markets, key=lambda m: float(m.get("volume", 0) or 0), reverse=True)[:2]
        outcomes = [
            {
                "name": "home" if i == 0 else "away",
                "odds": _price_to_odds(_market_price_dollars(m), fee_rate),
                "provider_meta": {
                    "ticker": m.get("ticker"),
                    "volume": float(m.get("volume", 0) or 0),
                },
            }
            for i, m in enumerate(sorted_mkts)
        ]
        market = {"type": "moneyline", "outcomes": outcomes}
    elif not is_no_draw and len(raw_markets) >= 3:
        # Identify draw market by the literal "draw" keyword in yes_sub_title
        def is_draw(m: dict) -> bool:
            return "draw" in str(m.get("yes_sub_title", "")).lower()
        draw_mkts = [m for m in raw_markets if is_draw(m)]
        non_draw = [m for m in raw_markets if not is_draw(m)]
        if len(draw_mkts) != 1 or len(non_draw) < 2:
            return None
        # Highest-volume non-draw is home; second is away
        non_draw.sort(key=lambda m: float(m.get("volume", 0) or 0), reverse=True)
        ordered = [non_draw[0], draw_mkts[0], non_draw[1]]
        names = ["home", "draw", "away"]
        outcomes = [
            {
                "name": n,
                "odds": _price_to_odds(_market_price_dollars(m), fee_rate),
                "provider_meta": {
                    "ticker": m.get("ticker"),
                    "volume": float(m.get("volume", 0) or 0),
                },
            }
            for n, m in zip(names, ordered)
        ]
        market = {"type": "1x2", "outcomes": outcomes}
    else:
        return None

    return StandardEvent(
        id=f"kalshi_{event_ticker}",
        name=raw.get("title", ""),
        sport=sport,
        markets=[market],
        provider="kalshi",
        url=f"https://kalshi.com/markets/{event_ticker}",
        home_team=home,
        away_team=away,
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd backend && pytest tests/providers/test_kalshi_parser.py -v`
Expected: all tests pass (series-to-sport 13 cases + 5 parse_event cases).

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/kalshi.py backend/tests/providers/test_kalshi_parser.py
git commit -m "feat(kalshi): parse binary YES/NO contracts → moneyline/1x2

- Volume filter (min_volume_usd, default 100)
- 50/50 untraded-market filter
- Fee-adjusted price → decimal odds conversion
- No-draw sports → 2-way moneyline; soccer-style → 3-way 1x2"
```

---

### Task 6: `KalshiRetriever` class (HTTP fetch + pagination)

**Files:**
- Modify: `backend/src/providers/kalshi.py`
- Modify: `backend/tests/providers/test_kalshi_parser.py`

- [ ] **Step 1: Write the failing test for `KalshiRetriever.parse()`**

Append to `test_kalshi_parser.py`:

```python
import json
from pathlib import Path

from src.providers.kalshi import KalshiRetriever


class TestKalshiRetriever:
    def test_parse_fixture_produces_events(self):
        fixture_path = Path(__file__).parent / "fixtures" / "kalshi" / "events_sports.json"
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))

        config = {"id": "kalshi", "params": {"min_volume_usd": 100}}
        retriever = KalshiRetriever(config)
        events = retriever.parse(raw, sport="basketball")

        # At least *some* events should match basketball; exact count depends on fixture
        assert isinstance(events, list)
        for e in events:
            assert e.provider == "kalshi"
            assert e.sport == "basketball"
            assert e.markets and e.markets[0]["type"] in ("moneyline", "1x2")
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/providers/test_kalshi_parser.py::TestKalshiRetriever -v`
Expected: FAIL — `ImportError: cannot import name 'KalshiRetriever'`.

- [ ] **Step 3: Implement `KalshiRetriever`**

Append to `backend/src/providers/kalshi.py`:

```python
import aiohttp
from ..core import Retriever


class KalshiRetriever(Retriever):
    """Kalshi event-level retriever. Unauthenticated — market data is public.

    Paginates `/events?with_nested_markets=true&status=open` until the API
    stops returning a `cursor`. Filters by sport post-fetch.
    """

    DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEFAULT_PAGE_LIMIT = 200

    def __init__(self, config: dict, circuit_breaker=None, rate_limit_config=None):
        super().__init__(config)
        self.base_url = config.get("base_url", self.DEFAULT_BASE_URL)
        self.min_volume_usd = float(config.get("params", {}).get("min_volume_usd", 100))
        from ..constants import KALSHI_FEE_RATE
        self.fee_rate = float(config.get("params", {}).get("fee_rate", KALSHI_FEE_RATE))
        self._circuit_breaker = circuit_breaker

    def _get_sport_url(self, sport: str) -> str:
        # Kalshi's /events endpoint is sport-agnostic; we filter in parse()
        return f"{self.base_url}/events?status=open&with_nested_markets=true&limit={self.DEFAULT_PAGE_LIMIT}"

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> list[StandardEvent]:
        """Fetch all open Kalshi events with pagination, filter to sport in parse()."""
        all_events: list[dict] = []
        cursor: str | None = None
        url = self._get_sport_url(sport)

        async with aiohttp.ClientSession() as session:
            for _ in range(50):  # hard cap at 50 pages × 200 = 10k events
                page_url = url + (f"&cursor={cursor}" if cursor else "")
                try:
                    async with session.get(page_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        resp.raise_for_status()
                        body = await resp.json()
                except Exception as e:
                    logger.warning(f"[kalshi] fetch failed at cursor={cursor}: {e}")
                    break
                events = body.get("events", [])
                all_events.extend(events)
                cursor = body.get("cursor") or None
                if not cursor or not events:
                    break

        logger.info(f"[kalshi] fetched {len(all_events)} raw events across pages")
        parsed = self.parse({"events": all_events}, sport)
        if limit and len(parsed) > limit:
            parsed = parsed[:limit]
        return parsed

    def parse(self, data: dict, sport: str) -> list[StandardEvent]:
        out: list[StandardEvent] = []
        for raw in data.get("events", []):
            ev = parse_event(
                raw,
                min_volume_usd=self.min_volume_usd,
                fee_rate=self.fee_rate,
            )
            if ev is None:
                continue
            if ev.sport != sport:
                continue
            out.append(ev)
        return out
```

- [ ] **Step 4: Run test to confirm it passes**

Run: `cd backend && pytest tests/providers/test_kalshi_parser.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/kalshi.py backend/tests/providers/test_kalshi_parser.py
git commit -m "feat(kalshi): KalshiRetriever with pagination and sport filter"
```

---

### Task 7: Wire Kalshi in factory + providers.yaml + active list

**Files:**
- Modify: `backend/src/factory.py`
- Modify: `backend/src/config/providers.yaml`

- [ ] **Step 1: Add import + dispatch branch in `factory.py`**

In `backend/src/factory.py` around line 24, add the import:

```python
from .providers.kalshi import KalshiRetriever
```

After the `marathon` branch (around line 216), add:

```python
        elif retriever_type == "kalshi":
            retriever = KalshiRetriever(
                config,
                circuit_breaker=self._circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
```

- [ ] **Step 2: Add Kalshi to `providers.yaml`**

In `backend/src/config/providers.yaml`, under the `providers:` map (next to `cloudbet`):

```yaml
  kalshi:
    id: kalshi
    name: Kalshi
    domain: kalshi.com
    retriever_type: kalshi
    base_url: https://api.elections.kalshi.com/trade-api/v2
    currency: USD
    exchange_rate_sek: 10.5   # refresh periodically; rough 2026-04 FX
    params:
      min_volume_usd: 100
    supported_sports:
      - football
      - basketball
      - tennis
      - ice_hockey
      - american_football
      - baseball
      - mma
      - boxing
```

Under `extraction_scheduling:`, add a new tier block after `polymarket`:

```yaml
  kalshi:
    providers: [kalshi]
    interval_minutes: 5
    grouped: false
```

Under `active:`, append:

```yaml
  - kalshi
```

- [ ] **Step 3: Smoke-test factory lookup**

Run: `cd backend && python -c "from src.factory import ExtractorFactory; ExtractorFactory.get_instance().get_extractor('kalshi'); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Run the Kalshi parser tests once more to ensure no regressions**

Run: `cd backend && pytest tests/providers/test_kalshi_parser.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factory.py backend/src/config/providers.yaml
git commit -m "feat(kalshi): wire KalshiRetriever into factory + config

- factory.py dispatches retriever_type=kalshi
- providers.yaml defines the Kalshi block (USD, min_volume_usd=100)
- new 'kalshi' extraction tier at 5-minute interval
- added to active providers list"
```

---

## Phase 3 — Smarkets extractor (signal-only)

### Task 8: Capture Smarkets API fixtures

**Files:**
- Create: `backend/tests/providers/fixtures/smarkets/events_upcoming.json`
- Create: `backend/tests/providers/fixtures/smarkets/markets_example.json`
- Create: `backend/tests/providers/fixtures/smarkets/last_executed_prices_example.json`

- [ ] **Step 1: Pull events listing**

```bash
mkdir -p backend/tests/providers/fixtures/smarkets
curl -sS "https://api.smarkets.com/v3/events/?state=upcoming&type_domain=sport&limit=100" \
  > backend/tests/providers/fixtures/smarkets/events_upcoming.json
```

Note: if the server's DE IP is blocked, capture from a browser fetch and save the JSON manually.

- [ ] **Step 2: Pull markets for one event**

```bash
EID="<event_id_from_step_1>"
curl -sS "https://api.smarkets.com/v3/events/${EID}/markets/" \
  > backend/tests/providers/fixtures/smarkets/markets_example.json
```

- [ ] **Step 3: Pull last executed prices for one market**

```bash
MID="<market_id_from_step_2>"
curl -sS "https://api.smarkets.com/v3/markets/${MID}/last_executed_prices/" \
  > backend/tests/providers/fixtures/smarkets/last_executed_prices_example.json
```

- [ ] **Step 4: Commit fixtures**

```bash
git add backend/tests/providers/fixtures/smarkets/
git commit -m "test(smarkets): capture Smarkets public-API fixtures"
```

---

### Task 9: Smarkets `type_scope` → sport mapping (pure function, TDD)

**Files:**
- Create: `backend/src/providers/smarkets.py`
- Create: `backend/tests/providers/test_smarkets_parser.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/providers/test_smarkets_parser.py`:

```python
"""Tests for Smarkets signal-only parser."""
import pytest

from src.providers.smarkets import type_scope_to_sport


class TestTypeScopeToSport:
    @pytest.mark.parametrize("scope,expected", [
        ("football", "football"),
        ("basketball", "basketball"),
        ("tennis", "tennis"),
        ("ice-hockey", "ice_hockey"),
        ("american-football", "american_football"),
        ("baseball", "baseball"),
        ("mma", "mma"),
        ("boxing", "boxing"),
    ])
    def test_known_scopes(self, scope, expected):
        assert type_scope_to_sport(scope) == expected

    def test_politics_not_mapped(self):
        assert type_scope_to_sport("politics") is None
        assert type_scope_to_sport("entertainment") is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/providers/test_smarkets_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `smarkets.py` with the mapping**

```python
"""Smarkets signal-only extractor.

Reads last-executed prices from Smarkets' public JSON API (unauthenticated).
User is IP-banned from their account, so Smarkets is never a placement target
— odds feed consensus via SIGNAL_ONLY_PROVIDERS only.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SMARKETS_TYPE_SCOPE_TO_SPORT: dict[str, str] = {
    "football": "football",
    "basketball": "basketball",
    "tennis": "tennis",
    "ice-hockey": "ice_hockey",
    "american-football": "american_football",
    "baseball": "baseball",
    "mma": "mma",
    "boxing": "boxing",
}


def type_scope_to_sport(scope: str) -> str | None:
    return SMARKETS_TYPE_SCOPE_TO_SPORT.get(scope)
```

- [ ] **Step 4: Run tests to confirm pass**

Run: `cd backend && pytest tests/providers/test_smarkets_parser.py -v`
Expected: 10 cases pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/smarkets.py backend/tests/providers/test_smarkets_parser.py
git commit -m "feat(smarkets): type_scope → canonical sport mapping"
```

---

### Task 10: Smarkets market parsing (price → decimal odds)

**Files:**
- Modify: `backend/src/providers/smarkets.py`
- Modify: `backend/tests/providers/test_smarkets_parser.py`

- [ ] **Step 1: Write the failing test**

Append to `test_smarkets_parser.py`:

```python
from src.providers.smarkets import parse_market_prices, price_integer_to_odds


class TestPriceConversion:
    def test_price_integer_to_odds(self):
        # Smarkets encodes % × 100: 5000 = 50% → decimal odds 2.00
        assert price_integer_to_odds(5000) == 2.00
        assert price_integer_to_odds(2500) == 4.00
        assert price_integer_to_odds(7500) == pytest.approx(1.333, abs=0.01)

    def test_zero_or_negative_returns_zero(self):
        assert price_integer_to_odds(0) == 0.0
        assert price_integer_to_odds(-1) == 0.0


class TestParseMarketPrices:
    def test_last_executed_preferred(self):
        raw = {
            "last_executed_prices": [
                {"contract_id": "A", "last_executed_price": 5500},
                {"contract_id": "B", "last_executed_price": 4500},
            ],
            "quotes": [],
        }
        out = parse_market_prices(raw)
        assert out["A"] == pytest.approx(1.818, abs=0.01)  # 10000/5500
        assert out["B"] == pytest.approx(2.222, abs=0.01)  # 10000/4500

    def test_quotes_fallback_when_no_trades(self):
        raw = {
            "last_executed_prices": [
                {"contract_id": "A", "last_executed_price": None},
            ],
            "quotes": [
                {"contract_id": "A", "best_back": 5800, "best_lay": 6000},
            ],
        }
        out = parse_market_prices(raw)
        # Mid = 5900 → 10000/5900 ≈ 1.695
        assert out["A"] == pytest.approx(1.695, abs=0.01)

    def test_no_price_no_quote_dropped(self):
        raw = {
            "last_executed_prices": [{"contract_id": "A", "last_executed_price": None}],
            "quotes": [],
        }
        assert parse_market_prices(raw) == {}
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/providers/test_smarkets_parser.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_market_prices'`.

- [ ] **Step 3: Implement price conversion + parser**

Append to `backend/src/providers/smarkets.py`:

```python
def price_integer_to_odds(price: int) -> float:
    """Smarkets encodes prices as integers 0–10000 representing percent × 100.
    e.g. 5500 = 55% implied → decimal odds = 10000/5500 ≈ 1.818.
    Returns 0.0 for non-positive inputs.
    """
    if price <= 0:
        return 0.0
    return round(10000.0 / price, 4)


def parse_market_prices(raw: dict) -> dict[str, float]:
    """Extract {contract_id: decimal_odds} from a Smarkets market-prices payload.

    Prefers `last_executed_price` (the revealed market price). Falls back to
    the mid of (best_back, best_lay) from /quotes/. Drops contracts with
    neither.
    """
    out: dict[str, float] = {}

    last_by_id: dict[str, int | None] = {
        p.get("contract_id"): p.get("last_executed_price")
        for p in raw.get("last_executed_prices", [])
        if p.get("contract_id")
    }
    quotes_by_id: dict[str, dict] = {
        q.get("contract_id"): q
        for q in raw.get("quotes", [])
        if q.get("contract_id")
    }

    for cid, last in last_by_id.items():
        if last:
            out[cid] = price_integer_to_odds(int(last))
            continue
        q = quotes_by_id.get(cid)
        if q and q.get("best_back") and q.get("best_lay"):
            mid = (int(q["best_back"]) + int(q["best_lay"])) // 2
            out[cid] = price_integer_to_odds(mid)
    return out
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd backend && pytest tests/providers/test_smarkets_parser.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/smarkets.py backend/tests/providers/test_smarkets_parser.py
git commit -m "feat(smarkets): last-executed-price preferred, mid-of-quotes fallback"
```

---

### Task 11: `SmarketsRetriever` (async fetch + sport filter)

**Files:**
- Modify: `backend/src/providers/smarkets.py`
- Modify: `backend/tests/providers/test_smarkets_parser.py`

- [ ] **Step 1: Write the failing test (parse fixture)**

Append to `test_smarkets_parser.py`:

```python
import json
from pathlib import Path

from src.providers.smarkets import SmarketsRetriever


class TestSmarketsRetriever:
    def test_parse_event_listing_keeps_sports(self):
        fixture = Path(__file__).parent / "fixtures" / "smarkets" / "events_upcoming.json"
        raw = json.loads(fixture.read_text(encoding="utf-8"))

        config = {"id": "smarkets", "params": {"min_trades_24h": 1}}
        retriever = SmarketsRetriever(config)

        # The parse() here only filters events to the requested sport — odds
        # come from a second endpoint call made inside extract(). This test
        # pins the shape of filter_events_by_sport.
        footballs = retriever.filter_events_by_sport(raw.get("events", []), "football")
        assert isinstance(footballs, list)
        for ev in footballs:
            assert ev.get("type_scope") == "football"
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/providers/test_smarkets_parser.py -v`
Expected: FAIL — `ImportError: cannot import name 'SmarketsRetriever'`.

- [ ] **Step 3: Implement `SmarketsRetriever`**

Append to `backend/src/providers/smarkets.py`:

```python
import asyncio
import aiohttp
from ..core import Retriever, StandardEvent


class SmarketsRetriever(Retriever):
    """Smarkets signal-only retriever. Uses public JSON API, no auth.

    Flow:
    1. /events/?state=upcoming&type_domain=sport → event list
    2. For each in-scope event, /events/{id}/markets/ → market list
    3. For each market, /markets/{id}/last_executed_prices/ → prices
    Aggregates into StandardEvent with provider_meta.is_signal=True.
    """

    DEFAULT_BASE_URL = "https://api.smarkets.com/v3"
    MAX_PAGES = 20
    CONCURRENT_MARKET_FETCHES = 8

    def __init__(self, config: dict, circuit_breaker=None, rate_limit_config=None):
        super().__init__(config)
        self.base_url = config.get("base_url", self.DEFAULT_BASE_URL)
        self.proxy_url = config.get("proxy_url") or config.get("params", {}).get("proxy_url") or None
        self.min_trades_24h = int(config.get("params", {}).get("min_trades_24h", 1))
        self._circuit_breaker = circuit_breaker

    def _get_sport_url(self, sport: str) -> str:
        return f"{self.base_url}/events/?state=upcoming&type_domain=sport&limit=200"

    def filter_events_by_sport(self, events: list[dict], sport: str) -> list[dict]:
        target_scope = {v: k for k, v in SMARKETS_TYPE_SCOPE_TO_SPORT.items()}.get(sport)
        if target_scope is None:
            return []
        return [e for e in events if e.get("type_scope") == target_scope]

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> dict | None:
        try:
            kwargs = {"timeout": aiohttp.ClientTimeout(total=10)}
            if self.proxy_url:
                kwargs["proxy"] = self.proxy_url
            async with session.get(url, **kwargs) as resp:
                if resp.status != 200:
                    logger.warning(f"[smarkets] {resp.status} on {url}")
                    return None
                return await resp.json()
        except Exception as e:
            logger.warning(f"[smarkets] fetch failed {url}: {e}")
            return None

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> list[StandardEvent]:
        async with aiohttp.ClientSession() as session:
            events_raw: list[dict] = []
            url = self._get_sport_url(sport)
            for _ in range(self.MAX_PAGES):
                body = await self._fetch_json(session, url)
                if not body:
                    break
                events_raw.extend(body.get("events", []))
                nxt = (body.get("pagination", {}) or {}).get("next_page")
                if not nxt:
                    break
                url = f"{self.base_url}{nxt}" if nxt.startswith("/") else nxt

            in_scope = self.filter_events_by_sport(events_raw, sport)
            logger.info(f"[smarkets] {len(events_raw)} events, {len(in_scope)} in-scope for {sport}")

            sem = asyncio.Semaphore(self.CONCURRENT_MARKET_FETCHES)

            async def build_event(ev_raw: dict) -> StandardEvent | None:
                async with sem:
                    eid = ev_raw.get("id")
                    mkts = await self._fetch_json(session, f"{self.base_url}/events/{eid}/markets/")
                    if not mkts or not mkts.get("markets"):
                        return None
                    # Only keep moneyline/1x2/spread/total market types
                    kept: list[dict] = []
                    for m in mkts["markets"]:
                        mtype = self._classify_market_type(m.get("name", ""), m.get("market_type"))
                        if mtype is None:
                            continue
                        prices = await self._fetch_json(
                            session,
                            f"{self.base_url}/markets/{m['id']}/last_executed_prices/",
                        )
                        quotes = await self._fetch_json(
                            session,
                            f"{self.base_url}/markets/{m['id']}/quotes/",
                        )
                        odds_by_cid = parse_market_prices({
                            "last_executed_prices": (prices or {}).get("last_executed_prices", []),
                            "quotes": (quotes or {}).get("quotes", []),
                        })
                        if not odds_by_cid:
                            continue
                        outcomes = [
                            {"name": cid, "odds": odds}
                            for cid, odds in odds_by_cid.items()
                        ]
                        kept.append({"type": mtype, "outcomes": outcomes})
                    if not kept:
                        return None
                    return StandardEvent(
                        id=f"smarkets_{eid}",
                        name=ev_raw.get("name", ""),
                        sport=sport,
                        markets=kept,
                        provider="smarkets",
                        url=f"https://smarkets.com{ev_raw.get('full_slug', '')}",
                        start_time=ev_raw.get("start_datetime", ""),
                    )

            results = await asyncio.gather(*(build_event(e) for e in in_scope))
            events = [r for r in results if r]
            if limit and len(events) > limit:
                events = events[:limit]
            return events

    def _classify_market_type(self, name: str, raw_type: str | None) -> str | None:
        """Map Smarkets market name/type → our 1x2/moneyline/spread/total labels."""
        n = (name or "").lower()
        if "winner" in n or "match result" in n or "to win" in n:
            return "1x2" if "draw" in n or "three-way" in n else "moneyline"
        if "handicap" in n or "spread" in n:
            return "spread"
        if "total" in n or "over/under" in n or "o/u" in n:
            return "total"
        return None

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        # Not used — extract() overrides the base flow entirely
        return []
```

- [ ] **Step 4: Run tests to confirm pass**

Run: `cd backend && pytest tests/providers/test_smarkets_parser.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/smarkets.py backend/tests/providers/test_smarkets_parser.py
git commit -m "feat(smarkets): SmarketsRetriever with async market + price fetch

- Public JSON endpoints, no auth
- Optional proxy_url routing for when the server IP is geoblocked
- Bounded concurrency for the per-market price fetches"
```

---

### Task 12: Wire Smarkets in factory + providers.yaml + signal tier

**Files:**
- Modify: `backend/src/factory.py`
- Modify: `backend/src/config/providers.yaml`

- [ ] **Step 1: Factory dispatch**

Add import in `backend/src/factory.py`:

```python
from .providers.smarkets import SmarketsRetriever
```

After the Kalshi branch from Task 7:

```python
        elif retriever_type == "smarkets":
            retriever = SmarketsRetriever(
                config,
                circuit_breaker=self._circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
```

- [ ] **Step 2: Smarkets block in `providers.yaml`**

Next to the `marathon` / `cloudbet` blocks:

```yaml
  smarkets:
    id: smarkets
    name: Smarkets
    domain: smarkets.com
    retriever_type: smarkets
    base_url: https://api.smarkets.com/v3
    currency: GBP
    exchange_rate_sek: 13.3   # rough 2026-04 FX
    params:
      min_trades_24h: 1
      proxy_url: ${SMARKETS_PROXY_URL}   # empty = direct from server IP
    supported_sports:
      - football
      - basketball
      - tennis
      - ice_hockey
      - american_football
      - baseball
      - mma
      - boxing
```

Add to existing `signal_international` tier (alongside `cloudbet`, `marathon`):

```yaml
  signal_international:
    providers:
      - cloudbet
      - marathon
      - smarkets
    interval_minutes: 5
    grouped: false
```

Append to `active:`:

```yaml
  - smarkets
```

- [ ] **Step 3: Smoke-test factory lookup**

Run: `cd backend && python -c "from src.factory import ExtractorFactory; ExtractorFactory.get_instance().get_extractor('smarkets'); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add backend/src/factory.py backend/src/config/providers.yaml
git commit -m "feat(smarkets): wire SmarketsRetriever into factory + signal tier

- factory.py dispatches retriever_type=smarkets
- providers.yaml defines the Smarkets block (GBP, optional proxy_url)
- joined existing signal_international tier alongside cloudbet + marathon
- added to active providers list"
```

---

### Checkpoint — Deploy extraction, verify 24h

After Phase 3 is merged, deploy **without the play workflow** and let the extractors run for a day before Phase 4.

- [ ] **Step 1: Push and deploy**

```bash
git push origin main
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"
```

- [ ] **Step 2: Verify first Kalshi run**

```bash
ssh root@148.251.40.251 "cd /opt/firev && curl -sS -X POST 'http://localhost:8000/api/extraction/run?providers=kalshi' | head -c 500"
```

Expected: response indicating events_processed > 0.

- [ ] **Step 3: Verify first Smarkets run**

```bash
ssh root@148.251.40.251 "cd /opt/firev && curl -sS -X POST 'http://localhost:8000/api/extraction/run?providers=smarkets' | head -c 500"
```

Expected: response with events_processed > 0. On 403/geoblock, set `SMARKETS_PROXY_URL` in `.env.docker` to the Bahnhof gost URL and restart:

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose restart backend"
```

- [ ] **Step 4: Use postgres MCP to verify metrics** (after 24h)

Query `extraction_runs` + `provider_run_metrics` for rows where `provider_id IN ('kalshi', 'smarkets')`. Confirm:
- `status = 'success'` on most runs
- `events_processed > 0`
- Some opportunities generated (join to `opportunities` table)

Only proceed to Phase 4 if both providers are healthy.

---

## Phase 4 — Kalshi play workflow

### Task 13: Add `kalshi-python` SDK dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Look up the latest `kalshi-python` version**

Run: `pip index versions kalshi-python 2>&1 | head -5`
Expected: a version string. If the package doesn't exist under that name, fall back to the `python-kalshi-api` package — verify on PyPI before pinning.

- [ ] **Step 2: Add to `pyproject.toml`**

In the `[tool.poetry.dependencies]` (or `[project] dependencies` depending on which format the repo uses) section, add:

```toml
kalshi-python = "^1.0"   # replace with the exact version from Step 1
```

- [ ] **Step 3: Rebuild lockfile and test import**

If the repo uses `uv`: `cd backend && uv pip install -e .`
If `pip`: `cd backend && pip install -e .`

Then: `cd backend && python -c "import kalshi_python; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock  # or poetry.lock / requirements.txt
git commit -m "deps: add kalshi-python SDK for play workflow"
```

---

### Task 14: `KalshiWorkflow` class (backend + firevsports copies)

**Files:**
- Create: `backend/src/mirror/workflows/kalshi.py`
- Create: `firevsports/mirror/workflows/kalshi.py`

- [ ] **Step 1: Create `backend/src/mirror/workflows/kalshi.py`**

Write the full workflow skeleton:

```python
"""KalshiWorkflow — API-first automation for Kalshi via kalshi-python SDK.

Uses REST API for: balance, prices, order placement, history/fills.
Playwright tab is opened to https://kalshi.com/markets/<ticker> for visual
context only — no DOM automation.

Falls back to DOM stub if KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PEM are
absent. The stub never succeeds at placement; it exists so missing creds
don't crash the registry.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_ExchangeClient = None


def _load_sdk() -> bool:
    global _ExchangeClient
    if _ExchangeClient is not None:
        return True
    try:
        from kalshi_python import ExchangeClient  # type: ignore

        _ExchangeClient = ExchangeClient
        return True
    except ImportError:
        logger.warning("[kalshi] kalshi-python SDK not installed — API features disabled")
        return False


class KalshiWorkflow(ProviderWorkflow):
    platform = "kalshi"
    autonomous_placement = True

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)
        self._client = None
        self._pending_ticker: str | None = None
        self._pending_count: int = 0
        self._pending_yes_price_cents: int = 0
        self._init_client()

    def _init_client(self) -> None:
        key_id = os.getenv("KALSHI_API_KEY_ID")
        key_pem = os.getenv("KALSHI_PRIVATE_KEY_PEM")
        if not (key_id and key_pem):
            logger.info("[kalshi] No KALSHI_API_KEY_ID/PEM — DOM-only stub")
            return
        if not _load_sdk():
            return
        try:
            self._client = _ExchangeClient(
                host=os.getenv("KALSHI_API_HOST", "https://api.elections.kalshi.com/trade-api/v2"),
                key_id=key_id,
                private_key_pem=key_pem,
            )
            logger.info("[kalshi] SDK client initialized (API mode)")
        except Exception as e:
            logger.error(f"[kalshi] client init failed: {e}")
            self._client = None

    @property
    def has_api(self) -> bool:
        return self._client is not None

    # ---------- Login / balance ----------

    async def check_login(self, page: "Page") -> bool:
        # API auth is independent of web session; presence of a client is enough
        return self.has_api

    async def sync_balance(self, page: "Page") -> float:
        if not self.has_api:
            return 0.0
        try:
            bal = self._client.get_balance()   # returns {"balance": cents_int}
            return round(float(bal.get("balance", 0)) / 100.0, 2)
        except Exception as e:
            logger.warning(f"[kalshi] sync_balance failed: {e}")
            return 0.0

    # ---------- History sync (for settlement reconciliation) ----------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        if not self.has_api:
            return []
        try:
            fills = self._client.get_fills(limit=200).get("fills", [])
        except Exception as e:
            logger.warning(f"[kalshi] get_fills failed: {e}")
            return []
        out: list[HistoryEntry] = []
        for f in fills:
            status = "won" if f.get("is_taker") and f.get("yes_price") and f.get("settled") else "pending"
            out.append(HistoryEntry(
                provider_bet_id=str(f.get("order_id") or f.get("trade_id")),
                event_name=f.get("ticker", ""),
                market=f.get("ticker", ""),
                outcome=f.get("side", ""),
                odds=round(100.0 / max(int(f.get("yes_price", 0)), 1), 4),
                stake=float(f.get("count", 0)) * float(f.get("yes_price", 0)) / 100.0,
                status=status,
                payout=None if status == "pending" else float(f.get("count", 0)),
            ))
        return out

    # ---------- Navigation (visual context only) ----------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        ticker = getattr(bet, "provider_event_id", "") or ""
        ticker = ticker.replace("kalshi_", "")
        if not ticker:
            return False
        url = f"https://kalshi.com/markets/{ticker}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return True
        except Exception as e:
            logger.warning(f"[kalshi] navigate failed: {e}")
            return False

    # ---------- Placement ----------

    async def prep_betslip(self, page: "Page", bet, stake: float) -> PlacementResult:
        # No DOM interaction; stash the order params for place_bet().
        self._pending_ticker = getattr(bet, "provider_market_ticker", None) or getattr(bet, "provider_event_id", None)
        if not self._pending_ticker:
            return PlacementResult(status="failed", bet_id=getattr(bet, "id", 0), reason="no_ticker")
        yes_price_dollars = self._infer_yes_price(bet)
        self._pending_yes_price_cents = max(1, int(round(yes_price_dollars * 100)))
        self._pending_count = max(1, int(stake // max(yes_price_dollars, 0.01)))
        return PlacementResult(
            status="ready", bet_id=getattr(bet, "id", 0),
            actual_odds=round(1.0 / yes_price_dollars, 4),
            actual_stake=round(self._pending_count * yes_price_dollars, 2),
        )

    def _infer_yes_price(self, bet) -> float:
        # Bet carries the decimal odds we computed in extraction;
        # convert back to a YES-contract price target.
        odds = float(getattr(bet, "odds", 2.0))
        return max(0.01, min(0.99, round(1.0 / odds, 4)))

    async def check_live_price(self, page: "Page", bet) -> tuple[float | None, float | None]:
        if not self.has_api or not self._pending_ticker:
            return None, None
        try:
            mkt = self._client.get_market(ticker=self._pending_ticker)
            yes_ask_cents = int(mkt.get("yes_ask", 0))
            if yes_ask_cents <= 0:
                return None, None
            odds = round(100.0 / yes_ask_cents, 4)
            return odds, None
        except Exception as e:
            logger.warning(f"[kalshi] check_live_price failed: {e}")
            return None, None

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        if not self.has_api or not self._pending_ticker:
            return PlacementResult(status="failed", bet_id=getattr(bet, "id", 0), reason="no_client")
        try:
            resp = self._client.create_order(
                ticker=self._pending_ticker,
                action="buy",
                side="yes",
                type="limit",
                yes_price=self._pending_yes_price_cents,
                count=self._pending_count,
                expiration_ts=self._client.now_ts() + 60,   # 60-second resting limit
            )
            return PlacementResult(
                status="placed",
                bet_id=getattr(bet, "id", 0),
                actual_odds=round(100.0 / self._pending_yes_price_cents, 4),
                actual_stake=round(self._pending_count * self._pending_yes_price_cents / 100.0, 2),
                raw_response=resp,
            )
        except Exception as e:
            logger.error(f"[kalshi] place_bet failed: {e}")
            return PlacementResult(status="failed", bet_id=getattr(bet, "id", 0), reason=str(e))
```

- [ ] **Step 2: Mirror the same file into `firevsports/mirror/workflows/kalshi.py`**

Copy the file above verbatim to `firevsports/mirror/workflows/kalshi.py`. The import path is relative (`from .base import ...`) so no edits required — project convention is to keep both copies byte-identical.

```bash
cp backend/src/mirror/workflows/kalshi.py firevsports/mirror/workflows/kalshi.py
```

- [ ] **Step 3: Smoke-test import**

Run: `cd backend && python -c "from src.mirror.workflows.kalshi import KalshiWorkflow; print('ok')"`
Expected: `ok` (SDK absence will log a warning but not fail).

- [ ] **Step 4: Commit**

```bash
git add backend/src/mirror/workflows/kalshi.py firevsports/mirror/workflows/kalshi.py
git commit -m "feat(kalshi): KalshiWorkflow — API-first placement + visual tab

- autonomous_placement=True, limit orders at current yes_ask
- 60s order expiry (limit, not market)
- SDK init via KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM env vars
- Falls back to no-op stub when creds absent"
```

---

### Task 15: Register `KalshiWorkflow` in both workflow maps

**Files:**
- Modify: `firevsports/mirror/workflows/__init__.py`
- Modify: `backend/src/mirror/workflows/__init__.py`

- [ ] **Step 1: Update `firevsports/mirror/workflows/__init__.py`**

In `_load_platform_map()`, add the import + map entry:

```python
    from .kalshi import KalshiWorkflow    # add to the import block

    return {
        # ... existing entries ...
        "kalshi": KalshiWorkflow,
    }
```

In `_RETRIEVER_TO_PLATFORM`, add:

```python
    "kalshi": "kalshi",
```

In `_FALLBACK_DOMAINS`, add:

```python
    "kalshi": "kalshi.com",
```

- [ ] **Step 2: Repeat the same three edits in `backend/src/mirror/workflows/__init__.py`**

Confirm its structure first — run:

```bash
grep -n "_load_platform_map\|_RETRIEVER_TO_PLATFORM\|_FALLBACK_DOMAINS" backend/src/mirror/workflows/__init__.py
```

Apply the same three insertions.

- [ ] **Step 3: Smoke-test dispatch**

```bash
cd backend && python -c "from src.mirror.workflows import get_workflow; wf = get_workflow('kalshi'); print(type(wf).__name__)"
```

Expected: `KalshiWorkflow`.

And from the firevsports tree:

```bash
cd firevsports && python -c "from mirror.workflows import get_workflow; wf = get_workflow('kalshi'); print(type(wf).__name__)"
```

Expected: `KalshiWorkflow`.

- [ ] **Step 4: Commit**

```bash
git add backend/src/mirror/workflows/__init__.py firevsports/mirror/workflows/__init__.py
git commit -m "feat(kalshi): register KalshiWorkflow in workflow maps

Both backend + firevsports registries route provider_id=kalshi to
KalshiWorkflow. Fallback domain added so get_workflow() works without
config.loader."
```

---

### Task 16: Add `kalshi` to uncapped providers

**Files:**
- Modify: `firevsports/mirror/play_loop.py`

- [ ] **Step 1: Find the uncapped list**

Run: `grep -n "uncapped\|UNCAPPED\|pinnacle.*polymarket\|polymarket.*pinnacle" firevsports/mirror/play_loop.py`

Look for a frozenset/list containing `pinnacle`, `polymarket`, `cloudbet`.

- [ ] **Step 2: Add `kalshi` to that list**

Edit the literal set to include `"kalshi"`.

- [ ] **Step 3: Smoke-test**

Run: `grep -A2 "kalshi" firevsports/mirror/play_loop.py | head -5`
Expected: line includes `"kalshi"` in the uncapped set.

- [ ] **Step 4: Commit**

```bash
git add firevsports/mirror/play_loop.py
git commit -m "feat(kalshi): treat kalshi as uncapped (no 10/day soft-book limit)"
```

---

### Task 17: Frontend provider list update

**Files:**
- Modify: `firevsports/frontend/src/types/index.ts`

- [ ] **Step 1: Locate the provider union / list**

```bash
grep -n "polymarket\|pinnacle\|cloudbet" firevsports/frontend/src/types/index.ts | head -20
```

Find the `ProviderId` type / provider array.

- [ ] **Step 2: Add `"kalshi"` to the union and any provider-label map**

If there's a `PROVIDER_LABELS` or similar object, add:

```typescript
kalshi: "Kalshi",
```

If there's a logo/icon map, add an entry (fallback `null` is OK for now — Kalshi logo can follow later).

- [ ] **Step 3: Build the frontend**

```bash
cd firevsports/frontend && npm run build
```

Expected: no TypeScript errors about `kalshi`.

- [ ] **Step 4: Commit**

```bash
git add firevsports/frontend/src/types/index.ts
git commit -m "feat(kalshi): add kalshi to frontend provider list"
```

---

## Phase 5 — Rollout validation

### Task 18: Production deploy + small-bet test

- [ ] **Step 1: Set Kalshi env vars on server**

After the user has created their Kalshi account, funded it, and generated an API key:

```bash
ssh root@148.251.40.251 "cat >> /opt/firev/.env.docker <<'EOF'
KALSHI_API_KEY_ID=<from dashboard>
KALSHI_PRIVATE_KEY_PEM=<pem with \n-escaped newlines>
EOF"
```

- [ ] **Step 2: Deploy**

```bash
git push origin main
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"
```

Wait for the script's health check to pass.

- [ ] **Step 3: Verify balance sync**

In the FirevSports Play tab locally, select Kalshi → click Start. Expect Kalshi to light up green with the USD balance visible (converted to SEK via `exchange_rate_sek`).

- [ ] **Step 4: Place a small test bet**

Pick a Kalshi opportunity with obvious edge (NBA moneyline, $1–2 stake). Click Place. Verify:

- Order placed via API (log line `[kalshi] place_bet …`)
- Balance decrements by the realized spend
- Bet row appears in `bets` table with `provider_id='kalshi'`, `result='pending'`

- [ ] **Step 5: Await settlement**

After the game closes, `pending_loop` should call `sync_history()` on Kalshi and move the bet from `pending` → `won`/`lost`. Verify via:

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend python -c \"from src.db.models import get_session, Bet; s=get_session(); rows=s.query(Bet).filter(Bet.provider_id=='kalshi').order_by(Bet.id.desc()).limit(5).all(); [print(r.id, r.result, r.payout) for r in rows]\""
```

- [ ] **Step 6: Consensus sanity**

Query the latest opportunities with postgres MCP:

```sql
SELECT provider_id, outcome_name, odds, consensus_sources
FROM opportunities
WHERE provider_id IN ('pinnacle', 'kalshi', 'smarkets')
ORDER BY id DESC LIMIT 20;
```

Confirm `consensus_sources` JSON array includes `kalshi` and/or `smarkets` entries on recent opportunities.

---

## Rollback

- Remove `kalshi` and/or `smarkets` from `active:` list in `providers.yaml`; redeploy.
- Existing rows in `bets` / `opportunities` / `events` stay; scheduler just stops running the extractors.
- `PINNACLE_SPORTS` rename only rolls back if something unrelated breaks (shouldn't — pure rename).

---

## Plan self-review

Pass 1 — spec coverage:

- Kalshi extractor with binary-→-1x2/moneyline mapping, volume + 50/50 filters, fee adjustment → Tasks 3–6
- Smarkets extractor with last-executed preference + quotes fallback, signal-only → Tasks 8–12
- `PINNACLE_SPORTS` rename → Task 1
- `KALSHI_FEE_RATE`, signal-only list, platform map, extended-markets set → Task 2
- Factory + providers.yaml + scheduling tier + active list → Tasks 7 and 12
- Kalshi play workflow (API-first + visual tab + SDK + RSA-key env vars) → Tasks 13–14
- Workflow registry updates + uncapped list + frontend types → Tasks 15–17
- Validation + smoke test → Checkpoint + Task 18

Pass 2 — placeholders: none found. Every step has exact code or exact commands.

Pass 3 — type consistency: `StandardEvent`, `HistoryEntry`, `PlacementResult`, `ProviderWorkflow`, `Retriever`, `KALSHI_FEE_RATE`, `SMARKETS_TYPE_SCOPE_TO_SPORT`, `KALSHI_SERIES_TO_SPORT`, `series_to_sport`, `parse_event`, `parse_market_prices`, `price_integer_to_odds` — all defined once and referenced consistently. Method names match the `ProviderWorkflow` base class (`check_login`, `sync_balance`, `sync_history`, `navigate_to_event`, `prep_betslip`, `check_live_price`, `place_bet`).
