# Bonus Mode Quick Start Guide

## What is Bonus Mode?

Bonus mode helps you extract maximum value from:
- **Free bets** (stake not returned, SNR)
- **Qualifying bets** (required to unlock bonuses)
- **Risk-free bets** (refunded if lost)

By finding the optimal hedge bet at a different bookmaker, you can lock in guaranteed profit or minimize qualifying losses.

## Quick Example

You have a $100 free bet at Unibet. Arsenal vs Chelsea, 1x2 market.

1. **Place anchor bet**: $100 on Arsenal @ 2.5 odds (Unibet)
2. **Find hedge**: API finds best opposing bet
3. **Place hedge bet**: $71.43 on Chelsea @ 2.1 odds (Bet365)
4. **Result**: Guaranteed $78.57 profit (78.6% retention)

Regardless of match outcome, you profit $78.57.

## Using the CLI Tool

```bash
cd backend

# Find best hedge for free bet
python scripts/bonus_matcher.py \
    --event "football:arsenal:chelsea:20260127" \
    --market "1x2" \
    --anchor-provider unibet \
    --anchor-outcome home \
    --anchor-odds 2.5 \
    --stake 100 \
    --free-bet

# Find hedge for qualifying bet
python scripts/bonus_matcher.py \
    --event "football:arsenal:chelsea:20260127" \
    --market "1x2" \
    --anchor-provider unibet \
    --anchor-outcome home \
    --anchor-odds 2.0 \
    --stake 50
```

**Output**:
```
======================================================================
BEST HEDGE FOUND
======================================================================

ANCHOR BET:
  Provider:  unibet
  Outcome:   home
  Odds:      2.5
  Stake:     $100.00

HEDGE BET:
  Provider:  bet365
  Outcome:   away
  Odds:      2.1
  Stake:     $71.43

RESULTS:
  Retention: 78.6%
  Profit:    $78.57
```

## Using the API

### 1. Find Best Hedge

**Request**:
```bash
curl -X POST http://localhost:8000/api/opportunities/bonus/match \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "football:arsenal:chelsea:20260127",
    "market": "1x2",
    "anchor_provider": "unibet",
    "anchor_outcome": "home",
    "anchor_odds": 2.5,
    "anchor_stake": 100,
    "is_free_bet": true,
    "counterpart_providers": ["bet365", "betsson"]
  }'
```

**Response**:
```json
{
  "event_id": "football:arsenal:chelsea:20260127",
  "market": "1x2",
  "anchor_provider": "unibet",
  "anchor_outcome": "home",
  "anchor_odds": 2.5,
  "anchor_stake": 100,
  "hedge_provider": "bet365",
  "hedge_outcome": "away",
  "hedge_odds": 2.1,
  "hedge_stake": 71.43,
  "qualifying_loss": -78.57,
  "retention_pct": 78.6
}
```

### 2. Filter Opportunities

```bash
# Find all bonus opportunities with 80%+ retention
curl "http://localhost:8000/api/opportunities?type=bonus&min_value=80"

# Find opportunities between specific providers
curl "http://localhost:8000/api/opportunities?provider1=unibet&provider2=bet365"

# Find football arbitrage opportunities
curl "http://localhost:8000/api/opportunities?type=arbitrage&sport=football"
```

### 3. Update Profile Settings

```bash
curl -X PUT http://localhost:8000/api/profile \
  -H "Content-Type: application/json" \
  -d '{
    "min_retention_pct": 85.0,
    "preferred_counterparts": ["bet365", "betsson"],
    "bonus_enabled": true
  }'
```

## Understanding Retention

**Retention %** = How much of your free bet value you keep as guaranteed profit.

| Retention | Quality | Example |
|-----------|---------|---------|
| 95%+ | Excellent | Close odds, minimal loss |
| 85-95% | Very Good | Good value extraction |
| 80-85% | Good | Acceptable for most bonuses |
| 70-80% | Fair | Consider if bonus is large |
| <70% | Poor | Odds too far apart |

## Common Use Cases

### Use Case 1: Free Bet (SNR)
- You have $100 free bet at Unibet
- Find event with close odds between providers
- Place free bet on one outcome
- Hedge at another provider
- Lock in ~80% of free bet as profit

### Use Case 2: Qualifying Bet
- Need to wager $50 to unlock $20 free bet
- Find low-margin market (close odds)
- Place $50 qualifying bet
- Hedge to minimize loss
- Typical loss: $2-5 to unlock $20 bonus

### Use Case 3: Risk-Free Bet
- First bet refunded if it loses (up to $100)
- Place maximum allowed on favorite
- If wins: Profit from high odds
- If loses: Get refund, then extract value

## Tips for Best Results

1. **Choose close odds**: Retention improves when anchor and hedge odds are similar
2. **Use multiple providers**: More hedging options = better retention
3. **Avoid same provider**: Can't hedge with same bookmaker
4. **Check market liquidity**: Ensure hedge provider accepts desired stake
5. **Act quickly**: Odds change frequently

## Workflow

1. **Get free bet/bonus** from provider
2. **Run extraction**: `python main.py --providers unibet bet365 betsson`
3. **Find event** with odds data
4. **Use CLI or API** to find best hedge
5. **Place both bets** immediately (odds change)
6. **Lock in profit** regardless of outcome

## Troubleshooting

**"No opposing odds found"**
- Event not in database yet
- Run extraction first: `python main.py`
- Check event ID matches database

**"No suitable hedge found"**
- All opposing odds are from same provider
- Add more providers to extraction
- Try different event/market

**Low retention (<70%)**
- Odds too far apart between providers
- Try different market (over/under vs 1x2)
- Look for closer odds on same event

## Next Steps

1. Run extraction to populate database
2. Test CLI tool with real event
3. Integrate API into frontend
4. Build UI for bonus mode workflow

For detailed implementation, see `BONUS_MODE_IMPLEMENTATION.md`.
