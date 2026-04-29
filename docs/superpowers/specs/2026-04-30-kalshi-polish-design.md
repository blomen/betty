# Kalshi Mirror — Polish to Polymarket Parity

**Status:** Approved (brainstorm)
**Date:** 2026-04-30
**Scope:** Verify and polish Kalshi end-to-end through the standard mirror workflow (sync → settle → navigate → autofill → record DB), matching the practical behavior Polymarket now has. Architecture unchanged.

## Background

`KalshiWorkflow` (`arnold/mirror/workflows/kalshi.py`, mirrored to `backend/src/mirror/workflows/kalshi.py`) was added 2026-04-18 and ran in stub mode while the user completed KYC + funded the account. Account is now funded, RSA keys are live in `.env.docker` (`KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PEM`), and the SDK (`kalshi-python` v2.1.4) is available. The workflow has not yet been exercised end-to-end on a real placement.

Polymarket has been polished over the last two weeks (cents-regex cap, JS-direct click, slug-redirect detection, sync_history merge of Loss/Redeemed/Bought, claim_banner + redeem_all settlement). Kalshi should reach the same "works in practice" bar without changing its architecture.

## Architecture (unchanged)

- Dedicated `KalshiWorkflow` class. No move to `arnold/mirror/workflows/strategies/`.
- `autonomous_placement = True` — placement is via `PortfolioApi.create_order`, not DOM intercept.
- Playwright tab opens `kalshi.com/markets/<ticker>` for visual context only; no DOM automation.
- SDK initialization, key materialization to `<data_dir>/kalshi_key.pem`, and stub fallback all stay.
- Routing through `_PLATFORM_MAP["kalshi"] = KalshiWorkflow` in `arnold/mirror/workflows/__init__.py` is unchanged.

## Gaps Closed in This Pass

| Step | Polymarket today | Kalshi today | Change |
|---|---|---|---|
| sync_balance | DOM scrape | `PortfolioApi.get_balance` | + cache last-known on transient failure |
| sync_history → settle | Loss/Redeemed/Bought DOM mapping | All fills marked `pending` | **Merge with `get_positions` `result` field** |
| check_live_price | `(odds, edge)` | `(odds, None)` | **Compute edge against `bet.fair_odds`** |
| prep_betslip stake math | `stake` literal | `count = stake // yes_price` (truncates) | `count = max(1, round(stake/yes_price))` |
| place_bet lifecycle | n/a (intercept) | fire-and-forget create_order, 60s server expiry | **Poll `get_order` ≤5 s, cancel if still resting, return `placed`/`failed`** |
| DB record | runner intercept path | runner autonomous path | **Verify** in live-fire test (no code change planned) |

## Component Detail

### 1. Settlement (the main fix)

`sync_history()` becomes a two-source merge:

1. **`PortfolioApi.get_positions()`** — authoritative for closed markets. Each `Position` carries `market_ticker`, `total_count`, `total_traded`, and (when settled) a `result` field (`yes` / `no` / `void`).
   - All Kalshi orders we place are `side="yes"` (per `create_order` in current code), so we always hold the YES contract. Mapping: `result="yes"` → `won`, `result="no"` → `lost`, `result="void"` → `void` (treat as refund/`pending`-cleared downstream).
   - Payout: `total_count * 1.0` if `won`, `0.0` if `lost`, stake-equivalent if `void`.
2. **`PortfolioApi.get_fills(limit=200)`** — supplies open positions that have no settled `result` yet, plus stake/odds/timestamp data.
   - Existing fill→HistoryEntry mapping stays (ticker, side, count, price_cents → odds, stake).
   - Status: `pending` if no settled position for that ticker.

**Merge logic:** index positions by `market_ticker`. For each fill, look up the position; if `result` is set, override the fill's `pending` with the resolved status. Otherwise leave as `pending`.

The pending-loop's existing 3-tier fuzzy match (provider_bet_id → ticker+outcome → event+market) handles the join to our DB rows. No downstream changes needed.

### 2. Live edge in `check_live_price`

```python
resp = self._markets.get_market(self._pending_ticker)
mkt = getattr(resp, "market", None)
yes_ask_dollars = getattr(mkt, "yes_ask_dollars", None)
yes_ask_cents = (
    float(yes_ask_dollars) * 100
    if yes_ask_dollars is not None
    else float(getattr(mkt, "yes_ask", 0) or 0)
)
if yes_ask_cents <= 0:
    return None, None
live_odds = round(100.0 / yes_ask_cents, 4)
fair_odds = bet.fair_odds if not isinstance(bet, dict) else bet.get("fair_odds")
live_edge = round((live_odds / float(fair_odds) - 1) * 100, 2) if fair_odds else None
return live_odds, live_edge
```

Field-name guard: SDK has shipped both `yes_ask` (cents int) and `yes_ask_dollars` (float 0–1). Read both, prefer dollars, fall back to cents.

### 3. prep_betslip stake math

Today: `count = max(1, int(stake // max(yes_price, 0.01)))` — truncates and reports the truncated cash as `actual_stake`.

New: `count = max(1, round(stake / max(yes_price, 0.01)))`. `actual_stake` becomes `count * yes_price`. `actual_odds` unchanged.

This brings the recorded stake in line with what's actually placed (DB row matches reality), avoiding the systematic under-counting today.

### 4. Order lifecycle in `place_bet`

```
1. create_order(type=limit, expiration_ts=now+60) → order_id
2. for up to 5 polls @ 1s:
     resp = get_order(order_id)
     state = resp.status  # SDK exact field/values verified during implementation
     if state == terminal-filled:
         return PlacementResult(status="placed", actual_*=fill values)
     if state == terminal-canceled-or-failed:
         return PlacementResult(status="failed", reason=resp.reason or state)
3. After 5 polls still resting:
     cancel_order(order_id)
     return PlacementResult(status="failed", reason="unfilled_within_5s")
```

State names (`resting`, `executed`, `canceled`, etc.) are the documented SDK values; exact field names and value casing get verified against the live SDK during implementation since prior memory shows divergence between docs and shipped models. Implementation pins to whatever the SDK actually returns.

`expiration_ts=now+60` stays as the server-side safety net in case the cancel call drops.

If `get_order` itself errors twice in a row, fall back to "trust the create response" — return `placed` if create returned 2xx — so a flaky polling endpoint doesn't double-cancel a real fill. Log warn.

### 5. DB record (verify only)

The autonomous_placement path in `provider_runner` already calls `POST /api/bets` after a `PlacementResult(status="placed")`. No code change planned. The live-fire test confirms the DB row appears with correct fields; if it doesn't, fix surfaces and we add a follow-up.

## Error Handling

| Source | Behavior |
|---|---|
| SDK not installed / no creds | `has_api=False` stub. All calls return safe defaults. Unchanged. |
| `get_balance` 4xx/5xx | Return last-known cached balance + warn. Add `(value, ts)` cache on workflow. |
| `get_market` failure | Return `(None, None)`. Runner skips bet. Unchanged. |
| `create_order` exception | `PlacementResult(status="failed", reason=str(e))`. Unchanged. |
| `get_order` polling — 2 errors in a row | Trust create response, return `placed`. Log warn. |
| `cancel_order` failure on resting timeout | Log error, still return `failed` reason `"unfilled_cancel_failed"`. Server-side 60s expiry = safety net. |
| Position has no `result` (market open) | Entry stays `pending`. |
| 429 rate-limit | Existing 1.5s inter-request delay + backoff (per provider memory) untouched. |

## Live-Fire Test Plan

One real $1–2 placement on the funded account against a high-liquidity market.

**Pre-flight:**
- `arnold.bat` running; Kalshi visible in provider list with green login indicator.
- `sync_balance` matches kalshi.com header.
- Cents/odds in UI match independent `MarketsApi.get_market` curl + key probe.
- `sync_history` returns cleanly (empty or known prior bets).

**Placement run:**
1. Click Place on a Kalshi value bet from the Sports tab.
2. Watch logs through `prep_betslip` (count/yes_price/actual_stake), `check_live_price` (edge), `place_bet` (create + poll + terminal state).
3. Postgres MCP: confirm row in `bets` with correct ticker, outcome, stake, odds, status=`pending`.
4. Refresh kalshi.com — confirm position visible.

**Settlement run** (after market close):
5. Trigger `sync_history` (next mirror cycle or manual).
6. Confirm bet flips to `won` / `lost` matching reality.
7. Confirm pending-loop reconciles bankroll.

**Failure-path test:**
8. Submit a deliberately off-market limit (e.g. 10¢ on a 50¢ contract).
9. Confirm 5s resting → cancel → `failed` / `unfilled_within_5s` in UI; no DB write.

**Acceptance criteria:**
- All runs above produce expected outcomes.
- No exceptions in backend logs.
- No orphaned orders on kalshi.com after a manual sweep.

## Out of Scope (deferred)

- Migration to the `strategies/` pattern (intel JSON + functional Strategy).
- Periodic positions-poller for sub-cycle settlement latency.
- Order-state surfacing in UI (resting / partial / canceled chips).
- Three-tier fuzzy settlement reconciliation analogous to Polymarket — stays at exact ticker+outcome match for now.
- Deposit/withdraw flow.
- Daily-cap enforcement specific to Kalshi.

## Files Touched

- `arnold/mirror/workflows/kalshi.py` — settle merge, edge calc, stake math, order lifecycle
- `backend/src/mirror/workflows/kalshi.py` — same changes (kept in sync)

No new files. No registry changes. No frontend changes.
