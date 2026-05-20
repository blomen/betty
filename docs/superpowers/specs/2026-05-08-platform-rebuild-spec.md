# Mirror Platform Rebuild — Phased Spec

> **Type:** Active multi-phase platform spec. Replaces ad-hoc per-provider debugging with a systematic 5-phase plan.
> **Goal:** Two end-to-end working modes across all 16+ providers, with discovery as a tool not a manual ritual.
> **Estimated total:** 6-7 days work.

## What this is

Today's BETINIA drain session uncovered 17 bugs across one provider's arb path. The honest assessment is that we'd been **autofilling DOM elements when we don't need to** (mode A semi-auto means user clicks anyway) and **storing state in three out-of-sync places** (mirror in-memory, ephemeral SSE, React state).

This spec replaces ad-hoc per-provider debugging with 5 systematic phases. Each phase has one acceptance test. Phase outputs are reusable across all 16+ providers, not BETINIA-specific.

## Two-mode endgame

| Provider type | Mode | Trigger | User does |
|---|---|---|---|
| **Unlimited** (Polymarket, Kalshi, Cloudbet) | **Full auto value bets** via SDK/API | `autonomous_placement=True` | **Nothing** — runs 24/7 |
| **Pinnacle** (unlimited but no API) | **Guided value bets** | `autonomous_placement=False` | Click Place on Pinnacle tab |
| **Soft books** (BETINIA, QUICKCASINO, all 16+ Altenar/Kambi/Gecko/etc.) | **Mode-A semi-auto arb drain** | `autonomous_placement=False`, F17 path | Click outcome + Place on each tab |

The split is already encoded in `arnold/mirror/play_loop.py:UNLIMITED_PROVIDERS` and `WorkflowMode` on the workflow class. F17 (2026-05-07) wired the soft-book half. The unlimited half exists in code but isn't end-to-end verified.

---

## Phase 0 — Discovery framework (1 day)

### Why
Onboarding a new provider currently takes hours of manual JSONL grepping. With 16+ providers and bookmaker DOMs / APIs changing every few weeks, this is the unsustainable cost center. Phase 0 makes provider onboarding a **single guided session** that produces a config draft.

### Current process (per `docs/mirror-workflow.md` §13)
1. Open site in mirror; interceptor records `data/mirror_recordings/mirror/*.jsonl`
2. Manually: set language, log in, view history, navigate to event, place a small bet
3. Manually grep JSONL for endpoints
4. Manually write a workflow class

### New process
1. **`POST /mirror/discover/start/{provider_id}`** — opens fresh tab + enables verbose JSONL recording for that provider
2. **Operator-facing prompt UI** (frontend modal): "Now: 1) log in 2) view bet history 3) navigate to one event 4) place a 10 kr bet 5) press Done"
3. **`POST /mirror/discover/analyze/{provider_id}`** — runs the analyzer on the recorded JSONL + outputs a candidate config
4. **`GET /mirror/discover/result/{provider_id}`** — returns the config draft for operator review
5. **Operator commits** the draft to `data/mirror_intel/{provider_id}.json` (reviewed, edited if needed)

### The analyzer
Heuristic-first, LLM-enhanced. New file: `arnold/mirror/discovery.py`.

```python
def analyze_session(jsonl_path: str, known_balance: float, known_event_id: str) -> ProviderConfigDraft:
    """Process a discovery JSONL recording → candidate config."""
    requests = parse_jsonl(jsonl_path)
    return ProviderConfigDraft(
        balance_url=find_balance_endpoint(requests, known_balance),
        balance_json_path=infer_json_path(requests, known_balance),
        history_url=find_history_endpoint(requests),
        history_field_map=infer_history_fields(requests),
        placement_url=find_placement_endpoint(requests),
        placement_response_fields=infer_placement_response(requests),
        event_url_template=infer_event_url(requests, known_event_id),
        domain=parse_domain(requests),
        login_indicator=find_login_proof(requests),
    )
```

Heuristics:
- **Balance endpoint**: response body contains a number within 1% of `known_balance`. Track the JSON path.
- **History endpoint**: response is JSON or HTML containing repeated bet-shaped structures (odds, stake, status fields).
- **Placement endpoint**: POST request whose timing correlates with the operator clicking Place. Response contains a bet ID.
- **Event URL template**: search GET requests for `known_event_id` substring; extract template by replacing the ID with `{event_id}`.

LLM enhancement (Phase 0 stretch): send the heuristic draft + 50-line JSONL summary to Claude API, get back a refined config. Cost: ~$0.05 per discovery. Optional.

### Acceptance test
Run discovery on a known-working provider (BETINIA). Compare generated config against the existing `arnold/mirror/workflows/altenar.py` constants. Should match the canonical balance URL, history URL, placement URL, event URL template within ≤1 manual edit.

### Files
- `arnold/mirror/discovery.py` — analyzer (new, ~200 lines)
- `arnold/mirror/router.py` — 3 new endpoints (`/discover/start`, `/analyze`, `/result`)
- `arnold/frontend/src/pages/DiscoveryPage.tsx` — operator prompt UI (~120 lines)
- `data/mirror_intel/{provider_id}.json` — output format

### Open source we can lean on
- **Playwright trace viewer** (`playwright show-trace`) — visualize a recorded session, browse DOM at each action. Useful for operator review.
- **mitmproxy** — could replace the current JSONL recorder for richer HTTP capture (HTTP/2, WebSocket frames, query params). Optional swap.
- **HAR exports** — Chrome DevTools' built-in HTTP archive format. Could be the ingest format for the analyzer instead of custom JSONL.

---

## Phase 1 — F17 simplification across all soft workflows (1 day)

### Why
F17 (2026-05-07) proved that mode-A semi-auto doesn't need DOM autofill. We just navigate; user clicks; interceptor records. This eliminates ~10,000 lines of per-provider DOM matchers that were the source of every `no_match` / `wrong_page` / drift mis-click bug.

F17 currently only branches in `arb_runner._load_all_legs`. Phase 1 propagates to:
- `provider_runner` (value-bet runner) for guided unlimited (Pinnacle) — currently still tries `prep_betslip`, should also skip
- All 16 soft workflow `prep_betslip` methods become **dead code** — delete them

### Workflow surface after Phase 1

```python
class Workflow:
    # Required
    domain: str
    home_url: str
    autonomous_placement: bool  # True for SDK-based (Polymarket, Kalshi, Cloudbet)

    async def find_tab(context) -> Page | None  # default OK
    async def check_login(page) -> bool
    async def sync_balance(page) -> float
    async def sync_history(page) -> list[HistoryEntry]
    async def navigate_to_event(page, bet) -> bool

    # Required only if autonomous_placement=True
    async def place_bet(page, bet, stake) -> PlacementResult
```

`prep_betslip`, `check_live_price`, `update_slip_stake`, `read_slip_odds` — **all deleted** for guided workflows. Kept only on autonomous workflows that need them for SDK pre-checks.

### Acceptance test
Per provider in {BETINIA, QUICKCASINO, SPELKLUBBEN, UNIBET, COMEON}: with arnold running + provider tab logged in, the runner navigates to one event and emits `arb_leg_synced {guided: true}` immediately. No `prep_betslip` call. No `no_match` failure mode possible.

### Files
- `arnold/mirror/workflows/altenar.py` — strip prep_betslip + helpers (~150 lines deleted)
- `arnold/mirror/workflows/kambi.py` — same (~120 lines)
- `arnold/mirror/workflows/gecko.py` — same (~100 lines)
- `arnold/mirror/workflows/comeon.py` — same (~80 lines)
- `arnold/mirror/workflows/spectate.py` — same (~80 lines)
- ✅ `arnold/mirror/provider_runner.py` — branched on `autonomous_placement` 2026-05-08; guided path synthesizes a `prepped` PlacementResult without DOM touch but still calls `check_live_price` (Pinnacle convergence needs live event-page odds, which don't require a populated slip — only the prep DOM click is skipped). Deletion of the now-dead `prep_betslip` methods across 6 soft workflows is pure cleanup and deferred to a follow-up commit.

---

## Phase 2 — Server DB as authoritative state (2 days)

### Why
Three out-of-sync state sources: mirror in-memory, ephemeral SSE, React state. Every `arnold.bat` restart wipes everything. Browser refresh wipes React. SSE has no replay. We've spent half this session band-aiding state recovery (state-seeding effects, polling, etc.) and still hit stale-state bugs (today's stuck "Log in to continue" red badge while runner was at ready_to_run).

### New DB schema (server-side, ~50 lines migration)

```sql
CREATE TABLE mirror_provider_state (
    provider_id TEXT PRIMARY KEY,
    logged_in BOOLEAN DEFAULT FALSE,
    balance NUMERIC,
    balance_currency TEXT,
    tab_url TEXT,
    tab_open BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE mirror_runner_state (
    provider_id TEXT PRIMARY KEY,
    state TEXT,
    mode TEXT,
    current_arb_group_id TEXT,
    current_opp_id INT,
    last_idle_reason TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE mirror_event_log (
    id BIGSERIAL PRIMARY KEY,
    provider_id TEXT,
    event_type TEXT,
    data JSONB,
    ts TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_mirror_event_log_pid_ts ON mirror_event_log (provider_id, ts DESC);
```

### New server endpoints (~200 lines)

```
POST /api/mirror/provider-state        # mirror upsert on login/balance/tab change
POST /api/mirror/runner-state          # runner upsert on state transition
POST /api/mirror/event                 # mirror appends every SSE event
GET  /api/mirror/state                 # bulk fetch all providers (frontend mount)
GET  /api/mirror/state/{pid}           # single provider
GET  /api/mirror/events?since={ts}     # SSE replay on browser reconnect
```

### Local mirror writes to DB

`arnold/mirror/browser.py:_on_response` already POSTs to `/api/bankroll/set/{pid}` for balance. Extend the pattern:
- On `login_detected` → POST `/api/mirror/provider-state {logged_in: true}`
- On `tab_close` → POST `{tab_open: false}`
- On runner state change → POST `/api/mirror/runner-state`
- On every `broadcaster.publish(event)` → fire-and-forget POST `/api/mirror/event`

All writes are async/non-blocking; failures don't break the runner.

### Frontend reads from DB

```ts
// On mount and every 5s:
const state = await fetch('/api/mirror/state').json()
setProviderBalances(state.providers.map(p => [p.provider_id, p.balance]))
setLoopProviderStatus(state.runners.reduce(...))
setActiveProviders(new Set(state.runners.filter(r => r.state !== 'idle').map(r => r.provider_id)))
```

Drop the `/play/status` polling. SSE still drives sub-5s updates between polls; DB is the recovery floor.

### Acceptance test
1. Start arnold, log in, run a workflow to ready_to_run
2. Hard-refresh browser → frontend reads DB → cards show correct state immediately (no red flash)
3. Restart `arnold.bat` → frontend continues showing the cached state from DB until mirror reconnects and starts fresh writes
4. Run runner for 5 min, then `kill -9` arnold and grep `mirror_event_log` — every SSE event from the session is recorded

### Files
- ✅ Server: `backend/src/db/models.py` — added `MirrorProviderState`, `MirrorRunnerState`, `MirrorEventLog` ORM classes (auto-created on next deploy via `Base.metadata.create_all`)
- ✅ Server: `backend/src/api/routes/mirror_state.py` — 6 endpoints (`/api/mirror/provider-state`, `/runner-state`, `/event`, `/state`, `/state/{pid}`, `/events`), upserts use Postgres `ON CONFLICT` for atomicity
- ✅ Server: registered in `backend/src/api/routes/__init__.py` + `backend/src/api/__init__.py`
- ✅ Local: `arnold/mirror/state_writer.py` — fire-and-forget POSTs via tunnel_client, swallows all errors
- ✅ Local: `arnold/mirror/sse.py:MirrorBroadcaster.publish` mirrors every event to `/api/mirror/event`
- ✅ Local: `arnold/mirror/browser.py` balance/login intercept writes to `/api/mirror/provider-state`
- ⚠️ Local: explicit runner-state writes from `arb_runner.py` and `provider_runner.py` deferred (event log already captures `provider_ready`/`_running`/`_complete` so state is reconstructable; explicit writes are an optimization)
- ✅ Frontend: `arnold/frontend/src/hooks/useMirrorState.ts` — standalone hook reading from `/api/mirror/state` every 5s. Gracefully degrades to empty + error string if the endpoint isn't deployed yet (returns last-known state). Tree-shaken out of the bundle until something imports it; ready to wire into PlayPage to augment/replace the existing seed polling once the backend ships.
- ⚠️ Server deploy required for endpoints + tables to exist — local writers will silently fail with 404 until the backend ships, and `useMirrorState` will return empty + error

---

## Phase 3 — Verify autonomous unlimited end-to-end (2 days)

### Why
Capability matrix says Polymarket / Kalshi / Cloudbet are ✅ autonomous, but per "matrix lies" pitfall this is intent-not-state. No one's seen a value bet placed by the runner without intervention. For 24/7 unlimited mode this is the entire feature.

### Per-provider verification (each ~4 hours)

For each of {Polymarket, Kalshi, Cloudbet}:
1. Pre-condition: provider auth set up (private key / API key in env)
2. Start arnold; runner spawns; `provider_running` fires
3. Server returns ≥3 positive-edge value bets for the provider
4. Runner pops top bet → calls `workflow.place_bet(page, bet, stake)`
5. **`place_bet` uses SDK/API directly** — no DOM clicks, no Playwright interaction
6. Placement response parsed → bet recorded to `/api/bets`
7. Repeat for next bet
8. Run unattended for 1 hour; count placements vs expected; verify no placements during pauses, no missed bets during running

### Pinnacle decision
Pinnacle is in UNLIMITED but has no public API (discontinued). Two options:
- **(A) Keep guided** — Pinnacle plays as a value-bet target via `ProviderRunner` but `autonomous_placement=False`. Runner navigates to matchup; user clicks Place. Same UX as soft books.
- **(B) Reverse-engineer web placement** — capture the `bets/straight` POST body, replicate via Python httpx. High maintenance (Pinnacle changes auth headers periodically).

**Recommend (A)** — Pinnacle stays guided. Add Pinnacle to UI under "Guided value bets" alongside soft books.

### Acceptance test
Polymarket / Kalshi / Cloudbet each: 1 hour unattended → ≥1 successful autonomous value bet placed → no errors → recorded in DB → bet count matches `provider_run_metrics`.

### Files
- `arnold/mirror/workflows/polymarket.py` — verify `place_bet` calls SDK
- `arnold/mirror/workflows/strategies/kalshi.py` — verify Kalshi REST POST works
- `arnold/mirror/workflows/strategies/cloudbet.py` — verify Cloudbet API POST works
- Possibly Phase 3.5: decouple Kalshi + Cloudbet from Playwright entirely (no tab needed for pure-API providers — server-side worker)

---

## Phase 4 — Daily smoke test cron per provider (0.5 days)

### Why
"Capability matrix lies" — checkmarks rot silently. By the time we discover a workflow is broken, we've lost a debug session. A daily cron runs the §12 acceptance checklist against each provider and updates the matrix automatically.

### Implementation

Server cron (`backend/src/jobs/mirror_smoke.py`):
- Runs once per day at 02:00 UTC (low-traffic window)
- For each provider with non-empty intel:
  - Probe `check_login` via cached session
  - Probe `sync_balance` — record value
  - Probe `sync_history` — record count
  - Probe `navigate_to_event` for one canonical test event
- Writes results to new table `mirror_provider_health`

Frontend §9 matrix replacement: read `mirror_provider_health` instead of static markdown. Renders ✅ if last 7 days all green; ⚠️ if intermittent; ❌ if 3+ consecutive failures.

### Acceptance test
After 1 day of cron runs, the §9 matrix in the UI shows live status. Manually break a workflow (e.g., point balance URL at /404); within 24h the matrix flips to ❌.

### Files
- ✅ Server: `backend/src/db/models.py` — `MirrorProviderHealth` ORM table (per-provider snapshot, rewritten by recompute, fed by `mirror_event_log` + cron home_url probes)
- ✅ Server: `backend/src/api/routes/mirror_state.py` — added `GET /api/mirror/health` (bulk snapshot for frontend §9 matrix) and `POST /api/mirror/health/recompute` (idempotent recompute from event log; safe to call ad-hoc or from a daily cron)
- ✅ Server: `backend/src/jobs/mirror_smoke.py` — daily cron landed. Async loop wakes every `MIRROR_SMOKE_INTERVAL_S` (default 24h), parallel-HTTP-probes every provider's `home_url` from `providers.yaml`, then recomputes event-derived health from `mirror_event_log`. Wired into FastAPI lifespan via `asyncio.create_task(smoke_loop())` in `backend/src/api/__init__.py:570`. Verified: imports OK, discovers 36 providers from yaml.
- ✅ Frontend: `arnold/frontend/src/components/MirrorMatrix.tsx` — live-data table reading `/api/mirror/health` every 30s, with a "recompute now" button that POSTs `/api/mirror/health/recompute`. Wired as a third sub-tab "Health" alongside Value Bets / Arbitrage in PlayPage. Bundle grew 414 → 420 KB. Pre-deploy: gracefully degrades to empty + error hint.

---

## What this spec is NOT

- **Not a redesign of extraction or scanning.** Server-side extraction + arb scanner stay as-is. They work.
- **Not a swap to Skyvern / Browser-Use / LLM agents.** Considered and rejected: too slow + costly per action for 24/7 autonomous, still flaky, the F17 simplification + Phase 0 discovery is cheaper.
- **Not a swap to a commercial scanner (BetBurger / RebelBetting).** Considered: cheapest if you only want drain, but you lose customization (target specific bonus mechanics, drain ordering, prefer Pinnacle hedge, etc.).
- **Not a rewrite of Playwright/FastAPI/React.** The stack is fine; the application of it was over-engineered.

## Phase ordering rationale

```
0 (Discovery)  ─┐
                ├─→ Phase 1 can start once Phase 0 lets us re-derive any provider config quickly
1 (F17 sweep)  ─┘
                ↓
2 (DB state) — independent, can run in parallel with Phase 1
                ↓
3 (Verify autonomous) — needs Phase 1 done (clean place_bet contract) AND Phase 2 done (state writes for unattended runs)
                ↓
4 (Smoke cron) — needs Phase 2's mirror_provider_health table; verifies Phase 3's unlimited stay healthy
```

If you want maximum velocity: Phase 0 first (frees future onboarding), then Phase 1 + Phase 2 in parallel, then Phase 3, then Phase 4. **Total: 4 calendar days if parallelized, 6.5 sequential.**

## Retirement criteria

This spec retires when:
- All 4 phases ✅ in their acceptance tests
- §9 capability matrix is auto-generated from `mirror_provider_health` (stops being a doc, becomes a query)
- BETINIA balance ≤ 50 kr (drain proven via real placements)
- 1 week of unattended autonomous Polymarket/Kalshi value betting with zero manual intervention

At retirement, the active drain spec (`2026-05-06-betinia-drain-workflow.md`) and this rebuild spec both move to `docs/superpowers/specs/archive/`.

## Notes on session sustainability

The 2026-05-06/07 session debugged 17 bugs across 6+ hours and exhausted the user. Process learnings to bake into the next session:

1. **Don't iterate on autofill bugs** — F17 made them moot. If you find yourself debugging `prep_betslip`, you're in the wrong code path.
2. **Trust /api/mirror/state** (Phase 2) — never poll multiple state sources and try to merge.
3. **Write to spec mid-session, not at end** — every bug-fix should land an F-row in the spec's "Bugs found and fixed" table at write time. Doc + code diff in one commit.
4. **Restart fatigue is real** — every `arnold.bat` restart wastes 30s of relogin. F15 reduced wedge-restart frequency. F16 reduced tab-loss-restart frequency. Phase 2 (DB state) eliminates "I lost my state" restarts entirely.
