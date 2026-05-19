# Provider Limit Tracking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track bookmaker account limits per profile+provider with auto-snapshotted betting stats, plus global provider limit risk ratings.

**Architecture:** Two layers — global `limit_risk`/`limit_notes` fields on the `Provider` model for known limiting behavior, and a new `ProfileProviderLimit` table for actual per-account limit records with immutable betting snapshots. CRUD API at `/api/limits`, PATCH on providers for global risk. Stats page gets a new provider breakdown section with limit actions.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), React/TypeScript/Tailwind (frontend), SQLite

**Spec:** `docs/superpowers/specs/2026-03-12-provider-limit-tracking-design.md`

---

## Chunk 1: Backend Data Model + Migration

### Task 1: Add LimitRisk/LimitType enums and ProfileProviderLimit model

**Files:**
- Modify: `backend/src/db/models.py`

- [ ] **Step 1: Add enums after existing RiskLevel enum (~line 34)**

Add after the `RiskLevel` enum (line 33):

```python
class LimitRisk(str, Enum):
    """How aggressively a provider is known to limit winners."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INSTANT = "instant"


class LimitType(str, Enum):
    """Type of limit imposed by a bookmaker."""
    STAKE_LIMITED = "stake_limited"
    MARKET_RESTRICTED = "market_restricted"
    ODDS_RESTRICTED = "odds_restricted"
    FULLY_BANNED = "fully_banned"
```

- [ ] **Step 2: Add limit_risk and limit_notes columns to Provider model (~line 92)**

Add after `is_enabled` (line 92):

```python
    # Limit risk (global — how aggressively this provider limits winners)
    limit_risk = Column(String, default="low")      # LimitRisk enum value
    limit_notes = Column(Text, nullable=True)        # Free-form context
```

- [ ] **Step 3: Add ProfileProviderLimit model**

Add after the `ProfileProviderBalance` class (after the balance model, before the Bet Tracking section):

```python
class ProfileProviderLimit(Base):
    """
    Tracks bookmaker-imposed limits per profile+provider.

    Records when a bookmaker limits an account, with an immutable
    snapshot of betting stats at detection time for correlation analysis.
    """
    __tablename__ = "profile_provider_limits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)

    limit_type = Column(String, nullable=False)     # LimitType enum value
    limit_level = Column(Integer, nullable=False)   # 1=minor, 2=moderate, 3=severe, 4=gutted, 5=closed
    detected_at = Column(DateTime, nullable=False, default=_utcnow)
    notes = Column(Text, nullable=True)

    # Immutable betting stats snapshot at detection time
    betting_snapshot = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint('profile_id', 'provider_id', 'limit_type', name='uq_profile_provider_limit_type'),
        Index('ix_limit_profile_provider', 'profile_id', 'provider_id'),
    )

    # Relationships
    profile = relationship("Profile")
    provider = relationship("Provider")
```

- [ ] **Step 4: Add migration for Provider.limit_risk and Provider.limit_notes**

In the `_run_migrations(engine)` function, add at the end (before the final closing):

```python
        # --- Provider limit risk columns ---
        try:
            cursor.execute("SELECT limit_risk FROM providers LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE providers ADD COLUMN limit_risk TEXT DEFAULT 'low'")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        try:
            cursor.execute("SELECT limit_notes FROM providers LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE providers ADD COLUMN limit_notes TEXT")
                raw.commit()
            except sqlite3.OperationalError:
                pass
```

- [ ] **Step 5: Verify model loads**

Run: `cd backend && python -c "from src.db.models import ProfileProviderLimit, LimitRisk, LimitType; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat: add ProfileProviderLimit model and Provider limit_risk columns"
```

---

## Chunk 2: Backend Repository + Service

### Task 2: Create LimitRepo

**Files:**
- Create: `backend/src/repositories/limit_repo.py`
- Modify: `backend/src/repositories/__init__.py`

- [ ] **Step 1: Create limit_repo.py**

```python
"""Limit repository - provider limit data access."""

from sqlalchemy.orm import Session

from ..db.models import ProfileProviderLimit


class LimitRepo:
    """Data access for provider limits."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, limit_id: int) -> ProfileProviderLimit | None:
        return self.db.query(ProfileProviderLimit).filter(
            ProfileProviderLimit.id == limit_id
        ).first()

    def list_limits(
        self,
        profile_id: int | None = None,
        provider_id: str | None = None,
    ) -> list[ProfileProviderLimit]:
        query = self.db.query(ProfileProviderLimit)
        if profile_id is not None:
            query = query.filter(ProfileProviderLimit.profile_id == profile_id)
        if provider_id is not None:
            query = query.filter(ProfileProviderLimit.provider_id == provider_id)
        return query.order_by(ProfileProviderLimit.detected_at.desc()).all()

    def get_existing(
        self, profile_id: int, provider_id: str, limit_type: str
    ) -> ProfileProviderLimit | None:
        return self.db.query(ProfileProviderLimit).filter(
            ProfileProviderLimit.profile_id == profile_id,
            ProfileProviderLimit.provider_id == provider_id,
            ProfileProviderLimit.limit_type == limit_type,
        ).first()

    def create(self, **kwargs) -> ProfileProviderLimit:
        limit = ProfileProviderLimit(**kwargs)
        self.db.add(limit)
        return limit

    def delete(self, limit: ProfileProviderLimit) -> None:
        self.db.delete(limit)
```

- [ ] **Step 2: Register in repositories/__init__.py**

Add import and export:

```python
from .limit_repo import LimitRepo
```

Add `"LimitRepo"` to `__all__`.

- [ ] **Step 3: Commit**

```bash
git add backend/src/repositories/limit_repo.py backend/src/repositories/__init__.py
git commit -m "feat: add LimitRepo for provider limit CRUD"
```

### Task 3: Create LimitService with snapshot generation

**Files:**
- Create: `backend/src/services/limit_service.py`

- [ ] **Step 1: Create limit_service.py**

```python
"""Limit service - provider limit recording with betting snapshot."""

import logging
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import Bet, Provider, Profile, ProfileProviderLimit
from ..repositories import LimitRepo

logger = logging.getLogger(__name__)


class LimitService:
    """Business logic for recording and managing provider limits."""

    def __init__(self, db: Session):
        self.db = db
        self.limit_repo = LimitRepo(db)

    def _build_snapshot(self, profile_id: int, provider_id: str) -> dict:
        """Build betting stats snapshot for a profile+provider."""
        bets = self.db.query(Bet).filter(
            Bet.profile_id == profile_id,
            Bet.provider_id == provider_id,
        ).all()

        if not bets:
            return {
                "total_bets": 0,
                "total_stake": 0.0,
                "total_profit": 0.0,
                "win_rate": None,
                "roi_pct": None,
                "avg_clv_pct": None,
                "avg_odds": None,
                "account_age_days": None,
                "first_bet_date": None,
                "last_bet_date": None,
                "sport_breakdown": {},
                "bet_type_breakdown": {},
                "market_breakdown": {},
                "bonus_bets": 0,
            }

        total_bets = len(bets)
        total_stake = sum(b.stake for b in bets)
        total_profit = sum(b.profit for b in bets)

        settled = [b for b in bets if b.result in ("won", "lost")]
        wins = sum(1 for b in settled if b.result == "won")
        win_rate = wins / len(settled) if settled else None

        roi_pct = (total_profit / total_stake * 100) if total_stake > 0 else None

        clv_values = [b.clv_pct for b in bets if b.clv_pct is not None]
        avg_clv_pct = sum(clv_values) / len(clv_values) if clv_values else None

        avg_odds = sum(b.odds for b in bets) / total_bets

        dates = sorted(b.placed_at for b in bets if b.placed_at)
        first_bet_date = dates[0].isoformat() if dates else None
        last_bet_date = dates[-1].isoformat() if dates else None
        account_age_days = (datetime.now(timezone.utc) - dates[0]).days if dates else None

        sport_breakdown = {}
        for b in bets:
            if b.event and b.event.sport:
                sport_breakdown[b.event.sport] = sport_breakdown.get(b.event.sport, 0) + 1

        bet_type_breakdown = {}
        for b in bets:
            bt = b.bet_type or "unknown"
            bet_type_breakdown[bt] = bet_type_breakdown.get(bt, 0) + 1

        market_breakdown = {}
        for b in bets:
            m = b.market or "unknown"
            market_breakdown[m] = market_breakdown.get(m, 0) + 1

        bonus_bets = sum(1 for b in bets if b.is_bonus)

        return {
            "total_bets": total_bets,
            "total_stake": round(total_stake, 2),
            "total_profit": round(total_profit, 2),
            "win_rate": round(win_rate, 3) if win_rate is not None else None,
            "roi_pct": round(roi_pct, 1) if roi_pct is not None else None,
            "avg_clv_pct": round(avg_clv_pct, 2) if avg_clv_pct is not None else None,
            "avg_odds": round(avg_odds, 2),
            "account_age_days": account_age_days,
            "first_bet_date": first_bet_date,
            "last_bet_date": last_bet_date,
            "sport_breakdown": sport_breakdown,
            "bet_type_breakdown": bet_type_breakdown,
            "market_breakdown": market_breakdown,
            "bonus_bets": bonus_bets,
        }

    def record_limit(
        self,
        profile_id: int,
        provider_id: str,
        limit_type: str,
        limit_level: int,
        notes: str | None = None,
        detected_at: datetime | None = None,
    ) -> dict:
        """Record a new provider limit with auto-generated betting snapshot."""
        # Validate provider exists
        provider = self.db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return {"success": False, "error": f"Provider {provider_id} not found"}

        # Check for existing limit of same type
        existing = self.limit_repo.get_existing(profile_id, provider_id, limit_type)
        if existing:
            return {
                "success": False,
                "error": f"Limit {limit_type} already exists for {provider_id}. Update or delete it first.",
            }

        snapshot = self._build_snapshot(profile_id, provider_id)

        limit = self.limit_repo.create(
            profile_id=profile_id,
            provider_id=provider_id,
            limit_type=limit_type,
            limit_level=limit_level,
            detected_at=detected_at or datetime.now(timezone.utc),
            notes=notes,
            betting_snapshot=snapshot,
        )
        self.db.commit()

        logger.info(
            "Recorded %s (level %d) for %s on profile %d — snapshot: %d bets, %.0f stake",
            limit_type, limit_level, provider_id, profile_id,
            snapshot["total_bets"], snapshot["total_stake"],
        )

        return {
            "success": True,
            "id": limit.id,
            "betting_snapshot": snapshot,
        }

    def update_limit(
        self,
        limit_id: int,
        limit_level: int | None = None,
        notes: str | None = None,
    ) -> dict:
        """Update mutable fields on a limit. Snapshot is immutable."""
        limit = self.limit_repo.get_by_id(limit_id)
        if not limit:
            return {"success": False, "error": "Limit not found"}

        if limit_level is not None:
            limit.limit_level = limit_level
        if notes is not None:
            limit.notes = notes
        self.db.commit()

        return {"success": True, "id": limit.id}

    def delete_limit(self, limit_id: int) -> dict:
        """Delete a limit record."""
        limit = self.limit_repo.get_by_id(limit_id)
        if not limit:
            return {"success": False, "error": "Limit not found"}

        self.limit_repo.delete(limit)
        self.db.commit()
        return {"success": True}

    def list_limits(
        self,
        profile_id: int | None = None,
        provider_id: str | None = None,
    ) -> list[dict]:
        """List limits with provider name included."""
        limits = self.limit_repo.list_limits(profile_id, provider_id)
        return [
            {
                "id": l.id,
                "profile_id": l.profile_id,
                "provider_id": l.provider_id,
                "provider_name": l.provider.name if l.provider else l.provider_id,
                "limit_type": l.limit_type,
                "limit_level": l.limit_level,
                "detected_at": l.detected_at.isoformat() if l.detected_at else None,
                "notes": l.notes,
                "betting_snapshot": l.betting_snapshot,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in limits
        ]
```

- [ ] **Step 2: Verify import**

Run: `cd backend && python -c "from src.services.limit_service import LimitService; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/limit_service.py
git commit -m "feat: add LimitService with betting snapshot generation"
```

---

## Chunk 3: Backend API Routes + Schemas

### Task 4: Add Pydantic schemas

**Files:**
- Modify: `backend/src/api/schemas.py`

- [ ] **Step 1: Add limit schemas at end of file**

Update the imports at the top of `schemas.py`:

```python
from typing import Literal, Optional
```

Then add at the end of the file:

```python
# ============ Limit Schemas ============

class LimitCreate(BaseModel):
    provider_id: str
    limit_type: Literal["stake_limited", "market_restricted", "odds_restricted", "fully_banned"]
    limit_level: int  # 1-5
    detected_at: Optional[str] = None  # ISO datetime string, defaults to now
    notes: Optional[str] = None


class LimitUpdate(BaseModel):
    limit_level: Optional[int] = None
    notes: Optional[str] = None


class LimitRiskUpdate(BaseModel):
    limit_risk: Literal["low", "medium", "high", "instant"]
    limit_notes: Optional[str] = None
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/api/schemas.py
git commit -m "feat: add Pydantic schemas for limit endpoints"
```

### Task 5: Create limits route

**Files:**
- Create: `backend/src/api/routes/limits.py`
- Modify: `backend/src/api/routes/__init__.py`

- [ ] **Step 1: Create limits.py**

```python
"""Provider limit API routes."""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import Profile
from ...services.limit_service import LimitService
from ..deps import get_db
from ..schemas import LimitCreate, LimitUpdate

router = APIRouter(prefix="/api/limits", tags=["limits"])


def _get_active_profile(db: Session) -> Profile:
    """Get active profile or raise 400."""
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        raise HTTPException(400, "No active profile")
    return profile


@router.get("")
async def list_limits(
    provider_id: str | None = None,
    db: Session = Depends(get_db),
):
    """List all limits for the active profile."""
    profile = _get_active_profile(db)
    service = LimitService(db)
    return service.list_limits(profile_id=profile.id, provider_id=provider_id)


@router.post("")
async def create_limit(data: LimitCreate, db: Session = Depends(get_db)):
    """Record a new provider limit with auto-generated betting snapshot."""
    profile = _get_active_profile(db)

    # Validate limit_level range (limit_type validated by Pydantic Literal)
    if not (1 <= data.limit_level <= 5):
        raise HTTPException(400, "limit_level must be between 1 and 5")

    detected_at = None
    if data.detected_at:
        try:
            detected_at = datetime.fromisoformat(data.detected_at)
        except ValueError:
            raise HTTPException(400, "Invalid detected_at format. Use ISO 8601.")

    service = LimitService(db)
    result = service.record_limit(
        profile_id=profile.id,
        provider_id=data.provider_id,
        limit_type=data.limit_type,
        limit_level=data.limit_level,
        notes=data.notes,
        detected_at=detected_at,
    )

    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@router.put("/{limit_id}")
async def update_limit(limit_id: int, data: LimitUpdate, db: Session = Depends(get_db)):
    """Update limit level or notes. Snapshot is immutable."""
    if data.limit_level is not None and not (1 <= data.limit_level <= 5):
        raise HTTPException(400, "limit_level must be between 1 and 5")

    service = LimitService(db)
    result = service.update_limit(limit_id, limit_level=data.limit_level, notes=data.notes)

    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result


@router.delete("/{limit_id}")
async def delete_limit(limit_id: int, db: Session = Depends(get_db)):
    """Delete a limit record."""
    service = LimitService(db)
    result = service.delete_limit(limit_id)

    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result
```

- [ ] **Step 2: Register in routes/__init__.py**

Add import:
```python
from .limits import router as limits_router
```

Add `'limits_router'` to `__all__`.

- [ ] **Step 3: Verify route registration**

Run: `cd backend && python -c "from src.api.routes import limits_router; print('prefix:', limits_router.prefix)"`
Expected: `prefix: /api/limits`

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/limits.py backend/src/api/routes/__init__.py
git commit -m "feat: add /api/limits CRUD endpoints"
```

### Task 6: Add PATCH endpoint for global limit_risk on providers

**Files:**
- Modify: `backend/src/api/routes/providers.py`

- [ ] **Step 1: Add import for LimitRiskUpdate schema**

In the imports at the top of `providers.py`, update the schema import:

```python
from ..schemas import ProviderCreate, ProviderUpdate, LimitRiskUpdate
```

- [ ] **Step 2: Add PATCH endpoint at end of file**

```python
@router.patch("/{provider_id}/limit-risk")
async def update_limit_risk(
    provider_id: str,
    data: LimitRiskUpdate,
    db: Session = Depends(get_db),
):
    """Set global limit risk rating for a provider."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    # limit_risk validated by Pydantic Literal
    provider.limit_risk = data.limit_risk
    if data.limit_notes is not None:
        provider.limit_notes = data.limit_notes
    provider.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "limit_risk": provider.limit_risk,
        "limit_notes": provider.limit_notes,
    }
```

- [ ] **Step 3: Include limit_risk in list_providers response**

In the `list_providers` endpoint, add to the provider dict (after `"bonus_status"`):

```python
            "limit_risk": p.limit_risk or "low",
            "limit_notes": p.limit_notes,
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/providers.py backend/src/api/schemas.py
git commit -m "feat: add PATCH /providers/{id}/limit-risk and expose limit_risk in list"
```

### Task 7: Register limits router in main app

**Files:**
- Modify: `backend/src/api/__init__.py`

- [ ] **Step 1: Add `limits_router` to the existing import block (~line 28-45)**

In `backend/src/api/__init__.py`, add `limits_router,` to the multi-line import from `.routes`:

```python
from .routes import (
    providers_router,
    bankroll_router,
    # ... existing routers ...
    settings_router,
    market_router,
    limits_router,      # ADD THIS LINE
)
```

- [ ] **Step 2: Add `app.include_router(limits_router)` after the other registrations (~line 305)**

```python
app.include_router(settings_router)
app.include_router(limits_router)
```

- [ ] **Step 3: Verify server starts**

Run: `cd backend && python -m src.app --help`
Expected: No import errors

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/__init__.py
git commit -m "feat: register limits router in FastAPI app"
```

---

## Chunk 4: Frontend — Types, API, Stats Page

### Task 8: Add TypeScript types and API methods

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add limit types to index.ts**

Add after existing type definitions:

```typescript
// Provider Limits
export interface ProviderLimit {
  id: number;
  profile_id: number;
  provider_id: string;
  provider_name: string;
  limit_type: 'stake_limited' | 'market_restricted' | 'odds_restricted' | 'fully_banned';
  limit_level: number;  // 1-5
  detected_at: string | null;
  notes: string | null;
  betting_snapshot: BettingSnapshot | null;
  created_at: string | null;
}

export interface BettingSnapshot {
  total_bets: number;
  total_stake: number;
  total_profit: number;
  win_rate: number | null;
  roi_pct: number | null;
  avg_clv_pct: number | null;
  avg_odds: number | null;
  account_age_days: number | null;
  first_bet_date: string | null;
  last_bet_date: string | null;
  sport_breakdown: Record<string, number>;
  bet_type_breakdown: Record<string, number>;
  market_breakdown: Record<string, number>;
  bonus_bets: number;
}
```

- [ ] **Step 2: Add API methods to api.ts**

Add to the `api` object:

```typescript
  // --- Limits ---
  async getLimits(providerId?: string): Promise<ProviderLimit[]> {
    const params = providerId ? `?provider_id=${providerId}` : '';
    return fetchJson<ProviderLimit[]>(`/limits${params}`);
  },

  async createLimit(data: {
    provider_id: string;
    limit_type: string;
    limit_level: number;
    notes?: string;
  }): Promise<{ success: boolean; id: number; betting_snapshot: BettingSnapshot }> {
    return fetchJson('/limits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async updateLimit(id: number, data: {
    limit_level?: number;
    notes?: string;
  }): Promise<{ success: boolean }> {
    return fetchJson(`/limits/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async deleteLimit(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/limits/${id}`, { method: 'DELETE' });
  },

  async updateProviderLimitRisk(providerId: string, data: {
    limit_risk: string;
    limit_notes?: string;
  }): Promise<{ success: boolean }> {
    return fetchJson(`/providers/${providerId}/limit-risk`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },
```

- [ ] **Step 3: Add ProviderLimit and BettingSnapshot to type imports where needed**

Ensure the types are exported from `types/index.ts`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/services/api.ts
git commit -m "feat: add limit types and API methods to frontend"
```

### Task 9: Add provider stats section to StatsPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/StatsPage.tsx`

- [ ] **Step 1: Add imports and state**

Update imports:
```typescript
import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { CLVChart } from './BetsPage';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { BankrollStats, Bet, ProviderLimit } from '@/types';
```

Add state inside `StatsPage`:
```typescript
const [limits, setLimits] = useState<ProviderLimit[]>([]);
const [limitForm, setLimitForm] = useState<{
  providerId: string;
  limitType: string;
  limitLevel: number;
  notes: string;
} | null>(null);
const [saving, setSaving] = useState(false);
```

- [ ] **Step 2: Fetch limits in fetchData**

Update the `fetchData` callback to also fetch limits:

```typescript
const fetchData = useCallback(async () => {
  setIsLoading(true);
  try {
    const [statsData, betsData, limitsData] = await Promise.all([
      api.getBankrollStats(),
      api.getBets(undefined, 500),
      api.getLimits(),
    ]);
    setStats(statsData);
    setBets(betsData.bets);
    setLimits(limitsData);
  } catch (err) {
    console.error('Failed to fetch stats:', err);
  } finally {
    setIsLoading(false);
  }
}, []);
```

- [ ] **Step 3: Add provider stats computation helper**

Add before the return statement:

```typescript
// Compute per-provider stats from bets
const providerStats = (() => {
  const grouped: Record<string, Bet[]> = {};
  for (const bet of bets) {
    const pid = bet.provider;
    if (!grouped[pid]) grouped[pid] = [];
    grouped[pid].push(bet);
  }

  return Object.entries(grouped)
    .map(([providerId, provBets]) => {
      const totalStake = provBets.reduce((s, b) => s + b.stake, 0);
      const totalProfit = provBets.reduce((s, b) => s + b.profit, 0);
      const settled = provBets.filter(b => b.result === 'won' || b.result === 'lost');
      const wins = settled.filter(b => b.result === 'won').length;
      const clvBets = provBets.filter(b => b.clv_pct != null);
      const avgClv = clvBets.length > 0
        ? clvBets.reduce((s, b) => s + (b.clv_pct ?? 0), 0) / clvBets.length
        : null;
      const provLimits = limits.filter(l => l.provider_id === providerId);

      return {
        providerId,
        totalBets: provBets.length,
        totalStake,
        totalProfit,
        roi: totalStake > 0 ? (totalProfit / totalStake) * 100 : 0,
        winRate: settled.length > 0 ? wins / settled.length : null,
        avgClv,
        limits: provLimits,
      };
    })
    .sort((a, b) => b.totalBets - a.totalBets);
})();
```

- [ ] **Step 4: Add limit form handlers**

```typescript
const handleMarkLimited = async () => {
  if (!limitForm) return;
  setSaving(true);
  try {
    await api.createLimit({
      provider_id: limitForm.providerId,
      limit_type: limitForm.limitType,
      limit_level: limitForm.limitLevel,
      notes: limitForm.notes || undefined,
    });
    setLimitForm(null);
    fetchData();
  } catch (err) {
    console.error('Failed to create limit:', err);
  } finally {
    setSaving(false);
  }
};

const handleDeleteLimit = async (id: number) => {
  try {
    await api.deleteLimit(id);
    fetchData();
  } catch (err) {
    console.error('Failed to delete limit:', err);
  }
};

const LIMIT_LEVEL_LABELS: Record<number, string> = {
  1: 'Minor', 2: 'Moderate', 3: 'Severe', 4: 'Gutted', 5: 'Closed',
};

const RISK_COLORS: Record<string, string> = {
  low: 'text-success',
  medium: 'text-warning',
  high: 'text-orange-400',
  instant: 'text-error',
};
```

- [ ] **Step 5: Add provider stats table JSX**

Add after the CLV chart section (`<CLVChart bets={bets} />`), before the closing `</div>`:

```tsx
{/* Provider Stats */}
{providerStats.length > 0 && (
  <div className="border-l-2 border-tabStats">
    <table className="sq">
      <thead>
        <tr>
          <th>Provider</th>
          <th className="text-right">Bets</th>
          <th className="text-right">Stake</th>
          <th className="text-right">Profit</th>
          <th className="text-right">ROI</th>
          <th className="text-right">CLV</th>
          <th className="text-right">Status</th>
        </tr>
      </thead>
      <tbody>
        {providerStats.map(ps => (
          <tr key={ps.providerId}>
            <td className="text-text">{ps.providerId}</td>
            <td className="text-right text-muted">{ps.totalBets}</td>
            <td className="text-right text-muted">{ps.totalStake.toFixed(0)}</td>
            <td className={`text-right ${ps.totalProfit >= 0 ? 'text-success' : 'text-error'}`}>
              {ps.totalProfit >= 0 ? '+' : ''}{ps.totalProfit.toFixed(0)}
            </td>
            <td className={`text-right ${ps.roi >= 0 ? 'text-success' : 'text-error'}`}>
              {ps.roi >= 0 ? '+' : ''}{ps.roi.toFixed(1)}%
            </td>
            <td className="text-right text-muted">
              {ps.avgClv != null ? `${ps.avgClv >= 0 ? '+' : ''}${ps.avgClv.toFixed(1)}%` : '—'}
            </td>
            <td className="text-right">
              {ps.limits.length > 0 ? (
                <span className="text-error text-xs">
                  {ps.limits.map(l => (
                    <button
                      key={l.id}
                      onClick={() => handleDeleteLimit(l.id)}
                      className="hover:line-through cursor-pointer"
                      title={`${l.limit_type} — ${l.notes || 'click to remove'}\nSnapshot: ${l.betting_snapshot?.total_bets ?? 0} bets`}
                    >
                      {LIMIT_LEVEL_LABELS[l.limit_level] || l.limit_level}/5
                    </button>
                  ))}
                </span>
              ) : (
                <button
                  onClick={() => setLimitForm({
                    providerId: ps.providerId,
                    limitType: 'stake_limited',
                    limitLevel: 3,
                    notes: '',
                  })}
                  className="text-xs text-muted hover:text-text"
                >
                  Mark Limited
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
)}

{/* Limit Form Modal */}
{limitForm && (
  <div className="border-l-2 border-error p-3 space-y-2">
    <div className="text-sm text-text font-medium">
      Mark {limitForm.providerId} as limited
    </div>
    <div className="flex gap-2 items-center flex-wrap">
      <select
        value={limitForm.limitType}
        onChange={e => setLimitForm({ ...limitForm, limitType: e.target.value })}
        className="bg-panel2 text-text text-xs px-2 py-1 rounded border border-border"
      >
        <option value="stake_limited">Stake Limited</option>
        <option value="market_restricted">Market Restricted</option>
        <option value="odds_restricted">Odds Restricted</option>
        <option value="fully_banned">Fully Banned</option>
      </select>
      <select
        value={limitForm.limitLevel}
        onChange={e => setLimitForm({ ...limitForm, limitLevel: Number(e.target.value) })}
        className="bg-panel2 text-text text-xs px-2 py-1 rounded border border-border"
      >
        {[1, 2, 3, 4, 5].map(n => (
          <option key={n} value={n}>{n} — {LIMIT_LEVEL_LABELS[n]}</option>
        ))}
      </select>
      <input
        type="text"
        placeholder="Notes (optional)"
        value={limitForm.notes}
        onChange={e => setLimitForm({ ...limitForm, notes: e.target.value })}
        className="bg-panel2 text-text text-xs px-2 py-1 rounded border border-border flex-1 min-w-[150px]"
      />
      <button
        onClick={handleMarkLimited}
        disabled={saving}
        className="text-xs px-3 py-1 bg-error/20 text-error hover:bg-error/30 rounded"
      >
        {saving ? 'Saving...' : 'Confirm'}
      </button>
      <button
        onClick={() => setLimitForm(null)}
        className="text-xs px-2 py-1 text-muted hover:text-text"
      >
        Cancel
      </button>
    </div>
  </div>
)}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/StatsPage.tsx
git commit -m "feat: add provider stats section with limit tracking to Stats page"
```

---

## Chunk 5: End-to-end verification

### Task 10: Verify full stack works

- [ ] **Step 1: Start backend and verify DB migration**

Run: `cd backend && python -m src.app extract --help`
Check: No errors, `profile_provider_limits` table created, Provider has `limit_risk` column.

- [ ] **Step 2: Test API endpoints with curl**

```bash
# List limits (should be empty)
curl http://localhost:8000/api/limits

# Create a limit
curl -X POST http://localhost:8000/api/limits \
  -H "Content-Type: application/json" \
  -d '{"provider_id":"unibet","limit_type":"stake_limited","limit_level":3,"notes":"Max 50kr on football"}'

# List limits again (should show the new limit with snapshot)
curl http://localhost:8000/api/limits

# Update limit level
curl -X PUT http://localhost:8000/api/limits/1 \
  -H "Content-Type: application/json" \
  -d '{"limit_level":4}'

# Verify providers list includes limit_risk
curl http://localhost:8000/api/providers | python -m json.tool | grep limit_risk
```

- [ ] **Step 3: Verify frontend renders**

Start frontend dev server, navigate to Stats tab, verify:
- Provider stats table appears with bets grouped by provider
- "Mark Limited" buttons work
- Inline form appears and submits successfully
- Limited providers show level badge

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address integration issues from end-to-end testing"
```
