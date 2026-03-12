# Trading Intraday System — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AMT + Orderflow intraday trading system that auto-detects 9 named setups at computed structural levels, confirmed by L2 orderflow signals, with auto SL/TP and two-step trade entry.

**Architecture:** Extend existing `market_data/` module with live Databento streaming, level engine, setup detectors, and L2 confirmation. Add 3 manual context gates (Layer A) above existing 4 auto-gates (Layer B). SSE stream for real-time L2 data to frontend. Rename Scanner tab to Intraday.

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy / SQLite (backend), React 19 / TypeScript / Vite / Tailwind (frontend), Databento Python SDK (market data), sse-starlette (real-time transport)

**Spec:** `docs/superpowers/specs/2026-03-12-trading-system-design.md`

---

## File Map

### Backend — New Files
| File | Responsibility |
|---|---|
| `backend/src/market_data/stream.py` | Databento live WebSocket client (Trades + MBP-1), tick storage |
| `backend/src/market_data/history.py` | Databento REST historical fetch (OHLCV-1d, OHLCV-1m, Trades) |
| `backend/src/market_data/levels.py` | Level engine: VP, VWAP bands, session levels, IB, order blocks, FVGs |
| `backend/src/market_data/tpo.py` | TPO / Market Profile: 30-min brackets, single prints, ledges, poor extremes |
| `backend/src/market_data/orderflow.py` | L2 confirmation signals: delta, CVD, VSA, tick vol, passive/active |
| `backend/src/market_data/metrics.py` | Session metrics: RF, ASPR, range baselines |
| `backend/src/market_data/setups/` | Setup detector package (one file per setup type) |
| `backend/src/market_data/setups/__init__.py` | Package init |
| `backend/src/market_data/setups/detector.py` | Setup orchestrator: runs all detectors, returns scored opportunities |
| `backend/src/market_data/setups/spring.py` | Spring / Liquidity Trap detector |
| `backend/src/market_data/setups/sfp.py` | Swing Failure Pattern detector |
| `backend/src/market_data/setups/poor_extreme.py` | Poor Extreme detector |
| `backend/src/market_data/setups/ib_break.py` | Initial Balance Break detector |
| `backend/src/market_data/setups/rule_80.py` | 80% Rule detector |
| `backend/src/market_data/setups/double_distribution.py` | Double Distribution Reversal detector |
| `backend/src/market_data/setups/break_from_balance.py` | Break from Balance detector |
| `backend/src/market_data/setups/fakeout.py` | Fakeout / Head Fake detector |
| `backend/src/market_data/setups/news_directional.py` | News Directional detector |
| `backend/src/market_data/cot.py` | CFTC COT report weekly fetcher |

### Backend — Modified Files
| File | Changes |
|---|---|
| `backend/src/db/models.py` | Add `MarketTrade`, `MarketLevel`, `MarketContext` models; extend `MarketSession`, `TradingSignal` |
| `backend/src/repositories/market_repo.py` | Add methods for new tables |
| `backend/src/services/market_service.py` | Add context gate CRUD, SSE stream generator, setup orchestration |
| `backend/src/api/routes/market.py` | Add SSE endpoint, context endpoints, level endpoints |
| `backend/src/market_data/databento_provider.py` | Extend with live stream capability |
| `backend/src/market_data/scanner.py` | Refactor to use new setup detectors |

### Frontend — Modified Files
| File | Changes |
|---|---|
| `frontend/src/types/market.ts` | Add `MarketContext`, `MarketLevel`, `StreamEvent` types; extend `TradingSignal` |
| `frontend/src/services/api.ts` | Add context gate, level, SSE endpoints |
| `frontend/src/components/Terminal/pages/TradingScannerPage.tsx` | Rename to Intraday, add Layer A gates, level panel, live L2 |
| `frontend/src/components/Terminal/TabBar.tsx` | Rename tradingScanner → tradingIntraday |
| `frontend/src/components/Terminal/Sidebar.tsx` | Update TabName type |
| `frontend/src/components/Terminal/TerminalWindow.tsx` | Update lazy import + case |

---

## Chunk 1: Phase 0 — DB Migrations + Phase 1 — Data Foundation

### Task 1: Add New DB Models

**Files:**
- Modify: `backend/src/db/models.py`

- [ ] **Step 1: Add MarketTrade model**

Add after `TradingSignal` class in `models.py`:

```python
class MarketTrade(Base):
    """Raw tick data from Databento live stream."""
    __tablename__ = "market_trades"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    ts = Column(DateTime, nullable=False)  # UTC from Databento
    price = Column(Float, nullable=False)
    size = Column(Integer, nullable=False)
    side = Column(String, nullable=False)  # "B" (bid aggressor) | "A" (ask aggressor)

    __table_args__ = (
        Index("ix_market_trades_symbol_ts", "symbol", "ts"),
    )
```

- [ ] **Step 2: Add MarketLevel model**

```python
class MarketLevel(Base):
    """Computed structural level for a session."""
    __tablename__ = "market_levels"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    date = Column(String, nullable=False)
    level_type = Column(String, nullable=False)  # "order_block", "fvg", "ledge", "single_print", "pdh", "pdl", "tokyo_high", etc.
    session = Column(String, nullable=True)  # "tokyo", "london", "ny", null
    price_low = Column(Float, nullable=False)
    price_high = Column(Float, nullable=False)  # = price_low for single-price levels
    direction = Column(String, nullable=True)  # "bullish", "bearish", null
    is_filled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_market_levels_symbol_date", "symbol", "date", "level_type"),
    )
```

- [ ] **Step 3: Add MarketContext model**

```python
class MarketContext(Base):
    """Manual context gate persistence (Layer A gates)."""
    __tablename__ = "market_context"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    # Gate 1: Macro
    macro_bias = Column(String, nullable=True)  # "bull", "bear", "neutral"
    risk_mode = Column(String, nullable=True)   # "risk_on", "risk_off", "mixed"
    cycle_phase = Column(String, nullable=True) # "early", "mid", "late", "recession"
    # Gate 2: Structure
    structure = Column(String, nullable=True)     # "uptrend", "downtrend", "ranging"
    structure_hl = Column(Float, nullable=True)   # Last confirmed HL (long invalidation below)
    structure_lh = Column(Float, nullable=True)   # Last confirmed LH (short invalidation above)
    # Gate 3: Day type
    day_type = Column(String, nullable=True)  # "trend", "normal", "normal_variation", "neutral", "composite"
    # VP anchors (Unix timestamps)
    vp_old_macro_start = Column(Integer, nullable=True)
    vp_ongoing_macro_start = Column(Integer, nullable=True)
    vp_leg_start = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", name="uq_market_context_symbol"),
    )
```

- [ ] **Step 4: Extend MarketSession with new columns**

Add these columns to the existing `MarketSession` class:

```python
    # New: session metrics
    rotation_factor = Column(Integer, nullable=True)
    aspr = Column(Float, nullable=True)
    aspr_percentile = Column(Float, nullable=True)
    ib_tpo_count = Column(Integer, nullable=True)
    value_migration = Column(String, nullable=True)  # "up", "down", "overlapping"
    # New: session levels
    pdh = Column(Float, nullable=True)
    pdl = Column(Float, nullable=True)
    tokyo_high = Column(Float, nullable=True)
    tokyo_low = Column(Float, nullable=True)
    london_high = Column(Float, nullable=True)
    london_low = Column(Float, nullable=True)
```

- [ ] **Step 5: Extend TradingSignal with new columns**

Add these columns to the existing `TradingSignal` class:

```python
    # New: multi-target + setup categorization
    suggested_target_2 = Column(Float, nullable=True)
    suggested_target_3 = Column(Float, nullable=True)
    level_touched = Column(String, nullable=True)
    setup_category = Column(String, nullable=True)  # "spring", "sfp", "poor_extreme", etc.
    rr_tp1 = Column(Float, nullable=True)
    rr_tp2 = Column(Float, nullable=True)
```

- [ ] **Step 6: Verify DB creates successfully**

Run: `cd backend && python -c "from src.db.models import init_db; init_db()"`
Expected: No errors. New tables created via SQLAlchemy `create_all`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat: add MarketTrade, MarketLevel, MarketContext models + extend MarketSession/TradingSignal"
```

---

### Task 2: Extend Market Repository

**Files:**
- Modify: `backend/src/repositories/market_repo.py`

- [ ] **Step 1: Add imports and new CRUD methods**

Add to `MarketRepo` class:

```python
from src.db.models import MarketTrade, MarketLevel, MarketContext

# --- MarketTrade ---
def bulk_insert_trades(self, trades: list[dict]):
    """Insert batch of ticks. trades = [{symbol, ts, price, size, side}, ...]"""
    self.db.bulk_insert_mappings(MarketTrade, trades)
    self.db.commit()

def prune_trades(self, symbol: str, before: datetime):
    """Delete ticks older than cutoff."""
    self.db.query(MarketTrade).filter(
        MarketTrade.symbol == symbol,
        MarketTrade.ts < before,
    ).delete()
    self.db.commit()

def get_trades(self, symbol: str, start: datetime, end: datetime) -> list[MarketTrade]:
    return self.db.query(MarketTrade).filter(
        MarketTrade.symbol == symbol,
        MarketTrade.ts >= start,
        MarketTrade.ts <= end,
    ).order_by(MarketTrade.ts).all()

# --- MarketLevel ---
def upsert_levels(self, symbol: str, date: str, levels: list[dict]):
    """Replace all levels for a session date."""
    self.db.query(MarketLevel).filter(
        MarketLevel.symbol == symbol,
        MarketLevel.date == date,
    ).delete()
    for lv in levels:
        lv["symbol"] = symbol
        lv["date"] = date
    self.db.bulk_insert_mappings(MarketLevel, levels)
    self.db.commit()

def get_levels(self, symbol: str, date: str) -> list[MarketLevel]:
    return self.db.query(MarketLevel).filter(
        MarketLevel.symbol == symbol,
        MarketLevel.date == date,
    ).all()

# --- MarketContext ---
def get_context(self, symbol: str) -> MarketContext | None:
    return self.db.query(MarketContext).filter(
        MarketContext.symbol == symbol,
    ).first()

def upsert_context(self, symbol: str, data: dict):
    """Create or update context for a symbol."""
    ctx = self.get_context(symbol)
    if ctx:
        for k, v in data.items():
            if hasattr(ctx, k):
                setattr(ctx, k, v)
    else:
        ctx = MarketContext(symbol=symbol, **data)
        self.db.add(ctx)
    self.db.commit()
    return ctx
```

- [ ] **Step 2: Verify import works**

Run: `cd backend && python -c "from src.repositories.market_repo import MarketRepo; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add backend/src/repositories/market_repo.py
git commit -m "feat: add MarketTrade, MarketLevel, MarketContext repo methods"
```

---

### Task 3: Databento Live Stream Client

**Files:**
- Create: `backend/src/market_data/stream.py`
- Modify: `backend/src/market_data/databento_provider.py`

- [ ] **Step 1: Install databento SDK if not present**

Run: `cd backend && pip install databento sse-starlette`

- [ ] **Step 2: Create stream.py — live Databento WebSocket client**

```python
"""Databento live stream client for Trades + MBP-1."""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class TickBuffer:
    """Thread-safe circular buffer of recent ticks."""
    max_size: int = 10_000
    ticks: deque = field(default_factory=lambda: deque(maxlen=10_000))
    # Running accumulators
    cvd: int = 0
    delta_1m: int = 0  # delta for current 1-min candle
    last_candle_ts: datetime | None = None

    def add(self, ts: datetime, price: float, size: int, side: str):
        self.ticks.append({"ts": ts, "price": price, "size": size, "side": side})
        delta = size if side == "A" else -size  # Ask aggressor = buy, Bid = sell
        self.cvd += delta
        self.delta_1m += delta

    def reset_candle_delta(self):
        d = self.delta_1m
        self.delta_1m = 0
        return d


class DabentoLiveStream:
    """Manages a persistent Databento live subscription."""

    def __init__(self, api_key: str, dataset: str = "GLBX.MDP3", symbol: str = "NQ.FUT"):
        self.api_key = api_key
        self.dataset = dataset
        self.symbol = symbol
        self.buffer = TickBuffer()
        self._running = False
        self._task: asyncio.Task | None = None
        self._subscribers: list[asyncio.Queue] = []

    async def start(self):
        """Start the live stream in background."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._stream_loop())
        logger.info("Databento live stream started for %s", self.symbol)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Databento live stream stopped")

    def subscribe(self) -> asyncio.Queue:
        """Get a queue that receives tick events."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def _stream_loop(self):
        """Main stream loop — connects to Databento and processes messages."""
        try:
            import databento as db

            client = db.Live(key=self.api_key)
            client.subscribe(
                dataset=self.dataset,
                schema="trades",
                symbols=[self.symbol],
            )

            async for record in client:
                if not self._running:
                    break

                ts = datetime.fromtimestamp(record.ts_event / 1e9, tz=timezone.utc)
                price = record.price / 1e9  # Databento fixed-point
                size = record.size
                side = "A" if record.side == "A" else "B"

                self.buffer.add(ts, price, size, side)

                event = {
                    "ts": ts.isoformat(),
                    "price": price,
                    "size": size,
                    "side": side,
                    "cvd": self.buffer.cvd,
                    "delta_1m": self.buffer.delta_1m,
                }

                for q in self._subscribers:
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass  # Drop if subscriber is slow

        except Exception as e:
            logger.error("Databento stream error: %s", e)
            if self._running:
                await asyncio.sleep(5)
                asyncio.create_task(self._stream_loop())  # Reconnect
```

- [ ] **Step 3: Verify import**

Run: `cd backend && python -c "from src.market_data.stream import DabentoLiveStream; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add backend/src/market_data/stream.py
git commit -m "feat: add Databento live stream client with tick buffer"
```

---

### Task 4: Historical Data Fetch Utility

**Files:**
- Create: `backend/src/market_data/history.py`

- [ ] **Step 1: Create history.py**

```python
"""Databento REST historical data fetch utilities."""
import logging
from datetime import datetime, date
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OHLCVBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


async def fetch_ohlcv_1d(
    api_key: str,
    symbol: str = "NQ.FUT",
    start: date | None = None,
    end: date | None = None,
    dataset: str = "GLBX.MDP3",
) -> list[OHLCVBar]:
    """Fetch daily OHLCV bars from Databento historical API."""
    import databento as db

    client = db.Historical(key=api_key)
    data = client.timeseries.get_range(
        dataset=dataset,
        symbols=[symbol],
        schema="ohlcv-1d",
        start=start.isoformat() if start else "2020-01-01",
        end=end.isoformat() if end else datetime.utcnow().strftime("%Y-%m-%d"),
    )
    bars = []
    for record in data:
        bars.append(OHLCVBar(
            ts=datetime.fromtimestamp(record.ts_event / 1e9),
            open=record.open / 1e9,
            high=record.high / 1e9,
            low=record.low / 1e9,
            close=record.close / 1e9,
            volume=record.volume,
        ))
    return bars


async def fetch_ohlcv_1m(
    api_key: str,
    symbol: str = "NQ.FUT",
    start: date | None = None,
    end: date | None = None,
    dataset: str = "GLBX.MDP3",
) -> list[OHLCVBar]:
    """Fetch 1-minute OHLCV bars from Databento historical API."""
    import databento as db

    client = db.Historical(key=api_key)
    data = client.timeseries.get_range(
        dataset=dataset,
        symbols=[symbol],
        schema="ohlcv-1m",
        start=start.isoformat() if start else "2025-01-01",
        end=end.isoformat() if end else datetime.utcnow().strftime("%Y-%m-%d"),
    )
    bars = []
    for record in data:
        bars.append(OHLCVBar(
            ts=datetime.fromtimestamp(record.ts_event / 1e9),
            open=record.open / 1e9,
            high=record.high / 1e9,
            low=record.low / 1e9,
            close=record.close / 1e9,
            volume=record.volume,
        ))
    return bars
```

- [ ] **Step 2: Verify import**

Run: `cd backend && python -c "from src.market_data.history import fetch_ohlcv_1d, fetch_ohlcv_1m; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add backend/src/market_data/history.py
git commit -m "feat: add Databento historical OHLCV fetch utility"
```

---

### Task 5: COT Report Fetcher

**Files:**
- Create: `backend/src/market_data/cot.py`

- [ ] **Step 1: Create COT fetcher**

```python
"""CFTC Commitment of Traders report fetcher."""
import logging
from dataclasses import dataclass
from datetime import date

import httpx

logger = logging.getLogger(__name__)

COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
# NQ / E-mini Nasdaq-100 CFTC code
NQ_CFTC_CODE = "209742"


@dataclass
class COTReport:
    report_date: date
    net_commercial: int
    net_non_commercial: int
    net_non_reportable: int
    open_interest: int


async def fetch_cot(cftc_code: str = NQ_CFTC_CODE, limit: int = 4) -> list[COTReport]:
    """Fetch latest COT reports for an instrument from CFTC Socrata API."""
    params = {
        "$where": f"cftc_contract_market_code='{cftc_code}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(limit),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(COT_URL, params=params)
            resp.raise_for_status()
            rows = resp.json()

        reports = []
        for row in rows:
            reports.append(COTReport(
                report_date=date.fromisoformat(row.get("report_date_as_yyyy_mm_dd", "")[:10]),
                net_commercial=int(row.get("comm_positions_long_all", 0)) - int(row.get("comm_positions_short_all", 0)),
                net_non_commercial=int(row.get("noncomm_positions_long_all", 0)) - int(row.get("noncomm_positions_short_all", 0)),
                net_non_reportable=int(row.get("nonrept_positions_long_all", 0)) - int(row.get("nonrept_positions_short_all", 0)),
                open_interest=int(row.get("open_interest_all", 0)),
            ))
        return reports
    except Exception as e:
        logger.error("COT fetch failed: %s", e)
        return []
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/cot.py
git commit -m "feat: add CFTC COT report fetcher"
```

---

## Chunk 2: Phase 2 — Orderflow Signals + Phase 3 — Level Engine

### Task 6: Orderflow Confirmation Module

**Files:**
- Create: `backend/src/market_data/orderflow.py`

- [ ] **Step 1: Create orderflow.py with delta, CVD, VSA computations**

```python
"""L2 orderflow confirmation signals computed from tick data."""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CandleFlow:
    """Orderflow data for a single candle."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    buy_volume: int
    sell_volume: int
    delta: int  # buy_volume - sell_volume
    tick_count: int
    spread: float  # high - low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_ratio(self) -> float:
        """Body as fraction of spread. Low ratio + high volume = absorption."""
        return self.body / self.spread if self.spread > 0 else 0


@dataclass
class OrderflowSignals:
    """Aggregated orderflow confirmation signals."""
    delta: int
    delta_aligned: bool        # Delta matches expected trade direction
    delta_divergence: bool     # Price vs delta disagree
    delta_unwind: bool         # Rapid delta flip at extreme
    cvd: int
    cvd_trend: str             # "rising", "falling", "flat"
    vsa_absorption: bool       # High volume + narrow spread
    tick_vol_accelerating: bool
    trapped_traders: bool


def build_candle_flow(ticks: list[dict], period_seconds: int = 60) -> list[CandleFlow]:
    """Build CandleFlow bars from raw ticks, grouped by period."""
    if not ticks:
        return []

    candles = []
    current_ticks = []
    period_start = None

    for tick in ticks:
        ts = tick["ts"] if isinstance(tick["ts"], datetime) else datetime.fromisoformat(tick["ts"])
        if period_start is None:
            period_start = ts.replace(second=0, microsecond=0)

        # Check if tick belongs to new period
        elapsed = (ts - period_start).total_seconds()
        if elapsed >= period_seconds and current_ticks:
            candles.append(_aggregate_candle(current_ticks, period_start))
            period_start = ts.replace(second=0, microsecond=0)
            current_ticks = []

        current_ticks.append(tick)

    if current_ticks and period_start:
        candles.append(_aggregate_candle(current_ticks, period_start))

    return candles


def _aggregate_candle(ticks: list[dict], ts: datetime) -> CandleFlow:
    prices = [t["price"] for t in ticks]
    buy_vol = sum(t["size"] for t in ticks if t["side"] == "A")
    sell_vol = sum(t["size"] for t in ticks if t["side"] == "B")
    return CandleFlow(
        ts=ts,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        volume=buy_vol + sell_vol,
        buy_volume=buy_vol,
        sell_volume=sell_vol,
        delta=buy_vol - sell_vol,
        tick_count=len(ticks),
        spread=max(prices) - min(prices),
    )


def compute_signals(
    candles: list[CandleFlow],
    direction: str,  # "long" or "short"
    lookback: int = 10,
) -> OrderflowSignals:
    """Compute orderflow confirmation signals from recent candle flow data."""
    if len(candles) < 3:
        return OrderflowSignals(
            delta=0, delta_aligned=False, delta_divergence=False,
            delta_unwind=False, cvd=0, cvd_trend="flat",
            vsa_absorption=False, tick_vol_accelerating=False,
            trapped_traders=False,
        )

    recent = candles[-lookback:] if len(candles) >= lookback else candles
    last = recent[-1]
    prev = recent[-2]

    # Delta
    total_delta = sum(c.delta for c in recent)
    delta_aligned = (total_delta > 0 and direction == "long") or (total_delta < 0 and direction == "short")

    # Delta divergence: price making new extreme but delta not confirming
    price_up = last.close > prev.close
    delta_positive = last.delta > 0
    delta_divergence = (price_up and not delta_positive) or (not price_up and delta_positive)

    # Delta unwind: sign flipped from previous candle + magnitude > 50% of prev
    delta_unwind = (
        (last.delta > 0 and prev.delta < 0 and abs(last.delta) > abs(prev.delta) * 0.5) or
        (last.delta < 0 and prev.delta > 0 and abs(last.delta) > abs(prev.delta) * 0.5)
    )

    # CVD
    cvd = sum(c.delta for c in recent)
    if len(recent) >= 5:
        cvd_first_half = sum(c.delta for c in recent[:len(recent)//2])
        cvd_second_half = sum(c.delta for c in recent[len(recent)//2:])
        if cvd_second_half > cvd_first_half * 1.2:
            cvd_trend = "rising"
        elif cvd_second_half < cvd_first_half * 0.8:
            cvd_trend = "falling"
        else:
            cvd_trend = "flat"
    else:
        cvd_trend = "flat"

    # VSA absorption: high volume + narrow body (body_ratio < 0.3) on last candle
    avg_volume = sum(c.volume for c in recent) / len(recent)
    vsa_absorption = last.volume > avg_volume * 1.5 and last.body_ratio < 0.3

    # Tick volume acceleration
    if len(recent) >= 4:
        recent_tick_avg = sum(c.tick_count for c in recent[-3:]) / 3
        prior_tick_avg = sum(c.tick_count for c in recent[:-3]) / max(1, len(recent) - 3)
        tick_vol_accelerating = recent_tick_avg > prior_tick_avg * 1.3
    else:
        tick_vol_accelerating = False

    # Trapped traders: delta flipped after aggressive move in one direction
    trapped_traders = delta_unwind and abs(last.delta) > avg_volume * 0.3

    return OrderflowSignals(
        delta=total_delta,
        delta_aligned=delta_aligned,
        delta_divergence=delta_divergence,
        delta_unwind=delta_unwind,
        cvd=cvd,
        cvd_trend=cvd_trend,
        vsa_absorption=vsa_absorption,
        tick_vol_accelerating=tick_vol_accelerating,
        trapped_traders=trapped_traders,
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/orderflow.py
git commit -m "feat: add orderflow confirmation module (delta, CVD, VSA, trapped traders)"
```

---

### Task 7: Session Metrics Module

**Files:**
- Create: `backend/src/market_data/metrics.py`

- [ ] **Step 1: Create metrics.py — RF, ASPR, range baselines**

```python
"""Session metrics: Rotation Factor, ASPR, range baselines."""
from dataclasses import dataclass


@dataclass
class SessionMetrics:
    rotation_factor: int
    aspr: float
    aspr_percentile: float | None  # vs historical baseline


def compute_rotation_factor(highs: list[float], lows: list[float]) -> int:
    """Compute Rotation Factor from sequential 30-min period highs/lows.

    Per 30-min period:
      current high > prev high → +1
      current high < prev high → -1
      current low > prev low → +1
      current low < prev low → -1
    """
    if len(highs) < 2:
        return 0

    rf = 0
    for i in range(1, len(highs)):
        if highs[i] > highs[i - 1]:
            rf += 1
        elif highs[i] < highs[i - 1]:
            rf -= 1

        if lows[i] > lows[i - 1]:
            rf += 1
        elif lows[i] < lows[i - 1]:
            rf -= 1
    return rf


def compute_aspr(ranges: list[float]) -> float:
    """Average Sub-Period Range from 30-min candle ranges."""
    if not ranges:
        return 0.0
    return sum(ranges) / len(ranges)


def compute_aspr_percentile(current_aspr: float, historical_asprs: list[float]) -> float:
    """Where current ASPR falls in historical distribution (0.0 = lowest, 1.0 = highest)."""
    if not historical_asprs:
        return 0.5
    below = sum(1 for h in historical_asprs if h <= current_aspr)
    return below / len(historical_asprs)


def detect_value_migration(
    today_vah: float, today_val: float,
    yesterday_vah: float, yesterday_val: float,
) -> str:
    """Detect value area migration: up, down, or overlapping."""
    if today_val > yesterday_val and today_vah > yesterday_vah:
        return "up"
    elif today_val < yesterday_val and today_vah < yesterday_vah:
        return "down"
    else:
        return "overlapping"
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/metrics.py
git commit -m "feat: add session metrics module (RF, ASPR, value migration)"
```

---

### Task 8: Level Engine

**Files:**
- Create: `backend/src/market_data/levels.py`

- [ ] **Step 1: Create levels.py — VP, VWAP, session levels, IB**

```python
"""Level engine: computes all structural levels from bar/tick data."""
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("US/Eastern")


@dataclass
class VolumeProfileLevel:
    price: float
    volume: int


@dataclass
class VolumeProfile:
    poc: float  # Price of control (highest volume)
    vah: float  # Value area high (70% volume boundary)
    val: float  # Value area low
    levels: list[VolumeProfileLevel] = field(default_factory=list)
    single_prints: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class VWAPBands:
    vwap: float
    sd1_upper: float
    sd1_lower: float
    sd2_upper: float
    sd2_lower: float
    sd3_upper: float
    sd3_lower: float


@dataclass
class SessionLevels:
    pdh: float | None = None
    pdl: float | None = None
    tokyo_high: float | None = None
    tokyo_low: float | None = None
    london_high: float | None = None
    london_low: float | None = None
    ib_high: float | None = None
    ib_low: float | None = None


def compute_volume_profile(
    trades: list[dict],
    tick_size: float = 0.25,
) -> VolumeProfile:
    """Build volume profile from trade ticks. Groups volume into price buckets."""
    if not trades:
        return VolumeProfile(poc=0, vah=0, val=0)

    # Bucket volume by price level
    buckets: dict[float, int] = {}
    for t in trades:
        price = round(t["price"] / tick_size) * tick_size
        buckets[price] = buckets.get(price, 0) + t["size"]

    if not buckets:
        return VolumeProfile(poc=0, vah=0, val=0)

    # POC = price with highest volume
    poc = max(buckets, key=buckets.get)
    total_volume = sum(buckets.values())

    # Value Area = 70% of total volume, expanding outward from POC
    sorted_prices = sorted(buckets.keys())
    poc_idx = sorted_prices.index(poc)
    va_volume = buckets[poc]
    va_target = total_volume * 0.70
    lo_idx = poc_idx
    hi_idx = poc_idx

    while va_volume < va_target and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        expand_up = buckets.get(sorted_prices[min(hi_idx + 1, len(sorted_prices) - 1)], 0) if hi_idx < len(sorted_prices) - 1 else 0
        expand_down = buckets.get(sorted_prices[max(lo_idx - 1, 0)], 0) if lo_idx > 0 else 0

        if expand_up >= expand_down and hi_idx < len(sorted_prices) - 1:
            hi_idx += 1
            va_volume += buckets[sorted_prices[hi_idx]]
        elif lo_idx > 0:
            lo_idx -= 1
            va_volume += buckets[sorted_prices[lo_idx]]
        else:
            hi_idx = min(hi_idx + 1, len(sorted_prices) - 1)
            va_volume += buckets.get(sorted_prices[hi_idx], 0)

    vah = sorted_prices[hi_idx]
    val = sorted_prices[lo_idx]

    # Detect single prints: prices with volume < 5% of POC volume
    poc_vol = buckets[poc]
    single_prints = []
    for i in range(1, len(sorted_prices)):
        if buckets[sorted_prices[i]] < poc_vol * 0.05:
            single_prints.append((sorted_prices[i], sorted_prices[i]))

    levels = [VolumeProfileLevel(price=p, volume=v) for p, v in sorted(buckets.items())]

    return VolumeProfile(poc=poc, vah=vah, val=val, levels=levels, single_prints=single_prints)


def compute_vwap_bands(trades: list[dict]) -> VWAPBands | None:
    """Compute VWAP + 1/2/3 SD bands from trade ticks."""
    if not trades:
        return None

    cum_pv = 0.0
    cum_vol = 0
    cum_pv2 = 0.0

    for t in trades:
        p = t["price"]
        v = t["size"]
        cum_pv += p * v
        cum_vol += v
        cum_pv2 += p * p * v

    if cum_vol == 0:
        return None

    vwap = cum_pv / cum_vol
    variance = (cum_pv2 / cum_vol) - (vwap * vwap)
    sd = math.sqrt(max(0, variance))

    return VWAPBands(
        vwap=vwap,
        sd1_upper=vwap + sd,
        sd1_lower=vwap - sd,
        sd2_upper=vwap + 2 * sd,
        sd2_lower=vwap - 2 * sd,
        sd3_upper=vwap + 3 * sd,
        sd3_lower=vwap - 3 * sd,
    )


def compute_session_levels(
    bars_1m: list[dict],
    session_date: datetime,
) -> SessionLevels:
    """Compute PDH/PDL, Tokyo/London H/L, IB from 1-minute bars.

    All session boundaries in US/Eastern time:
    - Tokyo: 20:00 - 00:00 ET (prior evening)
    - London: 03:00 - 08:30 ET
    - IB: 09:30 - 10:30 ET (first 60 min of RTH)
    - PDH/PDL: prior calendar day's RTH range
    """
    levels = SessionLevels()
    if not bars_1m:
        return levels

    today_et = session_date.astimezone(ET).date() if isinstance(session_date, datetime) else session_date

    for bar in bars_1m:
        bar_ts = bar["ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        bar_et = bar_ts.astimezone(ET)
        bar_date = bar_et.date()
        bar_time = bar_et.time()
        h, l = bar["high"], bar["low"]

        # PDH/PDL: yesterday's RTH (09:30-16:00)
        yesterday = today_et - timedelta(days=1)
        if bar_date == yesterday and time(9, 30) <= bar_time < time(16, 0):
            levels.pdh = max(levels.pdh or h, h)
            levels.pdl = min(levels.pdl or l, l)

        # Tokyo: 20:00-00:00 ET on prior evening
        if bar_date == yesterday and bar_time >= time(20, 0):
            levels.tokyo_high = max(levels.tokyo_high or h, h)
            levels.tokyo_low = min(levels.tokyo_low or l, l)
        elif bar_date == today_et and bar_time < time(0, 0):
            levels.tokyo_high = max(levels.tokyo_high or h, h)
            levels.tokyo_low = min(levels.tokyo_low or l, l)

        # London: 03:00-08:30 ET
        if bar_date == today_et and time(3, 0) <= bar_time < time(8, 30):
            levels.london_high = max(levels.london_high or h, h)
            levels.london_low = min(levels.london_low or l, l)

        # IB: 09:30-10:30 ET
        if bar_date == today_et and time(9, 30) <= bar_time < time(10, 30):
            levels.ib_high = max(levels.ib_high or h, h)
            levels.ib_low = min(levels.ib_low or l, l)

    return levels
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/levels.py
git commit -m "feat: add level engine (VP, VWAP bands, session levels, IB)"
```

---

### Task 9: TPO / Market Profile Module

**Files:**
- Create: `backend/src/market_data/tpo.py`

- [ ] **Step 1: Create tpo.py — 30-min brackets, single prints, ledges, poor extremes**

```python
"""TPO / Market Profile computation: 30-min brackets, anomalies."""
from dataclasses import dataclass, field
from datetime import datetime
import string


@dataclass
class TPOProfile:
    """Time Price Opportunity profile for a session."""
    letters: dict[float, list[str]]  # price → [A, B, C, ...]
    poc: float  # Price with most TPO letters
    vah: float
    val: float
    single_prints: list[float]  # Prices with only 1 letter
    ledges: list[float]  # Prices where profile cuts off abruptly
    poor_high: bool  # Thin tail at session high
    poor_low: bool   # Thin tail at session low
    ib_tpo_count: int  # Letters in first 2 brackets (A+B)


TPO_LETTERS = list(string.ascii_uppercase)


def compute_tpo_profile(
    bars_30m: list[dict],
    tick_size: float = 0.25,
) -> TPOProfile:
    """Build TPO profile from 30-min OHLCV bars.

    Each 30-min period gets a letter (A, B, C...).
    Each price level touched in that period gets that letter.
    """
    if not bars_30m:
        return TPOProfile(
            letters={}, poc=0, vah=0, val=0,
            single_prints=[], ledges=[], poor_high=False, poor_low=False, ib_tpo_count=0,
        )

    letters: dict[float, list[str]] = {}

    for i, bar in enumerate(bars_30m):
        letter = TPO_LETTERS[i] if i < len(TPO_LETTERS) else TPO_LETTERS[-1]
        low = round(bar["low"] / tick_size) * tick_size
        high = round(bar["high"] / tick_size) * tick_size

        price = low
        while price <= high + tick_size / 2:
            rounded = round(price / tick_size) * tick_size
            if rounded not in letters:
                letters[rounded] = []
            if letter not in letters[rounded]:
                letters[rounded].append(letter)
            price += tick_size

    if not letters:
        return TPOProfile(
            letters={}, poc=0, vah=0, val=0,
            single_prints=[], ledges=[], poor_high=False, poor_low=False, ib_tpo_count=0,
        )

    # POC = price with most letters
    poc = max(letters, key=lambda p: len(letters[p]))
    total_tpos = sum(len(v) for v in letters.values())

    # Value area = 70% of total TPOs
    sorted_prices = sorted(letters.keys())
    poc_idx = sorted_prices.index(poc)
    va_count = len(letters[poc])
    va_target = total_tpos * 0.70
    lo_idx = poc_idx
    hi_idx = poc_idx

    while va_count < va_target and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        up_count = len(letters.get(sorted_prices[min(hi_idx + 1, len(sorted_prices) - 1)], [])) if hi_idx < len(sorted_prices) - 1 else 0
        dn_count = len(letters.get(sorted_prices[max(lo_idx - 1, 0)], [])) if lo_idx > 0 else 0

        if up_count >= dn_count and hi_idx < len(sorted_prices) - 1:
            hi_idx += 1
            va_count += len(letters[sorted_prices[hi_idx]])
        elif lo_idx > 0:
            lo_idx -= 1
            va_count += len(letters[sorted_prices[lo_idx]])
        else:
            break

    vah = sorted_prices[hi_idx]
    val = sorted_prices[lo_idx]

    # Single prints: prices with exactly 1 letter
    single_prints = [p for p in sorted_prices if len(letters[p]) == 1]

    # Ledges: abrupt cutoff — price has 6+ fewer TPOs than its neighbor
    ledges = []
    for i in range(1, len(sorted_prices)):
        diff = abs(len(letters[sorted_prices[i]]) - len(letters[sorted_prices[i-1]]))
        if diff >= 6:
            ledges.append(sorted_prices[i])

    # Poor high/low: top/bottom 3 prices have ≤ 2 total letters
    top_3 = sorted_prices[-3:] if len(sorted_prices) >= 3 else sorted_prices
    bottom_3 = sorted_prices[:3] if len(sorted_prices) >= 3 else sorted_prices
    poor_high = sum(len(letters[p]) for p in top_3) <= 2
    poor_low = sum(len(letters[p]) for p in bottom_3) <= 2

    # IB TPO count: letters A and B
    ib_tpo_count = sum(1 for p in sorted_prices for l in letters[p] if l in ("A", "B"))

    return TPOProfile(
        letters=letters, poc=poc, vah=vah, val=val,
        single_prints=single_prints, ledges=ledges,
        poor_high=poor_high, poor_low=poor_low,
        ib_tpo_count=ib_tpo_count,
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/tpo.py
git commit -m "feat: add TPO/Market Profile module (brackets, single prints, ledges, poor extremes)"
```

---

### Task 10: SSE Stream Endpoint + Frontend Hook

**Files:**
- Modify: `backend/src/api/routes/market.py`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/types/market.ts`

- [ ] **Step 1: Add SSE endpoint to market routes**

Add to `backend/src/api/routes/market.py`:

```python
from sse_starlette.sse import EventSourceResponse
import asyncio, json

@router.get("/stream")
async def market_stream(symbol: str = "NQ"):
    """SSE stream of real-time tick data, candles, and level touches."""
    from src.market_data.stream import DabentoLiveStream

    # Get or create the singleton stream
    stream = _get_live_stream()
    if not stream:
        return {"error": "Live stream not available"}

    queue = stream.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": "tick", "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        except asyncio.CancelledError:
            stream.unsubscribe(queue)

    return EventSourceResponse(event_generator())
```

- [ ] **Step 2: Add StreamEvent type to frontend**

Add to `frontend/src/types/market.ts`:

```typescript
export interface StreamTickEvent {
  ts: string;
  price: number;
  size: number;
  side: 'A' | 'B';
  cvd: number;
  delta_1m: number;
}
```

- [ ] **Step 3: Add useMarketStream hook**

Create `frontend/src/hooks/useMarketStream.ts`:

```typescript
import { useState, useEffect, useRef } from 'react';
import type { StreamTickEvent } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(`/api/trading/market/stream?symbol=${symbol}`);
    esRef.current = es;

    es.addEventListener('tick', (e) => {
      setLastTick(JSON.parse(e.data));
    });
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [symbol]);

  return { lastTick, connected };
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/market.py frontend/src/types/market.ts frontend/src/hooks/useMarketStream.ts
git commit -m "feat: add SSE stream endpoint and useMarketStream frontend hook"
```

---

## Chunk 3: Phase 4 — Context Gates + Phase 5 — Setup Detector

### Task 11: Context Gate API + UI

**Files:**
- Modify: `backend/src/api/routes/market.py`
- Modify: `backend/src/services/market_service.py`
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add context endpoints to market routes**

```python
@router.get("/context")
async def get_context(symbol: str = "NQ", svc: MarketService = Depends(_svc)):
    ctx = svc.repo.get_context(symbol)
    if not ctx:
        return {"symbol": symbol, "gates_set": False}
    return {
        "symbol": ctx.symbol,
        "gates_set": True,
        "macro_bias": ctx.macro_bias,
        "risk_mode": ctx.risk_mode,
        "cycle_phase": ctx.cycle_phase,
        "structure": ctx.structure,
        "structure_hl": ctx.structure_hl,
        "structure_lh": ctx.structure_lh,
        "day_type": ctx.day_type,
        "vp_old_macro_start": ctx.vp_old_macro_start,
        "vp_ongoing_macro_start": ctx.vp_ongoing_macro_start,
        "vp_leg_start": ctx.vp_leg_start,
    }

@router.put("/context")
async def update_context(data: dict, symbol: str = "NQ", svc: MarketService = Depends(_svc)):
    ctx = svc.repo.upsert_context(symbol, data)
    return {"status": "ok", "symbol": symbol}
```

- [ ] **Step 2: Add frontend types + API methods**

Add to `frontend/src/types/market.ts`:

```typescript
export interface MarketContext {
  symbol: string;
  gates_set: boolean;
  macro_bias?: 'bull' | 'bear' | 'neutral';
  risk_mode?: 'risk_on' | 'risk_off' | 'mixed';
  cycle_phase?: 'early' | 'mid' | 'late' | 'recession';
  structure?: 'uptrend' | 'downtrend' | 'ranging';
  structure_hl?: number;
  structure_lh?: number;
  day_type?: 'trend' | 'normal' | 'normal_variation' | 'neutral' | 'composite';
  vp_old_macro_start?: number;
  vp_ongoing_macro_start?: number;
  vp_leg_start?: number;
}
```

Add to `api.ts` market section:

```typescript
async getMarketContext(symbol = 'NQ'): Promise<MarketContext> {
  const res = await fetch(`${this.base}/api/trading/market/context?symbol=${symbol}`);
  return res.json();
},
async updateMarketContext(data: Partial<MarketContext>, symbol = 'NQ') {
  const res = await fetch(`${this.base}/api/trading/market/context?symbol=${symbol}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return res.json();
},
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/market.py backend/src/services/market_service.py frontend/src/types/market.ts frontend/src/services/api.ts
git commit -m "feat: add context gate API + frontend types"
```

---

### Task 12: Setup Detector Package

**Files:**
- Create: `backend/src/market_data/setups/__init__.py`
- Create: `backend/src/market_data/setups/detector.py`
- Create: `backend/src/market_data/setups/poor_extreme.py` (first, simplest setup)

- [ ] **Step 1: Create package init**

```python
# backend/src/market_data/setups/__init__.py
"""Setup detection package — one module per named setup type."""
```

- [ ] **Step 2: Create detector.py — orchestrator**

```python
"""Setup detector orchestrator: runs all individual detectors and returns scored results."""
from dataclasses import dataclass
from datetime import datetime

from src.market_data.levels import VolumeProfile, VWAPBands, SessionLevels
from src.market_data.tpo import TPOProfile
from src.market_data.orderflow import OrderflowSignals


@dataclass
class SetupCandidate:
    """A detected setup opportunity."""
    setup_type: str        # "spring", "sfp", "poor_extreme", etc.
    setup_name: str        # Human readable: "Poor High Reversal"
    direction: str         # "long" or "short"
    level_touched: str     # Which level triggered: "vah", "pdh", "ib_high", etc.
    entry_price: float
    stop_price: float
    target_1: float
    target_2: float | None = None
    target_3: float | None = None
    base_score: float = 65.0  # Default until historical win rate available
    detected_at: datetime | None = None


@dataclass
class DetectorContext:
    """All data a setup detector needs."""
    vp: VolumeProfile
    vwap: VWAPBands | None
    session_levels: SessionLevels
    tpo: TPOProfile
    orderflow: OrderflowSignals
    last_price: float
    # Context gates
    macro_bias: str | None  # "bull", "bear", "neutral"
    structure: str | None   # "uptrend", "downtrend", "ranging"
    day_type: str | None    # "trend", "normal", etc.


def run_all_detectors(ctx: DetectorContext) -> list[SetupCandidate]:
    """Run all setup detectors and return candidates."""
    from src.market_data.setups.poor_extreme import detect_poor_extreme
    from src.market_data.setups.ib_break import detect_ib_break
    from src.market_data.setups.spring import detect_spring
    from src.market_data.setups.sfp import detect_sfp
    from src.market_data.setups.rule_80 import detect_rule_80
    from src.market_data.setups.fakeout import detect_fakeout
    from src.market_data.setups.break_from_balance import detect_break_from_balance
    from src.market_data.setups.double_distribution import detect_double_distribution
    from src.market_data.setups.news_directional import detect_news_directional

    detectors = [
        detect_poor_extreme,
        detect_ib_break,
        detect_spring,
        detect_sfp,
        detect_rule_80,
        detect_fakeout,
        detect_break_from_balance,
        detect_double_distribution,
        detect_news_directional,
    ]

    candidates = []
    for detector in detectors:
        try:
            result = detector(ctx)
            if result:
                candidates.extend(result if isinstance(result, list) else [result])
        except Exception:
            pass  # Individual detector failure shouldn't block others

    return candidates
```

- [ ] **Step 3: Create poor_extreme.py — first setup detector**

```python
"""Poor Extreme setup detector.

Trigger: Session makes new high/low on volume significantly below average;
thin tail in TPO profile.
"""
from src.market_data.setups.detector import DetectorContext, SetupCandidate


def detect_poor_extreme(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect poor extreme setups from TPO profile."""
    candidates = []

    # Check if day type is appropriate (not trend days)
    if ctx.day_type == "trend":
        return []

    # Poor high → short setup
    if ctx.tpo.poor_high and ctx.macro_bias != "bull":
        stop = ctx.session_levels.ib_high or ctx.vp.vah
        if stop and ctx.last_price > 0:
            candidates.append(SetupCandidate(
                setup_type="poor_extreme",
                setup_name="Poor High Reversal",
                direction="short",
                level_touched="session_high",
                entry_price=ctx.last_price,
                stop_price=stop * 1.001,  # Slightly above
                target_1=ctx.vp.poc,
                target_2=ctx.vp.val,
                target_3=ctx.session_levels.pdl,
                base_score=75.0,
            ))

    # Poor low → long setup
    if ctx.tpo.poor_low and ctx.macro_bias != "bear":
        stop = ctx.session_levels.ib_low or ctx.vp.val
        if stop and ctx.last_price > 0:
            candidates.append(SetupCandidate(
                setup_type="poor_extreme",
                setup_name="Poor Low Reversal",
                direction="long",
                level_touched="session_low",
                entry_price=ctx.last_price,
                stop_price=stop * 0.999,
                target_1=ctx.vp.poc,
                target_2=ctx.vp.vah,
                target_3=ctx.session_levels.pdh,
                base_score=75.0,
            ))

    return candidates
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/market_data/setups/
git commit -m "feat: add setup detector package with orchestrator + poor_extreme"
```

---

### Task 13–19: Remaining Setup Detectors

Each remaining setup detector follows the same pattern as `poor_extreme.py`. Create one file per setup:

- [ ] **Step 1: Create `ib_break.py`** — IB Break: price exits first 60-min range with delta + tick vol confirmation
- [ ] **Step 2: Create `spring.py`** — Spring: minor penetration below support, low volume, delta snap-back
- [ ] **Step 3: Create `sfp.py`** — SFP: price breaks swing H/L, CLOSES back inside, delta unwind + trapped traders
- [ ] **Step 4: Create `rule_80.py`** — 80% Rule: opens outside prior VA, trades back inside for 2 TPO periods, targets opposite VA extreme
- [ ] **Step 5: Create `fakeout.py`** — Fakeout: convincing break reverses, delta divergence, POC/VWAP holds
- [ ] **Step 6: Create `break_from_balance.py`** — Break from Balance: 3+ days overlapping VAs, ASPR compressed, delta sustained
- [ ] **Step 7: Create `double_distribution.py`** — Double Distribution: 2 VP peaks, secondary weaker, rotation to primary
- [ ] **Step 8: Create `news_directional.py`** — News: pre-scheduled release, directional candle post-spike, VSA on M1
- [ ] **Step 9: Commit all**

```bash
git add backend/src/market_data/setups/
git commit -m "feat: add all 9 setup detectors"
```

Each detector takes `DetectorContext` and returns `list[SetupCandidate]` or empty list. All follow the spec's entry/SL/TP rules from Section 6 of the design doc.

---

## Chunk 4: Phase 6 — Scoring + Risk + UI

### Task 20: Scoring Model

**Files:**
- Create: `backend/src/market_data/scoring.py`

- [ ] **Step 1: Create scoring.py**

```python
"""Scoring model: combines setup base score with confirmation adjustments."""
from src.market_data.setups.detector import SetupCandidate, DetectorContext
from src.market_data.orderflow import OrderflowSignals


def score_candidate(
    candidate: SetupCandidate,
    orderflow: OrderflowSignals,
    day_type_fits: bool,
    macro_aligned: bool,
    rf: int | None = None,
    aspr_percentile: float | None = None,
) -> float:
    """Apply adjustment factors to candidate's base score.

    Returns final score (0-100). Only surface to UI if >= 70.
    """
    score = candidate.base_score

    # Delta/CVD alignment
    if orderflow.delta_aligned:
        score += 10
    # Delta divergence (for reversal setups)
    if orderflow.delta_divergence and candidate.setup_type in ("spring", "sfp", "poor_extreme", "fakeout"):
        score += 10
    # VSA absorption
    if orderflow.vsa_absorption:
        score += 10
    # Tick volume
    if orderflow.tick_vol_accelerating:
        score += 8
    # Day type fit
    if day_type_fits:
        score += 10
    else:
        score -= 20
    # Macro alignment
    if macro_aligned:
        score += 5
    # Trapped traders
    if orderflow.trapped_traders:
        score += 8
    # RF/ASPR session context
    if rf is not None and aspr_percentile is not None:
        if aspr_percentile < 0.2:  # Compressed = breakout setups stronger
            if candidate.setup_type in ("ib_break", "break_from_balance"):
                score += 5

    return max(0, min(100, score))


def day_type_fits_setup(day_type: str | None, setup_type: str) -> bool:
    """Check if setup type is valid for the day type."""
    if not day_type:
        return True  # Unknown = allow

    trend_setups = {"ib_break", "break_from_balance"}
    reversal_setups = {"spring", "sfp", "poor_extreme", "rule_80", "fakeout", "double_distribution"}

    if day_type == "trend":
        return setup_type in trend_setups or setup_type == "news_directional"
    elif day_type in ("normal", "normal_variation"):
        return setup_type in reversal_setups or setup_type == "news_directional"
    elif day_type == "neutral":
        return setup_type in ("break_from_balance", "news_directional")

    return True  # composite = anything goes
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/scoring.py
git commit -m "feat: add scoring model with confirmation adjustments"
```

---

### Task 21: Risk Calculator

**Files:**
- Modify: `backend/src/market_data/setups/detector.py` (add R:R computation)

- [ ] **Step 1: Add R:R computation to SetupCandidate**

Add method to `SetupCandidate`:

```python
@property
def rr_tp1(self) -> float | None:
    if not self.stop_price or not self.target_1 or not self.entry_price:
        return None
    risk = abs(self.entry_price - self.stop_price)
    if risk == 0:
        return None
    reward = abs(self.target_1 - self.entry_price)
    return round(reward / risk, 2)

@property
def rr_tp2(self) -> float | None:
    if not self.stop_price or not self.target_2 or not self.entry_price:
        return None
    risk = abs(self.entry_price - self.stop_price)
    if risk == 0:
        return None
    reward = abs(self.target_2 - self.entry_price)
    return round(reward / risk, 2)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/setups/detector.py
git commit -m "feat: add R:R computation to SetupCandidate"
```

---

### Task 22: Rename Scanner Tab to Intraday

**Files:**
- Modify: `frontend/src/components/Terminal/Sidebar.tsx`
- Modify: `frontend/src/components/Terminal/TabBar.tsx`
- Modify: `frontend/src/components/Terminal/TerminalWindow.tsx`
- Rename: `TradingScannerPage.tsx` → `TradingIntradayPage.tsx`

- [ ] **Step 1: Update Sidebar TabName type**

Replace `'tradingScanner'` with `'tradingIntraday'` in the TabName union.

- [ ] **Step 2: Update TabBar**

In `STOCKS_TABS`, rename `tradingScanner` → `tradingIntraday`, label `'Scanner'` → `'Intraday'`.
In `TAB_COLORS`, rename `tradingScanner` → `tradingIntraday`.
In `DEFAULT_TAB`, update stocks default.

- [ ] **Step 3: Rename page file and update component name**

```bash
mv frontend/src/components/Terminal/pages/TradingScannerPage.tsx frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
```

Rename export from `TradingScannerPage` to `TradingIntradayPage` inside the file.

- [ ] **Step 4: Update TerminalWindow lazy import + switch case**

Update the lazy import path and component name. Update `renderPage()` switch from `tradingScanner` to `tradingIntraday`.

- [ ] **Step 5: Verify frontend compiles**

Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/
git commit -m "refactor: rename trading Scanner tab to Intraday"
```

---

### Task 23: Update Intraday Page with Layer A Gates + Level Panel

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Add Layer A context gate cards above existing confirmation strip**

Add 3 cards (Gate 1: Macro, Gate 2: Structure, Gate 3: Day Type) that read from `getMarketContext()` and write via `updateMarketContext()`. Each card has a dropdown/select for its value. Show auto-data alongside (COT, VIX, value migration for Gate 1).

- [ ] **Step 2: Add Session Metrics Row**

Below gates, show live: RF, ASPR (vs baseline), IB H/L, VWAP, POC.

- [ ] **Step 3: Update opportunity table columns**

Add columns: `TP2`, `TP3`, `Level Touched`, `R:R`. Show `setup_category` as colored badge.

- [ ] **Step 4: Add expanded row L2 panel**

In the expanded signal row, show live delta/CVD from `useMarketStream()`. Display confirmation signal checklist (delta aligned ✓, VSA absorption ✗, etc.).

- [ ] **Step 5: Add Level Map section**

Below the opportunity table, add a collapsible section listing all active levels from `market_levels` table, grouped by type: VP levels / VWAP bands / Session levels / Order blocks / FVGs / Single prints. Show current price position relative to nearest levels.

- [ ] **Step 6: Verify frontend compiles**

Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat: add Layer A gates, session metrics, updated opportunity table, L2 panel to Intraday page"
```

---

### Task 24: Integration — Wire Everything Together

**Files:**
- Modify: `backend/src/services/market_service.py`

- [ ] **Step 1: Extend compute_session to use new level engine**

In `MarketService.compute_session()`, after existing AMT analysis, call:
- `compute_session_levels()` for PDH/PDL, Tokyo/London/NY H/L
- `compute_tpo_profile()` for TPO anomalies
- `compute_rotation_factor()` and `compute_aspr()` for session metrics
- `detect_value_migration()` from prior session
- Store all new values in `MarketSession` extended columns

- [ ] **Step 2: Extend run_scan to use new setup detectors**

In `MarketService.run_scan()`, build `DetectorContext` from computed session + live orderflow, call `run_all_detectors()`, score each candidate with `score_candidate()`, filter by score ≥ 70, store as `TradingSignal` with new columns populated.

- [ ] **Step 3: Add levels endpoint**

```python
@router.get("/levels")
async def get_levels(symbol: str = "NQ", date: str = None, svc: MarketService = Depends(_svc)):
    levels = svc.repo.get_levels(symbol, date or datetime.utcnow().strftime("%Y-%m-%d"))
    return [{"level_type": l.level_type, "price_low": l.price_low, "price_high": l.price_high,
             "direction": l.direction, "session": l.session, "is_filled": l.is_filled} for l in levels]
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/market_service.py backend/src/api/routes/market.py
git commit -m "feat: wire level engine, setup detectors, and scoring into market service"
```

---

### Task 25: Visual Verification

- [ ] **Step 1: Start backend + frontend dev servers**
- [ ] **Step 2: Navigate to Intraday tab — verify 3 Layer A gate cards + 4 existing auto-gate cards render**
- [ ] **Step 3: Set Gate 1 macro bias to "bull" + "risk_on" — verify saves and persists on refresh**
- [ ] **Step 4: Click "Compute" — verify session data populates (POC, VWAP, IB, session levels, RF, ASPR)**
- [ ] **Step 5: Click "Scan" — verify opportunity table shows any detected setups with score, TP1/TP2/TP3, R:R**
- [ ] **Step 6: Expand a signal row — verify L2 panel shows (may be empty without live stream)**
- [ ] **Step 7: Verify Level Map section shows computed levels grouped by type**
- [ ] **Step 8: Commit any fixes**

```bash
git add -A
git commit -m "fix: address visual verification issues for Intraday page"
```

---

Plan complete and saved to `docs/superpowers/plans/2026-03-12-trading-intraday-system.md`. Ready to execute?
