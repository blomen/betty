# Trading Intraday Dashboard Redesign

**Date:** 2026-03-13
**Status:** Draft
**Approach:** A — Indicator Dashboard + Signal Table

## Problem

The current trading page uses a 2-layer manual gate system (Layer A: user sets macro bias/structure/day type, Layer B: auto-checks 4 confirmations) that blocks signal visibility until gates pass. Issues:

1. **Macro "bull/bear" is not determinable** from daily data — risk on/off is the correct framing
2. **Manual gates add friction** without clear value — the system already computes everything
3. **Missing analytical layers** — no multi-TF volume profiles, no price action structure display, no VWAP band visualization, no TPO distribution, no developing POC, no level map
4. **Doesn't match the betting page pattern** — which shows everything as indicators with no gating

## Solution

Replace the gated 2-layer system with a flat indicator dashboard that auto-computes and displays ALL analytical layers. Signals always visible. Only user inputs: leg/macro VP anchor dates.

## Page Layout — 10 Sections

### Section 1: Header Bar

```
NQ  21,847.50  +0.42%  ▲12.25              [Compute] [Scan] Thr: ═══○ 70
```

- Symbol, last price, change %, point change
- Compute button: fetches market data + runs AMT analysis
- Scan button: runs scanner + setup detectors
- Threshold slider: 30-95 (filters signal table)

**Data source:** Last price from `market_trades` or session data. Buttons trigger existing endpoints.

### Section 2: Macro Context Strip

```
Regime: Risk On │ VIX: 14.2 (-3%) │ DXY: 104.1 (+0.2%) │ 10Y: 4.32 (+2bp)
2Y: 4.65 │ 2s10s: -33bp │ GEX: +1.2B │ COT: +2,386 (+180/wk) │ P/C: 0.82
```

All auto-computed pills with color coding:
- **Green**: risk_on regime, low VIX, positive COT
- **Yellow**: mixed regime, neutral indicators
- **Red**: risk_off regime, high VIX, negative COT

**Data sources (concrete):**
- `regime`, `vix`, `vix_change_pct`, `dxy`, `dxy_change_pct`, `us10y`, `us10y_change_bps`, `us2y`, `yield_curve_spread`, `regime_score` — from `MacroSnapshot` via `fetch_macro_snapshot()` in `amt.py` (already fetched in `compute_session()`)
- `cot_net_position`, `cot_change_1w` — from existing `GET /trading/market/cot` endpoint which fetches CFTC data. Frontend already calls `api.getCotData(2)`. Inline into session response.
- `gex`, `put_call_ratio`, `es_nq_ratio_change` — **NOT currently fetched by any data source**. The M9 `extract_macro_features()` accepts these as parameters but doesn't fetch them. Two options:
  1. **Phase 1 (now):** Show as "N/A" with placeholder pills. These require external data sources (CBOE for options, custom for GEX).
  2. **Phase 2 (later):** Add `fetch_options_data()` to retrieve GEX/P/C from a data provider (SpotGamma, Unusual Whales, or manual input).

**Backend change:** Extend `MacroSnapshot` dataclass with optional `gex`, `put_call_ratio`, `es_nq_ratio_change` fields (default None). Merge COT data into session response. Phase 1 shows what we have, Phase 2 adds external options data.

### Section 3: Session Profile (Market Profile / TPO)

```
Market: Trending Up │ Day: Trend (M7:87%) │ Open: OTD
IB: 21,818-21,860 (42pt) ext +18 │ RF: +4 │ ASPR: 28 (P72)
Distribution: P-Shape │ Value Migration: Up
Poor High: No │ Poor Low: Yes │ Single Prints: 2
TPO POC: 21,822 │ TPO VAH: 21,858 │ TPO VAL: 21,785
Overnight: H 21,810 / L 21,760
```

**Fields displayed:**
- `market_type` — balanced / trending_up / trending_down
- `ml_day_type` + `ml_day_type_confidence` — M7 gate classifier prediction
- `opening_type` — OD / OTD / ORR / OA
- `ib_high`, `ib_low`, `ib_range` — Initial Balance + extension from current price
- `rotation_factor` — directional changes in 30-min brackets
- `aspr`, `aspr_percentile` — Average Sub-Period Range + historical percentile
- `distribution_type` — TPO shape: normal / double / p_shape / b_shape
- `value_migration` — up / down / overlapping vs prior session
- `poor_high`, `poor_low` — thin tail flags
- `single_prints` — count of low-volume gaps
- `tpo_poc`, `tpo_vah`, `tpo_val` — TPO-based profile levels
- `overnight_high`, `overnight_low` — Globex session extremes

**Data source:** Most fields from `SessionAnalysis` / `MarketSession` — already computed. Note: `rotation_factor`, `aspr`, `aspr_percentile` are computed in `compute_session()` and stored on the `MarketSession` DB row but are NOT included in `SessionAnalysis.to_dict()` / `session_json`. The route handler must merge these DB columns into the response alongside `session_json` data.

### Section 4: Price Action Structure

```
Structure: HH/HL (Uptrend)
Last HH: 21,865 │ Last HL: 21,780 │ Last LH: — │ Last LL: —
Swing High: 21,890 │ Swing Low: 21,720
Price vs VA: Above │ Price vs VWAP: +1.2 SD │ Price vs IB: Above
```

**Fields displayed:**
- Market structure classification: HH/HL (uptrend), LH/LL (downtrend), ranging
- Last confirmed swing points (HH, HL, LH, LL)
- Current swing high/low
- Price position relative to: Value Area, VWAP SD bands, Initial Balance

**Data source:**
- `price_vs_va`, `price_vs_vwap`, `price_vs_ib` — already computed in `SessionAnalysis`
- **NEW: Swing point detection** — needs implementation

**Backend change: `detect_swing_points(bars)`**

New function in `amt.py` or `levels.py`:
```python
def detect_swing_points(bars: list[dict], lookback: int = 5) -> dict:
    """Detect HH/HL/LH/LL swing structure from bar data.

    A swing high = bar where high > N bars before and after.
    A swing low = bar where low < N bars before and after.

    Returns:
        {
            "structure": "uptrend" | "downtrend" | "ranging",
            "last_hh": float | None,
            "last_hl": float | None,
            "last_lh": float | None,
            "last_ll": float | None,
            "swing_high": float,
            "swing_low": float,
        }
    """
```

Logic: Scan bars with lookback window. Identify pivot highs (higher than N bars on each side) and pivot lows. Classify structure based on whether successive pivots make HH/HL or LH/LL.

### Section 5: Multi-Timeframe Volume Profiles

```
Profile      Anchor              VAL      POC      VAH
Session      Today RTH           21,780   21,820   21,865
Weekly       Mon-Fri             21,650   21,740   21,830
Leg          Mar 10 [edit]       21,520   21,680   21,810
Macro        Feb 24 [edit]       21,200   21,450   21,720
Developing POC: 21,825 (migrating up from 21,810)
Naked POCs: 21,450 (Feb 28) │ 21,680 (Mar 8)
```

**Profiles:**
- **Session** — today's RTH bars → `compute_volume_profile()` (already exists)
- **Weekly** — Monday through current day → composite VP from this week's 1-min bars
- **Leg** — user-set anchor date to now → VP from that date range
- **Macro** — user-set anchor date to now → VP from that date range (uses daily bars for longer periods)
- **Developing POC** — track POC migration during session by recomputing VP incrementally
- **Naked POCs** — scan prior session POCs that haven't been tested (price never touched them since)

**User inputs:** Leg start date and Macro start date. Stored in existing `MarketContext.vp_leg_start` and `MarketContext.vp_ongoing_macro_start` fields (currently unused). Note: these DB columns are `Column(Integer)` storing Unix timestamps. The API accepts ISO date strings (`"2026-03-10"`) and converts to Unix timestamps for storage. The route handler converts back to ISO strings for the frontend.

**Backend changes:**

**1. No new `compute_composite_profile` function needed.**

The existing `compute_volume_profile(bars)` already accepts any bar list. For weekly/leg/macro profiles, pass it the right date range of bars. The orchestration (fetching + passing bars) happens in `market_service.py`.

**2. `detect_naked_pocs(sessions, current_bars)`**

New function in `levels.py`:
```python
def detect_naked_pocs(
    prior_sessions: list[dict],  # [{date, poc}, ...]
    bars_since: list[dict],      # All bars from oldest session to now
) -> list[dict]:
    """Find POCs from prior sessions that price has never revisited.

    A POC is 'naked' if no bar's low-high range includes that price
    since the session it was computed from.

    Returns: [{date, poc_price}, ...]
    """
```

**3. `compute_developing_poc(bars_so_far)`**

Track POC as bars accumulate during the session. Call `compute_volume_profile()` on progressively longer bar windows. Compare current POC vs POC from 30 min ago to determine migration direction.

**4. Fetch composite bars in `market_service.py`**

In `compute_session()` or a new endpoint, fetch bars for weekly/leg/macro ranges:
- Weekly: fetch 1-min bars for current week from cache/Databento
- Leg: fetch 1-min bars from `vp_leg_start` to now
- Macro: fetch daily bars from `vp_ongoing_macro_start` to now (daily sufficient for wide ranges)

### Section 6: VWAP & Standard Deviation Bands

```
VWAP: 21,820  │ Current: +1.2 SD
+3SD: 21,910 │ +2SD: 21,880 │ +1SD: 21,850
-1SD: 21,790 │ -2SD: 21,760 │ -3SD: 21,730
```

All 7 levels displayed as a compact row. Current price position highlighted (which band it's in).

**Data source:** Already computed — `VWAPBands` in `SessionAnalysis` has all fields: `vwap`, `upper_1sd`, `lower_1sd`, `upper_2sd`, `lower_2sd`, `upper_3sd`, `lower_3sd`.

### Section 7: Structural Levels

```
PDH: 21,890 │ PDL: 21,720 │ Wkly H: 21,905 │ Wkly L: 21,580 │ Mthly H: 21,950
Tokyo: 21,760-21,795 │ London: 21,800-21,840 │ IB: 21,818-21,860
OB ▲: 21,760-21,775 │ OB ▼: 21,880-21,895
FVG ▲: 21,700-21,720 │ FVG ▼: 21,870-21,885
```

**Levels displayed:**
- `pdh`, `pdl` — Prior Day High/Low (already computed)
- `weekly_high`, `weekly_low` — Week-to-date (already computed in `SessionLevels`)
- `monthly_high`, `monthly_low` — Month-to-date (already computed in `SessionLevels`)
- `tokyo_high/low`, `london_high/low` — Session ranges (already computed)
- `ib_high`, `ib_low` — Initial Balance (already computed)
- Order Blocks — bullish/bearish with price range + direction (already computed)
- FVGs — Fair Value Gaps with price range + direction (already computed)

**Data source:** `MarketLevel` table + `SessionLevels` — all already computed and stored.

### Section 8: Orderflow Signals

```
Delta: +842 │ Aligned ✓ │ Divergence ✗ │ Unwind ✗
CVD: +3,240 Rising │ VSA Absorption ✓
TickVol: Accelerating │ Trapped ✗ │ StopRun ✓
Big Trades: x3 net +240 │ P/A Ratio: 1.2
Footprint: ImbR 89% │ Stacked Imb x5 ▲Buy
```

All 16 `OrderflowSignals` fields displayed as color-coded pills:
- **Green** = signal active/positive
- **Gray** = signal inactive/neutral
- **Red** = signal active/negative (stop run, divergence)

**Data source:** `OrderflowSignals` from `_compute_live_orderflow()` — already computed in confirmations endpoint.

### Section 9: Signal Table

```
Score │ Setup              │ Level Context  │ Dir   │ Entry  │ Stop   │ Target │ R:R
 85   │ IB Extension Long  │ Session POC    │ LONG  │ 21,820 │ 21,795 │ 21,890 │ 2.8
 78   │ VWAP Bounce        │ +1 SD          │ LONG  │ 21,835 │ 21,810 │ 21,875 │ 1.6
 72   │ Spring @ OB        │ Bull OB        │ LONG  │ 21,770 │ 21,750 │ 21,840 │ 3.1
 65   │ Poor Low Retest    │ Weekly VAH     │ SHORT │ 21,830 │ 21,868 │ 21,760 │ 1.8
```

**Always visible** — no gating. Sorted by score descending. Filtered by threshold slider.

**Expanded row shows:**
- Condition breakdown (name, score, weight, auto/manual)
- M5 setup scorer prediction (if model loaded)
- M6 temporal pattern confidence (if model loaded)
- Level context (which VP/structural levels are nearby)
- Take Trade button → fill price → confirm

**Data source:** `run_scan()` output — already implemented.

### Section 10: Level Map (collapsible)

All levels from all sections sorted by proximity to current price. One vertical list showing every level with its type and source. Gives a "where am I relative to everything" view.

```
21,910  +3SD
21,905  Weekly High
21,895  OB ▼ (bearish)
21,890  PDH
21,880  +2SD
21,865  Session VAH
21,860  IB High
21,850  +1SD
21,847  ◄ PRICE ◄
21,840  London High
21,830  Weekly VP VAH
21,825  Developing POC ↑
21,822  TPO POC
21,820  Session POC │ VWAP
21,818  IB Low
21,810  Leg VP VAH │ Overnight High
21,795  Tokyo High
21,790  -1SD
21,785  TPO VAL
21,780  Session VAL │ Last HL
```

**Data source:** Aggregate all levels from sections 3-7 + VP POCs, sort by price, annotate with type. Pure frontend computation from existing data.

## Backend Changes Summary

### New Functions

| Function | File | Purpose |
|----------|------|---------|
| `detect_swing_points(bars, lookback=5)` | `levels.py` | HH/HL/LH/LL structure + swing high/low detection |
| `detect_naked_pocs(prior_sessions, bars_since)` | `levels.py` | Find untested prior-session POCs |
| `compute_developing_poc(bars_so_far)` | `amt.py` | Track POC migration direction during session |

Note: No new `compute_composite_profile` function needed — existing `compute_volume_profile(bars)` handles any bar range.

### Modified Functions

| Function | File | Change |
|----------|------|--------|
| `compute_session()` | `market_service.py` | Fetch weekly/leg/macro bars, compute composite VPs, add swing points, developing POC, naked POCs to response |
| `get_confirmations()` | `market_service.py` | Remove gate logic, return flat indicator data. Add M9 macro fields (GEX, P/C, ES/NQ) |
| Session response | `market.py` route | Expand response to include all new fields |

### Existing VP Computation (No Change Needed)

`compute_volume_profile(bars)` in `amt.py` already accepts any bar list. For weekly/leg/macro profiles, just pass it the right date range of bars. No new VP algorithm needed.

### Data Fetching for Multi-TF Profiles

| Profile | Bar source | Granularity |
|---------|-----------|-------------|
| Session | Today RTH bars | 1-min (already fetched) |
| Weekly | This week's bars | 1-min from cache |
| Leg | `vp_leg_start` to now | 1-min from cache |
| Macro | `vp_ongoing_macro_start` to now | 1-hour or daily from Databento |

Bars are cached in parquet files per day. Weekly/leg profiles just load multiple cached days. Macro profiles spanning months use daily bars (already available from `fetch_ohlcv_1d()`).

## Frontend Changes Summary

### Remove

- Layer A manual gates (macro_bias, structure, day_type dropdowns)
- Layer B confirmation cards with checkboxes
- Gate logic (`layerAReady`, `layerBReady`, `gatesPassed`)
- Override toggle system
- "Set at least 1 context gate" / "Waiting for auto confirmations" messages

### Add

- Section components for each of the 10 sections above
- Collapsible Level Map component
- Leg/Macro date input fields (in VP section)
- Color-coded indicator pills throughout

### Modify

- `TradingIntradayPage.tsx` — complete rewrite of the layout
- `market.ts` types — flatten `ConfirmationState` into indicator data, add new fields
- API calls — session endpoint returns expanded data, confirmations endpoint returns flat indicators

## API Response Shape

### Migration strategy for session_json

**Critical:** The existing `session_json` column stores `SessionAnalysis.to_dict()` in a flat format. The `run_scan()` method reconstructs `SessionAnalysis` from this JSON. We must NOT change the `session_json` storage format.

Instead, the route handler builds the nested response by:
1. Reading `session_json` (flat SessionAnalysis dict) from DB
2. Reading DB columns for fields not in session_json (`rotation_factor`, `aspr`, `aspr_percentile`)
3. Calling new functions (`detect_swing_points`, `detect_naked_pocs`) on cached bar data
4. Computing composite VPs from cached bars
5. Assembling the nested response in the route handler / service method

This means `session_json` stays backwards-compatible. All new data is computed on-the-fly or from additional DB columns.

### GET /trading/market/session (expanded)

Returns everything needed for sections 2-7. Built in route handler from multiple sources:
```json
{
  "session": {
    "poc": 21820, "vah": 21865, "val": 21780,
    "tpo_poc": 21822, "tpo_vah": 21858, "tpo_val": 21785,
    "distribution_type": "p_shape",
    "vwap": 21820,
    "vwap_1sd_upper": 21850, "vwap_1sd_lower": 21790,
    "vwap_2sd_upper": 21880, "vwap_2sd_lower": 21760,
    "vwap_3sd_upper": 21910, "vwap_3sd_lower": 21730,
    "ib_high": 21860, "ib_low": 21818, "ib_range": 42,
    "market_type": "trending_up",
    "opening_type": "OTD",
    "poor_high": false, "poor_low": true,
    "single_prints": [[21700, 21720]],
    "rotation_factor": 4,
    "aspr": 28, "aspr_percentile": 0.72,
    "value_migration": "up",
    "overnight_high": 21810, "overnight_low": 21760,
    "total_delta": 3240, "delta_divergence": false
  },
  "macro": {
    "regime": "risk_on", "regime_score": 0.6,
    "vix": 14.2, "vix_change_pct": -3.0,
    "dxy": 104.1, "dxy_change_pct": 0.2,
    "us10y": 4.32, "us10y_change_bps": 2,
    "us2y": 4.65, "yield_curve_spread": -0.33,
    "gex": 1200000000,
    "put_call_ratio": 0.82,
    "es_nq_ratio_change": -0.003,
    "cot_net_position": 2386, "cot_change_1w": 180
  },
  "structure": {
    "classification": "uptrend",
    "last_hh": 21865, "last_hl": 21780,
    "last_lh": null, "last_ll": null,
    "swing_high": 21890, "swing_low": 21720
  },
  "profiles": {
    "session": {"poc": 21820, "vah": 21865, "val": 21780},
    "weekly": {"poc": 21740, "vah": 21830, "val": 21650},
    "leg": {"poc": 21680, "vah": 21810, "val": 21520, "anchor": "2026-03-10"},
    "macro": {"poc": 21450, "vah": 21720, "val": 21200, "anchor": "2026-02-24"},
    "developing_poc": 21825,
    "developing_poc_direction": "up",
    "naked_pocs": [
      {"date": "2026-02-28", "price": 21450},
      {"date": "2026-03-08", "price": 21680}
    ]
  },
  "levels": [
    {"type": "pdh", "price_low": 21890, "price_high": 21890, "direction": null},
    {"type": "pdl", "price_low": 21720, "price_high": 21720, "direction": null},
    {"type": "tokyo_high", "price_low": 21795, "price_high": 21795},
    {"type": "order_block", "price_low": 21760, "price_high": 21775, "direction": "bullish"},
    {"type": "fvg", "price_low": 21700, "price_high": 21720, "direction": "bullish"}
  ],
  "price_position": {
    "last_price": 21847,
    "vs_va": "above",
    "vs_vwap": "above_1sd",
    "vs_ib": "above",
    "vwap_deviation_sd": 1.2
  },
  "ml_day_type": "trend",
  "ml_day_type_confidence": 87
}
```

### GET /trading/market/indicators (new — replaces confirmations)

Returns live orderflow data (Section 8). Macro data comes from session response (cached by frontend).
```json
{
  "orderflow": {
    "delta": 842, "delta_aligned": true,
    "delta_divergence": false, "delta_unwind": false,
    "cvd": 3240, "cvd_trend": "rising",
    "vsa_absorption": true, "tick_vol_accelerating": true,
    "trapped_traders": false, "stop_run_detected": true,
    "passive_active_ratio": 1.2,
    "big_trades_count": 3, "big_trades_net_delta": 240,
    "imbalance_ratio_max": 0.89,
    "stacked_imbalance_count": 5,
    "stacked_imbalance_direction": "buy"
  }
}
```

### PUT /trading/market/context (simplified)

Only stores VP anchor dates now:
```json
{
  "symbol": "NQ",
  "vp_leg_start": "2026-03-10",
  "vp_ongoing_macro_start": "2026-02-24"
}
```

Note: Field names match DB columns exactly (`vp_leg_start`, `vp_ongoing_macro_start`). API accepts ISO date strings, converts to Unix timestamps for DB storage.

### Orderflow direction after gate removal

`compute_signals()` requires a `direction` parameter ("long"/"short") to compute `delta_aligned`. With manual gates removed, direction comes from the auto-detected swing point structure:
- `structure == "uptrend"` → direction = "long"
- `structure == "downtrend"` → direction = "short"
- `structure == "ranging"` → direction = None, `delta_aligned` = False

This makes `delta_aligned` fully automatic based on price action structure rather than manual macro bias.

### Endpoint refresh strategy

- `GET /trading/market/session` — compute-once snapshot. Called after Compute button. Returns session profile, macro, structure, VP levels, structural levels.
- `GET /trading/market/indicators` — live-refresh. Called on interval (every 30s) or after Scan. Returns orderflow signals + ML predictions. Does NOT duplicate macro (frontend caches macro from session response).
- `POST /trading/market/scan` — generates signals. Called after Scan button.

### Error handling for multi-TF VP

Weekly/leg/macro VP computation requires fetching multi-day bar data from cache. Graceful degradation:
- If bars not cached for a date range, return `null` for that profile (frontend shows "No data")
- Session VP always available (computed from current session bars)
- Weekly/leg/macro profiles load async — frontend shows session VP immediately, fills in others as they arrive
- If `vp_leg_start` or `vp_ongoing_macro_start` is null (user hasn't set them), those profiles are omitted

## Implementation Order

1. **Backend: swing point detection** — `detect_swing_points()` in `levels.py`
2. **Backend: multi-TF VP** — fetch weekly/leg/macro bars, compute composite profiles
3. **Backend: naked POCs + developing POC** — scan prior sessions, track migration
4. **Backend: expand session response** — include all new fields in API
5. **Backend: new indicators endpoint** — replace confirmations with flat indicators
6. **Backend: expose M9 macro fields** — GEX, P/C, ES/NQ in response
7. **Frontend: rewrite TradingIntradayPage** — 10-section layout, remove gates
8. **Frontend: update types** — new response shapes
9. **Frontend: Level Map component** — aggregate + sort all levels
