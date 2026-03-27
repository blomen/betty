# Bet Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Intercept bet placements on Spelklubben (Gecko V2) via Playwright response listener and auto-log them to Firev.

**Architecture:** A headed Playwright browser with persistent context runs inside the FastAPI process. A response listener on the browser context intercepts bet placement API calls, parses the confirmation, stores raw traces, creates bets via existing BetService, and pushes SSE notifications to the frontend toast overlay.

**Tech Stack:** Python 3.10+ / Playwright (patchright) / FastAPI / SQLAlchemy / SQLite / React 19 / TypeScript / Tailwind

**Spec:** `docs/superpowers/specs/2026-03-19-bet-mirror-design.md`

---

## File Structure

```
backend/src/mirror/
├── __init__.py              # Exports MirrorService
├── interceptor.py           # BetInterceptor — browser lifecycle + response listener
├── service.py               # MirrorService — orchestrates interceptor + bet creation + broadcast
└── parsers/
    ├── __init__.py           # Parser registry
    └── gecko.py              # Gecko V2 bet response parser

backend/src/api/routes/mirror.py   # API endpoints: start/stop/status
backend/src/db/models.py           # Modify: add BetTrace model
backend/src/api/routes/__init__.py  # Modify: register mirror_router
backend/src/api/__init__.py         # Modify: include mirror_router
backend/src/app.py                  # Modify: add mirror CLI command

frontend/src/components/Terminal/BetMirrorToast.tsx   # Toast overlay component
frontend/src/hooks/useBetMirror.ts                    # SSE listener for bet_mirrored events
frontend/src/components/Terminal/TerminalWindow.tsx    # Modify: mount BetMirrorToast
```

---

### Task 1: BetTrace Database Model

**Files:**
- Modify: `backend/src/db/models.py`
- Test: `backend/tests/test_bet_trace_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_bet_trace_model.py
"""Tests for BetTrace model."""
import pytest
from datetime import datetime, timezone
from src.db.models import init_db, get_session, BetTrace


@pytest.fixture
def db():
    init_db(":memory:")
    session = get_session()
    yield session
    session.close()


def test_create_bet_trace(db):
    trace = BetTrace(
        timestamp=datetime.now(timezone.utc),
        provider_id="spelklubben",
        request_url="https://www.spelklubben.se/api/sb/v1/betslip/place",
        request_body='{"stake": 100}',
        response_body='{"betId": "abc123"}',
        provider_bet_id="abc123",
        parse_status="ok",
    )
    db.add(trace)
    db.commit()

    result = db.query(BetTrace).first()
    assert result.provider_id == "spelklubben"
    assert result.provider_bet_id == "abc123"
    assert result.parse_status == "ok"
    assert result.bet_id is None  # nullable FK


def test_bet_trace_rejected_status(db):
    trace = BetTrace(
        timestamp=datetime.now(timezone.utc),
        provider_id="spelklubben",
        request_url="https://example.com/api/sb/v1/betslip/place",
        request_body="{}",
        response_body='{"error": "odds changed"}',
        parse_status="rejected",
    )
    db.add(trace)
    db.commit()

    result = db.query(BetTrace).first()
    assert result.parse_status == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_bet_trace_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'BetTrace'`

- [ ] **Step 3: Add BetTrace model to models.py**

Add after the `Bet` class in `backend/src/db/models.py`:

```python
class BetTrace(Base):
    """Raw API trace from intercepted bet placement. Append-only."""
    __tablename__ = "bet_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    provider_id = Column(String, nullable=False)
    request_url = Column(String, nullable=False)
    request_body = Column(String, nullable=True)   # JSON string
    response_body = Column(String, nullable=True)   # JSON string
    bet_id = Column(Integer, ForeignKey("bets.id"), nullable=True)
    provider_bet_id = Column(String, nullable=True, index=True)
    parse_status = Column(String, nullable=False)  # "ok", "failed", "unmatched", "rejected"

    bet = relationship("Bet", backref="traces")
```

Also add `BetTrace` to the `init_db()` function's `Base.metadata.create_all()` call (it uses `Base.metadata` so it's automatic).

Also update the `Bet.bet_type` column comment to include `"mirror"`:
```python
bet_type = Column(String, nullable=True)    # "value", "dutch", "reverse", "polymarket", "boost", "mirror"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_bet_trace_model.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_bet_trace_model.py
git commit -m "feat(mirror): add BetTrace model for raw bet interception storage"
```

---

### Task 2: Gecko Bet Parser

**Files:**
- Create: `backend/src/mirror/__init__.py`
- Create: `backend/src/mirror/parsers/__init__.py`
- Create: `backend/src/mirror/parsers/gecko.py`
- Test: `backend/tests/test_gecko_bet_parser.py`

- [ ] **Step 1: Create mirror package structure**

```python
# backend/src/mirror/__init__.py
"""Bet mirror — intercept and log bets placed on bookmaker sites."""

# backend/src/mirror/parsers/__init__.py
"""Provider-specific bet response parsers."""
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_gecko_bet_parser.py
"""Tests for Gecko V2 bet response parser."""
import pytest
from src.mirror.parsers.gecko import GeckoBetParser


class TestGeckoBetParser:
    def setup_method(self):
        self.parser = GeckoBetParser()

    def test_is_bet_placement_url_positive(self):
        assert self.parser.is_bet_placement_url(
            "https://sb2frontend-altenar2.bfrp.io/api/sb/v1/betslip/place"
        )
        assert self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/betslip/coupon"
        )

    def test_is_bet_placement_url_negative(self):
        assert not self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/widgets/events-table/v2"
        )
        assert not self.parser.is_bet_placement_url(
            "https://example.com/api/sb/v1/odds/123"
        )

    def test_parse_confirmed_bet(self):
        # Placeholder response structure — will be updated after discovery phase
        response_body = {
            "data": {
                "betId": "bet_abc123",
                "status": "Confirmed",
                "stakes": [{"amount": 100.0}],
                "selections": [
                    {
                        "eventName": "Virginia United vs North Lakes United",
                        "marketTemplateName": "Match Winner",
                        "selectionName": "Virginia United",
                        "odds": 2.10,
                        "eventId": "evt_456",
                        "participants": [
                            {"label": "Virginia United", "side": 1},
                            {"label": "North Lakes United", "side": 2},
                        ],
                    }
                ],
            }
        }
        result = self.parser.parse(response_body)
        assert result is not None
        assert result["confirmation_id"] == "bet_abc123"
        assert result["odds"] == 2.10
        assert result["stake"] == 100.0
        assert result["home_team"] is not None
        assert result["away_team"] is not None

    def test_parse_rejected_bet(self):
        response_body = {
            "data": {
                "status": "Rejected",
                "rejectionReason": "Odds changed",
            }
        }
        result = self.parser.parse(response_body)
        assert result is None

    def test_is_rejection(self):
        rejected = {"data": {"status": "Rejected"}}
        confirmed = {"data": {"status": "Confirmed", "betId": "123"}}
        assert self.parser.is_rejection(rejected) is True
        assert self.parser.is_rejection(confirmed) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_gecko_bet_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement GeckoBetParser**

```python
# backend/src/mirror/parsers/gecko.py
"""Gecko V2 (OBG) bet response parser.

Parses bet placement API responses from Betsson Group sites
(Betsson, Betsafe, NordicBet, Spelklubben).

NOTE: The exact response schema will be confirmed during the discovery phase.
Field paths in parse() are best-guess based on the Gecko events-table API
structure and may need adjustment after a real bet placement is captured.
"""

import logging
from typing import Any

from ...matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

# URL path segments that indicate bet placement (not odds browsing)
_BET_URL_KEYWORDS = ("betslip", "bet/place", "coupon", "wager")


class GeckoBetParser:
    """Parse Gecko V2 bet placement API responses."""

    def is_bet_placement_url(self, url: str) -> bool:
        """Check if URL is a bet placement endpoint (not odds/events)."""
        lower = url.lower()
        return "/api/sb/" in lower and any(kw in lower for kw in _BET_URL_KEYWORDS)

    def is_rejection(self, body: dict) -> bool:
        """Check if response indicates a rejected bet."""
        data = body.get("data", {})
        status = data.get("status", "").lower()
        return status in ("rejected", "failed", "error", "declined")

    def parse(self, body: dict) -> dict[str, Any] | None:
        """Parse a confirmed bet response into structured fields.

        Returns dict with bet fields, or None if rejected/unparseable.
        """
        data = body.get("data", {})

        # Check for rejection
        if self.is_rejection(body):
            return None

        bet_id = data.get("betId")
        if not bet_id:
            logger.warning("No betId in response — cannot parse")
            return None

        # Extract stake
        stakes = data.get("stakes", [])
        stake = stakes[0].get("amount", 0.0) if stakes else 0.0

        # Extract selection details (first selection for singles)
        selections = data.get("selections", [])
        if not selections:
            logger.warning(f"No selections in bet {bet_id}")
            return None

        sel = selections[0]
        odds = sel.get("odds", 0.0)
        event_name = sel.get("eventName", "")
        gecko_event_id = sel.get("eventId", "")

        # Parse participants
        participants = sel.get("participants", [])
        home_team = None
        away_team = None
        if len(participants) >= 2:
            sorted_p = sorted(participants, key=lambda p: p.get("side", 0))
            home_team = normalize_team_name(sorted_p[0].get("label", ""))
            away_team = normalize_team_name(sorted_p[1].get("label", ""))
        elif event_name and " vs " in event_name:
            parts = event_name.split(" vs ", 1)
            home_team = normalize_team_name(parts[0])
            away_team = normalize_team_name(parts[1])

        # Map market type
        market_template = sel.get("marketTemplateName", "").lower()
        market = self._map_market(market_template)

        # Map outcome
        outcome = self._map_outcome(sel.get("selectionName", ""), home_team, away_team)

        # Extract point for spread/total
        point = sel.get("lineValue") or sel.get("handicap")
        if point is not None:
            point = float(point)

        return {
            "confirmation_id": str(bet_id),
            "odds": float(odds),
            "stake": float(stake),
            "market": market,
            "outcome": outcome,
            "point": point,
            "home_team": home_team,
            "away_team": away_team,
            "event_name": event_name,
            "gecko_event_id": str(gecko_event_id),
        }

    def _map_market(self, template_name: str) -> str | None:
        """Map Gecko market template name to standard market type."""
        t = template_name.lower()
        if any(kw in t for kw in ("winner", "1x2", "match result")):
            return "1x2" if "draw" not in t else "1x2"
        if any(kw in t for kw in ("moneyline", "2-way")):
            return "moneyline"
        if any(kw in t for kw in ("total", "over/under", "over under")):
            return "total"
        if any(kw in t for kw in ("handicap", "spread", "hcp")):
            return "spread"
        return None

    def _map_outcome(
        self, selection_name: str, home_team: str | None, away_team: str | None
    ) -> str | None:
        """Map selection name to standard outcome."""
        lower = selection_name.lower()
        if lower in ("draw", "x", "tie"):
            return "draw"
        if lower in ("over",):
            return "over"
        if lower in ("under",):
            return "under"
        # Match against team names
        if home_team and normalize_team_name(selection_name) == home_team:
            return "home"
        if away_team and normalize_team_name(selection_name) == away_team:
            return "away"
        # Fallback: "1" = home, "2" = away
        if lower == "1":
            return "home"
        if lower == "2":
            return "away"
        return selection_name
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_gecko_bet_parser.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/src/mirror/ backend/tests/test_gecko_bet_parser.py
git commit -m "feat(mirror): add Gecko V2 bet response parser"
```

---

### Task 3: BetInterceptor — Browser Lifecycle & Response Listener

**Files:**
- Create: `backend/src/mirror/interceptor.py`
- Test: `backend/tests/test_bet_interceptor.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_bet_interceptor.py
"""Tests for BetInterceptor."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.mirror.interceptor import BetInterceptor


def test_interceptor_initial_state():
    interceptor = BetInterceptor(provider_id="spelklubben")
    assert interceptor.provider_id == "spelklubben"
    assert interceptor.status == "stopped"
    assert interceptor.browser is None


def test_interceptor_user_data_dir():
    interceptor = BetInterceptor(provider_id="spelklubben")
    assert "mirror_profiles" in str(interceptor.user_data_dir)
    assert "spelklubben" in str(interceptor.user_data_dir)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_bet_interceptor.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement BetInterceptor**

```python
# backend/src/mirror/interceptor.py
"""BetInterceptor — headed Playwright browser for bet interception.

Launches a visible browser with persistent context. The user browses
and bets normally. A response listener intercepts bet placement API
calls and forwards them to a callback for processing.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class BetInterceptor:
    """Manages a headed Playwright browser that intercepts bet placements."""

    def __init__(
        self,
        provider_id: str,
        on_bet_response: Callable[[str, str | None, str], Awaitable[None]] | None = None,
        discovery: bool = False,
    ):
        """
        Args:
            provider_id: e.g. "spelklubben"
            on_bet_response: async callback(response_url, request_body, response_body)
                             called when a bet placement response is intercepted
            discovery: when True, log ALL /api/sb/ POST requests (for endpoint identification)
        """
        self.provider_id = provider_id
        self.on_bet_response = on_bet_response
        self.discovery = discovery
        self.status = "stopped"
        self.browser = None
        self.context = None
        self._playwright = None
        self._started_at = None

        # Persistent context dir — separate from extraction browsers
        from ..paths import get_app_data_dir
        self.user_data_dir = get_app_data_dir() / "data" / "mirror_profiles" / provider_id

    async def start(self, site_url: str = "https://www.spelklubben.se/sv/odds"):
        """Launch headed browser and register response listener."""
        if self.status == "listening":
            logger.warning(f"[mirror:{self.provider_id}] Already running")
            return

        from patchright.async_api import async_playwright
        from datetime import datetime, timezone

        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self.context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=False,
            viewport={"width": 1440, "height": 900},
            locale="sv-SE",
            timezone_id="Europe/Stockholm",
            args=["--disable-blink-features=AutomationControlled"],
        )

        # Attach listener to all current and future pages
        for page in self.context.pages:
            page.on("response", self._on_response)
        self.context.on("page", lambda page: page.on("response", self._on_response))

        # Navigate first page to the site
        page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await page.goto(site_url, wait_until="load", timeout=60000)

        self.status = "listening"
        self._started_at = datetime.now(timezone.utc)
        logger.info(f"[mirror:{self.provider_id}] Started — listening for bet placements")

    async def _on_response(self, response):
        """Response listener — filters for bet placement endpoints."""
        try:
            url = response.url
            # Only POST requests to the Gecko bet API
            if response.request.method != "POST":
                return
            if "/api/sb/" not in url.lower():
                return

            # Check if this looks like a bet placement URL
            from .parsers.gecko import GeckoBetParser
            parser = GeckoBetParser()
            if not parser.is_bet_placement_url(url):
                return

            # Read response body
            try:
                body_text = await response.text()
            except Exception as e:
                logger.debug(f"[mirror:{self.provider_id}] Could not read response body: {e}")
                return

            # Read request body (POST data)
            request_body = None
            try:
                request_body = response.request.post_data
            except Exception:
                pass

            logger.info(f"[mirror:{self.provider_id}] Intercepted bet placement: {url}")

            if self.on_bet_response:
                await self.on_bet_response(url, request_body, body_text)

        except Exception as e:
            logger.error(f"[mirror:{self.provider_id}] Error in response listener: {e}", exc_info=True)

    async def stop(self):
        """Detach listener and close browser."""
        if self.status != "listening":
            return

        self.status = "stopped"
        self._started_at = None

        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        logger.info(f"[mirror:{self.provider_id}] Stopped")

    def get_status(self) -> dict[str, Any]:
        """Return current status info."""
        return {
            "running": self.status == "listening",
            "provider": self.provider_id,
            "status": self.status,
            "since": self._started_at.isoformat() if self._started_at else None,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_bet_interceptor.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/interceptor.py backend/tests/test_bet_interceptor.py
git commit -m "feat(mirror): add BetInterceptor with Playwright response listener"
```

---

### Task 4: MirrorService — Orchestration Layer

**Files:**
- Create: `backend/src/mirror/service.py`
- Modify: `backend/src/mirror/__init__.py`
- Test: `backend/tests/test_mirror_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_mirror_service.py
"""Tests for MirrorService."""
import pytest
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from src.db.models import init_db, get_session, BetTrace
from src.mirror.service import MirrorService


@pytest.fixture
def db():
    init_db(":memory:")
    session = get_session()
    yield session
    session.close()


def test_store_trace_ok(db):
    service = MirrorService(provider_id="spelklubben", broadcaster=None)

    trace = service._store_trace(
        db=db,
        url="https://example.com/api/sb/v1/betslip/place",
        request_body='{"stake": 100}',
        response_body='{"data": {"betId": "abc123", "status": "Confirmed"}}',
        parse_status="ok",
        provider_bet_id="abc123",
        bet_id=42,
    )
    db.commit()

    assert trace.id is not None
    assert trace.provider_bet_id == "abc123"
    assert trace.bet_id == 42
    assert trace.parse_status == "ok"


def test_store_trace_rejected(db):
    service = MirrorService(provider_id="spelklubben", broadcaster=None)

    trace = service._store_trace(
        db=db,
        url="https://example.com/api/sb/v1/betslip/place",
        request_body="{}",
        response_body='{"data": {"status": "Rejected"}}',
        parse_status="rejected",
    )
    db.commit()

    assert trace.parse_status == "rejected"
    assert trace.bet_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_mirror_service.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement MirrorService**

```python
# backend/src/mirror/service.py
"""MirrorService — orchestrates bet interception, parsing, storage, and notification."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..db.models import get_session, BetTrace, Bet
from ..services.bet_service import BetService
from ..matching.normalizer import normalize_team_name
from .interceptor import BetInterceptor
from .parsers.gecko import GeckoBetParser

logger = logging.getLogger(__name__)


class MirrorService:
    """Coordinates BetInterceptor + parsing + BetService + Broadcaster."""

    def __init__(self, provider_id: str, broadcaster=None, discovery: bool = False):
        self.provider_id = provider_id
        self.broadcaster = broadcaster
        self.parser = GeckoBetParser()
        self.interceptor = BetInterceptor(
            provider_id=provider_id,
            on_bet_response=self._handle_bet_response,
            discovery=discovery,
        )

    async def start(self, site_url: str | None = None):
        """Start the mirror browser."""
        url = site_url or "https://www.spelklubben.se/sv/odds"
        await self.interceptor.start(site_url=url)

    async def stop(self):
        """Stop the mirror browser."""
        await self.interceptor.stop()

    def get_status(self) -> dict[str, Any]:
        """Get current mirror status."""
        return self.interceptor.get_status()

    async def _handle_bet_response(self, url: str, request_body: str | None, response_body: str):
        """Process an intercepted bet placement response."""
        try:
            body = json.loads(response_body)
        except json.JSONDecodeError:
            logger.warning(f"[mirror:{self.provider_id}] Invalid JSON response from {url}")
            await asyncio.to_thread(self._store_trace_sync, url, request_body, response_body, "failed")
            return

        # Check for rejection
        if self.parser.is_rejection(body):
            logger.info(f"[mirror:{self.provider_id}] Bet rejected")
            await asyncio.to_thread(self._store_trace_sync, url, request_body, response_body, "rejected")
            self._notify("bet_rejected", {"provider": self.provider_id, "reason": "Bet rejected by bookmaker"})
            return

        # Parse confirmed bet
        parsed = self.parser.parse(body)
        if parsed is None:
            logger.warning(f"[mirror:{self.provider_id}] Could not parse bet response")
            await asyncio.to_thread(self._store_trace_sync, url, request_body, response_body, "failed")
            return

        # Create bet and store trace in a sync thread
        result = await asyncio.to_thread(
            self._process_bet_sync, url, request_body, response_body, parsed
        )

        # Notify frontend
        self._notify("bet_mirrored", result)

    def _process_bet_sync(
        self, url: str, request_body: str | None, response_body: str, parsed: dict
    ) -> dict[str, Any]:
        """Synchronous: create bet + store trace (runs in thread via asyncio.to_thread)."""
        db = get_session()
        try:
            confirmation_id = parsed["confirmation_id"]

            # Dedup: check if this bet was already logged
            existing = db.query(Bet).filter(
                Bet.confirmation_id == confirmation_id
            ).first()
            if existing:
                logger.info(f"[mirror:{self.provider_id}] Bet {confirmation_id} already logged (dedup)")
                return {
                    "status": "duplicate",
                    "confirmation_id": confirmation_id,
                    "provider": self.provider_id,
                }

            # Match event in DB
            event_id = self._match_event(db, parsed)

            # Create bet via BetService
            bet_service = BetService(db)
            bet_result = bet_service.create_bet(
                event_id=event_id,
                provider_id=self.provider_id,
                market=parsed.get("market"),
                outcome=parsed.get("outcome"),
                odds=parsed["odds"],
                stake=parsed["stake"],
                point=parsed.get("point"),
                bet_type="mirror",
            )

            # Update confirmation_id on the created bet
            if "error" not in bet_result:
                bet_obj = db.query(Bet).get(bet_result["id"])
                if bet_obj:
                    bet_obj.confirmation_id = confirmation_id

            db.commit()

            bet_id = bet_result.get("id")
            parse_status = "ok" if event_id else "unmatched"
            if "error" in bet_result:
                parse_status = "failed"

            # Store trace
            self._store_trace(
                db=db,
                url=url,
                request_body=request_body,
                response_body=response_body,
                parse_status=parse_status,
                provider_bet_id=confirmation_id,
                bet_id=bet_id,
            )
            db.commit()

            event_display = parsed.get("event_name", "Unknown event")
            return {
                "status": "ok" if "error" not in bet_result else "error",
                "confirmation_id": confirmation_id,
                "provider": self.provider_id,
                "event": event_display,
                "market": parsed.get("market"),
                "outcome": parsed.get("outcome"),
                "odds": parsed["odds"],
                "stake": parsed["stake"],
                "matched": event_id is not None,
                "error": bet_result.get("error"),
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror:{self.provider_id}] Error processing bet: {e}", exc_info=True)
            return {"status": "error", "error": str(e), "provider": self.provider_id}
        finally:
            db.close()

    def _store_trace_sync(
        self, url: str, request_body: str | None, response_body: str, parse_status: str
    ):
        """Store trace in a new DB session (for rejected/failed bets)."""
        db = get_session()
        try:
            self._store_trace(db, url, request_body, response_body, parse_status)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror:{self.provider_id}] Error storing trace: {e}")
        finally:
            db.close()

    def _store_trace(
        self,
        db,
        url: str,
        request_body: str | None,
        response_body: str,
        parse_status: str,
        provider_bet_id: str | None = None,
        bet_id: int | None = None,
    ) -> BetTrace:
        """Insert a BetTrace record."""
        trace = BetTrace(
            timestamp=datetime.now(timezone.utc),
            provider_id=self.provider_id,
            request_url=url,
            request_body=request_body,
            response_body=response_body,
            bet_id=bet_id,
            provider_bet_id=provider_bet_id,
            parse_status=parse_status,
        )
        db.add(trace)
        return trace

    def _match_event(self, db, parsed: dict) -> str | None:
        """Try to match intercepted bet to an internal Event."""
        from ..db.models import Event
        from rapidfuzz import fuzz
        from datetime import timedelta

        home = parsed.get("home_team")
        away = parsed.get("away_team")
        if not home or not away:
            return None

        # Query events starting within next 7 days to keep candidate set small
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=7)
        events = db.query(Event).filter(
            Event.home_team.isnot(None),
            Event.away_team.isnot(None),
            Event.start_time >= now - timedelta(hours=3),  # include recently started
            Event.start_time <= cutoff,
        ).all()

        best_match = None
        best_score = 0.0

        for event in events:
            home_score = fuzz.ratio(home, event.home_team or "")
            away_score = fuzz.ratio(away, event.away_team or "")
            combined = (home_score + away_score) / 2

            if combined > best_score:
                best_score = combined
                best_match = event

        if best_match and best_score >= 75:
            logger.info(
                f"[mirror:{self.provider_id}] Matched to event {best_match.id} "
                f"(score={best_score:.0f})"
            )
            return best_match.id

        logger.warning(f"[mirror:{self.provider_id}] No match for {home} vs {away} (best={best_score:.0f})")
        return None

    def _notify(self, event_type: str, data: dict):
        """Publish SSE event if broadcaster is available."""
        if self.broadcaster:
            self.broadcaster.publish(event_type, data)
```

- [ ] **Step 4: Update `__init__.py`**

```python
# backend/src/mirror/__init__.py
"""Bet mirror — intercept and log bets placed on bookmaker sites."""
from .service import MirrorService

__all__ = ["MirrorService"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_mirror_service.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/src/mirror/ backend/tests/test_mirror_service.py
git commit -m "feat(mirror): add MirrorService orchestrating interception, parsing, and bet creation"
```

---

### Task 5: API Routes — Start/Stop/Status

**Files:**
- Create: `backend/src/api/routes/mirror.py`
- Modify: `backend/src/api/routes/__init__.py`
- Modify: `backend/src/api/__init__.py`

- [ ] **Step 1: Create mirror route module**

```python
# backend/src/api/routes/mirror.py
"""Mirror API routes — start/stop bet interception browser."""

import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks

from ...mirror.service import MirrorService
from ...pipeline.broadcast import odds_broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mirror", tags=["mirror"])

# Singleton mirror service (one provider at a time for v1)
_mirror: MirrorService | None = None


def _get_mirror() -> MirrorService | None:
    return _mirror


@router.post("/start")
async def start_mirror(
    background_tasks: BackgroundTasks,
    provider: str = "spelklubben",
    url: str | None = None,
    discovery: bool = False,
):
    """Start bet interception for a provider."""
    global _mirror

    if _mirror and _mirror.get_status()["running"]:
        raise HTTPException(400, f"Mirror already running for {_mirror.provider_id}")

    _mirror = MirrorService(provider_id=provider, broadcaster=odds_broadcaster, discovery=discovery)
    await _mirror.start(site_url=url)

    return _mirror.get_status()


@router.post("/stop")
async def stop_mirror():
    """Stop bet interception."""
    global _mirror

    if not _mirror:
        raise HTTPException(400, "No mirror running")

    await _mirror.stop()
    status = _mirror.get_status()
    _mirror = None
    return status


@router.get("/status")
async def mirror_status():
    """Get current mirror status."""
    if not _mirror:
        return {"running": False, "provider": None, "status": "stopped", "since": None}
    return _mirror.get_status()
```

- [ ] **Step 2: Register the router in `__init__.py`**

Add to `backend/src/api/routes/__init__.py`:

```python
from .mirror import router as mirror_router
```

Add `'mirror_router'` to the `__all__` list.

- [ ] **Step 3: Include router in FastAPI app**

Add to `backend/src/api/__init__.py` — import `mirror_router` from `routes` and add `app.include_router(mirror_router)` alongside the other routers.

- [ ] **Step 4: Verify server starts**

Run: `cd backend && python -c "from src.api import app; print('OK')"`
Expected: prints `OK` (no import errors)

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/mirror.py backend/src/api/routes/__init__.py backend/src/api/__init__.py
git commit -m "feat(mirror): add API routes for start/stop/status"
```

---

### Task 6: CLI Command — Mirror

**Files:**
- Modify: `backend/src/app.py`

- [ ] **Step 1: Add mirror command to Typer app**

Add to `backend/src/app.py`:

```python
@app.command()
def mirror(
    provider: str = typer.Argument("spelklubben", help="Provider to mirror bets from"),
    stop: bool = typer.Option(False, "--stop", help="Stop the mirror"),
):
    """Start/stop bet mirroring — delegates to running API server."""
    import httpx

    base = "http://localhost:8000/api/mirror"
    try:
        if stop:
            r = httpx.post(f"{base}/stop", timeout=10)
        else:
            r = httpx.post(f"{base}/start", params={"provider": provider}, timeout=30)
        r.raise_for_status()
        print(r.json())
    except httpx.ConnectError:
        print("Error: Backend server not running. Start it first.")
    except httpx.HTTPStatusError as e:
        print(f"Error: {e.response.json().get('detail', e.response.text)}")
```

- [ ] **Step 2: Verify CLI registers the command**

Run: `cd backend && python -m src.app mirror --help`
Expected: Shows help text for mirror command

- [ ] **Step 3: Commit**

```bash
git add backend/src/app.py
git commit -m "feat(mirror): add CLI command delegating to API server"
```

---

### Task 7: Frontend — BetMirrorToast Component

**Files:**
- Create: `frontend/src/hooks/useBetMirror.ts`
- Create: `frontend/src/components/Terminal/BetMirrorToast.tsx`
- Modify: `frontend/src/components/Terminal/TerminalWindow.tsx`

- [ ] **Step 1: Create the SSE hook**

Note: The existing `useOddsStream` hook already manages an SSE connection to `/api/extraction/stream`. Rather than creating a duplicate connection, we add the `bet_mirrored` and `bet_rejected` listeners to that existing hook and expose the toast state. However, since `useOddsStream` is mounted in a different component tree, the cleanest approach for v1 is a separate EventSource — the browser reuses the same HTTP/2 connection anyway. This duplication is intentional for isolation and can be unified later.

```typescript
// frontend/src/hooks/useBetMirror.ts
import { useState, useEffect, useCallback } from 'react';

export interface MirroredBet {
  id: number;
  status: string;
  confirmation_id?: string;
  provider: string;
  event: string;
  market: string | null;
  outcome: string | null;
  odds: number;
  stake: number;
  matched: boolean;
  error?: string;
  timestamp: number;
}

export function useBetMirror() {
  const [toasts, setToasts] = useState<MirroredBet[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    const addToast = (data: Partial<MirroredBet>) => {
      const toast: MirroredBet = {
        id: Date.now() + Math.random(),
        status: 'ok',
        provider: '',
        event: '',
        market: null,
        outcome: null,
        odds: 0,
        stake: 0,
        matched: false,
        timestamp: Date.now(),
        ...data,
      };
      setToasts(prev => [...prev, toast]);
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== toast.id));
      }, 5000);
    };

    es.addEventListener('bet_mirrored', (e: MessageEvent) => {
      addToast(JSON.parse(e.data));
    });

    es.addEventListener('bet_rejected', (e: MessageEvent) => {
      addToast({ ...JSON.parse(e.data), status: 'rejected' });
    });

    return () => es.close();
  }, []);

  return { toasts, dismiss };
}
```

- [ ] **Step 2: Create the toast component**

```tsx
// frontend/src/components/Terminal/BetMirrorToast.tsx
import { useBetMirror, MirroredBet } from '../../hooks/useBetMirror';

function ToastItem({ toast, onDismiss }: { toast: MirroredBet; onDismiss: () => void }) {
  const isError = toast.status === 'error' || toast.status === 'rejected';
  const isDuplicate = toast.status === 'duplicate';

  const borderColor = isError ? 'border-error/40' : isDuplicate ? 'border-muted/40' : 'border-success/40';
  const bgColor = isError ? 'bg-error/10' : isDuplicate ? 'bg-muted/10' : 'bg-success/10';
  const iconColor = isError ? 'text-error' : isDuplicate ? 'text-muted' : 'text-success';
  const icon = isError ? '!' : isDuplicate ? '~' : '✓';

  return (
    <div
      className={`border ${borderColor} ${bgColor} text-xs font-mono px-3 py-2 flex items-center gap-2 animate-fadeIn cursor-pointer`}
      onClick={onDismiss}
    >
      <span className={`${iconColor} font-bold`}>{icon}</span>
      {toast.status === 'rejected' ? (
        <span className="text-error">Bet rejected by {toast.provider}</span>
      ) : isDuplicate ? (
        <span className="text-muted">Duplicate bet skipped ({toast.confirmation_id})</span>
      ) : toast.error ? (
        <span className="text-error">Mirror error: {toast.error}</span>
      ) : (
        <>
          <span className="text-success">Bet captured:</span>
          <span className="text-fg">{toast.event}</span>
          <span className="text-muted">{toast.market} {toast.outcome}</span>
          <span className="text-fg">@ {toast.odds?.toFixed(2)}</span>
          <span className="text-muted">—</span>
          <span className="text-fg">{toast.stake} kr</span>
          {!toast.matched && <span className="text-warning">(unmatched)</span>}
        </>
      )}
    </div>
  );
}

export function BetMirrorToast() {
  const { toasts, dismiss } = useBetMirror();

  if (toasts.length === 0) return null;

  return (
    <div className="mx-3 mt-2 flex flex-col gap-1">
      {toasts.map(toast => (
        <ToastItem key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Mount toast in TerminalWindow**

In `frontend/src/components/Terminal/TerminalWindow.tsx`, add import and render `<BetMirrorToast />` right after `<ErrorNotificationBar />` (around line 156):

```tsx
import { BetMirrorToast } from './BetMirrorToast';

// In the JSX, after <ErrorNotificationBar />:
<ErrorNotificationBar />
<BetMirrorToast />
```

- [ ] **Step 4: Verify frontend builds**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useBetMirror.ts frontend/src/components/Terminal/BetMirrorToast.tsx frontend/src/components/Terminal/TerminalWindow.tsx
git commit -m "feat(mirror): add frontend toast notification for mirrored bets"
```

---

### Task 8: Discovery Mode — Broad POST Logger

**Files:**
- Modify: `backend/src/mirror/interceptor.py`

This is a temporary mode used once to discover the exact bet placement endpoint.

- [ ] **Step 1: Add discovery mode to BetInterceptor**

Add a `discovery` parameter to `BetInterceptor.__init__()` and modify `_on_response` to log ALL POST requests to `/api/sb/` when in discovery mode:

```python
# In __init__:
self.discovery = discovery  # When True, log ALL /api/sb/ POST requests

# In _on_response, replace the is_bet_placement_url check:
if self.discovery:
    # Discovery mode: log everything for analysis
    logger.info(f"[mirror:{self.provider_id}] [DISCOVERY] POST {url}")
    try:
        body_text = await response.text()
        logger.info(f"[mirror:{self.provider_id}] [DISCOVERY] Body preview: {body_text[:500]}")
    except Exception:
        pass
    if self.on_bet_response:
        request_body = response.request.post_data
        await self.on_bet_response(url, request_body, body_text)
    return
```

- [ ] **Step 2: Add discovery param to API start endpoint**

In `backend/src/api/routes/mirror.py`, add `discovery: bool = False` parameter to `start_mirror()` and pass it through to `MirrorService` → `BetInterceptor`.

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/interceptor.py backend/src/api/routes/mirror.py
git commit -m "feat(mirror): add discovery mode for endpoint identification"
```

---

### Task 9: Integration Test — End-to-End Flow

**Files:**
- Test: `backend/tests/test_mirror_integration.py`

- [ ] **Step 1: Write integration test**

```python
# backend/tests/test_mirror_integration.py
"""Integration test for the mirror flow: parse → dedup → store trace → create bet."""
import json
import pytest
from datetime import datetime, timezone

from src.db.models import init_db, get_session, Bet, BetTrace, Event, Provider
from src.mirror.service import MirrorService


@pytest.fixture
def db():
    init_db(":memory:")
    session = get_session()

    from datetime import timedelta
    # Create required provider
    session.add(Provider(id="spelklubben", name="Spelklubben", url="https://spelklubben.se"))
    # Create a matching event (start_time in the future so _match_event finds it)
    session.add(Event(
        id="test_evt_1",
        sport="football",
        home_team="virginia united",
        away_team="north lakes united",
        start_time=datetime.now(timezone.utc) + timedelta(hours=2),
    ))
    session.commit()
    yield session
    session.close()


def test_process_confirmed_bet(db):
    """Full flow: confirmed bet → trace + bet created."""
    service = MirrorService(provider_id="spelklubben", broadcaster=None)

    parsed = {
        "confirmation_id": "gecko_bet_999",
        "odds": 2.10,
        "stake": 100.0,
        "market": "1x2",
        "outcome": "home",
        "point": None,
        "home_team": "virginia united",
        "away_team": "north lakes united",
        "event_name": "Virginia United vs North Lakes United",
        "gecko_event_id": "evt_456",
    }

    result = service._process_bet_sync(
        url="https://example.com/api/sb/v1/betslip/place",
        request_body='{"stake": 100}',
        response_body=json.dumps({"data": {"betId": "gecko_bet_999"}}),
        parsed=parsed,
    )

    assert result["status"] == "ok"
    assert result["matched"] is True

    # Verify bet was created
    bet = db.query(Bet).filter(Bet.confirmation_id == "gecko_bet_999").first()
    assert bet is not None
    assert bet.odds == 2.10
    assert bet.stake == 100.0
    assert bet.provider_id == "spelklubben"

    # Verify trace was stored
    trace = db.query(BetTrace).first()
    assert trace is not None
    assert trace.provider_bet_id == "gecko_bet_999"
    assert trace.parse_status == "ok"


def test_dedup_prevents_double_logging(db):
    """Same confirmation_id should not create a second bet."""
    service = MirrorService(provider_id="spelklubben", broadcaster=None)

    parsed = {
        "confirmation_id": "gecko_bet_dedup",
        "odds": 1.80,
        "stake": 50.0,
        "market": "1x2",
        "outcome": "away",
        "point": None,
        "home_team": "virginia united",
        "away_team": "north lakes united",
        "event_name": "Virginia United vs North Lakes United",
        "gecko_event_id": "evt_456",
    }

    # First call: creates bet
    result1 = service._process_bet_sync("url", "{}", "{}", parsed)
    assert result1["status"] == "ok"

    # Second call: dedup
    result2 = service._process_bet_sync("url", "{}", "{}", parsed)
    assert result2["status"] == "duplicate"

    # Only one bet in DB
    count = db.query(Bet).filter(Bet.confirmation_id == "gecko_bet_dedup").count()
    assert count == 1
```

- [ ] **Step 2: Run integration test**

Run: `cd backend && python -m pytest tests/test_mirror_integration.py -v`
Expected: PASS (2 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_mirror_integration.py
git commit -m "test(mirror): add integration tests for end-to-end mirror flow"
```

---

## Task Dependency Graph

```
Task 1 (BetTrace model)  ──┐
Task 2 (Gecko parser)    ──┤── independent, can run in parallel
Task 7 (Frontend toast)  ──┘

Task 3 (BetInterceptor) ← depends on 2 (parser import)
Task 4 (MirrorService)  ← depends on 1, 2, 3
Task 5 (API routes)     ← depends on 4
Task 6 (CLI command)    ← depends on 5
Task 8 (Discovery mode) ← depends on 3, 4, 5
Task 9 (Integration test) ← depends on 1, 2, 4
```

**Parallelizable:** Tasks 1, 2, and 7 can run in parallel. Task 7 is fully independent of all backend tasks.

---

## Post-Implementation: Discovery Phase

After all tasks are complete:

1. Start the backend: `python run_dev.py`
2. Start the mirror in discovery mode: `curl -X POST "http://localhost:8000/api/mirror/start?provider=spelklubben&discovery=true"`
3. In the Playwright browser that opens: log in to Spelklubben, place a minimum-stake bet
4. Check backend logs for `[DISCOVERY]` entries — identify the bet placement endpoint and response schema
5. Update `GeckoBetParser.is_bet_placement_url()` with the exact URL pattern
6. Update `GeckoBetParser.parse()` field mappings based on the real response structure
7. Update `test_gecko_bet_parser.py` test fixtures with the real response schema
8. Switch off discovery mode and test with a real bet
