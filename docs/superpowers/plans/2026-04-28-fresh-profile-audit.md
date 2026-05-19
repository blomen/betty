# Fresh-Profile Audit + Bonus Deposit Hint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-shot system audit that proves a fresh betting profile sees every provider, every bonus, and a deposit-trigger hint inline on the arb page — and produces a deposit recommendation for the unlimited providers sized to fund the live value-bet feed.

**Architecture:** Three connected pieces. (1) Backend: a new `_seed_provider_bonuses` helper inserts `ProfileProviderBonus` rows for every yaml-configured bonus when a profile is created, plus `BankrollService.get_bankroll` exposes a per-provider `bonus_trigger_amount`. (2) Frontend: `PlayPage` renders a faint "deposit Xkr" label inline next to the balance cell when the trigger is set. (3) A standalone `scripts/audit_fresh_profile.py` creates the Audit profile, exercises the new endpoints, runs a `StakeCalculator` simulation against the live `/api/play/batch` feed at multiple bankroll levels, and writes a markdown report to `docs/audits/`.

**Tech Stack:** Python 3.10 + FastAPI + SQLAlchemy + pytest (backend); React 19 + TypeScript + Vite + Tailwind (frontend); local SQLite for tests, Postgres in prod.

**Spec:** [docs/superpowers/specs/2026-04-28-fresh-profile-audit-design.md](../specs/2026-04-28-fresh-profile-audit-design.md)

---

## File Structure

| File | Role | New / Modified |
|---|---|---|
| `backend/src/services/bonus_seed_service.py` | The `seed_provider_bonuses` helper — single responsibility, easy to test in isolation | **Create** |
| `backend/src/api/routes/profiles.py` | Wire seeding into `create_profile`, expose `POST /api/profiles/{id}/seed-bonuses` | Modify |
| `backend/src/services/bankroll_service.py` | Augment `get_bankroll` with `bonus_trigger_amount` / `bonus_currency` | Modify |
| `backend/tests/test_bonus_seed_service.py` | Unit tests for the seed helper | **Create** |
| `backend/tests/test_bankroll_service_trigger.py` | Unit tests for the new trigger fields on `get_bankroll` | **Create** |
| `backend/tests/test_profiles_route_seeding.py` | Integration test that `POST /api/profiles` triggers seeding | **Create** |
| `arnold/frontend/src/pages/PlayPage.tsx` | Upgrade `providerBalances` shape, render the deposit-trigger label | Modify |
| `scripts/audit_fresh_profile.py` | Audit runner: profile setup → endpoint sanity → stake simulation → report | **Create** |
| `docs/audits/2026-04-28-fresh-profile-audit.md` | Generated audit report | **Create** (script writes it) |

The seed helper is pulled into its own service file rather than buried inside `profiles.py` because (a) it's reused by the route and the script, (b) it has its own test surface, and (c) `profiles.py` already has 370 lines and shouldn't grow. `BankrollService` modification stays in place — it's the right home for balance-shaped data.

---

## Task 1: Bonus seed helper

**Files:**
- Create: `backend/src/services/bonus_seed_service.py`
- Test: `backend/tests/test_bonus_seed_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_bonus_seed_service.py
"""Tests for the ProfileProviderBonus seeding helper."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Profile, ProfileProviderBonus, Provider
from src.services.bonus_seed_service import seed_provider_bonuses


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    profile = Profile(id=1, name="Audit", is_active=False)
    session.add(profile)
    # Both providers exist in DB; only "unibet" + "leovegas" have bonus blocks in yaml.
    session.add_all([
        Provider(id="unibet", name="Unibet", is_enabled=True),
        Provider(id="leovegas", name="LeoVegas", is_enabled=True),
        Provider(id="pinnacle", name="Pinnacle", is_enabled=True),  # no bonus
    ])
    session.commit()
    yield session
    session.close()


def _yaml_bonuses():
    """Stub of providers.yaml bonus configs for tests."""
    return {
        "unibet":   {"type": "freebet", "amount": 1000, "trigger_mode": "single"},
        "leovegas": {"type": "bonusdeposit", "amount": 600, "trigger_multiplier": 6,
                     "trigger_odds": 1.80, "trigger_mode": "cumulative"},
    }


def test_seed_creates_one_row_per_yaml_bonus(db, monkeypatch):
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        _yaml_bonuses,
    )
    inserted = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    rows = db.query(ProfileProviderBonus).filter_by(profile_id=1).all()
    assert {r.provider_id for r in rows} == {"unibet", "leovegas"}
    assert all(r.bonus_status == "available" for r in rows)
    assert inserted == 2


def test_seed_skips_yaml_orphans(db, monkeypatch):
    """A yaml bonus for a provider not in the providers table is skipped, not raised."""
    yaml_with_orphan = dict(_yaml_bonuses(), ghost={"type": "freebet", "amount": 500})
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        lambda: yaml_with_orphan,
    )
    inserted = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    rows = db.query(ProfileProviderBonus).filter_by(profile_id=1).all()
    assert {r.provider_id for r in rows} == {"unibet", "leovegas"}
    assert inserted == 2  # ghost not counted


def test_seed_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        _yaml_bonuses,
    )
    seed_provider_bonuses(profile_id=1, db=db)
    db.commit()
    inserted_second = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    rows = db.query(ProfileProviderBonus).filter_by(profile_id=1).all()
    assert len(rows) == 2
    assert inserted_second == 0


def test_seed_respects_existing_in_progress_bonus(db, monkeypatch):
    """Pre-existing non-available row for one provider is left untouched."""
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        _yaml_bonuses,
    )
    db.add(ProfileProviderBonus(
        profile_id=1, provider_id="unibet",
        bonus_status="in_progress", bonus_type="freebet",
    ))
    db.commit()

    inserted = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    unibet_row = db.query(ProfileProviderBonus).filter_by(
        profile_id=1, provider_id="unibet").one()
    assert unibet_row.bonus_status == "in_progress"  # untouched
    assert inserted == 1  # only leovegas was new
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_bonus_seed_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.bonus_seed_service'`

- [ ] **Step 3: Write the implementation**

```python
# backend/src/services/bonus_seed_service.py
"""Seed ProfileProviderBonus rows from providers.yaml bonus configs."""

import logging

from sqlalchemy.orm import Session

from ..api.routes.providers import load_provider_bonuses
from ..db.models import ProfileProviderBonus, Provider

logger = logging.getLogger(__name__)


def seed_provider_bonuses(profile_id: int, db: Session) -> int:
    """Insert ProfileProviderBonus rows for every yaml-configured bonus.

    Idempotent: skips providers that already have a row for this profile,
    so existing in-progress bonuses are never disturbed. Yaml orphans
    (providers in yaml but not in DB) are logged and skipped.

    Returns the count of rows inserted.
    """
    yaml_bonuses = load_provider_bonuses()
    if not yaml_bonuses:
        return 0

    valid_provider_ids = {
        p.id for p in db.query(Provider).filter(
            Provider.id.in_(yaml_bonuses.keys())
        ).all()
    }
    yaml_orphans = set(yaml_bonuses.keys()) - valid_provider_ids
    if yaml_orphans:
        logger.warning("Skipping yaml-orphan bonuses (provider not in DB): %s", sorted(yaml_orphans))

    existing = {
        r.provider_id
        for r in db.query(ProfileProviderBonus.provider_id)
        .filter(ProfileProviderBonus.profile_id == profile_id)
        .all()
    }

    inserted = 0
    for provider_id in valid_provider_ids:
        if provider_id in existing:
            continue
        db.add(ProfileProviderBonus(
            profile_id=profile_id,
            provider_id=provider_id,
            bonus_status="available",
        ))
        inserted += 1

    return inserted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_bonus_seed_service.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/bonus_seed_service.py backend/tests/test_bonus_seed_service.py
git commit -m "feat(bonus): idempotent seed helper for ProfileProviderBonus rows"
```

---

## Task 2: Wire seeding into `create_profile` and expose endpoint

**Files:**
- Modify: `backend/src/api/routes/profiles.py:115-141` (create_profile) + add new endpoint after line 141
- Test: `backend/tests/test_profiles_route_seeding.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_profiles_route_seeding.py
"""Integration tests: profile creation seeds bonus rows; manual seed endpoint."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api import create_app
from src.api.deps import get_db
from src.db.models import Base, ProfileProviderBonus, Provider


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    session.add_all([
        Provider(id="unibet", name="Unibet", is_enabled=True),
        Provider(id="leovegas", name="LeoVegas", is_enabled=True),
    ])
    session.commit()

    def _override_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        lambda: {
            "unibet":   {"type": "freebet", "amount": 1000},
            "leovegas": {"type": "bonusdeposit", "amount": 600},
        },
    )

    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c, SessionLocal
    session.close()


def test_create_profile_seeds_bonus_rows(client):
    c, SessionLocal = client
    resp = c.post("/api/profiles", json={"name": "Audit"})
    assert resp.status_code == 200
    pid = resp.json()["profile"]["id"]

    s = SessionLocal()
    try:
        rows = s.query(ProfileProviderBonus).filter_by(profile_id=pid).all()
        assert {r.provider_id for r in rows} == {"unibet", "leovegas"}
        assert all(r.bonus_status == "available" for r in rows)
    finally:
        s.close()


def test_seed_endpoint_is_idempotent(client):
    c, SessionLocal = client
    pid = c.post("/api/profiles", json={"name": "Audit"}).json()["profile"]["id"]
    resp = c.post(f"/api/profiles/{pid}/seed-bonuses")
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 0  # already seeded by create

    s = SessionLocal()
    try:
        assert s.query(ProfileProviderBonus).filter_by(profile_id=pid).count() == 2
    finally:
        s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_profiles_route_seeding.py -v`
Expected: FAIL — `assert {'unibet', 'leovegas'} == set()` (no rows seeded) and 404 on `/seed-bonuses`.

- [ ] **Step 3: Modify `profiles.py`**

In `backend/src/api/routes/profiles.py`, **at the top** add the import:

```python
from ...services.bonus_seed_service import seed_provider_bonuses
```

In the `create_profile` function (currently `backend/src/api/routes/profiles.py:115-141`), replace the body so seeding runs after commit:

```python
@router.post("")
def create_profile(data: ProfileCreate, db: Session = Depends(get_db)):
    """Create a new profile with fresh state (0 balance) + seeded bonus rows."""
    profile_repo = ProfileRepo(db)
    existing = db.query(Profile).filter(Profile.name == data.name).first()
    if existing:
        raise HTTPException(400, f"Profile '{data.name}' already exists")

    color = data.color or _next_profile_color(db)

    profile = Profile(
        name=data.name,
        bankroll=0.0,
        currency="SEK",
        kelly_fraction=data.kelly_fraction or 0.25,
        max_stake_pct=data.max_stake_pct or 5.0,
        min_edge_pct=data.min_edge_pct or 2.0,
        is_active=False,
        color=color,
    )
    db.add(profile)
    db.commit()

    seed_provider_bonuses(profile.id, db)
    db.commit()

    return {
        "success": True,
        "profile": profile_to_dict(profile, profile_repo),
    }
```

Then **after** `delete_profile` (around line 233), add the manual-seed endpoint:

```python
@router.post("/{profile_id}/seed-bonuses")
def seed_bonuses(profile_id: int, db: Session = Depends(get_db)):
    """Idempotently seed ProfileProviderBonus rows from providers.yaml."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")
    inserted = seed_provider_bonuses(profile_id, db)
    db.commit()
    return {"success": True, "profile_id": profile_id, "inserted": inserted}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_profiles_route_seeding.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/profiles.py backend/tests/test_profiles_route_seeding.py
git commit -m "feat(profiles): auto-seed bonus rows on create + manual /seed-bonuses endpoint"
```

---

## Task 3: Expose `bonus_trigger_amount` from `/api/bankroll`

**Files:**
- Modify: `backend/src/services/bankroll_service.py:29-57` (`get_bankroll`)
- Test: `backend/tests/test_bankroll_service_trigger.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_bankroll_service_trigger.py
"""Tests for the new bonus_trigger_amount field on /api/bankroll."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Profile, ProfileProviderBalance, ProfileProviderBonus, Provider
from src.services.bankroll_service import BankrollService


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Profile(id=1, name="Audit", is_active=True))
    session.add_all([
        Provider(id="unibet", name="Unibet", is_enabled=True),
        Provider(id="leovegas", name="LeoVegas", is_enabled=True),
        Provider(id="pinnacle", name="Pinnacle", is_enabled=True),
    ])
    session.add_all([
        ProfileProviderBonus(profile_id=1, provider_id="unibet", bonus_status="available"),
        ProfileProviderBonus(profile_id=1, provider_id="leovegas", bonus_status="available"),
    ])
    session.commit()
    monkeypatch.setattr(
        "src.services.bankroll_service.load_provider_bonuses",
        lambda: {
            "unibet":   {"type": "freebet", "amount": 1000},
            "leovegas": {"type": "bonusdeposit", "amount": 600},
        },
    )
    yield session
    session.close()


def test_trigger_populated_when_balance_zero_and_available(db):
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["unibet"]["bonus_trigger_amount"] == 1000
    assert by_id["unibet"]["bonus_currency"] == "SEK"
    assert by_id["leovegas"]["bonus_trigger_amount"] == 600


def test_trigger_null_when_no_bonus_in_yaml(db):
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["pinnacle"]["bonus_trigger_amount"] is None


def test_trigger_null_when_balance_already_covers_amount(db):
    db.add(ProfileProviderBalance(profile_id=1, provider_id="leovegas", balance=600))
    db.commit()
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["leovegas"]["bonus_trigger_amount"] is None


def test_trigger_null_when_bonus_not_available(db):
    bonus = db.query(ProfileProviderBonus).filter_by(provider_id="unibet").one()
    bonus.bonus_status = "in_progress"
    db.commit()
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["unibet"]["bonus_trigger_amount"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_bankroll_service_trigger.py -v`
Expected: FAIL — `KeyError: 'bonus_trigger_amount'`.

- [ ] **Step 3: Modify `bankroll_service.py`**

In `backend/src/services/bankroll_service.py`, **at the top of the imports block**, add:

```python
from ..api.routes.providers import load_provider_bonuses
```

Replace the `get_bankroll` method (currently lines 29–57) with this version:

```python
    def get_bankroll(self) -> dict:
        """Get provider balances and total bankroll for active profile."""
        profile = self.profile_repo.get_active()
        providers = self.db.query(Provider).filter(Provider.is_enabled).all()

        yaml_bonuses = load_provider_bonuses()
        bonus_records = {
            b.provider_id: b
            for b in self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile.id)
            .all()
        }

        provider_data = []
        total_sek = 0.0
        for p in providers:
            balance = self.profile_repo.get_balance(profile.id, p.id)
            currency = get_provider_currency(p.id)
            rate = get_exchange_rate(p.id)
            total_sek += balance * rate

            cfg = yaml_bonuses.get(p.id) or {}
            amount = float(cfg.get("amount") or 0)
            record = bonus_records.get(p.id)
            is_available = record is not None and record.bonus_status == "available"
            trigger_actionable = (
                is_available and amount > 0 and balance < amount
            )

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

        return {
            "total": total_sek,
            "profile_id": profile.id,
            "profile_name": profile.name,
            "providers": provider_data,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_bankroll_service_trigger.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/bankroll_service.py backend/tests/test_bankroll_service_trigger.py
git commit -m "feat(bankroll): expose bonus_trigger_amount per provider in get_bankroll"
```

---

## Task 4: Frontend — upgrade `providerBalances` shape

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx:43-100` (state + interfaces)

The existing `providerBalances: Record<string, number>` is read in many places via `[pid] ?? 0` and `(d.balance ?? 0)`. Upgrade in two steps so we don't break behavior: first widen the type to `number | ProviderBalanceInfo`, then introduce a `getBalance(pid)` accessor used everywhere. (Single state migration. No new render yet — that comes in Task 5.)

- [ ] **Step 1: Add the new type and accessor near the top of `PlayPage.tsx`** (above `interface BatchBet`, around line 42)

```tsx
type ProviderBalanceInfo = {
  balance: number
  bonus_trigger?: number
  bonus_currency?: string
}
type ProviderBalanceLike = number | ProviderBalanceInfo
const getBalance = (b: ProviderBalanceLike | undefined): number =>
  typeof b === 'number' ? b : (b?.balance ?? 0)
const getTrigger = (b: ProviderBalanceLike | undefined): { amount: number; currency: string } | null => {
  if (b == null || typeof b === 'number') return null
  return b.bonus_trigger != null && b.bonus_trigger > 0
    ? { amount: b.bonus_trigger, currency: b.bonus_currency ?? 'SEK' }
    : null
}
```

- [ ] **Step 2: Widen the state type at line 95**

Change:

```tsx
const [providerBalances, setProviderBalances] = useState<Record<string, number>>({})
```

to:

```tsx
const [providerBalances, setProviderBalances] = useState<Record<string, ProviderBalanceLike>>({})
```

- [ ] **Step 3: Replace every numeric read of `providerBalances`**

Find all references using ripgrep:

```bash
cd arnold/frontend && grep -n "providerBalances\[" src/pages/PlayPage.tsx
```

For each occurrence of `providerBalances[pid] ?? 0`, replace with `getBalance(providerBalances[pid])`. For mirror-stream payloads where `d.balance` is read (e.g. line 265, line 375), no change needed — those payloads keep being numbers; only the top-level state gets the wider shape.

- [ ] **Step 4: Update the bankroll fetch path to map `bonus_trigger_amount` → state**

Locate the existing `/api/bankroll` fetch (search the file: `cd arnold/frontend && grep -n "api/bankroll\|getBankroll" src/pages/PlayPage.tsx`). In the response handler, map:

```tsx
const balanceMap: Record<string, ProviderBalanceLike> = {}
for (const p of result.providers ?? []) {
  balanceMap[p.id] = {
    balance: p.balance ?? 0,
    bonus_trigger: p.bonus_trigger_amount ?? undefined,
    bonus_currency: p.bonus_currency ?? undefined,
  }
}
setProviderBalances(balanceMap)
```

(If the existing fetch already populates `providerBalances` with raw numbers, replace that line with the loop above. If `useMirrorStream` separately writes balances as numbers, that path keeps working via `getBalance`.)

- [ ] **Step 5: TypeScript compile check**

```bash
cd arnold/frontend && npx tsc --noEmit
```

Expected: no errors. If `Record<string, number>` is read elsewhere as a strict number, fix the read site to use `getBalance()`.

- [ ] **Step 6: Commit**

```bash
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "refactor(playpage): widen providerBalances to carry bonus trigger info"
```

---

## Task 5: Frontend — render the "deposit Xkr" hint

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx` (the balance-cell render path inside the arb section)

The arb section's per-provider header currently shows the balance as plain text. We add a faint orange suffix when `getTrigger` returns non-null. Reused in the value section.

- [ ] **Step 1: Locate the balance-cell renderer**

```bash
cd arnold/frontend && grep -n "providerBalances\[\|getBalance\|deposit to play" src/pages/PlayPage.tsx
```

The existing arb-section card uses something like `{getBalance(providerBalances[pid]).toFixed(0)} SEK` near the cluster card header (around lines 980–1000 per spec exploration).

- [ ] **Step 2: Add a small renderer near the top of the file** (alongside `getBalance`/`getTrigger`)

```tsx
function BalanceCell({ pid, balances }: { pid: string; balances: Record<string, ProviderBalanceLike> }) {
  const balance = getBalance(balances[pid])
  const trigger = getTrigger(balances[pid])
  return (
    <span>
      <span>{balance.toFixed(0)} SEK</span>
      {trigger && balance < 1 && (
        <span className="ml-2 text-xs text-orange-400/80" title="Deposit to unlock provider bonus">
          · deposit {trigger.amount.toFixed(0)} {trigger.currency.toLowerCase()}
        </span>
      )}
    </span>
  )
}
```

The `balance < 1` guard means the hint disappears the moment the user actually deposits (no flicker on partial balances).

- [ ] **Step 3: Replace existing balance text in arb + value sections**

Search for every occurrence of the inline balance render:

```bash
cd arnold/frontend && grep -n "providerBalances\[" src/pages/PlayPage.tsx
```

Wherever a per-provider header renders balance, replace the literal `{getBalance(providerBalances[pid]).toFixed(0)} SEK` with `<BalanceCell pid={pid} balances={providerBalances} />`. (Don't replace places where `balance` is used as a number for arithmetic — only the visual cells.)

- [ ] **Step 4: Visual sanity check**

```bash
cd arnold/frontend && npx tsc --noEmit
cd arnold/frontend && npx eslint src/pages/PlayPage.tsx
```

Expected: zero errors.

- [ ] **Step 5: Commit**

```bash
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "feat(playpage): show 'deposit Xkr' hint next to balance for unclaimed bonuses"
```

---

## Task 6: Audit script skeleton — profile setup + endpoint smoke checks

**Files:**
- Create: `scripts/audit_fresh_profile.py`

The script connects to the local arnold FastAPI on `http://localhost:8000` (the launcher's port). It is one-shot and idempotent — re-runs are safe.

- [ ] **Step 1: Write the script skeleton**

```python
# scripts/audit_fresh_profile.py
"""One-shot audit: create the Audit profile, verify provider/bonus coverage,
compute deposit recommendation, write report to docs/audits/.

Usage:
    python scripts/audit_fresh_profile.py [--api http://localhost:8000]

Pre-req: arnold.bat is running so /api/* routes through the SSH tunnel to
the production server.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import httpx
import yaml


def find_or_create_audit_profile(api: httpx.Client) -> int:
    """Get the Audit profile id (create one if it doesn't exist)."""
    profiles = api.get("/api/profiles").raise_for_status().json()["profiles"]
    for p in profiles:
        if p["name"] == "Audit":
            return p["id"]
    created = api.post("/api/profiles", json={"name": "Audit"}).raise_for_status().json()
    return created["profile"]["id"]


def setup_audit_profile(api: httpx.Client, profile_id: int) -> None:
    api.post(f"/api/profiles/{profile_id}/seed-bonuses").raise_for_status()
    api.post(f"/api/profiles/{profile_id}/activate").raise_for_status()


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--repo-root", default=str(Path(__file__).parent.parent))
    parser.add_argument("--out", default=None,
                        help="Output report path (defaults to docs/audits/YYYY-MM-DD-fresh-profile-audit.md)")
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    out_path = Path(args.out) if args.out else (
        repo_root / "docs" / "audits" /
        f"{dt.date.today().isoformat()}-fresh-profile-audit.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.api, timeout=30.0) as api:
        profile_id = find_or_create_audit_profile(api)
        print(f"[audit] using Audit profile id={profile_id}")
        setup_audit_profile(api, profile_id)

        bankroll = api.get("/api/bankroll").raise_for_status().json()
        bonuses_yaml = api.get("/api/bankroll/bonuses").raise_for_status().json()

    yaml_path = repo_root / "backend" / "src" / "config" / "providers.yaml"
    yaml_doc = load_yaml(yaml_path)

    print(f"[audit] {len(bankroll['providers'])} providers in /api/bankroll")
    print(f"[audit] {len(bonuses_yaml)} yaml bonus blocks")
    print(f"[audit] report → {out_path}")

    # Subsequent tasks fill in real report sections.
    out_path.write_text(
        f"# Fresh-Profile Audit — {dt.date.today().isoformat()}\n\n"
        f"Profile id: {profile_id}\n"
        f"Providers in bankroll: {len(bankroll['providers'])}\n"
        f"Yaml bonus blocks: {len(bonuses_yaml)}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify script imports & argparse work**

```bash
python scripts/audit_fresh_profile.py --help
```

Expected: argparse usage block printed, exit code 0. (No API call yet.)

- [ ] **Step 3: Commit**

```bash
git add scripts/audit_fresh_profile.py
git commit -m "feat(audit): scaffold fresh-profile audit script (profile setup + skeleton report)"
```

---

## Task 7: Audit report — coverage sections

**Files:**
- Modify: `scripts/audit_fresh_profile.py` (replace the placeholder `out_path.write_text` block)

Three coverage sections: provider coverage, bonus coverage, arb-page sanity. Each emits markdown with `[!]` for hard failures and `[~]` for warnings.

- [ ] **Step 1: Add the report-building helpers**

Insert these constants near the top of `scripts/audit_fresh_profile.py`, after the imports:

```python
# Mirror of arnold/frontend/src/pages/PlayPage.tsx:6 — keep in sync.
UNLIMITED_PROVIDERS = {"pinnacle", "polymarket", "cloudbet", "kalshi"}

# Mirror of arnold/frontend/src/pages/PlayPage.tsx:24 — keep in sync.
SOFT_CLUSTER_MEMBERS = {
    "kambi": ["unibet", "leovegas", "expekt", "betmgm", "speedybet", "x3000", "goldenbull", "1x2"],
    "spectate": ["888sport", "mrgreen"],
    "altenar_main": ["betinia", "campobet", "lodur", "quickcasino", "swiper", "dbet"],
    "gecko_betsson": ["betsson", "nordicbet", "betsafe", "spelklubben"],
    "comeon_group": ["comeon", "lyllo", "hajper", "snabbare"],
}
SOFT_STANDALONES = {"vbet", "10bet", "tipwin", "coolbet", "bethard"}

# Signal-only providers expected to NOT appear on the arb page (no bet placement).
SIGNAL_ONLY_PROVIDERS = {"stake", "marathon", "consensus"}
```

- [ ] **Step 2: Add the section builders**

Insert before `def main()`:

```python
def build_provider_coverage(yaml_doc: dict, bankroll: dict) -> tuple[list[str], int]:
    """Verify every active yaml provider appears in /api/bankroll with balance=0."""
    yaml_active = set(yaml_doc.get("active_providers", []))
    bankroll_ids = {p["id"] for p in bankroll["providers"]}
    bankroll_by_id = {p["id"]: p for p in bankroll["providers"]}

    lines = ["## Provider coverage", ""]
    flags = 0

    missing = sorted(yaml_active - bankroll_ids)
    for pid in missing:
        lines.append(f"- [!] missing-from-bankroll: `{pid}` is active in yaml but absent from `/api/bankroll`")
        flags += 1

    nonzero = [p for p in bankroll["providers"]
               if p["id"] in yaml_active and (p["balance"] or 0) > 0]
    for p in nonzero:
        lines.append(f"- [!] non-zero-balance: `{p['id']}` has balance={p['balance']} on the Audit profile")
        flags += 1

    if flags == 0:
        lines.append(f"- [ok] all {len(yaml_active)} active providers present with balance=0")
    lines.append("")
    return lines, flags


def build_bonus_coverage(yaml_bonuses: dict, bankroll: dict) -> tuple[list[str], int]:
    """Verify every yaml bonus block surfaces a non-null bonus_trigger_amount."""
    by_id = {p["id"]: p for p in bankroll["providers"]}
    lines = ["## Bonus coverage", ""]
    flags = 0

    for pid, cfg in sorted(yaml_bonuses.items()):
        amount = cfg.get("amount", 0) or 0
        if amount <= 0:
            lines.append(f"- [~] zero-amount: `{pid}` yaml bonus has `amount={amount}`, no trigger surfaced")
            continue
        provider_row = by_id.get(pid)
        if provider_row is None:
            lines.append(f"- [!] yaml-orphan: `{pid}` has yaml bonus but provider not in `/api/bankroll`")
            flags += 1
            continue
        trigger = provider_row.get("bonus_trigger_amount")
        if trigger is None:
            lines.append(
                f"- [!] bonus-not-actionable: `{pid}` yaml has amount={amount} but `/api/bankroll` returned `bonus_trigger_amount=null`"
            )
            flags += 1
        else:
            lines.append(f"- [ok] `{pid}`: deposit {int(trigger)} {provider_row.get('bonus_currency', 'SEK')} ({cfg.get('type')})")

    lines.append("")
    return lines, flags


def build_arb_page_sanity(yaml_doc: dict) -> tuple[list[str], int]:
    """Verify every active yaml provider is reachable through PlayPage's cluster map."""
    yaml_active = set(yaml_doc.get("active_providers", []))
    reachable = set(UNLIMITED_PROVIDERS) | set(SOFT_STANDALONES)
    for members in SOFT_CLUSTER_MEMBERS.values():
        reachable.update(members)

    lines = ["## Arb-page sanity", ""]
    flags = 0

    orphans = sorted(yaml_active - reachable - SIGNAL_ONLY_PROVIDERS)
    for pid in orphans:
        lines.append(f"- [~] not-on-arb-page: `{pid}` is active but missing from PlayPage cluster map (add to SOFT_STANDALONES or a cluster)")

    expected_signal = sorted(yaml_active & SIGNAL_ONLY_PROVIDERS)
    for pid in expected_signal:
        lines.append(f"- [ok] `{pid}` correctly excluded from arb page (signal-only)")

    if not orphans:
        lines.append(f"- [ok] all {len(yaml_active)} providers reachable through cluster map (or signal-only)")
    lines.append("")
    return lines, flags
```

- [ ] **Step 3: Replace the placeholder write block in `main()`**

Replace the `out_path.write_text(...)` block with:

```python
    sections: list[str] = [
        f"# Fresh-Profile Audit — {dt.date.today().isoformat()}",
        "",
        f"**Profile id:** {profile_id} (`Audit`)",
        f"**API:** {args.api}",
        f"**Providers in `/api/bankroll`:** {len(bankroll['providers'])}",
        f"**Yaml bonus blocks:** {len(bonuses_yaml)}",
        "",
    ]
    total_critical = 0

    for builder in (
        lambda: build_provider_coverage(yaml_doc, bankroll),
        lambda: build_bonus_coverage(bonuses_yaml, bankroll),
        lambda: build_arb_page_sanity(yaml_doc),
    ):
        section_lines, flags = builder()
        sections.extend(section_lines)
        total_critical += flags

    sections.append("## Deposit recommendation")
    sections.append("")
    sections.append("_Filled in by Task 8._")
    sections.append("")

    sections.append(f"## Verdict: {'PASS' if total_critical == 0 else f'FAIL ({total_critical} critical flags)'}")
    sections.append("")

    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"[audit] {total_critical} critical flag(s); report written")
    return 0 if total_critical == 0 else 1
```

- [ ] **Step 4: Manual verification**

Start arnold (so the local API is up), then:

```bash
python scripts/audit_fresh_profile.py --out /tmp/audit-test.md
cat /tmp/audit-test.md | head -40
```

Expected: report with three sections, an `Audit` profile created in DB, exit code 0 (or 1 if real flags present).

- [ ] **Step 5: Commit**

```bash
git add scripts/audit_fresh_profile.py
git commit -m "feat(audit): provider/bonus/arb-page coverage report sections"
```

---

## Task 8: Audit report — deposit recommendation

**Files:**
- Modify: `scripts/audit_fresh_profile.py` (replace the "Filled in by Task 8" placeholder)

Algorithm: the script temporarily lifts the Audit profile balance to 1,000,000 SEK on Pinnacle so `/api/play/batch` returns the full feed without bankroll-related skipping. It captures the bet list, then runs the production `StakeCalculator` locally over a sweep of bankroll values to find the smallest `B*` where every bet returns no `skip_reason`. Then it builds the target-bankroll table.

- [ ] **Step 1: Add the StakeCalculator import + recommendation helpers**

In `scripts/audit_fresh_profile.py`, near the existing imports add:

```python
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))
from bankroll.stake_calculator import calculate_stake, dynamic_min_stake  # noqa: E402
```

Then before `def main()`:

```python
TARGET_BANKROLLS = [10_000, 25_000, 50_000, 100_000]
UNLIMITED_FOR_DEPOSIT = ("pinnacle", "polymarket", "cloudbet", "kalshi")
SIM_PROBE_PROVIDER = "pinnacle"  # arbitrary unlimited provider used only to pre-fund


def fetch_full_batch(api: httpx.Client, profile_id: int) -> list[dict]:
    """Fetch the full play batch with a temporarily inflated balance, then reset.

    The batch builder skips bets when bankroll is too small; we inflate to
    1,000,000 SEK so we observe the raw set, then restore to 0.
    """
    api.post(
        f"/api/bankroll/set/{SIM_PROBE_PROVIDER}",
        json={"balance": 1_000_000},
    ).raise_for_status()
    try:
        batch = api.post("/api/play/batch", json={}).raise_for_status().json()
    finally:
        api.post(
            f"/api/bankroll/set/{SIM_PROBE_PROVIDER}",
            json={"balance": 0},
        ).raise_for_status()
    return batch.get("bets") or batch.get("batch") or []


def simulate_at_bankroll(bets: list[dict], bankroll: float) -> dict:
    """Run calculate_stake at a given bankroll, return per-bet stakes + funded count."""
    funded = []
    skipped = []
    per_provider_stake: dict[str, float] = {}
    total_ev = 0.0
    for b in bets:
        edge_raw = (b["odds"] / b["fair_odds"] - 1.0) if b.get("fair_odds") else 0.0
        result = calculate_stake(
            bankroll_total=bankroll,
            edge_raw=edge_raw,
            odds=b["odds"],
            min_stake=dynamic_min_stake(bankroll),
        )
        if result.skip_reason or result.stake <= 0:
            skipped.append((b, result.skip_reason))
            continue
        funded.append((b, result.stake))
        per_provider_stake[b["provider_id"]] = (
            per_provider_stake.get(b["provider_id"], 0.0) + result.stake
        )
        total_ev += result.stake * edge_raw
    return {
        "funded": funded,
        "skipped": skipped,
        "per_provider_stake": per_provider_stake,
        "total_ev": total_ev,
        "bankroll": bankroll,
    }


def solve_min_bankroll(bets: list[dict], step: int = 1_000, ceiling: int = 500_000) -> dict:
    """Smallest bankroll funding 100% of bets (every result has no skip_reason)."""
    for B in range(step, ceiling + step, step):
        sim = simulate_at_bankroll(bets, float(B))
        if not sim["skipped"]:
            return sim
    # Couldn't fund all bets — return ceiling result anyway
    return simulate_at_bankroll(bets, float(ceiling))


def build_deposit_section(bets: list[dict]) -> list[str]:
    if not bets:
        return [
            "## Deposit recommendation",
            "",
            "_Value-bet feed empty at audit time — re-run during market hours._",
            "",
        ]

    solved = solve_min_bankroll(bets)
    sims = [(B, simulate_at_bankroll(bets, float(B))) for B in TARGET_BANKROLLS]

    def split(stakes: dict[str, float]) -> str:
        unlim_stakes = {p: stakes.get(p, 0.0) for p in UNLIMITED_FOR_DEPOSIT}
        total = sum(unlim_stakes.values())
        if total <= 0:
            return "—"
        return ", ".join(
            f"{p}={int(round(unlim_stakes[p] / total * 100))}%"
            for p in UNLIMITED_FOR_DEPOSIT if unlim_stakes[p] > 0
        )

    lines = [
        "## Deposit recommendation",
        "",
        f"**Live solve:** smallest bankroll funding 100% of {len(bets)} current bets:",
        "",
        f"- **Total: {int(solved['bankroll']):,} SEK**",
        f"- Per-unlimited-provider split (weighted by bet stakes): {split(solved['per_provider_stake'])}",
        f"- Bets fundable: {len(solved['funded'])}/{len(bets)}",
        f"- Total expected EV: {solved['total_ev']:.2f} SEK",
        "",
        "**Target-bankroll table:**",
        "",
        "| Bankroll | Bets fundable | % of feed | Total EV | Per-unlimited split |",
        "|---|---|---|---|---|",
    ]
    for B, sim in sims:
        pct = (len(sim["funded"]) / len(bets) * 100) if bets else 0.0
        lines.append(
            f"| {B:,} SEK | {len(sim['funded'])}/{len(bets)} | {pct:.0f}% | "
            f"{sim['total_ev']:.2f} SEK | {split(sim['per_provider_stake'])} |"
        )
    lines.append("")
    return lines
```

- [ ] **Step 2: Wire `build_deposit_section` into the report**

In `main()`, **replace** these three lines:

```python
    sections.append("## Deposit recommendation")
    sections.append("")
    sections.append("_Filled in by Task 8._")
    sections.append("")
```

with:

```python
    with httpx.Client(base_url=args.api, timeout=60.0) as api:
        bets = fetch_full_batch(api, profile_id)
    sections.extend(build_deposit_section(bets))
```

- [ ] **Step 3: Manual verification**

```bash
python scripts/audit_fresh_profile.py --out /tmp/audit-test.md
cat /tmp/audit-test.md
```

Expected: a "Deposit recommendation" section with either a live-solve number or the empty-feed message, plus the target-bankroll table.

- [ ] **Step 4: Commit**

```bash
git add scripts/audit_fresh_profile.py
git commit -m "feat(audit): deposit recommendation (live solve + target bankroll table)"
```

---

## Task 9: End-to-end verification + production deploy

The backend changes from Tasks 1–3 must reach the production server before the script's `/api/profiles/{id}/seed-bonuses` endpoint and `bonus_trigger_amount` field are reachable through the local arnold tunnel.

- [ ] **Step 1: Run the full backend test suite**

```bash
cd backend && pytest tests/test_bonus_seed_service.py tests/test_profiles_route_seeding.py tests/test_bankroll_service_trigger.py -v
```

Expected: all pass.

- [ ] **Step 2: Run frontend lint + typecheck**

```bash
cd arnold/frontend && npx tsc --noEmit && npx eslint src/pages/PlayPage.tsx
```

Expected: no errors.

- [ ] **Step 3: Push to main**

```bash
git push origin main
```

- [ ] **Step 4: Deploy via the lock-coordinated script**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

Expected: deploy script acquires lock, rebuilds, waits for `/health` to pass, returns success.

- [ ] **Step 5: Verify the new endpoint is live**

```bash
curl -s -u arnold:$BASIC_AUTH_PASSWORD https://148.251.40.251/api/bankroll | python -m json.tool | head -30
```

Expected: provider entries include `bonus_trigger_amount` (may be `null` for the active production profile if balance > 0 — that's fine; the field is present).

- [ ] **Step 6: Run the audit script against local arnold**

Start `arnold.bat` so the SSH tunnel is up, then:

```bash
python scripts/audit_fresh_profile.py
```

Expected: report written to `docs/audits/2026-04-28-fresh-profile-audit.md`. Exit 0 if no `[!]` flags. Audit profile visible via `/api/profiles`.

- [ ] **Step 7: Manual UI check**

In arnold.bat's browser:

1. Open the profile switcher, activate the **Audit** profile.
2. Open the **Sports → Arbitrage** tab.
3. Confirm: every soft cluster + standalone provider visible at `0 SEK`, with `· deposit Xkr` orange suffix wherever a bonus is configured.
4. Switch back to the real (non-Audit) profile. Confirm: the deposit hints disappear (because real balances > 0 or bonuses already claimed).

If any provider is missing from the arb page or a deposit hint fails to render despite a yaml bonus, fix in code (not in this plan) and re-run from Step 5.

- [ ] **Step 8: Commit the audit report**

```bash
git add docs/audits/2026-04-28-fresh-profile-audit.md
git commit -m "docs(audit): fresh-profile system audit 2026-04-28"
git push origin main
```

---

## Self-review notes

- **Spec coverage:** Components A–D and the four interactive decisions are each implemented by Tasks 1–8 (A=1+2, B=3, C=4+5, D=6+7+8). Task 9 covers the verification/deploy gate.
- **Type consistency:** `seed_provider_bonuses(profile_id, db)` signature is the same in Tasks 1, 2, 6, 7. `bonus_trigger_amount` / `bonus_currency` field names are identical in backend (Task 3) and frontend mapping (Task 4) and audit script (Task 7). `getBalance` / `getTrigger` / `BalanceCell` are introduced in Task 4 and used in Task 5.
- **Out-of-scope items kept out of plan:** no automatic deposit, no Audit profile cleanup, no wagering simulation, no bankroll-page bonus rendering — all per spec.
- **Known fragility:** `BatchBet`'s `fair_odds` field comes from the play-batch endpoint shape — if `/api/play/batch` returns `bets` keyed differently, `fetch_full_batch` falls back to `batch`. If both are absent the script prints `value-bet feed empty` rather than crashing.
