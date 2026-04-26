# Betinia Anchor + Pinnacle Counter Arb (Plan A)

**Date:** 2026-04-26
**Branch:** `feat/slip-odds-architecture`
**Status:** Spec — pending implementation plan

## 1. Goal

Wire the existing semi-automated arb pipeline end-to-end for a single anchor/counter pair: **Betinia (Altenar)** as the soft anchor and **Pinnacle** as the unlimited counter. The user opens both tabs; the mirror handles navigation, slip prep, realtime odds streaming, continuous re-ranking, and per-leg green/red ready states. The user manually clicks Place in each tab. The UI records each placement to the DB under one shared `arb_group_id`. No auto-placement.

## 2. Why now / motivation

- `ArbRunner` v2 emits new SSE events (`arb_legs_loaded`, `arb_alignment`, `arb_anchor_placed`, etc.) but `PlayPage.tsx` is still subscribed to the old event names (`arb_bet_ready`, `arb_hedge_placing`, etc.) — the UI is currently dark for v2.
- Only Altenar implements `read_slip_odds` + `update_slip_stake`. Without a Pinnacle implementation, ArbRunner can't stream the counter leg, can't compute a meaningful `all_green` gate, and can't re-write the counter stake when the anchor places at a different size than planned.
- Cloudbet, Polymarket, Kalshi, Kambi, Gecko, etc. are explicitly out of scope for this spec; they get added in follow-on specs once the end-to-end loop is proven on the simpler Betinia+Pinnacle pair.

## 3. Architecture

```
ArbRunner (per soft anchor — here: betinia)
├── Top-opp watcher loop  ── re-fetches /api/opportunities/arb-workflow every 5s,
│                            re-ranks; if a different opp now beats the loaded
│                            one by >= HYSTERESIS_PCT (0.5%), emit arb_dethroned
│                            and reload all legs against the new opp
├── Anchor leg (betinia/altenar)
│   ├── navigate_to_event()
│   ├── prep_betslip()
│   └── SlipOddsStream (1Hz read_slip_odds via WSDK localStorage)
├── Counter leg (pinnacle, NEW workflow)
│   ├── navigate_to_event()
│   ├── prep_betslip()      ── click outcome → adds to Pinnacle slip
│   └── SlipOddsStream (1Hz read_slip_odds — selectors from discovery)
├── Green Gate
│   ├── per-leg slip_state ∈ {loading, green, red}
│   └── arb_state ∈ {loading, ready, drift, dethroned}
└── Manual placements (both legs)
    ├── Anchor click in Betinia → XHR intercepted → arb_anchor_placed → record
    ├── Stake re-derived from anchor actual_stake/odds → pinnacle.update_slip_stake
    ├── Counter click in Pinnacle → XHR intercepted → arb_hedge_placed → record
    └── arb_complete

PlayPage UI
├── Subscribes to: arb_legs_loaded, arb_alignment, arb_anchor_placed,
│                  arb_anchor_rejected, arb_hedge_placed, arb_hedge_failed,
│                  arb_dethroned, arb_complete
├── Per-leg row: provider | live_odds | planned_odds | drift% | live_stake | dot
└── Banner: profit% + next-action prompt
```

## 4. Components

### 4.1 Pinnacle workflow (NEW: `arnold/mirror/workflows/pinnacle.py`)

Discovery-first. **No code lands until** `docs/superpowers/specs/2026-04-26-pinnacle-discovery.md` is written and committed. Discovery covers:

- Login state detection (cookie / DOM / API call)
- Balance scrape (DOM and/or XHR endpoint)
- History endpoint(s) for sync_history
- Event URL pattern + how to navigate from a known sport/league/team pair
- Outcome-add interaction: button selector(s) per market type, framework (React state? plain DOM?)
- Slip storage location: localStorage key, sessionStorage, in-memory React store, or pure DOM
- Slip odds element: selector path that reflects live drifting price
- Stake input element: selector + how reactivity is wired (controlled input, store dispatch, etc.)
- Place button selector + the placement XHR (URL pattern, request body, response shape including bet_id and stake-limit error codes)

Then implement these methods (override defaults from `base.ProviderWorkflow`):

| Method | Purpose |
|---|---|
| `find_tab` | Inherit base — domain match |
| `check_login` | DOM/cookie/API check from discovery |
| `sync_balance` | Discovery-driven |
| `sync_history` | Discovery-driven; populate `HistoryEntry[]` |
| `navigate_to_event(page, bet)` | URL or DOM nav to event page |
| `prep_betslip(page, bet, stake)` | Click outcome → slip filled → stake set → `PlacementResult(status="prepped")` |
| `read_slip_odds(page) -> float \| None` | Scrape current displayed slip price (1Hz polled) |
| `update_slip_stake(page, stake) -> bool` | Re-write stake field; return True on success |
| `parse_placement_response`, `parse_placement_status` | Extract bet_id + success/error from placement XHR |

Existing `arnold/mirror/workflows/strategies/pinnacle.py` (intel/strategy) is **not** the same thing — that's a price-quoting/auto-placement strategy. The new `pinnacle.py` workflow is a Playwright DOM workflow for the user-driven mirror flow. Both will coexist.

### 4.2 ArbRunner changes (`arnold/mirror/arb_runner.py`)

#### Continuous re-ranking ("dethrone")

- After `_load_all_legs(opp)` succeeds and we enter `STATE_STANDBY`, spawn `_top_opp_watcher_task = asyncio.create_task(self._watch_top_opp())`.
- `_watch_top_opp` loops every `RERANK_INTERVAL_S = 5.0`:
  - `opps = await self._fetch_arb_opps()`
  - if not opps or `opps[0]["opp_key"] == self.current_opp_key`: continue
  - if `opps[0]["guaranteed_profit_pct"] - self._current_recomputed_profit_pct >= DETHRONE_HYSTERESIS_PCT (0.5)`:
    - `self._broadcaster.publish("arb_dethroned", {"arb_group_id": self.current_arb_group_id, "old_profit": self._current_recomputed_profit_pct, "new_profit": opps[0]["guaranteed_profit_pct"]})`
    - Stop streams, clear counter events, set `self._dethroned_to = opps[0]`, then `self._anchor_event.set()` to unblock the awaiter
- `_stream_and_await_anchor` checks `self._dethroned_to is not None` after the wait returns; if set, returns `None`. The outer `_run` loop sees `anchor_result is None`, takes the dethrone branch (does NOT increment `rejected` stats), and re-enters `_load_all_legs(self._dethroned_to)`.
- Watcher cancelled when leaving `STATE_STANDBY` (entering `STATE_AWAITING_HEDGES`, completing, or stopping).

`opp_key` does not exist server-side today — add it as `f"{event_id}|{market}|{point or ''}|{outcome}"` computed in `_load_all_legs` and stored on `self.current_opp_key`. Backend doesn't need to change; comparison is local.

#### Green-gate

- Add `_compute_slip_state(planned_odds: float, live_odds: float) -> str`:
  - `"red"` if `live_odds is None or live_odds <= 0`
  - `"red"` if `live_odds < planned_odds * (1 - LEG_DRIFT_TOL_PCT)` where `LEG_DRIFT_TOL_PCT = 0.01`
  - else `"green"`
- In `_on_leg_odds_change`:
  - Compute per-leg `slip_state` for anchor + each counter using their `_planned_odds` (captured in `_load_all_legs`)
  - Compute `current_recomputed_profit_pct = recalc_profit_pct(anchor_live, counter_live[])` and store on `self._current_recomputed_profit_pct`
  - Compute `all_green = all(s == "green" for s in slip_states) and current_recomputed_profit_pct > 0`
  - Add `slip_state`, `planned_odds`, `drift_pct` to each `legs[]` entry on the `arb_alignment` event; add top-level `all_green` and `current_profit_pct`
- In `on_bet_intercepted` (anchor-side): if `not self._all_green` at intercept time, still call `_anchor_event.set()` so `_stream_and_await_anchor` proceeds, but record `intercepted_while_red=True` on the placement; `_stream_and_await_anchor` then emits `arb_anchor_rejected` with `reason="placed_while_red"` and **does not** record the bet to DB. (Pending-loop will reconcile via provider history later.)

This intentionally stays advisory: we cannot stop a user click on the live site. We refuse to register the placement as part of the arb_group when the gate is red.

#### Counter slip stake propagation

Already mostly wired in `_update_counter_slips_and_await_hedges`. The gap: today it reads `self._streams[pid]._page` (private). Refactor to expose `SlipOddsStream.page` as a public attribute so we don't poke privates. No behavior change — just a rename of the underscore field.

### 4.3 PlayPage UI (`arnold/frontend/src/pages/PlayPage.tsx`)

#### Remove old event handlers

Delete handlers for: `arb_bet_ready`, `arb_hedge_placing`, `arb_unhedged`. Keep `arb_hedge_failed` (still emitted for placement-XHR failures). Keep `arb_complete` (already handled).

#### Add new event handlers

| Event | UI effect |
|---|---|
| `arb_legs_loaded` | Set `arbLegs` state from `data.legs[]`; clear prior `arbAlignment`; show "Loading…" dots per leg |
| `arb_alignment` | Update each leg's `live_odds`, `drift_pct`, `slip_state`, `current_stake`; update top banner `profit_pct`, `all_green` |
| `arb_anchor_placed` | Mark anchor leg `placed: true`, show "Place counter on Pinnacle" prompt, freeze planned/live for the anchor row |
| `arb_anchor_rejected` | Toast with `reason`; clear arb state |
| `arb_hedge_placed` | Mark counter leg `placed: true` |
| `arb_hedge_failed` | Toast with `reason`; flag the counter leg in red |
| `arb_dethroned` | Toast "switched to higher-edge opp"; reset leg list to be repopulated by next `arb_legs_loaded` |
| `arb_complete` | Brief "✓ arb_group complete" banner; clear after 5s |

#### New per-leg row component

```
[●green | provider | live 2.05 (planned 2.10, -2.4%) | stake 250 | placed/awaiting]
```

A simple inline render inside the existing PlayPage layout, **not** a new component file. PlayPage is already 1434 lines — a follow-up refactor can extract this once the surface settles.

### 4.4 Backend changes

None for this spec. The runner reads `/api/opportunities/arb-workflow` which already returns `guaranteed_profit_pct`-sorted opps with full leg detail.

## 5. Data flow

1. User opens Betinia in mirror, logs in → `_wait_for_login` returns True
2. `_detect_pending` reconciles outstanding bets
3. Outer loop: `opps = await _fetch_arb_opps()` → pick `opps[0]`
4. `_load_all_legs(opp)`: parallel `find_tab` + `navigate_to_event` + `prep_betslip` for Betinia (anchor) and Pinnacle (counter). Start `SlipOddsStream` per leg. Capture `_planned_odds` per leg. Emit `arb_legs_loaded`.
5. Spawn `_top_opp_watcher_task`; enter `STATE_STANDBY`
6. Streams push odds → `_on_leg_odds_change` recomputes per-leg `slip_state` + `current_recomputed_profit_pct` + `all_green` → throttled `arb_alignment` broadcast (existing 0.5s throttle)
7. Branches:
   - **Dethroned path**: watcher detects new top opp with edge ≥ +0.5pp → emit `arb_dethroned`, stop streams, set `_dethroned_to`, kick `_anchor_event` → `_stream_and_await_anchor` returns None → outer loop reloads with `_dethroned_to` as the opp
   - **Anchor placed path**: user clicks Place on Betinia → Altenar XHR intercepted by `MirrorBrowser` → routed via `play_loop.on_bet_intercepted` → `runner.on_bet_intercepted(body, request_body)` → `_stream_and_await_anchor` returns `{actual_stake, actual_odds, body}` → `arb_anchor_placed` → `_record_bet(anchor)` → enter `STATE_AWAITING_HEDGES`
8. `_update_counter_slips_and_await_hedges`: re-derive counter stake from `actual_stake/actual_odds` → `pinnacle.update_slip_stake(page, new_stake)` → user sees the Pinnacle slip's stake field auto-fill → user clicks Place on Pinnacle → XHR intercepted → routed via `play_loop.on_bet_intercepted` → `runner.on_counter_bet_intercepted("pinnacle", body, request_body)` → `_counter_events["pinnacle"].set()`
9. All counter events fired → `arb_hedge_placed` per leg → `_record_bet(counter)` with same `arb_group_id` → `arb_complete` → loop back to step 3

## 6. Error handling

| Failure | Behavior |
|---|---|
| Pinnacle tab not open | `_load_all_legs` returns False, skip opp, broadcast `bet_skipped` with `reason="counter_no_tab"` |
| Pinnacle login expired | `prep_betslip` returns `status != "prepped"` → skip opp; user re-logs and next cycle picks it up |
| `read_slip_odds` returns None for ≥ 10 consecutive ticks | Treat as `red` with `reason="stale_slip"`; surface in alignment but otherwise no action — user sees the issue and can stop or re-prep |
| Anchor placed but counter went red while user is still in counter slip | Counter row stays red; user can choose to place anyway (recorded normally) or close the slip; if they walk away, `arb_complete` never fires and the anchor sits as a singleton — pending-loop reconciles |
| Counter intercepted but server returned error (`parse_placement_status.success=False`) | Emit `arb_hedge_failed` with reason; do NOT record; mark arb_group as unhedged-pending in UI; user can retry or close |
| `arb_dethroned` fired during `STATE_AWAITING_HEDGES` | Watcher is already cancelled — cannot happen |
| Stream tasks exception | Existing `_log_task_exception` callback logs and continues |
| Top-opp watcher exception | Caught and logged; watcher loop continues (does not kill runner) |

## 7. Testing

- **Unit (`arnold/tests/mirror/test_arb_runner.py`)**:
  - `_compute_slip_state(planned, live)` for green/red boundaries (drift = 0%, -0.99%, -1.0%, -1.01%, live=None, live=0)
  - Dethrone hysteresis: same opp → no fire; new opp +0.4pp → no fire; new opp +0.5pp → fire; new opp +1.0pp → fire
  - `arb_alignment` payload includes `slip_state`, `drift_pct`, `all_green`, `current_profit_pct`
- **Unit (`arnold/tests/workflows/test_pinnacle_slip.py`)**:
  - `read_slip_odds` against captured DOM/storage fixtures from discovery
  - `update_slip_stake` against captured slip state
  - `parse_placement_status` for success and known error shapes
- **Integration**: mock SlipOddsStream emits `green → drift → green → red` sequence; assert alignment events match
- **Live smoke**: one round in dev — open Betinia + Pinnacle, walk through one arb manually, confirm DB has two `bets` rows sharing one `arb_group:<id>` note
- **Regression**: existing `arnold/tests/workflows/test_altenar_slip.py` keeps passing

## 8. Out of scope (explicit)

- Cloudbet, Polymarket, Kalshi as counter legs (separate specs)
- Auto-place Pinnacle (deferred until manual flow proven)
- Kambi, Gecko, browser_soft anchor support (separate specs)
- `arb_unhedged` recovery flow / partial-arb cancellation (separate spec)
- Automatic max-stake learning from Pinnacle limit responses (separate spec)
- Refactoring `PlayPage.tsx` (already large) into smaller components

## 9. Open questions / risks

- **Pinnacle slip framework unknown until discovery.** If the slip is a fully-React component with no observable storage and reactive inputs that reject programmatic value writes, `update_slip_stake` may need to dispatch synthetic input events with React's hidden value setter — same pattern as some Gecko_v2 patches. Flagged for discovery to identify.
- **Pinnacle account jurisdiction** may show different markets / different event IDs vs. the Pinnacle API source the server uses for arb opps. Discovery must verify event-page URLs are reachable from the soft-side `event_id` — if they're not, we need an event mapping step. (Spec assumes URL is derivable; if not, the runner will fail at `navigate_to_event` and we re-spec.)
- **Re-rank thrash.** 5s polling + 0.5pp hysteresis was picked by intuition, not data. If we observe rapid dethrone churn during smoke, raise the interval and/or hysteresis.

## 10. Constants summary

| Constant | Value | Where |
|---|---|---|
| `RERANK_INTERVAL_S` | 5.0 | `arb_runner.py` |
| `DETHRONE_HYSTERESIS_PCT` | 0.5 | `arb_runner.py` |
| `LEG_DRIFT_TOL_PCT` | 0.01 (1%) | `arb_runner.py` |
| `_ALIGNMENT_BROADCAST_THROTTLE_S` | 0.5 (existing) | `arb_runner.py` |
| Stale-slip ticks before red | 10 | `arb_runner.py` (new counter on the stream) |
