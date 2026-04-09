# Rithmic Integration — Replace Databento (live) + Tradovate (broker)

**Date:** 2026-04-09
**Status:** Approved

## Purpose

Replace both Databento (live tick data, $100+/mo) and Tradovate (order execution) with a single `async-rithmic` connection. Rithmic provides ticks, time bars, L2 order book, and order execution through one library, free with any prop firm account. Databento stays for historical RL training replay only.

## Architecture

```
async-rithmic (one connection, 4 WebSocket plants)
├── Ticker Plant  → live ticks  → LevelMonitor.on_tick()
├── Ticker Plant  → 1m/5m bars  → market_candles DB (no manual aggregation)
├── Ticker Plant  → L2 book     → future orderflow features
├── History Plant → backfill    → gap fills on reconnect
└── Order Plant   → orders      → BrokerAdapter
                  → fills       → PositionTracker

Databento (historical only)
└── rl replay command → parquet files → episode generation
```

## Components

### RithmicStream (`rithmic/stream.py`)

Replaces `DatabentoLiveStream`. Single class managing all Rithmic data connections.

**Responsibilities:**
- Connect to Rithmic with prop firm credentials
- Stream live ticks → call `LevelMonitor.on_tick(price, size, ts)`
- Subscribe to 1m and 5m time bars → persist to `market_candles` table
- Stream L2 order book updates (store in memory for orderflow features)
- Auto-reconnect on network drops (built into async-rithmic)
- Gap backfill via History Plant on reconnect

**Interface:**
```python
class RithmicStream:
    async def start(self) -> None
    async def stop(self) -> None
    def set_level_monitor(self, monitor: LevelMonitor) -> None
    @property
    def is_connected(self) -> bool
```

**Tick handling:**
```python
async for tick in client.stream_market_data("NQM5", "CME"):
    self._level_monitor.on_tick(tick.price, tick.size, tick.timestamp)
    self._tick_buffer.add(tick)  # for orderflow computation
```

**Bar handling (replaces CandleFlow + manual aggregation):**
```python
async for bar in client.subscribe_to_time_bar_data("NQM5", "CME", bar_type="MINUTE_BAR"):
    repo.upsert_candle("NQ", "1m", bar.timestamp, 
                       float(bar.open), float(bar.high), float(bar.low), 
                       float(bar.close), int(bar.volume))
```

### RithmicBrokerClient (`rithmic/broker_client.py`)

Replaces `TradovateClient`. Same interface so `BrokerAdapter` doesn't change.

**Interface (matches TradovateClient):**
```python
class RithmicBrokerClient:
    async def connect(self) -> bool
    async def place_market_order(self, action: str, quantity: int) -> dict
    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict
    async def modify_order(self, order_id: int, new_stop_price: float) -> dict
    async def cancel_order(self, order_id: int) -> dict
    async def liquidate_position(self) -> dict
    async def get_positions(self) -> list[dict]
    async def get_orders(self) -> list[dict]
    async def close(self) -> None
```

**Implementation uses async-rithmic directly:**
```python
await self._client.submit_order(
    "NQM5", "CME", TransactionType.BUY, quantity,
    order_type=OrderType.MARKET
)
```

### RithmicConfig (`rithmic/config.py`)

```python
@dataclass
class RithmicConfig:
    user: str
    password: str
    system_name: str      # prop firm system name (e.g. "Topstep-Trader")
    gateway: str          # "PAPER", "TEST", or "LIVE"
    app_name: str         # "firev"
    symbol: str           # "NQM5"
    exchange: str         # "CME"
```

## What Stays The Same

| Component | Changes? | Why |
|-----------|----------|-----|
| `BrokerAdapter` | No | Calls client interface (swap Tradovate → Rithmic) |
| `PositionTracker` | No | Tracks fills from any source |
| `FlattenScheduler` | No | Calls adapter.flatten() |
| `LevelMonitor` | No | Receives on_tick() from any source |
| `SessionManager` | No | Processes specialist signals |
| `SpecialistEnsemble` | No | Pure inference, no data dependency |
| `LiveEpisodeCollector` | No | Gets state dict from LevelMonitor |
| `SignalLog` | No | Logs specialist decisions |
| Databento historical | Keep | Used by `rl replay` for training |

## What Gets Removed

| Component | Why |
|-----------|-----|
| `DatabentoLiveStream` | Replaced by RithmicStream for live data |
| `CandleFlow` class | Rithmic sends bars directly — no manual aggregation |
| `TradovateClient` | Replaced by RithmicBrokerClient |
| `_periodic_gap_backfill_loop` | Rithmic History Plant handles backfill |
| Databento live subscription ($100/mo) | No longer needed |
| np.float64 candle bug | Rithmic returns native Python types |

## What's New (Bonus)

| Feature | Impact |
|---------|--------|
| L2 order book | Future: depth-of-market features for RL model |
| ~5ms latency (vs 100-200ms) | Faster signal-to-fill, less slippage |
| Built-in auto-reconnect | No more manual watchdog/reconnect logic |
| Native time bars | Eliminates candle aggregation bugs |

## File Structure

```
backend/src/rithmic/
├── __init__.py
├── config.py              # RithmicConfig from env vars
├── stream.py              # RithmicStream (data: ticks, bars, L2)
└── broker_client.py       # RithmicBrokerClient (orders, fills)
```

## Modified Files

| File | Change |
|------|--------|
| `api/__init__.py` | Start RithmicStream instead of DatabentoLiveStream. Start RithmicBrokerClient instead of TradovateClient. |
| `broker/adapter.py` | Type hint accepts either client (same interface). No logic change. |
| `broker/config.py` | Remove Tradovate-specific fields. Add rithmic fields. |

## Configuration

```env
# Rithmic (data + execution)
RITHMIC_USER=
RITHMIC_PASSWORD=
RITHMIC_SYSTEM_NAME=Topstep-Trader
RITHMIC_GATEWAY=PAPER
RITHMIC_APP_NAME=firev
RITHMIC_SYMBOL=NQM5
RITHMIC_EXCHANGE=CME

# Broker risk rules (unchanged)
BROKER_ENABLED=false
BROKER_MAX_DAILY_LOSS=1000
BROKER_MAX_POSITION=2
BROKER_FLATTEN_ET=15:55
BROKER_MAX_TRAILING_DD=2000

# Databento (historical replay only)
DATABENTO_API_KEY=...
```

## Error Handling

- Connection loss → async-rithmic auto-reconnects with backoff
- Order rejection → log reason, don't retry
- If all Rithmic plants disconnect → flatten positions (safety)
- History Plant unavailable → use existing DB candles (graceful degradation)

## Dependencies

```
pip install async-rithmic>=1.5
```

Python 3.11+ required (async-rithmic requirement). Our server runs 3.10 — may need Docker base image update.
