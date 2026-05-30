# Steam Alert UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a new, actionable steam-flagged value bet appears, alert the user in-app — one-time sound + pin-to-top + strong highlight — so they can place it within the stale-line window.

**Architecture:** Pure-client, on the existing `/api/opportunities/play/batch` poll. A new `useSteamAlert` hook (with a pure `selectNewSteamAlerts` core) plays a synthesized WebAudio beep once per newly-seen actionable steam bet and returns the set of actionable-steam keys; PlayPage pins those rows to the top and highlights them. No backend code.

**Tech Stack:** React 19 / TypeScript / Vite; vitest (if installed) for the pure helper. Frontend-only — ships via `betty.bat`.

**Spec:** `docs/superpowers/specs/2026-05-30-steam-alert-ux-design.md`

**Scope:** v1 alert UX only. No auto/semi-auto placement, no dedicated fast loop, no desktop notification — all deferred. Server prerequisite `STEAM_DETECTOR_ENABLED=1` is user-controlled env (not in this plan).

**Deploy note:** Frontend-only. No backend rebuild, no migration, no betting-path change. Ready on next `betty.bat`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `frontend/src/hooks/useSteamAlert.ts` | Pure `selectNewSteamAlerts` + `currentActionableSteamKeys` + `steamKey` helpers; `useSteamAlert` hook (WebAudio beep + active-key set) | Create |
| `frontend/src/hooks/useSteamAlert.test.ts` | vitest unit tests for the pure helpers | Create |
| `frontend/src/pages/PlayPage.tsx` | Derive funded set; call hook; pin actionable-steam rows to top + highlight | Modify |

---

## Task 1: `useSteamAlert` hook + pure helpers

**Files:**
- Create: `frontend/src/hooks/useSteamAlert.ts`
- Create: `frontend/src/hooks/useSteamAlert.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/useSteamAlert.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { currentActionableSteamKeys, selectNewSteamAlerts, steamKey } from './useSteamAlert';

type Bet = Parameters<typeof steamKey>[0];

const bet = (over: Partial<Bet> = {}): Bet => ({
  event_id: 'e1', market: 'moneyline', outcome: 'home', provider: 'polymarket',
  point: null, edge_pct: 6, annotations: { steam_signal: { direction: 'up', provider_count: 3 } },
  ...over,
}) as Bet;

const FUNDED = new Set(['polymarket', 'kalshi']);
const FLOOR = 3;

describe('selectNewSteamAlerts', () => {
  it('returns an unseen actionable steam bet', () => {
    const keys = selectNewSteamAlerts([bet()], new Set(), FUNDED, FLOOR);
    expect(keys).toEqual([steamKey(bet())]);
  });

  it('excludes bets with no steam_signal direction', () => {
    const b = bet({ annotations: { steam_signal: null } });
    expect(selectNewSteamAlerts([b], new Set(), FUNDED, FLOOR)).toEqual([]);
  });

  it('excludes unfunded providers', () => {
    const b = bet({ provider: 'betsson' });
    expect(selectNewSteamAlerts([b], new Set(), FUNDED, FLOOR)).toEqual([]);
  });

  it('excludes bets below the edge floor', () => {
    const b = bet({ edge_pct: 1 });
    expect(selectNewSteamAlerts([b], new Set(), FUNDED, FLOOR)).toEqual([]);
  });

  it('excludes already-seen keys', () => {
    const b = bet();
    expect(selectNewSteamAlerts([b], new Set([steamKey(b)]), FUNDED, FLOOR)).toEqual([]);
  });

  it('dedupes within a batch', () => {
    const keys = selectNewSteamAlerts([bet(), bet()], new Set(), FUNDED, FLOOR);
    expect(keys).toEqual([steamKey(bet())]);
  });
});

describe('currentActionableSteamKeys', () => {
  it('returns all actionable steam keys regardless of seen', () => {
    const set = currentActionableSteamKeys([bet(), bet({ outcome: 'away' })], FUNDED, FLOOR);
    expect(set.has(steamKey(bet()))).toBe(true);
    expect(set.has(steamKey(bet({ outcome: 'away' })))).toBe(true);
    expect(set.has(steamKey(bet({ provider: 'betsson' })))).toBe(false);
  });
});

describe('steamKey', () => {
  it('is stable and includes provider + outcome + point', () => {
    expect(steamKey(bet({ point: -1.5 }))).toBe('e1|moneyline|home|polymarket|-1.5');
    expect(steamKey(bet({ point: null }))).toBe('e1|moneyline|home|polymarket|');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useSteamAlert.test.ts`
Expected: FAIL — module `./useSteamAlert` does not exist yet.
NOTE: if vitest is NOT installed in this environment (`npx vitest` errors with "not found"), skip running it; the test file still documents the contract, and you will instead verify via `npx tsc --noEmit` in Step 4 and the local client. Do not block on vitest availability.

- [ ] **Step 3: Implement the hook + helpers**

Create `frontend/src/hooks/useSteamAlert.ts`:

```ts
import { useEffect, useRef, useState } from 'react';

/** Minimum edge (%) for a steam bet to be alert-worthy. */
export const STEAM_ALERT_MIN_EDGE_PCT = 3;

/** Shape of the bet fields this module reads (a subset of the play-batch row). */
export interface SteamAlertBet {
  event_id: string;
  market: string;
  outcome: string;
  provider: string;
  point?: number | null;
  edge_pct?: number | null;
  annotations?: {
    steam_signal?: { direction?: 'up' | 'down'; provider_count?: number } | null;
  } | null;
}

/** Stable identity for a bet row. */
export function steamKey(b: SteamAlertBet): string {
  return `${b.event_id}|${b.market}|${b.outcome}|${b.provider}|${b.point ?? ''}`;
}

function isActionableSteam(b: SteamAlertBet, funded: Set<string>, edgeFloor: number): boolean {
  const dir = b.annotations?.steam_signal?.direction;
  if (!dir) return false;
  if (!funded.has(b.provider)) return false;
  if ((b.edge_pct ?? 0) < edgeFloor) return false;
  return true;
}

/** All currently-actionable steam keys (for pin/highlight), regardless of seen. */
export function currentActionableSteamKeys(
  bets: SteamAlertBet[],
  funded: Set<string>,
  edgeFloor: number,
): Set<string> {
  const out = new Set<string>();
  for (const b of bets) {
    if (isActionableSteam(b, funded, edgeFloor)) out.add(steamKey(b));
  }
  return out;
}

/** Keys of actionable steam bets not yet in `seen` (deduped). Pure. */
export function selectNewSteamAlerts(
  bets: SteamAlertBet[],
  seen: Set<string>,
  funded: Set<string>,
  edgeFloor: number,
): string[] {
  const fresh: string[] = [];
  const local = new Set<string>();
  for (const b of bets) {
    if (!isActionableSteam(b, funded, edgeFloor)) continue;
    const k = steamKey(b);
    if (seen.has(k) || local.has(k)) continue;
    local.add(k);
    fresh.push(k);
  }
  return fresh;
}

/** Best-effort short alert tone via WebAudio (no asset). Silent on failure. */
function playBeep(): void {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.36);
    osc.onended = () => ctx.close().catch(() => {});
  } catch {
    /* audio blocked (no user gesture yet) — pin/highlight still convey the alert */
  }
}

/**
 * Fires a one-time beep when a NEW actionable steam bet appears in `bets`, and
 * returns the set of all currently-actionable steam keys (for pin + highlight).
 * Session-scoped: a bet is alerted once; reappearance does not re-alert.
 */
export function useSteamAlert(
  bets: SteamAlertBet[],
  funded: Set<string>,
  edgeFloor: number = STEAM_ALERT_MIN_EDGE_PCT,
): Set<string> {
  const seen = useRef<Set<string>>(new Set());
  const [activeKeys, setActiveKeys] = useState<Set<string>>(new Set());

  useEffect(() => {
    const fresh = selectNewSteamAlerts(bets, seen.current, funded, edgeFloor);
    setActiveKeys(currentActionableSteamKeys(bets, funded, edgeFloor));
    if (fresh.length > 0) {
      for (const k of fresh) seen.current.add(k);
      playBeep();
    }
    // funded is rebuilt each render; depend on a stable signature to avoid churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bets, edgeFloor, [...funded].sort().join(',')]);

  return activeKeys;
}
```

- [ ] **Step 4: Verify**

Run: `cd frontend && npx vitest run src/hooks/useSteamAlert.test.ts` → expect PASS (8 assertions).
If vitest is unavailable: run `cd frontend && npx tsc --noEmit` → no errors in `useSteamAlert.ts`. (The PostToolUse hook also runs eslint --fix.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useSteamAlert.ts frontend/src/hooks/useSteamAlert.test.ts
git commit -m "feat(steam): useSteamAlert hook + pure steam-alert selectors"
```

---

## Task 2: Wire the alert into PlayPage (pin-to-top + highlight)

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx`

Locate seams by content (the file is large): the per-provider value-bet array that is `.sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0))`-ed before mapping to rows; the balances map (`balancesByProvider` / the map keyed by provider id used to style funded rows); and the row render block that reads `b.annotations` and shows the `steam` pill (~line 2700-2735).

- [ ] **Step 1: Import the hook**

At the top of `frontend/src/pages/PlayPage.tsx` (with the other imports), add:

```tsx
import { steamKey, useSteamAlert } from '@/hooks/useSteamAlert';
```

- [ ] **Step 2: Derive the funded set + call the hook**

Inside the PlayPage component body, after the value-bet batch array and the balances map are available, add (use the real variable names found in the file — `allValueBets` is a placeholder for the combined value-bet array; `balancesByProvider` for the per-provider balance map):

```tsx
  const fundedProviders = useMemo(
    () => new Set(
      Object.entries(balancesByProvider)
        .filter(([, bal]) => (bal ?? 0) > 0)
        .map(([pid]) => pid),
    ),
    [balancesByProvider],
  );
  const steamActiveKeys = useSteamAlert(allValueBets, fundedProviders);
```

If `useMemo` is not already imported from React, add it to the existing `import { ... } from 'react'`. If the combined value-bet array is built per-provider rather than as one array, build a flat array for the hook: `const allValueBets = useMemo(() => Object.values(batchByProvider).flat(), [batchByProvider])` (use the real batch container name).

- [ ] **Step 3: Pin actionable-steam bets to the top**

In the existing sort of the per-provider value bets, make actionable-steam rows sort first, then fall back to edge. Replace the existing comparator `(a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0)` with:

```tsx
        (a, b) => {
          const as = steamActiveKeys.has(steamKey(a)) ? 1 : 0;
          const bs = steamActiveKeys.has(steamKey(b)) ? 1 : 0;
          if (as !== bs) return bs - as; // steam-actionable first
          return (b.edge_pct ?? 0) - (a.edge_pct ?? 0);
        }
```

(If there are multiple such sorts — e.g. per provider section — apply the same change to each value-bet sort. Do NOT touch the arb-table sort.)

- [ ] **Step 4: Highlight alerting rows**

In the value-bet row render (the element whose children include the `steam` pill block), add an accent class when the row is actionable-steam. Find the row's container element and add to its className:

```tsx
        className={`${/* existing row classes */ ''} ${
          steamActiveKeys.has(steamKey(b)) ? 'ring-2 ring-amber-400 bg-amber-400/10' : ''
        }`}
```

Use the project's existing accent tokens if `ring-amber-400`/`bg-amber-400/10` aren't in use elsewhere — match whatever strong-accent convention the file already uses (grep for `ring-` / `amber` / the steam pill's own color classes and reuse them). The row must be visibly distinct from normal rows.

- [ ] **Step 5: Verify build + visual**

Run: `cd frontend && npx tsc --noEmit` → no errors. `cd frontend && npm run lint` → clean (or only pre-existing warnings).
Then start the local client (`local\betty.bat`), open Sports → value bets. With `STEAM_DETECTOR_ENABLED=1` on the server and a live steam-flagged value bet on a funded provider, confirm: the row jumps to the top, shows the accent highlight, and a beep plays once. (If no live steam bet is available, temporarily lower `STEAM_ALERT_MIN_EDGE_PCT` or confirm the no-steam case renders normally with no errors.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(steam): pin + highlight + sound alert for actionable steam value bets"
```

---

## Self-Review

**Spec coverage:**
- Component 1 (`useSteamAlert` + `selectNewSteamAlerts` pure core + WebAudio beep) → Task 1 ✓
- Component 2 (PlayPage: funded set, hook call, pin-to-top, highlight) → Task 2 ✓
- Noise control (steam + funded + edge≥floor + once-per-seen) → `isActionableSteam` + `selectNewSteamAlerts` + `seen` ref ✓
- Channels: sound + pin-to-top, no desktop notification → playBeep + sort/highlight; no Notification API ✓
- Edge cases (no steam → no-op; unfunded/below-floor excluded; seen→no re-alert; reappear→no re-spam; audio blocked→best-effort) → covered in hook + tests ✓
- Prereq `STEAM_DETECTOR_ENABLED` → noted as out-of-plan server env ✓

**Placeholder scan:** No TBD/TODO. Task 2 uses content-anchored variable names (`balancesByProvider`, `allValueBets`, the edge-sort comparator) with explicit instructions to substitute the real names found in the file — these are location directions, not vague requirements. The vitest-availability fallback is explicit.

**Type consistency:** `steamKey`, `selectNewSteamAlerts`, `currentActionableSteamKeys`, `useSteamAlert`, `STEAM_ALERT_MIN_EDGE_PCT`, `SteamAlertBet` all defined in Task 1 and used identically in the Task 1 test and Task 2 wiring. Return types: `selectNewSteamAlerts → string[]`, `currentActionableSteamKeys`/`useSteamAlert → Set<string>` — consistent across tasks.

**Open item for the implementer (located, not placeholder):** confirm the exact PlayPage names for the value-bet batch container, the balances map, and each value-bet `.sort(...)` site; the change instructions are anchored to the quoted patterns.
