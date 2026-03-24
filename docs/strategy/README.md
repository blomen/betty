# Trading Strategy Reference

NQ futures intraday trading based on Fabio/FlowHorse methodology.

## Documents

| # | Document | What it covers |
|---|----------|---------------|
| 01 | [Analysis Framework](01-analysis-framework.md) | AMT, volume profile, VWAP, orderflow, day types, trade grades, timeframes |
| 02 | [Risk Management](02-risk-management.md) | Position sizing, daily stops, intraday compounding, R:R targets, breakeven rules |
| 03 | [Execution Model](03-execution-model.md) | Pre-trade process, entry checklist, entry execution, exit/trail rules |
| 04 | [Setups & Patterns](04-setups-and-patterns.md) | IBOB, absorption, failed auction, PBD, reversal, gap logic |
| 05 | [Key Levels](05-key-levels.md) | Level hierarchy, how levels are used, what we don't track |
| 06 | [Session Structure](06-session-structure.md) | Session times, volume rhythm, trading window, daily routine, volatility filter |

## One-Line Summary

We trade NQ futures during the NY RTH session using volume profile levels as points of interest, confirmed by orderflow (footprint delta, imbalance clusters, absorption), with a trend-following primary model and reversal secondary model only after building a profit cushion.

## What This Strategy Needs From the Platform

These docs define WHAT we do. The platform needs to provide:

1. **Real-time tick data** → compute delta, CVD, footprint, imbalance clusters
2. **OHLCV bars** (1m, 5m, 15m) → structure analysis, IB range
3. **Volume profile** (session) → POC, VAH, VAL, HVN/LVN
4. **VWAP + standard deviations** → dynamic fair value
5. **Previous session levels** → POC, VA, high/low, naked POCs
6. **ATR / volatility data** → daily volatility filter
7. **Trade journal** → log entries, exits, R-multiples, grades
8. **Risk calculator** → position sizing based on account + risk %

This list directly informs what we need from Databento's API.
