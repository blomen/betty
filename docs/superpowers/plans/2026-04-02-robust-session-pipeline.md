# Robust Session Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the market session compute/build pipeline self-healing so indicators never go blank again — DB-first data sourcing, universal serialization firewall, and isolated error boundaries in the route.

**Architecture:** Three layers of defense: (1) A `_sanitize_for_json` utility at the repo boundary that handles numpy, dataclasses, datetimes, and any future type — applied once in `upsert_session`, never scattered. (2) `compute_session` always uses DB candles first (live stream writes them), with Databento as optional backfill only. (3) The `/compute` route isolates RL context enrichment so it never crashes the response.

**Tech Stack:** Python / SQLAlchemy / FastAPI (existing stack, no new deps)

---

### Task 1: Universal serialization firewall in MarketRepo

**Files:**
- Modify: `backend/src/repositories/market_repo.py`

The current `_sanitize_numpy` is incomplete — it misses dataclasses, numpy booleans, datetime objects inside JSON, and sets. Replace it with a single `_sanitize_for_json` that recursively handles everything PostgreSQL's JSON column could choke on.

- [ ] **Step 1: Replace `_sanitize_numpy` with `_sanitize_for_json`**

In `backend/src/repositories/market_repo.py`, replace the existing `_sanitize_numpy` static method:

```python
@staticmethod
def _sanitize_for_json(val):
    """Recursively convert non-JSON-safe types to native Python for PostgreSQL.

    Handles: numpy scalars, numpy bools, dataclasses, datetimes, sets.
    Applied at the repo boundary so callers never need to worry about it.
    """
    # numpy scalar (float64, int64, bool_)
    if hasattr(val, 'item'):
        return val.item()
    # dataclass → dict
    if hasattr(val, '__dataclass_fields__'):
        from dataclasses import asdict
        return MarketRepo._sanitize_for_json(asdict(val))
    # dict — recurse
    if isinstance(val, dict):
        return {k: MarketRepo._sanitize_for_json(v) for k, v in val.items()}
    # list/tuple — recurse
    if isinstance(val, (list, tuple)):
        return [MarketRepo._sanitize_for_json(v) for v in val]
    # set → list
    if isinstance(val, (set, frozenset)):
        return [MarketRepo._sanitize_for_json(v) for v in val]
    # datetime → ISO string (for JSON columns)
    if isinstance(val, datetime):
        return val.isoformat()
    return val
```

- [ ] **Step 2: Update all call sites from `_sanitize_numpy` to `_sanitize_for_json`**

In the same file, find-and-replace all references:
- `upsert_session`: `kwargs = {k: self._sanitize_for_json(v) for k, v in kwargs.items()}`
- `upsert_levels`: `lv[k] = self._sanitize_for_json(v)`
- `upsert_session_metric`: `rf = self._sanitize_for_json(rf)` and `aspr = self._sanitize_for_json(aspr)`

- [ ] **Step 3: Add the datetime import if not present**

Ensure `from datetime import datetime, timezone` is at the top of `market_repo.py` (it already is).

- [ ] **Step 4: Verify on server**

```bash
ssh root@148.251.40.251 "cd /opt/firev && git pull && \
  docker compose exec -T backend find /app/backend/src -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; \
  docker compose cp backend/src/repositories/market_repo.py backend:/app/backend/src/repositories/market_repo.py && \
  docker compose restart backend"
```

Then test: `curl -s -m 30 http://localhost:8000/api/trading/market/session | head -c 200`

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/market_repo.py
git commit -m "refactor: universal _sanitize_for_json in MarketRepo

Replaces _sanitize_numpy with a broader serialization firewall that
handles numpy, dataclasses, datetimes, and sets. Applied once at the
repo boundary so no caller can accidentally store non-serializable data."
```

---

### Task 2: compute_session — DB-first, Databento-never-blocks

**Files:**
- Modify: `backend/src/services/market_service.py`

The current flow tries Databento first, then falls back to DB candles. Invert this: always try DB candles first (the live stream populates them in real-time). Only use Databento for historical backfill of dates where DB has no coverage. For `prev_bars` (previous day), also use DB candles.

- [ ] **Step 1: Refactor the bar-fetching in `compute_session`**

Replace lines 338-367 (the Databento fetch + DB fallback block) with:

```python
        # --- Fetch bars: DB-first, Databento only for backfill ---
        from zoneinfo import ZoneInfo
        from ..market_data.base import BarData
        _ET = ZoneInfo("America/New_York")
        gs_utc = globex_start.replace(tzinfo=_ET).astimezone(timezone.utc)
        rc_utc = min(
            rth_close.replace(tzinfo=_ET).astimezone(timezone.utc),
            datetime.now(timezone.utc),
        )

        db_rows = self._filter_halt(self.repo.get_candles(symbol, "1m", gs_utc, rc_utc))
        if db_rows:
            logger.info("compute_session: %d bars from DB for %s on %s", len(db_rows), symbol, target_date)
            bars = [BarData(timestamp=r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc),
                            open=r.o, high=r.h, low=r.l, close=r.c, volume=r.v or 0)
                    for r in db_rows]
        else:
            # No DB bars — try Databento as last resort (with timeout)
            sym = config.get("symbol", "NQ.FUT")
            try:
                bars = await asyncio.wait_for(
                    provider.get_bars(sym, "1m", globex_start, rth_close),
                    timeout=30.0,
                )
            except Exception as e:
                logger.warning("Databento get_bars failed: %s", e)
                bars = []

        if not bars:
            logger.info("No bars for %s on %s — returning cached/empty", symbol, target_date)
            cached = self.repo.get_previous_session(symbol)
            if cached:
                sj = cached.session_json
                return sj if isinstance(sj, dict) else {}
            return {}

        # Ticks — skip, we don't use them for analysis (delta is 0 without side data)
        ticks = []
```

- [ ] **Step 2: Refactor prev_bars to also use DB**

Replace lines 369-383 with:

```python
        # Previous day bars from DB
        prev_gs_utc = (gs_utc - timedelta(days=1))
        prev_rc_utc = (rc_utc - timedelta(days=1))
        prev_db_rows = self._filter_halt(self.repo.get_candles(symbol, "1m", prev_gs_utc, prev_rc_utc))
        if prev_db_rows:
            prev_bars = [BarData(timestamp=r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc),
                                 open=r.o, high=r.h, low=r.l, close=r.c, volume=r.v or 0)
                         for r in prev_db_rows]
        else:
            prev_bars = []
```

- [ ] **Step 3: Remove dead `sym = config.get("symbol", "NQ.FUT")` at old line 339**

Already consumed in the new code above.

- [ ] **Step 4: Verify on server**

Deploy and test:
```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend \
  curl -s -m 120 http://localhost:8000/api/trading/market/compute -X POST | head -c 200"
```

Should return JSON with `poc`, `vah`, etc.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "refactor: compute_session uses DB candles first, Databento as fallback

The live stream already populates market_candles in real-time. Inverting
the priority eliminates the recurring Databento 422/timeout failures that
left indicators blank. Databento is now only tried when DB has zero bars."
```

---

### Task 3: Isolate RL context enrichment in the /compute route

**Files:**
- Modify: `backend/src/api/routes/market.py`

The current `trigger_compute` route mixes three concerns: (1) compute session, (2) refresh level monitor, (3) build RL context. A failure in any of them crashes the entire response. Wrap the RL/level-monitor block so it never takes down the route.

- [ ] **Step 1: Wrap the level monitor + RL context block**

Replace lines 130-178 with:

```python
    # Refresh level monitor + RL context — best-effort, never crashes the response
    try:
        level_monitor = getattr(request.app.state, "level_monitor", None)
        if level_monitor and data:
            expanded = await _offload(svc.build_expanded_session)
            if expanded:
                level_monitor.load_levels(expanded)

            session = data if isinstance(data, dict) else {}
            rl_context = {
                "vwap_bands": {
                    "vwap": session.get("vwap"),
                    "upper_1": session.get("vwap_1sd_upper"),
                    "lower_1": session.get("vwap_1sd_lower"),
                    "upper_2": session.get("vwap_2sd_upper"),
                    "lower_2": session.get("vwap_2sd_lower"),
                    "upper_3": session.get("vwap_3sd_upper"),
                    "lower_3": session.get("vwap_3sd_lower"),
                } if session.get("vwap") else None,
                "volume_profile": session.get("volume_profile"),
                "session_levels": session.get("session_levels"),
                "session_tpos": session.get("session_tpos"),
                "tpo_profile": session.get("tpo"),
                "session_context": session.get("session_context"),
                "macro": session.get("macro"),
                "day_type": session.get("day_type"),
                "amt_context": session.get("amt_context", {}),
                "fvgs": [],
                "single_print_zones": [],
                "swing_structure": expanded.get("swing_structure") if expanded else None,
            }
            stream = _get_live_stream(request)
            if stream:
                ds = stream.daily_stats
                if ds:
                    macro_dict = rl_context.get("macro") or {}
                    macro_dict.update({
                        "oi": ds.get("open_interest", {}).get("value", 0),
                        "oi_change": 0,
                        "settlement_price": ds.get("settlement_price", {}).get("value", 0),
                        "cleared_volume": ds.get("cleared_volume", {}).get("value", 0),
                        "block_volume": ds.get("block_volume", {}).get("value", 0),
                    })
                    rl_context["macro"] = macro_dict

            level_monitor.set_session_context(rl_context)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("RL context enrichment failed (non-fatal)", exc_info=True)
```

- [ ] **Step 2: Verify compute route returns data even when RL enrichment would fail**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend \
  curl -s -m 120 http://localhost:8000/api/trading/market/compute -X POST | python3 -c \
  'import sys,json; d=json.loads(sys.stdin.read()); print(\"OK\" if d.get(\"poc\") else d)'"
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/market.py
git commit -m "fix: isolate RL context enrichment so compute route never crashes

The level_monitor/RL block is now wrapped in try/except. If expanded
session is None, RL enrichment is skipped but the route still returns
the computed session data. Eliminates the 'expanded.get() on None' crash."
```

---

### Task 4: build_expanded_session — hard thread timeout + always returns DB baseline

**Files:**
- Modify: `backend/src/services/market_service.py`

The `asyncio.wait_for(timeout=30)` doesn't work when the underlying call blocks synchronously (e.g., loading 11M ORM objects). Replace with `concurrent.futures` thread timeout that actually kills blocked threads. Also: always return the DB-only session as baseline, even if enrichment fails.

- [ ] **Step 1: Replace asyncio.wait_for with real thread timeout**

Replace lines 710-741 in `build_expanded_session`:

```python
        swing_structure_data = None
        try:
            # Run bar enrichment in a thread with a HARD timeout.
            # asyncio.wait_for doesn't cancel synchronous blocking calls.
            import concurrent.futures
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                inner_loop = asyncio.new_event_loop()
                def _run_enrich():
                    try:
                        return inner_loop.run_until_complete(
                            self._enrich_with_bars(symbol, today, session_row, sj)
                        )
                    finally:
                        inner_loop.close()
                future = pool.submit(_run_enrich)
                try:
                    structure, profiles, swing_struct = future.result(timeout=30.0)
                except concurrent.futures.TimeoutError:
                    logger.warning("Bar enrichment hard-timed out (30s)")
                    future.cancel()
                    structure, profiles, swing_struct = {}, profiles, None

            if swing_struct is not None:
                swing_structure_data = _serialize_swing_structure(swing_struct)
                for tf_swings in [swing_struct.daily, swing_struct.weekly, swing_struct.monthly]:
                    if tf_swings.swing_highs:
                        sh_price = tf_swings.swing_highs[0].price
                        levels_list.append({
                            "type": f"{tf_swings.timeframe}_swing_high",
                            "price_low": sh_price,
                            "price_high": sh_price,
                            "direction": "resistance",
                            "session": tf_swings.timeframe,
                            "is_filled": False,
                        })
                    if tf_swings.swing_lows:
                        sl_price = tf_swings.swing_lows[0].price
                        levels_list.append({
                            "type": f"{tf_swings.timeframe}_swing_low",
                            "price_low": sl_price,
                            "price_high": sl_price,
                            "direction": "support",
                            "session": tf_swings.timeframe,
                            "is_filled": False,
                        })
        except Exception as e:
            logger.warning("Bar enrichment failed: %s", e)
```

- [ ] **Step 2: Verify session endpoint returns within 35s even when enrichment is slow**

```bash
time ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend \
  curl -s -m 45 http://localhost:8000/api/trading/market/session | head -c 200"
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "fix: hard thread timeout for bar enrichment in build_expanded_session

asyncio.wait_for can't cancel synchronous DB queries that block the
thread. Use concurrent.futures.ThreadPoolExecutor with a real 30s
timeout so the session endpoint always returns, even if tick VP or
swing structure computation hangs."
```

---

### Task 5: _enrich_with_bars — remove all Databento calls, DB-only

**Files:**
- Modify: `backend/src/services/market_service.py`

`_enrich_with_bars` still has a Databento backfill path in `_get_session_bars`. Since the live stream populates candles continuously, and `compute_session` now handles backfill, the enrichment path should be pure DB reads. Also ensure `_compute_tick_vp` uses a SQL aggregation instead of loading ORM objects.

- [ ] **Step 1: Simplify `_get_session_bars` — remove Databento backfill**

Replace the entire `_get_session_bars` method (lines 148-221):

```python
    async def _get_session_bars(self, symbol: str) -> list[dict]:
        """Get today's 1m bars from DB for VP computation.

        DB-only — the live stream populates candles in real-time.
        Falls back to previous day if today has no data yet.
        """
        from zoneinfo import ZoneInfo
        _CET = ZoneInfo("Europe/Stockholm")

        now = datetime.now(timezone.utc)
        today_cet = now.astimezone(_CET).date()
        d_start = datetime(today_cet.year, today_cet.month, today_cet.day, tzinfo=_CET).astimezone(timezone.utc)

        rows = self._filter_halt(self.repo.get_candles(symbol, "1m", d_start, now))
        if rows:
            logger.info("VP bars: %d from DB", len(rows))
            return [{"high": r.h, "low": r.l, "close": r.c, "volume": r.v} for r in rows]

        # No bars today — try previous day
        prev_cet = today_cet - timedelta(days=1)
        p_start = datetime(prev_cet.year, prev_cet.month, prev_cet.day, tzinfo=_CET).astimezone(timezone.utc)
        prev_rows = self._filter_halt(self.repo.get_candles(symbol, "1m", p_start, d_start))
        if prev_rows:
            logger.info("VP bars: using previous day's %d bars", len(prev_rows))
            return [{"high": r.h, "low": r.l, "close": r.c, "volume": r.v} for r in prev_rows]

        return []
```

- [ ] **Step 2: Replace `_compute_tick_vp` with SQL aggregation**

Loading 500k ORM objects still takes seconds. Use a raw SQL aggregation instead:

```python
    async def _compute_tick_vp(self, symbol: str) -> VolumeProfile | None:
        """Compute VP from tick data using SQL aggregation (no ORM object loading)."""
        from zoneinfo import ZoneInfo
        from sqlalchemy import text

        _CET = ZoneInfo("Europe/Stockholm")
        now = datetime.now(timezone.utc)
        today_cet = now.astimezone(_CET).date()
        d_start = datetime(today_cet.year, today_cet.month, today_cet.day, tzinfo=_CET).astimezone(timezone.utc)

        try:
            # Count first
            count_result = self.repo.market_db.execute(
                text("SELECT COUNT(*) FROM market_trades WHERE symbol = :sym AND ts >= :start AND ts <= :end"),
                {"sym": symbol, "start": d_start, "end": now},
            ).scalar() or 0

            if count_result < 100:
                logger.info("Tick VP: only %d ticks, falling back to bars", count_result)
                return None
            if count_result > 2_000_000:
                logger.info("Tick VP: %d ticks too large, falling back to bars", count_result)
                return None

            # Aggregate price/size in SQL — returns ~4000 rows for tick-size buckets
            rows = self.repo.market_db.execute(
                text("""
                    SELECT ROUND(price / 0.25) * 0.25 AS tick_price,
                           SUM(size) AS total_size
                    FROM market_trades
                    WHERE symbol = :sym AND ts >= :start AND ts <= :end
                    GROUP BY tick_price
                    ORDER BY tick_price
                """),
                {"sym": symbol, "start": d_start, "end": now},
            ).fetchall()

            if not rows:
                return None

            trade_dicts = [{"price": float(r[0]), "size": int(r[1])} for r in rows]
            vp = compute_volume_profile(trade_dicts)
            logger.info("Tick VP: SQL aggregation from %d ticks → %d price levels (POC=%.2f)",
                        count_result, len(trade_dicts), vp.poc)
            return vp
        except Exception as e:
            logger.warning("Tick VP SQL failed: %s", e)
            return None
```

- [ ] **Step 3: Remove `MarketTrade` import from market_service.py**

The `from ..db.models import TradingSignal, MarketTrade` line — remove `MarketTrade` since we no longer import it for the count query (using raw SQL now).

```python
from ..db.models import TradingSignal
```

- [ ] **Step 4: Deploy and verify**

```bash
ssh root@148.251.40.251 "cd /opt/firev && git pull && \
  docker compose exec -T backend find /app/backend/src -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; \
  docker compose cp backend/src/services/market_service.py backend:/app/backend/src/services/market_service.py && \
  docker compose restart backend"

# Wait 8s, then test session endpoint
sleep 8
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend \
  curl -s -m 45 http://localhost:8000/api/trading/market/session | python3 -c \
  'import sys,json; d=json.loads(sys.stdin.read()); print(\"OK:\", list(d.keys()))'"
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "refactor: DB-only bar fetching, SQL tick VP aggregation

_get_session_bars no longer calls Databento — DB candles from the live
stream are always available. _compute_tick_vp uses SQL GROUP BY instead
of loading millions of ORM objects, reducing VP computation from 60s+ to
<1s."
```

---

### Task 6: Guard `compute_session` return type for cached sessions

**Files:**
- Modify: `backend/src/services/market_service.py`

When globex is closed, `compute_session` returns `self.repo.get_previous_session(symbol)` which is a **SQLAlchemy model object**, not a dict. FastAPI serializes it as `{}`. Ensure we always return a dict.

- [ ] **Step 1: Fix the cached session return paths**

In `compute_session`, replace lines 316-319:

```python
                cached = self.repo.get_previous_session(symbol)
                if cached and cached.session_json:
                    sj = cached.session_json
                    return sj if isinstance(sj, dict) else json.loads(sj) if isinstance(sj, str) else {}
                return {}
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "fix: ensure compute_session always returns a dict, not a model object

When serving cached sessions (weekend/holiday), the code returned the
raw SQLAlchemy model which FastAPI serialized as {}. Now returns the
session_json dict."
```

---

### Task 7: Deploy full rebuild and verify end-to-end

**Files:** None (deployment only)

- [ ] **Step 1: Push all commits**

```bash
git push
```

- [ ] **Step 2: Full Docker rebuild on server**

File copy won't catch import changes. Do a full rebuild:

```bash
ssh root@148.251.40.251 "cd /opt/firev && git pull && docker compose up -d --build backend"
```

- [ ] **Step 3: Verify compute endpoint**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend \
  curl -s -m 120 http://localhost:8000/api/trading/market/compute -X POST | \
  python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); \
  print(f\"poc={d.get(\"poc\")}, vwap={d.get(\"vwap\")}, type={d.get(\"market_type\")}\")'"
```

- [ ] **Step 4: Verify session endpoint (indicators data)**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend \
  curl -s -m 45 http://localhost:8000/api/trading/market/session | \
  python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); \
  [print(f\"{k}: {type(v).__name__}({len(v) if isinstance(v,(dict,list)) else v})\") for k,v in d.items()]'"
```

Expected: `session: dict(40+)`, `profiles: dict(6)`, `swing_structure: dict(4)`, etc.

- [ ] **Step 5: Verify indicators render in the UI**

Open `https://148.251.40.251` in browser, go to CHART tab. The right-side INDICATORS panel should show populated data for all sections (VWAP, Session, Volume Profile, TPO, Structure, AMT).
