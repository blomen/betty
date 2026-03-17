# Level Monitor — Real-Time Level-Based Alert System

**Date:** 2026-03-15
**Status:** Reviewed

## Problem

The existing `MarketScanner` runs on a poll interval (every 5 min). It catches setups after they've formed, but misses the **moment of impact** — when price first touches a key level. Traders following Fabio/OrderFlowHorse methodology need real-time alerts when price reaches levels, with immediate orderflow confirmation scoring.

## Solution

Add a `LevelMonitor` that subscribes to the live tick stream and checks every tick against pre-computed levels. When price touches a level, it classifies the potential setup, scores orderflow confirmation in real-time, and pushes alerts via SSE. The existing scanner continues running for broader pattern detection.

## Architecture

```
                         ┌──────────────────┐
  Databento Live Ticks → │   TickBuffer     │
                         │ (existing stream) │
                         └────────┬─────────┘
                                  │ subscribe()
                    ┌─────────────▼──────────────┐
                    │       LevelMonitor          │
                    │                             │
                    │  1. Price vs Levels check   │
                    │  2. Setup classification    │
                    │  3. Orderflow confirmation  │
                    │  4. Score + alert           │
                    └─────────────┬──────────────┘
                                  │ SSE push
                    ┌─────────────▼──────────────┐
                    │     Frontend Alert Feed     │
                    └────────────────────────────┘
```

The `LevelMonitor` runs as an async task alongside the existing `DatabentoLiveStream`. It does NOT replace the `MarketScanner` — both coexist.

## Backend

### New file: `backend/src/market_data/level_monitor.py`

#### `LevelMonitor` class

```python
class LevelMonitor:
    """Singleton — stored on app.state.level_monitor, accessed by SSE routes."""

    def __init__(self, stream: DatabentoLiveStream, db_session_factory: sessionmaker):
        self.stream = stream
        self.db_session_factory = db_session_factory  # creates fresh DB sessions as needed
        self.levels: list[MonitoredLevel] = []
        self.active_alerts: dict[str, LevelAlert] = {}  # level_key → current alert (dedup)
        self.alert_history: list[LevelAlert] = []  # all alerts, newest first
        self.subscribers: list[asyncio.Queue] = []
        self._cooldowns: dict[str, datetime] = {}  # level_key → last_alert_time
        self._price_history: deque[tuple[float, datetime]] = deque(maxlen=200)  # (price, ts)
        self._cached_session: SessionAnalysis | None = None
        self._cached_orderflow: OrderflowSignals | None = None
        self._task: asyncio.Task | None = None
        self._cache_task: asyncio.Task | None = None
        self._expiry_task: asyncio.Task | None = None

    async def start(self):
        """Load levels, start monitor loop + background cache refresh + expiry sweep."""
        await self._refresh_caches()
        self._task = asyncio.create_task(self._monitor_loop())
        self._cache_task = asyncio.create_task(self._cache_refresh_loop())
        self._expiry_task = asyncio.create_task(self._expiry_sweep_loop())

    async def stop(self):
        for task in [self._task, self._cache_task, self._expiry_task]:
            if task:
                task.cancel()

    # ── Background cache refresh ──

    async def _cache_refresh_loop(self):
        """Refresh session + orderflow caches every 5 seconds. Reload levels every 60 seconds."""
        tick = 0
        while True:
            await asyncio.sleep(5)
            tick += 1
            try:
                await self._refresh_caches()
                if tick % 12 == 0:  # every 60s
                    await self._reload_levels()
            except Exception:
                pass  # log but don't crash

    async def _refresh_caches(self):
        """Refresh cached session + orderflow. Uses fresh DB session each time."""
        async with self.db_session_factory() as session:
            svc = MarketService(session)
            self._cached_session = await svc.build_session_analysis()
            self._cached_orderflow = await svc.compute_orderflow_signals()
        if not self.levels:
            await self._reload_levels()

    async def _reload_levels(self):
        """Pull levels from cached session. Atomic replacement (no mutation)."""
        if not self._cached_session:
            return
        self.levels = self._build_monitored_levels(self._cached_session)

    # ── Expiry sweep ──

    async def _expiry_sweep_loop(self):
        """Every 30 seconds, expire alerts where price has moved away."""
        while True:
            await asyncio.sleep(30)
            now = datetime.utcnow()
            current_price = self._price_history[-1][0] if self._price_history else None
            if current_price is None:
                continue
            expired_keys = []
            for key, alert in self.active_alerts.items():
                age = (now - alert.timestamp).total_seconds()
                distance = abs(current_price - alert.level.price)
                if age > 300 or distance > alert.level.proximity_threshold * 3:
                    expired_keys.append(key)
            for key in expired_keys:
                alert = self.active_alerts.pop(key)
                alert.state = "expired"
                await self._notify_subscribers({"event": "expire", "id": alert.id})

    # ── Monitor loop ──

    def _build_monitored_levels(self, session) -> list[MonitoredLevel]:
        """Convert session data into MonitoredLevel objects with per-category thresholds."""
        # Sources: VWAP bands, POC/VAH/VAL (session + weekly + leg + macro),
        # IB high/low, PDH/PDL, swing high/low, order blocks, FVGs,
        # overnight high/low, naked POCs, Tokyo/London levels
        #
        # Zone levels (OB, FVG): use near edge as price (the edge price faces),
        # not midpoint. e.g., bullish OB → use OB high (price approaches from above).
        ...

    async def _monitor_loop(self):
        """Subscribe to tick stream, check each tick against levels."""
        queue = self.stream.subscribe()
        try:
            while True:
                event = await queue.get()
                if event.get("type") != "tick":
                    continue
                price = event["price"]
                ts = datetime.fromisoformat(event["ts"])
                self._price_history.append((price, ts))
                self._check_price(price)
        except asyncio.CancelledError:
            pass
        finally:
            self.stream.unsubscribe(queue)

    def _check_price(self, price: float):
        """Check if price is within proximity of any monitored level.
        Synchronous — uses only cached data, no I/O."""
        levels = self.levels  # snapshot reference (atomic on CPython)
        for level in levels:
            distance = abs(price - level.price)
            if distance > level.proximity_threshold:
                continue
            if self._on_cooldown(level.key):
                continue

            # Check if we already have an active alert for this level — update it
            if level.key in self.active_alerts:
                self._update_alert(level, price)
                continue

            # New touch — classify and score
            alert = self._evaluate_touch(level, price)
            if alert:
                self._set_cooldown(level.key, seconds=30)
                self.active_alerts[level.key] = alert
                self.alert_history.insert(0, alert)
                if len(self.alert_history) > 50:
                    self.alert_history = self.alert_history[:50]
                asyncio.create_task(self._notify_subscribers({"event": "alert", "data": alert.to_dict()}))

    def _update_alert(self, level: MonitoredLevel, price: float):
        """Re-score confirmations for an existing active alert (price still at level)."""
        alert = self.active_alerts[level.key]
        old_state = alert.state
        alert.confirmations = self._score_confirmations(
            alert._setup, level, price, self._cached_session, self._cached_orderflow
        )
        alert.score = self._compute_score(alert.confirmations)
        alert.state = self._derive_state(alert.score)
        alert.updated_at = datetime.utcnow()
        if alert.state != old_state:
            asyncio.create_task(self._notify_subscribers({"event": "alert", "data": alert.to_dict()}))

    def _evaluate_touch(self, level: MonitoredLevel, price: float) -> LevelAlert | None:
        """Classify setup, score confirmations, compute trade plan. Synchronous."""
        session = self._cached_session
        of = self._cached_orderflow
        if not session:
            return None

        setup = self._classify_setup(level, price, session, of)
        confirmations = self._score_confirmations(setup, level, price, session, of)
        score = self._compute_score(confirmations)
        trade_plan = self._compute_trade_plan(setup, level, price, session)

        return LevelAlert(
            id=str(uuid4()),
            level=level,
            setup_type=setup.type,
            setup_name=setup.name,
            direction=setup.direction,
            score=score,
            state=self._derive_state(score),
            confirmations=confirmations,
            price_at_touch=price,
            fair_value_distance=self._fair_value_distance(price, session),
            suggested_entry=trade_plan.entry,
            suggested_stop=trade_plan.stop,
            suggested_target=trade_plan.target,
            rr=trade_plan.rr,
            timestamp=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            _setup=setup,  # internal, not serialized
        )

    def subscribe(self) -> asyncio.Queue:
        """Frontend SSE subscribes here to receive real-time alerts."""
        q = asyncio.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.subscribers:
            self.subscribers.remove(q)
```

#### Lifecycle & Injection

`LevelMonitor` is a **singleton** stored on `app.state`:

```python
# In FastAPI lifespan (app.py or api/__init__.py)
app.state.level_monitor = LevelMonitor(
    stream=app.state.databento_stream,
    db_session_factory=get_async_session,  # from db setup
)
await app.state.level_monitor.start()
```

It does NOT hold a `MarketService` reference. Instead, it creates fresh `MarketService` instances with fresh DB sessions inside `_refresh_caches()`. The hot path (`_check_price` → `_evaluate_touch`) reads only from cached `self._cached_session` and `self._cached_orderflow` — zero I/O, fully synchronous.

#### `MonitoredLevel` dataclass

```python
@dataclass
class MonitoredLevel:
    key: str              # unique ID: "session_vah", "vwap_2sd_upper", "swing_high_19905"
    price: float          # for zone levels (OB, FVG): the near edge facing price approach
    label: str            # "Session VAH", "+2 SD", "Swing High"
    category: str         # "value_area", "vwap", "ib", "swing", "structural"
    proximity_threshold: float  # points within which = "touching"
    setups: list[str]     # which setups this level can trigger: ["sfp", "rejection", "spring"]

# Per-category proximity thresholds (NQ, in points)
PROXIMITY_THRESHOLDS = {
    "value_area": 2.0,    # POC/VAH/VAL — tight, precise levels
    "ib": 2.0,            # IB high/low — precise
    "swing": 3.0,         # swing highs/lows — slightly wider
    "vwap": 3.0,          # VWAP — dynamic, needs some slack
    "vwap_extension": 5.0, # VWAP ±2/3 SD — wide bands, need more room
    "structural": 3.0,    # PDH/PDL, OB, FVG, Tokyo/London
    "naked": 2.0,         # naked POCs — precise
}
```

**Level → Setup mapping:**

| Level Category | Can Trigger |
|---------------|-------------|
| VAH, VAL | SFP, rejection, poor_extreme, acceptance_failure |
| POC | rejection, spring (if breaking away from POC) |
| VWAP ±2/3 SD | reversal_vwap, exhaustion |
| IB High/Low | ib_break, spring, sfp |
| Swing High/Low | sfp, break_of_structure, trapped_traders |
| PDH/PDL | sfp, rejection |
| Order Block | rejection, spring |
| FVG | gap_fill (mean reversion target) |
| Naked POC | rejection |

#### `LevelAlert` dataclass

```python
@dataclass
class LevelAlert:
    id: str                    # uuid4
    level: MonitoredLevel
    setup_type: str            # "sfp", "spring", "poor_extreme", "ib_break", etc.
    setup_name: str            # Human readable
    direction: str             # "long" or "short"
    score: float               # 0-100
    state: str                 # "monitoring", "developing", "confirmed"
    confirmations: list[Confirmation]
    price_at_touch: float
    fair_value_distance: float # points from POC
    fair_value_side: str       # "above" or "below"
    suggested_entry: float | None
    suggested_stop: float | None
    suggested_target: float | None
    rr: float | None
    timestamp: datetime
    updated_at: datetime
    _setup: SetupClassification = field(repr=False)  # internal, not serialized

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level_key": self.level.key,
            "level_label": self.level.label,
            "level_price": self.level.price,
            "setup_type": self.setup_type,
            "setup_name": self.setup_name,
            "direction": self.direction,
            "score": self.score,
            "state": self.state,
            "confirmations": [{"name": c.name, "met": c.met, "detail": c.detail} for c in self.confirmations],
            "price_at_touch": self.price_at_touch,
            "fair_value_distance": self.fair_value_distance,
            "fair_value_side": self.fair_value_side,
            "suggested_entry": self.suggested_entry,
            "suggested_stop": self.suggested_stop,
            "suggested_target": self.suggested_target,
            "rr": self.rr,
            "timestamp": self.timestamp.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

@dataclass
class Confirmation:
    name: str           # "absorption", "delta_divergence", "cvd_reversal", etc.
    met: bool
    detail: str | None  # "CVD -1200 → +300 in last 10 ticks"
```

#### Setup Classification Logic

The classifier uses `self._price_history` (last 200 ticks, ~40 seconds) to detect break-and-return patterns. This is the key state that enables SFP and Spring detection.

```python
def _classify_setup(self, level, price, session, of_signals) -> SetupClassification:
    """Determine which setup pattern matches the level touch."""

    direction = "short" if price > level.price else "long"

    # SFP: price broke level then returned inside
    if self._detected_break_and_return(level, price):
        return SetupClassification("sfp", "Swing Failure Pattern", direction)

    # Spring: aggressive break from balance, immediate reversal
    if level.category == "value_area" and self._detected_spring_pattern(level, price):
        return SetupClassification("spring", "Spring", direction)

    # Poor Extreme: test of low-volume extreme
    if (level.label.startswith("Session VA") and
        (session.poor_high and direction == "short" or session.poor_low and direction == "long")):
        return SetupClassification("poor_extreme", "Poor Extreme Test", direction)

    # IB Break: price leaving initial balance
    if level.category == "ib":
        return SetupClassification("ib_break", "IB Break", direction)

    # Exhaustion at VWAP extension
    if level.category == "vwap" and "sd" in level.key:
        return SetupClassification("exhaustion", "VWAP Extension", "short" if "upper" in level.key else "long")

    # Default: rejection test
    return SetupClassification("rejection", f"Level Test: {level.label}", direction)

def _detected_break_and_return(self, level: MonitoredLevel, current_price: float) -> bool:
    """SFP detection: did price break beyond level and return within the last 200 ticks?

    For swing high levels: price went ABOVE level.price (break), now BELOW (return).
    For swing low levels: price went BELOW level.price (break), now ABOVE (return).

    Requires:
    - At least 1 tick in history that broke beyond the level by > proximity_threshold
    - Current price is back within proximity_threshold of level (already checked by caller)
    - The break occurred within the last 200 ticks (~40 seconds)
    """
    if not self._price_history:
        return False

    threshold = level.proximity_threshold
    above_count = 0
    below_count = 0

    for hist_price, _ in self._price_history:
        if hist_price > level.price + threshold:
            above_count += 1
        elif hist_price < level.price - threshold:
            below_count += 1

    # SFP for a high-type level: price was above (broke out), now back at/below
    if level.category in ("swing", "pdh", "ib") and current_price <= level.price:
        return above_count >= 3  # need sustained break, not just 1 tick

    # SFP for a low-type level: price was below (broke out), now back at/above
    if level.category in ("swing", "pdl", "ib") and current_price >= level.price:
        return below_count >= 3

    return False

def _detected_spring_pattern(self, level: MonitoredLevel, current_price: float) -> bool:
    """Spring detection: aggressive break from value area followed by immediate reversal.

    For VAL: price dropped below VAL aggressively (>= 3 ticks below), now back above.
    For VAH: price rose above VAH aggressively (>= 3 ticks above), now back below.

    Spring differs from SFP in that it specifically targets value area boundaries
    and implies a "coiled spring" reversal — aggressive initial break, fast reversal.
    The speed is measured by requiring the break AND return within the 200-tick window.
    """
    if not self._price_history or len(self._price_history) < 5:
        return False

    threshold = level.proximity_threshold
    recent = list(self._price_history)[-50:]  # spring should be fast — last ~10 seconds

    broke_above = any(p > level.price + threshold for p, _ in recent)
    broke_below = any(p < level.price - threshold for p, _ in recent)

    if "val" in level.key.lower() and broke_below and current_price >= level.price:
        return True  # spring off VAL
    if "vah" in level.key.lower() and broke_above and current_price <= level.price:
        return True  # spring off VAH

    return False
```

#### Confirmation Scoring

```python
def _score_confirmations(self, setup, level, price, session, of) -> list[Confirmation]:
    """Score the Fabio/OrderFlowHorse confirmation checklist."""
    return [
        Confirmation(
            name="absorption",
            met=of.vsa_absorption,
            detail=f"Passive/active ratio: {of.passive_active_ratio:.1f}" if of.vsa_absorption else None,
        ),
        Confirmation(
            name="delta_divergence",
            met=of.delta_divergence,
            detail="Price vs delta disagree" if of.delta_divergence else None,
        ),
        Confirmation(
            name="cvd_reversal",
            met=self._cvd_reversed(of, setup.direction),
            detail=f"CVD trend: {of.cvd_trend}" if of.cvd_trend else None,
        ),
        Confirmation(
            name="big_trades",
            met=of.big_trades_count > 0 and self._big_trades_aligned(of, setup.direction),
            detail=f"x{of.big_trades_count} net Δ{of.big_trades_net_delta:+d}" if of.big_trades_count > 0 else None,
        ),
        Confirmation(
            name="away_from_fair_value",
            met=abs(price - (session.volume_profile.poc if session.volume_profile else price)) > 10,
            detail=f"POC at {session.volume_profile.poc:.0f}, price {price - session.volume_profile.poc:+.0f}pts away" if session.volume_profile else None,
        ),
        Confirmation(
            name="trapped_traders",
            met=of.trapped_traders,
            detail="Trapped traders detected" if of.trapped_traders else None,
        ),
        Confirmation(
            name="momentum_aligned",
            met=of.delta_aligned,
            detail=f"Delta {of.delta:+d} aligned with {setup.direction}" if of.delta_aligned else None,
        ),
    ]
```

#### Scoring Formula

```python
# Confirmation weights (not all equal — absorption and away_from_fair_value matter most)
CONFIRMATION_WEIGHTS = {
    "absorption": 20,
    "delta_divergence": 15,
    "cvd_reversal": 15,
    "big_trades": 10,
    "away_from_fair_value": 20,
    "trapped_traders": 10,
    "momentum_aligned": 10,
}
# Total possible: 100

def _compute_score(self, confirmations: list[Confirmation]) -> float:
    return sum(CONFIRMATION_WEIGHTS.get(c.name, 0) for c in confirmations if c.met)

def _derive_state(self, score: float) -> str:
    if score >= 75:
        return "confirmed"
    if score >= 50:
        return "developing"
    return "monitoring"
```

**Alert states:**
- `confirmed` (score >= 75): 4+ key confirmations met, ready to trade
- `developing` (score 50-74): 2-3 confirmations met, watch closely
- `monitoring` (score < 50): level touched, no confirmation yet

#### Cooldown & Deduplication

- After emitting an alert for a level, 30-second cooldown before same level can trigger again
- If price stays at level and confirmations change, update existing alert (don't create duplicate)
- Alerts expire after 5 minutes if price moves away from level (>3× proximity threshold)

#### Level Reload

- Levels reloaded every 60 seconds from cached session data (catches IB formation, new swing points, etc.)
- Full reload on manual Refresh button click

### Trade Plan Computation

```python
def _compute_trade_plan(self, setup, level, price, session) -> TradePlan:
    """Compute entry, stop, target based on setup type and levels."""

    if setup.direction == "long":
        entry = level.price  # enter at level
        stop = level.price - self._stop_distance(setup, session)
        target = session.volume_profile.poc if session.volume_profile else level.price + (level.price - stop) * 2
    else:
        entry = level.price
        stop = level.price + self._stop_distance(setup, session)
        target = session.volume_profile.poc if session.volume_profile else level.price - (stop - level.price) * 2

    rr = abs(target - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
    return TradePlan(entry=entry, stop=stop, target=target, rr=rr)
```

**Target logic by setup:**
- SFP/Spring/Rejection → target = POC (mean reversion to fair value)
- IB Break → target = 1.5× IB range extension
- Exhaustion → target = VWAP (mean reversion)
- Poor Extreme → target = POC

**Stop distance by setup:**

```python
def _stop_distance(self, setup: SetupClassification, session: SessionAnalysis) -> float:
    """Points beyond entry for stop loss placement."""
    ib_range = session.initial_balance.range if session.initial_balance else 20.0

    STOP_MAP = {
        "sfp": max(ib_range * 0.3, 8.0),        # tight — beyond the false extreme
        "spring": max(ib_range * 0.4, 10.0),     # beyond the spring point
        "poor_extreme": max(ib_range * 0.3, 8.0),
        "ib_break": ib_range * 0.5,              # half IB range (opposite edge)
        "exhaustion": max(ib_range * 0.5, 15.0),  # wider — VWAP extensions are volatile
        "rejection": max(ib_range * 0.3, 8.0),   # default
    }
    return STOP_MAP.get(setup.type, 10.0)
```

Stops are IB-range-relative (scales with volatility) with minimum floors to avoid impossibly tight stops on narrow IB days.

### SSE Endpoint

**New endpoint: `GET /api/trading/market/level-alerts`**

```python
@router.get("/market/level-alerts")
async def stream_level_alerts(request: Request):
    """SSE stream of real-time level alerts from LevelMonitor.
    Sends all active alerts on connect, then streams updates.
    Frontend should NOT make a separate REST call — this SSE handles initial state."""
    monitor: LevelMonitor = request.app.state.level_monitor
    queue = monitor.subscribe()

    async def event_generator():
        try:
            # Send current active alerts on connect (initial state)
            for alert in monitor.active_alerts.values():
                yield {"event": "alert", "data": json.dumps(alert.to_dict())}

            # Stream updates
            while True:
                msg = await asyncio.wait_for(queue.get(), timeout=30)
                yield {"event": msg["event"], "data": json.dumps(msg.get("data", msg.get("id")))}
        except asyncio.TimeoutError:
            yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            monitor.unsubscribe(queue)

    return EventSourceResponse(event_generator())
```

No separate REST endpoint needed — the SSE stream sends initial state on connect. This avoids the race window between REST load and first SSE event.

### Integration with existing code

**`MarketService` additions:**
- `get_live_orderflow() → OrderflowSignals` — compute from recent `TickBuffer` data (already available)
- `get_cached_session() → SessionAnalysis` — return last computed session without re-fetching

**`DatabentoLiveStream`:**
- No changes needed — `LevelMonitor` subscribes via existing `subscribe()` method

**Startup (`app.py` or FastAPI lifespan):**
```python
level_monitor = LevelMonitor(stream=live_stream, market_service=market_service)
await level_monitor.start()
```

## Frontend

### TradingIntradayPage — Alert Feed

Replace the current right-column panels with a single **alert feed**. Price Strip removed (user watches TradingView). Keep header + context strip.

```
┌──────────────────────────────────────────────────────────────┐
│  NQ 19847.50 ●Live +0.89 SD                    auto 5m [R]  │
├──────────────────────────────────────────────────────────────┤
│  Levels: 29 active  │  Alerts: 3                             │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ● 85  SFP at VAH (19865)                       12:34:02    │
│     Price broke VAH → closed back inside                     │
│     ✓ Absorption  ✓ Delta diverg  ✓ CVD reversal             │
│     ✗ Big trades  ✓ Away from FV  ✓ Trapped                  │
│     Fair value: 19820 (POC)  |  +45 pts above                │
│     → SHORT  E:19860  S:19880  T:19820  2.2R                │
│     [Take Trade]                                             │
│                                                              │
│  ◐ 72  Poor Extreme at VAL (19780)              12:31:15    │
│     Low volume rejection at extreme                          │
│     ✓ Absorption  ✗ Delta diverg  ✓ CVD flat                 │
│     ✓ Big trades  ✓ At FV boundary  ✗ Trapped                │
│     → LONG  E:19785  S:19765  T:19820  1.75R                │
│     [Take Trade]                                             │
│                                                              │
│  ○ 55  IB High touch (19860)                    12:28:40    │
│     Monitoring — waiting for confirmation                    │
│     ✗ Absorption  ✗ Delta diverg  ✓ CVD rising               │
│     Waiting for: rejection or acceptance                     │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  risk_on │ OTD │ IB 42pt RF+4 │ Val up │ Ranging ↔ │ P72    │
└──────────────────────────────────────────────────────────────┘
```

**Alert states and icons:**
- `●` red/green = **confirmed** (score >= 75) — ready to trade
- `◐` yellow = **developing** (score 50-74) — watch closely
- `○` grey = **monitoring** (score < 50) — no confirmation yet

**Alert row (collapsed):**
- State icon + score + setup name + level + timestamp
- One-line summary of what happened
- Confirmation badges: `✓`/`✗` for each of 7 confirmations
- Fair value distance
- Trade plan: direction, E/S/T, R:R
- Take Trade button (confirmed alerts only)

**Alert row (expanded — click to expand):**
- Confirmation details (the `detail` text for each confirmation)
- Orderflow snapshot: delta, CVD, big trades breakdown
- Level context: nearby levels, value area position

**Context strip (bottom):**
- One-line compact: regime, market type, IB range, RF, value migration, structure, ASPR percentile
- Same data as current `ContextStrip`, just pinned at bottom

### New hook: `useLevelAlerts`

```ts
function useLevelAlerts() {
  const [alerts, setAlerts] = useState<LevelAlert[]>([]);

  useEffect(() => {
    // SSE stream handles both initial state AND real-time updates (no separate REST call)
    const es = new EventSource('/api/trading/market/level-alerts');
    es.addEventListener('alert', (e) => {
      const alert: LevelAlert = JSON.parse(e.data);
      setAlerts(prev => {
        // Update existing alert or add new
        const idx = prev.findIndex(a => a.id === alert.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = alert;
          return next;
        }
        return [alert, ...prev];
      });
    });

    es.addEventListener('expire', (e) => {
      const id = JSON.parse(e.data);
      setAlerts(prev => prev.filter(a => a.id !== id));
    });

    return () => es.close();
  }, []);

  // Sort: confirmed first, then developing, then monitoring; within each group by score desc
  const sorted = useMemo(() =>
    [...alerts].sort((a, b) => {
      const stateOrder = { confirmed: 0, developing: 1, monitoring: 2 };
      const stateDiff = (stateOrder[a.state] ?? 3) - (stateOrder[b.state] ?? 3);
      return stateDiff !== 0 ? stateDiff : b.score - a.score;
    }),
    [alerts]
  );

  return sorted;
}
```

### Types

```ts
interface LevelAlert {
  id: string;
  level_key: string;
  level_label: string;
  level_price: number;
  setup_type: string;
  setup_name: string;
  direction: 'long' | 'short';
  score: number;
  state: 'monitoring' | 'developing' | 'confirmed';
  confirmations: Confirmation[];
  price_at_touch: number;
  fair_value_distance: number;
  fair_value_side: 'above' | 'below';
  suggested_entry: number | null;
  suggested_stop: number | null;
  suggested_target: number | null;
  rr: number | null;
  timestamp: string;
  updated_at: string;
}

interface Confirmation {
  name: string;
  met: boolean;
  detail: string | null;
}
```

## What Does NOT Change

- `MarketScanner` — continues running on 5-min poll for broader pattern detection
- `DatabentoLiveStream` — no changes, LevelMonitor subscribes via existing API
- `SessionAnalysis` / AMT computation — no changes
- `OrderflowSignals` computation — no changes (reused by LevelMonitor)
- Existing scanner signals API — still available
- Take Trade flow — same `handleTakeTrade` → `api.createTrade()`

## File Impact

| File | Change |
|------|--------|
| `backend/src/market_data/level_monitor.py` | **NEW** — LevelMonitor, MonitoredLevel, LevelAlert, Confirmation |
| `backend/src/api/routes/market.py` | Add SSE endpoint for level alerts |
| `backend/src/app.py` | Start LevelMonitor on app startup (app.state.level_monitor) |
| `frontend/src/hooks/useLevelAlerts.ts` | **NEW** — SSE hook for level alerts |
| `frontend/src/services/api.ts` | No changes (SSE-only, no REST) |
| `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` | Rewrite to alert feed layout |
| `frontend/src/types/market.ts` | Add `LevelAlert`, `Confirmation` types |

## Testing

- Unit test `_classify_setup` with mock level + price combinations
- Unit test `_score_confirmations` with mock orderflow signals
- Unit test cooldown/deduplication logic
- Integration test: feed mock ticks through LevelMonitor, verify alerts emitted
- Frontend: verify SSE connection, alert rendering, expand/collapse, Take Trade
