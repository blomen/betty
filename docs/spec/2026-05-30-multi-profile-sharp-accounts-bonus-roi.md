# Multi-Profile Sharp Accounts + Bonus-Profit Accounting — Design Spec

**Status:** Draft (awaiting user review) · **Date:** 2026-05-30
**Author:** rasmus + Claude
**Related:** `docs/spec/2026-05-XX-multi-book-sharp-blend.md`, `frontend/src/pages/PlayPage.tsx`, `backend/src/db/models.py`

---

## Overview

When opening a new profile the user must be able to choose, per profile, whether
it **reuses the existing sharp accounts** (Pinnacle / Polymarket / Kalshi /
Cloudbet — the "unlimited pool") or **creates fresh sharp accounts**. A profile
is, in practice, a **bonus-extraction campaign on fresh soft accounts** that is
**hedged against the shared sharp accounts**. (An `edge` profile — the ongoing
unlimited-pool volume — is the other kind.)

Two problems follow:

1. **Shared balances.** The sharp accounts hold real money in one real account.
   If two profiles both hedge on "my Pinnacle", that single real balance must be
   reflected everywhere — sharp balances cannot be per-profile copies.
2. **Honest ROI.** A bonus-extraction play is two legs: the soft-book bonus bet
   (free money) and a real-money sharp **hedge** that locks the bonus value. The
   hedge leg is real money but is **not an edge bet** — its EV is ≈ −vig and its
   variance is full-win-or-full-loss. Counting it in "true ROI" injects noise and
   destroys the metric. The locked bonus gain must be tracked **separately** as
   *bonus profit*, never entering the ROI denominator.

### Decisions locked during brainstorming

- **Profile = purpose.** Each profile has `kind ∈ {edge, bonus}`.
- **Rule B accounting.** **Both** legs of a bonus play (soft bonus leg + sharp
  hedge leg) are excluded from true ROI and summed into a separate **bonus
  profit** number. Classification is **by profile purpose**: every bet placed
  under a `bonus` profile is bonus profit; every bet under an `edge` profile is
  true ROI. (The same shared sharp account can hold both — they are separated by
  the `profile_id` already on each bet.)
- **Shared, labeled sharp accounts.** Sharp accounts are one shared balance shown
  as e.g. `POLY (rasmus) $76.29`. "Fresh sharp" creates a new labeled, independent
  shared account (e.g. `poly/alt2`).
- **Scoped visibility (robust model).** A fresh sharp account must **not** leak
  into every profile. Visibility is explicit via a **link table**, not a global
  flag.

---

## Why an Accounts layer (Approach A, link-table form)

Today balances live in `ProfileProviderBalance`, keyed `(profile_id,
provider_id)` — i.e. **per-profile copies**. That cannot represent "one real
sharp balance shared by N profiles". We introduce a first-class **Account** =
one real account at a provider, and make visibility explicit with a link table.

This is the normalized, textbook model. It was chosen over a lighter
"global-scope flag" variant specifically because the user requires **scoped
visibility** (fresh sharp accounts stay private) and **robustness** for what is a
crucial part of the app.

---

## Data Model

### New table: `accounts`

One row per real account the user owns at a provider.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `provider_id` | str FK → providers.id | |
| `label` | str | Human label, e.g. `rasmus`, `alt2`, `campaign-7` |
| `kind` | str | `sharp` \| `soft` |
| `balance` | float | The real balance. **Source of truth** (replaces `ProfileProviderBalance.balance`) |
| `currency` | str | Native currency (USDC/USD/SEK) — see cross-currency rule |
| `account_opened_at` | datetime, null | Carried over from `ProfileProviderBalance` (dormant-account handling) |
| `is_active` | bool | Soft-delete / hide |
| `created_at`, `updated_at` | datetime | |

Constraints: `UNIQUE(provider_id, label)`.

### New table: `profile_accounts` (link)

Explicit visibility. **A profile sees exactly the accounts linked to it.**

| Column | Type | Notes |
|---|---|---|
| `profile_id` | int FK → profiles.id | |
| `account_id` | int FK → accounts.id | |
| `created_at` | datetime | |

Constraints: `UNIQUE(profile_id, account_id)`. Composite PK on the pair.

### `profiles` — add one column

| Column | Type | Notes |
|---|---|---|
| `kind` | str, default `edge` | `edge` \| `bonus`. Drives ROI bucketing. |

### `bets` — add one column

| Column | Type | Notes |
|---|---|---|
| `account_id` | int FK → accounts.id, null | **Robust key** for "which real account". Required because a profile may link two accounts on the same provider, so `(profile_id, provider_id)` is no longer unique. Nullable only during migration backfill; the source of truth going forward. |

`provider_id` stays on `bets` for all existing readers (it is derivable from the
account but kept to avoid touching every query).

### Tables intentionally left keyed on `(profile_id, provider_id)`

`ProfileProviderBonus` and `ProfileProviderLimit` are **out of scope**. A bonus
profile has exactly one soft account per provider, so these remain unambiguous.
This keeps the change focused on balances + ROI.

---

## Behaviour

### Creating a profile ("open new profile" dialog)

```
Name:           [ campaign-7        ]
Purpose:        ( ) Edge   (•) Bonus campaign        → profiles.kind
Sharp accounts: (•) Use my sharp accounts
                ( ) Create fresh sharp accounts (label: [ alt2 ])
Soft account:   auto-label from profile name (bonus only)
```

- **Use my sharp accounts** → create `profile_accounts` links from the new
  profile to the **existing shared sharp accounts** (the ones linked to the edge
  profile). No new account rows, no balance copy.
- **Create fresh sharp accounts** → create new `accounts` rows
  (`kind='sharp'`, given label) and link them **only** to this profile. They do
  not appear elsewhere.
- **Bonus profile** → also create one `kind='soft'` account per soft provider the
  campaign uses (auto-labeled), linked only to this profile.

### Placing / recording a bet

The active profile + chosen provider resolves to a **specific linked account**
(the funded/selected one). `bets.account_id` is written. The placing profile's
`kind` is what later buckets the bet. No change to the mirror state machine or
intercept paths beyond stamping `account_id`.

### Balance display (Bankroll tab)

- Per profile: show the accounts **linked to that profile**. Shared sharp
  accounts render with their label — `POLY (rasmus) $76.29`.
- Grand totals across profiles sum over **distinct accounts** (a shared account
  linked to 3 profiles is counted **once**).
- All cross-account/cross-profile aggregation converts to one base currency
  (SEK) **before** summing, per the project cross-currency rule.

### Stats tab — two numbers

- **True ROI** — unchanged formula, but the aggregate **excludes every bet whose
  profile.kind = 'bonus'** (in addition to the existing `is_bonus` exclusion).
  Reflects edge bets only.
- **Bonus profit** — a separate total: sum of profit of **all** bets under
  `bonus` profiles (both legs), converted to SEK. **Never** enters the ROI
  denominator. Displayed as its own stat/line.

### `get_stats` change (backend/src/services/bankroll_service.py)

Today: `real_rows = [r for r in rows if not r.is_bonus]`. Extend the settled
aggregate query to carry the placing profile's `kind`, then:

- ROI aggregate = rows where `profile.kind = 'edge'` **and** `not is_bonus`.
- `bonus_profit` = SEK-summed profit of rows where `profile.kind = 'bonus'`
  (both legs), plus any stray `is_bonus` rows under edge profiles.

---

## Migration

1. Create `accounts`, `profile_accounts`; add `profiles.kind`, `bets.account_id`.
2. Existing profile(s) → `kind='edge'`.
3. Convert each `ProfileProviderBalance` row to an `Account` + a
   `profile_accounts` link:
   - sharp providers (`pinnacle`, `polymarket`, `kalshi`, `cloudbet`) →
     `kind='sharp'`, `label` defaulted (e.g. `rasmus`). If the same sharp
     provider appears under multiple profiles, **collapse to one shared account**
     (take the edge profile's balance as truth) and link all those profiles to it.
   - other providers → `kind='soft'`, `label` from the owning profile name.
   - carry `balance`, `currency` (via existing provider currency), `account_opened_at`.
4. Backfill `bets.account_id` by matching `(profile_id, provider_id)` to the
   linked account; for sharp providers, the shared account.
5. Verify balances reconcile, then **drop `ProfileProviderBalance`**.

A reversible path: keep `ProfileProviderBalance` for one release behind a feature
read-path flag if desired, but the plan targets a clean drop after verification.

### Deletion semantics (robustness)

Deleting a profile removes its `profile_accounts` links. Any account left with
**zero** remaining links is garbage-collected:

- soft campaign accounts were single-linked → they orphan and are deleted;
- shared sharp accounts retain links from other profiles (e.g. the edge profile)
  → they survive.

Bets retain `account_id` (FK may be set `ON DELETE SET NULL` for GC'd soft
accounts, or accounts are soft-hidden via `is_active=False` instead of hard
delete — **decision for the plan**: prefer `is_active=False` for any account that
has bets, hard-delete only bet-less orphans, to preserve Stats history).

---

## Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `accounts` / `profile_accounts` models | Persist real accounts + visibility | `profiles`, `providers` |
| Account repo | CRUD + "shared accounts for profile X", "distinct accounts" | models |
| Profile-create service | Wire links per dialog choice (use/fresh sharp, soft signup) | account repo |
| Balance set/sync (`/api/bankroll/set/{provider}`) | Resolve active profile+provider → account, write `accounts.balance` | account repo |
| Bet recording (`play_loop`, `pending_loop`) | Stamp `account_id` on insert | account repo |
| `get_stats` | kind-based ROI exclusion + `bonus_profit` | bet aggregate + profile.kind |
| Bankroll tab | Labeled accounts, distinct-account totals | bankroll API |
| Stats tab | True ROI + separate Bonus profit | stats API |
| Profile dialog | Purpose + sharp choice + label | profiles API |

---

## Out of scope

- Reworking `ProfileProviderBonus` / `ProfileProviderLimit` onto accounts.
- Any change to extraction, scanner, or mirror navigation logic.
- Auto-detecting hedge legs (classification is by profile purpose, not leg-pairing).

---

## Open questions for the implementation plan

1. Hard-delete vs `is_active=False` for accounts that have bets (lean:
   `is_active=False`).
2. `bets.account_id` FK on-delete behaviour (lean: `SET NULL`, since profile.kind
   on the bet's profile still drives bucketing and Stats reads survive).
3. Exact label defaults for the migration (`rasmus` for existing sharp pool?).
