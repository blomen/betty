# Setups & Patterns

Concrete trade setups, each with entry trigger, confirmation, and stop placement.

## Primary Setups (Trend-Following)

### 1. IBOB — Initial Balance Opening Breakout

**When:** Trend days, strong directional bias from pre-market analysis.

| Step | Action |
|------|--------|
| 1 | Mark first 15-minute range of RTH session (Initial Balance) |
| 2 | Wait for 5-min candle to CLOSE above/below the range |
| 3 | Confirm with volume: high participation, imbalance clusters on breakout candle |
| 4 | Enter in breakout direction |
| **Stop** | Opposite side of the 15-min IB range |
| **Target** | Trail with orderflow — minimum 1:2 R:R |

**Invalidation:** If volume dries on the breakout → likely failed auction, don't enter.

### 2. Absorption → Initiative

**When:** Price at value area boundary (VAH/VAL) in balance phase.

| Step | Action |
|------|--------|
| 1 | Identify responsive auction at VA boundary |
| 2 | Look for absorption: aggressive side being blocked by passive orders |
| 3 | Absorption signs: green at tops (buyers absorbed) or dark at bottoms (sellers absorbed) |
| 4 | Low delta %, low volume = balance confirmed |
| 5 | Wait for initiative candle: imbalance cluster, high volume, high delta |
| 6 | Enter in direction of initiative |
| **Stop** | Beyond the absorption zone |
| **Target** | Opposite VA boundary or POC |

### 3. Failed Auction

**When:** Price breaks out of balance but orderflow doesn't support continuation.

| Step | Action |
|------|--------|
| 1 | Price breaks out of value area or balance range |
| 2 | Volume DRIES on the breakout (decreasing participation) |
| 3 | Price returns inside range with INCREASING volume |
| 4 | Initiative candle confirms reversal direction |
| 5 | Enter toward opposite side of range |
| **Stop** | Beyond the failed breakout high/low |
| **Target** | POC or opposite VA boundary |

This is the same concept as "liquidity sweep" — validated with orderflow instead of just price.

### 4. PBD Profiles (Tom Forvald)

| Market State | Setup |
|-------------|-------|
| **P (uptrend)** | Failed auction below range → buy to other side. OR strong breakout above → follow |
| **B (downtrend)** | Failed auction above range → sell to other side. OR strong breakout below → follow |
| **D (consolidation)** | Failed auction either side → trade to opposite extreme (ping-pong) |
| **Reversal variant** | Range forms, price breaks out, breaks back in → trade to opposite extreme |

## Secondary Setups (Only With Profit Cushion)

### 5. Reversal at 2nd SD VWAP

**When:** Mid-to-late session, price at 2nd standard deviation of VWAP, you already have profits locked.

| Step | Action |
|------|--------|
| 1 | Price reaches 2nd SD of session VWAP |
| 2 | Look for absorption + initiative reversal on footprint |
| 3 | Enter toward POC (fair value) |
| **Stop** | Beyond the 2nd SD extreme |
| **Target** | POC — highest probability reversion target |
| **Win rate** | ~40% at 1:2.5 to 1:3 R:R |

Best window: mid-session to late session. Late session rebalancing typically retraces 50% of day's move.

### 6. Gap Logic (Larry Williams "Oops")

| Gap Direction | Trigger | Trade |
|--------------|---------|-------|
| Gap UP above previous day high | Price breaks back below PDH | Sell |
| Gap DOWN below previous day low | Price breaks back above PDL | Buy |

Minimum 20-point gap. Target: gap fill toward previous session close. 65-70% fill rate historically.

## Volume Confirmation Checklist (All Setups)

| At Level | Bullish Confirmation | Bearish Confirmation |
|----------|---------------------|---------------------|
| **VAL** | Seller absorption (dark at bottoms) → initiative buying | Break with high volume + imbalance clusters |
| **VAH** | Break with high volume + imbalance clusters | Buyer absorption (green at tops) → initiative selling |
| **Breakout** | HIGH volume + HIGH delta + imbalance clusters | Same |
| **Test/Retest** | Volume increases on retest = strong | Volume dies on retest = weak, expect continuation |
