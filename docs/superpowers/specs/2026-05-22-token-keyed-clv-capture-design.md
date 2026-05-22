# Token-Keyed CLV Capture — Design

**Date:** 2026-05-22
**Status:** Approved (brainstorming) — pending implementation plan

## Problem

Polymarket value bets are bleeding capital. The at-placement edge the scanner
shows is unreliable: it is computed against Pinnacle, which is not a sharp
baseline on the obscure esports / ITF / qualifier markets these bets land on,
and the polymarket→Pinnacle event match is frequently a false positive. So the
"+12–24%" edges that drew the bets are phantom.

**Closing Line Value (CLV) is the matching-free way to measure true edge** —
compare the odds you got to where the market *closed*. Consistently beating the
close = real edge; not beating it = none. The `bets` table already has the
columns (`closing_odds`, `clv_pct`, `provider_closing_odds`, `provider_clv_pct`),
but the capture is broken:

- `BetService.snapshot_closing_odds()` finds bets by joining `Event` and keys
  the Polymarket closing-odds lookup on `event_id`. The ~16-of-22 recent
  Polymarket bets recorded with **no `event_id`** get no CLV at all.
- For the bets it does find, it picks the Polymarket `Odds` row with
  `.first()` — **no outcome filter, no ordering** — so it grabs an arbitrary
  row, often the wrong outcome. Result: garbage (`provider_clv_pct` of +445%,
  +475%; a physically-impossible +40% cohort average).

The fix: capture the closing price keyed on the Polymarket **`token_id`**, which
pins market + outcome + line exactly — no `event_id`, no fuzzy matching — and
surface the aggregate as a trusted strategy-health metric.

## Goals

- A reliable `provider_clv_pct` for Polymarket bets: CLV vs Polymarket's *own*
  closing price, captured by `token_id`.
- A Betting-Stats view of aggregate CLV — the canonical "is this strategy +EV"
  read.

## Non-goals

- **Pinnacle-side `clv_pct`.** It carries the same `.first()`-wrong-outcome flaw
  and depends on the very event-matching this design routes around. Left as-is;
  `provider_clv_pct` becomes the *trusted* metric.
- **Backfilling existing bets.** No `token_id` is recoverable retroactively.
  Forward-only; the summary filters to clean rows.
- **Other providers.** Only Polymarket has a `token_id` to pin a market.

## Design

### 1 · Schema — `bets.token_id`

Add `bets.token_id VARCHAR NULL` — the Polymarket CLOB token for the bet's exact
outcome. The join key the capture job needs.

- Column on the `Bet` ORM model in `backend/src/db/models.py`.
- Entry `("bets", "token_id", "VARCHAR")` in the `_run_pg_migrations` additions
  list (idempotent `ADD COLUMN IF NOT EXISTS`).

### 2 · Capture `token_id` at record time

`token_id` is populated only for `provider_id='polymarket'` bets.

- **`/api/bets` POST** — `BetCreate` gains an optional `token_id: str | None`;
  `BetService` persists it.
- **Workflow value-bet placement** — `play_loop._record_manual_bet` reads
  `token_id` from the picked opp's `provider_meta` (the same field
  `_check_live_price` already uses) and includes it in the `/api/bets` payload.
- **Arb counter** — `arb_runner._record_bet` reads `token_id` from the leg's
  `provider_meta`.
- **Reactive / auto-poller sync** — the Polymarket position recorder reads the
  `asset` field of the `/positions` feed (Polymarket positions are keyed by
  `asset` = token_id) and includes it.

### 3 · Closing-price capture job

New `capture_polymarket_closing()` in `BetService`, scheduled in the backend
(~every 5 minutes — the backend runs 24/7 and already reaches the Polymarket
CLOB for extraction).

Selects `Bet` rows where:
- `provider_id = 'polymarket'`, `result = 'pending'`,
- `token_id IS NOT NULL`, `provider_closing_odds IS NULL`,
- `bets.start_time` (the column on the bet row — no `Event` join, no
  `event_id`) is in `[now − 3h, now]`: the event has started and is recent
  enough to still have a meaningful market.

A Polymarket bet with a NULL `start_time` cannot be timed and gets no CLV
(`provider_clv_pct` stays NULL — an honest gap, not a fake value). §2's
recording paths should populate `start_time` for Polymarket bets wherever the
opp / position metadata provides it.

For each: fetch the CLOB price for `token_id` from `clob.polymarket.com/price`
(`side=sell` — the executable ask, same call as `poly_clob.fetch_clob_ask`).

- **Guard:** accept only a live price `0.02 < price < 0.98`. A resolved market
  returns a degenerate `0`/`1` — skip it, leave `provider_closing_odds` NULL
  (CLV genuinely uncapturable beats CLV-as-garbage).
- **Convert:** `closing_odds = 1.0 / price`. This matches how Polymarket entry
  `odds` are stored (raw inverse of fill price), so CLV isolates the line
  movement and the 2% fee cancels. *Invariant:* closing odds and entry odds use
  the same convention — if entry-odds recording ever changes, this must too.
- **Store:** `provider_closing_odds = closing_odds`,
  `provider_clv_pct = round((bet.odds / closing_odds − 1) × 100, 2)`.

The existing `event_id`-keyed, `.first()`-based Polymarket lookup — present in
**both** `snapshot_closing_odds()` and `_calculate_clv()` — is **removed**; this
job becomes the sole source of `provider_closing_odds` / `provider_clv_pct`.
The Pinnacle-side logic in both methods is untouched.

A bet whose event never gets a valid capture (resolved before the job ran,
3h window elapsed) keeps `provider_clv_pct = NULL` — counted as "no CLV", never
as a fake value.

### 4 · `GET /api/bets/clv-summary`

Aggregates `provider_clv_pct` for the active profile.

- Query params: `bet_type` (optional filter), `from` / `to` (optional date
  range on `placed_at`).
- Considers only **clean rows**: `provider_id='polymarket'`,
  `token_id IS NOT NULL`, `provider_clv_pct IS NOT NULL`.
- Returns `{ n, avg_clv, median_clv, pct_beat_close, histogram }` where
  `pct_beat_close` is the share with `provider_clv_pct > 0` and `histogram`
  buckets CLV into `<−10, −10..−5, −5..0, 0..5, 5..10, >10`.
- Backed by a new `BetRepo.clv_summary(profile_id, ...)` query.

### 5 · Stats-page CLV panel

A "Closing Line Value" panel on the Betting Stats page
(`arnold/frontend/src/pages/StatsPage.tsx`), fed by `/api/bets/clv-summary`.

Shows: average CLV, % of bets that beat the close, `n`, and the histogram. A
one-line interpretation hint — *positive average and >50% beating the close =
real edge; otherwise not*. This is the canonical strategy-health view.

## Data flow

```
bet placed (polymarket)
  → token_id recorded on the bets row (§2)
       ↓ (event start_time passes)
capture_polymarket_closing()  — backend, every 5 min (§3)
  → fetch CLOB price for token_id → provider_closing_odds + provider_clv_pct
       ↓
GET /api/bets/clv-summary  — aggregate over clean rows (§4)
       ↓
Betting Stats → CLV panel (§5)
```

## Testing

- **token_id capture:** a `/api/bets` POST with `token_id` persists it; a
  Polymarket bet recorded without one stores NULL.
- **capture job** (mocked CLOB fetch): computes `provider_clv_pct` correctly
  from a known price; skips a degenerate `0.99` price (leaves NULL); skips a bet
  with no `token_id`; skips a bet whose `start_time` is older than 3h; does not
  re-capture a bet that already has `provider_closing_odds`.
- **clv-summary endpoint:** aggregates avg / median / `pct_beat_close` over a
  known set; excludes rows with NULL `token_id` or NULL `provider_clv_pct`;
  honors the `bet_type` and date filters.

## Deploy

- Backend rebuild — schema migration, the capture job + its schedule, the new
  endpoint.
- `arnold.bat` restart — the Stats-page panel and the `token_id` capture in the
  mirror recording paths.

## Rollout and risk

- **Forward-only.** Existing bets keep `token_id = NULL` and are excluded from
  the panel, so the contaminated historical `provider_clv_pct` never pollutes
  the metric. The clean signal accrues from deploy onward.
- The capture job is a read-only price fetch against Polymarket — low risk.
- **Interaction with arb-recording dedup:** the summary assumes one row per real
  position. The cross-path duplicate rows (arb-recording-dedup spec, Part 4)
  would otherwise double-count in the aggregate. Not a blocker — the panel is
  still directionally correct — but the dedup work makes the metric exact.

## Open items resolved during brainstorming

- Capture mechanism: live CLOB fetch keyed by `token_id` (vs reusing the
  extraction `Odds` table) — the only genuinely matching-free option.
- Surfacing: Stats-page panel + `GET /api/bets/clv-summary`.
- Scope: Polymarket only; Pinnacle-side `clv_pct` out of scope; forward-only,
  no backfill.
