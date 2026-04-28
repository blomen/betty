# Trading Bankroll → Prop Firm section

**Status:** Design approved, ready for implementation plan
**Date:** 2026-04-28
**Scope:** Bug fix + UI restructure of the Stocks → Bankroll → Trading sub-tab

## Problem

The Trading bankroll page renders blank values for Buying Power, account ID, and falls back to `"Active"`/`$4,500` (hardcoded) for status and Max DD.

Two underlying issues:

1. **Autonomous-mode bug.** With `STOCKS_AUTONOMOUS=true`, the server owns the TopstepX session. The local Arnold app's `stocks_runtime.py` no-ops, so `_state["topstepx_client"]` in `backend/src/stocks/dashboard.py` is never populated. The `/stocks/api/account-info` route checks `_state.get("topstepx_client")` and returns `{}` — frontend renders "—".
2. **Wrong field expectations.** The frontend expects `buyingPower` from TopstepX, but the live `/api/Account/search` payload doesn't include that field. Even if the client wiring worked, "BUYING POWER" would always be blank.

The hardcoded `MAX_LOSS = 4500` constant in [`BankrollPage.tsx:5`](../../arnold/frontend/src/pages/stocks/BankrollPage.tsx#L5) is also wrong — the real limit (`5000.0` for the active account) lives on the server as `TOPSTEPX_MAX_TRAILING_DD`.

## Goals

- Fix the autonomous-mode data fetch.
- Replace hardcoded prop-firm risk values with real config-driven ones.
- Establish a UI hierarchy (Prop Firm → Account → details) that trivially scales to multiple accounts and multiple prop firms without redesign.
- Surface only the **active** account today (per scope agreed during brainstorming — A3).

## Non-Goals

- Live risk state (session PnL, current trailing DD usage, halt reason). That is scope B/C from brainstorming and out of scope here.
- Account switching from the UI. The traded account is controlled server-side via `TOPSTEPX_ACCOUNT_ID`.
- Multi-prop-firm onboarding (Apex, MyFundedFutures, etc.). The data shape supports it; the integration work is separate.
- WebSocket push of balance updates. 10s polling is sufficient at this granularity.

## Ground Truth — TopstepX Account Payload

`POST /api/Account/search` with `{"onlyActiveAccounts": true}` returns:

```json
{
  "accounts": [
    {
      "id": 21480650,
      "name": "50KTC-V2-574123-24319286",
      "balance": 50000.0,
      "canTrade": true,
      "isVisible": true,
      "simulated": true
    },
    {
      "id": 21795795,
      "name": "PRAC-V2-574123-23514304",
      "balance": 163792.5,
      "canTrade": true,
      "isVisible": true,
      "simulated": true
    }
  ],
  "success": true,
  "errorCode": 0,
  "errorMessage": null
}
```

**Fields TopstepX provides:** `id`, `name`, `balance`, `canTrade`, `isVisible`, `simulated`.

**Fields TopstepX does NOT provide:** `buyingPower`, `dailyLossLimit`, `maxDrawdown`, `equity`. Risk limits are enforced server-side via env vars (`TOPSTEPX_MAX_TRAILING_DD`, `TOPSTEPX_MAX_DAILY_LOSS`) and surfaced through the new endpoint.

The `name` prefix encodes the product: `PRAC` (Practice), `50KTC` (50K Trading Combine), etc.

## Architecture

```
Server (Hetzner, autonomous broker owns TopstepX session)
└── GET /api/stocks/account
        ├── reads app.state.stocks_runtime.client
        ├── calls TopstepX /api/Account/search
        ├── joins active flag from client._account_id
        └── injects limits from TopstepXConfig (env)
              ↓ HTTP via SSH tunnel
Local Arnold (port 8000)
└── GET /stocks/api/account
        └── thin proxy via dashboard._proxy() (5s local cache,
            stale-cache fallback, persistent httpx client)
              ↓ fetch
Frontend (React)
└── BankrollPage (Trading sub-tab)
        ├── 10s poll → /stocks/api/account
        ├── headline "Total Capital" ← active account.balance
        └── PropFirmCard (per prop_firms[])
              └── AccountCard (per accounts[].active)
                    └── StatCard grid: Balance, Max Trail DD, Daily Loss, Status
```

## Data Contract

### `GET /api/stocks/account` (server)

Response:

```json
{
  "prop_firms": [
    {
      "id": "topstepx",
      "name": "TopstepX",
      "accounts": [
        {
          "id": 21795795,
          "name": "PRAC-V2-574123-23514304",
          "product": "PRAC",
          "balance": 163792.5,
          "can_trade": true,
          "simulated": true,
          "active": true,
          "limits": {
            "max_trailing_dd": 5000.0,
            "max_daily_loss": 1500.0
          }
        },
        {
          "id": 21480650,
          "name": "50KTC-V2-574123-24319286",
          "product": "50KTC",
          "balance": 50000.0,
          "can_trade": true,
          "simulated": true,
          "active": false,
          "limits": null
        }
      ]
    }
  ]
}
```

Shape decisions:

- **`prop_firms[]`** — array, not single object. Adding Apex/MFFU later requires no contract change.
- **`accounts[]`** per prop firm — full list from TopstepX, not just the active one. Server returns everything; UI decides what to render.
- **`active: true`** on exactly one account per prop firm — derived from `app.state.stocks_runtime.client._account_id`.
- **`limits`** is per-account (only populated for the active account today). Per-account because DD limits could legitimately differ across accounts within the same firm.
- **`product`** derived server-side from `name.split("-", 1)[0].upper()`. Cosmetic, lets the UI show readable labels without parsing.
- **Field naming** — snake_case throughout (`can_trade`, not `canTrade`) for consistency with the rest of the server API.

Failure modes (all return HTTP 200 — polling page must not flap):

- `stocks_runtime` is None (autonomous off / bootstrap failed) → `{"prop_firms": []}`.
- TopstepX REST call raises → return last-good cached payload from `app.state._account_cache` if available, else `{"prop_firms": []}`.

### `GET /stocks/api/account` (local proxy)

Thin pass-through using existing `dashboard._proxy("/api/stocks/account", cache_ttl=5.0)`. Reuses persistent httpx client through SSH tunnel and stale-cache fallback on tunnel error.

The old `/stocks/api/account-info` route is **renamed** to `/stocks/api/account` since it no longer returns a single account. No external consumers exist.

## Server Implementation

File: `backend/src/api/routes/stocks.py`. Adds a new route `@router.get("/account")` (full prefix becomes `/api/stocks/account` via the existing `router = APIRouter(prefix="/api/stocks")`).

```python
@router.get("/account")
async def get_account(request: Request):
    rt = getattr(request.app.state, "stocks_runtime", None)
    if rt is None:
        return {"prop_firms": []}

    client = rt.client
    cfg = client._config

    cache = getattr(request.app.state, "_account_cache", None)
    try:
        data = await client._post("/api/Account/search", {"onlyActiveAccounts": True})
        accounts = data.get("accounts", []) if isinstance(data, dict) else []
    except Exception:
        log.exception("TopstepX /Account/search failed")
        if cache is not None:
            return cache
        return {"prop_firms": []}

    active_id = client._account_id
    out_accounts = []
    for a in accounts:
        is_active = a.get("id") == active_id
        out_accounts.append({
            "id": a.get("id"),
            "name": a.get("name", ""),
            "product": (a.get("name", "").split("-", 1)[0] or "").upper(),
            "balance": a.get("balance"),
            "can_trade": a.get("canTrade", False),
            "simulated": a.get("simulated", False),
            "active": is_active,
            "limits": {
                "max_trailing_dd": cfg.max_trailing_dd,
                "max_daily_loss": cfg.max_daily_loss,
            } if is_active else None,
        })

    payload = {
        "prop_firms": [
            {"id": "topstepx", "name": "TopstepX", "accounts": out_accounts}
        ]
    }
    request.app.state._account_cache = payload
    return payload
```

Notes:

- Cache lives on `app.state._account_cache` (process-local). No TTL — overwritten on each successful fetch. Acceptable because balance changes only on fills (rare relative to a 10s poll).
- `cfg.max_trailing_dd` and `cfg.max_daily_loss` already exist on `TopstepXConfig` (`backend/src/stocks/config.py`). No new config plumbing.
- `client._post` already handles auth/refresh — token lifecycle untouched.
- `log` import: add `import logging; log = logging.getLogger(__name__)` at module level if not already present.

## Local Proxy Implementation

File: `backend/src/stocks/dashboard.py`. Replace the existing `/api/account-info` route (lines 372–392) with:

```python
@router.get("/api/account")
async def get_account():
    return await _proxy("/api/stocks/account", cache_ttl=5.0)
```

Drop the old route entirely (no consumers besides the frontend, which we are updating in lockstep).

## Frontend Implementation

### Types — `arnold/frontend/src/types/stocks.ts`

Replace the existing `Account` interface with:

```typescript
export interface AccountLimits {
  max_trailing_dd: number
  max_daily_loss: number
}

export interface PropFirmAccount {
  id: number
  name: string
  product: string
  balance: number | null
  can_trade: boolean
  simulated: boolean
  active: boolean
  limits: AccountLimits | null
}

export interface PropFirm {
  id: string
  name: string
  accounts: PropFirmAccount[]
}

export interface AccountResponse {
  prop_firms: PropFirm[]
}
```

If `getState()` in `useStocksApi.ts` is the only other consumer of the old `Account` type, delete the old interface. Otherwise keep it for now and migrate `getState()` separately.

### API client — `arnold/frontend/src/hooks/useStocksApi.ts`

Replace `getAccountInfo()`:

```typescript
getAccount() {
  return fetchJson<import('@/types/stocks').AccountResponse>('/account')
},
```

### Page — `arnold/frontend/src/pages/stocks/BankrollPage.tsx`

Full rewrite. Three components in the same file:

- **`BankrollPage`** (default export) — owns 10s polling, holds `AccountResponse | null`. Computes the active account for the headline "Total Capital" card. Renders one `<PropFirmCard>` per `prop_firms[]` entry.
- **`PropFirmCard`** — header (`PROP FIRM` label + firm name). Maps `accounts.filter(a => a.active)` to `<AccountCard>`s. Inactive accounts hidden today.
- **`AccountCard`** — header (`ACCOUNT` label + full `account.name` in mono). Subtitle line: product label (`PRAC` → "Practice", `50KTC` → "50K Combine", others → raw `product`) + "Simulated" chip if `simulated`. Then the 4-cell stats grid using existing `StatCard`.

Stats grid mapping (active account):

| Cell | Source | Format |
|------|--------|--------|
| Balance | `balance` | `$163,792.50` |
| Max Trail DD | `limits.max_trailing_dd` | `$5,000` |
| Daily Loss | `limits.max_daily_loss` | `$1,500` |
| Status | `can_trade` | green "Active" / red "Disabled" |

Empty state (when `prop_firms.length === 0`):

> No prop firm connected. Set `STOCKS_AUTONOMOUS=true` and configure TopstepX credentials.

Styled gray, no border accent.

### Visual hierarchy (matches user-requested nesting)

```
TRADING (sub-tab of Bankroll)
├── Total Capital                          ← headline card (existing)
│   $163,792.50
└── Prop Firm                              ← new section
    └── TopstepX                           ← prop_firm.name
        └── Account
            └── PRAC-V2-574123-23514304    ← account.name
                Practice • Simulated       ← product label + flags
                ┌──────────┬──────────────┬──────────────┬──────────┐
                │ BALANCE  │ MAX TRAIL DD │ DAILY LOSS   │ STATUS   │
                │ $163,792 │ $5,000       │ $1,500       │ Active   │
                └──────────┴──────────────┴──────────────┴──────────┘
```

### Removals

- `MAX_LOSS = 4500` constant — real value comes from server.
- `account?.buyingPower` references — field does not exist in TopstepX.
- The "Buying Power" StatCard.
- The `Account` interface if `getState()` does not need it.

## Testing

- **Server unit test** — mock `app.state.stocks_runtime.client._post` to return the captured payload above. Assert response shape matches the contract, the active account is correctly flagged, inactive account `limits` is null, snake_case field names.
- **Server failure-path test** — `stocks_runtime` is None → empty `prop_firms`. `_post` raises with no cache → empty `prop_firms`. `_post` raises with cache present → cached payload returned.
- **Frontend manual verification** — load page, confirm card hierarchy renders with real values from server, confirm 10s refresh updates balance after a (simulated) fill, confirm empty state when server returns `{"prop_firms": []}`.

No new e2e tests required — this is a single page with stable contracts on both sides.

## Deploy Considerations

The server change is a new endpoint (additive, no migration). The dashboard.py rename and frontend changes ship together — no period where the frontend hits a missing route.

**Coordination**: per CLAUDE.md, check for an open position before deploying (`curl /api/stocks/runtime-status`). At brainstorming time the active account had a long position open — defer deploy until flat.

Frontend rebuild required (Docker `--build` per memory `feedback_rebuild_frontend.md`). Backend rebuild required (Python code change).

## Future Extensibility

The `prop_firms[]` and `accounts[]` arrays are designed so the following changes are additive only (no contract or UI structure change):

- **Second TopstepX account active** — drop the `.filter(a => a.active)` in `PropFirmCard`; render all accounts.
- **Second prop firm (e.g. Apex)** — server appends another entry to `prop_firms[]`; UI maps over them automatically.
- **Per-account custom limits** — server fills `limits` on inactive accounts too (currently null).
- **Account-level metadata (e.g. "evaluation phase", "funded")** — add fields to `PropFirmAccount`; UI can choose to render or ignore.
