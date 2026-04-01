# Live Edge Fire Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace snapshot scan-then-fire with a live edge dashboard that continuously compares Polymarket live odds against Pinnacle fair odds and auto-fires bets with positive edge after fees.

**Architecture:** New `compute_edge()` utility reused by both scanner and live edge. New `get_live_edge()` method on MirrorService reads Polymarket page prices, fetches Pinnacle fair odds from DB, returns per-bet edge. New `fire_with_live_edge()` combines scan+fire in one pass — only places bets with edge > 0 after fees. Frontend replaces scan→confirm flow with a single "Fire All" button that triggers auto-fire, plus 10s polling for live edge display.

**Tech Stack:** Python/FastAPI, SQLAlchemy, Playwright, React/TypeScript

---

## File Structure

| File | Role |
|------|------|
| `backend/src/analysis/value.py` | Extract `compute_edge()` as reusable function |
| `backend/src/mirror/service.py` | New `get_live_edge()` + `fire_with_live_edge()`, remove `scan_polymarket_bets()` |
| `backend/src/api/routes/mirror.py` | New `POST /mirror/live-edge` + `POST /mirror/fire-live`, remove `POST /scan-batch` |
| `frontend/src/services/api/settings.ts` | New `getLiveEdge()` + `fireLive()`, remove old scan/fire methods |
| `frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx` | Live edge table + auto-fire UI |

---

### Task 1: Extract `compute_edge()` in `value.py`

**Files:**
- Modify: `backend/src/analysis/value.py:72-119`

- [ ] **Step 1: Add `compute_edge()` function above `find_value()`**

Add this function at line 80 (after `polymarket_effective_odds`):

```python
def compute_edge(provider: str, provider_odds: float, fair_odds: float) -> float | None:
    """Compute edge percentage of provider odds vs fair odds.

    For Polymarket, applies 2% fee on net profit before comparison.
    Returns edge as percentage (e.g. 5.0 for 5% edge), or None if inputs invalid.
    """
    if fair_odds <= 1 or provider_odds <= 1:
        return None
    effective_odds = polymarket_effective_odds(provider_odds) if provider == "polymarket" else provider_odds
    return (effective_odds / fair_odds - 1) * 100
```

- [ ] **Step 2: Refactor `find_value()` to use `compute_edge()`**

In `find_value()` (around line 109-113), replace the inline edge calculation:

```python
# OLD:
    effective_odds = polymarket_effective_odds(provider_odds) if provider == "polymarket" else provider_odds
    edge = (effective_odds / fair_odds) - 1
    edge_pct = edge * 100

# NEW:
    edge_pct = compute_edge(provider, provider_odds, fair_odds)
    if edge_pct is None:
        return None
```

- [ ] **Step 3: Verify nothing broke**

Run: `cd backend && python -c "from src.analysis.value import compute_edge, find_value; print(compute_edge('polymarket', 6.0, 3.5)); print(compute_edge('unibet', 2.5, 2.3))"`

Expected: Two float values printed (positive edges).

- [ ] **Step 4: Commit**

```bash
git add backend/src/analysis/value.py
git commit -m "refactor: extract compute_edge() from find_value() for reuse"
```

---

### Task 2: Add `get_live_edge()` to MirrorService

**Files:**
- Modify: `backend/src/mirror/service.py:1335-1437`

- [ ] **Step 1: Add `_fetch_fair_odds()` helper method**

Add this method to `MirrorService` (before `scan_polymarket_bets`):

```python
def _fetch_fair_odds(self, event_ids: list[str]) -> dict[str, dict[str, float]]:
    """Fetch Pinnacle devigged fair odds for events from DB.

    Returns: {event_id: {outcome: fair_odds}} where fair_odds are devigged.
    """
    from .deps import get_db_session
    from ..db.models import Odds
    from ..analysis.devig import get_fair_odds_for_outcome

    db = get_db_session()
    try:
        pinnacle_rows = (
            db.query(Odds)
            .filter(
                Odds.event_id.in_(event_ids),
                Odds.provider_id == "pinnacle",
            )
            .all()
        )

        # Group by (event_id, market, point) to build full markets for devigging
        from collections import defaultdict
        markets: dict[tuple, dict[str, float]] = defaultdict(dict)
        for row in pinnacle_rows:
            key = (row.event_id, row.market, row.point)
            markets[key][row.outcome] = row.odds

        # Devig each market, build result
        result: dict[str, dict[str, float]] = defaultdict(dict)
        for (event_id, market, point), market_odds in markets.items():
            if len(market_odds) >= 2:
                for outcome in market_odds:
                    fair = get_fair_odds_for_outcome(outcome, market_odds, method="multiplicative")
                    if fair is not None:
                        result[event_id][outcome] = fair
            else:
                # Single outcome (e.g. spread) — use raw as conservative estimate
                for outcome, odds in market_odds.items():
                    result[event_id][outcome] = odds
        return dict(result)
    finally:
        db.close()
```

- [ ] **Step 2: Fix the import path**

The `_fetch_fair_odds` method uses `from .deps import get_db_session`. Check how the mirror service currently gets DB sessions. Look at the existing patterns in the file — it likely uses `from ..api.deps import get_db` or creates sessions directly. Use the same pattern. The import for `get_fair_odds_for_outcome` should be `from ..analysis.devig import get_fair_odds_for_outcome`.

- [ ] **Step 3: Add `get_live_edge()` method**

Add this method to `MirrorService` (replace `scan_polymarket_bets` at line 1335):

```python
async def get_live_edge(self, bets: list[dict]) -> dict:
    """Read live Polymarket prices, compare against Pinnacle fair odds.

    Each bet dict: {bet_id, market_slug, outcome, expected_price, amount_usdc,
                    event_id, original_odds, _original_outcome, _market_type}
    Returns: {bets: [{bet_id, live_odds, fair_odds, edge_pct, status}]}
    """
    from ..analysis.value import compute_edge

    context = self.interceptor.context
    if not context or not context.pages:
        return {"error": "No mirror browser open", "bets": []}

    page = context.pages[0]

    # Fetch Pinnacle fair odds for all events in batch
    event_ids = list({b["event_id"] for b in bets if b.get("event_id")})
    fair_odds_map = self._fetch_fair_odds(event_ids)

    results = []
    for bet in bets:
        bet_id = bet["bet_id"]
        slug = bet["market_slug"]
        outcome = bet["outcome"]
        expected_price = bet["expected_price"]
        amount = bet["amount_usdc"]
        original_outcome = bet.get("_original_outcome", outcome).lower()
        market_type = bet.get("_market_type", "")
        event_id = bet.get("event_id", "")

        # Get fair odds for this outcome
        event_fair = fair_odds_map.get(event_id, {})
        bet_outcome = bet.get("_original_outcome", outcome).lower()
        fair = event_fair.get(bet_outcome)

        # Button index mapping (same as existing scan logic)
        if original_outcome in ("home", "over"):
            btn_index = 0
        elif original_outcome == "draw":
            btn_index = 1
        elif original_outcome in ("away", "under"):
            btn_index = 2 if market_type == "1x2" else 1
        else:
            btn_index = 0

        # Navigate and read live price
        slug_parts = slug.split("-")
        league = slug_parts[0] if slug_parts else ""
        market_url = f"https://polymarket.com/sports/{league}/{slug}"

        try:
            await page.goto(market_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector('button.trading-button', timeout=15000)
        except Exception as e:
            results.append({
                "bet_id": bet_id, "outcome": outcome, "event_id": event_id,
                "live_odds": None, "fair_odds": fair,
                "edge_pct": None, "stake": amount,
                "status": "error", "reason": f"Page load failed: {e}",
            })
            continue

        try:
            btn_data = await page.evaluate(
                "() => {"
                "  const btns = [...document.querySelectorAll('button.trading-button')];"
                "  return btns.map(b => {"
                "    const text = b.textContent || '';"
                "    const priceMatch = text.match(/([\\d.]+)\\u00a2/);"
                "    const price = priceMatch ? parseFloat(priceMatch[1]) / 100 : null;"
                "    return {text: text.trim().slice(0, 40), price};"
                "  });"
                "}"
            )

            if btn_index < len(btn_data) and btn_data[btn_index]["price"] is not None:
                live_price = btn_data[btn_index]["price"]
                live_odds = round(1 / live_price, 2) if live_price > 0.01 else 999

                edge_pct = compute_edge("polymarket", live_odds, fair) if fair else None

                status = "value" if edge_pct is not None and edge_pct > 0 else (
                    "negative" if edge_pct is not None else "no-sharp"
                )

                results.append({
                    "bet_id": bet_id, "outcome": outcome, "event_id": event_id,
                    "live_odds": live_odds, "fair_odds": round(fair, 2) if fair else None,
                    "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
                    "stake": amount, "status": status,
                })
            else:
                results.append({
                    "bet_id": bet_id, "outcome": outcome, "event_id": event_id,
                    "live_odds": None, "fair_odds": round(fair, 2) if fair else None,
                    "edge_pct": None, "stake": amount,
                    "status": "error", "reason": f"No price at button index {btn_index}",
                })
        except Exception as e:
            results.append({
                "bet_id": bet_id, "outcome": outcome, "event_id": event_id,
                "live_odds": None, "fair_odds": round(fair, 2) if fair else None,
                "edge_pct": None, "stake": amount,
                "status": "error", "reason": str(e),
            })

    self._notify("live_edge_complete", {"bets": results})
    return {"bets": results}
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/mirror/service.py
git commit -m "feat(mirror): add get_live_edge() — live odds vs Pinnacle fair odds"
```

---

### Task 3: Add `fire_with_live_edge()` to MirrorService

**Files:**
- Modify: `backend/src/mirror/service.py`

- [ ] **Step 1: Add `fire_with_live_edge()` method**

Add after `get_live_edge()`:

```python
async def fire_with_live_edge(self, bets: list[dict]) -> dict:
    """Scan live Polymarket prices, auto-fire bets with positive edge after fees.

    Each bet dict: same as get_live_edge().
    Returns: {placed: [...], skipped: [...], negative: [...], errors: [...]}
    """
    import asyncio
    from ..analysis.value import compute_edge

    context = self.interceptor.context
    if not context or not context.pages:
        return {"error": "No mirror browser open", "placed": [], "skipped": [], "negative": [], "errors": []}

    page = context.pages[0]

    # Fetch Pinnacle fair odds for all events
    event_ids = list({b["event_id"] for b in bets if b.get("event_id")})
    fair_odds_map = self._fetch_fair_odds(event_ids)

    placed = []
    skipped = []
    negative = []
    errors = []

    for bet in bets:
        bet_id = bet["bet_id"]
        event_id = bet.get("event_id", "")
        outcome = bet["outcome"]
        original_outcome = bet.get("_original_outcome", outcome).lower()
        market_type = bet.get("_market_type", "")
        slug = bet["market_slug"]
        amount = bet["amount_usdc"]
        expected_price = bet["expected_price"]
        max_slippage = bet.get("max_slippage_pct", 2.0)

        # Get fair odds
        event_fair = fair_odds_map.get(event_id, {})
        fair = event_fair.get(original_outcome)

        if fair is None:
            errors.append({"bet_id": bet_id, "reason": "No Pinnacle fair odds", "status": "no-sharp"})
            continue

        # Navigate to market page
        slug_parts = slug.split("-")
        league = slug_parts[0] if slug_parts else ""
        market_url = f"https://polymarket.com/sports/{league}/{slug}"

        try:
            await page.goto(market_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector('button.trading-button', timeout=15000)
        except Exception as e:
            errors.append({"bet_id": bet_id, "reason": f"Page load failed: {e}", "status": "error"})
            continue

        # Read live price
        if original_outcome in ("home", "over"):
            btn_index = 0
        elif original_outcome == "draw":
            btn_index = 1
        elif original_outcome in ("away", "under"):
            btn_index = 2 if market_type == "1x2" else 1
        else:
            btn_index = 0

        try:
            btn_data = await page.evaluate(
                "() => {"
                "  const btns = [...document.querySelectorAll('button.trading-button')];"
                "  return btns.map(b => {"
                "    const text = b.textContent || '';"
                "    const priceMatch = text.match(/([\\d.]+)\\u00a2/);"
                "    const price = priceMatch ? parseFloat(priceMatch[1]) / 100 : null;"
                "    return {text: text.trim().slice(0, 40), price};"
                "  });"
                "}"
            )
        except Exception as e:
            errors.append({"bet_id": bet_id, "reason": f"Could not read prices: {e}", "status": "error"})
            continue

        if btn_index >= len(btn_data) or btn_data[btn_index]["price"] is None:
            errors.append({"bet_id": bet_id, "reason": f"No price at button index {btn_index}", "status": "error"})
            continue

        live_price = btn_data[btn_index]["price"]
        live_odds = round(1 / live_price, 2) if live_price > 0.01 else 999
        edge_pct = compute_edge("polymarket", live_odds, fair)

        if edge_pct is None or edge_pct <= 0:
            negative.append({
                "bet_id": bet_id, "live_odds": live_odds, "fair_odds": round(fair, 2),
                "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
                "status": "negative",
            })
            self._notify("live_edge_skip", {
                "bet_id": bet_id, "live_odds": live_odds, "fair_odds": round(fair, 2),
                "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
            })
            continue

        # Edge is positive — place the bet using existing placement flow
        logger.info(
            f"[mirror] Firing bet {bet_id}: live_odds={live_odds}, "
            f"fair_odds={fair:.2f}, edge={edge_pct:.1f}%"
        )
        self._notify("live_edge_firing", {
            "bet_id": bet_id, "live_odds": live_odds, "fair_odds": round(fair, 2),
            "edge_pct": round(edge_pct, 1),
        })

        result = await self._place_single_polymarket_bet(
            page, bet_id, slug, outcome, amount, expected_price, max_slippage,
            original_outcome=bet.get("_original_outcome", outcome),
            market_type=market_type,
        )

        if result.get("status") == "placed":
            result["live_odds"] = live_odds
            result["fair_odds"] = round(fair, 2)
            result["edge_pct"] = round(edge_pct, 1)
            placed.append(result)
        elif result.get("status") == "skipped":
            result["live_odds"] = live_odds
            result["fair_odds"] = round(fair, 2)
            result["edge_pct"] = round(edge_pct, 1)
            skipped.append(result)
        else:
            errors.append(result)

    summary = {
        "placed": placed, "skipped": skipped, "negative": negative, "errors": errors,
        "total": len(bets),
    }
    self._notify("fire_live_complete", summary)
    return summary
```

- [ ] **Step 2: Remove old `scan_polymarket_bets()` method**

Delete the `scan_polymarket_bets` method (lines 1335-1437). It's fully replaced by `get_live_edge()`.

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/service.py
git commit -m "feat(mirror): add fire_with_live_edge() auto-fire on positive edge"
```

---

### Task 4: Update API routes

**Files:**
- Modify: `backend/src/api/routes/mirror.py:275-389`

- [ ] **Step 1: Add `POST /mirror/live-edge` endpoint**

Add after the existing `_resolve_batch_bets` function (around line 363):

```python
@router.post("/live-edge")
async def get_live_edge(request: FireBatchRequest):
    """Get live Polymarket odds compared against Pinnacle fair odds.

    Returns per-bet: live_odds, fair_odds, edge_pct, status.
    """
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    page = mirror.interceptor.context.pages[0]
    if "polymarket.com" not in (page.url or ""):
        raise HTTPException(400, f"Mirror browser is not on Polymarket (current: {page.url})")

    resolved = _resolve_batch_bets(request)
    if not resolved["bets"]:
        return {"bets": [], "resolve_errors": resolved["errors"]}

    result = await mirror.get_live_edge(resolved["bets"])
    result["resolve_errors"] = resolved["errors"]
    return result
```

- [ ] **Step 2: Add `POST /mirror/fire-live` endpoint**

Add after the new `live-edge` endpoint:

```python
@router.post("/fire-live")
async def fire_live(request: FireBatchRequest):
    """Scan live Polymarket prices and auto-fire bets with positive edge.

    Combines scan + fire in one pass. Only places bets where
    edge_pct > 0 after Polymarket's 2% fee.
    """
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    page = mirror.interceptor.context.pages[0]
    if "polymarket.com" not in (page.url or ""):
        raise HTTPException(400, f"Mirror browser is not on Polymarket (current: {page.url})")

    resolved = _resolve_batch_bets(request)
    if not resolved["bets"]:
        return {"placed": [], "skipped": [], "negative": [], "errors": resolved["errors"], "total": 0}

    result = await mirror.fire_with_live_edge(resolved["bets"])
    result["resolve_errors"] = resolved["errors"]
    return result
```

- [ ] **Step 3: Remove old `POST /scan-batch` endpoint**

Delete the `scan_polymarket_batch` function (lines 288-311). The `fire_polymarket_batch` endpoint (lines 366-388) can stay as a fallback but mark it deprecated with a docstring update.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/mirror.py
git commit -m "feat(api): add /mirror/live-edge and /mirror/fire-live endpoints"
```

---

### Task 5: Update frontend API client

**Files:**
- Modify: `frontend/src/services/api/settings.ts:195-209`

- [ ] **Step 1: Replace `scanPolymarketBatch` and `firePolymarketBatch` with new methods**

Replace lines 195-209:

```typescript
  async getLiveEdge(bets: { event_id: string; market: string; outcome: string; odds: number; stake: number }[]): Promise<any> {
    return fetchJson('/mirror/live-edge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bets }),
    });
  },

  async fireLive(bets: { event_id: string; market: string; outcome: string; odds: number; stake: number }[]): Promise<any> {
    return fetchJson('/mirror/fire-live', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bets }),
    });
  },
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/services/api/settings.ts
git commit -m "feat(api): replace scan/fire with getLiveEdge/fireLive client methods"
```

---

### Task 6: Rewrite ExecutionPanel for live edge

**Files:**
- Modify: `frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx:187-472`

- [ ] **Step 1: Update ProviderSection state and handlers**

Replace the state and handler section (lines 195-254) in `ProviderSection`:

```typescript
function ProviderSection({
  group,
  isExpanded,
  onToggle,
  placedSet,
  onToggleBet,
  onMarkAllDone,
}: ProviderSectionProps) {
  const [firing, setFiring] = useState(false);
  const [fireResult, setFireResult] = useState<string | null>(null);
  const [liveEdge, setLiveEdge] = useState<Record<string, any>>({});
  const [edgeLoading, setEdgeLoading] = useState(false);
  const [edgeError, setEdgeError] = useState<string | null>(null);

  const betKeys = group.bets.map(betKey);
  const placedCount = betKeys.filter((k) => placedSet.has(k)).length;
  const totalCount = betKeys.length;
  const allDone = placedCount === totalCount;
  const anyDone = placedCount > 0;
  const status: 'done' | 'in-progress' | 'pending' = allDone
    ? 'done'
    : anyDone
    ? 'in-progress'
    : 'pending';

  const tierClass = TIER_CLASSES[group.tier] ?? 'text-success';
  const isPoly = group.providerId === 'polymarket';

  const batchPayload = group.bets.map((b) => ({
    event_id: b.event_id,
    market: b.market,
    outcome: b.outcome,
    odds: b.odds,
    stake: b.stake,
  }));

  // Poll live edge every 10s when expanded and Polymarket
  useEffect(() => {
    if (!isExpanded || !isPoly || allDone) return;

    let cancelled = false;
    const fetchEdge = async () => {
      setEdgeLoading(true);
      try {
        const result = await api.getLiveEdge(batchPayload);
        if (cancelled) return;
        const map: Record<string, any> = {};
        for (const b of result.bets ?? []) {
          map[b.bet_id] = b;
        }
        setLiveEdge(map);
        setEdgeError(null);
      } catch (err: any) {
        if (!cancelled) setEdgeError(err.message || 'Failed to fetch live edge');
      } finally {
        if (!cancelled) setEdgeLoading(false);
      }
    };

    fetchEdge();
    const interval = setInterval(fetchEdge, 10_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [isExpanded, isPoly, allDone]);

  const handleFireLive = async () => {
    setFiring(true);
    setFireResult(null);
    try {
      const result = await api.fireLive(batchPayload);
      const p = result.placed?.length ?? 0;
      const s = result.skipped?.length ?? 0;
      const n = result.negative?.length ?? 0;
      const e = result.errors?.length ?? 0;
      setFireResult(`${p} placed, ${s} slippage, ${n} no-edge, ${e} errors`);
      if (p > 0) {
        onMarkAllDone(betKeys.slice(0, p));
      }
    } catch (err: any) {
      setFireResult(`Error: ${err.message || err}`);
    } finally {
      setFiring(false);
    }
  };
```

- [ ] **Step 2: Update the bet table to show live edge columns**

Replace the table section (lines 314-391). The table now shows Fair Odds and live Edge for all providers. For Polymarket, Edge updates live from polling. For soft, Edge is pre-computed from batch:

```typescript
          <table className="sq w-full">
            <colgroup>
              {!isPoly && <col style={{ width: '28px' }} />}
              <col />
              <col style={{ width: '60px' }} />
              <col style={{ width: '55px' }} />
              <col style={{ width: '55px' }} />
              <col style={{ width: '55px' }} />
              <col style={{ width: '65px' }} />
            </colgroup>
            <thead className="bg-panel">
              <tr>
                {!isPoly && <th className="text-left"></th>}
                <th className="text-left">Event · Outcome</th>
                <th className="text-right">Market</th>
                <th className="text-right">{isPoly ? 'Live' : 'Odds'}</th>
                <th className="text-right">Fair</th>
                <th className="text-right">Edge</th>
                <th className="text-right">Stake</th>
              </tr>
            </thead>
            <tbody>
              {group.bets.map((b, i) => {
                const key = betKey(b);
                const placed = placedSet.has(key);
                const eventName = `${b.display_home} v ${b.display_away}`;
                const outcomeLabel = resolveOutcome(
                  b.outcome,
                  {
                    home_team: b.display_home,
                    away_team: b.display_away,
                    display_home: b.display_home,
                    display_away: b.display_away,
                    market: b.market,
                  },
                  b.point,
                  false,
                );
                const stakeText = isPoly
                  ? `$${(b.stake / USDC_RATE).toFixed(1)}`
                  : `${Math.round(b.stake)} kr`;

                // Live edge data (Polymarket) or batch data (soft)
                const live = liveEdge[i];
                const displayOdds = isPoly && live?.live_odds ? live.live_odds : b.odds;
                const displayFair = isPoly && live?.fair_odds ? live.fair_odds : b.fair_odds;
                const displayEdge = isPoly && live?.edge_pct != null ? live.edge_pct : b.edge_pct;

                const edgeColor = displayEdge > 5
                  ? 'text-success'
                  : displayEdge > 0
                  ? 'text-amber-400'
                  : 'text-error';

                return (
                  <tr
                    key={key}
                    className={`${placed ? 'opacity-40 line-through' : ''} transition-opacity`}
                  >
                    {!isPoly && (
                      <td className="!py-1.5 !px-2">
                        <CheckCircle
                          checked={placed}
                          onToggle={() => onToggleBet(key)}
                        />
                      </td>
                    )}
                    <td className="!py-1.5">
                      <div className="text-sm text-text truncate max-w-[280px]" title={eventName}>
                        {eventName}
                      </div>
                      <div className="text-[11px] text-muted">{outcomeLabel}{b.point != null ? ` (${b.point > 0 ? '+' : ''}${b.point})` : ''}</div>
                    </td>
                    <td className="text-right text-sm text-muted">{marketLabel(b.market)}</td>
                    <td className="text-right text-sm text-text font-medium">{displayOdds.toFixed(2)}</td>
                    <td className="text-right text-sm text-muted">{displayFair?.toFixed(2) ?? '—'}</td>
                    <td className={`text-right text-sm font-semibold ${edgeColor}`}>
                      {displayEdge != null ? `${displayEdge > 0 ? '+' : ''}${displayEdge.toFixed(1)}%` : '—'}
                    </td>
                    <td className="text-right text-sm text-text">{stakeText}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
```

- [ ] **Step 3: Update the action buttons**

Replace the action buttons section (lines 428-467). Remove scan/confirm flow, add single "Fire All" for Polymarket:

```typescript
          {/* Edge status bar (Polymarket only) */}
          {isPoly && !allDone && (
            <div className="px-3 py-1.5 border-t border-border flex items-center gap-2">
              {edgeLoading && <span className="text-[10px] text-muted animate-pulse">Scanning live prices...</span>}
              {edgeError && <span className="text-[10px] text-error">{edgeError}</span>}
              {!edgeLoading && !edgeError && Object.keys(liveEdge).length > 0 && (
                <span className="text-[10px] text-muted">
                  {Object.values(liveEdge).filter((b: any) => b.status === 'value').length}/{Object.keys(liveEdge).length} with +edge
                </span>
              )}
            </div>
          )}

          {/* Actions */}
          {!allDone && (
            <div className="px-3 py-2 border-t border-border flex items-center justify-end gap-2">
              {fireResult && (
                <span className="text-xs text-muted mr-auto">{fireResult}</span>
              )}
              {isPoly && (
                <button
                  onClick={handleFireLive}
                  disabled={firing}
                  className="px-3 py-1 bg-success text-bg text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  {firing ? 'Firing...' : 'Fire All'}
                </button>
              )}
              <button
                onClick={() => onMarkAllDone(betKeys)}
                className="px-3 py-1 bg-tabPlay text-bg text-xs font-medium hover:opacity-90 transition-opacity"
              >
                Mark All Done
              </button>
            </div>
          )}
```

- [ ] **Step 4: Remove old scan result table**

Delete the scan results section (lines 393-426) — the `{isPoly && scanResult && (...)}` block. This is fully replaced by the live edge columns in the main table.

- [ ] **Step 5: Verify the frontend compiles**

Run: `cd frontend && npx tsc --noEmit`

Expected: No type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/ExecutionPanel.tsx
git commit -m "feat(fire): live edge dashboard with auto-fire on positive edge"
```

---

### Task 7: Fix DB session import in MirrorService

**Files:**
- Modify: `backend/src/mirror/service.py`

- [ ] **Step 1: Verify the DB session pattern used in mirror**

Check how other methods in `service.py` access the DB. The `_stage_settlements_sync` method likely uses `SessionLocal` or similar. The `_fetch_fair_odds` method needs to use the same pattern. Look for `SessionLocal`, `get_db`, or `Session` imports in the file.

Common pattern in this codebase for sync DB access in mirror:

```python
from ..db.models import SessionLocal
# ...
db = SessionLocal()
try:
    # query
finally:
    db.close()
```

Update `_fetch_fair_odds` to match whatever pattern the file already uses.

- [ ] **Step 2: Test the full flow end-to-end**

Start the backend server and test the new endpoint:

```bash
cd backend
curl -X POST http://localhost:8000/api/mirror/live-edge \
  -H "Content-Type: application/json" \
  -d '{"bets": [{"event_id": "test", "market": "moneyline", "outcome": "home", "odds": 6.0, "stake": 2.0}]}'
```

Expected: Either a proper response with edge data, or a 400 error "No mirror running" (which confirms the route works).

- [ ] **Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix(mirror): correct DB session import for _fetch_fair_odds"
```

---

### Task 8: Final cleanup

**Files:**
- Modify: `backend/src/mirror/service.py`
- Modify: `backend/src/api/routes/mirror.py`

- [ ] **Step 1: Remove any remaining references to `scan_polymarket_bets`**

Search for `scan_polymarket` across the codebase and remove any stale references:

```bash
cd backend && grep -r "scan_polymarket" src/
```

Remove any found references.

- [ ] **Step 2: Remove old `scanPolymarketBatch` references from frontend**

Search for `scanPolymarket` across frontend:

```bash
cd frontend && grep -r "scanPolymarket" src/
```

Remove any found references.

- [ ] **Step 3: Commit cleanup**

```bash
git add -u
git commit -m "chore: remove deprecated scan_polymarket references"
```
