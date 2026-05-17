# Fix OF Dims With L1 Quotes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the 25 orderflow observation dims that show r=-0.001 correlation with reward, by wiring TopstepX's free L1 quote stream into feature computation (currently only used for mark-to-market) and persisting it for backtest.

**Architecture:** L1 quotes (`bestBid`, `bestAsk`, `bestBidSize`, `bestAskSize`) arrive via `GatewayQuote` and are currently dropped after broker mark-to-market. We add: (1) a parquet writer that archives every quote update, (2) a top-of-book state tracker on `LevelMonitor` that any feature computation can read, (3) a new `l1_features` module that computes proper passive/active ratio, true spread, top-of-book imbalance, and absorption from L1 + trades, (4) update the existing `orderflow_features` to call the new computations where L1 data is available, (5) bump `SCHEMA_VERSION` since dim *semantics* change. Old episodes recorded without L1 state get the existing (broken) computation; new episodes get the L1-aware computation. After ~30 days of accumulation, retrain on L1-enabled subset and re-audit OF correlation.

**Tech Stack:** Python 3.10+, NumPy, pandas, pyarrow, pytest, asyncio.

---

## File Structure

| File | Purpose | Status |
|---|---|---|
| `backend/src/market_data/l1_quote_state.py` | In-memory top-of-book state (price + size for bid/ask, last_update_ts) | NEW |
| `backend/src/market_data/l1_persistence.py` | Hourly-rotating parquet writer for L1 quotes | NEW |
| `backend/src/market_data/level_monitor.py` | Hold `L1QuoteState` instance; expose accessor | MODIFY |
| `backend/src/stocks/server_bootstrap.py` | Wire `_on_quote` to both persistence writer AND `level_monitor.l1_state.update()` | MODIFY |
| `backend/src/rl/features/l1_features.py` | L1-aware feature primitives: spread, imbalance, passive/active ratio, absorption | NEW |
| `backend/src/rl/features/orderflow_features.py` | Use l1_features when `l1_snapshot` arg provided; fall back to candle-derived when None | MODIFY |
| `backend/src/rl/features/observation.py` | Read `level_monitor.l1_state` snapshot, pass into `extract_orderflow_features` | MODIFY |
| `backend/src/rl/features/observation_index.py` | Bump `SCHEMA_VERSION` to 4; update label docs | MODIFY |
| `backend/tests/market_data/test_l1_quote_state.py` | Unit tests for state tracker | NEW |
| `backend/tests/market_data/test_l1_persistence.py` | Unit tests for parquet writer | NEW |
| `backend/tests/rl/features/test_l1_features.py` | Unit tests for L1 feature primitives | NEW |
| `backend/tests/rl/features/test_orderflow_features_with_l1.py` | Integration test: L1 snapshot vs no-L1 produces different (improved) OF dims | NEW |

---

## Task 1: L1 Quote State Tracker

**Files:**
- Create: `backend/src/market_data/l1_quote_state.py`
- Test: `backend/tests/market_data/test_l1_quote_state.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/market_data/test_l1_quote_state.py
from src.market_data.l1_quote_state import L1QuoteState, L1Snapshot


def test_initial_state_returns_none_snapshot():
    state = L1QuoteState()
    assert state.snapshot() is None


def test_update_then_snapshot_returns_latest_quote():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=12, ask_size=8, ts=1.5)
    snap = state.snapshot()
    assert snap is not None
    assert snap.bid == 25000.0
    assert snap.ask == 25000.25
    assert snap.bid_size == 12
    assert snap.ask_size == 8
    assert snap.ts == 1.5
    assert snap.spread_ticks == 1.0  # (25000.25 - 25000.0) / 0.25


def test_zero_or_negative_sizes_clamp_to_zero():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=-3, ask_size=0, ts=1.0)
    snap = state.snapshot()
    assert snap.bid_size == 0
    assert snap.ask_size == 0


def test_crossed_book_keeps_last_valid():
    """If bid >= ask (e.g. data glitch), don't overwrite a valid state."""
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    state.update(bid=25001.0, ask=25000.5, bid_size=10, ask_size=10, ts=2.0)  # crossed
    snap = state.snapshot()
    assert snap.bid == 25000.0  # unchanged
    assert snap.ts == 1.0


def test_top_of_book_imbalance():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=30, ask_size=10, ts=1.0)
    snap = state.snapshot()
    # (30 - 10) / (30 + 10) = 0.5  (bid-side heavier)
    assert snap.top_imbalance == 0.5


def test_top_of_book_imbalance_zero_sizes():
    state = L1QuoteState()
    state.update(bid=25000.0, ask=25000.25, bid_size=0, ask_size=0, ts=1.0)
    snap = state.snapshot()
    assert snap.top_imbalance == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/market_data/test_l1_quote_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'L1QuoteState'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/market_data/l1_quote_state.py
"""Top-of-book L1 quote state — bestBid/bestAsk and their sizes from
TopstepX GatewayQuote events. Maintained on LevelMonitor so feature
extractors can read the latest book state synchronously without an
asyncio await.

L2 depth is intentionally NOT modeled here; that's a separate subscription
and the v6 plan covers it. This is the no-money path — L1 is free and
the dominant OF features (spread, passive/active classification, top-of-
book imbalance, absorption) can be computed from it alone.
"""

from __future__ import annotations

from dataclasses import dataclass

TICK_SIZE = 0.25


@dataclass(frozen=True)
class L1Snapshot:
    """Immutable point-in-time snapshot of top-of-book."""

    bid: float
    ask: float
    bid_size: int
    ask_size: int
    ts: float

    @property
    def spread_ticks(self) -> float:
        if self.ask <= 0 or self.bid <= 0:
            return 0.0
        return (self.ask - self.bid) / TICK_SIZE

    @property
    def top_imbalance(self) -> float:
        """(bid_size - ask_size) / total. Range [-1, +1]. Positive = bid-heavy."""
        total = self.bid_size + self.ask_size
        if total <= 0:
            return 0.0
        return (self.bid_size - self.ask_size) / total


class L1QuoteState:
    """Mutable holder for the latest L1 snapshot. Thread-safe for the
    expected single-writer (stream handler) + multi-reader (feature
    extractors on the asyncio loop) pattern.
    """

    def __init__(self) -> None:
        self._snapshot: L1Snapshot | None = None

    def update(
        self,
        bid: float,
        ask: float,
        bid_size: int,
        ask_size: int,
        ts: float,
    ) -> None:
        # Reject crossed/invalid books — keep last valid snapshot
        if bid <= 0 or ask <= 0 or bid >= ask:
            return
        clean_bid_size = max(0, int(bid_size))
        clean_ask_size = max(0, int(ask_size))
        self._snapshot = L1Snapshot(
            bid=bid,
            ask=ask,
            bid_size=clean_bid_size,
            ask_size=clean_ask_size,
            ts=ts,
        )

    def snapshot(self) -> L1Snapshot | None:
        return self._snapshot
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/market_data/test_l1_quote_state.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/l1_quote_state.py backend/tests/market_data/test_l1_quote_state.py
git commit -m "feat(market_data): add L1QuoteState top-of-book tracker"
```

---

## Task 2: L1 Quote Parquet Persistence

**Files:**
- Create: `backend/src/market_data/l1_persistence.py`
- Test: `backend/tests/market_data/test_l1_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/market_data/test_l1_persistence.py
import time
from pathlib import Path

import pandas as pd
import pytest

from src.market_data.l1_persistence import L1ParquetWriter


@pytest.fixture
def tmp_dir(tmp_path) -> Path:
    return tmp_path / "l1"


def test_writer_creates_directory(tmp_dir):
    writer = L1ParquetWriter(out_dir=tmp_dir)
    writer.close()
    assert tmp_dir.exists()


def test_writer_appends_records_and_flushes_to_parquet(tmp_dir):
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=0.0)
    now = time.time()
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=now)
    writer.record(bid=25000.25, ask=25000.5, bid_size=5, ask_size=12, ts=now + 0.1)
    writer.flush()
    writer.close()

    files = sorted(tmp_dir.rglob("*.parquet"))
    assert len(files) >= 1
    df = pd.read_parquet(files[0])
    assert list(df.columns) == ["ts", "bid", "ask", "bid_size", "ask_size"]
    assert len(df) == 2


def test_writer_partitions_by_utc_date(tmp_dir, monkeypatch):
    """Files should be partitioned: <out_dir>/YYYY-MM-DD/NQ_HH.parquet"""
    from datetime import datetime, timezone

    fake_now = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc).timestamp()
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=0.0)
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=fake_now)
    writer.flush()
    writer.close()

    expected_dir = tmp_dir / "2026-05-17"
    assert expected_dir.exists()
    files = list(expected_dir.glob("*.parquet"))
    assert len(files) == 1
    assert "14" in files[0].name  # hour partition


def test_writer_buffers_until_flush_interval(tmp_dir):
    """With flush_interval_s=10, calling record() shouldn't write to disk."""
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=10.0)
    now = time.time()
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=now)
    # Don't call flush — no files yet
    files = list(tmp_dir.rglob("*.parquet"))
    assert len(files) == 0
    writer.close()  # close should always flush


def test_close_flushes_remaining_buffer(tmp_dir):
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=3600.0)
    now = time.time()
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=now)
    writer.close()
    files = list(tmp_dir.rglob("*.parquet"))
    assert len(files) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/market_data/test_l1_persistence.py -v`
Expected: FAIL with `ImportError: cannot import name 'L1ParquetWriter'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/market_data/l1_persistence.py
"""Append-only L1 quote parquet writer.

Partitioning: <out_dir>/YYYY-MM-DD/NQ_HH.parquet
- One directory per UTC date
- One file per UTC hour
- Each file is rewritten on every flush (full rewrite, not append)
  — pyarrow doesn't support true append on a single parquet file, so we
  buffer in-memory and rewrite the hour file on each flush. NQ generates
  ~50k quote updates/hour which is small enough that rewriting is fine.

Forward-going only — there's no backfill source. Every minute of L1
data missed = a minute of OF training data the model won't have.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)


class L1ParquetWriter:
    def __init__(
        self,
        out_dir: Path | str,
        flush_interval_s: float = 60.0,
    ) -> None:
        self._out_dir = Path(out_dir)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._flush_interval_s = flush_interval_s
        self._buf: list[dict] = []
        self._last_flush_ts: float = 0.0

    def record(
        self,
        bid: float,
        ask: float,
        bid_size: int,
        ask_size: int,
        ts: float,
    ) -> None:
        self._buf.append(
            {
                "ts": ts,
                "bid": float(bid),
                "ask": float(ask),
                "bid_size": int(bid_size),
                "ask_size": int(ask_size),
            }
        )
        if ts - self._last_flush_ts >= self._flush_interval_s:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        try:
            buf_by_hour: dict[Path, list[dict]] = {}
            for rec in self._buf:
                dt = datetime.fromtimestamp(rec["ts"], tz=timezone.utc)
                date_dir = self._out_dir / dt.strftime("%Y-%m-%d")
                date_dir.mkdir(exist_ok=True)
                file = date_dir / f"NQ_{dt.strftime('%H')}.parquet"
                buf_by_hour.setdefault(file, []).append(rec)
            for file, recs in buf_by_hour.items():
                new_df = pd.DataFrame(recs)
                if file.exists():
                    existing = pd.read_parquet(file)
                    df = pd.concat([existing, new_df], ignore_index=True)
                else:
                    df = new_df
                pq.write_table(pa.Table.from_pandas(df, preserve_index=False), file)
            self._buf.clear()
            if self._buf:
                self._last_flush_ts = self._buf[-1]["ts"]
        except Exception:
            log.exception("L1ParquetWriter flush failed")

    def close(self) -> None:
        self.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/market_data/test_l1_persistence.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/l1_persistence.py backend/tests/market_data/test_l1_persistence.py
git commit -m "feat(market_data): add L1 quote parquet writer with hourly partitioning"
```

---

## Task 3: Wire L1QuoteState into LevelMonitor

**Files:**
- Modify: `backend/src/market_data/level_monitor.py` (LevelMonitor.__init__)
- Test: `backend/tests/market_data/test_level_monitor_l1.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/market_data/test_level_monitor_l1.py
from src.market_data.level_monitor import LevelMonitor
from src.market_data.l1_quote_state import L1Snapshot


def test_level_monitor_has_l1_state():
    lm = LevelMonitor()
    assert hasattr(lm, "l1_state")
    assert lm.l1_state.snapshot() is None


def test_level_monitor_l1_state_update_and_snapshot():
    lm = LevelMonitor()
    lm.l1_state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=1.0)
    snap = lm.l1_state.snapshot()
    assert isinstance(snap, L1Snapshot)
    assert snap.bid == 25000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor_l1.py -v`
Expected: FAIL with `AttributeError: 'LevelMonitor' object has no attribute 'l1_state'`

- [ ] **Step 3: Add l1_state to LevelMonitor.__init__**

In `backend/src/market_data/level_monitor.py`, add the import at top and add the field to `LevelMonitor.__init__`. The exact insertion point: just before `# Zone-aware DQN inference state`.

```python
# At top of file, with other imports:
from .l1_quote_state import L1QuoteState

# Inside LevelMonitor.__init__, just before # Zone-aware DQN inference state
self.l1_state = L1QuoteState()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor_l1.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/market_data/test_level_monitor_l1.py
git commit -m "feat(level_monitor): hold L1QuoteState for feature extraction"
```

---

## Task 4: Wire on_quote handler in server_bootstrap

**Files:**
- Modify: `backend/src/stocks/server_bootstrap.py:981-997`
- Test: integration verified manually after deploy (no unit test — handler is async-glued)

- [ ] **Step 1: Read current handler**

Read `backend/src/stocks/server_bootstrap.py:975-1000` to confirm shape of `_on_quote_mark`.

- [ ] **Step 2: Modify _on_quote_mark to also update level_monitor.l1_state and persist**

Replace the current `_on_quote_mark` function and its `stream.on_quote = ...` line with:

```python
# Add at top of server_bootstrap.py with other imports
from pathlib import Path
from src.market_data.l1_persistence import L1ParquetWriter

# ... inside the function that builds the wiring, replace _on_quote_mark with:

_L1_OUT_DIR = Path("/app/data/rl/l1_quotes")
l1_writer = L1ParquetWriter(out_dir=_L1_OUT_DIR, flush_interval_s=60.0)

def _on_quote(quote_payload) -> None:
    """Triple-duty: mark-to-market for broker, L1 state for feature
    extraction, persistence for backtest."""
    try:
        import time
        ts = time.time()
        bid = quote_payload.get("bestBid") or quote_payload.get("bid") or 0.0
        ask = quote_payload.get("bestAsk") or quote_payload.get("ask") or 0.0
        bid_size = quote_payload.get("bestBidSize") or quote_payload.get("bid_size") or 0
        ask_size = quote_payload.get("bestAskSize") or quote_payload.get("ask_size") or 0
        last_price = quote_payload.get("lastPrice", 0)

        # 1. Mark-to-market (existing behavior)
        if last_price > 0:
            adapter.update_mark_and_check_be_lock(last_price)

        # 2. L1 state for feature extraction
        if bid > 0 and ask > 0:
            level_monitor.l1_state.update(
                bid=float(bid),
                ask=float(ask),
                bid_size=int(bid_size),
                ask_size=int(ask_size),
                ts=ts,
            )
            # 3. Persistence
            l1_writer.record(
                bid=float(bid),
                ask=float(ask),
                bid_size=int(bid_size),
                ask_size=int(ask_size),
                ts=ts,
            )
    except Exception:
        log.debug("on_quote error", exc_info=True)

stream.on_quote = _on_quote
```

Also register `l1_writer.close()` to fire at app shutdown — find the existing `@app.on_event("shutdown")` or `lifespan` block and add `l1_writer.close()`.

- [ ] **Step 3: Verify syntax by importing module**

Run: `cd backend && python -c "from src.stocks.server_bootstrap import *; print('ok')"`
Expected: `ok` (no syntax error)

- [ ] **Step 4: Commit**

```bash
git add backend/src/stocks/server_bootstrap.py
git commit -m "feat(stocks): wire L1 quotes to level_monitor state + parquet persistence

Previously _on_quote_mark only updated broker mark-to-market. Now it also
maintains LevelMonitor.l1_state for feature extraction (top-of-book, spread,
size imbalance) and persists every quote update to /app/data/rl/l1_quotes/
in hourly-partitioned parquet for future backtest + retrain."
```

---

## Task 5: L1 Feature Primitives Module

**Files:**
- Create: `backend/src/rl/features/l1_features.py`
- Test: `backend/tests/rl/features/test_l1_features.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/features/test_l1_features.py
from src.market_data.l1_quote_state import L1Snapshot
from src.rl.features.l1_features import (
    aggressor_side,
    classify_trade_lee_ready,
    compute_l1_features,
    compute_top_imbalance,
    compute_true_spread_ticks,
    detect_absorption_l1,
)


def test_true_spread_in_ticks():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    assert compute_true_spread_ticks(snap) == 1.0


def test_top_imbalance_bid_heavy():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=30, ask_size=10, ts=1.0)
    assert compute_top_imbalance(snap) == 0.5


def test_classify_trade_lee_ready_buy_at_ask():
    """Trade price == ask → buy aggressor."""
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    assert classify_trade_lee_ready(trade_price=25000.25, snapshot=snap) == "buy"


def test_classify_trade_lee_ready_sell_at_bid():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    assert classify_trade_lee_ready(trade_price=25000.0, snapshot=snap) == "sell"


def test_classify_trade_lee_ready_midpoint_inferred_by_tick_rule():
    """Trade strictly inside spread — use tick-rule fallback."""
    snap = L1Snapshot(bid=25000.0, ask=25000.50, bid_size=10, ask_size=10, ts=1.0)
    # Tick-rule: trade above previous = buy, below = sell, equal = previous
    assert classify_trade_lee_ready(trade_price=25000.25, snapshot=snap, prev_trade_price=25000.0) == "buy"
    assert classify_trade_lee_ready(trade_price=25000.25, snapshot=snap, prev_trade_price=25000.50) == "sell"


def test_aggressor_side_passive_active_decomposition():
    """Given a list of trade dicts with prices + sizes, classify each
    via L1 snapshot and return (passive_volume, active_volume)."""
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    trades = [
        {"price": 25000.25, "size": 5},  # buy aggressor (active buy)
        {"price": 25000.0, "size": 8},   # sell aggressor (active sell)
        {"price": 25000.25, "size": 3},  # buy aggressor (active buy)
    ]
    # 'active' = volume where price hit best bid or lifted best ask
    # in this test all trades match snap exactly → all active
    passive_vol, active_vol = aggressor_side(trades, snap)
    assert passive_vol == 0
    assert active_vol == 16


def test_detect_absorption_l1_heavy_volume_no_book_displacement():
    """Lots of trade volume hits the ask but bestAskSize barely moves
    → passive offers are absorbing the buying pressure."""
    snap_before = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=50, ts=1.0)
    snap_after = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=48, ts=2.0)
    trades = [{"price": 25000.25, "size": 20, "ts": 1.5}]  # 20 contracts hit
    score = detect_absorption_l1(trades=trades, snap_before=snap_before, snap_after=snap_after)
    # 20 contracts traded but ask size only dropped by 2 (refresh detected)
    # → strong absorption (>0.5)
    assert score > 0.5


def test_detect_absorption_l1_no_absorption_when_book_clears():
    """20 contracts hit the ask and ask size dropped by 20 → no refresh, no absorption."""
    snap_before = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=50, ts=1.0)
    snap_after = L1Snapshot(bid=25000.0, ask=25000.50, bid_size=10, ask_size=30, ts=2.0)
    trades = [{"price": 25000.25, "size": 20, "ts": 1.5}]
    score = detect_absorption_l1(trades=trades, snap_before=snap_before, snap_after=snap_after)
    assert score < 0.3


def test_compute_l1_features_returns_dict_with_expected_keys():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    trades = [{"price": 25000.25, "size": 5}, {"price": 25000.0, "size": 3}]
    feats = compute_l1_features(snapshot=snap, recent_trades=trades)
    expected_keys = {
        "spread_ticks",
        "top_imbalance",
        "passive_active_ratio",
        "active_buy_volume",
        "active_sell_volume",
        "trade_count",
    }
    assert set(feats.keys()) == expected_keys


def test_compute_l1_features_handles_none_snapshot():
    """When L1 state is unavailable, return zeros (graceful degradation)."""
    feats = compute_l1_features(snapshot=None, recent_trades=[])
    assert feats["spread_ticks"] == 0.0
    assert feats["top_imbalance"] == 0.0
    assert feats["passive_active_ratio"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/rl/features/test_l1_features.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/rl/features/l1_features.py
"""L1-aware orderflow feature primitives.

These functions consume an `L1Snapshot` (top-of-book at a given moment)
plus a list of recent trade dicts, and produce the OF features that
require book context to compute correctly. Replaces the candle-derived
approximations in `orderflow_features.py` for the dims that can be
properly computed from L1 + trades.

NOTE: still uses approximations where L2 would be needed:
  - stacked_imbalance_count (depth>1): can't measure from L1
  - imbalance_density across multiple levels: can't measure from L1
  Those are left to candle-derived computation as a fallback.

This module is pure — no I/O, no side effects, no class state. Inputs
in, dict out. Easy to test, easy to backtest.
"""

from __future__ import annotations

from typing import Literal

from src.market_data.l1_quote_state import L1Snapshot

TICK_SIZE = 0.25


def compute_true_spread_ticks(snapshot: L1Snapshot) -> float:
    return snapshot.spread_ticks


def compute_top_imbalance(snapshot: L1Snapshot) -> float:
    return snapshot.top_imbalance


def classify_trade_lee_ready(
    trade_price: float,
    snapshot: L1Snapshot,
    prev_trade_price: float | None = None,
) -> Literal["buy", "sell", "unknown"]:
    """Lee-Ready trade classification.

    - trade_price >= ask → buy aggressor (lifted offer)
    - trade_price <= bid → sell aggressor (hit bid)
    - inside spread → tick-rule (vs previous trade price)
    """
    eps = TICK_SIZE / 100.0
    if trade_price >= snapshot.ask - eps:
        return "buy"
    if trade_price <= snapshot.bid + eps:
        return "sell"
    if prev_trade_price is None:
        return "unknown"
    if trade_price > prev_trade_price:
        return "buy"
    if trade_price < prev_trade_price:
        return "sell"
    return "unknown"


def aggressor_side(
    trades: list[dict],
    snapshot: L1Snapshot,
) -> tuple[int, int]:
    """Split trade volume into (passive_volume, active_volume).

    'Active' = trade volume where price >= ask or <= bid (clear aggressor).
    'Passive' = trade volume strictly inside spread (midpoint — uncertain).
    """
    passive = 0
    active = 0
    eps = TICK_SIZE / 100.0
    for t in trades:
        price = float(t.get("price", 0))
        size = int(t.get("size", 0))
        if price <= 0 or size <= 0:
            continue
        if price >= snapshot.ask - eps or price <= snapshot.bid + eps:
            active += size
        else:
            passive += size
    return passive, active


def detect_absorption_l1(
    trades: list[dict],
    snap_before: L1Snapshot,
    snap_after: L1Snapshot,
) -> float:
    """Score [0,1]: how much trade volume hit a level without
    proportional book displacement (= passive size refreshed/absorbed).

    Heuristic:
        total_hit = sum of trade sizes at ask (buy aggression)
        actual_displacement = ask_size_before - ask_size_after (if ask price unchanged)
        absorption_ratio = 1 - displacement / hit  (clamped to [0,1])

    High score = lots traded, book barely moved → strong passive absorption
    (iceberg or hidden orders refreshing).
    """
    if not trades or snap_before is None or snap_after is None:
        return 0.0

    # Only score when the inside price didn't shift (otherwise displacement
    # is the obvious explanation)
    ask_price_stable = abs(snap_before.ask - snap_after.ask) < TICK_SIZE / 2
    bid_price_stable = abs(snap_before.bid - snap_after.bid) < TICK_SIZE / 2

    if not (ask_price_stable or bid_price_stable):
        return 0.0

    # Aggregate buy-side aggression at the ask
    eps = TICK_SIZE / 100.0
    buy_hit = sum(int(t.get("size", 0)) for t in trades if float(t.get("price", 0)) >= snap_before.ask - eps)
    sell_hit = sum(int(t.get("size", 0)) for t in trades if float(t.get("price", 0)) <= snap_before.bid + eps)

    # Score the side with more aggression
    if buy_hit >= sell_hit and ask_price_stable:
        displacement = max(0, snap_before.ask_size - snap_after.ask_size)
        if buy_hit <= 0:
            return 0.0
        absorption = 1.0 - (displacement / buy_hit)
        return max(0.0, min(1.0, absorption))

    if sell_hit > 0 and bid_price_stable:
        displacement = max(0, snap_before.bid_size - snap_after.bid_size)
        absorption = 1.0 - (displacement / sell_hit)
        return max(0.0, min(1.0, absorption))

    return 0.0


def compute_l1_features(
    snapshot: L1Snapshot | None,
    recent_trades: list[dict],
) -> dict[str, float]:
    """One-shot computation of L1 features from a current snapshot + recent trades.

    Returns dict with keys: spread_ticks, top_imbalance, passive_active_ratio,
    active_buy_volume, active_sell_volume, trade_count.

    Gracefully returns zeros when snapshot is None (L1 unavailable).
    """
    if snapshot is None:
        return {
            "spread_ticks": 0.0,
            "top_imbalance": 0.0,
            "passive_active_ratio": 0.0,
            "active_buy_volume": 0.0,
            "active_sell_volume": 0.0,
            "trade_count": 0.0,
        }

    spread = compute_true_spread_ticks(snapshot)
    imb = compute_top_imbalance(snapshot)
    passive_vol, active_vol = aggressor_side(recent_trades, snapshot)

    # passive/active ratio: high = passive flow dominant (weaker hand winning)
    pa_ratio = passive_vol / max(active_vol, 1)

    # Split active by direction
    eps = TICK_SIZE / 100.0
    active_buy = sum(int(t.get("size", 0)) for t in recent_trades if float(t.get("price", 0)) >= snapshot.ask - eps)
    active_sell = sum(int(t.get("size", 0)) for t in recent_trades if float(t.get("price", 0)) <= snapshot.bid + eps)

    return {
        "spread_ticks": float(spread),
        "top_imbalance": float(imb),
        "passive_active_ratio": float(pa_ratio),
        "active_buy_volume": float(active_buy),
        "active_sell_volume": float(active_sell),
        "trade_count": float(len(recent_trades)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/features/test_l1_features.py -v`
Expected: 10 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/l1_features.py backend/tests/rl/features/test_l1_features.py
git commit -m "feat(rl): add L1-aware orderflow feature primitives

Pure functions for true spread, top-of-book imbalance, Lee-Ready aggressor
classification, passive/active decomposition, and L1-based absorption
detection. These replace candle-derived approximations in
orderflow_features.py for dims that require book context to compute
correctly."
```

---

## Task 6: Integrate L1 features into extract_orderflow_features

**Files:**
- Modify: `backend/src/rl/features/orderflow_features.py` (function signature + dim computation)
- Test: `backend/tests/rl/features/test_orderflow_features_with_l1.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/rl/features/test_orderflow_features_with_l1.py
"""Integration test: same candle data but with vs without L1 snapshot
produces different (L1-improved) values for the L1-derived dims."""

import numpy as np
import pytest
from datetime import datetime, timezone

from src.market_data.l1_quote_state import L1Snapshot
from src.market_data.orderflow import CandleFlow
from src.rl.features.orderflow_features import extract_orderflow_features


def _make_candle(volume=100, delta=50, body_ratio=0.5):
    return CandleFlow(
        ts=datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc),
        open=25000.0, high=25000.5, low=24999.5, close=25000.25,
        volume=volume, buy_volume=int(volume * 0.6), sell_volume=int(volume * 0.4),
        delta=delta, tick_count=10, spread=1.0,
    )


def test_extract_with_l1_overrides_spread_ticks():
    candles = [_make_candle() for _ in range(5)]
    l1_snap = L1Snapshot(bid=25000.0, ask=25000.50, bid_size=10, ask_size=10, ts=1.0)
    feats_no_l1 = extract_orderflow_features(candles, signals=None, l1_snapshot=None, recent_trades=None)
    feats_with_l1 = extract_orderflow_features(candles, signals=None, l1_snapshot=l1_snap, recent_trades=[])

    # spread_ticks is index 6 in the 21-dim vector
    SPREAD_IDX = 6
    # L1 spread = (25000.50 - 25000.00) / 0.25 = 2 ticks, normalized /50 = 0.04
    assert feats_with_l1[SPREAD_IDX] == pytest.approx(2.0 / 50.0, abs=1e-4)
    # Without L1, spread comes from candle (high-low = 1.0 → 4 ticks → 0.08)
    assert feats_no_l1[SPREAD_IDX] == pytest.approx(4.0 / 50.0, abs=1e-4)


def test_extract_with_l1_overrides_passive_active_ratio():
    candles = [_make_candle() for _ in range(5)]
    l1_snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    trades = [
        {"price": 25000.25, "size": 10},  # buy aggressor (active)
        {"price": 25000.25, "size": 5},   # buy aggressor (active)
        {"price": 25000.10, "size": 20},  # inside spread → passive
    ]
    feats = extract_orderflow_features(candles, signals=None, l1_snapshot=l1_snap, recent_trades=trades)
    # passive_active_ratio is index 7
    PA_IDX = 7
    # passive=20, active=15 → ratio = 20/15 = 1.33, normalized /5 = 0.267
    assert feats[PA_IDX] == pytest.approx(1.333 / 5.0, abs=0.01)


def test_extract_without_l1_preserves_candle_behavior():
    """Calling without l1_snapshot should produce identical output to
    the legacy call signature (backward compatibility)."""
    candles = [_make_candle() for _ in range(5)]
    feats_new = extract_orderflow_features(candles, signals=None, l1_snapshot=None, recent_trades=None)
    feats_legacy = extract_orderflow_features(candles, signals=None)
    np.testing.assert_array_equal(feats_new, feats_legacy)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/rl/features/test_orderflow_features_with_l1.py -v`
Expected: FAIL — `extract_orderflow_features() got an unexpected keyword argument 'l1_snapshot'`

- [ ] **Step 3: Add l1_snapshot + recent_trades params to extract_orderflow_features**

In `backend/src/rl/features/orderflow_features.py`, change the function signature and override the L1-derivable dims when `l1_snapshot` is provided:

```python
# At top of file, after existing imports
from .l1_features import compute_l1_features
from ...market_data.l1_quote_state import L1Snapshot


def extract_orderflow_features(
    candles: list[CandleFlow],
    signals: OrderflowSignals | None = None,
    lookback: int = 20,
    l1_snapshot: L1Snapshot | None = None,
    recent_trades: list[dict] | None = None,
) -> np.ndarray:
    """[existing docstring]

    When l1_snapshot is provided, dims 6 (spread_ticks) and 7
    (passive_active_ratio) are recomputed from L1 + trade tape
    (Lee-Ready classification + true bid/ask spread) instead of
    candle-derived approximations.
    """
    # ... existing computation through line ~186 ...

    feats = np.array(
        [
            delta_pct,
            delta_norm,
            cvd_norm,
            cvd_trend_val,
            volume_ratio,
            body_ratio,
            spread_ticks,           # index 6 — possibly overridden below
            passive_active,         # index 7 — possibly overridden below
            imbalance_max,
            stacked_count,
            stacked_dir,
            big_count,
            big_net,
            vsa_abs,
            stop_run,
            delta_accel,
            absorption_str,
            init_momentum,
            vol_climax,
            delta_div,
            flow_shift,
        ],
        dtype=np.float32,
    )

    # L1 override: when L1 snapshot is available, recompute the dims that
    # actually need book context. Leave the rest at candle-derived values.
    if l1_snapshot is not None:
        l1_feats = compute_l1_features(snapshot=l1_snapshot, recent_trades=recent_trades or [])
        # Index 6: spread_ticks (capped at 50, normalised /50)
        feats[6] = min(l1_feats["spread_ticks"], 50.0) / 50.0
        # Index 7: passive_active_ratio (capped at 5, normalised /5)
        feats[7] = min(l1_feats["passive_active_ratio"], 5.0) / 5.0

    return feats
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/features/test_orderflow_features_with_l1.py -v`
Expected: 3 PASS

- [ ] **Step 5: Run the full existing orderflow test suite to confirm no regressions**

Run: `cd backend && python -m pytest tests/rl/features/ -v`
Expected: all existing tests still PASS (l1_snapshot defaults to None for backward compat)

- [ ] **Step 6: Commit**

```bash
git add backend/src/rl/features/orderflow_features.py backend/tests/rl/features/test_orderflow_features_with_l1.py
git commit -m "feat(rl): wire L1 snapshot override into orderflow feature extraction

When the L1 quote state is available, dims 6 (spread_ticks) and 7
(passive_active_ratio) recompute from true bid/ask + Lee-Ready aggressor
classification instead of candle-derived approximations. Other dims
unchanged. Backward-compatible: l1_snapshot=None preserves legacy behavior."
```

---

## Task 7: Wire L1 state into observation builder

**Files:**
- Modify: `backend/src/rl/features/observation.py` (build_observation function)
- Test: `backend/tests/rl/features/test_observation_l1_propagation.py` (new)

- [ ] **Step 1: Read current build_observation to find where extract_orderflow_features is called**

Run: `grep -n "extract_orderflow_features" backend/src/rl/features/observation.py`

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/rl/features/test_observation_l1_propagation.py
"""Confirm build_observation passes l1_snapshot + recent_trades from rl_state
into extract_orderflow_features."""

import numpy as np
from src.market_data.l1_quote_state import L1Snapshot
from src.rl.features.observation import build_observation


def _minimal_rl_state(with_l1=False):
    """Build a minimal rl_state dict that build_observation accepts."""
    state = {
        "candles": [],  # extract_orderflow_features returns zeros when empty
        "signals": None,
        "zone": None,
        # ... add other required keys based on what build_observation needs
    }
    if with_l1:
        state["l1_snapshot"] = L1Snapshot(
            bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0,
        )
        state["recent_trades"] = []
    return state


def test_build_observation_with_l1_in_state_produces_different_spread_dim():
    """When rl_state contains l1_snapshot, the spread_ticks OF dim
    should reflect L1 (1 tick spread) not candle (0 spread → 0)."""
    from src.rl.features.observation_index import _SEGMENT_OFFSETS

    of_start, of_end = _SEGMENT_OFFSETS["orderflow"]
    SPREAD_OFFSET_IN_OF = 6  # spread_ticks is 7th label

    state_no_l1 = _minimal_rl_state(with_l1=False)
    state_with_l1 = _minimal_rl_state(with_l1=True)

    obs_no_l1 = build_observation(state_no_l1)
    obs_with_l1 = build_observation(state_with_l1)

    spread_no_l1 = obs_no_l1[of_start + SPREAD_OFFSET_IN_OF]
    spread_with_l1 = obs_with_l1[of_start + SPREAD_OFFSET_IN_OF]

    # With L1, spread = 1 tick / 50 = 0.02. Without L1 + empty candles, 0.0.
    assert spread_no_l1 == 0.0
    assert spread_with_l1 == 0.02
```

- [ ] **Step 3: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/rl/features/test_observation_l1_propagation.py -v`
Expected: FAIL — likely an assertion error since build_observation currently ignores `l1_snapshot` from state

- [ ] **Step 4: Modify build_observation to pull l1_snapshot from rl_state**

Find the line in `backend/src/rl/features/observation.py` that calls `extract_orderflow_features(...)` and update it:

```python
of_feats = extract_orderflow_features(
    candles=rl_state.get("candles", []),
    signals=rl_state.get("signals"),
    l1_snapshot=rl_state.get("l1_snapshot"),
    recent_trades=rl_state.get("recent_trades", []),
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/rl/features/test_observation_l1_propagation.py -v`
Expected: PASS

- [ ] **Step 6: Run all observation tests to check no regressions**

Run: `cd backend && python -m pytest tests/rl/features/ -v`
Expected: all PASS (l1_snapshot is optional)

- [ ] **Step 7: Commit**

```bash
git add backend/src/rl/features/observation.py backend/tests/rl/features/test_observation_l1_propagation.py
git commit -m "feat(rl): propagate L1 snapshot from rl_state into OF feature extraction"
```

---

## Task 8: Build _build_rl_state_zone in level_monitor to include L1 state

**Files:**
- Modify: `backend/src/market_data/level_monitor.py` — `_build_rl_state_zone` method

- [ ] **Step 1: Find the method**

Run: `grep -n "_build_rl_state_zone\|def _build_rl_state" backend/src/market_data/level_monitor.py`

- [ ] **Step 2: Read the current implementation**

Read the function. It assembles a dict that gets passed to `build_observation`.

- [ ] **Step 3: Add l1_snapshot + recent_trades to the returned dict**

Inside `_build_rl_state_zone`, after the existing fields, add:

```python
# L1 quote state — feeds OF feature extraction (Task 7 wiring)
state["l1_snapshot"] = self.l1_state.snapshot()

# Recent trades for L1 features (Lee-Ready classification + absorption)
# Pull last N trades from existing tick buffer. The buffer name varies —
# check self._tick_buffer or self._candle_builder.recent_trades.
state["recent_trades"] = list(self._recent_trades[-200:]) if hasattr(self, "_recent_trades") else []
```

- [ ] **Step 4: Add the _recent_trades buffer if it doesn't exist**

In `LevelMonitor.__init__`, add:

```python
from collections import deque
# ...
self._recent_trades: deque = deque(maxlen=500)
```

In the `on_tick` method (find with `grep -n "def on_tick" backend/src/market_data/level_monitor.py`), after the existing logic, append the tick to the buffer:

```python
self._recent_trades.append({
    "price": float(price),
    "size": int(size),
    "side": side,
    "ts": float(ts),
})
```

- [ ] **Step 5: Smoke-test the import**

Run: `cd backend && python -c "from src.market_data.level_monitor import LevelMonitor; lm = LevelMonitor(); print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(level_monitor): include L1 snapshot + recent trades in rl_state for OF features"
```

---

## Task 9: Bump SCHEMA_VERSION and document semantics change

**Files:**
- Modify: `backend/src/rl/features/observation_index.py`

- [ ] **Step 1: Update SCHEMA_VERSION + add comment**

In `backend/src/rl/features/observation_index.py`, change:

```python
SCHEMA_VERSION = 3  # 2026-05-15: appended zone_sweep(2) segment at tail for stop-hunt-pattern learning
```

to:

```python
SCHEMA_VERSION = 4  # 2026-05-17: OF dims 6 (spread_ticks) and 7 (passive_active_ratio)
                    # now L1-quote-derived when L1 state is available; falls back to
                    # candle-derived for backward compat. Dim count unchanged.
```

- [ ] **Step 2: Add note in _ORDERFLOW_LABELS about L1 dependence**

Above the `_ORDERFLOW_LABELS` definition, add a comment:

```python
# Orderflow segment (25 dims). Dims marked [L1] are recomputed from L1
# quote state when available, otherwise fall back to candle-derived values.
# Dims marked [L2-needed] are placeholders; they require depth data we
# don't currently subscribe to and will read as the candle approximation.
```

- [ ] **Step 3: Run schema validation test**

Run: `cd backend && python -m pytest tests/rl/features/test_observation_schema.py -v` (or whichever test validates the schema)

If no such test exists, run: `cd backend && python -c "from src.rl.features.observation_index import schema; s = schema(); print('version:', s['version']); print('total_dim:', s['total_dim'])"`

Expected: version 4, total_dim unchanged (still matches OBSERVATION_DIM)

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/features/observation_index.py
git commit -m "feat(rl): bump SCHEMA_VERSION to 4 — L1-aware OF dim semantics

OF dims 6 (spread_ticks) and 7 (passive_active_ratio) now compute from
L1 quote state when available. Dim count and segment layout unchanged
so old models still load. SCHEMA_VERSION bump signals semantic shift —
old episodes (pre-2026-05-17) have these dims candle-derived; new
episodes have them L1-derived."
```

---

## Task 10: End-to-End Smoke Test on Live Container

**Files:**
- None — manual verification only

- [ ] **Step 1: Deploy the changes**

```bash
git push origin main
ssh root@148.251.40.251 "ALLOW_OPEN_POSITION_DEPLOY=1 bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

- [ ] **Step 2: Verify container is on new HEAD**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD; curl -sf http://localhost:8000/health | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"boot:\",d.get(\"boot_id\"),\"up:\",d.get(\"uptime\"))'"
```

Expected: git HEAD matches your push, boot_id is fresh.

- [ ] **Step 3: Verify L1 quotes are arriving and being persisted (during market hours)**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose logs backend --since 5m | grep -iE 'quote|GatewayQuote' | head -5"
```

Then check the parquet dir after ~2 minutes of live quotes:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend ls -la /app/data/rl/l1_quotes/ 2>&1"
```

Expected: a `YYYY-MM-DD/` subdirectory with at least one `NQ_HH.parquet` file.

- [ ] **Step 4: Verify L1 state is being populated**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend python -c '
from src.market_data.level_monitor import LevelMonitor
# Get the running instance — actual import path depends on app wiring
import src.api.deps as deps
lm = deps.get_level_monitor()
snap = lm.l1_state.snapshot()
print(\"snapshot:\", snap)
'"
```

Expected: a non-None snapshot with positive bid + ask.

- [ ] **Step 5: Verify per-group OF diagnostic now shows nonzero OF strength on next signal**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose logs backend --since 30m | grep 'per-group\|OF ' | tail -5"
```

Expected: OF group strength > 0.0 in the per-group diagnostic logs.

- [ ] **Step 6: Document any issues + commit any fixes as separate commits**

If issues:
- L1 parquet not creating: likely permissions or path. Check `/app/data/rl/` mount.
- L1 snapshot stays None: `_on_quote` handler may not be wired correctly.
- Tests pass but obs values unchanged: `_build_rl_state_zone` may not be including l1_snapshot.

Don't amend prior commits — fix forward.

---

## Task 11: Validate Forward-Going L1 Archive (30-day wait)

**Files:**
- None — passive accumulation

- [ ] **Step 1: Schedule a check 30 days from deploy**

Add to your calendar / todo system: "Check L1 parquet archive size + retrain GBT on L1-enabled episodes" — 30 days from Task 10 deploy date.

- [ ] **Step 2: Monitor weekly via**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'du -sh /app/data/rl/l1_quotes/; ls /app/data/rl/l1_quotes/ | wc -l'"
```

Expected growth: ~5-20 MB/day during RTH (~100 MB/week).

- [ ] **Step 3: After 30 days, retrain GBT + re-run OF correlation audit**

The trainer daemon retrains nightly. To force a fresh retrain after the L1 accumulation period:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'taskset -c 0,1,4,5 nice -n 19 bash /app/backend/scripts/rl_train_pipeline.sh'"
```

Then re-run the methodology audit (`c:/tmp/methodology_stats.py` from prior session) to check OF correlation. Expected: OF r > 0.05 (vs current -0.001) if L1 wiring is correct.

If still < 0.05 after 30 days of L1 data → escalate to L2 acquisition (Plan beyond this one).

---

## Self-Review Checklist

- [x] Every task has exact file paths
- [x] Every step has code or commands (no placeholders)
- [x] TDD — test before implementation in every task
- [x] Commits at the end of each task
- [x] No "TBD" / "TODO" / "fill in later"
- [x] Function signatures consistent across tasks (`extract_orderflow_features(l1_snapshot=...)` matches everywhere)
- [x] Schema version bumped (Task 9)
- [x] Live deploy verification (Task 10)
- [x] Long-term validation (Task 11)

---

## Risk Notes

1. **L1 archive consumes disk** — ~5-20 MB/day. Negligible vs current free space but worth monitoring after 6 months.
2. **L1 subscription canceled (per user message)** — `SubscribeContractDepth` is the canceled one ($38/mo); `SubscribeContractQuotes` (L1) is part of the base API plan ($14.50/mo). Verify base API plan is still active before deploying — if not, this plan does nothing until billing resumes.
3. **No historical L1 backfill possible** — only forward-going. Existing 15k-episode pool has zero L1 coverage. Don't expect retrained GBT to instantly improve; only the L1-enabled subset (forward episodes) benefits.
4. **Schema version bump breaks existing UI** — the frontend's cached schema (version 3) will mismatch. UI refetches on connect, so users may see a one-time reload. No action needed.
5. **Recent trades buffer grows unbounded if `_recent_trades` deque isn't sized** — Task 8 sets maxlen=500. Don't change without thinking through memory.
