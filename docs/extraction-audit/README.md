# Extraction Audit

Cluster-by-cluster deep-dive of every extraction tier in Arnold. Each cluster
gets its own document with a fixed structure so we can compare apples to
apples and track changes as we apply targeted fixes.

## Why this exists

The night of 2026-04-25 produced a tier-wide stall in `api_soft` (4 providers
stuck simultaneously at 21:53 UTC) and 30+ leaked Chrome processes between
18:58 and 22:33 UTC. The post-incident audit revealed structural issues that
span clusters: config drift between YAML and code, ad-hoc `aiohttp.ClientSession`s
that bypass the shared `HttpTransport`, browser launches per provider instead
of a shared pool, and an analyzer running synchronously inside every per-provider
cycle.

Before fixing anything, document the current state of each cluster fully.
Then apply fixes one cluster at a time and watch the system between changes.

## Cluster ordering

| # | Cluster | Members | Why this order |
|---|---|---|---|
| 1 | sharp | pinnacle | Foundation — every other cluster depends on its fair-odds baseline |
| 2 | polymarket | polymarket | Independent JSON API, low risk |
| 3 | signal_international | cloudbet, marathon, stake (retired) | Mixed HTTP transports, no browser |
| 4 | kalshi + smarkets | each its own tier | Prediction-market sources with their own quirks |
| 5 | api_soft | unibet, betinia, betsson, bethard, spelklubben, vbet | Where the 21:53 UTC deadlock happened |
| 6 | browser_soft | 888sport, interwetten, 10bet, tipwin | Heavy Playwright; 10bet is the slow one |
| 7 | browser_antibot | coolbet, comeon | Camoufox + multi-league; chrome-leak source |

## Audit template

Every cluster doc follows the same structure:

1. **Inventory** — files, line counts, transport class, current YAML config.
2. **Extraction flow** — entry point → fetch → parse → normalize → return.
   Include line ranges so a future reader can navigate.
3. **Resource model** — sessions, browsers, locks, semaphores, proxy, cache.
4. **Lifecycle** — startup, per-call cleanup, error paths, leaks.
5. **Smells** — file:line + one-line summary + impact.
6. **Open-source comparable** — 1-2 mature OSS projects that solve the same
   problem and what they do differently.
7. **Verdict** — keep / refactor / replace, with reasoning.
8. **Ranked fixes** — table with effort estimate.
9. **Re-introduction notes** — filled in after fixes are applied and the
   cluster has been re-enabled.

## Index

| # | Cluster | Status | Doc | Shipped fixes (local on `feat/slip-odds-architecture`, not deployed) |
|---|---|---|---|---|
| 1 | sharp | **fixes shipped 2026-04-26** | [01-sharp-pinnacle.md](01-sharp-pinnacle.md) | `743fdb4e` — honor YAML `concurrent_leagues`, per-instance `_logged_unknown_types`, drop events with bad `start_time` |
| 2 | polymarket | **fixes shipped 2026-04-26** | [02-polymarket.md](02-polymarket.md) | `cf9316e6` — CLOB via HttpTransport, token-overlap Yes/No match, MAX_PAGES guards, VWAP dead-code simplified |
| 3 | signal_international | **fixes shipped 2026-04-26** | [03-signal-international.md](03-signal-international.md) | `8cd07e9a` — Cloudbet `Semaphore(20)` parallel competitions, Marathon year-boundary fix, Stake retired |
| 4 | kalshi + smarkets | **fixes shipped 2026-04-26** | [04-kalshi-smarkets.md](04-kalshi-smarkets.md) | `85ea70f1` (kalshi) HttpTransport + 60s cross-sport cache + token-overlap match · `5686b264` (smarkets) HttpTransport + SOCKS5 + 50 LOC dropped |
| (cross-cluster) | orchestrator | **shipped 2026-04-26** | (architectural — see this README's #5) | `dca7b348` — extract analyzer + ML hooks to post_extraction_worker. Eliminates the 21:53 UTC tier-stall root cause. -258 LOC from hot path |
| 5 | api_soft | **partial fixes shipped 2026-04-26** | [05-api-soft.md](05-api-soft.md) | `7196bad2` (gecko) retry budget 540s → 180s, no `transport.close()` per retry (force-kill source) · `d98f7507` (vbet) `python_socks.async_` (no event-loop blocking). **Pending:** spelklubben odds-not-saving, altenar substring fix |
| 6 | browser_soft | **fixes shipped 2026-04-26** | [06-browser-soft.md](06-browser-soft.md) | `99fcc9c7` (10bet) page-pool reuse + drop 8s `wait_for_timeout` fallback · `ae056ede` (tipwin) parallel pagination via `context.request.get` · `522d8e86` (interwetten) Truendo consent via `context.add_init_script` |
| 7 | browser_antibot | not started | [07-browser-antibot.md](07-browser-antibot.md) | — (ComeOn page-recycle alternative + Camoufox subprocess kill remain) |

**12 fix commits total** on `feat/slip-odds-architecture`. **11 of them deployed 2026-04-26 12:41 UTC** to the Hetzner server (server checked out from `main` to feature branch). One follow-up — `0d20ff52` (tipwin pagination URL-capture regression discovered post-deploy) — is committed + pushed but awaiting the next deploy batch (5-min cooldown + minimize extraction interruption).

### Post-deploy headline (cycle 1, 12:41–12:48 UTC, 7 min)

| Metric | Pre-deploy (3h average) | Post-deploy (7 min) |
|---|---|---|
| post_extraction_worker | (didn't exist) | started clean, 7 tier completions processed, 0 errors |
| api_soft tier-wide stalls | 30+ force-cancels in 6 min at 21:53 UTC | 0 |
| Cloudbet avg duration | 155 s | **25 s** (6.2× faster, target hit) |
| Pinnacle avg duration | 57 s | **27 s** (2.1× faster) |
| Force-kill chrome events | 30+/night | 5 in 7 min (all clustered at single 12:44:27 cold-start race) |
| Asyncio noise (Unclosed/Task destroyed) | 20+ at 00:49-02:36 UTC overnight | 0 since deploy |
| Tipwin events/run | ~766 | **40 → 944 after `0d20ff52` redeploy at 13:06 UTC** (regression fixed; 100 pages walked, 9 sports captured) |
| Browser_soft mass-failure | n/a | one-off at 12:44:27 (driver bootstrap race during simultaneous post-restart launch); 888sport recovered cleanly post-redeploy at 13:07 UTC with 892 events / 60s |

## Top-of-stack findings (cluster-spanning)

These appear in 3+ clusters and warrant solving once at the platform level
rather than per-provider:

1. **Ad-hoc `aiohttp.ClientSession` bypassing `HttpTransport`** — polymarket
   (CLOB), kalshi (entire flow), smarkets (entire flow). Resilience hole.
   *Fix once with a shared HttpTransport convention.*
2. **Outcome-name substring matching false-positive** — polymarket, kalshi,
   altenar all do `home_lower in question or question in home_lower`-style
   matching. "Real" matches both Real Madrid and Real Sociedad.
   *Fix once with a token-overlap helper in `matching/`.*
3. **Per-cycle browser launches** — gecko brands (3), browser_soft (4),
   browser_antibot (2) each run their own browsers. 9 browser instances at
   peak, ~10-15 GB total. *Fix once with a CDP-shared browser pool.*
4. **Force-kill chrome cleanup loop** is downstream symptom of either
   gecko's retry-loop close (cluster 5) or camoufox `__aexit__` swallow
   (cluster 7). *Fix at both sources.*
5. **The 21:53 UTC tier-wide stall** is not a provider bug — it's the
   analyzer running synchronously inside every per-provider hot path
   under a process-global threading.Lock, racing the cleanup loop on
   overlapping `odds`/`opportunities` rows. *Fix at the orchestrator
   level, see post_extraction_worker design.*

## Suggested staged re-introduction order

After we apply fixes, re-introduce clusters in increasing risk order:

1. **Sharp + polymarket** together (lowest risk; both already healthy).
2. **kalshi + smarkets** (after `HttpTransport` migration).
3. **signal_international** (cloudbet parallelization is the headline).
4. **api_soft** (ONLY after the orchestrator-level analyzer extraction;
   without that, the 21:53 stall recurs).
5. **browser_soft** (after page-pool fixes for 10bet and tipwin).
6. **browser_antibot** (after camoufox subprocess kill; lowest cadence so
   regressions take longest to surface — go last).
