# Unified Fire Workflow Design

**Date:** 2026-04-04
**Status:** Approved

## Context

The Play workflow has evolved through this session from a locked-batch capital-gated flow into a dynamic batch with inline capital plan. The fire window still uses continuous 1s DOM polling across all open tabs, which doesn't scale to 200+ bets and produces stale/wrong prices due to button matching issues.

The user needs a streamlined "Fire Everything" workflow that checks live prices right before placement, works across all provider types, and can later become fully autonomous.

## Design

### Batch View (unchanged from today's work)

- Auto-refreshes every 10s from DB odds
- Grouped by cluster with provider rows showing balance, bets, EV
- SSE detection for provider login and balance sync
- "Fire N bets" button in sticky header

### Fire Workflow (redesigned)

**Entry:** User clicks "Fire N bets" from the batch view.

**Provider queue:** Built from batch, sorted: Polymarket first, Pinnacle second, soft by EV descending. Grouped by cluster in the UI.

**Per-provider flow:**

1. Check if mirror has an open tab for this provider
2. If no tab: mark provider as "no tab — skipped", show in UI, move to next
3. If tab open: show provider's bets with DB odds (baseline)
4. User clicks "Fire Provider" to start placement sequence
5. For each bet (highest edge first):
   - Single DOM scrape of live price from the open tab
   - Compute live edge vs Pinnacle fair odds
   - If edge > 0: place the bet via mirror automation
   - If edge <= 0: skip with "negative edge" reason
   - Show result inline (placed/skipped/failed)
6. After all bets: advance to next provider automatically

**Price check strategy per provider type:**

| Provider | Price source | When |
|----------|-------------|------|
| Polymarket | DOM scrape of open tab (`_read_btn_prices`) | Once per bet, right before placement |
| Pinnacle | DB odds (extracted every 2min) | At batch build time — no live check needed |
| Soft API (Kambi, Altenar, etc.) | DB odds (extracted every 15min) | At batch build time — prices stable pre-match |
| Soft browser (Spectate, ComeOn, Tipwin) | DOM scrape of open tab | Once per bet, right before placement |

### Pinnacle Extraction Frequency

Change sharp tier interval from 5 minutes to 2 minutes in `providers.yaml`.

- Pinnacle extraction takes ~55s average, so 2-minute intervals won't overlap
- Polymarket extraction takes ~60s — they run grouped, total ~115s in a 120s window
- If this is too tight, split Pinnacle and Polymarket into separate tiers

### What to remove

- **Continuous 1s poll loop** in `fire_window.py` (`_poll_loop`, `POLL_INTERVAL_S`)
- **Live snapshot tracking** (`LiveSnapshot` dataclass, `live_snapshots` dict) — replaced by single price check at fire time
- **Frontend 1s state polling** (`setInterval(poll, 1_000)` in FireWindow.tsx)
- **`_find_btn_for_market` index-based matching** — already replaced with text-based matching

### What to keep

- **Mirror browser infrastructure** — tabs for provider sites, login detection, balance sync
- **SSE events** — provider detection, balance updates
- **`_read_btn_prices`** — DOM scraper for Polymarket button prices
- **`fire_provider`** function — but simplified to: check price → place → next
- **Balance-aware firing** — sort by edge, fire within balance, skip rest

### Fire Window UI (simplified)

**Queue view:** Same cluster-grouped style as batch view. Each provider row shows:
- Status dot (green = tab open + balance sufficient, amber = tab open + needs deposit, dim = no tab)
- Bet count, stake, EV
- "Fire" button per provider (or "No tab" label if not detected)

**Firing view:** Per-provider, shows bets being processed sequentially:
- Event name, outcome, DB odds, live price (from DOM), edge, stake
- Status per bet: pending → checking → placed/skipped/failed
- Auto-advances through bets, pauses on errors

**Summary view:** After all providers processed:
- Total placed, skipped (negative edge), skipped (no tab), failed
- EV captured vs EV missed

### Future: Auto-fire mode

Once the manual flow is proven reliable:
- Remove per-provider "Fire" confirmation
- "Fire Everything" walks through all providers with open tabs automatically
- Randomized delays between bets (30s-2min) for soft providers to avoid detection
- Sharp providers (Polymarket, Pinnacle) fire immediately

## Files to modify

### Backend
- `backend/src/services/fire_window.py` — simplify: remove poll loop, add per-bet price check
- `backend/src/mirror/service.py` — `_find_btn_for_market` already fixed (text-based matching)
- `backend/src/config/providers.yaml` — sharp tier interval 5min → 2min

### Frontend
- `frontend/src/components/Terminal/pages/play/FireWindow.tsx` — remove 1s polling, simplify to sequential fire flow
- `frontend/src/services/api/fireWindow.ts` — update API types if needed

## Verification

1. Open mirror, navigate to Polymarket, login
2. Open batch view — verify Polymarket shows green dot, correct balance
3. Click "Fire N bets" — verify fire window shows cluster-grouped queue
4. Click "Fire" on Polymarket — verify:
   - Each bet shows live price from DOM (not stale DB price)
   - Price matches what's visible on the Polymarket page
   - Bets with positive edge get placed
   - Bets with negative edge get skipped
5. Check Pinnacle extraction runs every ~2 minutes (server logs)
6. Verify no continuous polling — network tab should show no `/fire-window/state` requests
