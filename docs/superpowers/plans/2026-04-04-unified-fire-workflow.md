# Unified Fire Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify the fire window from continuous DOM polling to single price-check-before-placement, and increase Pinnacle extraction frequency to 2 minutes.

**Architecture:** The fire window becomes a sequential bet placer: for each provider with an open mirror tab, it checks the live price once per bet right before placing it, skips if negative edge. No continuous polling. The batch view (already built) feeds bets into this flow.

**Tech Stack:** Python/FastAPI backend, React/TypeScript frontend, Playwright for DOM scraping

---

### Task 1: Increase Pinnacle Extraction Frequency

**Files:**
- Modify: `backend/src/config/providers.yaml:854-860`

- [ ] **Step 1: Update sharp tier interval**

In `backend/src/config/providers.yaml`, find the sharp tier config (around line 854) and change `interval_minutes` from 5 to 2:

```yaml
  sharp:
    # Sharp reference (Pinnacle) + event matching (Polymarket)
    # Grouped: run together as one unit every 2 minutes
    providers: [pinnacle, polymarket]
    interval_minutes: 2
    grouped: true
```

- [ ] **Step 2: Verify extraction timing is safe**

Pinnacle takes ~55s, Polymarket ~60s. Grouped total ~115s in a 120s window. This is tight but workable. The comment about "170-270s" is outdated — current metrics show 55s avg.

- [ ] **Step 3: Commit**

```bash
git add backend/src/config/providers.yaml
git commit -m "perf(extraction): increase sharp tier frequency from 5min to 2min"
```

---

### Task 2: Simplify Backend Fire Window — Remove Poll Loop

**Files:**
- Modify: `backend/src/services/fire_window.py`

- [ ] **Step 1: Remove poll loop and LiveSnapshot infrastructure**

In `fire_window.py`, remove:
- `POLL_INTERVAL_S` constant (line 79)
- `LiveSnapshot` dataclass (lines 27-35)
- `live_snapshots` field from `FireWindow` dataclass (line 66)
- `_poll_task` field from `FireWindow` dataclass (line 69)
- `activate_provider()` function (lines 245-296) — no longer needed, activation is implicit
- `_poll_loop()` function (lines 299-308)
- `_update_live_prices()` function (lines 311-365)
- `_categorise()` function (lines 367-375)
- `_cancel_poll()` helper (lines 719-725)

- [ ] **Step 2: Simplify `get_live_state()` to return DB odds only**

Replace the current `get_live_state()` (which reads from LiveSnapshots) with a simple version that returns bet data from the fire window without live price overlays:

```python
def get_live_state() -> dict:
    """Return current provider's bets with DB odds (no live polling)."""
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    bets = _window.provider_bets.get(pid, []) if pid else []
    tier = bets[0].tier if bets else "soft"

    try:
        position = _window.provider_queue.index(pid) + 1 if pid else 0
    except ValueError:
        position = 0

    # Fetch current balance
    balance = None
    try:
        from ..repositories.profile_repo import ProfileRepo
        db = get_session()
        try:
            profile_repo = ProfileRepo(db)
            profile = profile_repo.get_active()
            if profile:
                balance = profile_repo.get_balance(profile.id, pid)
        finally:
            db.close()
    except Exception:
        pass

    bet_dicts = []
    total_stake = 0.0
    total_ev = 0.0
    for bet in bets:
        total_stake += bet.stake
        total_ev += bet.expected_profit
        bet_dicts.append({
            "bet_id": bet.bet_id,
            "provider_id": bet.provider_id,
            "event_id": bet.event_id,
            "market": bet.market,
            "outcome": bet.outcome,
            "point": bet.point,
            "odds": bet.odds,
            "fair_odds": bet.fair_odds,
            "edge_pct": bet.edge_pct,
            "stake": bet.stake,
            "expected_profit": bet.expected_profit,
            "display_home": bet.display_home,
            "display_away": bet.display_away,
            "sport": bet.sport,
            "tier": bet.tier,
            "market_slug": bet.market_slug,
            "poly_outcome": bet.poly_outcome,
            "original_outcome": bet.original_outcome,
            "start_time": bet.start_time,
        })

    return {
        "provider_id": pid,
        "tier": tier,
        "position": position,
        "total_providers": len(_window.provider_queue),
        "status": _window.status,
        "bets": bet_dicts,
        "balance": round(balance, 2) if balance is not None else None,
        "summary": {
            "total_bets": len(bets),
            "total_stake": round(total_stake, 2),
            "total_ev": round(total_ev, 2),
        },
    }
```

- [ ] **Step 3: Rewrite `fire_provider()` with per-bet price check**

Replace the current `fire_provider()` with a version that checks live price once per bet before placing:

```python
async def fire_provider(mirror_service) -> dict:
    """Fire bets for current provider with per-bet live price check.

    For each bet (sorted by edge descending):
    1. Check live price from mirror tab (DOM scrape)
    2. Compute live edge vs fair odds
    3. If edge > 0 and balance sufficient: place the bet
    4. Otherwise: skip
    """
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    if pid is None:
        return {"error": "no active provider"}

    _window.status = "firing"
    bets = _window.provider_bets.get(pid, [])

    # Sort by edge descending — best bets first
    bets_sorted = sorted(bets, key=lambda b: -b.edge_pct)

    # Get balance for this provider
    from ..repositories.profile_repo import ProfileRepo
    db = get_session()
    try:
        profile_repo = ProfileRepo(db)
        profile = profile_repo.get_active()
        balance = profile_repo.get_balance(profile.id, pid) if profile else float("inf")
    finally:
        db.close()

    placed = []
    excluded = []
    failed = []
    remaining_balance = balance

    for bet in bets_sorted:
        # Balance check
        if remaining_balance < bet.stake:
            excluded.append({"bet_id": bet.bet_id, "reason": "insufficient_balance"})
            continue

        # Live price check for browser-based providers
        live_edge = bet.edge_pct  # Default: use DB edge
        if pid == "polymarket" and mirror_service is not None:
            live_edge = await _check_live_price_poly(bet, mirror_service)
        # Future: add soft browser providers here

        if live_edge is None or live_edge <= 0:
            excluded.append({"bet_id": bet.bet_id, "reason": "negative_edge", "live_edge": live_edge})
            print(f"  *{bet.display_home} vs {bet.display_away}*{bet.market}*{bet.outcome}*SKIP edge={live_edge}*")
            continue

        # Place the bet
        print(f"  *{bet.display_home} vs {bet.display_away}*{bet.market}*{bet.outcome}*FIRE edge={live_edge:.1f}%*")

        if pid == "polymarket" and mirror_service is not None:
            try:
                poly_tabs = getattr(mirror_service, "_poly_tabs", {})
                page = poly_tabs.get(bet.market_slug)
                if page is None:
                    failed.append({"bet_id": bet.bet_id, "reason": "no_tab"})
                    continue

                snap_price = 1 / bet.odds if bet.odds > 0 else 0
                result = await mirror_service._place_single_polymarket_bet(
                    page=page,
                    bet_id=bet.bet_id,
                    slug=bet.market_slug,
                    outcome=bet.poly_outcome or bet.outcome,
                    amount=bet.stake,
                    expected_price=snap_price,
                    max_slippage=3.0,
                    original_outcome=bet.original_outcome,
                    market_type=bet.market,
                )
                if result.get("status") == "placed":
                    placed.append(result)
                    remaining_balance -= bet.stake
                else:
                    failed.append(result)
            except Exception as exc:
                logger.exception("Placement failed for bet %s", bet.bet_id)
                failed.append({"bet_id": bet.bet_id, "reason": str(exc)})
        else:
            # Non-Polymarket: manual placement (no automation yet)
            placed.append({
                "bet_id": bet.bet_id,
                "status": "manual",
                "provider_id": pid,
                "stake": bet.stake,
            })
            remaining_balance -= bet.stake

    # Close Polymarket tabs after firing
    if pid == "polymarket" and mirror_service is not None:
        try:
            await mirror_service.close_poly_tabs()
        except Exception:
            pass

    fire_result = {
        "provider_id": pid,
        "placed": placed,
        "failed": failed,
        "excluded": excluded,
        "summary": {
            "total": len(bets),
            "fired": len(placed),
            "failed": len(failed),
            "excluded": len(excluded),
        },
    }

    _window.fired_results[pid] = fire_result
    _advance_queue()
    return fire_result
```

- [ ] **Step 4: Add `_check_live_price_poly()` helper**

```python
async def _check_live_price_poly(bet: FireWindowBet, mirror_service) -> float | None:
    """Single DOM scrape of Polymarket button price for one bet.

    Returns live edge percentage, or None if price can't be read.
    """
    poly_tabs = getattr(mirror_service, "_poly_tabs", {})
    page = poly_tabs.get(bet.market_slug)
    if page is None:
        return None

    try:
        buttons = await mirror_service._read_btn_prices(page)
        matched = mirror_service._find_btn_for_market(
            buttons, bet.outcome, bet.market,
            home_name=bet.display_home, away_name=bet.display_away,
        )
        if not matched:
            return None
        price = matched.get("price")
        if not price or price <= 0 or price >= 1:
            return None
        live_odds = round(1 / price, 4)
        live_edge = compute_edge("polymarket", live_odds, bet.fair_odds)
        return live_edge
    except Exception:
        logger.debug("Live price check failed for bet %s", bet.bet_id, exc_info=True)
        return None
```

- [ ] **Step 5: Add `set_current_provider()` function**

Since we removed `activate_provider()`, we need a simple way to set the current provider:

```python
def set_current_provider(provider_id: str) -> dict:
    """Set the current provider for firing. No tab opening or polling."""
    if _window is None:
        return {"error": "no fire window open"}
    _window.current_provider = provider_id
    _window.status = "active"
    return get_live_state()
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/fire_window.py
git commit -m "refactor(fire): remove poll loop, add per-bet live price check"
```

---

### Task 3: Update Backend Fire Window API Routes

**Files:**
- Modify: `backend/src/api/routes/fire_window.py`

- [ ] **Step 1: Replace activate endpoint with set_current_provider**

Change the `activate` endpoint from calling `fw.activate_provider()` (which opened tabs + started polling) to `fw.set_current_provider()`:

```python
@router.post("/activate/{provider_id}")
async def activate_provider(provider_id: str):
    """Set the current provider for firing."""
    return fw.set_current_provider(provider_id)
```

Remove the mirror service dependency from activate — it's only needed in fire now.

- [ ] **Step 2: Ensure fire endpoint opens tabs before firing**

The fire endpoint should open Polymarket tabs if needed before calling `fire_provider`:

```python
@router.post("/fire")
async def fire_current_provider():
    """Fire bets: check live price per bet, place if +EV."""
    mirror = _get_active_mirror()

    # Ensure Polymarket tabs are open for price checking
    window = fw.get_window()
    if window and window.current_provider == "polymarket" and mirror:
        bets = window.provider_bets.get("polymarket", [])
        tab_bets = [
            {"market_slug": b.market_slug, "poly_outcome": b.poly_outcome, "bet_id": b.bet_id}
            for b in bets if b.market_slug
        ]
        try:
            await mirror._ensure_poly_tabs(tab_bets)
        except Exception:
            pass

    return await fw.fire_provider(mirror)
```

- [ ] **Step 3: Remove state polling endpoint (optional)**

Keep the `/fire-window/state` endpoint but it now returns DB odds only (no live data). This is still useful for the frontend to display bets.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/fire_window.py
git commit -m "refactor(fire-api): simplify activate, open tabs at fire time"
```

---

### Task 4: Simplify Frontend Fire Window

**Files:**
- Modify: `frontend/src/components/Terminal/pages/play/FireWindow.tsx`

- [ ] **Step 1: Remove continuous polling**

Remove the `useEffect` that polls `/fire-window/state` every 1 second (the `pollRef` / `setInterval` block). The fire window no longer needs continuous updates.

- [ ] **Step 2: Simplify phases**

Reduce phases from `'queue' | 'activating' | 'monitoring' | 'firing' | 'result' | 'complete'` to:

```typescript
type Phase = 'queue' | 'firing' | 'result' | 'complete';
```

Remove:
- `activating` phase (no tab opening step — tabs managed by mirror)
- `monitoring` phase (no continuous price display — prices checked at fire time)

- [ ] **Step 3: Update queue rendering**

Keep the cluster-grouped queue (already built). Each provider row:
- Shows bet count, stake, EV from the batch data
- "Fire" button that calls `handleActivate(provider_id)` then immediately `handleFire()`
- Or combine into one action: clicking "Fire" on a provider sets it as current and fires

```typescript
const handleFireProvider = useCallback(async (providerId: string) => {
  setError(null);
  setCurrentProvider(providerId);
  setPhase('firing');
  try {
    // Set current provider then fire
    await fireWindowApi.activate(providerId);
    const result = await fireWindowApi.fire();
    if (closedRef.current) return;
    setFireResult(result);
    setProviderResults(prev => [...prev, {
      providerId: result.provider_id,
      placed: result.summary.fired,
      failed: result.summary.failed,
      excluded: result.summary.excluded,
    }]);
    setQueue(prev => prev.map(q =>
      q.provider_id === result.provider_id ? { ...q, fired: true } : q
    ));
    setPhase('result');
  } catch (err: any) {
    if (closedRef.current) return;
    setError(err.message || 'Failed to fire bets');
    setPhase('queue');
  }
}, []);
```

- [ ] **Step 4: Update result phase**

Show placed/excluded/failed counts. "Next Provider" advances to the next unfired provider in the queue, or transitions to `complete` if all done.

- [ ] **Step 5: Remove SSE auto-activate**

Remove the SSE `useEffect` that auto-activated providers on `sync_available`. This was for the continuous monitoring approach. With manual "Fire" per provider, it's not needed.

- [ ] **Step 6: Rebuild frontend**

```bash
cd frontend && npx tsc --noEmit && npx vite build
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/FireWindow.tsx
git commit -m "refactor(fire-ui): remove polling, simplify to per-provider fire"
```

---

### Task 5: End-to-End Verification

- [ ] **Step 1: Restart mirror and test batch view**

```bash
.\mirror.bat
```

Open `http://127.0.0.1:8000`, go to Play tab. Verify:
- Batch shows all +EV bets grouped by cluster
- Provider rows show balance when detected via SSE
- "Fire N bets" button visible in header

- [ ] **Step 2: Test fire window queue**

Click "Fire N bets". Verify:
- Providers grouped by cluster (matching batch style)
- No continuous `/fire-window/state` requests in network tab
- Each provider has a "Fire" button

- [ ] **Step 3: Test Polymarket firing**

Open Polymarket in mirror browser, login. Click "Fire" on Polymarket provider. Verify:
- Terminal shows per-bet price checks: `*Cloud9 vs LYON*spread*home*FIRE edge=X%*`
- Bets with negative live edge show: `*...*SKIP edge=-X*`
- Results shown in the fire window

- [ ] **Step 4: Verify Pinnacle extraction frequency**

SSH to server, check extraction runs:
```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T postgres psql -U firev -d firev -c \"
SELECT start_time, duration_seconds FROM provider_run_metrics 
WHERE provider_id = 'pinnacle' ORDER BY start_time DESC LIMIT 5;
\""
```

Verify ~2 minute intervals between runs.

- [ ] **Step 5: Deploy to server**

```bash
git push origin main
ssh root@148.251.40.251 "cd /opt/firev && git pull && docker compose up -d --build backend"
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat(play): unified fire workflow — per-bet price check, 2min Pinnacle extraction"
```
