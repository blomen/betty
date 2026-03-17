# Per-Provider Scheduling & Row Freshness Timestamps

**Date:** 2026-03-17
**Status:** Approved

## Problem

The current tier-based scheduler groups all API soft providers into one 60m loop and all browser soft providers into another 60m loop. This causes:

1. **Uniform cadence regardless of speed** — tipwin (76s) and comeon (900s) both wait 60m before the next cycle, despite vastly different extraction times.
2. **Stale global indicators** — "SOFT extracting" / "SHARP extracting" tells the user nothing about which providers are done or how fresh specific data is.
3. **Browser providers bottleneck** — the 60m cooldown starts after the entire sequential browser tier finishes (~30-50 min total), meaning some providers wait 90+ minutes between runs.

## Solution

Replace tier-based scheduling with per-provider scheduling. Each soft provider gets its own async loop with a cooldown that starts after its own extraction completes. Remove global extraction indicators and show per-row freshness timestamps instead.

## Architecture

### Per-Provider Scheduling Model

Replace `TierState` with `ProviderSchedule` as the primary scheduling unit:

```python
@dataclass
class ProviderSchedule:
    provider_id: str
    category: str              # "sharp", "api_soft", "browser_soft"
    interval_seconds: int      # Cooldown AFTER completion
    running: bool = False
    task: Optional[asyncio.Task] = None
    last_completed: Optional[datetime] = None
    run_count: int = 0
    last_error: Optional[str] = None
    last_duration: Optional[float] = None
```

### Provider Loops

Each provider gets its own async loop:

```
provider_loop(schedule):
    wait for sharp_ready (if not sharp category)
    while running:
        try:
            acquire browser_semaphore (if browser provider)
            create isolated ExtractionPipeline instance
            run extraction for [this provider only]
            release browser_semaphore
            schedule.last_completed = now
            schedule.last_error = None
        except Exception as e:
            schedule.last_error = str(e)
            log error, update provider state to "failed"
        finally:
            update_provider_state(schedule)
        sleep(schedule.interval_seconds)
```

### Platform Consolidation (Fan-Out Providers)

Several providers in the `active` list are platform clones that fan out from a canonical provider (e.g., `mrgreen` is a Spectate clone of `888sport`, `lyllo` fans out from ComeOn, `betsafe`/`nordicbet`/`spelklubben`/`bethard` are Gecko V2 clones of `betsson`). These are **not listed in the scheduling config** because they are extracted as part of their canonical provider's extraction run (platform fan-out happens inside the pipeline, not the scheduler).

No change to fan-out behavior — it continues to work as today. The scheduler only lists canonical providers; clones are handled transparently by the pipeline.

**Sharp stays grouped** — Pinnacle + Polymarket run together in one loop on 1m fixed interval. No change to current behavior.

**Browser semaphore** — shared `asyncio.Semaphore(1)` ensures only one browser provider runs at a time. API providers don't acquire any semaphore.

**No collision avoidance between API and browser** — API is I/O-bound (HTTP), browser is CPU-bound (Playwright). They already coexist today across tiers with isolated pipeline instances and separate DB sessions.

### Cadences

| Category | Interval | Behavior |
|----------|----------|----------|
| Sharp (Pinnacle + Polymarket) | 1m | Fixed interval, grouped in one loop |
| API soft (unibet, betinia, betsson, vbet) | 15m | Per-provider cooldown after completion |
| Browser soft (888sport, coolbet, interwetten, comeon, 10bet, tipwin) | 60m | Per-provider cooldown after completion, sequential via semaphore(1) |
| Boosts | 60m | Independent fixed interval (no longer triggered by api_soft) |
| Settlement, cleanup, trading reset | Unchanged | 2m, 6h, 60s respectively |

### Config (providers.yaml)

```yaml
extraction_scheduling:
  sharp:
    providers: [pinnacle, polymarket]
    interval_minutes: 1
    grouped: true              # Run together as one unit

  api_soft:
    providers: [unibet, betinia, betsson, vbet]
    interval_minutes: 15
    grouped: false             # Each provider gets own loop

  browser_soft:
    max_concurrent_browsers: 1
    providers: [888sport, coolbet, interwetten, comeon, 10bet, tipwin]
    interval_minutes: 60
    grouped: false
```

### Pipeline Isolation

Each provider loop creates its own `ExtractionPipeline()` instance with isolated DB session and event cache, same as today's tier isolation but more granular. The sharp group shares one pipeline instance for Pinnacle + Polymarket.

### Watchdog

Same pattern as today but monitors per-provider tasks instead of per-tier tasks. Detects dead tasks (`task.done() == True`), stale `last_completed` (> 3x interval). Restart backoff is **per-provider**: first death → restart after 10s, second consecutive death of the same provider → restart after 30s. Reset the death counter on successful completion. Max 3 consecutive restarts before marking provider as permanently failed (requires manual restart via API).

## Frontend Changes

### Remove Global Extraction Indicators

- Delete `ExtractionProgressBar.tsx` component
- Remove "SOFT extracting / SHARP extracting" indicators from the UI
- Remove or slim down `useExtractionStatus.ts` hook

### Per-Row Freshness Timestamps

Add an "Updated" column to value/dutch/reverse/stats tables showing relative time since each provider's odds were last updated for that event:

```
Event               Provider    Odds    Edge    Updated
Real Madrid v Barca  unibet     2.10    +3.2%   4m
Real Madrid v Barca  betsson    2.05    +1.8%   18m
Real Madrid v Barca  coolbet    2.08    +2.4%   47m
```

- **Format:** compact relative time — `4m`, `18m`, `1h`, `2h`
- **Color coding:** green (<15m fresh), default (<60m), dim/yellow (>60m stale)
- **Data source:** `Odds.updated_at` timestamp — already exists per provider per event. Backend API responses for opportunities/value/dutch endpoints must include this field in their payloads (add to serialization if not already present)

### What Gets Removed

| Component | Action |
|-----------|--------|
| `ExtractionProgressBar.tsx` | Delete |
| Global extraction indicators in `TerminalWindow.tsx` / `TabBar.tsx` | Remove |
| `useExtractionStatus.ts` | Remove or slim to per-provider freshness only |
| Per-tier progress API endpoints | Remove or deprecate |

## Backend API Changes

### State Management (`state.py`)

Replace per-tier state with per-provider state. Since all provider loops are `asyncio.Task`s in the same event loop, no threading lock is needed — use simple dict updates (single-threaded asyncio is inherently safe). Remove the existing `threading.Lock` usage for extraction state.

```python
provider_states: dict[str, ProviderState] = {
    "pinnacle": {"running": False, "last_completed": None, "last_duration": None, ...},
    "unibet": {"running": False, "last_completed": None, "last_duration": None, ...},
    # ...
}
```

### Extraction API Routes

- Update `GET /api/extraction/progress` to return per-provider status
- Remove or deprecate `GET /api/extraction/tiers/progress`
- Remove WebSocket extraction progress endpoint (no longer needed without global progress bar)
- Keep `GET /api/extraction/freshness` (still useful for overall freshness)
- Keep manual trigger endpoints (`POST /api/extraction/run`, start/stop)

### Migration

Rename the YAML config key from `extraction_tiers` to `extraction_scheduling` in `providers.yaml`. Update all code that reads the old key. No backwards-compatibility shim — single cutover.

## Boosts Tier

Boosts run on their own independent 60m fixed interval. No longer triggered by `api_soft` completion (since there's no single "api_soft finished" moment). Boosts match against Pinnacle fair odds which refresh every 1m, so they don't need fresh soft data.

Boosts still wait for `sharp_ready` before first run (same as all non-sharp loops) to ensure Pinnacle fair odds are available for EV enrichment.

## What Doesn't Change

- Sharp tier behavior (Pinnacle + Polymarket grouped, 1m)
- Pool manager (browser semaphore, provider groups, rate limiting)
- Pipeline isolation pattern (just more granular)
- Settlement, cleanup, trading reset utility tiers
- Watchdog pattern (extended to more tasks)
- Provider extractors themselves (no changes to extraction logic)
