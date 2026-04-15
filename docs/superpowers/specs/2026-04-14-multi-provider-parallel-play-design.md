 # Multi-Provider Parallel Play

**Date**: 2026-04-14
**Scope**: FirevSports Play tab — enable opening and playing multiple providers simultaneously

## Problem

The play loop currently enforces single-provider-at-a-time: one global state machine, one active bet, one `current_provider` variable. When the queue hits a bet for a different provider, the loop breaks and waits for the user to manually start the next provider. This wastes time — the user must open, login, settle, and start each provider sequentially.

## Goal

Open multiple provider tabs at once, login to all, the loop navigates all of them to their next bet simultaneously, user places manually on whichever browser tab they want, interceptor records and advances that provider to its next bet.

## Design

### Architecture: ProviderRunner + PlayCoordinator

Replace the monolithic `PlayLoop._run()` with two layers:

**ProviderRunner** — one per active provider, runs as an independent asyncio task. Each runner owns its own state machine and processes bets for its provider from a shared cluster queue.

**PlayCoordinator** — replaces `PlayLoop`. Manages shared state: cluster queues, dedup set, provider runners. Spawns/stops runners. The existing `PlayLoop` class is refactored into this role.

### Per-Provider State Machine (ProviderRunner)

Each runner independently cycles through:

```
opening → login_waiting → settling → [navigating → ready → placing] (loop) → done
```

1. **opening** — find tab in browser context
2. **login_waiting** — wait for login (interceptor detects via balance)
3. **settling** — sync history, detect settlements
4. **navigating** — pop next bet from cluster queue, navigate to event
5. **ready** — event page loaded, user can place on browser tab
6. **placing** — interceptor caught bet, recording to DB
7. Back to step 4 (next bet) or **done** (queue empty / daily cap)

Each runner runs in its own `asyncio.Task`. Runners don't block each other.

### Queue Management

- Bets loaded once by the coordinator, partitioned into **per-cluster deques**
- Runners in the same cluster (e.g., betsson + nordicbet = `gecko_betsson`) pop from the same deque
- **Dedup on placement**: when a bet is placed on provider X for event+market Y, coordinator removes matching bet from the cluster deque (siblings don't navigate to an already-placed bet)
- **Cross-cluster independence**: Kambi cluster queue is unaffected by Gecko cluster placements
- **Standalone providers** (not in a cluster) get their own single-provider deque

### Interceptor → Runner Signaling

Currently `on_bet_intercepted()` sets a single `_bet_intercepted_event`. With multiple runners, the interceptor must signal the **correct runner**.

Approach: each ProviderRunner creates its own `asyncio.Event`. The coordinator maintains a `dict[str, ProviderRunner]` keyed by provider_id. When `on_bet_intercepted(provider_id, body)` fires:
1. Look up runner by provider_id
2. Set that runner's `_bet_intercepted_event`
3. Store the intercepted body on the runner

### SSE Events

All events already include `provider_id`. No new event types needed — the frontend just needs to handle multiple providers being in different states simultaneously.

Add a `provider_status` event broadcast periodically (or on state change) with all active runners' states:
```json
{
  "providers": {
    "betsson": {"state": "ready", "bet": {...}},
    "unibet": {"state": "navigating", "bet": {...}}
  }
}
```

### Frontend Changes

**Provider selection**: change from single-select (`activeSkin`) to multi-select (`activeProviders: Set<string>`). Clicking a provider skin toggles it on/off. Multiple providers across different clusters can be active.

**Start**: sends list of selected provider IDs + all their cluster bets + balances. One API call starts the coordinator which spawns runners for all selected providers.

**Status display**: replace the single "current bet ready" card with a per-provider status row:
```
[betsson: ready - Real Madrid v Barcelona ML @2.10]
[unibet: navigating - Liverpool v PSG Total O2.5 @1.85]
[spelklubben: settling...]
```

Minimal UI — user interacts on browser tabs directly. The status rows are informational.

**Stop**: stops all runners.

**Skip**: per-provider skip button on each status row (optional — interceptor timeout can auto-advance).

### API Changes

**`POST /mirror/play/start`** — change `provider_id: str | None` to `provider_ids: list[str]`

**`POST /mirror/play/skip`** — add `provider_id: str` param to skip a specific provider's current bet

**`POST /mirror/play/place`** — add `provider_id: str` param (though interceptor handles this automatically)

**`GET /mirror/play/status`** — return per-provider states instead of single global state

### Browser Tab Management

No changes needed. Multiple provider tabs already work — each provider has its own domain, `find_tab()` matches by domain. The user opens multiple tabs (frontend calls `openTab` for each selected provider).

## Files Changed

| File | Change |
|------|--------|
| `firevsports/mirror/play_loop.py` | Refactor into PlayCoordinator + ProviderRunner |
| `firevsports/mirror/router.py` | Update API endpoints for multi-provider |
| `firevsports/mirror/browser.py` | Route `on_bet_intercepted` to correct runner |
| `firevsports/frontend/src/pages/PlayPage.tsx` | Multi-select providers, per-provider status rows |
| `firevsports/frontend/src/hooks/useApi.ts` | Update `startPlayLoop` to send provider_ids list |

## What Stays The Same

- Browser lifecycle, tab opening, network interception
- Workflow classes (Kambi, Gecko, Altenar, etc.)
- Bet recording to server DB
- Cluster membership definitions
- Settlement sync logic (per-provider, just runs in parallel now)
- Pending loop (separate system, unaffected)

## Risks

- **Race conditions on cluster queue**: two runners in the same cluster popping simultaneously. Mitigation: use `asyncio.Lock` per cluster deque (asyncio is single-threaded so this is lightweight).
- **Interceptor routing**: if two providers place bets near-simultaneously, interceptor must correctly match response → provider. Already works — interceptor detects provider from page URL/domain.
- **UI complexity**: showing N providers simultaneously. Mitigation: minimal status rows, user interacts on browser tabs not in the UI.
