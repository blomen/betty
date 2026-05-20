# Arb Recording: Dedup Fix, Phantom Cleanup, Leg Linkage

**Date:** 2026-05-20
**Status:** Approved design

## Problem

An audit of recently recorded bets surfaced three defects in how Polymarket
"arb counter" legs are recorded:

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

**Not a defect:** the settlement logic. `settle()` only marks `lost` on genuine
on-chain resolution (`redeemable=True` + resolved price, or REDEEM/SELL
evidence). Once dedup is fixed, each position is recorded once and settled once,
correctly. No change to settlement.

## Scope

Three parts. Parts 1 and 3 touch the backend (schema + endpoint) and the local
client; Part 2 is a one-time DB operation.

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

## Deploy

- Parts 1 and 3 require a backend rebuild (schema migration + new endpoints) and
  an `arnold.bat` restart (local recorder + router + auto-poller changes).
- Part 2 is a DB operation run against `arnold-postgres-1`.

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
