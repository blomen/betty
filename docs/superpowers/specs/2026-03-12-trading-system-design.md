# Trading System Design — AMT + Orderflow Scanner

**Date:** 2026-03-12
**Status:** Approved for implementation planning
**Instrument:** NQ (primary), extensible to ES and others
**Data source:** Databento Standard ($179/month) — live Trades + MBP-1 + OHLCV

---

## 1. Purpose & Philosophy

This system replicates the sports betting edge-finding model but for futures trading.

| Sports Betting | Trading |
|---|---|
| Pinnacle fair odds | Fair value: VWAP / POC / VP levels |
| Soft book deviates from fair | Price deviates to key level |
| Edge % = opportunity score | Deviation score = opportunity score |
| 4 confirmation gates | Context gates (macro+structure) + L2 orderflow |
| Two-step bet entry | Two-step trade entry |
| Auto Kelly stake | Auto SL/TP from levels |

The theoretical foundation is **Auction Market Theory (AMT)** as taught by Fabio Valentini (World #1 scalper, Robbins Cup):
- Markets oscillate between **balance** (fair value / equilibrium) and **imbalance** (deviation)
- Trading opportunities arise at the extremes of imbalance when orderflow signals rotation back to fair value OR continuation of imbalance
- Every setup is a specific, named pattern with defined entry conditions, SL rules, TP targets, and historical win rate

The system is **rules-based, not ML-based**. Each component is explicit and auditable. ML may be layered onto the scoring model after 500+ labeled trades.

---

## 0. Current State (What Already Exists)

Before implementing anything, understand what is already live:

| Component | Exists | Location |
|---|---|---|
| Market session computation | Yes | `backend/src/services/market_service.py` → `compute_session()` |
| Session DB table | Yes | `market_sessions` (POC, VAH, VAL, VWAP bands, IB, overnight, delta, market_type, poor_high/low) |
| Signal DB table | Yes | `trading_signals` (setup_type, score, conditions JSON, entry, stop, target×1, vwap, poc, vah, val) |
| 4 auto-evaluated gates | Yes | `get_confirmations()` in MarketService: macro/span/fair_value/orderflow |
| Scanner page UI | Yes | `TradingScannerPage.tsx` with confirmation strip + opportunity table |
| Trade DB table | Yes | `trades` table |
| Market routes | Yes | `backend/src/api/routes/market.py` |

**What this spec adds vs replaces:**
- The existing 4 auto-gates (`macro/span/fair_value/orderflow`) are **kept** and renamed as Layer B (L2 auto-confirmations)
- The new 3 manual gates (Gate 1/2/3) are **added** as Layer A (contextual pre-filters) — they sit above the existing auto-gates
- `market_sessions` is **extended** with new columns (RF, ASPR, IB TPO count, session baselines) rather than replaced
- `trading_signals` is **extended** with `suggested_target_2`, `suggested_target_3`, `level_touched`, `setup_category` columns
- `market_context` is a **new table** for manual gate persistence
- `market_trades` is a **new table** for raw tick storage from Databento stream
- `market_levels` is a **new table** for computed level snapshots (order blocks, FVGs, ledges, single prints)

---

## 2. Data Layer

### Databento Standard Plan
- **Live stream:** Trades + MBP-1 (top of book) for real-time delta, CVD, footprint, L2
- **Historical L1:** 1 year of Trades/TBBO — for leg VP, session baselines, PDH/PDL reconstruction
- **Historical OHLCV-1d:** 15+ years — for macro VP anchors (old macro, ongoing macro)
- **Historical OHLCV-1m:** Session-level bars, session H/L computation

### Supplementary Free Sources
- **COT Report:** CFTC API (weekly, free) — feeds Gate 1 macro bias
- **Funding rates:** Exchange API (free) — crowding/positioning signal for Gate 1
- **VIX/DXY/Yields:** Can be fetched via OHLCV schemas for NQ correlates

### Storage Strategy
- Live Trades → stored to `market_trades` table in SQLite (intraday only, pruned nightly)
- Computed levels → `market_levels` table (refreshed each session)
- Session metrics history → `session_metrics` table (permanent, used for ASPR/RF baselines)
- Manual context inputs → `market_context` table (persists macro bias, structure labels, day type)
- Trade history → existing `trades` table (for per-setup win rate tracking)

### New DB Tables

**`market_trades`** (intraday tick storage, pruned nightly)
```
id          INTEGER PRIMARY KEY
symbol      TEXT NOT NULL          -- "NQ"
ts          DATETIME NOT NULL      -- UTC timestamp from Databento
price       REAL NOT NULL
size        INTEGER NOT NULL
side        TEXT NOT NULL          -- "B" (bid aggressor) | "A" (ask aggressor)
INDEX(symbol, ts)
```

**`market_levels`** (computed level snapshot per session)
```
id          INTEGER PRIMARY KEY
symbol      TEXT NOT NULL
date        TEXT NOT NULL          -- "2026-03-12"
level_type  TEXT NOT NULL          -- "order_block" | "fvg" | "ledge" | "single_print" | "pdh" | "pdl" | "session_high" | "session_low" | "tokyo_high" | "tokyo_low" | "london_high" | "london_low"
session     TEXT                   -- "tokyo" | "london" | "ny" | null
price_low   REAL NOT NULL
price_high  REAL NOT NULL          -- = price_low for single-price levels
direction   TEXT                   -- "bullish" | "bearish" | null
is_filled   BOOLEAN DEFAULT FALSE
created_at  DATETIME
INDEX(symbol, date, level_type)
```

**`market_context`** (manual gate persistence)
```
id            INTEGER PRIMARY KEY
symbol        TEXT NOT NULL
updated_at    DATETIME NOT NULL
-- Gate 1
macro_bias    TEXT                  -- "bull" | "bear" | "neutral"
risk_mode     TEXT                  -- "risk_on" | "risk_off" | "mixed"
cycle_phase   TEXT                  -- "early" | "mid" | "late" | "recession"
-- Gate 2
structure     TEXT                  -- "uptrend" | "downtrend" | "ranging"
structure_hl  REAL                  -- last confirmed HL price (long invalidation below this)
structure_lh  REAL                  -- last confirmed LH price (short invalidation above this)
-- Gate 3
day_type      TEXT                  -- "trend" | "normal" | "normal_variation" | "neutral" | "composite"
-- VP anchors (Unix timestamps)
vp_old_macro_start    INTEGER
vp_ongoing_macro_start INTEGER
vp_leg_start          INTEGER
UNIQUE(symbol)
```

**`market_sessions` extensions** (add columns to existing table via migration)
```
-- New columns added to existing market_sessions:
rotation_factor     INTEGER          -- running RF total for session
aspr                REAL             -- average sub-period range
aspr_percentile     REAL             -- percentile vs 1yr baseline (0.0–1.0)
ib_tpo_count        INTEGER          -- number of TPO letters in IB
value_migration     TEXT             -- "up" | "down" | "overlapping" | null
pdh                 REAL             -- prior day high
pdl                 REAL             -- prior day low
tokyo_high          REAL
tokyo_low           REAL
london_high         REAL
london_low          REAL
```

**`trading_signals` extensions** (add columns to existing table via migration)
```
-- New columns added to existing trading_signals:
suggested_target_2  REAL             -- TP2
suggested_target_3  REAL             -- TP3
level_touched       TEXT             -- which level triggered the setup
setup_category      TEXT             -- "spring" | "sfp" | "poor_extreme" | "ib_break" | "rule_80" | "double_dist" | "bfb" | "fakeout" | "news"
rr_tp1              REAL             -- R:R to TP1
rr_tp2              REAL             -- R:R to TP2
```

---

## 3. Level Engine

All levels are auto-computed. No daily manual input required.

### Volume Profile (4 Anchors)
| Anchor | Timeframe | Source | Update frequency |
|---|---|---|---|
| Old macro VP | Months–years | OHLCV-1d historical | When user sets new anchor timestamp |
| Ongoing macro VP | Weeks–months | OHLCV-1d historical | When user sets new anchor timestamp |
| Leg VP | Days–weeks | Trades (L1 history) | When user sets new anchor timestamp |
| Current session VP | Intraday | Live Trades stream | Real-time |

Anchor timestamps are stored in DB. User sets them once when structure shifts (maybe monthly for macro, weekly for leg). System recomputes the VP histogram automatically.

**Computed from each VP:** POC, VAH, VAL, single prints, low-volume nodes, high-volume nodes.

### VWAP Bands
- Rolling intraday VWAP anchored to session open
- ±1 SD, ±2 SD, ±3 SD bands
- Updated tick-by-tick from live Trades stream

### Initial Balance
- First 60 minutes of regular trading hours
- IB High, IB Low, IB Midpoint — hard boundaries for the day
- Auto-locked at 10:30 ET (or configured open + 60 min)

### TPO / Market Profile
- 30-minute brackets, letter-based (A = first bracket, B = second, etc.)
- TPO POC, TPO Value Area (70% of time)
- Single prints detected and stored
- Ledges detected (abrupt cutoff in profile = 6+ TPOs outside single TPO)
- Poor extremes detected (thin tail at session high/low)

### Session Levels (fully auto)
- **PDH / PDL** — prior day high/low from OHLCV-1d or recorded T-1 session
- **Tokyo session H/L** — 20:00–00:00 ET (configurable)
- **London session H/L** — 03:00–08:30 ET (configurable)
- **NY session H/L** — 09:30–16:00 ET (current session, live)
- **Weekly high/low** — prior week's range
- **Monthly high/low** — prior month's range

### Additional Level Types
- **Order blocks** — candles preceding impulsive moves where institutional positioning occurred; stored as price zones
- **Fair Value Gaps (FVGs)** — price gaps left by impulsive moves; stored as unfilled ranges, act as magnets
- **Value migration tracker** — daily: is today's VAH higher than yesterday's? Tracks trend exhaustion when VAs start overlapping

---

## 4. Session Metrics (Auto-Computed vs Historical Baseline)

These are quantitative health metrics for the current session, compared to a 1-year rolling baseline.

### Rotation Factor (RF)
Per 30-min period:
- Current high > prior period high → +1
- Current low < prior period low → +1
- Current high < prior period high → -1
- Current low > prior period low → -1

Running total displayed live. Baseline for NQ: ~56.
- RF 40–70 = normal session
- RF < 30 = compression → pre-breakout signal
- RF > 80 = strong trend day

### ASPR (Average Sub-Period Range)
Average of each 30-min candle's range across the session. Compared to 1-year daily ASPR distribution.
- ASPR at 1 SD below norm → compression → pre-breakout
- ASPR at 1 SD above norm → expanded volatility (trend day)
- Displayed as: current value / 1yr average / percentile

### Range Baseline Metrics (stored per session, used for pattern detection)
- Average rotation time (minutes from extreme to extreme)
- Average volume at session extremes
- Typical delta behavior at exhaustion points
When current session deviates from baseline → break from balance signal raised.

---

## 4b. Real-Time Transport

The live L2 panel in expanded signal rows requires real-time data push from backend to frontend. Polling (the current `POST /scan` model) is insufficient for tick-by-tick delta/CVD.

**Chosen transport: Server-Sent Events (SSE)**
- Simpler than WebSocket for one-directional push
- Fits FastAPI's `EventSourceResponse` (via `sse-starlette`)
- Frontend consumes with native `EventSource` API

**SSE endpoint:** `GET /api/trading/market/stream?symbol=NQ`

**Stream events (JSON lines):**
```
event: tick        → {price, size, side, delta_running, cvd_running, ts}
event: candle      → {open, high, low, close, volume, delta, vsa_signal, ts, period_mins}
event: level_touch → {level_type, price, setup_type_candidate, direction}
event: rf_update   → {rf, aspr, session_elapsed_mins}
```

Frontend hook: `useMarketStream(symbol)` — subscribes on mount, updates local state, disconnects on unmount.
The expanded signal row consumes `useMarketStream` for the live L2 panel. Main scanner table still polls `GET /api/trading/market/signals` every 30s.

---

## 5. Context Gates (Manual Input — 3 Gates)

You set these before each session. They persist until you change them.

### Gate 1: Macro Bias (update weekly)
You set: `bull / bear / neutral` + `risk_on / risk_off / mixed`

Supporting data auto-fetched and displayed:
- COT net positioning (CFTC, weekly)
- VIX level and trend
- DXY direction
- 10yr yield trend
- Funding rates (crowding indicator)
- NQ macro cycle phase (early / mid / late / recession)
- Value migration: is daily VA trending higher, lower, or overlapping?

This gate determines which direction opportunities are shown:
- `bull + risk_on` → long setups only
- `bear + risk_off` → short setups only
- `neutral` → both shown, lower base score

### Gate 2: Market Structure (update daily, semi-auto)
Algorithm detects swing pivots (N-bar fractal method). You confirm or reject the label.
- Current structure: `HH/HL (uptrend)` / `LH/LL (downtrend)` / `ranging`
- Key structural levels: the last confirmed HL (long invalidation below), last confirmed LH (short invalidation above)
- Structure invalidation level auto-used in SL calculator for relevant setups

### Gate 3: Day Type (set at session open, after first 30–60 min)
You classify: `trend day` / `normal day` / `normal variation` / `neutral/balance day` / `composite`

Day type gates which setups are valid:
- **Trend day:** IB Break and Break from Balance only (don't fade)
- **Normal day:** Spring, SFP, Poor Extreme, 80% Rule (fade the extremes)
- **Neutral/balance:** Wait — only trade confirmed break from balance
- **Composite:** Multiple setups valid, use fractal context

---

## 6. Setup Detector (10 Named Setup Types)

Each setup has: trigger conditions, required orderflow confirmations, SL rule, TP rule, base win rate.

### Setup 1: Spring / Liquidity Trap
- **Trigger:** Price briefly penetrates below support (or above resistance) on low volume, snaps back
- **Key condition:** Penetration must be minor; volume on penetration < prior average
- **Orderflow required:** Delta reversal on snap-back + volume expansion on return
- **SL:** Just beyond spring penetration extreme (tight)
- **TP1:** Opposite side of support/resistance zone; **TP2:** POC; **TP3:** Prior swing high/low
- **Base win rate:** tracked in DB (estimated ~80% on ideal setups)

### Setup 2: Swing Failure Trap (SFP)
- **Trigger:** Price breaks a significant swing high/low AND **closes back inside** the level (close required, not just wick)
- **Key condition:** Close-back rule is mandatory — wick only = no signal
- **Orderflow required:** High delta in breakout direction (buyers hitting asks on break), then delta unwind as price closes back inside; trapped traders forced to cover
- **SL:** Beyond swing failure candle extreme
- **TP1:** Fair value / IB midpoint; **TP2:** Opposite session extreme; **TP3:** Macro level
- **Base win rate:** ~65%

### Setup 3: Poor Extreme
- **Trigger:** Session makes new high/low on volume significantly below average; thin tail in TPO profile
- **Key condition:** Volume at extreme < 0.7× average volume at prior extremes in session
- **Orderflow required:** Delta divergence at extreme (price at new high but delta not confirming); immediate delta unwind
- **SL:** Just beyond poor extreme candle
- **TP1:** Session POC; **TP2:** Prior session level in reversal direction
- **Base win rate:** ~75%

### Setup 4: Initial Balance Break
- **Trigger:** Price breaks IB High or IB Low with expanding volume and delta aligned
- **Key condition:** No absorption back at IB boundary (clean break); delta must stay directional for 2+ bars after break
- **Orderflow required:** Sustained positive/negative delta through break; tick volume accelerating (not decelerating)
- **SL:** At IB boundary (opposite extreme)
- **TP1:** PDH/PDL; **TP2:** 80% rule projection; **TP3:** Prior week's high/low
- **Base win rate:** ~70%

### Setup 5: 80% Rule
- **Trigger:** Price opens OUTSIDE prior day's value area, then trades back INSIDE VA for 2 consecutive TPO periods (30 min each)
- **Key condition:** Must be 2 full consecutive TPO periods inside VA (not just wick inside)
- **Entry:** On close of 2nd TPO period back inside VA
- **Orderflow required:** Delta aligned with direction to opposite VA extreme
- **SL:** At VA re-entry point (stop if price exits VA again)
- **TP:** Opposite extreme of prior day's VA
- **Base win rate:** ~80%

### Setup 6: Double Distribution Reversal
- **Trigger:** Volume profile shows 2 distinct peaks; secondary distribution (the newer one) shows weakening volume and delta
- **Key condition:** Secondary VP POC volume < primary VP POC volume; delta at secondary distribution is inconsistent/choppy
- **Orderflow required:** Absorption at secondary distribution (high vol + small body), delta unwind, rotation back toward primary distribution
- **SL:** Beyond secondary distribution extreme
- **TP1:** Gap between distributions (single prints); **TP2:** Primary distribution POC
- **Base win rate:** ~65%

### Setup 7: Break from Balance / Equilibrium
- **Trigger:** Price exits a multi-period balanced range (3+ days overlapping VAs, or clear consolidation with ASPR at compression levels)
- **Key condition:** Must align with Gate 1 direction; RF should be trending in breakout direction; ASPR was compressed before break
- **Orderflow required:** Delta sustained directional post-break; tick volume accelerating; no absorption back at balance boundary
- **SL:** At balance zone boundary opposite to breakout direction
- **TP1:** Previous swing level; **TP2:** 1.5–2× balance range size projected from break; **TP3:** Macro VP level
- **Base win rate:** ~65%

### Setup 8: Fakeout / Head Fake
- **Trigger:** Price breaks a significant level convincingly, then reverses — delta divergence during the break (price at new extreme, delta not confirming)
- **Key condition:** POC or VWAP held during the "break" (key internal level not violated); rapid return through the level
- **Orderflow required:** Delta divergence on the breakout candle; absorption (high vol + narrow spread) at breakout level; volume expansion on reversal
- **SL:** Beyond fake break extreme
- **TP1:** Midpoint of prior range; **TP2:** Opposite side of faked level
- **Base win rate:** ~65%

### Setup 9: Wolfe Wave (5th Wave) — DEFERRED TO V2
Wolfe Wave requires a geometric pivot-detection algorithm (ZigZag with configurable swing %, trendline convergence test with ±X tick tolerance). Insufficient spec detail for v1 implementation. Deferred to a separate spec after v1 ships.

### Setup 10: News Directional
- **Trigger:** Major economic release (pre-scheduled); price forms balance in 30 min before news; news fires → wait for "directional candle" (unidirectional, much larger than session average range)
- **Key condition:** Do NOT trade the spike. Wait for the directional candle to close. If candle is mixed/doji = no trade
- **Orderflow required:** Post-news delta sustained in directional candle direction; VSA on M1 for precise entry timing (absorption within impulse = better entry)
- **SL:** Above/below opposite balance boundary
- **TP:** 1:2.5–1:3.7 R:R typical; target opposite balance margin
- **Base win rate:** tracked in DB

---

## 7. Confirmation Layer (L2 Auto, Real-Time)

All signals computed in real-time from live Trades + MBP-1 stream.

| Signal | What it is | Bullish interpretation | Bearish interpretation |
|---|---|---|---|
| Delta | Buy vol − sell vol per candle | Positive = buyers aggressive | Negative = sellers aggressive |
| Delta divergence | Price vs delta disagree | Price down, delta positive = buyers absorbing | Price up, delta negative = sellers absorbing |
| Delta unwind | Rapid delta flip at extreme | Negative delta flips positive = reversal starting | Positive delta flips negative = reversal starting |
| CVD trend | Cumulative delta direction | Rising CVD = sustained buy pressure | Falling CVD = sustained sell pressure |
| VSA absorption | High volume + narrow spread + mid-close | Institutional accumulation at level | Institutional distribution at level |
| Tick volume | Number of transactions per candle | Accelerating on up move = valid breakout | Decelerating on up move = exhaustion |
| Passive/active ratio | Limit vs market order flow | Heavy passive bids = institutions defending | Heavy passive asks = institutions distributing |
| Trapped traders | Wrong-sided positions being squeezed | Trapped shorts covering = buy fuel | Trapped longs liquidating = sell fuel |

**Confirmation scoring:** Each confirmed signal adds to the opportunity score. Delta divergence + VSA absorption together = strongest reversal signal combination.

---

## 8. Scoring Model

```
Base score = setup type base win rate (0–100, pulled from trade history DB)

Adjustments:
  Delta aligned with setup direction          +10
  Delta divergence present (reversal setups)  +10
  VSA absorption confirms                     +10
  Tick volume accelerating (breakout setups)  +8
  Timeframe confluence (same setup on HTF)    +10
  Day type fits setup type                    +10 / does not fit -20
  Gate 1 macro aligned                        +5
  RF/ASPR session context supports            +5
  Trapped traders detected                    +8

Final score = base + adjustments (capped 0–100)
Surface to UI if score ≥ 70
```

New setups with no trade history default to 65 as base score until 20+ trades logged.

---

## 9. Risk Calculator

SL and TP are computed automatically per setup type when an opportunity is detected.

**SL rules by setup:**
- Spring: beyond spring penetration low/high (tight, typically 3–8 ticks)
- SFP: beyond swing failure candle extreme
- Poor Extreme: beyond poor extreme candle
- IB Break: at IB boundary
- 80% Rule: at VA re-entry point
- Double Distribution: beyond secondary distribution extreme
- Break from Balance: at balance zone boundary
- Fakeout: beyond fake break extreme
- Wolfe Wave: beyond wave 5 extreme
- News: at opposite balance boundary

**TP rules (all setups):**
- TP1: nearest significant level in trade direction (auto-selected from level engine)
- TP2: 80% rule projection OR session opposite extreme
- TP3: daily/macro VP level OR weekly high/low

**R:R filter:** Only surface opportunities where TP1 R:R ≥ 1.5. If TP1 is too close, promote TP2 as primary target.

**Position sizing:** Kelly criterion applied using per-setup historical win rate and R:R. Capped at 2% account risk per trade (matching existing bankroll module).

---

## 9b. Gate Model Reconciliation

The existing `TradingScannerPage` has 4 auto-evaluated gates: `macro / span / fair_value / orderflow`. These come from `get_confirmations()` in MarketService.

**New model: 2-layer gate system**

- **Layer A (new, manual):** Gate 1 / Gate 2 / Gate 3 — contextual pre-filters set by you. Shown as 3 new cards above the existing confirmation strip.
- **Layer B (existing, auto):** The 4 existing auto-gates (`macro / span / fair_value / orderflow`) — kept as-is, renamed "Session Checks" in the UI.

An opportunity surfaces only when: at least 1 Layer A gate set (not all null) AND Layer B score ≥ 2/4. This avoids hard-blocking on days you haven't set gates yet.

**Timezone:** All Databento timestamps are UTC. Session boundary computations (IB = first 60 min of RTH, Tokyo = 20:00 ET = 01:00 UTC, London = 03:00 ET = 08:00 UTC) must convert using `pytz` / `zoneinfo` with US/Eastern. Never use naive datetimes for session windows.

---

## 10. UI / Scanner Page

The existing `TradingScannerPage` is refactored around this architecture.

### Layout (top to bottom)
1. **FilterBar** — instrument selector, setup type filter, min score threshold slider
2. **Context Panel** — 3 gate status cards (Gate 1/2/3) with your current inputs; click to edit; auto-data displayed alongside
3. **Session Metrics Row** — live RF, ASPR (vs baseline), IB High/Low, VWAP, POC, current VP metrics
4. **Opportunity Table** — surfaces when score ≥ 70 AND context gates set; sorted by score descending

### Opportunity Table Columns
`Setup | Direction | Level touched | Score | Entry | SL | TP1 | TP2 | R:R | Confirmations`

### Expanded Row (click to expand)
- Live L2 panel: delta bar chart, CVD line, footprint summary, VSA signal
- All confirmation signals listed with status (✓/✗/pending)
- Score breakdown (which factors contributed)
- Two-step entry: "Take Trade" → fill price → "Confirm" → saved to DB

### Level Map Panel (sidebar or separate tab)
- Visual list of all active levels from level engine
- Grouped by: VP levels / VWAP bands / Session levels / Order blocks / FVGs / Single prints
- Current price shown relative to nearest levels above/below
- Macro VP anchor timestamps — click to update

---

## 11. Backend Architecture

### New modules (fitting existing structure)

```
backend/src/
├── market/
│   ├── data/
│   │   ├── databento_stream.py     # Live Trades + MBP-1 WebSocket
│   │   ├── databento_history.py    # Historical fetch (OHLCV, L1)
│   │   └── cot_fetcher.py          # CFTC COT weekly fetch
│   ├── levels/
│   │   ├── volume_profile.py       # VP computation from trades
│   │   ├── vwap.py                 # VWAP + SD bands
│   │   ├── tpo.py                  # TPO / Market Profile
│   │   ├── session_levels.py       # PDH/PDL, session H/L
│   │   ├── order_blocks.py         # Order block detection
│   │   └── level_engine.py         # Orchestrates all level types
│   ├── metrics/
│   │   ├── rotation_factor.py      # RF computation
│   │   ├── aspr.py                 # ASPR vs baseline
│   │   └── range_baselines.py      # Session baseline storage
│   ├── setups/
│   │   ├── spring.py               # Setup 1
│   │   ├── sfp.py                  # Setup 2
│   │   ├── poor_extreme.py         # Setup 3
│   │   ├── ib_break.py             # Setup 4
│   │   ├── rule_80.py              # Setup 5
│   │   ├── double_distribution.py  # Setup 6
│   │   ├── break_from_balance.py   # Setup 7
│   │   ├── fakeout.py              # Setup 8
│   │   ├── wolfe_wave.py           # Setup 9
│   │   ├── news_directional.py     # Setup 10
│   │   └── setup_detector.py       # Orchestrates all setups
│   ├── orderflow/
│   │   ├── delta.py                # Per-candle delta + CVD
│   │   ├── vsa.py                  # Volume spread analysis
│   │   ├── footprint.py            # Footprint chart computation
│   │   └── confirmation.py         # Aggregates all L2 signals
│   ├── context/
│   │   └── context_service.py      # Gate 1/2/3 read/write
│   ├── risk/
│   │   └── risk_calculator.py      # SL/TP per setup type
│   ├── scoring/
│   │   └── scorer.py               # Score assembly
│   └── scanner/
│       └── trading_scanner.py      # Orchestrates full pipeline
```

Repositories and API routes follow existing patterns (`repositories/`, `services/`, `api/routes/`).

---

## 12. Build Order (Implementation Phases)

**Phase 0 — Audit + migrations (prerequisite)**
1. Audit existing `market_sessions`, `trading_signals` tables vs spec
2. DB migrations: extend `market_sessions` + `trading_signals` with new columns
3. Create `market_context`, `market_trades`, `market_levels` tables

**Phase 1 — Data foundation**
4. Databento stream client (live Trades + MBP-1) — persistent async task in backend
5. `market_trades` writer — store ticks, prune to current session on startup
6. Historical OHLCV-1d + OHLCV-1m REST fetch utility
7. COT fetcher (CFTC API, weekly)

**Phase 2 — Confirmation signals (moved up — setups depend on these)**
8. Per-candle delta + CVD from live stream
9. VSA computation (vol + spread + close position relative to range)
10. Tick volume acceleration/deceleration detection
11. SSE stream endpoint (`GET /api/trading/market/stream`)
12. Frontend `useMarketStream` hook

**Phase 3 — Level engine**
13. VWAP + SD bands (uses live stream)
14. Session levels: PDH/PDL, Tokyo/London/NY H/L (UTC-aware, OHLCV-1m)
15. Initial Balance detection (60-min from RTH open, UTC-aware)
16. Volume profile (4 anchors, uses Trades — anchors from `market_context`)
17. TPO / Market Profile (30-min brackets, single prints, ledges, poor extremes)
18. Session metrics: RF + ASPR vs 1yr baseline
19. Order blocks + FVG detection

**Phase 4 — Context gates**
20. `market_context` read/write API
21. Gate 1/2/3 input cards in Scanner page (above existing 4 auto-gate strip)
22. Gate 1 auto-data display (COT, VIX, DXY, funding rate, value migration)

**Phase 5 — Setup detector (in order of complexity)**
23. Poor Extreme
24. 80% Rule
25. IB Break
26. Spring
27. SFP (close-back requirement)
28. Fakeout
29. Break from Balance
30. Double Distribution
31. News Directional

**Phase 6 — Scoring + risk + UI**
32. Scoring model (base win rate from trades history, adjustments)
33. SL/TP calculator per setup type (TP1/TP2/TP3)
34. Opportunity table: updated columns including TP2, TP3, level_touched, R:R
35. Expanded row: live L2 panel consuming SSE stream
36. Level map panel (sidebar showing all active levels)

---

## 13. What Stays Manual

| Input | Frequency | Stored in |
|---|---|---|
| Old macro VP anchor timestamp | Monthly or when structure shifts | market_context |
| Ongoing macro VP anchor timestamp | Weekly or when phase changes | market_context |
| Leg VP anchor timestamp | When new leg begins | market_context |
| Gate 1: macro bias + risk mode | Weekly | market_context |
| Gate 2: structure label confirmation | Daily (semi-auto: approve algorithm's detection) | market_context |
| Gate 3: day type | At session open (after 30–60 min) | market_context |

Everything else is automated.

---

## 14. Out of Scope (Not in This Spec)

- Multi-instrument scanning (NQ only for v1)
- ML-based signal scoring (rules-based only for now)
- Automated order execution (manual two-step entry only)
- Backtesting engine (separate future spec)
- Mobile interface
- Wolfe Wave setup (deferred to v2 — needs geometric pivot detection algorithm spec)
- COT auto-interpretation (COT data fetched + displayed, bias set manually by user)
