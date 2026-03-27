# Per-Session TPO Letter Grid Visualization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the composite TPO histogram on the chart with 3 classic TPO letter grids — one anchored right-aligned inside each session box (Tokyo, London, NY).

**Architecture:** Enrich the existing `SessionTPO` dataclass with letter data + metadata, expose via a new API endpoint, fetch in `CandleChart.tsx`, render letter grids on the existing canvas overlay layer. Remove the composite TPO histogram.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript/lightweight-charts canvas overlay (frontend)

**Spec:** `docs/superpowers/specs/2026-03-27-per-session-tpo-visualization-design.md`

---

### Task 1: Enrich `SessionTPO` dataclass with letter data

**Files:**
- Modify: `backend/src/market_data/tpo.py:366-446`
- Test: `backend/tests/test_tpo_extended.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_tpo_extended.py`:

```python
from datetime import datetime, timezone

from src.market_data.tpo import (
    SessionTPO, compute_session_tpos,
)


def _make_bar_ts(o, h, l, c, ts_str, v=100):
    """Create a 30m bar dict with timestamp for session splitting."""
    ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "ts": ts}


class TestSessionTPOLetters:
    def test_session_tpo_has_letters(self):
        """SessionTPO should include letters dict after enrichment."""
        # Tokyo session: 2 bars at 01:00 and 01:30 UTC (= 02:00/02:30 CET → Tokyo)
        bars = [
            _make_bar_ts(100, 102, 99, 101, "2026-03-27T01:00:00"),
            _make_bar_ts(101, 103, 100, 102, "2026-03-27T01:30:00"),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        tky = result.tokyo
        assert tky is not None
        assert isinstance(tky.letters, dict)
        assert len(tky.letters) > 0
        # POC price should have the most letters
        poc_letters = tky.letters[tky.poc]
        assert len(poc_letters) >= 1

    def test_session_tpo_has_opening_type(self):
        """SessionTPO should include opening_type and opening_direction."""
        bars = [
            _make_bar_ts(100, 102, 99, 101, "2026-03-27T01:00:00"),
            _make_bar_ts(101, 103, 100, 102, "2026-03-27T01:30:00"),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        tky = result.tokyo
        assert tky is not None
        assert hasattr(tky, "opening_type")
        assert hasattr(tky, "opening_direction")
        assert tky.opening_type in ("OD", "OTD", "ORR", "OA")

    def test_session_tpo_has_excess_and_session_range(self):
        """SessionTPO should include excess counts and session high/low."""
        bars = [
            _make_bar_ts(100, 102, 99, 101, "2026-03-27T01:00:00"),
            _make_bar_ts(101, 103, 100, 102, "2026-03-27T01:30:00"),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        tky = result.tokyo
        assert tky is not None
        assert isinstance(tky.upper_excess, int)
        assert isinstance(tky.lower_excess, int)
        assert tky.session_high == 103
        assert tky.session_low == 99
        assert isinstance(tky.tpo_counts, dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py::TestSessionTPOLetters -v`
Expected: FAIL — `SessionTPO` missing `letters`, `opening_type`, etc.

- [ ] **Step 3: Enrich `SessionTPO` dataclass and `_build_session_tpo()`**

In `backend/src/market_data/tpo.py`, update `SessionTPO`:

```python
@dataclass
class SessionTPO:
    """Per-session TPO profile with full letter data for visualization."""
    session: str       # "tokyo" | "london" | "ny"
    poc: float
    vah: float
    val: float
    shape: str         # "p-shape" | "b-shape" | "d-shape" | "balanced" | "B-shape"
    ib_high: float
    ib_low: float
    ib_valid: bool     # False if IB bars have < MIN_IB_TPO_COUNT price levels
    poor_high: bool
    poor_low: bool
    # Visualization fields:
    letters: dict[float, list[str]] = field(default_factory=dict)
    tpo_counts: dict[float, int] = field(default_factory=dict)
    upper_excess: int = 0
    lower_excess: int = 0
    session_high: float = 0.0
    session_low: float = 0.0
    opening_type: str = "OA"
    opening_direction: str = "neutral"
```

Add `field` import at top of file:

```python
from dataclasses import dataclass, field
```

Update `_build_session_tpo()`:

```python
def _build_session_tpo(
    session_name: str,
    bars_30m: list[dict],
    tick_size: float,
) -> SessionTPO | None:
    """Build a SessionTPO from a slice of 30m bars for one session."""
    if not bars_30m:
        return None

    profile = compute_tpo_profile(bars_30m, tick_size=tick_size)
    shape = classify_tpo_shape(profile)
    opening_type, opening_direction = classify_opening_type(bars_30m)
    upper_excess, lower_excess = detect_excess(profile)

    # IB validity: check if first 2 bars touch enough price levels
    ib_bars = bars_30m[:2]
    ib_prices: set[float] = set()
    for bar in ib_bars:
        low_tick = round(bar["low"] / tick_size) * tick_size
        high_tick = round(bar["high"] / tick_size) * tick_size
        price = low_tick
        while price <= high_tick + tick_size / 2:
            ib_prices.add(round(price / tick_size) * tick_size)
            price += tick_size
    ib_valid = len(ib_prices) >= MIN_IB_TPO_COUNT

    return SessionTPO(
        session=session_name,
        poc=profile.poc,
        vah=profile.vah,
        val=profile.val,
        shape=shape,
        ib_high=profile.ib_high,
        ib_low=profile.ib_low,
        ib_valid=ib_valid,
        poor_high=profile.poor_high,
        poor_low=profile.poor_low,
        letters=profile.letters,
        tpo_counts={p: len(v) for p, v in profile.letters.items()},
        upper_excess=upper_excess,
        lower_excess=lower_excess,
        session_high=max(b["high"] for b in bars_30m),
        session_low=min(b["low"] for b in bars_30m),
        opening_type=opening_type,
        opening_direction=opening_direction,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py::TestSessionTPOLetters -v`
Expected: PASS

- [ ] **Step 5: Run full TPO test suite to check for regressions**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py tests/test_rl_tpo_extensions.py -v`
Expected: All PASS. The new default fields on `SessionTPO` are backward-compatible.

- [ ] **Step 6: Commit**

```bash
git add backend/src/market_data/tpo.py backend/tests/test_tpo_extended.py
git commit -m "feat(tpo): enrich SessionTPO with letters, opening type, excess"
```

---

### Task 2: Add `/api/trading/market/tpo/sessions` endpoint

**Files:**
- Modify: `backend/src/services/market_service.py` (add `get_session_tpos()`)
- Modify: `backend/src/api/routes/market.py` (add route)

- [ ] **Step 1: Add `get_session_tpos()` method to `MarketService`**

In `backend/src/services/market_service.py`, add after the existing `get_tpo_live()` method (around line 1742):

```python
    def get_session_tpos(self, symbol: str = "NQ") -> dict:
        """Per-session TPO profiles (Tokyo/London/NY) with full letter data. Cached 60s."""
        import time as _time
        from dataclasses import asdict
        from zoneinfo import ZoneInfo

        cache_key = f"tpo_sessions_{symbol}"
        now = _time.time()

        cached = MarketService._tpo_cache.get(cache_key)
        if cached and now - cached[0] < 60:
            return cached[1]

        _CET = ZoneInfo("Europe/Stockholm")
        now_cet = datetime.now(timezone.utc).astimezone(_CET)
        tpo_date = now_cet.date()
        day_start = datetime(tpo_date.year, tpo_date.month, tpo_date.day, tzinfo=_CET)
        day_end = day_start + timedelta(hours=22)

        start_utc = day_start.astimezone(timezone.utc)
        end_utc = min(day_end, datetime.now(timezone.utc).replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

        rows = self.repo.get_candles(symbol, "1m", start_utc, end_utc)

        # Build 30m bars with timestamps for session splitting
        chunk = []
        bars_30m_ts = []
        for r in rows:
            chunk.append(r)
            if len(chunk) == 30:
                bars_30m_ts.append({
                    "ts": chunk[0].ts,
                    "high": max(c.h for c in chunk),
                    "low": min(c.l for c in chunk),
                    "open": chunk[0].o,
                    "close": chunk[-1].c,
                    "volume": sum(c.v for c in chunk),
                })
                chunk = []

        session_tpo_set = compute_session_tpos(bars_30m_ts, tick_size=0.25)

        def _session_to_dict(s):
            if s is None:
                return None
            d = asdict(s)
            # Convert float keys to strings for JSON serialization
            d["letters"] = {str(k): v for k, v in d["letters"].items()}
            d["tpo_counts"] = {str(k): v for k, v in d["tpo_counts"].items()}
            return d

        result = {
            "date": tpo_date.isoformat(),
            "sessions": {
                "tokyo": _session_to_dict(session_tpo_set.tokyo),
                "london": _session_to_dict(session_tpo_set.london),
                "ny": _session_to_dict(session_tpo_set.ny),
            },
            "poc_migration_tokyo_london": session_tpo_set.poc_migration_tokyo_london,
            "poc_migration_london_ny": session_tpo_set.poc_migration_london_ny,
        }

        MarketService._tpo_cache[cache_key] = (now, result)
        return result
```

- [ ] **Step 2: Add route in `market.py`**

In `backend/src/api/routes/market.py`, add after the existing `/tpo/live` route (around line 575):

```python
@router.get("/tpo/sessions")
async def get_tpo_sessions(
    symbol: str = Query("NQ"),
    svc: MarketService = Depends(_svc),
):
    """Per-session TPO profiles (Tokyo/London/NY) with letter grids for chart visualization."""
    return svc.get_session_tpos(symbol=symbol)
```

- [ ] **Step 3: Verify endpoint works**

Run: `cd backend && python -m src.app` (start server) then:
```bash
curl http://localhost:8000/api/trading/market/tpo/sessions | python -m json.tool | head -30
```
Expected: JSON with `date`, `sessions.tokyo.letters`, `sessions.tokyo.poc`, etc.

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/market_service.py backend/src/api/routes/market.py
git commit -m "feat(api): add /tpo/sessions endpoint for per-session letter grids"
```

---

### Task 3: Add frontend types and API method

**Files:**
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/services/api/trading.ts`

- [ ] **Step 1: Add `SessionTPOData` and `SessionTPOResponse` types**

In `frontend/src/types/market.ts`, add after the `TPOLiveProfile` interface (around line 481):

```typescript
export interface SessionTPOData {
  letters: Record<string, string[]>;  // price → [A, B, C, ...]
  tpo_counts: Record<string, number>; // price → count
  poc: number;
  vah: number;
  val: number;
  ib_high: number;
  ib_low: number;
  ib_valid: boolean;
  shape: string;
  opening_type: string;
  opening_direction: string;
  poor_high: boolean;
  poor_low: boolean;
  upper_excess: number;
  lower_excess: number;
  session_high: number;
  session_low: number;
}

export interface SessionTPOResponse {
  date: string;
  sessions: {
    tokyo: SessionTPOData | null;
    london: SessionTPOData | null;
    ny: SessionTPOData | null;
  };
  poc_migration_tokyo_london: number;
  poc_migration_london_ny: number;
}
```

- [ ] **Step 2: Add API method**

In `frontend/src/services/api/trading.ts`, add after the `getTpoLive` method (around line 261):

```typescript
  async getSessionTPO(symbol = 'NQ'): Promise<import('@/types/market').SessionTPOResponse> {
    return fetchJson(`/trading/market/tpo/sessions?symbol=${symbol}`);
  },
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/services/api/trading.ts
git commit -m "feat(frontend): add SessionTPO types and API method"
```

---

### Task 4: Render per-session TPO letter grids on canvas

**Files:**
- Modify: `frontend/src/components/Terminal/pages/CandleChart.tsx`

This is the core rendering task. The letter grid is drawn on the canvas overlay inside each session box.

- [ ] **Step 1: Add session TPO data ref and fetch**

In `CandleChart.tsx`, add the ref after `tpoRef` (around line 200):

```typescript
  // Per-session TPO letter grid data
  const sessionTPORef = useRef<import('@/types/market').SessionTPOResponse | null>(null);
  const [sessionTPOLoaded, setSessionTPOLoaded] = useState(false);
```

Add the `import type { SessionTPOResponse } from '@/types/market'` to the imports at line 17 (append to existing import).

Add a fetch effect after the session levels fetch (around line 627):

```typescript
  // Fetch per-session TPO letter grid data — once on mount, re-fetch when session changes
  useEffect(() => {
    let cancelled = false;
    api.getSessionTPO('NQ').then(res => {
      if (!cancelled && res.sessions) {
        sessionTPORef.current = res;
        setSessionTPOLoaded(true);
        drawOverlays();
      }
    }).catch(err => { console.warn('[SessionTPO] fetch failed:', err); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
```

Add `sessionTPOLoaded` to the redraw trigger effect (line 630):

```typescript
  useEffect(() => { drawOverlays(); }, [vpLoaded, slLoaded, sessionTPOLoaded, hiddenLevels, tpo, drawOverlays]);
```

- [ ] **Step 2: Remove old composite TPO histogram**

In `drawOverlays()`, delete the entire block from line 428–455:

```typescript
    // --- TPO histogram on right edge (orange, next to VP histograms) ---
    const tpoData = tpoRef.current;
    if (tpoData && !hidden?.has('vp_tpo')) {
      // ... entire block ...
    }
```

- [ ] **Step 3: Add per-session TPO letter grid rendering**

In `drawOverlays()`, after the NY IB levels block (around line 426, where the old TPO block was), add:

```typescript
    // --- Per-session TPO letter grids (inside session boxes) ---
    const sessionTPO = sessionTPORef.current;
    if (sessionTPO && boxes.length > 0) {
      const SESSION_TPO_MAP: Record<string, { data: import('@/types/market').SessionTPOData | null; hiddenKey: string; color: string }> = {
        'Tokyo':    { data: sessionTPO.sessions.tokyo,  hiddenKey: 'tpo_tky_letters', color: '#06B6D4' },
        'London':   { data: sessionTPO.sessions.london, hiddenKey: 'tpo_ldn_letters', color: '#10B981' },
        'New York': { data: sessionTPO.sessions.ny,     hiddenKey: 'tpo_ny_letters',  color: '#EF4444' },
      };

      for (const box of boxes) {
        const tpoMeta = SESSION_TPO_MAP[box.name];
        if (!tpoMeta || !tpoMeta.data || hidden?.has(tpoMeta.hiddenKey)) continue;
        const tpoSession = tpoMeta.data;
        const color = tpoMeta.color;

        // Box right edge X coordinate (with padding)
        const boxRightX = timeScale.timeToCoordinate(toLocalEpoch(box.endEpoch) as Time);
        if (boxRightX === null) continue;
        const anchorX = Math.min(boxRightX - 4, rect.width);

        // Box width check: if too narrow, skip letters (graceful degradation)
        const boxLeftX = timeScale.timeToCoordinate(toLocalEpoch(box.startEpoch) as Time);
        const boxWidth = boxLeftX !== null ? Math.abs(anchorX - boxLeftX) : 200;
        if (boxWidth < 60) continue;

        // Sort prices descending (high to low on chart)
        const prices = Object.keys(tpoSession.letters).map(Number).sort((a, b) => b - a);
        if (prices.length === 0) continue;

        ctx.save();
        ctx.font = '9px monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';

        for (const price of prices) {
          const y = pSeries.priceToCoordinate(price);
          if (y === null || y < 0 || y > rect.height) continue;

          const letters = tpoSession.letters[String(price)];
          const letterStr = letters.join(' ');
          const isPOC = price === tpoSession.poc;
          const inVA = price >= tpoSession.val && price <= tpoSession.vah;

          // Opacity: POC=1.0, VA=0.7, outside=0.4
          const alpha = isPOC ? 1.0 : inVA ? 0.7 : 0.4;

          // POC row background highlight
          if (isPOC) {
            const textWidth = ctx.measureText(letterStr + ' ◄').width;
            ctx.fillStyle = `${color}1F`; // hex color + 12% alpha suffix
            ctx.fillRect(anchorX - textWidth - 6, y - 7, textWidth + 8, 14);
          }

          ctx.fillStyle = color;
          ctx.globalAlpha = alpha;
          ctx.fillText(isPOC ? `${letterStr} ◄` : letterStr, anchorX, y);
        }

        ctx.globalAlpha = 1.0;

        // --- Session metadata footer at bottom of box ---
        const boxBottomY = pSeries.priceToCoordinate(box.low);
        if (boxBottomY !== null) {
          const ibRange = tpoSession.ib_valid
            ? ((tpoSession.ib_high - tpoSession.ib_low) / 0.25).toFixed(0)
            : '—';
          const arrow = tpoSession.opening_direction === 'up' ? '↑'
            : tpoSession.opening_direction === 'down' ? '↓' : '↔';
          const footerText = `${tpoSession.shape}  IB:${ibRange}  ${tpoSession.opening_type}${arrow}  ex:${tpoSession.upper_excess}/${tpoSession.lower_excess}`;
          ctx.font = '8px monospace';
          ctx.fillStyle = color;
          ctx.globalAlpha = 0.5;
          ctx.textAlign = 'right';
          ctx.fillText(footerText, anchorX, boxBottomY + 12);
          ctx.globalAlpha = 1.0;
        }

        ctx.restore();

        // --- POC/VAH/VAL dashed extension lines ---
        const dayEndEpoch = box.endEpoch + (22 * 60 - epochToCETMinute(box.endEpoch)) * 60;
        const lineEndX = timeScale.timeToCoordinate(toLocalEpoch(dayEndEpoch) as Time);

        const prefixMap: Record<string, string> = { 'Tokyo': 'TKY', 'London': 'LDN', 'New York': 'NY' };
        const prefix = prefixMap[box.name] || '';

        const levels = [
          { price: tpoSession.poc, label: `${prefix} POC`, alpha: 0.6, dash: [4, 3], key: `tpo_${prefix.toLowerCase()}_poc` },
          { price: tpoSession.vah, label: `${prefix} VAH`, alpha: 0.4, dash: [2, 3], key: `tpo_${prefix.toLowerCase()}_vah` },
          { price: tpoSession.val, label: `${prefix} VAL`, alpha: 0.4, dash: [2, 3], key: `tpo_${prefix.toLowerCase()}_val` },
        ];

        for (const lv of levels) {
          if (hidden?.has(lv.key)) continue;
          const y = pSeries.priceToCoordinate(lv.price);
          if (y === null) continue;

          const lx = boxRightX ?? 0;
          const rx = lineEndX ?? rect.width;
          if (rx < 0 || lx > rect.width) continue;
          const drawX1 = Math.max(0, lx);
          const drawX2 = Math.min(rect.width, rx);

          ctx.save();
          ctx.strokeStyle = color;
          ctx.globalAlpha = lv.alpha;
          ctx.lineWidth = 1;
          ctx.setLineDash(lv.dash);
          ctx.beginPath();
          ctx.moveTo(drawX1, y);
          ctx.lineTo(drawX2, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = color;
          ctx.textAlign = 'left';
          ctx.fillText(lv.label, drawX1 + 3, y - 3);
          ctx.globalAlpha = 1.0;
          ctx.restore();
        }
      }
    }
```

- [ ] **Step 4: Verify rendering with dev server**

Start backend + frontend dev servers and open the chart. Verify:
- Letter grids appear inside Tokyo (cyan), London (green), NY (red) session boxes
- POC row is highlighted with ◄ marker
- Dashed POC/VAH/VAL lines extend rightward from each session box
- Metadata footer shows shape, IB range, opening type, excess at bottom of box
- Scrolling/zooming redraws correctly

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat(chart): render per-session TPO letter grids inside session boxes"
```

---

### Task 5: Update toggle groups and clean up composite TPO references

**Files:**
- Modify: `frontend/src/components/Terminal/pages/BookSnapshot.tsx`
- Modify: `frontend/src/components/Terminal/pages/L1Page.tsx`
- Modify: `frontend/src/components/Terminal/pages/CandleChart.tsx`

- [ ] **Step 1: Update `LEVEL_GROUPS` in BookSnapshot.tsx**

In `frontend/src/components/Terminal/pages/BookSnapshot.tsx`, replace the `tpo` group (line 14):

```typescript
// Old:
  tpo: ['t_poc', 't_vah', 't_val', 'vp_tpo'],

// New:
  tpo_tokyo:  ['tpo_tky_letters', 'tpo_tky_poc', 'tpo_tky_vah', 'tpo_tky_val'],
  tpo_london: ['tpo_ldn_letters', 'tpo_ldn_poc', 'tpo_ldn_vah', 'tpo_ldn_val'],
  tpo_ny:     ['tpo_ny_letters',  'tpo_ny_poc',  'tpo_ny_vah',  'tpo_ny_val'],
```

- [ ] **Step 2: Update the TPO section in BookSnapshot.tsx UI**

Replace the TPO Profile section (lines 158-197) to show per-session toggles. The `tpo` prop still provides the composite data for the sidebar stats display — keep reading it:

```typescript
      {/* TPO Profiles (per-session toggles) */}
      {tpo && (
        <div className="px-2 py-1 border-b border-border last:border-b-0">
          <div className="flex gap-2 mb-1">
            <button onClick={() => toggleCluster(['tpo_tokyo', 'tpo_london', 'tpo_ny'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer">TPO</button>
            <button onClick={() => toggleGroup('tpo_tokyo')} className={`text-[10px] cursor-pointer transition-colors ${isGroupHidden('tpo_tokyo') ? 'text-muted line-through' : 'text-cyan-400'}`}>TKY</button>
            <button onClick={() => toggleGroup('tpo_london')} className={`text-[10px] cursor-pointer transition-colors ${isGroupHidden('tpo_london') ? 'text-muted line-through' : 'text-emerald-400'}`}>LDN</button>
            <button onClick={() => toggleGroup('tpo_ny')} className={`text-[10px] cursor-pointer transition-colors ${isGroupHidden('tpo_ny') ? 'text-muted line-through' : 'text-red-400'}`}>NY</button>
          </div>

          <div>
            <Row label="Shape" value={tpo.profile_shape} color="text-orange-300" />
            <Row
              label="Opening"
              value={`${tpo.opening_type} ${tpo.opening_direction === 'up' ? '\u2191' : tpo.opening_direction === 'down' ? '\u2193' : '\u2194'}`}
              color="text-orange-300"
            />
            <Row
              label="Rotation"
              value={`${tpo.rotation_factor > 0 ? '+' : ''}${tpo.rotation_factor.toFixed(1)}`}
              color={tpo.rotation_factor > 0 ? 'text-emerald-400' : tpo.rotation_factor < 0 ? 'text-red-400' : 'text-muted2'}
            />
            <Row label="IB Range" value={(tpo.ib_high - tpo.ib_low).toFixed(2)} />
          </div>
        </div>
      )}
```

- [ ] **Step 3: Remove composite `tpo` prop from CandleChart**

In `frontend/src/components/Terminal/pages/CandleChart.tsx`:

Remove `tpo` from the `Props` interface (line 87):
```typescript
// Old:
  tpo?: TPOLiveProfile | null;
// Remove this line
```

Remove `tpoRef` (lines 199-200):
```typescript
// Delete:
  const tpoRef = useRef<TPOLiveProfile | null>(null);
  tpoRef.current = tpo ?? null;
```

Update the `CandleChart` function signature (line 171):
```typescript
// Old:
export function CandleChart({ lastCandle, session, hiddenLevels, tpo }: Props) {
// New:
export function CandleChart({ lastCandle, session, hiddenLevels }: Props) {
```

Remove `tpo` from the redraw deps (line 630):
```typescript
// Old:
  useEffect(() => { drawOverlays(); }, [vpLoaded, slLoaded, sessionTPOLoaded, hiddenLevels, tpo, drawOverlays]);
// New:
  useEffect(() => { drawOverlays(); }, [vpLoaded, slLoaded, sessionTPOLoaded, hiddenLevels, drawOverlays]);
```

Remove the `TPOLiveProfile` from the import on line 17 (keep `SessionTPOResponse`).

- [ ] **Step 4: Update L1Page to stop passing `tpo` to CandleChart**

In `frontend/src/components/Terminal/pages/L1Page.tsx`, line 63:

```typescript
// Old:
          <CandleChart lastCandle={lastCandle} session={session} hiddenLevels={hiddenLevels} tpo={tpo} />
// New:
          <CandleChart lastCandle={lastCandle} session={session} hiddenLevels={hiddenLevels} />
```

Keep the `tpo` state and `getTpoLive` fetch — `BookSnapshot` still uses it for the sidebar stats.

- [ ] **Step 5: Verify everything compiles and renders**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors.

Start dev servers and verify:
- Per-session letter grids render in session boxes
- TKY/LDN/NY toggle buttons work in sidebar
- Composite TPO histogram is gone
- Sidebar still shows composite TPO stats (shape, opening, rotation, IB)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/BookSnapshot.tsx frontend/src/components/Terminal/pages/L1Page.tsx frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat(chart): replace composite TPO with per-session toggle groups"
```

---

### Task 6: Final polish and edge cases

**Files:**
- Modify: `frontend/src/components/Terminal/pages/CandleChart.tsx`

- [ ] **Step 1: Add histogram fallback for narrow zoom**

The letter grid rendering in Task 4 already skips if `boxWidth < 60`. But instead of just skipping, add a compact histogram fallback. In the `drawOverlays()` per-session TPO block, replace the `if (boxWidth < 60) continue;` with:

```typescript
        if (boxWidth < 60) {
          // Fallback: compact histogram bars (like old composite TPO)
          const prices = Object.keys(tpoSession.tpo_counts).map(Number);
          const maxCount = Math.max(...prices.map(p => tpoSession.tpo_counts[String(p)]));
          if (maxCount <= 0) continue;
          const barMaxW = Math.min(boxWidth * 0.6, 30);
          for (const price of prices) {
            const y = pSeries.priceToCoordinate(price);
            if (y === null || y < 0 || y > rect.height) continue;
            const count = tpoSession.tpo_counts[String(price)];
            const barW = (count / maxCount) * barMaxW;
            const isPOC = price === tpoSession.poc;
            const inVA = price >= tpoSession.val && price <= tpoSession.vah;
            ctx.fillStyle = color;
            ctx.globalAlpha = isPOC ? 0.6 : inVA ? 0.35 : 0.2;
            ctx.fillRect(anchorX - barW, y - 1, barW, 2);
          }
          ctx.globalAlpha = 1.0;
          continue;
        }
```

- [ ] **Step 2: Verify zoom-out fallback**

Zoom out until session boxes are narrow. Verify histogram bars appear instead of unreadable text.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat(chart): add histogram fallback for narrow session boxes"
```
