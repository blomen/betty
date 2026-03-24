# Session Levels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute session levels (PDH/PDL, IBH/IBL, Tokyo H/L, London H/L) from 1m candles in the DB for multiple days, serve via API, and draw them as time-scoped horizontal lines on the chart canvas overlay.

**Architecture:** New service method computes session levels on-the-fly from `market_candles` table using existing `compute_session_levels()`. New API endpoint returns multi-day levels with CET time boundaries. Frontend draws them as time-scoped horizontal lines on the canvas overlay (same pattern as session boxes), replacing the current infinite price lines.

**Tech Stack:** Python/FastAPI (backend), lightweight-charts + Canvas API (frontend), existing `compute_session_levels()` from `levels.py`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/src/services/market_service.py` | Modify | Add `get_session_levels()` method |
| `backend/src/api/routes/market.py` | Modify | Add `GET /session-levels` endpoint |
| `frontend/src/services/api.ts` | Modify | Add `getSessionLevels()` API client |
| `frontend/src/types/market.ts` | Modify | Add `SessionLevelDay` type |
| `frontend/src/components/Terminal/pages/CandleChart.tsx` | Modify | Draw time-scoped level lines on canvas, remove infinite price lines for session levels |
| `backend/tests/test_session_levels_api.py` | Create | Test the new endpoint |

---

### Task 1: Backend — `get_session_levels()` service method

**Files:**
- Modify: `backend/src/services/market_service.py`

This method queries 1m candles from DB for the requested number of days, runs `compute_session_levels()` per day, and returns structured results with CET time boundaries for each level.

- [ ] **Step 1: Add `get_session_levels()` to MarketService**

Add this method after `get_developing_vwap()` (~line 1185):

```python
async def get_session_levels(self, symbol: str = "NQ", days: int = 5) -> dict:
    """Compute session levels (PDH/PDL, IB, Tokyo, London) from 1m candles for multiple days.

    Returns per-day levels with CET epoch boundaries so the frontend can draw
    time-scoped horizontal lines. Computes on-the-fly from market_candles —
    same logic used by RL backtesting.
    """
    from zoneinfo import ZoneInfo
    _CET = ZoneInfo("Europe/Stockholm")

    now = datetime.now(timezone.utc)
    today_cet = now.astimezone(_CET).date()

    # Fetch enough 1m candles to cover `days` trading days + 1 extra for PDH/PDL
    pad_days = days + (days // 5) * 2 + 3  # pad for weekends
    start_dt = datetime(
        today_cet.year, today_cet.month, today_cet.day,
        tzinfo=_CET,
    ) - timedelta(days=pad_days)
    start_utc = start_dt.astimezone(timezone.utc)

    rows = self._filter_halt(self.repo.get_candles(symbol, "1m", start_utc, now))
    if not rows:
        return {"days": [], "symbol": symbol}

    # Convert DB rows to bar dicts for compute_session_levels()
    bars = [
        {"ts": r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc), "high": r.h, "low": r.l}
        for r in rows
    ]

    # Group bars by CET date
    from collections import defaultdict
    bars_by_date: dict[str, list[dict]] = defaultdict(list)
    for b in bars:
        cet_date = b["ts"].astimezone(_CET).date().isoformat()
        bars_by_date[cet_date].append(b)

    # Get sorted dates (most recent first), limit to requested days
    sorted_dates = sorted(bars_by_date.keys(), reverse=True)[:days]

    result_days = []
    for date_str in sorted_dates:
        # Include previous day's bars for PDH/PDL computation
        prev_date = (datetime.strptime(date_str, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
        all_bars = bars_by_date.get(prev_date, []) + bars_by_date[date_str]

        dt_parsed = datetime.strptime(date_str, "%Y-%m-%d")
        from zoneinfo import ZoneInfo as _ZI
        session_date = dt_parsed.replace(hour=12, tzinfo=_ZI("US/Eastern"))
        sl = compute_session_levels(all_bars, session_date)

        # CET epoch boundaries for frontend time-scoping
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        def _cet_epoch(d, h, m):
            return int(datetime(d.year, d.month, d.day, h, m, tzinfo=_CET).timestamp())

        result_days.append({
            "date": date_str,
            "pdh": sl.pdh,
            "pdl": sl.pdl,
            "ib_high": sl.ib_high,
            "ib_low": sl.ib_low,
            "tokyo_high": sl.tokyo_high,
            "tokyo_low": sl.tokyo_low,
            "london_high": sl.london_high,
            "london_low": sl.london_low,
            # Time boundaries (CET epochs) for drawing scoped lines
            "tokyo_start": _cet_epoch(d, 0, 0),
            "tokyo_end": _cet_epoch(d, 8, 0),
            "london_start": _cet_epoch(d, 8, 0),
            "london_end": _cet_epoch(d, 15, 30),
            "ib_start": _cet_epoch(d, 15, 30),
            "ib_end": _cet_epoch(d, 16, 30),
            "ny_start": _cet_epoch(d, 15, 30),
            "ny_end": _cet_epoch(d, 22, 0),
            "day_start": _cet_epoch(d, 0, 0),
            "day_end": _cet_epoch(d, 22, 0),
        })

    return {"days": result_days, "symbol": symbol}
```

- [ ] **Step 2: Verify import of `compute_session_levels` already exists**

Check line 15 of `market_service.py` — it already imports `compute_session_levels` from `levels.py`. No new imports needed.

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "feat: add get_session_levels() for multi-day session level computation"
```

---

### Task 2: Backend — API endpoint

**Files:**
- Modify: `backend/src/api/routes/market.py`

- [ ] **Step 1: Add the endpoint**

Add after the `/volume-profile` endpoint (~line 309):

```python
@router.get("/session-levels")
async def get_session_levels(
    symbol: str = Query(default="NQ"),
    days: int = Query(default=5, ge=1, le=30),
    svc: MarketService = Depends(_svc),
):
    """Return per-day session levels (PDH/PDL, IB, Tokyo, London) with time boundaries."""
    return await svc.get_session_levels(symbol, days)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/api/routes/market.py
git commit -m "feat: add GET /session-levels endpoint"
```

---

### Task 3: Frontend — TypeScript types and API client

**Files:**
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add `SessionLevelDay` type**

Add after the `CandlesResponse` interface (~line 329) in `market.ts`:

```typescript
/** Per-day session levels with CET epoch boundaries for time-scoped chart drawing */
export interface SessionLevelDay {
  date: string;
  pdh: number | null;
  pdl: number | null;
  ib_high: number | null;
  ib_low: number | null;
  tokyo_high: number | null;
  tokyo_low: number | null;
  london_high: number | null;
  london_low: number | null;
  // CET epoch boundaries for time-scoping on chart
  tokyo_start: number;
  tokyo_end: number;
  london_start: number;
  london_end: number;
  ib_start: number;
  ib_end: number;
  ny_start: number;
  ny_end: number;
  day_start: number;
  day_end: number;
}

export interface SessionLevelsResponse {
  days: SessionLevelDay[];
  symbol: string;
}
```

- [ ] **Step 2: Add API client method**

Add after `getVolumeProfile()` in `api.ts`:

```typescript
async getSessionLevels(symbol = 'NQ', days = 5): Promise<import('@/types/market').SessionLevelsResponse> {
  return fetchJson(`/trading/market/session-levels?symbol=${symbol}&days=${days}`);
},
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/services/api.ts
git commit -m "feat: add SessionLevelDay type and getSessionLevels API client"
```

---

### Task 4: Frontend — Draw session levels on chart canvas

**Files:**
- Modify: `frontend/src/components/Terminal/pages/CandleChart.tsx`

This is the core visual change. Session levels become time-scoped horizontal lines drawn on the canvas overlay (like session boxes), replacing the infinite price lines currently at lines 540-554.

- [ ] **Step 1: Add session level data ref and fetch**

Add after the `vpDataRef` / `vpLoaded` state (~line 164):

```typescript
// Session levels overlay data (per-day PDH/PDL, IB, Tokyo, London)
const sessionLevelsRef = useRef<import('@/types/market').SessionLevelDay[]>([]);
const [slLoaded, setSlLoaded] = useState(false);
```

Add a new useEffect after the VP fetch effect (~line 394) to fetch session levels:

```typescript
// Fetch session levels for multi-day overlay
useEffect(() => {
  let cancelled = false;
  api.getSessionLevels('NQ', INITIAL_DAYS + 2).then(res => {
    if (!cancelled && res.days?.length) {
      sessionLevelsRef.current = res.days;
      setSlLoaded(true);
      drawOverlays();
    }
  }).catch(() => { /* skip if not available */ });
  return () => { cancelled = true; };
}, [session, drawOverlays]);
```

- [ ] **Step 2: Add session level drawing to `drawOverlays()`**

Inside the `drawOverlays` callback, after the VP histogram drawing block (after line 258, before the closing `}, [])`), add:

```typescript
// --- Session level lines (time-scoped horizontal lines) ---
const slDays = sessionLevelsRef.current;
const slHidden = hiddenRef.current;

type LevelDef = {
  key: string;
  field: 'pdh' | 'pdl' | 'ib_high' | 'ib_low' | 'tokyo_high' | 'tokyo_low' | 'london_high' | 'london_low';
  label: string;
  color: string;
  dash: number[];
  startField: 'day_start' | 'ib_end' | 'tokyo_end' | 'london_end';
  endField: 'day_end';
};

const levelDefs: LevelDef[] = [
  // PDH/PDL: visible from day start to day end (prior day reference)
  { key: 'pdh', field: 'pdh', label: 'PDH', color: '#FB923C', dash: [6, 3], startField: 'day_start', endField: 'day_end' },
  { key: 'pdl', field: 'pdl', label: 'PDL', color: '#FB923C', dash: [6, 3], startField: 'day_start', endField: 'day_end' },
  // IB: visible from IB end (16:30 CET) to day end
  { key: 'ibh', field: 'ib_high', label: 'IBH', color: '#F59E0B', dash: [3, 3], startField: 'ib_end', endField: 'day_end' },
  { key: 'ibl', field: 'ib_low', label: 'IBL', color: '#F59E0B', dash: [3, 3], startField: 'ib_end', endField: 'day_end' },
  // Tokyo: visible from Tokyo end (08:00 CET) to day end
  { key: 'tokyo_h', field: 'tokyo_high', label: 'TKY H', color: '#06B6D4', dash: [3, 3], startField: 'tokyo_end', endField: 'day_end' },
  { key: 'tokyo_l', field: 'tokyo_low', label: 'TKY L', color: '#06B6D4', dash: [3, 3], startField: 'tokyo_end', endField: 'day_end' },
  // London: visible from London end (15:30 CET) to day end
  { key: 'london_h', field: 'london_high', label: 'LDN H', color: '#10B981', dash: [3, 3], startField: 'london_end', endField: 'day_end' },
  { key: 'london_l', field: 'london_low', label: 'LDN L', color: '#10B981', dash: [3, 3], startField: 'london_end', endField: 'day_end' },
];

for (const day of slDays) {
  for (const def of levelDefs) {
    if (slHidden?.has(def.key)) continue;
    const price = day[def.field];
    if (price == null) continue;

    const startEpoch = day[def.startField];
    const endEpoch = day[def.endField];

    const x1 = timeScale.timeToCoordinate(toLocalEpoch(startEpoch) as Time);
    const x2 = timeScale.timeToCoordinate(toLocalEpoch(endEpoch) as Time);
    const y = pSeries.priceToCoordinate(price);

    if (x1 === null || x2 === null || y === null) continue;
    if (x2 < 0 || x1 > rect.width) continue; // off-screen

    // Draw dashed horizontal line
    ctx.save();
    ctx.strokeStyle = def.color;
    ctx.lineWidth = 1;
    ctx.setLineDash(def.dash);
    ctx.beginPath();
    ctx.moveTo(Math.max(0, x1), y);
    ctx.lineTo(Math.min(rect.width, x2), y);
    ctx.stroke();

    // Label at left edge of line
    ctx.setLineDash([]);
    ctx.font = '9px monospace';
    ctx.fillStyle = def.color;
    ctx.textAlign = 'left';
    const labelX = Math.max(2, x1 + 3);
    ctx.fillText(def.label, labelX, y - 3);
    ctx.restore();
  }
}
```

- [ ] **Step 3: Remove the old infinite price lines for session levels**

In the static price lines useEffect (~line 520-570), **remove** the session level lines that are now drawn on canvas. Keep only VP price lines (dPOC, dVAH, dVAL, wPOC, etc.).

Remove these lines (approximately lines 540-554):

```typescript
// DELETE these — now drawn as time-scoped canvas lines:
// Initial Balance
add('ibh', s.ib_high, '#F59E0B', 'IBH', LineStyle.Dotted);
add('ibl', s.ib_low,  '#F59E0B', 'IBL', LineStyle.Dotted);

// Prior Day High/Low
add('pdh', s.pdh, '#FB923C', 'PDH', LineStyle.Dashed);
add('pdl', s.pdl, '#FB923C', 'PDL', LineStyle.Dashed);

// Tokyo Session High/Low
add('tokyo_h', s.tokyo_high, '#06B6D4', 'TKY H', LineStyle.Dotted);
add('tokyo_l', s.tokyo_low,  '#06B6D4', 'TKY L', LineStyle.Dotted);

// London Session High/Low
add('london_h', s.london_high, '#10B981', 'LDN H', LineStyle.Dotted);
add('london_l', s.london_low,  '#10B981', 'LDN L', LineStyle.Dotted);
```

Keep the VP lines (dPOC, dVAH, dVAL, wPOC, wVAH, wVAL, mPOC, mVAH, mVAL) as infinite price lines — they are not session-scoped.

- [ ] **Step 4: Add `slLoaded` to the overlay redraw trigger**

Update the existing redraw useEffect (~line 397):

```typescript
useEffect(() => { drawOverlays(); }, [vpLoaded, slLoaded, hiddenLevels, drawOverlays]);
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat: draw session levels as time-scoped lines on chart canvas"
```

---

### Task 5: Test and verify

**Files:**
- Create: `backend/tests/test_session_levels_api.py`

- [ ] **Step 1: Write backend test**

```python
"""Test session levels endpoint returns correct structure."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from src.services.market_service import MarketService


class FakeCandle:
    def __init__(self, ts, h, l, c=0, o=0, v=100):
        self.ts = ts
        self.h = h
        self.l = l
        self.c = c or h
        self.o = o or l
        self.v = v


@pytest.mark.asyncio
async def test_session_levels_returns_per_day_structure():
    """Verify get_session_levels returns levels with time boundaries."""
    from zoneinfo import ZoneInfo
    _CET = ZoneInfo("Europe/Stockholm")

    # Create fake 1m candles spanning 2 days
    base = datetime(2026, 3, 18, 0, 0, tzinfo=_CET)
    candles = []
    for hour in range(0, 22):
        ts = (base + timedelta(hours=hour)).astimezone(timezone.utc)
        candles.append(FakeCandle(ts=ts, h=21500 + hour * 10, l=21490 + hour * 10))

    base2 = datetime(2026, 3, 19, 0, 0, tzinfo=_CET)
    for hour in range(0, 22):
        ts = (base2 + timedelta(hours=hour)).astimezone(timezone.utc)
        candles.append(FakeCandle(ts=ts, h=21600 + hour * 10, l=21590 + hour * 10))

    mock_db = MagicMock()
    svc = MarketService(mock_db)

    with patch.object(svc.repo, 'get_candles', return_value=candles):
        result = await svc.get_session_levels("NQ", days=2)

    assert "days" in result
    assert len(result["days"]) == 2

    day = result["days"][0]  # most recent
    assert day["date"] == "2026-03-19"
    assert "pdh" in day
    assert "ib_high" in day
    assert "tokyo_high" in day
    assert "london_high" in day
    # Time boundaries are present
    assert "tokyo_start" in day
    assert "day_end" in day
    # Time boundaries are integers (epochs)
    assert isinstance(day["tokyo_start"], int)
```

- [ ] **Step 2: Run test**

Run: `cd backend && python -m pytest tests/test_session_levels_api.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_session_levels_api.py
git commit -m "test: add session levels API test"
```

---

### Task 6: Visual verification

- [ ] **Step 1: Start dev servers and verify**

Start backend and frontend dev servers. Open the chart and verify:
- Session levels appear as time-scoped horizontal lines
- PDH/PDL (orange dashed) span the full day
- IBH/IBL (amber dotted) appear after 16:30 CET
- Tokyo H/L (cyan dotted) appear after 08:00 CET
- London H/L (green dotted) appear after 15:30 CET
- Scrolling back shows prior days' levels
- VP price lines (dPOC, wPOC, mPOC etc.) still work as infinite lines
- Level visibility toggles (hiddenLevels) still work

- [ ] **Step 2: Final commit if any fixes needed**
