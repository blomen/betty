# Arb Recording: Dedup Fix, Phantom Cleanup, Leg Linkage

**Date:** 2026-05-20
**Status:** Parts 1–3 approved design · Part 4 added 2026-05-22 (pending review)

## Problem

An audit of recently recorded bets surfaced four defects in how Polymarket
positions are recorded:

1. **Duplication death spiral.** The auto-poller runs `polymarket_api.sync()`
   every 5 minutes. Its dedup set (`known_ids`) is built from
   `fetch_db_pending()` — **pending bets only**. A *losing* Polymarket position
   stays in the `/positions` feed indefinitely (the losing token never
   auto-redeems). Once such a position is settled `lost`, it drops out of the
   pending set, so the next sync re-inserts it as a fresh row, and `settle()`
   re-marks it `lost`. Result: 70 `arb_counter` rows for only 28 real
   positions — one position (`0x156ca0…`, Geneva Open: Brooksby vs Ruud)
   recorded 11 times. Still actively producing new duplicate rows.

2. **No leg linkage.** An arb is a two-part position (a soft-book anchor + a
   Polymarket counter) whose combined payout is a guaranteed profit. The two
   legs are recorded by *different* paths — the soft leg via reactive history
   sync, the Polymarket leg via the auto-poller — and **nothing links them**.
   `arb_runner` sends an `arb_group_id` in a `notes` field, but `BetCreate` has
   no such field so it is silently dropped. There are zero `arb_anchor` rows in
   the DB. A single arb pair cannot be viewed or verified.

3. **42 phantom rows** already in the `bets` table from defect #1.

4. **Cross-path value-row phantoms (added 2026-05-22).** A second, distinct
   duplication path. The reactive history sync (`_record_unknown_open_bets`)
   builds its dedup set from `db_pending` — *pending bets for that provider
   only*. A Polymarket position already recorded as an `arb_counter` row by the
   auto-poller drops out of the pending set the moment it settles; the reactive
   sync, now blind to it, re-inserts it as a separate `bet_type=NULL` "value"
   row. The reactive path carries no on-chain hash, so its `provider_bet_id` is
   null and only the `(odds, stake)` signature could dedup it — and that
   signature set is also pending-only. Confirmed pairs: value rows 760, 761,
   774, 613 duplicate `arb_counter` rows 750, 767, 769, 609 (identical
   odds/stake/result/payout; the `arb_counter` row holds the real
   `provider_bet_id`). The value rows additionally carry a fuzzy-matched wrong
   `event_id` (hence garbage `fair_odds`), and can even mis-settle — row 614 is
   `lost` while its real counterpart 630 is `won`. Defect #1's fix (Part 1)
   does not touch this path; Part 2's cleanup is scoped to
   `bet_type='arb_counter'` and cannot see these `NULL`-typed rows.

**Not a defect:** the settlement logic. `settle()` only marks `lost` on genuine
on-chain resolution (`redeemable=True` + resolved price, or REDEEM/SELL
evidence). Once dedup is fixed, each position is recorded once and settled once,
correctly. No change to settlement.

## Scope

Four parts. Parts 1, 3 and 4a touch the backend (schema + endpoints) and the
local client; Part 2 and the Part 4b cleanup are one-time DB operations.

### Part 1 — Stop the duplication

Dedup against **all recorded conditionIds for the provider, any result status** —
not just pending. A conditionId is unique to a market and a resolved market
cannot be re-bet, so "seen once → never re-insert" is always correct.

- **`backend/src/api/routes/bets.py`** — new endpoint
  `GET /api/bets/recorded-ids?provider_id=<id>` returning
  `{"provider_bet_ids": [<all non-null provider_bet_id for that provider, any result>]}`
  for the active profile. Backed by a new `BetRepo.recorded_provider_bet_ids()`
  query.
- **`arnold/mirror/recorders/polymarket_api.py`** — `sync()` gains a
  `fetch_known_ids` callable. `known_ids` is built from it. `known_sigs` (the
  fallback for rows with no conditionId) and `fetch_db_pending` (used for
  conditionId backfill) are unchanged.
- **`arnold/mirror/recorders/kalshi_api.py`** — identical change.
- **`arnold/mirror/router.py`** — `sync_positions` wires a `fetch_known_ids`
  callable (calls the new endpoint via the tunnel client) into both recorders.

Net effect: each real position is recorded exactly once, ever.

### Part 2 — Clean the 42 phantom rows

One-time DB operation, no code:

- For `provider_id='polymarket'` `arb_counter` rows, group by `provider_bet_id`
  (conditionId). Where count > 1, keep one row, delete the rest (70 → 28).
- **Keep rule:** prefer a row with a non-null `settlement_source` (a
  manual-correction row = ground truth); otherwise the earliest `placed_at` /
  lowest `id` (the original placement — truest odds and stake).
- Before deleting, check FK children (`bet_traces`, `bet_postmortems`,
  `settlement_queue`) referencing the doomed rows and delete or reassign them
  first.
- The exact keep/delete list is shown to the user for sign-off **before** any
  `DELETE` runs.

### Part 3 — Link the two legs (`arb_group_id`)

- **Schema** — add `bets.arb_group_id VARCHAR NULL`:
  - Column on the `Bet` ORM model in `backend/src/db/models.py`.
  - Entry `("bets", "arb_group_id", "VARCHAR")` in the `_run_pg_migrations`
    `additions` list (idempotent `ADD COLUMN IF NOT EXISTS`).

- **Correlation pass** — new `backend/src/services/arb_correlation.py` with a
  `correlate_arbs(session)` function:
  - Candidate legs = `bets` rows with `arb_group_id IS NULL`, `placed_at`
    within the last 30 days.
  - **Counter** = a leg with `provider_id IN ('polymarket','kalshi')` and
    `bet_type='arb_counter'`. **Anchor** = any leg from another provider.
  - For each ungrouped counter, find anchor candidates by:
    - **HIGH confidence** — `event_id` equal and non-null on both, AND
      complementary outcomes (home↔away, over↔under), AND `placed_at` within a
      2-hour window. Auto-link.
    - **MEDIUM confidence** — counter `event_id` is null: both team names of an
      anchor candidate's event appear in the counter's `boost_event` title, AND
      `placed_at` within 2 hours, AND exactly one such candidate exists.
      Auto-link.
    - **LOW confidence** — multiple candidates, or only time overlap. **Left
      unlinked.** A wrong pair corrupts the analytics being fixed; no guessing.
  - On a match: if the anchor is already grouped, reuse its `arb_group_id`;
    otherwise mint a new `uuid4().hex[:12]`. Set both legs' `arb_group_id`.
    Set the anchor's `bet_type` to `arb_anchor` when it is currently null. A
    group accretes legs naturally, so 1-soft + multiple-counter arbs work.

- **`backend/src/api/routes/bets.py`** — `POST /api/bets/correlate-arbs`
  endpoint invoking the service; returns `{"linked": <n>, "groups": <n>}`.

- **`arnold/mirror/recorders/auto_poller.py`** — after each provider loop,
  `POST {local_url}/api/bets/correlate-arbs` (forwarded to the server by the
  local proxy) so newly recorded legs are linked within ~5 minutes.

- **Backfill** — run the endpoint once after deploy to link existing legs.

**Honest limitation:** Polymarket legs with a null `event_id` and an obscure
title (ITF / minor esports) that have no clear single anchor candidate are left
unlinked. Aggregate P&L is still correct from Parts 1–2; only per-arb grouping
is incomplete for those.

### Part 4 — Stop the reactive-sync value-row phantoms (added 2026-05-22)

Defect #4. Two pieces, mirroring Parts 1 + 2.

#### Part 4a — Dedup `_record_unknown_open_bets` against all recorded bets

`_record_unknown_open_bets` must dedup against **every** recorded bet for the
provider — any `result`, any `bet_type` — not just `db_pending`. Same
"seen once → never re-insert" principle as Part 1.

- **`backend/src/api/routes/bets.py`** — alongside the Part 1 endpoint
  `GET /api/bets/recorded-ids`, add `GET /api/bets/recorded-signatures?provider_id=<id>`
  returning the `(odds, stake)` signatures of **all** bets for that provider,
  **any `result` and any `bet_type`, including `arb_counter`**, for the active
  profile. The reactive path's rows have a null `provider_bet_id`, so the
  signature is the only key that can match an auto-poller `arb_counter` row —
  it must be present. Backed by a new `BetRepo.recorded_signatures()` query.
- **`arnold/mirror/pending_loop.py`** — `_record_unknown_open_bets` seeds
  `known_pids` from `recorded-ids` and `known_sigs` from `recorded-signatures`
  in addition to `db_pending`. The existing `_sig` rounding
  `(round(odds, 2), round(stake, 1))` already absorbs CLOB-fill cent drift —
  the 3.82-vs-3.83 case rounds to the same `3.8` — so it is left unchanged.
- **`arnold/mirror/provider_runner.py`** — the sibling
  `_record_unknown_open_bets` carries the identical flaw; apply the same change
  (or extract one shared helper both call).
- **Fail-closed** — if the `recorded-ids` / `recorded-signatures` fetch fails,
  abort the insert, exactly as the existing `db_pending is None` guard does. A
  blind insert re-creates the phantom.
- **`arnold/mirror/router.py`** — the reactive `_sync_provider` path that
  invokes `_record_unknown_open_bets` wires the two fetches through the tunnel
  client (same pattern Part 1 uses for `fetch_known_ids`).

#### Part 4b — Clean the existing value-row phantoms

One-time DB operation, no code:

- Target: `bets` rows where `provider_id IN ('polymarket','kalshi')`,
  `bet_type IS NULL`, `provider_bet_id IS NULL`, whose
  `(round(odds, 2), round(stake, 1))` equals that of an existing `arb_counter`
  row for the same provider.
- **Keep rule:** keep the `arb_counter` row — it carries the on-chain
  `provider_bet_id` and the Part 3 `arb_group_id`; delete the `NULL`-typed
  value row.
- FK children (`bet_traces`, `bet_postmortems`, `settlement_queue`) on the
  doomed rows are deleted/reassigned first — same as Part 2.
- The keep/delete list is shown to the user for sign-off **before** any
  `DELETE` runs.
- Audit set as of 2026-05-22: value rows 613, 614, 760, 761, 774. Resolve the
  live set from the query at run time — do not hard-code ids.

**Related, out of scope:** the phantom value rows are also fuzzy-matched to the
wrong canonical `event_id` (e.g. 761 → an unrelated esports match), which is
what makes their `fair_odds` and edge meaningless. Once Part 4 prevents and
cleans the phantoms this is moot for them — but a *genuine* reactively-synced
value bet can still receive a wrong `event_id` from the same fuzzy matcher.
That matcher is a separate fix, tracked separately.

## Deploy

- Parts 1, 3 and 4a require a backend rebuild (schema migration + new
  endpoints) and an `arnold.bat` restart (local recorder + router +
  auto-poller + pending-loop changes).
- Parts 2 and 4b are DB operations run against `arnold-postgres-1`.

## Verification

- Part 1: after deploy, run two auto-poller cycles; confirm a known
  settled-`lost` conditionId is **not** re-inserted (`skipped_dup` increments,
  `inserted` does not).
- Part 2: `SELECT provider_bet_id, count(*) FROM bets WHERE bet_type='arb_counter'
  GROUP BY 1 HAVING count(*) > 1` returns no rows.
- Part 3: after the backfill run, spot-check that linked groups each have a
  soft anchor + Polymarket counter on the same event, and that
  `sum(payout) > sum(stake)` per group (the guaranteed-profit invariant) for
  settled groups.
- Part 4a: after deploy, settle a Polymarket `arb_counter` position, then
  trigger a reactive history sync (navigate to the Polymarket portfolio).
  Confirm **no** new `bet_type=NULL` row appears for that position.
- Part 4b: this query returns 0 —
  `SELECT count(*) FROM bets v WHERE v.provider_id='polymarket'
  AND v.bet_type IS NULL AND v.provider_bet_id IS NULL AND EXISTS
  (SELECT 1 FROM bets c WHERE c.provider_id='polymarket'
  AND c.bet_type='arb_counter'
  AND round(c.odds::numeric,2)=round(v.odds::numeric,2)
  AND round(c.stake::numeric,1)=round(v.stake::numeric,1))`.
