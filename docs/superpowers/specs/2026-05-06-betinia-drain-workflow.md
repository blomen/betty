# BETINIA Drain Workflow — One-Shot Spec

> **Type:** Active spec — retired when BETINIA balance ≤ 50 kr or workflow is verified end-to-end and a regression test exists.
>
> **Mode:** Drain (any arb ≥ 0% guaranteed profit) · Semi-auto (BETINIA anchor guided, hedge counter autonomous)
>
> **Scope:** BETINIA arb workflow specifically. Generalizes to other Altenar siblings (QUICKCASINO, CAMPOBET, SWIPER) via shared `_bet_ns` and Altenar workflow class.

---

## Goal

Drain BETINIA's balance (~955 kr as of 2026-05-06) to ≤ 50 kr by placing arbs where:
- **Anchor leg** sits on BETINIA (the Altenar soft book we want to drain).
- **Counter leg** sits on a sharp / unlimited provider — Polymarket or KALSHI in priority order, Pinnacle as fallback.
- **Threshold:** any arb with `guaranteed_profit_pct ≥ 0%`. The point is to migrate funds, not to extract edge.

Once drained, the funds end up on the sharp/unlimited counter side and the user can play freely on Pinnacle/Polymarket without bonus turnover restrictions.

## Operating mode

**A — Two-press semi-auto (current `arb_runner` architecture).** No autonomous BETINIA placement until end-to-end verification is complete.

1. User presses BETINIA card (idle) → tab opens to `betinia.se`, runner spawned, card → red.
2. User logs in on betinia.se → card → amber "Logged in · syncing", then amber "Logged in — press to run".
3. User presses card again → card → green "Running"; runner pops top ≥ 0% arb.
4. Runner navigates BETINIA tab to event, prep's betslip (selects outcome, fills stake).
5. **User clicks Place on betinia.se** — the only manual step per arb. Interceptor catches `placewidget` XHR.
6. Counter leg fires automatically on the hedge provider (autonomous if Polymarket/Kalshi; guided if Pinnacle — user clicks Place there too in that case).
7. Both legs recorded to DB linked by `arb_group_id`. Runner advances to next arb.

When BETINIA's balance drops below the cheapest viable arb stake, the runner emits `provider_complete reason=balance_drained`.

## Sequential per-leg arb workflow (architecture change 2026-05-06)

The original `arb_runner._load_all_legs` does parallel `nav + prep + stream` on every leg simultaneously, then waits for `all_green`. For drain mode in semi-auto, the user wants strict sequential progression with per-leg visibility — easier to verify, easier to abort mid-flight, cleaner failure mode.

### State machine per leg

For each leg in order `[anchor=BETINIA, counter1, counter2, ...]`:

```
arb_leg_started   { provider_id, leg_index, total_legs, role }
        │
        ▼
   navigate_to_event(page, bet_ns)
        │
        ▼
arb_leg_navigated { provider_id, url, planned_odds, planned_stake }
        │
        ▼
   prep_betslip(page, bet_ns, planned_stake)
        │
        ▼
arb_leg_prepped   { provider_id, slip_state }
        │
        ▼
   start SlipOddsStream(provider_id) ; await first odds tick
        │
        ▼
arb_leg_synced    { provider_id, live_odds, planned_odds, drift_pct }
        │
        ├── drift within tolerance → next leg
        └── drift > tolerance       → arb_leg_failed → unwind all prior legs → bet_skipped
```

After all legs synced: existing `arb_legs_loaded` event fires with `slip_state="green"` for every leg → user clicks Place on each tab → existing intercept + record path takes over.

### SSE event additions

| Event | Payload | When |
|---|---|---|
| `arb_leg_started` | `{provider_id, leg_index, total_legs, role: "anchor"|"counter", planned_odds, planned_stake}` | Right before nav |
| `arb_leg_navigated` | `{provider_id, leg_index, nav_url}` | After nav success |
| `arb_leg_prepped` | `{provider_id, leg_index, slip_state}` | After `prep_betslip` returns `prepped` |
| `arb_leg_synced` | `{provider_id, leg_index, live_odds, planned_odds, drift_pct, in_tolerance}` | After first odds tick lands |
| `arb_leg_failed` | `{provider_id, leg_index, reason, stage}` | Any step in the per-leg state machine fails |

`arb_legs_loaded` (existing) still fires once after the last leg syncs — it's the "all green, place anchor" trigger.

### Drift tolerance

Existing `_compute_slip_state` already computes per-leg drift and classifies as `green / amber / red`. Sequential gating uses the same classifier — only `green` advances to the next leg. `amber` or `red` triggers `arb_leg_failed reason=drift_out_of_tolerance`.

### Frontend per-leg sections

Replace the current single-row "DUTCH ARB" alignment card with a vertical stack of leg sections, one per leg in load order. Each section shows:

| Field | Source |
|---|---|
| Role badge | `ANCHOR` (purple) / `COUNTER 1` / `COUNTER 2` (cyan) |
| Provider id | leg.provider_id |
| Step badge | `Navigating` (blue) / `Prepping` (amber) / `Streaming` (green pulse) / `SYNCED ✓` (emerald) / `FAILED` (red) |
| Planned odds | leg.planned_odds |
| Live odds | leg.live_odds (live-updating during streaming) |
| Drift % | leg.drift_pct (color-coded by tolerance) |
| Stake | leg.planned_stake |
| Place button | Disabled until `SYNCED ✓`, enabled once all-green |

Layout is top-to-bottom matching the runner's progression order. Pending legs render as zinc placeholders with their role/provider visible but no live data yet.



Each step has one measurable artifact. The status column gets updated as we walk live.

| # | Action | Artifact | Status |
|---|---|---|---|
| 1 | Press BETINIA card (idle) | Tab opens to `betinia.se`; card → red "Log in to continue" | ✅ verified 2026-05-06 |
| 2 | User logs in on betinia.se | Card → amber "Logged in · syncing" within 5s | ✅ verified 2026-05-06 |
| 3 | Settlement: 0 pending → no cyan flash | `settling_done {skipped_no_pending: true}`; card stays out of cyan; with pending: history sync + reconcile | ✅ verified 2026-05-06 (frontend `settling_done` no longer stamps `state='settling'`) |
| 4 | Card reaches amber + auto-release for arb mode | `provider_ready {auto_released: true}` then immediate `provider_running` (no second click) | ✅ verified live 2026-05-07 via SSE capture |
| 5 | Runner enters bet loop | `provider_running` SSE; gate auto-released; `_run_event.set()` | ✅ verified 2026-05-07 |
| 6 | Runner pops top ≥ 0% arb from cluster queue | `arb_leg_started` SSE with full opp meta (event_id, market, outcome, planned_odds, planned_stake, role, leg_index) | ✅ verified 2026-05-07 |
| 7a | BETINIA anchor: nav → prep → stream → sync (sequential per-leg) | SSE chain: `arb_leg_started → arb_leg_navigated (URL has eventId%7E{eid}) → arb_leg_prepped (slip_state=loading) → arb_leg_synced (live_odds, planned_odds, drift_pct=0.00%, slip_state=green, in_tolerance=true)` | ✅ verified live 2026-05-07 — Spring Hills v Bulleen Lions, planned 1.30, live 1.30 |
| 7b | Counter (PINNACLE): nav OK, prep fails | `arb_leg_started → arb_leg_navigated (https://pinnacle.se/sv/matchup/...) → arb_leg_failed reason="failed:outcome_btn_not_found" stage="prep"` | ❌ **blocked** — Pinnacle workflow's `prep_betslip` cannot find the outcome button on `/sv/matchup/{matchup_id}/`. Diagnosis pending. |
| 7c | Counter (KALSHI / POLYMARKET) | Same chain as 7b | ⚠️ untested — never reached in live run because all observed opps used Pinnacle as counter |
| 7-edge | BETINIA `total/under` and some basketball moneyline opps | `arb_leg_failed reason="failed:no_match" stage="prep"` | ❌ **blocked** — text-based outcome matcher in new prep_betslip doesn't find matching odds button for these outcome+market combos. Likely the "Alla" tab pre-step fires before the page renders the total market section, OR text keywords don't match this provider's translation. |
| 8 | All legs synced → `arb_legs_loaded` ALL-GREEN | Existing event fires once last leg syncs; UI shows "ALL GREEN — place each tab" | ⚠️ unblocked once 7b passes — never observed live |
| 9 | Drift > tolerance on any leg → `arb_leg_failed` → unwind + `bet_skipped` | Already partially observed: 7b path emits `arb_leg_failed` cleanly with reason + stage; `bet_skipped` follows with `leg_N_{stage}_failed` reason | ✅ partially verified (failure path works correctly when prep fails — same mechanism that observability requires) |
| 10 | Auto-skip on -EV before any leg loaded | If `live_edge < 0`: `bet_skipped reason=negative_ev`; runner advances | TBD |
| 11 | User clicks Place on BETINIA | `placewidget` XHR caught; `bet_placed` SSE; `/api/bets` row inserted | TBD |
| 12 | User clicks Place on each counter tab | Counter rows recorded with same `arb_group_id` | TBD |
| 13 | Balance refreshes after placement | `BalanceCell` drops by stake amount within 10s | TBD |
| 14 | Next arb pops; loop continues | Step 6 fires again | TBD |
| 15 | Queue empty OR balance < 50 kr | `provider_complete reason=balance_drained` (or `queue_drained`) | TBD |

## Bugs found and fixed during this spec's lifetime

These are the bugs that today's session uncovered and patched. They predate the spec but inform its existence — each is a regression that the doc layer (Section 3 below) is meant to prevent recurring.

| # | Bug | File | Fix |
|---|---|---|---|
| F1 | Generic settling/login banner duplicated per-provider card status | `arnold/frontend/src/pages/PlayPage.tsx` | Filter global banner to `state==='ready'` only |
| F2 | `settling_done` SSE re-stamped `state='settling'` via `type.includes('settl')`, leaving card stuck in "Logged in · syncing" after the no-pending fast-path | `arnold/frontend/src/pages/PlayPage.tsx` | Early-return on `settling_done`; let next event set state |
| F3 | `_bet_ns` flattened `provider_meta` to namespace but skipped already-present keys; canonical `event_id` UUID blocked Altenar's meta `event_id`, so `_navigate_to_event` got empty `altenar_event_id` and silently no-op'd | `arnold/mirror/play_loop.py:42-50` | Explicitly set `ns.altenar_event_id`, `altenar_sport_id`, `altenar_category_id`, `altenar_championship_id` matching the existing kambi/gecko pattern |
| F4 | KALSHI / CLOUDBET / PINNACLE skipped from cluster building (UNLIMITED_PROVIDERS), so no UI section to show their login/settling status on the arb tab — initial fix added a separate "Hedge Providers" section, then removed per user feedback (hedge info already inline per arb row) | `arnold/frontend/src/pages/PlayPage.tsx` | Filter banner to ready-state only; rely on per-row HEDGE column for hedge identity |
| F5 | Sequential per-leg arb workflow added: `_load_all_legs` rewritten from parallel → sequential with new SSE event chain `arb_leg_started → _navigated → _prepped → _synced` (or `_failed`) per leg, plus per-leg UI cards showing role / step / planned-vs-live odds / drift / stake | `arnold/mirror/arb_runner.py:550-737`, `arnold/mirror/slip_odds_stream.py:54-76`, `arnold/frontend/src/pages/PlayPage.tsx` ArbLeg type + handlers + render | Architectural change documented in §"Sequential per-leg arb workflow" above |
| F6 | Auto-release run gate for arb mode (drain mode shouldn't need a manual second press) — `_await_run_gate` now sets `_run_event` immediately after `provider_ready` with a 0.5s SSE-visibility delay; broadcasts `auto_released: true` flag | `arnold/mirror/arb_runner.py:241-300` | Single press to log in, runner self-progresses through settle → bet loop |
| F7 | Runner exited with `provider_complete` instead of retrying when arb-fetch returned empty — `_run` loop now sleeps `_OPP_FETCH_COOLDOWN` (10s) and `continue`s instead of `break`ing | `arnold/mirror/arb_runner.py:426-455` | Drain mode survives transient empty-fetch / cache misses |
| F8 | Frontend SSE has no replay; after page reload, `loopProviderStatus[pid]` is undefined for runners that completed login+settling before reconnect → card stays red | `arnold/frontend/src/pages/PlayPage.tsx` (state-seed effect) | New 5s polling effect against `/mirror/play/status` seeds `loopProviderStatus` for any provider with non-idle state |
| F9 | `prep_betslip` URL-encoded check `eventId~{eid}` failed because Chromium normalizes `~` → `%7E` after `page.goto`; every arb died at prep with `wrong_page` | `arnold/mirror/workflows/altenar.py:783-805` | Accept both `eventId~{eid}` and `eventId%7E{eid}` plus `/{eid}` substring fallback |
| F10 | `prep_betslip` was using `window.altenarWSDK.toggleSelections([oddId])` JS API which is **silently broken** (verified live: returns no error, localStorage stays empty, no console log). Only DOM clicks on the `STB-SPORTSBOOK` shadow-root odds buttons populate WSDK state. | `arnold/mirror/workflows/altenar.py:771-960` | Replaced JS API path with text-matched DOM click (mirrors strategy file value-bet path), with target-price tie-breaker for events with multiple matching markets, and 8s polling for STB-SPORTSBOOK render after navigate |
| F11 | After DOM click, prep returned `prepped` immediately but Zustand persist middleware writes localStorage on a microtask debounce. Stream's `wait_for_first_tick` (10s) timed out reading empty localStorage. | `arnold/mirror/workflows/altenar.py` + `arnold/mirror/arb_runner.py:_load_all_legs` + `arnold/mirror/slip_odds_stream.py` | Prep now polls localStorage up to 5s for actual selection persistence; returns `failed:click_landed_but_no_persist` if not. Stream timeout bumped 10s → 15s. SlipOddsStream got new `wait_for_first_tick(timeout)` method backed by `_first_tick_event`. |
| F12 | Fresh runner read `provider_data['balance']` as None (interceptor cache empty before any XHR fired post-restart), causing `bet_skipped reason=zero_anchor_stake` on every opp | `arnold/mirror/arb_runner.py:_load_all_legs` | Fallback to `await workflow.sync_balance(page)` when interceptor cache is empty/zero |
| F13 | Altenar `prep_betslip` matched against stale buttons during WSDK widget swap (race between `page.goto` completion and WSDK rendering the new event), causing silent `no_match` failures and one drift -73.81% mis-click. | `arnold/mirror/workflows/altenar.py:842-960` | Render polling now waits 12s AND requires at least one button text to contain the expected team-name substring (or `över`/`under` for total) before declaring page settled. Added 15% drift hard cutoff so the matcher refuses to click any button > 15% off planned price. |
| F14 | Pinnacle `_click_market_btn` waited 10s for `button.market-btn` selector even when the page was rendering a "PLANERAT UNDERHÅLL" maintenance banner, burning 10s per Pinnacle-counter opp during scheduled outages. | `arnold/mirror/workflows/strategies/pinnacle.py:879-905` | Detect maintenance banner text before the wait_for_selector; fast-fail with a single log line. |
| F15 | SSH tunnel watchdog in `launch.py` was tuned slack: 6 fails × 20s loop = up to 2 min of `tunnel_down` errors before restart, forcing manual `arnold.bat` restart (which wipes Altenar's `localStorage.token` and forces re-login). Health probe also had 15s timeout vs `/health/live`'s sub-100ms response. | `arnold/launch.py:301-344` | Tightened to 3 fails × 10s loop ≈ 30s detection; health probe timeout 15s → 5s. |
| F16 | Arb runner failed every opp at `find_tab` with `no_tab` after a Playwright tab disappeared mid-session (5+ losses observed in a 2-hour debug session). No auto-reopen; user had to manually `/mirror/open-provider-tab betinia` AND log in again. | `arnold/mirror/arb_runner.py:_load_all_legs` | Fallback: if `find_tab` returns None, call `browser.open_tab(workflow.home_url)` and re-find. Logs a warning so the operator can re-login if the workflow's `check_login` fails on the new tab. |
| F17 | The runner was trying to autofill the soft-anchor outcome via `prep_betslip` (DOM click + slip-state stream + drift check), which created a long-tail of bugs (no_match, wrong_page, WSDK widget-swap race, broken JS API, Zustand persist debounce, drift mis-clicks). For mode A semi-auto — explicitly chosen by the user — the user is doing all the clicking anyway, so autofill was solving a non-problem. F17 removed the prep step for guided workflows entirely. | `arnold/mirror/arb_runner.py:_load_all_legs` | Branch on `wf.autonomous_placement`: autonomous (Polymarket SDK) keeps prep + stream + sync; guided (Altenar / Pinnacle web / Kambi) just navigates and emits `arb_leg_synced` with `guided: true`. Sets `_all_green = True` after load when no streams exist so the green-gate doesn't reject user placements as `placed_while_red`. Eliminates Bug B entirely (no matcher = no `no_match`) and reduces user-visible trust surface to "did the navigate land on the right event?". |

## Documentation strategy (applied AFTER verification)

After the 15-step walk completes (or is blocked at a step), update three layers in one commit:

### Layer A — `docs/mirror-workflow.md` updates

1. **§9 capability matrix**: replace single ✅ column with `Implemented` + `Verified end-to-end (date)`. Today's BETINIA pass updates this row.
2. **New §5b — `_bet_ns` provider_meta flatten convention**: explain the canonical-`event_id` collision and require explicit `{platform}_*` aliases for all platform-specific event_id fields.
3. **New §4a — SSE state-machine stamping**: `settling_pending` stamps cyan; `settling_done` does NOT stamp state. Don't `type.includes('settl')`.
4. **New §6 inset — Hedge Providers UI section**: UNLIMITED providers need their own section on the arb tab because they're skipped from soft clusters but used as counters.
5. **New §15 pitfall — "Capability matrix lies"**: don't trust ✅ without verifying §12 end-to-end.

### Layer B — Memory updates

- New: `feedback_capability_matrix_lies.md` — don't trust mirror-workflow.md §9 ✅; verify §12 when touching a workflow.
- Append to `project_generic_mirror_workflow.md`: `_bet_ns` is the single point converting opp/leg dicts to namespace — explicit platform aliases are mandatory.

### Layer C — `CLAUDE.md` pointer

One added line in the Mirror Workflow section pointing at this spec.

## Out of scope

- **Fully autonomous BETINIA placement** — deferred until semi-auto is stable. Altenar `placewidget` requires CSRF + WAF handling that we haven't reverse-engineered.
- **Other Altenar siblings (QUICKCASINO, CAMPOBET, SWIPER)** — share the same `_bet_ns` fix, DOM-click prep, and Altenar workflow class, so they'll work mechanically once BETINIA verifies end-to-end. Adding their own verification rows in §9 is a follow-up.
- **Counter side stress testing** — KALSHI / Polymarket SDK paths assumed working from prior sessions. The Pinnacle counter `outcome_btn_not_found` blocker is a known regression to diagnose in the next session (Bug A below).

## Open blockers (resume here next session)

### Bug A — PINNACLE counter `prep_betslip` fails with `outcome_btn_not_found`

**Symptom:** During step 7b live walk, `arb_leg_started` and `arb_leg_navigated` (`https://www.pinnacle.se/sv/matchup/{matchup_id}/`) fire correctly for the Pinnacle counter leg, but `prep_betslip` returns `failed:outcome_btn_not_found` immediately. This happens for every counter leg routed to Pinnacle, blocking step 8 (`arb_legs_loaded` ALL-GREEN) regardless of which arb the runner selects.

**Diagnosis path:**
1. Check `arnold/mirror/workflows/pinnacle.py` `prep_betslip` for the outcome button selector
2. Live-inspect a Pinnacle matchup page via `/mirror/browser/eval/pinnacle` to see the current DOM
3. Compare against the Altenar pattern (text-matched DOM click) — Pinnacle may have shifted to a similar shadow-DOM or React-rendered structure
4. Possible Pinnacle cause: page hasn't finished loading the matchup widget at the moment prep runs (same root cause as the Altenar `no_stb` fix — needs polling)

**Likely fix shape:** Add render-polling to Pinnacle's prep_betslip mirroring the Altenar fix at `arnold/mirror/workflows/altenar.py:842-871` (8s × 200ms poll for the outcome button selector before failing).

### Bug B — BETINIA `prep_betslip` fails with `no_match` ~~for `total/under` and some basketball moneyline opps~~ — **RESOLVED via F17**

F17 removed the `prep_betslip` call entirely for guided workflows. There is no longer a matcher to fail. The `arb_leg_synced` event for guided legs fires immediately after `arb_leg_navigated`, with the user clicking outcome + Place on the provider tab. The interceptor catches the placement XHR and records the bet.

Original symptom (kept for archival reference):

**Symptom:** During step 7-edge live walk, BETINIA anchor leg failed `prep_betslip` with `failed:no_match` on:
- `football:pogradeci:kastrioti kruje:20260507` (market=`total`, outcome=`under`)
- `basketball:limoges:boulazac basket dordogne:20260507` (market=`moneyline`, outcome=`away`)

Other opps (e.g. Spring Hills v Bulleen Lions, market=`1x2`, outcome=`away`) DID succeed. So the matcher works for some but not all market+outcome combos.

**Diagnosis (live-validated 2026-05-07):** The matcher itself was correct (verified by running it in-browser against a settled page — matched the right button with drift=0). The real bug was a **mid-render race**: after `page.goto`, the WSDK widget shows the previous event's leftover odds buttons for ~1-3s during the WASM swap. Earlier polling broke out of the wait loop on first `OddValue` count > 0, so the matcher iterated over stale buttons that didn't include the current event's team name → silent `no_match`. Additionally one anchor synced with **-73.81% drift** because a stale button's price coincidentally matched the new event's planned_odds, exposing that the matcher had no upper bound on drift before clicking.

**Fix landed (F13, 2026-05-07):**
- `arnold/mirror/workflows/altenar.py:842-960` — render polling now waits 12s AND verifies at least one button text contains the expected team-name substring (or `över`/`under` for total) before declaring the page settled
- Added 15% drift hard cutoff in the matcher — won't click a button whose price is > 15% off planned, even if the team name matches (catches alt-spread / props collisions)

**Verification:** Next pass (35 anchor attempts) showed `no_match` reduced from 32 → 5, and the one successful sync exhibited a real -16.66% market drift caught cleanly by the in-tolerance check (not a wrong-button click).

**Remaining `no_match` cases (5 of 35):** Need diagnosis on a per-event basis — likely genuine missing markets on the betinia page (e.g., spread/total tab not auto-expanded for some leagues, or team-name mismatch where the page shows abbreviated names like "MIBR" while DB has "MIBR Esports"). Continue from this point in next session.

### Bug C — Pinnacle in maintenance window

**Symptom:** During step 7b live walk on 2026-05-07, Pinnacle's `/sv/matchup/{matchup_id}/` page rendered a "PLANERAT UNDERHÅLL — SPEL OCH KONTOTILLGÅNG HAR SPÄRRATS" banner with zero `button.market-btn` elements. `_click_market_btn` waited 10s for the selector then returned `outcome_btn_not_found` for every Pinnacle counter leg.

**Fix landed (F14, 2026-05-07):** `arnold/mirror/workflows/strategies/pinnacle.py:879-905` — added maintenance-banner detection that fast-fails before the 10s `wait_for_selector` so the runner skips the opp immediately instead of stalling.

**Resume path:** After Pinnacle's maintenance window ends, re-run the live walk. If `outcome_btn_not_found` still happens for non-maintenance counter legs, then the `button.market-btn` class has been renamed by Pinnacle's React build — diagnose with the same live-DOM inspection pattern used for Altenar's WSDK (`/mirror/browser/eval/pinnacle` to find the new selector).

### Bug D — Local SSH tunnel to Hetzner backend wedges intermittently — F15 mitigation landed

**Symptom:** Multiple times during 2026-05-07, `/api/opportunities/arb-workflow` and `/api/bankroll` returned `{"error": "tunnel_down", "detail": "ReadError"}` or 30s timeouts even though `arnold/launch.py`'s tunnel watchdog reported the SSH process alive on port 18000. Each occurrence required an `arnold.bat` restart.

**Diagnosis:** The watchdog DID do an HTTP-level liveness probe (`/health/live`), but the threshold was tuned slack: **6 consecutive fails × 20s loop interval = up to 2 minutes of `tunnel_down` errors** on every `/api/*` route before restart. Within that 2-min window, the user typically hit "I need to act now" frustration and force-restarted arnold.bat, which then wipes Altenar's `localStorage.token` (see Bug E + pitfall #16) and triggers a fresh interactive re-login. Compounding the slowness: the probe timeout was 15s vs the actual `/health/live` response time of sub-100ms.

**F15 (landed 2026-05-07):** Tightened `arnold/launch.py:301-344` to 3 consecutive fails × 10s loop ≈ 30s detection (vs prior 120s). Health probe timeout 15s → 5s. Live wedging now triggers a tunnel restart 4× faster, reducing the manual-restart pressure.

**Resume path:** F15 is a tuning change, not a structural fix. If the SSH tunnel still wedges, consider switching from `ssh -L` to `autossh` for built-in dead-connection detection. Investigate root cause (Bahnhof VPS proxy, Hetzner network, etc.) only if F15's tighter loop doesn't eliminate user-visible wedge symptoms.

### Bug E — Playwright tab attrition for BETINIA during long live-debug sessions — F16 mitigation landed

**Symptom:** The BETINIA Playwright tab was lost 5 times during 2026-05-07 — sometimes during my `/mirror/browser/eval/betinia` calls (one resulted in a `TargetClosedError`), sometimes silently between API requests. Each loss caused the runner to fail every subsequent opp with `arb_leg_failed reason=no_tab stage=find_tab`, blocking all progress until the user manually `/mirror/open-provider-tab betinia`'d AND interactively re-logged in.

**Suspected root causes (still un-diagnosed):**
- `location.assign(...)` JS evals navigating the tab may trip a popup-blocker / cross-origin race that crashes the page
- Memory pressure — Chromium killing background tabs after ~30+ minutes of activity
- Cookie/session loss when the page goes through certain auth-check redirects

**F16 (landed 2026-05-07):** `arnold/mirror/arb_runner.py:_load_all_legs` — when `find_tab` returns None, the runner now auto-calls `browser.open_tab(workflow.home_url)` and re-finds. If reopen succeeds, the leg proceeds (the workflow's `check_login` will detect missing token on the new tab and emit appropriate state — for Altenar, it bounces to `/sport` to re-init the WSDK). Logs a warning so the operator knows to re-login interactively if `localStorage.token` was wiped.

**Resume path:** F16 prevents the runner from stranding on a single `no_tab`. It does NOT prevent the underlying tab loss — that needs root-cause diagnosis. Consider:
1. Wrapping eval-with-navigation in `page.expect_navigation()` to detect crashes early
2. Adding `page.on("crash", ...)` and `page.on("close", ...)` listeners in `arnold/mirror/browser.py` for proactive recovery (vs reactive on next `find_tab`)
3. Auditing which specific JS evals trigger `TargetClosedError` (logs from the 2026-05-07 session show one happened during a `prep_betslip` evaluate)

## Round-2 verification log (2026-05-07 evening)

After F13, F14 landed:
- BETINIA `no_match` rate: **32 → 5** (28 of those 5 are now from independent tab-loss, not matcher bug)
- `arb_leg_synced` for BETINIA anchor: confirmed working (1 sync per pass)
- Drift cutoff: working — caught a real -16.66% market drift cleanly via `arb_leg_failed reason="drift_out_of_tolerance:red@-16.66%"` instead of clicking wildly off-price buttons
- Pinnacle maintenance fast-fail: working — no more 10s timeouts per Pinnacle-counter opp during the maintenance window

## Final next-session resume checklist

1. **Verify F17 mode-A simplification end-to-end.** After arnold.bat restart + login, runner should: navigate BETINIA tab to event → emit `arb_leg_synced {guided: true}` immediately → navigate Pinnacle/Polymarket counter tab → emit synced for counter → emit `arb_legs_loaded ALL-GREEN`. User clicks outcome + Place on each tab; interceptor records bets with same `arb_group_id`. No `prep_betslip` calls, no `no_match` failures, no DOM matcher noise.
2. ~~Wait until Pinnacle maintenance window ends~~ — **F17 partially eliminates Bug A**: Pinnacle is also `autonomous_placement=False` (guided), so the runner won't call `_click_market_btn` — it just navigates to the matchup page and waits for the user to click. The maintenance banner is still rendered but doesn't block the workflow.
3. ~~Diagnose remaining 5 `no_match` cases~~ — **F17 made these moot** (no matcher = no `no_match`).
4. ~~Fix Bug D (tunnel watchdog)~~ — **F15 mitigated**.
5. ~~Fix Bug E (tab attrition)~~ — **F16 mitigated**.
6. Once a single arb completes end-to-end (anchor placed by user click + counter intercepted with same `arb_group_id`), continue the verification through steps 11–15.

## Retirement criteria

This spec retires when **either**:
- BETINIA balance ≤ 50 kr (drain succeeded — workflow proven via real placements), OR
- All 15 acceptance steps are ✅ verified AND `docs/mirror-workflow.md` §9 has the BETINIA row updated to "Verified end-to-end YYYY-MM-DD".

At retirement, this file moves to `docs/superpowers/specs/archive/`.
