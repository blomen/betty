# OddOpp Bankroll Strategy

## Core Principle

**One fungible bankroll** - all provider balances are treated as one total.
Stakes are calculated from total bankroll, not per-provider.

---

## Production Stake Formula

```python
def calculate_stake(bankroll, edge_raw, odds, event_id):
    # 1. Guards
    if odds <= 1.10:  # Sanity
        return 0
    if odds < 1.80:   # Bonus requirement
        return 0
    if edge_raw <= 0.01:  # Min edge
        return 0

    # 2. Edge haircut (60% of estimated edge)
    edge_used = edge_raw * 0.60

    # 3. Dynamic Kelly (0.25 to 0.75)
    if edge_used <= 0.02:
        kelly = 0.25
    elif edge_used >= 0.06:
        kelly = 0.75
    else:
        t = (edge_used - 0.02) / 0.04
        kelly = 0.25 + t * 0.50

    # 4. Raw stake
    stake = kelly * bankroll * edge_used / (odds - 1)

    # 5. Caps
    stake = min(stake, bankroll * 0.03)  # Single bet: 3%
    stake = min(stake, event_cap_remaining)  # Event: 5%

    return stake
```

---

## Safety Features

| Feature | Value | Purpose |
|---------|-------|---------|
| **Edge Haircut** | 60% | Accounts for estimation error |
| **Min Edge** | 1% | Skip noise |
| **Min Odds (Sanity)** | 1.10 | Avoid div-by-zero stakes |
| **Min Odds (Bonus)** | 1.80 | Meet wagering requirements |
| **Single Bet Cap** | 3% | Limit single bet risk |
| **Event Cap** | 5% | Prevent correlation blowups |
| **Kelly Cap** | 0.75 | Never full Kelly |

---

## Kelly Scaling

| Raw Edge | After 60% Haircut | Kelly Fraction |
|----------|-------------------|----------------|
| 2% | 1.2% | 0.25 |
| 3% | 1.8% | 0.25 |
| 4% | 2.4% | 0.30 |
| 5% | 3.0% | 0.38 |
| 6% | 3.6% | 0.45 |
| 7% | 4.2% | 0.53 |
| 8% | 4.8% | 0.60 |
| 10% | 6.0% | 0.75 |

---

## Confidence Adjustment

**Low confidence bets** (weak match, stale odds) → clamp to Quarter Kelly (0.25)

```python
def get_kelly(edge_used, high_confidence=True):
    if not high_confidence:
        return 0.25  # Always conservative
    # ... normal scaling
```

---

## Expected Performance (Simulated)

### With 60% Haircut

| Timeframe | Median ROI | Worst 5% | Max Drawdown |
|-----------|------------|----------|--------------|
| 1 Year | +111% | +1.6% | 29% |
| 2 Years | +345% | +55% | 34% |
| 3 Years | +850% | +168% | 37% |

### Starting 10,000 kr

| Year | Median Bankroll |
|------|-----------------|
| 1 | 21,000 kr |
| 2 | 45,000 kr |
| 3 | 95,000 kr |

---

## Correlation Protection

**Problem**: 6 bets on same match across 6 providers = 18% exposure on one event.

**Solution**: Track exposure per event/cluster. Cap at 5% of bankroll.

```python
class EventExposureTracker:
    def __init__(self, max_pct=0.05):
        self.max_pct = max_pct
        self.exposures = {}  # event_id -> total stake

    def get_remaining(self, event_id, bankroll):
        max_exposure = bankroll * self.max_pct
        current = self.exposures.get(event_id, 0)
        return max(0, max_exposure - current)

    def record_bet(self, event_id, stake):
        self.exposures[event_id] = self.exposures.get(event_id, 0) + stake
```

---

## Bonus Clearing Phase

During bonus clearing, the same rules apply:
- Stakes from total bankroll
- Bonuses just add EV + wagering constraints
- Min odds 1.80 (bonus requirement)
- Transfer between providers as needed

**No separate "bonus mode"** - just filter for qualifying bets.

---

## Key Differences from Naive Kelly

| Naive Kelly | Production Kelly |
|-------------|------------------|
| Use raw edge | Apply 60% haircut |
| Fixed fraction | Dynamic 0.25-0.75 |
| No caps | 3% single / 5% event |
| No correlation guard | Track per-event exposure |
| Full Kelly on high edge | Cap at 0.75 |

---

## Implementation

See: `backend/src/bankroll/stake_calculator.py`

```python
from src.bankroll.stake_calculator import StakeCalculator

calc = StakeCalculator(bankroll=10000)

result = calc.calculate(
    edge_raw=0.05,
    odds=2.0,
    event_id="arsenal_vs_chelsea",
    high_confidence=True
)

if result.stake > 0:
    calc.record_bet("arsenal_vs_chelsea", result.stake)
    place_bet(result.stake)
```
