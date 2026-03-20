# TBBO Fetch + Book Features for RL Observation Vector

**Date:** 2026-03-20
**Status:** Approved
**Depends on:** [RL Trading Agent Design](2026-03-19-rl-trading-agent-design.md), [RL Level Precomputation](2026-03-20-rl-level-precomputation-design.md)

## Problem

The RL observation vector (107 dims) has no book/spread information. The agent can see orderflow (delta, CVD, volume) and structure (VWAP, VP, levels) but cannot see the bid-ask spread or book imbalance at the moment of a level touch. Spread behavior is a key confirmation signal — spread widening at a level indicates uncertainty, while compression indicates conviction.

Currently the fetcher only downloads `trades` schema from Databento. The `mbp-1` (top-of-book) data is available on the Standard plan for 12 months of history and live streaming, but isn't fetched for RL training.

## Solution: TBBO Schema + Book Feature Segment

### Why TBBO, not MBP-1

| Schema | What it contains | Records per session | Storage |
|---|---|---|---|
| `trades` | Every trade: price, size, side | ~800K | ~45 MB/month |
| `tbbo` | Trade + BBO **before** the trade | Same as trades | ~60 MB/month (wider rows) |
| `mbp-1` | Every BBO change (even without trades) | ~5-10M | ~400 MB/month |

TBBO is a superset of trades — every TBBO record has the trade fields (price, size, side) PLUS the bid/ask/bid_size/ask_size at the moment the trade executed. Same record count as trades, ~33% more storage. MBP-1 would be 10x the data for information we don't need at tick resolution during training.

### Data Pipeline

#### Fetch

Replace the current `trades` fetch with `tbbo`:

```
Databento Historical API
  schema="tbbo"  (was "trades")
  → NQ_YYYY-MM.parquet  (same filename, wider columns)
```

**TBBO record fields (from Databento SDK):**
- `ts_event` — nanosecond timestamp
- `price` — trade price (scaled by 1e9)
- `size` — trade size
- `side` — aggressor side (A/B)
- `bid_px_00` — best bid price before trade (scaled by 1e9, `_00` = level 0)
- `ask_px_00` — best ask price before trade (scaled by 1e9)
- `bid_sz_00` — best bid size before trade
- `ask_sz_00` — best ask size before trade

Note: Databento uses zero-indexed level suffixes (`_00`) across all multi-level book schemas.

**Parquet columns (after normalization):**
```
timestamp, price, size, side, bid, ask, bid_size, ask_size
```

**Backward compatibility:** Existing code that reads `timestamp, price, size, side` continues to work — the new columns are additive. The replay engine and precompute pipeline don't need changes for reading ticks; they just ignore the new columns until book features are wired up.

**Re-fetch required:** Existing `NQ_*.parquet` files only have trade data. They must be deleted and re-fetched with `tbbo` schema to get book columns. The fetcher skips files that already exist, so delete the old files first or add a `--force` flag.

### Book Accumulator

New `IncrementalBookStats` accumulator that tracks per-candle spread and book statistics from TBBO data:

```python
@dataclass
class BookStats:
    avg_spread: float          # Average spread across all trades in candle
    max_spread: float          # Max spread seen in candle
    avg_bid_size: float        # Average best bid size
    avg_ask_size: float        # Average best ask size
    trade_count: int           # Number of trades with valid book data

class IncrementalBookStats:
    def update(self, bid: float, ask: float, bid_size: int, ask_size: int) -> None
    def get() -> BookStats | None
    def reset() -> None
```

The accumulator resets each 1m candle (same lifecycle as `CandleAggregator`). The replay engine calls `update()` on every tick that has book data, and `get()` + `reset()` on bar close.

**Guard conditions:** Skip ticks where `bid <= 0`, `ask <= 0`, or `ask > price * 2` (catches both zero and INT64_MAX sentinel values from CME halts).

**Rolling window ownership:** The `ReplayEngine` maintains `_book_stats_window: list[BookStats]` (last 20 candles), analogous to `_candle_flows`. On each bar close: `_book_stats_window.append(book_acc.get())`, then `book_acc.reset()`. The window is passed to `extract_book_features()` for computing multi-candle features (spread_vs_avg, spread_compression).

**Warm-up:** Before 3 candles of book data have accumulated, `extract_book_features()` returns `zeros(5)`. The `spread_vs_avg` uses whatever candles are available (min 3), and `spread_compression` requires 5 candles to compute a meaningful slope — returns 0.0 until then.

### Book Features (5 dims)

New observation vector segment, appended after the macro segment:

| Index | Feature | Formula | Range |
|---|---|---|---|
| 0 | `spread_ticks_norm` | (ask - bid) / tick_size / 10 | [0, 1] capped at 10 ticks |
| 1 | `spread_vs_avg` | current_spread / avg_spread_20_candles / 3 | [0, 1] capped at 3x |
| 2 | `spread_widening` | 1.0 if spread > 2 × avg_spread else 0.0 | {0, 1} |
| 3 | `bid_ask_imbalance` | (bid_size - ask_size) / (bid_size + ask_size) | [-1, 1] |
| 4 | `spread_compression` | linear trend of spread over last 5 candles | [-1, 1] |

**Feature details:**

- **spread_ticks_norm:** The raw spread at the most recent trade, in ticks. NQ normal spread is 0.25 (1 tick). During volatility it widens to 2-10 ticks. Capped at 10 and divided by 10.

- **spread_vs_avg:** Contextualizes the current spread relative to recent history. A ratio of 1.0 = normal. >2.0 = unusual widening. Capped at 3.0 and divided by 3.

- **spread_widening:** Binary flag for extreme spread events (>2x average). This is the "something is happening" signal — when the spread doubles, market makers are pulling liquidity.

- **bid_ask_imbalance:** Who has more size on the book. Positive = bid-heavy (buyers waiting), negative = ask-heavy (sellers waiting). This is the passive order imbalance that your strategy docs describe as a key confirmation signal.

- **spread_compression:** Trend of spread over last 5 candles. Negative = spread narrowing (convergence, conviction building). Positive = spread widening (uncertainty). Computed as: linear regression slope of `[candle.avg_spread for candle in last_5]`, divided by `avg_spread_20_candles` for normalization, clipped to [-1, 1]. A slope of +avg_spread per candle maps to +1.0.

### Observation Vector Change

```
Before: 107 dims
  level_type(26) + orderflow(15) + structure(23) + tpo(13) + candle(15) + confluence(5) + macro(10)

After: 112 dims
  level_type(26) + orderflow(15) + structure(23) + tpo(13) + candle(15) + confluence(5) + macro(10) + book(5)
```

`OBSERVATION_DIM` changes from 107 to 112. This requires re-running `rl replay` and `rl train` — existing `.npy` episode files and model checkpoints are incompatible.

### Replay Engine Changes

1. **Tick processing:** On each tick, if `bid` and `ask` fields are present, update `IncrementalBookStats`.

2. **Bar close:** Get book stats snapshot, reset accumulator. Store per-candle book stats in a rolling window (last 20 candles, same as candle flows).

3. **State dict:** Add `book_stats` key containing the rolling window of `BookStats` objects.

4. **Observation builder:** New `extract_book_features(book_stats_window, tick_size)` function returns 5-dim numpy array.

### Graceful Degradation

If ticks don't have book columns (old Parquet files without TBBO data), the book features return zeros. This means:
- Existing Parquet files work until they're re-fetched
- The agent learns to use book features once TBBO data is available
- No hard dependency — book features are additive

## Files

| File | Change |
|---|---|
| `backend/src/rl/data/fetcher.py` | Change `schema="trades"` → `schema="tbbo"`, extract bid/ask/bid_size/ask_size fields |
| `backend/src/rl/data/accumulators.py` | Add `BookStats` dataclass and `IncrementalBookStats` accumulator |
| `backend/src/rl/features/book_features.py` | **NEW** — `extract_book_features()` returning 5-dim array |
| `backend/src/rl/features/observation.py` | Import and append book features segment, update OBSERVATION_DIM |
| `backend/src/rl/data/replay_engine.py` | Add book stats accumulator, feed TBBO data, pass to state dict |
| `backend/tests/test_rl_book_features.py` | **NEW** — tests for accumulator and feature extraction |

### What Does NOT Change

- `session_store.py` — precompute pipeline only needs price/size for VP, not book data
- `rl/config.py` — no new level types or hyperparameter changes
- `cli.py` — no new commands (fetcher handles the schema change transparently)
- `episode_builder.py` — unchanged, still labels by forward price action
- Network architecture — `OBSERVATION_DIM` is computed dynamically at import time; fresh network construction adapts automatically, but saved `.pt` checkpoints encode the old input dim and will fail to load

## Storage Impact

TBBO adds ~15 MB/month over trades-only (wider rows, same count):

| Timeframe | Trades only | TBBO |
|---|---|---|
| 1 month | ~45 MB | ~60 MB |
| 7 months (current) | ~303 MB | ~420 MB |
| 1 year | ~500 MB | ~700 MB |

Negligible increase for significantly richer data.

## Re-fetch Required

Existing Parquet files must be re-fetched with TBBO schema. Options:

**(A) Add `--force` flag to `rl fetch`** — deletes existing files before re-fetching. Recommended.

**(B) Separate filename pattern** — `NQ_YYYY-MM_tbbo.parquet`. Avoids overwriting but requires updating all consumers.

**Recommendation: Option A with opt-in `--force`.** Add `--force` flag that deletes existing files before re-fetching. Without `--force`, the fetcher skips existing files as before. One-time re-fetch when ready, same filenames, no consumer changes. Graceful degradation means old files still work (book features return zeros).

## Known Limitations

- **Book data quality during halts:** During the CME daily halt (17:00-18:00 ET), TBBO records may have stale bid/ask. The accumulator should skip ticks where `bid == 0` or `ask == 0`.
- **Spread is always 1 tick for NQ in normal conditions.** The spread features will be mostly constant (0.25 / 0.25 = 1 tick). Their value comes from the ~5% of the time when spreads widen — exactly when interesting things happen at levels.
- **OBSERVATION_DIM change breaks existing models.** Any saved `.npy` episodes and `.pt` checkpoints must be regenerated. This is expected during the pre-training development phase.
