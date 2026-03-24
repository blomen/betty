# Risk Management

Hard rules that protect capital. These are non-negotiable.

## Position Sizing

| Phase | Risk per trade | Condition |
|-------|---------------|-----------|
| **Start of day** | 0.25% of account | Default — every day starts here |
| **After 3% daily profit** | 0.50% (using 2% of day's profits) | Only risk house money |
| **Competition mode** | Gradually 0.25% → 0.40% | As account grows over weeks/months |

## Daily Stop Rules

| Rule | Action |
|------|--------|
| **3 stop-losses** | Done for the day — no exceptions |
| **3 consecutive stops** | Environment doesn't suit the strategy — walk away |
| **One good trade** | Covers all previous day's losses (due to minimum 1:2 R:R) |

The 3-stop rule isn't about money — it's about recognizing when the market doesn't match your model.

## Intraday Compounding

After building a profit cushion:

1. Lock in base profit (e.g., 3% from morning trades)
2. Set aside ~2% as locked profit
3. Risk remaining ~1% on 2-3 additional trades at higher size
4. Reversal trades ONLY taken with profit cushion — never as first trade of day

## R:R Targets

| Setup Type | Minimum R:R | Win Rate | Notes |
|------------|-------------|----------|-------|
| **Trend/Momentum** | 1:2 | ~50% | Primary model |
| **Reversal** | 1:2.5 to 1:3 | ~40% | Only with profit cushion |

Priority: **win rate over huge R:R**. "1:1 and 1:2 are not bad — I want to see a lot of profit, not one profit and a streak of 10 stop losses."

## Breakeven Rules

Moving to breakeven is NOT automatic — it depends on context:

| Market Condition | Breakeven Timing |
|-----------------|-----------------|
| **Fast/volatile day** | Within 15 seconds to 1 minute |
| **Slow accumulation day** | Let it breathe — wait for buyer/seller confirmation |
| **General rule** | Move BE when volume confirms aggression in your direction (big explosion candle) |

**Trigger to move BE:** If market breaks back through the aggression level that confirmed your entry, you want to be out.

## Key Statistics to Track

| Metric | Target |
|--------|--------|
| Win rate (trend) | ≥50% |
| Win rate (reversal) | ≥40% |
| Maximum drawdown | <20% |
| Commission impact | ~10% of gross |
| Daily max loss | 3 stops × 0.25% = 0.75% |

## One Position, One Asset

- Trade only NQ (NASDAQ futures)
- One position at a time
- Fractional entries allowed (accumulating at different levels with common stop-loss)
- No hedging, no spreading across instruments
