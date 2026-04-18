# Kalshi + Smarkets Integration Design

**Date**: 2026-04-18
**Status**: Approved, awaiting implementation plan

## Goal

Add two new sharp-adjacent providers to the Firev stack:

- **Kalshi** — CFTC-regulated US prediction-market exchange. Went global Oct 2025, Sweden not on exclusion list. API-first (REST + WebSocket). Treated as **playable + consensus input**, not sharp (Pinnacle stays the sole sharp source).
- **Smarkets** — UK/Swedish-licensed betting exchange. User is personally IP-banned, so **signal-only**: odds feed consensus but we do not place bets.

Neither replaces Pinnacle as the fair-odds baseline. Both strengthen consensus for value-bet edge detection.

## Non-goals

- No shared prediction-market base class refactor (Polymarket stays untouched; Kalshi duplicates shape)
- No alternate-line extraction (main line only, matches Pinnacle convention)
- No Smarkets bet placement (user is banned)
- No Kalshi front-end redesign (Play tab auto-picks up new provider via existing dropdown + `types/index.ts` list)

## Architecture

```
backend/src/
├── providers/
│   ├── kalshi.py               # NEW — Retriever, unauth REST for market data
│   └── smarkets.py             # NEW — Retriever, unauth public JSON
├── constants.py                # rename ALLOWED_SPORTS → PINNACLE_SPORTS
│                               # add KALSHI_FEE_RATE
│                               # add smarkets to SIGNAL_ONLY_PROVIDERS
│                               # extend PLATFORM_MAP, EXTENDED_MARKET_PROVIDERS
├── config/providers.yaml       # new kalshi + smarkets blocks, new kalshi tier
└── mirror/workflows/kalshi.py  # NEW — backend copy of workflow

firevsports/mirror/workflows/
└── kalshi.py                   # NEW — KalshiWorkflow, API-first + browser tab
```

Module responsibilities:

- `providers/kalshi.py` — pure data extractor, unauthenticated, paginates Kalshi markets, converts YES/NO binary contracts → `StandardEvent` with moneyline / 1x2 / spread / total outcomes. Volume filter, fee-adjusted effective odds.
- `providers/smarkets.py` — pure data extractor, unauthenticated, pulls last-executed prices from Smarkets' public JSON endpoints, emits `StandardEvent` with `is_signal=True` meta.
- `mirror/workflows/kalshi.py` — `KalshiWorkflow(ProviderWorkflow)`, `autonomous_placement=True`, API client (kalshi-python SDK) for balance/place, Playwright tab for visual context only (no DOM automation).

## Kalshi extractor

**Endpoint**: `https://api.elections.kalshi.com/trade-api/v2/markets` (public, unauthenticated). Paginate via `cursor` until exhausted.

**Event model**: Kalshi groups contracts under an **event** (e.g. `KXNBAGAME-26APR18LALGSW`). Each event contains one or more **markets** (binary contracts). Mapping:

- **Moneyline** (no-draw sports): event has two `yes`-priced contracts, one per team. Prices are complementary (sum ≈ $1 minus spread).
- **1x2** (soccer): event has three contracts (`HOME`, `DRAW`, `AWAY`).
- **Spread/total**: event may contain alternate lines as separate contracts. Pick the line closest to 50/50 = main line.

**Sport mapping** — `KALSHI_SERIES_TO_SPORT` dict keyed on ticker prefix:

```python
{
    "KXNBAGAME": "basketball",
    "KXNFLGAME": "american_football",
    "KXMLBGAME": "baseball",
    "KXNHLGAME": "ice_hockey",
    "KXNCAAFGAME": "american_football",
    "KXNCAABGAME": "basketball",
    "KXTENNIS": "tennis",   # prefix match
    "KXUFC": "mma",
    "KXBOXING": "boxing",
    "KXEPL": "football",
    "KXUCL": "football",
    "KXWC": "football",
    # extend as new series appear
}
```

Scope = `PINNACLE_SPORTS` (i.e. the renamed `ALLOWED_SPORTS`). Any series not in the map is ignored.

**Filters**:

- `status == "active"`
- `volume_usd >= MIN_MARKET_VOLUME` (default $100, config-tunable in `providers.yaml`)
- Skip markets where all prices equal exactly $0.50 (untraded)

**Price conversion**:

```python
effective_price = price + KALSHI_FEE_RATE * price * (1 - price)
decimal_odds = 1 / effective_price
```

`KALSHI_FEE_RATE = 0.02` in `constants.py`, tunable after live data.

**Depth / microstructure**: for markets passing the volume filter, call `GET /markets/{ticker}/orderbook` → top of book. Store `bid`, `ask`, `depth_usd` in outcome dict (same fields as Polymarket, same downstream consumption).

**Config** (`providers.yaml`):

```yaml
kalshi:
  id: kalshi
  name: Kalshi
  domain: kalshi.com
  retriever_type: kalshi
  base_url: https://api.elections.kalshi.com/trade-api/v2
  params:
    min_volume_usd: 100
  supported_sports: [football, basketball, tennis, ice_hockey,
                     american_football, baseball, mma, boxing]
```

**Scheduling**: new dedicated tier `kalshi`, `interval_minutes: 5`, `grouped: false`. Mirrors Polymarket's own tier.

## Smarkets extractor (signal-only)

**Endpoints** (unauthenticated public JSON, same as their web frontend uses):

- `GET https://api.smarkets.com/v3/events/?state=upcoming&type_domain=sport&limit=200&offset=…`
- `GET https://api.smarkets.com/v3/events/{event_id}/markets/`
- `GET https://api.smarkets.com/v3/markets/{market_id}/last_executed_prices/`
- `GET https://api.smarkets.com/v3/markets/{market_id}/quotes/` (fallback)

**IP strategy**: try direct from Hetzner (DE IP) first. If geoblocked, set `SMARKETS_PROXY_URL` env var to the existing Bahnhof SE gost endpoint (port 1080). Config-driven, empty = direct.

**Sport scope**: `PINNACLE_SPORTS`. Map Smarkets `type_scope` → canonical sport names:

```python
SMARKETS_TYPE_SCOPE_TO_SPORT = {
    "football": "football",
    "basketball": "basketball",
    "tennis": "tennis",
    "ice-hockey": "ice_hockey",
    "american-football": "american_football",
    "baseball": "baseball",
    "mma": "mma",
    "boxing": "boxing",
}
```

Politics, specials, niche sports excluded.

**Price model**:

1. Primary: `last_executed_price` — revealed fair price
2. Fallback: average of best back + best lay (mid-market from `/quotes/`)
3. Convert integer price (0–10000, representing %) → decimal: `decimal_odds = 10000 / price`
4. **No fee adjustment** — signal-only, commission doesn't enter stored odds

**Filters**: drop markets with no `last_executed_price` in the last 24h (`min_trades_24h: 1`).

**Parsing flow**:

1. Paginate `/events/` with `state=upcoming`, `type_domain=sport`, keep events in sport-scope
2. For each event, fetch `/markets/`, keep only 1x2 / moneyline / spread / total
3. For each kept market, fetch `/last_executed_prices/` (asyncio.gather with bounded semaphore)
4. Build `StandardEvent` with `provider_meta.is_signal = True`
5. Drop markets failing `min_trades_24h` filter

**Config** (`providers.yaml`):

```yaml
smarkets:
  id: smarkets
  name: Smarkets
  domain: smarkets.com
  retriever_type: smarkets
  base_url: https://api.smarkets.com/v3
  proxy_url: ${SMARKETS_PROXY_URL}   # empty = direct
  params:
    min_trades_24h: 1
  supported_sports: [football, basketball, tennis, ice_hockey,
                     american_football, baseball, mma, boxing]
```

**Scheduling**: add `smarkets` to existing `signal_international` tier alongside `cloudbet` + `marathon`. No new tier.

**Consensus integration**: `smarkets` added to `SIGNAL_ONLY_PROVIDERS`. Existing scanner already feeds signal-only odds into consensus — zero scanner changes.

## Kalshi play workflow

**Class**: `KalshiWorkflow(ProviderWorkflow)` in both `backend/src/mirror/workflows/kalshi.py` and `firevsports/mirror/workflows/kalshi.py`.

**Config flags**:

```python
platform = "kalshi"
autonomous_placement = True
```

**Auth**:

- Env vars: `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PEM` (multi-line PEM, escaped newlines) — stored in `.env.docker`
- Dependency: `kalshi-python` SDK (added to `pyproject.toml`)
- Lazy-load via `_load_sdk()` (same pattern as Polymarket)
- Fall back to DOM workflow stub if creds absent (safety net, never expected to trigger)

**Browser tab for visual context**:

- `navigate_to_event()` opens/reuses a Playwright tab at `https://kalshi.com/markets/<ticker>`
- No DOM automation — tab is purely visual
- `check_login()` stub returns True (API auth is independent of web session)

**Method implementations**:

| Method | Implementation |
|---|---|
| `sync_balance()` | `GET /portfolio/balance` (signed) → USD cents |
| `sync_history()` | `GET /portfolio/fills?limit=200` → `HistoryEntry` list |
| `navigate_to_event(event)` | open/reuse Playwright tab at market URL |
| `prep_betslip(outcome)` | store pending order params locally |
| `check_live_price(outcome)` | `GET /markets/{ticker}` → current `yes_ask`, re-evaluate edge |
| `place_bet()` | `POST /portfolio/orders` with `action=buy, side=yes, type=limit, yes_price, count`, 60s expiry; return `PlacementResult` with fill |
| `redeem_winnings()` | no-op (settles to USD balance on close) |
| `close_tab()` | close Playwright tab |

**Stake sizing**:

- Kalshi trades in whole contracts (integers). `count = floor(usd_stake / yes_price)`
- Minimum 1 contract
- Realized USD spend = `count * yes_price`, written back to bet record

**Settlement**:

- Kalshi resolves YES/NO at close; winning contracts pay $1.00
- Existing `pending_loop` picks up pending Kalshi bets, calls `sync_history()` periodically, matches fills via ticker + timestamp, writes settlement to `bets` table
- No new settlement logic

**Placement safety**:

- **Limit orders at current `yes_ask`** (not market orders). Prevents slippage on thin markets
- `expiration_ts = now + 60s`. If unfilled, frontend shows retry UI
- Diverges from Polymarket's market-take model; documented in code comment

**Daily cap**: uncapped (same tier as pinnacle, polymarket, cloudbet). Add `kalshi` to the uncapped-providers list in `play_loop.py`.

## Config, constants, env vars, rename

**`constants.py`** changes:

```python
# rename (mechanical)
PINNACLE_SPORTS = frozenset({...})   # was ALLOWED_SPORTS

# add
KALSHI_FEE_RATE = 0.02

# extend
SIGNAL_ONLY_PROVIDERS = frozenset({"marathon", "stake", "smarkets"})

PLATFORM_MAP.update({
    "kalshi": "kalshi",
    "smarkets": "smarkets",
})

EXTENDED_MARKET_PROVIDERS = SHARP_PROVIDERS | frozenset({"polymarket", "kalshi"})
```

Kalshi is deliberately **not** in `SHARP_PROVIDERS` — it feeds consensus, doesn't replace Pinnacle.

**`providers.yaml`** changes: two new provider blocks (defined under *Kalshi extractor* and *Smarkets extractor* above), new `kalshi` scheduling tier at `interval_minutes: 5`, `smarkets` appended to `signal_international` tier, both appended to `active`.

**Env vars** (`.env.docker`):

```
KALSHI_API_KEY_ID=<from kalshi dashboard>
KALSHI_PRIVATE_KEY_PEM=<multiline PEM, \n-escaped>
SMARKETS_PROXY_URL=   # empty = direct from DE IP; set to gost URL if blocked
```

**Python dependency** (`pyproject.toml`): `kalshi-python = "^1.0"` (verify exact latest during impl).

**Rename scope**: `ALLOWED_SPORTS` → `PINNACLE_SPORTS` via grep + replace across codebase (~10–15 files: scanner, extractor bootstrap, sport filtering). Ancillary but keeps the name honest — the constant represents Pinnacle's coverage, not a generic allowlist.

**User action items** (manual, outside code):

1. Create Kalshi account at `kalshi.com`, complete international KYC
2. Fund with USD (wire/card from SE bank)
3. Generate API key pair in Kalshi dashboard, copy ID + private PEM into `.env.docker`
4. Deploy via `scripts/server-deploy.sh rebuild backend`
5. Verify Kalshi extraction run via postgres MCP (`extraction_runs`, `provider_run_metrics`)
6. Test a small Kalshi bet end-to-end before relying on autonomous flow

## Testing

**Unit tests** (`backend/tests/`):

- `test_kalshi_parser.py` — fixtures for 2–3 events per sport (NBA moneyline, EPL 1x2, NFL spread, MLB total). Assert:
  - Binary YES/NO combines correctly into 2-way moneyline / 3-way 1x2
  - Volume filter drops markets below `MIN_MARKET_VOLUME`
  - Price → decimal conversion respects `KALSHI_FEE_RATE`
  - Alternate spread/total lines collapse to main line (closest to 50/50)
  - Outcome dict includes `bid`, `ask`, `depth_usd`, `provider_meta.ticker`
- `test_smarkets_parser.py` — fixtures for events/markets/last_executed_prices. Assert:
  - `last_executed_price` preferred; mid-back-lay fallback works
  - Markets without 24h trades dropped
  - Integer price (0–10000) → decimal conversion correct

No tests for the Kalshi workflow itself — follows Polymarket's convention (manual integration test instead).

## Rollout

1. Extractors + parsers + tests + `PINNACLE_SPORTS` rename in one PR
2. Deploy extractors only, let Kalshi + Smarkets run 24h collecting data
3. Verify: match rates, opportunities generated, no scanner regressions
4. Kalshi play workflow in a second PR once extraction is proven healthy
5. Enable autonomous Kalshi placement in Play UI after manual small-bet test

## Manual integration checklist

1. **Kalshi extraction dry run** — `POST /api/extraction/run?providers=kalshi`, verify `extraction_runs` success, nonzero `events_processed`, opportunity rows link back to Kalshi
2. **Smarkets extraction dry run** — same for smarkets. On geoblock error → set `SMARKETS_PROXY_URL`, retry
3. **Kalshi small-bet test** — fund $20, pick an NBA moneyline with obvious edge via Play tab, confirm placement, balance decrement, fill in `sync_history()`
4. **Settlement test** — wait for a placed Kalshi bet to resolve, verify `pending_loop` records settlement in `bets` table
5. **Consensus sanity** — scanner output includes kalshi + smarkets on `Opportunity.consensus_sources`

## Rollback

- Remove `kalshi` / `smarkets` from `active` list → scheduler stops running them; existing rows stay intact
- Rename rollback only if something breaks (shouldn't — pure rename)

## Open questions deferred to implementation

- Exact `kalshi-python` SDK version and method signatures — pin during impl
- Kalshi fee formula precision — start with flat `KALSHI_FEE_RATE = 0.02`, tune from fills data
- Smarkets actual DE-IP geoblock status — verify on first dry run, add proxy if needed
- Kalshi international KYC timeline — user-driven, not a code concern
