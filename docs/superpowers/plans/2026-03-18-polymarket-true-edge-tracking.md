# Polymarket True Edge Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Polymarket CLV tracking to show true provider CLV alongside Pinnacle cross-market edge, clean up ghost/trigger bet data, and add bonus exclusion to P&L reporting.

**Architecture:** Add `provider_closing_odds` and `provider_clv_pct` columns to the `Bet` model. Extend `snapshot_closing_odds()` and `_calculate_clv()` to capture Polymarket's own closing price. One-time DB cleanup for ghost bets, trigger flags, and currency fixes. Add `exclude_bonus` query param to bets endpoints.

**Tech Stack:** Python / SQLAlchemy / FastAPI / SQLite

**Spec:** `docs/superpowers/specs/2026-03-18-polymarket-true-edge-tracking-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/db/models.py` | Modify (line ~236) | Add 2 columns to `Bet` |
| `backend/src/services/bet_service.py` | Modify (lines 302-399) | Extend `_calculate_clv()` and `snapshot_closing_odds()` |
| `backend/src/repositories/bet_repo.py` | Modify (line 34-44) | Add `exclude_bonus` param to `list_for_profile()` |
| `backend/src/api/routes/bets.py` | Modify (lines 153-314) | Return new fields, add `exclude_bonus` param |
| `backend/src/api/routes/polymarket.py` | Modify (lines 757-863) | Return new fields, add `exclude_bonus` to mybets + stats |

---

### Task 1: DB Schema — Add provider CLV columns

**Files:**
- Modify: `backend/src/db/models.py:234-236`

- [ ] **Step 1: Add columns to Bet model**

In `backend/src/db/models.py`, after line 236 (`clv_pct = Column(...)`), add:

```python
    # Provider-specific CLV (e.g., Polymarket closing price — true same-market CLV)
    provider_closing_odds = Column(Float, nullable=True)  # Same-provider odds at event start
    provider_clv_pct = Column(Float, nullable=True)       # (bet.odds / provider_closing_odds - 1) * 100
```

- [ ] **Step 2: Add migration to `_run_migrations()` in models.py**

In `backend/src/db/models.py`, find `_run_migrations()` (line ~1311) and add the following at the end of the function, following the established pattern (before the closing of the function):

```python
        # Add provider CLV columns to bets (Polymarket same-market CLV)
        for col, col_type in [("provider_closing_odds", "FLOAT"), ("provider_clv_pct", "FLOAT")]:
            try:
                cursor.execute(f"SELECT {col} FROM bets LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute(f"ALTER TABLE bets ADD COLUMN {col} {col_type}")
                    raw.commit()
                except sqlite3.OperationalError:
                    pass
```

- [ ] **Step 3: Run migration by re-initializing DB**

```bash
cd backend
python -c "
from src.db.models import get_engine
from sqlalchemy import inspect
engine = get_engine()
insp = inspect(engine)
cols = [c['name'] for c in insp.get_columns('bets')]
assert 'provider_closing_odds' in cols, 'Missing provider_closing_odds'
assert 'provider_clv_pct' in cols, 'Missing provider_clv_pct'
print('Columns verified:', [c for c in cols if 'provider' in c or 'clv' in c])
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat(db): add provider_closing_odds and provider_clv_pct columns to Bet"
```

---

### Task 2: Data Cleanup — Ghost bets, triggers, currency

**Files:**
- None (DB-only operations)

- [ ] **Step 1: Verify and delete pinnacle ghost bets**

```bash
cd backend
python -c "
from src.db.models import get_session, Bet
from sqlalchemy import text
session = get_session()
# Verify all targets are pinnacle
ghosts = session.query(Bet).filter(Bet.id.in_([6, 15, 24, 64])).all()
for b in ghosts:
    print(f'  id={b.id} provider={b.provider_id} event={b.event_id} stake={b.stake} result={b.result}')
    assert b.provider_id == 'pinnacle', f'ABORT: id={b.id} is {b.provider_id}, not pinnacle!'
# Delete
for b in ghosts:
    session.delete(b)
session.commit()
print(f'Deleted {len(ghosts)} pinnacle ghost bets')
"
```

Expected: 4 pinnacle bets listed, then "Deleted 4 pinnacle ghost bets"

- [ ] **Step 2: Flag trigger bets as bonus**

```bash
cd backend
python -c "
from src.db.models import get_session, Bet
session = get_session()
triggers = session.query(Bet).filter(Bet.id.in_([70, 65])).all()
for b in triggers:
    print(f'  id={b.id} provider={b.provider_id} stake={b.stake} is_bonus={b.is_bonus}')
    b.is_bonus = True
    b.bonus_type = 'trigger'
session.commit()
print(f'Flagged {len(triggers)} trigger bets')
"
```

Expected: "Flagged 2 trigger bets"

- [ ] **Step 3: Fix polymarket currency to USDC**

```bash
cd backend
python -c "
from src.db.models import get_session, Bet
session = get_session()
fixed = session.query(Bet).filter(
    Bet.provider_id == 'polymarket',
    Bet.currency != 'USDC',
).all()
for b in fixed:
    print(f'  id={b.id} currency={b.currency} -> USDC')
    b.currency = 'USDC'
session.commit()
print(f'Fixed {len(fixed)} polymarket bets to USDC')
"
```

- [ ] **Step 4: Verify cleanup**

```bash
cd backend
python -c "
from src.db.models import get_session, Bet
session = get_session()
# No more pinnacle bets
pin = session.query(Bet).filter(Bet.provider_id == 'pinnacle').count()
assert pin == 0, f'Still {pin} pinnacle bets!'
# Triggers flagged
t70 = session.get(Bet, 70)
t65 = session.get(Bet, 65)
assert t70.is_bonus == True and t70.bonus_type == 'trigger', f'id=70 not flagged'
assert t65.is_bonus == True and t65.bonus_type == 'trigger', f'id=65 not flagged'
# All poly bets are USDC
bad_currency = session.query(Bet).filter(Bet.provider_id == 'polymarket', Bet.currency != 'USDC').count()
assert bad_currency == 0, f'{bad_currency} poly bets with wrong currency'
print('All cleanup verified OK')
"
```

---

### Task 3: Provider CLV in `snapshot_closing_odds()`

**Files:**
- Modify: `backend/src/services/bet_service.py:342-399`

- [ ] **Step 1: Extend snapshot_closing_odds to capture Polymarket provider odds**

In `backend/src/services/bet_service.py`, modify `snapshot_closing_odds()`. The current method finds pending bets on started events and captures Pinnacle closing odds. After the existing Pinnacle snapshot logic for each bet (line 392-393), add the Polymarket provider odds capture.

Replace the entire `snapshot_closing_odds` method (lines 342-399) with:

```python
    def snapshot_closing_odds(self) -> dict:
        """
        For all pending bets on events that have already started (start_time <= now),
        snapshot the current Pinnacle odds as closing_odds and compute CLV.
        For Polymarket bets, also snapshot the Polymarket closing price as
        provider_closing_odds for true same-market CLV.

        This should be called periodically (e.g., during extraction cleanup) to
        capture CLV before the odds/events are cleaned up from the database.

        Returns: {"processed": int, "updated": int, "provider_clv_updated": int}
        """
        now = datetime.now(timezone.utc)

        # Find pending bets on started events — need either Pinnacle or provider CLV
        pending_bets = (
            self.db.query(Bet)
            .join(Event, Event.id == Bet.event_id)
            .filter(
                Bet.result == "pending",
                Bet.event_id.isnot(None),
                Event.start_time.isnot(None),
                Event.start_time <= now,
            )
            .filter(
                # Need Pinnacle CLV, or provider CLV for Polymarket bets
                (Bet.closing_odds.is_(None)) |
                ((Bet.provider_id == "polymarket") & (Bet.provider_closing_odds.is_(None)))
            )
            .all()
        )

        processed = 0
        updated = 0
        provider_clv_updated = 0

        for bet in pending_bets:
            processed += 1
            if not bet.outcome or not bet.market:
                continue

            # --- Pinnacle CLV (cross-market edge) ---
            if bet.closing_odds is None:
                query = self.db.query(Odds).filter(
                    Odds.event_id == bet.event_id,
                    Odds.provider_id.in_(SHARP_PROVIDERS),
                    Odds.market == bet.market,
                    Odds.outcome == bet.outcome,
                )
                if bet.market in ("spread", "total") and bet.point is not None:
                    query = query.filter(Odds.point == bet.point)

                pinnacle_odds = query.first()

                if pinnacle_odds and pinnacle_odds.odds > 1.0:
                    bet.closing_odds = pinnacle_odds.odds
                    bet.clv_pct = round((bet.odds / pinnacle_odds.odds - 1) * 100, 2)
                    updated += 1

            # --- Provider CLV (same-market, Polymarket only) ---
            if bet.provider_id == "polymarket" and bet.provider_closing_odds is None:
                provider_query = self.db.query(Odds).filter(
                    Odds.event_id == bet.event_id,
                    Odds.provider_id == "polymarket",
                    Odds.market == bet.market,
                    Odds.outcome == bet.outcome,
                )
                if bet.market in ("spread", "total") and bet.point is not None:
                    provider_query = provider_query.filter(Odds.point == bet.point)

                poly_odds = provider_query.first()

                if poly_odds and poly_odds.odds > 1.0:
                    bet.provider_closing_odds = poly_odds.odds
                    bet.provider_clv_pct = round((bet.odds / poly_odds.odds - 1) * 100, 2)
                    provider_clv_updated += 1

        if updated > 0 or provider_clv_updated > 0:
            logger.info(
                f"[BetService] Snapshot closing odds: {updated}/{processed} Pinnacle, "
                f"{provider_clv_updated} provider CLV updated"
            )

        return {"processed": processed, "updated": updated, "provider_clv_updated": provider_clv_updated}
```

- [ ] **Step 2: Verify server starts**

```bash
cd backend && python -c "from src.services.bet_service import BetService; print('Import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/bet_service.py
git commit -m "feat(clv): snapshot Polymarket provider closing odds alongside Pinnacle"
```

---

### Task 4: Provider CLV in `_calculate_clv()` at settlement

**Files:**
- Modify: `backend/src/services/bet_service.py:302-340`

- [ ] **Step 1: Extend _calculate_clv to also compute provider CLV**

In `_calculate_clv()`, after the existing Pinnacle CLV logic, add provider CLV for Polymarket bets. Replace the method (lines 302-340) with:

```python
    def _calculate_clv(self, bet: Bet) -> float | None:
        """
        Calculate Closing Line Value for a settled bet.

        Pinnacle CLV = (bet_odds / pinnacle_closing_odds - 1) * 100
        Provider CLV = (bet_odds / provider_closing_odds - 1) * 100  (Polymarket only)

        Positive CLV means the bet was placed at better odds than the
        closing line — the #1 indicator of sharp betting skill.
        """
        if not bet.event_id or not bet.outcome or not bet.market:
            return None

        # --- Pinnacle CLV (cross-market) ---
        pinnacle_clv = None
        if bet.closing_odds is not None:
            # snapshot_closing_odds already captured it
            pinnacle_clv = round((bet.odds / bet.closing_odds - 1) * 100, 2)
        else:
            # Look up current Pinnacle odds for same event/market/outcome
            query = self.db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id.in_(SHARP_PROVIDERS),
                Odds.market == bet.market,
                Odds.outcome == bet.outcome,
            )
            if bet.market in ("spread", "total") and bet.point is not None:
                query = query.filter(Odds.point == bet.point)

            pinnacle_odds = query.first()

            if pinnacle_odds and pinnacle_odds.odds > 1.0:
                bet.closing_odds = pinnacle_odds.odds
                pinnacle_clv = round((bet.odds / pinnacle_odds.odds - 1) * 100, 2)

        # --- Provider CLV (same-market, Polymarket only) ---
        if bet.provider_id == "polymarket" and bet.provider_closing_odds is None:
            provider_query = self.db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id == "polymarket",
                Odds.market == bet.market,
                Odds.outcome == bet.outcome,
            )
            if bet.market in ("spread", "total") and bet.point is not None:
                provider_query = provider_query.filter(Odds.point == bet.point)

            poly_odds = provider_query.first()

            if poly_odds and poly_odds.odds > 1.0:
                bet.provider_closing_odds = poly_odds.odds
                bet.provider_clv_pct = round((bet.odds / poly_odds.odds - 1) * 100, 2)
        elif bet.provider_closing_odds is not None and bet.provider_clv_pct is None:
            # Snapshot captured odds but not CLV — compute now
            bet.provider_clv_pct = round((bet.odds / bet.provider_closing_odds - 1) * 100, 2)

        return pinnacle_clv
```

- [ ] **Step 2: Verify import**

```bash
cd backend && python -c "from src.services.bet_service import BetService; print('Import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/bet_service.py
git commit -m "feat(clv): compute provider CLV at settlement for Polymarket bets"
```

---

### Task 5: API — Return new fields + exclude_bonus on /api/bets

**Files:**
- Modify: `backend/src/repositories/bet_repo.py:34-44`
- Modify: `backend/src/api/routes/bets.py:153-314`

- [ ] **Step 1: Add exclude_bonus to BetRepo.list_for_profile()**

In `backend/src/repositories/bet_repo.py`, modify `list_for_profile` to accept and apply `exclude_bonus`:

```python
    def list_for_profile(
        self,
        profile_id: int,
        status: str | None = None,
        exclude_bonus: bool = False,
        limit: int = 50,
    ) -> list[Bet]:
        """List bets for a profile with optional status filter."""
        query = self.db.query(Bet).filter(Bet.profile_id == profile_id)
        if status:
            query = query.filter(Bet.result == status)
        if exclude_bonus:
            query = query.filter(Bet.is_bonus != True)
        return query.order_by(Bet.placed_at.desc()).limit(limit).all()
```

- [ ] **Step 2: Add exclude_bonus param and new response fields to list_bets**

In `backend/src/api/routes/bets.py`, modify the `list_bets` function signature (line 154) to add the param:

```python
@router.get("")
async def list_bets(
    status: Optional[str] = None,
    exclude_bonus: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
):
```

Update the `bet_repo.list_for_profile` call (line 164) to pass `exclude_bonus`:

```python
    bets = bet_repo.list_for_profile(profile.id, status=status, exclude_bonus=exclude_bonus, limit=limit)
```

In the response dict construction (around line 264-308), add the two new fields after `"closing_odds"` (line 283):

```python
            "closing_odds": b.closing_odds,
            "provider_closing_odds": b.provider_closing_odds,
            "provider_clv_pct": b.provider_clv_pct,
```

- [ ] **Step 3: Verify server starts**

```bash
cd backend && python -c "from src.api.routes.bets import router; print('Import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/repositories/bet_repo.py backend/src/api/routes/bets.py
git commit -m "feat(api): add provider CLV fields and exclude_bonus param to /api/bets"
```

---

### Task 6: API — Return new fields + exclude_bonus on /api/polymarket/mybets

**Files:**
- Modify: `backend/src/api/routes/polymarket.py:757-863`

- [ ] **Step 1: Add exclude_bonus param to mybets endpoint**

In `backend/src/api/routes/polymarket.py`, modify the function signature (line 758):

```python
@router.get("/mybets")
async def get_mybets(
    status: Optional[str] = None,
    exclude_bonus: bool = False,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
```

Add the bonus filter after the status filter (after line 773):

```python
    if exclude_bonus:
        query = query.filter(Bet.is_bonus != True)
```

- [ ] **Step 2: Add provider CLV fields to mybets response items**

In the `bet_items.append(...)` dict (line 796-819), add after `"fair_odds"`:

```python
            "clv_pct": b.clv_pct,
            "closing_odds": b.closing_odds,
            "provider_closing_odds": b.provider_closing_odds,
            "provider_clv_pct": b.provider_clv_pct,
```

- [ ] **Step 3: Add exclude_bonus to aggregate stats query**

The aggregate stats query (line 822-825) also needs the bonus filter. Replace:

```python
    # Aggregate stats
    all_bets = db.query(Bet).filter(
        Bet.profile_id == profile.id,
        Bet.provider_id == "polymarket",
    ).all()
```

With:

```python
    # Aggregate stats (same bonus filter as the list)
    stats_query = db.query(Bet).filter(
        Bet.profile_id == profile.id,
        Bet.provider_id == "polymarket",
    )
    if exclude_bonus:
        stats_query = stats_query.filter(Bet.is_bonus != True)
    all_bets = stats_query.all()
```

- [ ] **Step 4: Add avg provider CLV to stats**

After the `avg_edge` calculation (line 844), add average provider CLV:

```python
    # Average provider CLV (same-market, Polymarket closing price)
    provider_clvs = [b.provider_clv_pct for b in all_bets if b.provider_clv_pct is not None]
    avg_provider_clv = round(sum(provider_clvs) / len(provider_clvs), 2) if provider_clvs else None

    # Average Pinnacle CLV (cross-market edge)
    pinnacle_clvs = [b.clv_pct for b in all_bets if b.clv_pct is not None]
    avg_pinnacle_clv = round(sum(pinnacle_clvs) / len(pinnacle_clvs), 2) if pinnacle_clvs else None
```

In the stats dict (line 849-862), add after `"avg_edge"`:

```python
            "avg_provider_clv": avg_provider_clv,
            "avg_pinnacle_clv": avg_pinnacle_clv,
```

- [ ] **Step 5: Verify import**

```bash
cd backend && python -c "from src.api.routes.polymarket import router; print('Import OK')"
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes/polymarket.py
git commit -m "feat(api): add provider CLV and exclude_bonus to /api/polymarket/mybets"
```

---

### Task 7: Smoke test — End to end verification

- [ ] **Step 1: Start backend and verify endpoints**

```bash
cd backend && python -m src.app serve &
sleep 3
# Test /api/bets with exclude_bonus
curl -s "http://localhost:8000/api/bets?exclude_bonus=true&limit=3" | python -m json.tool | head -30
# Test /api/polymarket/mybets
curl -s "http://localhost:8000/api/polymarket/mybets?limit=3" | python -m json.tool | head -30
```

Verify:
- Response includes `provider_closing_odds` and `provider_clv_pct` fields (null for existing bets is expected)
- `exclude_bonus=true` filters out bonus bets
- Stats include `avg_provider_clv` and `avg_pinnacle_clv`

- [ ] **Step 2: Verify DB cleanup persisted**

```bash
cd backend
python -c "
from src.db.models import get_session, Bet
s = get_session()
pin = s.query(Bet).filter(Bet.provider_id == 'pinnacle').count()
triggers = s.query(Bet).filter(Bet.bonus_type == 'trigger').count()
bad_cur = s.query(Bet).filter(Bet.provider_id == 'polymarket', Bet.currency != 'USDC').count()
print(f'Pinnacle ghosts: {pin} (expect 0)')
print(f'Trigger bets flagged: {triggers} (expect 2)')
print(f'Bad poly currency: {bad_cur} (expect 0)')
"
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: polymarket true edge tracking — dual CLV, data cleanup, bonus exclusion"
```
