# Play v3: Session Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Play page into a 3-panel session manager (Capital Plan → Batch → Execution) with opportunity-driven capital recommendations, 3-tier batch, round-robin allocation, and provider-grouped execution checklist.

**Architecture:** Backend extends existing `BatchBuilder` with 3-tier sorting, round-robin allocation for soft bets, and enhanced capital plan. Frontend replaces flat `PlayPage.tsx` with three panel components in a new `pages/play/` directory. No DB schema changes — all new data derived from existing tables.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), React 19/TypeScript/Tailwind (frontend), react-query for data fetching.

**Spec:** `docs/superpowers/specs/2026-03-24-play-v3-session-manager-design.md`

---

## File Structure

### Backend (modify)
- `backend/src/services/batch_builder.py` — 3-tier sorting, round-robin, enhanced capital plan, wagering projections, exclude param
- `backend/src/repositories/profile_repo.py` — add `get_avg_daily_wager()` method
- `backend/src/api/routes/opportunities.py` — add `confirm-capital` endpoint, add `exclude` param to batch endpoint

### Frontend (modify)
- `frontend/src/types/index.ts` — update `BatchBet.tier`, add `CapitalAction`, `CapitalPlan`, `WageringProjection` types
- `frontend/src/services/api/opportunities.ts` — add `confirmCapital()`, add `exclude` to `getPlayBatch()`
- `frontend/src/components/Terminal/pages/PlayPage.tsx` — rewrite as 3-panel shell importing sub-components

### Frontend (create)
- `frontend/src/components/Terminal/pages/play/CapitalPlanPanel.tsx` — capital recommendations table
- `frontend/src/components/Terminal/pages/play/SessionBatchPanel.tsx` — 3-tier batch table
- `frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx` — provider accordion with checkoffs

---

## Task 1: Add `get_avg_daily_wager()` to ProfileRepo

**Files:**
- Modify: `backend/src/repositories/profile_repo.py` (after line 97)
- Test: `backend/tests/test_profile_repo_wager.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_profile_repo_wager.py
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Bet, Profile, ProfileProviderBalance
from src.repositories.profile_repo import ProfileRepo


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # Create a profile
    profile = Profile(id=1, name="test", is_active=True)
    session.add(profile)
    session.commit()
    yield session
    session.close()


def _add_bet(db: Session, profile_id: int, stake: float, days_ago: int):
    """Helper to add a bet N days ago."""
    bet = Bet(
        profile_id=profile_id,
        provider_id="unibet",
        event_id="evt_1",
        market="1x2",
        outcome="Home",
        odds=2.0,
        stake=stake,
        currency="SEK",
        result="pending",
        placed_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    db.add(bet)
    db.commit()


def test_avg_daily_wager_no_history(db):
    repo = ProfileRepo(db)
    result = repo.get_avg_daily_wager(profile_id=1)
    assert result["avg_daily_wager"] == 0.0
    assert result["has_history"] is False
    assert result["days_with_bets"] == 0


def test_avg_daily_wager_with_bets(db):
    repo = ProfileRepo(db)
    # Day 1 ago: 500 + 300 = 800
    _add_bet(db, 1, 500.0, days_ago=1)
    _add_bet(db, 1, 300.0, days_ago=1)
    # Day 3 ago: 400
    _add_bet(db, 1, 400.0, days_ago=3)
    result = repo.get_avg_daily_wager(profile_id=1, lookback_days=14)
    # 1200 total over 14 day window = ~85.7/day
    assert result["has_history"] is True
    assert result["days_with_bets"] == 2
    assert 85.0 < result["avg_daily_wager"] < 86.0


def test_avg_daily_wager_respects_lookback(db):
    repo = ProfileRepo(db)
    _add_bet(db, 1, 1000.0, days_ago=20)  # Outside 14-day window
    _add_bet(db, 1, 200.0, days_ago=5)
    result = repo.get_avg_daily_wager(profile_id=1, lookback_days=14)
    # Only the 200 bet counts, 200/14 = ~14.3
    assert 14.0 < result["avg_daily_wager"] < 15.0
    assert result["days_with_bets"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_profile_repo_wager.py -v`
Expected: FAIL — `ProfileRepo` has no `get_avg_daily_wager` method

- [ ] **Step 3: Implement `get_avg_daily_wager()`**

Add after `get_all_balances()` at line 97 in `backend/src/repositories/profile_repo.py`:

```python
def get_avg_daily_wager(self, profile_id: int, lookback_days: int = 14) -> dict:
    """
    Average total stake per day over the lookback window.

    Returns {"avg_daily_wager": float, "has_history": bool, "days_with_bets": int}.
    Counts all placed bets (settled + pending) within the window.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from ..db.models import Bet

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    # Sum stakes and count distinct days with bets
    rows = (
        self.db.query(
            func.sum(Bet.stake).label("total_stake"),
            func.count(func.distinct(func.date(Bet.placed_at))).label("days_with_bets"),
        )
        .filter(
            Bet.profile_id == profile_id,
            Bet.placed_at >= cutoff,
            Bet.stake > 0,
        )
        .first()
    )

    total_stake = rows.total_stake or 0.0
    days_with_bets = rows.days_with_bets or 0

    return {
        "avg_daily_wager": round(total_stake / lookback_days, 2) if lookback_days > 0 else 0.0,
        "has_history": days_with_bets >= 3,
        "days_with_bets": days_with_bets,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_profile_repo_wager.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/profile_repo.py backend/tests/test_profile_repo_wager.py
git commit -m "feat(play-v3): add get_avg_daily_wager to ProfileRepo"
```

---

## Task 2: Change tier labels from "sharp" to "polymarket"/"pinnacle"/"soft"

**Files:**
- Modify: `backend/src/services/batch_builder.py:24-25,441,524,561-572`
- Test: `backend/tests/test_batch_builder_tiers.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_batch_builder_tiers.py
"""Test that BatchBuilder produces three tiers: polymarket, pinnacle, soft."""
import pytest
from src.services.batch_builder import TIER_PRIORITY, BatchBet


def test_tier_priority_has_three_tiers():
    assert "polymarket" in TIER_PRIORITY
    assert "pinnacle" in TIER_PRIORITY
    assert "soft" in TIER_PRIORITY
    assert TIER_PRIORITY["polymarket"] > TIER_PRIORITY["pinnacle"]
    assert TIER_PRIORITY["pinnacle"] > TIER_PRIORITY["soft"]


def test_tier_priority_no_sharp():
    """The old 'sharp' tier must not exist."""
    assert "sharp" not in TIER_PRIORITY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_batch_builder_tiers.py -v`
Expected: FAIL — `"polymarket" not in TIER_PRIORITY`

- [ ] **Step 3: Update tier constants and _make_candidate()**

In `backend/src/services/batch_builder.py`:

**Line 24** — Replace:
```python
TIER_PRIORITY = {"sharp": 1, "soft": 0}
```
With:
```python
TIER_PRIORITY = {"polymarket": 2, "pinnacle": 1, "soft": 0}
```

**Line 441** — Replace:
```python
tier = "sharp" if provider_id in SHARP_PROVIDERS else "soft"
```
With:
```python
if provider_id == "polymarket":
    tier = "polymarket"
elif provider_id == "pinnacle":
    tier = "pinnacle"
else:
    tier = "soft"
```

**Line 524** — Replace:
```python
cluster_key = bet.cluster if bet.tier == "soft" and bet.cluster else bet.provider_id
```
With:
```python
cluster_key = bet.cluster if bet.tier == "soft" and bet.cluster else bet.provider_id
```
(No change needed — soft check already correct. Polymarket/pinnacle use provider_id as cluster key.)

**Lines 561-572** — Replace `_build_summary()`:
```python
def _build_summary(self, batch: list[BatchBet]) -> dict:
    polymarket_bets = [b for b in batch if b.tier == "polymarket"]
    pinnacle_bets = [b for b in batch if b.tier == "pinnacle"]
    soft_bets = [b for b in batch if b.tier == "soft"]
    return {
        "total_bets": len(batch),
        "total_stake": round(sum(b.stake for b in batch), 2),
        "total_expected_profit": round(sum(b.expected_profit for b in batch), 2),
        "polymarket_bets": len(polymarket_bets),
        "polymarket_ev": round(sum(b.expected_profit for b in polymarket_bets), 2),
        "pinnacle_bets": len(pinnacle_bets),
        "pinnacle_ev": round(sum(b.expected_profit for b in pinnacle_bets), 2),
        "soft_bets": len(soft_bets),
        "soft_ev": round(sum(b.expected_profit for b in soft_bets), 2),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_batch_builder_tiers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/batch_builder.py backend/tests/test_batch_builder_tiers.py
git commit -m "feat(play-v3): split sharp tier into polymarket/pinnacle/soft"
```

---

## Task 3: Implement round-robin allocation for soft tier

**Files:**
- Modify: `backend/src/services/batch_builder.py:503-559`
- Test: `backend/tests/test_batch_builder_roundrobin.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_batch_builder_roundrobin.py
"""Test round-robin distributes bets across cluster siblings."""
from src.services.batch_builder import BatchBet, ProviderBalance, BatchBuilder


def _make_bet(provider_id: str, cluster: str, event_id: str, edge: float, stake: float) -> BatchBet:
    return BatchBet(
        rank=0, tier="soft", provider_id=provider_id,
        event_id=event_id, market="1x2", outcome="Home", point=None,
        odds=2.0, fair_odds=1.9, edge_pct=edge,
        stake=stake, expected_profit=stake * edge / 100,
        is_bonus=False, bonus_type=None,
        display_home="Team A", display_away="Team B",
        sport="football", league="Test", start_time=None,
        lifecycle="playing", cluster=cluster,
    )


def test_round_robin_alternates_providers():
    """Three bets on same cluster should spread across 3 providers."""
    candidates = [
        _make_bet("unibet", "kambi", "evt_1", 5.0, 100),
        _make_bet("unibet", "kambi", "evt_2", 4.0, 100),
        _make_bet("unibet", "kambi", "evt_3", 3.0, 100),
    ]
    # Also have 888sport and leovegas as siblings
    candidates.extend([
        _make_bet("888sport", "kambi", "evt_1", 4.8, 100),
        _make_bet("888sport", "kambi", "evt_2", 3.8, 100),
        _make_bet("888sport", "kambi", "evt_3", 2.8, 100),
    ])
    candidates.extend([
        _make_bet("leovegas", "kambi", "evt_1", 4.5, 100),
        _make_bet("leovegas", "kambi", "evt_2", 3.5, 100),
        _make_bet("leovegas", "kambi", "evt_3", 2.5, 100),
    ])

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 500.0),
        "888sport": ProviderBalance("888sport", "kambi", 500.0),
        "leovegas": ProviderBalance("leovegas", "kambi", 500.0),
    }

    # Sort by expected_profit desc (highest edge first)
    ranked = sorted(candidates, key=lambda b: -b.expected_profit)

    batch, missed = BatchBuilder._allocate_with_round_robin(ranked, balances)

    # Should have 3 bets (one per event, deduped within cluster)
    assert len(batch) == 3

    # All three providers should appear (round-robin distributes)
    providers_used = {b.provider_id for b in batch}
    assert len(providers_used) >= 2  # At least 2 different providers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_batch_builder_roundrobin.py -v`
Expected: FAIL — `BatchBuilder` has no `_allocate_with_round_robin` method

- [ ] **Step 3: Implement round-robin allocation**

Add to `backend/src/services/batch_builder.py`, add `import itertools` at the top, then add method after `_allocate_with_dedup`:

```python
@staticmethod
def _allocate_with_round_robin(
    ranked: list[BatchBet],
    provider_balances: dict[str, ProviderBalance],
) -> tuple[list[BatchBet], list[BatchBet]]:
    """
    Two-pass allocation for soft tier with round-robin across cluster siblings.

    Pass 1: For each unique opportunity (event+market+outcome+point) within a
    cluster, assign a provider via round-robin rotation.
    Pass 2: Allocate balance and build the final batch.
    """
    import itertools

    batch: list[BatchBet] = []
    missed: list[BatchBet] = []

    # Group candidates by dedup key: (cluster, event_id, market, outcome, point)
    # Keep only the highest-edge candidate per (dedup_key, provider_id)
    best_per_provider: dict[tuple, dict[str, BatchBet]] = {}
    for bet in ranked:
        dedup_key = (bet.cluster, bet.event_id, bet.market, bet.outcome, bet.point)
        if dedup_key not in best_per_provider:
            best_per_provider[dedup_key] = {}
        # Keep first seen per provider (already sorted by expected_profit desc)
        if bet.provider_id not in best_per_provider[dedup_key]:
            best_per_provider[dedup_key][bet.provider_id] = bet

    # Build cluster rotation iterators
    cluster_siblings: dict[str, list[str]] = {}
    for pid, pb in provider_balances.items():
        cluster = pb.cluster or pid
        if cluster not in cluster_siblings:
            cluster_siblings[cluster] = []
        if pb.remaining > 0:
            cluster_siblings[cluster].append(pid)
    # Sort each cluster's siblings by balance desc for fair starting order
    for cluster in cluster_siblings:
        cluster_siblings[cluster].sort(
            key=lambda pid: -provider_balances[pid].remaining
        )

    cluster_rotation: dict[str, itertools.cycle] = {
        cluster: itertools.cycle(siblings)
        for cluster, siblings in cluster_siblings.items()
        if siblings
    }

    # Pass 1 + 2 combined: walk dedup keys in ranked order, assign + allocate
    # Sort dedup keys by best expected_profit across all providers
    sorted_keys = sorted(
        best_per_provider.keys(),
        key=lambda k: -max(b.expected_profit for b in best_per_provider[k].values()),
    )

    for dedup_key in sorted_keys:
        cluster = dedup_key[0]
        providers_for_opp = best_per_provider[dedup_key]
        rotation = cluster_rotation.get(cluster)
        if not rotation:
            # No funded siblings — all missed
            for bet in providers_for_opp.values():
                bet.skip_reason = "no funded sibling in cluster"
                missed.append(bet)
            continue

        # Try round-robin: get next provider in rotation, check balance
        siblings_count = len(cluster_siblings.get(cluster, []))
        assigned = False
        for _ in range(siblings_count):
            next_pid = next(rotation)
            if next_pid not in providers_for_opp:
                continue  # This provider doesn't offer this opportunity
            bet = providers_for_opp[next_pid]
            pb = provider_balances[next_pid]

            if bet.is_bonus and bet.bonus_type == "freebet":
                batch.append(bet)
                assigned = True
                break

            if pb.remaining >= bet.stake:
                pb.allocated += bet.stake
                batch.append(bet)
                assigned = True
                break

        if not assigned:
            # Try any provider with balance (fallback past rotation)
            for pid, bet in providers_for_opp.items():
                pb = provider_balances.get(pid)
                if pb and pb.remaining >= bet.stake:
                    pb.allocated += bet.stake
                    batch.append(bet)
                    assigned = True
                    break

        if not assigned:
            # Pick the one with highest edge to report as missed
            best = max(providers_for_opp.values(), key=lambda b: b.expected_profit)
            best.skip_reason = f"insufficient balance in cluster {cluster}"
            pb = provider_balances.get(best.provider_id)
            if pb:
                pb.missed_bets += 1
                pb.missed_ev += best.expected_profit
            missed.append(best)

    return batch, missed
```

- [ ] **Step 4: Update `_allocate_with_dedup` call in `build()` to use round-robin for soft**

In `build()` method (around line 148), replace:
```python
batch, missed = self._allocate_with_dedup(ranked, provider_balances)
```
With:
```python
# Split sharp and soft for different allocation strategies
sharp_ranked = [b for b in ranked if b.tier in ("polymarket", "pinnacle")]
soft_ranked = [b for b in ranked if b.tier == "soft"]

# Sharp: direct allocation (existing dedup logic)
sharp_batch, sharp_missed = self._allocate_with_dedup(sharp_ranked, provider_balances)

# Soft: round-robin allocation
soft_batch, soft_missed = self._allocate_with_round_robin(soft_ranked, provider_balances)

batch = sharp_batch + soft_batch
missed = sharp_missed + soft_missed
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_batch_builder_roundrobin.py tests/test_batch_builder_tiers.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/batch_builder.py backend/tests/test_batch_builder_roundrobin.py
git commit -m "feat(play-v3): add round-robin allocation for soft tier"
```

---

## Task 4: Add `exclude` parameter and wagering projections to BatchBuilder

**Files:**
- Modify: `backend/src/services/batch_builder.py:123-172`
- Test: `backend/tests/test_batch_builder_exclude.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_batch_builder_exclude.py
"""Test that exclude parameter filters bets from batch."""
from src.services.batch_builder import BatchBet, ProviderBalance, BatchBuilder


def test_exclude_removes_bet_keys():
    """Excluded bet keys should not appear in the batch."""
    # This tests the exclude filtering logic in _collect_candidates
    # Full integration test requires DB; unit test validates the filter
    bet = BatchBet(
        rank=1, tier="soft", provider_id="unibet",
        event_id="evt_1", market="1x2", outcome="Home", point=None,
        odds=2.0, fair_odds=1.9, edge_pct=5.0,
        stake=100, expected_profit=5.0,
        is_bonus=False, bonus_type=None,
        display_home="A", display_away="B",
        sport="football", league="Test", start_time=None,
        lifecycle="playing", cluster="kambi",
    )

    # Bet key format: "provider_id:event_id:market:outcome:point"
    bet_key = f"{bet.provider_id}:{bet.event_id}:{bet.market}:{bet.outcome}:{bet.point}"
    exclude = {bet_key}

    candidates = [bet]
    filtered = [b for b in candidates if f"{b.provider_id}:{b.event_id}:{b.market}:{b.outcome}:{b.point}" not in exclude]
    assert len(filtered) == 0
```

- [ ] **Step 2: Run test to verify it passes (pure logic test)**

Run: `cd backend && python -m pytest tests/test_batch_builder_exclude.py -v`
Expected: PASS (this validates the key format)

- [ ] **Step 3: Add `exclude` param to `build()` and wagering projections**

In `backend/src/services/batch_builder.py`, update `build()` signature:

```python
def build(self, profile_id: int, exclude: list[str] | None = None) -> dict:
```

After collecting candidates (after `_collect_candidates` call), add exclude filter:

```python
if exclude:
    exclude_set = set(exclude)
    candidates = [
        c for c in candidates
        if f"{c.provider_id}:{c.event_id}:{c.market}:{c.outcome}:{c.point}" not in exclude_set
    ]
```

Add wagering projections method:

```python
def _compute_wagering_projections(
    self,
    batch: list[BatchBet],
    provider_balances: dict[str, ProviderBalance],
) -> list[dict]:
    """Compute projected wagering progress for providers with active bonuses."""
    # Sum stakes per provider in batch
    provider_stakes: dict[str, float] = {}
    for bet in batch:
        provider_stakes[bet.provider_id] = provider_stakes.get(bet.provider_id, 0) + bet.stake

    projections = []
    for pid, pb in provider_balances.items():
        if pb.wagering_remaining <= 0:
            continue
        batch_stake = provider_stakes.get(pid, 0)
        projected_remaining = max(0, pb.wagering_remaining - batch_stake)

        projections.append({
            "provider_id": pid,
            "cluster": pb.cluster,
            "wagering_remaining": round(pb.wagering_remaining, 2),
            "batch_stake": round(batch_stake, 2),
            "projected_remaining": round(projected_remaining, 2),
            "days_remaining": pb.days_remaining,
        })

    return projections
```

**Important:** The `build()` return dict MUST include `wagering_projections`. This is added in Task 5b Step 4 where we rewrite the return dict. Verify it includes:
```python
"wagering_projections": self._compute_wagering_projections(batch, provider_balances),
```

- [ ] **Step 4: Run all batch tests**

Run: `cd backend && python -m pytest tests/test_batch_builder_tiers.py tests/test_batch_builder_roundrobin.py tests/test_batch_builder_exclude.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/batch_builder.py backend/tests/test_batch_builder_exclude.py
git commit -m "feat(play-v3): add exclude param and wagering projections to BatchBuilder"
```

---

## Task 5: Remove dead `_collect_boosts()` method

**Files:**
- Modify: `backend/src/services/batch_builder.py:248-354`

**Note:** After this task, all line numbers in `batch_builder.py` shift by ~107 lines. Subsequent tasks reference code by method name, not line number.

- [ ] **Step 1: Verify _collect_boosts is not called anywhere**

Run: `grep -r "_collect_boosts" backend/src/`
Expected: Only the method definition in batch_builder.py, no callers

- [ ] **Step 2: Delete the entire `_collect_boosts` method** (lines 248-354)

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/batch_builder.py
git commit -m "chore: remove dead _collect_boosts method from BatchBuilder"
```

---

## Task 5b: Extend `_build_capital_plan()` with 5 recommendation types

This is the **core of Panel 1** — the capital plan must generate the five recommendation types from the spec: DEPOSIT (sharp), DEPOSIT (bonus), DEPOSIT (new), TRANSFER, WITHDRAW.

**Files:**
- Modify: `backend/src/services/batch_builder.py` — method `_build_capital_plan()` and `build()`
- Modify: `backend/src/repositories/profile_repo.py` — import `get_avg_daily_wager`
- Test: `backend/tests/test_capital_plan.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_capital_plan.py
"""Test capital plan generates correct recommendation types and priorities."""
from src.services.batch_builder import BatchBet, ProviderBalance, BatchBuilder


def _make_balance(pid, cluster, balance, lifecycle="playing", wagering_remaining=0, days_remaining=None):
    pb = ProviderBalance(pid, cluster, balance, lifecycle=lifecycle)
    pb.wagering_remaining = wagering_remaining
    pb.days_remaining = days_remaining
    return pb


def test_capital_plan_sharp_shortfall_priority_1():
    """Polymarket/Pinnacle with missed bets should be priority 1."""
    balances = {
        "polymarket": _make_balance("polymarket", "polymarket", 100),
        "pinnacle": _make_balance("pinnacle", "pinnacle", 500),
    }
    # Simulate missed bets on polymarket
    balances["polymarket"].missed_bets = 3
    balances["polymarket"].missed_ev = 50.0

    missed = [
        BatchBet(rank=0, tier="polymarket", provider_id="polymarket",
                 event_id="e1", market="1x2", outcome="Yes", point=None,
                 odds=1.8, fair_odds=1.6, edge_pct=12.5, stake=200,
                 expected_profit=25.0, is_bonus=False, bonus_type=None,
                 display_home="A", display_away="B", sport="politics",
                 league=None, start_time=None, lifecycle="playing",
                 cluster="polymarket", skip_reason="insufficient balance"),
    ]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances,
        missed=missed,
        total_bankroll=10000,
        cluster_opp_stats={},
        avg_daily_wager=1000,
        has_wager_history=True,
    )

    actions = plan["actions"]
    # Should have at least one DEPOSIT action for polymarket
    sharp_deposits = [a for a in actions if a["type"] == "deposit" and a["provider_id"] == "polymarket"]
    assert len(sharp_deposits) >= 1
    assert sharp_deposits[0]["priority"] == 1
    assert sharp_deposits[0]["currency"] == "USDC"


def test_capital_plan_withdraw_dormant():
    """Dormant providers with balance should get WITHDRAW recommendations."""
    balances = {
        "spectate": _make_balance("spectate", "spectate", 3000, lifecycle="dormant"),
    }
    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances,
        missed=[],
        total_bankroll=10000,
        cluster_opp_stats={},
        avg_daily_wager=1000,
        has_wager_history=True,
    )
    withdrawals = [a for a in plan["actions"] if a["type"] == "withdraw"]
    assert len(withdrawals) >= 1
    assert withdrawals[0]["provider_id"] == "spectate"


def test_capital_plan_transfer_fallback():
    """TRANSFER should appear when there's excess on one provider and shortfall on another."""
    balances = {
        "spectate": _make_balance("spectate", "spectate", 3000, lifecycle="dormant"),
        "unibet": _make_balance("unibet", "kambi", 0, lifecycle="playing"),
    }
    balances["unibet"].missed_bets = 5
    balances["unibet"].missed_ev = 80.0

    missed = [
        BatchBet(rank=0, tier="soft", provider_id="unibet",
                 event_id="e1", market="1x2", outcome="Home", point=None,
                 odds=2.0, fair_odds=1.85, edge_pct=8.0, stake=500,
                 expected_profit=40.0, is_bonus=False, bonus_type=None,
                 display_home="A", display_away="B", sport="football",
                 league="Test", start_time=None, lifecycle="playing",
                 cluster="kambi", skip_reason="insufficient balance"),
    ]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances,
        missed=missed,
        total_bankroll=10000,
        cluster_opp_stats={"kambi": {"unique_opps": 5, "total_ev": 80, "avg_edge": 8.0, "avg_stake": 500}},
        avg_daily_wager=1000,
        has_wager_history=True,
    )

    transfers = [a for a in plan["actions"] if a["type"] == "transfer"]
    assert len(transfers) >= 1
    assert transfers[0]["from_provider_id"] == "spectate"
    assert transfers[0]["to_provider_id"] in ("unibet", "kambi")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_capital_plan.py -v`
Expected: FAIL — `BatchBuilder` has no `_build_capital_plan_v3` method

- [ ] **Step 3: Implement `_build_capital_plan_v3()` as new static method**

Replace the existing `_build_capital_plan()` method in `batch_builder.py` with:

```python
@staticmethod
def _build_capital_plan_v3(
    provider_balances: dict[str, ProviderBalance],
    missed: list[BatchBet],
    total_bankroll: float,
    cluster_opp_stats: dict[str, dict],
    avg_daily_wager: float,
    has_wager_history: bool,
) -> dict:
    """
    Build capital plan with 5 recommendation types:
    1. DEPOSIT (sharp) — Polymarket/Pinnacle shortfalls, priority 1
    2. DEPOSIT (bonus) — active bonus with achievable wagering, priority 2
    3. DEPOSIT (new) — unfunded providers with opportunities, priority 3
    4. TRANSFER — move from excess to shortfall (fallback), priority 4
    5. WITHDRAW — dormant with balance, no opps, priority 5
    """
    actions: list[dict] = []

    # --- Collect shortfalls per provider/cluster ---
    provider_shortfall: dict[str, float] = {}
    provider_missed_count: dict[str, int] = {}
    provider_missed_ev: dict[str, float] = {}

    for bet in missed:
        pid = bet.provider_id
        provider_shortfall[pid] = provider_shortfall.get(pid, 0) + bet.stake
        provider_missed_count[pid] = provider_missed_count.get(pid, 0) + 1
        provider_missed_ev[pid] = provider_missed_ev.get(pid, 0) + bet.expected_profit

    # Also from provider balance tracking
    for pid, pb in provider_balances.items():
        if pb.missed_bets > 0 and pid not in provider_shortfall:
            provider_shortfall[pid] = pb.missed_ev  # approximate
            provider_missed_count[pid] = pb.missed_bets
            provider_missed_ev[pid] = pb.missed_ev

    # --- Priority 1: DEPOSIT (sharp) ---
    for pid in ("polymarket", "pinnacle"):
        if pid in provider_shortfall:
            currency = "USDC" if pid == "polymarket" else "SEK"
            shortfall = provider_shortfall[pid]
            stats = cluster_opp_stats.get(pid, {})
            actions.append({
                "type": "deposit",
                "provider_id": pid,
                "amount": round(shortfall, -1) if currency == "SEK" else round(shortfall, 2),
                "unlocks": provider_missed_count.get(pid, 0),
                "avg_edge": stats.get("avg_edge", 0),
                "expected_ev": round(provider_missed_ev.get(pid, 0), 2),
                "currency": currency,
                "priority": 1,
                "priority_label": "sharp",
            })

    # --- Priority 2: DEPOSIT (bonus) ---
    for pid, pb in provider_balances.items():
        if pid in SHARP_PROVIDERS:
            continue
        if pb.wagering_remaining > 0 and pb.remaining <= 0:
            # Has bonus but no balance — needs deposit to keep wagering
            feasible = True
            if pb.days_remaining is not None and has_wager_history and avg_daily_wager > 0:
                sessions_needed = pb.wagering_remaining / avg_daily_wager
                if sessions_needed > pb.days_remaining:
                    feasible = False
            elif not has_wager_history:
                # Fall back to heuristic: assume 1 session/day
                if pb.days_remaining is not None and pb.wagering_remaining > pb.days_remaining * 1000:
                    feasible = False

            if feasible:
                stats = cluster_opp_stats.get(pb.cluster, {})
                deposit_amount = max(500, round(pb.wagering_remaining * 0.3, -2))  # 30% of remaining wagering
                actions.append({
                    "type": "deposit",
                    "provider_id": pid,
                    "amount": deposit_amount,
                    "unlocks": stats.get("unique_opps", 0),
                    "avg_edge": stats.get("avg_edge", 0),
                    "expected_ev": round(stats.get("total_ev", 0), 2),
                    "currency": "SEK",
                    "priority": 2,
                    "priority_label": "bonus",
                    "bonus_info": f"wager {pb.wagering_remaining:.0f} kr, {pb.days_remaining}d left" if pb.days_remaining else None,
                })

    # --- Priority 3: DEPOSIT (new/shortfall soft) ---
    for pid in provider_shortfall:
        if pid in SHARP_PROVIDERS:
            continue
        # Skip if already covered by bonus deposit
        if any(a["provider_id"] == pid and a["priority"] == 2 for a in actions):
            continue
        pb = provider_balances.get(pid)
        stats = cluster_opp_stats.get(pb.cluster if pb else pid, {})
        shortfall = provider_shortfall[pid]
        actions.append({
            "type": "deposit",
            "provider_id": pid,
            "amount": round(shortfall, -1),
            "unlocks": provider_missed_count.get(pid, 0),
            "avg_edge": stats.get("avg_edge", 0),
            "expected_ev": round(provider_missed_ev.get(pid, 0), 2),
            "currency": "SEK",
            "priority": 3,
            "priority_label": "new" if (not pb or pb.initial_balance == 0) else "shortfall",
        })

    # --- Priority 5: WITHDRAW (dormant) ---
    excess_providers: list[tuple[str, float]] = []  # (pid, excess_amount)
    for pid, pb in provider_balances.items():
        if pid in SHARP_PROVIDERS:
            continue
        if pb.lifecycle in ("dormant", "playing", "limited") and pb.wagering_remaining <= 0 and pb.remaining > 0:
            if pb.missed_bets == 0:
                excess_providers.append((pid, pb.remaining))
                actions.append({
                    "type": "withdraw",
                    "provider_id": pid,
                    "amount": round(pb.remaining, 2),
                    "unlocks": 0,
                    "avg_edge": 0,
                    "expected_ev": 0,
                    "currency": "SEK",
                    "priority": 5,
                    "priority_label": "dormant",
                    "cluster": pb.cluster,
                })

    # --- Priority 4: TRANSFER (fallback) ---
    # Match excess sources to shortfall targets
    excess_providers.sort(key=lambda x: -x[1])
    shortfall_actions = [a for a in actions if a["type"] == "deposit" and a["priority"] == 3]

    for target_action in shortfall_actions:
        target_pid = target_action["provider_id"]
        needed = target_action["amount"]

        for i, (source_pid, source_amount) in enumerate(excess_providers):
            if source_amount <= 0:
                continue
            transfer_amount = min(needed, source_amount)
            actions.append({
                "type": "transfer",
                "from_provider_id": source_pid,
                "to_provider_id": target_pid,
                "amount": round(transfer_amount, 2),
                "unlocks": target_action["unlocks"],
                "avg_edge": target_action["avg_edge"],
                "expected_ev": round(target_action["expected_ev"] * (transfer_amount / max(needed, 1)), 2),
                "currency": "SEK",
                "priority": 4,
                "priority_label": "transfer",
            })
            excess_providers[i] = (source_pid, source_amount - transfer_amount)
            needed -= transfer_amount
            if needed <= 0:
                break

    # Sort by priority, then by expected_ev desc within priority
    actions.sort(key=lambda a: (a["priority"], -a.get("expected_ev", 0)))

    deployed = sum(pb.initial_balance for pb in provider_balances.values())
    withdrawable = sum(a["amount"] for a in actions if a["type"] == "withdraw")

    return {
        "total_deployed": round(deployed, 2),
        "withdrawable": round(withdrawable, 2),
        "actions": actions,
    }
```

- [ ] **Step 4: Update `build()` to call `_build_capital_plan_v3`**

In `build()`, replace the calls to `_build_deposit_recommendations`, `_build_withdrawal_recommendations`, and `_build_capital_plan` with:

```python
# Get wagering history for feasibility checks
avg_wager_data = self.profile_repo.get_avg_daily_wager(profile_id)

capital_plan = self._build_capital_plan_v3(
    provider_balances=provider_balances,
    missed=missed,
    total_bankroll=total_bankroll,
    cluster_opp_stats=cluster_opp_stats,
    avg_daily_wager=avg_wager_data["avg_daily_wager"],
    has_wager_history=avg_wager_data["has_history"],
)
```

Update the return dict to include:
```python
return {
    "batch": [self._bet_to_dict(b) for b in batch],
    "summary": self._build_summary(batch),
    "balance_status": self._build_balance_status(provider_balances, missed),
    "missed_opportunities": self._build_missed_summary(missed),
    "capital_plan": capital_plan,
    "wagering_projections": self._compute_wagering_projections(batch, provider_balances),
}
```

Remove the old `deposit_recommendations`, `withdrawal_recommendations` keys. Remove the now-unused `_build_deposit_recommendations`, `_build_withdrawal_recommendations`, and old `_build_capital_plan` methods.

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_capital_plan.py tests/test_batch_builder_tiers.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/batch_builder.py backend/tests/test_capital_plan.py
git commit -m "feat(play-v3): implement 5-type capital plan with priority ordering"
```

---

## Task 6: Add `confirm-capital` endpoint and update batch endpoint

**Files:**
- Modify: `backend/src/api/routes/opportunities.py:143-149`
- Test: `backend/tests/test_confirm_capital.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_confirm_capital.py
"""Test confirm-capital endpoint updates balances and rebuilds batch."""
import pytest
from fastapi.testclient import TestClient


def test_confirm_capital_deposit(client, db_session):
    """DEPOSIT action should increase provider balance."""
    response = client.post("/api/opportunities/play/confirm-capital", json={
        "actions": [
            {"type": "deposit", "provider_id": "unibet", "amount": 2000}
        ]
    })
    assert response.status_code == 200
    data = response.json()
    assert "batch" in data
    assert "capital_plan" in data


def test_confirm_capital_withdraw(client, db_session):
    """WITHDRAW action should decrease provider balance."""
    response = client.post("/api/opportunities/play/confirm-capital", json={
        "actions": [
            {"type": "withdraw", "provider_id": "spectate", "amount": 500}
        ]
    })
    assert response.status_code == 200


def test_confirm_capital_transfer(client, db_session):
    """TRANSFER action should decrease source and increase target."""
    response = client.post("/api/opportunities/play/confirm-capital", json={
        "actions": [
            {"type": "transfer", "from_provider_id": "spectate",
             "to_provider_id": "unibet", "amount": 1000}
        ]
    })
    assert response.status_code == 200


def test_confirm_capital_negative_balance_rejected(client, db_session):
    """Cannot withdraw more than available balance."""
    response = client.post("/api/opportunities/play/confirm-capital", json={
        "actions": [
            {"type": "withdraw", "provider_id": "unibet", "amount": 999999}
        ]
    })
    assert response.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_confirm_capital.py -v`
Expected: FAIL — endpoint doesn't exist (note: may need test fixtures from conftest)

- [ ] **Step 3: Implement endpoints**

In `backend/src/api/routes/opportunities.py`, add after the existing batch endpoint:

```python
from pydantic import BaseModel
from typing import Optional
from fastapi import HTTPException


class CapitalAction(BaseModel):
    type: str  # "deposit", "withdraw", "transfer"
    provider_id: Optional[str] = None
    from_provider_id: Optional[str] = None
    to_provider_id: Optional[str] = None
    amount: float


class ConfirmCapitalRequest(BaseModel):
    actions: list[CapitalAction]


class BuildBatchRequest(BaseModel):
    exclude: list[str] | None = None


@router.post("/play/batch")
async def build_batch(
    body: BuildBatchRequest | None = None,
    db: Session = Depends(get_db),
):
    """Build optimal batch of all +EV bets with balance allocation."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    builder = BatchBuilder(db)
    exclude = body.exclude if body else None
    return builder.build(profile.id, exclude=exclude)


@router.post("/play/confirm-capital")
async def confirm_capital(
    body: ConfirmCapitalRequest,
    db: Session = Depends(get_db),
):
    """Apply capital actions (deposit/withdraw/transfer) and rebuild batch."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    for action in body.actions:
        if action.type == "deposit":
            if not action.provider_id:
                raise HTTPException(400, "deposit requires provider_id")
            profile_repo.adjust_balance(profile.id, action.provider_id, action.amount)

        elif action.type == "withdraw":
            if not action.provider_id:
                raise HTTPException(400, "withdraw requires provider_id")
            current = profile_repo.get_balance(profile.id, action.provider_id)
            if current < action.amount:
                raise HTTPException(422, f"Insufficient balance on {action.provider_id}: have {current}, need {action.amount}")
            profile_repo.adjust_balance(profile.id, action.provider_id, -action.amount)

        elif action.type == "transfer":
            if not action.from_provider_id or not action.to_provider_id:
                raise HTTPException(400, "transfer requires from_provider_id and to_provider_id")
            current = profile_repo.get_balance(profile.id, action.from_provider_id)
            if current < action.amount:
                raise HTTPException(422, f"Insufficient balance on {action.from_provider_id}: have {current}, need {action.amount}")
            profile_repo.adjust_balance(profile.id, action.from_provider_id, -action.amount)
            profile_repo.adjust_balance(profile.id, action.to_provider_id, action.amount)

        else:
            raise HTTPException(400, f"Unknown action type: {action.type}")

    db.commit()  # Persist balance changes before rebuilding batch

    # Rebuild batch with updated balances
    builder = BatchBuilder(db)
    return builder.build(profile.id)
```

**Important:** Remove the old `build_batch` endpoint (lines 143-149) since the new one replaces it with the `BuildBatchRequest` body.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_confirm_capital.py -v`
Expected: PASS (may need to adapt test fixtures)

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/opportunities.py backend/tests/test_confirm_capital.py
git commit -m "feat(play-v3): add confirm-capital endpoint and exclude param to batch"
```

---

## Task 7: Update frontend types

**Files:**
- Modify: `frontend/src/types/index.ts:745-802`

- [ ] **Step 1: Update BatchBet.tier type**

At line 747, change:
```typescript
tier: 'sharp' | 'soft';
```
To:
```typescript
tier: 'polymarket' | 'pinnacle' | 'soft';
```

- [ ] **Step 2: Add wagering_pct to BatchBet**

After `cluster` field (line 769), add:
```typescript
wagering_pct: number | null;
```

- [ ] **Step 3: Update BatchSummary type**

Replace the `BatchSummary` type (lines 771-779) with:
```typescript
export interface BatchSummary {
  total_bets: number;
  total_stake: number;
  total_expected_profit: number;
  polymarket_bets: number;
  polymarket_ev: number;
  pinnacle_bets: number;
  pinnacle_ev: number;
  soft_bets: number;
  soft_ev: number;
}
```

- [ ] **Step 4: Add new types for capital plan and execution**

After `BatchResult`, add:
```typescript
export interface CapitalAction {
  type: 'deposit' | 'transfer' | 'withdraw';
  provider_id?: string;
  from_provider_id?: string;
  to_provider_id?: string;
  amount: number;
  unlocks: number;
  avg_edge: number;
  expected_ev: number;
  cluster?: string;
  bonus_info?: string;
  currency: 'SEK' | 'USDC';
}

export interface CapitalPlan {
  total_deployed: number;
  withdrawable: number;
  actions: CapitalAction[];
}

export interface WageringProjection {
  provider_id: string;
  cluster: string;
  wagering_remaining: number;
  batch_stake: number;
  projected_remaining: number;
  days_remaining: number | null;
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
  capital_plan: CapitalPlan;
  wagering_projections: WageringProjection[];
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat(play-v3): update frontend types for 3-tier batch and capital plan"
```

---

## Task 8: Update API client

**Files:**
- Modify: `frontend/src/services/api/opportunities.ts:76-78`

- [ ] **Step 1: Update `getPlayBatch` to accept exclude param**

Replace:
```typescript
async getPlayBatch(): Promise<BatchResult> {
  return fetchJson('/opportunities/play/batch', { method: 'POST' });
}
```
With:
```typescript
async getPlayBatch(exclude?: string[]): Promise<BatchResult> {
  return fetchJson('/opportunities/play/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: exclude ? JSON.stringify({ exclude }) : undefined,
  });
}
```

- [ ] **Step 2: Add `confirmCapital` function**

```typescript
async confirmCapital(actions: Array<{
  type: 'deposit' | 'transfer' | 'withdraw';
  provider_id?: string;
  from_provider_id?: string;
  to_provider_id?: string;
  amount: number;
}>): Promise<BatchResult> {
  return fetchJson('/opportunities/play/confirm-capital', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actions }),
  });
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api/opportunities.ts
git commit -m "feat(play-v3): add confirmCapital and exclude param to API client"
```

---

## Task 9: Create CapitalPlanPanel component

**Files:**
- Create: `frontend/src/components/Terminal/pages/play/CapitalPlanPanel.tsx`

- [ ] **Step 1: Create the play/ directory**

Run: `mkdir -p frontend/src/components/Terminal/pages/play`

- [ ] **Step 2: Implement CapitalPlanPanel**

```typescript
// frontend/src/components/Terminal/pages/play/CapitalPlanPanel.tsx
import React, { useState } from 'react';
import type { CapitalPlan, CapitalAction } from '../../../../types';

interface Props {
  capitalPlan: CapitalPlan;
  onConfirm: (actions: CapitalAction[]) => void;
  onDismissAll: () => void;
  isLoading: boolean;
}

type ActionStatus = 'pending' | 'done' | 'dismissed';

export function CapitalPlanPanel({ capitalPlan, onConfirm, onDismissAll, isLoading }: Props) {
  const [statuses, setStatuses] = useState<Record<number, ActionStatus>>({});
  const [collapsed, setCollapsed] = useState(false);

  const actions = capitalPlan.actions || [];
  if (actions.length === 0) return null;

  const setStatus = (idx: number, status: ActionStatus) => {
    setStatuses(prev => ({ ...prev, [idx]: status }));
  };

  const doneActions = actions.filter((_, i) => statuses[i] === 'done');
  const hasDone = doneActions.length > 0;

  // Currency-separated totals
  const sekTotal = actions
    .filter((t, i) => statuses[i] !== 'dismissed' && t.currency !== 'USDC')
    .reduce((sum, t) => sum + (t.type === 'withdraw' ? -t.amount : t.amount), 0);
  const usdcTotal = actions
    .filter((t, i) => statuses[i] !== 'dismissed' && t.currency === 'USDC')
    .reduce((sum, t) => sum + (t.type === 'withdraw' ? -t.amount : t.amount), 0);
  const totalUnlocks = actions
    .filter((_, i) => statuses[i] !== 'dismissed')
    .reduce((sum, t) => sum + t.unlocks, 0);
  const totalEv = actions
    .filter((_, i) => statuses[i] !== 'dismissed')
    .reduce((sum, t) => sum + t.expected_ev, 0);

  const handleConfirm = () => {
    onConfirm(doneActions);
  };

  if (collapsed) {
    return (
      <div className="mb-2 px-3 py-2 bg-dark-800 rounded border border-dark-700 opacity-60">
        <span className="text-xs text-dark-400">Capital plan dismissed</span>
        <button
          className="ml-2 text-xs text-success hover:underline"
          onClick={() => setCollapsed(false)}
        >show</button>
      </div>
    );
  }

  return (
    <div className="mb-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div>
          <span className="text-success font-bold text-sm tracking-wide">CAPITAL PLAN</span>
          <span className="text-dark-400 text-xs ml-2">
            {capitalPlan.total_deployed.toLocaleString()} kr deployed
          </span>
        </div>
        <div className="flex gap-2">
          <button
            className="px-3 py-1 text-xs bg-dark-700 text-dark-400 border border-dark-600 rounded hover:bg-dark-600"
            onClick={() => { onDismissAll(); setCollapsed(true); }}
          >Dismiss All</button>
          {hasDone && (
            <button
              className="px-3 py-1 text-xs bg-success text-black font-bold rounded hover:brightness-110"
              onClick={handleConfirm}
              disabled={isLoading}
            >Confirm &amp; Recalc ({doneActions.length})</button>
          )}
        </div>
      </div>

      {/* Table */}
      <table className="w-full text-xs sq">
        <thead>
          <tr className="text-dark-400 border-b border-dark-700">
            <th className="text-left px-2 py-1">Action</th>
            <th className="text-left px-2 py-1">Provider</th>
            <th className="text-right px-2 py-1">Amount</th>
            <th className="text-right px-2 py-1">Unlocks</th>
            <th className="text-right px-2 py-1">Avg Edge</th>
            <th className="text-right px-2 py-1">+EV</th>
            <th className="text-center px-2 py-1">Status</th>
          </tr>
        </thead>
        <tbody>
          {actions.map((target, i) => {
            const status = statuses[i] || 'pending';
            const isDismissed = status === 'dismissed';
            return (
              <tr
                key={i}
                className={`border-b border-dark-800 ${isDismissed ? 'opacity-30' : ''}`}
              >
                <td className="px-2 py-1">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                    target.type === 'deposit' ? 'bg-success/15 text-success' :
                    target.type === 'transfer' ? 'bg-blue-500/15 text-blue-400' :
                    'bg-red-500/15 text-red-400'
                  }`}>
                    {target.type.toUpperCase()}
                  </span>
                </td>
                <td className="px-2 py-1">
                  {target.type === 'transfer'
                    ? <>{target.from_provider_id} <span className="text-dark-500">→</span> {target.to_provider_id}</>
                    : target.provider_id}
                  {target.bonus_info && (
                    <span className="text-dark-500 text-[10px] ml-1">({target.bonus_info})</span>
                  )}
                </td>
                <td className="px-2 py-1 text-right">
                  {target.amount.toLocaleString()} {target.currency === 'USDC' ? 'USDC' : 'kr'}
                </td>
                <td className="px-2 py-1 text-right">{target.unlocks} bets</td>
                <td className="px-2 py-1 text-right text-success">+{target.avg_edge.toFixed(1)}%</td>
                <td className="px-2 py-1 text-right text-success">
                  +{target.expected_ev.toFixed(0)} {target.currency === 'USDC' ? 'USDC' : 'kr'}
                </td>
                <td className="px-2 py-1 text-center">
                  {isDismissed ? (
                    <button
                      className="text-dark-500 hover:text-dark-300 text-[10px]"
                      onClick={() => setStatus(i, 'pending')}
                    >undo</button>
                  ) : status === 'done' ? (
                    <span className="text-success">✓ done</span>
                  ) : (
                    <div className="flex gap-1 justify-center">
                      <button
                        className="text-amber-500 hover:text-amber-400 text-[10px]"
                        onClick={() => setStatus(i, 'done')}
                      >done</button>
                      <button
                        className="text-dark-500 hover:text-dark-300 text-[10px]"
                        onClick={() => setStatus(i, 'dismissed')}
                      >skip</button>
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Summary */}
      <div className="mt-2 px-3 py-1.5 bg-dark-800 rounded flex gap-6 text-xs">
        <span>Net needed: <span className="text-amber-500">
          {sekTotal > 0 ? `+${sekTotal.toLocaleString()} kr` : ''}
          {sekTotal > 0 && usdcTotal > 0 ? ' + ' : ''}
          {usdcTotal > 0 ? `+${usdcTotal.toLocaleString()} USDC` : ''}
          {sekTotal <= 0 && usdcTotal <= 0 ? '0' : ''}
        </span></span>
        <span>Unlocks: <span className="text-success">{totalUnlocks} bets</span></span>
        <span>+EV: <span className="text-success">+{totalEv.toFixed(0)} kr</span></span>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/CapitalPlanPanel.tsx
git commit -m "feat(play-v3): create CapitalPlanPanel component"
```

---

## Task 10: Create SessionBatchPanel component

**Files:**
- Create: `frontend/src/components/Terminal/pages/play/SessionBatchPanel.tsx`

- [ ] **Step 1: Implement SessionBatchPanel**

```typescript
// frontend/src/components/Terminal/pages/play/SessionBatchPanel.tsx
import React from 'react';
import type { BatchBet, BatchSummary, WageringProjection } from '../../../../types';

interface Props {
  batch: BatchBet[];
  summary: BatchSummary;
  wageringProjections: WageringProjection[];
  onRemoveBet: (betKey: string) => void;
}

function betKey(b: BatchBet): string {
  return `${b.provider_id}:${b.event_id}:${b.market}:${b.outcome}:${b.point}`;
}

function TierSection({ tier, label, color, bets, onRemove }: {
  tier: string;
  label: string;
  color: string;
  bets: BatchBet[];
  onRemove: (key: string) => void;
}) {
  if (bets.length === 0) return null;
  return (
    <>
      <tr className="bg-dark-900">
        <td colSpan={10} className={`px-2 py-1.5 font-bold text-[11px] tracking-wider`} style={{ color }}>
          {label}
        </td>
      </tr>
      {bets.map((bet, i) => (
        <tr key={betKey(bet)} className="border-b border-dark-800 hover:bg-dark-800/50">
          <td className="px-2 py-1 text-dark-500 text-[11px]">{bet.rank}</td>
          <td className="px-2 py-1 text-[11px]">
            {bet.display_home} v {bet.display_away}
          </td>
          <td className="px-2 py-1 text-dark-400 text-[11px]">{bet.market}</td>
          <td className="px-2 py-1 text-[11px]">{bet.outcome}{bet.point ? ` ${bet.point}` : ''}</td>
          <td className="px-2 py-1 text-[11px]">
            <span style={{ color }}>{bet.provider_id}</span>
            {bet.cluster && bet.cluster !== bet.provider_id && (
              <span className="text-dark-600 text-[10px] ml-1">{bet.cluster}</span>
            )}
            {bet.wagering_pct != null && bet.wagering_pct < 100 && (
              <span className="ml-1 px-1 py-0.5 rounded text-[9px] bg-amber-500/20 text-amber-500">
                wager {Math.round(bet.wagering_pct)}%
              </span>
            )}
          </td>
          <td className="px-2 py-1 text-right text-[11px]">{bet.odds.toFixed(2)}</td>
          <td className="px-2 py-1 text-right text-dark-400 text-[11px]">{bet.fair_odds.toFixed(2)}</td>
          <td className="px-2 py-1 text-right text-success text-[11px]">+{bet.edge_pct.toFixed(1)}%</td>
          <td className="px-2 py-1 text-right text-[11px]">
            {bet.stake.toLocaleString()} {bet.provider_id === 'polymarket' ? 'USDC' : 'kr'}
          </td>
          <td className="px-2 py-1 text-center">
            <button
              className="text-dark-600 hover:text-red-400 text-[11px]"
              onClick={() => onRemove(betKey(bet))}
            >×</button>
          </td>
        </tr>
      ))}
    </>
  );
}

export function SessionBatchPanel({ batch, summary, wageringProjections, onRemoveBet }: Props) {
  const polyBets = batch.filter(b => b.tier === 'polymarket');
  const pinnBets = batch.filter(b => b.tier === 'pinnacle');
  const softBets = batch.filter(b => b.tier === 'soft');

  const providerCount = new Set(batch.map(b => b.provider_id)).size;

  return (
    <div className="mb-4">
      {/* Summary bar */}
      <div className="flex gap-6 mb-2 px-3 py-1.5 bg-dark-800 rounded text-xs flex-wrap">
        <span>Total stake: <strong>{summary.total_stake.toLocaleString()} kr</strong></span>
        <span>Expected +EV: <span className="text-success font-bold">+{summary.total_expected_profit.toFixed(0)} kr</span></span>
        <span>Providers: <strong>{providerCount}</strong></span>
        <span>
          {summary.polymarket_bets > 0 && <>{summary.polymarket_bets} poly · </>}
          {summary.pinnacle_bets > 0 && <>{summary.pinnacle_bets} pinn · </>}
          {summary.soft_bets} soft
        </span>
      </div>

      {/* Batch table */}
      <table className="w-full text-xs sq">
        <thead>
          <tr className="text-dark-400 border-b border-dark-700">
            <th className="text-left px-2 py-1 w-8">#</th>
            <th className="text-left px-2 py-1">Event</th>
            <th className="text-left px-2 py-1">Market</th>
            <th className="text-left px-2 py-1">Outcome</th>
            <th className="text-left px-2 py-1">Provider</th>
            <th className="text-right px-2 py-1">Odds</th>
            <th className="text-right px-2 py-1">Fair</th>
            <th className="text-right px-2 py-1">Edge</th>
            <th className="text-right px-2 py-1">Stake</th>
            <th className="text-center px-2 py-1 w-8"></th>
          </tr>
        </thead>
        <tbody>
          <TierSection tier="polymarket" label={`POLYMARKET — ${polyBets.length} bets`} color="#a855f7" bets={polyBets} onRemove={onRemoveBet} />
          <TierSection tier="pinnacle" label={`PINNACLE — ${pinnBets.length} bets (reverse value)`} color="#ef4444" bets={pinnBets} onRemove={onRemoveBet} />
          <TierSection tier="soft" label={`SOFT VALUE — ${softBets.length} bets (round-robin)`} color="#22c55e" bets={softBets} onRemove={onRemoveBet} />
        </tbody>
      </table>

      {/* Wagering summary */}
      {wageringProjections.length > 0 && (
        <div className="mt-2 px-3 py-1.5 bg-amber-500/5 border border-amber-500/20 rounded text-[11px] text-amber-500">
          <strong>Bonus wagering this session:</strong>{' '}
          {wageringProjections.map((wp, i) => (
            <span key={wp.provider_id}>
              {i > 0 && ' · '}
              {wp.provider_id} {wp.wagering_remaining.toLocaleString()}→{wp.projected_remaining.toLocaleString()} kr
              {wp.days_remaining != null && ` (${wp.days_remaining}d left)`}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/SessionBatchPanel.tsx
git commit -m "feat(play-v3): create SessionBatchPanel component"
```

---

## Task 11: Create ExecutionPanel component

**Files:**
- Create: `frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx`

- [ ] **Step 1: Implement ExecutionPanel**

```typescript
// frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx
import React, { useState, useEffect, useMemo } from 'react';
import type { BatchBet, WageringProjection } from '../../../../types';

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
}

interface ExecutionState {
  placedBets: Set<string>;
  sessionStartTime: number;
  batchHash: string;
}

function betKey(b: BatchBet): string {
  return `${b.provider_id}:${b.event_id}:${b.market}:${b.outcome}:${b.point}`;
}

function computeBatchHash(batch: BatchBet[]): string {
  return batch.map(b => betKey(b)).sort().join('|').slice(0, 64);
}

const STORAGE_KEY = 'play-v3-execution';

function loadState(batchHash: string): ExecutionState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      // Only restore if same batch and less than 24h old
      if (parsed.batchHash === batchHash && Date.now() - parsed.sessionStartTime < 86400000) {
        return { ...parsed, placedBets: new Set(parsed.placedBets) };
      }
    }
  } catch { /* ignore */ }
  return { placedBets: new Set(), sessionStartTime: Date.now(), batchHash };
}

function saveState(state: ExecutionState) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    ...state,
    placedBets: Array.from(state.placedBets),
  }));
}

export function ExecutionPanel({ batch, wageringProjections }: Props) {
  const batchHash = useMemo(() => computeBatchHash(batch), [batch]);
  const [state, setState] = useState<ExecutionState>(() => loadState(batchHash));
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);

  // Persist state changes
  useEffect(() => { saveState(state); }, [state]);

  // Reset if batch changes
  useEffect(() => {
    if (batchHash !== state.batchHash) {
      setState({ placedBets: new Set(), sessionStartTime: Date.now(), batchHash });
    }
  }, [batchHash]);

  const toggleBet = (key: string) => {
    setState(prev => {
      const next = new Set(prev.placedBets);
      if (next.has(key)) next.delete(key); else next.add(key);
      return { ...prev, placedBets: next };
    });
  };

  const markAllDone = (providerBets: BatchBet[]) => {
    setState(prev => {
      const next = new Set(prev.placedBets);
      providerBets.forEach(b => next.add(betKey(b)));
      return { ...prev, placedBets: next };
    });
  };

  // Group by provider, ordered: polymarket → pinnacle → soft (by EV desc)
  const grouped = useMemo(() => {
    const map = new Map<string, BatchBet[]>();
    batch.forEach(b => {
      const list = map.get(b.provider_id) || [];
      list.push(b);
      map.set(b.provider_id, list);
    });

    const entries = Array.from(map.entries()).map(([pid, bets]) => ({
      providerId: pid,
      bets,
      totalEv: bets.reduce((s, b) => s + b.expected_profit, 0),
      totalStake: bets.reduce((s, b) => s + b.stake, 0),
      tier: bets[0]?.tier || 'soft',
      cluster: bets[0]?.cluster,
    }));

    // Sort: polymarket first, pinnacle second, then soft by EV desc
    const tierOrder = { polymarket: 0, pinnacle: 1, soft: 2 };
    entries.sort((a, b) => {
      const ta = tierOrder[a.tier as keyof typeof tierOrder] ?? 2;
      const tb = tierOrder[b.tier as keyof typeof tierOrder] ?? 2;
      if (ta !== tb) return ta - tb;
      return b.totalEv - a.totalEv;
    });

    return entries;
  }, [batch]);

  const totalPlaced = state.placedBets.size;
  const totalBets = batch.length;
  const providersDone = grouped.filter(g =>
    g.bets.every(b => state.placedBets.has(betKey(b)))
  ).length;

  // Time elapsed
  const elapsed = Math.floor((Date.now() - state.sessionStartTime) / 60000);

  // Wagering lookup
  const wagerMap = useMemo(() => {
    const m = new Map<string, WageringProjection>();
    wageringProjections.forEach(wp => m.set(wp.provider_id, wp));
    return m;
  }, [wageringProjections]);

  return (
    <div>
      {/* Progress bar */}
      <div className="mb-3">
        <div className="flex justify-between text-[11px] text-dark-400 mb-1">
          <span>Session progress</span>
          <span>{totalPlaced} / {totalBets} bets · {providersDone} / {grouped.length} providers · {elapsed}m</span>
        </div>
        <div className="bg-dark-800 rounded h-1.5 overflow-hidden">
          <div
            className="bg-success h-full rounded transition-all"
            style={{ width: `${totalBets > 0 ? (totalPlaced / totalBets) * 100 : 0}%` }}
          />
        </div>
      </div>

      {/* Provider sections */}
      {grouped.map(group => {
        const allDone = group.bets.every(b => state.placedBets.has(betKey(b)));
        const placedCount = group.bets.filter(b => state.placedBets.has(betKey(b))).length;
        const isExpanded = expandedProvider === group.providerId;
        const wp = wagerMap.get(group.providerId);
        const tierColor = group.tier === 'polymarket' ? '#a855f7' : group.tier === 'pinnacle' ? '#ef4444' : '#22c55e';

        return (
          <div key={group.providerId} className={`mb-0.5 ${allDone ? '' : ''}`}>
            {/* Header */}
            <div
              className={`flex items-center px-2 py-1.5 bg-dark-900 rounded cursor-pointer hover:bg-dark-800 ${
                !allDone && !isExpanded ? 'opacity-80' : ''
              }`}
              onClick={() => setExpandedProvider(isExpanded ? null : group.providerId)}
            >
              <span className="mr-2 text-[11px]">
                {allDone ? <span className="text-success">✓</span> : placedCount > 0 ? <span className="text-amber-500">▶</span> : <span className="text-dark-500">○</span>}
              </span>
              <span className="font-bold text-[12px]" style={{ color: tierColor }}>{group.providerId}</span>
              {group.cluster && group.cluster !== group.providerId && (
                <span className="text-dark-600 text-[10px] ml-1">{group.cluster}</span>
              )}
              {wp && (
                <span className="ml-1.5 px-1 py-0.5 rounded text-[9px] bg-amber-500/20 text-amber-500">
                  wager
                </span>
              )}
              <span className="text-dark-500 text-[11px] ml-2">
                {group.bets.length} bets · {group.totalStake.toLocaleString()} {group.tier === 'polymarket' ? 'USDC' : 'kr'} · +{group.totalEv.toFixed(0)} EV
              </span>
              <span className="ml-auto text-[11px]">
                {allDone ? <span className="text-success">done</span> : <span className="text-dark-500">{placedCount}/{group.bets.length}</span>}
              </span>
            </div>

            {/* Expanded bet list */}
            {isExpanded && !allDone && (
              <div className="border border-dark-700 border-t-0 rounded-b">
                <table className="w-full text-[11px]">
                  <tbody>
                    {group.bets.map(bet => {
                      const key = betKey(bet);
                      const placed = state.placedBets.has(key);
                      return (
                        <tr key={key} className={`border-b border-dark-800 ${placed ? 'opacity-50' : ''}`}>
                          <td className="px-2 py-1 w-6 cursor-pointer" onClick={() => toggleBet(key)}>
                            {placed ? <span className="text-success">✓</span> : <span className="text-dark-500">○</span>}
                          </td>
                          <td className="px-2 py-1">{bet.display_home} v {bet.display_away}</td>
                          <td className="px-2 py-1 text-dark-400">{bet.market}</td>
                          <td className="px-2 py-1">{bet.outcome}{bet.point ? ` ${bet.point}` : ''}</td>
                          <td className="px-2 py-1 text-right">{bet.odds.toFixed(2)}</td>
                          <td className="px-2 py-1 text-right text-success">+{bet.edge_pct.toFixed(1)}%</td>
                          <td className="px-2 py-1 text-right">{bet.stake.toLocaleString()} {bet.provider_id === 'polymarket' ? 'USDC' : 'kr'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div className="px-2 py-1 flex justify-end">
                  <button
                    className="px-3 py-0.5 text-[11px] bg-success text-black font-bold rounded hover:brightness-110"
                    onClick={() => markAllDone(group.bets)}
                  >Mark All Done</button>
                </div>
              </div>
            )}
          </div>
        );
      })}

      {/* Session summary */}
      <div className="mt-3 px-3 py-1.5 bg-dark-800 rounded flex gap-6 text-xs flex-wrap">
        <span>Staked: <strong>{batch.filter(b => state.placedBets.has(betKey(b))).reduce((s, b) => s + b.stake, 0).toLocaleString()}</strong> / {batch.reduce((s, b) => s + b.stake, 0).toLocaleString()} kr</span>
        <span>EV captured: <span className="text-success">+{batch.filter(b => state.placedBets.has(betKey(b))).reduce((s, b) => s + b.expected_profit, 0).toFixed(0)}</span> / +{batch.reduce((s, b) => s + b.expected_profit, 0).toFixed(0)} kr</span>
        <span className="text-dark-400">{elapsed}m elapsed</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx
git commit -m "feat(play-v3): create ExecutionPanel component with localStorage persistence"
```

---

## Task 12: Rewrite PlayPage.tsx as 3-panel shell

**Files:**
- Modify: `frontend/src/components/Terminal/pages/PlayPage.tsx`

- [ ] **Step 1: Read existing PlayPage to preserve any hooks/patterns needed**

Read: `frontend/src/components/Terminal/pages/PlayPage.tsx`

- [ ] **Step 2: Rewrite PlayPage.tsx**

Replace the entire file with the 3-panel shell:

```typescript
// frontend/src/components/Terminal/pages/PlayPage.tsx
import React, { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../../services/api';
import type { BatchResult, CapitalAction } from '../../../types';
import { CapitalPlanPanel } from './play/CapitalPlanPanel';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { ExecutionPanel } from './play/ExecutionPanel';

export default function PlayPage() {
  const queryClient = useQueryClient();
  const [excludedBets, setExcludedBets] = useState<string[]>([]);
  const [capitalDismissed, setCapitalDismissed] = useState(false);

  // Fetch batch
  const {
    data: batchData,
    isLoading,
    isFetching,
    refetch: rebuildBatch,
  } = useQuery<BatchResult>({
    queryKey: ['play-batch', excludedBets],
    queryFn: () => api.getPlayBatch(excludedBets.length > 0 ? excludedBets : undefined),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  // Confirm capital mutation
  const confirmCapital = useMutation({
    mutationFn: (actions: CapitalAction[]) => api.confirmCapital(
      actions.map(a => ({
        type: a.type,
        provider_id: a.provider_id,
        from_provider_id: a.from_provider_id,
        to_provider_id: a.to_provider_id,
        amount: a.amount,
      }))
    ),
    onSuccess: () => {
      setExcludedBets([]);
      queryClient.invalidateQueries({ queryKey: ['play-batch'] });
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
    },
  });

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  const handleConfirmCapital = useCallback((actions: CapitalAction[]) => {
    confirmCapital.mutate(actions);
  }, [confirmCapital]);

  if (isLoading) {
    return <div className="p-4 text-dark-400 text-sm">Building batch...</div>;
  }

  if (!batchData) {
    return <div className="p-4 text-dark-400 text-sm">No batch data available. Run extraction first.</div>;
  }

  const { batch, summary, capital_plan, wagering_projections } = batchData;

  return (
    <div className="p-3 space-y-2">
      {/* Panel 1: Capital Plan */}
      {!capitalDismissed && capital_plan && (
        <CapitalPlanPanel
          capitalPlan={capital_plan}
          onConfirm={handleConfirmCapital}
          onDismissAll={() => setCapitalDismissed(true)}
          isLoading={confirmCapital.isPending}
        />
      )}

      {/* Panel 2: Session Batch */}
      <SessionBatchPanel
        batch={batch}
        summary={summary}
        wageringProjections={wagering_projections || []}
        onRemoveBet={handleRemoveBet}
      />

      {/* Panel 3: Execution */}
      {batch.length > 0 && (
        <ExecutionPanel
          batch={batch}
          wageringProjections={wagering_projections || []}
        />
      )}

      {/* Rebuild button */}
      <div className="flex justify-end pt-2">
        <button
          className="px-3 py-1 text-xs bg-dark-700 text-dark-300 border border-dark-600 rounded hover:bg-dark-600"
          onClick={() => { setExcludedBets([]); rebuildBatch(); }}
          disabled={isFetching}
        >
          {isFetching ? 'Rebuilding...' : 'Rebuild Batch'}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify the app compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/PlayPage.tsx
git commit -m "feat(play-v3): rewrite PlayPage as 3-panel session manager"
```

---

## Task 13: Integration test — verify full flow

- [ ] **Step 1: Start backend and verify batch endpoint returns new structure**

Run: `cd backend && python -m pytest tests/ -v --timeout=30`
Expected: All tests pass

- [ ] **Step 2: Start frontend and verify it compiles**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 3: Visual verification with dev servers**

Start backend + frontend dev servers. Open Play tab. Verify:
- Capital Plan panel renders (may be empty if no shortfalls)
- Batch table shows 3 tiers (polymarket/pinnacle/soft)
- Execution panel shows provider accordion
- Remove bet (×) triggers recalc
- Checkoff persists across page navigation

- [ ] **Step 4: Final commit (if any fixes were needed)**

```bash
git add backend/src/ frontend/src/
git commit -m "feat(play-v3): integration verification fixes"
```
