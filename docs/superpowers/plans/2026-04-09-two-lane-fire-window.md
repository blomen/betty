# Two-Lane Fire Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the fire window into two side-by-side lanes (sync + betting) powered by event-driven SSE streaming, replacing the current pull-based sequential flow.

**Architecture:** A new EventRouter classifies intercepted browser responses and routes them through persist-then-broadcast to 3 dedicated SSE channels. The frontend subscribes via 4 hooks, with bootstrap endpoints for reconnect resilience. Existing REST endpoints stay for mutations (Place, Skip, Confirm Settlement).

**Tech Stack:** Python/FastAPI (SSE via sse-starlette), SQLAlchemy (3 new tables), React/TypeScript (EventSource hooks), Playwright interception (extended for odds tickers)

**Spec:** `docs/superpowers/specs/2026-04-09-two-lane-fire-window-design.md`

---

## File Structure

### New files (backend)
- `backend/src/mirror/event_router.py` — EventRouter class: classifies intercepted responses, persists to DB, broadcasts to SSE channels
- `backend/src/mirror/channels.py` — 3 Broadcaster instances (sync, prices, actions) + SSE endpoint generators
- `backend/src/api/routes/mirror_stream.py` — FastAPI router: `/api/mirror/stream/sync`, `/stream/prices`, `/stream/actions` + bootstrap endpoints
- `backend/tests/test_event_router.py` — EventRouter unit tests
- `backend/tests/test_mirror_stream.py` — SSE channel + bootstrap endpoint tests

### New files (frontend)
- `frontend/src/hooks/useSyncStream.ts` — sync lane SSE hook
- `frontend/src/hooks/usePriceStream.ts` — price lane SSE hook
- `frontend/src/hooks/useBettingLane.ts` — betting lane actions hook
- `frontend/src/hooks/useProviderQueue.ts` — provider queue hook
- `frontend/src/components/Terminal/pages/play/SyncLane.tsx` — sync lane component
- `frontend/src/components/Terminal/pages/play/BettingLane.tsx` — betting lane component

### Modified files
- `backend/src/db/models.py` — add BalanceLog, SettlementQueue, PriceCache models
- `backend/src/mirror/interceptor.py` — forward all classified responses to EventRouter instead of direct callbacks
- `backend/src/mirror/service.py` — delegate to EventRouter, remove ad-hoc SSE publishing
- `backend/src/api/routes/fire_window.py` — add settlement confirm endpoint, wire to EventRouter
- `backend/src/app.py` — register mirror_stream router
- `frontend/src/components/Terminal/pages/play/FireWindow.tsx` — replace with two-lane layout
- `frontend/src/hooks/useBetMirror.ts` — deprecate, replaced by new hooks
- `frontend/src/services/api/fireWindow.ts` — add new API methods

---

## Task 1: DB Models (BalanceLog, SettlementQueue, PriceCache)

**Files:**
- Modify: `backend/src/db/models.py`
- Test: `backend/tests/test_new_models.py`

- [ ] **Step 1: Write test for new models**

```python
# backend/tests/test_new_models.py
"""Tests for BalanceLog, SettlementQueue, PriceCache models."""
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, BalanceLog, SettlementQueue, PriceCache


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_balance_log_insert(db):
    row = BalanceLog(
        provider_id="betsson",
        amount=1240.50,
        currency="SEK",
        source="intercepted",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.id is not None
    assert row.amount == 1240.50
    assert row.currency == "SEK"
    assert row.source == "intercepted"
    assert row.created_at is not None


def test_settlement_queue_lifecycle(db):
    row = SettlementQueue(
        provider_id="unibet",
        bet_id=42,
        result="won",
        payout=250.0,
    )
    db.add(row)
    db.commit()
    assert row.status == "pending"
    assert row.confirmed_at is None

    # Confirm
    row.status = "confirmed"
    row.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    assert row.status == "confirmed"


def test_price_cache_insert(db):
    row = PriceCache(
        provider_id="betsson",
        event_id="soccer:real_madrid:barcelona:2026-04-12",
        market="1x2",
        outcome="home",
        odds=2.15,
        source="intercepted",
    )
    db.add(row)
    db.commit()
    assert row.id is not None
    assert row.odds == 2.15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_new_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'BalanceLog'`

- [ ] **Step 3: Add models to models.py**

Add after the `BetTrace` class (around line 320) in `backend/src/db/models.py`:

```python
# ============ Mirror Streaming Tables ============

class BalanceLog(Base):
    """Append-only balance log from intercepted provider API responses."""
    __tablename__ = "balance_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), nullable=False, default="SEK")
    source = Column(String, nullable=False)  # 'intercepted' | 'api_fetch'
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index('ix_balance_log_provider_created', 'provider_id', 'created_at'),
    )


class SettlementQueue(Base):
    """Persistent settlement queue — survives restarts, user confirms before bankroll update."""
    __tablename__ = "settlement_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    bet_id = Column(Integer, ForeignKey("bets.id"), nullable=True)
    result = Column(String, nullable=False)  # 'won' | 'lost' | 'void'
    payout = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")  # 'pending' | 'confirmed'
    detected_at = Column(DateTime, default=_utcnow)
    confirmed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('ix_settlement_queue_provider_status', 'provider_id', 'status'),
    )


class PriceCache(Base):
    """Live price ticks from intercepted odds responses. Upsert per event+market+outcome."""
    __tablename__ = "price_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    market = Column(String, nullable=False)
    outcome = Column(String, nullable=False)
    odds = Column(Float, nullable=False)
    source = Column(String, nullable=False)  # 'intercepted' | 'dom' | 'api'
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint('provider_id', 'event_id', 'market', 'outcome', name='uq_price_cache_key'),
        Index('ix_price_cache_provider_event', 'provider_id', 'event_id'),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_new_models.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_new_models.py
git commit -m "feat(mirror): add BalanceLog, SettlementQueue, PriceCache models"
```

---

## Task 2: Mirror Channels (3 SSE Broadcasters)

**Files:**
- Create: `backend/src/mirror/channels.py`
- Test: `backend/tests/test_mirror_channels.py`

- [ ] **Step 1: Write test for mirror channels**

```python
# backend/tests/test_mirror_channels.py
"""Tests for dedicated mirror SSE channels."""
import asyncio
import pytest

from src.mirror.channels import sync_channel, price_channel, action_channel


@pytest.mark.asyncio
async def test_sync_channel_publish_subscribe():
    client_id, queue = sync_channel.subscribe()
    try:
        sync_channel.publish("balance_update", {"provider_id": "betsson", "amount": 1240.50})
        msg = queue.get_nowait()
        assert msg["event"] == "balance_update"
        assert msg["data"]["provider_id"] == "betsson"
        assert msg["data"]["amount"] == 1240.50
    finally:
        sync_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_price_channel_publish_subscribe():
    client_id, queue = price_channel.subscribe()
    try:
        price_channel.publish("price_update", {"provider_id": "betsson", "odds": 2.15})
        msg = queue.get_nowait()
        assert msg["event"] == "price_update"
        assert msg["data"]["odds"] == 2.15
    finally:
        price_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_action_channel_publish_subscribe():
    client_id, queue = action_channel.subscribe()
    try:
        action_channel.publish("bet_placed", {"bet_id": 42})
        msg = queue.get_nowait()
        assert msg["event"] == "bet_placed"
        assert msg["data"]["bet_id"] == 42
    finally:
        action_channel.unsubscribe(client_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_mirror_channels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mirror.channels'`

- [ ] **Step 3: Create channels.py**

```python
# backend/src/mirror/channels.py
"""Dedicated SSE broadcast channels for the two-lane fire window.

Three channels replace the single odds_broadcaster for mirror events:
- sync_channel:   balance, history, settlements, notifications, provider state
- price_channel:  live odds ticks, price verification, edge updates
- action_channel: navigation, autofill, bet placement/skip confirmations
"""
from ..pipeline.broadcast import Broadcaster

sync_channel = Broadcaster()
price_channel = Broadcaster()
action_channel = Broadcaster()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_mirror_channels.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/channels.py backend/tests/test_mirror_channels.py
git commit -m "feat(mirror): add dedicated SSE channels (sync, price, action)"
```

---

## Task 3: EventRouter

**Files:**
- Create: `backend/src/mirror/event_router.py`
- Test: `backend/tests/test_event_router.py`

- [ ] **Step 1: Write test for EventRouter**

```python
# backend/tests/test_event_router.py
"""Tests for EventRouter: classify, persist, broadcast."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

from src.mirror.event_router import EventRouter


@pytest.fixture
def router():
    return EventRouter()


def test_classify_balance_response(router):
    """Altenar balance URL should classify as 'balance'."""
    result = router.classify(
        url="https://sb2frontend-altenar2.bfrndz.com/api/account/balance",
        response_body={"balance": 1240.5},
    )
    assert result == "balance"


def test_classify_history_response(router):
    """Bet history URL should classify as 'history'."""
    result = router.classify(
        url="https://sb2frontend-altenar2.bfrndz.com/api/widget/widgetBetHistory",
        response_body={"bets": []},
    )
    assert result == "history"


def test_classify_bet_confirm(router):
    """Bet placement URL should classify as 'bet_confirm'."""
    result = router.classify(
        url="https://sb2frontend-altenar2.bfrndz.com/api/widget/placeWidget",
        response_body={"betId": "123"},
    )
    assert result == "bet_confirm"


def test_classify_odds_response(router):
    """Event details URL should classify as 'odds'."""
    result = router.classify(
        url="https://sb2frontend-altenar2.bfrndz.com/api/widget/GetEventDetails",
        response_body={"markets": []},
    )
    assert result == "odds"


def test_classify_notification(router):
    """Notification settings URL should classify as 'notification'."""
    result = router.classify(
        url="https://example.com/api/notifications/preferences",
        response_body={},
    )
    assert result == "notification"


def test_classify_unknown(router):
    """Unknown URL should return None."""
    result = router.classify(
        url="https://example.com/api/random/stuff",
        response_body={},
    )
    assert result is None


@pytest.mark.asyncio
async def test_route_balance_persists_and_broadcasts(router):
    """Balance response should persist to balance_log and broadcast."""
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.commit = MagicMock()

    with patch.object(router, '_get_session', return_value=mock_session), \
         patch.object(router, '_persist_balance') as mock_persist, \
         patch.object(router, '_broadcast') as mock_broadcast:
        mock_persist.return_value = {"provider_id": "betsson", "amount": 1240.5, "currency": "SEK"}
        await router.route(
            provider_id="betsson",
            category="balance",
            url="https://example.com/balance",
            response_body={"balance": 1240.5},
        )
        mock_persist.assert_called_once()
        mock_broadcast.assert_called_once_with("sync", "balance_update", mock_persist.return_value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_event_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mirror.event_router'`

- [ ] **Step 3: Implement EventRouter**

```python
# backend/src/mirror/event_router.py
"""EventRouter — classifies intercepted responses, persists to DB, broadcasts to SSE channels.

Replaces MirrorService's ad-hoc _handle_*() + _notify() pattern.
Every intercepted response flows: classify → persist → broadcast.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from .channels import sync_channel, price_channel, action_channel

logger = logging.getLogger(__name__)

# URL patterns for classification
_BALANCE_PATTERNS = (
    "/account/balance", "/mainbalance", "/wallets", "/wallet/balance",
    "/api/sb/v2/balance", "/cashier/balance",
)
_HISTORY_PATTERNS = (
    "/widgetBetHistory", "/bethistory", "/betHistory", "/bets?status=",
    "/bet-history", "/myBets", "/portfolio?tab=history",
)
_BET_PLACEMENT_PATTERNS = (
    "/placeWidget", "/placeBet", "/coupons", "/bets/straight",
    "/bets/parlay", "/bets/place", "clob.polymarket.com/order",
)
_ODDS_PATTERNS = (
    "/GetEventDetails", "/events-table", "/event/", "/odds/",
    "/offering/v2018/", "/market/",
)
_NOTIFICATION_PATTERNS = (
    "/notification", "/preferences", "/communication", "/consent",
    "/marketing", "/subscription",
)


class EventRouter:
    """Classifies intercepted browser responses and routes to persist + broadcast."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    def set_session_factory(self, factory):
        self._session_factory = factory

    def classify(self, url: str, response_body: Any = None) -> str | None:
        """Classify a URL into a category. Returns None if unrecognized."""
        url_lower = url.lower()
        for pattern in _BET_PLACEMENT_PATTERNS:
            if pattern.lower() in url_lower:
                return "bet_confirm"
        for pattern in _BALANCE_PATTERNS:
            if pattern.lower() in url_lower:
                return "balance"
        for pattern in _HISTORY_PATTERNS:
            if pattern.lower() in url_lower:
                return "history"
        for pattern in _ODDS_PATTERNS:
            if pattern.lower() in url_lower:
                return "odds"
        for pattern in _NOTIFICATION_PATTERNS:
            if pattern.lower() in url_lower:
                return "notification"
        return None

    async def route(
        self,
        provider_id: str,
        category: str,
        url: str,
        response_body: Any,
        request_body: Any = None,
        page_url: str | None = None,
    ):
        """Persist to DB, then broadcast to the appropriate SSE channel."""
        try:
            if category == "balance":
                data = self._persist_balance(provider_id, response_body)
                self._broadcast("sync", "balance_update", data)
            elif category == "history":
                data = self._persist_history(provider_id, response_body)
                if data:
                    self._broadcast("sync", "history_update", data)
            elif category == "odds":
                data = self._persist_prices(provider_id, response_body)
                if data:
                    self._broadcast("prices", "price_update", data)
            elif category == "bet_confirm":
                # Bet confirmations still go through MirrorService for parsing
                # Router just broadcasts the action event
                self._broadcast("actions", "bet_placed", {
                    "provider_id": provider_id,
                    "url": url,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            elif category == "notification":
                self._broadcast("sync", "notification_status", {
                    "provider_id": provider_id,
                    "url": url,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
        except Exception:
            logger.exception(f"EventRouter.route failed for {category} from {provider_id}")

    def _persist_balance(self, provider_id: str, body: Any) -> dict:
        """Parse balance from response body and persist to balance_log."""
        # Extract amount — providers use different response shapes
        amount = None
        if isinstance(body, dict):
            for key in ("balance", "amount", "availableBalance", "cash", "total"):
                if key in body:
                    val = body[key]
                    if isinstance(val, (int, float)):
                        amount = float(val)
                        break
                    elif isinstance(val, dict) and "amount" in val:
                        amount = float(val["amount"])
                        break
            # Gecko V2: nested wallets
            if amount is None and "wallets" in body:
                wallets = body["wallets"]
                if isinstance(wallets, list) and wallets:
                    amount = float(wallets[0].get("balance", 0))

        if amount is None:
            logger.warning(f"Could not extract balance for {provider_id} from {type(body)}")
            amount = 0.0

        data = {
            "provider_id": provider_id,
            "amount": amount,
            "currency": "SEK",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        if self._session_factory:
            try:
                from ..db.models import BalanceLog
                with self._session_factory() as session:
                    session.add(BalanceLog(
                        provider_id=provider_id,
                        amount=amount,
                        currency="SEK",
                        source="intercepted",
                    ))
                    session.commit()
            except Exception:
                logger.exception(f"Failed to persist balance for {provider_id}")

        return data

    def _persist_history(self, provider_id: str, body: Any) -> dict | None:
        """Persist bet history and detect settlements. Returns SSE payload or None."""
        # History parsing is provider-specific — delegate to MirrorService for now
        # Router just signals that history was received
        return {
            "provider_id": provider_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def _persist_prices(self, provider_id: str, body: Any) -> dict | None:
        """Extract odds from event details response and persist to price_cache."""
        # Price parsing is provider-specific — will be extended per-provider
        return {
            "provider_id": provider_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def _broadcast(self, channel: str, event_type: str, data: dict):
        """Broadcast to the appropriate SSE channel."""
        if channel == "sync":
            sync_channel.publish(event_type, data)
        elif channel == "prices":
            price_channel.publish(event_type, data)
        elif channel == "actions":
            action_channel.publish(event_type, data)

    def broadcast_action(self, event_type: str, data: dict):
        """Public method for MirrorService to broadcast action events."""
        self._broadcast("actions", event_type, data)

    def broadcast_sync(self, event_type: str, data: dict):
        """Public method for MirrorService to broadcast sync events."""
        self._broadcast("sync", event_type, data)

    def broadcast_price(self, event_type: str, data: dict):
        """Public method for broadcasting price events."""
        self._broadcast("prices", event_type, data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_event_router.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/event_router.py backend/tests/test_event_router.py
git commit -m "feat(mirror): add EventRouter — classify, persist, broadcast"
```

---

## Task 4: Mirror Stream API Routes

**Files:**
- Create: `backend/src/api/routes/mirror_stream.py`
- Modify: `backend/src/app.py`
- Test: `backend/tests/test_mirror_stream.py`

- [ ] **Step 1: Write test for mirror stream endpoints**

```python
# backend/tests/test_mirror_stream.py
"""Tests for mirror stream SSE endpoints and bootstrap routes."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.api.routes.mirror_stream import router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router, prefix="/api/mirror")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_bootstrap_queue(client):
    """GET /api/mirror/queue should return provider queue."""
    with patch("src.api.routes.mirror_stream._get_fire_window") as mock_fw:
        mock_fw.return_value = MagicMock(
            provider_queue=["betsson", "unibet"],
            provider_bets={"betsson": [MagicMock()], "unibet": [MagicMock(), MagicMock()]},
            current_provider="betsson",
        )
        resp = client.get("/api/mirror/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["providers"]) == 2
        assert data["providers"][0]["id"] == "betsson"


def test_bootstrap_state(client):
    """GET /api/mirror/state/{provider_id} should return provider sync state."""
    with patch("src.api.routes.mirror_stream._get_balance") as mock_bal, \
         patch("src.api.routes.mirror_stream._get_pending_bets") as mock_bets, \
         patch("src.api.routes.mirror_stream._get_pending_settlements") as mock_settle:
        mock_bal.return_value = {"amount": 1240.5, "currency": "SEK"}
        mock_bets.return_value = []
        mock_settle.return_value = []
        resp = client.get("/api/mirror/state/betsson")
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"]["amount"] == 1240.5


def test_confirm_settlements(client):
    """POST /api/mirror/settlements/confirm should confirm pending settlements."""
    with patch("src.api.routes.mirror_stream._confirm_settlements") as mock_confirm:
        mock_confirm.return_value = {"confirmed": 3, "provider_id": "betsson"}
        resp = client.post("/api/mirror/settlements/confirm", json={"provider_id": "betsson"})
        assert resp.status_code == 200
        assert resp.json()["confirmed"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_mirror_stream.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.api.routes.mirror_stream'`

- [ ] **Step 3: Implement mirror stream routes**

```python
# backend/src/api/routes/mirror_stream.py
"""Mirror streaming API — SSE channels + bootstrap endpoints for two-lane fire window."""
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ...mirror.channels import sync_channel, price_channel, action_channel
from ...db.models import BalanceLog, SettlementQueue, Bet
from ...services.fire_window import _window

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mirror-stream"])


# ---------- SSE Stream Endpoints ----------

@router.get("/stream/sync")
async def stream_sync(request: Request):
    """SSE stream: balance, history, settlements, notifications, provider state."""
    client_id, queue = sync_channel.subscribe()

    async def generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            sync_channel.unsubscribe(client_id)

    return EventSourceResponse(generator(), ping=15)


@router.get("/stream/prices")
async def stream_prices(request: Request):
    """SSE stream: live odds ticks, price verification, edge updates."""
    client_id, queue = price_channel.subscribe()

    async def generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            price_channel.unsubscribe(client_id)

    return EventSourceResponse(generator(), ping=15)


@router.get("/stream/actions")
async def stream_actions(request: Request):
    """SSE stream: navigation, autofill, bet placed/skipped."""
    client_id, queue = action_channel.subscribe()

    async def generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            action_channel.unsubscribe(client_id)

    return EventSourceResponse(generator(), ping=15)


# ---------- Bootstrap Endpoints ----------

def _get_fire_window():
    return _window


def _get_balance(provider_id: str) -> dict:
    """Get latest balance from balance_log."""
    from ...db.models import get_session
    with get_session() as session:
        row = (
            session.query(BalanceLog)
            .filter(BalanceLog.provider_id == provider_id)
            .order_by(BalanceLog.created_at.desc())
            .first()
        )
        if row:
            return {"amount": row.amount, "currency": row.currency, "updated_at": row.created_at.isoformat()}
        return {"amount": 0, "currency": "SEK", "updated_at": None}


def _get_pending_bets(provider_id: str) -> list[dict]:
    """Get pending bets for a provider."""
    from ...db.models import get_session
    with get_session() as session:
        bets = (
            session.query(Bet)
            .filter(Bet.provider_id == provider_id, Bet.result == "pending")
            .all()
        )
        return [
            {"id": b.id, "event_id": b.event_id, "market": b.market,
             "outcome": b.outcome, "odds": b.odds, "stake": b.stake}
            for b in bets
        ]


def _get_pending_settlements(provider_id: str) -> list[dict]:
    """Get pending settlements from settlement_queue."""
    from ...db.models import get_session
    with get_session() as session:
        rows = (
            session.query(SettlementQueue)
            .filter(SettlementQueue.provider_id == provider_id, SettlementQueue.status == "pending")
            .all()
        )
        return [
            {"id": r.id, "bet_id": r.bet_id, "result": r.result,
             "payout": r.payout, "detected_at": r.detected_at.isoformat()}
            for r in rows
        ]


@router.get("/state/{provider_id}")
async def bootstrap_state(provider_id: str):
    """Bootstrap endpoint: full sync state for a provider (called on frontend mount)."""
    return {
        "balance": _get_balance(provider_id),
        "pending_bets": _get_pending_bets(provider_id),
        "pending_settlements": _get_pending_settlements(provider_id),
        "notification_status": {"email": False, "sms": False, "push": False},
    }


@router.get("/prices/{provider_id}")
async def bootstrap_prices(provider_id: str):
    """Bootstrap endpoint: latest cached prices for a provider."""
    from ...db.models import get_session, PriceCache
    with get_session() as session:
        rows = (
            session.query(PriceCache)
            .filter(PriceCache.provider_id == provider_id)
            .all()
        )
        return [
            {"event_id": r.event_id, "market": r.market, "outcome": r.outcome,
             "odds": r.odds, "source": r.source,
             "age_seconds": (datetime.now(timezone.utc) - r.updated_at).total_seconds()
             if r.updated_at else None}
            for r in rows
        ]


@router.get("/queue")
async def bootstrap_queue():
    """Bootstrap endpoint: provider queue with status."""
    fw = _get_fire_window()
    if not fw:
        return {"providers": [], "active_provider": None, "pre_syncing": []}

    providers = []
    for pid in fw.provider_queue:
        bets = fw.provider_bets.get(pid, [])
        providers.append({
            "id": pid,
            "state": "active" if pid == fw.current_provider else "queued",
            "bets_remaining": len([b for b in bets if not getattr(b, 'fired', False)]),
        })
    return {
        "providers": providers,
        "active_provider": fw.current_provider,
        "pre_syncing": [],
    }


class SettlementConfirmRequest(BaseModel):
    provider_id: str


def _confirm_settlements(provider_id: str) -> dict:
    """Confirm all pending settlements for a provider."""
    from ...db.models import get_session
    with get_session() as session:
        rows = (
            session.query(SettlementQueue)
            .filter(SettlementQueue.provider_id == provider_id, SettlementQueue.status == "pending")
            .all()
        )
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = "confirmed"
            row.confirmed_at = now
            # Update the corresponding bet
            if row.bet_id:
                bet = session.query(Bet).get(row.bet_id)
                if bet:
                    bet.result = row.result
                    bet.payout = row.payout
                    bet.settled_at = now
                    bet.settlement_source = "mirror_stream"
        session.commit()

        data = {"confirmed": len(rows), "provider_id": provider_id}
        sync_channel.publish("settlement_confirmed", data)
        return data


@router.post("/settlements/confirm")
async def confirm_settlements(req: SettlementConfirmRequest):
    """Confirm pending settlements — applies to bankroll."""
    return _confirm_settlements(req.provider_id)
```

- [ ] **Step 4: Register mirror stream router in app.py**

In `backend/src/app.py`, add import and include:

```python
from .api.routes.mirror_stream import router as mirror_stream_router
# ... in the router registration section:
app.include_router(mirror_stream_router, prefix="/api/mirror")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_mirror_stream.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes/mirror_stream.py backend/src/app.py backend/tests/test_mirror_stream.py
git commit -m "feat(mirror): add SSE stream endpoints + bootstrap routes"
```

---

## Task 5: Wire EventRouter into Interceptor

**Files:**
- Modify: `backend/src/mirror/interceptor.py`
- Modify: `backend/src/mirror/service.py`

- [ ] **Step 1: Add EventRouter to BetInterceptor.__init__**

In `backend/src/mirror/interceptor.py`, add import at top:

```python
from .event_router import EventRouter
```

Add to `__init__()` (after the existing callback setup, around line 123):

```python
self.event_router = EventRouter()
```

- [ ] **Step 2: Wire _on_response to also route through EventRouter**

In `backend/src/mirror/interceptor.py`, in `_on_response()` (around line 262), add at the top of the method, before the existing pattern matching:

```python
# Route through EventRouter for SSE streaming
category = self.event_router.classify(url, body)
if category:
    import asyncio
    asyncio.ensure_future(self.event_router.route(
        provider_id=self._detect_provider_from_url(url) or "unknown",
        category=category,
        url=url,
        response_body=body,
        request_body=request_body,
    ))
```

Note: The existing callback logic stays — EventRouter adds streaming alongside it. The old callbacks handle provider-specific parsing; EventRouter handles persist + broadcast.

- [ ] **Step 3: Wire MirrorService to use EventRouter for action broadcasts**

In `backend/src/mirror/service.py`, add import:

```python
from .event_router import EventRouter
```

In `__init__()`, create the router:

```python
self.event_router = EventRouter()
```

In methods that currently call `self._notify()`, also broadcast via EventRouter:
- After `_record_intercepted_bet()` succeeds: `self.event_router.broadcast_action("bet_placed", {...})`
- After `_sync_balance()`: `self.event_router.broadcast_sync("balance_update", {...})`
- After `_stage_settlements_sync()`: `self.event_router.broadcast_sync("settlement_pending", {...})`
- After `confirm_settlements()`: `self.event_router.broadcast_sync("settlement_confirmed", {...})`

Keep the old `_notify()` calls intact for backward compatibility — the existing `/api/extraction/stream` consumers still need them.

- [ ] **Step 4: Test manually**

Run: `cd backend && pytest tests/ -v --timeout=30`
Expected: All existing tests still pass

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/interceptor.py backend/src/mirror/service.py
git commit -m "feat(mirror): wire EventRouter into interceptor + service"
```

---

## Task 6: Frontend — useSyncStream Hook

**Files:**
- Create: `frontend/src/hooks/useSyncStream.ts`

- [ ] **Step 1: Create the hook**

```typescript
// frontend/src/hooks/useSyncStream.ts
import { useState, useEffect, useRef, useCallback } from 'react';

interface Balance {
  amount: number;
  currency: string;
  updatedAt: string | null;
}

interface PendingBet {
  id: number;
  event_id: string;
  market: string;
  outcome: string;
  odds: number;
  stake: number;
}

interface Settlement {
  id: number;
  bet_id: number | null;
  result: 'won' | 'lost' | 'void';
  payout: number;
  detected_at: string;
}

interface NotificationStatus {
  email: boolean;
  sms: boolean;
  push: boolean;
}

interface SyncState {
  balance: Balance;
  pendingBets: PendingBet[];
  settlements: Settlement[];
  notifications: NotificationStatus;
  connected: boolean;
}

export function useSyncStream(providerId: string | null): SyncState {
  const [balance, setBalance] = useState<Balance>({ amount: 0, currency: 'SEK', updatedAt: null });
  const [pendingBets, setPendingBets] = useState<PendingBet[]>([]);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [notifications, setNotifications] = useState<NotificationStatus>({ email: false, sms: false, push: false });
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // Bootstrap state on mount / provider change
  useEffect(() => {
    if (!providerId) return;

    fetch(`/api/mirror/state/${providerId}`)
      .then(r => r.json())
      .then(data => {
        if (data.balance) setBalance({
          amount: data.balance.amount,
          currency: data.balance.currency,
          updatedAt: data.balance.updated_at,
        });
        if (data.pending_bets) setPendingBets(data.pending_bets);
        if (data.pending_settlements) setSettlements(data.pending_settlements);
        if (data.notification_status) setNotifications(data.notification_status);
      })
      .catch(err => console.error('Bootstrap sync state failed:', err));
  }, [providerId]);

  // SSE connection
  useEffect(() => {
    const es = new EventSource('/api/mirror/stream/sync');
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    es.addEventListener('balance_update', (e) => {
      const data = JSON.parse(e.data);
      if (!providerId || data.provider_id === providerId) {
        setBalance({ amount: data.amount, currency: data.currency || 'SEK', updatedAt: data.ts });
      }
    });

    es.addEventListener('history_update', (e) => {
      const data = JSON.parse(e.data);
      if (!providerId || data.provider_id === providerId) {
        // Re-fetch pending bets on history update
        fetch(`/api/mirror/state/${data.provider_id}`)
          .then(r => r.json())
          .then(state => {
            if (state.pending_bets) setPendingBets(state.pending_bets);
          });
      }
    });

    es.addEventListener('settlement_pending', (e) => {
      const data = JSON.parse(e.data);
      if (!providerId || data.provider_id === providerId) {
        if (data.settlements) setSettlements(data.settlements);
      }
    });

    es.addEventListener('settlement_confirmed', (e) => {
      const data = JSON.parse(e.data);
      if (!providerId || data.provider_id === providerId) {
        setSettlements([]);
        // Re-fetch balance after settlement
        fetch(`/api/mirror/state/${data.provider_id}`)
          .then(r => r.json())
          .then(state => {
            if (state.balance) setBalance({
              amount: state.balance.amount,
              currency: state.balance.currency,
              updatedAt: state.balance.updated_at,
            });
            if (state.pending_bets) setPendingBets(state.pending_bets);
          });
      }
    });

    es.addEventListener('notification_status', (e) => {
      const data = JSON.parse(e.data);
      if (!providerId || data.provider_id === providerId) {
        setNotifications(prev => ({ ...prev, ...data }));
      }
    });

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [providerId]);

  return { balance, pendingBets, settlements, notifications, connected };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/hooks/useSyncStream.ts
git commit -m "feat(frontend): add useSyncStream hook for sync lane"
```

---

## Task 7: Frontend — usePriceStream + useBettingLane + useProviderQueue Hooks

**Files:**
- Create: `frontend/src/hooks/usePriceStream.ts`
- Create: `frontend/src/hooks/useBettingLane.ts`
- Create: `frontend/src/hooks/useProviderQueue.ts`

- [ ] **Step 1: Create usePriceStream**

```typescript
// frontend/src/hooks/usePriceStream.ts
import { useState, useEffect, useRef } from 'react';

interface PriceState {
  domOdds: number | null;
  apiOdds: number | null;
  fairOdds: number | null;
  edge: number | null;
  priceMatch: boolean;
  lastUpdate: Date | null;
}

export function usePriceStream(providerId: string | null, betId: number | null): PriceState {
  const [state, setState] = useState<PriceState>({
    domOdds: null, apiOdds: null, fairOdds: null,
    edge: null, priceMatch: false, lastUpdate: null,
  });

  useEffect(() => {
    if (!providerId) return;

    const es = new EventSource('/api/mirror/stream/prices');

    es.addEventListener('price_update', (e) => {
      const data = JSON.parse(e.data);
      if (data.provider_id === providerId) {
        setState(prev => ({
          ...prev,
          apiOdds: data.odds ?? prev.apiOdds,
          lastUpdate: new Date(),
        }));
      }
    });

    es.addEventListener('price_verified', (e) => {
      const data = JSON.parse(e.data);
      if (betId && data.bet_id === betId) {
        setState(prev => ({
          ...prev,
          domOdds: data.dom_odds,
          apiOdds: data.api_odds,
          priceMatch: data.match,
          lastUpdate: new Date(),
        }));
      }
    });

    es.addEventListener('edge_update', (e) => {
      const data = JSON.parse(e.data);
      if (betId && data.bet_id === betId) {
        setState(prev => ({
          ...prev,
          fairOdds: data.fair_odds,
          edge: data.new_edge,
          lastUpdate: new Date(),
        }));
      }
    });

    return () => es.close();
  }, [providerId, betId]);

  return state;
}
```

- [ ] **Step 2: Create useBettingLane**

```typescript
// frontend/src/hooks/useBettingLane.ts
import { useState, useEffect, useCallback } from 'react';

interface BetDetails {
  bet_id: number;
  event_id: string;
  display_home: string;
  display_away: string;
  sport: string;
  league?: string;
  start_time?: string;
  market: string;
  outcome: string;
  odds: number;
  fair_odds: number;
  edge_pct: number;
  stake: number;
  kelly_pct?: number;
  point?: number;
}

type BettingStatus = 'idle' | 'navigating' | 'filling' | 'ready' | 'placing';

interface BettingLaneState {
  currentBet: BetDetails | null;
  upNext: BetDetails[];
  status: BettingStatus;
  placeBet: () => Promise<void>;
  skipBet: () => Promise<void>;
}

export function useBettingLane(providerId: string | null): BettingLaneState {
  const [currentBet, setCurrentBet] = useState<BetDetails | null>(null);
  const [upNext, setUpNext] = useState<BetDetails[]>([]);
  const [status, setStatus] = useState<BettingStatus>('idle');

  // Listen to action channel for status updates
  useEffect(() => {
    if (!providerId) return;

    const es = new EventSource('/api/mirror/stream/actions');

    es.addEventListener('navigated', () => setStatus('filling'));
    es.addEventListener('autofilled', () => setStatus('ready'));
    es.addEventListener('bet_placed', () => {
      setStatus('navigating');
      // Fetch next bet
      fetchNextBet();
    });
    es.addEventListener('bet_skipped', () => {
      setStatus('navigating');
      fetchNextBet();
    });

    return () => es.close();
  }, [providerId]);

  const fetchNextBet = useCallback(async () => {
    if (!providerId) return;
    try {
      const resp = await fetch(`/api/fire-window/next-bet`);
      const data = await resp.json();
      if (data.bet) {
        setCurrentBet(data.bet);
        setUpNext(data.up_next || []);
        setStatus('navigating');
      } else {
        setCurrentBet(null);
        setUpNext([]);
        setStatus('idle');
      }
    } catch (err) {
      console.error('Failed to fetch next bet:', err);
    }
  }, [providerId]);

  const placeBet = useCallback(async () => {
    if (!currentBet) return;
    setStatus('placing');
    try {
      await fetch(`/api/fire-window/place-bet/${currentBet.bet_id}`, { method: 'POST' });
      // SSE bet_placed event will trigger fetchNextBet
    } catch (err) {
      console.error('Failed to place bet:', err);
      setStatus('ready');
    }
  }, [currentBet]);

  const skipBet = useCallback(async () => {
    if (!currentBet) return;
    try {
      await fetch(`/api/fire-window/skip-bet/${currentBet.bet_id}`, { method: 'POST' });
      // SSE bet_skipped event will trigger fetchNextBet
    } catch (err) {
      console.error('Failed to skip bet:', err);
    }
  }, [currentBet]);

  // Initial fetch
  useEffect(() => {
    if (providerId) fetchNextBet();
  }, [providerId, fetchNextBet]);

  return { currentBet, upNext, status, placeBet, skipBet };
}
```

- [ ] **Step 3: Create useProviderQueue**

```typescript
// frontend/src/hooks/useProviderQueue.ts
import { useState, useEffect } from 'react';

interface ProviderQueueItem {
  id: string;
  state: 'active' | 'syncing' | 'queued' | 'done';
  betsRemaining: number;
}

interface ProviderQueueState {
  providers: ProviderQueueItem[];
  activeProvider: string | null;
  preSyncing: string[];
}

export function useProviderQueue(): ProviderQueueState {
  const [providers, setProviders] = useState<ProviderQueueItem[]>([]);
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  const [preSyncing, setPreSyncing] = useState<string[]>([]);

  // Bootstrap
  useEffect(() => {
    fetch('/api/mirror/queue')
      .then(r => r.json())
      .then(data => {
        setProviders((data.providers || []).map((p: any) => ({
          id: p.id,
          state: p.state,
          betsRemaining: p.bets_remaining,
        })));
        setActiveProvider(data.active_provider);
        setPreSyncing(data.pre_syncing || []);
      })
      .catch(err => console.error('Bootstrap queue failed:', err));
  }, []);

  // SSE updates
  useEffect(() => {
    const es = new EventSource('/api/mirror/stream/sync');

    es.addEventListener('provider_state', (e) => {
      const data = JSON.parse(e.data);
      setProviders(prev => prev.map(p =>
        p.id === data.provider_id ? { ...p, state: data.state } : p
      ));
      if (data.state === 'active') setActiveProvider(data.provider_id);
    });

    return () => es.close();
  }, []);

  return { providers, activeProvider, preSyncing };
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/usePriceStream.ts frontend/src/hooks/useBettingLane.ts frontend/src/hooks/useProviderQueue.ts
git commit -m "feat(frontend): add usePriceStream, useBettingLane, useProviderQueue hooks"
```

---

## Task 8: Frontend — SyncLane Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/play/SyncLane.tsx`

- [ ] **Step 1: Create SyncLane component**

```tsx
// frontend/src/components/Terminal/pages/play/SyncLane.tsx
import React from 'react';
import { useSyncStream } from '../../../../hooks/useSyncStream';

interface SyncLaneProps {
  providerId: string | null;
  onConfirmSettlements: () => void;
}

export function SyncLane({ providerId, onConfirmSettlements }: SyncLaneProps) {
  const { balance, pendingBets, settlements, notifications, connected } = useSyncStream(providerId);

  return (
    <div className="flex flex-col gap-3 p-3 border-r border-zinc-800 overflow-y-auto" style={{ flex: 1 }}>
      {/* Connection status */}
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
        {connected ? 'Streaming' : 'Disconnected'}
      </div>

      {/* Balance */}
      <div>
        <div className="text-xs font-semibold text-blue-400 mb-1">BALANCE</div>
        <div className="text-xl font-bold text-zinc-200">
          {balance.amount.toLocaleString('sv-SE', { minimumFractionDigits: 0 })}
          <span className="text-sm text-zinc-500 ml-1">{balance.currency}</span>
        </div>
        {balance.updatedAt && (
          <div className="text-[10px] text-zinc-600">
            updated {new Date(balance.updatedAt).toLocaleTimeString()}
          </div>
        )}
      </div>

      {/* Pending bets */}
      <div>
        <div className="text-xs font-semibold text-blue-400 mb-1">
          PENDING BETS ({pendingBets.length})
        </div>
        {pendingBets.length === 0 ? (
          <div className="text-xs text-zinc-600">No pending bets</div>
        ) : (
          <div className="flex flex-col gap-1">
            {pendingBets.map(bet => (
              <div key={bet.id} className="text-xs text-zinc-400 py-0.5 border-b border-zinc-800/50">
                {bet.event_id.split(':').slice(1, 3).join(' v ')} — {bet.outcome} @ {bet.odds} — {bet.stake} SEK
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Settlement gate */}
      {settlements.length > 0 && (
        <div className="border border-orange-500/50 rounded p-2 bg-orange-950/20">
          <div className="text-xs font-semibold text-orange-400 mb-1">
            SETTLEMENTS PENDING ({settlements.length})
          </div>
          {settlements.map(s => (
            <div key={s.id} className="flex justify-between text-xs py-0.5 border-b border-zinc-800/30">
              <span className="text-zinc-400">Bet #{s.bet_id}</span>
              <span className={s.result === 'won' ? 'text-green-400' : s.result === 'lost' ? 'text-red-400' : 'text-zinc-400'}>
                {s.result === 'won' ? '+' : s.result === 'lost' ? '-' : ''}{s.payout.toFixed(0)} SEK
              </span>
            </div>
          ))}
          <button
            onClick={onConfirmSettlements}
            className="mt-2 w-full bg-green-700 hover:bg-green-600 text-white text-xs py-1.5 rounded transition-colors"
          >
            Confirm All
          </button>
        </div>
      )}

      {/* Notifications */}
      <div>
        <div className="text-xs font-semibold text-blue-400 mb-1">NOTIFICATIONS</div>
        <div className="text-xs">
          <span className={notifications.email ? 'text-green-400' : 'text-orange-400'}>
            Email: {notifications.email ? 'muted' : 'pending'}
          </span>
        </div>
        <div className="text-xs">
          <span className={notifications.sms ? 'text-green-400' : 'text-orange-400'}>
            SMS: {notifications.sms ? 'muted' : 'pending'}
          </span>
        </div>
        <div className="text-xs">
          <span className={notifications.push ? 'text-green-400' : 'text-orange-400'}>
            Push: {notifications.push ? 'muted' : 'pending'}
          </span>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/SyncLane.tsx
git commit -m "feat(frontend): add SyncLane component"
```

---

## Task 9: Frontend — BettingLane Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/play/BettingLane.tsx`

- [ ] **Step 1: Create BettingLane component**

```tsx
// frontend/src/components/Terminal/pages/play/BettingLane.tsx
import React from 'react';
import { useBettingLane } from '../../../../hooks/useBettingLane';
import { usePriceStream } from '../../../../hooks/usePriceStream';

interface BettingLaneProps {
  providerId: string | null;
}

export function BettingLane({ providerId }: BettingLaneProps) {
  const { currentBet, upNext, status, placeBet, skipBet } = useBettingLane(providerId);
  const prices = usePriceStream(providerId, currentBet?.bet_id ?? null);

  if (!currentBet) {
    return (
      <div className="flex items-center justify-center flex-1 p-3">
        <div className="text-zinc-600 text-sm">
          {status === 'idle' ? 'No bets remaining' : 'Loading...'}
        </div>
      </div>
    );
  }

  const edgeColor = (currentBet.edge_pct ?? 0) >= 3 ? 'text-green-400' :
                    (currentBet.edge_pct ?? 0) >= 0 ? 'text-yellow-400' : 'text-red-400';

  return (
    <div className="flex flex-col gap-3 p-3 overflow-y-auto" style={{ flex: 1.2 }}>
      {/* Current bet */}
      <div className="pb-3 border-b border-zinc-800">
        <div className="text-xs font-semibold text-blue-400 mb-2">
          CURRENT BET
        </div>
        <div className="text-base font-semibold text-zinc-200 mb-1">
          {currentBet.display_home} vs {currentBet.display_away}
        </div>
        <div className="text-xs text-zinc-500 mb-3">
          {currentBet.sport} {currentBet.league && `· ${currentBet.league}`}
          {currentBet.start_time && ` · ${new Date(currentBet.start_time).toLocaleString('sv-SE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`}
        </div>

        <div className="grid grid-cols-4 gap-3 mb-3">
          <div>
            <div className="text-[9px] text-zinc-600">MARKET</div>
            <div className="text-xs text-zinc-300">{currentBet.market} → {currentBet.outcome}</div>
          </div>
          <div>
            <div className="text-[9px] text-zinc-600">ODDS</div>
            <div className="text-xs text-zinc-300">{currentBet.odds.toFixed(2)}</div>
          </div>
          <div>
            <div className="text-[9px] text-zinc-600">FAIR</div>
            <div className="text-xs text-zinc-500">{currentBet.fair_odds?.toFixed(2) ?? '—'}</div>
          </div>
          <div>
            <div className="text-[9px] text-zinc-600">EDGE</div>
            <div className={`text-xs font-semibold ${edgeColor}`}>
              {currentBet.edge_pct >= 0 ? '+' : ''}{currentBet.edge_pct.toFixed(1)}%
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3 mb-3">
          <div>
            <div className="text-[9px] text-zinc-600">STAKE</div>
            <div className="text-xs text-zinc-300">{currentBet.stake} SEK</div>
          </div>
          {currentBet.kelly_pct != null && (
            <div>
              <div className="text-[9px] text-zinc-600">KELLY</div>
              <div className="text-xs text-zinc-300">{currentBet.kelly_pct.toFixed(1)}%</div>
            </div>
          )}
          {currentBet.point != null && (
            <div>
              <div className="text-[9px] text-zinc-600">LINE</div>
              <div className="text-xs text-zinc-300">{currentBet.point > 0 ? '+' : ''}{currentBet.point}</div>
            </div>
          )}
        </div>
      </div>

      {/* Price stream */}
      <div className="border border-purple-500/30 rounded p-2 bg-purple-950/10">
        <div className="text-xs font-semibold text-purple-400 mb-1">LIVE PRICE STREAM</div>
        <div className="flex gap-4 text-xs">
          <div>
            <span className="text-zinc-500">DOM: </span>
            <span className="text-zinc-300 font-semibold">{prices.domOdds?.toFixed(2) ?? '—'}</span>
            {prices.priceMatch && <span className="text-green-400 ml-1">✓</span>}
          </div>
          <div>
            <span className="text-zinc-500">API: </span>
            <span className="text-zinc-300">{prices.apiOdds?.toFixed(2) ?? '—'}</span>
          </div>
          <div>
            <span className="text-zinc-500">Fair: </span>
            <span className="text-zinc-300">{prices.fairOdds?.toFixed(2) ?? '—'}</span>
          </div>
        </div>
        {prices.lastUpdate && (
          <div className="text-[9px] text-zinc-600 mt-1">
            updated {prices.lastUpdate.toLocaleTimeString()}
          </div>
        )}
      </div>

      {/* Status */}
      <div>
        <div className="text-xs font-semibold text-blue-400 mb-1">STATUS</div>
        <div className="text-xs">
          <span className={status === 'navigating' || status === 'filling' || status === 'ready' ? 'text-green-400' : 'text-zinc-600'}>
            {status === 'navigating' ? '⟳ Navigating...' :
             status === 'filling' ? '⟳ Auto-filling...' :
             status === 'ready' ? '✓ Ready to place' :
             status === 'placing' ? '⟳ Placing...' : '—'}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={placeBet}
          disabled={status !== 'ready'}
          className="flex-1 bg-green-700 hover:bg-green-600 disabled:bg-zinc-800 disabled:text-zinc-600 text-white py-2 rounded text-sm font-semibold transition-colors"
        >
          Place Bet
        </button>
        <button
          onClick={skipBet}
          disabled={status === 'placing'}
          className="flex-1 bg-zinc-800 hover:bg-zinc-700 disabled:text-zinc-600 text-zinc-400 py-2 rounded text-sm font-semibold transition-colors"
        >
          Skip
        </button>
      </div>

      {/* Up next */}
      {upNext.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-blue-400 mb-1">UP NEXT</div>
          {upNext.slice(0, 5).map((bet, i) => (
            <div key={i} className="text-xs text-zinc-500 py-0.5">
              {bet.display_home} v {bet.display_away} — {bet.outcome} @ {bet.odds.toFixed(2)} — edge {bet.edge_pct >= 0 ? '+' : ''}{bet.edge_pct.toFixed(1)}%
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/BettingLane.tsx
git commit -m "feat(frontend): add BettingLane component"
```

---

## Task 10: Frontend — Two-Lane FireWindowPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/play/FireWindow.tsx`

- [ ] **Step 1: Read the current FireWindow.tsx to understand its props and integration**

Run: Read `frontend/src/components/Terminal/pages/play/FireWindow.tsx` lines 1–50 for props interface and imports.

- [ ] **Step 2: Create the two-lane layout**

Replace the content of `FireWindow.tsx` with the two-lane layout. Keep the same props interface so parent components don't need changes:

```tsx
// frontend/src/components/Terminal/pages/play/FireWindow.tsx
import React, { useState, useEffect, useCallback } from 'react';
import { useProviderQueue } from '../../../../hooks/useProviderQueue';
import { SyncLane } from './SyncLane';
import { BettingLane } from './BettingLane';

interface FireWindowProps {
  batch: any;
  onComplete: () => void;
  onBack: () => void;
  onNewBatch?: () => void;
}

export default function FireWindow({ batch, onComplete, onBack, onNewBatch }: FireWindowProps) {
  const { providers, activeProvider, preSyncing } = useProviderQueue();
  const [isOpen, setIsOpen] = useState(false);

  // Open fire window on mount
  useEffect(() => {
    if (!isOpen && batch) {
      fetch('/api/fire-window/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(batch),
      })
        .then(r => r.json())
        .then(() => setIsOpen(true))
        .catch(err => console.error('Failed to open fire window:', err));
    }
  }, [batch, isOpen]);

  const handleConfirmSettlements = useCallback(async () => {
    if (!activeProvider) return;
    try {
      await fetch('/api/mirror/settlements/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider_id: activeProvider }),
      });
    } catch (err) {
      console.error('Settlement confirmation failed:', err);
    }
  }, [activeProvider]);

  return (
    <div className="flex flex-col h-full">
      {/* Provider queue bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 overflow-x-auto">
        {providers.map(p => (
          <div
            key={p.id}
            className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs whitespace-nowrap ${
              p.id === activeProvider
                ? 'bg-zinc-800 text-zinc-200'
                : p.state === 'done'
                ? 'text-zinc-600'
                : 'text-zinc-500'
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${
              p.state === 'active' ? 'bg-green-500' :
              p.state === 'syncing' ? 'bg-blue-500 animate-pulse' :
              p.state === 'done' ? 'bg-zinc-600' :
              'bg-zinc-700'
            }`} />
            {p.id}
            {p.betsRemaining > 0 && (
              <span className="text-zinc-600">({p.betsRemaining})</span>
            )}
          </div>
        ))}
        <div className="ml-auto flex gap-2">
          <button onClick={onBack} className="text-xs text-zinc-600 hover:text-zinc-400">Back</button>
          <button onClick={onComplete} className="text-xs text-zinc-600 hover:text-zinc-400">Close</button>
        </div>
      </div>

      {/* Two-lane layout */}
      <div className="flex flex-1 min-h-0">
        <SyncLane
          providerId={activeProvider}
          onConfirmSettlements={handleConfirmSettlements}
        />
        <BettingLane
          providerId={activeProvider}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify the build compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors (or only pre-existing ones)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/FireWindow.tsx
git commit -m "feat(frontend): replace FireWindow with two-lane layout"
```

---

## Task 11: Integration Test — Full Flow

**Files:**
- Test: `backend/tests/test_two_lane_integration.py`

- [ ] **Step 1: Write integration test**

```python
# backend/tests/test_two_lane_integration.py
"""Integration test: EventRouter → DB → SSE channel flow."""
import pytest
from unittest.mock import MagicMock

from src.mirror.event_router import EventRouter
from src.mirror.channels import sync_channel, price_channel, action_channel


@pytest.mark.asyncio
async def test_balance_route_persists_and_broadcasts():
    """EventRouter.route('balance') should persist to DB and broadcast to sync channel."""
    router = EventRouter()

    # Subscribe to sync channel
    client_id, queue = sync_channel.subscribe()
    try:
        await router.route(
            provider_id="betsson",
            category="balance",
            url="https://example.com/api/account/balance",
            response_body={"balance": 1500.0},
        )
        msg = queue.get_nowait()
        assert msg["event"] == "balance_update"
        assert msg["data"]["provider_id"] == "betsson"
        assert msg["data"]["amount"] == 1500.0
    finally:
        sync_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_action_broadcast():
    """EventRouter.broadcast_action should push to action channel."""
    router = EventRouter()

    client_id, queue = action_channel.subscribe()
    try:
        router.broadcast_action("navigated", {"bet_id": 42, "event_url": "/match/123"})
        msg = queue.get_nowait()
        assert msg["event"] == "navigated"
        assert msg["data"]["bet_id"] == 42
    finally:
        action_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_classify_and_route_full_flow():
    """Full flow: classify URL → route → broadcast."""
    router = EventRouter()

    client_id, queue = sync_channel.subscribe()
    try:
        url = "https://sb2frontend-altenar2.bfrndz.com/api/account/balance"
        category = router.classify(url, {"balance": 999.0})
        assert category == "balance"

        await router.route("betinia", category, url, {"balance": 999.0})
        msg = queue.get_nowait()
        assert msg["data"]["provider_id"] == "betinia"
        assert msg["data"]["amount"] == 999.0
    finally:
        sync_channel.unsubscribe(client_id)
```

- [ ] **Step 2: Run test**

Run: `cd backend && pytest tests/test_two_lane_integration.py -v`
Expected: 3 tests PASS

- [ ] **Step 3: Run all tests**

Run: `cd backend && pytest tests/ -v --timeout=30`
Expected: All tests pass (existing + new)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_two_lane_integration.py
git commit -m "test(mirror): add two-lane integration tests"
```

---

## Task 12: Build Frontend + Smoke Test

- [ ] **Step 1: Build frontend**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 2: Visual smoke test**

Open the app in browser, navigate to the fire window / play page. Verify:
- Two-lane layout renders (sync left, betting right)
- Provider queue bar shows at top
- SSE connections establish (check Network tab for `/api/mirror/stream/*`)

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: two-lane fire window — sync + betting lanes with SSE streaming"
```
