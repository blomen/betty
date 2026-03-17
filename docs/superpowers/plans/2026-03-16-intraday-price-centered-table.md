# Intraday Price-Centered Level Table Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom `LevelMonitorTable` with a `.sq` price-centered table matching the betting page pattern, with current price as a center divider row and levels sorted outward from it.

**Architecture:** Rewrite `TradingIntradayPage.tsx` to inline the level table using the `.sq` class pattern from `ValuePage.tsx`. Split levels into above/below arrays around the current price. Delete `LevelMonitorTable.tsx`. No backend or hook changes.

**Tech Stack:** React, TypeScript, Tailwind CSS, `.sq` table class from `index.css`

**Spec:** `docs/superpowers/specs/2026-03-16-intraday-price-centered-table-design.md`

---

## Chunk 1: Core Implementation

### Task 1: Rewrite TradingIntradayPage with price-centered `.sq` table

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

This is a single-file rewrite. The page is 230 lines. All hooks, state, handlers (lines 1-145) stay identical. Three changes:
1. Remove the `LevelMonitorTable` import (line 7)
2. Move the `loading` early-return (line 148) into the new block
3. Replace the return JSX (lines 150-229)

- [ ] **Step 1: Replace the JSX return block**

In `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`:

**A) Remove the import on line 7:**
```typescript
import { LevelMonitorTable } from './LevelMonitorTable';
```

**B) Remove the loading early-return on line 148:**
```typescript
if (loading) return <div className="text-zinc-500 text-sm p-4">Loading level monitor...</div>;
```
(This is relocated into the new return block below.)

Add these constants after the existing imports (before the component function):

```typescript
const STATUS_BADGES: Record<string, { text: string; cls: string }> = {
  watching: { text: 'WATCH', cls: 'bg-zinc-800 text-zinc-500' },
  approaching: { text: 'NEAR', cls: 'bg-amber-900/50 text-amber-400' },
  at_level: { text: 'AT LVL', cls: 'bg-cyan-900/50 text-cyan-300' },
  triggered: { text: 'TRIG', cls: 'bg-emerald-900/50 text-emerald-400' },
  rejected: { text: 'REJ', cls: 'bg-zinc-800 text-zinc-600' },
};

const CATEGORY_COLORS: Record<string, string> = {
  session: 'text-blue-400',
  band: 'text-purple-400',
  prior: 'text-amber-400',
  structure: 'text-cyan-400',
  overnight: 'text-zinc-400',
};
```

Replace the return block (lines 150-229) with:

```tsx
  // Price-centered level split
  const cp = currentPrice ?? 0;
  const aboveLevels = levels
    .filter(l => l.price > cp)
    .sort((a, b) => a.price - b.price); // furthest at top, closest at bottom
  const belowLevels = levels
    .filter(l => l.price <= cp)
    .sort((a, b) => b.price - a.price); // closest at top, furthest at bottom

  // Compact mode: 3 closest above + 3 closest below
  const displayAbove = battleActive ? aboveLevels.slice(-3) : aboveLevels;
  const displayBelow = battleActive ? belowLevels.slice(0, 3) : belowLevels;
  const hiddenCount = battleActive
    ? Math.max(0, aboveLevels.length - 3) + Math.max(0, belowLevels.length - 3)
    : 0;

  if (loading) return <div className="text-zinc-500 text-sm p-4">Loading level monitor...</div>;

  const renderLevelRow = (level: MonitoredLevel) => {
    const badge = STATUS_BADGES[level.status] || STATUS_BADGES.watching;
    const catColor = CATEGORY_COLORS[level.category] || 'text-zinc-400';
    const displayDist = -level.distance_ticks;
    const rowCls = level.status === 'at_level' ? 'border-l-2 border-cyan-400' :
                   level.status === 'approaching' ? 'animate-pulse' : '';

    return (
      <tr
        key={level.name}
        className={`${rowCls} cursor-pointer`}
        onClick={() => switchBattleLevel(level.name)}
      >
        <td className="text-right tabular-nums text-zinc-300">
          {level.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
        </td>
        <td>
          {level.name}
          {level.cluster.length > 0 && (
            <span className="text-amber-500 ml-1" title={level.cluster.join(', ')}>
              +{level.cluster.length}
            </span>
          )}
        </td>
        <td className={catColor}>{level.category}</td>
        <td className="text-right tabular-nums">
          {displayDist > 0 ? '+' : ''}{displayDist.toFixed(0)}
        </td>
        <td className="text-center">
          <span className={`px-1.5 py-0.5 text-[10px] ${badge.cls}`}>
            {badge.text}
          </span>
        </td>
      </tr>
    );
  };

  return (
    <div className="flex flex-col h-full gap-2" onClick={unlock}>

      {/* Header strip — matches FilterBar position on betting pages */}
      <div className="flex items-center gap-3 flex-wrap px-4 py-2.5 bg-panel border border-border">
        <TabIcon name="tradingIntraday" color={TAB_COLORS.tradingIntraday} size={16} />
        <span className="text-sm font-semibold text-text">Intraday</span>

        <div className="flex items-center gap-1 text-[10px]">
          <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
          <span className="text-zinc-500">{connected ? 'Live' : 'Offline'}</span>
        </div>

        <div className="flex-1" />

        <span className="text-[9px] text-zinc-600 font-mono">
          {lastRefresh && `${lastRefresh.toLocaleTimeString()}`}
        </span>
        <button onClick={handleRefresh} disabled={isRefreshing}
          className="text-[10px] px-2.5 py-1 border border-zinc-700 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40 transition-colors">
          {isRefreshing ? 'Computing...' : 'Refresh'}
        </button>
      </div>

      {/* Price-centered level table */}
      {levels.length > 0 ? (
        <div className="border-l-2 border-tabTradingScanner">
        <div className="overflow-y-auto" style={{ maxHeight: battleActive ? '240px' : 'calc(100vh - 280px)' }}>
        <table className="sq w-full table-fixed">
          <colgroup>
            <col style={{ width: '20%' }} />
            <col style={{ width: '30%' }} />
            <col style={{ width: '15%' }} />
            <col style={{ width: '15%' }} />
            <col style={{ width: '20%' }} />
          </colgroup>
          <thead className="sticky top-0 z-10 bg-panel">
            <tr>
              <th className="text-right">Price</th>
              <th>Level</th>
              <th>Type</th>
              <th className="text-right">Dist</th>
              <th className="text-center">Status</th>
            </tr>
          </thead>
          <tbody>
            {/* Levels above current price */}
            {displayAbove.map(renderLevelRow)}

            {/* Center price divider row */}
            <tr className="!bg-zinc-800/50 border-y border-tabTradingScanner/40">
              <td colSpan={5} className="!py-2">
                <div className="flex items-center gap-3 px-1">
                  <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
                  <span className="font-mono text-sm text-tabTradingScanner font-bold">
                    NQ {cp.toFixed(2)}
                  </span>
                  <span className="text-zinc-500 text-[10px]">{connected ? 'Live' : 'Offline'}</span>
                  {pricePos?.vwap_deviation_sd != null && (
                    <span className={`text-[10px] font-mono ${
                      Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
                      Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-yellow-400' : 'text-zinc-400'
                    }`}>
                      {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)} SD
                    </span>
                  )}
                </div>
              </td>
            </tr>

            {/* Levels below current price */}
            {displayBelow.map(renderLevelRow)}
          </tbody>
        </table>
        {hiddenCount > 0 && (
          <div className="text-zinc-600 text-center text-[10px] py-1 bg-panel border-t border-border">
            +{hiddenCount} more levels
          </div>
        )}
        </div>
        </div>
      ) : (
        !battleActive && (
          <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm">
            No levels loaded. Click Refresh to compute session.
          </div>
        )
      )}

      {/* Battle Screen */}
      {activeBattle && (
        <BattleScreen
          data={activeBattle}
          onTrade={handleTakeTrade}
          onDismiss={dismissBattle}
        />
      )}

      {/* Position Manager */}
      {positions.length > 0 && (
        <div className="border border-zinc-800 bg-zinc-900/30 p-2">
          <PositionManager
            positions={positions}
            onScale={handleScale}
            onClose={handleClose}
            onHold={() => {}}
            onUpdateStop={() => {}}
          />
        </div>
      )}
    </div>
  );
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Verify dev server renders**

Run: `cd frontend && npm run dev`
Open the Intraday tab — should show the `.sq` table with levels split above/below the center price row.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat(trading): rewrite intraday page with price-centered .sq table"
```

---

### Task 2: Delete LevelMonitorTable.tsx

**Files:**
- Delete: `frontend/src/components/Terminal/pages/LevelMonitorTable.tsx`

- [ ] **Step 1: Verify no other imports of LevelMonitorTable**

Run: `grep -r "LevelMonitorTable" frontend/src/ --include="*.tsx" --include="*.ts"`
Expected: No results (import was removed in Task 1)

- [ ] **Step 2: Delete the file**

```bash
rm frontend/src/components/Terminal/pages/LevelMonitorTable.tsx
```

- [ ] **Step 3: Verify TypeScript still compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add -u frontend/src/components/Terminal/pages/LevelMonitorTable.tsx
git commit -m "refactor(trading): remove LevelMonitorTable (absorbed into TradingIntradayPage)"
```

---

### Task 3: Verify build succeeds

- [ ] **Step 1: Run production build**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 2: Final commit if any fixups needed**

```bash
git add -u
git commit -m "fix(trading): fixups from intraday table refactor"
```
