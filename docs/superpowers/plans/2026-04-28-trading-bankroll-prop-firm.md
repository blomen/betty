# Trading Bankroll Prop Firm Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the autonomous-mode TopstepX account-info fetch and restructure the Trading bankroll page into a Prop Firm → Account hierarchy with real (not hardcoded) risk limits.

**Architecture:** New server endpoint `GET /api/stocks/account` reads `app.state.stocks_runtime.client`, calls TopstepX `/api/Account/search`, joins the active flag + per-account limits from `TopstepXConfig`, returns a nested `prop_firms[].accounts[]` shape. Local dashboard route `/stocks/api/account` becomes a thin proxy through the existing SSH tunnel. Frontend renders `<PropFirmCard>` → `<AccountCard>` components.

**Tech Stack:** FastAPI, httpx (existing TopstepXClient), React 19, TypeScript, Vite, Tailwind, pytest with `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-04-28-trading-bankroll-prop-firm-design.md`

---

## File Structure

**Server (Hetzner backend):**
- Modify: `backend/src/api/routes/stocks.py` — add `GET /account` route
- Test: `backend/tests/test_stocks_account_endpoint.py` — new pytest module

**Local proxy:**
- Modify: `backend/src/stocks/dashboard.py:372-392` — replace `/api/account-info` route with `/api/account` proxy

**Frontend:**
- Modify: `arnold/frontend/src/types/stocks.ts` — replace `Account` interface with nested `PropFirm` / `PropFirmAccount` / `AccountResponse`
- Modify: `arnold/frontend/src/hooks/useStocksApi.ts` — replace `getAccountInfo()` with `getAccount()`
- Modify: `arnold/frontend/src/pages/stocks/BankrollPage.tsx` — full rewrite with three components

**Deploy:**
- Pre-deploy gate: confirm broker is flat via `/api/stocks/runtime-status`
- `scripts/server-deploy.sh rebuild backend` (Python + frontend in same image, multi-stage)

---

## Pre-Flight: Verify Trading Position Is Flat

Per CLAUDE.md the autonomous broker held an open position during brainstorming. Do not deploy until flat.

- [ ] **Step 1: Check open position state**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'curl -s -H \"X-API-Key: \$ARNOLD_API_KEY\" http://localhost:8000/api/stocks/runtime-status'"
```

Expected output: `position.flat == true`. If `false`, defer the deploy task at the end of this plan until the position closes (or user manually flattens via the UI). The implementation/test tasks below can still be done locally — they don't deploy.

---

## Task 1: Server Endpoint — Failing Test

**Files:**
- Create: `backend/tests/test_stocks_account_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for GET /api/stocks/account endpoint."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.stocks import router
from src.stocks.config import TopstepXConfig


def _make_app(*, runtime_present: bool = True, account_search_payload=None,
              account_search_raises: Exception | None = None,
              active_account_id: int = 21795795):
    app = FastAPI()
    app.include_router(router)

    if runtime_present:
        cfg = TopstepXConfig(max_trailing_dd=5000.0, max_daily_loss=1500.0)
        client = MagicMock()
        client._config = cfg
        client._account_id = active_account_id
        if account_search_raises is not None:
            client._post = AsyncMock(side_effect=account_search_raises)
        else:
            client._post = AsyncMock(return_value=account_search_payload)

        runtime = MagicMock()
        runtime.client = client
        app.state.stocks_runtime = runtime

    return app


_LIVE_PAYLOAD = {
    "accounts": [
        {"id": 21480650, "name": "50KTC-V2-574123-24319286",
         "balance": 50000.0, "canTrade": True, "isVisible": True, "simulated": True},
        {"id": 21795795, "name": "PRAC-V2-574123-23514304",
         "balance": 163792.5, "canTrade": True, "isVisible": True, "simulated": True},
    ],
    "success": True, "errorCode": 0, "errorMessage": None,
}


def test_account_endpoint_returns_nested_prop_firm_shape():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    resp = client.get("/api/stocks/account")
    assert resp.status_code == 200
    body = resp.json()
    assert "prop_firms" in body
    assert len(body["prop_firms"]) == 1
    firm = body["prop_firms"][0]
    assert firm["id"] == "topstepx"
    assert firm["name"] == "TopstepX"
    assert len(firm["accounts"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_stocks_account_endpoint.py::test_account_endpoint_returns_nested_prop_firm_shape -v`

Expected: FAIL with `404 Not Found` — the `/account` route does not exist yet.

---

## Task 2: Server Endpoint — Minimal Implementation

**Files:**
- Modify: `backend/src/api/routes/stocks.py` — add new route

- [ ] **Step 1: Confirm `logging` is imported in the route module**

Run: `grep -n "^import\|^from" backend/src/api/routes/stocks.py | head -20`

Expected: `logging` may not be imported. If not, the implementation step adds it.

- [ ] **Step 2: Add the route at the end of the existing routes (before any helper functions)**

Insert after the `runtime_status` route (around line 128) in `backend/src/api/routes/stocks.py`:

```python
@router.get("/account")
async def get_account(request: Request):
    """Active TopstepX account + risk limits, scoped per prop firm.

    Shape is intentionally an array of prop_firms each with an array of
    accounts so adding a second prop firm or surfacing a second active
    account is purely additive.
    """
    rt = getattr(request.app.state, "stocks_runtime", None)
    if rt is None:
        return {"prop_firms": []}

    client = rt.client
    cfg = client._config

    cache = getattr(request.app.state, "_account_cache", None)
    try:
        data = await client._post(
            "/api/Account/search",
            {"onlyActiveAccounts": True},
        )
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

If `logging` is not yet imported at the top of the file, add at the module level:

```python
import logging

log = logging.getLogger(__name__)
```

(Keep imports grouped with the existing `from __future__ import annotations` block.)

- [ ] **Step 3: Run test to verify it passes**

Run: `cd backend && pytest tests/test_stocks_account_endpoint.py::test_account_endpoint_returns_nested_prop_firm_shape -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/stocks.py backend/tests/test_stocks_account_endpoint.py
git commit -m "feat(stocks): add /api/stocks/account endpoint with prop firm hierarchy"
```

---

## Task 3: Server Endpoint — Active Account Detail Tests

**Files:**
- Modify: `backend/tests/test_stocks_account_endpoint.py`

- [ ] **Step 1: Add tests for active flag, limits, product derivation, and snake_case fields**

Append to `backend/tests/test_stocks_account_endpoint.py`:

```python
def test_active_account_has_limits_and_inactive_does_not():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD, active_account_id=21795795)
    client = TestClient(app)
    body = client.get("/api/stocks/account").json()
    accounts = body["prop_firms"][0]["accounts"]
    by_id = {a["id"]: a for a in accounts}

    active = by_id[21795795]
    inactive = by_id[21480650]

    assert active["active"] is True
    assert inactive["active"] is False
    assert active["limits"] == {"max_trailing_dd": 5000.0, "max_daily_loss": 1500.0}
    assert inactive["limits"] is None


def test_account_fields_use_snake_case():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    body = client.get("/api/stocks/account").json()
    a = body["prop_firms"][0]["accounts"][0]

    assert "can_trade" in a
    assert "canTrade" not in a
    assert isinstance(a["can_trade"], bool)


def test_product_derived_from_account_name_prefix():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    body = client.get("/api/stocks/account").json()
    by_id = {a["id"]: a for a in body["prop_firms"][0]["accounts"]}

    assert by_id[21795795]["product"] == "PRAC"
    assert by_id[21480650]["product"] == "50KTC"
```

- [ ] **Step 2: Run all tests in the module**

Run: `cd backend && pytest tests/test_stocks_account_endpoint.py -v`

Expected: 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_stocks_account_endpoint.py
git commit -m "test(stocks): cover active flag, limits, snake_case, and product derivation"
```

---

## Task 4: Server Endpoint — Failure Mode Tests

**Files:**
- Modify: `backend/tests/test_stocks_account_endpoint.py`

- [ ] **Step 1: Add tests for the three failure paths from the spec**

Append to `backend/tests/test_stocks_account_endpoint.py`:

```python
def test_returns_empty_when_runtime_missing():
    app = _make_app(runtime_present=False)
    client = TestClient(app)
    resp = client.get("/api/stocks/account")
    assert resp.status_code == 200
    assert resp.json() == {"prop_firms": []}


def test_topstepx_failure_with_no_cache_returns_empty():
    app = _make_app(account_search_raises=RuntimeError("boom"))
    client = TestClient(app)
    resp = client.get("/api/stocks/account")
    assert resp.status_code == 200
    assert resp.json() == {"prop_firms": []}


def test_topstepx_failure_with_cache_returns_cached_payload():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    first = client.get("/api/stocks/account").json()
    assert first["prop_firms"][0]["accounts"]

    # Now make TopstepX fail; cache should be served
    runtime = app.state.stocks_runtime
    runtime.client._post = AsyncMock(side_effect=RuntimeError("transient"))
    second = client.get("/api/stocks/account").json()
    assert second == first
```

- [ ] **Step 2: Run the failure-path tests**

Run: `cd backend && pytest tests/test_stocks_account_endpoint.py -v`

Expected: 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_stocks_account_endpoint.py
git commit -m "test(stocks): cover runtime-missing, topstepx-failure, and cache-fallback paths"
```

---

## Task 5: Local Proxy — Replace `/api/account-info`

**Files:**
- Modify: `backend/src/stocks/dashboard.py:372-392`

- [ ] **Step 1: Replace the broken `/api/account-info` route with the new `/api/account` proxy**

In `backend/src/stocks/dashboard.py`, locate the route block (currently lines 372–392):

```python
    @router.get("/api/account-info")
    async def get_account_info():
        client = _state.get("topstepx_client")
        if not client:
            return {}
        try:
            data = await client._post(
                "/api/Account/search",
                {
                    "onlyActiveAccounts": True,
                },
            )
            accounts = data.get("accounts", []) if isinstance(data, dict) else data
            # Return the account the client is actually using
            acct = next(
                (a for a in accounts if a.get("id") == client._account_id),
                accounts[0] if accounts else {},
            )
            return acct
        except Exception:
            return {}
```

Replace with:

```python
    @router.get("/api/account")
    async def get_account():
        return await _proxy("/api/stocks/account", cache_ttl=5.0)
```

- [ ] **Step 2: Verify no other consumers reference the old route**

Run: `grep -rn "account-info\|getAccountInfo" arnold/ backend/ docs/`

Expected output should only show: docs (historical plan files — fine to leave), the line in `useStocksApi.ts` (fixed in Task 7), and the line in `BankrollPage.tsx` (fixed in Task 8). No production-code call sites elsewhere.

If any unexpected reference appears in production code (anything under `arnold/frontend/src/` or `backend/src/`), stop and reconcile before proceeding.

- [ ] **Step 3: Commit**

```bash
git add backend/src/stocks/dashboard.py
git commit -m "refactor(stocks): replace /account-info with /account proxy to server"
```

---

## Task 6: Frontend Types — Replace Account Interface

**Files:**
- Modify: `arnold/frontend/src/types/stocks.ts:229-235`

- [ ] **Step 1: Check whether `getState()` references the old `Account` type**

Run: `grep -n "Account" arnold/frontend/src/hooks/useStocksApi.ts arnold/frontend/src/pages/stocks/`

Expected: confirms `useStocksApi.ts:43` (`account: import('@/types/stocks').Account`) and `BankrollPage.tsx` use it. The `getState()` reference stays — keep `Account` defined as a backwards-compat alias for now (deleting it requires touching `getState()` consumers, which is out of scope here).

- [ ] **Step 2: Add the new types alongside the existing `Account` interface**

In `arnold/frontend/src/types/stocks.ts`, after the existing `Account` interface (around line 235), add:

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

- [ ] **Step 3: Type-check**

Run: `cd arnold/frontend && npx tsc --noEmit`

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add arnold/frontend/src/types/stocks.ts
git commit -m "feat(types): add PropFirm, PropFirmAccount, AccountResponse types"
```

---

## Task 7: Frontend API Client — Add `getAccount`

**Files:**
- Modify: `arnold/frontend/src/hooks/useStocksApi.ts:53-55`

- [ ] **Step 1: Replace `getAccountInfo` with `getAccount`**

In `arnold/frontend/src/hooks/useStocksApi.ts`, replace the block at lines 53–55:

```typescript
  getAccountInfo() {
    return fetchJson<import('@/types/stocks').Account>('/account-info')
  },
```

With:

```typescript
  getAccount() {
    return fetchJson<import('@/types/stocks').AccountResponse>('/account')
  },
```

- [ ] **Step 2: Type-check**

Run: `cd arnold/frontend && npx tsc --noEmit`

Expected: 1 error pointing at `BankrollPage.tsx` (still calls `getAccountInfo`). That is fixed in the next task — do not "fix" it here by re-adding `getAccountInfo`.

- [ ] **Step 3: Commit**

```bash
git add arnold/frontend/src/hooks/useStocksApi.ts
git commit -m "feat(api): replace getAccountInfo with getAccount returning AccountResponse"
```

---

## Task 8: Frontend Page — Rewrite BankrollPage

**Files:**
- Modify: `arnold/frontend/src/pages/stocks/BankrollPage.tsx` (full rewrite)

- [ ] **Step 1: Replace the entire file contents**

Overwrite `arnold/frontend/src/pages/stocks/BankrollPage.tsx` with:

```typescript
import { useState, useEffect } from 'react'
import { api } from '@/hooks/useStocksApi'
import type { AccountResponse, PropFirm, PropFirmAccount } from '@/types/stocks'

const PRODUCT_LABELS: Record<string, string> = {
  PRAC: 'Practice',
  '50KTC': '50K Combine',
}

function formatCurrency(value: number | null | undefined, opts?: { decimals?: number }): string {
  if (value == null || Number.isNaN(value)) return '—'
  const decimals = opts?.decimals ?? 0
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

export function BankrollPage() {
  const [data, setData] = useState<AccountResponse | null>(null)

  useEffect(() => {
    const poll = () => {
      api.getAccount().then(setData).catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 10_000)
    return () => clearInterval(iv)
  }, [])

  const propFirms = data?.prop_firms ?? []
  const activeAccount: PropFirmAccount | null =
    propFirms.flatMap(f => f.accounts).find(a => a.active) ?? null

  return (
    <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabTradingBankroll" />
        Bankroll
      </h2>

      <div className="border-l-2 border-tabTradingBankroll">
        <div className="border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider mb-1">Total Capital</div>
          <div className="text-text text-3xl font-semibold">
            {formatCurrency(activeAccount?.balance, { decimals: 2 })}
          </div>
        </div>
      </div>

      {propFirms.length === 0 ? (
        <EmptyPropFirm />
      ) : (
        propFirms.map(firm => <PropFirmCard key={firm.id} propFirm={firm} />)
      )}
    </div>
  )
}

function EmptyPropFirm() {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-xs font-mono text-zinc-400 uppercase tracking-wider mb-2">Prop Firm</div>
      <div className="text-sm text-zinc-500">
        No prop firm connected. Set <code className="text-zinc-300">STOCKS_AUTONOMOUS=true</code> and configure TopstepX credentials.
      </div>
    </div>
  )
}

function PropFirmCard({ propFirm }: { propFirm: PropFirm }) {
  const accounts = propFirm.accounts.filter(a => a.active)
  return (
    <div className="border-l-2 border-tabTradingBankroll">
      <div className="border border-zinc-800 bg-zinc-900 p-3 space-y-3">
        <div>
          <div className="text-xs font-mono text-zinc-400 uppercase tracking-wider">Prop Firm</div>
          <div className="text-text text-base font-semibold mt-0.5">{propFirm.name}</div>
        </div>
        <div className="space-y-3 pl-3 border-l border-zinc-800">
          {accounts.map(acct => <AccountCard key={acct.id} account={acct} />)}
        </div>
      </div>
    </div>
  )
}

function AccountCard({ account }: { account: PropFirmAccount }) {
  const productLabel = PRODUCT_LABELS[account.product] ?? account.product
  const status = account.can_trade ? 'Active' : 'Disabled'
  const statusColor = account.can_trade ? '#4ade80' : '#ef4444'

  return (
    <div className="space-y-2">
      <div>
        <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">Account</div>
        <div className="text-text font-mono text-sm mt-0.5">{account.name}</div>
        <div className="text-[10px] font-mono text-zinc-500 mt-0.5">
          {productLabel}
          {account.simulated && <span className="ml-2 px-1 py-0.5 border border-zinc-700 text-zinc-400">Simulated</span>}
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <StatCard label="Balance" value={formatCurrency(account.balance, { decimals: 2 })} color="#8b5cf6" />
        <StatCard label="Max Trail DD" value={formatCurrency(account.limits?.max_trailing_dd)} color="#ec4899" />
        <StatCard label="Daily Loss" value={formatCurrency(account.limits?.max_daily_loss)} color="#f97316" />
        <StatCard label="Status" value={status} color={statusColor} />
      </div>
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="border border-zinc-800 bg-zinc-950 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check the frontend**

Run: `cd arnold/frontend && npx tsc --noEmit`

Expected: 0 errors.

- [ ] **Step 3: Lint the file**

Run: `cd arnold/frontend && npx eslint src/pages/stocks/BankrollPage.tsx`

Expected: 0 errors. (PostToolUse hook should also auto-fix on save per CLAUDE.md.)

- [ ] **Step 4: Commit**

```bash
git add arnold/frontend/src/pages/stocks/BankrollPage.tsx
git commit -m "feat(stocks): rewrite Bankroll Trading page with PropFirm > Account hierarchy"
```

---

## Task 9: Local Bundle Build Verification

**Files:**
- None (verification only)

- [ ] **Step 1: Build the frontend bundle**

Run: `cd arnold/frontend && npm run build`

Expected: build succeeds, no TypeScript errors. The deployed image rebuilds the frontend in Stage 1 of the Dockerfile, but local build catches errors before pushing.

- [ ] **Step 2: Run the new server tests once more**

Run: `cd backend && pytest tests/test_stocks_account_endpoint.py -v`

Expected: 7 tests PASS.

- [ ] **Step 3: Run the broader stocks test suite for regressions**

Run: `cd backend && pytest tests/ -k "stocks" -v`

Expected: all stocks-related tests still pass (the new tests plus the existing `test_stocks_integration.py`).

---

## Task 10: Push & Deploy

**Files:**
- None (deploy operation)

- [ ] **Step 1: Re-verify position is flat before deploying**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'curl -s -H \"X-API-Key: \$ARNOLD_API_KEY\" http://localhost:8000/api/stocks/runtime-status'"
```

Expected: `position.flat == true`. If not flat, wait for the trade to close or coordinate with the user before continuing. Do NOT use `--force` to bypass.

- [ ] **Step 2: Push commits to main**

```bash
git push origin main
```

- [ ] **Step 3: Deploy via the lock-protected script**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

Expected: deploy script acquires `flock`, rebuilds backend image (~30s for code-only change since pyproject didn't change), waits for `/health` to return 200 within 2 min.

- [ ] **Step 4: Smoke-test the new server endpoint**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'curl -s -H \"X-API-Key: \$ARNOLD_API_KEY\" http://localhost:8000/api/stocks/account'"
```

Expected: JSON with `prop_firms[0].accounts[]` containing at least one account, the active one having `active: true` and `limits` populated.

- [ ] **Step 5: Local launcher reload + UI check**

Restart `arnold.bat` (or refresh the browser if it's already running) and click Bankroll → Trading. Verify:

- "Total Capital" shows the active account balance.
- "Prop Firm" header reads "TopstepX".
- "Account" sub-section shows the full account name (e.g. `PRAC-V2-574123-23514304`), with "Practice" + "Simulated" subtitle.
- Stats grid shows real Balance, Max Trail DD ($5,000), Daily Loss ($1,500), Status (Active green).
- No "—" placeholders anywhere on the page.

- [ ] **Step 6: Confirm the autonomous broker still trades**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend tail -20 /app/logs/trading_service.log"
```

Expected: no new errors, `stocks_runtime` still reports `running: true` via `/api/stocks/runtime-status`.

---

## Done

The page now renders real TopstepX data with a Prop Firm → Account hierarchy that scales additively to multiple accounts and prop firms.
