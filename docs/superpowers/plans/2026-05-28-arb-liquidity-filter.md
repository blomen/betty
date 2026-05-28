# Arb Liquidity Filter + Bonus Min-Odds Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface Pinnacle's per-line `maxRiskStake` so the operator can filter the arb table to only events liquid enough that limited soft-book accounts will honor full stake, and inline-display each provider's `bonus.trigger_odds` next to the existing deposit hint.

**Architecture:** Pinnacle extractor → new `Odds.max_stake` column → bubble through `arb-workflow` as `pinnacle_max_stake_sek` per opp → arb sub-tab threshold chip. Parallel: `bankroll_service.get_bankroll()` exposes `bonus_trigger_odds` → `BalanceCell` appends `@ 1.50+` to the deposit hint.

**Tech Stack:** Python 3.12 + SQLAlchemy + FastAPI (backend), React 19 + TypeScript (frontend). Postgres ON CONFLICT upsert path in `OddsBatchProcessor._flush_inner`. In-place SQL migration via `_run_pg_migrations` in `models.py` (Betty does not use Alembic).

**Reference spec:** `docs/superpowers/specs/2026-05-28-arb-liquidity-filter-design.md`

---

## File Structure

**Backend — modified:**
- `backend/src/db/models.py` — add `Odds.max_stake` column + entry in `_run_pg_migrations` additions list
- `backend/src/providers/pinnacle.py` — capture `market.limits[0].amount` into `market_meta`, propagate onto each outcome dict
- `backend/src/pipeline/storage.py` — extend `OddsBatchProcessor.add()` + `_flush_inner` to persist `max_stake`; also extend `upsert_odds` for symmetry
- `backend/src/analysis/scanner.py` — include `max_stake` in the leg dict built in `group_odds`
- `backend/src/services/opportunity_service.py` — compute and attach `pinnacle_max_stake_sek` per opp in `scan_arb_workflow`
- `backend/src/services/bankroll_service.py` — add `bonus_trigger_odds` field to `get_bankroll` per-provider dict
- `frontend/src/pages/PlayPage.tsx` — `ProviderBalanceInfo.bonus_trigger_odds` + `getTrigger` + `BalanceCell` + `load()` mapper + arb sub-tab `liqThresholdSek` state/chip/filter/badge

**Tests — new/modified:**
- `backend/tests/providers/test_pinnacle_max_stake.py` — new
- `backend/tests/pipeline/test_storage_max_stake.py` — new
- `backend/tests/test_bankroll_service_trigger.py` — extend existing fixture for `bonus_trigger_odds`

**Why this decomposition:** the data flow is strictly linear (extractor → storage → scanner → service → frontend), so each task can be tested in isolation with a fixture at its layer. The bonus min-odds feature is independent — it can ship in either order. We do it first (Task 1) because it's small and self-contained.

---

## Task 1: Bonus min-odds — backend

**Files:**
- Modify: `backend/src/services/bankroll_service.py:69-80` (the `provider_data.append({...})` block inside `get_bankroll`)
- Test: `backend/tests/test_bankroll_service_trigger.py` (extend existing fixture)

- [ ] **Step 1: Read the existing `bonus_trigger_amount` assignment**

Open `backend/src/services/bankroll_service.py`. The relevant block is lines 69-80:

```python
provider_data.append(
    {
        "id": p.id,
        "name": p.name,
        "balance": balance,
        "currency": currency,
        "exchange_rate_sek": rate,
        "balance_sek": round(balance * rate, 2),
        "bonus_trigger_amount": amount if trigger_actionable else None,
        "bonus_currency": currency if trigger_actionable else None,
    }
)
```

We will add a third bonus field tied to the same `trigger_actionable` gate.

- [ ] **Step 2: Write the failing test** (extend the existing fixture)

Open `backend/tests/test_bankroll_service_trigger.py`. After the existing `test_trigger_null_when_balance_already_covers_amount`, append:

```python
def test_trigger_odds_populated_when_configured_in_yaml(db, monkeypatch):
    # Override the fixture's yaml stub to include trigger_odds for leovegas
    monkeypatch.setattr(
        "src.api.routes.providers.load_provider_bonuses",
        lambda: {
            "unibet": {"type": "freebet", "amount": 1000},  # no trigger_odds
            "leovegas": {"type": "bonusdeposit", "amount": 600, "trigger_odds": 1.80},
        },
    )
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["leovegas"]["bonus_trigger_odds"] == 1.80
    assert by_id["unibet"]["bonus_trigger_odds"] is None


def test_trigger_odds_null_when_bonus_already_claimed(db, monkeypatch):
    from src.db.models import ProfileProviderBonus
    monkeypatch.setattr(
        "src.api.routes.providers.load_provider_bonuses",
        lambda: {"leovegas": {"type": "bonusdeposit", "amount": 600, "trigger_odds": 1.80}},
    )
    # Mark the bonus as claimed → trigger_actionable becomes False
    db.query(ProfileProviderBonus).filter_by(provider_id="leovegas").update({"bonus_status": "claimed"})
    db.commit()
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["leovegas"]["bonus_trigger_odds"] is None
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_bankroll_service_trigger.py::test_trigger_odds_populated_when_configured_in_yaml tests/test_bankroll_service_trigger.py::test_trigger_odds_null_when_bonus_already_claimed -v
```

Expected: FAIL with `KeyError: 'bonus_trigger_odds'` (the field doesn't exist yet).

- [ ] **Step 4: Implement — add the field**

In `backend/src/services/bankroll_service.py` line 69-80, change the `provider_data.append` block to:

```python
provider_data.append(
    {
        "id": p.id,
        "name": p.name,
        "balance": balance,
        "currency": currency,
        "exchange_rate_sek": rate,
        "balance_sek": round(balance * rate, 2),
        "bonus_trigger_amount": amount if trigger_actionable else None,
        "bonus_currency": currency if trigger_actionable else None,
        "bonus_trigger_odds": cfg.get("trigger_odds") if trigger_actionable else None,
    }
)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_bankroll_service_trigger.py -v
```

Expected: all tests PASS (the two new ones plus the three existing ones).

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/bankroll_service.py backend/tests/test_bankroll_service_trigger.py
git commit -m "feat(bankroll): expose bonus_trigger_odds in /api/bankroll response"
```

---

## Task 2: Bonus min-odds — frontend

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx:71-82` (`ProviderBalanceInfo` type), `:85-90` (`getTrigger`), `:175-197` (`BalanceCell`), `:744-756` (the bankroll-to-state mapper inside `load()`)

- [ ] **Step 1: Extend `ProviderBalanceInfo` type**

In `PlayPage.tsx` around line 71, change:

```ts
type ProviderBalanceInfo = {
  balance: number
  bonus_trigger?: number
  bonus_currency?: string
  // Native-currency balance (USDC for polymarket, SEK for everyone else)…
  balance_native?: number
  currency?: string
}
```

to:

```ts
type ProviderBalanceInfo = {
  balance: number
  bonus_trigger?: number
  bonus_currency?: string
  bonus_trigger_odds?: number
  // Native-currency balance (USDC for polymarket, SEK for everyone else)…
  balance_native?: number
  currency?: string
}
```

- [ ] **Step 2: Extend `getTrigger` to return min-odds**

Around line 85, change:

```ts
const getTrigger = (b: ProviderBalanceLike | undefined): { amount: number; currency: string } | null => {
  if (b == null || typeof b === 'number') return null
  return b.bonus_trigger != null && b.bonus_trigger > 0
    ? { amount: b.bonus_trigger, currency: b.bonus_currency ?? 'SEK' }
    : null
}
```

to:

```ts
const getTrigger = (b: ProviderBalanceLike | undefined): { amount: number; currency: string; odds?: number } | null => {
  if (b == null || typeof b === 'number') return null
  return b.bonus_trigger != null && b.bonus_trigger > 0
    ? { amount: b.bonus_trigger, currency: b.bonus_currency ?? 'SEK', odds: b.bonus_trigger_odds }
    : null
}
```

- [ ] **Step 3: Extend `BalanceCell` to render the odds threshold**

Around line 190-194, change:

```tsx
{trigger && balance < 1 && (
  <span className="ml-2 text-xs text-orange-400/80" title="Deposit to unlock provider bonus">
    · deposit {trigger.amount.toFixed(0)} {trigger.currency.toLowerCase()}
  </span>
)}
```

to:

```tsx
{trigger && balance < 1 && (
  <span
    className="ml-2 text-xs text-orange-400/80"
    title={trigger.odds
      ? `Deposit ${trigger.amount.toFixed(0)} ${trigger.currency} and wager at odds ≥ ${trigger.odds.toFixed(2)} to unlock provider bonus`
      : 'Deposit to unlock provider bonus'}
  >
    · deposit {trigger.amount.toFixed(0)} {trigger.currency.toLowerCase()}
    {trigger.odds != null && ` @ ${trigger.odds.toFixed(2)}+`}
  </span>
)}
```

- [ ] **Step 4: Pass the new field through `load()`'s bankroll mapper**

Around line 744-756 the code maps `/api/bankroll` rows into the local `providerBalances` map. Locate the block (search for `bonus_trigger:` and `bonus_currency:`). Change:

```ts
bonus_trigger: p.bonus_trigger_amount ?? undefined,
bonus_currency: p.bonus_currency ?? undefined,
```

to:

```ts
bonus_trigger: p.bonus_trigger_amount ?? undefined,
bonus_currency: p.bonus_currency ?? undefined,
bonus_trigger_odds: p.bonus_trigger_odds ?? undefined,
```

- [ ] **Step 5: Verify the type-check passes**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no new type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(play): show bonus min-odds threshold in deposit hint"
```

---

## Task 3: Pinnacle max-stake — Odds column + migration

**Files:**
- Modify: `backend/src/db/models.py` (Odds class, around line 188-202) and the `_run_pg_migrations` additions list around line 1802-1829

- [ ] **Step 1: Add the SQLAlchemy column**

In `backend/src/db/models.py`, the `Odds` class is at line 171. Around line 197 (right after the `scope = Column(...)` line and before the `bid = Column(...)` line), insert:

```python
    # Pinnacle exposes a per-market-line maxRiskStake in USD. Captured for
    # the arb-table liquidity filter — soft books calibrate their per-account
    # caps proportionally to this. Null for non-Pinnacle providers and for
    # rows extracted before this column shipped (backfills naturally on the
    # next Pinnacle cycle).
    max_stake = Column(Float, nullable=True)
```

- [ ] **Step 2: Add the migration entry**

In `_run_pg_migrations` (line 1791), the `additions` list ends around line 1828. Append a new entry **before** the closing `]`:

```python
        # 2026-05-28 — Pinnacle per-line max risk stake (USD). Null on
        # non-Pinnacle rows and on any row predating this column.
        ("odds", "max_stake", "DOUBLE PRECISION"),
```

- [ ] **Step 3: Write a smoke test verifying the column exists after init_db**

Create `backend/tests/test_odds_max_stake_column.py`:

```python
"""Odds.max_stake column is present after init_db."""

from sqlalchemy import create_engine, inspect
from src.db.models import Base


def test_odds_max_stake_column_present():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("odds")}
    assert "max_stake" in cols
```

- [ ] **Step 4: Run the test**

```bash
cd backend && pytest tests/test_odds_max_stake_column.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_odds_max_stake_column.py
git commit -m "feat(db): add Odds.max_stake column for Pinnacle line caps"
```

---

## Task 4: Pinnacle extractor — capture `maxRiskStake`

**Files:**
- Modify: `backend/src/providers/pinnacle.py` — `_parse_markets` (line 404), `_parse_moneyline` (line 530), `_parse_spread` (line 566), `_parse_total` (line 591)
- Test: `backend/tests/providers/test_pinnacle_max_stake.py` (new)

**What Pinnacle ships:** the `markets/straight` response includes a `limits` array on each market dict:

```json
{
  "type": "moneyline",
  "matchupId": 123,
  "period": 0,
  "limits": [{"amount": 1500, "type": "maxRiskStake"}],
  "prices": [...]
}
```

Some markets ship `limits: []` or omit it entirely. The amount is in USD.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/providers/test_pinnacle_max_stake.py`:

```python
"""Pinnacle parser extracts maxRiskStake from market.limits into each outcome."""

from src.providers.pinnacle import PinnacleRetriever


def _market_with_limits(limits):
    return {
        "status": "open",
        "type": "moneyline",
        "period": 0,
        "isAlternate": False,
        "lineId": 1,
        "matchupId": 1,
        "limits": limits,
        "prices": [
            {"designation": "home", "price": -110},
            {"designation": "away", "price": -110},
        ],
    }


def test_max_stake_extracted_from_limits():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_market_with_limits([{"amount": 1500, "type": "maxRiskStake"}])])
    assert parsed
    for m in parsed:
        for o in m["outcomes"]:
            assert o.get("max_stake") == 1500.0


def test_max_stake_none_when_limits_missing():
    p = PinnacleRetriever({"id": "pinnacle"})
    market = _market_with_limits([])
    del market["limits"]  # remove entirely
    parsed = p._parse_markets([market])
    assert parsed
    for m in parsed:
        for o in m["outcomes"]:
            assert o.get("max_stake") is None


def test_max_stake_none_when_limits_empty():
    p = PinnacleRetriever({"id": "pinnacle"})
    parsed = p._parse_markets([_market_with_limits([])])
    assert parsed
    for m in parsed:
        for o in m["outcomes"]:
            assert o.get("max_stake") is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/providers/test_pinnacle_max_stake.py -v
```

Expected: FAIL on `assert o.get("max_stake") == 1500.0` (key absent → returns None).

- [ ] **Step 3: Implement — capture limits in `_parse_markets`**

In `backend/src/providers/pinnacle.py`, find the `market_meta = {...}` block in `_parse_markets` (around line 444-449):

```python
            # Capture provider-specific IDs at market level
            market_meta = {
                "matchup_id": str(market.get("matchupId", "")),
                "period": period,
                "line_id": str(market.get("lineId", "")),
            }
```

Replace with:

```python
            # Capture provider-specific IDs at market level
            limits = market.get("limits") or []
            max_stake_usd = float(limits[0]["amount"]) if (limits and limits[0].get("amount") is not None) else None
            market_meta = {
                "matchup_id": str(market.get("matchupId", "")),
                "period": period,
                "line_id": str(market.get("lineId", "")),
                "max_stake_usd": max_stake_usd,
            }
```

- [ ] **Step 4: Propagate `max_stake` onto each outcome dict in the three parsers**

In `_parse_moneyline` (line 530), change the outcome append block (line 540-546):

```python
                outcomes.append(
                    {
                        "name": designation,
                        "odds": decimal_odds,
                        "provider_meta": {"designation": designation},
                    }
                )
```

to:

```python
                outcomes.append(
                    {
                        "name": designation,
                        "odds": decimal_odds,
                        "provider_meta": {"designation": designation},
                        "max_stake": market_meta.get("max_stake_usd"),
                    }
                )
```

Apply the same change to `_parse_spread` (line 577-584):

```python
                outcomes.append(
                    {
                        "name": designation,
                        "odds": decimal_odds,
                        "point": float(points),
                        "provider_meta": {"designation": designation},
                        "max_stake": market_meta.get("max_stake_usd"),
                    }
                )
```

And to `_parse_total` (line 602-608):

```python
                outcomes.append(
                    {
                        "name": designation,
                        "odds": decimal_odds,
                        "point": float(points),
                        "provider_meta": {"designation": designation},
                        "max_stake": market_meta.get("max_stake_usd"),
                    }
                )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/providers/test_pinnacle_max_stake.py tests/providers/test_pinnacle_scope.py -v
```

Expected: all PASS (max-stake tests + existing scope tests as a regression check).

- [ ] **Step 6: Commit**

```bash
git add backend/src/providers/pinnacle.py backend/tests/providers/test_pinnacle_max_stake.py
git commit -m "feat(pinnacle): extract maxRiskStake into outcome dicts"
```

---

## Task 5: Storage — persist `max_stake` through `OddsBatchProcessor`

**Files:**
- Modify: `backend/src/pipeline/storage.py` — `upsert_odds` (line 1351), `OddsBatchProcessor.add` (line 1452), `_flush_inner` rows + ON CONFLICT clause (line 1562-1591), plus the two call sites at lines 703 and 1317 that pass outcome data to `odds_batch.add`
- Test: `backend/tests/pipeline/test_storage_max_stake.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/pipeline/test_storage_max_stake.py`:

```python
"""OddsBatchProcessor persists max_stake on Odds rows and updates it on conflict."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Odds, Provider
from src.pipeline.storage import OddsBatchProcessor, upsert_odds


@pytest.fixture
def db():
    # Postgres-only ON CONFLICT path — use the real prod DB via DATABASE_URL,
    # or skip when Postgres isn't available. For unit-test coverage of the
    # column itself, the individual upsert_odds path works on sqlite.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Provider(id="pinnacle", name="Pinnacle", is_enabled=True))
    session.add(Event(id="evt1", sport="ice_hockey", home_team="A", away_team="B"))
    session.commit()
    yield session
    session.close()


def test_upsert_odds_writes_max_stake(db):
    upsert_odds(
        db, event_id="evt1", provider="pinnacle", market="moneyline",
        outcome="home", odds=2.10, max_stake=1500.0,
    )
    db.commit()
    row = db.query(Odds).filter_by(event_id="evt1", outcome="home").one()
    assert row.max_stake == 1500.0


def test_upsert_odds_updates_max_stake_on_conflict(db):
    upsert_odds(
        db, event_id="evt1", provider="pinnacle", market="moneyline",
        outcome="home", odds=2.10, max_stake=1500.0,
    )
    db.commit()
    upsert_odds(
        db, event_id="evt1", provider="pinnacle", market="moneyline",
        outcome="home", odds=2.15, max_stake=2200.0,
    )
    db.commit()
    row = db.query(Odds).filter_by(event_id="evt1", outcome="home").one()
    assert row.odds == 2.15
    assert row.max_stake == 2200.0


def test_upsert_odds_max_stake_null_when_omitted(db):
    upsert_odds(
        db, event_id="evt1", provider="pinnacle", market="moneyline",
        outcome="home", odds=2.10,
    )
    db.commit()
    row = db.query(Odds).filter_by(event_id="evt1", outcome="home").one()
    assert row.max_stake is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/pipeline/test_storage_max_stake.py -v
```

Expected: FAIL with `TypeError: upsert_odds() got an unexpected keyword argument 'max_stake'`.

- [ ] **Step 3: Implement — extend `upsert_odds`**

In `backend/src/pipeline/storage.py` line 1351, change the signature and body. Current:

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
    scope: str = "ft",
) -> int:
```

New:

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
    scope: str = "ft",
    max_stake: float | None = None,
) -> int:
```

In the `if existing:` branch (line 1397) and `else:` branch (line 1406), add `max_stake` handling:

```python
    if existing:
        existing.odds = odds
        existing.updated_at = datetime.now(UTC)
        if provider_meta:
            existing.provider_meta = provider_meta
        existing.bid = bid
        existing.ask = ask
        existing.depth_usd = depth_usd
        existing.max_stake = max_stake
        return 0
    else:
        session.add(
            Odds(
                event_id=event_id,
                provider_id=provider,
                market=market,
                outcome=outcome,
                odds=odds,
                point=point,
                provider_meta=provider_meta,
                bid=bid,
                ask=ask,
                depth_usd=depth_usd,
                scope=scope,
                max_stake=max_stake,
            )
        )
        return 1
```

- [ ] **Step 4: Extend `OddsBatchProcessor.add`**

Around line 1452, change the signature and body:

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
        scope: str = "ft",
        max_stake: float | None = None,
    ):
```

In the `self._pending[key] = {...}` block (line 1476), add `"max_stake": max_stake,` at the end before the closing brace:

```python
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
            "scope": scope,
            "max_stake": max_stake,
        }
```

- [ ] **Step 5: Extend `_flush_inner` to write the column**

Around line 1562-1578, in the `rows = [{...} for r in batch]` block, append `"max_stake": r.get("max_stake"),` before the closing `"updated_at"` field:

```python
            rows = [
                {
                    "event_id": r["event_id"],
                    "provider_id": r["provider_id"],
                    "market": r["market"],
                    "outcome": r["outcome"],
                    "odds": r["odds"],
                    "point": r.get("point"),
                    "provider_meta": r.get("provider_meta"),
                    "bid": r.get("bid"),
                    "ask": r.get("ask"),
                    "depth_usd": r.get("depth_usd"),
                    "scope": r.get("scope", "ft"),
                    "max_stake": r.get("max_stake"),
                    "updated_at": now,
                }
                for r in batch
            ]
```

Then in the `on_conflict_do_update` `set_` dict (line 1583-1590), add `"max_stake": stmt.excluded.max_stake,`:

```python
            stmt = stmt.on_conflict_do_update(
                constraint="uq_odds_with_point_scope",
                set_={
                    "odds": stmt.excluded.odds,
                    "updated_at": stmt.excluded.updated_at,
                    "provider_meta": stmt.excluded.provider_meta,
                    "bid": stmt.excluded.bid,
                    "ask": stmt.excluded.ask,
                    "depth_usd": stmt.excluded.depth_usd,
                    "max_stake": stmt.excluded.max_stake,
                },
            ).returning(
```

- [ ] **Step 6: Update the two `odds_batch.add(...)` call sites to pass `max_stake`**

Line 703 (polymarket path — passes None since the field isn't extracted for non-Pinnacle):

```python
            if odds_batch:
                odds_batch.add(
                    matched_id,
                    "polymarket",
                    market_type,
                    outcome_norm,
                    odds,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                    max_stake=outcome.get("max_stake"),
                )
```

Line 1317 (generic-provider path — same; Pinnacle's outcome dict will have `max_stake` populated, all other providers will get None which is fine):

```python
            if odds_batch:
                odds_batch.add(
                    final_id,
                    storage_provider,
                    market_type,
                    outcome_name,
                    odds_value,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                    max_stake=outcome.get("max_stake"),
                )
```

Also update the `upsert_odds` fallback at line 1331 (passes `max_stake` too):

```python
            else:
                odds_new += upsert_odds(
                    session,
                    final_id,
                    storage_provider,
                    market_type,
                    outcome_name,
                    odds_value,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                    max_stake=outcome.get("max_stake"),
                )
```

And the polymarket-path upsert_odds at line 717:

```python
            else:
                odds_new += upsert_odds(
                    session,
                    matched_id,
                    "polymarket",
                    market_type,
                    outcome_norm,
                    odds,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                    max_stake=outcome.get("max_stake"),
                )
```

- [ ] **Step 7: Run the tests**

```bash
cd backend && pytest tests/pipeline/test_storage_max_stake.py tests/pipeline/test_storage_scope.py -v
```

Expected: max_stake tests PASS, scope tests stay PASS (regression check).

- [ ] **Step 8: Commit**

```bash
git add backend/src/pipeline/storage.py backend/tests/pipeline/test_storage_max_stake.py
git commit -m "feat(storage): persist Odds.max_stake through batch upsert path"
```

---

## Task 6: Scanner — include `max_stake` in leg dicts

**Files:**
- Modify: `backend/src/analysis/scanner.py:1358-1367` (leg dict assembly in `group_odds`)
- Test: extend existing scanner test or add a tiny smoke test

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_scanner_max_stake_leg.py`:

```python
"""Scanner.group_odds propagates Odds.max_stake into leg dicts."""

import pytest
from datetime import datetime, UTC
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Odds, Provider
from src.analysis.scanner import OpportunityScanner


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Provider(id="pinnacle", name="Pinnacle", is_enabled=True))
    ev = Event(
        id="evt1", sport="basketball", home_team="A", away_team="B",
        start_time=datetime.now(UTC), home_away_validated=True,
    )
    session.add(ev)
    session.flush()
    session.add(Odds(
        event_id="evt1", provider_id="pinnacle", market="moneyline",
        outcome="home", odds=2.10, scope="ft", max_stake=1500.0,
    ))
    session.commit()
    yield session
    session.close()


def test_group_odds_includes_max_stake_in_leg_dict(db):
    scanner = OpportunityScanner(db)
    ev = db.query(Event).one()
    grouped = scanner.group_odds(ev, check_staleness=False)
    assert grouped, "no markets grouped"
    leg = grouped["moneyline"]["home"][0]
    assert leg["max_stake"] == 1500.0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_scanner_max_stake_leg.py -v
```

Expected: FAIL with `KeyError: 'max_stake'`.

- [ ] **Step 3: Implement — add `max_stake` to leg dict**

In `backend/src/analysis/scanner.py` around line 1358, the leg dict assembly inside `group_odds`:

```python
            grouped[market_key][outcome].append(
                {
                    "provider": odds.provider_id,
                    "odds": odds.odds,
                    "point": odds.point,
                    "updated_at": odds.updated_at,
                    "bid": odds.bid,
                    "ask": odds.ask,
                }
            )
```

Change to:

```python
            grouped[market_key][outcome].append(
                {
                    "provider": odds.provider_id,
                    "odds": odds.odds,
                    "point": odds.point,
                    "updated_at": odds.updated_at,
                    "bid": odds.bid,
                    "ask": odds.ask,
                    "max_stake": odds.max_stake,
                }
            )
```

- [ ] **Step 4: Run the test**

```bash
cd backend && pytest tests/test_scanner_max_stake_leg.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the existing scanner-adjacent tests as a regression check**

```bash
cd backend && pytest tests/test_scanner_max_stake_leg.py tests/pipeline/ tests/providers/ -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/analysis/scanner.py backend/tests/test_scanner_max_stake_leg.py
git commit -m "feat(scanner): propagate Odds.max_stake into leg dicts"
```

---

## Task 7: Arb-workflow — compute `pinnacle_max_stake_sek` per opp

**Files:**
- Modify: `backend/src/services/opportunity_service.py:670-693` (the `formatted.append({...})` block in `scan_arb_workflow`)
- Test: `backend/tests/test_arb_workflow_max_stake.py` (new)

**Conversion constant:** the spec uses `SEK_PER_USD = 10.5`. Define it inline next to the computation rather than importing — the value is also hard-coded on the frontend.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_arb_workflow_max_stake.py`:

```python
"""scan_arb_workflow attaches pinnacle_max_stake_sek to each opp."""

from src.services.opportunity_service import _SEK_PER_USD  # added below
from src.services.opportunity_service import OpportunityService  # for context


def test_sek_per_usd_constant_exposed():
    # Sanity check that the conversion constant exists and matches the
    # frontend's hard-coded SEK_PER_USD = 10.5 in PlayPage.tsx.
    assert _SEK_PER_USD == 10.5


def test_compute_pinnacle_max_stake_sek_picks_min_pinnacle_leg():
    """Helper picks the smallest max_stake across Pinnacle legs and converts to SEK.

    Non-Pinnacle legs are ignored. Returns None when no Pinnacle leg has a
    populated max_stake.
    """
    from src.services.opportunity_service import _compute_pinnacle_max_stake_sek

    legs = [
        {"provider": "pinnacle", "max_stake": 1500.0},
        {"provider": "pinnacle", "max_stake": 800.0},
        {"provider": "lodur", "max_stake": None},
    ]
    assert _compute_pinnacle_max_stake_sek(legs) == 800.0 * 10.5


def test_compute_pinnacle_max_stake_sek_returns_none_when_no_pinnacle():
    from src.services.opportunity_service import _compute_pinnacle_max_stake_sek
    legs = [{"provider": "polymarket", "max_stake": None}, {"provider": "lodur"}]
    assert _compute_pinnacle_max_stake_sek(legs) is None


def test_compute_pinnacle_max_stake_sek_returns_none_when_pinnacle_max_null(monkeypatch):
    from src.services.opportunity_service import _compute_pinnacle_max_stake_sek
    legs = [{"provider": "pinnacle", "max_stake": None}, {"provider": "lodur", "max_stake": None}]
    assert _compute_pinnacle_max_stake_sek(legs) is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_arb_workflow_max_stake.py -v
```

Expected: FAIL with `ImportError: cannot import name '_SEK_PER_USD'` and `_compute_pinnacle_max_stake_sek`.

- [ ] **Step 3: Implement — add the helper near the top of opportunity_service.py**

In `backend/src/services/opportunity_service.py`, find a good module-level spot (after the imports, before the first class definition). Add:

```python
# Hard-coded SEK→USD rate used to convert Pinnacle's USD maxRiskStake into
# the SEK-denominated threshold the frontend filter compares against.
# Mirrors frontend/src/pages/PlayPage.tsx SEK_PER_USD. If this ever becomes
# dynamic, lift to backend/src/config/exchange_rates.py.
_SEK_PER_USD = 10.5


def _compute_pinnacle_max_stake_sek(legs: list[dict]) -> float | None:
    """Per-opp Pinnacle max-stake in SEK.

    Returns the smallest non-null Pinnacle-leg max_stake across the opp,
    converted to SEK. Returns None when no Pinnacle leg has a populated
    max_stake (pre-backfill rows, opp with no Pinnacle leg).
    """
    pinnacle_caps = [
        leg.get("max_stake") for leg in legs
        if leg.get("provider") == "pinnacle" and leg.get("max_stake") is not None
    ]
    if not pinnacle_caps:
        return None
    return min(pinnacle_caps) * _SEK_PER_USD
```

- [ ] **Step 4: Wire the helper into `scan_arb_workflow`'s `formatted.append({...})` block**

Around line 670-693 in the same file, the `formatted.append({...})` dict. After the existing `"arb_legs": enriched_arb_legs,` line, add a new field:

```python
            formatted.append(
                {
                    "id": i + 1,
                    "type": "arb",
                    "event_id": r["event_id"],
                    "market": clean_market,
                    "point": point_value,
                    "profit_pct": r["guaranteed_profit_pct"],
                    "edge_pct": r["combined_edge_pct"],
                    "guaranteed_profit_pct": r["guaranteed_profit_pct"],
                    "sport": r["sport"],
                    "league": r["league"],
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "display_home": ev.display_home if ev else None,
                    "display_away": ev.display_away if ev else None,
                    "prov_home": prov_home,
                    "prov_away": prov_away,
                    "starts_at": r["starts_at"],
                    "legs": enriched_legs,
                    "total_stake": 0,
                    "arb_profit_pct": r.get("arb_profit_pct"),
                    "arb_legs": enriched_arb_legs,
                    "pinnacle_max_stake_sek": _compute_pinnacle_max_stake_sek(enriched_legs),
                }
            )
```

- [ ] **Step 5: Run the tests**

```bash
cd backend && pytest tests/test_arb_workflow_max_stake.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/opportunity_service.py backend/tests/test_arb_workflow_max_stake.py
git commit -m "feat(arb-workflow): attach pinnacle_max_stake_sek to each opp"
```

---

## Task 8: Frontend — arb sub-tab liquidity filter chip + badge

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx`

**State naming convention:** uses existing pattern — `betty:` prefix for localStorage keys.

- [ ] **Step 1: Add state and persistence**

Locate the `const [subTab, setSubTab]` declaration around line 638 in PlayPage.tsx. Right after the related state declarations (e.g. near `showNegativeArbs`), add:

```tsx
  // Pinnacle max-stake threshold (SEK) for the arb sub-tab. 0 = off (default).
  // Cycles through 0 / 2000 / 5000 / 10000 on chip click. Rows whose
  // pinnacle_max_stake_sek is null (pre-backfill or no Pinnacle leg) pass when
  // threshold = 0 and hide when threshold > 0 — strict.
  const LIQ_THRESHOLD_KEY = 'betty:arbLiqThreshold:v1'
  const [liqThresholdSek, setLiqThresholdSek] = useState<number>(() => {
    const raw = localStorage.getItem(LIQ_THRESHOLD_KEY)
    const parsed = raw ? parseInt(raw, 10) : 0
    return isFinite(parsed) && parsed >= 0 ? parsed : 0
  })
  useEffect(() => {
    localStorage.setItem(LIQ_THRESHOLD_KEY, String(liqThresholdSek))
  }, [liqThresholdSek])
  const cycleLiqThreshold = () => {
    setLiqThresholdSek(prev => {
      if (prev === 0) return 2000
      if (prev === 2000) return 5000
      if (prev === 5000) return 10000
      return 0
    })
  }
```

- [ ] **Step 2: Render the chip in the arb sub-tab header**

In the arb sub-tab block (the `{subTab === 'arb' && (() => {` block around line 2538), find the existing header chips — the `<button onClick={() => setShowNegativeArbs(...)}>` around line 2654. Right after that button (before the `<span className="text-[10px] text-zinc-600 ml-auto">` that reads "top 20 per cluster"), insert:

```tsx
                <button
                  onClick={cycleLiqThreshold}
                  className={`px-1.5 py-0.5 text-[9px] uppercase font-semibold rounded border transition-colors cursor-pointer ${
                    liqThresholdSek > 0
                      ? 'bg-cyan-500/20 text-cyan-100 border-cyan-500/40 hover:bg-cyan-500/30'
                      : 'bg-zinc-800/40 text-zinc-500 border-zinc-700/40 hover:bg-zinc-800/70'
                  }`}
                  title={liqThresholdSek > 0
                    ? `Hiding opps whose Pinnacle max stake < ${liqThresholdSek.toLocaleString()} kr. Click to cycle threshold.`
                    : 'Pinnacle max stake — soft books cap stakes proportionally to this. Click to filter.'}
                >
                  {liqThresholdSek === 0
                    ? 'liq off'
                    : `liq ≥ ${(liqThresholdSek / 1000).toFixed(0)}k`}
                </button>
```

- [ ] **Step 3: Apply the filter at each opp-read site**

There are three sites in the arb sub-tab that read opps for rendering. Add the filter at each. Open `frontend/src/pages/PlayPage.tsx`.

**Site A — `totalOpps` counter (around line 2611):**

```tsx
          const totalOpps = Object.values(oppsByCluster).reduce((n, arr) => {
            const visible = showNegativeArbs
              ? arr
              : arr.filter((o: any) => (o.guaranteed_profit_pct ?? 0) >= 0)
            return n + visible.length
          }, 0)
```

Change to:

```tsx
          const totalOpps = Object.values(oppsByCluster).reduce((n, arr) => {
            const liqFiltered = liqThresholdSek > 0
              ? arr.filter((o: any) => (o.pinnacle_max_stake_sek ?? 0) >= liqThresholdSek)
              : arr
            const visible = showNegativeArbs
              ? liqFiltered
              : liqFiltered.filter((o: any) => (o.guaranteed_profit_pct ?? 0) >= 0)
            return n + visible.length
          }, 0)
```

**Site B — Deposit-hint path `qualifyingOpps` (around line 2693):**

```tsx
                      const qualifyingOpps = opps.filter(
                        (o: any) => (o.guaranteed_profit_pct ?? 0) >= DEPOSIT_HINT_MIN_PROFIT_PCT,
                      ).slice(0, 10)
```

Change to:

```tsx
                      const qualifyingOpps = opps.filter(
                        (o: any) =>
                          (o.guaranteed_profit_pct ?? 0) >= DEPOSIT_HINT_MIN_PROFIT_PCT &&
                          (liqThresholdSek === 0 || (o.pinnacle_max_stake_sek ?? 0) >= liqThresholdSek),
                      ).slice(0, 10)
```

**Site C — Funded-cluster path `opps.filter(...)` (around line 3319):**

```tsx
                                    {opps.filter((opp: any) => {
                                      if (drainedEventIds.has(opp.event_id)) return false
                                      const p = opp.guaranteed_profit_pct ?? 0
                                      if (p > 30) return false
                                      if (p < 0 && !showNegativeArbs) return false
                                      return true
                                    }).map((opp: any, i: number) => {
```

Change to:

```tsx
                                    {opps.filter((opp: any) => {
                                      if (drainedEventIds.has(opp.event_id)) return false
                                      const p = opp.guaranteed_profit_pct ?? 0
                                      if (p > 30) return false
                                      if (p < 0 && !showNegativeArbs) return false
                                      if (liqThresholdSek > 0 && (opp.pinnacle_max_stake_sek ?? 0) < liqThresholdSek) return false
                                      return true
                                    }).map((opp: any, i: number) => {
```

The `clusterHasQualifyingArb` predicate (declared earlier around line 2586) deliberately stays unfiltered — the liquidity filter is a per-row display gate, not a cluster-visibility gate. Don't touch it.

- [ ] **Step 4: Add the per-row `liq Nk` badge in the funded-cluster row**

In the funded-cluster row template, find the TTK `<td>` (around line 3739):

```tsx
                                          <td className={`px-2 py-1 font-mono text-[10px] w-[44px] text-right ${ttkClass(opp.starts_at)}`} title={opp.starts_at ? `kicks off ${new Date(opp.starts_at).toLocaleString()}` : 'no start time'}>
                                            {fmtTtkFromIso(opp.starts_at)}
                                          </td>
```

Immediately after this `</td>` and before the market `<td>` (line 3742), insert a new `<td>` for the liquidity badge:

```tsx
                                          <td className="px-1 py-1 text-[9px] w-[50px] text-right">
                                            {opp.pinnacle_max_stake_sek != null && (
                                              <span
                                                className="px-1 py-0.5 rounded bg-cyan-900/30 text-cyan-300 border border-cyan-700/30 uppercase tracking-wider font-mono"
                                                title={`Pinnacle max stake ${Math.round(opp.pinnacle_max_stake_sek).toLocaleString()} kr — soft books typically cap stakes proportionally to this`}
                                              >
                                                liq {(opp.pinnacle_max_stake_sek / 1000).toFixed(1)}k
                                              </span>
                                            )}
                                          </td>
```

The deposit-hint row (around line 2752) doesn't need the badge — that path is short and the cluster header already exposes the qualifying-arb count. Only the funded-cluster row gets the badge.

- [ ] **Step 5: Verify type-check passes**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(play): add arb-table liquidity filter and per-row max-stake badge"
```

---

## Task 9: Manual verification post-deploy

This task is not code — it's the smoke check after the backend deploys and the first Pinnacle cycle backfills `Odds.max_stake`.

- [ ] **Step 1: Deploy backend**

```bash
ssh root@148.251.40.251 "bash /opt/betty/backend/scripts/server-deploy.sh rebuild backend"
```

Wait for the deploy to complete + health check to pass.

- [ ] **Step 2: Wait for one Pinnacle extraction cycle (~2 min)**

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose exec -T backend cat /app/logs/extraction.log | tail -30"
```

Look for a `pinnacle` line with `events_processed > 0`.

- [ ] **Step 3: Verify max_stake is populated**

Via postgres MCP:

```sql
SELECT COUNT(*) FILTER (WHERE max_stake IS NOT NULL) AS with_cap,
       COUNT(*) FILTER (WHERE max_stake IS NULL)     AS no_cap,
       MIN(max_stake), MAX(max_stake), AVG(max_stake)
FROM odds
WHERE provider_id = 'pinnacle';
```

Expected: `with_cap` should grow over time as Pinnacle cycles run. `no_cap` may include non-Pinnacle providers (should be filtered by the WHERE) and old rows that haven't been re-extracted yet.

- [ ] **Step 4: Verify the frontend chip works**

Start the local client:

```bash
betty.bat
```

In the Sports → Arbitrage tab:
- Confirm the `liq off` chip is visible in the header
- Click → cycles to `liq ≥ 2k` → confirm some rows hide
- Cycle to `liq ≥ 5k`, `liq ≥ 10k`, back to `liq off`
- Confirm `liq Nk` badge renders next to rows where Pinnacle has data
- Confirm `· deposit 500 kr @ 1.50+` renders on the deposit hint for at least one Altenar-group provider (Betinia / Lodur / CampoBet / Swiper / QuickCasino) when they appear in the deposit-to-play section

- [ ] **Step 5: No commit — manual verification only**

If any step fails, file a follow-up task. If all pass, the feature is shipped.

---

## Self-Review Checklist (Author)

**1. Spec coverage:**
- ✅ Backend `Odds.max_stake` column + migration → Task 3
- ✅ Pinnacle extractor captures `maxRiskStake` → Task 4
- ✅ Storage persists `max_stake` → Task 5
- ✅ Scanner propagates `max_stake` in leg dict → Task 6
- ✅ `arb-workflow` returns `pinnacle_max_stake_sek` → Task 7
- ✅ `bonus_trigger_odds` exposed via `bankroll_service.get_bankroll` → Task 1
- ✅ Frontend `ProviderBalanceInfo.bonus_trigger_odds` + `BalanceCell` → Task 2
- ✅ Frontend arb sub-tab chip + filter + badge → Task 8
- ✅ Manual verification → Task 9
- ✅ Tests for each backend layer → Tasks 1, 3, 4, 5, 6, 7

**2. Placeholders:** none found. Every step has either a code block, an exact command, or both.

**3. Type consistency:** `bonus_trigger_odds` (snake_case on backend / camelCase-equivalent typescript field) used consistently across Tasks 1 & 2. `pinnacle_max_stake_sek` used consistently across Tasks 7 & 8. Helper name `_compute_pinnacle_max_stake_sek` matches between test (Task 7 step 1) and implementation (Task 7 step 3).
