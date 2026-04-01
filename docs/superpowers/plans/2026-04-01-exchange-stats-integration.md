# Exchange Statistics Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Databento CME statistics (OI, settlement, cleared/block volume) into the DQN observation vector for both live and backtest, add frontend SSE listener + display, and sync dqnConfig.ts to actual 218-dim observation.

**Architecture:** New 5-dim "exchange stats" segment inserted after macro in the observation vector. Historical statistics fetched from Databento Historical API for backtest parity. Frontend receives stats via existing SSE stream and displays in BookSnapshot.

**Tech Stack:** Python/NumPy (feature extractor), Databento Python SDK (historical fetch), React/TypeScript (frontend)

---

### Task 1: Exchange Stats Feature Extractor

**Files:**
- Create: `backend/src/rl/features/exchange_stats_features.py`
- Test: `backend/tests/test_exchange_stats_features.py`

- [ ] **Step 1: Write the test file**

```python
# backend/tests/test_exchange_stats_features.py
"""Tests for exchange statistics feature extraction."""
import numpy as np
from src.rl.features.exchange_stats_features import extract_exchange_stats_features, _N_FEATURES


def test_none_returns_zeros():
    result = extract_exchange_stats_features(None, price=19000.0)
    assert result.shape == (_N_FEATURES,)
    assert result.dtype == np.float32
    assert (result == 0.0).all()


def test_empty_dict_returns_zeros():
    result = extract_exchange_stats_features({}, price=19000.0)
    assert result.shape == (_N_FEATURES,)
    assert (result == 0.0).all()


def test_full_stats():
    macro = {
        "oi": 250_000,
        "oi_change": 10_000,
        "settlement_price": 19050.0,
        "cleared_volume": 400_000,
        "block_volume": 20_000,
    }
    result = extract_exchange_stats_features(macro, price=19000.0)
    assert result.shape == (_N_FEATURES,)
    # oi_norm = 250000 / 1_000_000 = 0.25
    assert abs(result[0] - 0.25) < 1e-5
    # oi_change_norm = 10000 / 50000 = 0.2
    assert abs(result[1] - 0.2) < 1e-5
    # settlement_dist = (19000 - 19050) / (0.25 * 200) = -50 / 50 = -1.0
    assert abs(result[2] - (-1.0)) < 1e-5
    # cleared_vol_norm = 400000 / 500000 = 0.8
    assert abs(result[3] - 0.8) < 1e-5
    # block_vol_ratio = 20000 / 400000 = 0.05
    assert abs(result[4] - 0.05) < 1e-5


def test_clipping():
    macro = {
        "oi": 2_000_000,  # > 1M → clipped to 1.0
        "oi_change": 100_000,  # > 50k → clipped to 1.0
        "settlement_price": 18000.0,  # 1000pts away → clipped
        "cleared_volume": 1_000_000,  # > 500k → clipped to 1.0
        "block_volume": 900_000,  # ratio > 1 → clipped to 1.0
    }
    result = extract_exchange_stats_features(macro, price=19000.0)
    assert result[0] == 1.0  # oi clipped
    assert result[1] == 1.0  # oi_change clipped
    assert abs(result[2]) == 1.0  # settlement clipped
    assert result[3] == 1.0  # cleared_vol clipped
    assert result[4] == 1.0  # block_vol clipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_exchange_stats_features.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.rl.features.exchange_stats_features'`

- [ ] **Step 3: Write the feature extractor**

```python
# backend/src/rl/features/exchange_stats_features.py
"""CME exchange statistics feature extraction (OI, settlement, volume)."""
from __future__ import annotations

import numpy as np

_N_FEATURES = 5

# NQ tick size
_TICK_SIZE = 0.25


def extract_exchange_stats_features(macro: dict | None, price: float = 0.0) -> np.ndarray:
    """Extract 5 exchange-statistics features from the macro/state dict.

    Feature layout (indices 0-4):
      0  oi_norm          — open_interest / 1M, clipped 0-1
      1  oi_change_norm   — daily OI change / 50k, clipped ±1
      2  settlement_dist  — (price - settlement) / (tick × 200), clipped ±1
      3  cleared_vol_norm — cleared_volume / 500k, clipped 0-1
      4  block_vol_ratio  — block_volume / cleared_volume, clipped 0-1

    Returns zeros(5) if macro is None or keys are missing.
    """
    if not macro:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    oi = float(macro.get("oi", 0))
    oi_change = float(macro.get("oi_change", 0))
    settlement = float(macro.get("settlement_price", 0))
    cleared_vol = float(macro.get("cleared_volume", 0))
    block_vol = float(macro.get("block_volume", 0))

    # Settlement distance in ticks, normalised
    if settlement > 0 and price > 0:
        settlement_dist = (price - settlement) / (_TICK_SIZE * 200)
    else:
        settlement_dist = 0.0

    # Block volume ratio
    block_ratio = block_vol / max(cleared_vol, 1.0) if cleared_vol > 0 else 0.0

    return np.array([
        np.clip(oi / 1_000_000, 0.0, 1.0),
        np.clip(oi_change / 50_000, -1.0, 1.0),
        np.clip(settlement_dist, -1.0, 1.0),
        np.clip(cleared_vol / 500_000, 0.0, 1.0),
        np.clip(block_ratio, 0.0, 1.0),
    ], dtype=np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_exchange_stats_features.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/exchange_stats_features.py backend/tests/test_exchange_stats_features.py
git commit -m "feat(rl): add exchange stats feature extractor (5 dims: OI, settlement, volume)"
```

---

### Task 2: Wire Exchange Stats into Observation Vector

**Files:**
- Modify: `backend/src/rl/features/observation.py:1-10,170-205,224-244`

- [ ] **Step 1: Add import**

In `backend/src/rl/features/observation.py`, add this import after the existing feature imports (around line 10):

```python
from .exchange_stats_features import extract_exchange_stats_features
```

- [ ] **Step 2: Add segment extraction after seg_macro (line 171)**

Insert after line 171 (`seg_macro = extract_macro_features(macro)`):

```python
    # 8.5. Exchange stats (5) — OI, settlement, cleared/block volume
    seg_exchange = extract_exchange_stats_features(macro, price=price)
```

- [ ] **Step 3: Add to concatenation**

In the `np.concatenate()` call (line 191), insert `seg_exchange` after `seg_macro`:

```python
    obs = np.concatenate([
        seg_level,        # len(LevelType) — multi-hot (zone) or one-hot (legacy)
        seg_orderflow,    # 21
        seg_structure,    # 39
        seg_tpo,          # 38
        seg_candles,      # 15
        seg_zone_feats,   # 4 (zone) or 0 (legacy)
        seg_confluence,   # 5 (zone) or 8 (legacy)
        seg_macro,        # 11
        seg_exchange,     # 5
        seg_setup,        # 14
        seg_amt,          # 13
        seg_micro,        # 20
        seg_approach,     # 1
        seg_execution,    # 7
    ])
```

- [ ] **Step 4: Verify OBSERVATION_DIM updated**

Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; print(f'OBSERVATION_DIM = {OBSERVATION_DIM}')"`
Expected: `OBSERVATION_DIM = 218`

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/observation.py
git commit -m "feat(rl): wire exchange stats segment into observation vector (213→218)"
```

---

### Task 3: Historical Statistics Fetcher

**Files:**
- Modify: `backend/src/rl/data/fetcher.py` (add function after `fetch_cot_history`)
- Test: `backend/tests/test_fetch_statistics.py`

- [ ] **Step 1: Write test**

```python
# backend/tests/test_fetch_statistics.py
"""Tests for historical statistics data fetcher."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pathlib import Path


def test_fetch_statistics_history_saves_parquet(tmp_path):
    """Test that fetch_statistics_history creates a valid parquet file."""
    from src.rl.data.fetcher import fetch_statistics_history

    # Mock Databento client
    mock_client = MagicMock()

    # Create fake StatMsg-like records
    class FakeRecord:
        def __init__(self, stat_type_val, quantity, price, ts_ref, ts_event):
            self.stat_type = stat_type_val
            self.quantity = quantity
            self.price = price
            self.ts_ref = ts_ref
            self.ts_event = ts_event
            self.hd = MagicMock(ts_event=ts_event)

    from databento_dbn import StatType

    # Two days of data
    day1_ns = int(datetime(2025, 1, 6, 12, 30, tzinfo=timezone.utc).timestamp() * 1e9)
    day2_ns = int(datetime(2025, 1, 7, 12, 30, tzinfo=timezone.utc).timestamp() * 1e9)
    ref1_ns = int(datetime(2025, 1, 3, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)
    ref2_ns = int(datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)

    records = [
        FakeRecord(StatType.OPEN_INTEREST, 250000, 0, ref1_ns, day1_ns),
        FakeRecord(StatType.CLEARED_VOLUME, 400000, 0, ref1_ns, day1_ns),
        FakeRecord(StatType.BLOCK_VOLUME, 20000, 0, ref1_ns, day1_ns),
        FakeRecord(StatType.SETTLEMENT_PRICE, 0, int(19050.0 * 1e9), ref1_ns, day1_ns),
        FakeRecord(StatType.OPEN_INTEREST, 260000, 0, ref2_ns, day2_ns),
        FakeRecord(StatType.CLEARED_VOLUME, 350000, 0, ref2_ns, day2_ns),
        FakeRecord(StatType.BLOCK_VOLUME, 15000, 0, ref2_ns, day2_ns),
        FakeRecord(StatType.SETTLEMENT_PRICE, 0, int(19100.0 * 1e9), ref2_ns, day2_ns),
    ]
    mock_client.timeseries.get_range.return_value = records

    with patch("src.rl.data.fetcher.db") as mock_db_module:
        mock_db_module.Historical.return_value = mock_client

        result = fetch_statistics_history(
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 1, 31, tzinfo=timezone.utc),
            output_dir=tmp_path,
            api_key="test-key",
        )

    assert result is not None
    assert result.exists()

    import pandas as pd
    df = pd.read_parquet(result)
    assert len(df) == 2
    assert "open_interest" in df.columns
    assert "settlement_price" in df.columns
    assert "oi_change" in df.columns
    assert df["open_interest"].iloc[0] == 250000
    assert df["oi_change"].iloc[1] == 10000  # 260000 - 250000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_fetch_statistics.py -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_statistics_history'`

- [ ] **Step 3: Implement fetch_statistics_history**

Add this function to `backend/src/rl/data/fetcher.py` after the `fetch_cot_history` function (after line 302):

```python
def fetch_statistics_history(
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
    api_key: str | None = None,
) -> "Path | None":
    """Fetch daily CME statistics (OI, settlement, cleared/block volume) from Databento.

    Saves ``statistics_daily.parquet`` with columns:
        date, open_interest, cleared_volume, block_volume, settlement_price, oi_change

    Uses the ``statistics`` schema on GLBX.MDP3, filtering for relevant StatTypes.
    Groups by trading date (from ts_ref).
    """
    try:
        import databento as db
    except ImportError:
        logger.error("databento package not installed — pip install databento")
        return None

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas package not installed — pip install pandas pyarrow")
        return None

    key = api_key or os.environ.get("DATABENTO_API_KEY", "")
    if not key:
        raise ValueError("Databento API key not provided and DATABENTO_API_KEY env var not set")

    out_dir = output_dir or MACRO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "statistics_daily.parquet"

    client = db.Historical(key=key)

    try:
        data = client.timeseries.get_range(
            dataset=_DATASET,
            symbols=[_SYMBOL],
            stype_in="continuous",
            schema="statistics",
            start=start.isoformat(),
            end=end.isoformat(),
        )
    except Exception as exc:
        logger.error("Databento statistics fetch failed: %s", exc)
        return None

    from databento_dbn import StatType

    _QUANTITY_TYPES = {
        StatType.OPEN_INTEREST: "open_interest",
        StatType.CLEARED_VOLUME: "cleared_volume",
        StatType.BLOCK_VOLUME: "block_volume",
    }
    _PRICE_TYPES = {
        StatType.SETTLEMENT_PRICE: "settlement_price",
    }

    # Collect per-date stats
    daily: dict[str, dict] = {}  # date_str -> {col: value}
    for rec in data:
        st = rec.stat_type
        # Use ts_ref for the trading date this stat applies to
        ts_ref = rec.ts_ref if hasattr(rec, "ts_ref") else rec.hd.ts_event
        date_str = datetime.fromtimestamp(int(ts_ref) / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")

        if date_str not in daily:
            daily[date_str] = {}

        if st in _QUANTITY_TYPES:
            daily[date_str][_QUANTITY_TYPES[st]] = rec.quantity
        elif st in _PRICE_TYPES:
            daily[date_str][_PRICE_TYPES[st]] = rec.price / 1e9

    if not daily:
        logger.warning("No statistics data returned for %s – %s", start.date(), end.date())
        return None

    df = pd.DataFrame.from_dict(daily, orient="index")
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    df.sort_index(inplace=True)

    # Fill missing columns with 0
    for col in ("open_interest", "cleared_volume", "block_volume", "settlement_price"):
        if col not in df.columns:
            df[col] = 0

    # Compute day-over-day OI change
    df["oi_change"] = df["open_interest"].diff()

    df.to_parquet(out_path)
    logger.info("Wrote statistics history (%d days) to %s", len(df), out_path.name)
    return out_path
```

Also add at the top of fetcher.py (line 8, with the other imports), ensure `db` is importable at module scope for the mock to work — actually the existing pattern uses `import databento as db` inside each function, so follow that pattern. The mock patches `src.rl.data.fetcher.db` — update the test to patch correctly:

Actually, looking at the existing code, `databento` is imported inside each function. The test mock needs to patch differently. Update the test mock:

Replace the `with patch` block in the test with:

```python
    with patch("databento.Historical", return_value=mock_client):
        result = fetch_statistics_history(
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 1, 31, tzinfo=timezone.utc),
            output_dir=tmp_path,
            api_key="test-key",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_fetch_statistics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/fetcher.py backend/tests/test_fetch_statistics.py
git commit -m "feat(rl): add fetch_statistics_history for backtest OI/settlement/volume"
```

---

### Task 4: Merge Statistics into Backtest Macro Data

**Files:**
- Modify: `backend/src/rl/cli.py:19-67` (`_prepare_macro_data`)
- Modify: `backend/src/rl/cli.py:420-434` (training data loading)

- [ ] **Step 1: Update `_prepare_macro_data` signature and merge logic**

In `backend/src/rl/cli.py`, update the function signature at line 19:

```python
def _prepare_macro_data(macro_df, cot_df=None, stats_df=None) -> dict:
```

Add statistics lookup after the COT lookup block (after line 36), before line 38 (`macro_data: dict = {}`):

```python
    # Build statistics lookup: forward-fill daily stats
    stats_lookup: dict = {}
    if stats_df is not None and not stats_df.empty:
        import pandas as pd
        daily_idx = pd.date_range(stats_df.index.min(), stats_df.index.max(), freq="D")
        stats_daily = stats_df.reindex(daily_idx, method="ffill")
        for date_idx, row in stats_daily.iterrows():
            stats_lookup[str(date_idx)[:10]] = {
                "oi": float(row.get("open_interest", 0)),
                "oi_change": float(row.get("oi_change", 0)),
                "settlement_price": float(row.get("settlement_price", 0)),
                "cleared_volume": float(row.get("cleared_volume", 0)),
                "block_volume": float(row.get("block_volume", 0)),
            }
```

Add defaults to the entry dict (after the news defaults, around line 75):

```python
            # Exchange stats defaults (overwritten if available)
            "oi": 0.0,
            "oi_change": 0.0,
            "settlement_price": 0.0,
            "cleared_volume": 0.0,
            "block_volume": 0.0,
```

Add merge after the COT merge (after line 84):

```python
        # Merge exchange stats if available for this date
        stats = stats_lookup.get(date_str)
        if stats:
            entry.update(stats)
```

- [ ] **Step 2: Update training data loading**

In `backend/src/rl/cli.py`, around line 420-430, after COT loading:

```python
    # Load exchange statistics
    stats_path = MACRO_DIR / "statistics_daily.parquet"
    stats_df = None
    if stats_path.exists():
        try:
            stats_df = pd.read_parquet(stats_path)
            typer.echo(f"Loaded exchange statistics: {len(stats_df)} days.")
        except Exception as exc:
            typer.echo(f"Warning: could not load statistics data: {exc}")
    else:
        typer.echo("No statistics_daily.parquet found — exchange stats features will be zeroed.")
```

Update the `_prepare_macro_data` call at line 428:

```python
            macro_data = _prepare_macro_data(macro_df, cot_df=cot_df, stats_df=stats_df)
```

- [ ] **Step 3: Find and update any other `_prepare_macro_data` call sites**

Search for other calls — there's likely one more around line 863-867 (the eval/replay command). Apply the same pattern: load `stats_df`, pass to `_prepare_macro_data`.

- [ ] **Step 4: Verify import works**

Run: `cd backend && python -c "from src.rl.cli import _prepare_macro_data; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/cli.py
git commit -m "feat(rl): merge exchange statistics into backtest macro data"
```

---

### Task 5: Live Injection — Stream Stats into RL Context

**Files:**
- Modify: `backend/src/api/routes/market.py:139-152`

- [ ] **Step 1: Inject daily_stats into rl_context macro dict**

In `backend/src/api/routes/market.py`, after building `rl_context` (around line 152), add:

```python
        # Enrich macro with live exchange stats from Databento stream
        stream = _get_live_stream(request)
        if stream and rl_context.get("macro") is not None:
            ds = stream.daily_stats
            if ds:
                rl_context["macro"].update({
                    "oi": ds.get("open_interest", {}).get("value", 0),
                    "oi_change": 0,  # No prior-day ref in live — zeroed
                    "settlement_price": ds.get("settlement_price", {}).get("value", 0),
                    "cleared_volume": ds.get("cleared_volume", {}).get("value", 0),
                    "block_volume": ds.get("block_volume", {}).get("value", 0),
                })
        elif stream and rl_context.get("macro") is None:
            ds = stream.daily_stats
            if ds:
                rl_context["macro"] = {
                    "oi": ds.get("open_interest", {}).get("value", 0),
                    "oi_change": 0,
                    "settlement_price": ds.get("settlement_price", {}).get("value", 0),
                    "cleared_volume": ds.get("cleared_volume", {}).get("value", 0),
                    "block_volume": ds.get("block_volume", {}).get("value", 0),
                }
```

- [ ] **Step 2: Verify import works**

Run: `cd backend && python -c "from src.api.routes.market import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/market.py
git commit -m "feat(rl): inject live exchange stats from Databento stream into RL context"
```

---

### Task 6: Frontend — SSE Statistics Listener

**Files:**
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/hooks/useMarketStream.ts`

- [ ] **Step 1: Add StatisticsEvent type**

In `frontend/src/types/market.ts`, after the `StreamBookEvent` interface (around line 199), add:

```typescript
export interface StatisticsEvent {
  type: 'statistics';
  ts: string;
  stat: string;
  open_interest?: number;
  cleared_volume?: number;
  block_volume?: number;
  settlement_price?: number;
  vwap?: number;
  session_high?: number;
  session_low?: number;
  net_change?: number;
}
```

- [ ] **Step 2: Add listener in useMarketStream**

In `frontend/src/hooks/useMarketStream.ts`, add import:

```typescript
import type { StreamTickEvent, StreamBookEvent, CandleData, StatisticsEvent } from '@/types/market';
```

Add state (after line 8, `lastCandle` state):

```typescript
const [statistics, setStatistics] = useState<StatisticsEvent | null>(null);
```

Add event listener (after the `candle` listener, around line 33):

```typescript
    es.addEventListener('statistics', (e) => {
      setStatistics(JSON.parse(e.data));
    });
```

Add `statistics` to the hook's return value.

- [ ] **Step 3: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/hooks/useMarketStream.ts
git commit -m "feat(frontend): add SSE statistics event listener"
```

---

### Task 7: Frontend — Display Statistics in BookSnapshot

**Files:**
- Modify: `frontend/src/components/Terminal/pages/BookSnapshot.tsx`

- [ ] **Step 1: Add statistics to Props**

In `frontend/src/components/Terminal/pages/BookSnapshot.tsx`, add to the import line (line 2):

```typescript
import type { StreamBookEvent, CandleData, ExpandedSession, VPLevel, TPOLiveProfile, SessionTPOResponse, SessionTPOData, TimeframeSwings, StatisticsEvent } from '@/types/market';
```

Add to the `Props` interface (after line 31):

```typescript
  statistics?: StatisticsEvent | null;
```

Add to the destructuring (line 34):

```typescript
export function BookSnapshot({ session, hiddenLevels, setHiddenLevels, tpo: _tpo, sessionTPO, statistics }: Props) {
```

- [ ] **Step 2: Add stats display section**

Add a compact stats row inside the component — find the section where macro data is rendered (search for `macro` group rendering). Add a new section nearby:

```tsx
{/* Exchange Statistics */}
{statistics && !hiddenLevels.has('exchange_stats') && (
  <div className="flex gap-3 text-[10px] text-zinc-400 px-2 py-1 border-t border-zinc-800">
    {statistics.open_interest != null && (
      <span>OI: <span className="text-cyan-400">{(statistics.open_interest / 1000).toFixed(0)}k</span></span>
    )}
    {statistics.settlement_price != null && (
      <span>Sttl: <span className="text-amber-400">{statistics.settlement_price.toFixed(2)}</span></span>
    )}
    {statistics.cleared_volume != null && (
      <span>ClrVol: <span className="text-zinc-300">{(statistics.cleared_volume / 1000).toFixed(0)}k</span></span>
    )}
    {statistics.block_volume != null && (
      <span>BlkVol: <span className="text-zinc-300">{(statistics.block_volume / 1000).toFixed(0)}k</span></span>
    )}
  </div>
)}
```

- [ ] **Step 3: Add LEVEL_GROUPS entry**

Add to the `LEVEL_GROUPS` object (around line 22):

```typescript
  exchange_stats: ['exchange_stats'],
```

- [ ] **Step 4: Pass statistics prop from parent**

Find where `BookSnapshot` is rendered (in ChartPage.tsx or TradingContainer.tsx) and pass the `statistics` prop from `useMarketStream`.

- [ ] **Step 5: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/BookSnapshot.tsx
git commit -m "feat(frontend): display exchange statistics in BookSnapshot"
```

---

### Task 8: Sync dqnConfig.ts to Actual 218-dim Observation

**Files:**
- Rewrite: `frontend/src/components/Terminal/pages/dqnConfig.ts`

- [ ] **Step 1: Rewrite dqnConfig.ts**

Replace the entire content of `frontend/src/components/Terminal/pages/dqnConfig.ts` with:

```typescript
// dqnConfig.ts — maps each of the 218 DQN observation indices to display properties
// Synced with backend/src/rl/features/observation.py build_observation() segments

export interface DQNInputDef {
  index: number;
  label: string;
  segment: string;
}

export interface DQNSegment {
  name: string;
  color: string;
  start: number;
  end: number;  // exclusive
}

export const DQN_SEGMENTS: DQNSegment[] = [
  { name: 'LEVEL TYPE',     color: '#06b6d4', start: 0,   end: 25  },
  { name: 'ORDERFLOW',      color: '#10b981', start: 25,  end: 46  },
  { name: 'STRUCTURE',      color: '#8b5cf6', start: 46,  end: 85  },
  { name: 'TPO',            color: '#f59e0b', start: 85,  end: 123 },
  { name: 'CANDLES',        color: '#ec4899', start: 123, end: 138 },
  { name: 'ZONE',           color: '#a3e635', start: 138, end: 142 },
  { name: 'CONFLUENCE',     color: '#14b8a6', start: 142, end: 147 },
  { name: 'MACRO',          color: '#ef4444', start: 147, end: 158 },
  { name: 'EXCHANGE STATS', color: '#38bdf8', start: 158, end: 163 },
  { name: 'SETUP',          color: '#f97316', start: 163, end: 177 },
  { name: 'AMT',            color: '#a78bfa', start: 177, end: 190 },
  { name: 'MICRO',          color: '#22d3ee', start: 190, end: 210 },
  { name: 'APPROACH',       color: '#94a3b8', start: 210, end: 211 },
  { name: 'EXECUTION',      color: '#fb923c', start: 211, end: 218 },
];

// Level type names (indices 0-24) — matches LevelType enum in rl/config.py
const LEVEL_TYPES = [
  'daily_poc', 'daily_vah', 'daily_val',
  'weekly_poc', 'weekly_vah', 'weekly_val',
  'monthly_poc', 'monthly_vah', 'monthly_val',
  'vwap', 'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
  'pdh', 'pdl', 'tokyo_high', 'tokyo_low', 'nyib_high', 'nyib_low',
  'tpoc', 'tvah', 'tval', 'tibh', 'tibl',
  'naked_poc',
];

// Orderflow feature names (indices 25-45)
const ORDERFLOW = [
  'delta_pct', 'delta_norm', 'cvd_norm', 'cvd_trend',
  'vol_ratio', 'body_ratio', 'spread_ticks', 'pa_ratio',
  'imbal_max', 'stacked_cnt', 'stacked_dir',
  'big_cnt', 'big_net_delta', 'absorption', 'stop_run',
  'delta_accel', 'cvd_divergence', 'vol_trend', 'pa_trend', 'imbal_trend', 'time_weight',
];

// Structure feature names (indices 46-84)
const STRUCTURE = [
  'vwap_sd', 'in_va', 'poc_dist', 'vah_dist', 'val_dist', 'single_prints',
  'ib_range', 'poor_high', 'poor_low',
  'mkt_trend', 'mkt_range', 'mkt_neutral',
  'min_since_rth', 'sess_vol%', 'daily_range%', 'tod_sin', 'tod_cos',
  'sess_rth', 'sess_globex', 'sess_london',
  'ib_break_up', 'ib_break_dn', 'ib_intact',
  'swing_trend_d', 'swing_trend_w', 'swing_trend_m',
  'swing_dist_d', 'swing_dist_w', 'swing_dist_m',
  'swing_pos_d', 'swing_pos_w', 'swing_pos_m',
  'bos_d', 'bos_w', 'bos_m',
  'choch_d', 'choch_w', 'choch_m',
  'pdh_dist', 'pdl_dist', 'pdh_pdl_pos',
];

// TPO per-session features (indices 85-122) — 3 sessions × ~12 features + global
const TPO = [
  // Tokyo session
  'tky_poc_dist', 'tky_vah_dist', 'tky_val_dist', 'tky_in_va',
  'tky_shape_p', 'tky_shape_b', 'tky_shape_d',
  'tky_ib_range', 'tky_rotation', 'tky_opening_type',
  'tky_poc_migration', 'tky_excess',
  // London session
  'ldn_poc_dist', 'ldn_vah_dist', 'ldn_val_dist', 'ldn_in_va',
  'ldn_shape_p', 'ldn_shape_b', 'ldn_shape_d',
  'ldn_ib_range', 'ldn_rotation', 'ldn_opening_type',
  'ldn_poc_migration', 'ldn_excess',
  // NY session
  'ny_poc_dist', 'ny_vah_dist', 'ny_val_dist', 'ny_in_va',
  'ny_shape_p', 'ny_shape_b', 'ny_shape_d',
  'ny_ib_range', 'ny_rotation', 'ny_opening_type',
  'ny_poc_migration', 'ny_excess',
  // Global TPO
  'global_rotation', 'global_poc_migration',
];

// Candle window feature names (indices 123-137) — 5 candles × 3 features
const CANDLES = [
  'c1_delta', 'c1_vol', 'c1_body',
  'c2_delta', 'c2_vol', 'c2_body',
  'c3_delta', 'c3_vol', 'c3_body',
  'c4_delta', 'c4_vol', 'c4_body',
  'c5_delta', 'c5_vol', 'c5_body',
];

// Zone features (indices 138-141)
const ZONE = [
  'zone_width', 'zone_members', 'zone_hierarchy', 'zone_session_age',
];

// Confluence feature names (indices 142-146)
const CONFLUENCE = [
  'levels_near', 'cluster_score', 'dist_higher', 'dist_lower', 'hierarchy',
];

// Macro feature names (indices 147-157)
const MACRO = [
  'vix', 'vix_chg', 'regime', 'dxy_chg',
  'us10y_chg', 'us2y_chg', 'yield_curve',
  'cot_net', 'cot_chg', 'news_prox', 'news_imp',
];

// Exchange stats feature names (indices 158-162)
const EXCHANGE_STATS = [
  'oi_norm', 'oi_change', 'settlement_dist', 'cleared_vol', 'block_ratio',
];

// Setup detection feature names (indices 163-176)
const SETUP = [
  'poor_extr', 'ib_break', 'spring', 'sfp',
  'rule80', 'fakeout', 'brk_balance', 'dbl_dist',
  'news_dir', 'absorption', 'vwap_sd2', 'gap_logic', 'pbd', 'rsv_setup',
];

// AMT features (indices 177-189)
const AMT = [
  'day_trend', 'day_normal', 'day_neutral', 'day_range',
  'open_drive', 'open_test', 'open_reject', 'open_auction',
  'range_ext_up', 'range_ext_dn',
  'va_overlap', 'value_migration', 'globex_hl_ratio',
];

// Micro feature names (indices 190-209)
const MICRO = [
  'approach_vel', 'approach_accel', 'net_delta', 'delta_trend',
  'max_trade', 'big_trade%', 'buy_vol%', 'tick_spread',
  'consec_dir', 'reversal_cnt', 'time_compress', 'last5_vel',
  'last5_delta', 'bid_aggress', 'touch_size', 'linearity',
  'vol_surge', 'rsv_0', 'rsv_1', 'rsv_2',
];

// Approach direction (index 210)
const APPROACH = ['approach_dir'];

// Execution context (indices 211-217)
const EXECUTION = [
  'auction_quality', 'ib_time_pct', 'time_at_level', 'retest_count',
  'prior_touch_result', 'session_momentum', 'tick_velocity',
];

// Build the full 218-element array
export const DQN_INPUTS: DQNInputDef[] = [
  ...LEVEL_TYPES.map((label, i) => ({ index: i, label, segment: 'LEVEL TYPE' })),
  ...ORDERFLOW.map((label, i) => ({ index: 25 + i, label, segment: 'ORDERFLOW' })),
  ...STRUCTURE.map((label, i) => ({ index: 46 + i, label, segment: 'STRUCTURE' })),
  ...TPO.map((label, i) => ({ index: 85 + i, label, segment: 'TPO' })),
  ...CANDLES.map((label, i) => ({ index: 123 + i, label, segment: 'CANDLES' })),
  ...ZONE.map((label, i) => ({ index: 138 + i, label, segment: 'ZONE' })),
  ...CONFLUENCE.map((label, i) => ({ index: 142 + i, label, segment: 'CONFLUENCE' })),
  ...MACRO.map((label, i) => ({ index: 147 + i, label, segment: 'MACRO' })),
  ...EXCHANGE_STATS.map((label, i) => ({ index: 158 + i, label, segment: 'EXCHANGE STATS' })),
  ...SETUP.map((label, i) => ({ index: 163 + i, label, segment: 'SETUP' })),
  ...AMT.map((label, i) => ({ index: 177 + i, label, segment: 'AMT' })),
  ...MICRO.map((label, i) => ({ index: 190 + i, label, segment: 'MICRO' })),
  ...APPROACH.map((label, i) => ({ index: 210 + i, label, segment: 'APPROACH' })),
  ...EXECUTION.map((label, i) => ({ index: 211 + i, label, segment: 'EXECUTION' })),
];

/** Get segment color for a given segment name */
export function getSegmentColor(segmentName: string): string {
  return DQN_SEGMENTS.find(s => s.name === segmentName)?.color ?? '#52525b';
}

/** Hidden layer sizes (real Dueling DQN architecture: 256→256→128→64) */
export const HIDDEN_LAYERS = [256, 256, 128, 64] as const;
export const NUM_ACTIONS = 3;
export const ACTION_NAMES = ['CONT', 'REV', 'SKIP'] as const;
export const ACTION_COLORS = ['#10b981', '#ef4444', '#52525b'] as const;
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/dqnConfig.ts
git commit -m "fix(frontend): sync dqnConfig.ts to actual 218-dim observation vector"
```

---

### Task 9: Add `fetch-stats` CLI Command

**Files:**
- Modify: `backend/src/rl/cli.py` (add CLI command)

- [ ] **Step 1: Add fetch-stats command**

In `backend/src/rl/cli.py`, find the existing `fetch` command (that fetches ticks + macro + COT). Add a call to `fetch_statistics_history` alongside the existing fetchers:

```python
    # Fetch exchange statistics
    from src.rl.data.fetcher import fetch_statistics_history
    typer.echo("Fetching exchange statistics from Databento...")
    stats_path = fetch_statistics_history(start_dt, end_dt)
    if stats_path:
        typer.echo(f"Wrote statistics to {stats_path}")
    else:
        typer.echo("Warning: statistics fetch returned no data")
```

- [ ] **Step 2: Verify CLI loads**

Run: `cd backend && python -m src.rl.cli --help`
Expected: No import errors

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/cli.py
git commit -m "feat(rl): add exchange statistics to fetch CLI command"
```

---

### Task 10: Verify End-to-End

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && python -m pytest tests/test_exchange_stats_features.py tests/test_fetch_statistics.py -v`
Expected: All PASS

- [ ] **Step 2: Verify observation dim**

Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; assert OBSERVATION_DIM == 218, f'Expected 218, got {OBSERVATION_DIM}'; print('OBSERVATION_DIM = 218 ✓')"`
Expected: `OBSERVATION_DIM = 218 ✓`

- [ ] **Step 3: Verify frontend builds**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Verify dqnConfig segment count matches**

Run: `cd frontend && node -e "const c = require('./src/components/Terminal/pages/dqnConfig.ts'); console.log(c.DQN_INPUTS.length)"`
Or verify manually: sum of all segment arrays = 25+21+39+38+15+4+5+11+5+14+13+20+1+7 = 218

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: exchange statistics integration — end-to-end verification"
```
