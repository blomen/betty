# Mirror Wiring Status

Which data we can capture per provider when the mirror browser is active.

## Legend

| Symbol | Meaning |
|--------|---------|
| Y | Wired and working |
| ~ | Partially wired / needs testing |
| - | Not yet wired |
| N/A | Not applicable (e.g. no account) |

### Workflow Column

| Symbol | Meaning |
|--------|---------|
| A | Autonomous — full DOM automation, no user interaction |
| G | Guided — workflow has login/balance API, user places manually |
| M | Manual fallback — no workflow wired, interceptor catches API calls |

## Capabilities

| # | Provider | Platform | Workflow | Bet Placement | Settle Bets | Sync Balance | Settle Method |
|---|----------|----------|:---:|:---:|:---:|:---:|---|
| 1 | campobet | Altenar | G | ~ | Y | Y | API intercept: widgetBetHistory |
| 2 | quickcasino | Altenar | G | ~ | Y | Y | API intercept: widgetBetHistory |
| 3 | betinia | Altenar | G | ~ | Y | Y | API intercept: widgetBetHistory |
| 4 | swiper | Altenar | G | ~ | ~ | Y | API intercept: widgetBetHistory |
| 5 | lodur | Altenar | G | ~ | ~ | Y | API intercept: widgetBetHistory |
| 6 | dbet | Altenar | G | ~ | ~ | Y | API intercept: widgetBetHistory |
| 7 | spelklubben | Gecko V2 | G | Y | ~ | ~ | API intercept: coupon-history |
| 8 | betsson | Gecko V2 | G | Y | ~ | ~ | API intercept: coupon-history |
| 9 | betsafe | Gecko V2 | G | Y | ~ | ~ | API intercept: coupon-history |
| 10 | nordicbet | Gecko V2 | G | Y | ~ | ~ | API intercept: coupon-history |
| 11 | bethard | Gecko V2 | G | Y | ~ | ~ | API intercept: coupon-history |
| 12 | unibet | Kambi | G | ~ | ~ | Y | DOM scrape: /bethistory (Swedish) |
| 13 | leovegas | Kambi | G | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 14 | expekt | Kambi | G | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 15 | 888sport | Kambi | M | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 16 | speedybet | Kambi | G | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 17 | x3000 | Kambi | G | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 18 | goldenbull | Kambi | G | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 19 | 1x2 | Kambi | G | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 20 | comeon | Custom | M | - | - | - | Not wired |
| 21 | hajper | Custom | M | - | - | - | Not wired |
| 22 | lyllo | Custom | M | - | - | - | Not wired |
| 23 | snabbare | Snabbare | M | - | - | - | Not wired |
| 24 | 10bet | TenBet | M | - | - | - | Not wired |
| 25 | mrgreen | Spectate | M | - | - | - | Not wired |
| 26 | betmgm | Kambi | G | ~ | ~ | - | DOM scrape: /bethistory (Swedish) |
| 27 | vbet | BetConstruct | M | - | - | - | Not wired |
| 28 | interwetten | Interwetten | M | - | - | - | Not wired |
| 29 | coolbet | Coolbet | M | - | - | - | Not wired |
| 30 | tipwin | Tipwin | M | - | - | - | Not wired |
| 31 | pinnacle | Pinnacle | A | Y | Y | Y | API: /0.1/bets?status=settled |
| 32 | polymarket | Polymarket | A | ~ | ~ | Y | DOM scrape: /portfolio?tab=history |

### Settlement Auto-Detection

When the user navigates to a provider's bet history page in the mirror browser,
the system auto-detects and scrapes settlements. Detection triggers:

| Platform | Trigger URL | Method |
|----------|-------------|--------|
| Pinnacle | Any page with `spelhistorik` or `account` in URL | API call via page session |
| Altenar | Auto — interceptor catches `widgetBetHistory` API response | HTTP response intercept |
| Gecko V2 | Auto — interceptor catches `coupon-history` API response | HTTP response intercept |
| Kambi | `/betting/sports/bethistory` | DOM scrape (Swedish regex) |
| Polymarket | `/portfolio?tab=history` | DOM scrape (Lost/Claimed rows) |

## Platform Notes

### Altenar (campobet, quickcasino, betinia, swiper, lodur, dbet)
- **Bet placement**: `POST sb2betgateway-altenar2.biahosted.com/api/widget/placeWidget` — HTTP, fully parseable
- **Settle bets**: `POST sb2bethistory-gateway-altenar2.biahosted.com/api/WidgetReports/widgetBetHistory` — status codes: 1=won, 2=lost, 3=void, 4=cashout
- **Balance**: `GET {domain}/sv/api/v3/account/balance` — `result.cash.total`
- **Provider detection**: `integration` field in request body (campose, quickcasinose, betiniase2, etc.)
- **Open bets**: Same widgetBetHistory with `statuses=[0]` filter — needs wiring
- **Odds sync**: Not yet explored
- **Cashout**: Not yet explored — may be in widgetBetHistory or separate endpoint

### Gecko V2 (spelklubben, betsson, betsafe, nordicbet, bethard)
- **Bet placement**: `POST {domain}/api/sb/v2/coupons` — HTTP, couponId in response, odds/stake in request
- **Settle bets**: No bet history API discovered yet — need to find endpoint (may be in account section)
- **Balance**: `GET cloud-api.{domain}/wallets` — `Balances.SEK.Real.Balance`; also `payment-stats` for deposit/withdraw totals
- **Team names**: Resolved via events-table API enrichment or event cache from browsing
- **Open bets**: Not yet explored
- **Cashout**: Not yet explored

### Kambi (unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull, 1x2, betmgm)
- **Bet placement**: WebSocket on `push.aws.kambicdn.com` or `kambicdn.com` — frames contain couponId, odds, stake, event names
- **Settle bets**: No bet history API in HTTP layer — Kambi renders history client-side via WS. Need to intercept WS frames with settlement data
- **Balance**: `GET {domain}/wallitt/mainbalance` (Unibet pattern) — `balance.cash`
- **Provider detection**: Operator code in URL path (`/ubse/` = unibet, `/888se/` = 888sport, etc.)
- **Open bets**: Likely in WS frames — needs investigation
- **Cashout**: Kambi supports cashout via WS — needs investigation

### Custom / Other
- **comeon, hajper, lyllo**: ComeOn platform, likely WebSocket-based — needs investigation
- **snabbare**: Unknown API structure — needs investigation
- **10bet**: TenBet platform — needs investigation
- **mrgreen, 888sport**: Spectate platform — needs investigation
- **vbet**: BetConstruct — needs investigation
- **interwetten**: Custom — needs investigation
- **coolbet, tipwin**: Unknown — needs investigation
- **pinnacle**: REST API with auth — bet placement via `/v1/bets/straight`, balance via `api.arcadia.pinnacle.se/0.1/wallet/balance` → `{"amount": 535.0, "currency": "SEK"}`, deposit visible via cashier URL `depamount` param
- **polymarket**: Blockchain-based — different paradigm

### Polymarket (polymarket)
- **Wallet type**: Magic (email login), signature type 1
- **Balance**: `GET data-api.polymarket.com/value?user={proxy_wallet}` → `[{"value": 123.45}]` (USDC)
- **Deposit**: Via Swapped widget (`POST widget.swapped.com/api/v1/order/create_order`) → Stripe → USDC on Polygon
- **Open orders**: `GET clob.polymarket.com/data/orders` — intercepted from browser traffic
- **Bet placement**: Playwright UI automation — navigate to market → select outcome → verify price → enter amount → confirm via Fun.xyz
- **Price verification**: `GET clob.polymarket.com/book?token_id={id}` — check best ask vs expected price, abort if slippage > 2%
- **Settlement**: Via Gamma API `fetch_resolved()` — binary outcome markets resolve to $1 (won) or $0 (lost)
- **Proxy wallet**: `0x71fca29E6B31a93d262D2972C9b361Af371D426d`
- **Signing address**: `0x19a769e2F52baa34D16258F9cd5Fd6D572522974`

## API Endpoint Patterns Discovered

```
# Altenar
POST  sb2betgateway-altenar2.biahosted.com/api/widget/placeWidget      # bet placement
POST  sb2bethistory-gateway-altenar2.biahosted.com/api/WidgetReports/widgetBetHistory  # history
GET   {domain}/sv/api/v3/account/balance                                # balance
GET   sb2bonus-altenar2.biahosted.com/api/WidgetBonus/GetAvailableBoosts  # boosts

# Gecko V2
POST  {domain}/api/sb/v2/coupons                                       # bet placement
GET   {domain}/api/sb/v1/widgets/events-table/v2                       # event data
GET   cloud-api.{domain}/wallets                                        # balance
GET   cloud-api.{domain}/player/payment-stats                           # deposit/withdraw

# Kambi
WS    push.aws.kambicdn.com                                             # all WS traffic
GET   eu1.offering-api.kambicdn.com/offering/v2018/{op}/...             # odds
GET   cf-mt-auth-api.kambicdn.com/player/api/v2019/{op}/reward/...     # bonuses
GET   {domain}/wallitt/mainbalance                                      # balance (Unibet)

# Pinnacle
POST  api.arcadia.pinnacle.se/v1/bets/straight                         # bet placement
GET   api.arcadia.pinnacle.se/0.1/wallet/balance                       # balance
GET   cashier.pinnacle.se/GenericPaymentTrustly.asp?depamount={amt}    # deposit (Trustly)
GET   pinnacle.se/en/account/.../first-deposit/processed/              # deposit confirmed

# Polymarket
GET   data-api.polymarket.com/value?user={proxy_wallet}              # portfolio value (USDC)
GET   data-api.polymarket.com/v1/leaderboard?user={proxy_wallet}     # leaderboard
GET   gamma-api.polymarket.com/is-logged-in                          # auth check (type: magic)
GET   gamma-api.polymarket.com/users                                 # user profile + proxy wallet
GET   clob.polymarket.com/data/orders                                # open orders
GET   clob.polymarket.com/book?token_id={id}                         # order book
POST  api.fun.xyz/v1/fops                                            # tx execution (Fun.xyz)
POST  widget.swapped.com/api/v1/order/create_order                   # fiat deposit
GET   polymarket.com/api/account/has-deposited?address={wallet}      # deposit status
```

## Generic Wiring Workflow

For every provider, follow this process. Try API first (from JSONL recordings), fall back to DOM.

### Recording

The mirror records two types of data to JSONL (`data/mirror_recordings/mirror/*.jsonl`):

1. **HTTP traffic** — every API call with full request/response bodies
2. **DOM events** — every click (tag, text, class, coords) and input (name, value)

Both are in the same JSONL file, distinguished by `"type": "dom"` for DOM events.

### Wiring Steps (per provider)

When user visits a new provider site:

**Step 1: Discover APIs from JSONL**
```bash
# Find all unique API endpoints for a provider domain
grep "provider-domain.com" recordings.jsonl | jq -r '.url' | sort -u
```

**Step 2: Wire each capability (API first, DOM fallback)**

| Capability | API approach | DOM fallback |
|---|---|---|
| **Login detection** | Intercept balance/auth API response | DOM scrape for username/balance text |
| **Balance sync** | Intercept balance API (`/wallets`, `/balance`) | DOM scrape nav bar |
| **Settle bets** | Intercept bet history API response | DOM scrape bet history page |
| **Navigate to event** | Build URL from event slug/ID | Click through sport → league → event |
| **Read live price** | API call from page context (`page.evaluate(fetch)`) | DOM scrape price buttons |
| **Place bet** | API call with auth cookies from page context | DOM: click outcome → type stake → click confirm |
| **Record bet** | Parse API response (odds, stake, confirmation) | Parse DOM confirmation toast |

**Step 3: Test manually, then automate**
1. User does it once manually → JSONL captures the full path (HTTP + DOM clicks)
2. Wire the API/DOM approach into the workflow
3. User confirms it works → mark provider as wired in this doc
4. Enable auto-play when confident

### File Locations

- `backend/src/mirror/interceptor.py` — HTTP/WS/DOM listeners, bet patterns
- `backend/src/mirror/service.py` — orchestration, settlement, recording
- `backend/src/mirror/workflows/*.py` — per-platform implementations
- `backend/src/mirror/recorder.py` — JSONL writer (HTTP + DOM events)
- `backend/data/mirror_recordings/` — raw JSONL files per session
