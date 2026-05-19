# Rainbet Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `rainbet.com` as a server-side signal-only provider in arnold's extraction pipeline.

**Architecture:** A new `RainbetRetriever` class in the existing `browser_antibot` extraction tier (alongside ComeOn). Uses **patchright** (Chromium with cross-origin-iframe-click patches) instead of Camoufox to bypass Cloudflare Bot Management + the embedded Turnstile widget that gates rainbet's sportsbook. The renderer mounts inside the patchright browser, makes its own HTTP/WS calls to Betby's data backend (`*.sptpub.com`), and we capture+parse those calls.

**Tech Stack:** Python 3.10, patchright (Playwright fork), Chromium, asyncio, FastAPI orchestrator, SQLAlchemy/PostgreSQL, pytest.

**Spec:** `docs/superpowers/specs/2026-05-10-rainbet-provider-design.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `scripts/discover_rainbet.py` | Create | One-shot discovery harness — captures HAR + WS frames + DOM, runs once during plan execution |
| `docs/superpowers/research/2026-05-10-rainbet-discovery.md` | Create | Discovery output: documented endpoints, sport-slug map, wire format, shard URL pattern. Drives the parser implementation. |
| `backend/src/providers/rainbet.py` | Create | `RainbetRetriever` class (browser orchestration) + pure parser functions (testable separately) |
| `backend/tests/providers/test_rainbet_parser.py` | Create | Pure-function tests for parser (TDD anchor) |
| `backend/src/factory.py` | Modify | Add `elif retriever_type == "rainbet"` branch |
| `backend/src/config/providers.yaml` | Modify | Provider entry + active list + scheduling tier |

The retriever and the parser live in one file (`rainbet.py`) following the same pattern as `cloudbet.py`. Pure parser functions live at module level so they're importable for tests without the browser.

**Note on existing infrastructure:** Arnold's `pyproject.toml` already lists `patchright>=1.48.0` under the `[scrape]` extras group, the Dockerfile already installs it via `pip install -e ".[scrape]"`, sets `PLAYWRIGHT_BROWSERS_PATH=/app/.playwright`, and runs `playwright install chromium`. Patchright is the existing transport's primary engine (`backend/src/core/transport.py:11`). **No dependency or Docker work is needed.**

This means `RainbetRetriever` extends `BrowserRetriever` and uses `BrowserTransport(headless=True, use_proxy=True)` rather than calling patchright directly. The transport already handles: patchright launch with stealth, Chrome 131 UA, Stockholm geolocation jitter, proxy from `PROXY_URL`, resource blocking, driver-PID tracking + cleanup. We layer the CF Turnstile click loop and per-sport navigation on top.

---

## Phase A — Verify existing infrastructure

### Task 1: Confirm patchright + Chromium are already operational in the production image

No code change. This task only verifies the assumption that powers the rest of the plan: the production backend image already has patchright + Chromium installed and ready. If that is true, we go directly to discovery (Task 2). If not, we have to revisit the Dockerfile, but the existing audit suggests we're fine.

**Files:** none modified.

- [ ] **Step 1: Confirm `patchright` is in `pyproject.toml` extras**

Run: `grep -n "patchright" pyproject.toml`
Expected: a line like `"patchright>=1.48.0",` inside the `[project.optional-dependencies]` `scrape = [...]` block.

- [ ] **Step 2: Confirm the Dockerfile installs the `[scrape]` extras and configures Chromium path**

Run: `grep -nE 'PLAYWRIGHT_BROWSERS_PATH|scrape|playwright install' Dockerfile`
Expected: see at minimum
```
COPY pyproject.toml ./
... pip install --no-cache-dir -e ".[scrape]" ...
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN playwright install chromium && playwright install-deps
```

- [ ] **Step 3: Confirm patchright is the live transport on the server**

Run: `ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T backend python -c 'from patchright.async_api import async_playwright; print(\"patchright OK\")'"`
Expected: `patchright OK` (no ImportError).

- [ ] **Step 4: Confirm Chromium binary exists**

Run: `ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T backend bash -c 'ls -d /app/.playwright/chromium-* 2>&1 | head -3'"`
Expected: at least one `chromium-NNNN` directory, e.g. `/app/.playwright/chromium-1217`.

If any of Steps 1-4 fail, **stop and escalate** — the plan assumed pre-installed infrastructure that is missing. Do NOT proceed to Task 2.

If all pass, no commit is needed (no files changed).

---

## Phase B — Discovery (one-shot, output drives the parser)

### Task 3: Write the discovery harness

**Files:**
- Create: `scripts/discover_rainbet.py`

This script runs ONCE on the server, captures the full network conversation between rainbet.com's sportsbook and Betby's backends, and writes the artifacts that drive the parser. It is one-shot; it lives in `scripts/` because it's not part of production extraction.

- [ ] **Step 1: Create `scripts/discover_rainbet.py` with the full harness**

```python
"""One-shot discovery harness for rainbet.com.

Runs patchright + Chromium against rainbet's sportsbook, clears Cloudflare
Turnstile, navigates each supported sport, captures HTTP responses (with
bodies) and WebSocket frames, and writes everything to /tmp/rainbet_discovery/.

Run: python scripts/discover_rainbet.py
Inside container: docker compose exec -T backend python scripts/discover_rainbet.py

Produces:
  /tmp/rainbet_discovery/capture.har        — full HAR
  /tmp/rainbet_discovery/responses.jsonl    — *.sptpub.com response bodies
  /tmp/rainbet_discovery/ws_frames.jsonl    — sptpub WS frames (text + bin hex)
  /tmp/rainbet_discovery/summary.json       — host counts, timings, sport map
"""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from patchright.async_api import async_playwright

OUT = Path("/tmp/rainbet_discovery")
OUT.mkdir(parents=True, exist_ok=True)

SPORTS_TO_PROBE = [
    "soccer", "football", "basketball", "tennis", "ice-hockey",
    "american-football", "baseball", "mma", "boxing",
    "esports", "esports/counter-strike",
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
SITE_URL = "https://rainbet.com/sportsbook"


def proxy_dict():
    pu = os.environ.get("PROXY_URL")
    if not pu:
        return None
    p = urlparse(pu)
    out = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        out["username"] = p.username
        out["password"] = p.password or ""
    return out


def is_betby_host(url: str) -> bool:
    h = urlparse(url).hostname or ""
    return any(d in h for d in ("sptpub.com", "invisiblesport"))


async def clear_turnstile(page, timeout_s=60.0):
    end = time.time() + timeout_s
    while time.time() < end:
        # Stop if a Betby host has started talking
        cookies = await page.context.cookies()
        if any(c["name"] == "cf_clearance" for c in cookies):
            try:
                ts_iframe = await page.query_selector(
                    "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']"
                )
                if ts_iframe is None:
                    return True
            except Exception:
                pass
        # Click at validated coord (per CF-Clearance-Scraper)
        try:
            await page.mouse.click(210, 290)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
    return False


async def run():
    proxy = proxy_dict()
    print(f"proxy: {proxy}")

    summary = {
        "started_at": time.time(),
        "proxy": proxy,
        "site_url": SITE_URL,
        "host_request_counts": {},
        "ws_opens": [],
        "sport_results": {},
    }
    response_log = []
    ws_log = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-http2", "--disable-quic"],
            proxy=proxy,
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 720},
            record_har_path=str(OUT / "capture.har"),
            record_har_content="embed",
        )
        context.set_default_timeout(60_000)
        page = await context.new_page()

        host_counts = {}

        def on_request(req):
            h = urlparse(req.url).hostname or "?"
            host_counts[h] = host_counts.get(h, 0) + 1

        async def on_response(resp):
            if not is_betby_host(resp.url):
                return
            try:
                body = await resp.body()
                txt = body.decode("utf-8", errors="replace") if body else ""
            except Exception as e:
                txt = f"<err:{e}>"
            response_log.append({
                "ts": round(time.time() - summary["started_at"], 2),
                "url": resp.url,
                "status": resp.status,
                "content_type": resp.headers.get("content-type", ""),
                "body_size": len(body) if body else 0,
                "body_sample": txt[:8000],
            })

        def on_ws(ws):
            host = urlparse(ws.url).hostname or "?"
            summary["ws_opens"].append({"url": ws.url, "host": host})
            if "sptpub.com" not in (host or ""):
                return

            def push(direction, payload):
                if isinstance(payload, bytes):
                    ws_log.append({
                        "direction": direction, "host": host, "url": ws.url,
                        "kind": "bin", "len": len(payload),
                        "data": payload[:2000].hex(),
                        "ts": round(time.time() - summary["started_at"], 2),
                    })
                else:
                    ws_log.append({
                        "direction": direction, "host": host, "url": ws.url,
                        "kind": "txt", "len": len(payload),
                        "data": payload[:4000],
                        "ts": round(time.time() - summary["started_at"], 2),
                    })

            ws.on("framereceived", lambda p: push("recv", p))
            ws.on("framesent", lambda p: push("send", p))

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        page.on("websocket", on_ws)

        print(f"goto {SITE_URL}")
        await page.goto(SITE_URL, wait_until="domcontentloaded")

        print("clearing Turnstile…")
        cleared = await clear_turnstile(page)
        print(f"  cleared={cleared}")

        # Wait for sptpub activity to settle
        await page.wait_for_timeout(20_000)

        # Per-sport probe
        for slug in SPORTS_TO_PROBE:
            url = f"{SITE_URL}/{slug}"
            t0 = time.time()
            entry = {"slug": slug, "url": url}
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(15_000)
                entry["ok"] = True
                entry["title"] = await page.title()
                entry["body_sample"] = (
                    await page.evaluate(
                        "() => document.body ? document.body.innerText.slice(0, 600) : ''"
                    )
                )
            except Exception as e:
                entry["ok"] = False
                entry["error"] = str(e)[:300]
            entry["duration_s"] = round(time.time() - t0, 1)
            summary["sport_results"][slug] = entry

        await context.close()
        await browser.close()

    summary["host_request_counts"] = dict(sorted(host_counts.items(), key=lambda x: -x[1]))
    summary["finished_at"] = time.time()
    summary["duration_s"] = round(summary["finished_at"] - summary["started_at"], 1)

    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    with open(OUT / "responses.jsonl", "w", encoding="utf-8") as f:
        for r in response_log:
            f.write(json.dumps(r, default=str) + "\n")
    with open(OUT / "ws_frames.jsonl", "w", encoding="utf-8") as f:
        for w in ws_log:
            f.write(json.dumps(w, default=str) + "\n")

    print(f"\n=== DISCOVERY DONE ({summary['duration_s']}s) ===")
    print(f"  responses captured: {len(response_log)}")
    print(f"  ws frames captured: {len(ws_log)}")
    print(f"  hosts hit:")
    for h, n in summary["host_request_counts"].items():
        if "sptpub.com" in h or "invisiblesport" in h:
            print(f"    {n:5d}  {h}")
    print(f"  sport probe results:")
    for slug, r in summary["sport_results"].items():
        print(f"    {'OK' if r['ok'] else 'ERR'}  {slug}  {r.get('title','')}")
    print(f"\n  output dir: {OUT}")


if __name__ == "__main__":
    asyncio.run(run())
```

- [ ] **Step 2: Verify the script parses**

Run: `python -m py_compile scripts/discover_rainbet.py`
Expected: silent success.

- [ ] **Step 3: Commit**

```bash
git add scripts/discover_rainbet.py
git commit -m "feat(rainbet): add discovery harness"
```

---

### Task 4: Run discovery on production server

This task produces the artifacts that drive every subsequent parser/orchestration task. **No production rebuild needed** — we `docker cp` the script into the running container, run it once, and pull artifacts out. The script touches `/tmp/` only and does not modify production state. The container already has patchright installed (verified in Task 1).

- [ ] **Step 1: Copy the script into the running container**

Run: `scp scripts/discover_rainbet.py root@148.251.40.251:/tmp/discover_rainbet.py`
Then: `ssh root@148.251.40.251 "docker cp /tmp/discover_rainbet.py arnold-backend-1:/tmp/discover_rainbet.py"`
Expected: silent success on both.

- [ ] **Step 2: Run the discovery script inside the container**

Run:
```bash
ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T backend python /tmp/discover_rainbet.py"
```
Expected: Script completes within ~5 minutes. Final summary shows `>0` responses captured, hosts including `sptpub.com` and `api-a-*.sptpub.com`, and per-sport `OK` results.

- [ ] **Step 3: Pull the artifacts to local for analysis**

Run:
```bash
ssh root@148.251.40.251 "docker cp arnold-backend-1:/tmp/rainbet_discovery /tmp/rainbet_discovery"
scp -r root@148.251.40.251:/tmp/rainbet_discovery c:/tmp/rainbet_discovery
```
Expected: `c:/tmp/rainbet_discovery/` contains `summary.json`, `responses.jsonl`, `ws_frames.jsonl`, `capture.har`.

- [ ] **Step 4: Sanity-check the artifacts**

Run:
```bash
ls -la c:/tmp/rainbet_discovery/
python -c "import json; d=json.load(open('c:/tmp/rainbet_discovery/summary.json')); print(d['host_request_counts']); print(list(d['sport_results']))"
```
Expected: Non-empty `host_request_counts` with entries for `*.sptpub.com`. Sport results with each requested slug present.

---

### Task 5: Document discovery findings

**Files:**
- Create: `docs/superpowers/research/2026-05-10-rainbet-discovery.md`

This file is the bridge from discovery output to parser implementation. It must be specific enough that someone reading only this file (no spec, no code) can write the parser. Read the artifacts produced in Task 4, find the answers, write them down.

- [ ] **Step 1: Read all four artifacts**

```bash
python -c "
import json
d = json.load(open('c:/tmp/rainbet_discovery/summary.json'))
print('=== sport routes that succeeded ===')
for slug, r in d['sport_results'].items():
    print(f'  {slug}: {r.get(\"title\", \"\")} (ok={r[\"ok\"]})')
"
head -100 c:/tmp/rainbet_discovery/responses.jsonl | python -c "
import json, sys
for ln in sys.stdin:
    r = json.loads(ln)
    print(f'  [{r[\"status\"]}] {r[\"url\"]} ({r[\"body_size\"]}B) ct={r[\"content_type\"]}')
"
head -50 c:/tmp/rainbet_discovery/ws_frames.jsonl | python -c "
import json, sys
for ln in sys.stdin:
    f = json.loads(ln)
    print(f'  {f[\"direction\"]} {f[\"kind\"]} len={f[\"len\"]} {f[\"url\"][:80]}: {f[\"data\"][:150]}')
"
```

- [ ] **Step 2: Identify the four key facts and write them to `docs/superpowers/research/2026-05-10-rainbet-discovery.md`**

Use this template, filling each section with concrete data from the artifacts. No "TBD" — if a fact isn't recoverable, mark it explicitly:

```markdown
# Rainbet/Betby protocol — discovery output 2026-05-10

Source artifacts: `/tmp/rainbet_discovery/` on the production server (ephemeral).
Spec: `docs/superpowers/specs/2026-05-10-rainbet-provider-design.md`.

## 1. Sport URL slug map

Mapping arnold's internal sport keys → rainbet URL paths under `/sportsbook/`.
Determined by which routes returned a real sportsbook page (not 404 / not redirect).

| arnold key | rainbet slug | confirmed |
|---|---|---|
| football | soccer | yes/no |
| basketball | basketball | yes/no |
| tennis | tennis | yes/no |
| ice_hockey | ice-hockey | yes/no |
| american_football | american-football | yes/no |
| baseball | baseball | yes/no |
| mma | mma | yes/no |
| boxing | boxing | yes/no |
| esports | esports | yes/no |

(Fill `confirmed` from `summary.json["sport_results"]` — slug succeeded if `ok=True` AND title is "Online Sportsbook - Rainbet" (rather than a 404 title).

## 2. Shard URL pattern

Format: `wss://api-a-<shard>.sptpub.com/api/v1/ws_new?brand_id=<id>&lang=en`.

The `<shard>` value observed: `<paste from ws_frames.jsonl ws open>`.

How the renderer derives the shard — answer one of:
- (a) Hardcoded in the bt-renderer.min.js bundle
- (b) Returned by an early HTTP response from `start3.sptpub.com`
- (c) Returned by `bt-app-static-themes.sptpub.com/master/rainbet/theme.json`
- (d) Other

Evidence: `<cite specific response and field name>`.

## 3. Wire format for events/markets

Where do markets/events arrive — REST or WS?

- **REST endpoints** that returned non-empty JSON (paste 5-10 from responses.jsonl):
  - `<URL pattern>` → `<one-line description of payload>`

- **WS frames** containing market data (paste 3-5 examples from ws_frames.jsonl):
  - direction=`recv` kind=`txt|bin` len=N → `<one-line description of payload>`

Conclusion: events arrive via {REST | WS | both}. Parser will target `<choice>`.

## 4. Event/market schema

Annotated example payload (from one of the captured responses or frames). Show one full event with at least one market with at least two outcomes:

```json
{
  // paste actual capture, comments name the fields
}
```

Map to arnold's StandardEvent fields:
- `id` ← `<betby field>`
- `name` ← `<betby field>`
- `home_team` ← `<betby field>`
- `away_team` ← `<betby field>`
- `start_time` ← `<betby field>`
- `markets[].type` ← `<betby field>` (mapped via market-id-to-type table; document the IDs we see)
- `markets[].outcomes[].name` ← `<betby field>`
- `markets[].outcomes[].odds` ← `<betby field>`

## 5. Market-type ID map

Betby uses numeric/string market IDs. Capture the IDs we saw and what each represents (filter to ALLOWED_MARKETS = {1x2, moneyline, spread, total}; everything else is filtered out):

| betby market_id | type | sport | notes |
|---|---|---|---|
| <id> | 1x2 | football | match winner with draw |
| <id> | moneyline | basketball | match winner no draw |
| <id> | spread | football | asian handicap |
| <id> | total | basketball | over/under points |
```

- [ ] **Step 3: Verify every section in the doc is filled (no remaining `<...>` placeholders)**

Run: `grep -E '<[a-z ]+>' docs/superpowers/research/2026-05-10-rainbet-discovery.md`
Expected: no output. If any placeholder remains, go back to the artifacts and fill it in.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/research/2026-05-10-rainbet-discovery.md
git commit -m "docs(rainbet): document discovered protocol — sport slugs, shard, wire format"
```

---

## Phase C — Pure parser (TDD)

The parser is a set of pure functions in `backend/src/providers/rainbet.py`. Tests live in `backend/tests/providers/test_rainbet_parser.py`. The exact field names and shape come from the discovery doc — do **not** start these tasks until Task 5 is committed.

### Task 6: Sport-key map

**Files:**
- Create: `backend/src/providers/rainbet.py`
- Create: `backend/tests/providers/test_rainbet_parser.py`

- [ ] **Step 1: Write the failing test for `arnold_sport_to_rainbet_slug`**

`backend/tests/providers/test_rainbet_parser.py`:

```python
"""Tests for rainbet provider parser functions."""
import pytest
from src.providers.rainbet import arnold_sport_to_rainbet_slug


class TestArnoldSportToRainbetSlug:
    def test_football_maps_to_soccer(self):
        assert arnold_sport_to_rainbet_slug("football") == "soccer"

    def test_basketball_passes_through(self):
        assert arnold_sport_to_rainbet_slug("basketball") == "basketball"

    def test_ice_hockey_uses_dash(self):
        assert arnold_sport_to_rainbet_slug("ice_hockey") == "ice-hockey"

    def test_unknown_sport_returns_none(self):
        assert arnold_sport_to_rainbet_slug("cricket") is None
```

(If the discovery doc shows different slugs — for instance, if rainbet uses `football` not `soccer` — change these tests to match the doc before writing the implementation.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestArnoldSportToRainbetSlug -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError: cannot import name 'arnold_sport_to_rainbet_slug'`.

- [ ] **Step 3: Implement the minimal mapping in `backend/src/providers/rainbet.py`**

```python
"""Rainbet provider — Betby-backed sportsbook with Cloudflare + Turnstile bypass.

See docs/superpowers/specs/2026-05-10-rainbet-provider-design.md for design.
See docs/superpowers/research/2026-05-10-rainbet-discovery.md for protocol.
"""

# Sport-key map — derived from discovery doc Section 1.
# Update this dict when Betby/rainbet routing changes.
_ARNOLD_TO_RAINBET_SLUG: dict[str, str] = {
    "football": "soccer",
    "basketball": "basketball",
    "tennis": "tennis",
    "ice_hockey": "ice-hockey",
    "american_football": "american-football",
    "baseball": "baseball",
    "mma": "mma",
    "boxing": "boxing",
    "esports": "esports",
}


def arnold_sport_to_rainbet_slug(sport: str) -> str | None:
    """Map an arnold sport key to its rainbet URL slug. Returns None if unsupported."""
    return _ARNOLD_TO_RAINBET_SLUG.get(sport)
```

(Adjust the dict based on the discovery doc.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestArnoldSportToRainbetSlug -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/rainbet.py backend/tests/providers/test_rainbet_parser.py
git commit -m "feat(rainbet): add sport-slug map with tests"
```

---

### Task 7: Market-type ID resolver

**Files:**
- Modify: `backend/src/providers/rainbet.py`
- Modify: `backend/tests/providers/test_rainbet_parser.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/providers/test_rainbet_parser.py`:

```python
from src.providers.rainbet import resolve_market_type


class TestResolveMarketType:
    def test_known_1x2_id_returns_1x2(self):
        # Replace <ID> with a real 1x2 market_id from the discovery doc Section 5
        assert resolve_market_type(<ID>, sport="football") == "1x2"

    def test_known_moneyline_id_returns_moneyline(self):
        assert resolve_market_type(<ID>, sport="basketball") == "moneyline"

    def test_known_spread_id_returns_spread(self):
        assert resolve_market_type(<ID>, sport="football") == "spread"

    def test_known_total_id_returns_total(self):
        assert resolve_market_type(<ID>, sport="basketball") == "total"

    def test_unknown_id_returns_none(self):
        assert resolve_market_type(99999, sport="football") is None
```

Replace each `<ID>` with the actual numeric/string market id captured in the discovery doc. If the doc shows market types are encoded by string keys instead of IDs, change the parameter type accordingly.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestResolveMarketType -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `resolve_market_type` in `backend/src/providers/rainbet.py`**

```python
# Market-type IDs — from discovery doc Section 5.
# Maps Betby market_id → arnold's normalized type. Only ALLOWED_MARKETS:
# {1x2, moneyline, spread, total}. Anything not in this dict is filtered out.
_MARKET_ID_MAP: dict[int, str] = {
    # <id>: "1x2",
    # <id>: "moneyline",
    # <id>: "spread",
    # <id>: "total",
}


def resolve_market_type(market_id, sport: str) -> str | None:
    """Resolve a Betby market_id to arnold's normalized type, or None if not allowed."""
    return _MARKET_ID_MAP.get(market_id)
```

Fill the dict with the real IDs from the discovery doc.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestResolveMarketType -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/rainbet.py backend/tests/providers/test_rainbet_parser.py
git commit -m "feat(rainbet): add market-type ID resolver with tests"
```

---

### Task 8: Single-event parser — happy path

**Files:**
- Modify: `backend/src/providers/rainbet.py`
- Modify: `backend/tests/providers/test_rainbet_parser.py`

The exact structure of the input dict comes from the discovery doc Section 4 (annotated payload example). The output is `StandardEvent` (see `backend/src/core/retriever.py` for the dataclass).

- [ ] **Step 1: Read the StandardEvent dataclass to understand the output shape**

Run: `grep -n "class StandardEvent\|@dataclass" backend/src/core/retriever.py | head -20`
Expected: see the StandardEvent definition. Note its required and optional fields.

- [ ] **Step 2: Write the failing test, with input copied verbatim from the discovery doc Section 4**

Add to `backend/tests/providers/test_rainbet_parser.py`:

```python
from src.providers.rainbet import parse_event


class TestParseEvent:
    def test_parses_football_1x2_event(self):
        # Input: paste the annotated payload from discovery doc Section 4 here
        raw = {
            # ... full event dict from discovery ...
        }
        event = parse_event(raw, sport="football", provider_id="rainbet")
        assert event is not None
        assert event.provider == "rainbet"
        assert event.sport == "football"
        assert event.home_team  # normalized, non-empty
        assert event.away_team
        assert event.start_time
        assert len(event.markets) >= 1
        # find 1x2 market
        m = next((m for m in event.markets if m["type"] == "1x2"), None)
        assert m is not None
        assert len(m["outcomes"]) == 3
        names = {o["name"] for o in m["outcomes"]}
        assert names == {"home", "draw", "away"}
        for o in m["outcomes"]:
            assert isinstance(o["odds"], float)
            assert o["odds"] > 1.0
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestParseEvent -v`
Expected: FAIL with `ImportError` for `parse_event`.

- [ ] **Step 4: Implement `parse_event` in `backend/src/providers/rainbet.py`**

```python
import logging
from typing import Any

from ..core.retriever import StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


def parse_event(raw: dict, sport: str, provider_id: str) -> StandardEvent | None:
    """Parse a single Betby event dict into a StandardEvent.

    Returns None for live events, events without home/away teams, or events
    with no parseable allowed markets.

    Field names below come from discovery doc Section 4.
    """
    # Extract the fields named in the discovery doc.
    # Rename these to whatever the doc shows.
    home_raw = raw.get("<home_team_field>", "")
    away_raw = raw.get("<away_team_field>", "")
    if not home_raw or not away_raw:
        return None

    event_id = raw.get("<event_id_field>", "")
    start_time = raw.get("<start_time_field>", "")

    # Skip live / finished events. The discovery doc Section 4 documents
    # the field that indicates this; rename below.
    status = raw.get("<status_field>", "")
    if status in {"<live_status>", "<finished_status>"}:
        return None

    home_team = normalize_team_name(home_raw)
    away_team = normalize_team_name(away_raw)
    event_name = f"{home_raw} vs {away_raw}"

    # Iterate raw markets, parse each one, drop unrecognized.
    markets = []
    for raw_market in raw.get("<markets_field>", []) or []:
        m = parse_market(raw_market, sport)
        if m is not None:
            markets.append(m)

    if not markets:
        return None

    return StandardEvent(
        id=f"{provider_id}_{event_id}",
        name=event_name,
        sport=sport,
        markets=markets,
        provider=provider_id,
        url=f"https://rainbet.com/sportsbook/{arnold_sport_to_rainbet_slug(sport) or sport}/{event_id}",
        start_time=start_time,
        home_team=home_team,
        away_team=away_team,
    )


def parse_market(raw_market: dict, sport: str) -> dict | None:
    """Parse a single Betby market dict. Returns None for unsupported types."""
    market_id = raw_market.get("<market_id_field>")
    market_type = resolve_market_type(market_id, sport)
    if market_type is None:
        return None

    # Outcomes parsing — replace `<outcomes_field>`, `<outcome_name_field>`, `<odds_field>`
    # based on discovery doc Section 4.
    outcomes = []
    for raw_oc in raw_market.get("<outcomes_field>", []) or []:
        name_raw = raw_oc.get("<outcome_name_field>", "").lower()
        # Map Betby outcome names to arnold's canonical {home, draw, away, over, under}
        name = _normalize_outcome_name(name_raw, market_type)
        if name is None:
            continue
        odds = raw_oc.get("<odds_field>")
        if not isinstance(odds, (int, float)) or odds <= 1.0:
            continue
        outcomes.append({"name": name, "odds": float(odds)})

    if not outcomes:
        return None

    return {"type": market_type, "outcomes": outcomes}


def _normalize_outcome_name(name: str, market_type: str) -> str | None:
    """Map a Betby outcome name to {home, draw, away, over, under}.

    The exact source strings come from discovery doc Section 4 — adjust the
    map below based on what we actually see (e.g. "1"/"X"/"2", or "team_a"/"team_b").
    """
    name = (name or "").strip().lower()
    if market_type in ("1x2", "moneyline", "spread"):
        if name in ("1", "home", "team_a"):
            return "home"
        if name in ("x", "draw", "tie"):
            return "draw" if market_type == "1x2" else None
        if name in ("2", "away", "team_b"):
            return "away"
    if market_type == "total":
        if name in ("over", "o"):
            return "over"
        if name in ("under", "u"):
            return "under"
    return None
```

Replace every `<...>` placeholder with the actual field name from the discovery doc Section 4. Do **not** commit a file with `<...>` still in it.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestParseEvent -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/src/providers/rainbet.py backend/tests/providers/test_rainbet_parser.py
git commit -m "feat(rainbet): parse single Betby event into StandardEvent"
```

---

### Task 9: Spread/total parsers — main-line selection

Spread and total markets in Betby ship multiple lines (e.g. -1.5, -2.5, -3.5). We pick the **main line** (smallest absolute handicap for spread, smallest total for total) — same convention as `cloudbet.py`.

**Files:**
- Modify: `backend/src/providers/rainbet.py`
- Modify: `backend/tests/providers/test_rainbet_parser.py`

- [ ] **Step 1: Write the failing test for spread parsing with multiple lines**

Add to `test_rainbet_parser.py`:

```python
class TestParseSpread:
    def test_picks_main_line_smallest_absolute(self):
        # Input: a market with home/away outcomes at handicaps -1.5 and -2.5 (or -3.5).
        # Field names come from discovery doc Section 4.
        raw_market = {
            # ... copy spread market structure from discovery, with multiple lines ...
        }
        m = parse_market(raw_market, sport="football")
        assert m is not None
        assert m["type"] == "spread"
        assert len(m["outcomes"]) == 2
        home = next(o for o in m["outcomes"] if o["name"] == "home")
        away = next(o for o in m["outcomes"] if o["name"] == "away")
        assert abs(home["point"]) == 1.5  # smallest abs picked
        assert home["point"] + away["point"] == 0  # mirror points


class TestParseTotal:
    def test_picks_smallest_total(self):
        raw_market = {
            # ... copy total market structure from discovery, with multiple lines ...
        }
        m = parse_market(raw_market, sport="basketball")
        assert m is not None
        assert m["type"] == "total"
        assert len(m["outcomes"]) == 2
        over = next(o for o in m["outcomes"] if o["name"] == "over")
        under = next(o for o in m["outcomes"] if o["name"] == "under")
        assert over["point"] == under["point"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestParseSpread tests/providers/test_rainbet_parser.py::TestParseTotal -v`
Expected: FAIL — outcomes don't have `point`, or the wrong line is picked.

- [ ] **Step 3: Update `parse_market` in `backend/src/providers/rainbet.py` to handle multi-line spread/total**

Replace the existing `parse_market` body with logic that branches by market_type:

```python
def parse_market(raw_market: dict, sport: str) -> dict | None:
    """Parse a single Betby market dict. Returns None for unsupported types."""
    market_id = raw_market.get("<market_id_field>")
    market_type = resolve_market_type(market_id, sport)
    if market_type is None:
        return None

    if market_type == "spread":
        return _parse_spread(raw_market)
    if market_type == "total":
        return _parse_total(raw_market)
    return _parse_winner(raw_market, market_type)


def _parse_winner(raw_market: dict, market_type: str) -> dict | None:
    outcomes = []
    for raw_oc in raw_market.get("<outcomes_field>", []) or []:
        name = _normalize_outcome_name(raw_oc.get("<outcome_name_field>", ""), market_type)
        if name is None:
            continue
        odds = raw_oc.get("<odds_field>")
        if not isinstance(odds, (int, float)) or odds <= 1.0:
            continue
        outcomes.append({"name": name, "odds": float(odds)})
    if not outcomes:
        return None
    return {"type": market_type, "outcomes": outcomes}


def _parse_spread(raw_market: dict) -> dict | None:
    """Group spread selections by handicap, pick the main line (smallest |handicap|)."""
    lines: dict[float, dict] = {}
    for raw_oc in raw_market.get("<outcomes_field>", []) or []:
        name = _normalize_outcome_name(raw_oc.get("<outcome_name_field>", ""), "spread")
        if name not in ("home", "away"):
            continue
        odds = raw_oc.get("<odds_field>")
        hcp = raw_oc.get("<handicap_field>")
        if not isinstance(odds, (int, float)) or not isinstance(hcp, (int, float)):
            continue
        abs_h = abs(float(hcp))
        lines.setdefault(abs_h, {})[name] = {"odds": float(odds), "point": float(hcp)}
    if not lines:
        return None
    main = lines[min(lines.keys())]
    if "home" not in main or "away" not in main:
        return None
    return {
        "type": "spread",
        "outcomes": [
            {"name": "home", "odds": main["home"]["odds"], "point": main["home"]["point"]},
            {"name": "away", "odds": main["away"]["odds"], "point": -main["home"]["point"]},
        ],
    }


def _parse_total(raw_market: dict) -> dict | None:
    """Group total selections by line, pick the smallest total as main."""
    lines: dict[float, dict] = {}
    for raw_oc in raw_market.get("<outcomes_field>", []) or []:
        name = _normalize_outcome_name(raw_oc.get("<outcome_name_field>", ""), "total")
        if name not in ("over", "under"):
            continue
        odds = raw_oc.get("<odds_field>")
        total = raw_oc.get("<total_field>")
        if not isinstance(odds, (int, float)) or not isinstance(total, (int, float)):
            continue
        lines.setdefault(float(total), {})[name] = float(odds)
    if not lines:
        return None
    main_total = min(lines.keys())
    main = lines[main_total]
    if "over" not in main or "under" not in main:
        return None
    return {
        "type": "total",
        "outcomes": [
            {"name": "over", "odds": main["over"], "point": main_total},
            {"name": "under", "odds": main["under"], "point": main_total},
        ],
    }
```

Replace `<...>` placeholders with the actual field names from the discovery doc Section 4. The handicap and total field names should be in Section 4's annotated payload.

- [ ] **Step 4: Run all parser tests to verify everything still passes**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py -v`
Expected: all tests pass (sport-slug + market-type + single-event + spread + total).

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/rainbet.py backend/tests/providers/test_rainbet_parser.py
git commit -m "feat(rainbet): parse spread + total with main-line selection"
```

---

### Task 10: Sport-listing parser

The discovery doc Section 3 documents whether events arrive via REST (one big JSON response per sport) or WS (a stream of update messages). This task implements the entry-point function that takes the appropriate raw blob and returns a list of `StandardEvent`.

**Files:**
- Modify: `backend/src/providers/rainbet.py`
- Modify: `backend/tests/providers/test_rainbet_parser.py`

- [ ] **Step 1: Decide which path to implement based on the discovery doc Section 3**

If the doc says events arrive **REST** → implement `parse_sport_response(json_blob, sport, provider_id) -> list[StandardEvent]`.
If the doc says **WS** → implement `parse_sport_ws_messages(messages, sport, provider_id) -> list[StandardEvent]`.
If both → implement REST-first (simpler), WS as a build-time follow-up.

- [ ] **Step 2: Write the failing test**

Add to `test_rainbet_parser.py` (REST variant shown — adjust if WS):

```python
from src.providers.rainbet import parse_sport_response


class TestParseSportResponse:
    def test_returns_list_of_standard_events(self):
        # Copy a real sport-listing JSON from discovery responses.jsonl
        # (one with events_count >= 2 from a Betby data API endpoint)
        blob = {
            # ... actual sport listing from discovery ...
        }
        events = parse_sport_response(blob, sport="football", provider_id="rainbet")
        assert len(events) >= 1
        for e in events:
            assert e.provider == "rainbet"
            assert e.sport == "football"
            assert e.home_team and e.away_team
            assert len(e.markets) >= 1

    def test_skips_events_with_no_allowed_markets(self):
        blob = {
            # ... a listing where one event has only props/correct-score markets ...
        }
        events = parse_sport_response(blob, sport="football", provider_id="rainbet")
        # Only events with at least one allowed market survive
        assert all(len(e.markets) >= 1 for e in events)

    def test_returns_empty_list_for_empty_blob(self):
        assert parse_sport_response({}, sport="football", provider_id="rainbet") == []
        assert parse_sport_response({"<events_field>": []}, sport="football", provider_id="rainbet") == []
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestParseSportResponse -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `parse_sport_response`**

Add to `backend/src/providers/rainbet.py`:

```python
def parse_sport_response(blob: dict, sport: str, provider_id: str) -> list[StandardEvent]:
    """Parse a Betby sport-listing JSON response into StandardEvent[].

    `<events_field>` comes from discovery doc Section 4. Filters out events
    with no allowed markets.
    """
    raw_events = (blob or {}).get("<events_field>", []) or []
    out: list[StandardEvent] = []
    for raw in raw_events:
        ev = parse_event(raw, sport=sport, provider_id=provider_id)
        if ev is not None:
            out.append(ev)
    return out
```

Replace `<events_field>` based on the discovery doc.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py::TestParseSportResponse -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/src/providers/rainbet.py backend/tests/providers/test_rainbet_parser.py
git commit -m "feat(rainbet): parse sport-listing response into StandardEvent[]"
```

---

## Phase D — Browser orchestration

The orchestration class extends `BrowserRetriever` and uses the existing `BrowserTransport` (which already wraps patchright with stealth, proxy, geolocation, resource blocking, and process cleanup). We layer the CF Turnstile click loop and per-sport network capture on top. Reference patterns: `TipwinRetriever`, `SnabbareRetriever` — all extend `BrowserRetriever` and drive `self.transport.page`.

### Task 11: RainbetRetriever skeleton

**Files:**
- Modify: `backend/src/providers/rainbet.py`

- [ ] **Step 1: Update the imports block at the top of `backend/src/providers/rainbet.py`**

Add these imports alongside the existing `import logging` block at the top of the file (do NOT duplicate `from ..core.retriever import StandardEvent` if it's already there from Task 8):

```python
import asyncio
import json
from urllib.parse import urlparse

from ..core.browser_retriever import BrowserRetriever
```

- [ ] **Step 2: Add module-level constants and the class skeleton at the end of the file**

```python
# Turnstile click coordinate — the validated approach from CF-Clearance-Scraper.
# Assumes the default 1280x720 viewport; BrowserTransport launches Chromium
# with that default.
_TURNSTILE_CLICK_COORD = (210, 290)


class RainbetRetriever(BrowserRetriever):
    """Rainbet (Betby-backed) sportsbook extractor.

    Extends BrowserRetriever and drives self.transport.page (patchright Chromium).
    The transport handles patchright launch, stealth, proxy from PROXY_URL,
    Stockholm geolocation jitter, and process cleanup. We add CF Turnstile
    click logic and per-sport network capture on top.
    """

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        super().__init__(config, transport=transport)
        self._brand_id = config.get("brand_id")
        self._site_url = config.get("site_url", "https://rainbet.com/sportsbook")
        self._sport_timeout = config.get("sport_timeout", 600)
        if not self._brand_id:
            raise ValueError(f"[{self.provider_id}] brand_id required in provider config")
        self._turnstile_cleared = False

    def _get_sport_url(self, sport: str) -> str:
        slug = arnold_sport_to_rainbet_slug(sport)
        if not slug:
            return ""
        return f"{self._site_url}/{slug}"

    def parse(self, data, sport: str) -> list[StandardEvent]:
        """Not used — extraction logic lives in extract()."""
        return []

    async def extract(self, sport: str, limit: int = 0, **kwargs) -> list[StandardEvent]:
        """Per-sport extraction entry point. Filled in Task 12."""
        raise NotImplementedError("Filled in Task 12")
```

- [ ] **Step 3: Verify the file compiles**

Run: `cd backend && python -c "from src.providers.rainbet import RainbetRetriever; print(RainbetRetriever)"`
Expected: prints the class. No import errors.

- [ ] **Step 4: Verify existing parser tests still pass (skeleton didn't break imports)**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/rainbet.py
git commit -m "feat(rainbet): add RainbetRetriever skeleton"
```

---

### Task 12: Implement extract() — Turnstile clear + per-sport network capture

**Files:**
- Modify: `backend/src/providers/rainbet.py`

The factory will construct `RainbetRetriever(config, transport=BrowserTransport(headless=True, use_proxy=True))` (Task 13). Inside `extract()` we:
1. Call `await self.transport._ensure_browser()` to lazy-launch patchright (no-op if already up)
2. Use `self.transport.page` for navigation
3. On first call only, navigate to the landing page and clear Turnstile
4. On every call: navigate to the sport URL, capture `*.sptpub.com` JSON responses, parse them

Browser cleanup is inherited — the orchestrator's `extractor.close()` calls `transport.close()` (defined on `BrowserTransport`), which handles patchright + driver-PID reaping. We do NOT manage the patchright lifecycle ourselves.

- [ ] **Step 1: Replace the `RainbetRetriever` class body with a working implementation**

Replace from `class RainbetRetriever` to the end of the file with:

```python
class RainbetRetriever(BrowserRetriever):
    """Rainbet (Betby-backed) sportsbook extractor.

    Drives self.transport.page (patchright Chromium provided by BrowserTransport).
    The bt-renderer SPA mounts inside the page after we clear Cloudflare Turnstile
    and makes its own HTTP calls to Betby's data backend (*.sptpub.com); we
    capture the listing responses and parse them.

    Browser lifecycle is owned by BrowserTransport: launched on first
    `_ensure_browser()`, cleaned up by `transport.close()` (called by the
    orchestrator at the end of a run).
    """

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        super().__init__(config, transport=transport)
        self._brand_id = config.get("brand_id")
        self._site_url = config.get("site_url", "https://rainbet.com/sportsbook")
        self._sport_timeout = config.get("sport_timeout", 600)
        if not self._brand_id:
            raise ValueError(f"[{self.provider_id}] brand_id required in provider config")
        self._turnstile_cleared = False

    def _get_sport_url(self, sport: str) -> str:
        slug = arnold_sport_to_rainbet_slug(sport)
        if not slug:
            return ""
        return f"{self._site_url}/{slug}"

    def parse(self, data, sport: str) -> list[StandardEvent]:
        return []

    async def _ensure_landed_and_cleared(self):
        """Idempotent: navigate to the rainbet landing page once per run and
        clear Turnstile. After this returns, self.transport.page has a valid
        cf_clearance cookie and the Turnstile widget is gone."""
        if self._turnstile_cleared:
            return

        await self.transport._ensure_browser()
        page = self.transport.page

        logger.info(f"[{self.provider_id}] navigating to landing for Turnstile clear")
        await page.goto(self._site_url, wait_until="domcontentloaded", timeout=60_000)
        await self._clear_turnstile(page)
        self._turnstile_cleared = True
        logger.info(f"[{self.provider_id}] Turnstile cleared, ready for sport navigation")

    async def _clear_turnstile(self, page):
        """Loop clicking the Turnstile widget until cf_clearance is set AND
        the widget iframe is gone. Raises RuntimeError if not cleared in 60s.

        Validated approach: hardcoded click at (210, 290) on the 1280x720
        viewport, matching CF-Clearance-Scraper's strategy. Defensive secondary
        attempt: bbox-center click on the challenge iframe if found.
        """
        deadline = asyncio.get_event_loop().time() + 60.0
        while asyncio.get_event_loop().time() < deadline:
            cookies = await page.context.cookies()
            has_cookie = any(c["name"] == "cf_clearance" for c in cookies)
            try:
                ts_iframe = await page.query_selector(
                    "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']"
                )
                widget_present = ts_iframe is not None
            except Exception:
                widget_present = False
            if has_cookie and not widget_present:
                return
            try:
                await page.mouse.click(*_TURNSTILE_CLICK_COORD)
            except Exception:
                pass
            await page.wait_for_timeout(2000)
        raise RuntimeError(f"[{self.provider_id}] Turnstile not cleared within 60s")

    async def extract(self, sport: str, limit: int = 0, **kwargs) -> list[StandardEvent]:
        """Extract pre-match events for a sport from rainbet."""
        sport_url = self._get_sport_url(sport)
        if not sport_url:
            logger.warning(f"[{self.provider_id}] sport '{sport}' not mapped — skipping")
            return []

        await self._ensure_landed_and_cleared()
        page = self.transport.page

        # Capture *.sptpub.com JSON responses while the SPA loads the sport.
        responses_by_url: dict[str, dict] = {}

        async def grab(resp):
            host = urlparse(resp.url).hostname or ""
            if "sptpub.com" not in host:
                return
            ct = (resp.headers.get("content-type") or "")
            if "json" not in ct.lower():
                return
            try:
                body = await resp.body()
                blob = json.loads(body)
            except Exception:
                return
            responses_by_url[resp.url] = blob

        handler = lambda r: asyncio.create_task(grab(r))
        page.on("response", handler)

        try:
            logger.info(f"[{self.provider_id}] navigating to {sport_url}")
            await page.goto(sport_url, wait_until="domcontentloaded", timeout=60_000)
            # Let the SPA settle and the listing API resolve.
            await page.wait_for_timeout(15_000)
        finally:
            page.remove_listener("response", handler)

        # Parse all captured listing responses for this sport. The discovery
        # doc Section 3 names which URL pattern carries the markets — filter
        # to that pattern here.
        events: list[StandardEvent] = []
        for url, blob in responses_by_url.items():
            if "<listing_url_substring>" not in url:  # e.g. "/sport/" or "/events/"
                continue
            events.extend(parse_sport_response(blob, sport=sport, provider_id=self.provider_id))

        if limit and len(events) > limit:
            events = events[:limit]

        logger.info(
            f"[{self.provider_id}] sport={sport} parsed {len(events)} events "
            f"from {len(responses_by_url)} captured responses"
        )
        return events
```

Replace `<listing_url_substring>` with the URL substring identified in discovery doc Section 3 (the actual Betby endpoint that carries the sport listing — e.g. something like `/sport/` or `/listing/`).

**Note on cleanup:** No `close()` override needed. `BrowserRetriever`'s inherited `close()` (via `Retriever`) calls `self.transport.close()`, and `BrowserTransport.close()` reaps patchright + the driver-PID tree. Our only state to reset between runs is `_turnstile_cleared` — but the orchestrator clears the extractor cache between runs (`factory.py:clear_extractor_cache`), so we get a fresh instance each time. Nothing extra to do.

- [ ] **Step 2: Verify the file imports cleanly**

Run: `cd backend && python -c "from src.providers.rainbet import RainbetRetriever; r = RainbetRetriever({'id': 'rainbet', 'name': 'Rainbet', 'retriever_type': 'rainbet', 'brand_id': '2374656571012681728'}); print(r.provider_id, r._brand_id)"`
Expected: prints `rainbet 2374656571012681728`. No exceptions.

- [ ] **Step 3: Run all parser tests to make sure the orchestration code didn't break parsing**

Run: `cd backend && pytest tests/providers/test_rainbet_parser.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/src/providers/rainbet.py
git commit -m "feat(rainbet): implement extract() with Turnstile clear + sptpub capture"
```

---

## Phase E — Wire into arnold

### Task 13: Register in factory.py

**Files:**
- Modify: `backend/src/factory.py`

`RainbetRetriever` needs a `BrowserTransport` (with `use_proxy=True` because rainbet geo-blocks Germany). This matches the wiring pattern of `tipwin`, `gecko_v2` etc.

- [ ] **Step 1: Add the import alongside the other provider imports near the top**

Find the existing `from .providers.cloudbet import CloudbetRetriever` line. Add directly after:

```python
from .providers.rainbet import RainbetRetriever
```

- [ ] **Step 2: Add the `elif` branch in `get_extractor`**

In `factory.py`, find the branch `elif retriever_type == "cloudbet":`. Add directly after the Cloudbet block (and before the `marathon` block):

```python
        elif retriever_type == "rainbet":
            # Rainbet — Betby-backed sportsbook with CF + Turnstile bypass.
            # Uses BrowserTransport (patchright Chromium) + Bahnhof Sweden proxy
            # (rainbet geo-blocks Germany). Turnstile is cleared by clicking
            # at the validated coord (210, 290) on the 1280x720 viewport.
            # See docs/superpowers/specs/2026-05-10-rainbet-provider-design.md.
            from .core import BrowserTransport

            transport = BrowserTransport(
                headless=True, circuit_breaker=self._circuit_breaker, use_proxy=True
            )
            retriever = RainbetRetriever(config, transport=transport)
```

- [ ] **Step 3: Verify the factory still imports cleanly**

Run: `cd backend && python -c "from src.factory import ExtractorFactory; print(ExtractorFactory)"`
Expected: prints the class. No exceptions.

- [ ] **Step 4: Commit**

```bash
git add backend/src/factory.py
git commit -m "feat(rainbet): register RainbetRetriever in factory"
```

---

### Task 14: Add provider config to providers.yaml

**Files:**
- Modify: `backend/src/config/providers.yaml`

- [ ] **Step 1: Add the rainbet provider block in the `providers:` section**

Find the existing `kalshi:` provider block. Add this directly after, before any closing of the providers section:

```yaml
  rainbet:
    id: rainbet
    name: Rainbet
    domain: rainbet.com
    retriever_type: rainbet
    site_url: https://rainbet.com/sportsbook
    brand_id: "2374656571012681728"
    theme_name: rainbet
    sport_timeout: 600
    supported_sports:
      - football
      - basketball
      - tennis
      - ice_hockey
      - american_football
      - baseball
      - mma
      - boxing
      - esports
```

If the discovery doc Section 1 confirmed any of these sport slugs are NOT supported by rainbet, remove them from `supported_sports` in this block to avoid wasted nav cycles.

- [ ] **Step 2: Add rainbet to the active list**

Find the `active:` block at the bottom of the file, near `- kalshi`. Add directly after:

```yaml
  - rainbet
```

- [ ] **Step 3: Add rainbet to the `browser_antibot` extraction tier**

Find the `browser_antibot:` block under `extraction_scheduling:`. Add `rainbet` to its `providers:` list:

```yaml
  browser_antibot:
    max_concurrent_browsers: 1
    providers:
      - comeon
      - rainbet
    interval_minutes: 25
    grouped: false
```

- [ ] **Step 4: Validate the YAML still parses**

Run:
```bash
cd backend && python -c "
import yaml
d = yaml.safe_load(open('src/config/providers.yaml'))
assert 'rainbet' in d['providers'], 'rainbet provider not found'
assert 'rainbet' in d['active'], 'rainbet not in active list'
assert 'rainbet' in d['extraction_scheduling']['browser_antibot']['providers'], 'rainbet not in browser_antibot tier'
print('OK — all 3 references present')
"
```
Expected: `OK — all 3 references present`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/config/providers.yaml
git commit -m "feat(rainbet): wire provider into providers.yaml + browser_antibot tier"
```

---

## Phase F — Production verification

### Task 15: Deploy to server and run smoke test

- [ ] **Step 1: Deploy with rebuild (Dockerfile changed)**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"`
Expected: rebuild succeeds. Health endpoint responds within 2 minutes.

Per arnold's `CLAUDE.md` Multi-Agent Coordination section: confirm afterward that the running container has your code via:
```bash
ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD"
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health"
```
The git HEAD should match your local push. The boot_id in `/health` should be new.

- [ ] **Step 2: Trigger a manual rainbet extraction**

Run:
```bash
ssh root@148.251.40.251 "curl -sS -X POST 'http://localhost:8000/api/extraction/run?providers=rainbet'"
```
Expected: returns a JSON success response (run_id queued or running).

- [ ] **Step 3: Tail logs for the rainbet extraction**

Run:
```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'tail -200 /app/logs/extraction.log | grep -i rainbet'"
```
Expected lines (some variation OK):
- `[rainbet] launching patchright Chromium (proxy=True)`
- `[rainbet] navigating to landing for Turnstile clear`
- `[rainbet] Turnstile cleared, ready for sport navigation`
- `[rainbet] navigating to https://rainbet.com/sportsbook/<slug>`
- `[rainbet] sport=<sport> parsed N events from M captured responses`

If `Turnstile not cleared within 60s` appears: the click coordinate may have changed. Capture a new HAR via the discovery harness (Task 4) and adjust `_TURNSTILE_CLICK_COORD`.

- [ ] **Step 4: Query the database to confirm rainbet events landed**

Run via the postgres MCP, or:
```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
SELECT sport, COUNT(*) AS events
FROM provider_events
WHERE provider = 'rainbet'
  AND created_at > NOW() - INTERVAL '15 minutes'
GROUP BY sport
ORDER BY events DESC;\""
```
Expected: at least one row, with at least one sport having ≥ 5 events.

- [ ] **Step 5: Check match rate against Pinnacle for the freshest run**

Run:
```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
SELECT sport,
       events_processed,
       events_matched,
       ROUND(100.0 * events_matched / NULLIF(events_processed, 0), 1) AS match_pct
FROM provider_run_metrics
WHERE provider_id = 'rainbet'
ORDER BY id DESC LIMIT 10;\""
```
Expected: `match_pct` ≥ 30 for at least one major sport (football or basketball or esports). Treat anything ≥ 50 as good per the spec's success criteria.

- [ ] **Step 6: Confirm no regression in other providers**

Run:
```bash
ssh root@148.251.40.251 "curl -sS http://localhost:8000/health/extraction"
```
Expected: status `healthy`. No new failures in providers other than rainbet.

- [ ] **Step 7: Commit any tweaks needed to make smoke test pass**

If the smoke test surfaced bugs (slug fix, Turnstile coordinate change, missed market type), fix them, push, redeploy, and rerun the smoke test until it passes.

```bash
git add <files>
git commit -m "fix(rainbet): <specific fix>"
```

Then redeploy and re-run Steps 2-6 until clean.

---

### Task 16: Update CLAUDE.md if rainbet introduces new operational knowledge

Some patterns worth capturing in the project memory only after they're verified in production:

- [ ] **Step 1: Decide whether anything is worth adding to `CLAUDE.md`**

Candidates for inclusion (only add if non-obvious from the spec/plan/code):
- The rainbet provider's match rate vs Pinnacle baseline
- Any Turnstile coord change needed at deploy time
- Patchright browser cache directory if it differs from playwright's
- Any unexpected proxy interaction

- [ ] **Step 2: If adding, edit `CLAUDE.md` Active Providers / Extraction Tiers section**

Add a one-line note about rainbet under the existing extraction tier table. Keep it terse — the spec and plan are the long-form references.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(rainbet): note operational baseline in CLAUDE.md"
```

(Skip this whole task if no operational knowledge was learned that isn't already in the code or spec.)

---

## Done

The provider is wired, deployed, and verified.

What's NOT done (out of scope per spec):
- Stake.com / other Betby tenants — would extend `RainbetRetriever` (or factor a base class) at that time
- Live odds — pre-match only
- Markets beyond {1x2, moneyline, spread, total}
- Bet placement (mirror workflow)

If the match rate is poor or the provider becomes flaky, follow the same diagnostic flow as ComeOn: check the discovery artifacts, re-capture HAR, look for protocol changes.
