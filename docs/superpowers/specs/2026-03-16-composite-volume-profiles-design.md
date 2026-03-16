# Composite Volume Profile Levels (Daily / Weekly / Monthly)

**Date:** 2026-03-16
**Status:** Draft

## Problem

The level monitor shows only daily session VP levels (POC, VAH, VAL). Traders need multi-timeframe composite volume profile context — knowing where price sits relative to the weekly and monthly value areas is critical for determining whether a move is a rotation within balance or a breakout. This aligns with standard Market Profile / TPO composite periods (Daily, Weekly, Monthly).

## Design

### New Levels

Add 6 new monitored levels computed from composite volume profiles:

| Level | `level_type` | `session` | Category | Direction |
|-------|-------------|-----------|----------|-----------|
| Weekly POC | `weekly_poc` | `weekly` | `session` | `None` |
| Weekly VAH | `weekly_vah` | `weekly` | `session` | `resistance` |
| Weekly VAL | `weekly_val` | `weekly` | `session` | `support` |
| Monthly POC | `monthly_poc` | `monthly` | `session` | `None` |
| Monthly VAH | `monthly_vah` | `monthly` | `session` | `resistance` |
| Monthly VAL | `monthly_val` | `monthly` | `session` | `support` |

Daily POC/VAH/VAL already exist as `poc`, `vah`, `val` with `session=rth`.

### Data Fetching

**Weekly bars:** Reuse existing `_fetch_weekly_bars()` — iterates day-by-day (Mon→today), fetching RTH 1-min bars per day. Each day cached independently by `CachedMarketDataProvider`.

**Monthly bars:** New `_fetch_monthly_bars()` method — same day-by-day iteration pattern as `_fetch_weekly_bars()`, fetching RTH 1-hour bars per day from the 1st of current month through today. Day-by-day iteration is required because the cache keys by `start.date()` — a single month-spanning call would cache stale data permanently.

Both use the existing `compute_volume_profile()` function from `levels.py`, converting bars to `{price, size}` trade dicts via close price × volume.

**Minimum data threshold:** Skip weekly VP if < 2 trading days of data. Skip monthly VP if < 5 trading days. On those early days the composite would just duplicate the daily session VP.

### Backend Changes

#### 1. `MarketService._fetch_monthly_bars()` (new method)

Day-by-day iteration (same pattern as `_fetch_weekly_bars()`) to ensure correct caching:

```python
async def _fetch_monthly_bars(self, symbol: str) -> list[dict]:
    """Fetch 1-hour RTH bars for current calendar month (day-by-day for cache correctness)."""
    today = date.today()
    first_of_month = today.replace(day=1)
    provider = _get_provider()
    config = get_market_data_config()
    full_symbol = config.get("symbol", "NQ.FUT")
    sessions_cfg = config.get("sessions", {})
    bars_all = []
    current = first_of_month
    while current <= today:
        dt = datetime.combine(current, datetime.strptime(sessions_cfg.get("rth_open", "09:30"), "%H:%M").time())
        dt_close = datetime.combine(current, datetime.strptime(sessions_cfg.get("rth_close", "16:00"), "%H:%M").time())
        day_bars = await provider.get_bars(full_symbol, "1h", dt, dt_close)
        if day_bars:
            bars_all.extend([{"high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in day_bars])
        current += timedelta(days=1)
    return bars_all
```

#### 2. `MarketService.compute_session()` — add composite VP computation

After computing daily VP and session levels, compute weekly and monthly VPs. Pass them to `_session_levels_to_rows()`:

```python
# Weekly composite VP (min 2 trading days)
weekly_bars = await self._fetch_weekly_bars(symbol)
weekly_vp = None
if weekly_bars and len(weekly_bars) >= 780:  # ~2 days of 1-min bars
    wb_trades = [{"price": b["close"], "size": b.get("volume", 1)} for b in weekly_bars]
    weekly_vp = compute_volume_profile(wb_trades)

# Monthly composite VP (min 5 trading days)
monthly_bars = await self._fetch_monthly_bars(symbol)
monthly_vp = None
if monthly_bars and len(monthly_bars) >= 35:  # ~5 days of 1-hour bars
    mb_trades = [{"price": b["close"], "size": b.get("volume", 1)} for b in monthly_bars]
    monthly_vp = compute_volume_profile(mb_trades)

# Pass composite VPs to level row builder
level_rows = self._session_levels_to_rows(session_levels, session_data, weekly_vp, monthly_vp)
```

Note: The existing call `level_rows = self._session_levels_to_rows(session_levels, session_data)` on line 208 gets updated to pass the new args.

#### 3. `MarketService._session_levels_to_rows()` — add composite levels

Accept optional `weekly_vp` and `monthly_vp` parameters:

```python
@staticmethod
def _session_levels_to_rows(
    levels: SessionLevels,
    session_data: dict,
    weekly_vp: VolumeProfile | None = None,
    monthly_vp: VolumeProfile | None = None,
) -> list[dict]:
    # ... existing daily levels ...

    # Weekly composite VP levels
    if weekly_vp and weekly_vp.poc:
        _add("weekly_poc", weekly_vp.poc, None, "weekly")
        _add("weekly_vah", weekly_vp.vah, "resistance", "weekly")
        _add("weekly_val", weekly_vp.val, "support", "weekly")

    # Monthly composite VP levels
    if monthly_vp and monthly_vp.poc:
        _add("monthly_poc", monthly_vp.poc, None, "monthly")
        _add("monthly_vah", monthly_vp.vah, "resistance", "monthly")
        _add("monthly_val", monthly_vp.val, "support", "monthly")

    return rows
```

#### 4. `MarketService.build_expanded_session()` — add monthly profile

The `profiles` dict already computes `profiles["weekly"]` (line 277-282). Add the same for monthly:

```python
# Monthly composite (reuse _fetch_monthly_bars)
monthly_bars = await self._fetch_monthly_bars(symbol)
if monthly_bars and len(monthly_bars) >= 35:
    mb_trades = [{"price": b["close"], "size": b.get("volume", 1)} for b in monthly_bars]
    monthly_vp = compute_volume_profile(mb_trades)
    profiles["monthly"] = {"poc": monthly_vp.poc, "vah": monthly_vp.vah, "val": monthly_vp.val}
```

This ensures the REST API `/api/trading/market/session` response includes the monthly VP in the `profiles` dict alongside the existing weekly VP. Both `compute_session()` (persists levels to DB) and `build_expanded_session()` (serves REST API) now have consistent monthly VP data.

#### 5. `LevelMonitor._categorize()` — no change needed

Weekly/monthly VP levels have names like `weekly_poc`, `monthly_val`. The existing `_categorize()` falls through to `return "session"` for these, which is correct — they're session-type reference levels.

### Frontend Changes

**None.** The `LevelMonitorTable` renders all levels from the SSE stream. New levels appear automatically with their `level_type` as the name column and correct distance calculation.

### Data Cost

| Composite | Schema | Records/fetch | API calls | Frequency |
|-----------|--------|--------------|-----------|-----------|
| Weekly | `ohlcv-1m` | ~1,950 (5 days × 390 bars) | 5 (day-by-day) | On compute_session |
| Monthly | `ohlcv-1h` | ~154 (22 days × 7 bars) | 22 (day-by-day) | On compute_session |

All fetches go through `CachedMarketDataProvider` — completed days are cached permanently, only the current day refetches. The day-by-day iteration adds API calls but ensures cache correctness (cache keys by `start.date()`).

### TPO Alignment

These composite periods (Daily / Weekly / Monthly) align exactly with standard Market Profile TPO composite periods. When TPO composites are added later, they'll use the same time boundaries, making it trivial to cross-reference VP value area vs TPO value area on each timeframe.

## Files Modified

| File | Change |
|------|--------|
| `backend/src/services/market_service.py` | Add `_fetch_monthly_bars()`, compute weekly/monthly VP in `compute_session()`, pass to `_session_levels_to_rows()` |
| No other files | `compute_volume_profile()`, `LevelMonitor`, frontend all work as-is |

## Out of Scope

- TPO composite profiles (next phase)
- Context strip / price position vs composite VA (user can check chart)
- Swing-based "leg" or "macro cycle" composites
- Volume profile visualization on chart
