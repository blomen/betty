# Pinnacle Bet-History Discovery

**Date:** 2026-04-30
**Purpose:** Discover the URL pattern, request shape, and response shape of the bet-history XHR on pinnacle.se so `PinnacleMirrorWorkflow.sync_history` can intercept it. Until this doc is complete, `sync_history` falls back to DOM scrape (best-effort, low-volume only).

## Method

Manual capture using Chrome DevTools while logged into pinnacle.se. Steps:

1. Open https://www.pinnacle.se/en/account/bet-history/
2. Open DevTools → Network tab
3. Set the date filter to "Last 30 days" (or whatever surfaces a populated history)
4. Watch which XHRs fire; right-click → "Copy as fetch" the one(s) that contain bet rows
5. Note: URL, query params, request body, response body shape

## What to record

| Field | Notes |
|---|---|
| Endpoint URL pattern | e.g. `https://www.pinnacle.se/api/0.1/wagers/v3/...` |
| HTTP method | GET / POST |
| Auth | Bearer? Cookie? Custom header? |
| Pagination | offset/limit? cursor? page? |
| Response root path | `data.bets[]`? `result.wagers[]`? |
| Per-bet fields | provider_bet_id key, status field+values, odds key, stake key, payout key, event name path, market path, outcome path |
| Status values | What strings/codes map to won / lost / void / cashout |
| Odds format | Decimal? American? (Pinnacle uses American on slip — does history match?) |

## Acceptance

This doc is "complete" once `_BET_HISTORY_KEYWORDS` in `arnold/mirror/browser.py` has a Pinnacle-specific token and `_parse_pinnacle_history_entry` in `arnold/mirror/workflows/pinnacle.py` can map a real captured response into a `HistoryEntry`. Until then, `sync_history` returns the DOM-scrape best-effort and reconciliation runs in degraded mode.
