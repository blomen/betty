# Level Battle Station Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chart-centric TradingIntradayPage with a table-based level monitor + orderflow gauge battle screen for real-time trading decisions.

**Architecture:** Backend LevelMonitor class hooks into the existing DatabentoLiveStream tick callback, checks price proximity to session levels, and emits new SSE events (level_approaching, level_touched, orderflow_update, level_rejected). Frontend replaces CandleChart with LevelMonitorTable + BattleScreen components using a new useLevelMonitor hook.

**Tech Stack:** Python/FastAPI (backend), React 19/TypeScript/Tailwind (frontend), SSE via EventSource, existing Databento live stream infrastructure.

**Spec:** `docs/superpowers/specs/2026-03-16-level-battle-station-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `backend/src/market_data/level_monitor.py` | LevelMonitor class — tick callback, proximity detection, status state machine, SSE event emission, position target tracking |
| `frontend/src/hooks/useLevelMonitor.ts` | Hook consuming level_* SSE events, managing battle screen state |
| `frontend/src/components/Terminal/pages/GaugeBar.tsx` | Reusable gauge bar component (fill + value + label + color) |
| `frontend/src/components/Terminal/pages/BattleScreen.tsx` | Battle screen with grouped gauge rows + trade action bar |
| `frontend/src/components/Terminal/pages/LevelMonitorTable.tsx` | Level rows sorted by proximity, status badges |
| `frontend/src/components/Terminal/pages/PositionManager.tsx` | Open positions table with live P&L and target management |
| `frontend/src/hooks/useSound.ts` | Web Audio API sound system for level alerts |

### Modified Files

| File | Changes |
|------|---------|
| `backend/src/market_data/stream.py` | Wire LevelMonitor into DatabentoLiveStream._on_trade() |
| `backend/src/api/routes/market.py` | Add level_* event types to SSE stream endpoint |
| `frontend/src/hooks/useMarketStream.ts` | Expose EventSource ref for shared subscription |
| `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` | Full rewrite — replace chart layout with level table + battle screen + positions |
| `frontend/src/types/market.ts` | Add LevelStatus, BattleScreenData, OrderflowSnapshot types |
| `frontend/src/services/api.ts` | Remove getCandles(), add position management methods |

### Removed Files

| File | Reason |
|------|--------|
| `frontend/src/components/Terminal/pages/CandleChart.tsx` | Replaced by LevelMonitorTable + BattleScreen |
| `frontend/src/components/Terminal/pages/GaugeStrip.tsx` | Replaced by BattleScreen gauge rows |

---

## Chunk 1: Backend — LevelMonitor

### Task 1: LevelMonitor Class — Core State Machine

**Files:**
- Create: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Create LevelMonitor with level loading and distance calculation**

```python
"""Level proximity monitor. Plugs into DatabentoLiveStream as a tick callback."""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

TICK_SIZE = 0.25  # NQ tick size


class LevelStatus(str, Enum):
    WATCHING = "watching"
    APPROACHING = "approaching"
    AT_LEVEL = "at_level"
    TRIGGERED = "triggered"
    REJECTED = "rejected"


@dataclass
class MonitoredLevel:
    """A structural level being tracked for proximity."""
    name: str          # e.g. "VAH", "VWAP +2SD", "PDH"
    price: float
    category: str      # "session", "band", "prior", "structure", "overnight"
    status: LevelStatus = LevelStatus.WATCHING
    touched_at: float = 0.0  # epoch when first touched
    cluster: list[str] = field(default_factory=list)  # confluence level names

    def distance_ticks(self, price: float) -> float:
        return (price - self.price) / TICK_SIZE

    def abs_distance_ticks(self, price: float) -> float:
        return abs(self.distance_ticks(price))


class LevelMonitor:
    """Monitors price proximity to structural levels. Called on each tick."""

    APPROACHING_TICKS = 15
    AT_LEVEL_TICKS = 5
    REJECT_TICKS = 20  # must move beyond this to reject

    def __init__(self, publish_fn):
        """
        Args:
            publish_fn: callable(event_dict) — pushes to SSE subscriber queues.
        """
        self._publish = publish_fn
        self._levels: list[MonitoredLevel] = []
        self._last_orderflow_emit: float = 0.0
        self._orderflow_interval: float = 2.5  # seconds between gauge updates
        self._any_at_level: bool = False

    def load_levels(self, expanded_session: dict) -> None:
        """Load levels from an ExpandedSession dict. Called on compute_session()."""
        self._levels.clear()
        session = expanded_session.get("session", {})
        levels_list = expanded_session.get("levels", [])

        # Build from the unified levels list (already computed by build_expanded_session)
        for lv in levels_list:
            price = lv.get("price_low") or lv.get("price")
            if price is None:
                continue
            name = lv.get("type", "unknown")
            category = self._categorize(name)
            self._levels.append(MonitoredLevel(
                name=name,
                price=float(price),
                category=category,
            ))

        # Also add VWAP bands from session
        for band_name, key in [
            ("VWAP", "vwap"), ("VWAP +1SD", "vwap_1sd_upper"),
            ("VWAP -1SD", "vwap_1sd_lower"), ("VWAP +2SD", "vwap_2sd_upper"),
            ("VWAP -2SD", "vwap_2sd_lower"), ("VWAP +3SD", "vwap_3sd_upper"),
            ("VWAP -3SD", "vwap_3sd_lower"),
        ]:
            val = session.get(key)
            if val is not None:
                # Avoid duplicates if already in levels list
                if not any(l.name == band_name and abs(l.price - val) < TICK_SIZE for l in self._levels):
                    self._levels.append(MonitoredLevel(
                        name=band_name, price=float(val), category="band",
                    ))

        logger.info("LevelMonitor loaded %d levels", len(self._levels))

    @staticmethod
    def _categorize(name: str) -> str:
        name_lower = name.lower()
        if "vwap" in name_lower or "sd" in name_lower:
            return "band"
        if name_lower in ("pdh", "pdl"):
            return "prior"
        if "overnight" in name_lower or name_lower in ("on_high", "on_low"):
            return "overnight"
        if any(k in name_lower for k in ("swing", "naked", "ob", "fvg")):
            return "structure"
        return "session"

    def on_tick(self, price: float, size: int, ts: float) -> None:
        """Called on each trade tick. Checks all levels for proximity transitions."""
        now = time.time()
        at_level_levels = []

        for level in self._levels:
            if level.status == LevelStatus.TRIGGERED:
                continue  # Skip levels where trade was taken

            dist = level.abs_distance_ticks(price)
            old_status = level.status

            # State transitions
            if dist <= self.AT_LEVEL_TICKS:
                if old_status != LevelStatus.AT_LEVEL:
                    level.status = LevelStatus.AT_LEVEL
                    level.touched_at = now
                    self._on_level_touched(level, price)
                at_level_levels.append(level)

            elif dist <= self.APPROACHING_TICKS:
                if old_status == LevelStatus.WATCHING:
                    level.status = LevelStatus.APPROACHING
                    self._on_level_approaching(level, price, dist)
                elif old_status == LevelStatus.AT_LEVEL:
                    # Moving away but still in approaching zone — not rejected yet
                    pass

            elif old_status in (LevelStatus.AT_LEVEL, LevelStatus.APPROACHING):
                if dist > self.REJECT_TICKS:
                    level.status = LevelStatus.REJECTED
                    self._on_level_rejected(level, price)
                    # Reset to watching after a delay
                    level.status = LevelStatus.WATCHING

        # Mark confluence clusters
        if len(at_level_levels) > 1:
            cluster_names = [l.name for l in at_level_levels]
            for l in at_level_levels:
                l.cluster = [n for n in cluster_names if n != l.name]

        # Periodic orderflow updates while any level is AT_LEVEL
        self._any_at_level = bool(at_level_levels)
        if self._any_at_level and (now - self._last_orderflow_emit) >= self._orderflow_interval:
            self._emit_orderflow_update(price)
            self._last_orderflow_emit = now

    def mark_triggered(self, level_name: str) -> None:
        """Mark a level as triggered (trade taken). Called from trade creation."""
        for level in self._levels:
            if level.name == level_name:
                level.status = LevelStatus.TRIGGERED
                break

    def get_levels_snapshot(self, price: float) -> list[dict]:
        """Return all levels with current distance and status for REST API."""
        result = []
        for level in self._levels:
            result.append({
                "name": level.name,
                "price": level.price,
                "category": level.category,
                "status": level.status.value,
                "distance_ticks": round(level.distance_ticks(price), 1),
                "cluster": level.cluster,
            })
        result.sort(key=lambda x: abs(x["distance_ticks"]))
        return result

    # --- SSE event emitters ---

    def _on_level_approaching(self, level: MonitoredLevel, price: float, dist: float) -> None:
        self._publish({
            "type": "level_approaching",
            "level": level.name,
            "level_price": level.price,
            "category": level.category,
            "price": price,
            "distance_ticks": round(dist, 1),
        })

    def _on_level_touched(self, level: MonitoredLevel, price: float) -> None:
        self._publish({
            "type": "level_touched",
            "level": level.name,
            "level_price": level.price,
            "category": level.category,
            "price": price,
            "confluence": level.cluster,
        })

    def _on_level_rejected(self, level: MonitoredLevel, price: float) -> None:
        self._publish({
            "type": "level_rejected",
            "level": level.name,
            "level_price": level.price,
        })

    def _emit_orderflow_update(self, price: float) -> None:
        # Placeholder — will be filled in Task 2 with actual orderflow snapshot
        self._publish({
            "type": "orderflow_update",
            "price": price,
            "ts": time.time(),
        })
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(trading): add LevelMonitor class with proximity state machine"
```

### Task 2: Wire LevelMonitor into DatabentoLiveStream

**Files:**
- Modify: `backend/src/market_data/stream.py`

- [ ] **Step 1: Add LevelMonitor to DatabentoLiveStream.__init__ and _on_trade**

In `stream.py`, add LevelMonitor as an optional component of the stream. The monitor receives tick callbacks and publishes events through the same `_publish` mechanism.

Changes to `DatabentoLiveStream.__init__()` — add after `self._candle_flow`:
```python
from .level_monitor import LevelMonitor
self._level_monitor: LevelMonitor | None = None
```

Add a method to wire the monitor:
```python
def set_level_monitor(self, monitor: LevelMonitor) -> None:
    """Attach a level monitor to receive tick callbacks."""
    self._level_monitor = monitor
```

In `_stream_loop()`, after the candle flow update (around line 282), add:
```python
# Level proximity check
if self._level_monitor:
    self._level_monitor.on_tick(price, record.size, ts_epoch)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/stream.py
git commit -m "feat(trading): wire LevelMonitor into DatabentoLiveStream tick callback"
```

### Task 3: Orderflow Snapshot Packager

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Add orderflow snapshot computation to LevelMonitor**

Add a `_tick_buffer` reference and `_compute_orderflow_snapshot` method. The monitor needs access to the stream's TickBuffer to compute orderflow from recent ticks.

Add to `__init__`:
```python
self._tick_buffer = None  # Set via set_tick_buffer()
self._candle_flow_fn = None  # Callable returning recent CandleFlow objects
```

Add methods:
```python
def set_tick_buffer(self, tick_buffer) -> None:
    """Provide access to the stream's TickBuffer for orderflow computation."""
    self._tick_buffer = tick_buffer

def set_candle_flow_source(self, fn) -> None:
    """Provide callable that returns recent CandleFlow candles for orderflow."""
    self._candle_flow_fn = fn

def _compute_orderflow_snapshot(self) -> dict:
    """Compute orderflow signals for both directions and package as snapshot."""
    from .orderflow import compute_signals, build_candle_flow

    if not self._candle_flow_fn:
        return {}

    candles = self._candle_flow_fn()
    if not candles or len(candles) < 3:
        return {}

    # Compute for both directions
    long_signals = compute_signals(candles, "long", lookback=10)
    short_signals = compute_signals(candles, "short", lookback=10)

    return {
        "long": long_signals.__dict__,
        "short": short_signals.__dict__,
    }
```

Update `_on_level_touched` to include orderflow:
```python
def _on_level_touched(self, level: MonitoredLevel, price: float) -> None:
    snapshot = self._compute_orderflow_snapshot()
    self._publish({
        "type": "level_touched",
        "level": level.name,
        "level_price": level.price,
        "category": level.category,
        "price": price,
        "confluence": level.cluster,
        "orderflow": snapshot,
    })
```

Update `_emit_orderflow_update`:
```python
def _emit_orderflow_update(self, price: float) -> None:
    snapshot = self._compute_orderflow_snapshot()
    if snapshot:
        self._publish({
            "type": "orderflow_update",
            "price": price,
            "ts": time.time(),
            "orderflow": snapshot,
        })
```

- [ ] **Step 2: Wire tick_buffer and candle source in stream.py**

In `DatabentoLiveStream.set_level_monitor()`:
```python
def set_level_monitor(self, monitor: LevelMonitor) -> None:
    self._level_monitor = monitor
    monitor.set_tick_buffer(self._tick_buffer)
    # Provide candle source from the candle flow's recent history
    monitor.set_candle_flow_source(self._get_recent_candles)
```

Add `_get_recent_candles` method to DatabentoLiveStream — this returns CandleFlow objects built from TickBuffer data:
```python
def _get_recent_candles(self):
    """Build CandleFlow candles from recent tick buffer for orderflow computation."""
    from .orderflow import build_candle_flow
    ticks = list(self._tick_buffer.ticks)  # deque snapshot
    if len(ticks) < 10:
        return []
    return build_candle_flow(ticks, period_seconds=300)
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/src/market_data/stream.py
git commit -m "feat(trading): add orderflow snapshot to level monitor events"
```

### Task 4: Initialize LevelMonitor on App Startup

**Files:**
- Modify: `backend/src/api/__init__.py` (lifespan function)
- Modify: `backend/src/api/routes/market.py` (SSE event types + levels endpoint)

- [ ] **Step 1: Create and attach LevelMonitor in lifespan**

In `api/__init__.py`, after the DatabentoLiveStream is created and started (around line 196), add:

```python
from ..market_data.level_monitor import LevelMonitor

level_monitor = LevelMonitor(publish_fn=_databento_stream._publish)
_databento_stream.set_level_monitor(level_monitor)
app.state.level_monitor = level_monitor

# Load initial levels if session exists
try:
    from ..services.market_service import MarketService
    from ..db.models import get_session as _get_db_session
    db = _get_db_session()
    try:
        svc = MarketService(db)
        expanded = await svc.build_expanded_session()
        if expanded:
            level_monitor.load_levels(expanded)
    finally:
        db.close()
except Exception as e:
    logger.warning("Failed to load initial levels for monitor: %s", e)
```

- [ ] **Step 2: Refresh levels when compute_session is called**

In `market.py` route for `POST /compute`, add `request: Request` to the function signature, then after `svc.compute_session()` returns, reload the monitor:

```python
# Refresh level monitor with new session data
level_monitor = getattr(request.app.state, "level_monitor", None)
if level_monitor:
    expanded = await svc.build_expanded_session()
    if expanded:
        level_monitor.load_levels(expanded)
```

- [ ] **Step 3: Add GET /levels/live endpoint for current level states**

In `market.py`, add endpoint that returns live level snapshot:

```python
@router.get("/levels/live")
async def get_live_levels(request: Request, symbol: str = "NQ"):
    """Get all monitored levels with current distance and status."""
    monitor = getattr(request.app.state, "level_monitor", None)
    stream = getattr(request.app.state, "databento_stream", None)
    if not monitor or not stream:
        return {"levels": [], "price": None}
    # last_price from TickBuffer's most recent tick
    last_price = stream._tick_buffer.ticks[-1]["price"] if stream._tick_buffer.ticks else None
    return {
        "levels": monitor.get_levels_snapshot(last_price or 0),
        "price": last_price,
    }
```

- [ ] **Step 4: Add new event types to SSE stream**

The SSE stream endpoint already forwards all events from the queue by their `type` field. The new `level_approaching`, `level_touched`, `orderflow_update`, `level_rejected` events will automatically flow through since `_publish` pushes to the same subscriber queues. No changes needed to the SSE endpoint itself — it already uses `event_type = event.get("type", "tick")`.

Verify this by reading the event_generator in market.py — it should already handle arbitrary event types.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/__init__.py backend/src/api/routes/market.py
git commit -m "feat(trading): initialize LevelMonitor on startup, add /levels/live endpoint"
```

---

## Chunk 2: Frontend — Types, Hook, and Sound

### Task 5: Add New TypeScript Types

**Files:**
- Modify: `frontend/src/types/market.ts`

- [ ] **Step 1: Add level monitor and battle screen types**

Append to `market.ts`:

```typescript
// --- Level Battle Station types ---

export type LevelStatusType = 'watching' | 'approaching' | 'at_level' | 'triggered' | 'rejected';

export interface MonitoredLevel {
  name: string;
  price: number;
  category: 'session' | 'band' | 'prior' | 'structure' | 'overnight';
  status: LevelStatusType;
  distance_ticks: number;
  cluster: string[];  // confluence level names
}

export interface OrderflowSnapshot {
  long: OrderflowIndicators;
  short: OrderflowIndicators;
}

export interface LevelTouchedEvent {
  type: 'level_touched';
  level: string;
  level_price: number;
  category: string;
  price: number;
  confluence: string[];
  orderflow: OrderflowSnapshot;
}

export interface LevelApproachingEvent {
  type: 'level_approaching';
  level: string;
  level_price: number;
  category: string;
  price: number;
  distance_ticks: number;
}

export interface OrderflowUpdateEvent {
  type: 'orderflow_update';
  price: number;
  ts: number;
  orderflow: OrderflowSnapshot;
}

export interface LevelRejectedEvent {
  type: 'level_rejected';
  level: string;
  level_price: number;
}

export interface BattleScreenData {
  level: string;
  level_price: number;
  category: string;
  price: number;
  confluence: string[];
  orderflow: OrderflowSnapshot;
  structure: ExpandedSession['session'] | null;
  ml: {
    day_type: string | null;
    day_type_confidence: number | null;
  } | null;
  macro: ExpandedSession['macro'] | null;
  // Suggested trade levels (computed from nearby structure)
  suggested_entry: number;
  suggested_stop: number;
  targets: { name: string; price: number }[];
}

export interface PositionRow {
  trade_id: number;
  instrument: string;
  direction: 'long' | 'short';
  entry_price: number;
  current_size: number;  // contracts - partial exits
  original_size: number;
  current_price: number;
  pnl_points: number;
  pnl_dollars: number;
  stop_price: number;
  targets: { name: string; price: number; hit: boolean }[];
  next_target: { name: string; price: number } | null;
  status: 'running' | 'at_target' | 'stopped' | 'closed';
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/types/market.ts
git commit -m "feat(trading): add Level Battle Station TypeScript types"
```

### Task 6: Sound System Hook

**Files:**
- Create: `frontend/src/hooks/useSound.ts`

- [ ] **Step 1: Create Web Audio API hook**

```typescript
import { useRef, useCallback } from 'react';

/** Frequencies and durations for level alert tones. */
const TONES = {
  approaching: { freq: 440, duration: 0.1, gain: 0.15 },
  at_level: { freq: 880, duration: 0.2, gain: 0.3 },
  at_target: { freq: 660, duration: 0.15, gain: 0.25 },
} as const;

type ToneType = keyof typeof TONES;

export function useSound() {
  const ctxRef = useRef<AudioContext | null>(null);
  const unlockedRef = useRef(false);

  /** Must be called from a user interaction (click) to unlock audio. */
  const unlock = useCallback(() => {
    if (!ctxRef.current) {
      ctxRef.current = new AudioContext();
    }
    if (ctxRef.current.state === 'suspended') {
      ctxRef.current.resume();
    }
    unlockedRef.current = true;
  }, []);

  const play = useCallback((tone: ToneType) => {
    const ctx = ctxRef.current;
    if (!ctx || !unlockedRef.current) return;

    const { freq, duration, gain: gainVal } = TONES[tone];
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.value = gainVal;
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);

    osc.connect(gain).connect(ctx.destination);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + duration);
  }, []);

  return { unlock, play };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/hooks/useSound.ts
git commit -m "feat(trading): add Web Audio API sound hook for level alerts"
```

### Task 7: useLevelMonitor Hook

**Files:**
- Create: `frontend/src/hooks/useLevelMonitor.ts`
- Modify: `frontend/src/hooks/useMarketStream.ts`

- [ ] **Step 1: Expose EventSource ref from useMarketStream**

In `useMarketStream.ts`, change the internal `EventSource` to be stored on a ref that can be shared. Add to the return value:

```typescript
// Add to return:
return { lastTick, book, lastCandle, connected, esRef };
```

Where `esRef` is the existing ref to the EventSource instance (`useRef<EventSource | null>`).

- [ ] **Step 2: Create useLevelMonitor hook**

```typescript
import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  MonitoredLevel, BattleScreenData, OrderflowSnapshot,
  LevelTouchedEvent, LevelApproachingEvent, OrderflowUpdateEvent, LevelRejectedEvent,
} from '@/types/market';

interface LevelMonitorState {
  levels: MonitoredLevel[];
  activeBattle: BattleScreenData | null;
  battleActive: boolean;
}

export function useLevelMonitor(
  esRef: React.RefObject<EventSource | null>,
  sessionData: { session: any; macro: any; ml_day_type: any; ml_day_type_confidence: any } | null,
) {
  const [state, setState] = useState<LevelMonitorState>({
    levels: [],
    activeBattle: null,
    battleActive: false,
  });

  // Track which levels are at_level for status updates
  const levelStatusRef = useRef<Map<string, MonitoredLevel>>(new Map());

  useEffect(() => {
    const es = esRef.current;
    if (!es) return;

    const onApproaching = (e: MessageEvent) => {
      const data: LevelApproachingEvent = JSON.parse(e.data);
      levelStatusRef.current.set(data.level, {
        name: data.level,
        price: data.level_price,
        category: data.category as any,
        status: 'approaching',
        distance_ticks: data.distance_ticks,
        cluster: [],
      });
      setState(prev => ({
        ...prev,
        levels: Array.from(levelStatusRef.current.values())
          .sort((a, b) => Math.abs(a.distance_ticks) - Math.abs(b.distance_ticks)),
      }));
    };

    const onTouched = (e: MessageEvent) => {
      const data: LevelTouchedEvent = JSON.parse(e.data);
      levelStatusRef.current.set(data.level, {
        name: data.level,
        price: data.level_price,
        category: data.category as any,
        status: 'at_level',
        distance_ticks: 0,
        cluster: data.confluence,
      });

      // Compute suggested targets from nearby levels
      const allLevels = Array.from(levelStatusRef.current.values());
      const above = allLevels
        .filter(l => l.price > data.level_price)
        .sort((a, b) => a.price - b.price)
        .slice(0, 3)
        .map(l => ({ name: l.name, price: l.price }));
      const below = allLevels
        .filter(l => l.price < data.level_price)
        .sort((a, b) => b.price - a.price)
        .slice(0, 3)
        .map(l => ({ name: l.name, price: l.price }));

      const battle: BattleScreenData = {
        level: data.level,
        level_price: data.level_price,
        category: data.category,
        price: data.price,
        confluence: data.confluence,
        orderflow: data.orderflow,
        structure: sessionData?.session || null,
        ml: sessionData ? {
          day_type: sessionData.ml_day_type,
          day_type_confidence: sessionData.ml_day_type_confidence,
        } : null,
        macro: sessionData?.macro || null,
        suggested_entry: data.price,
        suggested_stop: below[0]?.price || data.price - 10,
        targets: above.length ? above : below,  // targets in likely direction
      };

      setState(prev => ({
        ...prev,
        levels: Array.from(levelStatusRef.current.values())
          .sort((a, b) => Math.abs(a.distance_ticks) - Math.abs(b.distance_ticks)),
        activeBattle: battle,
        battleActive: true,
      }));
    };

    const onOrderflow = (e: MessageEvent) => {
      const data: OrderflowUpdateEvent = JSON.parse(e.data);
      setState(prev => {
        if (!prev.activeBattle) return prev;
        return {
          ...prev,
          activeBattle: {
            ...prev.activeBattle,
            orderflow: data.orderflow,
            price: data.price,
          },
        };
      });
    };

    const onRejected = (e: MessageEvent) => {
      const data: LevelRejectedEvent = JSON.parse(e.data);
      levelStatusRef.current.set(data.level, {
        ...levelStatusRef.current.get(data.level)!,
        status: 'watching',
      });
      setState(prev => {
        const newState = {
          ...prev,
          levels: Array.from(levelStatusRef.current.values())
            .sort((a, b) => Math.abs(a.distance_ticks) - Math.abs(b.distance_ticks)),
        };
        // Close battle screen if the rejected level was the active one
        if (prev.activeBattle?.level === data.level) {
          newState.activeBattle = null;
          newState.battleActive = false;
        }
        return newState;
      });
    };

    es.addEventListener('level_approaching', onApproaching);
    es.addEventListener('level_touched', onTouched);
    es.addEventListener('orderflow_update', onOrderflow);
    es.addEventListener('level_rejected', onRejected);

    return () => {
      es.removeEventListener('level_approaching', onApproaching);
      es.removeEventListener('level_touched', onTouched);
      es.removeEventListener('orderflow_update', onOrderflow);
      es.removeEventListener('level_rejected', onRejected);
    };
  }, [esRef, sessionData]);

  const dismissBattle = useCallback(() => {
    setState(prev => ({ ...prev, activeBattle: null, battleActive: false }));
  }, []);

  const switchBattleLevel = useCallback((levelName: string) => {
    const level = levelStatusRef.current.get(levelName);
    if (!level || level.status !== 'at_level') return;
    // Re-trigger would need fresh data — for now just switch focus
    setState(prev => prev.activeBattle ? {
      ...prev,
      activeBattle: { ...prev.activeBattle, level: level.name, level_price: level.price },
    } : prev);
  }, []);

  return {
    levels: state.levels,
    activeBattle: state.activeBattle,
    battleActive: state.battleActive,
    dismissBattle,
    switchBattleLevel,
  };
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useLevelMonitor.ts frontend/src/hooks/useMarketStream.ts
git commit -m "feat(trading): add useLevelMonitor hook with SSE event handling"
```

---

## Chunk 3: Frontend — UI Components

### Task 8: GaugeBar Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/GaugeBar.tsx`

- [ ] **Step 1: Create reusable gauge bar**

```tsx
interface GaugeBarProps {
  label: string;
  /** 0-1 fill amount */
  fill: number;
  /** Raw display value */
  value: string;
  /** Assessment text: "STRONG", "HIGH", "NONE", etc. */
  assessment: string;
  /** Color variant based on direction confirmation */
  color: 'green' | 'red' | 'amber' | 'dim';
}

const COLOR_MAP = {
  green: { bar: 'bg-emerald-500', text: 'text-emerald-400', label: 'text-emerald-300' },
  red: { bar: 'bg-red-500', text: 'text-red-400', label: 'text-red-300' },
  amber: { bar: 'bg-amber-500', text: 'text-amber-400', label: 'text-amber-300' },
  dim: { bar: 'bg-zinc-600', text: 'text-zinc-500', label: 'text-zinc-500' },
};

export function GaugeBar({ label, fill, value, assessment, color }: GaugeBarProps) {
  const c = COLOR_MAP[color];
  const pct = Math.min(100, Math.max(0, fill * 100));

  return (
    <div className="flex items-center gap-2 font-mono text-xs min-w-[220px]">
      <span className="w-16 text-zinc-400 text-right shrink-0">{label}</span>
      <div className="flex-1 h-3 bg-zinc-800 rounded-sm overflow-hidden border border-zinc-700">
        <div className={`h-full ${c.bar} transition-all duration-300`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`w-14 text-right ${c.text} shrink-0`}>{value}</span>
      <span className={`w-16 text-right ${c.label} font-bold shrink-0`}>{assessment}</span>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/GaugeBar.tsx
git commit -m "feat(trading): add GaugeBar component"
```

### Task 9: BattleScreen Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/BattleScreen.tsx`

- [ ] **Step 1: Create battle screen with gauge rows + trade action bar**

Build the component using `GaugeBar` for each confirmation signal. Groups: Orderflow (Row 1), Structure (Row 2), ML & Context (Row 3). Bottom: trade action bar with entry/stop/targets + TRADE button.

Key logic:
- Map `OrderflowSnapshot.long` and `.short` to gauge fill/color/assessment values
- For each gauge, determine color by comparing long vs short signal strength
- Structure gauges are static (from session data passed as prop)
- ML gauges show day type, pattern, R-multiple
- Trade action bar shows suggested entry/stop/T1/T2/T3 with TRADE LONG / TRADE SHORT / DISMISS buttons

The component receives `BattleScreenData` as prop and `onTrade(direction, entry, stop, targets)` and `onDismiss` callbacks.

Helper function for mapping orderflow signals to gauge values:
```typescript
function orderflowToGauges(of: OrderflowSnapshot): GaugeBarProps[] {
  const { long: l, short: s } = of;
  // Delta: positive = bullish, negative = bearish
  const deltaVal = l.delta ?? 0;
  const deltaDir = deltaVal > 0 ? 'green' : deltaVal < 0 ? 'red' : 'dim';

  return [
    {
      label: 'DELTA', fill: Math.min(1, Math.abs(deltaVal) / 5000),
      value: deltaVal > 0 ? `+${deltaVal}` : `${deltaVal}`,
      assessment: deltaVal > 200 ? 'BULLISH' : deltaVal < -200 ? 'BEARISH' : 'FLAT',
      color: deltaDir,
    },
    {
      label: 'CVD', fill: l.cvd_trend === 'rising' ? 0.8 : l.cvd_trend === 'falling' ? 0.8 : 0.3,
      value: l.cvd_trend === 'rising' ? '↑↑' : l.cvd_trend === 'falling' ? '↓↓' : '--',
      assessment: l.cvd_trend === 'rising' ? 'STRONG' : l.cvd_trend === 'falling' ? 'STRONG' : 'FLAT',
      color: l.cvd_trend === 'rising' ? 'green' : l.cvd_trend === 'falling' ? 'red' : 'dim',
    },
    {
      label: 'ABSORB', fill: l.vsa_absorption ? 1.0 : 0.0,
      value: l.vsa_absorption ? 'YES' : '--',
      assessment: l.vsa_absorption ? 'HIGH' : 'NONE',
      color: l.vsa_absorption ? 'amber' : 'dim',
    },
    {
      label: 'IMBAL', fill: Math.min(1, (l.stacked_imbalance_count ?? 0) / 5),
      value: l.stacked_imbalance_count ? `${l.stacked_imbalance_direction} x${l.stacked_imbalance_count}` : '--',
      assessment: (l.stacked_imbalance_count ?? 0) >= 3 ? 'STACKING' : l.stacked_imbalance_count ? 'BUILDING' : 'NONE',
      color: l.stacked_imbalance_direction === 'buy' ? 'green' : l.stacked_imbalance_direction === 'sell' ? 'red' : 'dim',
    },
    {
      label: 'BIG', fill: Math.min(1, (l.big_trades_count ?? 0) / 10),
      value: l.big_trades_count ? `${l.big_trades_count}` : '--',
      assessment: (l.big_trades_net_delta ?? 0) > 0 ? 'BUY SIDE' : (l.big_trades_net_delta ?? 0) < 0 ? 'SELL SIDE' : 'NONE',
      color: (l.big_trades_net_delta ?? 0) > 0 ? 'green' : (l.big_trades_net_delta ?? 0) < 0 ? 'red' : 'dim',
    },
    {
      label: 'TRAPPED', fill: l.trapped_traders ? 0.9 : 0.0,
      value: l.trapped_traders ? 'YES' : '--',
      assessment: l.trapped_traders ? 'DETECTED' : 'NONE',
      color: l.trapped_traders ? 'amber' : 'dim',
    },
    {
      label: 'STOP RUN', fill: l.stop_run_detected ? 0.9 : 0.0,
      value: l.stop_run_detected ? 'YES' : '--',
      assessment: l.stop_run_detected ? 'DETECTED' : 'NONE',
      color: l.stop_run_detected ? 'amber' : 'dim',
    },
    {
      label: 'PA RATIO', fill: Math.min(1, (l.passive_active_ratio ?? 0) / 4),
      value: l.passive_active_ratio?.toFixed(1) ?? '--',
      assessment: (l.passive_active_ratio ?? 0) > 2 ? 'PASSIVE' : (l.passive_active_ratio ?? 0) > 1 ? 'BALANCED' : 'ACTIVE',
      color: (l.passive_active_ratio ?? 0) > 2 ? 'amber' : 'dim',
    },
  ];
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/BattleScreen.tsx
git commit -m "feat(trading): add BattleScreen component with orderflow gauges"
```

### Task 10: LevelMonitorTable Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/LevelMonitorTable.tsx`

- [ ] **Step 1: Create level table with status badges**

Table displaying all monitored levels sorted by distance. Rows highlight based on status:
- WATCHING: dim text
- APPROACHING: brightened, subtle pulse
- AT_LEVEL: accent highlight (cyan border-left), bold
- TRIGGERED: marked with checkmark
- REJECTED: briefly flash red, then fade

Columns: PRICE | LEVEL | TYPE | DIST | STATUS

Props:
```typescript
interface Props {
  levels: MonitoredLevel[];
  currentPrice: number | null;
  onLevelClick: (levelName: string) => void;
  compact?: boolean;  // true when battle screen is active (show only 4-5 rows)
}
```

When `compact` is true, only show the 4-5 closest levels to keep the battle screen visible.

Status badge styling matches existing pill patterns in the app (small colored pills).

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/LevelMonitorTable.tsx
git commit -m "feat(trading): add LevelMonitorTable component"
```

### Task 11: PositionManager Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/PositionManager.tsx`

- [ ] **Step 1: Create position table with action buttons**

Table for open positions. Columns: ENTRY | DIR | SIZE | CURRENT | P&L | STOP | NEXT LEVEL | DIST | STATUS | ACTIONS

Props:
```typescript
interface Props {
  positions: PositionRow[];
  onScale: (tradeId: number, pct: number) => void;
  onClose: (tradeId: number) => void;
  onHold: (tradeId: number) => void;
  onUpdateStop: (tradeId: number, newStop: number) => void;
}
```

P&L coloring: green for positive, red for negative. Position size shows current/original (e.g. "1/2 NQ").

When status is `at_target`, show action buttons: SCALE 50% | CLOSE ALL | HOLD.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/PositionManager.tsx
git commit -m "feat(trading): add PositionManager component"
```

---

## Chunk 4: Frontend — Page Assembly and Cleanup

### Task 12: Rewrite TradingIntradayPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Replace chart-centric layout with level battle station**

Full rewrite of the page. New structure:

```
┌──────────────────────────────────────────────┐
│ Header: "Intraday • Live"  [5m] [Refresh]    │
├──────────────────────────────────────────────┤
│ LevelMonitorTable (compact when battle open)  │
│   PRICE | LEVEL | TYPE | DIST | STATUS       │
├──────────────────────────────────────────────┤
│ BattleScreen (shown when level is AT_LEVEL)   │
│   Row 1: Orderflow gauges                    │
│   Row 2: Structure gauges                    │
│   Row 3: ML & Context gauges                 │
│   Trade Action Bar: ENTRY STOP T1 T2 T3     │
├──────────────────────────────────────────────┤
│ PositionManager (shown when positions exist)  │
│   ENTRY | DIR | SIZE | P&L | NEXT | STATUS   │
└──────────────────────────────────────────────┘
```

Key changes:
- Remove all CandleChart, GaugeStrip, candle state, candle fetching
- Replace signal list with LevelMonitorTable
- Add BattleScreen (conditionally rendered)
- Add PositionManager (conditionally rendered)
- Keep: SSE connection (useMarketStream), session fetch, refresh logic
- Add: useLevelMonitor hook, useSound hook
- On first click anywhere on page, call `sound.unlock()` to enable audio
- Fetch initial levels via `api.getLiveLevels()` on mount

Remove these imports: `CandleChart`, `GaugeStrip`, `CandleData`, candle-related types.
Add these imports: `LevelMonitorTable`, `BattleScreen`, `PositionManager`, `useLevelMonitor`, `useSound`.

The `handleTakeTrade` function changes from signal-based to battle-screen-based:
```typescript
const handleTakeTrade = async (direction: 'long' | 'short', entry: number, stop: number, targets: { name: string; price: number }[]) => {
  const result = await api.createTrade({
    instrument: 'NQ',
    direction,
    setup_type: activeBattle?.level || 'manual',
    entry_price: entry,
    stop_price: stop,
    targets: targets.map((t, i) => ({ price: t.price, contracts: 1, label: t.name })),
    contracts: 2,  // default, user can adjust
    notes: `Entry at ${activeBattle?.level} level`,
  });
  // ... handle result, refresh positions
};
```

- [ ] **Step 2: Add getLiveLevels to api.ts**

```typescript
async getLiveLevels(symbol = 'NQ'): Promise<{ levels: MonitoredLevel[]; price: number | null }> {
  return fetchJson(`/trading/market/levels/live?symbol=${symbol}`);
}
```

Remove `getCandles()` method from api.ts.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx frontend/src/services/api.ts
git commit -m "feat(trading): rewrite TradingIntradayPage with level battle station"
```

### Task 13: Remove Deprecated Files

**Files:**
- Delete: `frontend/src/components/Terminal/pages/CandleChart.tsx`
- Delete: `frontend/src/components/Terminal/pages/GaugeStrip.tsx`

- [ ] **Step 1: Delete CandleChart.tsx and GaugeStrip.tsx**

```bash
git rm frontend/src/components/Terminal/pages/CandleChart.tsx
git rm frontend/src/components/Terminal/pages/GaugeStrip.tsx
```

- [ ] **Step 2: Remove lightweight-charts dependency**

```bash
cd frontend && npm uninstall lightweight-charts
```

- [ ] **Step 3: Verify no remaining imports reference deleted files**

Search for `CandleChart` and `GaugeStrip` across the frontend codebase. Fix any remaining imports.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(trading): remove CandleChart, GaugeStrip, lightweight-charts"
```

---

## Chunk 5: Position Management & Progressive Payload

### Task 14: Position Target Tracker in LevelMonitor

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Add position tracking to LevelMonitor**

Add position awareness so the monitor can emit `position_at_target` events when open positions reach target levels.

Add to `__init__`:
```python
self._open_positions: list[dict] = []  # [{trade_id, direction, entry, stop, targets: [{name, price, hit}]}]
```

Add methods:
```python
def register_position(self, trade_id: int, direction: str, entry: float, stop: float, targets: list[dict]) -> None:
    """Register an open position for target monitoring."""
    self._open_positions.append({
        "trade_id": trade_id,
        "direction": direction,
        "entry_price": entry,
        "stop_price": stop,
        "targets": [{"name": t["name"], "price": t["price"], "hit": False} for t in targets],
    })

def close_position(self, trade_id: int) -> None:
    """Remove a closed position from monitoring."""
    self._open_positions = [p for p in self._open_positions if p["trade_id"] != trade_id]

def _check_positions(self, price: float) -> None:
    """Check if any open position has reached a target level."""
    for pos in self._open_positions:
        for target in pos["targets"]:
            if target["hit"]:
                continue
            dist = abs(price - target["price"]) / TICK_SIZE
            if dist <= self.AT_LEVEL_TICKS:
                target["hit"] = True
                snapshot = self._compute_orderflow_snapshot()
                self._publish({
                    "type": "position_at_target",
                    "trade_id": pos["trade_id"],
                    "target_name": target["name"],
                    "target_price": target["price"],
                    "price": price,
                    "direction": pos["direction"],
                    "orderflow": snapshot,
                })
```

Call `self._check_positions(price)` at the end of `on_tick()`.

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(trading): add position target tracking to LevelMonitor"
```

### Task 15: Async level_context Event (Progressive Payload)

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Add async context emission after level_touched**

When a level is touched, `level_touched` fires immediately with orderflow. ML and macro data are fetched asynchronously and emitted as a follow-up `level_context` event.

Add to `__init__`:
```python
self._loop: asyncio.AbstractEventLoop | None = None
self._db_session_factory = None
```

Add setter:
```python
def set_async_context(self, loop, db_session_factory) -> None:
    self._loop = loop
    self._db_session_factory = db_session_factory
```

Modify `_on_level_touched` to schedule async context fetch:
```python
def _on_level_touched(self, level: MonitoredLevel, price: float) -> None:
    snapshot = self._compute_orderflow_snapshot()
    self._publish({
        "type": "level_touched",
        "level": level.name,
        "level_price": level.price,
        "category": level.category,
        "price": price,
        "confluence": level.cluster,
        "orderflow": snapshot,
    })
    # Schedule async ML/macro fetch
    if self._loop and self._db_session_factory:
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._emit_level_context(level.name, level.price))
        )

async def _emit_level_context(self, level_name: str, level_price: float) -> None:
    """Fetch ML predictions and macro data, emit as follow-up event."""
    try:
        from ..services.market_service import MarketService
        db = self._db_session_factory()
        try:
            svc = MarketService(db)
            indicators = await svc.get_indicators()
            macro = await svc.get_macro_snapshot()
            self._publish({
                "type": "level_context",
                "level": level_name,
                "level_price": level_price,
                "ml": {
                    "day_type": indicators.get("ml_day_type"),
                    "day_type_confidence": indicators.get("ml_day_type_confidence"),
                },
                "macro": macro,
            })
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to emit level_context for %s: %s", level_name, e)
```

Wire `set_async_context` in `api/__init__.py` after creating the monitor:
```python
import asyncio
level_monitor.set_async_context(asyncio.get_event_loop(), _get_db_session)
```

- [ ] **Step 2: Add level_context handler to useLevelMonitor hook**

In `useLevelMonitor.ts`, add event listener:
```typescript
const onContext = (e: MessageEvent) => {
  const data = JSON.parse(e.data);
  setState(prev => {
    if (!prev.activeBattle || prev.activeBattle.level !== data.level) return prev;
    return {
      ...prev,
      activeBattle: {
        ...prev.activeBattle,
        ml: data.ml,
        macro: data.macro,
      },
    };
  });
};
es.addEventListener('level_context', onContext);
// ... and in cleanup: es.removeEventListener('level_context', onContext);
```

- [ ] **Step 3: Add position_at_target handler to useLevelMonitor hook**

```typescript
const onPositionTarget = (e: MessageEvent) => {
  const data = JSON.parse(e.data);
  setState(prev => ({
    ...prev,
    activeBattle: {
      level: data.target_name,
      level_price: data.target_price,
      category: 'target',
      price: data.price,
      confluence: [],
      orderflow: data.orderflow,
      structure: sessionData?.session || null,
      ml: prev.activeBattle?.ml || null,
      macro: prev.activeBattle?.macro || null,
      suggested_entry: data.price,
      suggested_stop: data.price,
      targets: [],
    },
    battleActive: true,
  }));
};
es.addEventListener('position_at_target', onPositionTarget);
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/src/api/__init__.py frontend/src/hooks/useLevelMonitor.ts
git commit -m "feat(trading): add level_context progressive payload and position_at_target events"
```

### Task 16: Scale-Out & Position Management API

**Files:**
- Modify: `backend/src/api/routes/trading.py`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add position management endpoints**

In `trading.py`, add routes for scale-out and stop management:

```python
@router.post("/trades/{trade_id}/scale")
async def scale_position(trade_id: int, pct: float = Query(default=50), db: Session = Depends(get_db)):
    """Scale out of a position by percentage. Creates TradeEvent(partial_exit)."""
    trade = db.query(Trade).get(trade_id)
    if not trade or trade.state == "closed":
        raise HTTPException(404, "Trade not found or closed")

    exit_contracts = max(1, int(trade.contracts * pct / 100))
    event = TradeEvent(
        trade_id=trade_id,
        event_type="partial_exit",
        details={"contracts": exit_contracts, "pct": pct, "price": None},  # price filled by caller
        notes=f"Scale out {pct}%",
    )
    db.add(event)

    # Move stop to breakeven after first scale
    if trade.be_price is None:
        trade.be_price = trade.entry_price
        trade.stop_price = trade.entry_price
        be_event = TradeEvent(trade_id=trade_id, event_type="move_to_be", details={"new_stop": trade.entry_price})
        db.add(be_event)

    db.commit()
    return {"success": True, "remaining_contracts": trade.contracts - exit_contracts}

@router.post("/trades/{trade_id}/close")
async def close_position(trade_id: int, db: Session = Depends(get_db)):
    """Close entire position."""
    trade = db.query(Trade).get(trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    trade.state = "closed"
    trade.closed_at = datetime.utcnow()
    event = TradeEvent(trade_id=trade_id, event_type="transition", from_state=trade.state, to_state="closed")
    db.add(event)
    db.commit()
    return {"success": True}

@router.post("/trades/{trade_id}/stop")
async def update_stop(trade_id: int, new_stop: float = Query(...), db: Session = Depends(get_db)):
    """Update stop price for a trade."""
    trade = db.query(Trade).get(trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    old_stop = trade.stop_price
    trade.stop_price = new_stop
    event = TradeEvent(trade_id=trade_id, event_type="trail_stop", details={"old": old_stop, "new": new_stop})
    db.add(event)
    db.commit()
    return {"success": True}
```

- [ ] **Step 2: Add corresponding API methods in api.ts**

```typescript
async scalePosition(tradeId: number, pct: number = 50): Promise<{ success: boolean; remaining_contracts: number }> {
  return fetchJson(`/trading/trades/${tradeId}/scale?pct=${pct}`, { method: 'POST' });
},

async closePosition(tradeId: number): Promise<{ success: boolean }> {
  return fetchJson(`/trading/trades/${tradeId}/close`, { method: 'POST' });
},

async updateStop(tradeId: number, newStop: number): Promise<{ success: boolean }> {
  return fetchJson(`/trading/trades/${tradeId}/stop?new_stop=${newStop}`, { method: 'POST' });
},
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/trading.py frontend/src/services/api.ts
git commit -m "feat(trading): add scale-out, close, and stop management API endpoints"
```

### Task 17: Structure and ML Gauge Helpers in BattleScreen

**Files:**
- Modify: `frontend/src/components/Terminal/pages/BattleScreen.tsx`

- [ ] **Step 1: Add structure gauge mapping (Row 2)**

```typescript
function structureToGauges(session: any): GaugeBarProps[] {
  if (!session) return [];
  return [
    {
      label: 'MKT TYPE', fill: session.market_type === 'trending_up' || session.market_type === 'trending_down' ? 0.9 : 0.4,
      value: session.market_type || '--',
      assessment: session.market_type?.includes('trending') ? 'TRENDING' : 'BALANCED',
      color: session.market_type?.includes('trending') ? 'green' : 'amber',
    },
    {
      label: 'OPEN', fill: session.opening_type === 'OD' ? 0.9 : 0.5,
      value: session.opening_type || '--',
      assessment: session.opening_type || 'UNKNOWN',
      color: session.opening_type === 'OD' ? 'green' : session.opening_type === 'ORR' ? 'red' : 'amber',
    },
    {
      label: 'DISTRIB', fill: session.distribution_type === 'double' ? 0.9 : 0.5,
      value: session.distribution_type || '--',
      assessment: (session.distribution_type || 'normal').toUpperCase(),
      color: session.distribution_type === 'p_shape' ? 'green' : session.distribution_type === 'b_shape' ? 'red' : 'amber',
    },
    {
      label: 'POOR H/L',
      fill: (session.poor_high || session.poor_low) ? 0.9 : 0.0,
      value: [session.poor_high && 'H', session.poor_low && 'L'].filter(Boolean).join('+') || '--',
      assessment: (session.poor_high || session.poor_low) ? 'UNFINISHED' : 'CLEAN',
      color: (session.poor_high || session.poor_low) ? 'amber' : 'dim',
    },
    {
      label: 'SWING', fill: 0.5,
      value: session.swing_structure || '--',
      assessment: session.swing_structure?.includes('up') ? 'HH/HL' : session.swing_structure?.includes('down') ? 'LH/LL' : 'RANGE',
      color: session.swing_structure?.includes('up') ? 'green' : session.swing_structure?.includes('down') ? 'red' : 'amber',
    },
    {
      label: 'SINGLES', fill: Math.min(1, (session.single_prints?.length || 0) / 5),
      value: `${session.single_prints?.length || 0}`,
      assessment: (session.single_prints?.length || 0) > 2 ? 'INITIATIVE' : 'FEW',
      color: (session.single_prints?.length || 0) > 2 ? 'amber' : 'dim',
    },
  ];
}
```

- [ ] **Step 2: Add ML & context gauge mapping (Row 3)**

```typescript
function mlToGauges(ml: BattleScreenData['ml'], macro: BattleScreenData['macro'], confluence: string[]): GaugeBarProps[] {
  return [
    {
      label: 'DAY TYPE',
      fill: (ml?.day_type_confidence || 0) / 100,
      value: ml?.day_type || '...',
      assessment: ml?.day_type_confidence ? `${ml.day_type_confidence}%` : 'LOADING',
      color: ml?.day_type ? 'amber' : 'dim',
    },
    {
      label: 'VIX',
      fill: Math.min(1, (macro?.vix || 20) / 40),
      value: macro?.vix?.toFixed(1) || '--',
      assessment: (macro?.vix || 20) < 18 ? 'LOW' : (macro?.vix || 20) > 25 ? 'HIGH' : 'NORMAL',
      color: (macro?.vix || 20) < 18 ? 'green' : (macro?.vix || 20) > 25 ? 'red' : 'amber',
    },
    {
      label: 'REGIME',
      fill: 0.5,
      value: macro?.regime || '--',
      assessment: (macro?.regime || 'neutral').toUpperCase(),
      color: macro?.regime === 'risk_on' ? 'green' : macro?.regime === 'risk_off' ? 'red' : 'amber',
    },
    {
      label: 'CONFLNC',
      fill: Math.min(1, confluence.length / 4),
      value: `${confluence.length + 1}`,
      assessment: confluence.length >= 2 ? 'STRONG' : confluence.length === 1 ? 'MODERATE' : 'SINGLE',
      color: confluence.length >= 2 ? 'green' : 'amber',
    },
  ];
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/BattleScreen.tsx
git commit -m "feat(trading): add structure and ML gauge mappings to BattleScreen"
```

---

## Chunk 6: Final Verification

### Task 18: Verify Build and Manual Test

- [ ] **Step 1: Wire sound into useLevelMonitor**

In `TradingIntradayPage.tsx`, call `sound.play('approaching')` and `sound.play('at_level')` when level events fire. The sound hook is used in the page component (not the level hook) since the page handles the unlock on first click:

```typescript
// In TradingIntradayPage:
const { unlock, play } = useSound();

// On first user interaction:
<div onClick={unlock}>

// In useEffect watching levels:
useEffect(() => {
  if (activeBattle) play('at_level');
}, [activeBattle]);
```

- [ ] **Step 3: Run frontend build**

```bash
cd frontend && npm run build
```

Fix any TypeScript errors.

- [ ] **Step 4: Start backend and frontend**

```bash
cd backend && python run_dev.py &
cd frontend && npm run dev
```

- [ ] **Step 5: Manual verification checklist**

1. Navigate to Intraday tab
2. Level table shows levels sorted by proximity
3. If market is open and Databento stream is active:
   - Levels update distance in real-time as ticks flow
   - When price approaches a level, row brightens
   - When price touches a level, battle screen expands with gauge bars
   - Gauge bars show orderflow data with colors
   - TRADE button is clickable and creates a trade record
   - DISMISS closes battle screen
4. If market is closed / no stream:
   - Level table shows levels with static distances
   - No battle screen fires (expected)
   - Positions table shows any existing positions

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix(trading): address build issues from level battle station integration"
```
