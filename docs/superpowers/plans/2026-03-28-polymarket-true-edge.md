# Polymarket True Edge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Subtract bid-ask spread cost from Polymarket edge calculations so `edge_pct` reflects actual execution edge (fee + spread).

**Architecture:** Fetch bid-side prices from CLOB alongside existing ask-side VWAP. Persist bid/ask/depth on the `odds` table. Scanner passes these through to `find_value()`, which subtracts spread cost for Polymarket.

**Tech Stack:** Python / SQLAlchemy / SQLite / aiohttp

**Spec:** `docs/superpowers/specs/2026-03-28-polymarket-true-edge-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/db/models.py` | Modify | Add `bid`, `ask`, `depth_usd` columns to `Odds` + migration |
| `backend/src/db/betting.py` | Modify | Add same columns (duplicate model) |
| `backend/src/providers/polymarket.py` | Modify | Fetch bid side, store best bid/ask, attach to outcomes |
| `backend/src/pipeline/storage.py` | Modify | `OddsBatchProcessor.add()` + `_flush_inner()` + `upsert_odds()` + `store_provider_event()` pass bid/ask/depth |
| `backend/src/analysis/value.py` | Modify | `find_value()` subtracts spread cost |
| `backend/src/analysis/scanner.py` | Modify | `group_odds()` includes bid/ask/depth; callsite passes to `find_value()` |
| `backend/tests/test_polymarket_true_edge.py` | Create | Tests for spread cost calculation |

---

### Task 1: Schema — add bid/ask/depth columns

**Files:**
- Modify: `backend/src/db/models.py:130-142` (Odds model)
- Modify: `backend/src/db/models.py:1513-1531` (migration block)
- Modify: `backend/src/db/betting.py:84-95` (duplicate Odds model)

- [ ] **Step 1: Add columns to Odds model in `models.py`**

In `backend/src/db/models.py`, add after line 140 (`provider_meta` column):

```python
    bid = Column(Float, nullable=True)        # Best bid price (probability 0-1, CLOB only)
    ask = Column(Float, nullable=True)        # Best ask price (probability 0-1, CLOB only)
    depth_usd = Column(Float, nullable=True)  # Total ask-side depth in USD (CLOB only)
```

- [ ] **Step 2: Add same columns to Odds model in `betting.py`**

In `backend/src/db/betting.py`, add after line 94 (`provider_meta` column):

```python
    bid = Column(Float, nullable=True)        # Best bid price (probability 0-1, CLOB only)
    ask = Column(Float, nullable=True)        # Best ask price (probability 0-1, CLOB only)
    depth_usd = Column(Float, nullable=True)  # Total ask-side depth in USD (CLOB only)
```

- [ ] **Step 3: Add migration for existing databases**

In `backend/src/db/models.py`, in the `init_db()` migration block, add after the `provider_meta` migration (~line 1531):

```python
        # Add CLOB microstructure columns to odds (Polymarket bid/ask/depth)
        for col, col_type in [("bid", "FLOAT"), ("ask", "FLOAT"), ("depth_usd", "FLOAT")]:
            try:
                with sqlite3.connect(db_path) as raw:
                    cursor = raw.cursor()
                    cursor.execute(f"ALTER TABLE odds ADD COLUMN {col} {col_type}")
                    raw.commit()
            except sqlite3.OperationalError:
                pass
```

- [ ] **Step 4: Verify migration works**

Run: `cd backend && python -c "from src.db.models import init_db; init_db()"`

Expected: No errors. The 3 new columns exist in the `odds` table.

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/src/db/betting.py
git commit -m "feat(schema): add bid/ask/depth columns to odds table for CLOB microstructure"
```

---

### Task 2: Provider — fetch bid side and attach bid/ask/depth to outcomes

**Files:**
- Modify: `backend/src/providers/polymarket.py:148-149` (instance vars)
- Modify: `backend/src/providers/polymarket.py:240-260` (`_fetch_clob_books`)
- Modify: `backend/src/providers/polymarket.py:1077-1094` (`_parse_market` yes/no branch)
- Modify: `backend/src/providers/polymarket.py:1108-1118` (`_parse_market` generic branch)

- [ ] **Step 1: Add instance variables for bid/ask storage**

In `backend/src/providers/polymarket.py`, add after line 149 (`self._clob_depth`):

```python
        self._clob_bids: dict = {}   # token_id -> best bid price (highest bid)
        self._clob_asks: dict = {}   # token_id -> best ask price (lowest ask)
```

- [ ] **Step 2: Extract bid and ask prices in `_fetch_clob_books()`**

In `backend/src/providers/polymarket.py`, inside the `fetch_book` inner function (~line 248), after the `asks` processing block, add bid/ask extraction. Replace the block starting at `asks = data.get("asks", [])` (line 248) through the VWAP storage (line 256) with:

```python
                            asks = data.get("asks", [])
                            bids = data.get("bids", [])
                            if asks:
                                vwap, depth_usd = self._calc_vwap_from_asks(asks, fill_size)
                                self._clob_depth[token_id] = depth_usd
                                if 0.01 < vwap < 0.99:
                                    self._clob_prices[token_id] = vwap
                                # Best ask = lowest price (asks sorted ascending)
                                try:
                                    best_ask = float(asks[0]["price"])
                                    if 0.01 < best_ask < 0.99:
                                        self._clob_asks[token_id] = best_ask
                                except (ValueError, TypeError, KeyError, IndexError):
                                    pass
                            if bids:
                                # Best bid = highest price (bids sorted descending)
                                try:
                                    best_bid = float(bids[0]["price"])
                                    if 0.01 < best_bid < 0.99:
                                        self._clob_bids[token_id] = best_bid
                                except (ValueError, TypeError, KeyError, IndexError):
                                    pass
```

- [ ] **Step 3: Add helper to build outcome dict with CLOB data**

In `backend/src/providers/polymarket.py`, add a new method after `_get_clob_depth_usd()` (~line 176):

```python
    def _build_outcome(self, name: str, price: float, token_id: str = None) -> dict:
        """Build an outcome dict with odds and optional CLOB microstructure data."""
        outcome = {"name": name, "odds": self._price_to_odds(price)}
        if token_id:
            bid = self._clob_bids.get(token_id)
            ask = self._clob_asks.get(token_id)
            depth = self._clob_depth.get(token_id)
            if bid is not None:
                outcome["bid"] = bid
            if ask is not None:
                outcome["ask"] = ask
            if depth is not None:
                outcome["depth_usd"] = depth
        return outcome
```

- [ ] **Step 4: Use `_build_outcome` in yes/no branch of `_parse_market()`**

In `backend/src/providers/polymarket.py`, replace lines 1088-1094 (the yes/no return block):

```python
                            return {
                                "type": "moneyline",
                                "outcomes": [
                                    {"name": matched_team, "odds": self._price_to_odds(yes_price)},
                                    {"name": other_team, "odds": self._price_to_odds(no_price)},
                                ]
                            }
```

With:

```python
                            return {
                                "type": "moneyline",
                                "outcomes": [
                                    self._build_outcome(matched_team, yes_price, yes_token),
                                    self._build_outcome(other_team, no_price, no_token),
                                ]
                            }
```

- [ ] **Step 5: Use `_build_outcome` in generic branch of `_parse_market()`**

In `backend/src/providers/polymarket.py`, replace lines 1115-1118 (the generic outcome append):

```python
                    formatted_outcomes.append({
                        "name": name,
                        "odds": self._price_to_odds(price),
                    })
```

With:

```python
                    formatted_outcomes.append(
                        self._build_outcome(name, price, token_id)
                    )
```

- [ ] **Step 6: Also update `_parse_map_winner_market` and `_parse_spread_market` and `_parse_total_market`**

Search for all other places in `polymarket.py` that build outcome dicts with `{"name": ..., "odds": ...}` and replace with `self._build_outcome()`. These methods also construct outcomes that flow to the odds table.

For each method, find the outcome construction and replace with `_build_outcome`. The token_id is available as a local variable in each method's parsing loop.

- [ ] **Step 7: Verify extraction still works**

Run: `cd backend && python -m src.app extract polymarket`

Expected: Extraction completes without errors. Check logs for CLOB book depth line — should still show token counts.

- [ ] **Step 8: Commit**

```bash
git add backend/src/providers/polymarket.py
git commit -m "feat(polymarket): fetch bid side from CLOB, attach bid/ask/depth to outcomes"
```

---

### Task 3: Storage — persist bid/ask/depth through the pipeline

**Files:**
- Modify: `backend/src/pipeline/storage.py:1080-1101` (`OddsBatchProcessor.add`)
- Modify: `backend/src/pipeline/storage.py:1140-1197` (`_flush_inner`)
- Modify: `backend/src/pipeline/storage.py:1001-1010` (`upsert_odds`)
- Modify: `backend/src/pipeline/storage.py:990-994` (`store_provider_event` outcome loop)

- [ ] **Step 1: Update `OddsBatchProcessor.add()` to accept bid/ask/depth**

In `backend/src/pipeline/storage.py`, modify the `add` method signature (~line 1080):

```python
    def add(
        self,
        event_id: str,
        provider: str,
        market: str,
        outcome: str,
        odds: float,
        point: float = None,
        provider_meta: dict = None,
        bid: float = None,
        ask: float = None,
        depth_usd: float = None,
    ):
        """Add odds record to batch (will be processed on flush)."""
        key = (event_id, provider, market, outcome, point)
        self._pending[key] = {
            "event_id": event_id,
            "provider_id": provider,
            "market": market,
            "outcome": outcome,
            "odds": odds,
            "point": point,
            "provider_meta": provider_meta,
            "bid": bid,
            "ask": ask,
            "depth_usd": depth_usd,
        }
        self._market_counts[market] = self._market_counts.get(market, 0) + 1

        if len(self._pending) >= self.batch_size:
            self.flush()
```

- [ ] **Step 2: Update `_flush_inner()` to write bid/ask/depth on update**

In `backend/src/pipeline/storage.py`, in `_flush_inner()` (~line 1189), after `existing.updated_at = now`, add:

```python
                existing.bid = record.get("bid")
                existing.ask = record.get("ask")
                existing.depth_usd = record.get("depth_usd")
```

- [ ] **Step 3: Update `_flush_inner()` to include bid/ask/depth on insert**

The insert path uses `to_insert.append(record)` which already includes bid/ask/depth from step 1. But the bulk insert at the end of `_flush_inner()` constructs `Odds(...)` objects. Find the bulk insert block (after `# Bulk insert new records`) and make sure bid/ask/depth are included. The record dict already has the keys, so if the insert uses `Odds(**record)` or similar dict unpacking, it should work. If it constructs explicitly, add the fields.

Read the bulk insert section to confirm the pattern:

```python
        if to_insert:
            for record in to_insert:
                record["updated_at"] = now
            self.session.bulk_insert_mappings(Odds, to_insert)
            self._insert_count += len(to_insert)
```

Since this uses `bulk_insert_mappings` with the full record dict, and the dict already contains `bid`, `ask`, `depth_usd` from step 1, this works automatically. No code change needed here.

- [ ] **Step 4: Update `upsert_odds()` to accept and persist bid/ask/depth**

In `backend/src/pipeline/storage.py`, modify `upsert_odds()` (~line 1001):

```python
def upsert_odds(
    session,
    event_id: str,
    provider: str,
    market: str,
    outcome: str,
    odds: float,
    point: float = None,
    provider_meta: dict = None,
    bid: float = None,
    ask: float = None,
    depth_usd: float = None,
) -> int:
```

And in the update branch, add `existing.bid = bid`, `existing.ask = ask`, `existing.depth_usd = depth_usd`.

In the insert branch, add `bid=bid, ask=ask, depth_usd=depth_usd` to the `Odds(...)` constructor.

- [ ] **Step 5: Pass bid/ask/depth from outcomes in `store_provider_event()`**

In `backend/src/pipeline/storage.py`, in the outcome loop (~line 992), extract the CLOB fields from the outcome dict and pass them through:

Replace:

```python
            if odds_batch:
                odds_batch.add(final_id, storage_provider, market_type, outcome_name, odds_value, point_value, provider_meta=provider_meta)
            else:
                odds_new += upsert_odds(session, final_id, storage_provider, market_type, outcome_name, odds_value, point_value, provider_meta=provider_meta)
```

With:

```python
            # CLOB microstructure (Polymarket only, None for others)
            bid_value = outcome.get('bid')
            ask_value = outcome.get('ask')
            depth_value = outcome.get('depth_usd')

            if odds_batch:
                odds_batch.add(final_id, storage_provider, market_type, outcome_name, odds_value, point_value,
                               provider_meta=provider_meta, bid=bid_value, ask=ask_value, depth_usd=depth_value)
            else:
                odds_new += upsert_odds(session, final_id, storage_provider, market_type, outcome_name, odds_value, point_value,
                                        provider_meta=provider_meta, bid=bid_value, ask=ask_value, depth_usd=depth_value)
```

- [ ] **Step 6: Run extraction and verify data persists**

Run: `cd backend && python -m src.app extract polymarket`

Then verify with sqlite:
```sql
SELECT provider_id, market, outcome, odds, bid, ask, depth_usd
FROM odds WHERE provider_id = 'polymarket' AND bid IS NOT NULL LIMIT 5;
```

Expected: Rows with non-null `bid`, `ask`, `depth_usd` values in the 0-1 probability range.

- [ ] **Step 7: Commit**

```bash
git add backend/src/pipeline/storage.py
git commit -m "feat(storage): persist bid/ask/depth from CLOB through OddsBatchProcessor"
```

---

### Task 4: Scanner — pass bid/ask through group_odds

**Files:**
- Modify: `backend/src/analysis/scanner.py:1097-1102` (`group_odds` append)

- [ ] **Step 1: Include bid/ask/depth in grouped odds dict**

In `backend/src/analysis/scanner.py`, replace the `grouped` append block (~line 1097):

```python
            grouped[market_key][outcome].append({
                "provider": odds.provider_id,
                "odds": odds.odds,
                "point": odds.point,
                "updated_at": odds.updated_at,
            })
```

With:

```python
            grouped[market_key][outcome].append({
                "provider": odds.provider_id,
                "odds": odds.odds,
                "point": odds.point,
                "updated_at": odds.updated_at,
                "bid": odds.bid,
                "ask": odds.ask,
            })
```

- [ ] **Step 2: Pass bid/ask to `find_value()` in `find_value_in_market()`**

In `backend/src/analysis/scanner.py`, modify the `find_value()` call (~line 1415):

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

- [ ] **Step 3: Commit**

```bash
git add backend/src/analysis/scanner.py
git commit -m "feat(scanner): pass bid/ask from odds through group_odds to find_value"
```

---

### Task 5: Edge calculation — subtract spread cost

**Files:**
- Modify: `backend/src/analysis/value.py:81-129` (`find_value`)
- Create: `backend/tests/test_polymarket_true_edge.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_polymarket_true_edge.py`:

```python
"""Tests for Polymarket true edge calculation with spread cost."""
from src.analysis.value import find_value, polymarket_effective_odds


def test_spread_cost_reduces_edge():
    """Edge should be lower when bid-ask spread is wide."""
    # Polymarket ask VWAP odds = 16.26 (ask price ~0.0615)
    # Pinnacle fair odds = 9.27
    # Without spread: edge = (effective_odds / 9.27 - 1) * 100
    # With spread: edge should be meaningfully lower

    vb_no_spread = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=16.26, fair_odds=9.27,
    )
    vb_with_spread = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=16.26, fair_odds=9.27,
        bid=0.04, ask=0.07,  # Wide spread: mid=0.055, ask=0.07
    )
    assert vb_no_spread is not None
    assert vb_with_spread is not None
    assert vb_with_spread.edge_pct < vb_no_spread.edge_pct


def test_tight_spread_minimal_impact():
    """Tight spread should barely change the edge."""
    vb_no_spread = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="polymarket", provider_odds=1.98, fair_odds=1.178,
        min_edge_pct=0,
    )
    vb_tight = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="polymarket", provider_odds=1.98, fair_odds=1.178,
        bid=0.50, ask=0.52,  # Tight spread: mid=0.51
        min_edge_pct=0,
    )
    assert vb_no_spread is not None
    assert vb_tight is not None
    # Tight spread should reduce edge by less than 5 percentage points
    assert vb_no_spread.edge_pct - vb_tight.edge_pct < 5


def test_no_bid_falls_back_to_fee_only():
    """When bid is None, should use current fee-only behavior."""
    vb_no_bid = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=3.0, fair_odds=2.5,
        bid=None, ask=0.35,
    )
    vb_baseline = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=3.0, fair_odds=2.5,
    )
    assert vb_no_bid is not None
    assert vb_baseline is not None
    assert vb_no_bid.edge_pct == vb_baseline.edge_pct


def test_non_polymarket_ignores_bid_ask():
    """Non-Polymarket providers should ignore bid/ask even if passed."""
    vb_with = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="unibet", provider_odds=2.10, fair_odds=1.90,
        bid=0.40, ask=0.50,
        min_edge_pct=0,
    )
    vb_without = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="unibet", provider_odds=2.10, fair_odds=1.90,
        min_edge_pct=0,
    )
    assert vb_with.edge_pct == vb_without.edge_pct


def test_spread_can_eliminate_edge():
    """Very wide spread should be able to push edge below min threshold."""
    vb = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=2.50, fair_odds=2.30,
        bid=0.20, ask=0.45,  # Extremely wide spread
        min_edge_pct=2.0,
    )
    # With such a wide spread, the edge should be eliminated
    assert vb is None


def test_fee_still_applied_with_spread():
    """Spread cost is additional to the 2% fee, not replacing it."""
    # Calculate what fee-only edge would be
    effective = polymarket_effective_odds(4.0)
    fee_only_edge = (effective / 3.0 - 1) * 100

    vb = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=4.0, fair_odds=3.0,
        bid=0.24, ask=0.26,  # Tight but nonzero spread
        min_edge_pct=0,
    )
    assert vb is not None
    # Edge should be less than fee-only (spread adds cost)
    assert vb.edge_pct < fee_only_edge
    # But should still be positive (spread is small)
    assert vb.edge_pct > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_polymarket_true_edge.py -v`

Expected: Multiple failures — `find_value()` doesn't accept `bid`/`ask` params yet.

- [ ] **Step 3: Implement spread cost in `find_value()`**

In `backend/src/analysis/value.py`, modify `find_value()` (~line 81):

```python
def find_value(
    event_id: str,
    market: str,
    outcome: str,
    provider: str,
    provider_odds: float,
    fair_odds: float,
    min_edge_pct: float = 2.0,
    bid: float = None,
    ask: float = None,
) -> Optional[ValueBet]:
    """
    Check if a bet has positive expected value.

    Args:
        event_id: Canonical event ID
        market: Market type
        outcome: Outcome name ("home", "over", etc.)
        provider: Provider offering the odds
        provider_odds: Decimal odds from provider
        fair_odds: Fair decimal odds (from Pinnacle de-vigged)
        min_edge_pct: Minimum edge to consider (default 2%)
        bid: Best bid price in probability space (0-1), CLOB only
        ask: Best ask price in probability space (0-1), CLOB only

    Returns:
        ValueBet if edge >= min_edge_pct, None otherwise
    """
    if fair_odds <= 1 or provider_odds <= 1:
        return None

    # For Polymarket, use effective odds after 2% fee on profit
    effective_odds = polymarket_effective_odds(provider_odds) if provider == "polymarket" else provider_odds

    # Calculate edge using effective (post-fee) odds
    edge = (effective_odds / fair_odds) - 1

    # For Polymarket with CLOB data, subtract bid-ask spread cost
    if provider == "polymarket" and bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        mid_odds = 1 / mid
        ask_odds = provider_odds  # Already VWAP-based
        spread_cost = (ask_odds - mid_odds) / mid_odds
        edge -= spread_cost

    edge_pct = edge * 100

    if edge_pct < min_edge_pct:
        return None

    fair_probability = 1 / fair_odds

    return ValueBet(
        event_id=event_id,
        market=market,
        outcome=outcome,
        provider=provider,
        provider_odds=provider_odds,
        fair_odds=round(fair_odds, 3),
        fair_probability=round(fair_probability, 3),
        edge_pct=round(edge_pct, 2),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_polymarket_true_edge.py -v`

Expected: All 7 tests pass.

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30`

Expected: No new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/src/analysis/value.py backend/tests/test_polymarket_true_edge.py
git commit -m "feat(value): subtract bid-ask spread cost from Polymarket edge calculation"
```

---

### Task 6: End-to-end verification

**Files:** None (verification only)

- [ ] **Step 1: Run full extraction**

Run: `cd backend && python -m src.app extract polymarket pinnacle`

Expected: Both extract successfully.

- [ ] **Step 2: Query to verify spread-adjusted edges**

```sql
SELECT
    e.home_team || ' vs ' || e.away_team as event,
    o.market, o.outcome,
    od.odds, od.bid, od.ask, od.depth_usd,
    o.edge_pct
FROM opportunities o
JOIN events e ON o.event_id = e.id
JOIN odds od ON od.event_id = o.event_id
    AND od.provider_id = o.provider1_id
    AND od.market = o.market
    AND od.outcome = o.outcome1
WHERE o.provider1_id = 'polymarket'
AND o.is_active = 1
AND od.bid IS NOT NULL
ORDER BY o.edge_pct DESC
LIMIT 10;
```

Expected: Edge values should be lower than before, especially for thin markets. The 72% LYON vs Contra edge should be significantly reduced.

- [ ] **Step 3: Commit final state if any fixes were needed**

```bash
git add -A
git commit -m "fix: address any issues found during e2e verification"
```
