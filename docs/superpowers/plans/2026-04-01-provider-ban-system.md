# Provider Ban System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a bookmaker closes/bans an account, record it and automatically exclude that provider from opportunities, capital allocation, bet placement, and extraction.

**Architecture:** Add a `ban_provider()` method to `LimitService` that atomically records a `fully_banned` limit (level 5) and disables extraction. Add a `get_banned_providers()` helper to `LimitRepo` used by OpportunityService, ProviderAllocator, and BetService to filter banned providers. Add a dedicated `POST /api/limits/ban` endpoint.

**Tech Stack:** Python / FastAPI / SQLAlchemy / SQLite

---

### Task 1: Add `get_banned_providers()` to LimitRepo

**Files:**
- Modify: `backend/src/repositories/limit_repo.py:19-29`
- Test: `backend/tests/test_ban_system.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ban_system.py
"""Tests for provider ban system."""

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Profile, Provider, ProfileProviderLimit, ProviderExtractionSetting
from src.repositories.limit_repo import LimitRepo


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # Seed: one profile, two providers
    session.add(Profile(id=1, name="test", is_active=True, bankroll=10000, currency="SEK"))
    session.add(Provider(id="coolbet", name="Coolbet"))
    session.add(Provider(id="snabbare", name="Snabbare"))
    session.add(Provider(id="unibet", name="Unibet"))
    session.commit()
    yield session
    session.close()


class TestGetBannedProviders:
    def test_no_bans_returns_empty(self, db: Session):
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_fully_banned_level5_returned(self, db: Session):
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="fully_banned", limit_level=5,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == {"coolbet"}

    def test_level4_not_banned(self, db: Session):
        """Only level 5 (account closed) counts as banned."""
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="fully_banned", limit_level=4,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_stake_limited_not_banned(self, db: Session):
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="stake_limited", limit_level=5,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_multiple_bans(self, db: Session):
        for pid in ("coolbet", "snabbare"):
            db.add(ProfileProviderLimit(
                profile_id=1, provider_id=pid,
                limit_type="fully_banned", limit_level=5,
                detected_at=datetime.now(timezone.utc),
            ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == {"coolbet", "snabbare"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestGetBannedProviders -v`
Expected: FAIL with `AttributeError: 'LimitRepo' object has no attribute 'get_banned_providers'`

- [ ] **Step 3: Implement `get_banned_providers()`**

Add to `backend/src/repositories/limit_repo.py` after the `list_limits` method:

```python
def get_banned_providers(self, profile_id: int) -> set[str]:
    """Get provider IDs where account is closed (fully_banned, level 5)."""
    rows = self.db.query(ProfileProviderLimit.provider_id).filter(
        ProfileProviderLimit.profile_id == profile_id,
        ProfileProviderLimit.limit_type == "fully_banned",
        ProfileProviderLimit.limit_level == 5,
    ).all()
    return {r[0] for r in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestGetBannedProviders -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/limit_repo.py backend/tests/test_ban_system.py
git commit -m "feat(limits): add get_banned_providers() to LimitRepo"
```

---

### Task 2: Add `ban_provider()` to LimitService

Records the ban + disables extraction in one call.

**Files:**
- Modify: `backend/src/services/limit_service.py:99-143`
- Modify: `backend/tests/test_ban_system.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_ban_system.py`:

```python
from src.services.limit_service import LimitService


class TestBanProvider:
    def test_ban_records_limit_and_disables_extraction(self, db: Session):
        service = LimitService(db)
        result = service.ban_provider(
            profile_id=1,
            provider_id="coolbet",
            notes="Account closed — Coolbet dialog",
        )
        assert result["success"] is True

        # Verify limit recorded
        repo = LimitRepo(db)
        assert "coolbet" in repo.get_banned_providers(profile_id=1)

        # Verify extraction disabled
        setting = db.query(ProviderExtractionSetting).filter(
            ProviderExtractionSetting.profile_id == 1,
            ProviderExtractionSetting.provider_id == "coolbet",
        ).first()
        assert setting is not None
        assert setting.enabled is False

    def test_ban_already_banned_returns_error(self, db: Session):
        service = LimitService(db)
        service.ban_provider(profile_id=1, provider_id="coolbet")
        result = service.ban_provider(profile_id=1, provider_id="coolbet")
        assert result["success"] is False
        assert "already" in result["error"].lower()

    def test_ban_invalid_provider_returns_error(self, db: Session):
        service = LimitService(db)
        result = service.ban_provider(profile_id=1, provider_id="nonexistent")
        assert result["success"] is False

    def test_ban_updates_existing_extraction_setting(self, db: Session):
        """If extraction setting already exists as enabled, flip it to False."""
        db.add(ProviderExtractionSetting(
            profile_id=1, provider_id="coolbet", enabled=True
        ))
        db.commit()
        service = LimitService(db)
        result = service.ban_provider(profile_id=1, provider_id="coolbet")
        assert result["success"] is True

        setting = db.query(ProviderExtractionSetting).filter(
            ProviderExtractionSetting.profile_id == 1,
            ProviderExtractionSetting.provider_id == "coolbet",
        ).first()
        assert setting.enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestBanProvider -v`
Expected: FAIL with `AttributeError: 'LimitService' object has no attribute 'ban_provider'`

- [ ] **Step 3: Implement `ban_provider()`**

Add to `backend/src/services/limit_service.py` after the `record_limit` method (around line 143):

```python
def ban_provider(
    self,
    profile_id: int,
    provider_id: str,
    notes: str | None = None,
) -> dict:
    """Ban a provider: record fully_banned limit (level 5) + disable extraction."""
    # Record the limit (reuse existing logic)
    result = self.record_limit(
        profile_id=profile_id,
        provider_id=provider_id,
        limit_type="fully_banned",
        limit_level=5,
        notes=notes,
    )
    if not result["success"]:
        return result

    # Disable extraction for this profile+provider
    from ..db.models import ProviderExtractionSetting
    existing = self.db.query(ProviderExtractionSetting).filter(
        ProviderExtractionSetting.profile_id == profile_id,
        ProviderExtractionSetting.provider_id == provider_id,
    ).first()
    if existing:
        existing.enabled = False
    else:
        self.db.add(ProviderExtractionSetting(
            profile_id=profile_id,
            provider_id=provider_id,
            enabled=False,
        ))
    self.db.commit()

    logger.info("Banned provider %s for profile %d — extraction disabled", provider_id, profile_id)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestBanProvider -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/limit_service.py backend/tests/test_ban_system.py
git commit -m "feat(limits): ban_provider() records limit + disables extraction"
```

---

### Task 3: Add `POST /api/limits/ban` and `DELETE /api/limits/ban/{provider_id}` endpoints

**Files:**
- Modify: `backend/src/api/routes/limits.py`
- Modify: `backend/src/api/schemas.py`

- [ ] **Step 1: Add schema**

Add to `backend/src/api/schemas.py` after `LimitUpdate` (around line 281):

```python
class BanProviderRequest(BaseModel):
    provider_id: str
    notes: Optional[str] = None
```

- [ ] **Step 2: Add ban endpoint**

Add to `backend/src/api/routes/limits.py` after the existing routes:

```python
from ..schemas import LimitCreate, LimitUpdate, BanProviderRequest


@router.post("/ban")
def ban_provider(data: BanProviderRequest, db: Session = Depends(get_db)):
    """Ban a provider — records fully_banned limit (level 5) and disables extraction."""
    profile = _get_active_profile(db)
    service = LimitService(db)
    result = service.ban_provider(
        profile_id=profile.id,
        provider_id=data.provider_id,
        notes=data.notes,
    )
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result
```

- [ ] **Step 3: Add unban endpoint**

Add to `backend/src/api/routes/limits.py`:

```python
from ...db.models import Profile, ProfileProviderLimit, ProviderExtractionSetting


@router.delete("/ban/{provider_id}")
def unban_provider(provider_id: str, db: Session = Depends(get_db)):
    """Unban a provider — removes fully_banned limit and re-enables extraction."""
    profile = _get_active_profile(db)

    limit = db.query(ProfileProviderLimit).filter(
        ProfileProviderLimit.profile_id == profile.id,
        ProfileProviderLimit.provider_id == provider_id,
        ProfileProviderLimit.limit_type == "fully_banned",
        ProfileProviderLimit.limit_level == 5,
    ).first()
    if not limit:
        raise HTTPException(404, f"No ban found for {provider_id}")

    db.delete(limit)

    # Re-enable extraction
    setting = db.query(ProviderExtractionSetting).filter(
        ProviderExtractionSetting.profile_id == profile.id,
        ProviderExtractionSetting.provider_id == provider_id,
    ).first()
    if setting:
        setting.enabled = True

    db.commit()
    return {"success": True, "provider_id": provider_id}
```

- [ ] **Step 4: Update import in limits.py**

Ensure the imports at the top of `backend/src/api/routes/limits.py` include:

```python
from ...db.models import Profile, ProfileProviderLimit, ProviderExtractionSetting
from ..schemas import LimitCreate, LimitUpdate, BanProviderRequest
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/limits.py backend/src/api/schemas.py
git commit -m "feat(api): add POST /api/limits/ban and DELETE /api/limits/ban/{provider_id}"
```

---

### Task 4: Filter banned providers from opportunities

**Files:**
- Modify: `backend/src/services/opportunity_service.py:65-105`
- Modify: `backend/tests/test_ban_system.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_ban_system.py`:

```python
from unittest.mock import MagicMock, patch
from src.db.models import Opportunity, Event


class TestOpportunityBanFiltering:
    def test_banned_provider_excluded_from_opportunities(self, db: Session):
        """Opportunities with banned provider in provider1_id should be excluded."""
        # Create event + opportunity
        event = Event(
            id="football:teamA:teamB:2026-04-05",
            sport="football", home_team="teamA", away_team="teamB",
        )
        db.add(event)
        db.add(Opportunity(
            event_id=event.id, type="value", market="1x2", outcome1="1",
            provider1_id="coolbet", provider2_id="pinnacle",
            odds1=2.5, odds2=2.0, edge_pct=5.0, is_active=True,
        ))
        db.add(Opportunity(
            event_id=event.id, type="value", market="1x2", outcome1="1",
            provider1_id="unibet", provider2_id="pinnacle",
            odds1=2.3, odds2=2.0, edge_pct=3.0, is_active=True,
        ))
        db.commit()

        # Ban coolbet
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="fully_banned", limit_level=5,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()

        from src.services.opportunity_service import OpportunityService
        service = OpportunityService(db)
        result = service.list_opportunities()

        provider_ids = [o["provider1_id"] for o in result["opportunities"]]
        assert "coolbet" not in provider_ids
        assert "unibet" in provider_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestOpportunityBanFiltering -v`
Expected: FAIL — coolbet opportunities still returned

- [ ] **Step 3: Add ban filtering to OpportunityService.list_opportunities()**

In `backend/src/services/opportunity_service.py`, add the ban filter after fetching rows (around line 105, after the `rows = self.opp_repo.find_active(...)` call):

```python
from ..repositories.limit_repo import LimitRepo

# ... inside list_opportunities(), after rows = self.opp_repo.find_active(...)

# Exclude banned providers
try:
    if not profile:
        profile = self.profile_repo.get_active()
    banned = LimitRepo(self.db).get_banned_providers(profile.id)
    if banned:
        rows = [(opp, evt) for opp, evt in rows if opp.provider1_id not in banned]
except Exception:
    banned = set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestOpportunityBanFiltering -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/opportunity_service.py backend/tests/test_ban_system.py
git commit -m "feat(opportunities): filter banned providers from opportunity list"
```

---

### Task 5: Hard-block banned providers in ProviderAllocator

**Files:**
- Modify: `backend/src/risk/allocator.py:151-225`
- Modify: `backend/tests/test_ban_system.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_ban_system.py`:

```python
from src.risk.allocator import ProviderAllocator


class TestAllocatorBanBlock:
    def test_banned_provider_gets_negative_score(self, db: Session):
        """Banned providers should get score -1 (same as capped)."""
        # Ban coolbet
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="fully_banned", limit_level=5,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()

        allocator = ProviderAllocator(db, profile_id=1)
        allocator.preload_limits()
        result = allocator.score_provider("coolbet")
        assert result.score == -1
        assert result.is_capped is True
        assert "banned" in result.reason.lower()

    def test_unbanned_provider_gets_normal_score(self, db: Session):
        allocator = ProviderAllocator(db, profile_id=1)
        allocator.preload_limits()
        result = allocator.score_provider("unibet")
        assert result.score >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestAllocatorBanBlock -v`
Expected: FAIL — banned provider still gets positive score

- [ ] **Step 3: Add ban check to `score_provider()`**

In `backend/src/risk/allocator.py`, add early return at the start of `score_provider()` (line ~160, after `group_bets` and `cap` but before scoring):

```python
# --- Banned check (level 5 = account closed) ---
limit_level = self._limits.get(provider_id, 0)
if limit_level >= 5:
    return AllocationResult(
        provider_id=provider_id,
        score=-1,
        reason="Banned — account closed",
        daily_bets_group=group_bets,
        daily_cap=cap,
        is_capped=True,
        wagering_remaining=0,
        edge_routing=None,
    )
```

Also remove the later `limit_level = self._limits.get(provider_id, 0)` line (~193) since it's now set earlier.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestAllocatorBanBlock -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/risk/allocator.py backend/tests/test_ban_system.py
git commit -m "feat(allocator): hard-block banned providers with score -1"
```

---

### Task 6: Gate bet placement on banned providers

**Files:**
- Modify: `backend/src/services/bet_service.py:48-75`
- Modify: `backend/tests/test_ban_system.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_ban_system.py`:

```python
from src.services.bet_service import BetService


class TestBetServiceBanGate:
    def test_bet_on_banned_provider_rejected(self, db: Session):
        # Ban coolbet
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="fully_banned", limit_level=5,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()

        service = BetService(db)
        result = service.create_bet(
            event_id=None,
            provider_id="coolbet",
            market="1x2",
            outcome="1",
            odds=2.5,
            stake=100,
        )
        assert "error" in result
        assert "banned" in result["error"].lower()

    def test_bet_on_active_provider_allowed(self, db: Session):
        # Give unibet balance so it passes balance check
        from src.db.models import ProfileProviderBalance
        db.add(ProfileProviderBalance(
            profile_id=1, provider_id="unibet", balance=1000
        ))
        db.commit()

        service = BetService(db)
        result = service.create_bet(
            event_id=None,
            provider_id="unibet",
            market="1x2",
            outcome="1",
            odds=2.5,
            stake=100,
        )
        assert "error" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestBetServiceBanGate -v`
Expected: FAIL — bet on banned provider goes through

- [ ] **Step 3: Add ban check to `create_bet()`**

In `backend/src/services/bet_service.py`, add after provider existence check (around line 74, after `if not provider: return {"error": ...}`):

```python
# Block bets on banned providers
from ..repositories.limit_repo import LimitRepo
banned = LimitRepo(self.db).get_banned_providers(profile.id)
if provider_id in banned:
    return {"error": f"Provider {provider_id} is banned — account closed"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ban_system.py::TestBetServiceBanGate -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/bet_service.py backend/tests/test_ban_system.py
git commit -m "feat(bets): reject bet placement on banned providers"
```

---

### Task 7: Seed Coolbet + Snabbare bans

Record the two known bans via a one-time data migration.

**Files:**
- Create: `backend/scripts/ban_providers.py`

- [ ] **Step 1: Write the migration script**

```python
# backend/scripts/ban_providers.py
"""One-time script: ban Coolbet and Snabbare for active profile."""

import sys
sys.path.insert(0, ".")

from src.db.models import get_session, Profile
from src.services.limit_service import LimitService


def main():
    session = get_session()
    profile = session.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        print("No active profile found")
        return

    service = LimitService(session)
    for provider_id, notes in [
        ("coolbet", "Account closed — 'Ditt konto är stängt' dialog on login"),
        ("snabbare", "Account closed — blocked from site"),
    ]:
        result = service.ban_provider(
            profile_id=profile.id,
            provider_id=provider_id,
            notes=notes,
        )
        status = "OK" if result["success"] else result.get("error", "unknown error")
        print(f"  {provider_id}: {status}")

    session.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

Run: `cd backend && python scripts/ban_providers.py`
Expected:
```
  coolbet: OK
  snabbare: OK
```

- [ ] **Step 3: Verify via API**

Run: `curl -s http://localhost:8000/api/limits | python -m json.tool`
Expected: Two entries with `limit_type: "fully_banned"` and `limit_level: 5` for coolbet and snabbare.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/ban_providers.py
git commit -m "chore: seed Coolbet + Snabbare bans"
```

---

### Task 8: Run full test suite

- [ ] **Step 1: Run ban system tests**

Run: `cd backend && python -m pytest tests/test_ban_system.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run full test suite for regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30`
Expected: No regressions — all existing tests still PASS

- [ ] **Step 3: Final commit if any fixups needed**

```bash
git add -A
git commit -m "test: verify full ban system integration"
```
