# Multi-Profile Sharp Accounts + Bonus-Profit Accounting — Implementation Plan

## Metadata
- **Spec:** [docs/spec/2026-05-30-multi-profile-sharp-accounts-bonus-roi.md](../spec/2026-05-30-multi-profile-sharp-accounts-bonus-roi.md)
- **Created:** 2026-05-30
- **Status:** Draft
- **Branch:** `design/multi-book-sharp-blend` (or a fresh worktree off it)

## Overview

We are introducing a first-class **Account** layer so the same real sharp
account (e.g. `polymarket/rasmus`) can be a single shared balance referenced by
many profiles, while soft bonus accounts stay per-campaign. Visibility is
explicit via a `profile_accounts` link table. Separately, we add
`profiles.kind ∈ {edge, bonus}` and rework Stats so that **all** bets under a
`bonus` profile (soft bonus leg + sharp hedge leg) are excluded from true ROI and
summed into a separate **bonus profit** number (Rule B).

Read the spec first. This plan captures task ordering, boundaries, and
verification — it does not restate the spec's data-model detail.

## Prerequisites

- Local backend dev environment (`cd backend && python run_dev.py`) and
  `pytest` working. **Do not deploy to the Hetzner server until the user asks.**
- This is backend (models/migration/repos/services/routes) + frontend
  (profile dialog, Bankroll, Stats). No extraction/scanner/mirror-nav changes.
- Work on a branch/worktree; the user's uncommitted `PlayPage.tsx` changes
  already in the tree are unrelated — do not revert them.

---

## Tasks

### Task 1: Data model — `accounts`, `profile_accounts`, new columns
**Goal:** Add the schema from spec §"Data Model" as SQLAlchemy models.

**Files:**
- `backend/src/db/models.py`

**Implementation:**
- Add `Account` model (`accounts` table) and `ProfileAccount` link model
  (`profile_accounts`) exactly per the spec tables, including
  `UNIQUE(provider_id, label)` and `UNIQUE(profile_id, account_id)`.
- Add `kind` column to `Profile` (default `"edge"`).
- Add `account_id` column to `Bet` (nullable FK → `accounts.id`, `ON DELETE SET
  NULL`, indexed). Keep existing `provider_id` on `Bet`.
- Add relationships mirroring existing style (e.g. `Profile.accounts`,
  `Account.profile_links`). Models file is ORM-only — no logic (CLAUDE.md rule).
- Follow the existing `_utcnow`, `__table_args__`/`Index` conventions in the file.

**Verify:**
- [ ] `python -c "from backend.src.db.models import Account, ProfileAccount"` imports clean.
- [ ] `ruff check` passes on the file.
- [ ] A throwaway `Base.metadata.create_all` against a temp sqlite/pg creates all tables with no FK errors.

---

### Task 2: Migration — backfill accounts from `ProfileProviderBalance`
**Goal:** Convert existing balances into `accounts` + `profile_accounts`, backfill
`bets.account_id`, default existing profiles to `kind='edge'`, then drop
`ProfileProviderBalance`. Follows spec §"Migration".

**Files:**
- `backend/src/db/models.py` (the in-`init_db` migration block where the other
  `ALTER TABLE ... ADD COLUMN` migrations live) **or** a new migration script
  consistent with how this project applies schema changes — match the existing
  pattern in the file (raw `cursor.execute` guarded by try/except on a probe
  SELECT).

**Implementation:**
- Idempotent + guarded like the existing migrations (probe for a column/table,
  skip if already migrated).
- Sharp providers (`pinnacle`, `polymarket`, `kalshi`, `cloudbet`): collapse to
  **one shared account per provider**, balance taken from the `edge`/active
  profile; link every profile that had a row to it. Default label `rasmus`.
- Other providers → `kind='soft'` account labeled from the owning profile name,
  single-linked.
- Carry `balance`, `currency` (use existing provider-currency resolution),
  `account_opened_at`.
- Backfill `bets.account_id` from `(profile_id, provider_id)` → linked account
  (shared account for sharp providers).
- Existing profiles → `kind='edge'`.
- Only after a verification query confirms balances reconcile, drop
  `ProfileProviderBalance`. If unsure, keep the table one release (spec allows)
  — but the default target is the clean drop. **Document which you did** in the
  migration comment.

**Verify:**
- [ ] On a copy of the real DB (or a seeded fixture), after migration: every
  pre-existing `ProfileProviderBalance` row maps to exactly one `(account,
  profile_accounts)` and per-profile SEK balance totals match pre-migration.
- [ ] No `bets` row that had a resolvable `(profile_id, provider_id)` is left
  with NULL `account_id`.
- [ ] Sharp providers have exactly one `accounts` row each (not one per profile).
- [ ] Re-running the migration is a no-op (idempotent).

---

### Task 3: Account repository
**Goal:** One access point for account CRUD + the queries the rest of the app needs.

**Files:**
- `backend/src/repositories/account_repo.py` (new)
- Wire into wherever repos are registered/instantiated (match `ProfileRepo` usage).

**Implementation:**
- Methods (names indicative): `get_or_create(provider_id, label, kind, currency)`,
  `link(profile_id, account_id)`, `unlink`, `accounts_for_profile(profile_id)`,
  `distinct_accounts()` (for grand totals), `set_balance(account_id, balance)`,
  `resolve(profile_id, provider_id)` → the linked account for that provider (the
  funded/selected one; if a profile links >1 account on a provider, prefer the
  active/selected — define a deterministic rule and document it).
- All DB access goes through this repo (CLAUDE.md: no raw `session.query` in
  routes/services).

**Verify:**
- [ ] Unit tests: shared sharp account resolves to the SAME account id from two
  different profiles; `distinct_accounts()` de-dupes; `resolve` returns the
  linked account.
- [ ] `pytest backend/tests/...account_repo...` green.

---

### Task 4: Balance set/sync writes to `accounts.balance`
**Goal:** `POST /api/bankroll/set/{provider_id}` and any balance-sync path update
the shared `accounts.balance`, not a per-profile copy.

**Files:**
- `backend/src/api/routes/bankroll.py`
- `backend/src/services/bankroll_service.py` (and `ProfileRepo.set_balance`
  callers — redirect to `account_repo`)
- Mirror balance push callers in `local/mirror/` only if they call the same
  service path (they POST to the API, so likely no change — confirm).

**Implementation:**
- Resolve active profile + `provider_id` → account via `account_repo.resolve`,
  write `accounts.balance`.
- `get_bankroll()` returns accounts **linked to the active profile**, each with
  label, native + SEK balance, currency, exchange rate (preserve current
  response shape; add `label` + `account_id`). Cross-currency conversion stays
  per CLAUDE.md rule.

**Verify:**
- [ ] Setting Polymarket balance under profile A, then reading bankroll under
  profile B (also linked to that shared account) shows the same updated number.
- [ ] A fresh-sharp account under profile C is NOT visible to A/B.
- [ ] Existing bankroll endpoint contract still satisfied (frontend types).

---

### Task 5: Bet recording stamps `account_id`
**Goal:** Every newly recorded bet carries `account_id`.

**Files:**
- `local/mirror/play_loop.py` (`_record_manual_bet`)
- `local/mirror/pending_loop.py` (`_record_unknown_open_bets`)
- `backend/src/repositories/bet_repo.py` / `BetCreate` schema (add `account_id`)
- Any other `bet_repo.create` callers.

**Implementation:**
- At record time, resolve `(active profile, provider)` → account via the repo and
  set `account_id`. Do not change dedup keys or the deferred-record rules
  (CLAUDE.md mirror invariants) — only add the field.

**Verify:**
- [ ] A bet placed via the normal flow lands with non-NULL `account_id` pointing
  at the correct (shared sharp or soft) account.
- [ ] Mirror dedup invariants unchanged (existing mirror tests still pass).

---

### Task 6: Stats — Rule B ROI + separate bonus profit
**Goal:** True ROI excludes all `bonus`-profile bets; add a `bonus_profit` total.

**Files:**
- `backend/src/services/bankroll_service.py` (`get_stats`, `_row_profit`)
- `backend/src/repositories/bet_repo.py` (`get_settled_aggregates` — carry the
  bet's profile `kind`)
- `frontend/src/pages/StatsPage.tsx` + `frontend/src/types` + stats API service

**Implementation:**
- Extend the settled-aggregate query to include the placing profile's `kind`.
- ROI aggregate = rows where `kind='edge'` AND `not is_bonus` (keeps existing
  `is_bonus` exclusion as a subset).
- `bonus_profit` (SEK) = summed profit of all rows where `kind='bonus'` (both
  legs) + any stray `is_bonus` rows under edge profiles. Convert to SEK before
  summing (CLAUDE.md cross-currency rule).
- Add `bonus_profit` to the stats response and render it on StatsPage as its own
  number, visually separate from ROI (do not fold into total_profit/ROI).
- Note: stats are currently for the **active** profile. Decide & document whether
  bonus profit shown is active-profile-only or all-profiles aggregate — default
  to **all-profiles bonus profit** so the user sees total harvested regardless of
  which profile is active. Confirm with existing StatsPage scoping.

**Verify:**
- [ ] Seed: one edge profile with a winning value bet, one bonus profile with a
  bonus leg + sharp hedge leg. Assert: ROI denominator excludes both bonus-profile
  legs; `bonus_profit` equals the locked gain; ROI matches the edge bet alone.
- [ ] Flipping the hedge leg result (won↔lost) does NOT change true ROI (proves
  Rule B), only redistributes within bonus_profit legs (net bonus_profit ~ stable).
- [ ] StatsPage renders bonus profit distinctly; ROI unchanged when bonus bets exist.

---

### Task 7: Profile-create service wiring
**Goal:** Honor the dialog choices: edge/bonus, use-shared-sharp vs fresh-sharp,
soft-account signup.

**Files:**
- `backend/src/services/profile_service.py`
- `backend/src/api/routes/profiles.py` (`ProfileCreate` schema + create route)
- `backend/src/repositories/account_repo.py`

**Implementation:**
- Extend `ProfileCreate`: `kind: 'edge'|'bonus'`, `use_shared_sharp: bool`,
  `fresh_sharp_label: str | None`.
- On create:
  - `use_shared_sharp=True` → link the existing shared sharp accounts (those
    linked to the edge profile) to the new profile.
  - else (fresh) → create new `kind='sharp'` accounts with `fresh_sharp_label`,
    link only to this profile.
  - `kind='bonus'` → also create one `kind='soft'` account per relevant soft
    provider, auto-labeled from the profile name, single-linked.
- Profile delete: remove links; GC accounts with zero links unless they have
  bets → set `is_active=False` instead (spec deletion semantics).

**Verify:**
- [ ] Create bonus profile with shared sharp → links point at existing shared
  accounts (no new sharp rows).
- [ ] Create with fresh sharp → new sharp account exists, linked only to it.
- [ ] Delete a bonus profile → soft accounts with no bets are gone; shared sharp
  survives; accounts with bets become `is_active=False`, not deleted.

---

### Task 8: Frontend — "open new profile" dialog
**Goal:** UI for purpose + sharp choice + label, per spec §"Creating a profile".

**Files:**
- `frontend/src/pages/PlayPage.tsx` (the profile-create panel visible top-right
  in the screenshot — "New profile name" / Create)
- `frontend/src/services/api/profiles.ts`, `frontend/src/types`

**Implementation:**
- Add Purpose radio (Edge / Bonus campaign), Sharp-accounts radio (Use mine /
  Create fresh + label input), submit the new `ProfileCreate` fields.
- Bankroll tab already gets labels from Task 4 — render `PROVIDER (label)`.
- Keep the existing terminal/retro styling; minimal, consistent with current UI.

**Verify:**
- [ ] Manually (Claude Preview / `preview_screenshot`) create each combination;
  confirm the resulting profile's Bankroll shows the right labeled accounts and
  shared-sharp balances mirror across profiles.
- [ ] `npm run lint` clean.

---

## Verification Strategy

End-to-end on local dev against a copy of (or seeded) DB:
1. Migration runs idempotently; balances reconcile (Task 2 checks).
2. Backend test suite green, incl. new repo/stats tests (Tasks 3, 6).
3. Manual flow: create an edge profile + a bonus profile sharing sharp accounts;
   set a sharp balance under one → mirrored in the other; place an edge bet and a
   bonus pair → Stats shows clean true ROI + separate bonus profit; fresh-sharp
   account stays private.
4. `ruff` + `npm run lint` clean.

## Rollback Plan

- All work on a branch; revert via git if needed.
- The risky, irreversible step is Task 2 dropping `ProfileProviderBalance`.
  Mitigate: take a DB dump before running against any real data; if the spec's
  "keep one release" option is chosen, the old table remains as a fallback read
  path and rollback is a code revert only.

## Open decisions (carried from spec; resolve during implementation)
1. Account soft-delete vs hard-delete when bets exist → lean `is_active=False`.
2. `bets.account_id` on-delete → `SET NULL` (profile.kind on the bet's profile
   still drives bucketing).
3. Migration label defaults → `rasmus` for the existing shared sharp pool.
4. Stats bonus-profit scope: active-profile vs all-profiles → lean all-profiles.
5. `account_repo.resolve` rule when a profile links >1 account on one provider.
