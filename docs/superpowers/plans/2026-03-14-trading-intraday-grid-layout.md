# Trading Intraday Grid Layout Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the TradingIntradayPage right column from a single scrolling column into a fixed CSS grid with 5 focused panels.

**Architecture:** Replace the current `flex flex-col gap-2 overflow-y-auto` right column with a CSS grid using `grid-rows-[auto_minmax(160px,2fr)_minmax(200px,3fr)]`. Delete `SessionPanel`, replace with horizontal `ContextStrip`. Merge Volume Profiles + VP Anchors into one panel. Add Macro POC row (new data display).

**Tech Stack:** React 19, Tailwind CSS, existing component patterns

**Spec:** `docs/superpowers/specs/2026-03-14-trading-intraday-grid-layout-design.md`

---

## Chunk 1: Grid Layout Restructure

All changes in one file: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

No tests — this is a pure visual/layout refactor with no logic changes. Verification is visual (browser).

---

### Task 1: Create ContextStrip component (replaces SessionPanel)

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx:334-478`

- [ ] **Step 1: Write the ContextStrip component**

Replace `SessionPanel` (lines 334-478) with a new `ContextStrip` that renders all the same data horizontally in three groups separated by dividers.

```tsx
function ContextStrip({ session }: { session: ExpandedSession | null }) {
  const s = session?.session;
  const structure = session?.structure;
  const macro = session?.macro;
  if (!s) return null;

  return (
    <div className="border border-zinc-800 rounded bg-zinc-900/30 px-3 py-2 flex items-start gap-0 text-[11px] font-mono">
      {/* MACRO */}
      {macro && (
        <div className="pr-3 border-r border-zinc-700 mr-3 min-w-0">
          <div className="text-[9px] text-zinc-500 uppercase mb-0.5">Macro</div>
          <div className="flex flex-wrap gap-x-2 gap-y-0.5">
            <span className={macro.regime === 'risk_on' ? 'text-green-400' : macro.regime === 'risk_off' ? 'text-red-400' : 'text-yellow-400'}>
              {macro.regime?.replace('_', ' ')}
            </span>
            {macro.vix != null && (
              <span className={macro.vix < 18 ? 'text-green-400' : macro.vix > 25 ? 'text-red-400' : 'text-yellow-400'}>
                VIX {macro.vix.toFixed(1)}
                {macro.vix_change_pct != null && (
                  <span className="text-zinc-500 text-[9px] ml-0.5">
                    {macro.vix_change_pct > 0 ? '+' : ''}{macro.vix_change_pct.toFixed(1)}%
                  </span>
                )}
              </span>
            )}
            {macro.dxy != null && <span className="text-zinc-300">DXY {macro.dxy.toFixed(1)}</span>}
            {macro.us10y != null && <span className="text-zinc-300">10Y {macro.us10y.toFixed(2)}%</span>}
            {macro.yield_curve_spread != null && (
              <span className={macro.yield_curve_spread > 0 ? 'text-green-400' : 'text-red-400'}>
                2s10s {macro.yield_curve_spread > 0 ? '+' : ''}{macro.yield_curve_spread.toFixed(0)}bp
              </span>
            )}
          </div>
        </div>
      )}

      {/* SESSION */}
      <div className="pr-3 border-r border-zinc-700 mr-3 min-w-0">
        <div className="text-[9px] text-zinc-500 uppercase mb-0.5">Session</div>
        <div className="flex flex-wrap gap-x-2 gap-y-0.5">
          {s.market_type && <span className="text-cyan-400">{s.market_type}</span>}
          {s.opening_type && <span className="text-zinc-300">{s.opening_type}</span>}
          {s.ib_range != null && <span className="text-cyan-300">IB {s.ib_range.toFixed(0)}pt</span>}
          {s.rotation_factor != null && (
            <span className={s.rotation_factor > 0 ? 'text-green-400' : s.rotation_factor < 0 ? 'text-red-400' : 'text-zinc-400'}>
              RF {s.rotation_factor > 0 ? '+' : ''}{s.rotation_factor}
            </span>
          )}
          {s.aspr != null && (
            <span className="text-zinc-300">
              ASPR {s.aspr.toFixed(1)}
              {s.aspr_percentile != null && <span className="text-zinc-500"> P{(s.aspr_percentile * 100).toFixed(0)}</span>}
            </span>
          )}
          {s.distribution_type && <span className="text-zinc-400">{s.distribution_type}</span>}
          {s.value_migration && (
            <span className={s.value_migration === 'up' ? 'text-green-400' : s.value_migration === 'down' ? 'text-red-400' : 'text-zinc-400'}>
              Val {s.value_migration}
            </span>
          )}
          {(s.poor_high || s.poor_low) && (
            <span className="text-orange-400">
              {[s.poor_high && 'PoorH', s.poor_low && 'PoorL'].filter(Boolean).join(' ')}
            </span>
          )}
          {s.single_prints && s.single_prints.length > 0 && (
            <span className="text-yellow-400">SP x{s.single_prints.length}</span>
          )}
        </div>
      </div>

      {/* STRUCTURE */}
      <div className="min-w-0">
        <div className="text-[9px] text-zinc-500 uppercase mb-0.5">Structure</div>
        <div className="flex flex-wrap gap-x-2 gap-y-0.5">
          {structure && (
            <span className={
              structure.structure === 'uptrend' ? 'text-green-400' :
              structure.structure === 'downtrend' ? 'text-red-400' : 'text-yellow-400'
            }>
              {structure.structure === 'uptrend' ? 'HH/HL ↑' :
               structure.structure === 'downtrend' ? 'LH/LL ↓' : 'Ranging ↔'}
            </span>
          )}
          {session?.ml_day_type && (
            <span className="text-purple-400">
              ML: {session.ml_day_type}
              {session.ml_day_type_confidence != null && (
                <span className="text-zinc-500 ml-0.5">{(session.ml_day_type_confidence * 100).toFixed(0)}%</span>
              )}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Delete the old SessionPanel function**

Remove the entire `SessionPanel` function (lines 334-478 in the current file). It is fully replaced by `ContextStrip`.

---

### Task 2: Create VolumeProfilesPanel component

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Write VolumeProfilesPanel component**

This merges the current inline "Volume Profiles" section and "VP Anchors" section into a single panel component. Adds the new Macro POC row.

```tsx
function VolumeProfilesPanel({ session, onAnchorUpdate }: {
  session: ExpandedSession | null;
  onAnchorUpdate: (field: 'vp_leg_start' | 'vp_ongoing_macro_start', value: string) => void;
}) {
  const profiles = session?.profiles;
  if (!profiles) return (
    <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5 flex items-center justify-center text-zinc-600 text-[10px]">
      No profile data
    </div>
  );

  return (
    <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5 flex flex-col min-h-0">
      <div className="text-[10px] text-zinc-500 uppercase mb-1.5">Volume Profiles</div>

      {/* POC table */}
      <div className="space-y-0.5 text-[10px] font-mono flex-1">
        {profiles.session && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Sess</span>
            <span className="text-yellow-400">{profiles.session.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.session.val?.toFixed(0)}-{profiles.session.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.weekly && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Wkly</span>
            <span className="text-yellow-400">{profiles.weekly.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.weekly.val?.toFixed(0)}-{profiles.weekly.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.leg && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Leg</span>
            <span className="text-yellow-400">{profiles.leg.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.leg.val?.toFixed(0)}-{profiles.leg.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.macro && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Macro</span>
            <span className="text-yellow-400">{profiles.macro.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.macro.val?.toFixed(0)}-{profiles.macro.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.developing_poc != null && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Dev</span>
            <span className="text-yellow-400">
              {profiles.developing_poc.toFixed(0)}
              {profiles.developing_poc_direction === 'up' ? ' ↑' : profiles.developing_poc_direction === 'down' ? ' ↓' : ''}
            </span>
          </div>
        )}
      </div>

      {/* VP Anchors — pinned at bottom */}
      <div className="flex gap-3 text-[10px] mt-auto pt-2 border-t border-zinc-800">
        <div className="flex items-center gap-1">
          <span className="text-zinc-500">Leg:</span>
          <input type="date"
            className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
            defaultValue={profiles.leg?.anchor ?? ''}
            onBlur={e => onAnchorUpdate('vp_leg_start', e.target.value)} />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-zinc-500">Macro:</span>
          <input type="date"
            className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
            defaultValue={profiles.macro?.anchor ?? ''}
            onBlur={e => onAnchorUpdate('vp_ongoing_macro_start', e.target.value)} />
        </div>
      </div>
    </div>
  );
}
```

---

### Task 3: Wrap OrderflowPanel in panel chrome

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx:253-331`

- [ ] **Step 1: Add panel border/header wrapper to OrderflowPanel**

The existing `OrderflowPanel` renders content without an outer border. Wrap its return JSX in panel chrome to match the grid:

Change the outer `<div className="space-y-1.5">` in `OrderflowPanel` (line 278) to:

```tsx
<div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5 space-y-1.5">
```

The "ORDERFLOW" header and connection dot are already rendered inside the component (line 279-281), so no additional header is needed.

Also update the empty state (lines 258-264) to include the same panel chrome:

```tsx
return (
  <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5">
    <div className="text-zinc-500 text-[10px] py-2">
      Orderflow {connected ? <span className="text-green-400">● Live</span> : <span className="text-red-400">● Off</span>}
      <div className="mt-1 text-zinc-600">Waiting for data...</div>
    </div>
  </div>
);
```

---

### Task 4: Restructure TradingIntradayPage layout to CSS grid

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx:730-813`

- [ ] **Step 1: Replace the right column layout**

Replace the current right column (lines 746-812):

```tsx
{/* OLD: Single scrolling column */}
<div className="flex-1 min-w-0 flex flex-col gap-2 overflow-y-auto">
  <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5">
    <SessionPanel session={session} />
  </div>
  <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5">
    <OrderflowPanel ... />
  </div>
  {session?.profiles && ( <div>...VP Anchors...</div> )}
  <div>...Signals...</div>
</div>
```

With the new CSS grid layout:

```tsx
{/* RIGHT: Grid panels */}
<div className="flex-1 min-w-0 grid grid-rows-[auto_minmax(160px,2fr)_minmax(200px,3fr)] gap-2 overflow-hidden">

  {/* Row 1: Context Strip */}
  <ContextStrip session={session} />

  {/* Row 2: Orderflow + Volume Profiles */}
  <div className="grid grid-cols-2 gap-2 min-h-0">
    <OrderflowPanel of={indicators?.orderflow} connected={connected} lastTick={lastTick} />
    <VolumeProfilesPanel session={session} onAnchorUpdate={handleAnchorUpdate} />
  </div>

  {/* Row 3: Signals */}
  <div className="border border-zinc-800 rounded bg-zinc-900/30 min-h-0 flex flex-col">
    <div className="sticky top-0 bg-zinc-900 border-b border-zinc-800 px-3 py-1.5 flex items-center justify-between flex-shrink-0">
      <span className="text-xs font-semibold text-text">
        Signals <span className="text-tabTradingScanner">{signals.length}</span>
      </span>
      {signals.length === 0 && (
        <span className="text-[10px] text-zinc-600">Auto-scanning every 5 min (thr 70)</span>
      )}
    </div>

    {signals.length === 0 ? (
      <div className="p-4 text-center text-zinc-600 text-xs">
        No signals above threshold (70). Auto-scanning every 5 min.
      </div>
    ) : (
      <div className="overflow-y-auto flex-1 min-h-0">
        {signals.map(sig => (
          <SignalRow
            key={sig.id}
            sig={sig}
            expanded={expandedSignal === sig.id}
            onToggle={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
            onTakeTrade={handleTakeTrade}
            connected={connected}
            lastTick={lastTick}
          />
        ))}
      </div>
    )}
  </div>
</div>
```

- [ ] **Step 2: Remove the old inline VP Anchors section**

Delete the standalone VP Anchors `<div>` block (lines 759-779 in current file) — this is now inside `VolumeProfilesPanel`.

- [ ] **Step 3: Remove the old inline Volume Profiles rendering from SessionPanel**

Already handled — `SessionPanel` was deleted in Task 1 and replaced by `ContextStrip` (which does not render profiles).

---

### Task 5: Visual verification and commit

- [ ] **Step 1: Start dev server and verify**

Run: `cd frontend && npm run dev`

Open browser at `http://localhost:5173`, navigate to the Intraday tab. Verify:
- Price ladder scrolls independently on the left
- Context strip shows macro/session/structure horizontally with dividers
- Orderflow and Volume Profiles sit side by side in the middle row
- Macro POC row appears in Volume Profiles (new)
- VP Anchor date pickers work (blur triggers update)
- Signals panel fills remaining space and scrolls independently
- Signal expand/collapse and Take Trade still work
- Refresh button still works

- [ ] **Step 2: Test small viewport**

Resize browser window to ~768px height. Verify:
- Panels don't collapse to unreadable sizes (minmax enforces minimums)
- If viewport too small, outer container scrolls

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "refactor(trading): restructure intraday page to fixed grid panel layout

Replace single scrolling column with CSS grid: ContextStrip (horizontal
macro/session/structure), Orderflow panel, Volume Profiles panel (with
new Macro POC row), and Signals panel. Each panel has independent scroll
and clear visual boundaries."
```
