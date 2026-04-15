# Volume Profile Improvements

**Date:** 2026-04-12
**Scope:** Historical per-day VP, visual polish, live refresh

## Current State

- Backend computes VP via `get_volume_profile_curve(symbol, timeframe)` in `market_service.py`
- Session VP uses tick data (SQL aggregation from `market_trades`), falls back to 1m bars
- Weekly/monthly use 1m bars with TPO-style normalization (`volume=1` per bar)
- Frontend fetches all 3 timeframes once on mount, renders as horizontal histograms on right edge
- No historical VP — today's profile overlays all scrolled-back candles
- Cache TTL: 300s for all timeframes
- Bars: 2px tall, max 80px wide, subtle opacity (6%/20%/60%)

## Changes

### 1. Historical Per-Day VP

**Backend — `market_service.py`:**
- Add `date: str | None = None` param to `get_volume_profile_curve()`
- When `date` provided (format `YYYY-MM-DD`), compute session VP for that specific CET trading day
- Tick path: filter `market_trades WHERE ts BETWEEN day_start AND day_end`
- Bar fallback: filter `market_candles` for that date
- Cache key includes date: `(symbol, "session", "2026-04-11")`
- Weekly/monthly ignore `date` param (composite by nature)

**API — `routes/market.py`:**
- Add `date: str | None = Query(default=None)` to `get_volume_profile` endpoint
- Pass through to service

**Frontend — `CandleChart.tsx`:**
- Track visible CET dates from the candle time range
- Maintain `vpHistoryRef: Map<string, VPData>` keyed by date string
- On scroll (debounced 300ms), detect new visible dates → fetch `getVP('session', date)` for each
- Each day's histogram renders only within that day's time column (x-bounded from day start to day end)
- Today's VP uses the existing global fetch (auto-refreshed)

**Frontend — `useApi.ts`:**
- Update `getVP(tf, date?)` to pass optional `?date=YYYY-MM-DD` query param

### 2. Visual Tweaks

All in `CandleChart.tsx` `drawOverlays()`:

| Property | Before | After |
|----------|--------|-------|
| Bar height | 2px | 3px |
| Max bar width | 80px | 120px |
| Outside VA opacity | 6% | 12% |
| Inside VA opacity | 20% | 35% |
| POC opacity | 60% | 80% |
| POC label | none | "POC" text at right edge of bar |
| VAH/VAL lines | none | Dashed horizontal line + "VAH"/"VAL" label |

POC label: `ctx.fillText("POC", xRight - barW - 28, y + 3)` in the timeframe's color.

VAH/VAL lines: full-width dashed lines at VAH and VAL prices, same color as the VP timeframe, 1px, `[4, 4]` dash. Label at left edge. Only drawn for daily VP (weekly/monthly would be too cluttered).

### 3. Live Refresh

**Backend:**
- Session VP cache TTL: 300s → 30s (always — simplifies logic, session VP is cheap to compute)

**Frontend — `CandleChart.tsx`:**
- Add `useEffect` with 30s `setInterval` that re-fetches session VP for today
- Only active during market hours (check if current CET hour is 15:30–22:00 weekday)
- On fetch, update `vpDataRef` for 'session' key and trigger redraw
- Weekly/monthly stay fetch-once-on-mount (no refresh needed)

## Files Changed

- `backend/src/services/market_service.py` — date param, cache TTL
- `backend/src/api/routes/market.py` — date query param
- `firevstocks/frontend/src/hooks/useApi.ts` — date param on getVP
- `firevstocks/frontend/src/pages/CandleChart.tsx` — historical VP, visuals, live refresh
- `firevstocks/frontend/src/types/index.ts` — VPData type if needed
