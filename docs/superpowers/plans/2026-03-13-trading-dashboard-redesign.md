# Trading Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 2-layer manual gate system with a flat auto-computed indicator dashboard showing all analytical layers, with multi-timeframe volume profiles and always-visible signals.

**Architecture:** Backend adds 3 new functions (swing detection, naked POCs, developing POC) and expands the session response to include composite VPs and structure data. Frontend rewrites TradingIntradayPage from gate-based to 10-section indicator dashboard. session_json storage format unchanged — new data assembled in route handler.

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy / Databento | React 19 / TypeScript

**Spec:** `docs/superpowers/specs/2026-03-13-trading-dashboard-redesign.md`

---

## Chunk 1: Backend — New Analysis Functions

### Task 1: Swing Point Detection

**Files:**
- Modify: `backend/src/market_data/levels.py:277` (append new function)
- Create: `backend/tests/test_swing_points.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_swing_points.py
from src.market_data.levels import detect_swing_points


def test_uptrend_structure():
    """Bars that make HH/HL should classify as uptrend."""
    # Simulate 20 bars with clear uptrend: rising highs and rising lows
    bars = []
    for i in range(20):
        bars.append({
            "high": 100 + i * 2 + (5 if i in (5, 15) else 0),
            "low": 95 + i * 2 - (5 if i in (3, 13) else 0),
            "close": 98 + i * 2,
        })
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "uptrend"
    assert result["swing_high"] is not None
    assert result["swing_low"] is not None
    assert result["last_hh"] is not None
    assert result["last_hl"] is not None


def test_downtrend_structure():
    """Bars that make LH/LL should classify as downtrend."""
    bars = []
    for i in range(20):
        bars.append({
            "high": 140 - i * 2 + (5 if i in (5, 15) else 0),
            "low": 135 - i * 2 - (5 if i in (3, 13) else 0),
            "close": 137 - i * 2,
        })
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "downtrend"
    assert result["last_lh"] is not None
    assert result["last_ll"] is not None


def test_ranging_structure():
    """Bars that oscillate within range should classify as ranging."""
    bars = []
    for i in range(20):
        offset = 5 if i % 4 < 2 else -5
        bars.append({
            "high": 120 + offset,
            "low": 110 + offset,
            "close": 115 + offset,
        })
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "ranging"


def test_insufficient_bars():
    """Fewer bars than 2*lookback+1 should return ranging with no swings."""
    bars = [{"high": 100, "low": 95, "close": 97}] * 3
    result = detect_swing_points(bars, lookback=3)
    assert result["structure"] == "ranging"
    assert result["swing_high"] is None
    assert result["swing_low"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_swing_points.py -v`
Expected: FAIL with `ImportError: cannot import name 'detect_swing_points'`

- [ ] **Step 3: Implement detect_swing_points**

Append to `backend/src/market_data/levels.py`:

```python
def detect_swing_points(bars: list[dict], lookback: int = 5) -> dict:
    """Detect HH/HL/LH/LL swing structure from bar data.

    A swing high = bar whose high > all bars within lookback on each side.
    A swing low = bar whose low < all bars within lookback on each side.

    Returns dict with structure classification and swing levels.
    """
    n = len(bars)
    if n < 2 * lookback + 1:
        return {
            "structure": "ranging",
            "last_hh": None, "last_hl": None,
            "last_lh": None, "last_ll": None,
            "swing_high": None, "swing_low": None,
        }

    # Find pivot highs and lows
    pivot_highs: list[tuple[int, float]] = []  # (index, price)
    pivot_lows: list[tuple[int, float]] = []

    for i in range(lookback, n - lookback):
        high = bars[i]["high"]
        low = bars[i]["low"]
        is_pivot_high = all(
            high >= bars[j]["high"] for j in range(i - lookback, i + lookback + 1) if j != i
        )
        is_pivot_low = all(
            low <= bars[j]["low"] for j in range(i - lookback, i + lookback + 1) if j != i
        )
        if is_pivot_high:
            pivot_highs.append((i, high))
        if is_pivot_low:
            pivot_lows.append((i, low))

    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return {
            "structure": "ranging",
            "last_hh": pivot_highs[-1][1] if pivot_highs else None,
            "last_hl": None, "last_lh": None, "last_ll": None,
            "swing_high": pivot_highs[-1][1] if pivot_highs else None,
            "swing_low": pivot_lows[-1][1] if pivot_lows else None,
        }

    # Classify structure from last 2 pivot highs and lows
    ph1, ph2 = pivot_highs[-2][1], pivot_highs[-1][1]
    pl1, pl2 = pivot_lows[-2][1], pivot_lows[-1][1]

    hh = ph2 > ph1  # Higher high
    hl = pl2 > pl1  # Higher low
    lh = ph2 < ph1  # Lower high
    ll = pl2 < pl1  # Lower low

    if hh and hl:
        structure = "uptrend"
    elif lh and ll:
        structure = "downtrend"
    else:
        structure = "ranging"

    return {
        "structure": structure,
        "last_hh": ph2 if hh else None,
        "last_hl": pl2 if hl else None,
        "last_lh": ph2 if lh else None,
        "last_ll": pl2 if ll else None,
        "swing_high": pivot_highs[-1][1],
        "swing_low": pivot_lows[-1][1],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_swing_points.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/levels.py backend/tests/test_swing_points.py
git commit -m "feat(trading): add swing point detection (HH/HL/LH/LL structure)"
```

---

### Task 2: Naked POC Detection

**Files:**
- Modify: `backend/src/market_data/levels.py` (append new function)
- Create: `backend/tests/test_naked_pocs.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_naked_pocs.py
from src.market_data.levels import detect_naked_pocs


def test_naked_poc_not_revisited():
    """POC from prior session that was never touched should be detected."""
    prior_sessions = [
        {"date": "2026-03-10", "poc": 21450.0},
        {"date": "2026-03-11", "poc": 21680.0},
        {"date": "2026-03-12", "poc": 21820.0},
    ]
    # Bars since Mar 10 — price stayed 21500-21900, never hit 21450
    bars_since = [
        {"high": 21600, "low": 21500},
        {"high": 21700, "low": 21550},
        {"high": 21900, "low": 21700},
        {"high": 21850, "low": 21750},
    ]
    result = detect_naked_pocs(prior_sessions, bars_since)
    # 21450 was never touched, should be naked
    naked_prices = [r["price"] for r in result]
    assert 21450.0 in naked_prices
    # 21680 was touched (bar 21550-21700 covers it)
    assert 21680.0 not in naked_prices
    # 21820 was touched (bar 21750-21850 covers it)
    assert 21820.0 not in naked_prices


def test_no_naked_pocs():
    """All POCs touched should return empty list."""
    prior_sessions = [{"date": "2026-03-10", "poc": 21600.0}]
    bars_since = [{"high": 21700, "low": 21500}]
    result = detect_naked_pocs(prior_sessions, bars_since)
    assert result == []


def test_empty_sessions():
    """No prior sessions should return empty list."""
    result = detect_naked_pocs([], [])
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_naked_pocs.py -v`
Expected: FAIL with `ImportError: cannot import name 'detect_naked_pocs'`

- [ ] **Step 3: Implement detect_naked_pocs**

Append to `backend/src/market_data/levels.py`:

```python
def detect_naked_pocs(
    prior_sessions: list[dict],
    bars_since: list[dict],
) -> list[dict]:
    """Find POCs from prior sessions that price has never revisited.

    A POC is 'naked' if no bar's low-high range includes that price
    since the session it was computed from.

    Args:
        prior_sessions: [{date, poc}, ...] ordered oldest to newest
        bars_since: All bars from oldest session date to now

    Returns: [{date, price}, ...] for naked POCs only
    """
    if not prior_sessions:
        return []

    naked = []
    for session in prior_sessions:
        poc = session["poc"]
        touched = any(
            bar["low"] <= poc <= bar["high"]
            for bar in bars_since
        )
        if not touched:
            naked.append({"date": session["date"], "price": poc})

    return naked
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_naked_pocs.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/levels.py backend/tests/test_naked_pocs.py
git commit -m "feat(trading): add naked POC detection for untested prior-session POCs"
```

---

### Task 3: Developing POC Tracker

**Files:**
- Modify: `backend/src/market_data/levels.py` (append new function — uses dict-based VP)
- Create: `backend/tests/test_developing_poc.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_developing_poc.py
from src.market_data.levels import compute_developing_poc


def test_poc_migrating_up():
    """POC should migrate up when recent volume concentrates higher."""
    # First half: volume concentrated at 100
    bars = []
    for i in range(10):
        bars.append({"high": 102, "low": 98, "close": 100, "volume": 1000})
    # Second half: volume concentrated at 105
    for i in range(10):
        bars.append({"high": 107, "low": 103, "close": 105, "volume": 1500})

    result = compute_developing_poc(bars)
    assert result["developing_poc"] is not None
    assert result["direction"] == "up"


def test_poc_stable():
    """Stable POC should report flat direction."""
    bars = [{"high": 102, "low": 98, "close": 100, "volume": 1000}] * 20
    result = compute_developing_poc(bars)
    assert result["direction"] == "flat"


def test_empty_bars():
    """No bars should return None."""
    result = compute_developing_poc([])
    assert result["developing_poc"] is None
    assert result["direction"] == "flat"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_developing_poc.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_developing_poc'`

- [ ] **Step 3: Implement compute_developing_poc**

Append to `backend/src/market_data/levels.py` (NOT amt.py — levels.py has a dict-based `compute_volume_profile` that accepts `list[dict]`):

```python
def compute_developing_poc(bars: list[dict], tick_size: float = 0.25) -> dict:
    """Track POC migration by comparing current POC vs POC from first half.

    Uses the dict-based compute_volume_profile from this module (levels.py),
    which accepts list[dict] with "high"/"low"/"close"/"volume" keys.

    Returns:
        {
            "developing_poc": float | None,
            "prior_poc": float | None,
            "direction": "up" | "down" | "flat",
        }
    """
    if not bars:
        return {"developing_poc": None, "prior_poc": None, "direction": "flat"}

    current_vp = compute_volume_profile(bars, tick_size)
    current_poc = current_vp.poc

    # Compare against first-half POC
    half = max(1, len(bars) // 2)
    first_half_vp = compute_volume_profile(bars[:half], tick_size)
    prior_poc = first_half_vp.poc

    if current_poc is None or prior_poc is None:
        return {"developing_poc": current_poc, "prior_poc": prior_poc, "direction": "flat"}

    diff = current_poc - prior_poc
    threshold = tick_size * 4  # 1 point for NQ

    if diff > threshold:
        direction = "up"
    elif diff < -threshold:
        direction = "down"
    else:
        direction = "flat"

    return {
        "developing_poc": current_poc,
        "prior_poc": prior_poc,
        "direction": direction,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_developing_poc.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/levels.py backend/tests/test_developing_poc.py
git commit -m "feat(trading): add developing POC migration tracker"
```

---

## Chunk 2: Backend — Expanded Session Response

### Task 4: Expand compute_session to include new data

**Files:**
- Modify: `backend/src/services/market_service.py:60-246` (compute_session method)
- Modify: `backend/src/market_data/amt.py:75-88` (MacroSnapshot dataclass)

- [ ] **Step 1: Add optional fields to MacroSnapshot**

In `backend/src/market_data/amt.py`, find the `MacroSnapshot` dataclass (line ~75) and add fields:

```python
@dataclass
class MacroSnapshot:
    vix: float | None = None
    vix_change_pct: float | None = None
    dxy: float | None = None
    dxy_change_pct: float | None = None
    us10y: float | None = None
    us10y_change_bps: float | None = None
    us2y: float | None = None
    yield_curve_spread: float | None = None
    regime: str = "unknown"
    regime_score: float = 0.0
    fetched_at: str | None = None
    # Phase 2 fields — None until external data sources added
    gex: float | None = None
    put_call_ratio: float | None = None
    es_nq_ratio_change: float | None = None
```

- [ ] **Step 2: Add build_expanded_session method to MarketService**

In `backend/src/services/market_service.py`, add a new method after `compute_session()` (~line 246):

```python
async def build_expanded_session(self, symbol: str = "NQ") -> dict:
    """Build the expanded session response with all analytical layers.

    Assembles data from:
    - session_json (flat SessionAnalysis dict)
    - DB columns (rotation_factor, aspr, aspr_percentile)
    - New computed data (swing points, composite VPs, naked POCs, developing POC)
    """
    from src.market_data.levels import detect_swing_points, detect_naked_pocs
    from src.market_data.levels import compute_developing_poc, compute_volume_profile

    # Get current session from DB
    # NOTE: If repo doesn't have get_latest_session(), use:
    #   session_row = self.repo.get_session(datetime.now().strftime("%Y-%m-%d"), symbol)
    # Similarly, get_recent_sessions() may need to be added to MarketRepo
    # as: db.query(MarketSession).filter_by(symbol=symbol).order_by(MarketSession.date.desc()).limit(N).all()
    session_row = self.repo.get_latest_session(symbol)
    if not session_row:
        return None

    session_data = json.loads(session_row.session_json) if isinstance(session_row.session_json, str) else session_row.session_json

    # Get bars for swing point detection (today's 1-min bars)
    bars = session_data.get("bars", [])
    if not bars:
        # Fetch from cache if not in session_json
        bars = await self._fetch_bars_for_date(symbol, session_row.date)

    # 1. Swing points
    structure = detect_swing_points(bars, lookback=5)

    # 2. Multi-TF volume profiles
    profiles = {"session": {"poc": session_row.poc, "vah": session_row.vah, "val": session_row.val}}

    # Weekly composite
    weekly_bars = await self._fetch_weekly_bars(symbol)
    if weekly_bars:
        weekly_vp = compute_volume_profile(weekly_bars)
        profiles["weekly"] = {"poc": weekly_vp.poc, "vah": weekly_vp.vah, "val": weekly_vp.val}

    # Leg and Macro profiles from context anchor dates
    ctx = self.repo.get_context(symbol)
    if ctx and ctx.vp_leg_start:
        from datetime import datetime, timezone
        leg_start = datetime.fromtimestamp(ctx.vp_leg_start, tz=timezone.utc).strftime("%Y-%m-%d")
        leg_bars = await self._fetch_bars_range(symbol, leg_start)
        if leg_bars:
            leg_vp = compute_volume_profile(leg_bars)
            profiles["leg"] = {"poc": leg_vp.poc, "vah": leg_vp.vah, "val": leg_vp.val, "anchor": leg_start}

    if ctx and ctx.vp_ongoing_macro_start:
        from datetime import datetime, timezone
        macro_start = datetime.fromtimestamp(ctx.vp_ongoing_macro_start, tz=timezone.utc).strftime("%Y-%m-%d")
        macro_bars = await self._fetch_bars_range(symbol, macro_start, daily=True)
        if macro_bars:
            macro_vp = compute_volume_profile(macro_bars)
            profiles["macro"] = {"poc": macro_vp.poc, "vah": macro_vp.vah, "val": macro_vp.val, "anchor": macro_start}

    # 3. Developing POC
    dev_poc = compute_developing_poc(bars)
    profiles["developing_poc"] = dev_poc["developing_poc"]
    profiles["developing_poc_direction"] = dev_poc["direction"]

    # 4. Naked POCs
    prior_sessions = self.repo.get_recent_sessions(symbol, limit=20)
    prior_pocs = [{"date": s.date, "poc": s.poc} for s in prior_sessions if s.poc]
    all_bars = await self._fetch_bars_range(symbol, prior_sessions[-1].date if prior_sessions else session_row.date)
    profiles["naked_pocs"] = detect_naked_pocs(prior_pocs, all_bars or [])

    # 5. COT data
    cot_data = self._get_cot_summary()

    # 6. Levels from DB
    levels = self.repo.get_levels(symbol, session_row.date)
    levels_list = [
        {
            "type": lv.level_type,
            "price_low": lv.price_low,
            "price_high": lv.price_high,
            "direction": lv.direction,
            "session": lv.session,
            "is_filled": lv.is_filled,
        }
        for lv in levels
    ]

    # Build macro dict with COT merged
    macro = session_data.get("macro", {})
    if cot_data:
        macro["cot_net_position"] = cot_data.get("net_non_commercial")
        macro["cot_change_1w"] = cot_data.get("change_1w")

    # Assemble nested response
    return {
        "session": {
            **{k: session_data.get(k) for k in [
                "poc", "vah", "val", "tpo_poc", "tpo_vah", "tpo_val",
                "distribution_type", "vwap",
                "vwap_1sd_upper", "vwap_1sd_lower",
                "vwap_2sd_upper", "vwap_2sd_lower",
                "vwap_3sd_upper", "vwap_3sd_lower",
                "ib_high", "ib_low", "ib_range",
                "market_type", "opening_type",
                "poor_high", "poor_low", "single_prints",
                "value_migration", "overnight_high", "overnight_low",
                "total_delta", "delta_divergence",
            ]},
            # Merge DB-only columns
            "rotation_factor": session_row.rotation_factor,
            "aspr": session_row.aspr,
            "aspr_percentile": session_row.aspr_percentile,
        },
        "macro": macro,
        "structure": structure,
        "profiles": profiles,
        "levels": levels_list,
        "price_position": {
            "last_price": session_data.get("last_price"),
            "vs_va": session_data.get("price_vs_va"),
            "vs_vwap": session_data.get("price_vs_vwap"),
            "vs_ib": session_data.get("price_vs_ib"),
            "vwap_deviation_sd": self._compute_vwap_deviation_sd(session_data),
        },
        "ml_day_type": None,  # Populated by indicators endpoint
        "ml_day_type_confidence": None,
    }
```

- [ ] **Step 3: Add helper methods for bar fetching**

Add these helper methods to `MarketService` in `market_service.py`:

```python
async def _fetch_bars_for_date(self, symbol: str, date_str: str | None) -> list[dict]:
    """Fetch 1-min bars for a specific date from cache/Databento.

    NOTE: Uses the module-level _get_provider() function, NOT self._get_data_provider().
    The provider may be sync (CachedMarketDataProvider) or async — handle both.
    """
    if not date_str:
        return []
    try:
        from src.market_data.cache import get_cached_provider
        provider = get_cached_provider()
        bars = provider.get_bars(symbol, "1m", date_str, date_str)
        return bars if bars else []
    except Exception as e:
        logger.warning("Failed to fetch bars for %s %s: %s", symbol, date_str, e)
        return []

async def _fetch_weekly_bars(self, symbol: str) -> list[dict]:
    """Fetch 1-min bars for current week (Monday to today)."""
    from datetime import datetime, timedelta
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    try:
        from src.market_data.cache import get_cached_provider
        provider = get_cached_provider()
        bars = provider.get_bars(symbol, "1m", monday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
        return bars if bars else []
    except Exception as e:
        logger.warning("Failed to fetch weekly bars: %s", e)
        return []

async def _fetch_bars_range(self, symbol: str, start_date: str, daily: bool = False) -> list[dict]:
    """Fetch bars from start_date to today. Use daily=True for long ranges."""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    interval = "1d" if daily else "1m"
    try:
        from src.market_data.cache import get_cached_provider
        provider = get_cached_provider()
        bars = provider.get_bars(symbol, interval, start_date, today)
        return bars if bars else []
    except Exception as e:
        logger.warning("Failed to fetch bars range %s to %s: %s", start_date, today, e)
        return []

def _get_cot_summary(self) -> dict | None:
    """Get latest COT data from DB (sync — reads from cot_reports table).

    NOTE: The COT module has fetch_cot (async, fetches from CFTC) and
    a DB table cot_reports. Use the DB directly via self.db for sync access.
    """
    try:
        from sqlalchemy import text
        rows = self.db.execute(
            text("SELECT * FROM cot_reports ORDER BY report_date DESC LIMIT 2")
        ).fetchall()
        if not rows:
            return None
        latest = dict(rows[0]._mapping)
        change_1w = None
        if len(rows) > 1:
            prev = dict(rows[1]._mapping)
            change_1w = (latest.get("net_non_commercial", 0) or 0) - (prev.get("net_non_commercial", 0) or 0)
        return {
            "net_non_commercial": latest.get("net_non_commercial"),
            "change_1w": change_1w,
        }
    except Exception:
        return None
```

**NOTE:** The actual import path for the cached provider depends on how it's instantiated in the existing codebase. Check `market_service.py` for how `compute_session()` gets its provider — likely through a factory function or module-level singleton. Use the same pattern. If `get_cached_provider()` doesn't exist, create a simple factory that returns `CachedMarketDataProvider` with the standard cache directory.

- [ ] **Step 4: Verify imports and no syntax errors**

Run: `cd backend && python -c "from src.services.market_service import MarketService; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/market_service.py backend/src/market_data/amt.py
git commit -m "feat(trading): expand session response with composite VPs, swing points, naked POCs"
```

---

### Task 5: Replace confirmations endpoint with indicators

**Files:**
- Modify: `backend/src/services/market_service.py:568-702` (get_confirmations → get_indicators)
- Modify: `backend/src/api/routes/market.py:79` (confirmations route)

- [ ] **Step 1: Rename and simplify get_confirmations to get_indicators**

In `backend/src/services/market_service.py`, find `get_confirmations` (line ~568). Replace the gate logic with flat indicator output. The method should:
1. Compute live orderflow (already does this)
2. Get M7 day type prediction (already does this)
3. Return flat dict without `checked` fields — just data
4. Derive direction from swing points instead of `macro_bias`

```python
def get_indicators(self, symbol: str | None = None) -> dict:
    """Return live indicator data (orderflow + ML predictions).

    No gate logic — just computes and returns signal data.
    Direction for delta_aligned derived from swing point structure.
    """
    symbol = symbol or "NQ"

    # Get session for context
    session_row = self.repo.get_latest_session(symbol)
    if not session_row:
        session_row = self.repo.get_session(
            datetime.now().strftime("%Y-%m-%d"), symbol
        )
    session_data = {}
    if session_row and session_row.session_json:
        session_data = json.loads(session_row.session_json) if isinstance(
            session_row.session_json, str) else session_row.session_json

    # Bars are NOT stored in session_json — must fetch from cache
    bars = await self._fetch_bars_for_date(symbol, session_row.date if session_row else None)

    # Compute live orderflow — direction from structure, not manual gates
    from src.market_data.levels import detect_swing_points
    structure = detect_swing_points(bars, lookback=5)
    struct_class = structure.get("structure", "ranging")

    if struct_class == "uptrend":
        direction = "long"
    elif struct_class == "downtrend":
        direction = "short"
    else:
        direction = None

    of_signals = self._compute_live_orderflow(symbol, session_data, direction=direction)

    # M7 day type prediction (existing logic)
    ml_day_type = None
    ml_day_type_confidence = None
    try:
        from src.ml.serving.predictor import get_predictor
        from src.ml.models.gate_classifier import DAY_TYPE_LABELS
        predictor = get_predictor()
        if predictor.is_loaded("gate_classifier"):
            gate_features = self._build_gate_features(session_data, session_row)
            if gate_features:
                pred = predictor.predict("gate_classifier", gate_features)
                if pred and "class" in pred:
                    ml_day_type = DAY_TYPE_LABELS.get(pred["class"], "unknown")
                    probs = pred.get("probabilities", [])
                    ml_day_type_confidence = round(max(probs) * 100, 1) if probs else None
    except Exception as e:
        logger.debug("M7 prediction skipped: %s", e)

    # Return flat indicator data
    return {
        "orderflow": {
            "delta": of_signals.delta,
            "delta_aligned": of_signals.delta_aligned,
            "delta_divergence": of_signals.delta_divergence,
            "delta_unwind": of_signals.delta_unwind,
            "cvd": of_signals.cvd,
            "cvd_trend": of_signals.cvd_trend,
            "vsa_absorption": of_signals.vsa_absorption,
            "tick_vol_accelerating": of_signals.tick_vol_accelerating,
            "trapped_traders": of_signals.trapped_traders,
            "passive_active_ratio": of_signals.passive_active_ratio,
            "big_trades_count": of_signals.big_trades_count,
            "big_trades_net_delta": of_signals.big_trades_net_delta,
            "stop_run_detected": of_signals.stop_run_detected,
            "imbalance_ratio_max": of_signals.imbalance_ratio_max,
            "stacked_imbalance_count": of_signals.stacked_imbalance_count,
            "stacked_imbalance_direction": of_signals.stacked_imbalance_direction,
        },
        "ml_day_type": ml_day_type,
        "ml_day_type_confidence": ml_day_type_confidence,
    }
```

- [ ] **Step 2: Update _compute_live_orderflow to accept direction parameter**

In `market_service.py`, find `_compute_live_orderflow` (line ~704). Add optional `direction` parameter:

```python
def _compute_live_orderflow(self, symbol: str, session_data: dict, direction: str | None = None) -> "OrderflowSignals":
```

Update the part that currently reads `macro_bias` from context to use the passed `direction` parameter instead. If `direction` is None, pass `"long"` as default (compute_signals requires a direction).

- [ ] **Step 3: Update route in market.py**

In `backend/src/api/routes/market.py`, find the confirmations endpoint (line ~79). Add a new `/indicators` route and keep the old `/confirmations` as an alias:

```python
@router.get("/indicators")
async def get_indicators(svc: MarketService = Depends(get_market_service)):
    """Live orderflow indicators + ML predictions."""
    return svc.get_indicators()


# Keep old endpoint as alias for backwards compatibility during transition
@router.get("/confirmations")
async def get_confirmations(svc: MarketService = Depends(get_market_service)):
    """Deprecated: use /indicators instead."""
    return svc.get_indicators()
```

Replace the existing `GET /session` endpoint (line ~22 of market.py) **in-place** with the expanded version:

```python
@router.get("/session")
async def get_session(svc: MarketService = Depends(get_market_service)):
    """Expanded session data with all analytical layers.
    Replaces old flat session response — now returns nested structure
    with macro, structure, profiles, levels, price_position.
    """
    result = await svc.build_expanded_session()
    if not result:
        return {"error": "No session computed yet. Run Compute first."}
    return result
```

**IMPORTANT:** Remove the old `get_current_session` handler at line ~22. Do NOT add a second `/session` route — FastAPI would use the first one registered.

- [ ] **Step 4: Update context PUT to handle date strings**

In `backend/src/api/routes/market.py`, find the context PUT endpoint (line ~173). Add conversion from ISO date string to Unix timestamp:

```python
@router.put("/context")
async def update_context(data: dict, symbol: str = "NQ", svc: MarketService = Depends(get_market_service)):
    """Update market context — now only VP anchor dates."""
    from datetime import datetime
    # Convert ISO date strings to Unix timestamps for DB storage
    for field in ["vp_leg_start", "vp_ongoing_macro_start"]:
        if field in data and isinstance(data[field], str):
            dt = datetime.strptime(data[field], "%Y-%m-%d")
            data[field] = int(dt.timestamp())
    svc.repo.upsert_context(symbol, data)
    return {"status": "ok", "symbol": symbol}
```

- [ ] **Step 5: Verify imports**

Run: `cd backend && python -c "from src.api.routes.market import router; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/market_service.py backend/src/api/routes/market.py
git commit -m "feat(trading): replace gate confirmations with flat indicators endpoint"
```

---

### Task 6: Update run_scan to use auto-detected direction

**Files:**
- Modify: `backend/src/services/market_service.py:248-380` (run_scan method)

- [ ] **Step 1: Update run_scan to derive direction from swing points**

In `run_scan()`, find where it reads `ctx_model.macro_bias` to determine trade direction. Replace with:

```python
# Auto-detect direction from price action structure (replaces manual macro_bias)
from src.market_data.levels import detect_swing_points
bars = session_data.get("bars", [])
structure = detect_swing_points(bars, lookback=5)
struct_class = structure.get("structure", "ranging")

if struct_class == "uptrend":
    direction = "long"
elif struct_class == "downtrend":
    direction = "short"
else:
    direction = "long"  # Default to long when ranging
```

Pass this `direction` to `_compute_live_orderflow()` and to the scanner.

- [ ] **Step 2: Update _run_setup_detectors to use swing-point direction**

`_run_setup_detectors()` also reads `ctx_model.macro_bias` to determine direction. Update it to accept a `direction` parameter instead:

```python
# In _run_setup_detectors, replace macro_bias lookup with parameter:
def _run_setup_detectors(self, session_data, direction: str | None = None, ...):
    # direction comes from swing point detection, not manual gates
```

Pass the same `direction` from Task 6 Step 1 into `_run_setup_detectors()`.

- [ ] **Step 3: Remove gate checks from run_scan**

Find any code in `run_scan()` that checks `gates_set`, `layerAReady`, or similar gate conditions. Remove these checks — signals should always be generated regardless of gate state.

- [ ] **Step 3: Verify scan still works**

Run: `cd backend && python -c "from src.services.market_service import MarketService; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "feat(trading): remove gate checks from scanner, use auto-detected direction"
```

---

## Chunk 3: Frontend — Types and API Updates

### Task 7: Update TypeScript types

**Files:**
- Modify: `frontend/src/types/market.ts`

- [ ] **Step 1: Add new interfaces for expanded session response**

In `frontend/src/types/market.ts`, add/replace interfaces:

```typescript
// Swing point structure from detect_swing_points()
export interface PriceStructure {
  classification: 'uptrend' | 'downtrend' | 'ranging';
  last_hh: number | null;
  last_hl: number | null;
  last_lh: number | null;
  last_ll: number | null;
  swing_high: number | null;
  swing_low: number | null;
}

// Multi-timeframe volume profile data
export interface VPLevel {
  poc: number;
  vah: number;
  val: number;
  anchor?: string;  // ISO date for leg/macro
}

export interface NakedPOC {
  date: string;
  price: number;
}

export interface ProfilesData {
  session: VPLevel;
  weekly?: VPLevel | null;
  leg?: VPLevel | null;
  macro?: VPLevel | null;
  developing_poc: number | null;
  developing_poc_direction: 'up' | 'down' | 'flat';
  naked_pocs: NakedPOC[];
}

// Structural level from MarketLevel table
export interface StructuralLevel {
  type: string;
  price_low: number;
  price_high: number;
  direction: string | null;
  session?: string;
  is_filled?: boolean;
}

// Price position relative to key levels
export interface PricePosition {
  last_price: number | null;
  vs_va: 'above' | 'within' | 'below';
  vs_vwap: string;
  vs_ib: 'above' | 'within' | 'below';
  vwap_deviation_sd?: number;
}

// Expanded session response (replaces old MarketSession for dashboard)
export interface ExpandedSession {
  session: MarketSession;
  macro: MacroSnapshot & {
    cot_net_position?: number | null;
    cot_change_1w?: number | null;
    gex?: number | null;
    put_call_ratio?: number | null;
    es_nq_ratio_change?: number | null;
  };
  structure: PriceStructure;
  profiles: ProfilesData;
  levels: StructuralLevel[];
  price_position: PricePosition;
  ml_day_type: string | null;
  ml_day_type_confidence: number | null;
}

// Orderflow indicator data (replaces ConfirmationCard for orderflow)
export interface OrderflowIndicators {
  delta: number | null;
  delta_aligned: boolean;
  delta_divergence: boolean;
  delta_unwind: boolean;
  cvd: number | null;
  cvd_trend: 'rising' | 'falling' | 'flat';
  vsa_absorption: boolean;
  tick_vol_accelerating: boolean;
  trapped_traders: boolean;
  passive_active_ratio: number | null;
  big_trades_count: number;
  big_trades_net_delta: number;
  stop_run_detected: boolean;
  imbalance_ratio_max: number | null;
  stacked_imbalance_count: number;
  stacked_imbalance_direction: 'buy' | 'sell' | 'neutral';
}

// Indicators response (replaces ConfirmationState)
export interface IndicatorsResponse {
  orderflow: OrderflowIndicators;
  ml_day_type: string | null;
  ml_day_type_confidence: number | null;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or only pre-existing ones)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/market.ts
git commit -m "feat(trading): add TypeScript types for expanded dashboard response"
```

---

### Task 8: Update API service

**Files:**
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add new API methods**

In `frontend/src/services/api.ts`, add new methods and update existing ones:

```typescript
// New: get expanded session (replaces getMarketSession for dashboard)
async getExpandedSession(): Promise<ExpandedSession> {
  const res = await this.get('/trading/market/session');
  return res;
},

// New: get live indicators (replaces getConfirmations)
async getIndicators(): Promise<IndicatorsResponse> {
  const res = await this.get('/trading/market/indicators');
  return res;
},

// Updated: simplified context update (only VP anchors)
async updateVPAnchors(data: { vp_leg_start?: string; vp_ongoing_macro_start?: string }, symbol: string = 'NQ'): Promise<void> {
  await this.put(`/trading/market/context?symbol=${symbol}`, data);
},
```

Keep existing methods (`getConfirmations`, `getMarketSession`, etc.) for backwards compatibility with other pages.

- [ ] **Step 2: Add imports for new types**

Add `ExpandedSession`, `IndicatorsResponse` to the import from `../types/market`.

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services/api.ts
git commit -m "feat(trading): add API methods for expanded session and indicators"
```

---

## Chunk 4: Frontend — Page Rewrite

### Task 9: Rewrite TradingIntradayPage — Section Components

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` (full rewrite)

This is the largest task. The page goes from ~805 lines with gate logic to a flat indicator dashboard.

- [ ] **Step 1: Replace Layer A/B gate logic with data fetching**

Remove all gate state management (lines ~98-140 of current file):
- Remove `context`, `overrides`, `layerACount`, `layerAReady`, `layerBCount`, `layerBReady`, `gatesPassed`
- Replace with simple data state:

```typescript
const [session, setSession] = useState<ExpandedSession | null>(null);
const [indicators, setIndicators] = useState<IndicatorsResponse | null>(null);
const [signals, setSignals] = useState<TradingSignal[]>([]);
const [loading, setLoading] = useState(false);
const [threshold, setThreshold] = useState(70);
```

- [ ] **Step 2: Update data fetching**

Replace gate-related fetching with:

```typescript
const fetchSession = async () => {
  try {
    const data = await api.getExpandedSession();
    setSession(data);
  } catch (e) { console.error('Session fetch failed:', e); }
};

const fetchIndicators = async () => {
  try {
    const data = await api.getIndicators();
    setIndicators(data);
  } catch (e) { console.error('Indicators fetch failed:', e); }
};

// Auto-refresh indicators every 30s
useEffect(() => {
  fetchIndicators();
  const interval = setInterval(fetchIndicators, 30000);
  return () => clearInterval(interval);
}, []);
```

- [ ] **Step 3: Build Section 1 — Header Bar**

```tsx
{/* Header */}
<div className="flex items-center justify-between mb-3">
  <div className="flex items-center gap-2">
    <span className="text-cyan-400 font-bold text-sm">NQ</span>
    {session?.price_position?.last_price && (
      <span className="text-zinc-300">{session.price_position.last_price.toLocaleString()}</span>
    )}
  </div>
  <div className="flex items-center gap-2">
    <button onClick={handleCompute} className="px-3 py-1 text-xs border border-zinc-700 rounded text-cyan-400 hover:bg-zinc-800">
      Compute
    </button>
    <button onClick={handleScan} className="px-3 py-1 text-xs border border-zinc-700 rounded text-cyan-400 hover:bg-zinc-800">
      Scan
    </button>
    <span className="text-zinc-500 text-xs">Thr:</span>
    <input type="range" min={30} max={95} value={threshold} onChange={e => setThreshold(+e.target.value)}
      className="w-20 accent-cyan-500" />
    <span className="text-zinc-300 text-xs w-6">{threshold}</span>
  </div>
</div>
```

- [ ] **Step 4: Build Section 2 — Macro Context Strip**

```tsx
{/* Macro Context */}
{session?.macro && (
  <div className="bg-zinc-900/50 border border-zinc-800 rounded-md p-2.5 mb-2">
    <div className="flex flex-wrap gap-2 items-center text-xs">
      <Pill label="Regime" value={session.macro.regime?.replace('_', ' ')}
        color={session.macro.regime === 'risk_on' ? 'green' : session.macro.regime === 'risk_off' ? 'red' : 'yellow'} />
      <Sep />
      <Pill label="VIX" value={session.macro.vix?.toFixed(1)}
        color={(session.macro.vix ?? 20) < 18 ? 'green' : (session.macro.vix ?? 20) > 25 ? 'red' : 'yellow'} />
      <Sep />
      <Pill label="DXY" value={session.macro.dxy?.toFixed(1)} color="zinc" />
      <Sep />
      <Pill label="10Y" value={`${session.macro.us10y?.toFixed(2)} (${session.macro.us10y_change_bps > 0 ? '+' : ''}${session.macro.us10y_change_bps}bp)`} color="zinc" />
      <Sep />
      <Pill label="COT" value={session.macro.cot_net_position != null ? `${session.macro.cot_net_position > 0 ? '+' : ''}${session.macro.cot_net_position.toLocaleString()}` : 'N/A'}
        color={session.macro.cot_net_position != null && session.macro.cot_net_position > 0 ? 'green' : 'red'} />
    </div>
  </div>
)}
```

Helper components (define at bottom of file or in a shared section):

```tsx
const Pill = ({ label, value, color }: { label: string; value: string | undefined; color: string }) => {
  const colors: Record<string, string> = {
    green: 'bg-green-900/50 text-green-400',
    red: 'bg-red-900/50 text-red-400',
    yellow: 'bg-yellow-900/50 text-yellow-400',
    cyan: 'bg-cyan-900/50 text-cyan-400',
    purple: 'bg-purple-900/50 text-purple-400',
    zinc: 'text-zinc-300',
  };
  return (
    <div className="flex items-center gap-1">
      <span className="text-zinc-500 text-[10px] uppercase">{label}</span>
      <span className={`${colors[color] ?? colors.zinc} px-1.5 py-0.5 rounded text-[11px]`}>{value ?? 'N/A'}</span>
    </div>
  );
};

const Sep = () => <div className="w-px h-4 bg-zinc-700" />;
```

- [ ] **Step 5: Build Sections 3-8 following same pattern**

Each section follows the same pattern as Section 2:
- Container div with `bg-zinc-900/50 border border-zinc-800 rounded-md p-2.5 mb-2`
- Flex/grid layout with Pill components and Sep dividers
- Data from `session` (Sections 3-7) or `indicators` (Section 8)

**Section 3 (Session Profile):** Display market_type, ml_day_type, opening_type, IB range, RF, ASPR, distribution_type, value_migration, poor_high/low, single_prints count, TPO POC/VAH/VAL, overnight H/L.

**Section 4 (Price Structure):** Display structure classification, last HH/HL/LH/LL, swing high/low, price vs VA/VWAP/IB.

**Section 5 (Multi-TF VP):** Grid with Profile/Anchor/VAL/POC/VAH columns. Session row always shown. Weekly/Leg/Macro rows shown if data exists. Leg/Macro anchor dates as `<input type="date">` fields that call `api.updateVPAnchors()`. Developing POC and naked POCs below the grid.

**Section 6 (VWAP Bands):** Display all 7 VWAP levels in a compact row with current deviation highlighted.

**Section 7 (Structural Levels):** Flex-wrap of all level types from `session.levels` with color by type.

**Section 8 (Orderflow):** Existing orderflow strip (lines 444-477 of current file) — keep this almost unchanged, but read from `indicators.orderflow` instead of `confirmations.orderflow`.

- [ ] **Step 6: Keep signal table (Section 9) largely unchanged**

The existing signal table (lines 543-719) stays mostly the same. Only change:
- Remove the gate check (`if (!gatesPassed) return <message>`)
- Always render the table
- Data comes from `signals` state (populated by scan)

- [ ] **Step 7: Build Section 10 — Level Map (collapsible)**

```tsx
{/* Level Map */}
<details className="mt-2">
  <summary className="text-zinc-500 text-xs uppercase cursor-pointer hover:text-zinc-300">
    Level Map ({allLevels.length} levels)
  </summary>
  <div className="mt-2 bg-zinc-900/50 border border-zinc-800 rounded-md p-2.5 text-xs font-mono">
    {allLevels.map((lv, i) => (
      <div key={i} className={`flex justify-between py-0.5 ${lv.isPrice ? 'text-cyan-400 font-bold' : 'text-zinc-400'}`}>
        <span>{lv.price.toLocaleString()}</span>
        <span className="text-zinc-500">{lv.labels.join(' │ ')}</span>
      </div>
    ))}
  </div>
</details>
```

Build `allLevels` by aggregating:
- Session/Weekly/Leg/Macro POC/VAH/VAL from `session.profiles`
- VWAP + SD bands from `session.session`
- Structural levels from `session.levels`
- IB H/L, PDH/PDL, Tokyo/London from `session.session`
- Current price marker
- Sort by price descending

- [ ] **Step 8: Verify TypeScript compiles and page renders**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat(trading): rewrite intraday page as 10-section indicator dashboard"
```

---

### Task 10: Clean up old gate types and dead code

**Files:**
- Modify: `frontend/src/types/market.ts` (deprecate old types)
- Modify: `frontend/src/services/api.ts` (mark old methods deprecated)

- [ ] **Step 1: Add deprecation comments to old types**

Mark `ConfirmationState`, `ConfirmationCard` as `@deprecated` in JSDoc comments. Don't delete them yet — other code may reference them.

- [ ] **Step 2: Remove unused MarketContext fields from context update calls**

If no other page uses `macro_bias`, `structure`, `day_type` context fields, the old PUT /context handler can be simplified. Check other pages first.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/services/api.ts
git commit -m "chore(trading): deprecate old gate types, clean up dead code"
```

---

## Chunk 5: Integration Testing

### Task 11: End-to-end verification

**Files:**
- No new files — manual verification

- [ ] **Step 1: Start backend**

Run: `cd backend && python -m src.app serve`
Expected: Server starts on :8000

- [ ] **Step 2: Test expanded session endpoint**

Run: `curl -s http://localhost:8000/api/trading/market/session | python -m json.tool | head -50`
Expected: Nested JSON with `session`, `macro`, `structure`, `profiles`, `levels`, `price_position` keys

- [ ] **Step 3: Test indicators endpoint**

Run: `curl -s http://localhost:8000/api/trading/market/indicators | python -m json.tool`
Expected: JSON with `orderflow` object and `ml_day_type`

- [ ] **Step 4: Test VP anchor update**

Run: `curl -s -X PUT "http://localhost:8000/api/trading/market/context?symbol=NQ" -H "Content-Type: application/json" -d '{"vp_leg_start": "2026-03-10"}'`
Expected: `{"status": "ok", "symbol": "NQ"}`

- [ ] **Step 5: Start frontend and verify page renders**

Run: `cd frontend && npm run dev`
Open: http://localhost:5173 → navigate to Intraday tab
Expected: 10-section layout with indicator pills, no gate checkboxes, signal table visible

- [ ] **Step 6: Click Compute and verify data populates**

Click Compute button. Expected: All sections populate with session data.

- [ ] **Step 7: Click Scan and verify signals appear**

Click Scan button. Expected: Signal table shows scored setups.

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "feat(trading): complete dashboard redesign — all 10 sections working"
```
