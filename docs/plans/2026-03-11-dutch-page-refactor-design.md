# Dutch Page Refactor — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the Anchor tab and refactor the Dutch Bets tab with inclusive provider filtering, outcome-count market constraints, and dutch-ratio stake editing.

**Architecture:** Pure frontend refactor of `DutchPage.tsx`. No backend changes. Provider filter becomes inclusive (all providers including sharp). Client-side filtering with outcome-count constraints. Stake editing maintains equal dutch payouts across legs.

**Tech Stack:** React 19, TypeScript, Tailwind CSS

---

### Task 1: Remove Anchor Tab and Simplify Tab Structure

**Files:**
- Modify: `frontend/src/components/Terminal/pages/DutchPage.tsx:12-18,57-88,345-385`

**Step 1: Remove anchor imports and types**

Remove line 12 (`DutchAnchorPage` import from DrainPage), line 15 (`'anchor'` from `DutchTab` union), line 18 (`anchorBetFilter`), and line 88 (`anchorBetsCount` state).

Change line 15 from:
```typescript
type DutchTab = 'dutch' | 'anchor' | 'mybets';
```
to:
```typescript
type DutchTab = 'dutch' | 'mybets';
```

Remove lines 88 and 93 (`anchorBetsCount` state and its fetch).

**Step 2: Simplify the tab bar**

Replace the tab bar (lines 358-377) — remove the anchor tab entry:

```typescript
<div className="flex gap-1 border-b border-border">
  {([
    { id: 'dutch' as DutchTab, label: 'Dutch Bets', count: sortedDutch.length },
    { id: 'mybets' as DutchTab, label: 'My Bets', count: myBetsCount },
  ]).map(tab => (
    <button
      key={tab.id}
      onClick={() => setActiveTab(tab.id)}
      className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
        activeTab === tab.id
          ? 'border-success text-success'
          : 'border-transparent text-muted hover:text-text'
      }`}
    >
      {tab.label}
      {tab.count != null && <span className="ml-1 text-muted">({tab.count})</span>}
    </button>
  ))}
</div>
```

**Step 3: Remove anchor tab rendering**

Remove lines 379-381:
```typescript
{activeTab === 'anchor' && (
  <DutchAnchorPage providers={providers} />
)}
```

**Step 4: Verify the build compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors related to DutchPage

**Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/DutchPage.tsx
git commit -m "feat(dutch): remove anchor tab, simplify to dutch+mybets tabs"
```

---

### Task 2: Make Provider Filter Inclusive (All Providers Including Sharp)

**Files:**
- Modify: `frontend/src/components/Terminal/pages/DutchPage.tsx:121-154`

**Step 1: Update `availableProviders` to include ALL providers (remove `!leg.is_sharp` filter)**

Replace lines 121-129:
```typescript
const availableProviders = useMemo(() => {
  const set = new Set<string>();
  for (const opp of opportunities) {
    for (const leg of opp.legs || []) {
      set.add(leg.provider);
    }
  }
  return Array.from(set).sort();
}, [opportunities]);
```

**Step 2: Update provider filter logic to require ALL legs match**

Replace lines 151-155. The new filter keeps an opp only if EVERY leg's provider is in the selected set:

```typescript
if (selectedProviders.size > 0) {
  result = result.filter(d =>
    (d.legs || []).every(leg => selectedProviders.has(leg.provider))
  );
}
```

**Step 3: Add outcome-count constraint**

Add after the provider filter block (after the code from step 2):

```typescript
// When fewer than 3 providers selected, hide markets with more outcomes than providers
if (selectedProviders.size > 0 && selectedProviders.size < 3) {
  result = result.filter(d => (d.legs || []).length <= selectedProviders.size);
}
```

**Step 4: Verify the build compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

**Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/DutchPage.tsx
git commit -m "feat(dutch): inclusive provider filter with outcome-count constraint"
```

---

### Task 3: Refactor Stake Editing to Dutch-Ratio Mode

**Files:**
- Modify: `frontend/src/components/Terminal/pages/DutchPage.tsx:73-78,207-228,594-635,656-673`

**Step 1: Replace `anchorStake` state with `stakeOverride`**

Replace lines 77-78:
```typescript
// Stake override: key = "oppId|legIdx", value = edited stake for that leg
const [stakeOverride, setStakeOverride] = useState<Record<string, number>>({});
```

**Step 2: Rewrite `getEffectiveStakes` to use dutch-ratio recalculation**

Replace lines 207-228 with:

```typescript
const getEffectiveStakes = (opp: DutchOpp): { totalStake: number; legStakes: number[] } => {
  const legs = opp.legs || [];
  const baseTotalStake = opp.total_stake || 0;

  // Check if any leg has a stake override
  let editedIdx = -1;
  let editedStake = 0;
  for (let i = 0; i < legs.length; i++) {
    const key = `${opp.id}|${i}`;
    if (key in stakeOverride) {
      editedIdx = i;
      editedStake = stakeOverride[key];
      break;
    }
  }

  if (editedIdx >= 0 && legs[editedIdx]) {
    const editedOdds = getEffectiveOdds(opp.id, editedIdx, legs[editedIdx].odds);
    // Dutch ratio: equal payout across all outcomes
    // payout = editedStake * editedOdds
    // otherStake_i = payout / otherOdds_i
    const payout = editedStake * editedOdds;
    const legStakes = legs.map((leg, i) => {
      if (i === editedIdx) return editedStake;
      const odds = getEffectiveOdds(opp.id, i, leg.odds);
      return payout / odds;
    });
    return {
      totalStake: legStakes.reduce((sum, s) => sum + s, 0),
      legStakes,
    };
  }

  return {
    totalStake: baseTotalStake,
    legStakes: legs.map(leg => leg.stake ?? (baseTotalStake > 0 ? baseTotalStake * leg.stake_pct / 100 : 0)),
  };
};
```

**Step 3: Update stake editing UI in expanded rows**

Replace the stake cell (lines 594-635). Change `anchorStake`/`isAnchorLeg` references to use `stakeOverride`:

```typescript
<td className="text-right">
  <div className="flex items-center justify-end gap-1">
    {isEditingThisStake ? (
      <input
        type="number"
        step="1"
        autoFocus
        defaultValue={legStake > 0 ? legStake.toFixed(0) : ''}
        placeholder="Stake"
        className="w-20 bg-bg border border-success/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-success"
        onBlur={(e) => {
          const val = parseFloat(e.target.value);
          if (!isNaN(val) && val > 0) {
            // Clear any other leg overrides for this opp, set this one
            setStakeOverride(prev => {
              const next: Record<string, number> = {};
              // Keep overrides for other opps
              for (const [k, v] of Object.entries(prev)) {
                if (!k.startsWith(`${opp.id}|`)) next[k] = v;
              }
              next[stakeKey] = val;
              return next;
            });
          }
          setEditingStake(null);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          else if (e.key === 'Escape') setEditingStake(null);
        }}
      />
    ) : (
      <span
        onClick={() => setEditingStake(stakeKey)}
        className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-success/50 transition-colors ${isEditedLeg ? 'text-success font-medium border-success/30' : 'text-text border-transparent'}`}
        title="Click to adjust stake (other legs auto-adjust)"
      >
        {legStake > 0 ? `${legStake.toFixed(0)} kr` : '-'}
      </span>
    )}
    {isEditedLeg && (
      <button
        onClick={() => setStakeOverride(prev => {
          const next = { ...prev };
          delete next[stakeKey];
          return next;
        })}
        className="text-muted2 hover:text-text text-[10px]"
        title="Reset to default stake"
      >
        x
      </button>
    )}
  </div>
  {legStake > 0 && <span className="text-muted2 text-[10px]">({leg.stake_pct.toFixed(0)}%)</span>}
</td>
```

Where `isEditedLeg` is computed at the top of the leg map (replacing `isAnchorLeg`):

```typescript
const isEditedLeg = stakeKey in stakeOverride;
```

**Step 4: Update the expanded footer**

Replace `hasAnchor` references in the footer (lines 656-673). Change:
- `hasAnchor` → `hasStakeEdit` (check if any `stakeOverride` key starts with `${opp.id}|`)
- Remove `anchorStake` references

```typescript
const hasStakeEdit = Object.keys(stakeOverride).some(k => k.startsWith(`${opp.id}|`));
```

Then in the footer JSX, replace `hasAnchor` with `hasStakeEdit`.

**Step 5: Verify the build compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

**Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/DutchPage.tsx
git commit -m "feat(dutch): dutch-ratio stake editing — adjust one leg, others auto-recalculate"
```

---

### Task 4: Visual Verification

**Step 1: Start dev servers and verify**

Use `preview_start` for both backend and frontend. Navigate to the Dutch page.

**Step 2: Verify anchor tab is gone**

Only "Dutch Bets" and "My Bets" tabs should appear.

**Step 3: Verify provider filter includes Pinnacle**

Open the Provider dropdown — should list all providers including pinnacle, polymarket.

**Step 4: Test filter behavior**

- Select 2 providers (e.g., unibet + betsson) → only 2-outcome markets shown
- Select 3 providers → 1x2 markets also appear
- Clear filter → all opps shown

**Step 5: Test stake editing**

- Expand a dutch opp
- Click a leg's stake, enter a new value
- Verify other leg(s) update to maintain equal payout
- Verify total stake and guaranteed profit update in footer

**Step 6: Take screenshot as proof**

Use `preview_screenshot` to capture the working state.

**Step 7: Commit any fixes**

If any issues found during verification, fix and commit.
