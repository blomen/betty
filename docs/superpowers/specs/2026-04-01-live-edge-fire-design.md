# Live Edge Fire Dashboard

**Date:** 2026-04-01  
**Status:** Draft  
**Scope:** Replace snapshot scan-then-fire with a live edge dashboard that continuously compares mirror-observed odds against Pinnacle fair odds and auto-fires bets with positive edge.

---

## Problem

1. **Delta is confusing**: Current delta = `(live_price - expected_price) / expected_price`, calculated from the price perspective. Positive delta = worse odds. Users expect positive = good.
2. **Snapshot, not live**: Scan is a one-shot request. Odds move between scan and fire.
3. **No sharp comparison**: Delta compares live vs batch expected price — not vs Pinnacle fair odds. It shows drift, not value.
4. **Polymarket only**: Soft books and Pinnacle have no scan/fire flow at all.
5. **Manual fire**: User must review scan results and click "Confirm & Place" even when edge is clearly positive.

## Solution

### A. Replace delta with edge vs sharp

For every bet in the execution panel, continuously compute:

```
effective_odds = polymarket_effective_odds(live_odds)  # for polymarket
effective_odds = live_odds                              # for soft/pinnacle

edge_pct = (effective_odds / fair_odds - 1) × 100
```

Where:
- `live_odds` = odds observed from the mirror page (Polymarket trading buttons) or batch odds (soft books — they don't change between extraction and placement)
- `fair_odds` = Pinnacle devigged odds from DB (latest extraction)
- `polymarket_effective_odds()` applies the 2% fee on net profit (already exists in `value.py`)
- Spread cost is already reflected in Polymarket odds (VWAP from asks at extraction time)

**Positive edge = value exists. Negative = value gone.** No ambiguity.

### B. Continuous polling for Polymarket

When the Polymarket section is expanded in the execution panel:

1. Frontend polls `GET /mirror/live-edge?provider=polymarket` every 10 seconds
2. Backend navigates to each Polymarket market page, reads trading button prices
3. Fetches current Pinnacle fair odds for each event from DB
4. Returns per-bet: `{ live_odds, fair_odds, edge_pct, status }`

For soft books: no polling needed. The batch odds ARE the live odds (placed manually). Edge is pre-computed from the batch and doesn't change. Fair odds column still shown for reference.

### C. Auto-fire on positive edge

When live edge data arrives for Polymarket bets:

1. Any bet with `edge_pct > 0` after fees → automatically queue for placement
2. Backend places the bet using existing `_place_single_polymarket_bet()` flow
3. Existing slippage guard (2% default) catches any CLOB book depth surprises at fill time
4. If slippage causes `edge_pct` to go negative at fill time → skip the bet, report "slippage"
5. Frontend updates bet row: placed bets get checkmark, skipped bets show reason

**No manual scan → review → confirm flow.** The dashboard IS the scan. Fire is automatic when edge > 0.

### D. Redesigned ExecutionPanel bet table

All providers get the same table structure:

| Column | Source | Notes |
|--------|--------|-------|
| Event · Outcome | Batch | Event name + outcome label |
| Market | Batch | ML, 1x2, spread, total |
| Live Odds | Mirror (poly) / Batch (soft) | What you'd get placed at |
| Fair Odds | Pinnacle DB | Devigged sharp line |
| Edge% | Computed | `(effective / fair - 1) × 100` |
| Stake | Batch | USDC or kr |
| Status | Live | `ready` / `firing` / `placed` / `skipped` / `negative` |

Edge color coding:
- Green (`text-success`): edge > 5%
- Yellow (`text-amber-400`): edge 0–5%
- Red (`text-error`): edge < 0%

Status badges:
- `ready` — positive edge, waiting for auto-fire cycle
- `firing` — placement in progress
- `placed` — successfully placed
- `skipped` — slippage exceeded at fill time
- `negative` — edge went negative, won't fire

### E. Auto-fire flow (backend)

New method `MirrorService.fire_with_live_edge(bets)`:

```
1. For each bet in batch:
   a. Navigate to Polymarket market page
   b. Read live price from trading button
   c. Compute live_odds = 1 / live_price
   d. Fetch Pinnacle fair_odds from DB for this event+market+outcome
   e. Compute edge_pct = (polymarket_effective_odds(live_odds) / fair_odds - 1) × 100
   f. If edge_pct > 0: place the bet (existing placement flow with slippage guard)
   g. If edge_pct <= 0: skip, return status="negative"
   h. Broadcast result per bet via SSE/websocket for live UI update
2. Return summary: { placed: [...], skipped: [...], negative: [...] }
```

### F. Frontend polling + auto-fire trigger

```
ProviderSection (Polymarket):
  1. On expand: start polling GET /mirror/live-edge every 10s
  2. Display live edge table (columns above)
  3. "Fire All" button → POST /mirror/fire-batch-live
     - Backend iterates bets, checks live edge per bet, fires if positive
     - Results stream back, UI updates per-bet status
  4. On collapse or all placed: stop polling

ProviderSection (Soft / Pinnacle):
  1. Show batch table with pre-computed edge (from batch builder)
  2. Fair odds column from batch data (already computed)
  3. Manual checkboxes + "Mark All Done" (unchanged — these are placed manually)
```

## Files Changed

### Backend — modified

- **`mirror/service.py`**
  - New `get_live_edge(bets)` → returns per-bet `{ live_odds, fair_odds, edge_pct }`
  - New `fire_with_live_edge(bets)` → scans + auto-fires in one pass
  - Remove `scan_polymarket_bets()` (replaced by `get_live_edge`)
  - Keep `_place_single_polymarket_bet()` unchanged (reused by new fire flow)

- **`api/routes/mirror.py`**
  - New `GET /mirror/live-edge` — accepts provider + bet list, returns edge data
  - New `POST /mirror/fire-batch-live` — scans and fires in one pass
  - Deprecate `POST /mirror/scan-batch` (no longer needed)

- **`analysis/value.py`**
  - Extract `compute_edge(provider, live_odds, fair_odds) -> float` as reusable function (currently inline in `find_value`)

### Frontend — modified

- **`ExecutionPanel.tsx`**
  - Replace scan result table with live edge table (always visible when expanded)
  - Add polling hook for Polymarket sections (10s interval)
  - Replace "Scan Prices" → "Confirm & Place" flow with single "Fire All" button
  - Per-bet status badges (ready/firing/placed/skipped/negative)
  - Show Fair Odds column for all providers
  - Edge coloring: green >5%, yellow 0-5%, red <0%

- **`api.ts`**
  - New `getLiveEdge(provider, bets)` method
  - New `fireBatchLive(provider, bets)` method

### Removed

- `POST /mirror/scan-batch` endpoint (replaced by live-edge)
- Scan result UI in ExecutionPanel (replaced by live edge table)
- Old delta calculation logic

## Edge Cases

- **Pinnacle fair odds missing**: If no Pinnacle odds for an event (unmatched), show edge as `—` and status `no-sharp`. Don't auto-fire.
- **Mirror not running**: Live edge endpoint returns error. Frontend shows "Mirror offline" banner, falls back to batch edge display.
- **Page navigation during fire**: Bets are fired sequentially (one page at a time). Each bet navigates to its own market page.
- **Odds move between edge check and placement**: The existing slippage guard (2% tolerance) catches this. If fill price makes edge negative → skip.

## Notes

- `polymarket_effective_odds()` already exists in `value.py` and accounts for 2% fee
- Spread cost already reflected in Polymarket odds from extraction (VWAP from asks) — no double-counting
- Soft book edge doesn't change live because those odds are placed manually at the displayed price
- Polling stops when section is collapsed or all bets are placed/skipped
