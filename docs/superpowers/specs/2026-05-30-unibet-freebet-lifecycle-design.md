# Unibet Freebet Lifecycle UI (Sports tab, inline)

**Date:** 2026-05-30
**Status:** Design — approved verbally, pending written-spec review
**Scope:** `frontend/` only. No backend code changes → no server redeploy.
**Branch:** `worktree-unibet-freebet-lifecycle` (off `origin/main` @ 08b84943)

## Problem

A fresh (zero-balance) Unibet account offers a 1000 SEK freebet: deposit 1000,
place a qualifying bet (≥ min-odds), and the 1000 freebet unlocks. The backend
already models this entire lifecycle, but **the Sports-tab UI exposes none of
it** — the only bonus action wired up is `claim-bonus` ("I took this on another
account, hide the row"). There is no way, from the UI, to:

1. Tell Betty a deposit happened and start freebet tracking.
2. See qualifying-bet wagering progress.
3. Unlock the freebet once the qualifying bet is placed.
4. Mark the freebet as used.

So the user must drive the whole thing with manual `curl`/Postman calls or track
it in their head.

## What already exists (reused as-is, no changes)

**Backend state machine** — fully built and correct:

- Config: `backend/src/config/providers.yaml` → `unibet.bonus = {type: freebet, amount: 1000, trigger_mode: single}`.
- States: `available → trigger_needed → freebet_available → completed` (`profile_repo.advance_freebet_status`, `profile_repo.py:610`).
- `record_wagering` (`profile_repo.py:355`) auto-accumulates `wagered_amount` on every recorded bet for a `trigger_needed`/`in_progress` row. For `freebet` type it deliberately does **not** auto-advance on completion (`profile_repo.py:401-403`) — an explicit `trigger_settled` action is required. This is the "user confirms" gate we want.
- `record_wagering` is already invoked when a bet is recorded (`bet_service.py:379`).

**Backend endpoints** — all the transitions already have routes:

- `POST /api/bankroll/bonus-transition/{pid}` with `action ∈ {start_freebet, trigger_settled, freebet_used}` (`bankroll.py:310`). `start_freebet` creates the `trigger_needed` row from yaml config and **does not touch the balance** (correct — the deposit already synced via DOM scrape; `deposit_with_bonus` would double-count it).
- `GET /api/bankroll/status` → `bonus_progress: Record<pid, BonusProgressEntry>` with `status`, `bonus_type`, `bonus_amount`, `wagering_requirement`, `wagered_amount`, `min_odds`, `progress_pct` (`bankroll_service.get_status`, `bankroll.py` route). Already wrapped in the frontend as `bankrollApi.getBankrollStatus()`.
- `GET /api/bankroll/bonuses` → static yaml configs `{pid: {type, amount, min_odds, ...}}` (`bankroll.py:50`). Needs a thin frontend wrapper (not yet present).
- `POST /api/bankroll/backfill-wagering` → replays settled bets through `record_wagering` for active bonuses (`bankroll.py:413`). Used as the safeguard below.
- `POST /api/bankroll/claim-bonus/{pid}` → existing "dismiss" action, kept unchanged.

**Frontend types** — `BonusProgressEntry` already defined (`types/index.ts:160`).

## Key gap discovered during design

PlayPage's 10s poll (`load()`, `PlayPage.tsx:1117`) fetches `/bankroll`
(`getBankrollSummary`), which returns only `bonus_trigger_amount` /
`bonus_currency` / `bonus_trigger_odds` — **not** `bonus_status` or wagering
progress. And once balance ≥ the freebet amount, `bonus_trigger_amount` goes
`null` (the provider stops being "trigger-actionable"), so the trigger info
vanishes exactly when we need it post-deposit.

**Resolution (still pure-frontend):**
- Add `getBankrollStatus()` to the `load()` poll → new `bonusProgress` state. Gives live status + wagering for any provider with a bonus row.
- Add a one-time-on-mount `getProviderBonuses()` fetch → `bonusConfigs` state. Gives the static freebet amount/type/min-odds for providers **without** a row yet (fresh accounts), independent of balance.

## Approach

State-driven inline chip in the Sports tab, reading three sources:

| Source | When fetched | Provides |
|---|---|---|
| `/bankroll` (existing) | every poll | balance, `bonus_trigger` (pre-deposit) |
| `/bankroll/status` (add to poll) | every poll | `bonus_progress[pid]`: live status + wagering |
| `/bankroll/bonuses` (add, once) | on mount | `bonusConfigs[pid]`: static freebet amount/type/min-odds |

"Auto-advance where signals exist" is realized as **stateless per-poll
detection that auto-surfaces the next confirm button** — no fragile
balance-diffing across polls:

| Resolved state | Chip renders | Action (button) |
|---|---|---|
| freebet config exists, status `available`/absent, balance **<** amount | existing "deposit N sek" hint + `start tracking` + `mark claimed` | `start tracking` → `bonus-transition: start_freebet` |
| freebet config exists, status `available`/absent, balance **≥** amount | "✓ deposit detected — start freebet tracking" + `mark claimed` | `start freebet tracking` → `bonus-transition: start_freebet` |
| status `trigger_needed`, `wagered < requirement` | "qualifying bet: {wagered}/{requirement} @ ≥{min_odds}" progress | none (place the qualifying bet in browser; existing `record_wagering` counts it) |
| status `trigger_needed`, `wagered ≥ requirement` | "✓ qualifying bet done — unlock freebet" | `unlock freebet` → `bonus-transition: trigger_settled` |
| status `freebet_available` | "🎁 {amount} freebet ready — place it, then:" | `mark freebet used` → `bonus-transition: freebet_used` |
| status `completed`/`claimed` | nothing (row drops out — existing behavior) | — |

The final `freebet_used` step is a one-click confirm (not auto-on-placement),
which is what keeps this pure-frontend. Auto-on-placement would require a
`bet_service.py` branch on `bonus_status` (backend redeploy) — explicitly
deferred as a possible follow-up.

## Components

- **`<BonusChip pid balance bonusProgress bonusConfig pending onChanged />`** — new shared component encapsulating all of the above (state resolution + the existing "mark claimed" button + new transition buttons). Defined **inside `frontend/src/pages/PlayPage.tsx`** (as a component above the default export), so it reuses the existing module-level helpers (`getBalance`, `getTrigger`, `DRAIN_THRESHOLD_SEK`) without a cross-file refactor. (PlayPage is already large; if the chip grows past ~120 lines, extract it to `frontend/src/components/BonusChip.tsx` and lift the shared helpers — but start in-file.)
  - **Why shared:** PlayPage has **two** render sites for the bonus chip today — the soft-cluster deposit-hint (`PlayPage.tsx:3229`) and the funded-cluster anchor (`PlayPage.tsx:3409`) — with duplicated "mark claimed" logic. CLAUDE.md explicitly flags "two divergent renders" as a recurring bug source here. Extracting one component and using it at both sites removes the duplication rather than tripling it.
- **`bankrollApi.bonusTransition(pid, action)`** — new wrapper for `POST /bankroll/bonus-transition/{pid}` (`services/api/bankroll.ts`).
- **`bankrollApi.getProviderBonuses()`** — new wrapper for `GET /bankroll/bonuses` (`services/api/bankroll.ts`).
- **PlayPage `load()`** — add `getBankrollStatus()` to the existing `Promise.all`; store `bonus_progress` in new `bonusProgress` state. Add a mount-effect `getProviderBonuses()` → `bonusConfigs` state.

## Data flow (happy path)

1. User deposits 1000 on the Unibet site → mirror balance sync sets balance to 1000.
2. Next poll: balance ≥ 1000, status `available`/absent → chip shows "deposit detected — start freebet tracking".
3. User clicks → `start_freebet` → row becomes `trigger_needed` (req=1000, min_odds from config, mode `single`, **no balance change**).
4. User places the qualifying bet in the browser (an arb BET leg) → intercepted → `/api/bets` → `record_wagering` increments `wagered_amount`.
5. Poll reflects `wagered ≥ requirement` → chip shows "unlock freebet".
6. User clicks → `trigger_settled` → status `freebet_available`.
7. Chip shows "🎁 1000 freebet ready". User places the freebet in the browser, then clicks "mark freebet used" → `freebet_used` → `completed` → row drops out.

## Edge cases & safeguards

- **Qualifying bet placed before tracking started.** `record_wagering` no-ops without a `trigger_needed` row, so `wagered_amount` would stay 0. Mitigation: when status is `trigger_needed` with `wagered_amount == 0` but the provider has settled bets, the chip shows a small `replay wagering` link → `POST /bankroll/backfill-wagering`. The ordering nudge (start tracking appears as soon as the deposit is detected, before any bet) makes this rare.
- **Qualifying bet below `min_odds`.** `record_wagering` skips it; progress won't move. The chip shows `@ ≥{min_odds}` so the user knows the bar. (No hard block — informational only.)
- **Optimistic refresh.** Every transition button awaits its POST then calls `load()` immediately (same pattern as the existing "mark claimed" button), so feedback is snappy rather than waiting for the next poll tick.
- **Failure handling.** Transition POST failures are caught and `console.warn`ed (matching the existing claim-bonus button); the row stays in its prior state and the next poll re-renders truth.
- **Generality.** Logic keys off `bonus_type === 'freebet'` and live status, not a hardcoded provider — any freebet-configured Kambi book (not just Unibet) gets the same chip. `bonusdeposit`-type bonuses are out of scope for this chip and fall through to the existing rendering.

## Out of scope (flagged, not built)

- **Freebet stake accounting.** A placed freebet records as a normal `stake=1000` bet, but a freebet's stake is not at risk (only winnings pay out). `bets.is_bonus` already exists and `get_stats` handles bonus bets correctly (`bankroll_service.py:109-123`), but ensuring the *recorded* freebet bet is flagged `is_bonus=true` is a separate concern in the mirror recording path. Leave a `// TODO` near the `freebet_used` action noting this; do not expand scope here.
- **Auto-advance `freebet_available → completed` on bet interception.** Needs a `bet_service.py` branch on `bonus_status` (backend redeploy). Deferred.
- **Bankroll-tab overview panel.** The Bankroll tab already surfaces `get_status` bonus progress separately; no changes there.

## Testing

- **Type/lint:** `cd frontend && npm run lint` and `npx tsc --noEmit` clean (PostToolUse eslint hook also runs on save).
- **Manual (via `local\betty.bat`):** drive a fresh Unibet profile through deposit → start tracking → qualifying bet → unlock → freebet used, confirming each state renders and each button advances. Verify both render sites (a bonus-only soft cluster and a funded anchor cluster) show the chip identically.
- **No backend tests** — no backend changes.

## Deployment

Pure local-client/frontend change (`frontend/` only). Ships via `local\betty.bat`
(Vite + local FastAPI). **No `server-deploy.sh` rebuild.** Confirm with
`git diff --name-only origin/main...HEAD` touching only `frontend/` + `docs/`.
