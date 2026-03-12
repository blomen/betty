# Trading Intraday System — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AMT + Orderflow intraday trading system that auto-detects 9 named setups at computed structural levels, confirmed by L2 orderflow signals, with auto SL/TP and two-step trade entry.

**Architecture:** Extend existing `market_data/` module with live Databento streaming, level engine, setup detectors, and L2 confirmation. Add 3 manual context gates (Layer A) above existing 4 auto-gates (Layer B). SSE stream for real-time L2 data to frontend. Rename Scanner tab to Intraday.

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy / SQLite (backend), React 19 / TypeScript / Vite / Tailwind (frontend), Databento Python SDK (market data), sse-starlette (real-time transport)

**Spec:** `docs/superpowers/specs/2026-03-12-trading-system-design.md`

**Implementation Notes:**
- `_utcnow` helper already exists in `models.py` (defined as `lambda: datetime.now(timezone.utc)`)
- Use **relative imports** within `market_data/` (e.g., `from .levels import ...`, `from .detector import ...`) to match existing codebase style
- Use **relative imports** in `repositories/` (e.g., `from ..db.models import ...`)
- Gate 1 auto-data (VIX, DXY, yields) comes from existing `/api/trading/market/macro` endpoint (`macro_provider.py`)
- `wolfe_wave.py`, `footprint.py`, and funding rate fetcher are **deferred to v2**

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
| `backend/src/market_data/scoring.py` | Scoring model + Kelly position sizing |
| `backend/src/market_data/cot.py` | CFTC COT report weekly fetcher |

> **Deferred to v2:** `wolfe_wave.py` (Wolfe Wave detector), `footprint.py` (footprint chart), funding rate fetcher.

### Backend — Modified Files
| File | Changes |
|---|---|
| `backend/src/db/models.py` | Add `MarketTrade`, `MarketLevel`, `MarketContext`, `SessionMetric` models; extend `MarketSession`, `TradingSignal` |
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

- [ ] **Step 4: Add SessionMetric model (ASPR/RF historical baselines)**

```python
class SessionMetric(Base):
    """Permanent session metrics history for ASPR/RF baselines."""
    __tablename__ = "session_metrics"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD
    rotation_factor = Column(Integer, nullable=True)
    aspr = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_session_metrics_symbol_date"),
        Index("ix_session_metrics_symbol", "symbol"),
    )
```

- [ ] **Step 5a: Extend MarketSession with new columns**

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

- [ ] **Step 5b: Extend TradingSignal with new columns**

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
from src.db.models import MarketTrade, MarketLevel, MarketContext, SessionMetric

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

# --- SessionMetric ---
def upsert_session_metric(self, symbol: str, date: str, rf: int, aspr: float):
    """Insert or update session metric for ASPR/RF baselines."""
    existing = self.db.query(SessionMetric).filter(
        SessionMetric.symbol == symbol,
        SessionMetric.date == date,
    ).first()
    if existing:
        existing.rotation_factor = rf
        existing.aspr = aspr
    else:
        self.db.add(SessionMetric(symbol=symbol, date=date, rotation_factor=rf, aspr=aspr))
    self.db.commit()

def get_historical_asprs(self, symbol: str, limit: int = 20) -> list[float]:
    """Get recent ASPR values for percentile computation."""
    rows = self.db.query(SessionMetric.aspr).filter(
        SessionMetric.symbol == symbol,
        SessionMetric.aspr.isnot(None),
    ).order_by(SessionMetric.date.desc()).limit(limit).all()
    return [r[0] for r in rows]
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


class DatabentoLiveStream:
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

Run: `cd backend && python -c "from src.market_data.stream import DatabentoLiveStream; print('OK')"`

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
from datetime import datetime, date, timezone
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
        end=end.isoformat() if end else datetime.now(timezone.utc).strftime("%Y-%m-%d"),
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
        end=end.isoformat() if end else datetime.now(timezone.utc).strftime("%Y-%m-%d"),
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
    passive_active_ratio: float  # > 1.0 = more passive (limit) orders than aggressive


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
            trapped_traders=False, passive_active_ratio=0.0,
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

    # Passive/active ratio: total volume vs delta magnitude
    total_vol = sum(c.volume for c in recent)
    total_abs_delta = sum(abs(c.delta) for c in recent)
    passive_active_ratio = (total_vol - total_abs_delta) / max(1, total_abs_delta)

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
        passive_active_ratio=round(passive_active_ratio, 2),
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
    weekly_high: float | None = None
    weekly_low: float | None = None
    monthly_high: float | None = None
    monthly_low: float | None = None


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
    - Tokyo: 20:00 - 02:00 ET (prior evening into early morning)
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

        # Tokyo: 20:00 ET prior day to 02:00 ET current day
        if bar_date == yesterday and bar_time >= time(20, 0):
            levels.tokyo_high = max(levels.tokyo_high or h, h)
            levels.tokyo_low = min(levels.tokyo_low or l, l)
        elif bar_date == today_et and bar_time < time(2, 0):
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

        # Weekly H/L (current week, Mon-Fri RTH)
        week_start = today_et - timedelta(days=today_et.weekday())
        if week_start <= bar_date <= today_et and time(9, 30) <= bar_time < time(16, 0):
            levels.weekly_high = max(levels.weekly_high or h, h)
            levels.weekly_low = min(levels.weekly_low or l, l)

        # Monthly H/L (current month RTH)
        if bar_date.year == today_et.year and bar_date.month == today_et.month and time(9, 30) <= bar_time < time(16, 0):
            levels.monthly_high = max(levels.monthly_high or h, h)
            levels.monthly_low = min(levels.monthly_low or l, l)

    return levels
```

- [ ] **Step 2: Add order block and FVG detection**

Append to `levels.py`:

```python
@dataclass
class OrderBlock:
    price_low: float
    price_high: float
    direction: str  # "bullish" or "bearish"
    volume: int


@dataclass
class FairValueGap:
    price_low: float
    price_high: float
    direction: str  # "bullish" or "bearish"


def detect_order_blocks(bars: list[dict], min_move_pct: float = 0.003) -> list[OrderBlock]:
    """Detect order blocks: last candle before an impulsive move."""
    blocks = []
    if len(bars) < 3:
        return blocks

    for i in range(1, len(bars) - 1):
        move = bars[i + 1]["close"] - bars[i]["close"]
        move_pct = abs(move) / bars[i]["close"] if bars[i]["close"] > 0 else 0

        if move_pct >= min_move_pct:
            # Impulsive move detected — prior candle is the order block
            ob = bars[i]
            direction = "bullish" if move > 0 else "bearish"
            blocks.append(OrderBlock(
                price_low=ob["low"],
                price_high=ob["high"],
                direction=direction,
                volume=ob.get("volume", 0),
            ))

    return blocks


def detect_fvgs(bars: list[dict]) -> list[FairValueGap]:
    """Detect Fair Value Gaps: gap between candle N-1 and N+1 that candle N didn't fill."""
    gaps = []
    if len(bars) < 3:
        return gaps

    for i in range(1, len(bars) - 1):
        prev_bar = bars[i - 1]
        next_bar = bars[i + 1]

        # Bullish FVG: prev_bar high < next_bar low (gap up)
        if prev_bar["high"] < next_bar["low"]:
            gaps.append(FairValueGap(
                price_low=prev_bar["high"],
                price_high=next_bar["low"],
                direction="bullish",
            ))

        # Bearish FVG: prev_bar low > next_bar high (gap down)
        if prev_bar["low"] > next_bar["high"]:
            gaps.append(FairValueGap(
                price_low=next_bar["high"],
                price_high=prev_bar["low"],
                direction="bearish",
            ))

    return gaps
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/market_data/levels.py
git commit -m "feat: add level engine (VP, VWAP, session levels, IB, order blocks, FVGs)"
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

# Module-level singleton for live stream
_live_stream = None

def _get_live_stream():
    """Get or create the singleton DatabentoLiveStream."""
    global _live_stream
    if _live_stream is None:
        import os
        api_key = os.environ.get("DATABENTO_API_KEY")
        if not api_key:
            return None
        from src.market_data.stream import DatabentoLiveStream
        _live_stream = DatabentoLiveStream(api_key=api_key)
    return _live_stream

@router.get("/stream")
async def market_stream(symbol: str = "NQ"):
    """SSE stream of real-time tick data, candles, and level touches."""
    from src.market_data.stream import DatabentoLiveStream

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

### Task 13: IB Break Detector

**Files:** Create: `backend/src/market_data/setups/ib_break.py`

- [ ] **Step 1: Create ib_break.py**

```python
"""IB Break setup: price exits first 60-min range with conviction."""
from .detector import DetectorContext, SetupCandidate


def detect_ib_break(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Initial Balance breakout — price exits IB range with delta + tick vol."""
    candidates = []
    ib_h = ctx.session_levels.ib_high
    ib_l = ctx.session_levels.ib_low
    if not ib_h or not ib_l:
        return []

    ib_range = ib_h - ib_l
    if ib_range <= 0:
        return []

    # Break above IB → long
    if ctx.last_price > ib_h and ctx.macro_bias != "bear":
        if ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
            candidates.append(SetupCandidate(
                setup_type="ib_break",
                setup_name="IB Break Long",
                direction="long",
                level_touched="ib_high",
                entry_price=ctx.last_price,
                stop_price=ib_h - ib_range * 0.25,  # SL = 25% back inside IB
                target_1=ib_h + ib_range * 1.0,      # TP1 = 1x IB extension
                target_2=ib_h + ib_range * 1.5,      # TP2 = 1.5x
                target_3=ctx.session_levels.weekly_high,
                base_score=70.0,
            ))

    # Break below IB → short
    if ctx.last_price < ib_l and ctx.macro_bias != "bull":
        if ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
            candidates.append(SetupCandidate(
                setup_type="ib_break",
                setup_name="IB Break Short",
                direction="short",
                level_touched="ib_low",
                entry_price=ctx.last_price,
                stop_price=ib_l + ib_range * 0.25,
                target_1=ib_l - ib_range * 1.0,
                target_2=ib_l - ib_range * 1.5,
                target_3=ctx.session_levels.weekly_low,
                base_score=70.0,
            ))

    return candidates
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/setups/ib_break.py
git commit -m "feat: add IB Break setup detector"
```

---

### Task 14: Spring Detector

**Files:** Create: `backend/src/market_data/setups/spring.py`

- [ ] **Step 1: Create spring.py**

```python
"""Spring / Liquidity Trap: minor penetration below support, low volume, snap-back."""
from .detector import DetectorContext, SetupCandidate


def detect_spring(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Wyckoff spring — brief dip below support on low volume, then reversal."""
    candidates = []

    # Check support levels: VAL, PDL, session low
    support_levels = [
        ("val", ctx.vp.val),
        ("pdl", ctx.session_levels.pdl),
    ]

    for level_name, level_price in support_levels:
        if not level_price or level_price <= 0:
            continue

        # Spring = price dipped below level but currently above (snap-back)
        penetration = level_price - ctx.last_price
        if -level_price * 0.003 < penetration < level_price * 0.001:
            # Price is near/just above level after dipping below
            if ctx.last_price >= level_price * 0.998:
                # Confirm: delta unwind (sellers exhausted) or low volume
                if ctx.orderflow.delta_unwind or not ctx.orderflow.tick_vol_accelerating:
                    if ctx.macro_bias != "bear":
                        candidates.append(SetupCandidate(
                            setup_type="spring",
                            setup_name=f"Spring at {level_name.upper()}",
                            direction="long",
                            level_touched=level_name,
                            entry_price=ctx.last_price,
                            stop_price=level_price * 0.997,  # SL below spring low
                            target_1=ctx.vp.poc,
                            target_2=ctx.vp.vah,
                            target_3=ctx.session_levels.pdh,
                            base_score=72.0,
                        ))

    return candidates
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/setups/spring.py
git commit -m "feat: add Spring/Liquidity Trap setup detector"
```

---

### Task 15: SFP Detector

**Files:** Create: `backend/src/market_data/setups/sfp.py`

- [ ] **Step 1: Create sfp.py**

```python
"""Swing Failure Pattern: price breaks swing H/L then CLOSES back inside."""
from .detector import DetectorContext, SetupCandidate


def detect_sfp(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect SFP — price pierces a level but closes back inside (requires close confirmation)."""
    candidates = []

    # SFP at highs → short (price broke above resistance but closed back below)
    resistance_levels = [
        ("vah", ctx.vp.vah),
        ("pdh", ctx.session_levels.pdh),
        ("ib_high", ctx.session_levels.ib_high),
    ]

    for level_name, level_price in resistance_levels:
        if not level_price or level_price <= 0:
            continue
        # Price is now below level (closed back) and delta shows unwind
        if ctx.last_price < level_price and ctx.last_price > level_price * 0.997:
            if ctx.orderflow.delta_unwind and ctx.orderflow.trapped_traders:
                if ctx.macro_bias != "bull":
                    candidates.append(SetupCandidate(
                        setup_type="sfp",
                        setup_name=f"SFP Short at {level_name.upper()}",
                        direction="short",
                        level_touched=level_name,
                        entry_price=ctx.last_price,
                        stop_price=level_price * 1.002,  # SL above the swing high
                        target_1=ctx.vp.poc,
                        target_2=ctx.vp.val,
                        target_3=ctx.session_levels.pdl,
                        base_score=75.0,
                    ))

    # SFP at lows → long
    support_levels = [
        ("val", ctx.vp.val),
        ("pdl", ctx.session_levels.pdl),
        ("ib_low", ctx.session_levels.ib_low),
    ]

    for level_name, level_price in support_levels:
        if not level_price or level_price <= 0:
            continue
        if ctx.last_price > level_price and ctx.last_price < level_price * 1.003:
            if ctx.orderflow.delta_unwind and ctx.orderflow.trapped_traders:
                if ctx.macro_bias != "bear":
                    candidates.append(SetupCandidate(
                        setup_type="sfp",
                        setup_name=f"SFP Long at {level_name.upper()}",
                        direction="long",
                        level_touched=level_name,
                        entry_price=ctx.last_price,
                        stop_price=level_price * 0.998,
                        target_1=ctx.vp.poc,
                        target_2=ctx.vp.vah,
                        target_3=ctx.session_levels.pdh,
                        base_score=75.0,
                    ))

    return candidates
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/setups/sfp.py
git commit -m "feat: add SFP (Swing Failure Pattern) setup detector"
```

---

### Task 16: 80% Rule Detector

**Files:** Create: `backend/src/market_data/setups/rule_80.py`

- [ ] **Step 1: Create rule_80.py**

```python
"""80% Rule: opens outside prior VA, trades back inside for 2+ TPO periods."""
from .detector import DetectorContext, SetupCandidate


def detect_rule_80(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect 80% Rule — price opens outside VA, re-enters, targets opposite VA extreme."""
    candidates = []
    vah = ctx.vp.vah
    val = ctx.vp.val
    poc = ctx.vp.poc

    if not vah or not val or vah <= val:
        return []

    # Check TPO profile: need at least 2 letters inside VA after opening outside
    # Use ib_tpo_count as proxy for time spent inside VA
    if ctx.tpo.ib_tpo_count < 4:  # 2 TPO periods × ~2 price levels each
        return []

    # Opened above VA, now trading inside → target VAL (80% chance)
    if ctx.session_levels.ib_high and ctx.session_levels.ib_high > vah:
        if val < ctx.last_price < vah:
            candidates.append(SetupCandidate(
                setup_type="rule_80",
                setup_name="80% Rule Short (opened above VA)",
                direction="short",
                level_touched="vah",
                entry_price=ctx.last_price,
                stop_price=vah * 1.002,
                target_1=poc,
                target_2=val,
                target_3=ctx.session_levels.pdl,
                base_score=78.0,  # High base: 80% historical probability
            ))

    # Opened below VA, now trading inside → target VAH
    if ctx.session_levels.ib_low and ctx.session_levels.ib_low < val:
        if val < ctx.last_price < vah:
            candidates.append(SetupCandidate(
                setup_type="rule_80",
                setup_name="80% Rule Long (opened below VA)",
                direction="long",
                level_touched="val",
                entry_price=ctx.last_price,
                stop_price=val * 0.998,
                target_1=poc,
                target_2=vah,
                target_3=ctx.session_levels.pdh,
                base_score=78.0,
            ))

    return candidates
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/setups/rule_80.py
git commit -m "feat: add 80% Rule setup detector"
```

---

### Task 17: Fakeout Detector

**Files:** Create: `backend/src/market_data/setups/fakeout.py`

- [ ] **Step 1: Create fakeout.py**

```python
"""Fakeout / Head Fake: convincing break that reverses, POC/VWAP holds."""
from .detector import DetectorContext, SetupCandidate


def detect_fakeout(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect fakeout — apparent breakout reverses with delta divergence."""
    candidates = []
    poc = ctx.vp.poc
    vwap = ctx.vwap.vwap if ctx.vwap else None

    if not poc:
        return []

    # Key confirmation: delta divergence (price says breakout, delta says no)
    if not ctx.orderflow.delta_divergence:
        return []

    # Fakeout above resistance → short
    resistance_levels = [
        ("vah", ctx.vp.vah),
        ("pdh", ctx.session_levels.pdh),
    ]

    for level_name, level_price in resistance_levels:
        if not level_price:
            continue
        # Price broke above then returned near level
        if ctx.last_price <= level_price * 1.001 and ctx.last_price >= level_price * 0.997:
            if ctx.macro_bias != "bull":
                # Confirm POC/VWAP is holding (price above both)
                anchor_holds = (vwap and ctx.last_price > vwap * 0.998) or ctx.last_price > poc * 0.998
                if anchor_holds or ctx.orderflow.vsa_absorption:
                    candidates.append(SetupCandidate(
                        setup_type="fakeout",
                        setup_name=f"Fakeout Short at {level_name.upper()}",
                        direction="short",
                        level_touched=level_name,
                        entry_price=ctx.last_price,
                        stop_price=level_price * 1.003,
                        target_1=poc,
                        target_2=ctx.vp.val,
                        target_3=ctx.session_levels.pdl,
                        base_score=68.0,
                    ))

    # Fakeout below support → long
    support_levels = [
        ("val", ctx.vp.val),
        ("pdl", ctx.session_levels.pdl),
    ]

    for level_name, level_price in support_levels:
        if not level_price:
            continue
        if ctx.last_price >= level_price * 0.999 and ctx.last_price <= level_price * 1.003:
            if ctx.macro_bias != "bear":
                anchor_holds = (vwap and ctx.last_price < vwap * 1.002) or ctx.last_price < poc * 1.002
                if anchor_holds or ctx.orderflow.vsa_absorption:
                    candidates.append(SetupCandidate(
                        setup_type="fakeout",
                        setup_name=f"Fakeout Long at {level_name.upper()}",
                        direction="long",
                        level_touched=level_name,
                        entry_price=ctx.last_price,
                        stop_price=level_price * 0.997,
                        target_1=poc,
                        target_2=ctx.vp.vah,
                        target_3=ctx.session_levels.pdh,
                        base_score=68.0,
                    ))

    return candidates
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/setups/fakeout.py
git commit -m "feat: add Fakeout/Head Fake setup detector"
```

---

### Task 18: Break from Balance + Double Distribution Detectors

**Files:**
- Create: `backend/src/market_data/setups/break_from_balance.py`
- Create: `backend/src/market_data/setups/double_distribution.py`

- [ ] **Step 1: Create break_from_balance.py**

```python
"""Break from Balance: 3+ days of overlapping VAs, ASPR compressed, then breakout."""
from .detector import DetectorContext, SetupCandidate


def detect_break_from_balance(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Break from Balance — range compression then directional break."""
    candidates = []
    vah = ctx.vp.vah
    val = ctx.vp.val
    poc = ctx.vp.poc

    if not vah or not val or vah <= val:
        return []

    va_range = vah - val

    # Break above balance → long
    if ctx.last_price > vah:
        if ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
            if ctx.day_type != "neutral":  # Neutral = still balanced
                candidates.append(SetupCandidate(
                    setup_type="break_from_balance",
                    setup_name="Break from Balance Long",
                    direction="long",
                    level_touched="vah",
                    entry_price=ctx.last_price,
                    stop_price=vah - va_range * 0.15,  # Tight SL just inside VA
                    target_1=vah + va_range * 0.5,
                    target_2=vah + va_range * 1.0,
                    target_3=ctx.session_levels.weekly_high,
                    base_score=72.0,
                ))

    # Break below balance → short
    if ctx.last_price < val:
        if ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
            if ctx.day_type != "neutral":
                candidates.append(SetupCandidate(
                    setup_type="break_from_balance",
                    setup_name="Break from Balance Short",
                    direction="short",
                    level_touched="val",
                    entry_price=ctx.last_price,
                    stop_price=val + va_range * 0.15,
                    target_1=val - va_range * 0.5,
                    target_2=val - va_range * 1.0,
                    target_3=ctx.session_levels.weekly_low,
                    base_score=72.0,
                ))

    return candidates
```

- [ ] **Step 2: Create double_distribution.py**

```python
"""Double Distribution Reversal: 2 VP peaks, secondary weaker, rotation back to primary."""
from .detector import DetectorContext, SetupCandidate


def detect_double_distribution(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Double Distribution — bimodal VP with weaker secondary peak."""
    candidates = []
    if not ctx.vp.levels or len(ctx.vp.levels) < 10:
        return []

    # Find two peaks in VP distribution
    poc = ctx.vp.poc
    poc_vol = max(l.volume for l in ctx.vp.levels)

    # Look for secondary peak: > 50% of POC volume, at least 5 ticks away
    secondary_peaks = []
    for level in ctx.vp.levels:
        if level.volume > poc_vol * 0.5 and abs(level.price - poc) > 5 * 0.25:
            secondary_peaks.append(level)

    if not secondary_peaks:
        return []

    # Secondary peak above POC → price should rotate down to primary
    for sec in secondary_peaks:
        if sec.price > poc and sec.volume < poc_vol:
            # Price near secondary peak → expect rotation down to POC
            if abs(ctx.last_price - sec.price) < (sec.price - poc) * 0.2:
                if ctx.macro_bias != "bull":
                    candidates.append(SetupCandidate(
                        setup_type="double_distribution",
                        setup_name="Double Distribution Short (rotate to POC)",
                        direction="short",
                        level_touched="secondary_vp_peak",
                        entry_price=ctx.last_price,
                        stop_price=sec.price * 1.002,
                        target_1=poc,
                        target_2=ctx.vp.val,
                        base_score=68.0,
                    ))

        elif sec.price < poc and sec.volume < poc_vol:
            if abs(ctx.last_price - sec.price) < (poc - sec.price) * 0.2:
                if ctx.macro_bias != "bear":
                    candidates.append(SetupCandidate(
                        setup_type="double_distribution",
                        setup_name="Double Distribution Long (rotate to POC)",
                        direction="long",
                        level_touched="secondary_vp_peak",
                        entry_price=ctx.last_price,
                        stop_price=sec.price * 0.998,
                        target_1=poc,
                        target_2=ctx.vp.vah,
                        base_score=68.0,
                    ))

    return candidates
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/market_data/setups/break_from_balance.py backend/src/market_data/setups/double_distribution.py
git commit -m "feat: add Break from Balance + Double Distribution detectors"
```

---

### Task 19: News Directional Detector

**Files:** Create: `backend/src/market_data/setups/news_directional.py`

- [ ] **Step 1: Create news_directional.py**

```python
"""News Directional: post-release directional candle with VSA confirmation."""
from .detector import DetectorContext, SetupCandidate


def detect_news_directional(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect News Directional — directional M1 candle after scheduled release.

    Note: This detector relies on tick_vol_accelerating as proxy for news spike.
    Real news calendar integration is deferred to v2.
    """
    candidates = []

    # News spike proxy: very high tick volume + strong delta alignment
    if not ctx.orderflow.tick_vol_accelerating:
        return []
    if not ctx.orderflow.delta_aligned:
        return []

    # Must also have VSA confirmation (big candle, not absorption)
    if ctx.orderflow.vsa_absorption:
        return []  # Absorption = rejection, not directional

    # Bullish news candle → long
    if ctx.orderflow.delta > 0 and ctx.orderflow.cvd_trend == "rising":
        if ctx.macro_bias != "bear":
            candidates.append(SetupCandidate(
                setup_type="news_directional",
                setup_name="News Directional Long",
                direction="long",
                level_touched="news_spike",
                entry_price=ctx.last_price,
                stop_price=ctx.last_price * 0.997,  # Tight stop: 0.3%
                target_1=ctx.vp.vah if ctx.vp.vah and ctx.vp.vah > ctx.last_price else ctx.last_price * 1.005,
                target_2=ctx.session_levels.pdh,
                target_3=ctx.session_levels.weekly_high,
                base_score=65.0,  # Lower base: news is noisy
            ))

    # Bearish news candle → short
    if ctx.orderflow.delta < 0 and ctx.orderflow.cvd_trend == "falling":
        if ctx.macro_bias != "bull":
            candidates.append(SetupCandidate(
                setup_type="news_directional",
                setup_name="News Directional Short",
                direction="short",
                level_touched="news_spike",
                entry_price=ctx.last_price,
                stop_price=ctx.last_price * 1.003,
                target_1=ctx.vp.val if ctx.vp.val and ctx.vp.val < ctx.last_price else ctx.last_price * 0.995,
                target_2=ctx.session_levels.pdl,
                target_3=ctx.session_levels.weekly_low,
                base_score=65.0,
            ))

    return candidates
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/setups/news_directional.py
git commit -m "feat: add News Directional setup detector"
```

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
    timeframe_confluence: bool = False,
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
    # Timeframe confluence (same setup visible on HTF)
    if timeframe_confluence:
        score += 10
    # RF/ASPR session context
    if rf is not None and aspr_percentile is not None:
        if aspr_percentile < 0.2:  # Compressed = breakout setups stronger
            if candidate.setup_type in ("ib_break", "break_from_balance"):
                score += 5
    # Passive/active ratio: high ratio at key level = absorption
    if orderflow.passive_active_ratio > 2.0:
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

- [ ] **Step 2: Add R:R filter and Kelly position sizing to scoring.py**

Add to `backend/src/market_data/scoring.py`:

```python
def filter_by_rr(candidates: list, min_rr: float = 1.5) -> list:
    """Filter candidates: only surface if TP1 R:R >= min_rr."""
    return [c for c in candidates if c.rr_tp1 and c.rr_tp1 >= min_rr]


def kelly_position_size(
    win_rate: float,
    avg_rr: float,
    account_balance: float,
    max_risk_pct: float = 0.02,
) -> float:
    """Kelly criterion position sizing, capped at max_risk_pct of account.

    Returns dollar risk amount (not contracts).
    """
    if win_rate <= 0 or avg_rr <= 0:
        return 0.0
    # Kelly fraction: f* = (bp - q) / b where b = avg_rr, p = win_rate, q = 1 - p
    b = avg_rr
    p = win_rate
    q = 1 - p
    kelly_f = (b * p - q) / b
    # Half-Kelly for safety
    half_kelly = kelly_f / 2
    # Cap at max_risk_pct
    risk_fraction = max(0, min(half_kelly, max_risk_pct))
    return round(account_balance * risk_fraction, 2)
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/market_data/setups/detector.py backend/src/market_data/scoring.py
git commit -m "feat: add R:R computation, R:R filter, and Kelly position sizing"
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

### Task 23a: Layer A Gate Cards Component

**Files:** Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Add Layer A gate cards above existing confirmation strip**

Add before the existing confirmation cards in the page's JSX:

```tsx
// State for context gates
const [context, setContext] = useState<MarketContext | null>(null);

useEffect(() => {
  api.getMarketContext().then(setContext);
}, []);

const updateGate = async (field: string, value: string) => {
  await api.updateMarketContext({ [field]: value });
  const updated = await api.getMarketContext();
  setContext(updated);
};

// In the render, add above existing confirmation cards:
<div className="flex gap-2 mb-3">
  {/* Gate 1: Macro */}
  <div className="bg-zinc-800 p-2 rounded flex-1 border border-zinc-700">
    <div className="text-xs text-zinc-400 mb-1">Gate 1: Macro</div>
    <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white"
      value={context?.macro_bias || ''}
      onChange={e => updateGate('macro_bias', e.target.value)}>
      <option value="">—</option>
      <option value="bull">Bull</option>
      <option value="bear">Bear</option>
      <option value="neutral">Neutral</option>
    </select>
    <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white mt-1"
      value={context?.risk_mode || ''}
      onChange={e => updateGate('risk_mode', e.target.value)}>
      <option value="">Risk Mode —</option>
      <option value="risk_on">Risk On</option>
      <option value="risk_off">Risk Off</option>
      <option value="mixed">Mixed</option>
    </select>
  </div>
  {/* Gate 2: Structure */}
  <div className="bg-zinc-800 p-2 rounded flex-1 border border-zinc-700">
    <div className="text-xs text-zinc-400 mb-1">Gate 2: Structure</div>
    <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white"
      value={context?.structure || ''}
      onChange={e => updateGate('structure', e.target.value)}>
      <option value="">—</option>
      <option value="uptrend">Uptrend (HH/HL)</option>
      <option value="downtrend">Downtrend (LH/LL)</option>
      <option value="ranging">Ranging</option>
    </select>
  </div>
  {/* Gate 3: Day Type */}
  <div className="bg-zinc-800 p-2 rounded flex-1 border border-zinc-700">
    <div className="text-xs text-zinc-400 mb-1">Gate 3: Day Type</div>
    <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white"
      value={context?.day_type || ''}
      onChange={e => updateGate('day_type', e.target.value)}>
      <option value="">—</option>
      <option value="trend">Trend</option>
      <option value="normal">Normal</option>
      <option value="normal_variation">Normal Variation</option>
      <option value="neutral">Neutral</option>
      <option value="composite">Composite</option>
    </select>
  </div>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat: add Layer A context gate cards to Intraday page"
```

---

### Task 23b: Session Metrics Row

**Files:** Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Add session metrics row below gate cards**

```tsx
{/* Session Metrics Row — below gates, above table */}
{session && (
  <div className="flex gap-3 mb-3 text-xs">
    <div className="bg-zinc-800 px-3 py-1.5 rounded">
      <span className="text-zinc-400">RF:</span>{' '}
      <span className={session.rotation_factor > 0 ? 'text-green-400' : session.rotation_factor < 0 ? 'text-red-400' : 'text-zinc-300'}>
        {session.rotation_factor ?? '—'}
      </span>
    </div>
    <div className="bg-zinc-800 px-3 py-1.5 rounded">
      <span className="text-zinc-400">ASPR:</span>{' '}
      <span className="text-zinc-200">{session.aspr?.toFixed(2) ?? '—'}</span>
      {session.aspr_percentile != null && (
        <span className="text-zinc-500 ml-1">({(session.aspr_percentile * 100).toFixed(0)}%ile)</span>
      )}
    </div>
    <div className="bg-zinc-800 px-3 py-1.5 rounded">
      <span className="text-zinc-400">IB:</span>{' '}
      <span className="text-zinc-200">{session.ib_high?.toFixed(2) ?? '—'} / {session.ib_low?.toFixed(2) ?? '—'}</span>
    </div>
    <div className="bg-zinc-800 px-3 py-1.5 rounded">
      <span className="text-zinc-400">VWAP:</span>{' '}
      <span className="text-blue-400">{session.vwap?.toFixed(2) ?? '—'}</span>
    </div>
    <div className="bg-zinc-800 px-3 py-1.5 rounded">
      <span className="text-zinc-400">POC:</span>{' '}
      <span className="text-yellow-400">{session.poc?.toFixed(2) ?? '—'}</span>
    </div>
    <div className="bg-zinc-800 px-3 py-1.5 rounded">
      <span className="text-zinc-400">Migration:</span>{' '}
      <span className={session.value_migration === 'up' ? 'text-green-400' : session.value_migration === 'down' ? 'text-red-400' : 'text-zinc-300'}>
        {session.value_migration ?? '—'}
      </span>
    </div>
  </div>
)}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat: add session metrics row (RF, ASPR, IB, VWAP, POC) to Intraday"
```

---

### Task 23c: Updated Table Columns + L2 Panel

**Files:** Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Update opportunity table columns**

In the table header, add columns after existing ones:

```tsx
<th className="px-2 py-1.5 text-left">Level</th>
<th className="px-2 py-1.5 text-right">TP2</th>
<th className="px-2 py-1.5 text-right">TP3</th>
<th className="px-2 py-1.5 text-right">R:R</th>
```

In each table row:

```tsx
<td className="px-2 py-1.5 text-left">
  <span className="bg-zinc-700 px-1.5 py-0.5 rounded text-xs">{signal.level_touched}</span>
</td>
<td className="px-2 py-1.5 text-right text-zinc-300">{signal.suggested_target_2?.toFixed(2) ?? '—'}</td>
<td className="px-2 py-1.5 text-right text-zinc-300">{signal.suggested_target_3?.toFixed(2) ?? '—'}</td>
<td className="px-2 py-1.5 text-right">
  <span className={signal.rr_tp1 >= 2 ? 'text-green-400' : signal.rr_tp1 >= 1.5 ? 'text-yellow-400' : 'text-zinc-400'}>
    {signal.rr_tp1?.toFixed(1) ?? '—'}
  </span>
</td>
```

Show `setup_category` as colored badge in the Setup column:

```tsx
const SETUP_COLORS: Record<string, string> = {
  spring: 'bg-emerald-600', sfp: 'bg-blue-600', poor_extreme: 'bg-purple-600',
  ib_break: 'bg-orange-600', rule_80: 'bg-cyan-600', fakeout: 'bg-red-600',
  break_from_balance: 'bg-amber-600', double_distribution: 'bg-pink-600',
  news_directional: 'bg-indigo-600',
};
// In column:
<span className={`px-1.5 py-0.5 rounded text-xs ${SETUP_COLORS[signal.setup_category] || 'bg-zinc-600'}`}>
  {signal.setup_category}
</span>
```

- [ ] **Step 2: Add expanded row L2 panel**

In the expanded row section (when a signal row is clicked), add:

```tsx
const { lastTick, connected } = useMarketStream();

// In expanded content:
<div className="bg-zinc-900 p-3 rounded mt-2">
  <div className="text-xs text-zinc-400 mb-2">L2 Orderflow {connected ? '🟢' : '🔴'}</div>
  <div className="flex gap-4 text-xs">
    <div>Delta: <span className={lastTick?.delta_1m > 0 ? 'text-green-400' : 'text-red-400'}>
      {lastTick?.delta_1m ?? 0}</span></div>
    <div>CVD: <span className="text-zinc-200">{lastTick?.cvd ?? 0}</span></div>
    <div>Last: <span className="text-zinc-200">{lastTick?.price?.toFixed(2)}</span></div>
  </div>
  {/* Confirmation checklist from signal conditions */}
  {signal.conditions && (() => {
    const of = JSON.parse(signal.conditions)?.orderflow;
    if (!of) return null;
    return (
      <div className="flex gap-3 mt-2 text-xs">
        <span>{of.delta_aligned ? '✓' : '✗'} Delta</span>
        <span>{of.vsa_absorption ? '✓' : '✗'} VSA</span>
        <span>{of.delta_divergence ? '✓' : '✗'} Divergence</span>
        <span>{of.tick_vol_accelerating ? '✓' : '✗'} Tick Vol</span>
        <span>{of.trapped_traders ? '✓' : '✗'} Trapped</span>
      </div>
    );
  })()}
</div>
```

- [ ] **Step 3: Verify frontend compiles**

Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat: add updated table columns, setup badges, and L2 expanded panel"
```

---

### Task 23d: Level Map Section

**Files:** Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Add level map collapsible section below table**

```tsx
const [levels, setLevels] = useState<any[]>([]);
const [showLevels, setShowLevels] = useState(false);

const loadLevels = async () => {
  const res = await fetch(`/api/trading/market/levels?symbol=NQ`);
  const data = await res.json();
  setLevels(data);
  setShowLevels(true);
};

// After the opportunity table:
<div className="mt-3">
  <button onClick={loadLevels} className="text-xs text-zinc-400 hover:text-zinc-200">
    {showLevels ? '▼' : '▶'} Level Map ({levels.length} levels)
  </button>
  {showLevels && levels.length > 0 && (
    <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
      {['vp', 'vwap', 'session', 'order_block', 'fvg', 'single_print'].map(type => {
        const filtered = levels.filter(l => l.level_type.includes(type));
        if (!filtered.length) return null;
        return (
          <div key={type} className="bg-zinc-800 p-2 rounded">
            <div className="text-zinc-400 mb-1 uppercase">{type.replace('_', ' ')}</div>
            {filtered.map((l, i) => (
              <div key={i} className="flex justify-between">
                <span className="text-zinc-300">{l.level_type}</span>
                <span className="text-zinc-200">{l.price_low.toFixed(2)}{l.price_high !== l.price_low ? `-${l.price_high.toFixed(2)}` : ''}</span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  )}
</div>
```

- [ ] **Step 2: Verify frontend compiles**

Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat: add Level Map section to Intraday page"
```

---

### Task 24: Integration — Wire Everything Together

**Files:**
- Modify: `backend/src/services/market_service.py`

- [ ] **Step 1: Extend compute_session to use new level engine**

Add these imports and calls at the end of `MarketService.compute_session()`:

```python
from src.market_data.levels import compute_session_levels, compute_volume_profile, compute_vwap_bands
from src.market_data.tpo import compute_tpo_profile
from src.market_data.metrics import compute_rotation_factor, compute_aspr, compute_aspr_percentile, detect_value_migration

# Inside compute_session(), after existing AMT logic:

# Compute session levels from 1-min bars
bars_1m = [{"ts": t.ts, "high": t.price + 0.25, "low": t.price - 0.25} for t in trades]  # Approximate from ticks
session_levels = compute_session_levels(bars_1m, session_date)
session.pdh = session_levels.pdh
session.pdl = session_levels.pdl
session.tokyo_high = session_levels.tokyo_high
session.tokyo_low = session_levels.tokyo_low
session.london_high = session_levels.london_high
session.london_low = session_levels.london_low

# TPO from 30-min bars
tpo = compute_tpo_profile(bars_30m)

# Session metrics: RF from 30-min highs/lows
highs_30m = [b["high"] for b in bars_30m]
lows_30m = [b["low"] for b in bars_30m]
rf = compute_rotation_factor(highs_30m, lows_30m)
ranges_30m = [b["high"] - b["low"] for b in bars_30m]
aspr = compute_aspr(ranges_30m)
historical = self.repo.get_historical_asprs(symbol)
aspr_pct = compute_aspr_percentile(aspr, historical)

session.rotation_factor = rf
session.aspr = aspr
session.aspr_percentile = aspr_pct
session.ib_tpo_count = tpo.ib_tpo_count

# Value migration vs prior session
prev_session = self.repo.get_previous_session(symbol)
if prev_session and prev_session.vah and prev_session.val:
    session.value_migration = detect_value_migration(
        session.vah, session.val, prev_session.vah, prev_session.val
    )

# Persist session metric for baseline history
self.repo.upsert_session_metric(symbol, session.date, rf, aspr)
```

- [ ] **Step 2: Extend run_scan to use new setup detectors**

Add to `MarketService.run_scan()`:

```python
from src.market_data.setups.detector import DetectorContext, run_all_detectors
from src.market_data.orderflow import build_candle_flow, compute_signals
from src.market_data.scoring import score_candidate, day_type_fits_setup, filter_by_rr

# Inside run_scan(), after computing session:

# Build orderflow signals from recent ticks
recent_ticks = self.repo.get_trades(symbol, start=session_start, end=now)
tick_dicts = [{"ts": t.ts, "price": t.price, "size": t.size, "side": t.side} for t in recent_ticks]
candles = build_candle_flow(tick_dicts, period_seconds=60)

# Get context gates
ctx_model = self.repo.get_context(symbol)
direction = "long" if (ctx_model and ctx_model.macro_bias == "bull") else "short" if (ctx_model and ctx_model.macro_bias == "bear") else "long"
orderflow = compute_signals(candles, direction)

# Build detector context
detector_ctx = DetectorContext(
    vp=VolumeProfile(poc=session.poc, vah=session.vah, val=session.val, levels=[], single_prints=[]),
    vwap=compute_vwap_bands(tick_dicts) if tick_dicts else None,
    session_levels=session_levels,
    tpo=tpo,
    orderflow=orderflow,
    last_price=tick_dicts[-1]["price"] if tick_dicts else 0,
    macro_bias=ctx_model.macro_bias if ctx_model else None,
    structure=ctx_model.structure if ctx_model else None,
    day_type=ctx_model.day_type if ctx_model else None,
)

# Run all detectors
raw_candidates = run_all_detectors(detector_ctx)

# Score and filter
scored = []
for c in raw_candidates:
    fits = day_type_fits_setup(detector_ctx.day_type, c.setup_type)
    macro_ok = (c.direction == "long" and detector_ctx.macro_bias != "bear") or \
               (c.direction == "short" and detector_ctx.macro_bias != "bull")
    final_score = score_candidate(c, orderflow, fits, macro_ok, session.rotation_factor, session.aspr_percentile)
    if final_score >= 70:
        c.base_score = final_score
        scored.append(c)

# R:R filter: only TP1 >= 1.5
scored = filter_by_rr(scored, min_rr=1.5)

# Store as TradingSignal rows
for c in scored:
    signal = TradingSignal(
        session_id=session.id,
        symbol=symbol,
        setup_type=c.setup_type,
        setup_category=c.setup_type,
        score=c.base_score,
        direction=c.direction,
        entry_price=c.entry_price,
        suggested_stop=c.stop_price,
        suggested_target=c.target_1,
        suggested_target_2=c.target_2,
        suggested_target_3=c.target_3,
        level_touched=c.level_touched,
        rr_tp1=c.rr_tp1,
        rr_tp2=c.rr_tp2,
        conditions=json.dumps({"orderflow": orderflow.__dict__}),
    )
    self.repo.db.add(signal)
self.repo.db.commit()
```

- [ ] **Step 3: Add levels endpoint**

```python
@router.get("/levels")
async def get_levels(symbol: str = "NQ", date: str = None, svc: MarketService = Depends(_svc)):
    levels = svc.repo.get_levels(symbol, date or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
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
