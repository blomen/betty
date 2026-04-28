# Fresh-Profile Audit + Bonus Deposit Hint — Design

**Date:** 2026-04-28
**Status:** Approved (ready for implementation plan)

## Goal

Verify end-to-end that a brand-new betting profile sees the system's full capability surface — every provider listed, zero balance, zero bet history, every configured bonus visible as `available`, and deposit-trigger amounts surfaced inline next to balance on the arb/value pages. Also produce a deposit recommendation for the unlimited providers (`pinnacle`, `polymarket`, `cloudbet`, `kalshi`) sized to fund the currently-available value bets.

## Three Deliverables

1. **Audit profile** — a real `Profile` row named `"Audit"` in the production DB. Profiles are isolated by design (separate balances, bonuses, history, browser port), so this does not touch the user's existing profile state. Reusable.
2. **"Deposit Xkr" hint** — rendered next to the balance cell on the arb/value pages whenever a provider has an available bonus tied to a deposit trigger and the balance is currently zero.
3. **Audit report** — a markdown file at `docs/audits/2026-04-28-fresh-profile-audit.md` that captures provider coverage, bonus coverage, arb-page reachability, and the deposit recommendation (live + target-bankroll table).

## Components

### A. Backend — bonus auto-seed on profile creation

**File:** `backend/src/api/routes/profiles.py`

`create_profile` ([line 115](../../../backend/src/api/routes/profiles.py#L115)) currently inserts only a `Profile` row. It does **not** seed `ProfileProviderBonus` rows; those appear lazily when `bankroll_service.deposit_with_bonus` runs. A fresh profile therefore has empty bonus state until the first deposit, which means the audit can't observe "all bonuses available" — the canonical request.

Fix: extract a helper `_seed_provider_bonuses(profile_id, db)` that scans `providers.yaml`, identifies every provider whose entry has a `bonus` block, and inserts one `ProfileProviderBonus` row per provider with `bonus_status="available"` and the yaml-defined `wagering_requirement` / `min_odds` / `expires_at` cleared (timer starts on actual claim, not on seeding).

The helper is **idempotent** — it skips providers that already have a row for this profile, so it can also be invoked against the existing default profile to backfill missing rows without disturbing in-progress bonuses.

Expose `POST /api/profiles/{id}/seed-bonuses` for manual re-runs and call the helper from `create_profile` after `db.commit()`.

### B. Backend — deposit-trigger field in `/api/bankroll`

**File:** `backend/src/services/bankroll_service.py`, method `get_bankroll`.

Each provider entry returned by `GET /api/bankroll` gains two optional fields:

- `bonus_trigger_amount: float | None` — the yaml `min_deposit` from the provider's bonus block.
- `bonus_currency: str | None` — provider's native currency for that trigger.

Populated when **all** of:

- The provider has a `bonus` block in `providers.yaml`.
- The active profile has a `ProfileProviderBonus` row with `bonus_status="available"`.
- Current per-provider balance is `0` (or below the bonus's `min_deposit`).

Returned `null`/absent in every other case so the frontend doesn't render a stale hint.

### C. Frontend — "deposit Xkr" hint label

**File:** `arnold/frontend/src/pages/PlayPage.tsx`

Today `providerBalances` is `Record<string, number>` (line 95). Upgrade to:

```ts
type ProviderBalanceInfo = { balance: number; bonus_trigger?: number; bonus_currency?: string }
const [providerBalances, setProviderBalances] = useState<Record<string, ProviderBalanceInfo>>({})
```

Backwards-compatible: every read site that expects a number now reads `.balance`. Fetch path through `/api/bankroll` populates the optional `bonus_trigger` / `bonus_currency` fields when the backend supplies them.

Render a faint orange hint inline next to the balance cell on the **arb** section and reuse the same renderer in the **value** section for visual consistency:

```
0 SEK · deposit 1000 kr
```

Only render when `bonus_trigger` is set; balance > 0 path is unchanged.

### D. Audit script + report generator

**File:** `scripts/audit_fresh_profile.py` (new, one-shot, runnable locally).

Sequence:

1. `GET /api/profiles` — if a profile named `"Audit"` already exists, capture its `id`. Otherwise `POST /api/profiles {name:"Audit"}` and capture the new `id`.
2. `POST /api/profiles/{id}/seed-bonuses` to ensure `ProfileProviderBonus` rows exist (idempotent).
3. `POST /api/profiles/{id}/activate`.
4. `GET /api/bankroll`, `GET /api/bankroll/bonuses`, `GET /api/value-bets`, read `backend/src/config/providers.yaml`.
5. Compute four report sections:
   - **Provider coverage** — every active provider in yaml appears in `/api/bankroll` with `balance=0`. Flag gaps as `[!] missing-from-bankroll`.
   - **Bonus coverage** — every yaml `bonus` block maps to a `ProfileProviderBonus` row with `status="available"` and the `/api/bankroll` response exposes a non-null `bonus_trigger_amount`. Flag misconfigurations as `[!] bonus-not-actionable`.
   - **Arb-page sanity** — every yaml provider is reachable through PlayPage's cluster map (the union of `SOFT_CLUSTER_MEMBERS` values, `SOFT_STANDALONES`, and `UNLIMITED_PROVIDERS`). Flag orphans as `[~] not-on-arb-page`.
   - **Deposit recommendation** — see below.
6. Write the markdown report to `docs/audits/2026-04-28-fresh-profile-audit.md`.

#### Deposit recommendation algorithm

Pull the current value-bet feed snapshot. For each bet, the responsible "unlimited" provider is the one in `{pinnacle, polymarket, cloudbet, kalshi}` actually offering the value (the bet's `provider_id`).

**Live solve.** Iteratively raise hypothetical total bankroll `B` from 0 in 1k SEK steps. At each step, run the same `StakeCalculator` used in production (dynamic Kelly 0.25–0.75, 2% single-bet cap, dynamic min-stake floor) for every bet in the feed and record each bet's `skip_reason`. Stop at the smallest `B` where every bet returns `skip_reason == None` — i.e. no bet is rejected for being below the min-stake floor or for hitting the cap with a still-uncovered Kelly target. Report `B*` as the recommended bankroll, and split it across the four unlimited providers weighted by the **sum of resulting stakes assigned to that provider** in the solved feed (heavy-Pinnacle days deposit mostly to Pinnacle).

**Target-bankroll table.** For `B ∈ {10k, 25k, 50k, 100k}` SEK, report:

| Bankroll | Bets fundable | % of feed | Total expected EV | Per-provider split |

Provides context — if `B*` is much smaller than 25k, the user can deposit conservatively without losing meaningful coverage.

If the value-bet feed is empty at audit time, the live-solve section reports `"feed empty, re-run during market hours"` and the target-bankroll table is omitted.

## Data flow

```
1. POST /api/profiles {name:"Audit"}     → profiles row
2. seed_provider_bonuses(profile.id)     → ProfileProviderBonus rows (status="available")
3. POST /api/profiles/{id}/activate      → switch active flag in DB
4. GET  /api/bankroll                    → balance=0 + bonus_trigger per provider
5. GET  /api/value-bets                  → snapshot fed into deposit-recommendation solve
6. Write report → docs/audits/2026-04-28-fresh-profile-audit.md
7. (Frontend now renders "deposit 1000kr" labels for every available bonus)
```

## Error handling

- **yaml-orphan bonus** (yaml lists a bonus for a provider not in the DB): seed helper logs warning, skips, audit report records `[!] yaml-orphan`.
- **Missing or zero `min_deposit`** in yaml: `bonus_trigger_amount` is `null`, frontend renders no hint.
- **Empty value-bet feed**: deposit recommendation explicitly states the feed is empty rather than reporting `0 kr`.
- **`/api/bankroll` shape change**: new fields are optional in the TS interface, old responses still render — no crash.
- **Audit profile already exists** (re-run): script skips creation, still seeds (idempotent), still activates, still produces a report.

## Testing / verification

- **Backend unit:** running the seed helper twice on the same profile leaves row count unchanged; `bonus_trigger_amount` populated only when `available` + `balance == 0`; helper handles yaml-orphan providers without raising.
- **Manual frontend:** activate Audit profile via arnold.bat, open the arb page, every soft cluster + standalone visible at `0 SEK · deposit Xkr` for providers with configured bonuses; switch to the real profile, hints disappear (because balance > 0 or bonus already claimed).
- **Report-as-deliverable:** the report's section-1–3 must show **zero `[!]` flags**. `[~]` warnings (e.g., `not-on-arb-page` for signal-only providers like `stake`/`marathon`/`consensus`) are expected and explained inline.

## Out of scope

- Performing an actual deposit (still manual via existing `POST /api/bankroll/deposit/{provider_id}`).
- Cleanup / deletion of the Audit profile — kept for re-runs; remove via existing `DELETE /api/profiles/{id}` when no longer needed.
- Wagering-progress simulation (the audit only verifies seeding, not bonus completion paths).
- Bonus rendering on the bankroll page (already exists per `BankrollPage.tsx`).
- Server-side production deploy — backend changes (A)+(B) are bundled into one rebuild at the end of implementation; script (D) runs against the local arnold tunnel.

## Open questions

None at design-approval time. All four interactive questions (real-DB profile, fix-as-part-of-audit, both-deposit-flavors, verify-and-seed) were resolved during the brainstorm.
