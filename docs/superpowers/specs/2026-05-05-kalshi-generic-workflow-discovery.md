# Kalshi → Generic Workflow Discovery

**Date:** 2026-05-05
**Status:** Discovery complete; implementation pending user approval
**Scope:** Replace the kalshi-python SDK path with browser-cookie web-traffic interception, conforming to the generic-workflow contract in [docs/mirror-workflow.md](../../mirror-workflow.md).

---

## 0. TL;DR

Kalshi's web app talks to **`api.elections.kalshi.com/v1/...`** — a stable cookie-authed REST API the SPA already drives during normal use. Every method in the §5 workflow contract maps cleanly to one or two endpoints there, with no DOM scraping required. Switching off the SDK eliminates: the per-user RSA key file (`data/kalshi_key.pem`), the `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY_PEM` env vars, and the `kalshi-python` dep. Auth becomes "user is logged in to kalshi.com in the mirror" — same model as Polymarket, Altenar, etc.

**All four discovery unknowns are now resolved** (verified live via a real $1 market buy on 2026-05-05). One non-trivial divergence from the SDK: the web order POST takes `market_id` (UUID), not `market_ticker` — workflow has to resolve the mapping during `navigate_to_event`.

---

## 1. Current state (what we're replacing)

| File | Role today |
|------|-----------|
| `arnold/mirror/workflows/strategies/kalshi.py` | Strategy override; all auth + API via `kalshi-python` SDK |
| `data/mirror_intel/kalshi.json` | Intel; `autonomous_placement: true`, `login.method: balance_api`, `event_url_template: /markets/{provider_market_ticker}` |
| `data/kalshi_key.pem` + `arnold/data/kalshi_key.pem` | RSA private key materialised on disk by `_key_path()` |
| `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PEM` env vars | Required for SDK auth |

**Browser tab is opened but unused** — the strategy's docstring is explicit: *"The Playwright tab at https://kalshi.com/markets/<ticker> exists for visual context only — no DOM automation. All real work is REST API."* And the URL template is wrong against today's site (`/markets/{ticker}` → 404; real URL is `/markets/{series_lower}/{slug}/{event_lower}`).

**Browser interception is not wired**: `_BALANCE_KEYWORDS`, `_HISTORY_KEYWORDS`, `_BET_PLACEMENT_KEYWORDS` in [browser.py](../../../arnold/mirror/browser.py) contain no kalshi patterns; `_DOMAIN_TO_PROVIDER` contains `"kalshi.com"` for tab-classification only.

---

## 2. Discovery method

Driven via `POST /mirror/browser/eval/kalshi` with the user's logged-in session. Captured:
1. `performance.getEntriesByType('resource')` — every URL the SPA fetched while we drove portfolio tabs + a market page.
2. Wrapped `window.fetch` + `XMLHttpRequest.{open,setRequestHeader,send}` to record request headers and bodies for the `api.elections.kalshi.com` host.
3. Direct fetches with credentials to verify endpoint existence + response shape.

DOM was inspected only for navigation hooks (Portfolio link, role=tab elements). No DOM scraping is needed for any data field — every value is in JSON responses.

---

## 3. Auth model

| Concern | Where |
|---------|-------|
| Session identity | `userId` cookie (uuid) — exposed to JS; stamped into every user-scoped path |
| Session secret | HTTP-only session cookies (browser holds them) |
| CSRF token | `localStorage["csrfToken"]` (JSON-wrapped: `{"value":"...="}`) — also re-sent as `x-csrf-token` header on every authed XHR |
| AWS WAF token | `aws-waf-token` cookie (binary blob ~314 B) — re-sent verbatim as `x-aws-waf-token` header on every authed XHR. Refresh visible in `awswaf_token_refresh_timestamp` localStorage key |

**Required headers on every authed call to `api.elections.kalshi.com`:**

```
accept: application/json
x-csrf-token: <localStorage.csrfToken value>
x-aws-waf-token: <aws-waf-token cookie value>
cookie: <browser default — Playwright sends them>
```

Bare `fetch(url, {credentials:'include'})` returns **401 `INVALID_CSRF_TOKEN`** without these headers (verified). With them, all endpoints below return 200.

**Empirical:** the runtime CSRF differs from `localStorage["csrfToken"]` by a JSON wrapper (`{"value":"X"}` vs `X`). Strategy must `JSON.parse` the localStorage value before using it.

---

## 4. Endpoint catalog (cookie-authed, all GET unless noted)

User UUID below is `<U>` = `userId` cookie.

### Login proof / balance / portfolio

| Endpoint | Response (live sample) | Purpose |
|----------|------------------------|---------|
| `/v1/users/<U>/balance` | `{"balance": 1000}` | **Cash balance, in cents.** 1000 = $10.00. THIS is `sync_balance`. |
| `/v1/users/<U>/portfolio/current_value` | `{"value":{"a":1000,"v":0,"pending_v":0,"unrealized_pnl":0,"ts":1777582800,"cumulative_deposits":1000}}` | `a`=cash cents, `v`=position market value, `pending_v`=resting-order locked, `unrealized_pnl`. Optional secondary balance source. |
| `/v1/users/<U>/sampled_portfolio` | (not captured — for chart) | History of `a`+`v`. Not needed by workflow. |

### Settlement / bet history

| Endpoint | Maps to | Notes |
|----------|---------|-------|
| `/v1/users/<U>/event_positions?position_status=open` | open positions | Empty array when none |
| `/v1/users/<U>/event_positions?position_status=close&settlement_status=unsettled` | "Activity" — closed but not yet paid out | |
| `/v1/users/<U>/event_positions?position_status=close&settlement_status=settled&limit=N` | **bet history** | THIS is `sync_history`'s "settled" feed. Pair with `position_status=open` for "pending". |
| `/v1/users/<U>/orders?status=resting&page_size=200&cursor=` | resting (open limit) orders | Use to detect partially-filled bets that are still resting |
| `/v1/users/<U>/trades?...` | individual fills | Granular per-fill data; `event_positions` already aggregates per market |

### Event / market navigation

| Endpoint | Purpose |
|----------|---------|
| `/v1/cached/events/?tickers={EVENT_TICKER}` | Event detail by ticker (lightweight, cached) |
| `/v1/series/{SERIES_TICKER}/events/{EVENT_TICKER}` | Event detail with markets nested |
| `/v1/series/{SERIES_TICKER}/events/{EVENT_TICKER}?with_markdown=true` | Same + description markdown |
| `/v1/cached/series/{SERIES}/events/{EVENT}?with_markdown=true` | Cached variant |
| `/v1/series/?series_tickers={CSV}` | Bulk series fetch |
| `/v1/events/?tickers={CSV}` | Bulk events fetch by ticker (used for browse pages) |
| `/v1/series/{SERIES}/markets/{MARKET_UUID}/forecast_history?start_ts=...&end_ts=...&period_interval=...&candlestick_function=mean_p` | OHLC-style yes-price candles (chart) |

**URL pattern on kalshi.com:** `/markets/{series_lower}/{event_slug}/{event_lower}` — example `/markets/kxtrumpmention/what-will-trump-say/kxtrumpmention-26apr30`. The slug segment is decorative; only `series_lower` and `event_lower` map to API tickers (uppercase).

**Mapping bet → URL:**
- We already store `provider_event_id` / `provider_market_ticker` on opportunities.
- Kalshi event ticker convention: `{SERIES}-{EVENT_SUFFIX}` (e.g. `KXTRUMPMENTION-26APR30`). Series ticker is the prefix before the first `-`.
- Slug segment can be omitted: `https://kalshi.com/markets/kxtrumpmention/x/kxtrumpmention-26apr30` resolves; the SPA will redirect `x` to the canonical slug. Workflow can hardcode `x` or `_` for the slug to skip the lookup.

### Live orderbook

No WebSocket observed during placement on a live NBA market — Kalshi's web app uses **HTTP polling**, not WS, for price updates. Live yes_bid / yes_ask / last_price are present on the market detail records (delivered via `/v1/cached/events/?tickers={EVENT_TICKER}` and `/v1/series/{SERIES}/events/{EVENT_TICKER}`). For tighter live pricing, the public-API `/v1/markets/{MARKET_TICKER}/orderbook` endpoint should work (Kalshi's docs API host); not confirmed live but standard.

For the workflow, **read `yes_ask` from the event detail call already used in `navigate_to_event`** — one fewer round-trip, and the SPA itself does this.

### Place order — CAPTURED 2026-05-05

`POST https://api.elections.kalshi.com/v1/users/<U>/orders`

Headers (in addition to the standard `x-csrf-token` + `x-aws-waf-token`):
```
content-type: application/json
accept: application/json
```

Request body (single $1 market buy, 1 contract YES @ $0.87):
```json
{
  "market_id": "543fbae4-4da3-4fd1-b2d1-08cc66890f49",
  "side": "yes",
  "user_side": "yes",
  "order_action": "buy",
  "order_type": "market",
  "time_in_force": "immediate_or_cancel",
  "count_fp": "1.00",
  "price_dollars": "0.8700",
  "expiration_unix_ts": 0,
  "max_cost_cents": 0,
  "sell_position_capped": false,
  "post_only": false,
  "order_source": "web"
}
```

**Response 201:**
```json
{
  "order": {
    "order_id": "8a294c31-15b9-4dae-83ec-54b98ffeae26",
    "user_id": "...",
    "market_id": "543fbae4-4da3-4fd1-b2d1-08cc66890f49",
    "status": "pending",
    "is_yes": true,
    "price": 87,
    "price_dollars": "0.8700",
    "create_ts": "2026-05-05T14:50:50.332388Z",
    "expiration_ts": null,
    "order_type": "market",
    "order_action": "buy",
    "user_side": "yes",
    "initial_count": 1, "initial_count_fp": "1.00",
    "fill_count": 1,    "fill_count_fp": "1.00",
    "remaining_count": 0, "remaining_count_fp": "0.00",
    "taker_fees": 0,
    "extra_cost": 0
  },
  "status": ""
}
```

**Important shape divergences from the SDK we're replacing:**

| SDK contract (today) | Web API (real) |
|----------------------|----------------|
| `ticker` (string, e.g. `KXNBAGAME-26MAY05LALOKC-OKC`) | `market_id` (UUID, e.g. `543fbae4-...`) — must be resolved first |
| `count` (int) | `count_fp` (string, supports fractional) |
| `yes_price` (int cents) | `price_dollars` (string dollars, 4-decimal) |
| `type: "limit"` | `order_type: "market"` (or `"limit"`) — distinct field name |
| `expiration_ts: int(time.time())+60` | `expiration_unix_ts: 0` (means "no expiration") + `time_in_force: "immediate_or_cancel"` for IOC market orders |

**Status interpretation:** `order.status: "pending"` is the create-time response; the order is actually **fully filled** when `fill_count == initial_count` (and `remaining_count == 0`). Don't rely on the `status` string — it may not have updated server-side yet by the time the POST returns. Use the count fields. (This matches the SDK strategy's existing logic: poll fill state, not status string.)

**Fee:** 1¢ per $1-equivalent contract — `position_cost: 87`, `fees_paid: 1` post-fill (visible in `/v1/users/<U>/positions/{MARKET_ID}`). Account dropped from $10.00 → $8.99 (-87¢ - 1¢) after this fill.

### Resolving `market_ticker` → `market_id` (the new step)

The web API requires `market_id` (UUID) on POST, not `market_ticker`. Sources for the mapping:

| Endpoint | Returns |
|----------|---------|
| `GET /v1/cached/events/?tickers={EVENT_TICKER}` | Event with `markets[]` nested; each market has both `id` (UUID) and `ticker` |
| `GET /v1/series/{SERIES}/events/{EVENT_TICKER}` | Same shape, fresher (uncached) |
| `GET /v1/users/<U>/event_positions/{EVENT_TICKER}` | Per-market positions with `market_id` + `market_ticker` (only useful if user already has a position) |

Workflow does the lookup once during `navigate_to_event`: fetch the event, build a `{market_ticker: market_id}` dict, store on the page or in the workflow's `_pending` dict alongside `yes_price_cents`.

### Cash balance — second source

The web also exposes `GET /v1/users/<U>/balances` (PLURAL) returning subaccount-shaped data:
```json
{"subaccount_balances": [{"subaccount_number": 0, "balance": "8.9900", "updated_ts": 1777990334}]}
```
Balance is a **string in dollars** here, vs `/balance` (singular) which returns `{"balance": 899}` int-cents. **Prefer `/balance`** — simpler parsing, no string-decimal conversion.

---

## 5. Mapping to the §5 workflow contract

| Method | Implementation in generic Kalshi |
|--------|---------------------------------|
| `domain` | `kalshi.com` (already in `_DOMAIN_TO_PROVIDER`) |
| `home_url` | `https://kalshi.com/portfolio` (logged-in users land here; balance call fires automatically) |
| `find_tab(context)` | Standard match on `kalshi.com` host. No change. |
| `check_login(page)` | `await _api_get('/v1/users/<U>/balance')` — 200 with valid `{balance:int}` ⇒ logged in; 401 / no `userId` cookie ⇒ not logged in. |
| `sync_balance(page)` | Same call; return `balance / 100.0` in dollars. |
| `fetch_balance(page)` | Same as `sync_balance` — cheap (single 200 with no body parsing concern). Add it so READY_TO_RUN polls keep balance fresh. |
| `sync_history(page)` | `await _api_get('/v1/users/<U>/event_positions?position_status=open')` + `?position_status=close&settlement_status=settled&limit=50`. Map to `HistoryEntry` per the SDK strategy's existing logic (status="pending"|"won"|"lost"|"void"). |
| `navigate_to_event(page, bet)` | `page.goto(f"https://kalshi.com/markets/{series_lower}/x/{event_ticker_lower}")`. No DOM wait beyond `domcontentloaded`. |
| `prep_betslip(page, bet, stake)` | Compute `yes_price_cents`, `count`, store in module-level `_pending` dict (mirrors current SDK strategy). Optionally pre-click YES/NO toggle in the betslip DOM if the user wants a visible cue, but not required for placement (we POST directly). |
| `check_live_price(page, bet)` | Read `yes_ask` from `/v1/cached/events/?tickers={EVENT_TICKER}` (already fetched in `navigate_to_event`). Compute `live_odds = 100 / yes_ask_cents`. No WS needed. |
| `place_bet(page, bet, stake)` | `await _api_post('/v1/users/<U>/orders', body=…)` with `market_id` (NOT `ticker`), `count_fp` (string), `price_dollars` (string), `time_in_force: "immediate_or_cancel"`, `order_type: "market"` for IOC market buys. Returns 201 with the filled order; treat `fill_count == initial_count` as filled. Poll `/v1/users/<U>/orders/{order_id}` only if `remaining_count > 0` (rare for market IOC). |
| `parse_placement_response(body)` | Extract `body["order"]["order_id"]`. Fill: `body["order"]["fill_count"]` (int). Price: `body["order"]["price"]` (cents int) or `body["order"]["price_dollars"]` (string). |

The polling logic for `place_bet` (5 polls × 1 s, then cancel; trust create on poll-failure × 2) is non-obvious and well-tested. **Port it line-for-line.** The fields change from SDK attributes to JSON keys; that's it.

### Helper: `_api_get` / `_api_post`

```python
async def _api(page, method, path, body=None):
    csrf_raw = await page.evaluate("localStorage.getItem('csrfToken')")
    csrf = json.loads(csrf_raw)["value"] if csrf_raw and csrf_raw.startswith("{") else csrf_raw
    waf = await page.evaluate("document.cookie.match(/aws-waf-token=([^;]+)/)?.[1]")
    user_id = await page.evaluate("document.cookie.match(/userId=([^;]+)/)?.[1]")
    if not (csrf and waf and user_id):
        raise NotLoggedIn
    url = "https://api.elections.kalshi.com" + path.replace("<U>", user_id)
    return await page.context.request.fetch(
        url,
        method=method,
        headers={"accept": "application/json", "x-csrf-token": csrf, "x-aws-waf-token": waf,
                 **({"content-type": "application/json"} if body else {})},
        data=json.dumps(body).encode() if body else None,
    )
```

Use Playwright's `page.context.request` (not `aiohttp`) so the cookie jar is shared with the live session. No extra `httpx` client needed.

---

## 6. Mapping to the §7 8-step provider checklist

### Step 1 — Interception wiring

Add to [browser.py](../../../arnold/mirror/browser.py):

```python
_BALANCE_KEYWORDS = (..., "/v1/users/", "/balance",  # narrow to api.elections.kalshi.com host check below
                     # OR specific:
                     "api.elections.kalshi.com/v1/users/")  # then post-filter on '/balance' substring
_HISTORY_KEYWORDS = (..., "event_positions")
_BET_PLACEMENT_KEYWORDS = (..., "api.elections.kalshi.com/v1/users/")  # post-filter on '/orders' POST
```

Cleaner: per-provider keyword tuples gated on `_detect_provider(url) == "kalshi"`. Match the Polymarket precedent — its placement keyword is the FQDN-prefix `clob.polymarket.com/order`; Kalshi's would be `api.elections.kalshi.com/v1/users/`-prefix + path-suffix `/orders` (POST) for placement, `/balance` for balance, `/event_positions` for history.

`_extract_balance` for kalshi: response is `{"balance": cents_int}` → `cents/100`.

### Step 2 — Open + login

`home_url = https://kalshi.com/portfolio`. Login detection: `check_login` ⇒ a successful `/v1/users/<U>/balance` 200. Failure mode: page still on `/sign-in` or `userId` cookie missing.

### Step 3 — Balance sync

Interceptor (passive, fires when SPA polls `/balance` every ~30 s on the portfolio page) feeds `_post_balance_async`. Active `sync_balance` calls the endpoint directly.

### Step 4 — Settlement

`sync_history` returns `HistoryEntry` list from `event_positions` open + settled. **Settlement matching reuses the existing 3-tier logic in [provider_runner.py](../../../arnold/mirror/provider_runner.py)** — no Kalshi-specific changes.

### Step 5 — Run gate

No provider-specific behaviour. Standard yellow → green flow. `fetch_balance` keeps the balance fresh during idle.

### Step 6 — Navigate

`navigate_to_event` → `page.goto(...)` to the canonical market URL.

### Step 7 — Sync odds + edge

`check_live_price` → orderbook fetch → `(decimal_odds, edge_pct)`.

### Step 8 — Place

`place_bet` → POST `/v1/users/<U>/orders` → poll → return `PlacementResult`. The interceptor will catch the same POST and route through `browser._on_response` → `parse_placement_response` automatically; the workflow's own POST is the canonical path, the interceptor mostly serves as the trigger to re-sync balance afterwards.

---

## 7. Cluster + capability matrix updates

No changes needed. Kalshi is already in:
- `UNLIMITED_PROVIDERS = {"pinnacle","polymarket","cloudbet","kalshi"}` ([provider_runner.py](../../../arnold/mirror/provider_runner.py)).
- Generic-workflow factory in [workflows/__init__.py](../../../arnold/mirror/workflows/__init__.py).
- §10 cluster table as standalone (no siblings).
- §9 capability matrix row at line 628 of [docs/mirror-workflow.md](../../mirror-workflow.md). Existing checkmarks remain accurate after the migration — implementation surface changes, the user-visible behaviour does not.

---

## 8. Migration plan

1. ~~**Capture the two missing pieces**~~ — ✅ done 2026-05-05; full body shape in §4.
2. **Write `arnold/mirror/workflows/strategies/kalshi.py`** as a full rewrite — no SDK import, no `kalshi-python` dep, only `page.context.request` calls. Polling logic is rarely needed: market-IOC orders fill in a single round-trip (`fill_count == initial_count` on the 201 response). Limit orders or partial fills hit the existing poll path.
3. **Update `data/mirror_intel/kalshi.json`**:
   - `domain`: `kalshi.com` (unchanged)
   - `home_url`: `https://kalshi.com/portfolio` (NEW — current intel doesn't set one explicitly; defaults to root)
   - `event_url_template`: `/markets/{series_lower}/x/{event_ticker_lower}` (FIX — current `/markets/{provider_market_ticker}` is wrong)
   - `login.method`: `cookie_balance` (semantic rename — implementation no longer relies on SDK)
   - drop `autonomous_placement: true` if generic-workflow infers it from `place_bet` being defined; keep otherwise.
4. **Add interception keywords** to [browser.py](../../../arnold/mirror/browser.py): `api.elections.kalshi.com` per-domain keyword set as outlined in §6 Step 1.
5. **Drop env vars + key file**: remove `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PEM` from `.env` / `.env.docker`; delete `arnold/data/kalshi_key.pem` + `backend/data/kalshi_key.pem` + the `_key_path` helper.
6. **Drop `kalshi-python` dependency** from `pyproject.toml` (verify nothing else imports it — `backend/src/providers/kalshi.py` may; if so, keep the dep until provider extraction is migrated, or use the public REST API directly there too).
7. **Acceptance test:** walk the §12 checklist on a $1 live placement. Daily-cap row 8 doesn't apply (Kalshi is UNCAPPED). Cluster sibling row 18 doesn't apply (standalone).

---

## 9. Risks / open questions

| Risk | Mitigation |
|------|-----------|
| **CSRF token rotates mid-session** — strategy may cache a stale value | Re-read `localStorage["csrfToken"]` on every authed call (cheap; one `page.evaluate`). Don't cache. |
| **`aws-waf-token` cookie expires** — observed ~5 min lifetime in similar AWS WAF setups | The SPA refreshes it automatically (visible in `awswaf_session_storage` localStorage). Re-read the cookie on every call rather than caching. |
| **Order POST shape differs from SDK** | TODO §4 — capture before writing the strategy. |
| **Live orderbook is WebSocket-only** (didn't observe one in ~30 s loiter) | If polling `/orderbook` is too stale for arb timing, fall back to opening the SPA's WS connection ourselves — discoverable by clicking around the live "Mentions" market for longer. |
| **Geo / IP gating** | Kalshi requires US IP for trading. The mirror runs on the user's local IP; if VPN is involved, this can break placement with a 4xx. The current SDK setup also relies on this — no regression. |
| **Account verification + KYC** | Same — already handled via the live web account; SDK auth bypassed nothing here. |

---

## 10. Why this is the right shape

- **Eliminates the only provider in the mirror that needs a private key on disk.** Every other generic-workflow provider uses cookie session only.
- **Removes dependency on `kalshi-python`** (unmaintained relative to Kalshi's public API; SDK lags new endpoints).
- **Makes Kalshi auth identical to Polymarket / Altenar / Kambi** — onboarding is now "log in with the mirror open."
- **Keeps the validated placement-polling logic** (5×1 s, trust-on-poll-error) — that's the part of the current strategy worth porting verbatim.
- **Visual context for the user** — the betslip is now actually visible during placement (today the SDK fires while the page is on `/`).
