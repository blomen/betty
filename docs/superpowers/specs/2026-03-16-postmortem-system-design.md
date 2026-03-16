# Postmortem System Design

**Date:** 2026-03-16
**Status:** Draft

## Overview

Automated post-settlement analysis system for both betting and trading. Classifies outcomes, computes quality metrics, and surfaces patterns from historical data. No manual journaling for bets — purely data-driven. Trading adds automated metrics alongside existing manual TradeReview.

## Decisions

- **Betting:** Automated metrics only — no manual review prompts
- **Trading:** Both — keep TradeReview manual journal + add automated analytics layer
- **Output:** Dashboard/report pages for periodic review — no real-time placement warnings
- **Architecture:** Batch materialization — new tables populated after settlement, recomputable on demand

## Data Model

### BetPostmortem Table

One row per settled bet (1:1 with Bet). Primary key is `bet_id` (FK to bets.id).

| Column | Type | Description |
|--------|------|-------------|
| bet_id | FK → bets.id | Primary key |
| classification | VARCHAR | expected_loss, edge_erosion, false_edge, sizing_error, expected_win, bonus_win |
| edge_at_placement | FLOAT | Derived: `(bet.odds / bet.fair_odds_at_placement - 1) * 100` |
| clv_pct | FLOAT | Copied from `bet.clv_pct` (= `(bet.odds / bet.closing_odds - 1) * 100`) |
| clv_confirmed | BOOLEAN | True if closing odds available AND `(event.start_time - bet.placed_at) <= 12h` |
| expected_win_pct | FLOAT | Implied probability: `1 / bet.fair_odds_at_placement` |
| kelly_fraction | FLOAT | `actual_stake / kelly_optimal_stake` — uses current profile bankroll (approximation, see notes) |
| is_oversized | BOOLEAN | kelly_fraction > 1.5 |
| is_undersized | BOOLEAN | kelly_fraction < 0.5 |
| variance_score | FLOAT | On win: `1 - expected_win_pct`, on loss: `expected_win_pct` (0=expected, 1=max surprise) |
| computed_at | DATETIME | When this row was last computed |
| version | INTEGER | Recomputation count (tracks reclassifications) |

### TradePostmortem Table

One row per closed trade (1:1 with Trade). Primary key is `trade_id` (FK to trades.id).

| Column | Type | Description |
|--------|------|-------------|
| trade_id | FK → trades.id | Primary key |
| classification | VARCHAR | expected_loss, stop_too_wide, thesis_invalid, expected_win, runner |
| r_multiple | FLOAT | Copied from trade for fast queries |
| setup_avg_r | FLOAT | Average R for this setup type at time of compute |
| setup_win_rate | FLOAT | Win rate for this setup type |
| stop_quality | VARCHAR | optimal, too_wide (too_tight deferred — requires post-close price data) |
| target_quality | VARCHAR | hit_target, partial_exit_good, missed_runner, exited_early |
| streak_position | INTEGER | Position in win/loss streak at time of trade (negative = losing streak) |
| routine_psych_avg | FLOAT | Psych score from DailyRoutine on trade day |
| rules_followed | BOOLEAN | From TradeReview if exists, else null |
| computed_at | DATETIME | When this row was last computed |
| version | INTEGER | Recomputation count |

## Classification Logic

### Bet Classification

**On loss:**
1. Kelly ratio > 1.5 → `sizing_error` (oversized stake — checked first regardless of edge)
2. CLV available AND negative AND edge_at_placement < 1% → `false_edge` (no real edge, CLV confirms)
3. CLV available AND negative AND edge_at_placement ≥ 1% → `edge_erosion` (had edge, market moved against)
4. No CLV + edge_at_placement < 1% → `false_edge` (thin/no real edge, no CLV to confirm)
5. CLV positive → `expected_loss` (had real edge, lost to variance)
6. Fallback → `expected_loss`

**On win:**
1. CLV positive → `expected_win` (real edge, deserved win)
2. CLV negative or absent → `bonus_win` (got lucky, no confirmed edge)

Priority: sizing_error > false_edge > edge_erosion > expected_loss. A bet with edge < 1% that also has negative CLV is `false_edge`, not `edge_erosion` — edge erosion requires meaningful edge to have existed.

**Void/push bets:** Bets with `result = "void"` are skipped — no postmortem row is created. Only `won` and `lost` results are classified.

### Trade Classification

**On loss (R < 0):**
1. R < -1.0 + stop was moved wider (TradeEvent type=`trail_stop` where `details.new_stop` is farther from entry than `details.old_stop`) → `stop_too_wide`
2. Setup type avg R < 0 across all closed trades (n ≥ 5) → `thesis_invalid`
3. R ≥ -1.0 → `expected_loss` (stopped out at planned stop)
4. Fallback → `expected_loss`

**On win (R > 0):**
1. R ≥ 2.0 → `runner` (let profits run)
2. R > 0 → `expected_win`

Note: `stop_too_tight` is deferred to a future iteration — requires post-close price data (market data feed integration). The VARCHAR classification column allows adding it later without schema changes.

## Batch Pipeline

### Trigger Points

1. **Inline after settlement** — `settle_bet()` and `close_trade()` call `PostmortemService.compute_single()` synchronously within the same DB session. If compute fails, log a warning and continue — the bet/trade is still settled, the postmortem row is just absent (caught by bulk recompute later).
2. **Manual recompute** — `POST /api/postmortem/recompute` reprocesses all settled bets/trades. Used after algorithm changes or when CLV data arrives late. Protected by an in-memory lock — returns 409 if already running.

### Compute Flow

```
Trigger (settle or recompute)
  → Gather context (fair_odds, closing_odds, CLV, Kelly optimal, streak)
  → Classify (apply decision tree)
  → Benchmark (setup avg R, win rate, Kelly ratio, variance score)
  → Store (UPSERT into postmortem table, increment version)
```

### Recompute Scope

- **Single:** After settlement, compute only the just-settled bet/trade
- **Bulk:** On manual recompute, process all settled records where `version < CURRENT_ALGO_VERSION` or postmortem row is missing

No automatic scheduler — recompute is either inline or on-demand.

## Pattern Detection Engine

### Segmentation Dimensions

**Betting:**
- Market (1x2, spread, total)
- Provider (each soft book)
- Sport (football, basketball, hockey, etc.)
- Edge band (<2%, 2-5%, 5-10%, 10%+)
- TTK band (<6h, 6-24h, 24-48h, 48h+)
- Odds range (<1.5, 1.5-2.5, 2.5-4.0, 4.0+)
- Day of week
- Classification category

**Trading:**
- Setup type
- Instrument
- Direction (long/short)
- Session (pre-market, RTH, post)
- Streak position (after N consecutive wins/losses)
- Psych score band (<5, 5-7, 7+)
- Day of week
- R-multiple band

### Pattern Rules

All rules require minimum sample size (n ≥ 10 for bets, n ≥ 5 for trade setups).

| Rule | Trigger | Severity |
|------|---------|----------|
| Losing segment | ROI < -10% in any segment | ▼ red |
| Winning segment | ROI > +5% in any segment | ▲ green |
| Edge erosion hotspot | ≥40% of losses are edge_erosion in TTK/provider segment | ● amber |
| Sizing alert | ≥3 sizing_error in trailing 30 days | ● amber |
| Streak impact | Win rate after N losses deviates >15pp from baseline | ▼ red |
| Psych correlation | Avg R differs >0.5R between psych score bands | ● purple |
| Setup underperformer | Setup type avg R < 0 with n ≥ 5 | ▼ red |
| False edge concentration | ≥30% of losses in provider+market segment are false_edge | ▼ red |

Patterns are computed on-the-fly when the dashboard is loaded (from pre-computed postmortem data). No separate pattern storage table needed.

## API Endpoints

All routes in `backend/src/api/routes/postmortem.py`:

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/postmortem/bets | Classified bets with filters (classification, market, provider, sport, date range). Scoped to active profile. |
| GET | /api/postmortem/bets/summary | Aggregate stats by classification (count, avg edge, avg CLV, P/L, ROI) |
| GET | /api/postmortem/bets/patterns | Auto-detected pattern insights |
| GET | /api/postmortem/trades | Classified trades with filters. Scoped to active trading account. |
| GET | /api/postmortem/trades/summary | Aggregate stats by classification |
| GET | /api/postmortem/trades/patterns | Auto-detected pattern insights |
| POST | /api/postmortem/recompute | Force recompute all postmortems. Returns 409 if already running. |

## Frontend

### New PostmortemPage Tab (Betting)

New tab in the terminal sidebar. Contains:

1. **Summary cards** — total settled, % expected losses, % false edge, % sizing errors
2. **Loss breakdown table** — rows by classification, columns: count, avg edge%, avg CLV%, total P/L, ROI
3. **Pattern insights** — auto-generated text insights from PatternDetector, color-coded by severity

Follows existing UI patterns: FilterBar with MultiSelectDropdown for classification/market/provider/sport filtering.

### TradingStatsPage Enhancement (Trading)

New postmortem section added to existing TradingStatsPage:

1. **Summary cards** — closed trades, % expected losses, % stop issues, psych correlation %
2. **Pattern insights** — streak impact, setup performance, psych score correlation, direction bias

## File Structure

```
backend/src/
  db/models.py                    + BetPostmortem, TradePostmortem models
  analysis/postmortem.py          PostmortemClassifier — decision tree logic
  analysis/patterns.py            PatternDetector — segmentation + rule engine
  services/postmortem_service.py  Orchestrates classify + detect + store
  repositories/postmortem_repo.py CRUD for postmortem tables
  api/routes/postmortem.py        7 API endpoints

frontend/src/components/Terminal/pages/
  PostmortemPage.tsx               New tab — betting postmortem dashboard
  TradingStatsPage.tsx             + postmortem section added
```

## Implementation Notes

- **edge_at_placement is derived**, not read from Bet model. Bet stores `fair_odds_at_placement` and `odds` — edge is computed as `(odds / fair_odds_at_placement - 1) * 100`.
- **clv_pct is copied** from Bet model's existing `clv_pct` field. No need to re-derive.
- **clv_confirmed** uses `(bet.start_time - bet.placed_at)` as proxy for closing odds reliability. Bet model stores `start_time` directly — no join to events table needed.
- **Kelly fraction uses current bankroll** as approximation. Historical bankroll-at-placement is not stored. This is acceptable for pattern detection (relative sizing matters more than absolute), but documented as an approximation.
- **variance_score formula**: on win = `1 - expected_win_pct`, on loss = `expected_win_pct`. A 70% favorite losing gets variance_score 0.7 (surprising). A 30% underdog losing gets 0.3 (expected).
- **Pattern confidence**: patterns include sample size in output. Rules fire at minimum thresholds (n ≥ 10 bets, n ≥ 5 trades) but output includes count for user to judge reliability.
- **Indexes**: `(classification, version)` composite on both tables (supports filtering + recompute queries). Primary keys `(bet_id)` / `(trade_id)` are auto-indexed.
- **PatternDetector joins postmortem + bets/trades** tables for ROI/P&L computation — postmortem tables store classification and metrics, not financial data.
- **All bet types** (value, boost, dutch) use the same classification logic. Boost bets may show as `bonus_win` more often since the boost edge isn't reflected in CLV.

## Out of Scope

- Real-time placement warnings (future iteration)
- Manual journaling for bets (trading has TradeReview already)
- `stop_too_tight` trade classification (requires post-close price data / market data feed)
- Historical bankroll tracking for precise Kelly analysis
- Automated strategy adjustment based on patterns
