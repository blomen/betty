# Polymarket API-First Workflow Design

**Date**: 2026-04-14  
**Status**: Draft  
**Goal**: Replace DOM-based Polymarket workflow with API-first approach using `py-clob-client` SDK, matching Altenar's GUIDED two-phase pattern.

## Problem

The current Polymarket workflow is 100% DOM-based:
- **Balance**: Scrapes `nav` text for "Cash $XXX" — breaks on any UI change
- **Prices**: Reads button text for ¢ prices — no orderbook depth, fragile selectors
- **Stake filling**: Clicks +$1/+$5/+$10/+$100 buttons — can't fill arbitrary amounts like $37.42
- **Positions**: Scrapes portfolio rows with regex — misses data, fragile
- **Settlement**: Fuzzy text matching scraped history rows against DB — unreliable
- **Placement**: Records bet as "placed" without actually submitting — user must click Buy manually

## Solution

Use the Polymarket CLOB API (`py-clob-client` SDK) for all operations. Browser stays open for visual context only (user sees the market page). The workflow becomes GUIDED mode like Altenar: prep → user confirms → submit order via API.

## Architecture

```
Extraction (server)                    Play Loop (local)
┌─────────────────┐                    ┌──────────────────────────┐
│ Gamma API        │                    │ PolymarketWorkflow       │
│ → parse markets  │                    │                          │
│ → store token_id │──── provider_meta ─│→ check_login (API)       │
│   in provider_meta│   (event_slug,    │→ sync_balance (API)      │
│ → CLOB orderbook │    token_id,       │→ navigate_to_event (DOM) │
│   for pricing    │    poly_home/away) │→ check_live_price (API)  │
└─────────────────┘                    │→ prep_betslip (SDK sign)  │
                                       │→ confirm_bet (SDK post)   │
                                       │→ sync_history (Data API)  │
                                       │→ fetch_positions (API)    │
                                       │→ settle_all (API + DB)    │
                                       └──────────────────────────┘
                                                │
                                       ClobClient(key, chain_id=137,
                                                  signature_type=1)
```

## Authentication

Polymarket uses a two-level auth system:
- **L1**: Wallet private key signs EIP-712 messages → generates API credentials
- **L2**: API credentials (apiKey, secret, passphrase) + HMAC-SHA256 for trading requests

**Setup**:
1. User exports Polymarket wallet private key (Settings → Export in browser)
2. Store in `.env.local` (gitignored): `POLY_PRIVATE_KEY=0x...`, `POLY_FUNDER_ADDRESS=0x...`
3. On workflow init, create `ClobClient` and derive API creds once
4. Signature type `1` (POLY_PROXY) for email/Magic wallet accounts (most common)

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    key=os.getenv("POLY_PRIVATE_KEY"),
    chain_id=137,  # Polygon mainnet
    signature_type=1,  # POLY_PROXY (email wallet)
    funder=os.getenv("POLY_FUNDER_ADDRESS"),
)
client.set_api_creds(client.create_or_derive_api_creds())
```

**Fallback**: If no private key is configured, fall back to current DOM-based workflow (graceful degradation). Log a warning on init.

## Extraction Change: Store `token_id` in `provider_meta`

Currently `_build_outcome()` uses `token_id` for CLOB price lookups but does NOT persist it. We need to store it so the play loop can reference it for live price checks and order placement.

**Change in `_build_outcome()`** (`backend/src/providers/polymarket.py`):
```python
def _build_outcome(self, name, price, token_id=None, **extra):
    outcome = {"name": name, "odds": self._price_to_odds(price)}
    if token_id:
        outcome["token_id"] = token_id  # NEW — persisted to provider_meta
        # ... existing bid/ask/depth fields ...
    outcome.update(extra)
    return outcome
```

**Storage pipeline** (`backend/src/pipeline/storage.py`): Already merges outcome-level fields into `provider_meta` via `outcome.get("provider_meta", {})`. But `token_id` is a top-level outcome field, not nested under `provider_meta`. Two options:

- **Option A**: Move `token_id` into a nested `provider_meta` dict on the outcome. Requires changing `_build_outcome` to nest it.
- **Option B**: Have the storage pipeline explicitly extract `token_id` from the outcome dict and include it in `provider_meta`.

**Chosen: Option A** — cleaner, keeps provider_meta responsibility in the provider code:
```python
def _build_outcome(self, name, price, token_id=None, **extra):
    outcome = {"name": name, "odds": self._price_to_odds(price)}
    if token_id:
        meta = {"token_id": token_id}
        if (bid := self._clob_bids.get(token_id)) is not None:
            meta["bid"] = bid
        if (ask := self._clob_asks.get(token_id)) is not None:
            meta["ask"] = ask
        if (depth := self._clob_depth.get(token_id)) is not None:
            meta["depth_usd"] = depth
        outcome["provider_meta"] = meta
    outcome.update(extra)
    return outcome
```

This way `provider_meta` on each odds row contains: `{event_slug, poly_home, poly_away, token_id, bid, ask, depth_usd}`.

## Workflow Methods

### `__init__`

```python
class PolymarketWorkflow(ProviderWorkflow):
    platform = "polymarket"

    def __init__(self, provider_id, domain, mode=WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)
        self._client: ClobClient | None = None
        self._init_client()

    def _init_client(self):
        key = os.getenv("POLY_PRIVATE_KEY")
        funder = os.getenv("POLY_FUNDER_ADDRESS")
        if not key:
            logger.warning("[polymarket] No POLY_PRIVATE_KEY — API features disabled, DOM fallback")
            return
        sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
        self._client = ClobClient(
            host="https://clob.polymarket.com",
            key=key, chain_id=137,
            signature_type=sig_type,
            funder=funder,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())
        logger.info("[polymarket] CLOB client initialized")
```

Mode changes from `AUTONOMOUS` → `GUIDED` (two-phase like Altenar).

### `check_login` — API-based

No more DOM scraping. Check if CLOB client has valid credentials by calling a lightweight endpoint.

```python
async def check_login(self, page):
    if not self._client:
        return await self._check_login_dom(page)  # DOM fallback
    try:
        result = self._client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return result is not None
    except Exception:
        return False
```

### `sync_balance` — API-based

```python
async def sync_balance(self, page):
    if not self._client:
        return await self._sync_balance_dom(page)
    try:
        result = self._client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return float(result.get("balance", 0)) / 1e6  # USDC has 6 decimals
    except Exception as e:
        logger.warning(f"[polymarket] sync_balance API failed: {e}")
        return -1
```

### `check_live_price` — CLOB orderbook

Replace DOM button scraping with direct CLOB API call. Uses `token_id` from `provider_meta`.

```python
async def check_live_price(self, page, bet):
    if not self._client:
        return await self._check_live_price_dom(page, bet)

    token_id = getattr(bet, "token_id", None)
    fair_odds = getattr(bet, "fair_odds", None)
    if not token_id or not fair_odds:
        return None, None

    try:
        book = self._client.get_order_book(token_id)
        # Walk asks for VWAP at target fill size
        asks = book.get("asks", [])
        if not asks:
            return None, None
        best_ask = float(asks[0]["price"])
        live_odds = 1.0 / best_ask
        edge = compute_edge("polymarket", live_odds, fair_odds)
        return round(live_odds, 3), edge
    except Exception as e:
        logger.warning(f"[polymarket] check_live_price API failed: {e}")
        return None, None
```

### `navigate_to_event` — DOM (visual context)

Keep DOM navigation for visual context — user needs to see the market page. But remove outcome clicking and stake filling (SDK handles placement).

```python
async def navigate_to_event(self, page, bet):
    slug = getattr(bet, "event_slug", None) or getattr(bet, "market_slug", None)
    if not slug:
        return False
    url = f"https://polymarket.com/event/{slug}"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_selector("button", timeout=10000)
    return True
```

Simpler than before — no more outcome clicking or quick-add button clicking.

### `prep_betslip` — SDK sign order (NEW)

Phase 1: Build and sign the order locally. Show user the details. Don't submit yet.

```python
async def prep_betslip(self, page, bet, stake):
    if not self._client:
        # DOM fallback: click outcome + fill stake buttons (existing logic)
        return await self._prep_betslip_dom(page, bet, stake)

    token_id = getattr(bet, "token_id", None)
    if not token_id:
        return PlacementResult(status="failed", bet_id=0, reason="no token_id")

    try:
        # Get current best ask price
        book = self._client.get_order_book(token_id)
        asks = book.get("asks", [])
        if not asks:
            return PlacementResult(status="failed", bet_id=0, reason="no asks in orderbook")

        best_ask = float(asks[0]["price"])
        size = stake / best_ask  # shares = dollars / price_per_share

        # Build and sign order (not submitted yet)
        order_args = OrderArgs(
            price=best_ask,
            size=round(size, 2),
            side=BUY,
            token_id=token_id,
        )
        self._pending_order = self._client.create_order(order_args)
        self._pending_price = best_ask
        self._pending_size = size

        live_odds = round(1.0 / best_ask, 3)
        return PlacementResult(
            status="prepped",
            bet_id=0,
            actual_odds=live_odds,
            actual_stake=stake,
            reason=f"Order signed: {round(size, 2)} shares @ {best_ask:.4f}",
        )
    except Exception as e:
        logger.error(f"[polymarket] prep_betslip failed: {e}")
        return PlacementResult(status="failed", bet_id=0, reason=str(e))
```

### `confirm_bet` — SDK post order (NEW)

Phase 2: User clicked Place → submit the pre-signed order to CLOB.

```python
async def confirm_bet(self, page):
    if not self._client or not self._pending_order:
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    try:
        resp = self._client.post_order(self._pending_order, OrderType.GTC)
        order_id = resp.get("orderID") or resp.get("id", "")
        success = resp.get("success", False)

        if success:
            return PlacementResult(
                status="placed",
                bet_id=order_id,
                actual_odds=round(1.0 / self._pending_price, 3),
                actual_stake=round(self._pending_size * self._pending_price, 2),
            )
        else:
            error = resp.get("errorMsg", "unknown")
            return PlacementResult(status="failed", bet_id=0, reason=error)
    except Exception as e:
        return PlacementResult(status="failed", bet_id=0, reason=str(e))
    finally:
        self._pending_order = None
```

### `fetch_positions` — Data API

Replace portfolio DOM scraping with Data API call.

```python
async def fetch_positions(self, page):
    if not self._client:
        return []  # Can't fetch without auth
    try:
        # Data API: GET /data/positions?user=ADDRESS
        address = os.getenv("POLY_FUNDER_ADDRESS")
        positions = requests.get(
            f"https://data-api.polymarket.com/positions",
            params={"user": address}
        ).json()
        return [
            PositionEntry(
                provider_bet_id=p.get("asset", ""),
                event_name=p.get("title", ""),
                market=p.get("market", "1x2"),
                outcome=p.get("outcome", ""),
                odds=round(1.0 / float(p["avgPrice"]), 3) if float(p.get("avgPrice", 0)) > 0 else 2.0,
                stake=round(float(p.get("size", 0)) * float(p.get("avgPrice", 0)), 2),
                potential_payout=round(float(p.get("size", 0)), 2),
            )
            for p in positions
            if float(p.get("size", 0)) > 0
        ]
    except Exception as e:
        logger.warning(f"[polymarket] fetch_positions failed: {e}")
        return []
```

### `sync_history` — Data API + DB reconciliation

Replace DOM history scraping with Data API trades endpoint.

```python
async def sync_history(self, page):
    if not self._client:
        return await self._sync_history_dom(page)  # DOM fallback

    address = os.getenv("POLY_FUNDER_ADDRESS")
    trades = requests.get(
        f"https://data-api.polymarket.com/trades",
        params={"user": address}
    ).json()

    # Reconcile trades against DB (same logic as before but with structured data)
    # ... (reuse existing DB reconciliation logic with structured trade data)
```

### Settlement

Settlement stays largely the same conceptually (match positions against DB, settle won/lost), but uses `fetch_positions()` API data instead of DOM scraping. The `redeem_all()` and `claim_banner()` methods stay DOM-based — blockchain redemption requires on-chain interaction through the UI.

**Keep DOM-based**:
- `redeem_all()` — must click Redeem buttons (on-chain tx through UI)
- `claim_banner()` — must click Claim button (on-chain tx through UI)
- `_dismiss_modal()` — UI-only

**Replace with API**:
- `scrape_portfolio()` → `fetch_positions()` (Data API)
- `scrape_history()` → Data API trades
- `scan_portfolio_settlements()` → API positions + DB matching

## Play Loop Integration

The play loop already supports GUIDED mode (Altenar uses it). The flow becomes:

1. `navigate_to_event(page, bet)` → opens market page (visual context)
2. `prep_betslip(page, bet, stake)` → signs order via SDK, returns `status="prepped"`
3. `check_live_price(page, bet)` → CLOB orderbook API
4. Auto-skip if negative EV
5. Broadcast `bet_ready` → user sees bet details in UI
6. User clicks Place → `confirm_bet(page)` → posts signed order to CLOB
7. Response parsed → bet recorded to DB

**Key change**: No more `on_bet_intercepted` for Polymarket — the SDK response IS the confirmation. The play loop's `_place_event` triggers `confirm_bet()` directly.

To make this work, the play loop needs a small addition: when `workflow.mode == GUIDED` and `_place_event` fires, call `workflow.confirm_bet(page)` and use that result instead of the intercepted body. This path already exists for Altenar — Polymarket just needs to switch from AUTONOMOUS to GUIDED mode.

## Environment Variables

Added to `.env.local` (gitignored, local PC only):

| Variable | Required | Description |
|----------|----------|-------------|
| `POLY_PRIVATE_KEY` | Yes (for API) | Wallet private key (0x...) |
| `POLY_FUNDER_ADDRESS` | Yes (for API) | Polygon wallet address |
| `POLY_SIGNATURE_TYPE` | No (default: 1) | 0=EOA, 1=POLY_PROXY (email wallet), 2=GNOSIS_SAFE |

## Dependencies

Add to `pyproject.toml` (firevsports section or main):
```
py-clob-client>=0.18.0
```

The SDK pulls in `web3`, `eth-account`, etc. These are only needed locally (FirevSports), not on the server.

## DOM Fallback

Every API method has a `_*_dom` fallback that preserves the current DOM-based behavior. This means:
- If `POLY_PRIVATE_KEY` is not set → full DOM fallback (current behavior, no regression)
- If API call fails → individual method falls back to DOM

The DOM fallback methods are the current implementations moved into private methods.

## What Changes vs Current

| Aspect | Before (DOM) | After (API) |
|--------|-------------|-------------|
| Mode | AUTONOMOUS | GUIDED |
| Balance | Nav text scrape | `get_balance_allowance()` |
| Prices | Button ¢ text | `get_order_book()` VWAP |
| Stake filling | +$1/+$5/+$10/+$100 clicks | SDK `create_order()` with exact amount |
| Placement | User clicks Buy manually | SDK `post_order()` on user confirm |
| Positions | Portfolio DOM scrape | Data API `/positions` |
| History | History tab DOM scrape | Data API `/trades` |
| Settlement detection | DOM scrape + fuzzy match | API positions + DB match |
| Redeem/Claim | DOM clicks | DOM clicks (unchanged — on-chain) |
| Navigation | DOM (full: click outcome, fill) | DOM (minimal: just open page) |

## What Does NOT Change

- Extraction pipeline (server) — unchanged except `_build_outcome` persisting `token_id`
- Browser interceptor keywords — keep `clob.polymarket.com/order` detection as backup
- Settlement flow structure — claim → redeem → settle DB → void ghosts
- Play loop structure — just switches from AUTONOMOUS to GUIDED path
- `_dismiss_modal` — stays DOM-based (UI modal)

## Risk & Mitigation

| Risk | Mitigation |
|------|------------|
| Private key exposure | `.env.local` gitignored, local PC only, never transmitted |
| SDK breaks / API changes | DOM fallback for every method |
| Token allowance not set | Check on init, log warning with instructions |
| Rate limits (15K/10s) | We make ~1 call per bet — nowhere near limits |
| Order rejected (price moved) | Parse error response, show to user, allow retry |
| USDC balance check wrong | Compare API balance vs DOM balance on first login |
