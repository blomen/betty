# Arb Drain Blockers — Hedge Timeout, Stake Caps, Pinnacle-Tab Arbitration, Pinnacle History

**Date:** 2026-04-30
**Branch:** TBD (`fix/arb-drain-blockers` suggested)
**Status:** Spec — pending implementation plan
**Owner:** main agent (parallel agent owns the unlimited-side / Kalshi polish lane)

## 1. Goal

Unblock the Betinia(soft)↔Pinnacle(unlimited) arb loop so that real soft-side balance drains into the unlimited side without ArbRunner getting stuck, double-driven, or leaving un-reconcilable bets behind. Four fixes, each addressing a concrete failure mode the audit identified:

1. **Hedge wait timeout** — never block forever on a counter that doesn't show up.
2. **Anchor stake cap** — respect site max-stake / `_stake_caps` learned from prior limit responses.
3. **Pinnacle-tab arbitration** — stop `ProviderRunner(pinnacle)` and `ArbRunner(*).counter=pinnacle` from fighting over the same Pinnacle slip.
4. **Pinnacle `sync_history`** — let counter placements actually reconcile against settled outcomes.

Out of scope (separate specs): expanding to Kambi/Gecko/ComeOn anchors, expanding to Polymarket/Cloudbet/Kalshi counters, fixing the server-side `counterpart_providers` query filter, replacing free-text `arb_group_id` with a structured FK column.

## 2. Why now

- The drain loop already exists end-to-end. These four bugs are what make it hang, mis-size, collide, or leak rows.
- Pinnacle is the only working unlimited counter today (Polymarket/Cloudbet/Kalshi need anchor-side adapters). Every soft anchor we add later will hit fix #3 the same way.
- The parallel agent's lane (Kalshi mirror polish) does not touch any of these files.

## 3. Architecture

```
ArbRunner (per soft anchor — betinia)
├── _load_all_legs()
│   └── (FIX #2) anchor_stake = min(balance, stake_caps[anchor], site_min..site_max)
├── _stream_and_await_anchor()
├── _update_counter_slips_and_await_hedges()
│   └── (FIX #1) gather(*counter_events, timeout=COUNTER_HEDGE_TIMEOUT_S)
│        on TimeoutError → arb_hedge_failed(reason="user_timeout") per missing leg,
│                          DO NOT _record_bet for missing legs,
│                          DO _record_bet for legs whose intercept already fired
└── (no other change)

PlayCoordinator
├── _spawn_runners()
│   └── (FIX #3) if pid==pinnacle AND any selected pid is a soft anchor:
│        spawn PinnacleSharedRunner instead of ProviderRunner
└── on_bet_intercepted(provider_id="pinnacle", ...)
    └── (FIX #3) routing: ArbRunner counter intercept ALWAYS wins over PinnacleSharedRunner

PinnacleSharedRunner (NEW)  — lives in arnold/mirror/pinnacle_shared.py
├── Same login / settle / history flow as ProviderRunner
├── Owns the Pinnacle tab in two modes:
│     mode=value      → behaves like ProviderRunner: places value bets via cluster queue
│     mode=counter    → anchored by ArbRunner; passes find_tab/page through; suspends value placement
├── State machine adds STATE_LENT_TO_ARB
└── ArbRunner asks for the page via lend_to_arb(arb_group_id) → returns Page; release_to_value() returns ownership

PinnacleMirrorWorkflow (existing)
└── (FIX #4) sync_history()
    ├── Discovery first: GET https://www.pinnacle.se/en/account/bet-history/
    │   intercept the JSON XHR (URL pattern + body shape go in discovery doc)
    ├── parse_history_entry(): map bet rows → HistoryEntry
    └── pagination fallback: walk pages until empty or 5-page cap
```

## 4. Components

### 4.1 Hedge wait timeout — `arb_runner.py`

`COUNTER_HEDGE_TIMEOUT_S = 180.0` (3 minutes — long enough for the user to switch tabs and click, short enough that a closed tab doesn't lock the runner for the rest of the session).

In `_update_counter_slips_and_await_hedges`:

```python
try:
    await asyncio.wait_for(
        asyncio.gather(*(ev.wait() for ev in self._counter_events.values())),
        timeout=COUNTER_HEDGE_TIMEOUT_S,
    )
except asyncio.TimeoutError:
    # For each unfired counter event, emit arb_hedge_failed and skip recording.
    # Counters that fired before the timeout are still recorded by the loop below.
    for leg in self._counter_legs:
        pid = leg["provider"]
        if pid not in self._counter_intercepted:
            self._broadcaster.publish(
                "arb_hedge_failed",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "counter_provider": pid,
                    "outcome": leg.get("outcome"),
                    "reason": "user_timeout",
                    "max_stake": None,
                },
            )
```

Rationale for not auto-cancelling the anchor: the anchor is already placed at the site by the time this code runs. Cancellation would require an additional cashout/void flow we don't have. Leaving the anchor un-hedged is bad but it's the existing reality; surfacing it via SSE lets the user manually hedge from another window.

### 4.2 Anchor stake cap — `arb_runner.py`

Inject `stake_caps: dict[str, float]` into `ArbRunner.__init__`. Wire from `PlayCoordinator._spawn_runners` (it already owns `self._stake_caps`).

`_load_all_legs` change:

```python
balance = self._browser.provider_data.get(self.provider_id, {}).get("balance") or 0.0
cap = self._stake_caps.get(self.provider_id)  # learned from prior limit responses (see provider_runner.py)
anchor_stake = round(min(balance, cap) if cap else balance, 2)
```

Counter caps are picked up post-anchor in `_update_counter_slips_and_await_hedges` — when a counter slip rejects with `max_stake`, the existing rejection path emits `arb_hedge_failed`; we do NOT auto-resize counters mid-flight (would unbalance the arb). User retries from the next opp.

### 4.3 Pinnacle-tab arbitration — new `PinnacleSharedRunner`

**Decision:** new class, not a flag on ProviderRunner. ProviderRunner is already complex; lending semantics belong in their own state machine.

Lives in `arnold/mirror/pinnacle_shared.py`. Inherits the public surface of ProviderRunner (`start/stop/skip/place/on_bet_intercepted/get_status`) so PlayCoordinator can treat it identically.

Public extension:
```python
async def lend_to_arb(self, arb_group_id: str) -> Page:
    """ArbRunner asks for the Pinnacle page. We pause value-bet flow,
    return the current Pinnacle tab. Idempotent if already lent."""

def release_to_value(self) -> None:
    """ArbRunner is done with the slip (success, fail, or timeout).
    We resume value-bet flow on the next loop iteration."""
```

State additions: `STATE_LENT_TO_ARB`. While in this state:
- Value bet loop blocks on `self._lent_event` (an `asyncio.Event` cleared on lend, set on release)
- `on_bet_intercepted` routes to ArbRunner via PlayCoordinator's existing counter-intercept path (no change there — the routing already prioritizes counter events over anchor events)

PlayCoordinator change in `_spawn_runners`:
```python
soft_anchors_present = any(p not in UNLIMITED_PROVIDERS for p in provider_ids)
if pid == "pinnacle" and soft_anchors_present:
    runner = PinnacleSharedRunner(...)  # else fall through to ProviderRunner
```

ArbRunner change in `_load_all_legs`:
```python
counter_pid = "pinnacle"
shared = self._pinnacle_shared  # injected via __init__
if shared:
    page = await shared.lend_to_arb(self.current_arb_group_id)
else:
    page = await wf.find_tab(self._browser.context)  # current behavior
# ... prep, stream, intercept ...
finally:
    if shared: shared.release_to_value()
```

For non-Pinnacle counters (Polymarket/Cloudbet/Kalshi when they come online), the lend dance is skipped — they're not running ProviderRunner concurrently in the soft-drain mode (the user picks soft anchors only; unlimited counters are passive tabs).

### 4.4 Pinnacle `sync_history` — `pinnacle.py`

Two-phase:

**Phase A (this spec)** — open the bet-history page in a hidden Playwright nav, intercept whatever XHR fires. The interceptor in `browser.py` already catches `bethistory|widgetreports|/api/v3/history` patterns; Pinnacle's pattern needs to be added to `_BET_HISTORY_KEYWORDS` once discovered.

**Phase B (follow-on, blocked on Phase A)** — implement `_parse_pinnacle_bet` mapping → `HistoryEntry`.

Discovery doc: `docs/superpowers/specs/2026-04-30-pinnacle-history-discovery.md` (write before code).

Stub fallback: if discovery hasn't landed yet, `sync_history` calls `_load_history_via_dom_scrape(page)`:
- `await page.goto("https://www.pinnacle.se/en/account/bet-history/")`
- `await page.wait_for_selector("table, [data-testid*=history], .bet-history-row", timeout=10000)`
- Parse visible rows: provider_bet_id (link href or row id), event name, market, odds, stake, status, payout
- Return `list[HistoryEntry]` (best-effort; returns `[]` on selector miss)

DOM scrape is a temporary bridge — it will be replaced by the XHR interceptor once Phase A's URL pattern is discovered. The DOM path is fine for reconciling small numbers of pending bets (we're pre-launch volume — tens of bets/day, not thousands).

## 5. SSE event additions

| Event | When | Payload |
|---|---|---|
| `arb_hedge_failed` (existing) | new reason `"user_timeout"` | `{arb_group_id, counter_provider, outcome, reason, max_stake}` |
| `pinnacle_lent` (new) | `lend_to_arb` called | `{arb_group_id}` |
| `pinnacle_released` (new) | `release_to_value` called | `{arb_group_id}` |

UI (`PlayPage.tsx`) gets a hedge-timeout toast and a "Pinnacle: lent to arb" indicator on the Pinnacle row when the shared runner is in `STATE_LENT_TO_ARB`.

## 6. Testing

Each fix is independently testable:

**Fix #1 — hedge timeout**
- Unit: mock counter_events that never fire → assert `arb_hedge_failed` for every counter, no `_record_bet` calls
- Live: place anchor on a Betinia event without opening Pinnacle tab → after 180s, SSE fires, runner advances

**Fix #2 — stake cap**
- Unit: set `_stake_caps["betinia"] = 50`, balance=200 → assert anchor_stake = 50
- Unit: cap = None, balance = 200 → assert anchor_stake = 200
- Unit: balance < cap → assert anchor_stake = balance

**Fix #3 — Pinnacle arbitration**
- Unit: spawn PinnacleSharedRunner with mocked browser → call lend → assert `STATE_LENT_TO_ARB`, value loop blocked → call release → assert state clears, loop resumes
- Live: select betinia + pinnacle, both run; observe Pinnacle slip is owned by ArbRunner during arb load (no value-bet nav until release)

**Fix #4 — Pinnacle history**
- Discovery first (no code lands without discovery doc)
- Unit (after discovery): canned response fixture → assert `HistoryEntry` parsing
- Live: place a Pinnacle bet, wait for settlement, run reconcile → assert DB row matches provider truth

## 7. Risk + rollback

| Risk | Mitigation |
|---|---|
| Hedge timeout fires too aggressively, marks legitimate placements as failed | 180s default; configurable via env `ARB_HEDGE_TIMEOUT_S` |
| Stake cap regression sizes everything to 0 (cap learned wrong) | Floor at site min (env `ARB_MIN_ANCHOR_STAKE`, default 10); skip opp if anchor_stake < min |
| PinnacleSharedRunner deadlocks if release is missed (exception in arb path) | `_load_all_legs` wraps lend in try/finally; `_run` outer try/finally also calls release |
| Pinnacle history DOM scrape breaks on site redesign | Returns `[]` on selector miss → reconciler falls back to existing pending-loop behavior (no regression); replace with XHR path once discovered |

Rollback: each fix is one commit. Revert the offending commit; behavior returns to pre-spec state with no DB schema changes.

## 8. Files touched

| File | Change |
|---|---|
| `arnold/mirror/arb_runner.py` | hedge timeout (#1), stake cap injection (#2), pinnacle_shared lending (#3) |
| `arnold/mirror/play_loop.py` | spawn PinnacleSharedRunner when soft anchors present (#3); pass `stake_caps` to ArbRunner (#2) |
| `arnold/mirror/pinnacle_shared.py` | NEW — shared Pinnacle runner with lend/release (#3) |
| `arnold/mirror/workflows/pinnacle.py` | implement `sync_history` via DOM scrape (#4) |
| `arnold/mirror/browser.py` | extend `_BET_HISTORY_KEYWORDS` with Pinnacle pattern once discovered (#4 phase A) |
| `arnold/frontend/src/pages/PlayPage.tsx` | render `arb_hedge_failed(reason="user_timeout")`, `pinnacle_lent`/`pinnacle_released` indicators |
| `backend/tests/mirror/test_arb_runner_hedge_timeout.py` | NEW unit tests |
| `backend/tests/mirror/test_arb_runner_stake_cap.py` | NEW unit tests |
| `backend/tests/mirror/test_pinnacle_shared_runner.py` | NEW unit tests |
| `docs/superpowers/specs/2026-04-30-pinnacle-history-discovery.md` | NEW discovery doc (precedes #4 code) |

## 9. Order of implementation

Strictly sequential — each step is independently mergeable and verifiable:

1. Fix #2 (stake cap) — smallest blast radius; unblocks first real placement.
2. Fix #1 (hedge timeout) — runner-level robustness; lets us exercise the loop without manual nuking.
3. Fix #3 (Pinnacle arbitration) — required before running betinia + pinnacle in the same session.
4. Fix #4 phase A (Pinnacle history discovery) — paper documentation only.
5. Fix #4 phase B (sync_history implementation) — depends on phase A.

After all four land: smoke-test betinia↔pinnacle end-to-end with one real placement, verify DB has both legs under one `arb_group:` notes tag, verify reconciliation closes the loop on settlement.
