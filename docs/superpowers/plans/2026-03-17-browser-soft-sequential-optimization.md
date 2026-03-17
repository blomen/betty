# Browser Soft Sequential Execution + ComeOn Optimization

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix chronic browser_soft tier failures (4/5 providers timing out) by running browser providers sequentially and adding provider-level time budgets to ComeOn.

**Architecture:** Change `browser_soft` tier from parallel `asyncio.gather` to sequential execution via a new `sequential` flag on the orchestrator. Add provider-level time budget and sport reordering to ComeOn's extract loop. Reduce football league cap.

**Tech Stack:** Python 3.10+ / asyncio / Playwright / Camoufox

---

## Context

**Problem:** The `browser_soft` tier runs 5 browser providers in parallel (capped at 3 browser slots). Resource contention causes 4/5 to chronically timeout:
- **ComeOn**: 0/5 success — football+basketball each burn 360s for 0 events, consuming the 900s provider timeout
- **Interwetten**: 2/5 success — 700s timeout is borderline
- **10bet**: 2/5 success — 1200s timeout is borderline
- **Coolbet**: 4/5 success — occasionally fails under contention
- **888sport**: 5/5 success — API-based, always fast

**Solution:** Run browser providers one-at-a-time (each gets full CPU/RAM) and fix ComeOn's sport loop to prioritize fast sports and enforce a provider-level time budget.

**Files overview:**
- Modify: `backend/src/pipeline/orchestrator.py` — add sequential execution mode
- Modify: `backend/src/config/providers.yaml` — add `sequential: true` to `browser_soft` tier + reduce ComeOn football league cap
- Modify: `backend/src/providers/comeon_multileague.py` — provider-level time budget + sport reordering

---

## Task 1: Add sequential execution mode to orchestrator

**Files:**
- Modify: `backend/src/pipeline/orchestrator.py:868-881`

Currently all providers run via `asyncio.gather` (line 880-881). Add a `sequential` flag that runs providers one-at-a-time instead.

- [ ] **Step 1: Add `sequential` parameter to `run()` method**

In `backend/src/pipeline/orchestrator.py`, add `sequential: bool = False` parameter to the `run()` method signature at line 442:

```python
async def run(
    self,
    polymarket: bool | None = None,
    providers: list[str] | None = None,
    max_events_per_sport: int = 9999,
    on_progress: Callable[[str], None] | None = None,
    tier_name: str | None = None,
    sequential: bool = False,
) -> dict:
```

- [ ] **Step 2: Replace gather block with sequential/parallel branch**

Replace lines 879-881 in `backend/src/pipeline/orchestrator.py`:

```python
# OLD:
provider_tasks = [extract_with_concurrency_limit(pid) for pid in available_providers]
provider_results_list = await asyncio.gather(*provider_tasks)
```

With:

```python
if sequential:
    # Sequential mode: run one provider at a time (browser_soft)
    # Each provider gets full CPU/RAM — no browser slot contention
    provider_results_list = []
    for pid in available_providers:
        log_progress(f"[{pid}] Starting (sequential mode, {len(provider_results_list)+1}/{len(available_providers)})")
        result = await extract_with_error_handling(pid)
        provider_results_list.append(result)
else:
    # Parallel mode: run all providers concurrently with pool limits
    provider_tasks = [extract_with_concurrency_limit(pid) for pid in available_providers]
    provider_results_list = await asyncio.gather(*provider_tasks)
```

Note: sequential mode skips `extract_with_concurrency_limit` entirely — no semaphore needed when running one at a time.

- [ ] **Step 3: Commit**

```bash
git add backend/src/pipeline/orchestrator.py
git commit -m "feat(orchestrator): add sequential execution mode for browser providers"
```

---

## Task 2: Wire sequential flag from providers.yaml through scheduler

**Files:**
- Modify: `backend/src/config/providers.yaml:848-865` — add `sequential: true` to `browser_soft`
- Modify: `backend/src/pipeline/scheduler.py:976-1026` — pass sequential flag through

- [ ] **Step 1: Add sequential flag to browser_soft tier config**

In `backend/src/config/providers.yaml`, add `sequential: true` to the `browser_soft` tier:

```yaml
  browser_soft:
    # Heavy browser-based extractors — every 60 minutes
    # Runs on startup, then every 60 minutes
    # Sequential mode: one provider at a time to avoid browser resource contention
    sequential: true
    providers:
      # ... (unchanged)
```

- [ ] **Step 2: Pass sequential flag from scheduler to pipeline**

In `backend/src/pipeline/scheduler.py`, update `_run_with_state_updates` (line 976) to accept and pass the sequential flag:

```python
async def _run_with_state_updates(self, providers: list[str], tier_name: str = "default", sequential: bool = False) -> dict:
```

Then at line 1026 where it calls `tier_pipeline.run()`:

```python
_results = await tier_pipeline.run(providers=providers, tier_name=tier_name, sequential=sequential)
```

- [ ] **Step 3: Pass sequential from tier config in _tier_loop**

In `backend/src/pipeline/scheduler.py`, update the `start_tier` method (line 92) to accept `sequential`:

```python
async def start_tier(
    self,
    name: str,
    providers: list[str],
    interval_seconds: int,
    run_immediately: bool = True,
    wait_for_sharp: bool = False,
    sequential: bool = False,
):
```

Add `sequential` to `TierState` dataclass (line 24):

```python
@dataclass
class TierState:
    """Tracks state for a single extraction tier."""
    name: str
    providers: list[str]
    interval_seconds: int
    running: bool = False
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    last_run: Optional[datetime] = None
    run_count: int = 0
    sequential: bool = False
```

Pass `sequential` when creating `TierState` (line 113-118):

```python
tier = TierState(
    name=name,
    providers=providers,
    interval_seconds=interval_seconds,
    running=True,
    sequential=sequential,
)
```

Pass in `_tier_loop` at line 213:

```python
results = await self._run_with_state_updates(tier.providers, tier_name=tier.name, sequential=tier.sequential)
```

And in `start_all()` at line 301, read from config:

```python
for tier_name, tier_config in tiers.items():
    providers = [p for p in tier_config.get("providers", []) if p not in disabled]
    interval_minutes = tier_config.get("interval_minutes", 60)
    wait_for_sharp = tier_name != "sharp"
    sequential = tier_config.get("sequential", False)

    await self.start_tier(
        name=tier_name,
        providers=providers,
        interval_seconds=interval_minutes * 60,
        run_immediately=True,
        wait_for_sharp=wait_for_sharp,
        sequential=sequential,
    )
```

- [ ] **Step 4: Pass sequential in _tier_loop signature**

Update `_tier_loop` (line 173) and `_restart_tier` (line 149) to propagate the sequential flag:

In `_tier_loop` signature:

```python
async def _tier_loop(self, tier: TierState, run_immediately: bool, wait_for_sharp: bool = False):
```

No change needed — `tier.sequential` is already on the TierState object, and it's read at line 213 via `tier.sequential`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/config/providers.yaml backend/src/pipeline/scheduler.py
git commit -m "feat(scheduler): wire sequential flag from providers.yaml to orchestrator"
```

---

## Task 3: ComeOn provider-level time budget + sport reordering

**Files:**
- Modify: `backend/src/providers/comeon_multileague.py:141-187` — add time budget + reorder sports
- Modify: `backend/src/config/providers.yaml:614-641` — reduce football league cap

- [ ] **Step 1: Add sport priority ordering to ComeOn**

In `backend/src/providers/comeon_multileague.py`, add a class constant after `SPORT_URL_MAP` (after line 48):

```python
# Sports ordered by extraction speed (fastest first).
# Tennis/handball/mma complete in <60s. Football/basketball often timeout at 360s.
# Extracting fast sports first ensures we get data before provider timeout hits.
SPORT_PRIORITY = [
    'tennis', 'mma', 'handball', 'esports', 'cricket',
    'table_tennis', 'rugby', 'baseball', 'american_football',
    'ice_hockey', 'basketball', 'football',  # slowest last
]
```

- [ ] **Step 2: Add provider-level time budget to extract() sport loop**

Replace the sport loop in `extract()` at lines 167-187:

```python
all_events = []
sports_attempted = 0
provider_timeout = self.config.get("provider_timeout", 900)
provider_start = time.time()

# Sort sports by priority (fast sports first)
sports_to_extract = sorted(
    sports_to_extract,
    key=lambda s: self.SPORT_PRIORITY.index(s) if s in self.SPORT_PRIORITY else 99,
)

for sport_key in sports_to_extract:
    # Provider-level time budget: stop starting new sports at 80% of provider timeout
    elapsed = time.time() - provider_start
    if elapsed > provider_timeout * 0.80:
        logger.warning(
            f"[{self.provider_id}] Provider time-budget exit at {elapsed:.0f}s "
            f"({sports_attempted} sports, {len(all_events)} events). "
            f"Skipping remaining: {sports_to_extract[sports_to_extract.index(sport_key):]}"
        )
        break

    try:
        sports_attempted += 1
        sport_events = await self._extract_single_sport(
            sport_key, target_leagues=target_leagues, limit=limit
        )
        logger.debug(f"[{self.provider_id}] {sport_key}: {len(sport_events)} events")
        all_events.extend(sport_events)
    except Exception as e:
        logger.error(f"[{self.provider_id}] Failed to extract {sport_key}: {e}")

if not all_events:
    raise RetryableError(
        f"0 events from {sports_attempted} sport(s) — possible page/SPA failure",
        provider_id=self.provider_id,
    )

return all_events
```

- [ ] **Step 3: Reduce football league cap**

In `backend/src/providers/comeon_multileague.py`, update `SPORT_LEAGUE_CAPS` (line 218):

```python
SPORT_LEAGUE_CAPS: Dict[str, int] = {
    "football": 15,     # Reduced from 30 — top 15 cover ~90% of Pinnacle matches
    "basketball": 15,   # Reduced from 20
    "ice_hockey": 15,   # Reduced from 20
    "tennis": 15,       # Reduced from 20
}
DEFAULT_LEAGUE_CAP = 10  # Reduced from 15
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/providers/comeon_multileague.py
git commit -m "fix(comeon): add provider-level time budget and sport priority ordering"
```

---

## Task 4: Reorder browser_soft providers in YAML (fastest first)

**Files:**
- Modify: `backend/src/config/providers.yaml:848-865`

- [ ] **Step 1: Reorder providers in browser_soft tier**

In sequential mode, order matters — fastest providers run first to get data into the DB sooner. Update the `browser_soft` tier:

```yaml
  browser_soft:
    # Heavy browser-based extractors — every 60 minutes
    # Sequential mode: one provider at a time to avoid browser resource contention
    # Ordered fastest-first so data is available sooner
    sequential: true
    providers:
      # Spectate — API-based, fastest (30-400s)
      - 888sport
      # Coolbet — Camoufox, reliable (170-470s)
      - coolbet
      # Interwetten — 16-20 concurrent tabs (500-700s)
      - interwetten
      # ComeOn Group — Camoufox + SPA scraping (700-900s)
      - comeon
      # 10Bet — DOM scraping, slowest (1000-1200s)
      - 10bet
    interval_minutes: 60
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/config/providers.yaml
git commit -m "perf(browser_soft): sequential execution, ordered fastest-first"
```

---

## Task 5: Verify end-to-end

- [ ] **Step 1: Start backend and verify scheduler logs**

```bash
cd backend
python -m src.app serve
```

Watch logs for:
- `[Scheduler] Starting tier 'browser_soft': ... sequential=True`
- `[Scheduler:browser_soft] Waiting for sharp tier to complete first run...`
- After sharp completes: `[888sport] Starting (sequential mode, 1/5)`
- Each provider starts only after the previous one finishes

- [ ] **Step 2: Monitor first browser_soft run**

Watch for:
- 888sport completes first (~30-400s)
- coolbet starts after 888sport finishes
- comeon logs show sport ordering: tennis first, football last
- comeon logs show provider time-budget exits instead of hard timeouts
- All 5 providers complete (even if some have partial data)

- [ ] **Step 3: Query extraction results**

After the run completes, query via sqlite MCP:

```sql
SELECT provider_id, status, events_processed, duration_seconds, error_message
FROM provider_run_metrics
WHERE run_id = (SELECT id FROM extraction_runs WHERE trigger = 'browser_soft' ORDER BY start_time DESC LIMIT 1)
ORDER BY start_time
```

Expected: all 5 providers show `status='success'` or at least partial events (vs current 4/5 failures).
