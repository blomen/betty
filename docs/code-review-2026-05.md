# Betty Code Review — May 2026

Deep audit across **backend** (`backend/src/`, ~50k LOC), **local client** (`local/`, ~10k LOC), and **frontend** (`frontend/src/`, dominated by one 4339-line file). Three parallel reviewers each produced opinionated findings; this document synthesizes them into a single prioritized roadmap.

The lead with the loudest impact: there is at least one **money-at-risk currency bug in the arb runner**, and two query-correctness bugs that should be invisible-failing in production right now. Those go first.

---

## P0 — Money at risk / production correctness (fix this week)

### 1. Cross-currency arb sizing is broken
`local/mirror/arb_math.py:20` — `recalc_counter_stakes(anchor_stake, anchor_odds, counter_odds)` takes raw floats with no currency awareness. Callers in `local/mirror/arb_runner.py:540, 682, 836, 929` pass mixed currencies (SEK anchor + USDC/USD counters) without conversion. With USDC/SEK ≈ 0.09, a SEK-funded anchor paired with a Polymarket or Kalshi counter sizes the counter ~10× too large. Worst-case payout math at `arb_runner.py:925-944` has the same defect. Grep on `arb_runner.py` returns zero hits for `to_sek`, `exchange_rate`, or `currency` — this is exactly the "off-by-5-10×" hypothesis CLAUDE.md flags first.

**Fix:** introduce `local/mirror/currency.py` with `(provider_id) → currency` lookup and `convert(amount, src, dst)`. Make `arb_math` take currency-tagged legs and convert everything to one base before computing. As a *guard* in the meantime, assert single-currency at the entry of `recalc_counter_stakes` so the function fails loudly instead of silently sizing wrong.

### 2. Postgres bool-equals-integer in raw SQL — query throws
`backend/src/services/play_service.py:245` — `WHERE type = 'value' AND is_active = 1` against `opportunities.is_active BOOLEAN`. Postgres rejects `boolean = integer` with `operator does not exist`. Fix: `is_active = TRUE`.

Sibling bug: `backend/src/ml/serving/predictor.py:39` does `filter_by(is_active=1)` against `MlModelRegistry.is_active` which is wrongly defined as `Integer` (`db/models.py:1680`). Change the column to `Boolean` and the filter to `True`.

### 3. Cross-currency `sum(stake)` in `/api/bets` summary
`backend/src/api/routes/bets.py:469-470` — `summarize()` sums `r.stake` and `r.payout - r.stake` over a `Bet` query that has no `provider_id` filter when `provider_id is None`. SEK + USD + USDC stakes added as raw floats → `staked`, `profit`, `roi_pct` all wrong by ~10× on mixed sets. Sibling: `backend/src/services/batch_builder.py:967, 1443` ships a `total_stake` over a multi-provider batch without conversion.

**Fix:** group by currency in the SQL, or `to_sek(...)` per row before summing.

### 4. State-machine violation — auto-nav in `/poll-portfolio`
`local/mirror/router.py:860, 883` does `await page.goto(url)` to portfolio + history URLs inside `/poll-portfolio/{provider_id}`. CLAUDE.md mirror-invariant: "Only `navigate_to_event` is auto. Everything else passive." This endpoint will clobber an open betslip mid-place. Either delete it (reactive sync covers the case) or guard with the `/event/` URL check from `pending_loop.py:353`.

### 5. `provider_runner._record_unknown_open_bets` uses the OLD buggy dedup
`local/mirror/provider_runner.py:1915-2012` dedups on `(odds, stake)` set only — not `provider_bet_id` first, not Counter-based, no within-call insert tracking. This is the pre-fix shape that caused the Betinia ×3 and ×22 paginated-history dups. `pending_loop.py:479-549` has the correct version. **Quick fix:** delete the runner's local copy, delegate to `pending_loop._record_unknown_open_bets`.

### 6. Missing `placedEventMarketKeys` filter on the Arb tab
`frontend/src/pages/PlayPage.tsx:3388-3402` — the arb-tab `opps.filter(…)` checks `drainedEventIds`, profit, liquidity, ttk — but NOT the placed-bet blacklist. The value tab has the elaborate `valueBetBlacklist` (2151-2198) with `1x2 ↔ moneyline` normalisation. CLAUDE.md mirror-invariant explicitly says the arb table must derive `placedEventMarketKeys` from `pendingByProvider` and filter. Today, a placed arb keeps re-appearing as clickable until the event drains.

**Fix:** rename `valueBetBlacklist` → `placedBlacklist`, apply in both tabs.

### 7. PlayPage setter inside setter — StrictMode double-write
`frontend/src/pages/PlayPage.tsx:1774, 1880` — `setLiveLegOdds(om => …)` called *inside* the updater function passed to `setOppsByCluster(prev => …)`. Updaters must be pure; React 18 StrictMode runs them twice and React 19 concurrent batching changes ordering. Move the `setLiveLegOdds` call out of the updater.

---

## P1 — High-impact refactors (next 2-4 weeks)

### A. Split `PlayPage.tsx` (4339 lines → ~12 files under `pages/play/`)

This is the single biggest payoff in the codebase. Concrete extraction:

| New file | What it owns | Source lines |
|---|---|---|
| `pages/play/PlayPage.tsx` | Shell, subtab switcher, layout | ~200 lines |
| `ValueBetsTab.tsx` | `subTab === 'value'` branch | 3899-4292 |
| `ArbTab.tsx` | `subTab === 'arb'` wrapper | 2579-3896 |
| `ArbClusterBlock.tsx` | One funded cluster card | ~2912-3889 |
| `ArbRow.tsx` | Single row of the arb table | 3403-3881 |
| `ArbStakeCalculator.tsx` | Picked-event stake widget | 3030-3260 |
| `PendingRow.tsx` | **Unified** pending-bet row | 3290-3375 + 4140-4220 |
| `BalanceCell.tsx` | Already inline at 176-204 — extract + `memo()` |
| `AnnotationBadges.tsx` | Lines 2294-2339 |
| `hooks/useArbOpps.ts` | Owns `oppsByCluster`, persistence, live-override apply | 565-1198 |
| `hooks/useMirrorEvents.ts` | Discriminated-union dispatch, replaces the 600-line `if (type === …)` chain | 1451-2051 |
| `hooks/usePersistedSet.ts` | Generalises drainedEventIds, enabledCounters, liveLegOdds patterns |
| `hooks/useProviderLoginPolling.ts` | Collapses both 10s polls (unlimited 1215, soft 1319) into one |

This refactor also **kills the divergent-PendingRow risk** mentioned in CLAUDE.md mirror invariants (two render sites with subtly different cols).

### B. Mount tabs conditionally
`App.tsx:78-94` uses `className="hidden"` to switch tabs, leaving PlayPage's ~30 `useEffect`s running while the user is on Bankroll or Stats. SSE traffic + polling load is 100% nominal even with PlayPage off-screen. Change to conditional render `{activeTab === 'play' && <PlayPage/>}`, or move SSE/polling into a top-level provider that pauses when `document.hidden` or `activeTab !== 'play'`.

### C. Delete the backend `mirror/` subtree (~3000 lines dead)
`backend/src/mirror/*` (workflows/, parsers/, interceptor.py, service.py) + `backend/src/api/routes/mirror.py` (1246 lines) duplicate `local/mirror/`. The frontend talks to LOCAL `/mirror/*`, not `/api/mirror/*` — only a handful of endpoints in `mirror_state.py` and `mirror_stream.py` are actually consumed. CLAUDE.md is explicit: "The server has no frontend… All betting happens through the local client."

Keep only what `fire_window.py` and the mirror state endpoints need. Extract `_get_active_mirror` somewhere small. Delete the rest.

### D. Split `local/mirror/router.py` (2660 → ~600 lines)
The biggest god-file in the local client. Proposed split:

```
local/mirror/
├── router.py                    (FastAPI wiring + composition, ~400 lines)
├── picked_opps.py               (lines 27-96)
├── live_odds_sync.py            (consolidates _poll_live_price + _poll_guided_live_price)
├── reactive_sync.py             (lines 369-540 — _on_browser_event + _reactive_history_sync)
└── routes/
    ├── portfolio.py             (lines 644-1037)
    ├── play.py                  (lines 1675-2393)
    └── diag.py                  (lines 1039-1428 — debug endpoints, env-gated)
```

Bonus: kill the literal `if False and …` dead branch at `router.py:2324-2341`.

### E. Split `backend/src/analysis/scanner.py` (2227 lines, one god class)
`OpportunityScanner` has 30+ methods covering value, arb, reverse value, bonus, spread anomaly detection, platform-conflict resolution. Split:

- `analysis/scanners/value.py` → `ValueScanner`
- `analysis/scanners/arb.py` → `ArbScanner`
- `analysis/scanners/bonus.py` → `BonusScanner`
- `analysis/filters.py` → all 8 `_drop_*` / `_filter_*` / `_fix_*` methods (1444, 1505, 1544, 1608, 1999, 2041) become a `MarketFilterPipeline`
- shared base in `analysis/scanners/base.py`

### F. Provider extractor base class
19 `*Retriever` classes in `backend/src/providers/` repeat the same sport-iteration / error-wrap / scope-map / skip-live-events pattern. Altenar, Vbet, Smarkets all run on BetConstruct — near-trivial common base. Currently only `mixins/rsocket.py` exists; ~1500 lines of duplication waiting to be extracted.

### G. Retire legacy workflows
`local/mirror/workflows/altenar.py` and `kambi.py` overlap with `workflows/strategies/altenar.py` (and the strategies platform). Altenar exists in both forms; Kambi is legacy-only. Finish the migration to intel-JSON + strategy override, kill the legacy classes.

### H. Split the other backend mountains
- `pipeline/orchestrator.py` (2103 lines) — extract `DeferredResolver` (82-218), `ExtractionTierRunner` (1000-1200), `OrchestratorAnalysisRunner` (1340-1500)
- `services/fire_window.py` (1763 lines) — extract `FireWindowStore`, `ClusterMerger`, live-edge checking
- `services/batch_builder.py` (1448 lines) — separate summary, cluster stats, missed-bet analytics
- `pipeline/storage.py` (1804 lines) — split `store_polymarket_event`, `store_provider_event`, `OddsBatchProcessor` into `pipeline/storage/*.py`
- `local/mirror/browser.py` (1522) → `browser/{interception,lifecycle,security}.py`
- `local/mirror/arb_runner.py` (1375) → `arb_runner/{leg_loader,anchor_streaming,hedge,currency}.py`

---

## P2 — Performance & polish (continuous)

### Backend
- **Hoist hot-path ML imports** — `scanner.py:354-355, 1845-1849`, `orchestrator.py:438, 1344, 1354, 1466`, `analyzer.py:230`: imports inside per-outcome / per-tier loops. Mechanical edit, no behavior change.
- **OddsBatchProcessor backoff jitter** — `pipeline/storage.py:1068, 1553`, `analyzer.py:230`, `orchestrator.py:202, 1840` use `time.sleep` after deadlock rollback (acceptable; runs in `to_thread`). Randomize backoff to break deadlock cycles faster.
- **`backend/src/providers/_template.py`** — scaffolding, not registered. Delete or move to `docs/`.
- Confirm which ComeOn extractor is live: `comeon_dom_js`, `comeon_dom_parser`, `comeon_multileague` — likely 2 of 3 are dead.

### Local client
- **Delete dead `PendingLoop._run` polling chain** in `pending_loop.py:180-216` — `start()` never called from anywhere (~250 lines).
- **Delete orphaned modules** — `local/mirror/recorder.py` (whole file, replaced by `recorders/`), `local/mirror/discovery_v2.py` (zero outside imports). ~600 lines.
- **Fix `_reactive_history_sync` TOCTOU** — `router.py:427` `if lock.locked(): return` then `async with lock` at :445. Replace with `wait_for(lock.acquire(), timeout=0)` or drop the pre-check (the 5s debounce is the real gate).
- **`slip_odds_stream` backoff + diff** — `slip_odds_stream.py:108` polls 1Hz per leg with no change-detection or backoff. ~10 active legs = 10 Hz of `/api/odds/live-update` through the SSH tunnel even when nothing moved.

### Frontend
- **Memoise derived chains** in PlayPage 2200-2281 (`bets`, `softProviders`, `byCluster`, `clusterIds`, `totalEv`, `totalPending`). They run on every keystroke into a stake input today.
- **Stable per-row callbacks** — `onRowClick`, `navigateLeg`, `resolveOutcome` in 3403-3881 recreate ~50 closures per render-row × cluster. After ArbRow extraction these become `useCallback`.
- **`pickedLegMetaByProvider.current` mutated during render** at line 3568, iterated at 3557. Move to effect/callback.
- **Merge `hooks/useApi.ts` into `services/api/`** — PlayPage is the only consumer of the legacy `api` object; every other file imports `@/services/api`. Different method sets → two API surfaces. Migrate, delete `hooks/useApi.ts`.
- **Convert `useMirrorState` to react-query** (or similar) — currently polls `/api/mirror/state` every 5s with no cache, no abort, no error backoff (`hooks/useMirrorState.ts:69`).
- **Reuse `connectionManager`** — PlayPage rolls its own 10s/3s `load()` polling at lines 800-821, duplicating logic that already exists with proper boot_id detection.
- **Type the SSE event union** — define `MirrorEventMap` discriminated union in `types/mirror.ts`. Eliminates ~30% of `any` casts in PlayPage.

### Quick wins (≤1h each)

1. Fix `play_service.py:245` `is_active = 1` → `is_active = TRUE`.
2. Fix `predictor.py:39` `is_active=1` → `is_active=True`.
3. Replace `bets.py:469-470` with per-currency summaries (or require `provider_id`).
4. Move `setLiveLegOdds` calls out of `setOppsByCluster` updaters at PlayPage 1774, 1880.
5. Apply the `valueBetBlacklist` filter to the Arb tab at PlayPage 3388-3402.
6. Delete `local/mirror/router.py:2324-2341` (`if False and …` dead block).
7. Delete the dead `PendingLoop._run` chain (~250 lines).
8. Delete `local/mirror/recorder.py` and `discovery_v2.py` (~600 lines).
9. Replace `chat.py:61` bare `except:` with `except (json.JSONDecodeError, KeyError):`.
10. Hoist ML imports in `scanner.py` and `orchestrator.py` hot loops to module top.
11. Delete or guard `/poll-portfolio` endpoint in `local/mirror/router.py:830-946`.
12. Add currency-precondition assertion in `arb_math.recalc_counter_stakes` (stop-gap before #1 P0 fix).

---

## Ranked task list (effort vs payoff)

Ordered so the top items move the most needle per hour.

| # | Task | Effort | Payoff |
|---|---|---|---|
| 1 | **Plumb currency through arb sizing** — `local/mirror/arb_math.py` + `arb_runner.py` + new `local/mirror/currency.py`. Single most important change in this report. | 1–2 d | Stops money-at-risk |
| 2 | Fix raw-SQL `is_active = 1` in `play_service.py:245` + `predictor.py:39` | 10 min | Eliminates failing queries |
| 3 | Fix cross-currency sum in `bets.py:summarize` + `batch_builder.py:967` | 1 h | Stats numbers actually correct |
| 4 | Delete `page.goto` in `/poll-portfolio` (or delete endpoint) | 30 min | Stops slip-clobber state-machine violation |
| 5 | Replace `provider_runner._record_unknown_open_bets` with `pending_loop._record_unknown_open_bets` | 30 min | Eliminates duplicate-row regression risk |
| 6 | Apply placed-bet blacklist to Arb tab (PlayPage 3388) | 30 min | Stops "already-placed arb keeps re-appearing" |
| 7 | Move `setLiveLegOdds` out of updater (PlayPage 1774, 1880) | 15 min | Removes StrictMode double-write bug |
| 8 | Conditional-mount tabs in `App.tsx:78` | 30 min | ~⅔ SSE+polling load reduction when off-PlayPage |
| 9 | Memoise PlayPage derived chains (2200-2281) | 1 h | Eliminates payout-input keystroke jank |
| 10 | Delete dead local code: `pending_loop._run` + `mirror/recorder.py` + `discovery_v2.py` + `router.py if False` block | 1 h | -1000 lines, removes footguns |
| 11 | Split `PlayPage.tsx` into `pages/play/` per the plan above | 3–5 d | Maintainability + perf |
| 12 | Split `local/mirror/router.py` per the plan above | 2–3 d | Maintainability + testability |
| 13 | Delete backend `mirror/` subtree + audit `routes/mirror.py` | 1–2 d | -3000 lines, removes confusion |
| 14 | Hoist hot-path ML imports + add deadlock backoff jitter | 1 h | Modest perf, easy |
| 15 | Split `analysis/scanner.py` into Value/Arb/Bonus scanners + `MarketFilterPipeline` | 2–3 d | Maintainability of the most critical analytical surface |
| 16 | Provider extractor base class (consolidate ~1500 lines of duplication) | 3–5 d | Future provider work much faster |
| 17 | Merge `hooks/useApi.ts` into `services/api/` + type SSE event union | 1 d | Type safety + one API surface |
| 18 | Retire legacy `local/mirror/workflows/{altenar,kambi}.py` in favor of strategies | 2–3 d | Workflow split becomes coherent |
| 19 | Convert `useMirrorState` to react-query; reuse `connectionManager` from PlayPage | 1 d | Reliability + dedup |
| 20 | Split remaining 1000+ line backend files (`orchestrator`, `fire_window`, `batch_builder`, `storage`, `browser`, `arb_runner`) | 1–2 wk | Steady payoff over time |

---

## Suggested next move

Items 1–10 are all ≤1 hour each except #1 (the arb currency plumb). That's 5–6 hours of work covering every P0 + the highest-value cleanups. **Do those first.**

After that, choose between two bigger-effort tracks based on what's hurting more right now:

- *PlayPage refactor* (item 11) if you're spending time fighting the file.
- *Backend `mirror/` deletion + scanner split* (items 13 + 15) if you want to materially reduce server surface area.

The provider base class (item 16) is the highest-leverage backend refactor for *future* work, but doesn't unlock anything urgent today.

---

# Second-Pass Cross-Validation — May 2026

Three new independent agents repeated the audit with no knowledge of the first pass. The diff is below: **confirmed findings**, **new findings the first pass missed**, **one reversal** (the first pass got the backend `mirror/` subtree exactly backwards — it's not dead, it's exposed in production).

## Most important reversal

**First pass said:** "Delete `backend/src/mirror/*` + `backend/src/api/routes/mirror.py` — ~3000 lines of dead code duplicating `local/mirror/`."

**Second pass said:** "Wired in production via `app.include_router(mirror_router)` at `api/__init__.py:610`. If anything POSTs to `/api/mirror/start`, it spawns a Playwright Firefox on the headless Hetzner container — which will crash or wedge memory. There's no env-guard."

**Verified.** Line 610 confirmed. The endpoints are not reached *by the legitimate UI* (which talks to LOCAL `/mirror/*`), but they ARE registered with no auth check beyond nginx basic-auth (which is shared with the rest of the API). Any authenticated client misfire or test that hits the wrong base URL will try to spawn a browser.

**Revised plan:** Don't just delete it. Either (a) guard `mirror_router` registration with `if os.getenv("BETTY_ENABLE_BACKEND_MIRROR")`, defaulting OFF on the server, or (b) refuse any `/api/mirror/*` request when the host is detected as the server. Then the deletion is safe to do as a follow-up.

Task #18 has been rewritten — see updated list at the bottom.

## High-confidence findings (both passes agreed)

These are the items both reviewers independently flagged. Treat them as confirmed:

| # | Finding | Files |
|---|---|---|
| 1 | Arb sizing is currency-blind — money at risk | `local/mirror/arb_math.py:20`, `arb_runner.py:540/682/836/929` |
| 2 | Cross-currency `sum(stake)` in `/api/bets` summary | `backend/src/api/routes/bets.py:469-470` + `services/batch_builder.py:967/1443` |
| 3 | `provider_runner._record_unknown_open_bets` uses old buggy dedup; should delegate to `pending_loop`'s version | `local/mirror/provider_runner.py:1915-2017` vs `pending_loop.py:479-549` |
| 4 | `MlModelRegistry.is_active = Integer` then filtered with `=1` | `db/models.py:1680` + `ml/serving/predictor.py:39` |
| 5 | Tabs hidden via `className="hidden"` keep all polling alive | `App.tsx:78-94` |
| 6 | PlayPage derived chains (`bets`, `byCluster`, `clusterIds`, `totalEv`, `totalPending`) not memoised | `PlayPage.tsx:2200-2281` (1st pass numbering) / `:2482-2563` (2nd pass numbering) |
| 7 | Dead code blocks: `if False` in `router.py`, orphaned `recorder.py` + `discovery_v2.py`, dead `pending_loop._run`/`_sync_all`/etc | `local/mirror/{router.py:2324, recorder.py, discovery_v2.py, pending_loop.py:210-216}` |
| 8 | File-size refactors: split `PlayPage.tsx`, `router.py`, `scanner.py`, `orchestrator.py`, etc | — |
| 9 | Provider extractor base class to dedupe ~1500 lines | `backend/src/providers/*` |
| 10 | Two API clients (`hooks/useApi.ts` vs `services/api/`) — PlayPage is the only consumer of the legacy one | — |

## NEW findings the first pass missed

### NEW-1: Three `filter(not Column)` SQLAlchemy bugs — silent SQL breakage [P0]

Python's `not` evaluates an object's truth value; SQLAlchemy column expressions are truthy as Python objects, so `not Column` yields a constant `False`. The filter then matches nothing — the query is silently broken.

- `backend/src/pipeline/scheduler.py:1005` — `session.query(Opportunity).filter(not Opportunity.is_active).delete()` in the opportunities-cleanup job. Either deletes nothing or under stricter SQLAlchemy raises. Confirmed by reading the file.
- `backend/src/api/routes/polymarket.py:820, :881` — `query.filter(not Bet.is_bonus)` when `exclude_bonus=True`. The Polymarket stats endpoint returns the wrong rows. Confirmed by reading the file.
- `repositories/opportunity_repo.py:384` uses the correct `~Opportunity.is_active` form, which is what proves the others are bugs.

**Fix:** swap `not Column` → `~Column` or `Column.is_(False)`. Add a CI lint rule.

### NEW-2: Backend `mirror_router` registered in production [P0 security]

See "Most important reversal" above. `api/__init__.py:610`.

### NEW-3: `strategies/altenar.py:_sync_history` calls `_click_history_tab` — auto-nav invariant violation [P0]

`local/mirror/workflows/strategies/altenar.py:131-169` clicks UI tabs to switch between "öppet" / "settled" during sync. CLAUDE.md's mirror invariant explicitly says "no clicks/`page.goto` in `sync_history`." This will clobber whatever tab the user has open during a settle. (Caveat: 2nd pass also notes this strategy may not actually be reached at runtime — see NEW-9.)

### NEW-4: Hardcoded FX rate in `providers.yaml` drifts silently [P1]

`backend/src/config/providers.yaml:247` — `exchange_rate_sek: 10.50` for Polymarket. Static value. With 5-10% annual FX drift, every `stake_sek`, Kelly sizing, P&L number, and arb sanity gate is biased. No telemetry tracks staleness.

**Fix:** fetch daily from an FX API into a `currency_rates` table, or at minimum add a `last_updated_at` field and a health-check that flags if > 7 days old.

### NEW-5: `taskkill firefox.exe` on Linux server is dead code [trivial]

`backend/src/api/__init__.py:121-126` calls `subprocess.run(["taskkill", "/F", "/IM", "firefox.exe", "/T"])` at startup. Linux has no `taskkill`; raises `FileNotFoundError`, swallowed by `with suppress(Exception)`. Pure dead code disguised as cleanup. Verified.

### NEW-6: `useMirrorStream` drops SSE events under React 19 batching [P0]

`frontend/src/hooks/useMirrorStream.ts:57-61` writes every event to a single `lastEvent` state slot. If two SSE events arrive in the same React batch (e.g. `bet_recorded` + `balance_intercepted`, or `arb_alignment` + `arb_leg_odds`), only the latter triggers the dependent effect — the earlier event silently disappears. The 100-event ring buffer exists but no consumer reads it.

**Fix:** change to a callback-based emitter (subscribers register `onEvent(type, cb)`) so each event is delivered independently, OR queue + drain in an effect.

### NEW-7: `state_writer._fire_post` task GC race [P1]

`local/mirror/state_writer.py:51-60` does `loop.create_task(_do())` without holding a strong ref. `browser.py:251` already has this pattern fixed with a module-level `_background_tasks` set. State_writer fires on every `publish()` → POST to `/api/mirror/event` can be GC'd before httpx opens the socket. Silent event drops on low-traffic loops.

### NEW-8: `_reactive_history_sync` always broadcasts `recorded: 0` [P1]

`local/mirror/router.py:1944` — `recorded` is initialised to 0 and never reassigned. `_record_unknown_open_bets` mutates the DB but returns `None`. UI gets misleading data in the `settling_done` event.

### NEW-9: `strategies/altenar.py` may be unreachable code [P1 refactor]

2nd-pass routing analysis: `workflows/__init__._PROVIDER_TO_PLATFORM` maps every altenar pid to `AltenarWorkflow` (the legacy stub class). The strategy is only loaded if `data/mirror_intel/{pid}.json` exists AND the pid is NOT in `_PROVIDER_TO_PLATFORM`. Since all altenar pids are in the map, the strategy file is never reached. Same likely for `strategies/cloudbet.py` (which has a placeholder placement endpoint at :384). Verify by listing `data/mirror_intel/` and `_PROVIDER_TO_PLATFORM` keys, then delete unreachable strategies.

### NEW-10: `StatsPage` Fragment keys missing in `.map` [P1]

`frontend/src/pages/StatsPage.tsx:913-1093` — `historyBets.map(bet => <> <tr key={bet.id}>...</tr> {isExpanded && <tr>...</tr>} </>)`. React requires `<Fragment key={...}>` for fragments inside `.map`. Today: console warnings + risk of inline-edit state attaching to the wrong row when sort order changes.

### NEW-11: `BankrollPage.fmtAmount` mislabels non-SEK toasts [P1]

`frontend/src/pages/BankrollPage.tsx:29-36/43`. User types `$100` for Polymarket; toast displays `"Balance set to 1050 kr"`. Internally the value is correct (SEK-converted), but the unit label is wrong — confusing. Pass native currency through to the toast.

### NEW-12: Fresh `httpx.AsyncClient` per task in poly/kalshi pollers [P1 perf]

`local/mirror/poly_clob.py:41`, `poly_live_poller.py:104`, `recorders/auto_poller.py:81`, `recorders/kalshi_api.py:111` all `async with httpx.AsyncClient(...)` per call instead of reusing `http_client.tunnel_client()`. Every tick = fresh TCP + TLS + SSH-channel handshake.

### NEW-13: `SpecialOdds` time fields stored as String [P1]

`backend/src/db/models.py:1110-1120` — `event_time`, `expires_at`, `scraped_at` declared `String` not `DateTime`. Future-date queries can't use proper indexing.

### NEW-14: 186 raw `db.query(...)` calls bypass the repo layer [P2]

2nd-pass grep: ~112 in routes, ~74 in services do raw `session.query(...)`. The whole point of `repositories/` was to centralise data access. Each bypass risks N+1, missing joinedload, and inconsistent filter logic.

### NEW-15: Naive datetime everywhere — 10+ `.replace(tzinfo=None)` calls [P2]

`bets.start_time` is `DateTime` naïve. Files scattered with `.replace(tzinfo=None)` to normalise: `pipeline/storage.py:452/454/920/922`, `services/arb_correlation.py:94`, `services/auto_settle.py:30`, `services/bankroll_service.py:228`, etc. Switch to `TIMESTAMPTZ` once + remove the dozen normalisers.

### NEW-16: `Profile.is_active` has no partial unique index [P2]

Two profile rows can simultaneously be `is_active = TRUE` if `profile_repo.set_active` ever races. Add `CREATE UNIQUE INDEX ix_profile_one_active ON profiles(is_active) WHERE is_active = TRUE`.

### NEW-17: PlayPage `setBetRecordedToasts` setTimeout leaks on unmount [P2]

`PlayPage.tsx:2197, 2207, 2221` — dismiss timeouts not tracked, not cleared on unmount or effect re-run. React warning + tiny memory leak under StrictMode.

### NEW-18: 8 raw `fetch()` calls in PlayPage bypass `connectionManager` [P1]

`PlayPage.tsx:1203, 1253, 1478, 1485, 1494, 1592, 1635, 3087, 3278` — direct `fetch()` calls. When the tunnel wedges, these hang instead of fast-failing like the `api.*` wrapper does. Compounds with the half-done useApi.ts migration.

### NEW-19: `valueBetBlacklist.matchesByName` is unbounded O(legs × fuzzyEntries × bigrams) [P1 perf]

`PlayPage.tsx:2433-2480` — fuzzy match called inside every row render. With ~200 pending × 5 clusters × 20 opps × 3 legs ≈ 60k bigram-set comparisons per paint.

## Updated ranked task list

Items added or revised from the cross-pass:

| # | Task | Priority | Effort |
|---|---|---|---|
| **22** | **Fix three `not Column` SQLAlchemy bugs** at `scheduler.py:1005`, `polymarket.py:820, 881` — silent SQL breakage | **P0** | 30 min |
| **23** | **Guard `mirror_router` registration behind env flag** at `api/__init__.py:610` — security regression today | **P0** | 30 min |
| **24** | **Fix `useMirrorStream` event-drop on batched SSE** — switch to callback emitter | **P0** | 2 h |
| **25** | **Fix `strategies/altenar._sync_history` auto-nav violation** OR confirm strategy is unreachable and delete it | **P0 / P1** | 1 h |
| **26** | **Add strong-ref task set to `state_writer.py`** — copy pattern from `browser.py:251` | **P1** | 30 min |
| **27** | **Add Fragment keys in `StatsPage.tsx:913`** | **P1** | 10 min |
| **28** | **Fix `BankrollPage.fmtAmount` non-SEK toast labels** | **P1** | 30 min |
| **29** | **Switch poly/kalshi pollers to `tunnel_client()`** — drop 4 ad-hoc httpx contexts | **P1** | 1 h |
| **30** | **Fix `_reactive_history_sync` `recorded` always 0** | **P1** | 15 min |
| **31** | **Replace `SpecialOdds` String time fields with DateTime + migrate** | **P1** | 2 h |
| **32** | **Audit + delete `taskkill firefox.exe` Linux stub** at `api/__init__.py:121` | **trivial** | 5 min |
| **33** | **Plan FX-rate refresh job** to replace static `providers.yaml:247` | **P1** | half-day |
| **34** | **Migrate raw `db.query` calls** to repositories layer (186 sites — iterative) | **P2** | weeks |
| **35** | **Switch `bets.start_time` to `TIMESTAMPTZ`** + delete `.replace(tzinfo=None)` scatter | **P2** | 1 day |
| **36** | **Add partial unique index on `Profile.is_active`** | **P2** | 15 min |

**Revised #18 (was: "delete backend mirror subtree"):** First guard registration (task #23), then delete after a release proves no client hits `/api/mirror/*` in nginx logs.

## Independent-audit summary

- **Confidence in shared findings**: very high — items 1-10 above are confirmed by two independent reads.
- **Net new P0s from second pass**: 3 (SQLAlchemy `not` bugs, mirror-router exposure, SSE event drop).
- **Net new P1s from second pass**: ~10.
- **Reversed conclusions**: 1 (backend mirror subtree).
- **First pass weaknesses**: skipped BankrollPage and StatsPage entirely; missed Python-truthiness footguns in SQLAlchemy; misread the prod risk of the backend mirror routes.
- **Second pass weaknesses**: less thorough on the big-file split plans (first pass gave more concrete extraction tables).

Net: combining both passes is the right play. The roadmap is solid. Start with tasks #22, #23, and #6 (currency in arb sizing) — all three are short and each closes a real production risk.

---

# Strategic Optimization Review — Root to Top

The previous reviews itemised bugs. This one steps back and asks: given the architecture as it stands, where are the highest-leverage *strategic* moves? Working through the stack from infrastructure up to UX, focusing on the changes that pay off across many tasks at once.

## L0 — Infrastructure (Hetzner box, Docker, nginx, Postgres tuning)

**What's good.** The deploy script is mature (`flock`, 5-min rebuild cooldown, `/health` verification, aggressive `docker image prune`). Postgres is tuned for the box (4 GB `shared_buffers`, 10 GB `effective_cache_size`, 256 MB `work_mem`, NVMe `random_page_cost=1.1`). Memory partitioning is explicit (12 GB Postgres / 48 GB backend / 4 GB OS) so OOM kills the container, not the kernel. nginx has HSTS, rate limiting, security headers, and SSE-aware buffering off. Daily `pg_dump` with 7-day retention.

**Where the leverage is.**

- **Single-node cliff.** One i7-7700 with 4c/8t, 64 GB RAM, running the entire data plane. No failover, no read replica, no standby. The extraction pool is *already* capped at 4 workers because of the GIL on 4 cores. **Move:** add a cheap second node (even another Hetzner box) running a Postgres read replica + a standby extraction worker. The frontend's stats/bankroll queries route to the replica, which buys both throughput headroom and a disaster-recovery story. Effort: 1-2 days. Payoff: removes the single-node failure mode for cents on the dollar.

- **Backup retention is too aggressive.** 7 days of `pg_dump`s is fine for "the disk died yesterday" but not for "we shipped a bug a month ago that quietly corrupted Bets." Cheap fix: weekly off-box copy to S3/B2 (object storage with lifecycle to 1 year). Effort: 1 hour. Payoff: insurance.

- **No restore drill.** Backups that have never been restored aren't backups. **Move:** add a `restore-test.sh` that spins up Postgres from the latest backup in a throwaway container and runs a smoke query. Wire it weekly via cron. Effort: half-day.

- **Dockerfile rebuilds the Playwright browser layer every time `pyproject.toml` changes.** Split the Playwright install into its own stage that doesn't depend on the pip layer, and the rebuilds-after-pyproject case drops from ~2 min to ~30 s. Effort: 1 hour.

- **Health check is misnamed.** `/health/live` is what `HEALTHCHECK` uses (correct — it should be cheap), but the container restart policy means a stuck full `/health` (which hits the DB + market data poller) would never restart the container. Already correct here — flagging because it's an easy regression in the future. Add a comment in `docker-compose.yml`.

- **nginx basic auth is the only auth layer.** Same credentials shared by every authenticated route, including `/api/mirror/*` (see review's P0 task #23). **Move:** per-route API keys for the mirror and extraction admin endpoints, or simply env-guard the dangerous ones (already proposed). Effort: half-day.

## L1 — Data layer (schema, indexes, types)

**What's good.** The schema has the composite indexes the scanner actually needs (`(event, market, point, scope)`, `(provider, market)`). FKs are enforced (per CLAUDE.md, this is the post-SQLite migration win). `Bet.currency` exists and is the source of truth.

**Where the leverage is.**

- **No `Money` type. Currency boundary is implicit everywhere.** Despite `local/mirror/currency.py` existing, currency is a `String` column on `Bet`, absent on `Odds` (implicit from provider), and re-added by hand in services. Every cross-currency bug found in this review (arb_runner, bets.summarize, batch_builder, BankrollPage toast) is a symptom of one missing abstraction: a `Money(amount, currency)` value type that simply *refuses to compose* with another `Money` of a different currency without an explicit `.to(target)` call. Effort to introduce: 2-3 days (define the type, migrate aggregation sites, type-check). Payoff: kills an entire class of bug forever. This is the single highest-leverage architectural move in this review.

- **`bets.start_time` is naive `DateTime`** with 10+ `.replace(tzinfo=None)` calls scattered to keep it that way. **Move:** switch to `TIMESTAMPTZ`, delete every normalisation site. Effort: 1 day. Payoff: removes a recurring foot-gun.

- **`opportunities.outcomes` is JSON with no schema.** Frontend reads specific keys (`home`, `away`, `over`, `under`, etc.); nothing enforces shape. **Move:** Pydantic model + check constraint, or even a JSON Schema check trigger in Postgres. Effort: half-day. Payoff: schema changes don't silently break downstream.

- **`MlModelRegistry.is_active = Integer`** vs every other `is_active = Boolean`. Already in the task list — but the broader move is a 10-minute linter that flags model-column type drift.

- **`OddsMovement` is append-only with no partitioning.** Steam-detection writes per-tick per-provider per-event. At 26 providers × thousands of events × minutes-per-day, this table grows monotonically forever. **Move:** range-partition by `recorded_at` monthly, set lifecycle to drop partitions > 90 days old (or whatever the steam detector actually needs). Effort: half-day, one-time. Payoff: query performance stays flat as data grows, backup size stops compounding.

- **No partial unique on `Profile.is_active`.** Two profiles can race to both be active. One-line index. Already on the list.

- **`SpecialOdds` time fields stored as `String`.** Already on the list, but worth calling out as part of the broader "make time first-class" move alongside `bets.start_time`.

- **Repository pattern is half-done — 186 raw `db.query(...)` calls bypass it.** Either commit (gradually migrate, add lint to block new raw queries) or abandon (delete the repo layer and document that services own data access directly). Half-done abstractions cost more than either decision. Effort to migrate iteratively: weeks (one subsystem per session). Payoff: predictable query patterns, easier to introduce join hints / read replicas later.

## L2 — Extraction pipeline (concurrency, scheduling, caching)

**What's good.** Pool manager with per-type semaphores, circuit breaker with three-state recovery, TTL-LRU cache, dedicated `_EXTRACTION_POOL` to keep `/health` snappy under load, staggered start to avoid write stampede.

**Where the leverage is.**

- **Orchestrator is monolithic.** One in-process flow per tick: fetch → normalize → match → store → scan. A single slow provider on a tier holds up the analysis pass for that tier. **Move:** decouple extraction from analysis via an in-process asyncio queue (cheap) or, eventually, Redis Streams (durable). Extraction posts `{provider_id, run_id, events}`; analysis drains. Effort: 1-2 days. Payoff: a slow Kambi extraction no longer delays the value-bet scan from the providers that already finished.

- **Cache hit-rate has no visibility.** `_hits` / `_misses` exist but aren't surfaced anywhere. **Move:** expose at `/health/extraction` and surface in the existing extraction report. Effort: 1 hour.

- **Circuit breaker fires per-provider but pool manager doesn't know.** A provider in OPEN state still holds a slot in its group semaphore (it just returns fast). **Move:** when CB opens, release the slot immediately so the next provider in the group can run. Effort: half-day.

- **Per-tier rebuild of "is this event in our normalised set" is hot.** `matching/matcher.py:50` already caches normalisation results but the *match* step runs per event per tier. **Move:** maintain a `(sport, normalised_home, normalised_away, date)` → `event_id` lookup that's refreshed once per tick instead of recomputed per provider. Effort: 1 day. Payoff: matching is currently a real fraction of tick time.

- **`time.sleep` in the deadlock-retry paths.** Already on the bug list (scanner / orchestrator / analyzer). Beyond fixing the blocking, add jittered backoff so two concurrent extractors don't sleep in lockstep. Effort: 1 hour.

## L3 — Analysis & matching

**What's good.** Devig is correctly multiplicative. Pinnacle-as-sharp is enforced via `SHARP_PROVIDERS`. Scanner has filters for malformed markets (`MIN_VALID_PROB_SUM`, `MAX_ODDS_RATIO`).

**Where the leverage is.**

- **Scanner is a god class.** Already in the refactor list. The strategic move is to break it into a *pipeline* of small composable steps (`Normalise → Devig → MatchAgainstSharp → ApplyFilters → EmitOpportunities`) where each step is a pure function on a value type. The current scanner is harder to optimise per-step because everything reads/writes the same `grouped` dict. Effort: 3-5 days. Payoff: each step gets independently profilable + cacheable, ML hooks land in one place not three.

- **Sharp dependency is total.** If Pinnacle extraction fails, the entire value detector goes dark for that sport. **Move:** secondary sharp baseline (Smarkets exchange odds are fine for major Euro football; Cloudbet for crypto-friendly markets) with explicit fallback semantics + a flag in the opportunity payload. Effort: 1 week. Payoff: graceful degradation when Pinnacle hiccups.

- **Hot-path ML imports inside loops.** Already on the list. The strategic frame: there's no clear "feature extraction batch" boundary. Right now feature extraction happens per outcome inside the scanner, which forces lazy imports. **Move:** extract a single `features = compute_features(opportunities[])` step that runs once per scan, with imports hoisted. Effort: 1 day.

## L4 — Service layer & API

**What's good.** SSE for both extraction stream and market data, gzip compression, dedicated thread pool for health probes, proper exception handler.

**Where the leverage is.**

- **No HTTP response cache.** `/api/opportunities`, `/api/bankroll/state`, `/api/extraction/state` are hit hard from the local client. None cache. **Move:** `/health/extraction` and `/api/extraction/state` already serve out of an in-memory snapshot; surface this consistently with an `ETag` + `Cache-Control: max-age=N` so the local client can short-circuit on 304. Effort: half-day per endpoint. Payoff: SSH tunnel chatter drops materially.

- **SSE events have no schema, no replay, no backpressure.** Backend POSTs blindly through the tunnel; frontend reads `lastEvent`. If the frontend is slow, events queue and the user sees stale data. **Move:** define an `event_id` + `seq` per event, frontend acks `last_seen_seq`, backend can replay missed events on reconnect. Effort: 2-3 days. Payoff: reliable event delivery — fixes the "useMirrorStream drops events" P0 *and* makes reconnect-after-disconnect actually work.

- **Routes still own business logic in several places** (`extraction.py`, `bonus_arbs.py`, `bets.py`). The architecture wants thin handlers + thick services + thin repos. **Move:** continuous refactor — for every PR touching a route, also move some logic to the service. Effort: continuous. Payoff: predictable.

- **No request-level tracing.** A slow `/api/opportunities` is currently invisible. **Move:** simple middleware that logs `path`, `status`, `duration_ms`, `db_queries` per request (via SQLAlchemy event hooks). Effort: half-day. Payoff: real perf visibility.

## L5 — Local client (SSH tunnel, Playwright, state machine)

**What's good.** Reactive history sync replaces polling per the invariant. SSH tunnel watchdog exists. Per-provider lock in `_reactive_history_sync`.

**Where the leverage is.**

- **Playwright is the single most expensive resource the client uses.** One Chromium per provider × multiple tabs × heavy DOM watchers + interceptors. **Move 1 (cheap):** reuse browser contexts more aggressively; today each provider opens a fresh tab from scratch. **Move 2 (deeper):** for providers with stable APIs (Polymarket CLOB, Kalshi, Pinnacle), reverse-engineer enough of the placement flow to do API-mode placement without driving the browser at all. Already partially done — finish it for the 3-5 providers that are easy. Effort: 1 week per provider. Payoff: bet placement latency drops from ~5 s to <500 ms, dropped intercepts go away.

- **State machine lives in code, not in a state machine library.** `IDLE/OPENING/LOGIN_WAITING/SETTLING/...` are strings sprinkled across `play_loop`, `arb_runner`, `router`, `browser`. **Move:** explicit `transitions`-library state machine with declared transitions + side-effects per edge. Effort: 1 week. Payoff: every "the runner got stuck in SETTLING" bug becomes diagnosable + testable.

- **router.py is the choke point.** Already in the refactor list. Strategic frame: extract the "what runs per event" logic into a typed dispatcher that doesn't know about FastAPI. Then `router.py` is just `app.add_api_route(...)` calls plus the dispatcher. Effort: per the existing plan.

- **No latency budget per state transition.** A bet placement involves ~10 hops (DOM nav → balance scrape → odds confirm → fill stake → user click → intercept → POST → DB → SSE → UI). Each hop should have a budget; today there's no way to see where time goes. **Move:** add `latency_ms_per_step` to the broadcast SSE event. Effort: half-day. Payoff: profiling without instrumentation.

## L6 — Frontend (state, queries, code split, types)

**What's good.** Already on React 19 + TanStack Query + react-virtual. SWC compiler. Per-tab `ErrorBoundary`.

**Where the leverage is.**

- **Tabs not code-split.** `vite.config.ts` has no `build.rollupOptions.output.manualChunks`. Bankroll and Stats ship in the same chunk as the 4339-line PlayPage. **Move:** route-level `React.lazy()` so Bankroll/Stats are separate chunks. Effort: 1 hour. Payoff: cold-load drops, especially when the user just wants to check bankroll.

- **Two API surfaces — `hooks/useApi.ts` vs `services/api/`.** Already on the list. Strategic frame: pick one, delete the other, lint to prevent regressions.

- **SSE event types are entirely `any`.** Every `data.foo` is unchecked. **Move:** define `MirrorEventMap` discriminated union *generated from* a backend Pydantic model so backend/frontend stay in lockstep. Effort: 1-2 days (set up `pydantic-to-typescript` or similar). Payoff: backend rename = compile-time frontend break.

- **Mutations don't invalidate queries.** TanStack Query is in the deps but I don't see broad use of `useMutation` + `queryClient.invalidateQueries`. Most state is `useState` driven manually. **Move:** put server state behind `useQuery` consistently; let TanStack Query handle the cache. Effort: 1-2 weeks (incremental). Payoff: removes ~half the `useState`s in PlayPage along with the stale-state bugs.

- **No frontend tests.** CI runs `tsc --noEmit` only. **Move:** Vitest + React Testing Library; one happy-path test per page + one per critical hook (`useArbOpps`, `useMirrorStream`). Effort: 2-3 days to set up + initial tests. Payoff: prevents the next "the divergent PendingRow drifted again" bug.

- **No bundle analysis.** Add `rollup-plugin-visualizer` to `vite.config.ts`. Effort: 30 minutes. Payoff: visibility into what's actually heavy.

## L7 — Cross-cutting (observability, types, testing, ML)

This is where the project's "weakest link" is — and where one investment pays off across every other layer.

- **No observability stack.** Logs only. No metrics, no traces, no dashboards. For a 24/7 system that handles money, this is the single biggest gap. **Move:** add OpenTelemetry SDK + a free-tier collector (Grafana Cloud has a generous free tier; Better Stack is fine; even just Prometheus + Grafana on the same box). Instrument: extraction tick duration, match rate, scanner duration, SSE events sent, bets placed/rejected. Effort: 2-3 days. Payoff: every other optimisation in this list becomes measurable.

- **No structured error reporting.** When the local client crashes, the user sees nothing. **Move:** Sentry free tier (or self-host GlitchTip). Effort: 1 hour. Payoff: every silent failure shows up.

- **Currency is the recurring foot-gun.** Already called out in L1 — a `Money` type is the single highest-leverage cross-cutting change in this review.

- **Tests skew wrong.** `arb_math.py` (200 lines of pure math) has multiple test files; `router.py` (2660 lines of stateful coordination) has minimal coverage; `browser.py` (1522 lines) has none. **Move:** for every file > 500 lines on the critical path, target ≥1 test per public function before further changes. Effort: continuous. Payoff: refactor with confidence.

- **ML subsystem has no lifecycle story.** Models live in `db/MlModelRegistry`, features come from `ml/features/`, but there's no documented training cadence, no drift detection, no shadow eval. **Move:** even a simple "weekly retraining job + log new model vs old on the last week's labelled bets" is a leap from where it is. Effort: 1 week. Payoff: the ML investment starts paying off instead of being a wired-but-unmonitored maybe-feature.

## L8 — UX / workflow (top of stack)

This is where users feel the wins. Most of the above lands here eventually.

- **Bet-placement latency.** Today: ~5 s for a soft book DOM-mode bet. **Move (sum of L5 changes):** under 1 s for API-mode providers. Felt every single placement.

- **Cold-load latency.** Today: full bundle ships even when the user just wants Bankroll. **Move (L6 code-split):** <1 s to interactive for Bankroll/Stats.

- **Stale-data feel on PlayPage.** Caused by SSE drops + manual `useState` + hidden-tab polling. **Move (L4 SSE protocol + L6 query lib + L6 conditional mount):** drops cease; cross-tab navigation feels instant; tabs only poll when visible.

- **Feedback when something fails.** Today: SSH tunnel wedges and PlayPage hangs forever. **Move (L4 ETag + L5 latency budget + L7 Sentry):** every hop has a visible state.

- **Recovery from "the runner got stuck."** Today: requires log inspection. **Move (L5 state machine + L7 metrics):** every stuck-runner shows up as a Grafana alert with the offending state.

## The one-thing answer

If forced to pick a single change that pays off across more of this list than any other: **introduce a `Money(amount, currency)` value type and a typed SSE event protocol.** Together they kill the two recurring foot-guns (currency and event drift) that account for most of the P0 bugs in this review, and they make every later refactor safer because the types push back when something's wrong.

After that, the order is: observability stack (you can't optimise what you can't see), then SSE protocol (everything user-facing rides on it), then API-mode placement for at least Polymarket + Kalshi + Pinnacle (biggest UX win), then split PlayPage + router (long-tail maintainability).

The previous two passes' ranked task lists remain the right execution plan for the *bug fixes*. This layer review tells you what to build *between* those fixes so the next set of bugs gets easier rather than harder to catch.
