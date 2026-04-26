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

| # | Cluster | Status | Doc | Headline finding |
|---|---|---|---|---|
| 1 | sharp | audited 2026-04-26 | [01-sharp-pinnacle.md](01-sharp-pinnacle.md) | YAML `concurrent_leagues: 10` silently ignored — code uses `MAX_CONCURRENT_LEAGUES = 50` |
| 2 | polymarket | audited 2026-04-26 | [02-polymarket.md](02-polymarket.md) | CLOB ad-hoc session bypasses HttpTransport (no breaker/retry/proxy); Yes/No team substring bug |
| 3 | signal_international | audited 2026-04-26 | [03-signal-international.md](03-signal-international.md) | Cloudbet competitions serial (30s+); Marathon year-boundary bug breaks Dec→Jan; Stake is dead code |
| 4 | kalshi + smarkets | audited 2026-04-26 | [04-kalshi-smarkets.md](04-kalshi-smarkets.md) | Both bypass HttpTransport entirely; `extractor.close()` raises AttributeError silently; Kalshi re-walks events 17×/cycle |
| 5 | api_soft | audited 2026-04-26 | [05-api-soft.md](05-api-soft.md) | 21:53 stall is orchestrator+DB issue, not provider; Gecko 3 brands × 3 Chromiums = chrome-leak source; vbet blocks event loop |
| 6 | browser_soft | audited 2026-04-26 | [06-browser-soft.md](06-browser-soft.md) | 10bet new_page/competition + 8s wait fallback = 2168s timeout; tipwin 120 sequential gotos; interwetten 24-tab cookie storm |
| 7 | browser_antibot | audited 2026-04-26 | [07-browser-antibot.md](07-browser-antibot.md) | ComeOn per-sport page recycle costs 35-50s/cycle; Camoufox `__aexit__` swallow is orphan-process source |

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
