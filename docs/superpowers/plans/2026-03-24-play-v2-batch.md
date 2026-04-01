# Play v2: Unified Batch System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Play v1's single-provider view with a unified batch system that collects all +EV bets (soft value, Pinnacle reverse, Polymarket), ranks them by expected profit with sharps prioritized, shows balance allocation, and fires everything in one batch.

**Architecture:** Create a `BatchBuilder` service that queries all opportunity types, deduplicates across cluster siblings, ranks by tier (sharp first) then expected profit, allocates balance, and returns a ready-to-fire batch. Expose via `POST /api/play/batch`. Replace PlayPage frontend with batch-driven UI using the existing `createBatchBets()` API for firing.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), React/TypeScript/TanStack Query (frontend)

**Spec:** `docs/superpowers/specs/2026-03-24-play-v2-unified-batch-design.md`

---

### Task 1: Create BatchBuilder service

**Files:**
- Create: `backend/src/services/batch_builder.py`

This is the core logic. It collects all +EV opportunities, deduplicates, ranks, allocates balance, and returns the batch.

- [ ] **Step 1: Read existing data sources**

Before coding, read these files to understand the APIs:
- `backend/src/repositories/opportunity_repo.py` — `find_active()` method (lines 18-75), returns `list[(Opportunity, Event)]`
- `backend/src/bankroll/stake_calculator.py` — `calculate_stake()` (lines 189-368), returns `StakeResult`
- `backend/src/services/play_service.py` — `PlaySessionService` (the v1 service), `derive_lifecycle()` function
- `backend/src/constants.py` — `PLATFORM_GROUPS` (lines 62-83) for cluster membership

- [ ] **Step 2: Create batch_builder.py**

```python
"""Batch builder — collects all +EV opportunities, ranks, allocates balance."""

from __future__ import annotations
from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from src.constants import PLATFORM_GROUPS, PLATFORM_MAP
from src.repositories.opportunity_repo import OpportunityRepo
from src.repositories.profile_repo import ProfileRepo
from src.bankroll.stake_calculator import calculate_stake, round_stake_natural, dynamic_min_stake
from src.services.play_service import derive_lifecycle


@dataclass
class BatchBet:
    """A single bet in the batch."""
    rank: int
    tier: str  # "sharp" or "soft"
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None
    odds: float
    fair_odds: float
    edge_pct: float
    stake: float
    expected_profit: float
    is_bonus: bool
    bonus_type: str | None
    # Display fields
    home_team: str
    away_team: str
    display_home: str | None
    display_away: str | None
    sport: str
    league: str
    starts_at: str | None
    # Lifecycle context
    lifecycle: str | None = None
    cluster: str | None = None


@dataclass
class ProviderBalance:
    """Balance tracking during allocation."""
    provider_id: str
    cluster: str | None
    initial_balance: float
    allocated: float = 0.0
    lifecycle: str = "playing"
    min_odds: float | None = None
    trigger_mode: str = "cumulative"
    bonus_amount: float = 0.0
    is_bonus_phase: bool = False

    @property
    def remaining(self) -> float:
        return self.initial_balance - self.allocated

    @property
    def excess(self) -> float:
        return max(0, self.remaining)


class BatchBuilder:
    """Builds optimal batch of bets ranked by expected profit."""

    SHARP_PROVIDERS = {"pinnacle", "polymarket"}

    def __init__(self, db: Session):
        self.db = db
        self.opp_repo = OpportunityRepo(db)
        self.profile_repo = ProfileRepo(db)

    def build(self, profile_id: int) -> dict:
        """Build a complete batch with balance allocation."""

        # 1. Load all balances and bonus states
        provider_balances = self._load_provider_balances(profile_id)
        total_bankroll = sum(pb.initial_balance for pb in provider_balances.values())

        if total_bankroll <= 0:
            return {"batch": [], "summary": self._empty_summary(), "balance_status": [], "missed_opportunities": {"total_bets": 0, "total_ev": 0}}

        # 2. Collect all +EV opportunities
        candidates = self._collect_candidates(total_bankroll, provider_balances)

        # 3. Deduplicate within clusters (one copy per event+market+outcome per platform)
        candidates = self._deduplicate(candidates, provider_balances)

        # 4. Rank: sharps first, then by expected profit
        candidates.sort(key=lambda c: (-self._tier_priority(c), -c.expected_profit))

        # 5. Allocate balance
        batch, missed = self._allocate(candidates, provider_balances)

        # Number the batch
        for i, bet in enumerate(batch):
            bet.rank = i + 1

        # 6. Build response
        return {
            "batch": [self._bet_to_dict(b) for b in batch],
            "summary": self._build_summary(batch),
            "balance_status": self._build_balance_status(provider_balances, missed),
            "missed_opportunities": {
                "total_bets": len(missed),
                "total_ev": round(sum(m.expected_profit for m in missed), 2),
                "reason": "insufficient_balance",
            },
        }

    def _load_provider_balances(self, profile_id: int) -> dict[str, ProviderBalance]:
        """Load balances and bonus states for all providers with balance."""
        balances = {}

        # Build provider -> cluster mapping
        provider_to_cluster = {}
        for group_name, group_info in PLATFORM_GROUPS.items():
            for pid in group_info["members"]:
                provider_to_cluster[pid] = group_name

        # Get all balances from profile
        all_balances = self.profile_repo.get_all_balances(profile_id)

        for provider_id, balance in all_balances.items():
            if balance <= 0:
                continue

            bonus_info = self.profile_repo.get_bonus_status(profile_id, provider_id)
            bonus_status = bonus_info.get("status")
            # We need limit info too
            limit_level = None  # Will be loaded from limits if needed

            lifecycle = derive_lifecycle(balance, bonus_status, limit_level)

            balances[provider_id] = ProviderBalance(
                provider_id=provider_id,
                cluster=provider_to_cluster.get(provider_id),
                initial_balance=balance,
                lifecycle=lifecycle,
                min_odds=bonus_info.get("min_odds") if bonus_status in ("trigger_needed", "in_progress") else None,
                trigger_mode=bonus_info.get("trigger_mode", "cumulative"),
                bonus_amount=bonus_info.get("bonus_amount", 0),
                is_bonus_phase=bonus_status in ("trigger_needed", "freebet_available"),
            )

        return balances

    def _collect_candidates(self, total_bankroll: float, provider_balances: dict[str, ProviderBalance]) -> list[BatchBet]:
        """Collect all +EV opportunities across all types."""
        candidates = []

        # Query all active value opportunities (includes soft, polymarket)
        value_opps = self.opp_repo.find_active(type="value", limit=2000)
        for opp, event in value_opps:
            if opp.provider1_id not in provider_balances:
                continue  # No balance on this provider

            pb = provider_balances[opp.provider1_id]

            # Check bonus phase min_odds
            if pb.min_odds and opp.odds1 < pb.min_odds:
                continue

            # Calculate stake
            stake_result = calculate_stake(
                bankroll_total=total_bankroll,
                edge_raw=opp.edge_pct / 100,
                odds=opp.odds1,
                min_odds=pb.min_odds or 0,
            )
            if stake_result.skip_reason or stake_result.stake <= 0:
                continue

            # Override stake for single-shot trigger
            stake = stake_result.stake
            is_bonus = False
            bonus_type = None

            if pb.lifecycle == "deposited" and pb.trigger_mode == "single":
                stake = pb.bonus_amount  # Fixed trigger stake
            elif pb.lifecycle == "freebet":
                stake = pb.bonus_amount
                is_bonus = True
                bonus_type = "free_bet"

            candidates.append(BatchBet(
                rank=0,
                tier="sharp" if opp.provider1_id in self.SHARP_PROVIDERS else "soft",
                provider_id=opp.provider1_id,
                event_id=opp.event_id,
                market=opp.market,
                outcome=opp.outcome1,
                point=opp.point,
                odds=opp.odds1,
                fair_odds=opp.odds2 or 0,
                edge_pct=round(opp.edge_pct, 2),
                stake=round(stake, 2),
                expected_profit=round(opp.edge_pct / 100 * stake, 2),
                is_bonus=is_bonus,
                bonus_type=bonus_type,
                home_team=event.home_team,
                away_team=event.away_team,
                display_home=getattr(event, "display_home", None),
                display_away=getattr(event, "display_away", None),
                sport=event.sport,
                league=event.league or "",
                starts_at=event.start_time.isoformat() if event.start_time else None,
                lifecycle=pb.lifecycle,
                cluster=pb.cluster,
            ))

        # Query Pinnacle reverse value opportunities
        reverse_opps = self.opp_repo.find_active(type="reverse_value", limit=500)
        for opp, event in reverse_opps:
            if "pinnacle" not in provider_balances:
                continue

            stake_result = calculate_stake(
                bankroll_total=total_bankroll,
                edge_raw=opp.edge_pct / 100,
                odds=opp.odds1,
                min_odds=0,
            )
            if stake_result.skip_reason or stake_result.stake <= 0:
                continue

            candidates.append(BatchBet(
                rank=0,
                tier="sharp",
                provider_id="pinnacle",
                event_id=opp.event_id,
                market=opp.market,
                outcome=opp.outcome1,
                point=opp.point,
                odds=opp.odds1,
                fair_odds=opp.odds2 or 0,
                edge_pct=round(opp.edge_pct, 2),
                stake=round(stake_result.stake, 2),
                expected_profit=round(opp.edge_pct / 100 * stake_result.stake, 2),
                is_bonus=False,
                bonus_type=None,
                home_team=event.home_team,
                away_team=event.away_team,
                display_home=getattr(event, "display_home", None),
                display_away=getattr(event, "display_away", None),
                sport=event.sport,
                league=event.league or "",
                starts_at=event.start_time.isoformat() if event.start_time else None,
                lifecycle="playing",
                cluster=None,
            ))

        return candidates

    def _deduplicate(self, candidates: list[BatchBet], provider_balances: dict[str, ProviderBalance]) -> list[BatchBet]:
        """Deduplicate: one copy per event+market+outcome per cluster."""
        seen: dict[str, BatchBet] = {}  # (cluster, event_id, market, outcome, point) -> best candidate

        for c in candidates:
            # Sharp providers never deduplicate against each other
            if c.tier == "sharp":
                key = (c.provider_id, c.event_id, c.market, c.outcome, c.point)
            else:
                # Soft: deduplicate within cluster
                cluster = c.cluster or c.provider_id
                key = (cluster, c.event_id, c.market, c.outcome, c.point)

            if key not in seen:
                seen[key] = c
            else:
                # Keep the one on the provider with more balance
                existing = seen[key]
                existing_bal = provider_balances.get(existing.provider_id)
                current_bal = provider_balances.get(c.provider_id)
                if current_bal and existing_bal and current_bal.remaining > existing_bal.remaining:
                    seen[key] = c

        return list(seen.values())

    def _allocate(self, ranked: list[BatchBet], provider_balances: dict[str, ProviderBalance]) -> tuple[list[BatchBet], list[BatchBet]]:
        """Walk ranked list, allocate balance. Returns (batch, missed)."""
        batch = []
        missed = []

        for bet in ranked:
            pb = provider_balances.get(bet.provider_id)
            if not pb or pb.remaining < bet.stake:
                missed.append(bet)
                continue

            pb.allocated += bet.stake
            batch.append(bet)

        return batch, missed

    @staticmethod
    def _tier_priority(bet: BatchBet) -> int:
        """Sharp = 1 (higher priority), Soft = 0."""
        return 1 if bet.tier == "sharp" else 0

    def _build_summary(self, batch: list[BatchBet]) -> dict:
        sharp = [b for b in batch if b.tier == "sharp"]
        soft = [b for b in batch if b.tier == "soft"]
        return {
            "total_bets": len(batch),
            "total_stake": round(sum(b.stake for b in batch), 2),
            "total_expected_profit": round(sum(b.expected_profit for b in batch), 2),
            "sharp_bets": len(sharp),
            "sharp_ev": round(sum(b.expected_profit for b in sharp), 2),
            "soft_bets": len(soft),
            "soft_ev": round(sum(b.expected_profit for b in soft), 2),
        }

    def _build_balance_status(self, provider_balances: dict[str, ProviderBalance], missed: list[BatchBet]) -> list[dict]:
        """Build per-provider balance allocation status."""
        # Count missed bets and EV per provider
        missed_by_provider: dict[str, list[BatchBet]] = {}
        for m in missed:
            missed_by_provider.setdefault(m.provider_id, []).append(m)

        statuses = []
        for pid, pb in provider_balances.items():
            missed_list = missed_by_provider.get(pid, [])
            status = {
                "provider_id": pid,
                "cluster": pb.cluster,
                "balance": round(pb.initial_balance, 2),
                "allocated": round(pb.allocated, 2),
                "remaining": round(pb.remaining, 2),
                "missed_bets": len(missed_list),
                "missed_ev": round(sum(m.expected_profit for m in missed_list), 2),
            }
            if pb.remaining > 0:
                status["excess"] = round(pb.remaining, 2)
            statuses.append(status)

        # Add providers with missed bets but no balance (need deposit)
        for pid, missed_list in missed_by_provider.items():
            if pid not in provider_balances:
                statuses.append({
                    "provider_id": pid,
                    "cluster": None,
                    "balance": 0,
                    "allocated": 0,
                    "remaining": 0,
                    "shortfall": round(sum(m.stake for m in missed_list), 2),
                    "missed_bets": len(missed_list),
                    "missed_ev": round(sum(m.expected_profit for m in missed_list), 2),
                })

        return statuses

    @staticmethod
    def _empty_summary() -> dict:
        return {"total_bets": 0, "total_stake": 0, "total_expected_profit": 0, "sharp_bets": 0, "sharp_ev": 0, "soft_bets": 0, "soft_ev": 0}

    @staticmethod
    def _bet_to_dict(bet: BatchBet) -> dict:
        return {
            "rank": bet.rank,
            "tier": bet.tier,
            "provider_id": bet.provider_id,
            "event_id": bet.event_id,
            "market": bet.market,
            "outcome": bet.outcome,
            "point": bet.point,
            "odds": bet.odds,
            "fair_odds": bet.fair_odds,
            "edge_pct": bet.edge_pct,
            "stake": bet.stake,
            "expected_profit": bet.expected_profit,
            "is_bonus": bet.is_bonus,
            "bonus_type": bet.bonus_type,
            "home_team": bet.home_team,
            "away_team": bet.away_team,
            "display_home": bet.display_home,
            "display_away": bet.display_away,
            "sport": bet.sport,
            "league": bet.league,
            "starts_at": bet.starts_at,
            "lifecycle": bet.lifecycle,
            "cluster": bet.cluster,
        }
```

- [ ] **Step 3: Verify ProfileRepo has get_all_balances()**

Read `backend/src/repositories/profile_repo.py` to check if `get_all_balances(profile_id)` exists. If not, add it — it should return `dict[str, float]` mapping provider_id to balance. Check for existing methods like `get_total_bankroll()` that already query all balances, and follow the same pattern.

- [ ] **Step 4: Verify imports and smoke test**

```bash
cd backend && python -c "from src.services.batch_builder import BatchBuilder; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/batch_builder.py
git commit -m "feat(play-v2): create BatchBuilder service with unified ranking and balance allocation"
```

---

### Task 2: Add batch API endpoints

**Files:**
- Modify: `backend/src/api/routes/opportunities.py`

- [ ] **Step 1: Read existing route patterns**

Read `backend/src/api/routes/opportunities.py` to understand how `get_play_session` and other endpoints work (dependency injection, profile loading).

Also read `backend/src/api/routes/bets.py` to understand the existing `create_batch_bets()` endpoint (lines 390-482) — we'll reuse it for firing.

- [ ] **Step 2: Add POST /play/batch endpoint**

Add to `backend/src/api/routes/opportunities.py`:

```python
from src.services.batch_builder import BatchBuilder

@router.post("/play/batch")
async def build_batch(db: Session = Depends(get_db)):
    """Build optimal batch of all +EV bets with balance allocation."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    builder = BatchBuilder(db)
    return builder.build(profile.id)
```

No separate `/play/fire` endpoint needed — the existing `POST /api/bets/batch` already handles batch placement with per-leg retry and partial failure reporting.

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/opportunities.py
git commit -m "feat(play-v2): add POST /api/play/batch endpoint"
```

---

### Task 3: Add frontend types and API calls

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/services/api/opportunities.ts`

- [ ] **Step 1: Add batch types**

In `frontend/src/types/index.ts`, add after the `PlaySession` interface:

```typescript
export interface BatchBet {
  rank: number;
  tier: 'sharp' | 'soft';
  provider_id: string;
  event_id: string;
  market: string;
  outcome: string;
  point: number | null;
  odds: number;
  fair_odds: number;
  edge_pct: number;
  stake: number;
  expected_profit: number;
  is_bonus: boolean;
  bonus_type: string | null;
  home_team: string;
  away_team: string;
  display_home: string | null;
  display_away: string | null;
  sport: string;
  league: string;
  starts_at: string | null;
  lifecycle: string | null;
  cluster: string | null;
}

export interface BatchSummary {
  total_bets: number;
  total_stake: number;
  total_expected_profit: number;
  sharp_bets: number;
  sharp_ev: number;
  soft_bets: number;
  soft_ev: number;
}

export interface ProviderBalanceStatus {
  provider_id: string;
  cluster: string | null;
  balance: number;
  allocated: number;
  remaining: number;
  excess?: number;
  shortfall?: number;
  missed_bets: number;
  missed_ev: number;
}

export interface BatchResult {
  batch: BatchBet[];
  summary: BatchSummary;
  balance_status: ProviderBalanceStatus[];
  missed_opportunities: {
    total_bets: number;
    total_ev: number;
    reason: string;
  };
}
```

- [ ] **Step 2: Add buildBatch API call**

In `frontend/src/services/api/opportunities.ts`, add:

```typescript
async getPlayBatch(): Promise<BatchResult> {
  return fetchJson('/opportunities/play/batch', { method: 'POST' });
},
```

Import `BatchResult` from `@/types`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/services/api/opportunities.ts
git commit -m "feat(play-v2): add BatchResult types and buildBatch API call"
```

---

### Task 4: Rewrite PlayPage for batch-driven UI

**Files:**
- Modify: `frontend/src/components/Terminal/pages/PlayPage.tsx`

This is the largest frontend task. The PlayPage should be rewritten to show the batch instead of individual cluster-filtered opportunities.

- [ ] **Step 1: Read the current PlayPage fully**

Read `frontend/src/components/Terminal/pages/PlayPage.tsx` to understand the existing structure (lines 1-840).

- [ ] **Step 2: Replace PlayPage with batch-driven UI**

The new PlayPage should:

**Data flow:**
- Primary query: `api.getPlayBatch()` (builds batch on server)
- Keep `api.getPlaySession()` for the cluster panel (deploy recommendations)
- Fire uses existing `api.createBatchBets()` from `bets.ts`

**Layout:**

```
┌──────────────────────────────────────────────────────┐
│ Play                                   [Build Batch] │
├──────────────────────────────────────────────────────┤
│ BATCH: 24 bets │ 4,200 kr │ +380 kr EV               │
│ Sharp: 5 (+95 kr) │ Soft: 19 (+285 kr)                │
├──────────────────────────────────────────────────────┤
│ SHARP                                                 │
│ # PROVIDER   EVENT          OUTCOME    ODDS EDGE STAKE│
│ 1 pinnacle   Lakers v Celts Over 210.5 2.15 +7%  150 │
│ 2 polymarket  Trump wins MI  Yes       1.45 +12% 200 │
│                                                       │
│ SOFT                                                  │
│ 3 spelklubben Plzen v Sparta Under 4.5  2.30 +25% 190│
│ ...                                                   │
├──────────────────────────────────────────────────────┤
│ BALANCE                                               │
│ ✓ pinnacle     3,200 → 2,750  (450 allocated)        │
│ ✓ spelklubben  1,100 → 610    (490 allocated)        │
│ ✗ betinia        200 → needs 1k (8 bets, +120 EV)    │
├──────────────────────────────────────────────────────┤
│ [Fire playable (16 bets, +260 EV)]  [Rebuild]        │
│ Deploy: comeon, vbet, 10bet (+1 each)                 │
└──────────────────────────────────────────────────────┘
```

**Key components to implement:**

1. **BatchSummaryBar** — total bets, stake, EV, sharp/soft breakdown
2. **BatchTable** — flat list with tier headers, using a simplified row (no expand/dropdown needed — batch already decided provider and stake)
3. **BalancePanel** — per-provider allocation with excess/shortfall indicators
4. **FireButton** — calls `createBatchBets()` with the batch, shows per-bet results
5. **DeployRecommendations** — from PlaySession data (reuse v1 logic)

**Row structure (simpler than OpportunityRow):**
Each row is a fixed bet — provider already chosen, stake already calculated. No expansion needed. Just display: rank, provider, event, outcome, odds, fair, edge%, stake, EV.

Optional: X button to remove a bet from the batch (client-side filter, recalculates balance).

**Fire flow:**
1. User clicks "Fire playable"
2. Convert batch to legs array matching `createBatchBets()` input format
3. Call `api.createBatchBets(legs)`
4. Show results: green checkmark for success, red X for failure per row
5. Auto-rebuild batch (refetch) after firing

- [ ] **Step 3: Handle remove-from-batch**

Track removed bets in client state (`Set<string>` of `event_id|market|outcome|point`). Filter the batch response before display. When rebuilding, clear the removed set.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/PlayPage.tsx
git commit -m "feat(play-v2): rewrite PlayPage with batch-driven UI"
```

---

### Task 5: End-to-end testing

**Files:**
- No new files — manual verification

- [ ] **Step 1: Start dev servers**

Start backend and frontend dev servers per `.claude/launch.json`.

- [ ] **Step 2: Test batch building**

```bash
curl -X POST http://localhost:8000/api/opportunities/play/batch | python -m json.tool
```

Verify:
- Returns batch with sharp bets first
- Each bet has provider_id, odds, stake, edge_pct, expected_profit
- Summary counts match
- Balance status shows per-provider allocation
- Missed opportunities count providers with insufficient balance

- [ ] **Step 3: Test frontend**

Open the Play tab. Verify:
- Batch loads on page mount
- Sharp tier header + bets shown first
- Soft tier header + bets shown second
- Balance panel shows provider allocations
- "Fire playable" button is visible with correct bet count and EV
- Deploy recommendations appear for unfunded clusters

- [ ] **Step 4: Test firing**

Click "Fire playable". Verify:
- All bets are placed via batch API
- Per-bet success/failure indicators shown
- Batch auto-rebuilds after firing
- Placed bets no longer appear in new batch

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix(play-v2): e2e testing fixes"
```
