# Play System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a session-driven Play system that auto-selects providers, filters bets by bonus phase, sizes stakes correctly, and auto-advances through clusters — replacing the manual cluster selection UI.

**Architecture:** Add `trigger_mode` to bonus model, create a `PlaySessionService` that computes lifecycle states and builds session data from existing models, expose via `/api/play/session` endpoint, update ClusterPanel + ValuePage to consume session data with auto-advance logic.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), React/TypeScript/TanStack Query (frontend)

**Spec:** `docs/superpowers/specs/2026-03-24-play-system-design.md`

---

### Task 1: Add `trigger_mode` to ProfileProviderBonus model

**Files:**
- Modify: `backend/src/db/profiles.py:55-109` (ProfileProviderBonus model)
- Modify: `backend/src/db/models.py` (if ProfileProviderBonus is duplicated here, add column there too)

- [ ] **Step 1: Add trigger_mode column to ProfileProviderBonus**

In `backend/src/db/profiles.py`, add to ProfileProviderBonus class after the `deposit_amount` column:

```python
trigger_mode = Column(String, default="cumulative")  # "single" or "cumulative"
```

**Important:** `ProfileProviderBonus` may be defined in both `backend/src/db/models.py` and `backend/src/db/profiles.py`. Check which one is actually imported at runtime (grep for `from src.db` imports). Only one can be the active ORM class for `profile_provider_bonuses` — modify that one. If both files define it, the unused definition should be removed or aliased to avoid SQLAlchemy mapper conflicts.

- [ ] **Step 2: Run Alembic migration or manual ALTER TABLE**

Since this project uses SQLite, add the column directly:

```python
# In the init_db() function or via a migration script:
# ALTER TABLE profile_provider_bonuses ADD COLUMN trigger_mode VARCHAR DEFAULT 'cumulative'
```

Or add it to the `init_db()` function's column-addition block if one exists.

- [ ] **Step 3: Update providers.yaml bonus configs with trigger_mode**

In `backend/src/config/providers.yaml`, add `trigger_mode` to each provider's bonus config. For providers with freebet type where one qualifying bet unlocks the freebet, use `single`. For deposit match bonuses where wagering accumulates across many bets, use `cumulative`.

Example additions:
```yaml
unibet:
  bonus:
    trigger_mode: single  # one bet >= deposit at 1.80+

leovegas:
  bonus:
    trigger_mode: cumulative  # wager deposit × multiplier across many bets

betinia:
  bonus:
    trigger_mode: cumulative
```

Review each provider's bonus section and set accordingly.

- [ ] **Step 4: Commit**

```bash
git add backend/src/db/profiles.py backend/src/db/models.py backend/src/config/providers.yaml
git commit -m "feat(play): add trigger_mode column to ProfileProviderBonus"
```

---

### Task 2: Update ProfileRepo bonus methods to accept trigger_mode

**Files:**
- Modify: `backend/src/db/profiles.py:285-336` (start_bonus_trigger)
- Modify: `backend/src/db/profiles.py:381-424` (start_freebet_tracking)

- [ ] **Step 1: Update start_bonus_trigger() to accept and store trigger_mode**

In `backend/src/db/profiles.py`, update the `start_bonus_trigger()` method signature (line ~285) to add `trigger_mode: str = "cumulative"` parameter. Inside the method, set `bonus.trigger_mode = trigger_mode` when creating the bonus record.

```python
def start_bonus_trigger(
    self, profile_id: int, provider_id: str,
    bonus_amount: float, trigger_wagering: float,
    trigger_min_odds: float = 1.50,
    main_wagering_multiplier: float = 12.0,
    main_min_odds: float = 1.80,
    deadline_days: int | None = None,
    deposit_amount: float | None = None,
    trigger_mode: str = "cumulative",  # NEW
) -> dict:
```

And inside the method body where the bonus is created, add:
```python
bonus.trigger_mode = trigger_mode
```

- [ ] **Step 2: Update start_freebet_tracking() to accept and store trigger_mode**

Same pattern for `start_freebet_tracking()` (line ~381):

```python
def start_freebet_tracking(
    self, profile_id: int, provider_id: str,
    bonus_amount: float, min_odds: float = 1.80,
    trigger_wagering: float | None = None,
    deadline_days: int | None = None,
    trigger_mode: str = "single",  # freebets default to single-shot
) -> dict:
```

And set `bonus.trigger_mode = trigger_mode` in the method body.

- [ ] **Step 3: Update get_bonus_status() to include trigger_mode in return dict**

In `get_bonus_status()` (line ~121), add `trigger_mode` to the returned dict:

```python
return {
    ...existing fields...,
    "trigger_mode": bonus.trigger_mode or "cumulative",
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/db/profiles.py
git commit -m "feat(play): pass trigger_mode through ProfileRepo bonus methods"
```

---

### Task 3: Update BetService trigger advancement to respect trigger_mode

**Files:**
- Modify: `backend/src/services/bet_service.py:261-273` (settle_bet trigger logic)

- [ ] **Step 1: Read the current trigger advancement logic**

Read `backend/src/services/bet_service.py` lines 237-291 to understand the current `settle_bet()` flow. Focus on lines 261-273 where it auto-advances freebet triggers.

- [ ] **Step 2: Update trigger advancement to check trigger_mode**

Currently (lines 268-269) the logic checks:
```python
bet.odds >= (bonus.min_odds or 1.80) and bet.stake >= (bonus.bonus_amount or 0)
```

This is the single-shot check. For cumulative mode, the advancement should happen when `wagered_amount >= wagering_requirement` instead. Update the logic:

```python
# After record_wagering() call (which updates wagered_amount):
if bonus and bonus.bonus_status == "trigger_needed":
    trigger_mode = getattr(bonus, "trigger_mode", "cumulative")

    if trigger_mode == "single":
        # Single-shot: one bet meets stake + odds requirements
        if (bet.odds >= (bonus.min_odds or 1.80) and
            bet.stake >= (bonus.bonus_amount or 0)):
            # advance to freebet_available or in_progress
            ...existing advancement logic...
    else:
        # Cumulative: total wagered meets requirement
        if bonus.wagered_amount >= bonus.wagering_requirement:
            # advance to freebet_available or in_progress
            ...existing advancement logic...
```

Keep the existing advancement actions (setting status, adjusting balance for deposit match) — just change the condition that triggers them.

- [ ] **Step 3: Verify record_wagering still works for cumulative mode**

Read `backend/src/db/profiles.py:182-235` to confirm `record_wagering()` accumulates `wagered_amount` for trigger_needed status. It should already do this — verify it doesn't skip recording when `bonus_status == "trigger_needed"`.

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/bet_service.py
git commit -m "feat(play): respect trigger_mode (single vs cumulative) in settle_bet"
```

---

### Task 4: Create PlaySessionService

**Files:**
- Create: `backend/src/services/play_service.py`

This is the core logic: computes provider lifecycle states, picks active siblings, filters opportunities by bonus phase, sizes stakes.

- [ ] **Step 1: Create play_service.py with lifecycle state derivation**

```python
"""Play session service — computes session data for the Play panel."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.constants import PLATFORM_GROUPS, PLATFORM_MAP
from src.bankroll.stake_calculator import dynamic_min_stake
from src.repositories.profile_repo import ProfileRepo
from src.repositories.opportunity_repo import OpportunityRepo
from src.risk.allocator import ProviderAllocator


def derive_lifecycle(
    balance: float,
    bonus_status: str | None,
    limit_level: int | None,
) -> str:
    """Derive provider lifecycle state from existing data.

    Returns one of: available, deposited, wagering, freebet, playing, limited, dormant.
    """
    has_balance = balance > 0

    # Limited overrides other states (still playable, just flagged)
    is_limited = limit_level is not None and limit_level > 0

    if not has_balance and bonus_status in (None, "available", "completed", "claimed"):
        return "dormant" if bonus_status in ("completed", "claimed") else "available"

    if bonus_status == "trigger_needed":
        return "deposited"
    if bonus_status == "freebet_available":
        return "freebet"
    if bonus_status == "in_progress":
        return "wagering"

    # Has balance, no active bonus restriction
    if is_limited:
        return "limited"
    return "playing"


class PlaySessionService:
    """Builds session data for the Play panel."""

    def __init__(self, db: Session):
        self.db = db
        self.profile_repo = ProfileRepo(db)
        self.opp_repo = OpportunityRepo(db)

    def get_session(self, profile_id: int) -> dict:
        """Build complete session data: clusters with siblings, states, opp counts."""

        # Create allocator with profile_id (required by constructor)
        allocator = ProviderAllocator(self.db, profile_id)

        # Preload all data in bulk (methods are parameterless, use self.profile_id)
        allocator.preload_daily_bets()
        allocator.preload_wagering()
        allocator.preload_balances()
        allocator.preload_limits()

        balances = allocator._balances  # provider_id -> balance
        wagering = allocator._wagering  # provider_id -> {remaining, status, ...}
        limits = allocator._limits      # provider_id -> limit_level

        # Count unique opps per cluster
        cluster_opp_counts = self._count_unique_opps_per_cluster()

        # Get min stake threshold
        total_bankroll = sum(balances.values())
        min_stake = dynamic_min_stake(total_bankroll)

        clusters = []
        for group_name, group_info in PLATFORM_GROUPS.items():
            cluster = self._build_cluster(
                group_name, group_info["members"], group_info["canonical"],
                balances, wagering, limits,
                cluster_opp_counts.get(group_name, 0),
                min_stake, profile_id,
            )
            if cluster:
                clusters.append(cluster)

        # Standalone providers
        grouped = set()
        for g in PLATFORM_GROUPS.values():
            grouped.update(g["members"])

        for pid, platform in PLATFORM_MAP.items():
            if pid not in grouped and pid not in ("pinnacle", "polymarket"):
                cluster = self._build_cluster(
                    pid, [pid], pid,
                    balances, wagering, limits,
                    cluster_opp_counts.get(pid, 0),
                    min_stake, profile_id,
                )
                if cluster:
                    clusters.append(cluster)

        # Sort clusters by wagering urgency (highest remaining/days_left first)
        clusters.sort(key=lambda c: c["urgency"], reverse=True)

        return {
            "clusters": clusters,
            "total_bankroll": round(total_bankroll, 2),
            "min_stake": round(min_stake, 2),
        }

    def _build_cluster(
        self, name: str, members: list[str], canonical: str,
        balances: dict, wagering: dict, limits: dict,
        unique_opps: int, min_stake: float, profile_id: int,
    ) -> dict | None:
        """Build cluster dict with active siblings and lifecycle states."""

        # Build sibling info
        siblings = []
        for pid in members:
            balance = balances.get(pid, 0)
            wag = wagering.get(pid, {})
            limit_level = limits.get(pid)
            bonus_status = wag.get("status")

            lifecycle = derive_lifecycle(balance, bonus_status, limit_level)

            bonus_info = self.profile_repo.get_bonus_status(profile_id, pid)

            siblings.append({
                "provider_id": pid,
                "balance": round(balance, 2),
                "lifecycle": lifecycle,
                "bonus_status": bonus_status,
                "trigger_mode": bonus_info.get("trigger_mode", "cumulative") if bonus_info else "cumulative",
                "wagering_remaining": round(wag.get("remaining", 0), 2),
                "wagering_progress_pct": round(bonus_info.get("progress_pct", 0), 1) if bonus_info else 0,
                "min_odds": bonus_info.get("min_odds", 1.80) if bonus_info else None,
                "bonus_amount": bonus_info.get("bonus_amount", 0) if bonus_info else 0,
                "limit_level": limit_level,
                "expires_at": str(bonus_info.get("expires_at", "")) if bonus_info and bonus_info.get("expires_at") else None,
                "days_remaining": bonus_info.get("days_remaining") if bonus_info else None,
            })

        # Determine max active siblings: 2 if >=30 unique opps, else 1
        max_siblings = 2 if unique_opps >= 30 else 1

        # Pick active siblings: non-dormant, non-available, sorted by urgency
        active_states = ("deposited", "wagering", "freebet", "playing", "limited")
        active = [s for s in siblings if s["lifecycle"] in active_states]
        active.sort(key=lambda s: self._sibling_urgency(s), reverse=True)
        active = active[:max_siblings]

        # Total balance across active siblings
        total_balance = sum(s["balance"] for s in active)

        # Hide cluster if total balance < min stake
        if total_balance < min_stake and not any(s["lifecycle"] in ("deposited", "freebet") for s in active):
            return None

        # Compute cluster urgency for sorting
        urgency = max((self._sibling_urgency(s) for s in active), default=0)

        active_ids = {s["provider_id"] for s in active}

        return {
            "id": name,
            "label": name.replace("_", " ").title(),
            "canonical": canonical,
            "active_siblings": active,
            "available_siblings": [s for s in siblings if s["lifecycle"] == "available"],
            "dormant_siblings": [s for s in siblings if s["lifecycle"] == "dormant"],
            "total_balance": round(total_balance, 2),
            "playable_count": len(active),
            "unique_opps": unique_opps,
            "urgency": round(urgency, 2),
        }

    @staticmethod
    def _sibling_urgency(sibling: dict) -> float:
        """Score sibling by wagering urgency. Higher = more urgent."""
        remaining = sibling.get("wagering_remaining", 0)
        days = sibling.get("days_remaining") or 60

        # Bonus phase priority: trigger/freebet > wagering > playing
        phase_bonus = {
            "deposited": 100,   # trigger needed = high priority
            "freebet": 90,      # freebet available = use it
            "wagering": 50 + (remaining / max(days, 1)),  # urgency by remaining/time
            "playing": 10,
            "limited": 5,
        }
        return phase_bonus.get(sibling["lifecycle"], 0)

    def _count_unique_opps_per_cluster(self) -> dict[str, int]:
        """Count unique (event+market+outcome) opportunities per cluster."""
        from sqlalchemy import text

        # Build provider -> cluster mapping
        provider_cluster = {}
        for group_name, group_info in PLATFORM_GROUPS.items():
            for pid in group_info["members"]:
                provider_cluster[pid] = group_name
        # Standalones
        grouped = set()
        for g in PLATFORM_GROUPS.values():
            grouped.update(g["members"])
        for pid in PLATFORM_MAP:
            if pid not in grouped and pid not in ("pinnacle", "polymarket"):
                provider_cluster[pid] = pid

        result = self.db.execute(text("""
            SELECT provider1_id, event_id, market, outcome1
            FROM opportunities
            WHERE type = 'value' AND is_active = 1
        """))

        cluster_unique: dict[str, set] = {}
        for row in result:
            cluster = provider_cluster.get(row[0])
            if cluster:
                if cluster not in cluster_unique:
                    cluster_unique[cluster] = set()
                cluster_unique[cluster].add((row[1], row[2], row[3]))

        return {c: len(keys) for c, keys in cluster_unique.items()}
```

- [ ] **Step 2: Verify the service works with existing data**

Quick smoke test — import and call in a Python shell or add a temporary test:

```python
from src.services.play_service import PlaySessionService
# ... setup db session, get profile_id
service = PlaySessionService(db)
session = service.get_session(profile_id)
print(session)
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/play_service.py
git commit -m "feat(play): create PlaySessionService with lifecycle derivation"
```

---

### Task 5: Add /api/play/session endpoint

**Files:**
- Modify: `backend/src/api/routes/opportunities.py` (add after line ~128)

- [ ] **Step 1: Read the current route file structure**

Read `backend/src/api/routes/opportunities.py` to understand the existing dependency injection pattern (`_get_service`, db session handling).

- [ ] **Step 2: Add the play session endpoint**

Add after the existing endpoints:

```python
from src.services.play_service import PlaySessionService

@router.get("/play/session")
async def get_play_session(db: Session = Depends(get_db)):
    """Get session data for Play panel: clusters, siblings, lifecycle states."""
    from src.repositories.profile_repo import ProfileRepo
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    service = PlaySessionService(db)
    return service.get_session(profile.id)
```

Make sure the route prefix is correct. If the router is mounted at `/api/opportunities`, the endpoint will be `/api/opportunities/play/session`. Alternatively, create a separate router for `/api/play` if cleaner.

Check how other routes in this file get the active profile — follow the same pattern.

- [ ] **Step 3: Test the endpoint**

```bash
curl http://localhost:8000/api/opportunities/play/session | python -m json.tool
```

Verify it returns clusters with active_siblings, lifecycle states, balances, and urgency scores.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/opportunities.py
git commit -m "feat(play): add GET /api/play/session endpoint"
```

---

### Task 6: Add frontend API call and types

**Files:**
- Modify: `frontend/src/services/api/opportunities.ts` (add getPlaySession)
- Modify: `frontend/src/types/index.ts` (add PlaySession types)

- [ ] **Step 1: Add PlaySession types**

In `frontend/src/types/index.ts`, add after the ClusterSummary interface (line ~707):

```typescript
export interface PlaySibling {
  provider_id: string;
  balance: number;
  lifecycle: 'available' | 'deposited' | 'wagering' | 'freebet' | 'playing' | 'limited' | 'dormant';
  bonus_status: string | null;
  trigger_mode: 'single' | 'cumulative';
  wagering_remaining: number;
  wagering_progress_pct: number;
  min_odds: number | null;
  bonus_amount: number;
  limit_level: number | null;
  expires_at: string | null;
  days_remaining: number | null;
}

export interface PlayCluster {
  id: string;
  label: string;
  canonical: string;
  active_siblings: PlaySibling[];
  available_siblings: PlaySibling[];
  dormant_siblings: PlaySibling[];
  total_balance: number;
  playable_count: number;
  unique_opps: number;
  urgency: number;
}

export interface PlaySession {
  clusters: PlayCluster[];
  total_bankroll: number;
  min_stake: number;
}
```

- [ ] **Step 2: Add getPlaySession API call**

In `frontend/src/services/api/opportunities.ts`, add:

```typescript
export async function getPlaySession(): Promise<PlaySession> {
  return fetchJson('/opportunities/play/session');
}
```

And add it to the api object export in `frontend/src/services/api.ts`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/services/api/opportunities.ts frontend/src/services/api.ts
git commit -m "feat(play): add PlaySession types and API call"
```

---

### Task 7: Update ClusterPanel for Play session mode

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ClusterPanel.tsx`

- [ ] **Step 1: Read the current ClusterPanel**

Read `frontend/src/components/Terminal/pages/ClusterPanel.tsx` fully to understand the current structure (lines 1-151).

- [ ] **Step 2: Update ClusterPanel to accept PlaySession data**

Replace the current ClusterPanel to work with PlaySession data instead of the old ClusterInfo + separate summary query. The panel should:

1. **Props**: Accept `PlayCluster[]` instead of `ClusterInfo[]`
2. **Pills**: Show cluster label + playable_count (number of active siblings with balance)
3. **Auto-hide**: Don't show clusters with 0 playable siblings (already filtered server-side)
4. **Sibling bar**: When cluster selected, show active siblings with balance + badge
5. **Badges**: Derive from lifecycle state:
   - `deposited` → TRG (+ bonus_amount for single, + progress% for cumulative)
   - `freebet` → FREE
   - `wagering` → WAGER + progress%
   - `playing` → PLAY (or no badge)
   - `limited` → LTD

Update the component:

```tsx
import type { PlayCluster, PlaySibling } from '@/types';

interface ClusterPanelProps {
  clusters: PlayCluster[];
  activeCluster: string | null;
  activeProvider: string | null;
  onClusterSelect: (id: string | null) => void;
  onProviderSelect: (id: string) => void;
}

function getBadge(sibling: PlaySibling): { text: string; color: string } | null {
  switch (sibling.lifecycle) {
    case 'deposited':
      if (sibling.trigger_mode === 'single') {
        return { text: `TRG ${sibling.bonus_amount}kr`, color: 'text-amber-400' };
      }
      return { text: `TRG ${sibling.wagering_progress_pct}%`, color: 'text-amber-400' };
    case 'freebet':
      return { text: 'FREE', color: 'text-blue-400' };
    case 'wagering':
      return { text: `WAGER ${sibling.wagering_progress_pct}%`, color: 'text-purple-400' };
    case 'limited':
      return { text: 'LTD', color: 'text-red-400' };
    default:
      return null;
  }
}
```

Keep the existing styling patterns (compact `sq` class, existing color scheme).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/ClusterPanel.tsx
git commit -m "feat(play): update ClusterPanel for PlaySession with lifecycle badges"
```

---

### Task 8: Update ValuePage to use PlaySession and auto-advance

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ValuePage.tsx`

- [ ] **Step 1: Read the current cluster play mode section**

Read `frontend/src/components/Terminal/pages/ValuePage.tsx` lines 416-572 to understand the current cluster state management, auto-selection, and filtering.

- [ ] **Step 2: Replace cluster queries with PlaySession query**

Replace the two separate queries (getClusters + getClusterSummary) with a single getPlaySession query:

```typescript
// Replace lines 419-433 with:
const { data: playSession } = useQuery({
  queryKey: ['play-session'],
  queryFn: () => api.getPlaySession(),
  staleTime: 30_000,
  refetchInterval: 60_000,
});
const clusters = playSession?.clusters ?? [];
```

- [ ] **Step 3: Update auto-select logic for urgency-based selection**

Replace the current useEffect (lines 435-452) with urgency-based auto-selection:

```typescript
useEffect(() => {
  if (!clusters.length) return;

  // Auto-select most urgent cluster if none selected
  if (!activeCluster) {
    const first = clusters[0]; // Already sorted by urgency from backend
    if (first) {
      setActiveCluster(first.id);
      const bestSibling = first.active_siblings[0]; // Sorted by urgency
      if (bestSibling) setActiveClusterProvider(bestSibling.provider_id);
    }
    return;
  }

  // Find current cluster
  const currentCluster = clusters.find(c => c.id === activeCluster);
  if (!currentCluster) {
    // Cluster disappeared (balance ran out) — advance to next
    const next = clusters[0];
    if (next) {
      setActiveCluster(next.id);
      setActiveClusterProvider(next.active_siblings[0]?.provider_id ?? null);
    }
    return;
  }

  // Auto-advance sibling within cluster when balance hits 0
  if (activeClusterProvider) {
    const sibling = currentCluster.active_siblings.find(s => s.provider_id === activeClusterProvider);
    if (!sibling || sibling.balance <= 0) {
      const next = currentCluster.active_siblings.find(s => s.balance > 0 && s.provider_id !== activeClusterProvider);
      if (next) {
        setActiveClusterProvider(next.provider_id);
      } else {
        // All siblings empty — advance to next cluster
        const nextCluster = clusters.find(c => c.id !== activeCluster && c.total_balance > 0);
        if (nextCluster) {
          setActiveCluster(nextCluster.id);
          setActiveClusterProvider(nextCluster.active_siblings[0]?.provider_id ?? null);
        }
      }
    }
  } else {
    // No provider selected — pick first with balance
    const first = currentCluster.active_siblings.find(s => s.balance > 0);
    if (first) setActiveClusterProvider(first.provider_id);
  }
}, [clusters, activeCluster, activeClusterProvider]);
```

- [ ] **Step 4: Update opportunity filtering by bonus phase**

Update the filtering logic in the `grouped` useMemo (lines 516-568). When a provider has a bonus phase, filter opportunities by the phase's min_odds requirement:

```typescript
// Inside the cluster mode filtering section:
if (activeClusterProvider && playSession) {
  const cluster = clusters.find(c => c.id === activeCluster);
  const sibling = cluster?.active_siblings.find(s => s.provider_id === activeClusterProvider);

  result = result.filter(o => o.provider1 === activeClusterProvider);

  if (sibling) {
    // Filter by bonus phase min_odds
    if (sibling.lifecycle === 'deposited' || sibling.lifecycle === 'wagering') {
      const minOdds = sibling.min_odds ?? 1.80;
      result = result.filter(o => o.odds1 >= minOdds);
    }

    // For single-shot trigger: only show bets if provider balance >= trigger amount
    if (sibling.lifecycle === 'deposited' && sibling.trigger_mode === 'single') {
      if (sibling.balance < sibling.bonus_amount) {
        result = []; // Can't afford trigger bet — show nothing
      }
    }
  }
}
```

- [ ] **Step 5: Update stake display for trigger/freebet phases**

In the opportunity display, override stake for special phases:

```typescript
// When computing effective stake for display:
if (sibling?.lifecycle === 'deposited' && sibling.trigger_mode === 'single') {
  // Single-shot trigger: fixed stake = bonus_amount
  effectiveStake = sibling.bonus_amount;
} else if (sibling?.lifecycle === 'freebet') {
  // Freebet: fixed stake = bonus_amount, is_bonus=true
  effectiveStake = sibling.bonus_amount;
}
```

- [ ] **Step 6: Add session stats bar**

Below the opportunity table, add a minimal session stats bar. Track session state in a ref:

```typescript
const [sessionStats, setSessionStats] = useState({ placed: 0, wagered: 0 });

// In the placeBet callback, increment:
const handlePlaceBet = useCallback((opp, odds, stake) => {
  placeBet.mutate({ ...betData });
  setSessionStats(prev => ({
    placed: prev.placed + 1,
    wagered: prev.wagered + stake,
  }));
}, []);
```

Render at the bottom of the table:
```tsx
<div className="flex gap-4 text-xs text-gray-500 px-2 py-1">
  <span>{currentSibling?.balance ?? 0} kr left</span>
  <span>~{Math.floor((currentSibling?.balance ?? 0) / avgStake)} bets</span>
  <span>{sessionStats.placed} placed</span>
  <span>{sessionStats.wagered} kr wagered</span>
</div>
```

- [ ] **Step 7: Update ClusterPanel rendering to pass PlaySession data**

Update the ClusterPanel render call (lines 823-832) to pass the new data shape:

```tsx
{activeTab === 'value' && clusters.length > 0 && (
  <ClusterPanel
    clusters={clusters}
    activeCluster={activeCluster}
    activeProvider={activeClusterProvider}
    onClusterSelect={handleClusterSelect}
    onProviderSelect={setActiveClusterProvider}
  />
)}
```

This should already work since we updated ClusterPanel props in Task 7.

- [ ] **Step 8: Update bet placement to handle freebet and trigger phases**

When placing a bet in trigger/freebet mode, pass the right flags:

```typescript
// In the placeBet handler:
const isFreebetPhase = currentSibling?.lifecycle === 'freebet';

placeBet.mutate({
  ...existingBetData,
  is_bonus: isFreebetPhase,
  bonus_type: isFreebetPhase ? 'free_bet' : undefined,
  stake: effectiveStake,
});
```

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/Terminal/pages/ValuePage.tsx
git commit -m "feat(play): integrate PlaySession with auto-advance and bonus phase filtering"
```

---

### Task 9: End-to-end testing

**Files:**
- No new files — manual testing via running frontend + backend

- [ ] **Step 1: Start dev servers**

Start backend and frontend dev servers per `.claude/launch.json` config.

- [ ] **Step 2: Verify Play panel loads with current account data**

Open the Value Bets tab. Verify:
- Cluster pills appear with correct balance counts
- Active providers show correct lifecycle badges (WAGER, TRG, etc.)
- Opportunities are filtered to the active provider
- Session stats bar shows at the bottom

- [ ] **Step 3: Test auto-advance**

If a provider's balance is near 0 after placing bets, verify the UI auto-switches to the next sibling, then the next cluster.

- [ ] **Step 4: Test bonus phase filtering**

For a provider in `wagering` state with min_odds=1.80, verify that bets with odds < 1.80 are filtered out.

For a provider in `deposited` state with `trigger_mode: single`, verify the stake shows the trigger amount.

- [ ] **Step 5: Verify Play panel rename**

Confirm the label says "Play" (not "Cluster") in the UI.

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix(play): end-to-end testing fixes"
```
