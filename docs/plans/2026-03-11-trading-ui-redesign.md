# Trading UI Redesign — Confirmation-Gated Scanner

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the trading section to mirror the sports betting UI. 4 confirmation boxes gate opportunities — when all check, signals appear in a ValuePage-style table with two-step "Take Trade" flow.

**Architecture:** 3 tabs (Scanner, Bankroll, Stats). Scanner page = FilterBar → Confirmation Strip → Market State Row → Gated Opportunity Table. Backend adds `/confirmations` endpoint that auto-evaluates macro/span/fair-value/orderflow from SessionAnalysis. Frontend gates signal display behind all 4 confirmations.

**Tech Stack:** React 19 / TypeScript / Tailwind (frontend), FastAPI / SQLAlchemy (backend). Existing market data pipeline (Databento → AMT → Scanner) unchanged.

---

### Task 1: Backend — Add Confirmations Endpoint

**Files:**
- Modify: `backend/src/services/market_service.py`
- Modify: `backend/src/api/routes/market.py`

**Step 1: Add `get_confirmations()` to MarketService**

Open `backend/src/services/market_service.py`. Add this method to the `MarketService` class after `get_session_history()`:

```python
def get_confirmations(self, symbol: str | None = None) -> dict:
    """Evaluate 4 confirmation gates from current session data."""
    session_data = self.get_current_session(symbol)
    if not session_data:
        return {
            "macro": {"checked": False, "regime": "unknown", "vix": None},
            "span": {"checked": False, "structure": "no_data"},
            "fair_value": {"checked": False, "deviation_sd": None, "price_vs_va": "unknown"},
            "orderflow": {"checked": False, "delta": None, "divergence": False},
        }

    # Macro: risk_on = checked
    macro = session_data.get("macro") or {}
    regime = macro.get("regime", "unknown")
    macro_checked = regime == "risk_on"

    # Span: check market_type for trending structure
    market_type = session_data.get("market_type", "unknown")
    if market_type in ("trending_up", "trending_down"):
        span_checked = True
        span_structure = "bullish" if market_type == "trending_up" else "bearish"
    else:
        span_checked = False
        span_structure = "no_clear_structure"

    # Fair Value: price beyond 1.5SD from VWAP or outside VA
    vwap = session_data.get("vwap")
    last_price = session_data.get("last_price")
    vwap_1sd_upper = session_data.get("vwap_1sd_upper")
    vwap_1sd_lower = session_data.get("vwap_1sd_lower")
    price_vs_va = session_data.get("price_vs_va", "unknown")

    deviation_sd = None
    fv_checked = False
    if vwap and last_price and vwap_1sd_upper and vwap_1sd_lower:
        sd_width = vwap_1sd_upper - vwap
        if sd_width > 0:
            deviation_sd = round((last_price - vwap) / sd_width, 2)
            fv_checked = abs(deviation_sd) >= 1.5 or price_vs_va in ("above", "below")

    # Orderflow: delta confirms direction (nonzero + matches trend)
    total_delta = session_data.get("total_delta")
    divergence = session_data.get("delta_divergence", False)
    of_checked = False
    if total_delta is not None:
        # Delta confirms if it aligns with market structure or is divergent
        if market_type == "trending_up" and total_delta > 0:
            of_checked = True
        elif market_type == "trending_down" and total_delta < 0:
            of_checked = True
        elif divergence:
            of_checked = True  # Divergence is a signal too

    return {
        "macro": {"checked": macro_checked, "regime": regime, "vix": macro.get("vix")},
        "span": {"checked": span_checked, "structure": span_structure},
        "fair_value": {"checked": fv_checked, "deviation_sd": deviation_sd, "price_vs_va": price_vs_va},
        "orderflow": {"checked": of_checked, "delta": total_delta, "divergence": divergence},
    }
```

**Step 2: Add route to market.py**

Open `backend/src/api/routes/market.py`. Add after the `/macro` route:

```python
@router.get("/confirmations")
async def get_confirmations(svc: MarketService = Depends(_svc)):
    """Get auto-evaluated confirmation gates for trading."""
    return svc.get_confirmations()
```

**Step 3: Verify endpoint works**

Run: `curl http://localhost:8000/api/trading/market/confirmations`
Expected: JSON with 4 confirmation objects (macro/span/fair_value/orderflow), each with `checked` boolean.

**Step 4: Commit**

```bash
git add backend/src/services/market_service.py backend/src/api/routes/market.py
git commit -m "feat: add /confirmations endpoint for trading gate evaluation"
```

---

### Task 2: Frontend Types + API Client

**Files:**
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/services/api.ts`

**Step 1: Add ConfirmationState type**

Open `frontend/src/types/market.ts`. Add at the end of file:

```typescript
export interface ConfirmationCard {
  checked: boolean;
  regime?: string;
  vix?: number | null;
  structure?: string;
  deviation_sd?: number | null;
  price_vs_va?: string;
  delta?: number | null;
  divergence?: boolean;
}

export interface ConfirmationState {
  macro: ConfirmationCard;
  span: ConfirmationCard;
  fair_value: ConfirmationCard;
  orderflow: ConfirmationCard;
}
```

**Step 2: Add API method**

Open `frontend/src/services/api.ts`. Find the market data section (near `getMacroSnapshot`). Add:

```typescript
async getConfirmations(): Promise<ConfirmationState> {
  const res = await fetch(`${this.base}/api/trading/market/confirmations`);
  if (!res.ok) throw new Error('Failed to fetch confirmations');
  return res.json();
},
```

Import the type at the top of the file where other market types are imported:

```typescript
import type { ConfirmationState } from '@/types/market';
```

Note: if `api.ts` doesn't import from `@/types/market`, add the import. Check existing import pattern — the file uses inline types for most things. If so, just add the method without import and use `Promise<any>` temporarily, then the Scanner page will import the type directly.

**Step 3: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/services/api.ts
git commit -m "feat: add ConfirmationState type and getConfirmations API method"
```

---

### Task 3: Strip Down Tab Structure

**Files:**
- Modify: `frontend/src/components/Terminal/Sidebar.tsx` (line 3 — TabName type)
- Modify: `frontend/src/components/Terminal/TabBar.tsx` (lines 18-25, 32-35, 38-56)
- Modify: `frontend/src/components/Terminal/TerminalWindow.tsx` (lines 18-23, 114-126)

**Step 1: Update TabName type in Sidebar.tsx**

Open `frontend/src/components/Terminal/Sidebar.tsx`. Line 3 currently:
```typescript
export type TabName = 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'profiles' | 'settings' | 'tradingBankroll' | 'tradingToday' | 'tradingBuilder' | 'tradingTrades' | 'tradingJournal' | 'tradingScanner';
```

Replace with:
```typescript
export type TabName = 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'profiles' | 'settings' | 'tradingScanner' | 'tradingBankroll' | 'tradingStats';
```

**Step 2: Update STOCKS_TABS in TabBar.tsx**

Open `frontend/src/components/Terminal/TabBar.tsx`. Replace STOCKS_TABS (lines 18-25):

```typescript
const STOCKS_TABS: Tab[] = [
  { name: 'tradingScanner',  label: 'Scanner',  color: '#06B6D4' },
  { name: 'tradingBankroll', label: 'Bankroll', color: '#EC4899' },
  { name: 'tradingStats',    label: 'Stats',    color: '#1E88E5' },
];
```

Update DEFAULT_TAB (line 34):
```typescript
stocks: 'tradingScanner',
```

Update TAB_COLORS — remove `tradingToday`, `tradingBuilder`, `tradingTrades`, `tradingJournal`. Add `tradingStats`. Result:
```typescript
export const TAB_COLORS: Record<string, string> = {
  value: '#FF9800',
  dutch: '#10b981',
  reverse: '#EF5350',
  polymarket: '#A855F7',
  stats: '#1E88E5',
  bankroll: '#EC4899',
  specials: '#A78BFA',
  bets: '#1E88E5',
  profiles: '#A78BFA',
  settings: '#9AA0A6',
  success: '#10b981',
  tradingScanner: '#06B6D4',
  tradingBankroll: '#EC4899',
  tradingStats: '#1E88E5',
};
```

**Step 3: Update TerminalWindow.tsx**

Open `frontend/src/components/Terminal/TerminalWindow.tsx`.

Remove lazy imports for deleted pages (lines 19-22):
```typescript
// DELETE these lines:
const TradingTodayPage = lazy(...)
const TradingBuilderPage = lazy(...)
const TradingTradesPage = lazy(...)
const TradingJournalPage = lazy(...)
```

Add lazy import for new Stats page:
```typescript
const TradingStatsPage = lazy(() => import('./pages/TradingStatsPage').then(m => ({ default: m.TradingStatsPage })));
```

In `renderPage()` switch statement, remove cases for `tradingToday`, `tradingBuilder`, `tradingTrades`, `tradingJournal`. Add:
```typescript
case 'tradingStats':
  return <TradingStatsPage />;
```

Keep existing `tradingBankroll` and `tradingScanner` cases.

**Step 4: Delete unused page files**

Delete these files:
- `frontend/src/components/Terminal/pages/TradingTodayPage.tsx`
- `frontend/src/components/Terminal/pages/TradingBuilderPage.tsx`
- `frontend/src/components/Terminal/pages/TradingJournalPage.tsx`

Keep `TradingTradesPage.tsx` for now — Stats page will reuse some of its patterns.

**Step 5: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors. If there are import errors from deleted files, fix them.

**Step 6: Commit**

```bash
git add -A
git commit -m "refactor: strip trading tabs to Scanner/Bankroll/Stats"
```

---

### Task 4: Rewrite TradingScannerPage — Confirmation-Gated Layout

**Files:**
- Rewrite: `frontend/src/components/Terminal/pages/TradingScannerPage.tsx`

This is the big task. The new Scanner page has 4 sections: FilterBar, Confirmation Strip, Market State Row, Gated Opportunity Table.

**Step 1: Write the full new TradingScannerPage**

Replace the entire contents of `frontend/src/components/Terminal/pages/TradingScannerPage.tsx` with:

```tsx
import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { MarketSession, TradingSignal, ScanCondition, ConfirmationState } from '@/types/market';

type ConfirmationKey = 'macro' | 'span' | 'fair_value' | 'orderflow';

export function TradingScannerPage() {
  const [session, setSession] = useState<MarketSession | null>(null);
  const [signals, setSignals] = useState<TradingSignal[]>([]);
  const [confirmations, setConfirmations] = useState<ConfirmationState | null>(null);
  const [overrides, setOverrides] = useState<Record<ConfirmationKey, boolean | null>>({
    macro: null, span: null, fair_value: null, orderflow: null,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isComputing, setIsComputing] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [expandedSignal, setExpandedSignal] = useState<number | null>(null);
  const [threshold, setThreshold] = useState(70);
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [takingTrade, setTakingTrade] = useState<number | null>(null);
  const [entryPrice, setEntryPrice] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [sessionRes, signalsRes, confirmRes] = await Promise.all([
        api.getMarketSession().catch(() => null),
        api.getMarketSignals().catch(() => ({ signals: [] })),
        api.getConfirmations().catch(() => null),
      ]);
      if (sessionRes && !sessionRes.status) setSession(sessionRes);
      setSignals(signalsRes.signals || []);
      if (confirmRes) setConfirmations(confirmRes);
    } catch (err) {
      console.error('Failed to fetch market data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleCompute = async () => {
    setIsComputing(true);
    try {
      const res = await api.triggerMarketCompute();
      if (res && !res.status) setSession(res);
      // Refresh confirmations after compute
      const confirmRes = await api.getConfirmations().catch(() => null);
      if (confirmRes) setConfirmations(confirmRes);
    } catch (err) {
      console.error('Compute failed:', err);
    } finally {
      setIsComputing(false);
    }
  };

  const handleScan = async () => {
    setIsScanning(true);
    try {
      const res = await api.triggerMarketScan(threshold);
      setSignals(res.signals || []);
      setLastScan(new Date().toLocaleTimeString());
      // Refresh confirmations after scan
      const confirmRes = await api.getConfirmations().catch(() => null);
      if (confirmRes) setConfirmations(confirmRes);
    } catch (err) {
      console.error('Scan failed:', err);
    } finally {
      setIsScanning(false);
    }
  };

  const toggleOverride = (key: ConfirmationKey) => {
    setOverrides(prev => {
      const autoChecked = confirmations?.[key]?.checked ?? false;
      const current = prev[key];
      if (current === null) {
        // First click: override to opposite of auto
        return { ...prev, [key]: !autoChecked };
      }
      // Second click: clear override (back to auto)
      return { ...prev, [key]: null };
    });
  };

  const isChecked = (key: ConfirmationKey): boolean => {
    if (overrides[key] !== null) return overrides[key]!;
    return confirmations?.[key]?.checked ?? false;
  };

  const allConfirmed = (['macro', 'span', 'fair_value', 'orderflow'] as ConfirmationKey[]).every(isChecked);

  const handleTakeTrade = async (signal: TradingSignal) => {
    const price = parseFloat(entryPrice);
    if (!price || !signal) return;
    try {
      await api.createTrade({
        instrument: session?.symbol || 'NQ',
        direction: signal.direction,
        setup_type: signal.setup_type,
        entry_price: price,
        stop_price: signal.suggested_stop || 0,
        targets: signal.suggested_target ? [{ price: signal.suggested_target }] : [],
        contracts: 1,
        notes: `Scanner signal: ${signal.setup_name} (score: ${signal.score})`,
      });
      setTakingTrade(null);
      setEntryPrice('');
    } catch (err) {
      console.error('Failed to create trade:', err);
    }
  };

  if (isLoading) return <div className="text-muted text-sm">Loading scanner...</div>;

  const hasSession = session && session.poc;

  return (
    <div className="space-y-3 max-w-5xl">
      {/* A. FilterBar */}
      <div className="flex items-center gap-3 flex-wrap border-b border-border pb-2">
        <TabIcon name="tradingScanner" color={TAB_COLORS.tradingScanner} size={18} />
        <span className="text-sm font-semibold text-text">Scanner</span>
        <div className="flex-1" />
        <button
          onClick={handleCompute}
          disabled={isComputing}
          className="text-xs px-3 py-1 border border-tabTradingScanner/50 text-tabTradingScanner rounded hover:bg-tabTradingScanner/10 disabled:opacity-40"
        >
          {isComputing ? 'Computing...' : 'Compute'}
        </button>
        <button
          onClick={handleScan}
          disabled={isScanning || !hasSession}
          className="text-xs px-3 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40"
        >
          {isScanning ? 'Scanning...' : 'Scan'}
        </button>
        <div className="flex items-center gap-1.5 text-xs text-muted">
          <label>Thr:</label>
          <input type="range" min={30} max={95} step={5} value={threshold}
            onChange={e => setThreshold(parseInt(e.target.value))}
            className="w-16 accent-[#06B6D4]" />
          <span className="font-mono text-text w-5">{threshold}</span>
        </div>
        {lastScan && <span className="text-[10px] text-muted">Last: {lastScan}</span>}
      </div>

      {/* B. Confirmation Strip */}
      <div className="grid grid-cols-4 gap-2">
        <ConfirmCard
          label="Macro"
          checked={isChecked('macro')}
          autoChecked={confirmations?.macro?.checked ?? false}
          overridden={overrides.macro !== null}
          onClick={() => toggleOverride('macro')}
          detail={confirmations?.macro?.regime === 'risk_on' ? 'RISK ON' :
                  confirmations?.macro?.regime === 'risk_off' ? 'RISK OFF' : 'MIXED'}
          subDetail={confirmations?.macro?.vix != null ? `VIX ${confirmations.macro.vix.toFixed(1)}` : undefined}
          detailColor={confirmations?.macro?.regime === 'risk_on' ? 'text-success' :
                       confirmations?.macro?.regime === 'risk_off' ? 'text-error' : 'text-yellow'}
        />
        <ConfirmCard
          label="Span"
          checked={isChecked('span')}
          autoChecked={confirmations?.span?.checked ?? false}
          overridden={overrides.span !== null}
          onClick={() => toggleOverride('span')}
          detail={confirmations?.span?.structure === 'bullish' ? 'Bullish structure' :
                  confirmations?.span?.structure === 'bearish' ? 'Bearish structure' : 'No clear structure'}
          detailColor={confirmations?.span?.checked ? 'text-success' : 'text-muted'}
        />
        <ConfirmCard
          label="Fair Value"
          checked={isChecked('fair_value')}
          autoChecked={confirmations?.fair_value?.checked ?? false}
          overridden={overrides.fair_value !== null}
          onClick={() => toggleOverride('fair_value')}
          detail={confirmations?.fair_value?.deviation_sd != null
            ? `${confirmations.fair_value.deviation_sd > 0 ? '+' : ''}${confirmations.fair_value.deviation_sd} SD`
            : confirmations?.fair_value?.price_vs_va || 'No data'}
          detailColor={confirmations?.fair_value?.checked ? 'text-tabTradingScanner' : 'text-muted'}
        />
        <ConfirmCard
          label="Orderflow"
          checked={isChecked('orderflow')}
          autoChecked={confirmations?.orderflow?.checked ?? false}
          overridden={overrides.orderflow !== null}
          onClick={() => toggleOverride('orderflow')}
          detail={confirmations?.orderflow?.delta != null
            ? `Delta ${confirmations.orderflow.delta > 0 ? '+' : ''}${confirmations.orderflow.delta.toLocaleString()}`
            : 'No data'}
          subDetail={confirmations?.orderflow?.divergence ? 'Divergence' : undefined}
          detailColor={confirmations?.orderflow?.checked ? 'text-success' : 'text-muted'}
        />
      </div>

      {/* C. Market State Row */}
      {hasSession && (
        <div className="flex items-center gap-3 text-xs font-mono flex-wrap px-1">
          {session.poc && <Badge label="POC" value={session.poc.toFixed(0)} />}
          {session.vah && session.val && <Badge label="VA" value={`${session.val.toFixed(0)}-${session.vah.toFixed(0)}`} />}
          {session.vwap && <Badge label="VWAP" value={session.vwap.toFixed(0)} color="text-warning" />}
          {session.ib_high && session.ib_low && <Badge label="IB" value={`${session.ib_low.toFixed(0)}-${session.ib_high.toFixed(0)}`} />}
          {session.overnight_high && session.overnight_low && <Badge label="ON" value={`${session.overnight_low.toFixed(0)}-${session.overnight_high.toFixed(0)}`} color="text-muted" />}
          {session.total_delta != null && (
            <Badge label="Delta"
              value={`${session.total_delta > 0 ? '+' : ''}${session.total_delta.toLocaleString()}`}
              color={session.total_delta > 0 ? 'text-success' : 'text-error'} />
          )}
          {session.last_price && <Badge label="Price" value={session.last_price.toFixed(2)} color="text-text" />}
        </div>
      )}

      {/* D. Gated Opportunity Table */}
      <div className="border border-border bg-panel rounded">
        <div className="flex items-center justify-between px-4 py-2 border-b border-border">
          <h3 className="text-sm font-semibold text-text">
            Opportunities ({allConfirmed ? signals.length : 0})
          </h3>
          {!allConfirmed && (
            <span className="text-xs text-muted">
              {(['macro', 'span', 'fair_value', 'orderflow'] as ConfirmationKey[]).filter(k => !isChecked(k)).length} confirmation{(['macro', 'span', 'fair_value', 'orderflow'] as ConfirmationKey[]).filter(k => !isChecked(k)).length !== 1 ? 's' : ''} remaining
            </span>
          )}
        </div>

        {!allConfirmed ? (
          <div className="p-6 text-center text-muted text-sm">
            Waiting for confirmations...
          </div>
        ) : signals.length === 0 ? (
          <div className="p-4 text-center text-muted text-sm">
            {hasSession ? 'No signals above threshold.' : 'Compute session first, then scan.'}
          </div>
        ) : (
          <div className="divide-y divide-border">
            {signals.map(sig => {
              const rr = sig.suggested_entry && sig.suggested_stop && sig.suggested_target
                ? Math.abs(sig.suggested_target - sig.suggested_entry) / Math.abs(sig.suggested_entry - sig.suggested_stop)
                : null;
              return (
                <div key={sig.id}>
                  <button
                    onClick={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-panel2/50 transition-colors"
                  >
                    {/* Score */}
                    <div className="w-10 flex-shrink-0">
                      <div className={`text-sm font-mono font-bold ${sig.score >= 80 ? 'text-success' : sig.score >= 70 ? 'text-tabTradingScanner' : 'text-warning'}`}>
                        {sig.score.toFixed(0)}
                      </div>
                      <div className="w-full bg-panel2 rounded-full h-1 mt-0.5">
                        <div className="h-1 rounded-full" style={{
                          width: `${sig.score}%`,
                          backgroundColor: sig.score >= 80 ? '#4CAF50' : sig.score >= 70 ? '#06B6D4' : '#FF9800'
                        }} />
                      </div>
                    </div>

                    {/* Setup */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-text font-medium truncate">{sig.setup_name}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded border ${
                          sig.direction === 'long' ? 'border-success/50 text-success' : 'border-error/50 text-error'
                        }`}>
                          {sig.direction.toUpperCase()}
                        </span>
                      </div>
                    </div>

                    {/* Levels */}
                    <div className="flex gap-3 text-xs text-muted flex-shrink-0">
                      {sig.suggested_entry && <span>E:<span className="font-mono text-text ml-0.5">{sig.suggested_entry.toFixed(0)}</span></span>}
                      {sig.suggested_stop && <span>S:<span className="font-mono text-error ml-0.5">{sig.suggested_stop.toFixed(0)}</span></span>}
                      {sig.suggested_target && <span>T:<span className="font-mono text-success ml-0.5">{sig.suggested_target.toFixed(0)}</span></span>}
                      {rr && <span>R:R <span className="font-mono text-tabTradingScanner">{rr.toFixed(1)}</span></span>}
                    </div>

                    <span className={`text-muted text-xs transition-transform ${expandedSignal === sig.id ? 'rotate-90' : ''}`}>▸</span>
                  </button>

                  {/* Expanded: conditions + Take Trade */}
                  {expandedSignal === sig.id && (
                    <div className="px-4 pb-3 space-y-2 bg-panel2/30">
                      {sig.conditions.map((c, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs">
                          <div className="w-7 text-right font-mono text-muted">{Math.round(c.score * 100)}%</div>
                          <div className="w-16 bg-panel2 rounded-full h-1">
                            <div className="h-1 rounded-full" style={{
                              width: `${c.score * 100}%`,
                              backgroundColor: c.score >= 0.7 ? '#4CAF50' : c.score >= 0.4 ? '#FF9800' : '#EF5350'
                            }} />
                          </div>
                          <span className={c.is_auto ? 'text-text' : 'text-muted italic'}>{c.name}</span>
                          {!c.is_auto && <span className="text-[10px] text-muted/50">(manual)</span>}
                        </div>
                      ))}

                      {/* Take Trade */}
                      {takingTrade === sig.id ? (
                        <div className="flex items-center gap-2 pt-2 border-t border-border">
                          <span className="text-xs text-muted">Fill price:</span>
                          <input
                            type="number"
                            step="0.25"
                            value={entryPrice}
                            onChange={e => setEntryPrice(e.target.value)}
                            placeholder={sig.suggested_entry?.toFixed(2) || ''}
                            className="bg-panel2 border border-border rounded px-2 py-1 text-sm font-mono text-text w-28"
                            autoFocus
                          />
                          <button
                            onClick={() => handleTakeTrade(sig)}
                            disabled={!entryPrice}
                            className="text-xs px-3 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40"
                          >
                            Confirm
                          </button>
                          <button
                            onClick={() => { setTakingTrade(null); setEntryPrice(''); }}
                            className="text-xs px-2 py-1 text-muted hover:text-text"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="pt-2 border-t border-border">
                          <button
                            onClick={() => { setTakingTrade(sig.id); setEntryPrice(sig.suggested_entry?.toFixed(2) || ''); }}
                            className="text-xs px-4 py-1.5 bg-tabTradingScanner text-bg rounded hover:bg-tabTradingScanner/80 font-medium"
                          >
                            Take Trade
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- Subcomponents ---------- */

function ConfirmCard({
  label, checked, autoChecked, overridden, onClick, detail, subDetail, detailColor,
}: {
  label: string;
  checked: boolean;
  autoChecked: boolean;
  overridden: boolean;
  onClick: () => void;
  detail: string;
  subDetail?: string;
  detailColor: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`border rounded p-2.5 text-left transition-colors ${
        checked
          ? 'border-tabTradingScanner/60 bg-tabTradingScanner/5'
          : 'border-border bg-panel hover:bg-panel2/50'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <div className={`w-4 h-4 rounded border-2 flex items-center justify-center text-[10px] ${
          checked ? 'border-tabTradingScanner bg-tabTradingScanner text-bg' : 'border-muted'
        }`}>
          {checked && '✓'}
        </div>
        <span className="text-xs font-medium text-text">{label}</span>
        {autoChecked && !overridden && (
          <span className="text-[9px] px-1 rounded bg-tabTradingScanner/20 text-tabTradingScanner ml-auto">auto</span>
        )}
        {overridden && (
          <span className="text-[9px] px-1 rounded bg-warning/20 text-warning ml-auto">override</span>
        )}
      </div>
      <div className={`text-xs font-mono ${detailColor}`}>{detail}</div>
      {subDetail && <div className="text-[10px] text-muted">{subDetail}</div>}
    </button>
  );
}

function Badge({ label, value, color = 'text-tabTradingScanner' }: { label: string; value: string; color?: string }) {
  return (
    <span className="text-muted">
      {label} <span className={`${color}`}>{value}</span>
    </span>
  );
}
```

**Step 2: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

**Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingScannerPage.tsx
git commit -m "feat: rewrite Scanner page with confirmation-gated opportunities"
```

---

### Task 5: Create TradingStatsPage

**Files:**
- Create: `frontend/src/components/Terminal/pages/TradingStatsPage.tsx`

**Step 1: Write TradingStatsPage**

This page uses existing `api.getTradingAnalytics()` and `api.getTrades()` which already exist.

```tsx
import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Trade, TradingAnalytics } from '@/types/trading';

type FilterState = {
  setup_type: string;
  direction: string;
  result: string; // 'all' | 'win' | 'loss'
};

export function TradingStatsPage() {
  const [analytics, setAnalytics] = useState<TradingAnalytics | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [expandedTrade, setExpandedTrade] = useState<number | null>(null);
  const [filters, setFilters] = useState<FilterState>({
    setup_type: 'all', direction: 'all', result: 'all',
  });

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [analyticsRes, tradesRes] = await Promise.all([
        api.getTradingAnalytics({}).catch(() => null),
        api.getTrades({}).catch(() => []),
      ]);
      if (analyticsRes) setAnalytics(analyticsRes);
      setTrades(Array.isArray(tradesRes) ? tradesRes : []);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const filteredTrades = trades.filter(t => {
    if (filters.setup_type !== 'all' && t.setup_type !== filters.setup_type) return false;
    if (filters.direction !== 'all' && t.direction !== filters.direction) return false;
    if (filters.result === 'win' && (t.realized_pnl ?? 0) <= 0) return false;
    if (filters.result === 'loss' && (t.realized_pnl ?? 0) >= 0) return false;
    return true;
  });

  if (isLoading) return <div className="text-muted text-sm">Loading stats...</div>;

  return (
    <div className="space-y-4 max-w-5xl">
      <div className="flex items-center gap-2">
        <TabIcon name="tradingStats" color={TAB_COLORS.tradingStats} size={18} />
        <span className="text-sm font-semibold text-text">Trade Stats</span>
      </div>

      {/* Summary Cards */}
      {analytics && (
        <div className="grid grid-cols-5 gap-2">
          <StatCard label="Trades" value={String(analytics.total)} />
          <StatCard label="Win Rate" value={`${(analytics.win_rate * 100).toFixed(0)}%`}
            color={analytics.win_rate >= 0.5 ? 'text-success' : 'text-error'} />
          <StatCard label="P&L" value={`${analytics.total_pnl >= 0 ? '+' : ''}${analytics.total_pnl.toFixed(0)}`}
            color={analytics.total_pnl >= 0 ? 'text-success' : 'text-error'} />
          <StatCard label="Avg R" value={analytics.avg_r?.toFixed(2) || '—'}
            color={(analytics.avg_r ?? 0) >= 0 ? 'text-success' : 'text-error'} />
          <StatCard label="Profit Factor" value={analytics.profit_factor?.toFixed(2) || '—'}
            color={(analytics.profit_factor ?? 0) >= 1 ? 'text-success' : 'text-error'} />
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 text-xs">
        <select value={filters.direction} onChange={e => setFilters(f => ({ ...f, direction: e.target.value }))}
          className="bg-panel2 border border-border rounded px-2 py-1 text-text">
          <option value="all">All Directions</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>
        <select value={filters.result} onChange={e => setFilters(f => ({ ...f, result: e.target.value }))}
          className="bg-panel2 border border-border rounded px-2 py-1 text-text">
          <option value="all">All Results</option>
          <option value="win">Wins</option>
          <option value="loss">Losses</option>
        </select>
        <span className="text-muted ml-auto">{filteredTrades.length} trades</span>
      </div>

      {/* Trade History Table */}
      <div className="border border-border bg-panel rounded">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-muted">
              <th className="text-left px-3 py-2">Date</th>
              <th className="text-left px-3 py-2">Setup</th>
              <th className="text-left px-3 py-2">Dir</th>
              <th className="text-right px-3 py-2">Entry</th>
              <th className="text-right px-3 py-2">Exit</th>
              <th className="text-right px-3 py-2">P&L</th>
              <th className="text-right px-3 py-2">R</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filteredTrades.length === 0 ? (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-muted">No trades yet.</td></tr>
            ) : filteredTrades.map(t => (
              <tr key={t.id}
                onClick={() => setExpandedTrade(expandedTrade === t.id ? null : t.id)}
                className="hover:bg-panel2/50 cursor-pointer transition-colors">
                <td className="px-3 py-2 text-muted font-mono">
                  {t.opened_at ? new Date(t.opened_at).toLocaleDateString() : '—'}
                </td>
                <td className="px-3 py-2 text-text">{t.setup_type}</td>
                <td className="px-3 py-2">
                  <span className={t.direction === 'long' ? 'text-success' : 'text-error'}>
                    {t.direction.toUpperCase()}
                  </span>
                </td>
                <td className="px-3 py-2 text-right font-mono text-text">{t.entry_price.toFixed(2)}</td>
                <td className="px-3 py-2 text-right font-mono text-text">
                  {t.state === 'closed' && t.realized_pnl != null ? (t.entry_price + t.realized_pnl / t.contracts).toFixed(2) : '—'}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${
                  (t.realized_pnl ?? 0) > 0 ? 'text-success' : (t.realized_pnl ?? 0) < 0 ? 'text-error' : 'text-muted'
                }`}>
                  {t.realized_pnl != null ? `${t.realized_pnl >= 0 ? '+' : ''}${t.realized_pnl.toFixed(0)}` : '—'}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${
                  (t.r_multiple ?? 0) > 0 ? 'text-success' : (t.r_multiple ?? 0) < 0 ? 'text-error' : 'text-muted'
                }`}>
                  {t.r_multiple != null ? `${t.r_multiple >= 0 ? '+' : ''}${t.r_multiple.toFixed(2)}R` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatCard({ label, value, color = 'text-text' }: { label: string; value: string; color?: string }) {
  return (
    <div className="border border-border bg-panel rounded p-2.5 text-center">
      <div className="text-[10px] text-muted mb-0.5">{label}</div>
      <div className={`text-sm font-mono font-semibold ${color}`}>{value}</div>
    </div>
  );
}
```

**Step 2: Export from pages index (if exists)**

Check if `frontend/src/components/Terminal/pages/index.ts` exists and exports trading pages. If so, remove exports for deleted pages and add `TradingStatsPage`. If not, the lazy import in TerminalWindow handles it.

**Step 3: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

**Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingStatsPage.tsx
git commit -m "feat: add TradingStatsPage with summary cards and trade history"
```

---

### Task 6: Visual Verification

**Step 1: Start dev servers**

Start both backend (`:8000`) and frontend (`:5173`).

**Step 2: Verify tab structure**

Navigate to the app → click Stocks sidebar → verify only 3 tabs show: Scanner, Bankroll, Stats. Scanner should be active by default.

**Step 3: Verify Scanner page**

- 4 confirmation cards should render (may show "No data" if no session computed)
- Click "Compute" → should fetch market data
- Click "Scan" → should generate signals
- Confirmation cards should auto-check based on data
- Click a confirmation card to toggle override
- When all 4 checked → opportunity table should show signals
- Click a signal to expand → see conditions + "Take Trade" button
- Click "Take Trade" → enter price → "Confirm" → should save to DB

**Step 4: Verify Stats page**

- Switch to Stats tab → should show summary cards + trade history
- Any trade created from Scanner should appear here

**Step 5: Verify no console errors**

Open browser DevTools → Console tab → verify no TypeScript/React errors.

**Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix: address visual verification issues"
```

---

### Task 7: Cleanup — Delete Dead Code

**Step 1: Delete unused page files**

```bash
rm frontend/src/components/Terminal/pages/TradingTodayPage.tsx
rm frontend/src/components/Terminal/pages/TradingBuilderPage.tsx
rm frontend/src/components/Terminal/pages/TradingJournalPage.tsx
```

**Step 2: Check for remaining references**

Run: `grep -r "TradingToday\|TradingBuilder\|TradingJournal" frontend/src/`
Expected: No matches (all imports were removed in Task 3).

If matches found, remove the dead references.

**Step 3: Clean unused TAB_COLORS and tailwind classes**

The old colors (`tradingToday: '#FACC15'`, `tradingBuilder: '#22C55E'`, etc.) were already removed in Task 3. Verify `tailwind.config.js` doesn't have unused trading color references.

**Step 4: Final TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

**Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove dead trading pages and unused references"
```

---

Plan complete and saved to `docs/plans/2026-03-11-trading-ui-redesign.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?