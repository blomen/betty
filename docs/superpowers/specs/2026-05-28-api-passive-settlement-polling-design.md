# API-Passive Settlement Polling

**Status:** Design
**Date:** 2026-05-28
**Context:** A Pinnacle pending bet from 19:31 stayed "PENDING · 15 kr" in Betty's UI long after Pinnacle's API had already settled it. Pinnacle's `_sync_history` and `_settle_all` have been API-only since the 2026-05-28 DOM-misclassification fix, but settlement only actually runs when the user manually navigates to Pinnacle's history page (reactive sync) or starts an active play session. Nothing polls. Kalshi / Polymarket / Cloudbet share the same shape and the same gap.

## Problem

The mirror has an explicit auto-nav invariant ([CLAUDE.md:260](../../CLAUDE.md#L260)):

> **PendingLoop polling is DELETED.** Recovery is reactive — user navigates to history → `history_intercepted` → `_reactive_history_sync(pid)` → workflow.sync_history → reconcile.

The reason for that rule is real: for DOM-driven workflows (Altenar / Gecko / Kambi), `sync_history` does `page.goto` / tab clicks ([altenar.py:131-169](../../local/mirror/workflows/strategies/altenar.py#L131)). Polling them in the background clobbered open betslips. So the polling chain was disabled — `PendingLoop` is instantiated but [`start()` is never called](../../local/mirror/router.py#L243-L250).

But four strategies do `sync_history` purely via `page.evaluate(fetch(...))` against the provider's REST API — no navigation, no clicks, no DOM mutation:

| Provider | `_sync_history` shape |
|---|---|
| Pinnacle | `GET /bets?status=unsettled` + `GET /bets?status=settled` |
| Cloudbet | `GET /sports-betting/v4/bets/positions?status=ACCEPTED` + `?status=COMPLETED` |
| Kalshi | API call |
| Polymarket | API call |

For these, polling is safe. The auto-nav invariant doesn't apply because there is no nav. The blanket "don't poll" rule is overcorrecting.

## Goals

- Pinnacle (and Cloudbet / Kalshi / Polymarket) pending bets in Betty's DB get reconciled against the provider's API within ~60 s of the provider settling them, without the user needing to navigate anywhere.
- DOM-driven providers (Altenar, Gecko, Kambi, generic Spectate-shape, etc.) continue to use reactive sync only — their event-page DOM-clobber protection stays intact.
- Minimal new surface area. Reuse the proven `_sync_provider → reconcile_and_publish → _record_unknown_open_bets` chain that already exists in [pending_loop.py](../../local/mirror/pending_loop.py).

## Non-Goals

- Auto-place bets via API. Placement stays manual / interceptor-based for every provider. The user's "no auto placements" call stands.
- Revive polling for DOM-driven providers. They keep reactive sync.
- Bypass the login check or tab-existence check. If the user closes the Pinnacle tab or logs out, polling silently no-ops, same as today's reactive path would.
- Clean up the dead `PendingLoop._run` / `_sync_all` punch-list items in [docs/code-review-2026-05.md](../../code-review-2026-05.md#L125) for unrelated providers. The same `_run` code becomes live again under this change; broader cleanup is a separate effort.

## Design

### Strategy-level flag

Add to the `Strategy` dataclass ([base.py](../../local/mirror/workflows/base.py)):

```python
@dataclass
class Strategy:
    ...
    # True for strategies whose sync_history runs purely via page.evaluate(fetch(...))
    # — no page.goto, no DOM clicks. Safe to poll in the background even while the
    # user is on an event page; cannot clobber an open betslip.
    sync_history_is_passive: bool = False
```

Forward through `GenericWorkflow` ([generic.py](../../local/mirror/workflows/generic.py)):

```python
@property
def sync_history_is_passive(self) -> bool:
    return bool(self.strategy and self.strategy.sync_history_is_passive)
```

Set `sync_history_is_passive=True` in the `Strategy(...)` constructor of:
- [pinnacle.py](../../local/mirror/workflows/strategies/pinnacle.py#L1434)
- [kalshi.py](../../local/mirror/workflows/strategies/kalshi.py#L666)
- [polymarket.py](../../local/mirror/workflows/strategies/polymarket.py)
- [cloudbet.py](../../local/mirror/workflows/strategies/cloudbet.py)

Also fix the stale module docstring at [pinnacle.py:6](../../local/mirror/workflows/strategies/pinnacle.py#L6) — `sync_history(): DOM scrape + API fallback` is no longer accurate; the DOM path was deleted on 2026-05-28.

### Relax the event-page guard for passive providers

[`PendingLoop._sync_provider`](../../local/mirror/pending_loop.py#L353) currently has:

```python
has_event = "/event/" in current_url or "#/event/" in current_url
if has_event:
    logger.debug(...)
    return
```

Change to:

```python
has_event = "/event/" in current_url or "#/event/" in current_url
if has_event and not workflow.sync_history_is_passive:
    logger.debug(...)
    return
```

Same relaxation in [`_refresh_balances`](../../local/mirror/pending_loop.py#L289):

```python
if "/event/" in url_lower or "#/event/" in url_lower:
    if not workflow.sync_history_is_passive:
        continue
```

(`sync_balance` is API-based for the same four providers and is the actual signal the operator sees in the top-bar.)

### Wire start/stop into router lifecycle

[router.py:243-250](../../local/mirror/router.py#L243):

Replace the "intentionally NOT started" comment block with one that explains the new rule:

```python
# PendingLoop runs in the background, but each per-provider tick is gated on
# workflow.sync_history_is_passive — DOM-driven providers (Altenar/Gecko/Kambi)
# are still skipped when the tab is on an event page so their sync_history's
# page.goto / DOM clicks can't clobber an open betslip. API-passive providers
# (Pinnacle/Kalshi/Polymarket/Cloudbet) settle every 60 s regardless of where
# their tab is parked.
pending_loop = PendingLoop(browser, broadcaster, proxy_url)
pending_loop.start()
```

No explicit `stop()` call. The existing `play_loop` follows the same pattern — `stop()` is only exposed via its `/play/stop` HTTP endpoint, not wired to FastAPI shutdown — and asyncio task cancellation at event-loop exit covers process teardown. If we ever add a clean shutdown sequence, both loops should be added together.

### Data flow (Pinnacle worked example)

```
t=0      User places a 15 kr bet on Pinnacle → XHR intercepted → DB row {result=pending}
t=0..N   Match plays out. Betty UI shows "PENDING 1 bets · 15 kr".
                                                                         (today: blocks here forever)

         Under this design:

t=60s    PendingLoop._run tick
           → _sync_all
             → _refresh_balances:
                  Pinnacle tab is on /sports/... (not /event/). Refresh balance via API → POST /api/bankroll/set/pinnacle.
             → _fetch_pending: GET /api/opportunities/play/pending-bets → {pinnacle: [{bet_id=N, odds=8.37, stake=15, ...}]}
             → _sync_provider("pinnacle", db_bets):
                  has_event check: passes (passive bypass even if user IS on /event/).
                  check_login: True (cookies + harvested headers).
                  workflow.sync_history(page):
                    → page.evaluate(fetch("/bets?status=unsettled"))   [empty — bet already settled]
                    → page.evaluate(fetch("/bets?status=settled"))     [includes bet N as outcome=loss]
                    → returns [HistoryEntry(provider_bet_id="2238830388", status="lost", payout=0, ...)]
                  reconcile_and_publish:
                    → tier-1 provider_bet_id match against db_bets
                    → POST /api/bets/{N}/settle {result: "lost", payout: 0}
                    → broadcast "settlement" SSE
                  _record_unknown_open_bets: no new pending → no-op.
t=60s+   Frontend SSE updates PENDING row → next /pending-bets poll → row drops from "PENDING" → flips to W/L history.
```

### Safety properties

1. **No DOM clobber** — the guard relaxation is gated on `sync_history_is_passive`, which is opt-in per strategy. Altenar/Gecko/Kambi keep their event-page skip and stay on reactive sync.
2. **No silent placement** — placement code paths are untouched. `autonomous_placement` stays False on Pinnacle. The user's "no auto placements" constraint is unaffected.
3. **No tunnel storm** — 4 providers × 2 API calls per tick × 1 tick/60s = 8 reqs/min. Pinnacle already does similar volume during arb runs.
4. **Login still required** — the existing three-tier `check_login` gate ([pending_loop.py:368-388](../../local/mirror/pending_loop.py#L368)) runs every tick. Logged-out providers skip silently.
5. **Browser-not-running short-circuit** — `_sync_all` already returns immediately when `not (browser.running and browser.context)` ([pending_loop.py:226](../../local/mirror/pending_loop.py#L226)). Polling adds no work when betty is closed.

### What about ProviderRunner overlap?

[ProviderRunner](../../local/mirror/provider_runner.py#L1668) already calls `sync_history` for `autonomous_placement=True` providers (Kalshi/Polymarket/Cloudbet) during its lifecycle. With this change, the same providers also get polled by PendingLoop. That's fine:

- Both paths feed into `reconcile_and_publish`, which is idempotent — settling an already-settled bet is a no-op
- `_record_unknown_open_bets` dedups on `provider_bet_id` then `(odds, stake)` counter ([pending_loop.py:_record_unknown_open_bets](../../local/mirror/pending_loop.py)) — paginated double-insertion was the bug that drove this dedup, exactly the case here
- The two paths run on different cadences (Runner: per active-play cycle; PendingLoop: 60s); whichever fires first wins, the loser no-ops

## Testing

Add to [test_pending_loop.py](../../local/tests/test_pending_loop.py):

1. **`test_sync_provider_skips_event_page_for_dom_driven`** — workflow with `sync_history_is_passive=False`, tab on `/event/123`. Assert `sync_history` is NOT called and the function returns early.
2. **`test_sync_provider_proceeds_on_event_page_for_passive`** — workflow with `sync_history_is_passive=True`, tab on `/event/123`. Assert `sync_history` IS called and reconcile path runs.
3. **`test_refresh_balances_skips_event_page_for_dom_driven`** — same shape, for the balance path.
4. **`test_refresh_balances_proceeds_on_event_page_for_passive`** — same shape, expects `sync_balance` called.
5. **Smoke** — start PendingLoop with a fake browser whose context is None; assert `_sync_all` returns immediately with no exceptions logged.

Manual verification (live):
1. Place a small Pinnacle bet on a match starting in <30 min.
2. Wait for the match to settle on Pinnacle's side.
3. Watch Betty's UI: PENDING row flips to W/L within ~60s without touching the Pinnacle history tab.
4. Check `extraction.log` / mirror logs for one `[PendingLoop] syncing pinnacle` per minute.

## Open Questions

- Should the poll interval (`_POLL_INTERVAL = 60`) be different for API-passive vs. originally-intended (mixed) providers? **Decision:** keep 60s for now. Tune later if Pinnacle settlement lag is noticeable to the operator.
- Should we drop the `_record_unknown_open_bets` step for API-passive providers, since their placement is already always intercepted? **Decision:** keep it. Manual bets placed on the bookmaker site (outside Betty) still need to surface; that's exactly what `_record_unknown_open_bets` is for.
