# Per-Provider Scheduling & Row Freshness Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace tier-based extraction scheduling with per-provider scheduling, remove global extraction indicators, and add per-row freshness timestamps.

**Architecture:** Each soft provider gets its own async loop with cooldown-after-completion. Sharp (Pinnacle + Polymarket) stays grouped at 1m. Browser providers gated by a shared semaphore(1). Frontend removes ExtractionProgressBar and adds relative timestamps to odds rows.

**Tech Stack:** Python asyncio, FastAPI, SQLAlchemy, React/TypeScript

**Spec:** `docs/superpowers/specs/2026-03-17-per-provider-scheduling-design.md`

---

## File Structure

### Backend (modify)
- `backend/src/pipeline/scheduler.py` — Replace TierState with ProviderSchedule, rewrite loops
- `backend/src/config/providers.yaml` — Rename `extraction_tiers` → `extraction_scheduling`, add `grouped` flag
- `backend/src/api/state.py` — Replace tier states with per-provider states, remove threading locks
- `backend/src/api/routes/extraction.py` — Update progress endpoint, remove tiers/progress and WebSocket endpoints
- `backend/src/analysis/value.py` — Add `odds_updated_at` field to ValueBet dataclass
- `backend/src/analysis/scanner.py` — Populate `odds_updated_at` from Odds.updated_at

### Frontend (modify)
- `frontend/src/components/Terminal/FilterBar.tsx` — Update FreshnessIndicator to work with per-provider state
- `frontend/src/hooks/useExtractionStatus.ts` — Remove tier progress polling, simplify to provider freshness
- `frontend/src/services/api.ts` — Remove tier progress API calls, update types
- `frontend/src/components/Terminal/pages/ValuePage.tsx` — Add Updated column
- `frontend/src/components/Terminal/pages/DutchPage.tsx` — Add Updated column
- `frontend/src/components/Terminal/pages/ReversePage.tsx` — Add Updated column
- `frontend/src/components/Terminal/pages/PolymarketPage.tsx` — Add Updated column

### Frontend (delete)
- `frontend/src/components/Terminal/ExtractionProgressBar.tsx`

---

## Task 1: Update providers.yaml config

**Files:**
- Modify: `backend/src/config/providers.yaml:823-866`

- [ ] **Step 1: Rename extraction_tiers to extraction_scheduling and add grouped flag**

Replace the `extraction_tiers` section with:

```yaml
extraction_scheduling:
  sharp:
    providers: [pinnacle, polymarket]
    interval_minutes: 1
    grouped: true

  api_soft:
    providers: [unibet, betinia, betsson, vbet]
    interval_minutes: 15
    grouped: false

  browser_soft:
    max_concurrent_browsers: 1
    providers: [888sport, coolbet, comeon, 10bet, tipwin]
    interval_minutes: 60
    grouped: false
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/config/providers.yaml
git commit -m "config: rename extraction_tiers to extraction_scheduling with per-provider cadences"
```

---

## Task 2: Rewrite scheduler — ProviderSchedule dataclass and per-provider loops

**Files:**
- Modify: `backend/src/pipeline/scheduler.py:23-34` (TierState → ProviderSchedule)
- Modify: `backend/src/pipeline/scheduler.py:62-83` (init — new data structures)
- Modify: `backend/src/pipeline/scheduler.py:94-156` (start_tier → start_provider)
- Modify: `backend/src/pipeline/scheduler.py:182-255` (_tier_loop → _provider_loop)
- Modify: `backend/src/pipeline/scheduler.py:273-279` (_load_extraction_tiers → _load_scheduling_config)
- Modify: `backend/src/pipeline/scheduler.py:301-343` (start_all — iterate providers)
- Modify: `backend/src/pipeline/scheduler.py:351-401` (_watchdog_loop — monitor per-provider)
- Modify: `backend/src/pipeline/scheduler.py:431-488` (boosts — remove _boost_trigger, own 60m loop)

- [ ] **Step 1: Replace TierState with ProviderSchedule dataclass**

At `scheduler.py:23-34`, replace `TierState` with:

```python
@dataclass
class ProviderSchedule:
    """Schedule state for a single provider (or grouped sharp providers)."""
    provider_id: str              # Single provider or "sharp" for grouped
    category: str                 # "sharp", "api_soft", "browser_soft"
    interval_seconds: int         # Cooldown AFTER completion
    providers: list[str] | None = None  # Only for grouped (sharp): list of providers
    running: bool = False
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    last_completed: Optional[datetime] = None
    run_count: int = 0
    last_error: Optional[str] = None
    last_duration: Optional[float] = None
    consecutive_failures: int = 0
```

- [ ] **Step 2: Update __init__ — replace tier dicts with provider schedule dicts**

In `__init__` (around lines 62-83), replace tier-related attributes:

```python
# Replace these:
#   self._tiers: dict[str, TierState] = {}
#   self._tier_locks: dict[str, asyncio.Lock] = {}
#   self._boost_trigger = asyncio.Event()

# With:
self._schedules: dict[str, ProviderSchedule] = {}  # key = provider_id or "sharp"
self._provider_locks: dict[str, asyncio.Lock] = {}
self._browser_semaphore = asyncio.Semaphore(1)  # Only 1 browser at a time
# Keep self._sharp_ready = asyncio.Event()
```

- [ ] **Step 3: Rewrite _load_extraction_tiers → _load_scheduling_config**

Replace `_load_extraction_tiers` (lines 273-279) to read `extraction_scheduling` key instead of `extraction_tiers`:

```python
def _load_scheduling_config(self) -> dict:
    """Load extraction_scheduling from providers.yaml."""
    config = load_provider_config()
    return config.get("extraction_scheduling", {})
```

- [ ] **Step 4: Rewrite start_all to create per-provider schedules**

Replace `start_all` (lines 301-343). For each category in config:
- If `grouped: true` (sharp): create one `ProviderSchedule(provider_id="sharp", providers=[pinnacle, polymarket], ...)`
- If `grouped: false`: create one `ProviderSchedule` per provider in the list
- Filter disabled providers from active profile
- Call `_start_schedule(schedule)` for each
- Start boosts on own 60m interval (no `_boost_trigger`)
- Start settlement, cleanup, trading_reset unchanged

```python
async def start_all(self):
    config = self._load_scheduling_config()
    disabled = self._get_disabled_providers()

    for category_name, category_config in config.items():
        providers = [p for p in category_config["providers"] if p not in disabled]
        if not providers:
            continue

        interval = category_config["interval_minutes"] * 60
        grouped = category_config.get("grouped", False)

        if grouped:
            schedule = ProviderSchedule(
                provider_id=category_name,
                category=category_name,
                interval_seconds=interval,
                providers=providers,
            )
            await self._start_schedule(schedule)
        else:
            for provider_id in providers:
                schedule = ProviderSchedule(
                    provider_id=provider_id,
                    category=category_name,
                    interval_seconds=interval,
                )
                await self._start_schedule(schedule)

    # Boosts — own 60m loop, waits for sharp_ready
    await self.start_boosts_tier(interval_seconds=3600)
    # Settlement, cleanup, trading_reset — unchanged
    await self._start_settlement_tier()
    await self._start_cleanup_tier()
    await self._start_trading_reset_tier()
    # Watchdog
    self._watchdog_task = asyncio.create_task(self._watchdog_loop())
```

- [ ] **Step 5: Write _start_schedule and _provider_loop**

Replace `start_tier` and `_tier_loop` with:

```python
async def _start_schedule(self, schedule: ProviderSchedule):
    """Start a provider's extraction loop."""
    schedule.running = True
    self._schedules[schedule.provider_id] = schedule
    self._provider_locks[schedule.provider_id] = asyncio.Lock()
    schedule.task = asyncio.create_task(
        self._provider_loop(schedule),
        name=f"extraction-{schedule.provider_id}",
    )
    logger.info(f"[Scheduler] Started {schedule.provider_id} ({schedule.category}, "
                f"interval={schedule.interval_seconds}s)")

async def _provider_loop(self, schedule: ProviderSchedule):
    """Main loop for a single provider (or grouped sharp)."""
    # Wait for sharp if not sharp category
    if schedule.category != "sharp":
        logger.info(f"[Scheduler] {schedule.provider_id} waiting for sharp readiness...")
        await self._sharp_ready.wait()

    while schedule.running:
        start = datetime.now(timezone.utc)
        lock = self._provider_locks[schedule.provider_id]

        try:
            async with lock:
                # Acquire browser semaphore if browser provider
                if schedule.category == "browser_soft":
                    async with self._browser_semaphore:
                        results = await self._run_provider_extraction(schedule)
                else:
                    results = await self._run_provider_extraction(schedule)

            schedule.last_completed = datetime.now(timezone.utc)
            schedule.last_duration = (schedule.last_completed - start).total_seconds()
            schedule.last_error = None
            schedule.consecutive_failures = 0
            schedule.run_count += 1

            # Set sharp_ready after first sharp run
            if schedule.category == "sharp" and not self._sharp_ready.is_set():
                self._sharp_ready.set()
                logger.info("[Scheduler] Sharp ready — unblocking soft providers")

        except Exception as e:
            schedule.last_error = str(e)
            schedule.consecutive_failures += 1
            logger.exception(f"[Scheduler] {schedule.provider_id} extraction failed: {e}")
        finally:
            # Update frontend state
            update_provider_state(schedule.provider_id, {
                "running": False,
                "last_completed": schedule.last_completed.isoformat() if schedule.last_completed else None,
                "last_duration": schedule.last_duration,
                "last_error": schedule.last_error,
                "category": schedule.category,
            })

        # Cooldown AFTER completion (full interval elapses after run finishes)
        await asyncio.sleep(schedule.interval_seconds)
```

- [ ] **Step 6: Write _run_provider_extraction helper (replaces _run_with_state_updates)**

This replaces the ~130-line `_run_with_state_updates` method. The old method handled per-tier state updates, metrics polling (500ms), and DB count reconciliation. All of that is now unnecessary — per-provider state is updated directly in `_provider_loop` (step 5) and the 500ms metrics polling (`poll_metrics_and_update_state`) is removed in Task 4.

Delete `_run_with_state_updates` and `_build_final_state` entirely. Replace with:

```python
async def _run_provider_extraction(self, schedule: ProviderSchedule) -> dict:
    """Run extraction for a single provider or grouped providers."""
    providers = schedule.providers or [schedule.provider_id]

    # Update state to running
    update_provider_state(schedule.provider_id, {
        "running": True,
        "category": schedule.category,
    })

    pipeline = ExtractionPipeline()
    try:
        results = await pipeline.run(
            providers=providers,
            tier_name=schedule.category,
        )
        return results
    finally:
        await pipeline.close()
```

Also update `run_once()` (the manual extraction trigger, around line 908) to use this same helper instead of `_run_with_state_updates`. Create a temporary `ProviderSchedule` for the manual run.

- [ ] **Step 7: Update _watchdog_loop for per-provider monitoring**

Replace the tier-based watchdog (lines 351-401) to iterate `self._schedules` instead of `self._tiers`. Track `consecutive_failures` per provider — if a provider has 3+ consecutive failures, mark as permanently failed and skip restart.

- [ ] **Step 8: Update boosts loop — remove _boost_trigger dependency**

In `_boosts_loop` (lines 447-488):
1. Keep `await self._sharp_ready.wait()` before first run
2. Remove `self._boost_trigger` event from `__init__`
3. Remove `self._boost_trigger.clear()` and `asyncio.wait_for(self._boost_trigger.wait(), timeout=...)` pattern
4. Replace with simple `await asyncio.sleep(interval_seconds)` after each run
5. Remove the `_boost_trigger.set()` call that was in the old `_tier_loop` (previously triggered after api_soft completion)

- [ ] **Step 9: Update all stop methods**

Replace `stop_tier` with `stop_provider(provider_id)` and update `stop_all` to iterate `self._schedules`.

- [ ] **Step 10: Commit**

```bash
git add backend/src/pipeline/scheduler.py
git commit -m "refactor(scheduler): per-provider scheduling with independent cooldowns"
```

---

## Task 3: Update state.py — per-provider state management

**Files:**
- Modify: `backend/src/api/state.py`

- [ ] **Step 1: Replace tier state with provider state**

Remove `tier_states`, `tier_state_lock`, `update_tier_state`, `get_tier_states`. Replace with:

```python
# Per-provider extraction state (no lock needed — single-threaded asyncio)
provider_states: dict[str, dict] = {}

def update_provider_state(provider_id: str, updates: dict):
    """Update state for a single provider."""
    if provider_id not in provider_states:
        provider_states[provider_id] = {
            "running": False,
            "last_completed": None,
            "last_duration": None,
            "last_error": None,
            "category": None,
        }
    provider_states[provider_id].update(updates)

def get_provider_states() -> dict:
    """Return copy of all provider states."""
    return dict(provider_states)
```

- [ ] **Step 2: Remove WebSocket ConnectionManager**

Delete the `ConnectionManager` class and `ws_manager` singleton (lines 64-94). It's no longer needed without the global progress bar.

- [ ] **Step 3: Remove threading locks for extraction state**

Remove `extraction_state_lock` and `tier_state_lock`. The `extraction_state` dict can remain for backward compat of manual trigger endpoints, but remove the lock wrapping.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/state.py
git commit -m "refactor(state): per-provider state management, remove tier state and WS manager"
```

---

## Task 4: Update extraction API routes

**Files:**
- Modify: `backend/src/api/routes/extraction.py`

- [ ] **Step 1: Update GET /api/extraction/progress**

Replace the tier-based progress endpoint (lines 256-296) to return per-provider status:

```python
@router.get("/progress")
async def get_extraction_progress():
    """Per-provider extraction status."""
    states = get_provider_states()
    return {
        "providers": states,
        "any_running": any(s.get("running", False) for s in states.values()),
    }
```

- [ ] **Step 2: Remove GET /api/extraction/tiers/progress**

Delete the endpoint at lines 299-356 and its response models.

- [ ] **Step 3: Remove WebSocket endpoint**

Delete the `websocket_extraction_progress` endpoint and related imports.

- [ ] **Step 4: Remove poll_metrics_and_update_state**

Delete the background polling function (lines 24-100) that updated tier state every 500ms. Per-provider state is now updated directly by the scheduler loops.

- [ ] **Step 5: Keep freshness endpoint but simplify**

The `GET /api/extraction/freshness` endpoint (lines 688-728) can stay as-is — it queries `Odds.updated_at` directly from DB grouped by provider category.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes/extraction.py
git commit -m "refactor(api): per-provider progress endpoint, remove tier progress and WS"
```

---

## Task 5: Add odds_updated_at to ValueBet and scanner

**Files:**
- Modify: `backend/src/analysis/value.py:25-60`
- Modify: `backend/src/analysis/scanner.py`

- [ ] **Step 1: Add odds_updated_at field to ValueBet**

In `value.py:46` (after `skip_reason`), add:

```python
# Freshness tracking
odds_updated_at: Optional[str] = None  # ISO timestamp of when this provider's odds were last updated
```

- [ ] **Step 2: Add updated_at to group_odds output**

In `scanner.py`, the `group_odds()` method (around line 1080) strips Odds ORM objects into plain dicts with only `provider`, `odds`, `point`. Add `updated_at` to this dict:

```python
# In group_odds(), where the dict is built (around line 1080-1084):
{
    "provider": odds.provider_id,
    "odds": odds.decimal_odds,
    "point": odds.point,
    "updated_at": odds.updated_at,  # Add this
}
```

- [ ] **Step 3: Set odds_updated_at in find_value_in_market**

In `find_value_in_market`, after a `ValueBet` is created from `find_value()` (the `vb` variable), set the `updated_at` from the per-provider dict `po`:

```python
# After vb = find_value(...) returns a ValueBet, around line 1333:
if vb:
    vb.odds_updated_at = po.get("updated_at").isoformat() if po.get("updated_at") else None
```

Note: `find_value()` in `value.py` is a pure function with no ORM access. The ORM data flows through `group_odds()` → per-provider dicts → `find_value_in_market` iteration. That's why we add `updated_at` to the dict in Step 2 and read it in Step 3.

- [ ] **Step 4: Commit**

```bash
git add backend/src/analysis/value.py backend/src/analysis/scanner.py
git commit -m "feat(scanner): include odds_updated_at in ValueBet for row freshness"
```

---

## Task 6: Delete ExtractionProgressBar and update frontend hooks

**Files:**
- Delete: `frontend/src/components/Terminal/ExtractionProgressBar.tsx`
- Modify: `frontend/src/hooks/useExtractionStatus.ts`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Delete ExtractionProgressBar.tsx**

Remove the file entirely.

- [ ] **Step 2: Remove all imports/usages of ExtractionProgressBar**

Search for imports of `ExtractionProgressBar` across the frontend and remove them. Check `TerminalWindow.tsx`, `TabBar.tsx`, and any other files that render it.

- [ ] **Step 3: Simplify useExtractionStatus.ts**

Remove `useTiersProgress` and `useExtractionProgress` hooks. Keep `useExtractionFreshness` (still needed for FreshnessIndicator in filter bars). Remove `getTiersProgress` and `getExtractionProgress` API calls and their types from `api.ts`.

**Important:** `useExtractionFreshness` currently depends on `useTiersProgress()` to check `anyRunning` and conditionally suppress refetching. Since `useTiersProgress` is removed, update `useExtractionFreshness` to always poll on a fixed 60s interval regardless of running state.

Slim down the main polling hook to only fetch freshness data:

```typescript
export function useExtractionStatus(): void {
  // Poll freshness every 30s on fixed interval (no "running" state to check)
}
```

- [ ] **Step 4: Remove tier-related types from api.ts**

Remove `TiersProgressResponse`, `TierProgress`, `ExtractionProgress`, `ProviderProgress` interfaces and their API methods.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(frontend): remove ExtractionProgressBar, simplify extraction hooks"
```

---

## Task 7: Update FreshnessIndicator for per-provider model

**Files:**
- Modify: `frontend/src/components/Terminal/FilterBar.tsx:406-470`

- [ ] **Step 1: Simplify FreshnessIndicator**

The `FreshnessIndicator` component currently checks `useTiersProgress()` to show "extracting" for running tiers. Since we removed tier progress, simplify it to only show age from timestamps. Remove the `TIER_RUNNING_MAP` logic and `runningDisplayTiers` set.

Keep the timestamp-based age display (HH:MM format with color coding). The component still receives `[label, isoTimestamp]` pairs from each page.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/FilterBar.tsx
git commit -m "refactor(frontend): simplify FreshnessIndicator without tier progress"
```

---

## Task 8: Add Updated column to table pages

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ValuePage.tsx`
- Modify: `frontend/src/components/Terminal/pages/DutchPage.tsx`
- Modify: `frontend/src/components/Terminal/pages/ReversePage.tsx`
- Modify: `frontend/src/components/Terminal/pages/PolymarketPage.tsx`

- [ ] **Step 1: Create a shared relativeTime helper**

Add to `frontend/src/components/Terminal/FilterBar.tsx` (alongside `FreshnessIndicator`) or a shared utils file. Export it so all pages can import it:

```typescript
function relativeTime(isoTimestamp: string | null | undefined): { text: string; className: string } {
  if (!isoTimestamp) return { text: "—", className: "text-zinc-600" };
  const diffMs = Date.now() - new Date(isoTimestamp).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return { text: "<1m", className: "text-green-400" };
  if (mins < 15) return { text: `${mins}m`, className: "text-green-400" };
  if (mins < 60) return { text: `${mins}m`, className: "text-zinc-400" };
  const hrs = Math.floor(mins / 60);
  return { text: `${hrs}h`, className: "text-yellow-500/70" };
}
```

- [ ] **Step 2: Add Updated column to ValuePage**

In `ValuePage.tsx`, add an "Updated" column header to the table and render `relativeTime(row.odds_updated_at)` in each row. Place it as the last column.

- [ ] **Step 3: Add Updated column to DutchPage**

Same pattern — add "Updated" column showing freshness of the odds used in the dutch calculation.

- [ ] **Step 4: Add Updated column to ReversePage**

Same pattern.

- [ ] **Step 5: Add Updated column to PolymarketPage**

PolymarketPage already has `updated_at` in its data (from the polymarket route). Display it using the same `relativeTime` helper.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/
git commit -m "feat(frontend): add per-row Updated column with relative freshness timestamps"
```

---

## Task 9: Integration testing and verification

- [ ] **Step 1: Start backend and verify scheduler logs**

```bash
cd backend && python -m src.app serve
```

Check logs for:
- Each provider starting its own loop with correct interval
- Sharp providers running grouped every 1m
- API soft providers running individually every 15m
- Browser providers acquiring semaphore sequentially
- Boosts running on own 60m interval

- [ ] **Step 2: Start frontend and verify UI**

```bash
cd frontend && npm run dev
```

Verify:
- No ExtractionProgressBar visible
- FreshnessIndicator still shows age in filter bars
- Updated column visible on Value, Dutch, Reverse, Polymarket pages
- Timestamps color-coded correctly (green < 15m, default < 60m, yellow > 60m)

- [ ] **Step 3: Verify manual extraction trigger still works**

```bash
curl -X POST "http://localhost:8000/api/extraction/run?providers=pinnacle"
```

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: integration fixes for per-provider scheduling"
```
