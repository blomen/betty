# Polymarket top-edge convergence loop

**Date:** 2026-04-30
**Scope:** `arnold/mirror/provider_runner.py` (polymarket-only path), `arnold/mirror/play_loop.py`
**Status:** design — ready for plan

## Problem

The polymarket runner sometimes sits at READY on a non-top-edge bet while a higher-edge opportunity is visible in the queue. Concrete example (screenshot 2026-04-30):

- Top of queue: `HereWeGoAgain v Crashers` Under 2.5 — cached edge **+23.0%**
- Runner state: READY on `Vitality v G2` ML G2 — live edge **+19.9%**
- The `HereWeGoAgain` bet is still in the queue (visible at row 1) but the runner does not navigate to it.

This is wrong. The runner should always be sitting on the bet whose live edge is currently the top of the queue.

## Root cause (current behavior)

After `prep_betslip` the runner immediately broadcasts `bet_ready` and enters the READY wait. Two watchers run in parallel during READY:

1. `_watch_polymarket` — polls `check_live_price` every 1s, updates `live_edge_holder[0]`, broadcasts `live_price` SSE. **Does not skip** on its own.
2. `_watch_for_better` — polls the queue every 3s. If `queue_top >= live_edge + 2pts` (DETHRONE_HYSTERESIS_PCT), fires `_skip_event`.

When dethrone fires, the active bet is **dropped** from the runner. It's gone from the queue and only re-appears 10s later via `_refresh_batch` from `/api/opportunities/play/batch`, with the **cached** batch edge — not the just-measured live edge. Hard-fail prep results (`navigation_redirected`, `no_cent_button_matched`, `event_closed`, `click_failed`) follow the same drop-without-reinsert pattern.

Net effect: there are gaps where the runner is sitting on a non-top bet because the actual top has been transiently dropped from the queue.

## Goal

Polymarket runner always sits at READY on the bet whose **live edge** is currently the top of the queue. When the active bet's live edge drifts below another bet's edge (with hysteresis appropriate to the phase), re-insert the active bet at its **live**-measured edge into the cluster queue, pop the new top, navigate, repeat. Keep iterating until the bet on screen genuinely is the top.

## Non-goals

- Generalizing to Pinnacle, Cloudbet, Kalshi. Pinnacle is autonomous-placement (no user wait); Cloudbet/Kalshi don't show the same intra-event price drift pattern. Scoping the change to polymarket keeps the blast radius small. Generalize later if the same drift shows up on other UNCAPPED providers.
- Changing the soft-book ArbRunner. Out of scope.
- Replacing `_refresh_batch` or the cached-vs-live tradeoff for non-active queue bets. Active bet uses live edge; queue bets stay on cached edge until they themselves become active (decision A).

## Design

### State machine

Insert a **convergence phase** between `NAVIGATING` and `READY`. No new state constant — the convergence checks happen inside the existing post-`prep_betslip` block, before broadcasting `bet_ready`.

```
pop top → NAVIGATING → prep_betslip → [convergence loop]
                                         │
                                         ├─ live edge ≥ queue top  → bet_ready → READY
                                         └─ live edge < queue top  → re-insert at live edge,
                                                                     pop new top → NAVIGATING
```

### Convergence loop (new)

After `prep_betslip` returns `prepped`:

1. Read `live_odds, live_edge` via `workflow.check_live_price(page, bet_ns)`.
2. Stamp `bet["edge_pct"] = live_edge` (so re-insertion uses the just-measured value).
3. Peek queue top: `top_edge = peek_top_edge(_active_key)` (excluding the active bet's own re-added entry, same exclusion the existing dethrone watcher uses).
4. **Zero-hysteresis check (convergence phase):** if `top_edge is not None and top_edge > live_edge`:
   - Push the bet back into its cluster queue (`push_bet(cluster, bet)`), which inserts and re-sorts by `edge_pct` desc.
   - Increment `convergence_iter` counter.
   - If `convergence_iter < 5`: `continue` the bet loop — pop new top, navigate, prep, re-check. Do **not** broadcast `bet_skipped` (this is internal churn; emit `bet_converging` instead — see Telemetry).
   - If `convergence_iter >= 5`: log a warning, break out of convergence and broadcast `bet_ready` on whatever we have. Let the existing READY-state dethrone (2pts hysteresis) handle further drift.
5. Otherwise (`live_edge >= top_edge` or no top): `convergence_iter = 0`, broadcast `bet_ready`, enter the existing READY wait.

`convergence_iter` lives on the runner (or in the bet loop frame), reset to 0 each time we successfully reach READY.

### READY-state dethrone (modified — re-insert instead of drop)

Existing `_watch_for_better` keeps its 2pts hysteresis (decision C: asymmetric — strict during convergence, hysteretic at READY). But the dethrone path is changed: instead of just firing `_skip_event` and broadcasting `bet_skipped` with reason "dethroned by …", it must:

1. Stamp `bet["edge_pct"] = live_edge_holder[0]` (current live edge).
2. Push the bet back into the cluster queue.
3. Set a new `_dethrone_reinsert` flag so the post-wait code path skips the `bet_skipped` broadcast (this isn't a skip, it's a re-rank) — broadcast `bet_reinserted` instead.
4. Set `_skip_event` so the wait exits and the loop pops the new top.

The `bet_intercepted` race (user clicks Place at the same instant dethrone fires) is already guarded by `if self._bet_intercepted_event.is_set(): handle_placement(...)` in the existing code — that branch wins and the placement proceeds against the dethroned bet (matches current semantics).

### Hard-fail handling (decision A: 60s TTL)

When `prep_betslip` returns `failed` with reason matching `{navigation_redirected, no_cent_button_matched, event_closed, click_failed}`, in addition to the existing `bet_skipped` broadcast and `stats["skipped"] += 1`:

- Call `_mark_recently_skipped(bet)` so `_refresh_batch` excludes it for 60s.
- Do **not** re-insert into the queue.

After 60s the bet can re-enter via the next `_refresh_batch`. If it fails again, it gets another 60s exclusion, and so on. This handles transient page-not-loaded-yet cases without permanently blacklisting bets that briefly redirect.

`prep_failed` reasons that are *not* in this list (none currently exist, but for forward compat) follow the existing path — re-insertable so a soft prep failure doesn't lose the bet.

### Queue helpers

[arnold/mirror/play_loop.py](arnold/mirror/play_loop.py): add a `_make_push_bet(cluster)` factory next to `_make_pop_bet` and `_make_peek_top_edge`. Returns a closure `push(bet)` that:

- Appends bet to `self._cluster_queues[cluster]`.
- Sorts the queue by `edge_pct` desc.
- Updates `self._queue_total`.
- Idempotent: if `(event_id, market, outcome)` already in queue, replace its `edge_pct` instead of appending a duplicate.

Wire it into `ProviderRunner.__init__` as a new `push_bet` callable parameter (parallel to `pop_bet` / `peek_top_edge`). Pass it through from `_spawn_runners`.

### Telemetry (two new SSE events)

- `bet_converging` — `{provider_id, bet, live_edge, queue_top, iteration}` — emitted on each convergence iteration. Useful for the Sports tab UI to show "checking N…" so the user understands why the screen flipped.
- `bet_reinserted` — `{provider_id, bet, old_cached_edge, new_live_edge}` — emitted when the READY-state dethrone re-inserts. This is the audit trail for "why did my +EV bet vanish from the top".

Existing `bet_dethroned` event is removed (it's redundant with `bet_reinserted` and only fired on drop, which no longer happens).

### Out-of-scope behaviors (kept as-is)

- `_recently_skipped` for **user-initiated** skips: 60s TTL, unchanged.
- Auto-skip on `live_edge < 0` in `_on_slip_change` callback: unchanged.
- `_refresh_batch` interval (10s): unchanged.
- Polymarket-specific `_watch_polymarket` task (Amount-keeper + live-edge poll): unchanged.
- Pinnacle / Cloudbet / Kalshi runners: unchanged (gated behind `if pid == "polymarket":`).

## Walkthrough — fixes the screenshot bug

Initial state: queue is `[HereWeGoAgain @ 23, Vitality @ 19.9, GameHunters @ 20.5, ...]` sorted desc.

1. `pop_bet` → `HereWeGoAgain` (top). `convergence_iter = 0`.
2. `navigate_to_event` → polymarket page loads.
3. `prep_betslip` → success, cents read = 67.61, live odds 1.48, live edge measured.
   - Case A: live edge confirms 23% → `top_edge = peek(exclude=HereWeGoAgain) = 20.5` (GameHunters), `23 > 20.5` → broadcast `bet_ready` → READY.
   - Case B: live edge tightened to 18% → `push_bet(HereWeGoAgain @ 18)`, queue is now `[GameHunters @ 20.5, Vitality @ 19.9, HereWeGoAgain @ 18, ...]`, `continue`. `convergence_iter = 1`.
4. (Case B) `pop_bet` → `GameHunters` (new top). Navigate. Prep. Read live edge.
   - If live edge confirms 20.5% → `top_edge = peek(exclude=GameHunters) = 19.9` → READY.
   - If tightened to 17% → push back, pop next, repeat.
5. Cap at 5 iterations. After that, accept whatever we have and let the 2pts READY dethrone take over. Prevents infinite churn on a flapping queue.

This eliminates the screenshot bug: `Vitality` can never sit at READY while `HereWeGoAgain` is in the queue at higher edge — convergence forces a re-pop until live edges agree with queue ordering.

## Code touchpoints

- [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py)
  - Hard-fail handler: add `_mark_recently_skipped(bet)` for the 4 failure reasons.
  - Insert convergence loop after `prep_betslip` success, gated `if pid == "polymarket":`.
  - Modify dethrone path in `_watch_for_better` to push-back-and-broadcast-`bet_reinserted` instead of dropping.
  - Add `_push_bet` constructor parameter and use it from convergence + dethrone.
- [arnold/mirror/play_loop.py](arnold/mirror/play_loop.py)
  - Add `_make_push_bet(cluster)` next to `_make_pop_bet` / `_make_peek_top_edge`.
  - Pass `push_bet=self._make_push_bet(cluster)` into `ProviderRunner(...)`.
- No changes to [arnold/mirror/workflows/strategies/polymarket.py](arnold/mirror/workflows/strategies/polymarket.py) — `_check_live_price` already returns `(live_odds, live_edge)`.

## Testing

Manual end-to-end on a live polymarket session:

1. Start play with polymarket selected. Confirm runner navigates to whatever the top-edge bet currently is.
2. Watch `bet_converging` SSE events in the browser dev console. Should see 0-3 iterations on entry, then settle at `bet_ready`.
3. Wait for a market to tighten. Confirm `bet_reinserted` fires when queue top exceeds live edge by 2pts. Confirm next bet on screen is genuinely top.
4. Force a `prep_failed` by manually closing a polymarket event (or hitting an invalid slug). Confirm the bet is excluded from queue for 60s, then can return.
5. Stress: load a queue of 20+ polymarket bets with widely varying edges. Confirm convergence cap (5) prevents infinite churn — runner eventually reaches READY even if every prep produces some live drift.

## Risks

- **Navigation cost.** Each iteration is ~3-5s. Worst case (5 iterations) is ~25s before READY. Acceptable — equivalent to a slow first navigation today, and only happens during initial convergence.
- **Live-edge accuracy.** `check_live_price` reads cents from the betslip and computes edge from `fair_odds`. If `fair_odds` is missing on the bet (rare for polymarket), the function returns `(None, None)` — convergence treats this as "no info, assume top" and proceeds to READY. Equivalent to current behavior.
- **Queue corruption.** `push_bet` mutates the cluster queue while the coordinator's `_refresh_batch` may also mutate it. Both operations run on the same asyncio event loop (no real concurrency), so no lock needed.
- **Telemetry volume.** `bet_converging` fires up to 5 times per pop. At ~20 polymarket pops per session, that's ~100 events max — negligible for local SSE.
