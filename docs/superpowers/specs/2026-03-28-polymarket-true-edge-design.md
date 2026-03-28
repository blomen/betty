# Polymarket True Edge — Design Spec

## Problem

Polymarket edges shown in the UI are inflated because they don't account for bid-ask spread costs. Example: LYON vs Contra shows +72% edge, but Polymarket's ask price is far from the mid-price on a thin order book. The 2% fee is already deducted, but spread cost is not.

## Goal

Subtract the bid-ask spread cost from Polymarket edge calculations so `edge_pct` reflects what you'd actually capture after execution costs (fee + spread).

No frontend changes — `edge_pct` just becomes more accurate.

## Current State

- **Provider** (`polymarket.py`): Fetches ask-side order book per token, calculates VWAP at `fill_size_usd=25`, stores VWAP in `self._clob_prices` and depth in `self._clob_depth`. Bid side is **not fetched**.
- **Storage** (`storage.py`): `OddsBatchProcessor.add()` accepts `odds`, `point`, `provider_meta`. No bid/ask/depth fields. The VWAP is stored as `odds.odds`.
- **Scanner** (`scanner.py`): `group_odds()` builds `{"provider", "odds", "point", "updated_at"}` dicts from `event.odds`. These flow into `find_value()`.
- **Edge calc** (`value.py`): `find_value()` applies 2% Polymarket fee via `polymarket_effective_odds()`, then `edge = (effective_odds / fair_odds) - 1`.
- **DB schema**: `odds` table has `odds FLOAT` (the VWAP), no bid/ask/depth columns.

## Design

### 1. Schema — add bid/ask/depth to `odds` table

Add 3 nullable `FLOAT` columns to the `Odds` model (in both `db/betting.py` and `db/models.py`):

```python
bid = Column(Float, nullable=True)        # Best bid price (probability, 0-1)
ask = Column(Float, nullable=True)        # Best ask price (probability, 0-1)
depth_usd = Column(Float, nullable=True)  # Total ask-side depth in USD
```

These are null for all non-CLOB providers. Auto-migrate via `ensure_columns()` pattern already used in the codebase.

### 2. Provider — fetch bid side, pass bid/ask/depth through

**`polymarket.py` changes:**

- `_fetch_clob_books()`: Extract best bid price from `data["bids"]` (highest price entry = `bids[0]["price"]`, since bids are sorted descending). Store in `self._clob_bids: dict[str, float]` (token_id → best bid price). Also store best ask price (lowest = `asks[0]["price"]`) in `self._clob_asks: dict[str, float]`.
- `_calc_vwap_from_asks()`: No changes — VWAP calculation stays the same.
- `_parse_market()` / outcome construction: Attach `bid`, `ask`, `depth_usd` to each outcome dict so they flow through to storage.

**Data flow through StandardEvent:**

The outcome dicts in `StandardEvent.markets[].outcomes[]` already carry arbitrary fields. Add `bid`, `ask`, `depth_usd` to the outcome dict when available. These will be read by storage.

### 3. Storage — persist bid/ask/depth

**`OddsBatchProcessor.add()`**: Accept optional `bid`, `ask`, `depth_usd` params. Include them in the pending record dict.

**`_flush_inner()`**: Write `bid`, `ask`, `depth_usd` on both insert and update paths.

### 4. Scanner — pass bid/ask through group_odds

**`group_odds()`** in `scanner.py`: Include `bid`, `ask`, `depth_usd` in the per-provider dict:

```python
grouped[market_key][outcome].append({
    "provider": odds.provider_id,
    "odds": odds.odds,
    "point": odds.point,
    "updated_at": odds.updated_at,
    "bid": odds.bid,       # None for non-CLOB providers
    "ask": odds.ask,
    "depth_usd": odds.depth_usd,
})
```

### 5. Edge calculation — subtract spread cost

**`value.py` — modify `find_value()`**: Add optional `bid` and `ask` params.

```python
def find_value(
    event_id, market, outcome, provider, provider_odds, fair_odds,
    min_edge_pct=2.0,
    bid: float = None,    # NEW
    ask: float = None,     # NEW
) -> Optional[ValueBet]:
```

When `provider == "polymarket"` and both `bid` and `ask` are available:

```python
effective_odds = polymarket_effective_odds(provider_odds)  # existing fee deduction
mid = (bid + ask) / 2
mid_odds = 1 / mid
ask_odds = provider_odds  # already VWAP-based
spread_cost = (ask_odds - mid_odds) / mid_odds
edge_pct = ((effective_odds / fair_odds) - 1 - spread_cost) * 100
```

If `bid` is None (no bids on book), fall back to current behavior (fee-only deduction).

### 6. Scanner callsite — pass bid/ask to find_value

In `scanner.py` `find_value_in_market()`, pass the new fields:

```python
vb = find_value(
    event_id=event_id,
    market=market,
    outcome=outcome,
    provider=po["provider"],
    provider_odds=po["odds"],
    fair_odds=fair_odds,
    min_edge_pct=min_edge_pct,
    bid=po.get("bid"),
    ask=po.get("ask"),
)
```

## Edge Formula Summary

For Polymarket with bid/ask available:
```
effective_odds = (1 - 0.02) * ask_vwap_odds + 0.02    # fee-adjusted
mid = (bid + ask) / 2                                   # mid-price in probability space
spread_cost = (ask_vwap_odds - (1/mid)) / (1/mid)      # spread as fraction of mid odds
true_edge = ((effective_odds / pinnacle_fair) - 1 - spread_cost) * 100
```

For Polymarket without bid data, or any other provider: unchanged (current behavior).

## What This Does NOT Change

- Frontend display — no new columns, `edge_pct` field stays the same
- Stake sizing — Kelly still uses the same `edge_pct`
- Other providers — bid/ask/depth columns are null, no behavior change
- Polymarket extraction — same markets, same VWAP, same liquidity filters
- `fill_size_usd` — stays at $25 fixed

## Files Changed

| File | Change |
|------|--------|
| `backend/src/db/betting.py` | Add `bid`, `ask`, `depth_usd` columns to `Odds` |
| `backend/src/db/models.py` | Same (duplicate model) |
| `backend/src/providers/polymarket.py` | Fetch bid side, attach bid/ask/depth to outcomes |
| `backend/src/pipeline/storage.py` | `OddsBatchProcessor.add()` accepts and persists new fields |
| `backend/src/analysis/value.py` | `find_value()` subtracts spread cost for Polymarket |
| `backend/src/analysis/scanner.py` | `group_odds()` passes bid/ask/depth; callsite passes to `find_value()` |
