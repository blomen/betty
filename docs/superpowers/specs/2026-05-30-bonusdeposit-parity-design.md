# Bonusdeposit Lifecycle Parity (works + tracks)

**Date:** 2026-05-30
**Status:** Design — approved (scope=both, balance=state-machine-adjusts, one PR backend-first)
**Branch:** `worktree-bonusdeposit-parity` (off `origin/main`, which has the freebet `BonusChip` + `create_bet` derivation)
**Scope:** Two phases in one PR — Phase 1 backend (→ redeploy), Phase 2 frontend (no redeploy).

## Problem

The freebet lifecycle is now surfaced + tracked in the Sports tab. The other
bonus type — `bonusdeposit` (the majority: ~18 providers) — is **not at parity**:

1. **Sports-tab `BonusChip` ignores it.** `resolveBonusChipState` returns `none`
   for anything non-freebet, so a bonusdeposit provider shows only the generic
   "deposit X" hint + "mark claimed" — no lifecycle (no start, no wagering
   progress, no bonus-unlocked, no completion). Progress is visible only in the
   Bankroll tab (`get_status.bonus_progress`).
2. **Backend tracking is unverified and has at least one config bug.** The
   state machine exists but has no tests, and the wager-first providers are
   misconfigured.

## Bonusdeposit state machine (already exists, kept; balance-adjusting per decision)

`deposit_with_bonus(provider_id, amount)` (`bankroll_service.py:417`) arms one of
two shapes based on config:

- **Two-phase** (`trigger_odds` set): `start_bonus_trigger` → `trigger_needed`,
  adds *deposit* to balance (bonus locked). `record_wagering` accumulates on each
  settled bet; when `wagered ≥ deposit×trigger_multiplier` at `trigger_odds`, it
  credits the bonus (`adjust_balance += bonus`) and either completes (if main
  `wager_req ≤ 0`) or moves to `in_progress` with `wager_req = bonus×wagering_multiplier`.
- **Single-phase** (no `trigger_odds`): `start_bonus_wagering` → `in_progress`,
  adds *deposit + bonus* immediately, main `wager_req = bonus×wagering_multiplier`.

`in_progress` → `completed` when `wagered ≥ wager_req`. `bonus-transition` provides
manual overrides. **Balance adjustments stay** (user's choice: the state machine
owns balance for these books; they are not DOM-balance-synced).

## Config audit (all ~18 bonusdeposit providers; none set `wagering_multiplier`)

Three real shapes, all currently relying on the `wagering_multiplier` default `10.0`:

| Shape | Providers | Intended | Current (default mult=10) |
|---|---|---|---|
| **Wager-first** (trigger only, "bonus as cash") | leovegas ×6, expekt ×20, betmgm ×10 (all @1.80) | trigger completion → **done** | **BUG**: spurious `bonus×10` main phase |
| **Trigger-then-main** | betinia/campobet/swiper/quickcasino ×1 @1.50 | trigger unlocks bonus → main wagering → done | main `wager_req = bonus×10` (placeholder; may not match T&C) |
| **Immediate** | speedybet, x3000, goldenbull, 1x2, lodur, 888sport, spelklubben, bethard, 10bet, snabbare, comeon, + others | bonus at deposit → single wagering → done | `wager_req = bonus×10` (placeholder) |

## Phase 1 — Backend: verify + harden *(backend → redeploy)*

**Goal:** make the *code* provably correct for each config **shape**, and fix the
one clear config bug. **Do NOT** invent real per-bookmaker wagering numbers for
placeholder configs — that needs the user's T&C knowledge; flag them instead.

1. **Fix wager-first:** add `wagering_multiplier: 0` to `leovegas`, `expekt`,
   `betmgm` in `providers.yaml`, so `record_wagering` completes at trigger
   (`wager_req = bonus×0 = 0 → completed`, bonus credited as cash). Verify
   `record_wagering`'s `wager_req <= 0 → completed` branch does exactly this.
2. **Pytest matrix** (`backend/tests/test_bet_service_bonusdeposit.py` +/or
   `test_profile_repo_bonusdeposit.py`, fixture pattern from
   `test_bankroll_service_trigger.py` / `test_ban_system.py`) covering, per shape,
   the full path `deposit_with_bonus → record_wagering(settled bets) → transitions
   → balance crediting → completed`:
   - **Two-phase trigger-then-main:** deposit D; after `wagered ≥ D×trig_mult` →
     bonus credited (`balance += bonus`), `in_progress`, `wager_req = bonus×mult`;
     after main met → `completed`.
   - **Wager-first (mult=0):** deposit D; after trigger met → bonus credited,
     `completed` immediately (no main phase). *(Guards the bug fix.)*
   - **Single-phase immediate:** deposit D; balance += D + bonus at deposit;
     `in_progress`; after `wager_req` met → `completed`.
   - **Balance assertions** at each transition (deposit added once, bonus credited
     once, no double-credit on repeated `record_wagering`).
   - **`min_odds` gate:** a bet below `trigger_odds`/`min_odds` does not count.
3. **`is_bonus` non-misfire test:** a bet placed while a bonusdeposit row is
   `in_progress` is recorded `is_bonus=False` (the freebet derivation keys only on
   `freebet_available`). Real-money main-phase bets must stay real.
4. **Fix any state-machine bugs surfaced** by the matrix (e.g., double-credit,
   wrong `min_odds` after phase switch, `trigger_mode` edge with bonusdeposit).
5. **Flag incomplete configs:** for trigger-then-main + immediate providers whose
   `wagering_multiplier` is a placeholder default, leave a `# TODO(bonus-terms)`
   in `providers.yaml` noting the real T&C multiplier must be confirmed by the
   user. Do not guess numbers.

## Phase 2 — Frontend: extend `BonusChip` *(frontend → no redeploy)*

Extend `resolveBonusChipState` (`frontend/src/pages/bonusChipState.ts`) + the
`BonusChip` component (`PlayPage.tsx`) for bonusdeposit. New states:

| Resolved state | Condition | Chip renders | Action |
|---|---|---|---|
| `bd_deposit` | `bonusdeposit` config, status `available`/absent, `isDrained && pendingCount===0` | "matched bonus up to {amount}" + **deposit-amount input** + "deposit & start" + "mark claimed" | `deposit_with_bonus(pid, amount)` (existing `depositWithBonus` mutation) |
| `bd_trigger` | status `trigger_needed`, type `bonusdeposit` | "trigger: {wagered}/{req} @ ≥{minOdds}" + replay | — (place bets; `record_wagering` advances) |
| `bd_wagering` | status `in_progress` | "🔓 bonus unlocked — wager: {wagered}/{req} @ ≥{minOdds}" + replay | — |
| `completed`/`claimed` | — | nothing | — |

Details:
- **Deposit-amount input:** small inline number field; defaults to the bonus cap
  (`config.amount`), editable (deposits can be < cap). Disabled while `busy`;
  fires once (after start, status leaves `available` so the input isn't re-shown —
  natural idempotency).
- **`bonusType` routing:** the resolver branches on `progress.bonus_type ??
  config.type`. Freebet states unchanged. `in_progress` (currently → `none`) now
  routes to `bd_wagering`.
- **Wiring:** add `api.depositWithBonus(pid, amount)` shim to `hooks/useApi.ts`
  (the `services/` layer has it; PlayPage's `api` object does not).
- **Currency:** display uses `bonus_currency`/`triggerCurrency`; all bonusdeposit
  providers are SEK (consistent with the freebet finding).

## Caveats (explicit)

- **Balance double-count:** `deposit & start` adds the deposit to the tracked
  balance. This assumes the user is NOT separately DOM-syncing/manual-setting
  balance for that provider (their stated model). The action is explicit
  (enter amount → click) and disabled while in flight to avoid accidental
  double-adds.
- **Placeholder wagering terms:** the chip displays whatever the config encodes.
  Where `wagering_multiplier` is a placeholder, the displayed requirement is only
  as correct as the config (Phase 1 flags these).

## Out of scope (flag, not build)

- `bonus_profit` / Rule-B ROI separation (`[[project_profile_account_model]]`) —
  a distinct accounting effort.
- Authoring real per-bookmaker T&C wagering multipliers (needs user input).
- Auto-detecting the deposit from a balance jump for bonusdeposit (the deposit is
  variable + state-machine-owned, so it's an explicit amount entry, not detection).

## Branch / PR

One branch `worktree-bonusdeposit-parity`, backend-first (TDD) then frontend, one
PR, one deploy (matches the freebet precedent; single extraction interruption).

## Testing

- **Backend:** `cd backend && pytest tests/test_bet_service_bonusdeposit.py
  tests/test_bankroll_service_trigger.py tests/test_ban_system.py -q` green.
- **Frontend:** extend `bonusChipState.test.ts` with bonusdeposit cases;
  `npx tsc --noEmit`, `npm test`, `npm run build` green. (No `npm run lint` — it
  doesn't exist; see `[[project_frontend_verification_gates]]`.)
- **Manual:** drive a bonusdeposit provider (e.g. betinia) through deposit & start
  → trigger wagering → bonus unlocked → main wagering → completed via `betty.bat`.

## Deployment

Backend change (config + any code fixes) → `server-deploy.sh rebuild backend`
via `/deploy`, with HEAD/boot_id/code-in-container verification. Frontend ships
in the same image (rebuild covers it).
