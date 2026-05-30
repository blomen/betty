# Bonusdeposit Lifecycle Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `bonusdeposit` bonuses to lifecycle parity with freebet — backend tracking provably correct (tests + the wager-first config fix), and the Sports-tab `BonusChip` surfacing the deposit → trigger → bonus-unlocked → main-wagering → done flow.

**Architecture:** Phase 1 verifies/hardens the existing balance-adjusting state machine (`profile_repo` + `bankroll_service.deposit_with_bonus` + `record_wagering`) with a pytest matrix and one config fix. Phase 2 extends the existing pure resolver (`bonusChipState.ts`) + `BonusChip` (`PlayPage.tsx`) to branch on `bonus_type` and render bonusdeposit states, wiring the existing `deposit_with_bonus` endpoint. One branch, backend-first, one PR, one deploy.

**Tech Stack:** Python 3.12 / SQLAlchemy / pytest (backend); React 19 / TS / Vite / vitest (frontend). Worktree: `c:\Users\rasmu\betty\.claude\worktrees\bonusdeposit-parity` (branch `worktree-bonusdeposit-parity`, off `origin/main`).

**Reference spec:** `docs/superpowers/specs/2026-05-30-bonusdeposit-parity-design.md`

---

## File Structure

- **Create** `backend/tests/test_bonusdeposit_lifecycle.py` — state-machine matrix (profile_repo unit tests) + the wager-first config-wiring test + the is_bonus non-misfire test.
- **Modify** `backend/src/config/providers.yaml` — add `wagering_multiplier: 0` to leovegas/expekt/betmgm; add `# TODO(bonus-terms)` flags on placeholder configs.
- **Modify** `frontend/src/pages/bonusChipState.ts` — branch resolver on `bonus_type`; add bonusdeposit states.
- **Modify** `frontend/src/pages/bonusChipState.test.ts` — add bonusdeposit cases (freebet cases unchanged).
- **Modify** `frontend/src/hooks/useApi.ts` — add `depositWithBonus` shim.
- **Modify** `frontend/src/pages/PlayPage.tsx` — extend `BonusChip` for bonusdeposit states (deposit-amount input + progress).

All backend commands run from `backend/` with `python -m pytest`; frontend from `frontend/` with `npx`. Use forward-slash `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/<dir>"`.

---

## PHASE 1 — Backend: verify + harden

### Task 1: State-machine matrix (profile_repo unit tests)

These are **verification** tests — they should PASS against current code (the engine is correct; only configs are wrong). If any fail, that's a real bug to fix in `profile_repo.record_wagering` before moving on.

**Files:**
- Create: `backend/tests/test_bonusdeposit_lifecycle.py`

- [ ] **Step 1: Write the tests**

Create `backend/tests/test_bonusdeposit_lifecycle.py`:

```python
# backend/tests/test_bonusdeposit_lifecycle.py
"""Bonusdeposit state-machine lifecycle: two-phase, wager-first, single-phase."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Bet, Profile, ProfileProviderBalance, ProfileProviderBonus, Provider
from src.repositories import ProfileRepo
from src.services.bet_service import BetService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Profile(id=1, name="t", is_active=True, bankroll=100000, currency="SEK"))
    # providers used across tests
    for pid in ("betinia", "leovegas", "speedybet"):
        s.add(Provider(id=pid, name=pid))
    s.commit()
    yield s
    s.close()


def _bal(repo, pid):
    return repo.get_balance(1, pid)


def test_two_phase_trigger_then_main(db: Session):
    repo = ProfileRepo(db)
    db.add(ProfileProviderBalance(profile_id=1, provider_id="betinia", balance=1000))
    db.commit()
    # deposit 1000, bonus 1000, trigger = deposit*1 = 1000 @1.50, main = bonus*8 @1.80
    repo.start_bonus_trigger(
        1, "betinia", bonus_amount=1000, trigger_wagering=1000,
        trigger_min_odds=1.50, main_wagering_multiplier=8, main_min_odds=1.80,
        deposit_amount=1000,
    )
    st = repo.get_bonus_status(1, "betinia")
    assert st["status"] == "trigger_needed"
    assert st["wagering_requirement"] == 1000

    # meet trigger with one 1000 bet @ 2.0 (>=1.50)
    repo.record_wagering(1, "betinia", stake=1000, odds=2.0)
    st = repo.get_bonus_status(1, "betinia")
    assert st["status"] == "in_progress"          # bonus unlocked, main phase
    assert _bal(repo, "betinia") == 2000          # 1000 deposit + 1000 bonus credited
    assert st["wagering_requirement"] == 8000     # bonus 1000 * 8
    assert st["min_odds"] == 1.80                 # switched to main_min_odds

    # meet main with 8000 @ 2.0
    repo.record_wagering(1, "betinia", stake=8000, odds=2.0)
    assert repo.get_bonus_status(1, "betinia")["status"] == "completed"


def test_wager_first_completes_at_trigger(db: Session):
    """wagering_multiplier=0 -> trigger completion credits bonus and COMPLETES
    (no spurious main phase). Guards the leovegas/expekt/betmgm fix."""
    repo = ProfileRepo(db)
    db.add(ProfileProviderBalance(profile_id=1, provider_id="leovegas", balance=600))
    db.commit()
    repo.start_bonus_trigger(
        1, "leovegas", bonus_amount=600, trigger_wagering=3600,
        trigger_min_odds=1.80, main_wagering_multiplier=0, main_min_odds=1.80,
        deposit_amount=600,
    )
    repo.record_wagering(1, "leovegas", stake=3600, odds=1.9)
    st = repo.get_bonus_status(1, "leovegas")
    assert st["status"] == "completed"
    assert _bal(repo, "leovegas") == 1200         # 600 + 600 bonus credited once


def test_single_phase_immediate(db: Session):
    repo = ProfileRepo(db)
    repo.start_bonus_wagering(1, "speedybet", bonus_amount=500, wagering_multiplier=10, min_odds=1.80)
    st = repo.get_bonus_status(1, "speedybet")
    assert st["status"] == "in_progress"
    assert st["wagering_requirement"] == 5000     # bonus 500 * 10
    repo.record_wagering(1, "speedybet", stake=5000, odds=2.0)
    assert repo.get_bonus_status(1, "speedybet")["status"] == "completed"


def test_min_odds_gate_blocks_low_odds_bets(db: Session):
    repo = ProfileRepo(db)
    repo.start_bonus_trigger(
        1, "betinia", bonus_amount=1000, trigger_wagering=1000,
        trigger_min_odds=1.80, main_wagering_multiplier=8, main_min_odds=1.80,
        deposit_amount=1000,
    )
    repo.record_wagering(1, "betinia", stake=1000, odds=1.50)   # below 1.80
    st = repo.get_bonus_status(1, "betinia")
    assert st["status"] == "trigger_needed"        # did not advance
    assert st["wagered_amount"] == 0               # did not count


def test_no_double_credit_on_repeated_record(db: Session):
    """Once in_progress, further record_wagering must not re-credit the bonus."""
    repo = ProfileRepo(db)
    db.add(ProfileProviderBalance(profile_id=1, provider_id="betinia", balance=1000))
    db.commit()
    repo.start_bonus_trigger(
        1, "betinia", bonus_amount=1000, trigger_wagering=1000,
        trigger_min_odds=1.50, main_wagering_multiplier=8, main_min_odds=1.80,
        deposit_amount=1000,
    )
    repo.record_wagering(1, "betinia", stake=1000, odds=2.0)    # trigger -> in_progress, +1000
    assert _bal(repo, "betinia") == 2000
    repo.record_wagering(1, "betinia", stake=2000, odds=2.0)    # partial main progress
    assert _bal(repo, "betinia") == 2000                        # NO further credit


def test_is_bonus_not_misfired_for_bonusdeposit_in_progress(db: Session):
    """A bet placed while a bonusdeposit row is in_progress is real money
    (is_bonus=False) — the freebet derivation keys only on freebet_available."""
    db.add(ProfileProviderBalance(profile_id=1, provider_id="betinia", balance=10000))
    db.add(ProfileProviderBonus(
        profile_id=1, provider_id="betinia", bonus_status="in_progress",
        bonus_type="bonusdeposit", bonus_amount=1000, wagering_requirement=8000,
        wagered_amount=0.0, min_odds=1.80,
    ))
    db.commit()
    result = BetService(db).create_bet(
        event_id=None, provider_id="betinia", market="1x2", outcome="1",
        odds=2.0, stake=1000, is_bonus=False,
    )
    assert result.get("success") is True, result
    bet = db.query(Bet).filter(Bet.id == result["bet_id"]).first()
    assert bet.is_bonus is False
```

- [ ] **Step 2: Run — expect PASS (verification)**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/backend" && python -m pytest tests/test_bonusdeposit_lifecycle.py -v`
Expected: all 6 PASS. **If any fail, STOP** — a real engine bug is present; report the failure (the controller decides the fix) before continuing.

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity"
git add backend/tests/test_bonusdeposit_lifecycle.py
git commit -m "test(bonusdeposit): state-machine lifecycle matrix (two-phase/wager-first/single + is_bonus guard)"
```

---

### Task 2: Fix the wager-first config bug (RED → GREEN)

**Files:**
- Modify: `backend/src/config/providers.yaml` (leovegas, expekt, betmgm bonus blocks)
- Modify: `backend/tests/test_bonusdeposit_lifecycle.py` (add config-wiring test)

- [ ] **Step 1: Write the failing config-wiring test**

Append to `backend/tests/test_bonusdeposit_lifecycle.py`:

```python
def test_leovegas_config_completes_at_trigger(db: Session):
    """Integration via deposit_with_bonus reading the REAL yaml: leovegas is
    wager-first, so after the trigger wager it must COMPLETE (not enter a main
    phase). Fails until wagering_multiplier:0 is set in providers.yaml."""
    from src.services.bankroll_service import BankrollService

    svc = BankrollService(db)
    res = svc.deposit_with_bonus("leovegas", 600)   # deposit == bonus cap
    assert res["bonus_status"] == "trigger_needed"
    trig_req = res["wagering_requirement"]           # deposit * trigger_multiplier = 600*6
    assert trig_req == 3600

    repo = ProfileRepo(db)
    repo.record_wagering(1, "leovegas", stake=trig_req, odds=1.9)   # meet trigger @1.80+
    assert repo.get_bonus_status(1, "leovegas")["status"] == "completed"
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/backend" && python -m pytest tests/test_bonusdeposit_lifecycle.py::test_leovegas_config_completes_at_trigger -v`
Expected: FAIL — status is `in_progress` (default `wagering_multiplier=10` created a spurious `600*10=6000` main phase).

- [ ] **Step 3: Fix the configs**

In `backend/src/config/providers.yaml`, add `wagering_multiplier: 0` under the bonus block for each wager-first provider. For **leovegas** (find `id: leovegas` → its `bonus:` block):

```yaml
    bonus:
      type: bonusdeposit
      amount: 600
      trigger_multiplier: 6        # Wager-first: wager deposit×6 at 1.80+ → bonus as cash
      trigger_odds: 1.80
      trigger_mode: cumulative
      wagering_multiplier: 0       # trigger completion = done (no main wagering phase)
```

Do the same for **expekt** (`amount: 1000`, `trigger_multiplier: 20`) and **betmgm** (`amount: 500`, `trigger_multiplier: 10`) — add `wagering_multiplier: 0` to each bonus block.

- [ ] **Step 4: Run — expect PASS**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/backend" && python -m pytest tests/test_bonusdeposit_lifecycle.py -v`
Expected: all PASS (7 now).

- [ ] **Step 5: Commit**

```bash
cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity"
git add backend/src/config/providers.yaml backend/tests/test_bonusdeposit_lifecycle.py
git commit -m "fix(bonus): wager-first providers complete at trigger (wagering_multiplier:0)"
```

---

### Task 3: Flag placeholder wagering configs

No test — annotation only, so future readers (and the user) know which `wagering_multiplier` values are real vs. the `10.0` default placeholder.

**Files:**
- Modify: `backend/src/config/providers.yaml`

- [ ] **Step 1: Annotate the trigger-then-main + immediate bonusdeposit providers**

For each `bonusdeposit` provider that does NOT set an explicit `wagering_multiplier` after Task 2 (i.e. all of them except leovegas/expekt/betmgm — betinia, campobet, swiper, quickcasino, speedybet, x3000, goldenbull, 1x2, lodur, 888sport, spelklubben, bethard, 10bet, snabbare, comeon, and any other bonusdeposit blocks), add a comment line inside the bonus block:

```yaml
      # TODO(bonus-terms): wagering_multiplier unset -> defaults to 10 (placeholder).
      # Confirm real T&C wagering (× bonus) + min_odds + deadline_days before relying on tracking.
```

Place it as the last line of each such `bonus:` block. Do not change any values.

- [ ] **Step 2: Verify yaml still parses (no test infra change)**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/backend" && python -m pytest tests/test_bonusdeposit_lifecycle.py -q`
Expected: still all PASS (comments don't change behavior; this confirms the yaml is still valid and loads).

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity"
git add backend/src/config/providers.yaml
git commit -m "docs(bonus): flag placeholder wagering_multiplier configs (TODO bonus-terms)"
```

---

## PHASE 2 — Frontend: extend BonusChip for bonusdeposit

### Task 4: Extend the resolver (TDD)

**Files:**
- Modify: `frontend/src/pages/bonusChipState.ts`
- Modify: `frontend/src/pages/bonusChipState.test.ts`

- [ ] **Step 1: Add the failing bonusdeposit tests**

Append to `frontend/src/pages/bonusChipState.test.ts` (inside the existing `describe`, or a new one):

```ts
describe('resolveBonusChipState — bonusdeposit', () => {
  const bd = (over: Partial<BonusChipInput> = {}): BonusChipInput => ({
    balanceNative: 0, isDrained: true, pendingCount: 0,
    progress: null, config: { type: 'bonusdeposit', amount: 500 },
    triggerCurrency: 'SEK', ...over,
  })

  test('available bonusdeposit, drained -> bd_deposit with cap amount', () => {
    expect(resolveBonusChipState(bd())).toEqual({ kind: 'bd_deposit', amount: 500, currency: 'SEK' })
  })

  test('trigger_needed -> bd_trigger progress', () => {
    const progress = { status: 'trigger_needed', bonus_type: 'bonusdeposit', bonus_amount: 500, wagering_requirement: 500, wagered_amount: 200, min_odds: 1.5 }
    expect(resolveBonusChipState(bd({ progress, isDrained: false, balanceNative: 500 })))
      .toEqual({ kind: 'bd_trigger', wagered: 200, requirement: 500, minOdds: 1.5 })
  })

  test('in_progress -> bd_wagering progress with bonus amount', () => {
    const progress = { status: 'in_progress', bonus_type: 'bonusdeposit', bonus_amount: 500, wagering_requirement: 5000, wagered_amount: 1200, min_odds: 1.8 }
    expect(resolveBonusChipState(bd({ progress, isDrained: false, balanceNative: 1000 })))
      .toEqual({ kind: 'bd_wagering', wagered: 1200, requirement: 5000, minOdds: 1.8, bonusAmount: 500 })
  })

  test('completed -> none', () => {
    const progress = { status: 'completed', bonus_type: 'bonusdeposit', bonus_amount: 500, wagering_requirement: 5000, wagered_amount: 5000, min_odds: 1.8 }
    expect(resolveBonusChipState(bd({ progress }))).toEqual({ kind: 'none' })
  })

  test('bonusdeposit funded (not drained, no row) -> none (no clutter)', () => {
    expect(resolveBonusChipState(bd({ isDrained: false, balanceNative: 500 }))).toEqual({ kind: 'none' })
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/frontend" && npx vitest run src/pages/bonusChipState.test.ts`
Expected: the new bonusdeposit tests FAIL (resolver returns `{kind:'none'}` for non-freebet); existing 12 freebet tests still PASS.

- [ ] **Step 3: Restructure the resolver to branch on bonus_type**

Replace the body of `resolveBonusChipState` in `frontend/src/pages/bonusChipState.ts` (keep the types/interfaces above it unchanged) and extend the `BonusChipState` union. Full new content from `export type BonusChipState` onward:

```ts
export type BonusChipState =
  | { kind: 'none' }
  | { kind: 'deposit_hint'; amount: number; currency: string }
  | { kind: 'deposit_detected'; amount: number; currency: string }
  | { kind: 'wagering'; wagered: number; requirement: number; minOdds: number }
  | { kind: 'unlock_ready'; amount: number }
  | { kind: 'freebet_ready'; amount: number }
  | { kind: 'bd_deposit'; amount: number; currency: string }
  | { kind: 'bd_trigger'; wagered: number; requirement: number; minOdds: number }
  | { kind: 'bd_wagering'; wagered: number; requirement: number; minOdds: number; bonusAmount: number }

// A deposit "counts" once the balance reaches ~90% of the freebet amount —
// tolerant of rounding/fees on the bookmaker side. Below that the user still
// gets a manual "start tracking" button via deposit_hint, so they're never
// blocked by detection being slightly off.
const DEPOSIT_DETECT_RATIO = 0.9

export function resolveBonusChipState(input: BonusChipInput): BonusChipState {
  const { balanceNative, isDrained, pendingCount, progress, config, triggerCurrency } = input
  const status = progress?.status ?? null
  const bonusType = progress?.bonus_type ?? config?.type ?? null

  if (bonusType === 'freebet') {
    // --- Freebet lifecycle (unchanged) ---
    if (status === 'trigger_needed') {
      const requirement = progress!.wagering_requirement
      const wagered = progress!.wagered_amount
      // requirement > 0 is intentional: a zero requirement keeps the chip in
      // 'wagering' rather than instantly offering unlock on a 0/0 false positive.
      if (requirement > 0 && wagered >= requirement) {
        return { kind: 'unlock_ready', amount: progress!.bonus_amount }
      }
      return { kind: 'wagering', wagered, requirement, minOdds: progress!.min_odds }
    }
    if (status === 'freebet_available') {
      return { kind: 'freebet_ready', amount: progress!.bonus_amount }
    }
    if (status === 'completed' || status === 'claimed' || status === 'in_progress') {
      return { kind: 'none' }
    }
    if (!config) return { kind: 'none' }
    const amount = config.amount ?? 0
    if (amount <= 0) return { kind: 'none' }
    if (balanceNative >= amount * DEPOSIT_DETECT_RATIO) {
      return { kind: 'deposit_detected', amount, currency: triggerCurrency }
    }
    if (isDrained && pendingCount === 0) {
      return { kind: 'deposit_hint', amount, currency: triggerCurrency }
    }
    return { kind: 'none' }
  }

  if (bonusType === 'bonusdeposit') {
    // --- Bonusdeposit lifecycle ---
    if (status === 'trigger_needed') {
      return {
        kind: 'bd_trigger',
        wagered: progress!.wagered_amount,
        requirement: progress!.wagering_requirement,
        minOdds: progress!.min_odds,
      }
    }
    if (status === 'in_progress') {
      return {
        kind: 'bd_wagering',
        wagered: progress!.wagered_amount,
        requirement: progress!.wagering_requirement,
        minOdds: progress!.min_odds,
        bonusAmount: progress!.bonus_amount,
      }
    }
    if (status === 'completed' || status === 'claimed') {
      return { kind: 'none' }
    }
    // available / absent: offer deposit & start (explicit amount), bonus-only only
    if (!config) return { kind: 'none' }
    const amount = config.amount ?? 0
    if (amount <= 0) return { kind: 'none' }
    if (isDrained && pendingCount === 0) {
      return { kind: 'bd_deposit', amount, currency: triggerCurrency }
    }
    return { kind: 'none' }
  }

  return { kind: 'none' }
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/frontend" && npx vitest run src/pages/bonusChipState.test.ts`
Expected: all PASS (12 freebet + 5 bonusdeposit = 17). Also `npx tsc --noEmit` exit 0.

- [ ] **Step 5: Commit**

```bash
cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity"
git add frontend/src/pages/bonusChipState.ts frontend/src/pages/bonusChipState.test.ts
git commit -m "feat(bonus): resolver handles bonusdeposit states (bd_deposit/bd_trigger/bd_wagering)"
```

---

### Task 5: Add `depositWithBonus` API shim

**Files:**
- Modify: `frontend/src/hooks/useApi.ts`

- [ ] **Step 1: Add the shim**

In `frontend/src/hooks/useApi.ts`, immediately after the existing `backfillWagering:` line (added for freebet), add:

```ts
  depositWithBonus: (providerId: string, amount: number) =>
    apiFetch<any>(`/api/bankroll/deposit/${providerId}`, { method: 'POST', body: JSON.stringify({ amount }) }),
```

- [ ] **Step 2: Verify type-check**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/frontend" && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity"
git add frontend/src/hooks/useApi.ts
git commit -m "feat(bonus): add depositWithBonus api shim"
```

---

### Task 6: Extend `BonusChip` for bonusdeposit states

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx` (the `BonusChip` component, just above `export default function PlayPage()`)

- [ ] **Step 1: Add bonusdeposit rendering + a deposit-amount input**

In `BonusChip`, the component currently `return null` when `state.kind === 'none'` and renders the freebet states. Add handling for the three `bd_*` kinds. Locate the line `// state.kind === 'freebet_ready'` (the final freebet branch) and insert the bonusdeposit branches BEFORE it (after the `unlock_ready` block). Insert:

```tsx
  if (state.kind === 'bd_deposit') {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-amber-400">matched bonus up to {state.amount.toFixed(0)} {state.currency.toLowerCase()}</span>
        <input
          type="number"
          value={depositAmt}
          min={0}
          onChange={(e) => setDepositAmt(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          className="w-16 px-1 py-0.5 text-[10px] rounded bg-zinc-900 text-zinc-200 border border-zinc-700"
          title="Deposit amount (defaults to the bonus cap; edit if you deposited less)."
        />
        <button
          disabled={busy}
          onClick={(e) => {
            e.stopPropagation()
            const amt = Number(depositAmt)
            if (!Number.isFinite(amt) || amt <= 0) return
            run(() => api.depositWithBonus(pid, amt))
          }}
          className={`${btn} bg-emerald-900/40 text-emerald-300 border-emerald-700/50 hover:bg-emerald-800/50`}
          title={`Record your ${pidLabel} deposit and arm bonus tracking. Adds the deposit to the tracked balance.`}
        >
          deposit &amp; start
        </button>
        {claimBtn}
      </span>
    )
  }

  if (state.kind === 'bd_trigger') {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-zinc-400">
          trigger: {state.wagered.toFixed(0)}/{state.requirement.toFixed(0)} @ ≥{state.minOdds.toFixed(2)}
        </span>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); run(() => api.backfillWagering()) }}
          className={`${btn} bg-zinc-800 text-zinc-500 border-zinc-700 hover:text-zinc-300`}
          title="Replay settled bets through wagering (use if bets were placed before tracking started)."
        >
          replay
        </button>
      </span>
    )
  }

  if (state.kind === 'bd_wagering') {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-emerald-300">🔓 {state.bonusAmount.toFixed(0)} bonus unlocked</span>
        <span className="text-zinc-400">
          wager: {state.wagered.toFixed(0)}/{state.requirement.toFixed(0)} @ ≥{state.minOdds.toFixed(2)}
        </span>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); run(() => api.backfillWagering()) }}
          className={`${btn} bg-zinc-800 text-zinc-500 border-zinc-700 hover:text-zinc-300`}
          title="Replay settled bets through wagering."
        >
          replay
        </button>
      </span>
    )
  }
```

- [ ] **Step 2: Add the `depositAmt` state hook**

The `bd_deposit` branch references `depositAmt`/`setDepositAmt`. Add this hook near the top of `BonusChip`, right after the existing `const [busy, setBusy] = useState(false)` line:

```tsx
  // Deposit-amount buffer for bonusdeposit "deposit & start". Defaults to the
  // bonus cap (config.amount via the resolved state); stored as a string so the
  // field is freely editable. Only read when state.kind === 'bd_deposit'.
  const [depositAmt, setDepositAmt] = useState<string>(String(config?.amount ?? ''))
```

(`config` is already a prop on `BonusChip`.)

- [ ] **Step 3: Verify tsc + vitest + build**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/frontend" && npx tsc --noEmit && npx vitest run src/pages/bonusChipState.test.ts && npm run build`
Expected: tsc exit 0; vitest 17 pass; `vite build` succeeds.

- [ ] **Step 4: Commit**

```bash
cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity"
git checkout -- frontend/tsconfig.tsbuildinfo 2>/dev/null; true
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(bonus): BonusChip renders bonusdeposit lifecycle (deposit&start + trigger/main progress)"
```

---

### Task 7: Full verification + scope check

**Files:** none (verification only).

- [ ] **Step 1: Backend suite (touching the bonus machine)**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/backend" && python -m pytest tests/test_bonusdeposit_lifecycle.py tests/test_bankroll_service_trigger.py tests/test_bet_service_freebet.py tests/test_ban_system.py -q`
Expected: all PASS.

- [ ] **Step 2: Frontend gates**

Run: `cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity/frontend" && npx tsc --noEmit && npx vitest run && npm run build`
Expected: tsc exit 0; all vitest pass; build succeeds.

- [ ] **Step 3: Discard build artifact + confirm scope**

Run:
```bash
cd "c:/Users/rasmu/betty/.claude/worktrees/bonusdeposit-parity"
git checkout -- frontend/tsconfig.tsbuildinfo 2>/dev/null; true
git status --porcelain   # expect empty
git diff --name-only origin/main...HEAD | grep -vE '^(backend|frontend|docs)/' || echo "BACKEND/FRONTEND/DOCS ONLY ✓"
```
Expected: clean tree; changed files only under `backend/` (config + tests), `frontend/`, `docs/`.

- [ ] **Step 4: Manual smoke (with user; needs a bonusdeposit provider)**

Interactive — do with the user, don't automate. Via `local\betty.bat`, pick a bonusdeposit provider (e.g. betinia): chip shows "matched bonus up to N" + deposit input → enter amount, "deposit & start" → chip shows "trigger: x/y" → place qualifying bets → "🔓 bonus unlocked — wager: x/y" → place wagering bets → chip drops out at completion. For a wager-first book (leovegas), confirm it completes right at trigger (no main phase).

---

## Deployment (post-merge, user-confirmed)

This PR includes backend (`providers.yaml` + tests) → a rebuild is required. After merge to main, deploy via `/deploy` (`server-deploy.sh rebuild backend`) and verify server HEAD == merge commit, `/health` boot_id changed, and `wagering_multiplier: 0` present in the running container's `providers.yaml`. Not a plan task — handled in finishing-a-development-branch.

---

## Self-Review Notes

- **Spec coverage:** wager-first fix (Task 2), state-machine verification matrix + balance/min-odds/no-double-credit (Task 1), is_bonus non-misfire (Task 1 final test), placeholder-config flagging (Task 3), resolver bonusdeposit states (Task 4), depositWithBonus wiring (Task 5), chip rendering with deposit input (Task 6), verification + scope (Task 7). ✓
- **Type consistency:** `BonusChipState` union extended with `bd_deposit`/`bd_trigger`/`bd_wagering`; the chip branches match the resolver kinds exactly; `depositWithBonus(pid, amount)` signature matches the chip call. `bonusAmount` field name consistent between resolver `bd_wagering` and the chip render.
- **TDD:** Task 1 is verification (pass-first; stop if red = real bug). Task 2 and Task 4 are red→green. Task 6 is render wiring (verified by tsc/build; the logic it depends on is unit-tested in Task 4).
- **Balance:** state machine adjusts balance (user's decision); tests assert single-credit (`test_no_double_credit_on_repeated_record`).
