# Fire Window — Provider-by-Provider Batch Execution

**Date:** 2026-04-02
**Status:** Approved

## Summary

A fire window system that lets you build a batch, allocate capital, then step through providers one at a time — opening live tabs, comparing real-time prices against sharp fair odds, and firing +EV bets per provider with manual confirmation.

## Flow

```
Play page → Build Batch → Capital Alloc → Provider Queue
  → Activate Provider (open tabs, start live polling)
  → Watch live edges (3-5s refresh)
  → Fire +EV bets (negative-edge auto-excluded)
  → Next provider → ... → Done
```

## Backend: FireWindowService

**New module:** `backend/src/services/fire_window.py`

Orchestrates the full fire window lifecycle. State held in memory (single-user system, one window at a time).

### Methods

| Method | Purpose |
|--------|---------|
| `open_window(profile_id)` | Build batch via BatchBuilder, run capital allocation, group bets by provider, return ordered provider queue (Polymarket first) |
| `activate_provider(provider_id)` | Tell mirror to open all tabs for that provider's bets, start live price poll loop (3-5s) |
| `get_live_state()` | Return current provider's bets with: original odds, live odds, fair odds, original edge, live edge, delta, status |
| `fire_provider()` | Filter out negative-edge bets, fire remaining +EV bets, record to DB, advance to next provider |
| `skip_provider()` | Skip current provider, advance queue |
| `close_window()` | Cleanup, close tabs, stop poll loop |

### State

```python
@dataclass
class FireWindow:
    profile_id: str
    batch: list[BatchBet]
    provider_queue: list[str]          # ordered provider IDs
    current_provider: str | None
    provider_bets: dict[str, list]     # provider_id -> bets
    live_snapshots: dict[int, LiveSnapshot]  # bet_id -> latest snapshot
    created_at: datetime
    status: str  # "ready" | "active" | "firing" | "complete"
```

```python
@dataclass
class LiveSnapshot:
    bet_id: int
    live_odds: float
    fair_odds: float
    live_edge: float
    original_edge: float
    delta: float              # live_edge - original_edge
    category: str             # "improved" | "stable" | "degraded" | "negative"
    last_updated: datetime
```

### Delta Categories

| Category | Condition |
|----------|-----------|
| `improved` | live_edge > original_edge + 1pp |
| `stable` | abs(delta) <= 1pp |
| `degraded` | live_edge < original_edge - 1pp, still > 0 |
| `negative` | live_edge <= 0, auto-excluded from fire |

## Live Price Polling

When a provider is activated, a background async task reads prices every 3-5s:

- **Polymarket:** Reuses `MirrorService._read_btn_prices(page)` on each open tab
- Converts price -> odds (`1/price`), computes live edge via `compute_edge(provider, live_odds, fair_odds)`
- Updates `live_snapshots` dict in-place
- On fire: only bets with `live_edge > 0` are included; filtered-out bets returned in response

**Future soft providers:** Same pattern but different scraping per provider type (DOM, API intercept, etc.)

## API Endpoints

**Prefix:** `/api/fire-window`

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/open` | Build batch + capital alloc, return provider queue + summary |
| `POST` | `/activate/{provider_id}` | Open tabs, start poll, return initial bet state |
| `GET` | `/state` | Current provider's live bet states + delta |
| `POST` | `/fire` | Fire current provider's +EV bets, advance queue, return results |
| `POST` | `/skip` | Skip current provider, advance queue |
| `POST` | `/close` | Tear down window, close tabs |
| `GET` | `/queue` | Remaining provider queue with bet counts |

## Frontend: Provider Wizard in ExecutionPanel

Replaces current batch display when fire window is active.

### States

`idle` -> `loading` -> `active` -> `firing` -> `complete`

### Active State (one provider at a time)

- **Header:** Provider name + queue position badge ("1 of 3")
- **Table columns:** Event, Outcome, Stake, Original Odds, Live Odds, Fair Odds, Edge %, Delta
- **Row colors:** Green (improved), dim (stable), yellow (degraded), red/strikethrough (negative/excluded)
- **Live update:** Odds + edge columns refresh every 3-5s (frontend polls `GET /state`)
- **Footer:** Total stake (+EV bets only), total EV, excluded count
- **Buttons:** "Fire [N] bets" (primary), "Skip provider" (secondary)

### After Firing

- Brief result summary (placed/skipped/errors)
- Auto-advance to next provider after 2s, or click to advance immediately

### Queue Complete

- Summary across all providers: bets placed, total stake, total EV, bets excluded

### Staleness

If the window was opened > 15 minutes ago and no provider activated yet, show "Refresh batch" button that re-runs `open_window()`.

## Lifecycle

1. Open Play page -> click "Build batch" -> `POST /open` -> batch + alloc computed
2. UI shows provider queue -> click first provider -> `POST /activate/polymarket` -> tabs open, poll starts
3. Watch live edges update -> hit "Fire" -> `POST /fire` -> places +EV bets, returns results
4. Auto-advance to next provider, repeat
5. All done -> `POST /close` -> cleanup

## Scope

- **Phase 1 (this spec):** Polymarket only — DOM button price reading, manual confirm
- **Phase 2 (future):** Soft providers — different scraping per provider, same wizard flow
- **Phase 3 (future):** Autonomous fire — auto-confirm when all bets are +EV, no manual step

## Key Constraints

- Single window at a time (opening new closes previous)
- No scheduler integration — fully manual from Play page
- Negative-edge bets auto-excluded but visible (strikethrough)
- Mirror auto-started if not running (reuse existing ensure-started pattern)
