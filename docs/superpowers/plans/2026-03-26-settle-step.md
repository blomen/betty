# Settle Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Settle" step (Step 0) to the Play page that shows pending bets and lets the user settle them manually or via mirror auto-detection before the capital plan runs.

**Architecture:** New `GET /api/play/pending-bets` and `POST /api/play/settle-bet` endpoints in the existing opportunities routes. New `POST /api/mirror/ensure-started` idempotent endpoint. New `SettlePanel.tsx` component. PlayPage updated from 3-step to 4-step flow.

**Tech Stack:** Python/FastAPI, React/TypeScript, react-query, SSE (existing EventSource)

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `frontend/src/components/Terminal/pages/play/SettlePanel.tsx` | Settle UI: pending bets grouped by provider, manual W/L/V, mirror auto-settle |
| Modify | `backend/src/api/routes/opportunities.py` | Add `GET /play/pending-bets` and `POST /play/settle-bet` endpoints |
| Modify | `backend/src/api/routes/mirror.py` | Add `POST /mirror/ensure-started` idempotent endpoint |
| Modify | `frontend/src/components/Terminal/pages/PlayPage.tsx` | 4-step flow, mirror ensure-started on mount, pending count in step indicator |
| Modify | `frontend/src/services/api/opportunities.ts` | Add `getPendingBets()` and `settleBet()` API methods |
| Modify | `frontend/src/types/index.ts` | Add `PendingBet`, `PendingBetsResponse` types |
| Create | `backend/tests/test_settle_endpoints.py` | Tests for pending-bets and settle-bet endpoints |

---

### Task 1: Backend — Pending Bets Endpoint

**Files:**
- Modify: `backend/src/api/routes/opportunities.py`
- Create: `backend/tests/test_settle_endpoints.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_settle_endpoints.py`:

```python
"""Tests for Play settle endpoints."""

import pytest
from datetime import datetime, timezone, timedelta


def test_pending_bets_returns_grouped_by_provider(client, db_session, active_profile):
    """GET /api/opportunities/play/pending-bets returns pending bets grouped by provider."""
    from src.db.models import Bet, Event

    # Create events
    ev1 = Event(id="ev1", home_team="Real Madrid", away_team="Barcelona",
                sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=2))
    ev2 = Event(id="ev2", home_team="Liverpool", away_team="Arsenal",
                sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=3))
    db_session.add_all([ev1, ev2])
    db_session.flush()

    # Create pending bets on different providers
    b1 = Bet(profile_id=active_profile.id, event_id="ev1", provider_id="unibet",
             market="1x2", outcome="home", odds=2.10, stake=150.0, result="pending",
             placed_at=datetime.now(timezone.utc) - timedelta(hours=12))
    b2 = Bet(profile_id=active_profile.id, event_id="ev2", provider_id="unibet",
             market="total", outcome="over", odds=1.85, stake=200.0, result="pending",
             placed_at=datetime.now(timezone.utc) - timedelta(hours=6))
    b3 = Bet(profile_id=active_profile.id, event_id="ev1", provider_id="betsson",
             market="1x2", outcome="away", odds=3.40, stake=100.0, result="pending",
             placed_at=datetime.now(timezone.utc) - timedelta(hours=8))
    # Settled bet — should NOT appear
    b4 = Bet(profile_id=active_profile.id, event_id="ev1", provider_id="unibet",
             market="1x2", outcome="draw", odds=3.20, stake=80.0, result="won", payout=256.0,
             placed_at=datetime.now(timezone.utc) - timedelta(hours=24))
    db_session.add_all([b1, b2, b3, b4])
    db_session.commit()

    resp = client.get("/api/opportunities/play/pending-bets")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_pending"] == 3
    assert data["total_stake"] == 450.0
    assert len(data["providers"]) == 2

    # Check unibet group
    unibet = next(p for p in data["providers"] if p["provider_id"] == "unibet")
    assert unibet["pending_count"] == 2
    assert len(unibet["bets"]) == 2

    # Check betsson group
    betsson = next(p for p in data["providers"] if p["provider_id"] == "betsson")
    assert betsson["pending_count"] == 1


def test_pending_bets_empty(client, db_session, active_profile):
    """GET /api/opportunities/play/pending-bets returns empty when no pending bets."""
    resp = client.get("/api/opportunities/play/pending-bets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_pending"] == 0
    assert data["providers"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_settle_endpoints.py -v`
Expected: FAIL (404 — endpoint doesn't exist yet)

- [ ] **Step 3: Implement the endpoint**

Add to `backend/src/api/routes/opportunities.py` (after the existing play routes, before the `BuildBatchRequest` class):

```python
@router.get("/play/pending-bets")
async def get_pending_bets(db: Session = Depends(get_db)):
    """Get all pending (unsettled) bets grouped by provider."""
    from ...db.models import Bet, Event
    from ...repositories import ProfileRepo

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    pending = (
        db.query(Bet)
        .filter(Bet.profile_id == profile.id, Bet.result == "pending")
        .order_by(Bet.provider_id, Bet.placed_at)
        .all()
    )

    # Build event name lookup
    event_ids = {b.event_id for b in pending if b.event_id}
    events = {}
    if event_ids:
        for ev in db.query(Event).filter(Event.id.in_(event_ids)).all():
            events[ev.id] = f"{ev.home_team} vs {ev.away_team}" if ev.home_team and ev.away_team else ev.id

    # Group by provider
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for bet in pending:
        groups[bet.provider_id].append({
            "id": bet.id,
            "event_name": events.get(bet.event_id, bet.event_id or "Unknown"),
            "market": bet.market,
            "outcome": bet.outcome,
            "odds": bet.odds,
            "stake": bet.stake,
            "currency": bet.currency or "SEK",
            "placed_at": bet.placed_at.isoformat() if bet.placed_at else None,
        })

    providers = [
        {
            "provider_id": pid,
            "pending_count": len(bets),
            "total_stake": sum(b["stake"] for b in bets),
            "bets": bets,
        }
        for pid, bets in sorted(groups.items())
    ]

    return {
        "providers": providers,
        "total_pending": len(pending),
        "total_stake": sum(b.stake for b in pending),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_settle_endpoints.py::test_pending_bets_returns_grouped_by_provider tests/test_settle_endpoints.py::test_pending_bets_empty -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/opportunities.py backend/tests/test_settle_endpoints.py
git commit -m "feat(play-v3): add GET /play/pending-bets endpoint"
```

---

### Task 2: Backend — Settle Bet Endpoint

**Files:**
- Modify: `backend/src/api/routes/opportunities.py`
- Modify: `backend/tests/test_settle_endpoints.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_settle_endpoints.py`:

```python
def test_settle_bet_won(client, db_session, active_profile):
    """POST /api/opportunities/play/settle-bet settles a bet as won."""
    from src.db.models import Bet, Event

    ev = Event(id="ev-settle", home_team="PSG", away_team="Bayern",
               sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(ev)
    db_session.flush()

    bet = Bet(profile_id=active_profile.id, event_id="ev-settle", provider_id="betsson",
              market="1x2", outcome="home", odds=2.50, stake=100.0, result="pending")
    db_session.add(bet)
    db_session.commit()
    bet_id = bet.id

    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": bet_id, "result": "won"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bet_id"] == bet_id
    assert data["result"] == "won"
    assert data["payout"] == 250.0  # stake * odds

    # Verify DB
    db_session.refresh(bet)
    assert bet.result == "won"
    assert bet.payout == 250.0
    assert bet.settled_at is not None


def test_settle_bet_lost(client, db_session, active_profile):
    """POST /api/opportunities/play/settle-bet settles a bet as lost."""
    from src.db.models import Bet, Event

    ev = Event(id="ev-settle2", home_team="Inter", away_team="Milan",
               sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(ev)
    db_session.flush()

    bet = Bet(profile_id=active_profile.id, event_id="ev-settle2", provider_id="unibet",
              market="1x2", outcome="home", odds=1.90, stake=200.0, result="pending")
    db_session.add(bet)
    db_session.commit()

    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": bet.id, "result": "lost"})
    assert resp.status_code == 200
    assert resp.json()["payout"] == 0.0


def test_settle_bet_void(client, db_session, active_profile):
    """POST /api/opportunities/play/settle-bet settles a bet as void (stake returned)."""
    from src.db.models import Bet, Event

    ev = Event(id="ev-settle3", home_team="Ajax", away_team="Feyenoord",
               sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(ev)
    db_session.flush()

    bet = Bet(profile_id=active_profile.id, event_id="ev-settle3", provider_id="betsson",
              market="spread", outcome="home", odds=1.95, stake=150.0, point=-1.5, result="pending")
    db_session.add(bet)
    db_session.commit()

    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": bet.id, "result": "void"})
    assert resp.status_code == 200
    assert resp.json()["payout"] == 150.0  # stake returned


def test_settle_bet_not_found(client):
    """POST /api/opportunities/play/settle-bet returns 404 for unknown bet."""
    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": 99999, "result": "won"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_settle_endpoints.py::test_settle_bet_won tests/test_settle_endpoints.py::test_settle_bet_lost tests/test_settle_endpoints.py::test_settle_bet_void tests/test_settle_endpoints.py::test_settle_bet_not_found -v`
Expected: FAIL (404 — endpoint doesn't exist)

- [ ] **Step 3: Implement the endpoint**

Add to `backend/src/api/routes/opportunities.py`, after the pending-bets route:

```python
class SettleBetRequest(BaseModel):
    bet_id: int
    result: str  # "won", "lost", "void"


@router.post("/play/settle-bet")
async def settle_bet(body: SettleBetRequest, db: Session = Depends(get_db)):
    """Manually settle a single pending bet."""
    from ...services.bet_service import BetService
    from ...db.models import Bet

    if body.result not in ("won", "lost", "void"):
        raise HTTPException(400, f"Invalid result: {body.result}. Must be won, lost, or void.")

    bet = db.query(Bet).get(body.bet_id)
    if not bet:
        raise HTTPException(404, f"Bet {body.bet_id} not found")

    # Calculate payout
    if body.result == "won":
        payout = bet.stake * bet.odds
    elif body.result == "void":
        payout = bet.stake
    else:
        payout = 0.0

    bet_service = BetService(db)
    result = bet_service.settle_bet(body.bet_id, body.result, payout)
    if "error" in result:
        raise HTTPException(400, result["error"])

    db.commit()

    return {
        "bet_id": body.bet_id,
        "result": body.result,
        "payout": payout,
        "settled_at": bet.settled_at.isoformat() if bet.settled_at else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_settle_endpoints.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/opportunities.py backend/tests/test_settle_endpoints.py
git commit -m "feat(play-v3): add POST /play/settle-bet endpoint"
```

---

### Task 3: Backend — Mirror Ensure-Started Endpoint

**Files:**
- Modify: `backend/src/api/routes/mirror.py`

- [ ] **Step 1: Add the idempotent ensure-started endpoint**

Add to `backend/src/api/routes/mirror.py`, after the existing `start_mirror` route:

```python
@router.post("/ensure-started")
async def ensure_mirror_started():
    """Idempotent: start mirror if not already running, otherwise return status."""
    if _any_running():
        for m in _mirrors.values():
            status = m.get_status()
            if status["running"]:
                return status
        return {"running": True, "status": "running"}

    mirror = MirrorService(broadcaster=odds_broadcaster, provider_id=_DEFAULT_PROVIDER)
    await mirror.start()
    _mirrors[_DEFAULT_PROVIDER] = mirror
    return mirror.get_status()
```

- [ ] **Step 2: Verify manually**

Run: `cd backend && python -c "from src.api.routes.mirror import router; print([r.path for r in router.routes])"`
Expected: Output includes `/api/mirror/ensure-started`

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/mirror.py
git commit -m "feat(play-v3): add POST /mirror/ensure-started idempotent endpoint"
```

---

### Task 4: Frontend — Types and API Methods

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/services/api/opportunities.ts`

- [ ] **Step 1: Add types**

Add to `frontend/src/types/index.ts`, after the `BatchResult` interface:

```typescript
export interface PendingBet {
  id: number;
  event_name: string;
  market: string | null;
  outcome: string | null;
  odds: number;
  stake: number;
  currency: string;
  placed_at: string | null;
}

export interface PendingBetGroup {
  provider_id: string;
  pending_count: number;
  total_stake: number;
  bets: PendingBet[];
}

export interface PendingBetsResponse {
  providers: PendingBetGroup[];
  total_pending: number;
  total_stake: number;
}

export interface SettleBetResult {
  bet_id: number;
  result: string;
  payout: number;
  settled_at: string | null;
}
```

- [ ] **Step 2: Add API methods**

Add to `frontend/src/services/api/opportunities.ts` inside the `opportunitiesApi` object, after the `confirmCapital` method:

```typescript
  async getPendingBets(): Promise<PendingBetsResponse> {
    return fetchJson('/opportunities/play/pending-bets');
  },

  async settleBet(betId: number, result: 'won' | 'lost' | 'void'): Promise<SettleBetResult> {
    return fetchJson('/opportunities/play/settle-bet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bet_id: betId, result }),
    });
  },

  async ensureMirrorStarted(): Promise<any> {
    return fetchJson('/mirror/ensure-started', { method: 'POST' });
  },
```

Also add the type imports at the top of `opportunities.ts`:

```typescript
import type { PendingBetsResponse, SettleBetResult } from '../../types';
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/services/api/opportunities.ts
git commit -m "feat(play-v3): add pending bets types and API methods"
```

---

### Task 5: Frontend — SettlePanel Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/play/SettlePanel.tsx`

- [ ] **Step 1: Create the SettlePanel component**

Create `frontend/src/components/Terminal/pages/play/SettlePanel.tsx`:

```tsx
import { useState, useEffect, useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { PendingBetGroup, PendingBet } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  onContinue: () => void;
  pendingCount: number;
  setPendingCount: (n: number) => void;
}

type BetSettleState = 'pending' | 'won' | 'lost' | 'void' | 'auto-settled';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('sv-SE', { month: 'short', day: 'numeric' });
}

function resultColor(result: string): string {
  if (result === 'won') return 'text-success';
  if (result === 'lost') return 'text-red-400';
  if (result === 'void') return 'text-amber-400';
  return 'text-dark-400';
}

// ---------------------------------------------------------------------------
// BetRow
// ---------------------------------------------------------------------------

function BetRow({
  bet,
  state,
  onSettle,
  isSettling,
}: {
  bet: PendingBet;
  state: BetSettleState;
  onSettle: (result: 'won' | 'lost' | 'void') => void;
  isSettling: boolean;
}) {
  const settled = state !== 'pending';

  return (
    <div
      className={`flex items-center gap-3 px-3 py-1.5 border-b border-dark-700/50 transition-opacity ${
        settled ? 'opacity-50' : ''
      }`}
    >
      {/* Event info */}
      <div className="flex-1 min-w-0">
        <div className="text-xs text-text truncate">{bet.event_name}</div>
        <div className="text-[10px] text-dark-400 flex items-center gap-2">
          {bet.market && <span>{bet.market}</span>}
          {bet.outcome && <span className="text-dark-300">{bet.outcome}</span>}
          {bet.point !== undefined && bet.point !== null && (
            <span className="text-dark-300">{bet.point > 0 ? '+' : ''}{bet.point}</span>
          )}
        </div>
      </div>

      {/* Odds + Stake */}
      <div className="text-right flex-shrink-0">
        <div className="text-xs text-text font-medium">{bet.odds.toFixed(2)}</div>
        <div className="text-[10px] text-dark-400">
          {bet.stake.toFixed(0)} {bet.currency === 'USDC' ? 'USDC' : 'kr'}
        </div>
      </div>

      {/* Date */}
      <div className="text-[10px] text-dark-500 w-12 text-right flex-shrink-0">
        {formatDate(bet.placed_at)}
      </div>

      {/* Actions or result */}
      <div className="flex items-center gap-1 flex-shrink-0 w-20 justify-end">
        {settled ? (
          <span className={`text-[10px] font-bold uppercase ${resultColor(state)}`}>
            {state === 'auto-settled' ? '✓ auto' : `✓ ${state}`}
          </span>
        ) : (
          <>
            <button
              onClick={() => onSettle('won')}
              disabled={isSettling}
              className="px-1.5 py-0.5 text-[10px] font-bold border border-success/40 text-success hover:bg-success/10 disabled:opacity-30 transition-colors"
              title="Won"
            >
              W
            </button>
            <button
              onClick={() => onSettle('lost')}
              disabled={isSettling}
              className="px-1.5 py-0.5 text-[10px] font-bold border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-30 transition-colors"
              title="Lost"
            >
              L
            </button>
            <button
              onClick={() => onSettle('void')}
              disabled={isSettling}
              className="px-1.5 py-0.5 text-[10px] font-bold border border-amber-500/40 text-amber-400 hover:bg-amber-500/10 disabled:opacity-30 transition-colors"
              title="Void"
            >
              V
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ProviderGroup
// ---------------------------------------------------------------------------

function ProviderGroup({
  group,
  settledBets,
  settlingBetId,
  onSettle,
}: {
  group: PendingBetGroup;
  settledBets: Record<number, BetSettleState>;
  settlingBetId: number | null;
  onSettle: (betId: number, result: 'won' | 'lost' | 'void') => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  const unsettledCount = group.bets.filter(b => !settledBets[b.id]).length;

  return (
    <div className="border border-dark-700 bg-dark-800 mb-2">
      {/* Header */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-dark-700/50 transition-colors"
      >
        <span className="text-[10px] text-dark-500">{collapsed ? '▶' : '▼'}</span>
        <span className="text-xs font-medium text-text">{group.provider_id}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
          unsettledCount > 0 ? 'bg-amber-500/20 text-amber-400' : 'bg-success/20 text-success'
        }`}>
          {unsettledCount > 0 ? `${unsettledCount} pending` : 'all settled'}
        </span>
        <span className="text-[10px] text-dark-500 ml-auto">
          {group.total_stake.toFixed(0)} kr staked
        </span>
      </button>

      {/* Bet rows */}
      {!collapsed && group.bets.map(bet => (
        <BetRow
          key={bet.id}
          bet={bet}
          state={settledBets[bet.id] || 'pending'}
          onSettle={(result) => onSettle(bet.id, result)}
          isSettling={settlingBetId === bet.id}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SettlePanel
// ---------------------------------------------------------------------------

export function SettlePanel({ onContinue, pendingCount, setPendingCount }: Props) {
  const queryClient = useQueryClient();
  const [settledBets, setSettledBets] = useState<Record<number, BetSettleState>>({});
  const [settlingBetId, setSettlingBetId] = useState<number | null>(null);

  // Fetch pending bets
  const { data, isLoading } = useQuery({
    queryKey: ['pending-bets'],
    queryFn: () => api.getPendingBets(),
    staleTime: 30_000,
  });

  // Update pending count for step indicator
  useEffect(() => {
    if (data) {
      const remaining = data.total_pending - Object.keys(settledBets).length;
      setPendingCount(Math.max(0, remaining));
    }
  }, [data, settledBets, setPendingCount]);

  // Manual settle mutation
  const settleMutation = useMutation({
    mutationFn: ({ betId, result }: { betId: number; result: 'won' | 'lost' | 'void' }) =>
      api.settleBet(betId, result),
    onMutate: ({ betId }) => setSettlingBetId(betId),
    onSuccess: (resp) => {
      setSettledBets(prev => ({ ...prev, [resp.bet_id]: resp.result as BetSettleState }));
      setSettlingBetId(null);
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
    },
    onError: () => setSettlingBetId(null),
  });

  // Listen for mirror auto-settlements via SSE
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    es.addEventListener('settlements_pending', (e: MessageEvent) => {
      const payload = JSON.parse(e.data);
      const settlements = payload.settlements || [];
      // Auto-confirm mirror settlements
      if (settlements.length > 0) {
        api.confirmMirrorSettlements().then(() => {
          const autoSettled: Record<number, BetSettleState> = {};
          for (const s of settlements) {
            autoSettled[s.bet_id] = 'auto-settled';
          }
          setSettledBets(prev => ({ ...prev, ...autoSettled }));
          queryClient.invalidateQueries({ queryKey: ['bankroll'] });
          queryClient.invalidateQueries({ queryKey: ['pending-bets'] });
        }).catch(err => console.error('[settle] auto-confirm failed', err));
      }
    });

    return () => es.close();
  }, [queryClient]);

  const handleSettle = useCallback((betId: number, result: 'won' | 'lost' | 'void') => {
    settleMutation.mutate({ betId, result });
  }, [settleMutation]);

  if (isLoading) {
    return <div className="p-4 text-dark-400 text-sm">Loading pending bets...</div>;
  }

  const providers = data?.providers || [];
  const totalPending = data?.total_pending || 0;
  const settledCount = Object.keys(settledBets).length;
  const remaining = totalPending - settledCount;

  return (
    <div className="p-4 flex flex-col items-center">
      <div className="w-full max-w-lg">

        {/* Header */}
        <div className="text-center mb-4">
          <div className="text-[10px] text-dark-400 uppercase tracking-widest mb-1">Settle Pending Bets</div>
          {totalPending > 0 ? (
            <div className="text-sm text-text">
              {remaining > 0 ? (
                <><span className="text-amber-400 font-bold">{remaining}</span> unsettled</>
              ) : (
                <span className="text-success font-bold">All settled</span>
              )}
              {settledCount > 0 && (
                <span className="text-dark-400 ml-2">({settledCount} done this session)</span>
              )}
            </div>
          ) : (
            <div className="text-sm text-success">No pending bets</div>
          )}
        </div>

        {/* Provider groups */}
        {providers.map(group => (
          <ProviderGroup
            key={group.provider_id}
            group={group}
            settledBets={settledBets}
            settlingBetId={settlingBetId}
            onSettle={handleSettle}
          />
        ))}

        {/* Continue button */}
        <div className="text-center mt-4">
          <button
            onClick={onContinue}
            className="px-5 py-2 bg-success text-black text-xs font-bold rounded hover:opacity-90 transition-opacity"
          >
            {remaining > 0
              ? `Continue with ${remaining} unsettled →`
              : totalPending > 0
              ? 'All settled — Continue →'
              : 'No pending bets — Continue →'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify the file compiles**

Run: `cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20`
Expected: No errors in SettlePanel.tsx (other pre-existing errors are OK)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/SettlePanel.tsx
git commit -m "feat(play-v3): create SettlePanel component with manual + mirror settle"
```

---

### Task 6: Frontend — Wire SettlePanel into PlayPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/PlayPage.tsx`

- [ ] **Step 1: Update PlayPage to 4-step flow**

Replace the entire content of `frontend/src/components/Terminal/pages/PlayPage.tsx`:

```tsx
import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { BatchResult, CapitalAction } from '@/types';
import { SettlePanel } from './play/SettlePanel';
import { CapitalPlanPanel } from './play/CapitalPlanPanel';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { ExecutionPanel } from './play/ExecutionPanel';

// ---------------------------------------------------------------------------
// Step definitions
// ---------------------------------------------------------------------------

type Step = 'settle' | 'capital' | 'batch' | 'execute';

const STEPS: { key: Step; label: string }[] = [
  { key: 'settle', label: 'Settle' },
  { key: 'capital', label: 'Capital Plan' },
  { key: 'batch', label: 'Session Batch' },
  { key: 'execute', label: 'Execute' },
];

function StepIndicator({
  current,
  onNavigate,
  pendingCount,
  mirrorRunning,
}: {
  current: Step;
  onNavigate: (s: Step) => void;
  pendingCount: number;
  mirrorRunning: boolean;
}) {
  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-dark-900">
      {/* Mirror status dot */}
      <div
        className={`w-2 h-2 rounded-full mr-2 ${mirrorRunning ? 'bg-success' : 'bg-dark-600'}`}
        title={mirrorRunning ? 'Mirror running' : 'Mirror not running'}
      />

      {STEPS.map((step, i) => {
        const isActive = step.key === current;
        const currentIdx = STEPS.findIndex((s) => s.key === current);
        const isPast = i < currentIdx;

        // Show pending count on settle step
        const label = step.key === 'settle' && pendingCount > 0
          ? `${step.label} (${pendingCount})`
          : step.label;

        return (
          <div key={step.key} className="flex items-center gap-1">
            {i > 0 && (
              <span className={`text-[10px] mx-1 ${isPast ? 'text-success' : 'text-dark-600'}`}>→</span>
            )}
            <button
              onClick={() => onNavigate(step.key)}
              className={`text-[11px] px-2 py-0.5 transition-colors ${
                isActive
                  ? 'text-success font-bold border-b border-success'
                  : isPast
                  ? 'text-success/60 hover:text-success cursor-pointer'
                  : 'text-dark-500 hover:text-dark-300 cursor-pointer'
              }`}
            >
              {isPast ? '✓ ' : isActive ? '● ' : '○ '}
              {label}
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PlayPage
// ---------------------------------------------------------------------------

export function PlayPage() {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step>('settle');
  const [excludedBets, setExcludedBets] = useState<string[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [mirrorRunning, setMirrorRunning] = useState(false);

  // Lazy-start mirror on mount
  useEffect(() => {
    api.ensureMirrorStarted()
      .then(() => setMirrorRunning(true))
      .catch(() => setMirrorRunning(false));
  }, []);

  // Fetch batch (only when past settle step)
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
    enabled: step !== 'settle',
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
      setStep('batch');
    },
  });

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  const handleConfirmCapital = useCallback((actions: CapitalAction[]) => {
    confirmCapital.mutate(actions);
  }, [confirmCapital]);

  const handleSkipCapital = useCallback(() => {
    setStep('batch');
  }, []);

  const handleLockBatch = useCallback(() => {
    setStep('execute');
  }, []);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Step indicator */}
      <StepIndicator
        current={step}
        onNavigate={setStep}
        pendingCount={pendingCount}
        mirrorRunning={mirrorRunning}
      />

      {/* Step content */}
      <div className="flex-1 overflow-y-auto">
        {step === 'settle' && (
          <SettlePanel
            onContinue={() => setStep('capital')}
            pendingCount={pendingCount}
            setPendingCount={setPendingCount}
          />
        )}

        {step === 'capital' && (
          <>
            {isLoading ? (
              <div className="p-4 text-dark-400 text-sm">Building batch...</div>
            ) : !batchData ? (
              <div className="p-4 text-dark-400 text-sm">No batch data available. Run extraction first.</div>
            ) : (
              <CapitalPlanPanel
                capitalPlan={batchData.capital_plan}
                onConfirm={handleConfirmCapital}
                onSkip={handleSkipCapital}
                isLoading={confirmCapital.isPending}
              />
            )}
          </>
        )}

        {step === 'batch' && (
          <div className="flex flex-col flex-1 min-h-0">
            {isLoading ? (
              <div className="p-4 text-dark-400 text-sm">Building batch...</div>
            ) : !batchData ? (
              <div className="p-4 text-dark-400 text-sm">No batch data available. Run extraction first.</div>
            ) : (
              <>
                <SessionBatchPanel
                  batch={batchData.batch}
                  summary={batchData.summary}
                  wageringProjections={batchData.wagering_projections || []}
                  onRemoveBet={handleRemoveBet}
                />

                {/* Action bar */}
                <div className="flex items-center justify-between px-3 py-2 border-t border-border bg-dark-900">
                  <button
                    className="px-3 py-1 text-xs text-dark-400 border border-dark-600 hover:bg-dark-800 transition-colors"
                    onClick={() => setStep('capital')}
                  >
                    ← Capital Plan
                  </button>
                  <div className="flex items-center gap-2">
                    <button
                      className="px-3 py-1 text-xs bg-dark-700 text-dark-300 border border-dark-600 hover:bg-dark-600"
                      onClick={() => { setExcludedBets([]); rebuildBatch(); }}
                      disabled={isFetching}
                    >
                      {isFetching ? 'Rebuilding...' : 'Rebuild'}
                    </button>
                    {batchData.batch.length > 0 && (
                      <button
                        className="px-4 py-1 text-xs bg-success text-black font-bold hover:opacity-90 transition-opacity"
                        onClick={handleLockBatch}
                      >
                        Execute ({batchData.batch.length} bets) →
                      </button>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {step === 'execute' && batchData && (
          <div className="flex flex-col flex-1 min-h-0">
            <ExecutionPanel
              batch={batchData.batch}
              wageringProjections={batchData.wagering_projections || []}
            />

            {/* Back to batch */}
            <div className="flex items-center px-3 py-2 border-t border-border bg-dark-900">
              <button
                className="px-3 py-1 text-xs text-dark-400 border border-dark-600 hover:bg-dark-800 transition-colors"
                onClick={() => setStep('batch')}
              >
                ← Back to Batch
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify the file compiles**

Run: `cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/PlayPage.tsx
git commit -m "feat(play-v3): wire SettlePanel as step 0 in 4-step play flow"
```

---

### Task 7: Smoke Test — End-to-End Verification

**Files:** None (verification only)

- [ ] **Step 1: Run backend tests**

Run: `cd backend && python -m pytest tests/test_settle_endpoints.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 2>&1 | tail -20`
Expected: No new failures

- [ ] **Step 3: Run frontend type check**

Run: `cd frontend && npx tsc --noEmit --pretty 2>&1 | tail -20`
Expected: No new errors

- [ ] **Step 4: Commit if any fixups needed**

Only if steps above revealed issues that needed fixes.
