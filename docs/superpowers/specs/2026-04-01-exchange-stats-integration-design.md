# Exchange Statistics Integration Design

**Date:** 2026-04-01  
**Status:** Approved  
**Scope:** Wire Databento CME statistics schema into DQN observation vector, backtest replay, and frontend visualization. Sync dqnConfig.ts to actual 218-dim observation.

---

## Problem

The Databento live stream now subscribes to the `statistics` schema (trades + mbp-1 + statistics), collecting daily CME-published data: open interest, cleared volume, block volume, settlement price, VWAP, session high/low. However:

1. **Live DQN**: Statistics are collected in `stream._daily_stats` but never reach the observation vector
2. **Backtest/Replay**: No historical statistics data exists — the replay engine can't train on features it doesn't have
3. **Frontend**: The `statistics` SSE event is published but no listener exists — silently dropped
4. **DQN config drift**: Frontend `dqnConfig.ts` shows 160 inputs; actual backend `OBSERVATION_DIM` is 213 (zone mode)

## Solution

### A. New "Exchange Stats" observation segment (5 dims)

Dedicated segment between macro (11) and setup (14):

| Index | Name | Formula | Source | Normalization |
|-------|------|---------|--------|---------------|
| 0 | `oi_norm` | open_interest / 1,000,000 | `StatType.OPEN_INTEREST` quantity | clip 0-1 |
| 1 | `oi_change_norm` | (today_oi - prev_oi) / 50,000 | Derived from consecutive OI | clip ±1 |
| 2 | `settlement_dist` | (price - settlement) / (tick_size × 200) | `StatType.SETTLEMENT_PRICE` price | clip ±1 |
| 3 | `cleared_vol_norm` | cleared_volume / 500,000 | `StatType.CLEARED_VOLUME` quantity | clip 0-1 |
| 4 | `block_vol_ratio` | block_volume / max(cleared_volume, 1) | Both quantity fields | clip 0-1 |

- Observation dim: 213 → 218 (zone mode), 212 → 217 (legacy mode)
- Fallback: all zeros when stats unavailable (same pattern as macro)
- Requires model retrain (weight shapes change)

### B. Historical statistics fetch for backtest parity

New `fetch_statistics_history()` in `rl/data/fetcher.py`:

- Databento Historical API, `schema="statistics"`, `GLBX.MDP3`
- Filters: `OPEN_INTEREST`, `CLEARED_VOLUME`, `BLOCK_VOLUME`, `SETTLEMENT_PRICE`
- Groups by trading date (from `ts_ref`)
- Saves to `data/macro/statistics_daily.parquet`

Columns: `date | open_interest | cleared_volume | block_volume | settlement_price | oi_change`

Injection:
- `_prepare_macro_data()` in `cli.py` merges statistics alongside macro + COT
- Keys added to daily macro dict: `oi`, `oi_change`, `settlement_price`, `cleared_volume`, `block_volume`
- Forward-fill gaps (same as COT weekly → daily)
- `replay_engine._build_state()` passes enriched macro dict — no structural changes

### C. Live injection path

- `/compute` route in `market.py` builds `rl_context` — inject `stream.daily_stats` into macro dict
- `level_monitor._build_rl_state()` / `_build_rl_state_zone()` already pass macro through to observation
- New `extract_exchange_stats_features()` reads from state dict, returns `np.ndarray(5)`

### D. Frontend: SSE listener + display

Add `statistics` event listener in `useMarketStream.ts`:

```typescript
interface StatisticsEvent {
  type: 'statistics';
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

- New state `statistics` exposed from hook
- Display in `BookSnapshot.tsx` as compact stats row (OI, settlement, cleared vol)

### E. Sync dqnConfig.ts to actual 218-dim observation

Full rewrite to match real `observation.py` segments:

| Segment | Dims | Start | End |
|---------|------|-------|-----|
| LEVEL TYPE | 25 | 0 | 25 |
| ORDERFLOW | 21 | 25 | 46 |
| STRUCTURE | 39 | 46 | 85 |
| TPO | 38 | 85 | 123 |
| CANDLES | 15 | 123 | 138 |
| ZONE | 4 | 138 | 142 |
| CONFLUENCE | 5 | 142 | 147 |
| MACRO | 11 | 147 | 158 |
| EXCHANGE STATS | 5 | 158 | 163 |
| SETUP | 14 | 163 | 177 |
| AMT | 13 | 177 | 190 |
| MICRO | 20 | 190 | 210 |
| APPROACH | 1 | 210 | 211 |
| EXECUTION | 7 | 211 | 218 |

Feature labels derived from actual Python extraction code. Neural net visualization adapts automatically.

## Files Changed

### Backend — new files
- `backend/src/rl/features/exchange_stats_features.py` — 5-dim feature extractor

### Backend — modified files
- `backend/src/rl/features/observation.py` — add segment 8.5 (exchange stats)
- `backend/src/rl/data/fetcher.py` — add `fetch_statistics_history()`
- `backend/src/rl/cli.py` — merge statistics into `_prepare_macro_data()`
- `backend/src/api/routes/market.py` — inject `stream.daily_stats` into rl_context
- `backend/src/market_data/level_monitor.py` — pass exchange stats in state dict

### Frontend — modified files
- `frontend/src/hooks/useMarketStream.ts` — add `statistics` event listener
- `frontend/src/types/market.ts` — add `StatisticsEvent` type
- `frontend/src/components/Terminal/pages/BookSnapshot.tsx` — display stats row
- `frontend/src/components/Terminal/pages/dqnConfig.ts` — full rewrite to 218-dim

## Notes

- Model retrain required after observation dim change (213 → 218)
- Databento statistics subscription already added to `stream.py` (no additional cost)
- Historical statistics fetch uses Databento Historical API (costs against existing plan quota)
- CME publishes OI/settlement once daily (~7:30 AM CT) — not real-time
- VWAP and session_high/low from statistics are intraday but we only use OI/settlement/cleared/block in the observation (VWAP computed from ticks, session H/L from candles)
