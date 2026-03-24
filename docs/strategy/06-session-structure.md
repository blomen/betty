# Session Structure

When we trade, when we don't, and how the session unfolds.

## Session Times

| Session | Hours (ET) | Relevance |
|---------|-----------|-----------|
| **ETH (Globex)** | 6:00 PM - 9:30 AM | Context only — overnight range, gap analysis |
| **RTH (Cash)** | 9:30 AM - 4:00 PM | Where we trade. This is where volume lives |

We trade ONLY the New York RTH session.

## Intraday Volume Rhythm

```
Volume
  │
  █                                          █
  █ █                                      █ █
  █ █ █                                  █ █ █
  █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █
  └──────────────────────────────────────────────
  9:30  10:00  10:30  11:00  ...  3:00  3:30  4:00
       ↑ Open              Mid-session         Close ↑
```

U-shaped curve:
- **Opening auction**: High volume burst (MOO orders, institutional fills)
- **Mid-session**: Volume dies down
- **Closing auction**: Volume surge (MOC orders)

This pattern = institutional VWAP execution. They spread orders across the session.

## Our Trading Window

| Time (ET) | Phase | Action |
|-----------|-------|--------|
| Pre-9:30 | **Prep** | Check ATR/volatility (consolidation >70% → skip day), review previous day structure |
| 9:30 - 9:45 | **Initial Balance forming** | Watch only — do NOT trade the opening chaos |
| 9:45 - 10:00 | **IB complete** | Mark IB range, identify breakout direction |
| 10:00 - 2:00 | **Primary window** | IBOB, absorption, failed auction setups. Trail with orderflow |
| 2:00 - 3:30 | **Late session** | Reversal setups ONLY (if profitable). Late rebalancing retraces ~50% of day's move |
| 3:30 - 4:00 | **Close** | No new trades — closing auction volatility |

## Daily Routine

### Pre-Session Checklist
1. Check ATR/expected volatility for the day
2. Mark previous day: POC, VAH, VAL, high, low
3. Check for naked POCs from prior sessions
4. Note overnight range and any gap
5. Determine bias from 15-min chart structure
6. Identify 2-3 points of interest for the session

### During Session
- One position at a time
- Start at 0.25% risk
- After 3 stops → done
- After 3% profit → can increase size on house money
- Trail every 10-15 minutes based on orderflow

### Post-Session
- Log all trades (entry, exit, R-multiple, grade A/B/C)
- Review: did I follow the plan? Did orderflow confirm?
- Note any pattern deviations for the day type

## Volatility Filter

Before trading, check expected daily range (ATR):
- **High volatility expected**: Normal trading, full setup menu
- **Low volatility / consolidation >70% probable**: Skip the day entirely
- **Source**: Mataf or equivalent ATR data per asset per session
