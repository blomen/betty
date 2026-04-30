# Run-Gate for Provider Bet Placement

**Date:** 2026-04-30
**Status:** Design

## Problem

Today, clicking a provider card on the Play page (`startSkin(pid)`) does the entire workflow in one shot: open tab → wait for login → settle pending → check daily cap → **immediately enter the bet placement loop**. The user has no chance to interleave actions — e.g. open three soft books for a planned arb, then play value bets afterward — because every selected provider starts firing value bets the moment login completes.

The user wants step-by-step control: opening a provider should bring it through login + balance sync + pending settlement *automatically*, but bet placement must wait for an explicit user signal per provider.

## Goals

1. Selecting a provider opens its tab and runs login → balance sync → pending settlement automatically. This includes continuous passive sync (balance + pending) while the provider sits idle.
2. Bet placement (value or arb, depending on which sub-tab the card lives in) is gated behind a per-provider Run signal.
3. Run is a **toggle**: pressing it again pauses placement back to ready without tearing down the tab/login/balance.
4. A "ready" (paused) provider still auto-serves as an arb counter when another provider's anchor fires (`on_bet_intercepted` continues to route counter-bets to non-running providers).
5. The card itself is the click region; no new button is added. State is communicated via card color.

## Non-Goals

- No global "Run all" or per-sub-tab Run.
- No change to ArbRunner's anchor/counter coordination logic — only the entry into its bet loop is gated.
- No change to the existing settlement / placement / pending-loop pipelines.
- Pinnacle is not playable through this UI; the gate applies to soft providers + the unlimited group (polymarket, cloudbet, kalshi). Pinnacle as a sharp source is unaffected.

## Card Color States

Single click region per provider card. The existing card already toggles selected/unselected; we extend it with five active-session colors.

| State | Color | Trigger | Click behavior |
|---|---|---|---|
| Idle / unselected (funded) | **amber** (unchanged) | default | Click → open tab → "Tab open" |
| Idle / unselected (unfunded) | grey (unchanged) | default | non-actionable |
| Tab open, not logged in | **blue** | tab opened, `check_login` polling | Click → deselect / close session |
| Logged in, syncing/settling | **cyan** | `check_login` true; balance sync + `_detect_pending` running | Click → deselect / close session |
| Ready (paused) | **yellow** | sync + settle complete, daily cap OK, bet loop gated | Click → toggle to Running |
| Running | **green** | bet loop active — popping queue, navigating, awaiting Place/Skip | Click → toggle back to Ready |

Click while amber/grey opens the session. Click while blue/cyan deselects (closes the session). Click while yellow/green toggles the run gate without touching the session. Long-press / right-click / explicit close affordance for full deselect from yellow/green can be added later if needed; for now toggling Run from green back to yellow leaves the session running, and a separate "deselect" path is reached only by stopping the entire play loop (existing behavior on the last selected provider).

## Backend Architecture

### New runner state

Add `STATE_READY_TO_RUN = "ready_to_run"` to `play_loop.py`. Imported and re-used by both `ProviderRunner` and `ArbRunner`.

### Per-runner Run gate

Each runner (both `ProviderRunner` and `ArbRunner`) gains:

```python
self._run_event: asyncio.Event  # set = run; cleared = pause
```

Initial state: cleared (paused). The gate sits between step 4 (daily cap check) and the existing bet loop entry.

### Flow change in `_run` (both runners)

```
1. STATE_PROVIDER_OPENING — find tab
2. STATE_LOGIN_WAITING — wait for login
3. STATE_SETTLING — _detect_pending (one-shot at startup)
4. (Daily cap check)
5. NEW: STATE_READY_TO_RUN — broadcast provider_ready; await self._run_event.wait()
6. (Existing bet loop body — unchanged)
```

### Pause behavior (toggling Running → Ready)

When a running runner is paused mid-loop:

- Outside any active bet (between iterations): the loop checks `self._run_event.is_set()` at the top of each iteration; if cleared, transitions back to `STATE_READY_TO_RUN`, broadcasts `provider_ready`, and awaits the event again.
- Mid-bet (currently navigating, prepping, or at READY waiting for user): finish or skip the active bet, then drop back to `READY_TO_RUN`. Concretely, pausing while at `STATE_READY` waiting on a Place/Skip simulates a Skip on the current bet, then idles. We don't tear down the slip stream mid-iteration — just let the iteration unwind naturally.

### Continuous passive sync while ready

Today, `_detect_pending` runs once at startup. Goal #1 also requires continuous sync while sitting at `READY_TO_RUN`.

Two-pronged approach:
- Balance sync: an asyncio task spawned per runner when entering `READY_TO_RUN`, that calls `workflow.fetch_balance` (or the same path used by `_fetch_balance` in the runner) every `READY_BALANCE_SYNC_INTERVAL_S = 60`. Cancelled when the runner exits ready (either to running, to teardown, or on stop).
- Pending settlement: piggyback on the existing global `pending_loop` — it already short-circuits when the browser isn't up, and runs site-wide. While ready, the per-runner balance task also calls `_detect_pending` every `READY_PENDING_SYNC_INTERVAL_S = 300` (5 min). Conservative interval since settlement events are infrequent and `_detect_pending` is heavier than balance fetch.

Both tasks are cancelled the moment the runner enters its bet loop, to avoid contending with workflow.navigate_to_event for the same browser tab.

### Counter-bet routing (unchanged)

`play_loop.on_bet_intercepted` continues to route counter-bets to runners awaiting hedges, regardless of `_run_event`. The arb counter path checks `runner.state` for `STATE_AWAITING_HEDGES`, not the run gate — and `STATE_AWAITING_HEDGES` is only entered after the *anchor's* Run was pressed. So a yellow (Ready) provider can still serve as a counter for another provider's anchor without contradiction.

### API surface (`/mirror/router.py`)

New endpoints:

- `POST /mirror/play/run/{provider_id}` — sets `_run_event` for that runner. 200 if found and in `STATE_READY_TO_RUN`, 409 otherwise.
- `POST /mirror/play/pause/{provider_id}` — clears `_run_event`. 200 if running, 409 otherwise.
- (Optional, deferrable) `POST /mirror/play/toggle/{provider_id}` — single endpoint that flips the gate.

Existing endpoints unaffected: `/mirror/play/start`, `/mirror/play/stop`, `/mirror/play/skip`, `/mirror/play/status`. The frontend calls `startMirror` + `openTab` + `startPlayLoop` exactly as today; the only difference is that `startPlayLoop` no longer auto-enters bet placement — that requires the new `run` call.

### SSE events

New events emitted via `MirrorBroadcaster`:

- `provider_ready` — `{provider_id, state: "ready_to_run", balance, placed_today, daily_cap}` — emitted on entering `STATE_READY_TO_RUN` (whether from initial settle or from pause).
- `provider_running` — `{provider_id}` — emitted on `_run_event` set, just before the bet loop iteration begins.

## Frontend Architecture

### `startSkin(pid)` no longer fires placement

Today `startSkin` calls `startMirror` → `openTab` → `startPlayLoop` and the runner immediately enters placement. After this change, `startPlayLoop` still creates the runner, but the runner stops at `STATE_READY_TO_RUN`. `startSkin` itself is unchanged.

### New per-card click handler when ready/running

`PlayPage.tsx`:

```ts
const onCardClick = (pid: string) => {
  const state = providerCardState[pid] // derived from SSE + status poll
  if (state === 'idle') return startSkin(pid)
  if (state === 'tab_open' || state === 'logged_in_syncing') return deselectProvider(pid)
  if (state === 'ready_to_run') return api.runProvider(pid)   // yellow → green
  if (state === 'running')      return api.pauseProvider(pid) // green  → yellow
}
```

### State derivation

Card state is derived from the existing `loopProviderStatus[pid].state` field returned by `/mirror/play/status`, plus SSE events. The five card colors map directly onto the runner's existing state machine:

| Runner state | Card state | Color |
|---|---|---|
| (no runner) | idle | amber/grey |
| `provider_opening` / `login_waiting` | tab_open | blue |
| `settling` | logged_in_syncing | cyan |
| `ready_to_run` (NEW) | ready_to_run | yellow |
| `navigating` / `ready` / `placing` | running | green |

### Status polling

Existing 1s `/mirror/play/status` poll already reports `loopProviderStatus[pid].state`. Add the new state to the union type and color-class mapping. No new polling endpoint needed.

## Migration / Compatibility

The default behavior changes: previously, selecting a provider auto-started placement; now it stops at ready. This is the user-requested behavior change, not a regression — but anyone with muscle memory of "select = bet" needs to know about the new yellow→green press.

To make this discoverable:
- The yellow card displays a small "Press to run" pill in its corner.
- The first time a user reaches yellow in a session, a one-line toast explains "Click again to start placing bets" (dismissable; suppressed for the rest of the session).

No DB or stored-config changes. No backend API removal — only additions.

## Testing

Backend unit tests:
- Runner reaches `STATE_READY_TO_RUN`, `_run_event` cleared by default.
- Setting `_run_event` advances runner into bet loop; clearing it drops back to `STATE_READY_TO_RUN` between iterations.
- Counter-bet routing: a runner at `STATE_READY_TO_RUN` correctly serves as a counter for another runner's anchor.
- Continuous-sync tasks cancel cleanly on transition into bet loop and on `stop()`.

Manual frontend test:
- Open three providers; verify each card cycles blue → cyan → yellow without firing bets.
- Press one to green; verify only that one starts placing.
- Press a green back to yellow mid-bet; verify the active bet finishes/skips and the card returns to yellow.
- Anchor an arb on one provider; verify yellow counters auto-fire hedges.
