# ML System Design for BankrollBBQ

## Overview

Machine learning layer for both sports betting and futures trading. Both domains follow the same paradigm: fair value baseline → edge detection → confirmation scoring → risk-managed sizing. ML replaces hardcoded thresholds and fixed scoring weights with learned models, starting with data collection from day one and progressively unlocking more capable models as data accumulates.

**Key principle:** Log continuous values, not just thresholds. Log sequences, not just snapshots. Start collecting everything now — models train later, but missing data is gone forever.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    ML Feature Store                      │
│  (SQLite tables: ml_features, candle_snapshots,          │
│   economic_events, news_impact, options_flow)             │
├──────────────────────┬──────────────────────────────────┤
│   Sports Betting     │         Futures Trading           │
│                      │                                    │
│  M1: Edge Quality    │  M5: Setup Score Predictor        │
│  M2: Limit Predictor │  M6: Temporal Pattern Recognizer  │
│  M3: Devig Selector  │  M7: Dynamic Gate Classifier      │
│  M4: Boost Calibrator│  M9: Macro/News Engine            │
│                      │                                    │
├──────────────────────┴──────────────────────────────────┤
│              M8: Adaptive Kelly Sizing (cross-domain)    │
└─────────────────────────────────────────────────────────┘
```

## Existing Tables Referenced

The following tables already exist in `db/models.py` and are extended (not created) by this spec:

- **`opportunities`** — value bet opportunities from scanner. Has `provider1_id`, `edge_pct`, `market`, etc. We ADD columns to this table (see below).
- **`trading_signals`** — generated trade signals. Has `id`, `signal_type`, `direction`, `score`, `conditions` (JSON array of `{name, score, weight, is_auto, detail}`). We ENRICH the `conditions` JSON.
- **`trades`** — completed trades with `entry_price`, `exit_price`, `r_multiple`, `result`. Used for outcome labeling.
- **`bets`** — placed bets with `odds`, `stake`, `result`. Used for outcome labeling.

The following modules already exist and are integration points (not new code):
- `analysis/scanner.py`, `analysis/devig.py`, `analysis/ev_enrichment.py` — sports betting pipeline
- `market_data/scanner.py`, `market_data/scoring.py`, `market_data/orderflow.py` — trading pipeline
- `bankroll/stake_calculator.py`, `risk/calculator.py`, `risk/features.py` — sizing and risk
- `market_data/cot.py` — COT data fetcher (exists, needs wiring to storage)

The following modules DO NOT exist and must be built:
- Everything under `backend/src/ml/` — the entire ML layer
- `data/economic_calendar.py` — economic event fetcher
- Migration scripts for new tables and columns

## Data Collection Phase (Start Immediately)

### Schema Migration Strategy

SQLite has limited ALTER TABLE support. For new columns on existing tables:

```sql
-- Add columns one at a time (SQLite supports ADD COLUMN)
ALTER TABLE opportunities ADD COLUMN prob_sum REAL;
ALTER TABLE opportunities ADD COLUMN odds_ratio REAL;
-- ... (one ALTER per column, in a migration script)
```

For new tables: standard CREATE TABLE (see below). All migrations will be Python scripts in `backend/src/ml/migrations/` that check `IF NOT EXISTS` / `column_exists()` before applying, making them idempotent and safe to re-run.

### New Database Tables

#### `ml_features`
One row per opportunity (betting) or signal (trading). Stores the full continuous feature vector at decision time.

```sql
CREATE TABLE ml_features (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,              -- 'betting' or 'trading'
    source_id TEXT NOT NULL,           -- opportunity.id or trading_signal.id
    source_type TEXT NOT NULL,         -- 'opportunity', 'signal', 'boost'
    features JSON NOT NULL,            -- full feature dict (see per-model sections)
    feature_version INTEGER NOT NULL DEFAULT 1, -- schema version (increment when features change)
    outcome REAL,                      -- NULL until resolved: CLV for betting, R-multiple for trading
    outcome_binary INTEGER,            -- NULL until resolved: 1=win, 0=loss
    resolved_at DATETIME,
    created_at DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX idx_ml_features_domain ON ml_features(domain);
CREATE INDEX idx_ml_features_source ON ml_features(source_type, source_id);
```

#### `candle_snapshots`
Last 20 candles of orderflow data at signal time. This is the training data for Model 6 (temporal pattern recognition).

```sql
CREATE TABLE candle_snapshots (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER NOT NULL REFERENCES trading_signals(id),
    candles JSON NOT NULL,             -- array of 20 candle objects (see below)
    timeframe TEXT DEFAULT '1m',
    created_at DATETIME DEFAULT (datetime('now'))
);
```

Each candle object in the JSON array:
```json
{
    "ts": "2026-03-12T15:30:00Z",
    "open": 21500.25, "high": 21505.50, "low": 21498.00, "close": 21503.75,
    "volume": 4250,
    "delta": 380,
    "delta_pct": 0.089,
    "cvd": 12500,
    "tick_count": 1820,
    "body_ratio": 0.42,
    "close_position": 0.77,
    "spread_ticks": 30,
    "passive_active_ratio": 1.8,
    "big_trades_count": 12,
    "big_trades_net_delta": 85,
    "vwap_distance_ticks": -8,
    "poc_distance_ticks": 15,
    "imbalance_ratio_max": 3.5,
    "stacked_imbalance_count": 2,
    "stacked_imbalance_direction": "buy"
}
```

#### `economic_events`
Scheduled economic releases with historical surprise data.

```sql
CREATE TABLE economic_events (
    id INTEGER PRIMARY KEY,
    event_name TEXT NOT NULL,           -- 'CPI', 'NFP', 'FOMC', 'PPI', 'Jobless Claims', etc.
    event_datetime DATETIME NOT NULL,
    importance INTEGER NOT NULL,        -- 1=low, 2=medium, 3=high
    forecast REAL,
    actual REAL,
    previous REAL,
    surprise REAL,                      -- actual - forecast (NULL before release)
    created_at DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX idx_econ_events_datetime ON economic_events(event_datetime);
```

#### `news_impact`
Price response to economic events. Populated after each event.

```sql
CREATE TABLE news_impact (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES economic_events(id),
    symbol TEXT NOT NULL DEFAULT 'NQ',
    price_before REAL NOT NULL,        -- 1 min before release
    price_1m REAL,                     -- 1 min after
    price_5m REAL,                     -- 5 min after
    price_15m REAL,
    price_30m REAL,
    price_60m REAL,
    immediate_impact_pct REAL,         -- (price_1m - price_before) / price_before * 100
    sustained_impact_pct REAL,         -- (price_30m - price_before) / price_before * 100
    reversal_pct REAL,                 -- how much of initial move reversed by 60m
    vix_at_event REAL,
    delta_1m_after REAL,               -- net delta in the minute after release
    volume_1m_after REAL,
    created_at DATETIME DEFAULT (datetime('now'))
);
```

#### `options_flow`
Daily options/gamma data for macro context.

```sql
CREATE TABLE options_flow (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,                 -- YYYY-MM-DD
    symbol TEXT NOT NULL DEFAULT 'NQ',
    gex REAL,                          -- gamma exposure estimate
    gex_flip_level REAL,               -- price where gamma flips sign
    net_options_delta REAL,            -- net directional options flow
    put_call_ratio REAL,
    total_options_volume REAL,
    vix_level REAL,
    vix_1d_change REAL,
    vix_term_structure TEXT,           -- 'contango' or 'backwardation'
    dxy_level REAL,
    dxy_1d_change REAL,
    us10y_level REAL,
    us10y_1d_change REAL,
    us02y_level REAL,
    yield_curve_spread REAL,           -- 10Y - 2Y
    es_nq_ratio REAL,                  -- relative strength
    created_at DATETIME DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_options_flow_date ON options_flow(date, symbol);
```

#### `cot_data`
Weekly Commitment of Traders data (separate from daily `options_flow` to respect different frequencies).

```sql
CREATE TABLE cot_data (
    id INTEGER PRIMARY KEY,
    report_date TEXT NOT NULL,          -- YYYY-MM-DD (Tuesday report date)
    symbol TEXT NOT NULL DEFAULT 'NQ',
    net_position INTEGER,              -- net speculative position
    net_change INTEGER,                -- week-over-week change
    long_pct REAL,                     -- % of open interest held long
    short_pct REAL,
    open_interest INTEGER,
    open_interest_change INTEGER,
    created_at DATETIME DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_cot_date ON cot_data(report_date, symbol);
```

### New Columns on Existing Tables

#### `opportunities` table — add:
- `prob_sum REAL` — sum of devigged probabilities at scan time
- `odds_ratio REAL` — provider odds / sharp odds
- `odds_age_minutes REAL` — time since provider last updated
- `sharp_age_minutes REAL` — time since Pinnacle last moved
- `time_to_start_minutes REAL` — minutes until event
- `provider_count INTEGER` — number of providers with odds on this event
- `provider_odds_rank INTEGER` — rank among providers (1 = best price)
- `market_consensus_spread REAL` — std dev of odds across providers
- `pinnacle_overround REAL` — Pinnacle margin on this market
- `closing_line_value REAL` — populated post-event (actual edge vs closing line)

#### `trading_signals` table — enrich `conditions` JSON:
Currently conditions stores `[{name, score, weight, is_auto, detail}]`. Extend each condition with continuous values:

```json
{
    "name": "Delta aligned with direction",
    "score": 10,
    "weight": 0.1,
    "is_auto": true,
    "detail": "Positive delta on bullish setup",
    "continuous": {
        "delta_magnitude": 380,
        "delta_pct_of_volume": 0.089,
        "cvd_slope_5bar": 45.2,
        "cvd_slope_10bar": 32.1,
        "cvd_acceleration": -13.1,
        "volume_ratio_vs_20bar": 1.45,
        "volume_ratio_vs_session": 1.12,
        "body_ratio_last": 0.42,
        "body_ratio_avg_3bar": 0.55,
        "spread_last_ticks": 30,
        "spread_ratio_vs_avg": 1.2,
        "passive_active_ratio": 1.8,
        "trapped_magnitude": 0.35,
        "tick_count_ratio": 1.3,
        "absorption_bar_count": 2,
        "delta_divergence_bars": 3,
        "delta_unwind_speed_bars": 1,
        "big_trades_count": 12,
        "big_trades_net_delta": 85,
        "imbalance_ratio_max": 3.5,
        "stacked_imbalance_count": 2,
        "stop_run_magnitude_ticks": 5,
        "stop_run_volume_ratio": 0.6,
        "distance_to_level_ticks": 3,
        "distance_to_poc_ticks": 15,
        "distance_to_vwap_ticks": -8,
        "price_position_in_va": 0.72,
        "price_vs_vwap_sd": 1.3,
        "ib_range_ticks": 120,
        "ib_range_vs_avg": 0.85,
        "va_width_ticks": 80,
        "va_width_vs_yesterday": 0.92,
        "single_print_count_above": 2,
        "single_print_count_below": 1,
        "nearest_fvg_distance_ticks": 25,
        "nearest_ob_distance_ticks": 40,
        "num_levels_within_20_ticks": 3,
        "minutes_since_rth_open": 45,
        "minutes_since_ib_close": -15,
        "session_volume_total": 1200000000,
        "session_volume_acceleration": 1.35,
        "unfinished_auction_count_above": 1,
        "unfinished_auction_count_below": 0
    }
}
```

---

## Model Specifications

### Model 1: Edge Quality Classifier (Sports Betting)

**Question:** "Is this edge real or noise? Will CLV confirm it?"

**Architecture:** XGBoost binary classifier

**Min training data:** 200 bets with CLV tracking

**Replaces:** Hardcoded `MIN_VALID_PROB_SUM=0.90`, `MAX_ODDS_RATIO=1.35`, `MAX_ODDS_AGE_HOURS=2`, `MAX_EDGE_PCT=50.0`

**Features:**

Market context:
- `edge_pct` — raw edge vs Pinnacle
- `prob_sum` — sum of devigged probabilities (market completeness)
- `odds_ratio` — provider odds / sharp odds (mismatch proxy)
- `odds_age_minutes` — time since last provider update
- `sharp_age_minutes` — time since Pinnacle last moved
- `time_to_start_minutes` — minutes until event starts
- `pinnacle_overround` — Pinnacle's current margin on this market
- `num_providers_with_odds` — how many soft books price this event
- `provider_odds_rank` — is this the best soft price (1) or 5th best (5)?
- `odds_movement_direction` — provider line moving toward or away from sharp?
- `odds_movement_magnitude` — how much has it moved in last 30 min?
- `sharp_line_stability` — Pinnacle movement rate (volatile = less reliable)
- `market_consensus_spread` — std dev of odds across all providers (tight = efficient)

Provider context:
- `provider_platform` — Kambi/Altenar/Gecko/etc (one-hot encoded)
- `is_platform_outlier` — does this provider disagree with its own platform siblings?
- `provider_historical_clv_avg` — this provider's average CLV over last 30 days
- `provider_update_frequency` — how often does this provider typically update?
- `provider_match_rate` — extraction health proxy

Event context:
- `sport` — categorical (football, basketball, tennis, etc.)
- `league_liquidity_proxy` — number of providers offering this league
- `market_type` — 1x2/moneyline/spread/total
- `spread_point` / `total_point` — the actual line
- `home_team_popularity_proxy` — derived from average odds spread

Temporal:
- `hour_of_day` — some times have staler odds (overnight)
- `day_of_week` — weekend lines behave differently
- `minutes_since_extraction` — freshness of this scan

**Target:** `closing_line_value > 0` (binary: edge was real)

**Prediction output:** Probability that edge is real. Replace fixed thresholds with: `if model_1_prob > 0.5: accept_bet()`

---

### Model 2: Provider Limit Predictor (Sports Betting)

**Question:** "How many more bets can I place at this provider before limiting?"

**Architecture:** XGBoost regression (predicted bets remaining) or Cox proportional hazards (survival curve)

**Min training data:** 10-20 limit events across providers. **Low-data strategy:** With so few positive examples, start with logistic regression on 3-5 features (`clv_score`, `total_bet_count_at_provider`, `max_single_bet_edge`, `bet_frequency_trend`, `similar_platform_limit_count`) rather than XGBoost. Graduate to XGBoost only after 50+ limit events. Use strong L2 regularization and leave-one-out cross-validation.

**Replaces:** Linear weighted risk score in `risk/calculator.py`

**Features:**

Betting pattern (from existing `risk/features.py`):
- `stake_entropy` — coefficient of variation + round number ratio
- `market_diversity` — sport/league concentration (Herfindahl index)
- `timing_regularity` — hour/day entropy
- `outcome_correlation` — hedging detection
- `clv_score` — average positive CLV (THE #1 limiter signal)
- `win_rate_deviation` — vs expected from odds
- `bonus_usage_ratio` — % of bets using bonuses

New features to add:
- `avg_stake_vs_provider_median` — are your stakes unusually large for this book?
- `bet_frequency_trend` — increasing/decreasing bet rate (ramp-up looks suspicious)
- `max_single_bet_edge` — max edge on any single bet (flags sharp bettor instantly)
- `sport_concentration_top3` — % of bets in top 3 sports
- `time_between_bets_cv` — very regular = bot-like
- `time_from_odds_change_to_bet` — betting immediately after line moves = sharp signal
- `same_side_as_sharp_movement_pct` — how often bets align with subsequent sharp movement
- `account_age_days`
- `total_bet_count_at_provider`
- `total_turnover_at_provider`
- `has_used_freebet` — bonus abusers get flagged
- `similar_platform_limit_count` — if limited at 3 Kambi books, #4 may share data
- `deposit_withdrawal_ratio`

**Target:** Estimated bets remaining before limit (regression) or binary limited/not

**Prediction output:** "You have ~40 bets left at Betsson" — used for action routing and Kelly scaling

---

### Model 3: Devig Method Selector (Sports Betting)

**Question:** "Which devigging method gives most accurate fair odds for this market?"

**Architecture:** Multi-class XGBoost (3 classes: multiplicative, additive, power/Shin)

**Min training data:** 500 bets across sports/markets

**Replaces:** Hardcoded multiplicative devigging everywhere

**Features:**
- `sport` — tennis devig differs from football
- `market_type` — 1x2 vs spread vs total
- `num_outcomes` — 2-way vs 3-way
- `pinnacle_overround` — low margin = less method sensitivity
- `favourite_odds` — extreme favourites devig differently
- `odds_range` — max odds minus min odds in market
- `league_tier` — top leagues have tighter lines
- `market_age_hours` — early lines may have different margin structure
- `has_draw_option` — 3-way markets need different treatment

**Target:** For each resolved bet, compute CLV under all three methods (multiplicative, additive, power/Shin). Label with the method whose absolute CLV is closest to 0 on average for that sport/market combination. This requires storing all three devigged fair odds at bet time (a one-time expansion of the feature vector), then resolving against closing line to determine which method was most accurate.

**Prediction output:** `{"method": "multiplicative", "confidence": 0.82}` — used in `analysis/devig.py`

---

### Model 4: LLM Boost Calibrator (Sports Betting)

**Question:** "How much should I trust this LLM probability estimate?"

**Architecture:** Isotonic regression or Platt scaling on top of LLM output

**Min training data:** 100 resolved boosts

**Clarification:** The LLM still does all research (Brave Search for player stats, team form, injuries, head-to-head records). This model only calibrates the LLM's probability output based on historical accuracy patterns.

**Features:**
- `llm_raw_probability` — the LLM's estimate
- `llm_confidence` — self-reported confidence (1-5)
- `boost_type` — single / 2-leg combo / 3-leg combo (player props are filtered out by PROP_KEYWORDS before reaching this model)
- `sport`, `league`
- `num_legs` — more legs = more compounding error
- `has_pinnacle_match` — could we verify any legs against sharp odds?
- `pinnacle_implied_prob` — ground truth for matched legs (if available)
- `legs_matched_ratio` — fraction of combo legs with Pinnacle pricing
- `original_odds` — bookmaker's pre-boost price (their estimate)
- `boosted_odds` — the boosted price
- `boost_margin` — how much did they boost it? (small boost = probably already near +EV)
- `keyword_flags` — "anytime scorer", "both teams", "over X.5" (some bet types systematically mispriced)
- `hours_to_event` — boosts posted early may have stale research
- `provider` — some books boost more aggressively than others
- `day_of_week` — weekend boosts on popular games vs midweek niche
- `llm_reasoning_length` — proxy for research depth (short = less data found)
- `brave_results_count` — how much data was available for research

**Target:** Actual outcome (win=1, loss=0). Calibration model outputs adjusted probability.

**Prediction output:** `calibrated_probability = f(llm_probability, features)` — retrained weekly as results come in

---

### Model 5: Setup Score Predictor (Trading)

**Question:** "What's the expected R-multiple for this signal given all context?"

**Architecture:** LightGBM gradient boosted trees (regression for R-multiple, classification for win/loss)

**Min training data:** 200 trades (meaningful signal at 50)

**Replaces:** Fixed +10/+8/+5 scoring weights in `scoring.py`. The key advantage: learns interactions like "spring + low_ASPR + delta_divergence = 3.2R average" vs "spring + high_RF + no_divergence = -0.5R average"

**Features:**

Setup identity:
- `setup_type` — spring, sfp, poor_extreme, ib_break, rule_80, fakeout, break_from_balance, double_distribution, news_directional (categorical)
- `direction` — long/short
- `level_type_touched` — poc, vah, val, vwap, vwap_2sd, ib_high, ib_low, order_block, fvg, pdh, pdl, tokyo, london, single_print, ledge (categorical)

Orderflow features (CONTINUOUS, not boolean):
- `delta_magnitude` — raw delta value
- `delta_pct_of_volume` — normalized delta strength (delta / volume)
- `cvd_slope_5bar` — slope of cumulative delta over last 5 bars
- `cvd_slope_10bar` — slope over last 10 bars
- `cvd_acceleration` — change in CVD slope (flattening or steepening?)
- `volume_ratio_vs_20bar_avg` — how unusual is current volume?
- `volume_ratio_vs_session_avg` — vs today's average
- `body_ratio_last_bar` — close position within range (0=doji, 1=full body)
- `body_ratio_avg_3bar` — recent candle conviction
- `spread_last_bar_ticks` — high minus low in ticks
- `spread_ratio_vs_avg` — expansion or contraction?
- `passive_active_ratio` — limit order absorption ratio (continuous)
- `trapped_magnitude` — how much delta unwound (continuous, not boolean)
- `tick_count_ratio_vs_avg` — activity level
- `absorption_bar_count` — consecutive absorption bars (1=weak, 3=strong)
- `delta_divergence_bars` — how many bars has price/delta disagreed?
- `delta_unwind_speed_bars` — how fast did delta flip? (1 bar=violent, 5 bars=gradual)

Footprint / institutional flow:
- `imbalance_ratio_max` — highest buy:sell or sell:buy ratio at any price level in last 5 bars (Fabio's 300-400% threshold becomes a continuous feature)
- `stacked_imbalance_count` — consecutive price levels with imbalance in same direction
- `stacked_imbalance_direction` — buy/sell/none
- `unfinished_auction_count_above` — price levels above current with zero contracts on one side
- `unfinished_auction_count_below` — same below
- `big_trades_count_5bar` — number of 30+ contract trades in last 5 bars
- `big_trades_net_delta_5bar` — net direction of institutional-size trades
- `big_trades_at_level` — did large trades cluster at the triggering level?
- `stop_run_magnitude_ticks` — how far past level did price penetrate before reversing
- `stop_run_volume_ratio` — volume during stop run vs average (low = spring, high = breakout)
- `session_volume_total` — total session volume (Fabio's "1 billion... 2 billion" conviction metric)
- `session_volume_acceleration` — rate of volume growth (how fast is participation increasing?)

Market structure:
- `distance_to_level_ticks` — how far from the triggering level
- `distance_to_poc_ticks` — distance to session POC
- `distance_to_vwap_ticks` — distance to VWAP
- `price_position_in_va` — 0=at VAL, 0.5=at POC, 1.0=at VAH (continuous)
- `price_vs_vwap_sd` — how many SDs from VWAP (continuous, not band-based)
- `ib_range_ticks` — today's IB width
- `ib_range_vs_avg` — is IB narrow (compression) or wide (expansion)?
- `va_width_ticks` — today's value area width
- `va_width_vs_yesterday` — expanding or contracting?
- `single_print_count_above` — unfilled gaps above price
- `single_print_count_below` — unfilled gaps below
- `nearest_fvg_distance_ticks` — magnet proximity
- `nearest_ob_distance_ticks` — order block proximity
- `num_levels_within_20_ticks` — level congestion (many levels = choppy)
- `num_levels_within_50_ticks` — wider context

Session context:
- `rotation_factor` — per-period trend measure
- `aspr` — raw value
- `aspr_percentile` — vs 1-year baseline
- `aspr_vs_yesterday` — expanding or contracting day-over-day?
- `market_type` — balanced/trending_up/trending_down (categorical)
- `opening_type` — OD/OTD/ORR/OA (categorical)
- `poor_high`, `poor_low` — thin extremes (boolean)
- `value_migration` — up/down/overlapping (categorical)
- `minutes_since_rth_open` — time of day (first 30 min ≠ lunch ≠ close)
- `minutes_since_ib_close` — time since IB established
- `bars_since_last_level_touch` — recency of structure interaction
- `tpo_distribution_type` — normal/p_shape/b_shape/double (categorical)
- `tpo_letter_count` — how developed is the profile (A-D=early, A-M=mature)
- `is_macro_window` — within 10 min before/after the hour (high-probability reversal zones)

Multi-timeframe:
- `yesterday_market_type` — (trend followed by normal = different from 2 trend days)
- `yesterday_close_vs_va` — closed inside or outside VA?
- `overnight_range_vs_avg` — big overnight move = gap risk
- `overnight_direction` — gap up / gap down / flat
- `3day_value_migration_trend` — sustained migration = trend, flip = reversal
- `weekly_rf_trend` — is rotation increasing or decreasing this week?

Macro (from Model 9):
- `vix_level`, `vix_change_1d`
- `gex` — gamma exposure (negative = amplifies moves)
- `gex_flip_distance_ticks` — distance to GEX flip level
- `net_options_delta` — directional options flow
- `dxy_change_1d`, `us10y_change_1d`
- `yield_curve_spread`
- `es_nq_ratio_change` — rotation between indices
- `news_event_minutes_away` — proximity to scheduled release
- `news_event_importance` — 1/2/3
- `post_news_minutes` — if news already dropped, how long ago?
- `cot_net_position` — Commitment of Traders net (weekly)
- `cot_change_1w` — direction of institutional positioning change

**Feature staging (p >> n mitigation):** With ~70 features and min 200 trades, start with 15-20 highest-prior features (setup_type, direction, delta_pct_of_volume, cvd_slope_5bar, volume_ratio_vs_20bar_avg, distance_to_level_ticks, price_position_in_va, ib_range_vs_avg, minutes_since_rth_open, vix_level, gex). Add remaining features in batches of 10 as data grows past 500/1000 trades. Use LightGBM's built-in feature importance to prune zero-contribution features.

**Target:** R-multiple of resulting trade (regression) or win/loss (classification)

**Prediction output:** Predicted R-multiple + confidence interval. Replaces composite score.

---

### Model 6: Temporal Pattern Recognizer (Trading)

**Question:** "Given the last 20 candles of orderflow, is a reversal/continuation imminent?"

**Architecture:** 1D-CNN (preferred for speed) or LSTM. Small model, runs in <10ms.

**Min training data:** 500 trades with candle snapshots. Start collecting data NOW.

**Input:** Sequence of candle features, shape (20, N) where N = features per candle:
- `delta` — buy vol minus sell vol
- `delta_pct` — delta / volume (normalized)
- `cvd` — running cumulative delta
- `volume` — total volume
- `volume_ratio` — vs session average
- `spread_ticks` — high minus low
- `body_ratio` — (close - open) / (high - low)
- `close_position` — where close sits in range (0=low, 1=high)
- `tick_count` — number of transactions
- `passive_active_ratio` — limit vs market orders
- `vwap_distance_ticks` — ticks from VWAP
- `poc_distance_ticks` — ticks from POC
- `imbalance_ratio_max` — highest per-level buy:sell ratio in this bar
- `stacked_imbalance_count` — stacked imbalances in this bar
- `big_trades_count` — institutional-size trades in this bar
- `big_trades_net_delta` — net direction of large trades

**Patterns it should learn to recognize:**

Reversal patterns:
1. **Failed auction** — price probes beyond level on declining volume/delta → absorption bar (high vol, small body, stacked imbalances from passive side) → delta unwind (sign flip) → CVD trend reversal → snap-back on increasing volume
2. **Exhaustion spike** — massive delta bar in trend direction → immediate opposite delta → volume drops → price stalls → reversal candle closes opposite
3. **Absorption sequence** — 2-3 bars with high volume but no price progress (passive orders absorbing via high passive_active_ratio) → delta diverges → breakaway opposite direction
4. **Trapped trader flush** — break of level → initial delta aligned → sudden delta flip with high magnitude → rapid retracement → continuation past original level
5. **Slow grind reversal** — CVD gradually flattening while price continues trending → volume declining per-bar → tick count dropping → sudden volume spike opposite direction
6. **Double tap rejection** — price tests same level twice → second test has lower delta commitment → faster rejection → wider spread reversal candle
7. **Iceberg absorption** — repeated high passive_active_ratio at same price → aggressive side can't move price → multiple bars of stacking volume → eventual capitulation and reversal

Continuation patterns:
8. **Breakout confirmation** — break of IB/VA → pullback to boundary on low volume → delta stays aligned on pullback (no flip) → tick acceleration on bounce → continuation
9. **Stair-step trend** — each pullback holds higher low → delta positive on each push → CVD steadily rising → volume expands on pushes, contracts on pullbacks
10. **Re-accumulation** — after impulse move → tight range forms → volume contracts → delta oscillates near zero → breakout candle with volume + delta alignment + stacked imbalances
11. **News momentum** — massive volume spike + directional delta → brief pause (1-3 bars) → continuation with renewed delta (not absorption). Negative gamma amplifies.
12. **Stacked imbalance breakout** — 3+ consecutive price levels with 300%+ buy:sell ratio → immediate continuation in imbalance direction

Trap/fakeout patterns:
13. **Stop hunt and reverse** — spike through level on high tick count → immediate opposite delta flood → close back inside → expansion opposite way
14. **Delta divergence break** — price breaks key level → delta doesn't confirm (opposite sign or weak) → volume declining on "break" → reversal
15. **Volume void traverse** — price moves quickly through single-print/FVG zone → no resting orders → velocity spike → snap-back to originating value area when next volume cluster found
16. **Liquidity cascade** — head fake triggers stops → removed take-profits on opposing side reduce floor → breakout orders trigger on other side → cascading sell/buy pressure (from Block Roots analysis)

Regime/session patterns:
17. **Session volume inflection** — volume doubling within 30 min (1B → 2B) = regime shift from balance to trend
18. **Negative gamma acceleration** — post-level-break, price accelerates as market makers hedge. Identified by: increasing volume + increasing delta magnitude + expanding spread per bar
19. **Macro window reversal** — within 10-min window around the hour, price finds support/resistance at FVG and reverses. Different pattern profile than non-macro-window reversals.

**Target options:**
- Classification: next 5 bars direction (up/down/chop)
- Regression: max favorable excursion in next 10 bars (R-potential)
- Sequence-to-one: probability of reversal within N bars

**Prediction output:** `{direction: "reversal_long", probability: 0.78, pattern_match: "failed_auction", confidence: 0.85}`

---

### Model 7: Dynamic Gate Classifier (Trading)

**Question:** "What day type is this? What's the macro regime?"

**Architecture:** Random Forest multi-class

**Min training data:** 100 labeled sessions

**Replaces:** Manual Gate 3 (day type) initially. Eventually Gate 1 (macro bias) and Gate 2 (structure).

**Day type classification features:**
- `rf_after_ib` — rotation factor in first 60 minutes
- `ib_range_vs_aspr_baseline` — wide IB = possible trend, narrow = possible neutral
- `opening_type` — OD strongly suggests trend (categorical)
- `first_hour_delta_total` — directional conviction in first hour
- `first_hour_volume_vs_avg`
- `value_migration_direction` — up/down/overlapping
- `overnight_range_pct` — big gap = volatile open
- `gap_filled_pct` — how much of overnight gap filled in first 30 min
- `yesterday_market_type` — (trend → normal is common)
- `ib_tpo_count` — lots of TPO letters in IB = balance forming early
- `poor_high_or_low_in_ib` — thin extreme in first hour
- `first_hour_big_trades_count` — institutional activity level
- `session_volume_first_hour` — above or below normal
- `vix_level` — high VIX = more trend days
- `gex` — negative gamma = more trend days

**Target:** Day type label from `market_context.day_type` (trend/normal/normal_variation/neutral/composite)

**Macro regime classification features:**
- `vix_level`, `vix_5d_change`, `vix_term_structure`
- `dxy_level`, `dxy_5d_change`
- `us10y_level`, `us10y_5d_change`
- `yield_curve_spread`, `yield_curve_5d_change`
- `cot_net_position`, `cot_1w_change`
- `es_nq_ratio_5d_change`
- `5day_value_migration_trend`
- `weekly_rf_avg`
- `gex_level`
- `geopolitical_risk_score` — from LLM classification of news headlines (see below)

**Target:** Macro bias label from `market_context.macro_bias` (bull/bear/neutral)

**Progression:** Starts as suggestion ("looks like a trend day"), auto-classifies as accuracy improves above 80%.

---

### Model 8: Adaptive Kelly Sizing (Cross-Domain)

**Question:** "What fraction of Kelly should I use for this specific opportunity?"

**Architecture:** XGBoost regression

**Min training data:** 300 bets/trades

**Replaces:** Linear Kelly interpolation by edge (sports) / fixed 1% risk (trading)

**Features:**

Shared:
- `model_confidence` — confidence from upstream model (M1/M5/M6)
- `predicted_edge` (betting) or `predicted_r_multiple` (trading)
- `historical_win_rate` — for this provider (betting) or setup type (trading)
- `historical_avg_r` — for this provider/setup
- `recent_drawdown_pct` — last 5 trades/bets cumulative
- `consecutive_wins` / `consecutive_losses`
- `daily_pnl_current` — already up or down today
- `weekly_pnl_current`
- `account_utilization` — % of daily budget used
- `time_of_day` — late-session trades may warrant smaller size
- `volatility_regime` — ASPR percentile (trading) or sharp line stability (betting)

Sports-specific:
- `provider_remaining_lifetime_est` — from Model 2 (don't blow budget on nearly-limited accounts)
- `is_freebet` — different Kelly for freebets (100% retention target)
- `bonus_wagering_remaining` — adjust for bonus clearing

Trading-specific:
- `setup_type`
- `gex` — negative gamma = wider moves = potentially scale down
- `correlation_with_open_positions` — if already long NQ, size another long differently
- `session_volume_regime` — high volume = more reliable signals

**Target:** Optimal Kelly fraction (backtest-derived via walk-forward optimization)

**Prediction output:** `kelly_fraction = 0.35` — applied to standard Kelly formula

---

### Model 9: Macro & News Context Engine (Trading)

**Question:** "What is the current macro regime and how should it affect trading decisions?"

**Architecture:** Multi-component — XGBoost for regime classification, lookup tables for news impact, LLM for qualitative analysis

**Data sources to ingest:**

Scheduled economic events:
- Economic calendar API — FOMC, NFP, CPI, PPI, jobless claims, GDP, retail sales, ISM, etc.
- Fields: event_name, datetime, importance (1-3), forecast, actual, previous, surprise
- Ingested daily into `economic_events` table

Market regime indicators (fetched daily/hourly):
- VIX — level + 1d/5d change + term structure (contango/backwardation)
- DXY — dollar index level + change
- US10Y, US02Y — yields + yield curve spread
- ES/NQ ratio — relative strength between indices
- GEX — gamma exposure estimate
- Put/call ratio + net options volume direction

Commitment of Traders (weekly):
- Already have `cot.py` fetcher — wire output to `cot_data` table (separate from daily `options_flow`)
- Net position + weekly change for NQ/ES

Cross-asset correlations (rolling 20-day):
- NQ vs ES, NQ vs DXY, NQ vs US10Y
- When correlations break from norm = regime change signal

Institutional report sentiment (weekly, **aspirational** — requires data source):
- LLM reads macro summaries from free sources (Fed speeches, FOMC minutes, public bank commentary on X/blogs)
- Paid bank research (GS, JPM, BofA) can be added later if subscriptions are available
- Outputs: sentiment_score (-1 to +1), key_themes (list), risk_factors (list)
- Stored in dedicated `macro_sentiment` table

Geopolitical risk:
- LLM classifies weekly news headlines for geopolitical risk level (1-10)
- Factors: wars/conflicts, trade policy (tariffs, BRICS), central bank actions, election cycles
- Not a prediction — just a risk regime classification

**How news flows through the system:**

```
Economic Calendar API → economic_events table (daily fetch)
    ↓
Pre-event (T-30 min):
    - Model 5 receives: news_event_minutes_away, importance
    - Scanner can reduce confidence for continuation setups (news invalidation risk)
    - Scanner boosts news_directional setup scoring
    ↓
Post-event (actual released):
    - Compute surprise = actual - forecast
    - Store in economic_events
    - Capture NQ price at T+1/5/15/30/60 → news_impact table
    - Model 6 (temporal) processes the volume/delta spike pattern
    ↓
Historical accumulation:
    - news_impact table grows: event_type × surprise × NQ response
    - Model learns: "hot CPI + VIX > 20 + downtrend = sustained selloff"
    - Model learns: "NFP miss + VIX < 15 + uptrend = dip-buy within 30min"
    - Model learns: "FOMC rate hold + negative gamma = 2SD move either way"
```

**Session-specific norms:**

Different sessions have different volume/volatility norms (from your notes: "Asian session apparently worst but I trade it profitably"):
- Asian (20:00-02:00 ET): Low volume, range-bound, macro window reversals dominate
- London (03:00-08:30 ET): Increasing volume, trend initiation common
- NY open (09:30-10:30 ET): Highest volume, IB formation, most setups fire
- NY midday (10:30-14:00 ET): Declining volume, mean-reversion dominant
- NY close (14:00-16:00 ET): Position squaring, gamma effects amplified

Each session has different baseline expectations for the features. Model 5 can learn this via `minutes_since_rth_open`, but explicit session encoding helps.

---

## Progressive Unlock Timeline

| Data Milestone | Models Unlocked | Impact |
|---|---|---|
| Day 1 | None — collecting features | Feature store populating with every scan/signal |
| 50 trades / 200 bets | M1 (edge quality), M4 (boost calibration) | Filter bad edges, calibrate LLM boosts |
| 100 sessions | M7 (day type classifier) | Auto-suggest day type (replaces manual Gate 3) |
| 200 trades / 500 bets | M2 (limit prediction), M3 (devig selector), M5 (setup scorer) | Learned scoring weights, provider lifetime estimates, optimal devig |
| 50 news events | M9 (news impact patterns) | Pre/post-news scoring adjustments |
| 300 trades/bets | M8 (adaptive Kelly) | Dynamic position sizing across both domains |
| 500 trades + candle data | M6 (temporal patterns) | Failed auction detection, squeeze patterns, fakeout recognition |
| 1000+ trades | Full system | All models mature, cross-domain optimization, auto-gates |

---

## Training Pipeline

### Data Preparation
- Feature extraction runs at scan/signal time → writes to `ml_features`
- Outcome labeling runs post-event (CLV for bets, R-multiple for trades)
- Weekly batch job joins features + outcomes for training

### Model Training
- **Walk-forward cross-validation:** train on months 1-3, validate on month 4, slide forward by 1 month
  - Minimum training window: 2 months (shorter = skip this fold)
  - Step size: 1 month
  - **Purge/embargo:** 24-hour gap between training end and validation start to prevent data leakage from correlated events (a bet placed at 23:59 shouldn't validate against an event starting at 00:01)
  - Final metric: average across all folds (not single best fold)
- No future data leakage — all features are point-in-time
- Hyperparameter tuning via Optuna with Bayesian optimization
- For M6 (temporal patterns, PyTorch): z-score normalization per feature across each 20-candle window before inference

### Model Versioning & Rollback
- Models serialized to `backend/data/models/` with version naming: `{model_name}_v{N}.joblib` (or `.pt` for PyTorch)
- **Model registry table** in SQLite:
  ```sql
  CREATE TABLE ml_model_registry (
      id INTEGER PRIMARY KEY,
      model_name TEXT NOT NULL,          -- 'edge_quality', 'setup_scorer', etc.
      version INTEGER NOT NULL,
      file_path TEXT NOT NULL,           -- relative path to serialized model
      training_data_count INTEGER,       -- how many samples trained on
      validation_metric REAL,            -- primary metric on validation fold
      baseline_metric REAL,              -- rules-based baseline on same data
      is_active INTEGER DEFAULT 0,       -- 1 = currently serving
      created_at DATETIME DEFAULT (datetime('now'))
  );
  ```
- **Auto-rollback:** After training, if new model's validation metric is worse than the active model (or worse than rules-based baseline), the new version is saved but NOT activated. Alert logged. Previous version stays active.
- **Manual override:** API endpoint to activate/deactivate any model version
- Keep last 5 versions per model; prune older versions automatically

### Model Serving
- Models loaded at backend startup from `ml_model_registry` (active versions only)
- Prediction runs inline during scan/signal generation
- **Latency budget:** M6 (temporal) must run in <10ms — use 1D-CNN with max 64 hidden units, not LSTM. If latency exceeds budget, fall back to rules.
- Fallback to rules-based scoring if model unavailable or confidence below threshold
- A/B testing: log both model prediction and rules-based score, compare outcomes
- **A/B significance:** Require minimum 100 predictions with outcomes before evaluating switchover. Use one-sided binomial test (p < 0.05) that ML beats rules-based on the primary metric.

### Monitoring
- Track Brier score (calibration) and log-loss per model per week
- Alert if model accuracy degrades below rules-based baseline → auto-rollback to previous version
- Retrain weekly with expanding training window

### Data Retention
- `ml_features`: Retain indefinitely (primary training data, growth ~2K rows/day = ~730K/year, manageable for SQLite)
- `candle_snapshots`: Retain indefinitely (smaller volume, ~50 rows/day)
- `news_impact`, `economic_events`: Retain indefinitely (low volume)
- `options_flow`: Retain indefinitely (~365 rows/year)
- If SQLite performance degrades beyond 2M rows in `ml_features`, add date-based partitioning (archive pre-2-year data to `ml_features_archive`)

---

## Technology Stack

- **ML framework:** scikit-learn + LightGBM/XGBoost (tabular models), PyTorch (temporal model M6)
- **Feature store:** SQLite tables (same DB, no new infrastructure)
- **Training orchestration:** Simple Python scripts in `backend/src/ml/`
- **Model serialization:** joblib (sklearn/xgboost), torch.save (PyTorch)
- **No external services** — everything runs locally, same as the rest of the system

### Directory Structure
```
backend/src/ml/
├── features/
│   ├── betting_features.py      # Extract features for M1-M4
│   ├── trading_features.py      # Extract features for M5-M8
│   ├── candle_features.py       # Extract candle snapshots for M6
│   ├── macro_features.py        # Fetch and store macro data for M9
│   └── store.py                 # Write/read ml_features table
├── models/
│   ├── edge_quality.py          # M1
│   ├── limit_predictor.py       # M2
│   ├── devig_selector.py        # M3
│   ├── boost_calibrator.py      # M4
│   ├── setup_scorer.py          # M5
│   ├── temporal_patterns.py     # M6
│   ├── gate_classifier.py       # M7
│   ├── adaptive_kelly.py        # M8
│   └── macro_engine.py          # M9
├── training/
│   ├── train_all.py             # Weekly training orchestrator
│   ├── evaluate.py              # Walk-forward evaluation
│   └── monitor.py               # Accuracy tracking + alerts
├── serving/
│   └── predictor.py             # Load models + serve predictions
└── data/
    └── economic_calendar.py     # Fetch economic events
```

---

## Integration Points

### Sports Betting Scanner (`analysis/scanner.py`)
- After scanning, call `betting_features.extract()` → write to `ml_features`
- If M1 trained: replace hardcoded thresholds with `edge_quality.predict(features)`
- If M3 trained: call `devig_selector.predict()` before devigging in `analysis/devig.py`

### Sports Betting Specials (`analysis/ev_enrichment.py`)
- After LLM enrichment, call `boost_calibrator.calibrate(llm_prob, features)`
- Replace raw LLM probability with calibrated probability

### Sports Betting Risk (`risk/calculator.py`)
- Replace linear risk score with `limit_predictor.predict(features)`
- Feed provider lifetime estimate to `adaptive_kelly.predict()`

### Trading Scanner (`market_data/scanner.py`)
- At signal generation, call `trading_features.extract()` → write to `ml_features`
- Call `candle_features.snapshot()` → write to `candle_snapshots`
- If M5 trained: replace fixed scoring with `setup_scorer.predict(features)`
- If M6 trained: call `temporal_patterns.predict(candle_sequence)` as additional feature for M5

### Trading Gates (`market_data/scoring.py`)
- If M7 trained: call `gate_classifier.predict_day_type()` as suggestion
- Surface as "ML suggests: trend day (78% confidence)" alongside manual gate

### Position Sizing (`bankroll/stake_calculator.py` + trading risk)
- If M8 trained: replace linear Kelly with `adaptive_kelly.predict(features)`
- Applies to both sports betting stakes and trading contract sizing

### Macro Context (`market_data/` + API routes)
- Daily fetch: economic calendar, VIX, DXY, yields, options flow → `options_flow` table
- Weekly fetch: COT data → existing `cot.py` + store
- Post-news: capture price responses → `news_impact` table
- Serve macro snapshot to frontend via existing `/api/trading/market/macro` endpoint

---

## Key Design Decisions

1. **SQLite, not a separate ML database.** Everything stays in the same DB. No new infrastructure, no deployment complexity. SQLite handles the volume fine (thousands of features/day, not millions).

2. **Continuous values alongside booleans.** The scoring system keeps working as-is. The `continuous` dict in conditions JSON is additive — old code ignores it, ML code reads it. Zero breaking changes.

3. **Gradual replacement, not big bang.** Each model can be enabled independently. Rules-based scoring is the fallback. A/B testing ensures ML only takes over when it demonstrably beats heuristics.

4. **LLM stays for research, ML for calibration.** The LLM's job (browsing player stats, injury news, team form via Brave Search) can't be replaced by tabular ML. ML calibrates the LLM's probability output, not the research process.

5. **Cross-domain Kelly is one model.** Kelly math is Kelly math. The features differ (provider risk vs setup type) but the optimization objective is identical: maximize log-wealth growth rate. One model serves both.

6. **Candle snapshots are the most valuable new data.** This is the data that captures what you learn manually — "in a failed auction, this is what the indicators do before reversal." Without these snapshots, Model 6 can never be trained. Start collecting from day one.

7. **No psych features.** Sleep/focus/emotional scores are not quantifiable ML inputs. The psych gate remains as a manual pre-trade ritual, not an ML feature.
