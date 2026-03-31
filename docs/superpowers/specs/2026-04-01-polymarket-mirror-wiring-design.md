# Polymarket Mirror Wiring Design

**Date**: 2026-04-01
**Branch**: `play-v3-session-manager`

## Context

Polymarket is the only provider in the mirror wiring matrix with zero capabilities wired. The user browses Polymarket in the mirror browser (Magic/email wallet, signature type 1). The CLOB SDK (`py-clob-client`) cannot be used because Magic wallets don't expose private keys. All interaction must go through browser traffic interception and Playwright UI automation.

### Account Details (from mirror traffic)
- **Wallet type**: Magic (email login)
- **Signing address**: `0x19a769e2F52baa34D16258F9cd5Fd6D572522974`
- **Proxy wallet** (holds funds): `0x71fca29E6B31a93d262D2972C9b361Af371D426d`
- **Currency**: USDC (exchange rate: 10.50 SEK/USDC)

## Goals

Wire all mirror capabilities for Polymarket:
1. **Balance sync** — automatic from browser traffic
2. **Deposit detection** — automatic from balance delta
3. **Bet placement** — Playwright UI automation, triggered from Fire step
4. **Price verification** — confirm CLOB price hasn't moved before each bet
5. **Open orders** — intercept order data from browser traffic
6. **Settlement** — match resolved markets against pending bets

## 1. Passive Interception

### 1.1 Provider Detection

Add `polymarket.com` to `BetInterceptor._PROVIDER_DOMAINS`:
```python
"polymarket.com": "polymarket",
```

### 1.2 Balance Sync

**Intercept pattern**: `data-api.polymarket.com/value` (GET)

Response format:
```json
[{"user": "0x71fca...", "value": 0}]
```

The `value` field is portfolio value in USDC. Add to `_FINANCIAL_KEYWORDS` or handle via a Polymarket-specific check in `_on_response` (since the URL pattern differs from existing sportsbook balance endpoints).

**Balance extraction** in `MirrorService._extract_balance`:
```python
# Polymarket: [{"user": "0x...", "value": 123.45}]
if isinstance(data, list) and data and "user" in data[0] and "value" in data[0]:
    return float(data[0]["value"])
```

Convert USDC → SEK using the rate from `providers.yaml` (10.50). The existing `_sync_balance` flow handles delta detection and `deposit_detected` SSE events.

### 1.3 Deposit Detection

Two sources:
1. **Swapped widget**: Intercept `POST widget.swapped.com/api/v1/order/create_order` — indicates user initiated a fiat→USDC deposit. Store as trace.
2. **Balance delta**: When balance increases between syncs, the existing `_sync_balance` flow already fires `deposit_detected`.

No new code needed for (2). For (1), add `swapped.com` URL pattern as a Polymarket-specific financial intercept.

### 1.4 Open Orders

**Intercept pattern**: `clob.polymarket.com/data/orders` (GET)

This endpoint returns the user's open limit orders. Parse and expose via SSE for the frontend to display order status.

## 2. Active Bet Placement

### 2.1 API Endpoint

```
POST /api/mirror/place-bets
```

Request body:
```json
{
  "bets": [
    {
      "bet_id": 42,
      "market_slug": "btc-updown-5m-1774997100",
      "token_id": "abc123...",
      "outcome": "Yes",
      "amount_usdc": 25.0,
      "expected_price": 0.62,
      "max_slippage_pct": 2.0
    }
  ]
}
```

- `bet_id`: Internal bet ID (from batch/capital allocation step)
- `market_slug`: Polymarket event slug for navigation
- `token_id`: CLOB token ID for price verification
- `outcome`: "Yes" or "No"
- `amount_usdc`: Stake in USDC
- `expected_price`: Price at time of batch creation (0.00–1.00)
- `max_slippage_pct`: Max acceptable price movement (default 2%)

### 2.2 Price Verification

Before confirming each bet:

1. Intercept `GET clob.polymarket.com/book?token_id=...` from the browser, OR
2. Fetch the order book directly via `context.request.get(f"https://clob.polymarket.com/book?token_id={token_id}")`

Compare current best ask (for Buy) or best bid (for Sell) against `expected_price`:
```python
slippage_pct = abs(current_price - expected_price) / expected_price * 100
if slippage_pct > max_slippage_pct:
    # Abort — price moved too much
    return {"status": "skipped", "reason": "slippage", "expected": expected_price, "actual": current_price}
```

### 2.3 Playwright Automation Flow

Sequential per bet (Polymarket UI handles one tx at a time):

```
1. Navigate to https://polymarket.com/{market_slug}
2. Wait for market page to load (networkidle)
3. Click the outcome button ("Yes" or "No")
4. Verify price in the order form matches expectations (slippage check)
5. Clear amount input → type amount_usdc
6. Click "Buy" / confirm button
7. Wait for Fun.xyz transaction popup → auto-confirm if it appears
8. Wait for confirmation response (intercept POST api.fun.xyz/v1/fops)
9. Report result via SSE: bet_placed or bet_failed
```

**Selectors** (to be refined by inspecting Polymarket DOM):
- Outcome buttons: likely `[data-testid="outcome-yes"]` / `[data-testid="outcome-no"]` or similar
- Amount input: the USDC amount field in the order form
- Buy button: the primary CTA in the order form
- Fun.xyz confirm: popup/iframe confirmation button

**Error handling**:
- Network timeout → retry once, then skip with `bet_failed`
- Insufficient balance → abort remaining bets, report
- Cloudflare challenge → abort, notify user
- Fun.xyz popup doesn't appear → timeout after 15s, skip

### 2.4 Result Capture

Intercept the Fun.xyz transaction response (`POST api.fun.xyz/v1/fops`) to confirm the bet was actually placed on-chain. Parse the response for tx hash and confirmation status.

Also intercept `GET clob.polymarket.com/data/orders` after placement to verify the order appears.

### 2.5 SSE Events

| Event | Data | When |
|---|---|---|
| `polymarket_bet_placing` | `{bet_id, market_slug, outcome, amount}` | Starting placement |
| `polymarket_bet_price_check` | `{bet_id, expected, actual, slippage_pct}` | After price verification |
| `polymarket_bet_placed` | `{bet_id, tx_hash, final_price, amount}` | Confirmed on-chain |
| `polymarket_bet_failed` | `{bet_id, reason, details}` | Failed/skipped |
| `polymarket_batch_complete` | `{placed, skipped, failed, total}` | All bets processed |

## 3. Settlement

Polymarket markets resolve on-chain. The existing `PolymarketRetriever.fetch_resolved()` already checks Gamma API for closed events and winner resolution.

Wire into mirror settlement flow:
1. After extraction runs, check for resolved Polymarket markets
2. Match against pending Polymarket bets in DB (by event + outcome)
3. Stage settlements (same `_pending_settlements` pattern as other providers)
4. User confirms via existing `POST /api/mirror/settlements/confirm`

Payout = `stake_usdc / buy_price` if won, 0 if lost (binary outcome markets).

## 4. Files

| File | Change |
|---|---|
| `mirror/interceptor.py` | Add `polymarket.com` to `_PROVIDER_DOMAINS`, add Polymarket URL patterns to financial keywords |
| `mirror/service.py` | Add USDC balance extraction, `place_polymarket_bets()` automation method, Polymarket settlement matching |
| `mirror/parsers/polymarket.py` | **New** — parse balance, orders, Fun.xyz tx responses |
| `api/routes/mirror.py` | Add `POST /api/mirror/place-bets` endpoint |
| `docs/mirror-wiring.md` | Update Polymarket row with wired capabilities |

## 5. Not in Scope

- `py-clob-client` SDK (Magic wallet incompatible)
- On-chain balance queries (everything from browser traffic)
- Changes to ExecutionPanel UI (already supports Polymarket tier)
- Limit order support (market orders only via UI)
- Sell/exit position automation (buy only for now)
