# Fire Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-by-provider fire window that opens live tabs, streams price comparisons against sharp fair odds, and lets the user confirm firing +EV bets per provider.

**Architecture:** New `FireWindowService` holds session state (provider queue, live snapshots). API routes expose open/activate/state/fire/skip/close. Frontend replaces the ExecutionPanel with a step-by-step provider wizard that polls live state every 3s.

**Tech Stack:** Python/FastAPI (backend service + routes), React/TypeScript (wizard UI), existing MirrorService for tab management + DOM price reading.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/src/services/fire_window.py` | FireWindowService — session state, poll loop, fire logic |
| Create | `backend/src/api/routes/fire_window.py` | API routes: open/activate/state/fire/skip/close/queue |
| Modify | `backend/src/api/routes/__init__.py` | Register fire_window_router |
| Modify | `backend/src/api/__init__.py` | include_router for fire_window |
| Create | `frontend/src/services/api/fireWindow.ts` | Frontend API client for fire-window endpoints |
| Modify | `frontend/src/services/api/index.ts` | Re-export fire window API |
| Create | `frontend/src/components/Terminal/pages/play/FireWindow.tsx` | Provider wizard UI component |
| Modify | `frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx` | Replace body with FireWindow |

---

## Task 1: FireWindowService — Core State & Open

**Files:**
- Create: `backend/src/services/fire_window.py`

- [ ] **Step 1: Create the service with data structures and open_window()**

```python
"""Fire Window — provider-by-provider batch execution with live price comparison."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..analysis.value import compute_edge

logger = logging.getLogger(__name__)


@dataclass
class LiveSnapshot:
    """Live price snapshot for a single bet."""
    bet_id: int
    live_odds: float | None = None
    fair_odds: float | None = None
    live_edge: float | None = None
    original_edge: float = 0.0
    delta: float = 0.0
    category: str = "pending"  # improved | stable | degraded | negative | pending
    last_updated: datetime | None = None


@dataclass
class FireWindowBet:
    """A bet in the fire window, enriched from BatchBet."""
    bet_id: int
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None
    odds: float
    fair_odds: float
    edge_pct: float
    stake: float
    expected_profit: float
    display_home: str
    display_away: str
    sport: str
    tier: str
    # Polymarket-specific
    market_slug: str | None = None
    poly_outcome: str | None = None
    original_outcome: str | None = None


@dataclass
class FireWindow:
    """Active fire window session."""
    provider_queue: list[str]
    provider_bets: dict[str, list[FireWindowBet]]
    current_provider: str | None = None
    live_snapshots: dict[int, LiveSnapshot] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "ready"  # ready | active | firing | complete
    _poll_task: Optional[asyncio.Task] = field(default=None, repr=False)
    fired_results: dict[str, dict] = field(default_factory=dict)


# Singleton — one window at a time
_window: FireWindow | None = None


def get_window() -> FireWindow | None:
    return _window


def open_window(batch: list[dict], provider_order: list[str] | None = None) -> dict:
    """Build fire window from an allocated batch.

    Args:
        batch: List of BatchBet dicts (from allocate_capital response).
        provider_order: Optional ordering. Defaults to polymarket first, then by EV desc.

    Returns window summary with provider queue.
    """
    global _window

    # Close existing window if any
    if _window and _window._poll_task:
        _window._poll_task.cancel()

    # Group bets by provider
    provider_bets: dict[str, list[FireWindowBet]] = {}
    for i, b in enumerate(batch):
        pid = b["provider_id"]
        if not b.get("funded", True):
            continue  # Skip unfunded bets

        # Resolve Polymarket metadata
        market_slug = None
        poly_outcome = None
        if pid == "polymarket":
            market_slug = b.get("_market_slug") or b.get("market_slug")
            poly_outcome = b.get("_poly_outcome") or b.get("poly_outcome")

        fw_bet = FireWindowBet(
            bet_id=i,
            provider_id=pid,
            event_id=b["event_id"],
            market=b["market"],
            outcome=b["outcome"],
            point=b.get("point"),
            odds=b["odds"],
            fair_odds=b["fair_odds"],
            edge_pct=b["edge_pct"],
            stake=b["stake"],
            expected_profit=b["expected_profit"],
            display_home=b["display_home"],
            display_away=b["display_away"],
            sport=b["sport"],
            tier=b["tier"],
            market_slug=market_slug,
            poly_outcome=poly_outcome,
            original_outcome=b["outcome"],
        )
        provider_bets.setdefault(pid, []).append(fw_bet)

    # Determine provider order: polymarket first, then pinnacle, then soft by total EV desc
    if provider_order is None:
        tier_sort = {"polymarket": 0, "pinnacle": 1}
        provider_order = sorted(
            provider_bets.keys(),
            key=lambda p: (
                tier_sort.get(provider_bets[p][0].tier, 2),
                -sum(b.expected_profit for b in provider_bets[p]),
            ),
        )

    _window = FireWindow(
        provider_queue=provider_order,
        provider_bets=provider_bets,
    )

    return _build_queue_response()


def _build_queue_response() -> dict:
    """Build the queue response for the current window."""
    if not _window:
        return {"status": "no_window"}

    queue = []
    for pid in _window.provider_queue:
        bets = _window.provider_bets.get(pid, [])
        queue.append({
            "provider_id": pid,
            "bet_count": len(bets),
            "total_stake": round(sum(b.stake for b in bets), 2),
            "total_ev": round(sum(b.expected_profit for b in bets), 2),
            "tier": bets[0].tier if bets else "unknown",
            "fired": pid in _window.fired_results,
        })

    return {
        "status": _window.status,
        "current_provider": _window.current_provider,
        "queue": queue,
        "created_at": _window.created_at.isoformat(),
    }


def close_window():
    """Tear down the fire window."""
    global _window
    if _window and _window._poll_task:
        _window._poll_task.cancel()
    _window = None
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/fire_window.py
git commit -m "feat(fire-window): add FireWindowService with core state and open_window"
```

---

## Task 2: FireWindowService — Activate Provider & Live Polling

**Files:**
- Modify: `backend/src/services/fire_window.py`

- [ ] **Step 1: Add activate_provider() and the poll loop**

Append to `fire_window.py`:

```python
async def activate_provider(provider_id: str, mirror_service) -> dict:
    """Activate a provider: open tabs (for Polymarket) and start live price polling.

    Args:
        provider_id: The provider to activate.
        mirror_service: MirrorService instance for tab management.

    Returns initial bet state with snapshots.
    """
    if not _window:
        return {"error": "No fire window open"}

    if provider_id not in _window.provider_bets:
        return {"error": f"Provider '{provider_id}' not in queue"}

    # Stop previous poll if any
    if _window._poll_task and not _window._poll_task.done():
        _window._poll_task.cancel()
        try:
            await _window._poll_task
        except (asyncio.CancelledError, Exception):
            pass

    _window.current_provider = provider_id
    _window.status = "active"
    bets = _window.provider_bets[provider_id]

    # Initialize snapshots with batch-time values
    for bet in bets:
        _window.live_snapshots[bet.bet_id] = LiveSnapshot(
            bet_id=bet.bet_id,
            fair_odds=bet.fair_odds,
            original_edge=bet.edge_pct,
            live_odds=bet.odds,
            live_edge=bet.edge_pct,
            delta=0.0,
            category="stable",
            last_updated=datetime.now(timezone.utc),
        )

    # For Polymarket: open tabs and start polling
    if provider_id == "polymarket":
        # Build bet dicts for _ensure_poly_tabs
        tab_bets = []
        for bet in bets:
            if bet.market_slug:
                tab_bets.append({
                    "market_slug": bet.market_slug,
                    "outcome": bet.poly_outcome or bet.outcome,
                    "_original_outcome": bet.original_outcome,
                    "_market_type": bet.market,
                })
        if tab_bets:
            await mirror_service._ensure_poly_tabs(tab_bets)

        # Start background poll loop
        _window._poll_task = asyncio.create_task(
            _poll_loop(provider_id, mirror_service),
            name=f"fire-window-poll-{provider_id}",
        )

    return get_live_state()


async def _poll_loop(provider_id: str, mirror_service):
    """Background loop: read live prices from open tabs every 3s."""
    while _window and _window.current_provider == provider_id and _window.status == "active":
        try:
            await _update_live_prices(provider_id, mirror_service)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[FireWindow] Poll error for {provider_id}: {e}")
        await asyncio.sleep(3)


async def _update_live_prices(provider_id: str, mirror_service):
    """Read live prices from mirror tabs and update snapshots."""
    if not _window or provider_id != _window.current_provider:
        return

    bets = _window.provider_bets.get(provider_id, [])
    now = datetime.now(timezone.utc)

    if provider_id == "polymarket":
        for bet in bets:
            slug = bet.market_slug
            if not slug:
                continue
            page = mirror_service._poly_tabs.get(slug)
            if page is None or page.is_closed():
                continue

            try:
                btn_data = await mirror_service._read_btn_prices(page)
            except Exception:
                continue

            btn_index = mirror_service._btn_index_for_outcome(
                bet.original_outcome or bet.outcome, bet.market
            )
            if btn_index >= len(btn_data) or btn_data[btn_index]["price"] is None:
                continue

            live_price = btn_data[btn_index]["price"]
            live_odds = round(1 / live_price, 2) if live_price > 0.01 else 999.0
            live_edge = compute_edge(provider_id, live_odds, bet.fair_odds)

            snap = _window.live_snapshots.get(bet.bet_id)
            if snap:
                snap.live_odds = live_odds
                snap.live_edge = live_edge
                snap.delta = (live_edge or 0) - snap.original_edge
                snap.last_updated = now
                # Categorize
                if live_edge is None or live_edge <= 0:
                    snap.category = "negative"
                elif snap.delta > 1:
                    snap.category = "improved"
                elif snap.delta < -1:
                    snap.category = "degraded"
                else:
                    snap.category = "stable"


def get_live_state() -> dict:
    """Return current provider's bets with live snapshots."""
    if not _window or not _window.current_provider:
        return {"error": "No active provider"}

    provider_id = _window.current_provider
    bets = _window.provider_bets.get(provider_id, [])
    position = _window.provider_queue.index(provider_id) + 1 if provider_id in _window.provider_queue else 0
    total_providers = len(_window.provider_queue)

    bet_states = []
    for bet in bets:
        snap = _window.live_snapshots.get(bet.bet_id)
        bet_states.append({
            "bet_id": bet.bet_id,
            "event_id": bet.event_id,
            "display_home": bet.display_home,
            "display_away": bet.display_away,
            "market": bet.market,
            "outcome": bet.outcome,
            "point": bet.point,
            "sport": bet.sport,
            "stake": bet.stake,
            "expected_profit": bet.expected_profit,
            "original_odds": bet.odds,
            "live_odds": snap.live_odds if snap else bet.odds,
            "fair_odds": snap.fair_odds if snap else bet.fair_odds,
            "original_edge": snap.original_edge if snap else bet.edge_pct,
            "live_edge": snap.live_edge if snap else bet.edge_pct,
            "delta": snap.delta if snap else 0.0,
            "category": snap.category if snap else "pending",
            "last_updated": snap.last_updated.isoformat() if snap and snap.last_updated else None,
        })

    # Summary stats
    active_bets = [b for b in bet_states if b["category"] != "negative"]
    excluded_bets = [b for b in bet_states if b["category"] == "negative"]

    return {
        "provider_id": provider_id,
        "tier": bets[0].tier if bets else "unknown",
        "position": position,
        "total_providers": total_providers,
        "status": _window.status,
        "bets": bet_states,
        "summary": {
            "total_bets": len(bet_states),
            "active_bets": len(active_bets),
            "excluded_bets": len(excluded_bets),
            "total_stake": round(sum(b["stake"] for b in active_bets), 2),
            "total_ev": round(sum(b["expected_profit"] for b in active_bets), 2),
        },
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/fire_window.py
git commit -m "feat(fire-window): add activate_provider with live price polling loop"
```

---

## Task 3: FireWindowService — Fire, Skip, Advance

**Files:**
- Modify: `backend/src/services/fire_window.py`

- [ ] **Step 1: Add fire_provider(), skip_provider(), and advance logic**

Append to `fire_window.py`:

```python
async def fire_provider(mirror_service) -> dict:
    """Fire +EV bets for the current provider. Filters out negative-edge bets.

    Returns: {placed: [...], excluded: [...], errors: [...], provider_id, advanced_to}
    """
    if not _window or not _window.current_provider:
        return {"error": "No active provider"}

    # Stop poll loop
    if _window._poll_task and not _window._poll_task.done():
        _window._poll_task.cancel()
        try:
            await _window._poll_task
        except (asyncio.CancelledError, Exception):
            pass

    _window.status = "firing"
    provider_id = _window.current_provider
    bets = _window.provider_bets.get(provider_id, [])

    # Final price update before firing
    await _update_live_prices(provider_id, mirror_service)

    # Split into fireable vs excluded
    to_fire = []
    excluded = []
    for bet in bets:
        snap = _window.live_snapshots.get(bet.bet_id)
        live_edge = snap.live_edge if snap else bet.edge_pct
        if live_edge is not None and live_edge > 0:
            to_fire.append(bet)
        else:
            excluded.append({
                "bet_id": bet.bet_id,
                "event": f"{bet.display_home} vs {bet.display_away}",
                "outcome": bet.outcome,
                "live_edge": round(live_edge, 1) if live_edge is not None else None,
                "reason": "negative_edge",
            })

    placed = []
    errors = []

    if provider_id == "polymarket" and to_fire:
        # Fire via mirror — reuse existing _place_single_polymarket_bet
        for bet in to_fire:
            snap = _window.live_snapshots.get(bet.bet_id)
            slug = bet.market_slug
            if not slug:
                errors.append({"bet_id": bet.bet_id, "reason": "no_market_slug"})
                continue

            page = mirror_service._poly_tabs.get(slug)
            if page is None or page.is_closed():
                errors.append({"bet_id": bet.bet_id, "reason": "tab_closed"})
                continue

            expected_price = round(1 / bet.odds, 4) if bet.odds > 1 else 0.5
            try:
                result = await mirror_service._place_single_polymarket_bet(
                    page=page,
                    bet_id=bet.bet_id,
                    slug=slug,
                    outcome=bet.poly_outcome or bet.outcome,
                    amount=bet.stake,
                    expected_price=expected_price,
                    max_slippage=3.0,
                    original_outcome=bet.original_outcome or bet.outcome,
                    market_type=bet.market,
                )
                if result.get("status") == "placed":
                    result["live_odds"] = snap.live_odds if snap else bet.odds
                    result["fair_odds"] = snap.fair_odds if snap else bet.fair_odds
                    result["live_edge"] = round(snap.live_edge, 1) if snap and snap.live_edge else bet.edge_pct
                    placed.append(result)
                else:
                    errors.append(result)
            except Exception as e:
                errors.append({"bet_id": bet.bet_id, "reason": str(e)})

    # Close tabs for this provider
    if provider_id == "polymarket":
        await mirror_service.close_poly_tabs()

    # Store results and advance
    fire_result = {
        "provider_id": provider_id,
        "placed": placed,
        "excluded": excluded,
        "errors": errors,
        "total": len(bets),
    }
    _window.fired_results[provider_id] = fire_result

    advanced_to = _advance_queue()
    fire_result["advanced_to"] = advanced_to
    return fire_result


def skip_provider() -> dict:
    """Skip current provider without firing. Advance to next."""
    if not _window or not _window.current_provider:
        return {"error": "No active provider"}

    provider_id = _window.current_provider

    # Stop poll loop
    if _window._poll_task and not _window._poll_task.done():
        _window._poll_task.cancel()

    _window.fired_results[provider_id] = {"provider_id": provider_id, "skipped": True}
    advanced_to = _advance_queue()

    return {
        "skipped": provider_id,
        "advanced_to": advanced_to,
        "status": _window.status,
    }


def _advance_queue() -> str | None:
    """Advance to next provider in queue. Returns next provider_id or None if done."""
    if not _window:
        return None

    current_idx = _window.provider_queue.index(_window.current_provider) if _window.current_provider in _window.provider_queue else -1
    next_idx = current_idx + 1

    if next_idx < len(_window.provider_queue):
        next_provider = _window.provider_queue[next_idx]
        _window.current_provider = next_provider
        _window.status = "ready"
        _window.live_snapshots.clear()
        return next_provider
    else:
        _window.current_provider = None
        _window.status = "complete"
        return None


def get_fired_summary() -> dict:
    """Get summary of all fired providers."""
    if not _window:
        return {"error": "No fire window"}

    results = []
    total_placed = 0
    total_excluded = 0
    total_errors = 0

    for pid in _window.provider_queue:
        r = _window.fired_results.get(pid)
        if not r:
            results.append({"provider_id": pid, "status": "pending"})
            continue
        if r.get("skipped"):
            results.append({"provider_id": pid, "status": "skipped"})
            continue
        n_placed = len(r.get("placed", []))
        n_excluded = len(r.get("excluded", []))
        n_errors = len(r.get("errors", []))
        total_placed += n_placed
        total_excluded += n_excluded
        total_errors += n_errors
        results.append({
            "provider_id": pid,
            "status": "fired",
            "placed": n_placed,
            "excluded": n_excluded,
            "errors": n_errors,
        })

    return {
        "providers": results,
        "total_placed": total_placed,
        "total_excluded": total_excluded,
        "total_errors": total_errors,
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/fire_window.py
git commit -m "feat(fire-window): add fire_provider, skip_provider, and queue advancement"
```

---

## Task 4: API Routes

**Files:**
- Create: `backend/src/api/routes/fire_window.py`
- Modify: `backend/src/api/routes/__init__.py`
- Modify: `backend/src/api/__init__.py`

- [ ] **Step 1: Create fire_window routes**

```python
"""Fire Window API routes — provider-by-provider batch execution."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...services import fire_window as fw
from .mirror import _get_active_mirror, _mirrors, _start_lock, _any_running

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fire-window", tags=["fire-window"])


class OpenRequest(BaseModel):
    batch: list[dict]
    provider_order: list[str] | None = None


@router.post("/open")
def open_fire_window(request: OpenRequest):
    """Build fire window from an allocated batch."""
    if not request.batch:
        raise HTTPException(400, "Empty batch")
    return fw.open_window(request.batch, request.provider_order)


@router.post("/activate/{provider_id}")
async def activate_provider(provider_id: str):
    """Open tabs for provider and start live price polling."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")

    # Auto-ensure mirror is started for Polymarket
    mirror = _get_active_mirror()
    if not mirror and provider_id == "polymarket":
        from ...mirror.service import MirrorService
        from ...pipeline.broadcast import odds_broadcaster
        async with _start_lock:
            if not _any_running():
                mirror = MirrorService(broadcaster=odds_broadcaster, provider_id="spelklubben")
                await mirror.start()
                _mirrors["spelklubben"] = mirror
            else:
                mirror = _get_active_mirror()

    if not mirror:
        raise HTTPException(400, "Could not start mirror browser")

    return await fw.activate_provider(provider_id, mirror)


@router.get("/state")
def get_state():
    """Get current provider's live bet states + delta."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    return fw.get_live_state()


@router.post("/fire")
async def fire_current_provider():
    """Fire +EV bets for current provider, advance to next."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider to fire")

    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    return await fw.fire_provider(mirror)


@router.post("/skip")
def skip_current_provider():
    """Skip current provider, advance to next."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider to skip")
    return fw.skip_provider()


@router.get("/queue")
def get_queue():
    """Get the provider queue with status."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    return fw._build_queue_response()


@router.post("/close")
async def close_fire_window():
    """Close fire window, cleanup tabs."""
    mirror = _get_active_mirror()
    if mirror:
        await mirror.close_poly_tabs()
    fw.close_window()
    return {"status": "closed"}


@router.get("/summary")
def get_summary():
    """Get summary of all fired providers."""
    return fw.get_fired_summary()
```

- [ ] **Step 2: Register the router in `__init__.py`**

In `backend/src/api/routes/__init__.py`, add:
```python
from .fire_window import router as fire_window_router
```

And add `'fire_window_router'` to `__all__`.

In `backend/src/api/__init__.py`, add after the `mirror_router` line:
```python
app.include_router(fire_window_router)
```

And add the import:
```python
from .routes import fire_window_router
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/fire_window.py backend/src/api/routes/__init__.py backend/src/api/__init__.py
git commit -m "feat(fire-window): add API routes for fire window"
```

---

## Task 5: Wire Polymarket Metadata into Batch

The fire window needs `market_slug` and `poly_outcome` on each Polymarket bet. The current BatchBet doesn't carry these. We need to resolve them when opening the window.

**Files:**
- Modify: `backend/src/services/fire_window.py`

- [ ] **Step 1: Add Polymarket metadata resolution to open_window()**

Add a helper function before `open_window()`:

```python
def _resolve_polymarket_meta(bets: list[dict]) -> dict[str, dict]:
    """Resolve Polymarket slugs and outcomes from DB for batch bets.

    Returns: {event_id: {market_slug, poly_outcome_map: {outcome: display_name}}}
    """
    from ..db.models import Odds, get_session

    poly_bets = [b for b in bets if b.get("provider_id") == "polymarket"]
    if not poly_bets:
        return {}

    event_ids = list({b["event_id"] for b in poly_bets})
    meta_map = {}

    with get_session() as session:
        rows = session.query(Odds).filter(
            Odds.event_id.in_(event_ids),
            Odds.provider_id == "polymarket",
        ).all()

        for row in rows:
            pm = row.provider_meta if isinstance(row.provider_meta, dict) else {}
            slug = pm.get("event_slug", "")
            if not slug:
                continue

            if row.event_id not in meta_map:
                meta_map[row.event_id] = {"market_slug": slug, "poly_outcome_map": {}}

            # Map outcome to Polymarket display name
            poly_home = pm.get("poly_home", "")
            poly_away = pm.get("poly_away", "")
            outcome = row.outcome
            if outcome == "home" and poly_home:
                meta_map[row.event_id]["poly_outcome_map"]["home"] = poly_home
            elif outcome == "away" and poly_away:
                meta_map[row.event_id]["poly_outcome_map"]["away"] = poly_away
            elif outcome == "draw":
                meta_map[row.event_id]["poly_outcome_map"]["draw"] = "Draw"
            elif outcome == "over":
                meta_map[row.event_id]["poly_outcome_map"]["over"] = "Over"
            elif outcome == "under":
                meta_map[row.event_id]["poly_outcome_map"]["under"] = "Under"

    return meta_map
```

Then update the Polymarket section of `open_window()` to use it:

```python
    # Resolve Polymarket metadata from DB
    poly_meta = _resolve_polymarket_meta(batch)

    # Group bets by provider
    provider_bets: dict[str, list[FireWindowBet]] = {}
    for i, b in enumerate(batch):
        pid = b["provider_id"]
        if not b.get("funded", True):
            continue

        market_slug = None
        poly_outcome = None
        if pid == "polymarket":
            event_meta = poly_meta.get(b["event_id"], {})
            market_slug = event_meta.get("market_slug")
            poly_outcome = event_meta.get("poly_outcome_map", {}).get(b["outcome"])

        # ... rest of FireWindowBet construction unchanged
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/fire_window.py
git commit -m "feat(fire-window): resolve Polymarket slugs and outcomes from DB on window open"
```

---

## Task 6: Frontend API Client

**Files:**
- Create: `frontend/src/services/api/fireWindow.ts`
- Modify: `frontend/src/services/api/index.ts` (or wherever the api object is assembled)

- [ ] **Step 1: Create the fire window API client**

```typescript
import { fetchJson } from './client';

export interface FireWindowBet {
  bet_id: number;
  event_id: string;
  display_home: string;
  display_away: string;
  market: string;
  outcome: string;
  point: number | null;
  sport: string;
  stake: number;
  expected_profit: number;
  original_odds: number;
  live_odds: number;
  fair_odds: number;
  original_edge: number;
  live_edge: number;
  delta: number;
  category: 'improved' | 'stable' | 'degraded' | 'negative' | 'pending';
  last_updated: string | null;
}

export interface ProviderQueueItem {
  provider_id: string;
  bet_count: number;
  total_stake: number;
  total_ev: number;
  tier: string;
  fired: boolean;
}

export interface LiveState {
  provider_id: string;
  tier: string;
  position: number;
  total_providers: number;
  status: string;
  bets: FireWindowBet[];
  summary: {
    total_bets: number;
    active_bets: number;
    excluded_bets: number;
    total_stake: number;
    total_ev: number;
  };
}

export interface FireResult {
  provider_id: string;
  placed: any[];
  excluded: any[];
  errors: any[];
  total: number;
  advanced_to: string | null;
}

export const fireWindowApi = {
  open(batch: any[], providerOrder?: string[]) {
    return fetchJson<{ status: string; queue: ProviderQueueItem[]; created_at: string }>(
      '/fire-window/open',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch, provider_order: providerOrder }),
      },
    );
  },

  activate(providerId: string) {
    return fetchJson<LiveState>(
      `/fire-window/activate/${providerId}`,
      { method: 'POST' },
      120_000, // 2 min — opening tabs is slow
    );
  },

  getState() {
    return fetchJson<LiveState>('/fire-window/state');
  },

  fire() {
    return fetchJson<FireResult>(
      '/fire-window/fire',
      { method: 'POST' },
      300_000, // 5 min — sequential bet placement
    );
  },

  skip() {
    return fetchJson<{ skipped: string; advanced_to: string | null; status: string }>(
      '/fire-window/skip',
      { method: 'POST' },
    );
  },

  getQueue() {
    return fetchJson<{ status: string; queue: ProviderQueueItem[]; current_provider: string | null }>(
      '/fire-window/queue',
    );
  },

  close() {
    return fetchJson<{ status: string }>(
      '/fire-window/close',
      { method: 'POST' },
    );
  },

  getSummary() {
    return fetchJson<{ providers: any[]; total_placed: number; total_excluded: number; total_errors: number }>(
      '/fire-window/summary',
    );
  },
};
```

- [ ] **Step 2: Export from api index**

Check how the existing `api` object is structured (likely in `frontend/src/services/api/index.ts` or assembled in `settings.ts`) and add:

```typescript
export { fireWindowApi } from './fireWindow';
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api/fireWindow.ts frontend/src/services/api/index.ts
git commit -m "feat(fire-window): add frontend API client for fire window endpoints"
```

---

## Task 7: FireWindow UI Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/play/FireWindow.tsx`

- [ ] **Step 1: Create the provider wizard component**

```tsx
import { useState, useEffect, useCallback, useRef } from 'react';
import { ProviderName } from '../../../ProviderName';
import { fireWindowApi } from '@/services/api/fireWindow';
import type { LiveState, FireWindowBet, ProviderQueueItem, FireResult } from '@/services/api/fireWindow';

interface Props {
  batch: any[];
  onComplete: () => void;
  onBack: () => void;
}

type Phase = 'queue' | 'activating' | 'monitoring' | 'firing' | 'result' | 'complete';

const CATEGORY_CLASSES: Record<string, string> = {
  improved: 'text-success',
  stable: 'text-muted',
  degraded: 'text-warning',
  negative: 'text-danger line-through opacity-50',
  pending: 'text-muted animate-pulse',
};

const POLL_INTERVAL = 3000;

export function FireWindow({ batch, onComplete, onBack }: Props) {
  const [queue, setQueue] = useState<ProviderQueueItem[]>([]);
  const [liveState, setLiveState] = useState<LiveState | null>(null);
  const [phase, setPhase] = useState<Phase>('queue');
  const [fireResult, setFireResult] = useState<FireResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<any>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Open fire window on mount
  useEffect(() => {
    fireWindowApi.open(batch).then((res) => {
      setQueue(res.queue);
    }).catch((err) => setError(err.message));

    return () => {
      // Cleanup on unmount
      stopPolling();
      fireWindowApi.close().catch(() => {});
    };
  }, []); // batch is stable from parent

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const state = await fireWindowApi.getState();
        setLiveState(state);
      } catch {
        // Polling error — ignore, next tick will retry
      }
    }, POLL_INTERVAL);
  }, [stopPolling]);

  const handleActivate = useCallback(async (providerId: string) => {
    setPhase('activating');
    setError(null);
    try {
      const state = await fireWindowApi.activate(providerId);
      setLiveState(state);
      setPhase('monitoring');
      startPolling();
    } catch (err: any) {
      setError(err.message);
      setPhase('queue');
    }
  }, [startPolling]);

  const handleFire = useCallback(async () => {
    stopPolling();
    setPhase('firing');
    setError(null);
    try {
      const result = await fireWindowApi.fire();
      setFireResult(result);
      setPhase('result');

      // Refresh queue
      const q = await fireWindowApi.getQueue();
      setQueue(q.queue);

      if (q.status === 'complete') {
        const s = await fireWindowApi.getSummary();
        setSummary(s);
        setPhase('complete');
      }
    } catch (err: any) {
      setError(err.message);
      setPhase('monitoring');
      startPolling();
    }
  }, [stopPolling, startPolling]);

  const handleSkip = useCallback(async () => {
    stopPolling();
    try {
      const result = await fireWindowApi.skip();
      const q = await fireWindowApi.getQueue();
      setQueue(q.queue);

      if (q.status === 'complete') {
        const s = await fireWindowApi.getSummary();
        setSummary(s);
        setPhase('complete');
      } else {
        setLiveState(null);
        setPhase('queue');
      }
    } catch (err: any) {
      setError(err.message);
    }
  }, [stopPolling]);

  const handleAdvance = useCallback(async () => {
    if (fireResult?.advanced_to) {
      setFireResult(null);
      setLiveState(null);
      setPhase('queue');
    } else {
      const s = await fireWindowApi.getSummary();
      setSummary(s);
      setPhase('complete');
    }
  }, [fireResult]);

  // ── Queue view ──────────────────────────────────────────────────────
  if (phase === 'queue') {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between px-3 py-2 border-b border-border">
          <span className="text-sm font-medium">Fire Window — Select Provider</span>
          <button onClick={onBack} className="text-xs text-muted hover:text-foreground">Back</button>
        </div>
        {error && <div className="px-3 text-xs text-danger">{error}</div>}
        {queue.map((p) => (
          <button
            key={p.provider_id}
            onClick={() => !p.fired && handleActivate(p.provider_id)}
            disabled={p.fired}
            className={`w-full flex items-center justify-between px-3 py-2 border border-border hover:bg-panel2/50 transition-colors ${p.fired ? 'opacity-50' : ''}`}
          >
            <div className="flex items-center gap-2">
              <ProviderName id={p.provider_id} />
              <span className="text-xs text-muted">{p.bet_count} bets</span>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span>${p.total_stake.toFixed(0)} {p.tier === 'polymarket' ? 'USDC' : 'SEK'}</span>
              <span className="text-success">+{p.total_ev.toFixed(1)} EV</span>
              {p.fired && <span className="text-muted">done</span>}
            </div>
          </button>
        ))}
      </div>
    );
  }

  // ── Activating ──────────────────────────────────────────────────────
  if (phase === 'activating') {
    return (
      <div className="flex items-center justify-center py-12">
        <span className="text-sm text-muted animate-pulse">Opening tabs...</span>
      </div>
    );
  }

  // ── Monitoring (live prices) ────────────────────────────────────────
  if (phase === 'monitoring' && liveState) {
    const { bets, summary: s, position, total_providers, provider_id } = liveState;
    const activeBets = bets.filter(b => b.category !== 'negative');

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between px-3 py-2 border-b border-border">
          <div className="flex items-center gap-2">
            <ProviderName id={provider_id} />
            <span className="text-xs text-muted">{position} of {total_providers}</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleSkip} className="text-xs px-2 py-1 border border-border hover:bg-panel2/50">
              Skip
            </button>
            <button
              onClick={handleFire}
              className="text-xs px-3 py-1 bg-success/20 text-success border border-success/30 hover:bg-success/30"
            >
              Fire {s.active_bets} bets
            </button>
          </div>
        </div>

        {error && <div className="px-3 text-xs text-danger">{error}</div>}

        <table className="w-full text-xs sq">
          <thead>
            <tr className="text-muted border-b border-border">
              <th className="text-left px-2 py-1">Event</th>
              <th className="text-left px-2 py-1">Mkt</th>
              <th className="text-right px-2 py-1">Stake</th>
              <th className="text-right px-2 py-1">Orig</th>
              <th className="text-right px-2 py-1">Live</th>
              <th className="text-right px-2 py-1">Fair</th>
              <th className="text-right px-2 py-1">Edge%</th>
              <th className="text-right px-2 py-1">Delta</th>
            </tr>
          </thead>
          <tbody>
            {bets.map((bet) => (
              <tr key={bet.bet_id} className={`border-b border-border/30 ${CATEGORY_CLASSES[bet.category]}`}>
                <td className="px-2 py-1 truncate max-w-[180px]">
                  {bet.display_home} v {bet.display_away}
                </td>
                <td className="px-2 py-1">{bet.outcome}{bet.point ? ` ${bet.point}` : ''}</td>
                <td className="text-right px-2 py-1">{bet.stake.toFixed(0)}</td>
                <td className="text-right px-2 py-1">{bet.original_odds.toFixed(2)}</td>
                <td className="text-right px-2 py-1 font-mono">{bet.live_odds.toFixed(2)}</td>
                <td className="text-right px-2 py-1">{bet.fair_odds.toFixed(2)}</td>
                <td className="text-right px-2 py-1 font-mono">
                  {bet.live_edge !== null ? `${bet.live_edge.toFixed(1)}%` : '—'}
                </td>
                <td className="text-right px-2 py-1 font-mono">
                  {bet.delta > 0 ? '+' : ''}{bet.delta.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="flex items-center justify-between px-3 py-2 text-xs text-muted border-t border-border">
          <span>{s.active_bets} active, {s.excluded_bets} excluded</span>
          <span>Stake: {s.total_stake.toFixed(0)} | EV: +{s.total_ev.toFixed(1)}</span>
        </div>
      </div>
    );
  }

  // ── Firing ──────────────────────────────────────────────────────────
  if (phase === 'firing') {
    return (
      <div className="flex items-center justify-center py-12">
        <span className="text-sm text-warning animate-pulse">Firing bets...</span>
      </div>
    );
  }

  // ── Result ──────────────────────────────────────────────────────────
  if (phase === 'result' && fireResult) {
    return (
      <div className="space-y-3 px-3 py-4">
        <div className="text-sm font-medium">
          <ProviderName id={fireResult.provider_id} /> — Results
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="border border-success/30 bg-success/10 p-2 rounded">
            <div className="text-success font-mono text-lg">{fireResult.placed.length}</div>
            <div className="text-muted">Placed</div>
          </div>
          <div className="border border-warning/30 bg-warning/10 p-2 rounded">
            <div className="text-warning font-mono text-lg">{fireResult.excluded.length}</div>
            <div className="text-muted">Excluded</div>
          </div>
          <div className="border border-danger/30 bg-danger/10 p-2 rounded">
            <div className="text-danger font-mono text-lg">{fireResult.errors.length}</div>
            <div className="text-muted">Errors</div>
          </div>
        </div>
        <button
          onClick={handleAdvance}
          className="w-full text-xs px-3 py-2 border border-border hover:bg-panel2/50"
        >
          {fireResult.advanced_to ? `Next: ${fireResult.advanced_to}` : 'View Summary'}
        </button>
      </div>
    );
  }

  // ── Complete ────────────────────────────────────────────────────────
  if (phase === 'complete' && summary) {
    return (
      <div className="space-y-3 px-3 py-4">
        <div className="text-sm font-medium">Fire Window Complete</div>
        <div className="space-y-1 text-xs">
          {summary.providers.map((p: any) => (
            <div key={p.provider_id} className="flex items-center justify-between py-1 border-b border-border/30">
              <ProviderName id={p.provider_id} />
              <span className={p.status === 'fired' ? 'text-success' : 'text-muted'}>
                {p.status === 'fired' ? `${p.placed} placed` : p.status}
              </span>
            </div>
          ))}
        </div>
        <div className="flex items-center justify-between text-xs pt-2 border-t border-border">
          <span>Total: {summary.total_placed} placed, {summary.total_excluded} excluded</span>
        </div>
        <button
          onClick={onComplete}
          className="w-full text-xs px-3 py-2 border border-border hover:bg-panel2/50"
        >
          Done
        </button>
      </div>
    );
  }

  // Fallback
  return (
    <div className="px-3 py-4 text-xs text-muted">
      {error ? <span className="text-danger">{error}</span> : 'Loading fire window...'}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/FireWindow.tsx
git commit -m "feat(fire-window): add provider wizard UI component"
```

---

## Task 8: Wire FireWindow into ExecutionPanel

**Files:**
- Modify: `frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx`

- [ ] **Step 1: Replace ExecutionPanel body with FireWindow**

At the top of `ExecutionPanel.tsx`, add the import:

```typescript
import { FireWindow } from './FireWindow';
```

Replace the current component body. The ExecutionPanel currently renders provider groups with individual ProviderSection components. Replace the return statement to delegate to FireWindow:

```typescript
export default function ExecutionPanel({ batch, wageringProjections, onBack }: Props) {
  const [completed, setCompleted] = useState(false);

  if (completed) {
    return (
      <div className="px-3 py-4 text-xs text-success">
        All providers processed. <button onClick={onBack} className="underline">Back to batch</button>
      </div>
    );
  }

  return (
    <FireWindow
      batch={batch}
      onComplete={() => setCompleted(true)}
      onBack={onBack}
    />
  );
}
```

Remove the now-unused internal components (ProviderSection, groupByProvider helper, etc.) and their related imports (api.fireLive, api.getLiveEdge). Keep the Props interface and betKey/computeBatchHash if used elsewhere, otherwise remove.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx
git commit -m "feat(fire-window): wire FireWindow into ExecutionPanel, remove old provider sections"
```

---

## Task 9: Smoke Test End-to-End

**Files:** None (testing only)

- [ ] **Step 1: Start backend locally and verify routes register**

```bash
cd backend && python -c "from src.api.routes.fire_window import router; print(f'Routes: {[r.path for r in router.routes]}')"
```

Expected output: Routes listing `/open`, `/activate/{provider_id}`, `/state`, `/fire`, `/skip`, `/queue`, `/close`, `/summary`.

- [ ] **Step 2: Test open_window with a mock batch**

```bash
cd backend && python -c "
from src.services.fire_window import open_window
batch = [
    {'provider_id': 'polymarket', 'event_id': 'e1', 'market': 'moneyline', 'outcome': 'home',
     'point': None, 'odds': 2.10, 'fair_odds': 2.00, 'edge_pct': 3.0, 'stake': 25.0,
     'expected_profit': 0.75, 'display_home': 'Team A', 'display_away': 'Team B',
     'sport': 'soccer', 'tier': 'polymarket', 'funded': True},
]
result = open_window(batch)
print(f'Status: {result[\"status\"]}')
print(f'Queue: {result[\"queue\"]}')
"
```

Expected: Status `ready`, queue with 1 provider (polymarket), 1 bet.

- [ ] **Step 3: Verify frontend builds**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: No errors related to fire window files.

- [ ] **Step 4: Commit any fixes from smoke testing**

```bash
git add -u
git commit -m "fix(fire-window): smoke test fixes"
```
