# Stats Page Per-Profile Account Styles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Stats page into a profile-scoped dashboard whose layout adapts to each profile's account *style* (Personal vs Bonus Extraction), and fix the correctness bugs (profile-blindness, 500-bet chart drift, widget sprawl).

**Architecture:** Add a `style` column to `Profile`. Make `/bets`, `/bets/analytics`, `/bankroll`, `/bankroll/stats` accept an optional `profile_id` (omitted ⇒ active profile, fully back-compat). Extend the analytics endpoint with currency-correct `by_strategy` + `by_provider` groupings + `clv_positive_pct`, and add a cheap `/bets/equity-curve` endpoint over the full bet set whose realized-P/L total matches `BankrollService.get_stats`. Decompose the 1,489-line `StatsPage.tsx` into focused `components/stats/*` modules driven by a `useStatsData(profileId, range)` hook; render Personal vs Bonus secondary panels by the selected profile's style.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / pytest (backend); React 19 / TypeScript / Vite / Tailwind / @tanstack/react-query / vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-05-30-stats-page-profile-styles-design.md`

**Working dir:** worktree `worktree-design+stats-profile-styles` (branched from `origin/main`). Run backend commands from `backend/`, frontend from `frontend/`. **All line numbers below are against the worktree's `origin/main` files** (verified during planning) — but always locate code by its symbol name / section anchor too, since line numbers drift.

---

## Verified codebase facts (pinned so you don't re-discover them)

- `ProfileRepo.get_total_bankroll(profile_id)` **already exists** (`backend/src/repositories/profile_repo.py:88`) — returns SEK total across all provider balances. Do NOT add it.
- `ProfileRepo.get_active()` (`profile_repo.py:31`) auto-creates a `default` active profile when none exists.
- `ProfileRepo.get_bonus_statuses_batch(profile_id, provider_ids)` (`profile_repo.py:285`) and `get_all_registered_providers(profile_id)` (`profile_repo.py:148`) exist.
- `BankrollService.get_stats()` return dict **already includes** `profile_id`, `total_profit`, `roi_pct`, `win_rate`, `avg_clv`, `clv_positive_pct`, `clv_count`, `total_staked`, `wins`, `losses`, `voids`, `total_bets` (`backend/src/services/bankroll_service.py:148-168`). `total_profit` is computed over **real-money (non-bonus) settled bets only** (`real_rows`).
- `get_analytics`'s per-bet list variable is named **`bets`** (`backend/src/api/routes/bets.py:438`). `summarize(rows)` returns keys `n, won, lost, void, win_pct, implied_pct, avg_displayed_edge_pct, staked, profit, roi_pct, avg_clv_pct` (`bets.py:482-494`) and converts each row to SEK via the in-route `to_sek(amount, provider_id, currency)` (`bets.py:463-466`). `UTC` is imported inside `get_analytics` via `from datetime import datetime, timedelta` plus the module-level `from datetime import UTC`.
- Frontend `BankrollStats` type (`frontend/src/types/index.ts:186-204`) **already has** `avg_clv`, `clv_positive_pct`, `clv_count`, `total_profit`, `roi_pct`, `total_staked`, `wins/losses/voids/total_bets` (it has **no** `bankroll` field — pass live bankroll separately).
- Frontend `api.updateProfile(id, ProfileUpdate)` exists and uses **PUT** (`frontend/src/services/api/profiles.ts:17`); the backend `update_profile` route is **PUT** (`backend/src/api/routes/profiles.py:159`).
- `TabBar` exports `TabIcon` and `TAB_COLORS` with a `stats` key — the current `StatsPage` already imports them (`StatsPage.tsx:9`). Reuse that import verbatim.
- `/api/bankroll/status` exists (`backend/src/api/routes/bankroll.py:247` → `service.get_status()`).
- Alembic is wired: `backend/alembic/versions/001..005`, numeric string ids (`revision="005"`, `down_revision="004"`). Next is **`006`**.
- `BonusProgressEntry` TS type (`types/index.ts:160`) already models the bonus-status shape (status, bonus_type, progress_pct, days_remaining, …).

---

## File Structure

**Backend (modify):**
- `backend/src/db/models.py` — add `Profile.style` column + idempotent in-code migration
- `backend/src/api/schemas.py` — add `style` to `ProfileCreate`/`ProfileUpdate`
- `backend/src/api/routes/profiles.py` — emit `style` in `profile_to_dict`; accept in create/update; new `GET /{profile_id}/bonus-statuses`
- `backend/src/repositories/profile_repo.py` — add `get(profile_id)` (NOTE: `get_total_bankroll` already exists)
- `backend/src/repositories/bet_repo.py` — add `get_settled_for_curve(profile_id, cutoff)`
- `backend/src/api/routes/bets.py` — `profile_id` on `list_bets` + `get_analytics`; `by_strategy`/`by_provider` + `clv_positive_pct`; new `equity_curve` route
- `backend/src/api/routes/bankroll.py` — `profile_id` on `/bankroll` + `/bankroll/stats`
- `backend/src/services/bankroll_service.py` — `get_bankroll(profile_id=None)` + `get_stats(profile_id=None)`
- `backend/alembic/versions/006_add_profile_style.py` — formal migration (hand-authored, numeric convention)

**Backend (create test):**
- `backend/tests/test_stats_profile_styles.py` — self-contained (StaticPool fixture + client), covering model/migration, profile_id resolution, by_strategy/by_provider, equity-curve (incl. bonus-agreement)

**Frontend (modify):**
- `frontend/src/types/index.ts` — `Profile.style`, `ProfileCreate/Update.style`
- `frontend/src/services/api/bets.ts` — `profile_id` on `getBets`/`getAnalytics`; new `getEquityCurve`; `by_strategy`/`by_provider` in return type; `export` `AnalyticsBucket` + add `clv_positive_pct`
- `frontend/src/services/api/bankroll.ts` — `profile_id` on `getBankroll`/`getBankrollStats`
- `frontend/src/services/api/profiles.ts` — `getProfileBonusStatuses(profileId)`
- `frontend/src/components/ProfileSelector.tsx` — style toggle in create form
- `frontend/src/pages/StatsPage.tsx` — reduce to shell + sub-tab routing + style dispatch

**Frontend (create):**
- `frontend/src/hooks/useStatsData.ts`
- `frontend/src/components/stats/StatsHeader.tsx`, `KpiBlock.tsx`, `charts.tsx`, `StrategySplit.tsx`, `EdgeAnalytics.tsx`, `BonusPanel.tsx`, `BetHistory.tsx`, `ShadowCLV.tsx`
- `frontend/src/components/stats/lanes.ts`, `equity.ts` (+ `lanes.test.ts`, `equity.test.ts`)

---

## PHASE 1 — Backend: data model + profile_id plumbing

### Task 1: `Profile.style` column + migration + self-contained test harness

**Files:**
- Modify: `backend/src/db/models.py` (Profile class ~line 649; migration block ~line 1636)
- Create: `backend/alembic/versions/006_add_profile_style.py`
- Create: `backend/tests/test_stats_profile_styles.py`

- [ ] **Step 1: Create the test file WITH its own fixtures + the first failing tests**

The conftest `db_session` is a plain in-memory engine that cannot back a TestClient (each connection gets a fresh DB). Use a StaticPool engine + a `client` that overrides `get_db` — copied from the proven pattern in `backend/tests/api/test_bonus_arbs.py:26-58`. Create `backend/tests/test_stats_profile_styles.py`:

```python
"""Tests for Stats page per-profile account styles."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import app
from src.api.deps import get_db
from src.db.models import Base, Bet, Profile


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Profile(id=1, name="test", is_active=True))
    s.commit()
    yield s
    s.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_profile_has_style_default_personal(db_session):
    p = Profile(name="t_style_default")
    db_session.add(p)
    db_session.commit()
    assert p.style == "personal"


def test_profile_style_settable(db_session):
    p = Profile(name="t_style_bonus", style="bonus_extraction")
    db_session.add(p)
    db_session.commit()
    assert p.style == "bonus_extraction"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_stats_profile_styles.py -v`
Expected: FAIL — `AttributeError`/`None` for `Profile.style`.

- [ ] **Step 3: Add the column to the model**

In `backend/src/db/models.py`, in `class Profile`, immediately after the `color` column (~line 649):

```python
    color = Column(String, nullable=True)  # Hex color for Chrome border (auto-assigned)
    style = Column(String, nullable=False, default="personal")  # "personal" | "bonus_extraction"
```

- [ ] **Step 4: Add idempotent in-code migration**

In `backend/src/db/models.py`, right after the `chrome_port` migration block (~line 1636):

```python
        # Add style to profiles (Stats per-profile account styles)
        try:
            cursor.execute("SELECT style FROM profiles LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profiles ADD COLUMN style TEXT NOT NULL DEFAULT 'personal'")
                raw.commit()
            except sqlite3.OperationalError:
                pass
```

- [ ] **Step 5: Add the Alembic revision (hand-authored, numeric convention)**

Create `backend/alembic/versions/006_add_profile_style.py` (match the existing `00N` style; head is `005`):

```python
"""Add style column to profiles (Stats per-profile account styles).

Revision ID: 006
Revises: 005
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("style", sa.String(), nullable=False, server_default="personal"))


def downgrade() -> None:
    op.drop_column("profiles", "style")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_stats_profile_styles.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add backend/src/db/models.py backend/alembic/versions/006_add_profile_style.py backend/tests/test_stats_profile_styles.py
git commit -m "feat(profiles): add style column (personal|bonus_extraction)"
```

---

### Task 2: Surface `style` through schemas + profiles route

**Files:**
- Modify: `backend/src/api/schemas.py:150-179`
- Modify: `backend/src/api/routes/profiles.py` (`profile_to_dict` ~line 63; `create_profile` ~line 126; `update_profile` ~line 166)
- Test: `backend/tests/test_stats_profile_styles.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_stats_profile_styles.py`:

```python
def test_profile_to_dict_includes_style(db_session):
    from src.api.routes.profiles import profile_to_dict
    from src.repositories import ProfileRepo
    p = Profile(name="t_dict_style", style="bonus_extraction")
    db_session.add(p)
    db_session.commit()
    d = profile_to_dict(p, ProfileRepo(db_session))
    assert d["style"] == "bonus_extraction"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_stats_profile_styles.py::test_profile_to_dict_includes_style -v`
Expected: FAIL — `KeyError: 'style'`.

- [ ] **Step 3: Add `style` to schemas**

In `backend/src/api/schemas.py`, add to `ProfileCreate` (after `color`): `style: str | None = "personal"`. Add to `ProfileUpdate` (after `color`): `style: str | None = None`.

- [ ] **Step 4: Emit + accept `style` in the route**

In `backend/src/api/routes/profiles.py`, in `profile_to_dict` return dict (after the `"color"` key):

```python
        "color": profile.color or PROFILE_COLORS[0],
        "style": profile.style or "personal",
```

In `create_profile`, add `style=data.style or "personal"` to the `Profile(...)` constructor.
In `update_profile`, add: `if data.style is not None: profile.style = data.style`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_stats_profile_styles.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/schemas.py backend/src/api/routes/profiles.py backend/tests/test_stats_profile_styles.py
git commit -m "feat(profiles): expose style in create/update/serialization"
```

---

### Task 3: `profile_id` param + `ProfileRepo.get`

**Files:**
- Modify: `backend/src/repositories/profile_repo.py` (add `get`)
- Modify: `backend/src/api/routes/bets.py` (`list_bets` ~163, `get_analytics` ~409)
- Modify: `backend/src/api/routes/bankroll.py` (`/bankroll` ~31, `/bankroll/stats` ~122)
- Modify: `backend/src/services/bankroll_service.py` (`get_bankroll`, `get_stats`)
- Test: `backend/tests/test_stats_profile_styles.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_profile_repo_get_returns_by_id(db_session):
    from src.repositories import ProfileRepo
    p = Profile(name="t_repo_get")
    db_session.add(p)
    db_session.commit()
    assert ProfileRepo(db_session).get(p.id).id == p.id


def test_profile_repo_get_none_returns_active(db_session):
    from src.repositories import ProfileRepo
    repo = ProfileRepo(db_session)
    assert repo.get(None).id == repo.get_active().id
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_stats_profile_styles.py -k profile_repo_get -v`
Expected: FAIL — no attribute `get`.

- [ ] **Step 3: Add `get` to ProfileRepo**

In `backend/src/repositories/profile_repo.py`, after `get_active`:

```python
    def get(self, profile_id: int | None) -> Profile:
        """Resolve a profile by id, or the active profile when id is None/missing."""
        if profile_id is None:
            return self.get_active()
        profile = self.db.query(Profile).filter(Profile.id == profile_id).first()
        return profile or self.get_active()
```

- [ ] **Step 4: Run repo test to verify it passes**

Run: `cd backend && pytest tests/test_stats_profile_styles.py -k profile_repo_get -v`
Expected: PASS.

- [ ] **Step 5: Thread `profile_id` through `list_bets`**

In `backend/src/api/routes/bets.py`:

```python
@router.get("")
def list_bets(
    status: str | None = None,
    exclude_bonus: bool = False,
    limit: int = 50,
    profile_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Get bet history for a profile (active profile if profile_id omitted)."""
    profile_repo = ProfileRepo(db)
    bet_repo = BetRepo(db)
    profile = profile_repo.get(profile_id)
```

(Rest of `list_bets` already uses `profile.id` — unchanged.)

- [ ] **Step 6: Thread `profile_id` through `get_analytics`**

In `backend/src/api/routes/bets.py`, `get_analytics`:

```python
@router.get("/analytics")
def get_analytics(
    provider_id: str | None = None,
    days: int = 90,
    profile_id: int | None = None,
    db: Session = Depends(get_db),
):
    ...
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get(profile_id)
    if not profile:
        raise HTTPException(404, "No active profile")
```

- [ ] **Step 7: Thread `profile_id` through bankroll service + routes**

In `backend/src/services/bankroll_service.py`, change `get_stats` signature + profile resolution (the return dict ALREADY contains `"profile_id": profile.id` — no dict change needed):

```python
    def get_stats(self, profile_id: int | None = None) -> dict:
        """Get bankroll statistics for a profile (active if profile_id omitted)."""
        from ..config import get_exchange_rate
        profile = self.profile_repo.get(profile_id)
```

Do the same for `get_bankroll`: add `profile_id: int | None = None` and replace its `self.profile_repo.get_active()` with `self.profile_repo.get(profile_id)`.

In `backend/src/api/routes/bankroll.py`:

```python
@router.get("")
def get_bankroll(profile_id: int | None = None, service: BankrollService = Depends(_get_service)):
    """Get provider balances and total bankroll for a profile (active if omitted)."""
    return service.get_bankroll(profile_id)


@router.get("/stats")
def get_bankroll_stats(profile_id: int | None = None, service: BankrollService = Depends(_get_service)):
    """Get bankroll statistics for a profile (active if omitted)."""
    return service.get_stats(profile_id)
```

- [ ] **Step 8: Write the back-compat test (contract is verifiable — `profile_id` is already in the dict)**

Append:

```python
def test_get_stats_profile_id_matches_active_when_omitted(db_session):
    from src.services import BankrollService
    svc = BankrollService(db_session)
    active_id = svc.profile_repo.get_active().id
    assert svc.get_stats()["profile_id"] == active_id
    assert svc.get_stats(active_id)["profile_id"] == active_id
```

- [ ] **Step 9: Run to verify they pass**

Run: `cd backend && pytest tests/test_stats_profile_styles.py tests/test_bankroll_service_get_stats.py -v`
Expected: PASS (incl. existing get_stats tests — back-compat).

- [ ] **Step 10: Commit**

```bash
git add backend/src/repositories/profile_repo.py backend/src/api/routes/bets.py backend/src/api/routes/bankroll.py backend/src/services/bankroll_service.py backend/tests/test_stats_profile_styles.py
git commit -m "feat(api): optional profile_id on bets/analytics/bankroll (active fallback)"
```

---

## PHASE 2 — Backend: aggregates

### Task 4: `clv_positive_pct` + `by_strategy` in analytics

**Files:**
- Modify: `backend/src/api/routes/bets.py` (`summarize` ~482-494; grouping + return dict ~538-548)
- Test: `backend/tests/test_stats_profile_styles.py`

- [ ] **Step 1: Write the failing test (assert ALL consumed keys, not just profit/n)**

The frontend lane/provider tables call `.toFixed()` on `staked`, `profit`, `roi_pct`, `avg_clv_pct`, `clv_positive_pct` and read `n`, `win_pct` — assert they all exist so a missing/renamed key fails here, not at runtime:

```python
def test_analytics_by_strategy_lanes(client, db_session):
    from src.db.models import Bet
    from src.repositories import ProfileRepo
    pid = ProfileRepo(db_session).get_active().id
    db_session.add_all([
        Bet(profile_id=pid, provider_id="betsson", market="1x2", outcome="home",
            odds=2.0, stake=100.0, currency="SEK", bet_type="value",
            result="won", payout=200.0, clv_pct=3.0),
        Bet(profile_id=pid, provider_id="betsson", market="1x2", outcome="home",
            odds=2.0, stake=100.0, currency="SEK", bet_type="value",
            result="lost", payout=0.0, clv_pct=-1.0),
        Bet(profile_id=pid, provider_id="pinnacle", market="1x2", outcome="home",
            odds=2.0, stake=100.0, currency="SEK", bet_type="arb",
            result="won", payout=200.0),
    ])
    db_session.commit()
    r = client.get(f"/api/bets/analytics?days=3650&profile_id={pid}").json()
    v = r["by_strategy"]["Value"]
    for k in ("n", "win_pct", "staked", "profit", "roi_pct", "avg_clv_pct", "clv_positive_pct"):
        assert k in v, f"missing key {k}"
    assert v["n"] == 2
    assert v["profit"] == 0.0            # +100 and -100
    assert v["clv_positive_pct"] == 50.0
    assert r["by_strategy"]["Arb"]["n"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_stats_profile_styles.py::test_analytics_by_strategy_lanes -v`
Expected: FAIL — `KeyError: 'by_strategy'`.

- [ ] **Step 3: Add `clv_positive_pct` to `summarize`**

In `backend/src/api/routes/bets.py`, inside `summarize`, after `clvs = [...]`:

```python
        clvs = [r.clv_pct for r in rows if r.clv_pct is not None]
        clv_pos = sum(1 for c in clvs if c >= 0)
```

Add to the returned dict after `"avg_clv_pct"`:

```python
            "avg_clv_pct": round(sum(clvs) / len(clvs), 2) if clvs else None,
            "clv_positive_pct": round(100 * clv_pos / len(clvs), 1) if clvs else None,
```

- [ ] **Step 4: Add lane mapping + grouping (loop over `bets` — the verified var name)**

Before the final `return {`:

```python
    _LANE = {"value": "Value", "arb": "Arb", "reverse": "Reverse", "boost": "Boost"}

    by_strategy = {}
    for b in bets:
        by_strategy.setdefault(_LANE.get(b.bet_type or "", "Other"), []).append(b)
```

Add to the return dict:

```python
        "by_strategy": {k: summarize(v) for k, v in by_strategy.items()},
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && pytest tests/test_stats_profile_styles.py::test_analytics_by_strategy_lanes -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes/bets.py backend/tests/test_stats_profile_styles.py
git commit -m "feat(analytics): by_strategy lanes + clv_positive_pct"
```

---

### Task 5: `by_provider` in analytics

**Files:**
- Modify: `backend/src/api/routes/bets.py`
- Test: `backend/tests/test_stats_profile_styles.py`

- [ ] **Step 1: Write the failing test**

```python
def test_analytics_by_provider_currency_correct(client, db_session):
    from src.db.models import Bet
    from src.repositories import ProfileRepo
    pid = ProfileRepo(db_session).get_active().id
    db_session.add_all([
        Bet(profile_id=pid, provider_id="betsson", market="1x2", outcome="home",
            odds=2.0, stake=100.0, currency="SEK", bet_type="value",
            result="won", payout=200.0),
        Bet(profile_id=pid, provider_id="polymarket", market="moneyline", outcome="home",
            odds=2.0, stake=10.0, currency="USDC", bet_type="value",
            result="won", payout=20.0),
    ])
    db_session.commit()
    r = client.get(f"/api/bets/analytics?days=3650&profile_id={pid}").json()
    assert r["by_provider"]["betsson"]["profit"] == 100.0
    assert r["by_provider"]["polymarket"]["profit"] > 100.0   # +10 USDC ~105 SEK
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_stats_profile_styles.py::test_analytics_by_provider_currency_correct -v`
Expected: FAIL — `KeyError: 'by_provider'`.

- [ ] **Step 3: Add the grouping**

After the `by_strategy` block:

```python
    by_provider = {}
    for b in bets:
        by_provider.setdefault(b.provider_id, []).append(b)
```

Add to the return dict:

```python
        "by_provider": {k: summarize(v) for k, v in by_provider.items()},
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/test_stats_profile_styles.py::test_analytics_by_provider_currency_correct -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/bets.py backend/tests/test_stats_profile_styles.py
git commit -m "feat(analytics): by_provider currency-correct grouping"
```

---

### Task 6: equity-curve endpoint (matches `get_stats` realized P/L)

**Files:**
- Modify: `backend/src/repositories/bet_repo.py` (add `get_settled_for_curve`)
- Modify: `backend/src/api/routes/bets.py` (new route)
- Test: `backend/tests/test_stats_profile_styles.py`

**Convention (spec §8):** `get_stats.total_profit` counts **non-bonus** settled bets only (`real_rows`). The equity curve MUST use the same convention so its `total_profit_sek` equals the KPI Net Profit — therefore it **skips `is_bonus` rows** for both cumulative profit and staked.

- [ ] **Step 1: Write the failing tests (incl. bonus-agreement with get_stats)**

```python
def test_equity_curve_cumulative_and_baseline(client, db_session):
    from src.db.models import Bet
    from src.repositories import ProfileRepo
    from datetime import datetime, timedelta, UTC
    pid = ProfileRepo(db_session).get_active().id
    t0 = datetime.now(UTC) - timedelta(days=2)
    db_session.add_all([
        Bet(profile_id=pid, provider_id="betsson", market="1x2", outcome="home",
            odds=2.0, stake=100.0, currency="SEK", bet_type="value",
            result="won", payout=200.0, placed_at=t0),
        Bet(profile_id=pid, provider_id="betsson", market="1x2", outcome="home",
            odds=2.0, stake=100.0, currency="SEK", bet_type="value",
            result="lost", payout=0.0, placed_at=t0 + timedelta(hours=1)),
    ])
    db_session.commit()
    r = client.get(f"/api/bets/equity-curve?days=3650&profile_id={pid}").json()
    assert r["total_profit_sek"] == 0.0
    assert [round(p["cum_profit_sek"]) for p in r["points"]] == [100, 0]
    assert "current_bankroll_sek" in r
    assert "total_staked_sek" in r


def test_equity_curve_total_matches_get_stats_with_bonus(client, db_session):
    """A winning bonus bet must NOT inflate the curve vs the KPI Net Profit."""
    from src.db.models import Bet
    from src.services import BankrollService
    from src.repositories import ProfileRepo
    pid = ProfileRepo(db_session).get_active().id
    db_session.add_all([
        Bet(profile_id=pid, provider_id="betsson", market="1x2", outcome="home",
            odds=2.0, stake=100.0, currency="SEK", bet_type="value",
            result="won", payout=200.0),
        Bet(profile_id=pid, provider_id="betsson", market="1x2", outcome="home",
            odds=3.0, stake=50.0, currency="SEK", bet_type="value",
            is_bonus=True, result="won", payout=150.0),
    ])
    db_session.commit()
    curve = client.get(f"/api/bets/equity-curve?profile_id={pid}").json()
    stats_profit = BankrollService(db_session).get_stats(pid)["total_profit"]
    assert curve["total_profit_sek"] == stats_profit   # both exclude the bonus bet
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_stats_profile_styles.py -k equity_curve -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add the repo query**

In `backend/src/repositories/bet_repo.py`, in `class BetRepo`:

```python
    def get_settled_for_curve(self, profile_id: int, cutoff=None) -> list:
        """Ordered settled bets for the equity curve — minimal columns, no enrichment.

        Returns Row objects with: placed_at, result, payout, stake, currency,
        provider_id, is_bonus. Ordered by placed_at ASC.
        """
        q = self.db.query(
            Bet.placed_at, Bet.result, Bet.payout, Bet.stake,
            Bet.currency, Bet.provider_id, Bet.is_bonus,
        ).filter(
            Bet.profile_id == profile_id,
            Bet.result.in_(("won", "lost", "void")),
        )
        if cutoff is not None:
            q = q.filter(Bet.placed_at >= cutoff)
        return q.order_by(Bet.placed_at.asc()).all()
```

- [ ] **Step 4: Add the route (excludes bonus rows to match get_stats)**

In `backend/src/api/routes/bets.py`, next to `get_analytics`:

```python
@router.get("/equity-curve")
def equity_curve(
    days: int | None = None,
    profile_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Cumulative realized P/L (SEK) over a profile's settled, real-money bets.

    Cheap: minimal columns, no per-bet odds/Pinnacle enrichment. Excludes bonus
    bets so total_profit_sek matches BankrollService.get_stats (spec §8); the
    end value reconciles to current bankroll so the chart matches the KPIs.
    """
    from datetime import datetime, timedelta

    from ...config import get_exchange_rate
    from ...repositories import BetRepo, ProfileRepo

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get(profile_id)
    if not profile:
        raise HTTPException(404, "No active profile")

    cutoff = (datetime.now(UTC) - timedelta(days=days)) if days else None
    rows = BetRepo(db).get_settled_for_curve(profile.id, cutoff)

    def to_sek(amount, provider_id, currency):
        return amount if (currency or "SEK") == "SEK" else amount * get_exchange_rate(provider_id)

    def bet_profit(row):  # non-bonus realized P/L, mirrors get_stats real_rows
        if row.result == "won":
            return row.payout - row.stake
        if row.result == "lost":
            return -row.stake
        return 0.0

    points = []
    cum = 0.0
    staked = 0.0
    for row in rows:
        if row.is_bonus:
            continue
        cum += to_sek(bet_profit(row), row.provider_id, row.currency)
        staked += to_sek(row.stake, row.provider_id, row.currency)
        points.append({
            "t": row.placed_at.isoformat() if row.placed_at else None,
            "cum_profit_sek": round(cum, 2),
        })

    return {
        "points": points,
        "total_profit_sek": round(cum, 2),
        "total_staked_sek": round(staked, 2),
        "current_bankroll_sek": round(profile_repo.get_total_bankroll(profile.id), 2),
    }
```

Note: `UTC` is already module-imported in `bets.py`; if a linter flags scope, add `from datetime import UTC` to the local import line.

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && pytest tests/test_stats_profile_styles.py -k equity_curve -v`
Expected: PASS.

- [ ] **Step 6: Run the full backend stats suite**

Run: `cd backend && pytest tests/test_stats_profile_styles.py tests/test_bet_repo.py tests/test_bankroll_service_get_stats.py -v`
Expected: PASS. (Do NOT add `tests/test_analytics.py` — it covers a `RecommendationManager`, not the analytics HTTP route.)

- [ ] **Step 7: Commit**

```bash
git add backend/src/repositories/bet_repo.py backend/src/api/routes/bets.py backend/tests/test_stats_profile_styles.py
git commit -m "feat(api): GET /bets/equity-curve — realized P/L (SEK), matches get_stats"
```

---

### Task 6b: `GET /api/profiles/{id}/bonus-statuses` (powers the Bonus panel — spec §5.5/§7)

**Files:**
- Modify: `backend/src/api/routes/profiles.py`
- Test: `backend/tests/test_stats_profile_styles.py`

- [ ] **Step 1: Write the failing test**

```python
def test_profile_bonus_statuses_endpoint(client, db_session):
    from src.repositories import ProfileRepo
    pid = ProfileRepo(db_session).get_active().id
    r = client.get(f"/api/profiles/{pid}/bonus-statuses")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)   # {provider_id: {status, bonus_type, progress_pct, days_remaining, ...}}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_stats_profile_styles.py::test_profile_bonus_statuses_endpoint -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add the route**

In `backend/src/api/routes/profiles.py`:

```python
@router.get("/{profile_id}/bonus-statuses")
def get_profile_bonus_statuses(profile_id: int, db: Session = Depends(get_db)):
    """Per-provider bonus status for a profile (powers the Stats bonus panel)."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get(profile_id)
    provider_ids = sorted(profile_repo.get_all_registered_providers(profile.id))
    return profile_repo.get_bonus_statuses_batch(profile.id, provider_ids)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/test_stats_profile_styles.py::test_profile_bonus_statuses_endpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/profiles.py backend/tests/test_stats_profile_styles.py
git commit -m "feat(api): GET /profiles/{id}/bonus-statuses for the Stats bonus panel"
```

---

## PHASE 3 — Frontend: types + API

### Task 7: TS types + API methods

**Files:**
- Modify: `frontend/src/types/index.ts` (Profile/ProfileCreate/ProfileUpdate)
- Modify: `frontend/src/services/api/bets.ts` (getBets, getAnalytics, getEquityCurve, AnalyticsBucket)
- Modify: `frontend/src/services/api/bankroll.ts`
- Modify: `frontend/src/services/api/profiles.ts` (getProfileBonusStatuses)

- [ ] **Step 1: Add `style` to Profile types**

In `frontend/src/types/index.ts`, add to `Profile` (after `color`): `style: 'personal' | 'bonus_extraction';`. Add `style?: 'personal' | 'bonus_extraction';` to both `ProfileCreate` and `ProfileUpdate`.

- [ ] **Step 2: `export` AnalyticsBucket + add `clv_positive_pct`; add params + equity-curve to bets API**

In `frontend/src/services/api/bets.ts`: ensure `AnalyticsBucket` is `export`ed and add `clv_positive_pct: number | null;` to it. Update methods:

```typescript
  async getBets(
    status?: 'pending' | 'won' | 'lost' | 'void',
    limit = 50,
    profileId?: number,
  ): Promise<{ bets: Bet[]; count: number }> {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    params.set('limit', limit.toString());
    if (profileId != null) params.set('profile_id', String(profileId));
    return fetchJson(`/bets?${params}`);
  },

  async getAnalytics(
    providerId?: string,
    days = 90,
    profileId?: number,
  ): Promise<{
    provider_id: string | null;
    days: number;
    cutoff: string;
    overall: AnalyticsBucket | null;
    by_sport: Record<string, AnalyticsBucket>;
    by_edge_bucket: Record<string, AnalyticsBucket>;
    by_sport_and_bucket: Record<string, AnalyticsBucket>;
    by_sport_and_market: Record<string, AnalyticsBucketWithMultiplier>;
    by_strategy: Record<string, AnalyticsBucket>;
    by_provider: Record<string, AnalyticsBucket>;
    bucket_confidence_enabled: boolean;
  }> {
    const params = new URLSearchParams();
    if (providerId) params.set('provider_id', providerId);
    params.set('days', String(days));
    if (profileId != null) params.set('profile_id', String(profileId));
    return fetchJson(`/bets/analytics?${params}`);
  },

  async getEquityCurve(
    profileId?: number,
    days?: number,
  ): Promise<{
    points: { t: string | null; cum_profit_sek: number }[];
    total_profit_sek: number;
    total_staked_sek: number;
    current_bankroll_sek: number;
  }> {
    const params = new URLSearchParams();
    if (profileId != null) params.set('profile_id', String(profileId));
    if (days != null) params.set('days', String(days));
    return fetchJson(`/bets/equity-curve?${params}`);
  },
```

- [ ] **Step 3: Add `profile_id` to bankroll API**

```typescript
  async getBankroll(profileId?: number): Promise<BankrollInfo> {
    const q = profileId != null ? `?profile_id=${profileId}` : '';
    return fetchJson<BankrollInfo>(`/bankroll${q}`);
  },

  async getBankrollStats(profileId?: number): Promise<BankrollStats> {
    const q = profileId != null ? `?profile_id=${profileId}` : '';
    return fetchJson<BankrollStats>(`/bankroll/stats${q}`);
  },
```

- [ ] **Step 4: Add bonus-statuses to profiles API**

In `frontend/src/services/api/profiles.ts`, add to `profilesApi`:

```typescript
  async getProfileBonusStatuses(profileId: number): Promise<Record<string, import('@/types').BonusProgressEntry>> {
    return fetchJson(`/profiles/${profileId}/bonus-statuses`);
  },
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors (all new params optional; existing call sites compile).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/services/api/bets.ts frontend/src/services/api/bankroll.ts frontend/src/services/api/profiles.ts
git commit -m "feat(api-client): profile_id params, equity-curve, by_strategy/by_provider, bonus-statuses, profile style"
```

---

## PHASE 4 — Frontend: decomposed components

> Tasks 8–16 move logic out of `StatsPage.tsx` into `components/stats/*`. Move code by **symbol name** (line numbers are worktree-current but drift). Each component-extraction task leaves the tree compiling by adding a re-export import to `StatsPage.tsx` for the moved symbols; Task 15 then rewrites `StatsPage.tsx` and deletes the now-orphaned originals.

### Task 8: pure helpers (vitest) + `useStatsData` + `StatsHeader`

**Files:**
- Create: `frontend/src/components/stats/lanes.ts`, `equity.ts` (+ `.test.ts` each)
- Create: `frontend/src/hooks/useStatsData.ts`, `frontend/src/components/stats/StatsHeader.tsx`

Note on vitest: `frontend/vitest.config.ts` exists (jsdom, globals:false) but has **no `@` alias** and no setup files. These two tests use only relative imports + explicit `import { describe, it, expect } from 'vitest'`, so they run as-is. (If component tests are added later, add `resolve.alias` for `@` to the vitest config — out of scope here.)

- [ ] **Step 1: Write failing pure-helper tests**

`frontend/src/components/stats/lanes.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { LANE_ORDER, laneLabel } from './lanes';

describe('lanes', () => {
  it('orders lanes Value, Arb, Reverse, Boost, Other', () => {
    expect(LANE_ORDER).toEqual(['Value', 'Arb', 'Reverse', 'Boost', 'Other']);
  });
  it('labels are identity (backend already returns lane names)', () => {
    expect(laneLabel('Value')).toBe('Value');
  });
});
```

`frontend/src/components/stats/equity.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { toEquityPoints } from './equity';

describe('toEquityPoints', () => {
  it('anchors baseline so the last point equals current bankroll', () => {
    const pts = toEquityPoints(
      [{ t: '2026-01-01T00:00:00Z', cum_profit_sek: 100 },
       { t: '2026-01-02T00:00:00Z', cum_profit_sek: 0 }],
      { total_profit_sek: 0, current_bankroll_sek: 5000 },
    );
    expect(pts[pts.length - 1].value).toBe(5000);
    expect(pts[0].value).toBe(5100);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/components/stats/lanes.test.ts src/components/stats/equity.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement the helpers**

`frontend/src/components/stats/lanes.ts`:

```typescript
export const LANE_ORDER = ['Value', 'Arb', 'Reverse', 'Boost', 'Other'] as const;
export type Lane = (typeof LANE_ORDER)[number];
export function laneLabel(lane: string): string {
  return lane;
}
```

`frontend/src/components/stats/equity.ts`:

```typescript
export interface EquityPoint { date: Date; value: number }

/** Convert backend equity-curve points to baseline-anchored equity values.
 *  baseline = current_bankroll - total_profit; value = baseline + cum_profit.
 *  Guarantees the final point equals current bankroll. */
export function toEquityPoints(
  points: { t: string | null; cum_profit_sek: number }[],
  meta: { total_profit_sek: number; current_bankroll_sek: number },
): EquityPoint[] {
  const baseline = meta.current_bankroll_sek - meta.total_profit_sek;
  return points
    .filter((p) => p.t)
    .map((p) => ({ date: new Date(p.t as string), value: baseline + p.cum_profit_sek }));
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd frontend && npx vitest run src/components/stats/lanes.test.ts src/components/stats/equity.test.ts`
Expected: PASS.

- [ ] **Step 5: Create the data hook**

`frontend/src/hooks/useStatsData.ts`:

```typescript
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';

export type StatsRange = 'all' | '90d' | '30d' | '7d';
export const RANGE_DAYS: Record<StatsRange, number> = { all: 3650, '90d': 90, '30d': 30, '7d': 7 };

/** Single source of truth for the Stats page, keyed on (profileId, range).
 *  KPIs + equity curve are all-time; analytics + history honor the range. */
export function useStatsData(profileId: number | undefined, range: StatsRange) {
  const days = RANGE_DAYS[range];
  const enabled = profileId != null;

  const stats = useQuery({
    queryKey: ['bankroll', 'stats', profileId],
    queryFn: () => api.getBankrollStats(profileId), staleTime: 30_000, enabled,
  });
  const equity = useQuery({
    queryKey: ['bets', 'equity-curve', profileId],
    queryFn: () => api.getEquityCurve(profileId), staleTime: 30_000, enabled,
  });
  const analytics = useQuery({
    queryKey: ['bets', 'analytics', profileId, days],
    queryFn: () => api.getAnalytics(undefined, days, profileId), staleTime: 60_000, enabled,
  });
  const bets = useQuery({
    queryKey: ['bets', 'all', profileId],
    queryFn: () => api.getBets(undefined, 500, profileId), staleTime: 30_000, enabled,
  });

  return { stats, equity, analytics, bets };
}
```

- [ ] **Step 6: Create the header**

`frontend/src/components/stats/StatsHeader.tsx`:

```typescript
import { useProfiles } from '@/hooks/useProfiles';
import { api } from '@/services/api';
import { useQueryClient } from '@tanstack/react-query';
import type { StatsRange } from '@/hooks/useStatsData';

const RANGES: StatsRange[] = ['all', '90d', '30d', '7d'];

export function StatsHeader({
  profileId, setProfileId, range, setRange,
}: {
  profileId: number | undefined;
  setProfileId: (id: number) => void;
  range: StatsRange;
  setRange: (r: StatsRange) => void;
}) {
  const { profiles } = useProfiles();
  const qc = useQueryClient();
  const selected = profiles.find((p) => p.id === profileId);
  const nextStyle = selected?.style === 'bonus_extraction' ? 'personal' : 'bonus_extraction';

  const toggleStyle = async () => {
    if (!selected) return;
    await api.updateProfile(selected.id, { style: nextStyle });   // PUT /profiles/{id}
    qc.invalidateQueries({ queryKey: ['profiles'] });
  };

  return (
    <div className="flex items-center justify-between flex-wrap gap-2">
      <div className="flex items-center gap-2">
        <select
          className="px-2 py-1 text-xs bg-panel border border-border text-text"
          value={profileId ?? ''}
          onChange={(e) => setProfileId(Number(e.target.value))}
        >
          {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button
          onClick={toggleStyle}
          title="Click to switch this profile's stats style"
          className={`px-2 py-0.5 text-[10px] uppercase tracking-wider rounded border ${
            selected?.style === 'bonus_extraction'
              ? 'bg-tabBankroll/20 text-tabBankroll border-tabBankroll/40'
              : 'bg-accent/15 text-accent border-accent/40'
          }`}
        >
          {selected?.style === 'bonus_extraction' ? 'Bonus Extraction' : 'Personal'}
        </button>
      </div>
      <div className="flex gap-1">
        {RANGES.map((r) => (
          <button key={r} onClick={() => setRange(r)}
            className={`px-2 py-0.5 text-[10px] rounded border ${
              range === r ? 'bg-tabBets/20 text-tabBets border-tabBets/40'
                          : 'bg-panel2 text-muted border-border hover:text-text'}`}>
            {r}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Typecheck + test**

Run: `cd frontend && npx vitest run src/components/stats && npx tsc -b`
Expected: vitest PASS; tsc no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/stats/lanes.ts frontend/src/components/stats/equity.ts frontend/src/components/stats/lanes.test.ts frontend/src/components/stats/equity.test.ts frontend/src/hooks/useStatsData.ts frontend/src/components/stats/StatsHeader.tsx
git commit -m "feat(stats): pure helpers + useStatsData hook + StatsHeader (profile/style/range)"
```

---

### Task 9: Move charts (charts.tsx) + KpiBlock

**Files:**
- Create: `frontend/src/components/stats/charts.tsx` (move `CHART` @86, `polyChart` @88, `BankrollChart` @109, `CLVChart` @234 from `StatsPage.tsx`)
- Create: `frontend/src/components/stats/KpiBlock.tsx`

- [ ] **Step 1: Move chart code into charts.tsx**

Cut `CHART`, `polyChart`, `BankrollChart`, `CLVChart` (`StatsPage.tsx:86-371`) into a new `frontend/src/components/stats/charts.tsx`. Do NOT move `getTTK`/`formatTTK`/`getTTKTier`/`CLV_BADGE`/`SortKey`/`SortDir`/`getSortValue` (those go to `BetHistory` in Task 13) and do NOT move `toSEK`/`RATE_TO_SEK` (`StatsPage.tsx:53-58`) — the retargeted `BankrollChart` no longer needs them (they become dead and are deleted in Task 15). `polyChart` IS kept — `CLVChart` still calls it. Header imports for charts.tsx:

```typescript
import { useMemo } from 'react';
import type { Bet } from '@/types';
import type { EquityPoint } from './equity';
```

Export `CHART`, `BankrollChart`, `CLVChart`. **`CLVChart`'s signature stays unchanged**: `export function CLVChart({ bets, title = 'CLV Trend', recentWindow = 10 }: { bets: Bet[]; showTTKLegend?: boolean; title?: string; recentWindow?: number })`.

- [ ] **Step 2: Retarget `BankrollChart` to equity-curve points**

Change `BankrollChart` to consume pre-computed `EquityPoint[]` instead of reconstructing from `bets`:

```typescript
export function BankrollChart({ points, totalStaked }: { points: EquityPoint[]; totalStaked?: number }) {
  const data = points;
  if (data.length < 2) return null;
  // ... KEEP the existing rendering body verbatim — it already reads `data`
  //     as {date, value}[], which is exactly EquityPoint[]. Delete the old
  //     `useMemo` that built `data` from `bets`/`currentBankroll`/toSEK.
```

Relabel the chart title text from `Bankroll` to `Bankroll (realized P/L)`.

- [ ] **Step 3: Create KpiBlock with its FINAL signature**

`frontend/src/components/stats/KpiBlock.tsx` (`BankrollStats` already has `total_profit/roi_pct/wins/losses/voids/total_bets/avg_clv/clv_positive_pct/clv_count` — verified; bankroll is passed separately as `bankrollSek`):

```typescript
import type { BankrollStats } from '@/types';

export function KpiBlock({ stats, bankrollSek }: { stats: BankrollStats; bankrollSek: number }) {
  return (
    <div className="border-l-2 border-tabBets">
      <div className="grid grid-cols-5 gap-px bg-border border border-border">
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Net Profit</div>
          <div className={`text-lg font-semibold ${stats.total_profit >= 0 ? 'text-success' : 'text-error'}`}>
            {stats.total_profit >= 0 ? '+' : ''}{stats.total_profit.toFixed(0)} kr
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">ROI</div>
          <div className={`text-lg font-semibold ${stats.roi_pct >= 0 ? 'text-success' : 'text-error'}`}>
            {stats.roi_pct >= 0 ? '+' : ''}{stats.roi_pct.toFixed(1)}%
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Bets</div>
          <div className="text-text text-lg font-semibold">{stats.total_bets}</div>
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-success">{stats.wins}W</span>
            <span className="text-error">{stats.losses}L</span>
            <span className="text-muted">{stats.voids}V</span>
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Avg CLV</div>
          {stats.clv_count > 0 ? (
            <>
              <div className={`text-lg font-semibold ${stats.avg_clv >= 0 ? 'text-success' : 'text-error'}`}>
                {stats.avg_clv >= 0 ? '+' : ''}{stats.avg_clv.toFixed(1)}%
              </div>
              <div className="text-[10px] text-muted">{stats.clv_positive_pct.toFixed(0)}% beat close</div>
            </>
          ) : (
            <div className="text-lg font-semibold text-muted">-</div>
          )}
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Bankroll</div>
          <div className="text-text text-lg font-semibold">{bankrollSek.toFixed(0)} kr</div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Keep the tree compiling**

In `StatsPage.tsx`, delete the moved chart definitions and add at the top: `import { BankrollChart, CLVChart, CHART } from '@/components/stats/charts';`. The old `BetsPage` still references `BankrollChart`/`CLVChart` with the OLD props — temporarily comment out ONLY those two JSX usages in `BetsPage` (they're fully replaced in Task 15) to keep `tsc` green, or leave them and accept the type error until Task 15 (subagent-driven execution reviews per task — note the expected error). Prefer commenting the two usages with `{/* replaced in Task 15 */}`.

Run: `cd frontend && npx tsc -b`
Expected: no errors after commenting the two old chart usages.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/stats/charts.tsx frontend/src/components/stats/KpiBlock.tsx frontend/src/pages/StatsPage.tsx
git commit -m "refactor(stats): extract charts; equity-curve-fed BankrollChart; KpiBlock"
```

---

### Task 10: StrategySplit (Personal)

**Files:**
- Create: `frontend/src/components/stats/StrategySplit.tsx`

- [ ] **Step 1: Implement** (`AnalyticsBucket` is already exported + has `clv_positive_pct` from Task 7)

`frontend/src/components/stats/StrategySplit.tsx`:

```typescript
import type { AnalyticsBucket } from '@/services/api/bets';
import { LANE_ORDER } from './lanes';

export function StrategySplit({ byStrategy }: { byStrategy: Record<string, AnalyticsBucket> }) {
  const rows = LANE_ORDER.map((lane) => [lane, byStrategy[lane]] as const).filter(([, v]) => v != null);
  if (rows.length === 0) return null;
  return (
    <div className="border border-border bg-panel2 overflow-hidden">
      <div className="px-2 py-1 text-[10px] text-muted uppercase tracking-wider bg-bg border-b border-border">
        Strategy split
      </div>
      <table className="w-full text-[11px] font-mono">
        <thead className="bg-bg/50">
          <tr>
            <th className="px-2 py-1 text-left">lane</th>
            <th className="px-2 py-1 text-right">n</th>
            <th className="px-2 py-1 text-right">win%</th>
            <th className="px-2 py-1 text-right">staked</th>
            <th className="px-2 py-1 text-right">profit</th>
            <th className="px-2 py-1 text-right">ROI%</th>
            <th className="px-2 py-1 text-right">CLV%</th>
            <th className="px-2 py-1 text-right">beat%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([lane, v]) => v && (
            <tr key={lane} className="border-t border-border/50 hover:bg-bg/30">
              <td className="px-2 py-1">{lane}</td>
              <td className="px-2 py-1 text-right">{v.n}</td>
              <td className="px-2 py-1 text-right">{v.win_pct ?? '-'}</td>
              <td className="px-2 py-1 text-right text-muted">{v.staked.toFixed(0)}</td>
              <td className={`px-2 py-1 text-right ${v.profit >= 0 ? 'text-success' : 'text-error'}`}>
                {v.profit >= 0 ? '+' : ''}{v.profit.toFixed(0)}
              </td>
              <td className={`px-2 py-1 text-right ${(v.roi_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                {v.roi_pct?.toFixed(1) ?? '-'}
              </td>
              <td className={`px-2 py-1 text-right ${(v.avg_clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                {v.avg_clv_pct?.toFixed(1) ?? '-'}
              </td>
              <td className="px-2 py-1 text-right text-muted">{v.clv_positive_pct?.toFixed(0) ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd frontend && npx tsc -b` (no NEW errors). Then:

```bash
git add frontend/src/components/stats/StrategySplit.tsx
git commit -m "feat(stats): StrategySplit lane table (Personal)"
```

---

### Task 11: Move EdgeAnalytics (Personal)

**Files:**
- Create: `frontend/src/components/stats/EdgeAnalytics.tsx`

- [ ] **Step 1: Extract**

Create `frontend/src/components/stats/EdgeAnalytics.tsx` exporting `EdgeAnalytics({ analytics })`. Locate in `StatsPage.tsx` the block headed by the "Realized vs Displayed Edge (90d)" `<h3>` and its three tables (per-sport, per-edge-bucket, sport×market Kelly-confidence) — currently ~`StatsPage.tsx:730-913`. Move that JSX in. Replace the internal `analyticsProvider`/`activeAnalytics` all/polymarket toggle with the passed `analytics` prop (drop the toggle — the page is now profile-scoped). Keep collapse local:

```typescript
import { useState } from 'react';
import type { api } from '@/services/api';

type Analytics = Awaited<ReturnType<typeof api.getAnalytics>>;

export function EdgeAnalytics({ analytics }: { analytics: Analytics }) {
  const [collapsed, setCollapsed] = useState(true);
  // header button toggling `collapsed`; then the three tables reading
  // analytics.by_sport, analytics.by_edge_bucket, analytics.by_sport_and_market.
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd frontend && npx tsc -b` (no new errors). Then:

```bash
git add frontend/src/components/stats/EdgeAnalytics.tsx
git commit -m "refactor(stats): extract EdgeAnalytics (per-sport/edge-bucket, profile-scoped)"
```

---

### Task 12: BonusPanel (Bonus) — value captured + sharp P/L + bonus status

**Files:**
- Create: `frontend/src/components/stats/BonusPanel.tsx`

- [ ] **Step 1: Implement** (combines `by_provider` profit, the `/profiles/{id}/bonus-statuses` data, sharp-pool P/L, and the existing `BonusArbTracker`)

`frontend/src/components/stats/BonusPanel.tsx`:

```typescript
import { useQuery } from '@tanstack/react-query';
import type { AnalyticsBucket } from '@/services/api/bets';
import { api } from '@/services/api';
import { ProviderName } from '@/components/ProviderName';
import { BonusArbTracker } from '@/components/BonusArbTracker';

const UNLIMITED = new Set(['pinnacle', 'cloudbet', 'kalshi', 'polymarket']);

export function BonusPanel({ profileId, byProvider }: { profileId: number; byProvider: Record<string, AnalyticsBucket> }) {
  const { data: bonus } = useQuery({
    queryKey: ['profile', 'bonus-statuses', profileId],
    queryFn: () => api.getProfileBonusStatuses(profileId),
    staleTime: 60_000,
  });

  const providers = Object.entries(byProvider)
    .filter(([, v]) => v != null)
    .sort(([, a], [, b]) => (b?.profit ?? 0) - (a?.profit ?? 0));
  const sharpPnl = providers.filter(([pid]) => UNLIMITED.has(pid)).reduce((s, [, v]) => s + (v?.profit ?? 0), 0);

  return (
    <div className="space-y-2">
      <div className="border border-border bg-panel2 overflow-hidden">
        <div className="px-2 py-1 text-[10px] text-muted uppercase tracking-wider bg-bg border-b border-border flex justify-between">
          <span>Per-provider bonus + value captured</span>
          <span className={sharpPnl >= 0 ? 'text-success' : 'text-error'}>
            Sharp-side P/L: {sharpPnl >= 0 ? '+' : ''}{sharpPnl.toFixed(0)} kr
          </span>
        </div>
        <table className="w-full text-[11px] font-mono">
          <thead className="bg-bg/50">
            <tr>
              <th className="px-2 py-1 text-left">provider</th>
              <th className="px-2 py-1 text-left">bonus</th>
              <th className="px-2 py-1 text-right">wager%</th>
              <th className="px-2 py-1 text-right">days</th>
              <th className="px-2 py-1 text-right">staked</th>
              <th className="px-2 py-1 text-right">value</th>
              <th className="px-2 py-1 text-right">ROI%</th>
            </tr>
          </thead>
          <tbody>
            {providers.map(([pid, v]) => {
              const b = bonus?.[pid];
              return v && (
                <tr key={pid} className="border-t border-border/50 hover:bg-bg/30">
                  <td className="px-2 py-1"><ProviderName name={pid} /></td>
                  <td className="px-2 py-1 text-muted">{b ? `${b.bonus_type ?? '-'} · ${b.status}` : '-'}</td>
                  <td className="px-2 py-1 text-right text-muted">{b && b.progress_pct != null ? `${b.progress_pct.toFixed(0)}%` : '-'}</td>
                  <td className="px-2 py-1 text-right text-muted">{b?.days_remaining ?? '-'}</td>
                  <td className="px-2 py-1 text-right text-muted">{v.staked.toFixed(0)}</td>
                  <td className={`px-2 py-1 text-right ${v.profit >= 0 ? 'text-success' : 'text-error'}`}>
                    {v.profit >= 0 ? '+' : ''}{v.profit.toFixed(0)}
                  </td>
                  <td className={`px-2 py-1 text-right ${(v.roi_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                    {v.roi_pct?.toFixed(1) ?? '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <BonusArbTracker />
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd frontend && npx tsc -b` (no new errors). Then:

```bash
git add frontend/src/components/stats/BonusPanel.tsx
git commit -m "feat(stats): BonusPanel — per-provider bonus status, value captured, sharp-side P/L"
```

---

### Task 13: Move BetHistory (shared)

**Files:**
- Create: `frontend/src/components/stats/BetHistory.tsx`

- [ ] **Step 1: Extract**

Create `frontend/src/components/stats/BetHistory.tsx` exporting `BetHistory({ bets, isLoading, refetch })` (NO `search` prop — the search box is internal). Move from `StatsPage.tsx`: `getTTK`/`formatTTK`/`TTKConfidence`/`getTTKTier`/`CLV_BADGE` (`:17-48`), `SortKey`/`SortDir`/`getSortValue` (`:60-81`), `SortHeader` (`:375-405`), the history table block (`:915-1147`), and the edit/cashout handlers + their local state. Keep `useBetMutations` + local sort/search/expanded/edit state internal. Header imports:

```typescript
import { Fragment, useState, useMemo } from 'react';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useBetMutations } from '@/hooks/useBetMutations';
import { displayTeamName } from '@/utils/formatters';
import { resolveOutcome as resolveOutcomeBase, fmtAmount, fmtProfit } from '@/utils/betting';
import { ProviderName } from '@/components/ProviderName';
import type { Bet } from '@/types';

export type SortDir = 'asc' | 'desc';

export function BetHistory({ bets, isLoading, refetch }: { bets: Bet[]; isLoading: boolean; refetch: () => void }) {
  // moved code; `historyBets` memo applies internal search + sort over `bets`.
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd frontend && npx tsc -b` (no new errors). Then:

```bash
git add frontend/src/components/stats/BetHistory.tsx
git commit -m "refactor(stats): extract BetHistory (table + sort + inline edit/cashout)"
```

---

### Task 14: Move ShadowCLV (scanner-quality, profile-independent)

**Files:**
- Create: `frontend/src/components/stats/ShadowCLV.tsx`

- [ ] **Step 1: Extract the FULL shadow block (1157–1487, incl. SportBlendTable)**

Create `frontend/src/components/stats/ShadowCLV.tsx` and move `TYPE_COLOR` (@1157), `ShadowCLVView` (@1163), `ShadowSummary` (@1186), `MultiLineCLVChart` (@1225), `ShadowSortKey` (@1335), `BreakdownTable` (@1337), `getShadowSortVal` (@1422), and **`SportBlendTable` (@1434)** — `ShadowCLVView` renders `<SportBlendTable rows={data.sport_blend_comparison} />`, so it MUST come along. Export `ShadowCLVView` (it takes **no props**). Imports:

```typescript
import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { ProviderName } from '@/components/ProviderName';
import { CHART } from './charts';
import type {
  OppSnapshotBreakdownRow, OppSnapshotHistoryPoint, OppSnapshotSummary, SportBlendComparisonRow,
} from '@/services/api/oppSnapshots';

type SortDir = 'asc' | 'desc';
```

(If `SportBlendComparisonRow` lives elsewhere, follow its current import in `StatsPage.tsx`.)

- [ ] **Step 2: Typecheck + commit**

Run: `cd frontend && npx tsc -b` (no new errors). Then:

```bash
git add frontend/src/components/stats/ShadowCLV.tsx
git commit -m "refactor(stats): extract ShadowCLV (scanner-quality, profile-independent)"
```

---

### Task 15: Rewrite the StatsPage shell

**Files:**
- Modify: `frontend/src/pages/StatsPage.tsx`

- [ ] **Step 1: Replace StatsPage with the shell**

Delete all now-moved code and the dead `toSEK`/`RATE_TO_SEK`. Replace `frontend/src/pages/StatsPage.tsx` with:

```typescript
import { useState, useEffect, useMemo } from 'react';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useProfiles } from '@/hooks/useProfiles';
import { useStatsData, type StatsRange, RANGE_DAYS } from '@/hooks/useStatsData';
import { toEquityPoints } from '@/components/stats/equity';
import { TabIcon, TAB_COLORS } from '@/components/TabBar';
import { StatsHeader } from '@/components/stats/StatsHeader';
import { KpiBlock } from '@/components/stats/KpiBlock';
import { BankrollChart, CLVChart } from '@/components/stats/charts';
import { StrategySplit } from '@/components/stats/StrategySplit';
import { EdgeAnalytics } from '@/components/stats/EdgeAnalytics';
import { BonusPanel } from '@/components/stats/BonusPanel';
import { BetHistory } from '@/components/stats/BetHistory';
import { ShadowCLVView } from '@/components/stats/ShadowCLV';

export function StatsPage() {
  const { activeProfile, profiles } = useProfiles();
  // Bump persisted key: legacy stored value was 'bets'|'shadow'; new union is 'profile'|'shadow'.
  const [subTab, setSubTab] = usePersistedState<'profile' | 'shadow'>('bbq_stats_subTab_v2', 'profile');
  const [profileId, setProfileId] = useState<number | undefined>(undefined);
  const [range, setRange] = usePersistedState<StatsRange>('bbq_stats_range', '90d');

  useEffect(() => {
    if (profileId == null && activeProfile) setProfileId(activeProfile.id);
  }, [activeProfile, profileId]);

  const selected = profiles.find((p) => p.id === profileId);
  const { stats, equity, analytics, bets } = useStatsData(profileId, range);

  const equityPoints = equity.data ? toEquityPoints(equity.data.points, equity.data) : [];

  // Range filters the history list (spec §6) — KPIs + curve stay all-time.
  const historyBets = useMemo(() => {
    const all = bets.data?.bets ?? [];
    if (range === 'all') return all;
    const cutoff = Date.now() - RANGE_DAYS[range] * 86400_000;
    return all.filter((b) => new Date(b.placed_at).getTime() >= cutoff);
  }, [bets.data, range]);

  return (
    <div className="space-y-3 min-w-0 overflow-y-auto overflow-x-hidden flex-1 min-h-0">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="stats" color={TAB_COLORS.stats} size={16} />
          Stats
        </h2>
      </div>

      <div className="flex items-center gap-1 -mx-1 -mt-1">
        <button onClick={() => setSubTab('profile')}
          className={`px-3 py-1 text-[11px] font-semibold uppercase tracking-wider rounded ${subTab === 'profile' ? 'bg-tabBets/30 text-tabBets border border-tabBets/40' : 'text-muted hover:text-text border border-transparent'}`}>
          Profile Stats
        </button>
        <button onClick={() => setSubTab('shadow')}
          title="Scanner CLV for every detected opp (profile-independent)"
          className={`px-3 py-1 text-[11px] font-semibold uppercase tracking-wider rounded ${subTab === 'shadow' ? 'bg-tabBets/30 text-tabBets border border-tabBets/40' : 'text-muted hover:text-text border border-transparent'}`}>
          Shadow CLV
        </button>
      </div>

      {subTab === 'shadow' && <ShadowCLVView />}

      {subTab === 'profile' && (
        <>
          <StatsHeader profileId={profileId} setProfileId={setProfileId} range={range} setRange={setRange} />

          {stats.data && <KpiBlock stats={stats.data} bankrollSek={equity.data?.current_bankroll_sek ?? 0} />}

          <div className="grid grid-cols-2 gap-[1px] bg-[#161b22]">
            {equityPoints.length >= 2 && (
              <BankrollChart points={equityPoints} totalStaked={equity.data?.total_staked_sek} />
            )}
            {bets.data && <CLVChart bets={bets.data.bets.filter((b) => !b.is_bonus)} />}
          </div>

          {selected?.style === 'bonus_extraction' ? (
            (analytics.data && profileId != null) && <BonusPanel profileId={profileId} byProvider={analytics.data.by_provider} />
          ) : (
            <>
              {analytics.data && <StrategySplit byStrategy={analytics.data.by_strategy} />}
              {analytics.data && <EdgeAnalytics analytics={analytics.data} />}
            </>
          )}

          {bets.data && (
            <BetHistory bets={historyBets} isLoading={bets.isLoading} refetch={() => bets.refetch()} />
          )}
        </>
      )}
    </div>
  );
}
```

Keep the named export (App.tsx imports `{ StatsPage }`). Remove the old `export { BetsPage as StatsPage };` line. Do NOT add a default export.

- [ ] **Step 2: Typecheck + lint + unit tests**

Run: `cd frontend && npx tsc -b && npm run lint && npx vitest run src/components/stats`
Expected: no type errors, lint clean, vitest PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/StatsPage.tsx
git commit -m "refactor(stats): profile-scoped shell with style-adaptive layout"
```

---

### Task 16: Style toggle in ProfileSelector create form

**Files:**
- Modify: `frontend/src/components/ProfileSelector.tsx`

- [ ] **Step 1: Add a style choice to creation**

Add `const [newStyle, setNewStyle] = useState<'personal' | 'bonus_extraction'>('personal');`, pass it in `create.mutateAsync({ name, style: newStyle })`, and add to the create `<form>` (next to the name input):

```typescript
<select value={newStyle} onChange={(e) => setNewStyle(e.target.value as 'personal' | 'bonus_extraction')}
  className="px-1 py-1 bg-panel2 border border-border text-text text-[10px]">
  <option value="personal">Personal</option>
  <option value="bonus_extraction">Bonus</option>
</select>
```

(`ProfileCreate` already has `style` from Task 7; `useProfiles.create` takes `ProfileCreate`.)

- [ ] **Step 2: Typecheck + lint + commit**

Run: `cd frontend && npx tsc -b && npm run lint`. Then:

```bash
git add frontend/src/components/ProfileSelector.tsx
git commit -m "feat(profiles): choose account style when creating a profile"
```

---

## PHASE 5 — Verification

### Task 17: Full verification + manual checklist

- [ ] **Step 1: Backend suite**

Run: `cd backend && pytest tests/test_stats_profile_styles.py tests/test_bet_repo.py tests/test_bankroll_service_get_stats.py tests/test_profiles_route_seeding.py -v`
Expected: all PASS.

- [ ] **Step 2: Backend lint**

Run: `cd backend && ruff check src/ && ruff format --check src/`
Expected: clean (fix findings).

- [ ] **Step 3: Frontend typecheck + lint + unit + build**

Run: `cd frontend && npx tsc -b && npm run lint && npm test && npm run build`
Expected: clean; vitest PASS; build succeeds.

- [ ] **Step 4: Manual verification (`betty.bat` or Claude Preview)**

- Header shows the selected profile + style badge; switching the picker changes all numbers but NOT the Play tab's active provider highlight.
- KPI **Bankroll** equals the **end of the bankroll curve**; KPI **Net Profit** equals the curve's total rise (incl. a profile with bonus bets — both exclude bonus).
- Toggling the style badge flips Personal layout (StrategySplit + EdgeAnalytics) ↔ Bonus layout (BonusPanel w/ bonus status + sharp-side P/L).
- Range control updates StrategySplit/EdgeAnalytics/history but NOT KPI block or bankroll curve.
- "Shadow CLV" sub-tab renders the scanner view (incl. SportBlendTable) unchanged.

- [ ] **Step 5: Finalize**

Use `superpowers:finishing-a-development-branch` to open a PR. PR body MUST note: **touches `backend/` → run `server-deploy.sh rebuild backend` after merge** (spec §10); frontend ships via `betty.bat`.

---

## Self-Review Notes

- **Spec coverage:** §4 → Tasks 1–2; §5.1 → Task 3; §5.2 → Task 4; §5.3 → Task 5; §5.4 → Task 6; §5.5/§7 bonus columns → Task 6b + Task 12 (no longer deferred); §6 decomposition → Tasks 8–16; §6 range-filters-history → Task 15 Step 1; §7 layout → Task 15; §8 correctness (curve==KPI, bonus agreement) → Tasks 6/9/15; §9 testing → Tasks 1–17.
- **Type consistency:** `AnalyticsBucket` gains `clv_positive_pct` and is `export`ed in **Task 7** (consumed by Tasks 10/12). `EquityPoint` (Task 8) produced by `toEquityPoints`, consumed by `BankrollChart` (Task 9) + shell (Task 15). `KpiBlock({stats, bankrollSek})` final signature authored in Task 9 — matches the only caller (Task 15). `CLVChart({bets})` signature explicitly unchanged. `StatsRange`/`RANGE_DAYS` defined in Task 8, used by `StatsHeader` + shell.
- **Verified-fact pins** (top of plan) remove every false-positive flagged in review: `get_total_bankroll` exists; `get_stats` already returns `profile_id`+clv fields; `BankrollStats` already typed; `summarize` keys + loop var `bets` confirmed; `updateProfile` is PUT; TabBar exports; `/bankroll/status` exists; alembic numeric convention.
- **Compile-between-tasks:** extraction Tasks 9/11/13/14 keep `tsc` green via re-export imports / commenting the two old chart usages; Task 15 does the final rewrite. Noted in Task 9 Step 4.
