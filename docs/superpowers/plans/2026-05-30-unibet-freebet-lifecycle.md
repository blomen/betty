# Unibet Freebet Lifecycle UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Unibet (and any freebet-type Kambi book) deposit → qualifying-bet → unlock → freebet-used lifecycle into the Sports tab as an inline, state-driven chip.

**Architecture:** Pure-frontend. A new pure function `resolveBonusChipState()` maps live bonus state (from the existing `/bankroll/status` + `/bankroll/bonuses` endpoints) to one of six display states; a new `<BonusChip>` component renders that state and fires the existing `/bankroll/bonus-transition` and `/bankroll/claim-bonus` endpoints. The chip replaces the duplicated "mark claimed" button at PlayPage's two render sites. No backend changes → no server redeploy.

**Tech Stack:** React 19 + TypeScript + Vite, vitest (pure-logic tests), Tailwind. All work in the worktree at `C:\Users\rasmu\betty\.claude\worktrees\unibet-freebet-lifecycle`.

**Reference spec:** `docs/superpowers/specs/2026-05-30-unibet-freebet-lifecycle-design.md`

---

## File Structure

- **Create** `frontend/src/pages/bonusChipState.ts` — pure state-resolution logic + types. No React. The only unit-tested module.
- **Create** `frontend/src/pages/bonusChipState.test.ts` — vitest tests for the resolver (the bug-prone branching).
- **Modify** `frontend/src/hooks/useApi.ts` — add `getBankrollStatus`, `getProviderBonuses`, `bonusTransition`, `claimBonus` methods to the `api` object.
- **Modify** `frontend/src/pages/PlayPage.tsx` — add `getBalanceNative` helper; add `bonusProgress` + `bonusConfigs` state; fetch them in `load()` / on mount; define the `<BonusChip>` component; replace the two duplicated "mark claimed" sites with `<BonusChip>`.

> **Note vs spec:** the spec mentioned `services/api/bankroll.ts`, but PlayPage imports its API from `hooks/useApi.ts` (the `services/` layer is react-query, used only by the Bankroll page). New methods therefore go in `useApi.ts`.

---

## Task 0: Verify worktree frontend deps are installed

**Files:** none (environment check).

- [ ] **Step 1: Confirm `node_modules` + vitest are present**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && ls node_modules/.bin/vitest && ls node_modules/.bin/tsc
```
Expected: both paths print (no "No such file"). If missing, run `npm install --no-audit --no-fund` in that `frontend/` dir first (one-time; worktrees don't share the main checkout's `node_modules`).

---

## Task 1: Pure state-resolution module (TDD)

**Files:**
- Create: `frontend/src/pages/bonusChipState.ts`
- Test: `frontend/src/pages/bonusChipState.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/bonusChipState.test.ts`:

```ts
import { describe, test, expect } from 'vitest'
import { resolveBonusChipState, type BonusChipInput } from './bonusChipState'

// Minimal base input: a freebet provider, fresh account, no deposit yet.
const base: BonusChipInput = {
  balanceNative: 0,
  isDrained: true,
  pendingCount: 0,
  progress: null,
  config: { type: 'freebet', amount: 1000, min_odds: 1.8 },
  triggerCurrency: 'SEK',
}

describe('resolveBonusChipState', () => {
  test('fresh freebet provider, no deposit -> deposit_hint', () => {
    expect(resolveBonusChipState(base)).toEqual({ kind: 'deposit_hint', amount: 1000, currency: 'SEK' })
  })

  test('balance covers the freebet amount, no row yet -> deposit_detected', () => {
    expect(resolveBonusChipState({ ...base, balanceNative: 1000, isDrained: false }))
      .toEqual({ kind: 'deposit_detected', amount: 1000, currency: 'SEK' })
  })

  test('deposit detection tolerates rounding (>= 90% of amount)', () => {
    expect(resolveBonusChipState({ ...base, balanceNative: 950, isDrained: false }).kind)
      .toBe('deposit_detected')
  })

  test('partial balance below detection but not drained -> none (no clutter)', () => {
    expect(resolveBonusChipState({ ...base, balanceNative: 300, isDrained: false }))
      .toEqual({ kind: 'none' })
  })

  test('no freebet config and no row -> none', () => {
    expect(resolveBonusChipState({ ...base, config: null })).toEqual({ kind: 'none' })
  })

  test('non-freebet config -> none', () => {
    expect(resolveBonusChipState({ ...base, config: { type: 'bonusdeposit', amount: 1000 } }))
      .toEqual({ kind: 'none' })
  })

  test('trigger_needed, wagering incomplete -> wagering', () => {
    const progress = { status: 'trigger_needed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 200, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, balanceNative: 1000, isDrained: false, progress }))
      .toEqual({ kind: 'wagering', wagered: 200, requirement: 1000, minOdds: 1.8 })
  })

  test('trigger_needed, wagering met -> unlock_ready', () => {
    const progress = { status: 'trigger_needed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, balanceNative: 0, progress }))
      .toEqual({ kind: 'unlock_ready', amount: 1000 })
  })

  test('freebet_available -> freebet_ready', () => {
    const progress = { status: 'freebet_available', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, progress }))
      .toEqual({ kind: 'freebet_ready', amount: 1000 })
  })

  test('completed -> none', () => {
    const progress = { status: 'completed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, progress })).toEqual({ kind: 'none' })
  })

  test('claimed -> none (already dismissed)', () => {
    const progress = { status: 'claimed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 0, wagered_amount: 0, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, progress })).toEqual({ kind: 'none' })
  })

  test('active lifecycle wins even with config absent (live row is source of truth)', () => {
    const progress = { status: 'freebet_available', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, config: null, progress }))
      .toEqual({ kind: 'freebet_ready', amount: 1000 })
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npx vitest run src/pages/bonusChipState.test.ts
```
Expected: FAIL — `Failed to resolve import "./bonusChipState"` (module doesn't exist yet).

- [ ] **Step 3: Write the minimal implementation**

Create `frontend/src/pages/bonusChipState.ts`:

```ts
// Pure decision logic for the Sports-tab freebet chip. No React, no I/O — kept
// in its own module so the branching (the bug-prone part) is unit-testable in
// isolation without mounting PlayPage. <BonusChip> in PlayPage.tsx renders the
// result; the resolver decides WHICH of the six states applies.

/** Subset of BonusProgressEntry (/bankroll/status) the resolver needs. */
export interface BonusChipProgress {
  status: string
  bonus_type: string | null
  bonus_amount: number
  wagering_requirement: number
  wagered_amount: number
  min_odds: number
}

/** Subset of a /bankroll/bonuses yaml entry the resolver needs. */
export interface ProviderBonusConfig {
  type?: string
  amount?: number
  min_odds?: number
}

export interface BonusChipInput {
  /** Provider balance in its OWN currency (compared against the native-currency freebet amount). */
  balanceNative: number
  /** Caller-supplied "near-empty" flag (PlayPage: bal < DRAIN_THRESHOLD_SEK). */
  isDrained: boolean
  pendingCount: number
  /** Live bonus row for this provider, or null if none exists yet. */
  progress: BonusChipProgress | null
  /** Static yaml bonus config for this provider, or null. */
  config: ProviderBonusConfig | null
  /** Display currency for the amount (e.g. 'SEK'). */
  triggerCurrency: string
}

export type BonusChipState =
  | { kind: 'none' }
  | { kind: 'deposit_hint'; amount: number; currency: string }
  | { kind: 'deposit_detected'; amount: number; currency: string }
  | { kind: 'wagering'; wagered: number; requirement: number; minOdds: number }
  | { kind: 'unlock_ready'; amount: number }
  | { kind: 'freebet_ready'; amount: number }

// A deposit "counts" once the balance reaches ~90% of the freebet amount —
// tolerant of rounding/fees on the bookmaker side. Below that the user still
// gets a manual "start tracking" button via deposit_hint, so they're never
// blocked by detection being slightly off.
const DEPOSIT_DETECT_RATIO = 0.9

export function resolveBonusChipState(input: BonusChipInput): BonusChipState {
  const { balanceNative, isDrained, pendingCount, progress, config, triggerCurrency } = input
  const status = progress?.status ?? null

  // 1) Active freebet lifecycle — live row is source of truth, independent of balance.
  if (status === 'trigger_needed') {
    const requirement = progress!.wagering_requirement
    const wagered = progress!.wagered_amount
    if (requirement > 0 && wagered >= requirement) {
      return { kind: 'unlock_ready', amount: progress!.bonus_amount }
    }
    return { kind: 'wagering', wagered, requirement, minOdds: progress!.min_odds }
  }
  if (status === 'freebet_available') {
    return { kind: 'freebet_ready', amount: progress!.bonus_amount }
  }
  // completed/claimed -> row drops out (existing behavior). in_progress is a
  // bonusdeposit phase, not handled by the freebet chip.
  if (status === 'completed' || status === 'claimed' || status === 'in_progress') {
    return { kind: 'none' }
  }

  // 2) No active row (status 'available' or absent). Need a freebet config.
  const bonusType = progress?.bonus_type ?? config?.type ?? null
  if (bonusType !== 'freebet' || !config) return { kind: 'none' }
  const amount = config.amount ?? 0
  if (amount <= 0) return { kind: 'none' }

  // Deposit detected: balance covers (most of) the freebet amount.
  if (balanceNative >= amount * DEPOSIT_DETECT_RATIO) {
    return { kind: 'deposit_detected', amount, currency: triggerCurrency }
  }
  // Pre-deposit hint: only for bonus-only providers (near-empty, no pending),
  // matching the existing onlyBonus gate so funded clusters aren't cluttered.
  if (isDrained && pendingCount === 0) {
    return { kind: 'deposit_hint', amount, currency: triggerCurrency }
  }
  return { kind: 'none' }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npx vitest run src/pages/bonusChipState.test.ts
```
Expected: PASS — all 12 tests green.

- [ ] **Step 5: Commit**

```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle"
git add frontend/src/pages/bonusChipState.ts frontend/src/pages/bonusChipState.test.ts
git commit -m "feat(freebet): pure resolver for sports-tab bonus chip state"
```

---

## Task 2: API methods on the `api` object

**Files:**
- Modify: `frontend/src/hooks/useApi.ts` (the `api` object PlayPage imports)

- [ ] **Step 1: Add the four bonus methods**

In `frontend/src/hooks/useApi.ts`, inside the `api = { ... }` object, immediately after the existing `getBankrollStats:` line (`getBankrollStats: () => apiFetch<any>('/api/bankroll/stats'),`), add:

```ts
  // Bonus / freebet lifecycle. /status and /bonuses already exist server-side;
  // these are the missing shims for the Sports-tab BonusChip. bonus-transition
  // advances freebet phases (start_freebet | trigger_settled | freebet_used);
  // claim-bonus dismisses ("taken on another account").
  getBankrollStatus: () => apiFetch<any>('/api/bankroll/status'),
  getProviderBonuses: () => apiFetch<any>('/api/bankroll/bonuses'),
  bonusTransition: (providerId: string, action: 'start_freebet' | 'trigger_settled' | 'freebet_used') =>
    apiFetch<any>(`/api/bankroll/bonus-transition/${providerId}`, { method: 'POST', body: JSON.stringify({ action }) }),
  claimBonus: (providerId: string) =>
    apiFetch<any>(`/api/bankroll/claim-bonus/${providerId}`, { method: 'POST' }),
  backfillWagering: () => apiFetch<any>('/api/bankroll/backfill-wagering', { method: 'POST' }),
```

- [ ] **Step 2: Verify type-check passes**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npx tsc --noEmit
```
Expected: no errors (exit 0).

- [ ] **Step 3: Commit**

```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle"
git add frontend/src/hooks/useApi.ts
git commit -m "feat(freebet): add bonus-status/bonuses/transition api shims"
```

---

## Task 3: Plumb bonus state into PlayPage

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx` (helper near `getBalance` ~line 89; state ~line 565; `load()` ~line 1117; a new mount effect)

- [ ] **Step 1: Add `getBalanceNative` helper**

In `frontend/src/pages/PlayPage.tsx`, immediately after the `getBalance` helper (ends ~line 90), add:

```ts
// Balance in the provider's OWN currency (not normalised to SEK). Needed to
// compare against native-currency freebet amounts. Falls back to the SEK-
// normalised balance when balance_native is absent (SEK providers: equal).
const getBalanceNative = (b: ProviderBalanceLike | undefined): number =>
  typeof b === 'number' ? b : (b?.balance_native ?? b?.balance ?? 0)
```

- [ ] **Step 2: Add bonus state + import the config/progress types**

At the top of `PlayPage.tsx`, add an import for the resolver types (place near the other type imports; the file already uses the `@/types` alias elsewhere, but the resolver is a sibling module):

```ts
import { resolveBonusChipState, type BonusChipProgress, type ProviderBonusConfig } from './bonusChipState'
import type { BonusProgressEntry } from '@/types'
```

Then, next to the `providerBalances` state declaration (~line 565), add:

```ts
  // Live bonus rows (/bankroll/status) + static yaml configs (/bankroll/bonuses).
  // Kept separate from providerBalances because /bankroll (the balance poll)
  // does NOT carry bonus_status, and the trigger amount vanishes once balance
  // >= amount. configs are fetched once on mount (static); progress every poll.
  const [bonusProgress, setBonusProgress] = useState<Record<string, BonusProgressEntry>>({})
  const [bonusConfigs, setBonusConfigs] = useState<Record<string, ProviderBonusConfig>>({})
```

- [ ] **Step 3: Fetch `/bankroll/status` in the poll**

In `load()` (~line 1119), change the `Promise.all` to add the status fetch. Replace:

```ts
      const [result, pendingResult, bankrollResult] = await Promise.all([
        api.getPlayBatch(),
        api.getPendingBets().catch(() => ({ providers: [] })),
        api.getBankrollSummary().catch(() => ({ providers: [] })),
      ])
```

with:

```ts
      const [result, pendingResult, bankrollResult, statusResult] = await Promise.all([
        api.getPlayBatch(),
        api.getPendingBets().catch(() => ({ providers: [] })),
        api.getBankrollSummary().catch(() => ({ providers: [] })),
        api.getBankrollStatus().catch(() => ({ bonus_progress: {} })),
      ])
```

Then, immediately after `setProviderBalances(balanceMap)` (~line 1141), add:

```ts
      setBonusProgress(statusResult.bonus_progress ?? {})
```

- [ ] **Step 4: Fetch `/bankroll/bonuses` once on mount**

Immediately after the existing `useEffect` that sets up the poll (the one ending with `}, [load])` ~line 1174), add a new effect:

```ts
  // Static bonus configs (freebet amount/type/min-odds per provider). Fetched
  // once — they come from providers.yaml and don't change within a session.
  // Needed for fresh accounts whose balance now masks the trigger amount.
  useEffect(() => {
    api.getProviderBonuses()
      .then((cfg: Record<string, ProviderBonusConfig>) => setBonusConfigs(cfg ?? {}))
      .catch(() => { /* leave configs empty; chip falls back to live progress */ })
  }, [])
```

- [ ] **Step 5: Verify type-check passes**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npx tsc --noEmit
```
Expected: errors ONLY about `resolveBonusChipState`/`BonusChipProgress` being imported-but-unused (they're used in Task 4). No other errors. If `tsc` flags unused imports as errors here, proceed — Task 4 consumes them; otherwise it's clean.

> If the project's tsconfig treats unused imports as hard errors and blocks the commit, combine Tasks 3 and 4 into one commit instead of committing here.

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle"
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(freebet): plumb bonus_progress + configs into PlayPage poll"
```

---

## Task 4: BonusChip component

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx` (add a component above `export default function PlayPage()`)

- [ ] **Step 1: Add the `<BonusChip>` component**

In `frontend/src/pages/PlayPage.tsx`, immediately ABOVE `export default function PlayPage()`, add:

```tsx
// Inline freebet-lifecycle chip for the Sports tab. Renders one of six states
// (see resolveBonusChipState) and fires the existing bonus-transition /
// claim-bonus endpoints. Shared by BOTH cluster render sites so the logic
// can't drift (CLAUDE.md flags "two divergent renders" as a recurring bug).
function BonusChip(props: {
  pid: string
  balanceNative: number
  isDrained: boolean
  pendingCount: number
  progress: BonusChipProgress | null
  config: ProviderBonusConfig | null
  currency: string
  onChanged: () => void
}) {
  const { pid, balanceNative, isDrained, pendingCount, progress, config, currency, onChanged } = props
  const state = resolveBonusChipState({ balanceNative, isDrained, pendingCount, progress, config, triggerCurrency: currency })
  const [busy, setBusy] = useState(false)

  if (state.kind === 'none') return null

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      onChanged()
    } catch (err) {
      console.warn(`[bonus-chip] ${pid} action failed`, err)
    } finally {
      setBusy(false)
    }
  }

  const pidLabel = pid.toUpperCase()
  const btn = 'px-1.5 py-0.5 text-[9px] uppercase tracking-wider rounded border cursor-pointer disabled:opacity-50'
  const claimBtn = (
    <button
      disabled={busy}
      onClick={(e) => { e.stopPropagation(); run(() => api.claimBonus(pid)) }}
      className={`${btn} bg-zinc-800 text-zinc-400 border-zinc-700 hover:bg-zinc-700 hover:text-zinc-200`}
      title={`Mark ${pidLabel}'s bonus as claimed — hides this row. Reversible from Bankroll tab.`}
    >
      mark claimed
    </button>
  )

  if (state.kind === 'deposit_hint') {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-amber-400">deposit {state.amount.toFixed(0)} {state.currency.toLowerCase()}</span>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); run(() => api.bonusTransition(pid, 'start_freebet')) }}
          className={`${btn} bg-emerald-900/40 text-emerald-300 border-emerald-700/50 hover:bg-emerald-800/50`}
          title={`Start freebet tracking for ${pidLabel} (after you deposit, then place one qualifying bet).`}
        >
          start tracking
        </button>
        {claimBtn}
      </span>
    )
  }

  if (state.kind === 'deposit_detected') {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-emerald-400">✓ deposit detected</span>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); run(() => api.bonusTransition(pid, 'start_freebet')) }}
          className={`${btn} bg-emerald-900/40 text-emerald-300 border-emerald-700/50 hover:bg-emerald-800/50`}
          title={`Start the ${state.amount.toFixed(0)} ${state.currency} freebet tracking for ${pidLabel}.`}
        >
          start freebet tracking
        </button>
        {claimBtn}
      </span>
    )
  }

  if (state.kind === 'wagering') {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-zinc-400">
          qualifying bet: {state.wagered.toFixed(0)}/{state.requirement.toFixed(0)} @ ≥{state.minOdds.toFixed(2)}
        </span>
        {/* Escape hatch: if the qualifying bet was placed BEFORE tracking
            started, wagered stays 0. Replay settled bets to backfill. */}
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); run(() => api.backfillWagering()) }}
          className={`${btn} bg-zinc-800 text-zinc-500 border-zinc-700 hover:text-zinc-300`}
          title="Replay settled bets through wagering (use if you placed the qualifying bet before starting tracking)."
        >
          replay
        </button>
      </span>
    )
  }

  if (state.kind === 'unlock_ready') {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-emerald-400">✓ qualifying bet done</span>
        <button
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); run(() => api.bonusTransition(pid, 'trigger_settled')) }}
          className={`${btn} bg-emerald-700/50 text-emerald-100 border-emerald-500/60 hover:bg-emerald-600/60 font-bold`}
          title={`Unlock the ${state.amount.toFixed(0)} freebet for ${pidLabel}.`}
        >
          unlock freebet
        </button>
      </span>
    )
  }

  // state.kind === 'freebet_ready'
  // TODO(freebet-accounting): the placed freebet records as a normal stake=amount
  // bet, but a freebet's stake is not at risk. Ensure the recorded bet is flagged
  // is_bonus=true in the mirror recording path so stats/ROI don't over-count it.
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-yellow-300">🎁 {state.amount.toFixed(0)} freebet ready — place it, then:</span>
      <button
        disabled={busy}
        onClick={(e) => { e.stopPropagation(); run(() => api.bonusTransition(pid, 'freebet_used')) }}
        className={`${btn} bg-yellow-800/40 text-yellow-200 border-yellow-600/50 hover:bg-yellow-700/50`}
        title={`Mark ${pidLabel}'s freebet as used (after placing it in the browser).`}
      >
        mark freebet used
      </button>
    </span>
  )
}
```

- [ ] **Step 2: Verify type-check passes**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npx tsc --noEmit
```
Expected: no errors (exit 0) — the Task 3 imports are now consumed.

- [ ] **Step 3: Verify resolver tests still pass + lint clean**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npx vitest run src/pages/bonusChipState.test.ts && npm run lint
```
Expected: tests PASS; lint exits 0 (the PostToolUse eslint hook may have already auto-fixed formatting).

- [ ] **Step 4: Commit**

```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle"
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(freebet): BonusChip component (six lifecycle states)"
```

---

## Task 5: Wire BonusChip into both render sites

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx` — site 1 (~line 3225-3248, soft-cluster deposit hint) and site 2 (~line 3401-3432, funded-cluster anchor)

> Line numbers drift as earlier tasks edit the file. Locate each site by its anchor text below, not by absolute line.

- [ ] **Step 1: Replace site 1 (the `members.map` "mark claimed" button)**

Find the block inside `members.map(pid => { ... })` that computes `onlyBonus` and renders the mark-claimed button (anchor: the comment `// POSTs /bankroll/claim-bonus/{pid} → bonus_status`). Replace the whole returned `<div>` body's bonus section. Specifically, replace:

```tsx
                              const bal = getBalance(providerBalances[pid])
                              const trig = getTrigger(providerBalances[pid])
                              const pending = pendingByProvider[pid]?.length ?? 0
                              const onlyBonus =
                                (trig?.amount ?? 0) > 0 && bal < DRAIN_THRESHOLD_SEK && pending === 0
                              return (
                                <div key={pid} className="flex items-center gap-1.5">
                                  <span className="text-zinc-400 uppercase text-[10px] tracking-wider">{pid}</span>
                                  <BalanceCell pid={pid} balances={providerBalances} onSaved={load} />
                                  {onlyBonus && (
                                    <button
                                      onClick={async (e) => {
                                        e.stopPropagation()
                                        try {
                                          const r = await fetch(`/api/bankroll/claim-bonus/${pid}`, { method: 'POST' })
                                          if (!r.ok) throw new Error(`status ${r.status}`)
                                          load()  // immediate refresh; the 5-s poll would
                                                  // catch it eventually but the user clicked
                                                  // so feedback should be snappy.
                                        } catch (err) {
                                          console.warn(`[claim-bonus] ${pid} failed`, err)
                                        }
                                      }}
                                      className="px-1.5 py-0.5 text-[9px] uppercase tracking-wider rounded bg-zinc-800 text-zinc-400 border border-zinc-700 hover:bg-zinc-700 hover:text-zinc-200 cursor-pointer"
                                      title={`Mark ${pid.toUpperCase()}'s bonus as claimed — hides this row. Reversible from Bankroll tab.`}
                                    >
                                      mark claimed
                                    </button>
                                  )}
                                </div>
                              )
```

with:

```tsx
                              const bal = getBalance(providerBalances[pid])
                              const trig = getTrigger(providerBalances[pid])
                              const pending = pendingByProvider[pid]?.length ?? 0
                              return (
                                <div key={pid} className="flex items-center gap-1.5">
                                  <span className="text-zinc-400 uppercase text-[10px] tracking-wider">{pid}</span>
                                  <BalanceCell pid={pid} balances={providerBalances} onSaved={load} />
                                  <BonusChip
                                    pid={pid}
                                    balanceNative={getBalanceNative(providerBalances[pid])}
                                    isDrained={bal < DRAIN_THRESHOLD_SEK}
                                    pendingCount={pending}
                                    progress={bonusProgress[pid] ?? null}
                                    config={bonusConfigs[pid] ?? null}
                                    currency={trig?.currency ?? 'SEK'}
                                    onChanged={load}
                                  />
                                </div>
                              )
```

- [ ] **Step 2: Replace site 2 (the funded-anchor IIFE "mark claimed" button)**

Find the IIFE inside the funded-cluster anchor render (anchor: comment `Surface a "mark claimed" affordance — POST flips` and the `const onlyBonus =` inside `(() => { ... })()`). Replace:

```tsx
                                {(() => {
                                  const trig = getTrigger(providerBalances[pid])
                                  const onlyBonus =
                                    (trig?.amount ?? 0) > 0 && bal < DRAIN_THRESHOLD_SEK && pending === 0
                                  if (!onlyBonus) return null
                                  return (
                                    <button
                                      onClick={async (e) => {
                                        e.stopPropagation()
                                        try {
                                          const r = await fetch(`/api/bankroll/claim-bonus/${pid}`, { method: 'POST' })
                                          if (!r.ok) throw new Error(`status ${r.status}`)
                                          load()
                                        } catch (err) {
                                          console.warn(`[claim-bonus] ${pid} failed`, err)
                                        }
                                      }}
                                      className="px-1.5 py-0.5 text-[9px] uppercase tracking-wider rounded bg-zinc-800 text-zinc-400 border border-zinc-700 hover:bg-zinc-700 hover:text-zinc-200 cursor-pointer"
                                      title={`Mark ${pid.toUpperCase()}'s bonus as claimed — hides this row. Reversible from Bankroll tab.`}
                                    >
                                      mark claimed
                                    </button>
                                  )
                                })()}
```

with:

```tsx
                                <BonusChip
                                  pid={pid}
                                  balanceNative={getBalanceNative(providerBalances[pid])}
                                  isDrained={bal < DRAIN_THRESHOLD_SEK}
                                  pendingCount={pending}
                                  progress={bonusProgress[pid] ?? null}
                                  config={bonusConfigs[pid] ?? null}
                                  currency={getTrigger(providerBalances[pid])?.currency ?? 'SEK'}
                                  onChanged={load}
                                />
```

- [ ] **Step 3: Verify type-check + lint + tests**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npx tsc --noEmit && npm run lint && npx vitest run src/pages/bonusChipState.test.ts
```
Expected: tsc exit 0, lint exit 0, tests PASS. (If tsc reports `bal`/`trig` unused at a site, ensure they're still referenced — `bal` is used in `isDrained`; at site 1 `trig` is used for `currency`. At site 2 `trig` was removed; confirm no dangling reference.)

- [ ] **Step 4: Commit**

```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle"
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(freebet): render BonusChip at both cluster sites (kills mark-claimed dup)"
```

---

## Task 6: Full build + manual verification

**Files:** none (verification only).

- [ ] **Step 1: Full production build (catches anything tsc --noEmit misses)**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle/frontend" && npm run build
```
Expected: `tsc -b` + `vite build` succeed, exit 0, `dist/` produced.

- [ ] **Step 2: Confirm the diff is frontend/docs only (no backend redeploy)**

Run:
```bash
cd "C:/Users/rasmu/betty/.claude/worktrees/unibet-freebet-lifecycle" && git diff --name-only origin/main...HEAD | grep -vE '^(frontend|docs)/' || echo "FRONTEND/DOCS ONLY ✓"
```
Expected: prints `FRONTEND/DOCS ONLY ✓` (no backend paths).

- [ ] **Step 3: Manual smoke (via `local\betty.bat`, requires a Unibet profile)**

This is interactive — perform with the user, do not automate:
1. Start Betty (`local\betty.bat`), open Sports tab.
2. Fresh Unibet (balance 0): confirm the chip shows `deposit N sek` + `start tracking` + `mark claimed`.
3. Deposit 1000 on Unibet → balance syncs → chip shows `✓ deposit detected` + `start freebet tracking`.
4. Click `start freebet tracking` → chip switches to `qualifying bet: 0/1000 @ ≥X`.
5. Place a qualifying bet (≥ min-odds) in the browser → after it records, chip shows `✓ qualifying bet done` + `unlock freebet`.
6. Click `unlock freebet` → chip shows `🎁 1000 freebet ready` + `mark freebet used`.
7. Place the freebet, click `mark freebet used` → chip disappears (status completed).
8. Confirm the chip renders identically whether the provider is in a bonus-only deposit cluster (site 1) or a funded anchor cluster (site 2).

- [ ] **Step 4: Final state — branch ready for PR**

No commit needed (Task 6 is verification). The branch `worktree-unibet-freebet-lifecycle` is ready to finish via PR (use superpowers:finishing-a-development-branch).

---

## Self-Review Notes

- **Spec coverage:** deposit-detect→start_freebet (Task 4 deposit_hint/deposit_detected), wagering display + replay safeguard (Task 4 wagering), unlock→trigger_settled (unlock_ready), freebet_used (freebet_ready), shared component killing the two-site duplication (Task 5), `/bankroll/status` + `/bankroll/bonuses` plumbing (Tasks 2-3), pure-frontend/no-redeploy (Task 6 Step 2). Out-of-scope freebet-accounting flagged as a `// TODO` in Task 4 Step 1. ✓
- **Correction from spec:** API shims go in `hooks/useApi.ts` (PlayPage's actual API source), not `services/api/bankroll.ts`. Noted in File Structure.
- **Type consistency:** `resolveBonusChipState`, `BonusChipInput`, `BonusChipProgress`, `ProviderBonusConfig`, `BonusChipState` defined in Task 1 and consumed unchanged in Tasks 3-4. `api.bonusTransition` signature (Task 2) matches calls in Task 4. `getBalanceNative` defined in Task 3, used in Task 5.
- **Currency:** detection compares `balanceNative` vs native-currency `amount`; all configured freebets are SEK softbooks (rate 1, so native == sek), so the comparison is exact for the real providers.
