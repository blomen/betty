# Slip-Odds Architecture & Semi-Auto Arb Workflow

**Date:** 2026-04-25
**Status:** Approved (design); ready for implementation plan
**Scope:** Backend (mirror runners, workflows, SSE), Frontend (PlayPage), Server (extraction cadence)

## Problem

Today's placement decisions consume **scanner-computed** odds and edge values that can be 1–15 minutes stale (depending on extraction tier). Two consequences:

1. **Arb workflow auto-hedges** counter-legs immediately after the anchor is intercepted, using stale scanner odds. By the time the counter-leg navigation + prep finishes, the arb margin can be gone — the hedge fires anyway and books a loss.
2. **Value bets** check live odds once at `prep_betslip` time via `check_live_price`, but anything between that one-shot check and the user's click is invisible — and the scanner's `edge_pct` shown in the UI may not reflect what the slip will actually book.

Architecturally, the scanner is treated as ground truth. It isn't. The slip widget on each provider's site is.

## Solution: Two-Tier Odds Architecture

```
EXTRACTION TIER  (discovery)              REAL-TIME TIER  (truth)
─────────────────────────────             ─────────────────────────────
providers.yaml schedulers          →      SlipOddsStream per active leg
OpportunityScanner                        Reads workflow.read_slip_odds(page)
Outputs: candidate value bets,            ~1Hz per loaded slip
        candidate arb opps                Recomputes profit% / edge% per tick
edge_pct, guaranteed_profit_pct           Broadcasts arb_alignment / live_price
                                          
Cadence: 1min → 30min                     Cadence: ~1Hz, only for loaded slips
Role: WATCHLIST                           Role: PLACEMENT GROUND TRUTH
"What's worth loading slips on?"          "What is the slip actually showing?"
```

The scanner picks **what to load**. The slip stream decides **whether the math still works**. Every placement decision the user makes is informed by the slip stream, not by scanner output.

## Workflow Contract Additions

Every workflow that participates in placement (soft + unlimited providers) implements:

- **`navigate_to_event(page, bet_ns)`** — exists, unchanged
- **`prep_betslip(page, bet_ns, stake)`** — exists, unchanged
- **`read_slip_odds(page) -> float | None`** — NEW. Idempotent scrape of the price the loaded slip currently displays. Returns `None` if slip is empty / errored / closed.
- **`update_slip_stake(page, stake)`** — NEW (factor out of `prep_betslip` where it already exists). Re-write the stake field on a loaded slip without re-navigating.
- **`confirm_bet(page)`** — exists for autonomous workflows. Not invoked by the runner in the new model — the user clicks Place inside the mirror tab; existing interceptor wiring records the response.

## SlipOddsStream Component

New module: `arnold/mirror/slip_odds_stream.py`.

- Per-leg poller (one task per slip-loaded provider tab)
- Polls `workflow.read_slip_odds(page)` at configurable interval (default 1.0s)
- Maintains the latest odds value per `(provider_id, event_id, market, outcome)` key
- Aggregates across all active legs of a runner
- Throttled SSE broadcast: emit `arb_alignment` (or `live_price` for value bets) at most every 0.5s, suppress if no leg's odds changed
- Stops automatically when the slip clears or the runner stops

Both `ArbRunner` and `ProviderRunner` instantiate and consume the stream. A single stream owns at most one tab's poller; multi-leg arbs aggregate multiple streams.

## Arb Workflow (semi-auto, mirror-clicked)

Per arb opportunity:

```
1. PICK + LOAD ALL LEGS  (no placements)
   ├─ pick highest-profit watchlist opp where:
   │     - 2-way: 1 soft + 1 unlimited counter
   │     - 3-way: 1 soft + 2 unlimited counters (no second soft leg)
   ├─ for each leg in parallel:
   │     - ensure logged in on its provider tab
   │     - navigate to event + outcome
   │     - fill betslip @ planned stake
   │       (anchor = full soft balance, capped at site max;
   │        counters = anchor_stake × anchor_odds / counter_odds)
   └─ broadcast `arb_legs_loaded` { per_leg slip_state, planned stakes }

2. STREAM SLIP ODDS  (mirror scrape, ~1Hz, every leg)
   ├─ recompute profit% on every tick
   │     profit% = 1 / (1/anchor_odds + Σ 1/counter_odds) − 1
   ├─ recompute counter stakes from current anchor stake × odds ratio
   ├─ if a counter stake drifted ≥ 1 SEK or ≥ 1%, update its slip in place
   └─ broadcast `arb_alignment` { profit_pct, per_leg: {odds, stake, slip_state} }

3. STANDBY — INTERCEPT MIRROR PLACEMENTS  (no system trigger)
   ├─ user clicks Place INSIDE mirror on SOFT tab
   │   ├─ interceptor catches placement response
   │   ├─ ACCEPTED (full or partial)
   │   │   ├─ record actual_stake / actual_odds for soft
   │   │   ├─ recompute counter stakes from actual anchor stake
   │   │   ├─ update each counter slip in place
   │   │   ├─ broadcast `arb_anchor_placed`
   │   │   └─ keep streaming (now informational — anchor risk is on)
   │   └─ REJECTED
   │       ├─ broadcast `arb_anchor_rejected` { reason }
   │       └─ go to step 5 (iterate)
   │
   └─ user clicks Place INSIDE mirror on EACH COUNTER tab
       ├─ each click → interceptor → record per leg
       ├─ broadcast `arb_hedge_placed` per leg
       └─ all hedges in → `arb_complete`, write arb_group_id linkage

4. EXPOSURE WARNINGS  (UI surface only, no blocking)
   ├─ user clicks counter BEFORE soft → UI flashes red, system records
   ├─ user clicks soft when alignment negative → UI shows live profit% loud
   └─ system never blocks a mirror click; user always retains control

5. ITERATE on REJECT
   ├─ on soft REJECTED: pop next-best opp on same soft provider
   │     swap soft slip; rebuild counter slips on counter tabs;
   │     resume streaming
   └─ block (event, market) on all involved providers after success
```

### Stake Rules
- **Anchor stake** = full remaining balance on the soft side, capped at site max stake
- **Counter stakes** = `anchor_stake × anchor_odds / counter_odds` (equal-payout arb)
- No `stake_pct` from scanner — that field is ignored for execution
- Counter slip stake updates throttled: re-write only when new value differs by ≥ 1 SEK *or* ≥ 1% of current stake

### 3-Way Arb Constraint
3-way opps require **exactly one soft leg + remaining counters on unlimited providers** (Pinnacle, Polymarket, Cloudbet, Kalshi). Two soft legs in one arb is rejected at opp-pick time.

Investigation needed during plan: confirm whether `/api/opportunities/arb-workflow` already emits 3-way opps shaped this way. If the scanner emits opps with multiple soft legs, the runner filters them out. If a server-side filter is preferable, that's a small scoped change in `OpportunityScanner.scan_arb`.

## Value-Bet Workflow Update

`ProviderRunner` (used for unlimited providers in value-bet mode) extends to consume `SlipOddsStream`:

- After `prep_betslip` succeeds, start a single-leg `SlipOddsStream` for the loaded slip
- Replace one-shot `check_live_price` with continuous slip-odds streaming
- Recompute live `edge_pct` per tick using scraped odds vs. cached fair odds (Pinnacle)
- Broadcast `live_price` events (existing event name) on every meaningful change
- `bet_ready` event payload now includes a streaming-edge field; UI shows live edge instead of scanner edge

User still clicks Place inside the mirror tab — interceptor records — system never auto-confirms.

## React UI Changes (PlayPage)

Status-only surface. **No "Place bet" buttons in React for arb or value bets.**

- New SSE handlers: `arb_legs_loaded`, `arb_alignment`, `arb_anchor_placed`, `arb_anchor_rejected`
- Existing handlers reused: `arb_hedge_placed`, `arb_unhedged`, `arb_complete`, `bet_ready`, `bet_placed`, `live_price`
- Arb card shows: live profit%, per-leg odds, per-leg stake, per-leg slip state (loaded / errored / placed), warning banner when alignment is negative or when user clicks counter before anchor
- Value-bet card shows: live edge%, slip state, drift indicator (live odds vs scanner odds)
- The card highlights which mirror tab to click next (visual breadcrumb: "click Place on Unibet → then Pinnacle")

## Extraction Cadence Relaxation

Slip-odds streaming makes scanner freshness less load-bearing for placement safety. Existing tiers (`extraction_scheduling` in `providers.yaml`) can be relaxed to reduce server load:

| Tier | Current Cooldown | Proposed Cooldown | Rationale |
|---|---|---|---|
| `sharp` (pinnacle) | 1 min | 2 min | Slip-side Pinnacle scrape catches drift between cycles |
| `polymarket` | 5 min | 10 min | Same |
| `api_soft` | 2 min | 5 min | Watchlist freshness, not placement |
| `browser_soft` | 10 min | 15 min | Same |
| `browser_antibot` | 15 min | 30 min | Heavy CPU cost; biggest win from relaxation |
| `signal_international` | 5 min | 10 min | Same |

Apply during plan execution; verify `/health/extraction` match-rate metric stays stable. Roll back per-tier if match rate degrades.

## Data Logging (Optional)

New table `slip_odds_ticks`:

```sql
CREATE TABLE slip_odds_ticks (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  provider_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  market TEXT NOT NULL,
  outcome TEXT NOT NULL,
  scraped_odds REAL NOT NULL,
  scanner_odds REAL,             -- last known scanner odds at scrape time
  drift_pct REAL                 -- (scraped - scanner) / scanner * 100
);
CREATE INDEX ix_slip_odds_event ON slip_odds_ticks(event_id, market, outcome);
CREATE INDEX ix_slip_odds_ts ON slip_odds_ticks(ts);
```

One row per stream tick per leg while a slip is loaded. Cheap (slips are loaded for tens of seconds at a time, not 24/7). Useful for tuning the alignment threshold, validating the architecture's assumptions, and as a foothold for future live-odds work.

Logging is gated by env var `SLIP_ODDS_LOGGING=true` so it can be turned off if storage cost becomes a concern.

## Out of Scope

- The extraction pipeline core (providers.yaml structure, OpportunityScanner internals)
- Server API contracts: `/api/opportunities/play/batch` and `/api/opportunities/arb-workflow` keep their response shapes — they're just relabeled "watchlist" in the new mental model
- Settlement, bankroll, stats subsystems
- Stocks side
- Live-odds (in-play) extraction — this design keeps premarket-only behavior; the slip-streaming layer is forward-compatible if live odds are added later

## New SSE Events

| Event | When | Payload |
|---|---|---|
| `arb_legs_loaded` | All legs prepped on their tabs | `{arb_group_id, legs: [{provider_id, event_id, market, outcome, planned_stake, planned_odds, slip_state}]}` |
| `arb_alignment` | Every meaningful slip-odds tick (throttled to 0.5s) | `{arb_group_id, profit_pct, legs: [{provider_id, current_odds, current_stake, slip_state}]}` |
| `arb_anchor_placed` | Soft placement intercepted with status=accepted | `{arb_group_id, provider_id, actual_stake, actual_odds}` |
| `arb_anchor_rejected` | Soft placement intercepted with status=rejected | `{arb_group_id, provider_id, reason}` |
| `arb_hedge_placed` (existing — reused) | Counter placement intercepted | `{arb_group_id, counter_provider, outcome, actual_odds, actual_stake}` |
| `arb_complete` (existing — reused) | All counter legs placed | `{arb_group_id, guaranteed_profit_pct}` |
| `live_price` (existing — reused for value bets) | Slip-odds tick on a single-leg stream | `{event_id, market, outcome, live_odds, live_edge}` |

## Removed Events / Code

- `arb_hedge_placing`, `arb_hedge_failed`, `arb_unhedged` — these came from the auto-hedge code path which no longer fires hedge attempts. They can be removed along with `_place_counter_legs` and `_place_on_provider` in `ArbRunner`.
- `_handle_anchor_placement`'s gating wait (`_bet_intercepted_event`) before triggering hedges — replaced by the standby intercept loop.

## Files Touched (Estimate)

**Backend:**
- `arnold/mirror/arb_runner.py` — major rewrite (replace auto-hedge with load+stream+intercept loop)
- `arnold/mirror/provider_runner.py` — wire SlipOddsStream into value-bet flow
- `arnold/mirror/slip_odds_stream.py` — new file
- `arnold/mirror/workflows/base.py` — add `read_slip_odds`, `update_slip_stake` to base contract
- `arnold/mirror/workflows/{kambi,gecko_v2,altenar,spectate,comeon,interwetten,...}.py` — implement `read_slip_odds` per workflow
- `arnold/mirror/workflows/{pinnacle,polymarket,cloudbet,kalshi}.py` — implement `read_slip_odds` per workflow
- `arnold/mirror/router.py` — register new SSE event types if any new endpoints needed (most reuse existing routes)
- `backend/src/db/models.py` — `slip_odds_ticks` table + `_run_pg_migrations` entry (optional, gated by env)

**Frontend:**
- `arnold/frontend/src/pages/PlayPage.tsx` — new SSE handlers, status-only arb card layout, live-edge display for value bets
- `arnold/frontend/src/hooks/useMirrorStream.ts` — confirm new event types pass through (likely no change; opaque pass-through)

**Server:**
- `backend/src/config/providers.yaml` — relax cooldowns per the table above
- `backend/src/analysis/scanner.py` — investigate + possibly enforce 3-way "1 soft + N unlimited" constraint (deferred to plan)

## Acceptance Criteria

1. With a logged-in soft provider and a logged-in unlimited provider, starting an arb session loads slips on both tabs without firing any placement.
2. The PlayPage arb card displays a live profit% number that updates as slip odds change on either tab.
3. Clicking Place inside the soft mirror tab is intercepted and recorded as the anchor; the counter slip's stake field updates to match the actual placed anchor stake.
4. Clicking Place inside the counter mirror tab is intercepted and linked to the same `arb_group_id` as the anchor.
5. If the soft placement is rejected, the runner pops the next-best opp and rebuilds slips on every tab without restarting the session.
6. A 3-way arb opp loads slips on three tabs (1 soft + 2 unlimited) and the alignment math correctly considers all three.
7. Extraction match-rate metric (`/health/extraction`) does not degrade after cadence relaxation.
8. With `SLIP_ODDS_LOGGING=true`, slip-odds ticks are persisted to `slip_odds_ticks` and queryable via the postgres MCP.

## Open Items for Implementation Plan

- Per-workflow `read_slip_odds` implementation: each workflow's slip widget DOM differs. Plan tasks should be one task per workflow with discovery + implementation steps.
- 3-way arb scanner output investigation (confirm shape, decide where to enforce the soft-count constraint).
- SlipOddsStream throttling parameters: 1Hz poll + 0.5s broadcast throttle are starting defaults; tune from `slip_odds_ticks` data.
- Migration order: backend rewrite → workflow `read_slip_odds` per provider → frontend handlers → cadence relaxation last (lowest risk to roll back).
