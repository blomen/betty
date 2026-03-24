# Data Requirements — L1 & L2

Everything the trading system needs, cleanly separated into raw data (L1) and derived data (L2).

## L1 — Raw Market Data (From Databento)

Data that comes off the wire. We receive and store it, no computation.

### What Databento Offers (GLBX.MDP3 / CME)

| Schema | What it is | Granularity | Cost |
|--------|-----------|-------------|------|
| `trades` | Every trade: price, size, aggressor side (A/B/N) | Tick-by-tick | Medium |
| `tbbo` | Trade + best bid/ask BEFORE the trade | Tick (trade-space) | Medium |
| `mbp-1` | Top-of-book updates (every BBO change) | Every book update | Medium-High |
| `mbp-10` | 10-level order book | Every book update | High |
| `mbo` | Full order-by-order book | Every order event | Highest |
| `bbo-1s` | Best bid/ask sampled | 1-second | Low |
| `ohlcv-1s` | OHLCV bars | 1-second | Low |
| `ohlcv-1m` | OHLCV bars | 1-minute | Low |
| `ohlcv-1h` | OHLCV bars | 1-hour | Lowest |
| `ohlcv-1d` | OHLCV bars | Daily | Lowest |
| `definition` | Instrument metadata (tick size, multiplier, expiry) | Static | Free |
| `statistics` | Settlement, open interest, daily limits | Daily | Low |
| `status` | Trading halts, session state changes | Event-driven | Low |

**Symbol:** `NQ.c.0` (continuous front-month, auto-rolls). `stype_in="continuous"`.

### What We Need (L1)

| L1 Feed | Databento Schema | Use | Status |
|---------|-----------------|-----|--------|
| **Tick trades** | `trades` | Foundation for footprint, delta, CVD, volume profile | HAVE (historical + live) |
| **1-minute bars** | `ohlcv-1m` | Structure timeframe, IB range, session profile | HAVE |
| **Daily bars** | `ohlcv-1d` | ATR, previous session OHLC, volatility filter | HAVE |
| **Top-of-book** | `mbp-1` | Live bid/ask, spread, passive order tracking | HAVE (live stream) |
| **1-second bars** | `ohlcv-1s` | Build 15-sec execution bars | MISSING — need to add |
| **Open interest** | `statistics` | OI changes at levels — new positions forming/closing, conviction filter | MISSING — need to add |
| **Instrument def** | `definition` | Tick size, multiplier, contract specs | MISSING — currently hardcoded |

**Open interest detail:** The `statistics` schema publishes `stat_type=OPEN_INTEREST` for GLBX.MDP3 (CME). OI changes tell us whether volume at a level is opening new positions or closing existing ones:
- Volume rising + OI rising = new positions being established (conviction)
- Volume rising + OI falling = positions being closed (covering/liquidation)
- Volume declining + OI rising at a level = stealth accumulation
- Volume declining + OI short at a level = bearish confirmation (Fabio's "declining volume into level" signal)

### What We Don't Need (L1)

| Schema | Why skip |
|--------|---------|
| `mbp-10` | 10-level depth — our strategy doesn't use depth beyond top-of-book |
| `mbo` | Full order book reconstruction — massive data. Future consideration for passive order tracking |
| `bbo-1s` / `bbo-1m` | Sampled BBO — redundant, we have `mbp-1` live |
| `tbbo` | Trade + BBO — redundant, we have `trades` + `mbp-1` separately |
| `imbalance` | Equity-only auction imbalance data (paired/unpaired qty, auction collars). NOT footprint imbalances — those are L2 computed from trades |
| `ohlcv-1h` | Redundant — resample from 1m |
| `status` | Trading halts — useful for alerting but not strategy-critical |

### L1 Summary

We need **6 feeds** from Databento. We have 4, missing 2 (`ohlcv-1s`, `statistics`), and 1 nice-to-have (`definition`).

---

## L2 — Derived / Computed Data (Built from L1)

Everything we compute ourselves from L1 inputs. Grouped by what they serve in the strategy.

### L2.1 — Bar Aggregation

Built from L1 bars or ticks. These are the timeframes the strategy operates on.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **15-min bars** | `ohlcv-1m` | Resample: OHLC agg, volume sum | HAVE |
| **5-min bars** | `ohlcv-1m` | Resample | HAVE |
| **15-sec bars** | `ohlcv-1s` | Resample 1s → 15s | MISSING (need L1 `ohlcv-1s` first) |
| **Range bars (20-tick)** | `trades` | Accumulate ticks until range threshold | MISSING |

### L2.2 — Volume Profile

The core analytical structure. Shows WHERE volume traded across price levels.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **Session volume profile** | `trades` or `ohlcv-1m` | Histogram: volume at each price tick | HAVE (AMT engine) |
| **POC** (Point of Control) | Volume profile | Price with max volume | HAVE |
| **Value Area (VAH/VAL)** | Volume profile | Expand from POC until 70% of total volume | HAVE |
| **HVN / LVN** | Volume profile | Peaks and valleys in histogram | PARTIAL |
| **Naked POC** | Previous session POC | Track across sessions, flag if unvisited | HAVE (level_monitor) |
| **Developing POC/VA** | Live `trades` stream | Update histogram in real-time | HAVE (stream.py) |
| **Previous session levels** | Previous session profile | Store: POC, VAH, VAL, high, low | HAVE |

### L2.3 — VWAP

Dynamic fair value anchored to session open.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **Session VWAP** | `trades` or `ohlcv-1m` | `Σ(price × volume) / Σ(volume)` | HAVE |
| **VWAP 1st SD** | VWAP + trades | Standard deviation of price from VWAP, volume-weighted | HAVE |
| **VWAP 2nd SD** | Same | 2 × SD — overextension zone | HAVE |
| **VWAP 3rd SD** | Same | 3 × SD — extreme (7% of sessions) | HAVE |

### L2.4 — Orderflow / Footprint (CRITICAL GAPS HERE)

The strategy's confirmation layer. Every entry requires orderflow validation.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **Delta per candle** | `trades` | Sum: +size if side='A', −size if side='B' | HAVE |
| **Delta %** | `trades` | `delta / total_volume` per candle | **MISSING** |
| **CVD** (Cumulative Volume Delta) | `trades` | Running sum of delta across session | HAVE (TickBuffer) |
| **Footprint matrix** | `trades` | Group by (candle_period, price_level) → {bid_vol, ask_vol} | **MISSING** |
| **Auction imbalance** | Footprint matrix | Diagonal compare: ask[price] vs bid[price−1]. >200% = imbalance | **MISSING** |
| **Imbalance clusters** | Imbalances | 3+ consecutive price levels with imbalance | **MISSING** |
| **Absorption** | `mbp-1` + `trades` | Large passive fills at a level blocking aggressive side | **MISSING** |

**This is the biggest gap.** We have the L1 input (`trades`) but aren't computing the key L2 derivatives that drive trade confirmation.

### L2.5 — Open Interest Analysis

Derived from L1 `statistics` (OI) combined with L1 `trades` (volume). Provides conviction context at key levels.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **OI change per session** | `statistics` | Delta of OI from session open | **MISSING** |
| **OI + Volume divergence** | `statistics` + `trades` | Compare volume trend vs OI trend at a level — divergence signals covering vs accumulation | **MISSING** |
| **New position detection** | `statistics` + `trades` | Rising OI + rising volume = new positions forming; signal conviction at level tests | **MISSING** |
| **Liquidation detection** | `statistics` + `trades` | Falling OI + rising volume = forced exits; look for reversal after flush | **MISSING** |

### L2.6 — Order Block Detection

Large clustered orders at a price level that previously caused a strong move. When price revisits the zone, expect a reaction. Confirmation signal for reversals.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **Order blocks** | `trades` + `ohlcv-1m` | Identify candles with outsized volume + strong directional close → mark the origin zone | **MISSING** |
| **OB proximity alert** | Order blocks + live price | Flag when price approaches a previously identified order block zone | **MISSING** |
| **OB + level confluence** | Order blocks + monitored levels | Order block above/below a key level = higher confidence reversal signal | **MISSING** |

### L2.7 — Top-of-Book Analysis

Derived from the live MBP-1 stream.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **Live spread** | `mbp-1` | ask − bid | HAVE (TopOfBook) |
| **Bid/ask size** | `mbp-1` | Direct from stream | HAVE |
| **Spread widening events** | `mbp-1` | Detect spread > N × normal | MISSING |
| **Passive order absorption** | `mbp-1` over time | Track bid/ask size changes at a level vs trades hitting it | **MISSING** |

### L2.8 — Session Context

Higher-level derived data for daily planning.

| L2 Output | Computed from (L1) | How | Status |
|-----------|-------------------|-----|--------|
| **Initial Balance** | `ohlcv-1m` (9:30–9:45 ET) | High/low of first 15 min RTH | HAVE (scanner) |
| **ATR (N-day)** | `ohlcv-1d` | Average true range over N sessions | PARTIAL |
| **Day type classification** | Volume profile shape + ATR | Normal / Neutral / Trend / Consolidation | PARTIAL (AMT) |
| **Gap size** | Previous close vs current open | Simple diff | HAVE |
| **Overnight range** | ETH bars | High/low of Globex session | PARTIAL |

### L2.9 — Trade Journal (Internal, No L1)

| L2 Output | Source | Status |
|-----------|--------|--------|
| Trade entry/exit log | User input | HAVE |
| R-multiple | `(exit − entry) / (entry − stop)` | HAVE |
| **Trade grade (A/B/C)** | User classification | **MISSING** — not in data model |
| Daily P&L | Sum of closed trades | HAVE |
| Account risk state | Config + daily P&L | HAVE |

---

## Gap Summary

### L1 Gaps (Data to Fetch)

| Priority | What | Schema | Effort |
|----------|------|--------|--------|
| 1 | **1-second bars** | `ohlcv-1s` | Small — add to DabentoProvider |
| 2 | **Open interest** | `statistics` | Small — subscribe + parse `stat_type=OPEN_INTEREST` |
| 3 | Instrument definition | `definition` | Small — one-time fetch, replace hardcoded config |

### L2 Gaps (Data to Compute)

| Priority | What | Input (L1/L2) | Effort |
|----------|------|---------------|--------|
| 1 | **Footprint matrix** | L1 `trades` | Medium — group by candle + price level |
| 2 | **Auction imbalance** | L2 footprint | Small — diagonal comparison, >200% threshold |
| 3 | **Imbalance clusters** | L2 imbalances | Small — scan for 3+ consecutive |
| 4 | **Delta %** | L1 `trades` | Trivial — delta / volume |
| 5 | **OI change tracking** | L1 `statistics` | Small — delta from session open, store per-update |
| 6 | **OI + volume divergence** | L1 `statistics` + `trades` | Medium — compare trends at level proximity |
| 7 | **Order block detection** | L1 `trades` + `ohlcv-1m` | Medium — identify outsized-volume directional candles, mark zones |
| 8 | **Order block confluence** | L2 order blocks + levels | Small — check OB proximity when level is tested |
| 9 | **15-sec bars** | L1 `ohlcv-1s` | Small — resample |
| 10 | **Absorption detection** | L1 `mbp-1` + `trades` | Medium — track passive vs aggressive at levels |
| 11 | **Trade grade A/B/C** | Internal | Trivial — add field to model |

### Build Order

```
L1: ohlcv-1s fetch ─────────────────────────────┐
                                                 ├─→ L2: 15-sec bars
L1: trades (HAVE) ──┬─→ L2: footprint ──┬─→ L2: imbalance ──→ L2: imbalance clusters
                    ├─→ L2: delta %      │
                    ├─→ L2: CVD (HAVE)   │
                    └─→ L2: order blocks ─────→ L2: OB + level confluence
                                         │
L1: mbp-1 (HAVE) ───────────────────────┴─→ L2: absorption detection

L1: statistics (OI) ──→ L2: OI change tracking ──→ L2: OI + volume divergence
```

**Two critical paths:**
1. **trades → footprint → imbalance → clusters** — core orderflow confirmation
2. **statistics → OI tracking → OI/volume divergence** — conviction at levels

Order blocks and absorption are independent and can be built in parallel.
