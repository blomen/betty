# Key Levels Reference

Structural levels we track, in order of importance for trade decisions.

## Level Hierarchy

| Priority | Level | Source | Why it matters |
|----------|-------|--------|---------------|
| 1 | **Session VPOC** | Current session volume profile | Fair value — highest probability reversal target |
| 2 | **VAH / VAL** | Previous session volume profile | Where the game is played — next session respects these precisely |
| 3 | **VWAP + SDs** | Anchored to NY open, real-time | Dynamic fair value. 2nd SD = overextension zone |
| 4 | **Naked POC** | Previous sessions (unvisited) | Strong magnet — price tends to fill these |
| 5 | **Previous Day High / Low** | Price action | Context only, not primary trigger. Checking PDH/PDL can add ~5% win rate on reversals |
| 6 | **Imbalance Clusters** | Footprint (3+ consecutive imbalances) | Act as support/resistance from aggressive activity |
| 7 | **HVN / LVN** | Volume profile | HVN = price gravitates toward. LVN = price moves through fast |
| 8 | **Initial Balance** | First 15-min RTH range | Breakout direction sets the day's bias |

## How Levels Are Used

### At VAH (Looking to Sell)
1. Absorption: green at tops of footprint candles (buyers absorbed by passive sellers)
2. Low delta %, low volume = responsive auction confirmed
3. Wait for initiative selling candle (imbalance cluster short, increasing volume)
4. Enter short

### At VAL (Looking to Buy)
1. Absorption: dark at bottoms (sellers absorbed by passive buyers)
2. Low delta %, low volume = responsive auction confirmed
3. Wait for initiative buying candle
4. Enter long

### Breakout Confirmation
- Need: HIGH volume + HIGH delta + imbalance clusters
- If volume dries on breakout → likely failed auction → prepare for reversal

### Test / Retest
- Volume increases on retest = strong level, expect continuation
- Volume dies on retest = weak, expect failure

## What We DON'T Track

- Weekly highs/lows
- Asia session range
- Monthly levels
- Fibonacci levels
- Moving averages (we use VWAP instead)
- Any level older than previous session (except naked POCs)

## Data Window

**Only 2 sessions matter: previous day and current day.** That's it.

Previous session provides: POC, VAH, VAL, high, low, naked POC (if any).
Current session provides: developing POC, VA, VWAP + SDs, IB range, footprint levels.
