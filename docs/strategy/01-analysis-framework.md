# Analysis Framework

How we read the market before looking for trades. This is the foundation — everything else builds on it.

## Core Philosophy

**"Price action is the result of volume impact."** Volume is the cause, price is the effect. Always validate price structure with orderflow.

We follow a momentum/trend-following approach (Fabio/FlowHorse methodology). We never trade reversals as a primary model — we join the market when there is momentum, volume, and price follow-up.

## Auction Market Theory (AMT)

Markets alternate between two states:

| State | Meaning | What to look for |
|-------|---------|-----------------|
| **Balance** | Fair value found, buyers and sellers agree | Range-bound, bell-curve volume profile, responsive auctions at edges |
| **Imbalance** | One side dominant, price discovering new levels | Directional move, initiative auctions, high delta |

### PBD Framework (Tom Forvald)
- **P** = Uptrend (price discovery up)
- **B** = Downtrend (price discovery down)
- **D** = Consolidation (balance/range)

Identifying the current state determines which setups are valid.

## Volume Profile

The core analytical tool — shows WHERE volume traded, not just how much.

| Concept | Definition | Why it matters |
|---------|-----------|---------------|
| **POC** (Point of Control) | Price level with highest volume | Fair value — price returns here with highest probability |
| **Value Area** (VA) | 1 SD of volume (~70%) | The range where the market "agreed" on price |
| **VAH** | Value Area High | Upper boundary — responsive sellers live here |
| **VAL** | Value Area Low | Lower boundary — responsive buyers live here |
| **HVN** (High Volume Node) | Peaks in profile | Acceptance zones — price gravitates toward these |
| **LVN** (Low Volume Node) | Valleys in profile | Rejection zones — price moves through quickly |
| **Naked POC** | Previous session POC not yet revisited | Strong magnet for future price action |

## VWAP (Volume Weighted Average Price)

Anchored to NY session start. Dynamic fair value that develops in real-time.

| Band | Meaning | Action |
|------|---------|--------|
| Within 1st SD | Normal range | Trade with trend |
| At 2nd SD | Overextended | Reversal zone (only with profit cushion) |
| At 3rd SD | Extreme — only 7% of sessions reach this | Strong reversal probability |

## Orderflow / Footprint

How we confirm what's happening at key levels:

| Metric | Definition | Signal |
|--------|-----------|--------|
| **Delta** | Ask vol − Bid vol | Net aggression direction |
| **Delta %** | Delta / Total Volume | How one-sided the candle is |
| **Imbalance** | Diagonal bid vs ask comparison, >200% ratio | Initiative activity |
| **Imbalance Cluster** | 3+ consecutive imbalance levels | Clearest initiation signal |
| **Absorption** | Aggressive side blocked by passive orders | Green at tops = sellers absorbing buyers |
| **CVD** | Cumulative Volume Delta (running sum) | Sustained pressure direction |

### Responsive vs Initiative

| Type | Volume | Delta % | Pattern | Meaning |
|------|--------|---------|---------|---------|
| **Responsive** | Low | Low | Absorption at extremes | Balance — range continuation |
| **Initiative** | High | High | Imbalance clusters | Breakout — directional move |

## Day Types

Classify the day early to know which setups are valid:

| Day Type | Characteristics | Strategy |
|----------|----------------|----------|
| **Trend** | Strong directional, initiative auctions, high volume on impulse | IBOB, continuation trades |
| **Normal** | Bell-curve profile, stays within VA | Responsive trades at VA edges |
| **Neutral** | Range extension both sides, no clear bias | Fade extremes carefully |
| **Consolidation** | Tight range, low volatility | Don't trade — skip the day |

Use ATR data to predict expected volatility. If consolidation is >70% probable, don't trade.

## Trade Model Grades

| Grade | Description | Win Rate |
|-------|-------------|----------|
| **A** | All boxes ticked, highest confluence | ~60-62% |
| **B** | Good trade, most boxes ticked | ~57% |
| **C** | Some edge, fewer confirmations | ~50% |

Take all grades — more executions = more profit. The edge between grades isn't large enough to justify sitting out B/C models.

## Timeframes

| Purpose | Timeframe |
|---------|-----------|
| **Bias/Direction** | 15-minute |
| **Structure** | 1-minute |
| **Execution** | 15-second or 20-tick range bars |

Only use 2 days of data: previous day and current day. No weekly highs/lows, no Asia range.
