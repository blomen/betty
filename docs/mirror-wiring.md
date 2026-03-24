# Mirror Wiring Status

Which data we can capture per provider when the mirror browser is active.

## Legend

| Symbol | Meaning |
|--------|---------|
| Y | Wired and working |
| ~ | Partially wired / needs testing |
| - | Not yet wired |
| N/A | Not applicable (e.g. no account) |

## Capabilities

| # | Provider | Platform | Bet Placement | Settle Bets | Sync Balance | Sync Open Bets | Sync Odds | Cashout |
|---|----------|----------|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | campobet | Altenar | ~ | Y | Y | - | - | - |
| 2 | quickcasino | Altenar | ~ | Y | Y | - | - | - |
| 3 | betinia | Altenar | ~ | Y | Y | - | - | - |
| 4 | swiper | Altenar | ~ | ~ | Y | - | - | - |
| 5 | lodur | Altenar | ~ | ~ | Y | - | - | - |
| 6 | dbet | Altenar | ~ | ~ | Y | - | - | - |
| 7 | spelklubben | Gecko V2 | Y | - | ~ | - | - | - |
| 8 | betsson | Gecko V2 | Y | - | ~ | - | - | - |
| 9 | betsafe | Gecko V2 | Y | - | ~ | - | - | - |
| 10 | nordicbet | Gecko V2 | Y | - | ~ | - | - | - |
| 11 | bethard | Gecko V2 | Y | - | ~ | - | - | - |
| 12 | unibet | Kambi | ~ | - | Y | - | - | - |
| 13 | leovegas | Kambi | ~ | - | - | - | - | - |
| 14 | expekt | Kambi | ~ | - | - | - | - | - |
| 15 | 888sport | Kambi | ~ | - | - | - | - | - |
| 16 | speedybet | Kambi | ~ | - | - | - | - | - |
| 17 | x3000 | Kambi | ~ | - | - | - | - | - |
| 18 | goldenbull | Kambi | ~ | - | - | - | - | - |
| 19 | 1x2 | Kambi | ~ | - | - | - | - | - |
| 20 | comeon | Custom | - | - | - | - | - | - |
| 21 | hajper | Custom | - | - | - | - | - | - |
| 22 | lyllo | Custom | - | - | - | - | - | - |
| 23 | snabbare | Snabbare | - | - | - | - | - | - |
| 24 | 10bet | TenBet | - | - | - | - | - | - |
| 25 | mrgreen | Spectate | - | - | - | - | - | - |
| 26 | betmgm | Kambi | ~ | - | - | - | - | - |
| 27 | vbet | BetConstruct | - | - | - | - | - | - |
| 28 | interwetten | Interwetten | - | - | - | - | - | - |
| 29 | coolbet | Coolbet | - | - | - | - | - | - |
| 30 | tipwin | Tipwin | - | - | - | - | - | - |
| 31 | pinnacle | Pinnacle | - | N/A | - | - | - | - |
| 32 | polymarket | Polymarket | - | N/A | - | - | - | N/A |

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
- **pinnacle**: REST API with auth — bet placement via `/v1/bets/straight`
- **polymarket**: Blockchain-based — different paradigm

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
```
