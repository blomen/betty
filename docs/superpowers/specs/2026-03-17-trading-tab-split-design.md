# Trading Tab Split: Monitor + Execute

## Context

The current `TradingIntradayPage` handles two conceptually distinct modes in a single component:
- **Scanning mode**: Level table + context sidebar — answers "where is price relative to fair value?"
- **Battle mode**: Orderflow/structure gauges + trade action — answers "at this level, are we continuing or reversing?"

These map to different stages of the trading workflow and should be separate tabs for clarity and focus.

## Design

### Tab Structure

Replace the single "Intraday" tab with two top-level tabs in the Stocks category:

| Tab | Label | Color | Purpose |
|-----|-------|-------|---------|
| `tradingMonitor` | Monitor | `#06B6D4` (cyan) | Fair value measurement: levels, VWAP, vol profile, macro, ML |
| `tradingExecute` | Execute | `#EF4444` (red) | Direction decision: gauges, trade action, positions |
| `tradingBankroll` | Bankroll | `#EC4899` (pink) | Unchanged |
| `tradingStats` | Stats | `#1E88E5` (blue) | Unchanged |

Default tab: `tradingMonitor`.

### Monitor Tab

Contents (extracted from scanning mode):
- **Level monitoring table** — price-centered, distance-sorted, color-coded by category
- **ContextSidebar** — session type, volume profile (POC/VAH/VAL), VWAP + SD, macro regime, ML day type
- **IntradayHeader** — title "Monitor", connection status, current price

Interactions:
- Click a level row → auto-switch to Execute tab. Note: `switchBattleLevel()` in `useLevelMonitor.ts` only works for levels with `status === 'at_level'`. For other levels, the tab switches but Execute shows the empty/stale state. This is acceptable for V1 — previewing non-triggered levels is a future enhancement.

### Execute Tab

Contents (extracted from battle mode):
- **IntradayHeader** — title "Execute", battle context badge when active
- **Gauge grid** — Orderflow (8) + Structure (6) + ML/Context (4) in 2-column layout
- **NearbyLevelStrip** — ±3 levels for quick level switching
- **TradeActionBar** — suggested entry/stop/targets + LONG/SHORT buttons
- **PositionManager** — open positions with P&L, scale/close actions (currently placeholder with empty state — awaiting positions API integration)

Empty states:
- **First load (no battle ever)**: "Watching for level trigger" with live price + nearest 3 levels above/below
- **After battle dismissed**: Show last battle data with "STALE" badge and dimmed header — useful for post-trade review. Requires adding a `lastBattle` ref in TradingContainer that caches `activeBattle` before `dismissBattle()` clears it. No changes to `useLevelMonitor.ts` needed — the stale state lives in the container.

### Auto-Switch Behavior

On `level_touched` event → automatically switch active tab to Execute (same as current battle trigger, but now changes tab instead of toggling mode).

### State Management: TradingContainer

Both tabs share EventSource connections and hooks. To avoid teardown/reconnect on tab switch, a **TradingContainer** wrapper component owns the shared state:

```
TerminalWindow
  └── TradingContainer (rendered for BOTH tradingMonitor and tradingExecute)
        ├── useMarketStream('NQ')  — single EventSource
        ├── useLevelMonitor()      — single EventSource
        ├── useSound()             — audio alerts (browser unlock preserved across tabs)
        ├── session data fetch     — single 30s refresh (handleRefresh, isRefreshing, lastRefresh)
        ├── trade action handlers  — handleTakeTrade, handleScale, handleClose
        ├── onSwitchToExecute()    — called on level_touched SSE + level row clicks
        └── renders MonitorPage OR ExecutePage based on activeSubTab prop
```

Props interface:
```typescript
interface TradingContainerProps {
  activeSubTab: 'tradingMonitor' | 'tradingExecute';
  onSwitchToExecute: () => void;  // calls handleTabChange('tradingExecute') in TerminalWindow
}
```

TerminalWindow routing — both cases render the same component type at the same tree position, preserving React identity and EventSource connections:
```typescript
case 'tradingMonitor':
case 'tradingExecute':
  return (
    <TradingContainer
      activeSubTab={activeTab as 'tradingMonitor' | 'tradingExecute'}
      onSwitchToExecute={() => handleTabChange('tradingExecute')}
    />
  );
```

Do NOT wrap TradingContainer differently between the two cases or add a key prop based on activeSubTab.

## Files to Modify

| File | Action | Changes |
|------|--------|---------|
| [Sidebar.tsx](frontend/src/components/Terminal/Sidebar.tsx) | Modify | Update `TabName` union: remove `tradingIntraday`, add `tradingMonitor` + `tradingExecute` |
| [TabBar.tsx](frontend/src/components/Terminal/TabBar.tsx) | Modify | Update `STOCKS_TABS`, `DEFAULT_TAB`, `TAB_COLORS` |
| [TradingContainer.tsx](frontend/src/components/Terminal/pages/TradingContainer.tsx) | Create | Shared state wrapper: hooks, data fetching, trade handlers, auto-switch effect |
| [MonitorPage.tsx](frontend/src/components/Terminal/pages/MonitorPage.tsx) | Create | Level table + ContextSidebar (scanning mode extraction) |
| [ExecutePage.tsx](frontend/src/components/Terminal/pages/ExecutePage.tsx) | Create | Gauges + TradeActionBar + PositionManager (battle mode extraction) |
| [TerminalWindow.tsx](frontend/src/components/Terminal/TerminalWindow.tsx) | Modify | Route both tab names to TradingContainer, pass `onSwitchToExecute` callback |
| [IntradayHeader.tsx](frontend/src/components/Terminal/pages/IntradayHeader.tsx) | Modify | Accept `tabLabel` and `tabColor` props |
| [TradingIntradayPage.tsx](frontend/src/components/Terminal/pages/TradingIntradayPage.tsx) | Delete | Replaced by Container + Monitor + Execute |

## Existing Components Reused As-Is

- `ContextSidebar.tsx` — used in MonitorPage
- `NearbyLevelStrip.tsx` — used in ExecutePage
- `TradeActionBar.tsx` — used in ExecutePage
- `GaugeBar.tsx` — used in ExecutePage
- `PositionManager.tsx` — used in ExecutePage
- `gaugeHelpers.ts` — used in ExecutePage

## Future: Profiles/Structure Tab

A third tab for multi-timeframe analysis — not in scope for this implementation but captured here for continuity.

**Content:** TPO charts, volume profiles at multiple scopes (session, leg, macro cycle), anchor-based custom profiles with configurable start/end dates. Key levels (POC, VAH, VAL, poor highs/lows, single prints) derived from each scope.

**Data source:** Databento OHLCV bars already available via `DabentoProvider` (`backend/src/market_data/databento_provider.py`). `compute_volume_profile()` in `backend/src/market_data/levels.py` computes VP from bar data. Weekly/monthly composite VPs already planned in `docs/superpowers/plans/2026-03-16-composite-volume-profiles.md`. TPO can be computed from 30-min bars (standard TPO letter assignment).

**Anchor profiles:** User sets a start date (e.g., macro cycle low, leg start) → system fetches bars from that date to now → computes VP → shows POC/VAH/VAL as levels. Multiple anchors can coexist.

## Verification

1. `npm run build` — no TypeScript errors
2. Start dev servers, open frontend
3. Verify Stocks category shows 4 tabs: Monitor, Execute, Bankroll, Stats
4. Monitor tab: level table renders with context sidebar, levels are clickable
5. Execute tab: shows empty state on first load ("Watching for level trigger")
6. Click a level in Monitor → switches to Execute with that level's battle data
7. Verify EventSource connections (market stream, level monitor) survive tab switches — check Network tab, should see single SSE connection maintained
8. If battle triggers via SSE event → auto-switches to Execute tab
9. Position manager appears on Execute tab when positions exist
