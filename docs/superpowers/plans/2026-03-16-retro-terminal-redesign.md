# Retro Terminal Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin the Firev frontend shell with a Gameboy/retro terminal aesthetic — green-tinted palette, square corners, thick borders, inverted active states.

**Architecture:** Pure visual reskin of 6 files. No new files, no structural changes, no behavior changes. Color tokens in Tailwind + CSS variables form the foundation; component files (Sidebar, TabBar, FilterBar, TerminalWindow) consume them.

**Tech Stack:** React 19, TypeScript, Tailwind CSS 3.4, Vite

**Spec:** `docs/superpowers/specs/2026-03-16-retro-terminal-redesign-design.md`

---

## Chunk 1: Foundation + Shell Components

### Task 1: Color Palette & Global CSS

Update design tokens and global styles. This must land first — all other tasks depend on these colors.

**Files:**
- Modify: `frontend/tailwind.config.js`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Update Tailwind color tokens**

In `frontend/tailwind.config.js`, update `theme.extend.colors`:

```js
bg: '#0a0e0a',
panel: '#131a13',
panel2: '#1a231a',
border: '#2a3a2a',
text: '#d4e0d4',
muted: '#7a9a7a',
muted2: '#5a7a5a',
accentBg: '#0a1a0a',
accentBorder: '#2a3a2a',
tableBorder: '#2a3a2a',
```

Leave ALL other color entries (`accent`, `success`, `warning`, `error`, `yellow`, `calloutBorder`, all `tab*` colors) untouched.

- [ ] **Step 2: Update CSS custom properties**

In `frontend/src/index.css`, update `:root` block to match the same new hex values:

```css
:root {
  --bg: #0a0e0a;
  --panel: #131a13;
  --panel2: #1a231a;
  --border: #2a3a2a;
  --text: #d4e0d4;
  --muted: #7a9a7a;
  --muted2: #5a7a5a;
  --accent: #4FC3F7;
  --accentBg: #0a1a0a;
  --accentBorder: #2a3a2a;
  --success: #4CAF50;
  --error: #EF5350;
  --warning: #FF9800;
  --tableBorder: #2a3a2a;
  --calloutBorder: #4CAF50;
  /* Tab colors unchanged */
  --tab-arb: #22c55e;
  --tab-value: #FF9800;
  --tab-bonus: #A78BFA;
  --tab-bets: #1E88E5;
  --tab-bankroll: #EC4899;
  --tab-profiles: #7C3AED;
  --tab-polymarket: #A855F7;
  --tab-stats: #9AA0A6;
}
```

- [ ] **Step 3: Add global border-radius reset**

In `frontend/src/index.css`, add immediately after the `@tailwind utilities;` line:

```css
/* Retro: square corners everywhere */
*, *::before, *::after {
  border-radius: 0 !important;
}
```

- [ ] **Step 4: Update scrollbar styles**

In `frontend/src/index.css`, update the scrollbar section:

```css
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-thumb {
  background: var(--muted2);
}
```

Track stays: `background: var(--bg);`. Remove the `::-webkit-scrollbar-thumb:hover` rule (no hover effects — retro principle).

- [ ] **Step 5: Update selection highlight**

In `frontend/src/index.css`, update `::selection`:

```css
::selection {
  background-color: rgba(122, 154, 122, 0.3);
  color: #ffffff;
}
```

- [ ] **Step 6: Update table.sq styles**

In `frontend/src/index.css`, replace the entire `table.sq` block with:

```css
/* Square bordered table style — retro data grid */
table.sq {
  border-collapse: collapse;
  width: 100%;
  outline: 2px solid var(--border);
}

table.sq th,
table.sq td {
  border: 1px solid var(--border);
  padding: 8px 12px;
  text-align: left;
  vertical-align: middle;
}

table.sq th {
  background-color: var(--panel2);
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  border-bottom: 2px solid var(--border);
}

table.sq td {
  background-color: var(--bg);
  color: var(--text);
  font-size: 12px;
  line-height: 1.6;
}

table.sq tbody tr:nth-child(even) td {
  background-color: #0f150f;
}

table.sq tr:hover td {
  background-color: var(--panel);
}

table.sq tr:hover td:first-child {
  box-shadow: inset 3px 0 0 var(--muted);
}

table.sq tr.expanded td {
  background-color: var(--panel);
}

table.sq tr.expanded td:first-child {
  box-shadow: inset 3px 0 0 var(--muted);
}
```

- [ ] **Step 7: Verify build compiles**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors. The entire app now has the green-tinted palette and square corners.

- [ ] **Step 8: Commit**

```bash
git add frontend/tailwind.config.js frontend/src/index.css
git commit -m "style: retro terminal foundation — green palette, square corners, chunky tables"
```

---

### Task 2: Sidebar Redesign

Wider sidebar with thick border selection boxes, logo, and terminal separator.

**Files:**
- Modify: `frontend/src/components/Terminal/Sidebar.tsx`

- [ ] **Step 1: Replace entire Sidebar component**

Replace the full content of `Sidebar.tsx` with:

```tsx
import { TabIcon } from './TabBar';

export type TabName = 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'profiles' | 'settings' | 'tradingIntraday' | 'tradingBankroll' | 'tradingStats';
export type CategoryName = 'sports' | 'stocks';

interface SidebarProps {
  activeCategory: CategoryName;
  onCategoryChange: (category: CategoryName) => void;
  onProfileClick: () => void;
  isProfileActive: boolean;
  onSettingsClick: () => void;
  isSettingsActive: boolean;
}

function SidebarButton({
  isActive,
  onClick,
  title,
  children,
}: {
  isActive: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-12 h-12 flex items-center justify-center ${
        isActive
          ? 'border-2 border-text text-text'
          : 'border-2 border-transparent text-muted hover:border-muted hover:text-text'
      }`}
      title={title}
    >
      {children}
    </button>
  );
}

export function Sidebar({ activeCategory, onCategoryChange, onProfileClick, isProfileActive, onSettingsClick, isSettingsActive }: SidebarProps) {
  const isOverlay = isProfileActive || isSettingsActive;

  return (
    <div className="w-16 bg-panel border-r-2 border-border flex flex-col items-center py-4 flex-shrink-0">
      {/* Logo */}
      <div className="mb-4">
        <TabIcon name="app" color="currentColor" size={24} />
      </div>

      {/* Categories */}
      <nav className="flex flex-col gap-1">
        <SidebarButton
          isActive={activeCategory === 'sports' && !isOverlay}
          onClick={() => onCategoryChange('sports')}
          title="Sports"
        >
          <TabIcon name="sports" color="currentColor" size={20} />
        </SidebarButton>
        <SidebarButton
          isActive={activeCategory === 'stocks' && !isOverlay}
          onClick={() => onCategoryChange('stocks')}
          title="Stocks"
        >
          <TabIcon name="stocks" color="currentColor" size={20} />
        </SidebarButton>
      </nav>

      {/* Separator */}
      <div className="flex-1 flex items-center justify-center">
        <span className="text-muted2 text-[10px] select-none">──</span>
      </div>

      {/* Settings */}
      <SidebarButton
        isActive={isSettingsActive}
        onClick={onSettingsClick}
        title="Settings"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </SidebarButton>

      {/* Profile */}
      <SidebarButton
        isActive={isProfileActive}
        onClick={onProfileClick}
        title="Profiles"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      </SidebarButton>
    </div>
  );
}
```

Key changes from current:
- `w-14` → `w-16`, `py-3` → `py-4`, `border-r` → `border-r-2`
- Added BBQ logo at top via `TabIcon name="app"`
- Extracted `SidebarButton` helper with border-based active/hover states (no `bg-panel2`, no `rounded`, no `transition-colors`)
- SVG icons 18→20px
- `──` separator in the flex spacer

- [ ] **Step 2: Verify build compiles**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/Sidebar.tsx
git commit -m "style: retro sidebar — thick borders, logo, terminal separator"
```

---

### Task 3: Tab Bar Redesign

Inverted active tabs, bracket labels, dot icons, uppercase text.

**Files:**
- Modify: `frontend/src/components/Terminal/TabBar.tsx`

- [ ] **Step 1: Replace TabBar component**

In `frontend/src/components/Terminal/TabBar.tsx`, replace only the `TabBar` function (lines 106-134). Keep all imports, types, constants (`SPORTS_TABS`, `STOCKS_TABS`, `TABS_BY_CATEGORY`, `DEFAULT_TAB`, `TAB_COLORS`), and the `TabIcon` component definition untouched.

Replace the `TabBar` function with:

```tsx
export function TabBar({ tabs, activeTab, onTabChange }: TabBarProps) {
  if (tabs.length === 0) return null;

  return (
    <div className="flex items-center gap-1 border-b-2 border-border bg-panel px-3 flex-shrink-0">
      {tabs.map(tab => {
        const isActive = activeTab === tab.name;
        return (
          <button
            key={tab.name}
            onClick={() => onTabChange(tab.name)}
            className={`
              flex items-center gap-1.5 px-4 py-2.5 text-xs font-mono
              uppercase tracking-wider outline-none
              ${isActive ? 'font-bold' : 'text-muted hover:text-text'}
            `}
            style={isActive ? { backgroundColor: tab.color, color: '#0a0e0a' } : undefined}
          >
            <span style={{ color: isActive ? '#0a0e0a' : tab.color }}>●</span>
            <span>{isActive ? `[ ${tab.label} ]` : tab.label}</span>
          </button>
        );
      })}
    </div>
  );
}
```

Key changes:
- `gap-0` → `gap-1`, `px-2` → `px-3`, `border-b` → `border-b-2`
- `px-3 py-2` → `px-4 py-2.5`, added `uppercase tracking-wider`
- Removed `transition-colors duration-150`, `border-b-2 -mb-px` bottom border approach
- Active: inline `backgroundColor: tab.color, color: '#0a0e0a'`, `font-bold`
- Inactive: `text-muted hover:text-text`
- `TabIcon` call replaced with `●` dot character
- Active label: `[ Label ]`, inactive: `Label`

- [ ] **Step 2: Verify build compiles**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/TabBar.tsx
git commit -m "style: retro tab bar — inverted active, brackets, dot icons"
```

---

### Task 4: FilterBar Retro Treatment

Thick borders, text checkboxes, inverted active dropdowns, remove transitions.

**Files:**
- Modify: `frontend/src/components/Terminal/FilterBar.tsx`

This file is large (~500+ lines). The changes are targeted edits, not a full rewrite. Read the file first, then apply these specific changes:

- [ ] **Step 1: Read FilterBar.tsx fully**

Read `frontend/src/components/Terminal/FilterBar.tsx` to understand current structure before making edits.

- [ ] **Step 2: Update MultiSelectDropdown trigger button (line ~112-137)**

Current trigger button (line 114):
```tsx
className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] transition-all duration-150 ${
  hasFilter ? '' : 'bg-panel2 text-muted hover:text-text hover:bg-panel2/80'
}`}
style={hasFilter ? { background: `${hex}15`, color: hex, fontWeight: 500 } : undefined}
```

Replace with:
```tsx
className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] border-2 ${
  hasFilter ? 'font-medium' : 'border-border bg-panel2 text-muted hover:text-text'
}`}
style={hasFilter ? { backgroundColor: hex, color: '#0a0e0a', borderColor: hex } : undefined}
```

Key changes: removed `transition-all duration-150`, added `border-2`, inactive gets `border-border`, active gets inverted (accent bg, dark text).

Also remove `transition-transform duration-150` from the chevron SVG on line 128.

- [ ] **Step 3: Update dropdown popover (line ~141)**

Current:
```tsx
<div className="absolute top-full left-0 mt-1 z-50 w-56 bg-panel border border-border shadow-xl shadow-black/30 overflow-hidden">
```

Replace with:
```tsx
<div className="absolute top-full left-0 mt-1 z-50 w-56 bg-panel border-2 border-border overflow-hidden">
```

Changes: `border` → `border-2`, removed `shadow-xl shadow-black/30`.

- [ ] **Step 4: Replace custom checkbox with text characters (lines ~188-201)**

Current checkbox is a styled `<span>` with SVG checkmark (NOT a native `<input type="checkbox">`). Replace lines 188-201:

Current:
```tsx
{/* Checkbox */}
<span
  className={`w-[18px] h-[18px] rounded-[4px] border-2 flex items-center justify-center shrink-0 transition-all duration-150 ${
    isActive ? 'border-transparent' : 'border-muted/40 hover:border-muted/60'
  }`}
  style={isActive ? { background: hex, borderColor: hex } : undefined}
>
  {isActive && (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
      <path d="M2.5 6L5 8.5L9.5 3.5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )}
</span>
```

Replace with:
```tsx
{/* Checkbox */}
<span
  className={`font-mono text-xs shrink-0 ${isActive ? '' : 'text-muted'}`}
  style={isActive ? { color: hex } : undefined}
>
  {isActive ? '[x]' : '[ ]'}
</span>
```

Also on line 187, remove `transition-colors` from the parent button.

- [ ] **Step 5: Remove transitions from all sub-components**

Search the entire file for `transition-all duration-150`, `transition-colors`, and `transition-transform` classes. Remove them from:
- Select all / Clear buttons (lines ~161, 168): remove `transition-colors`
- Option row buttons (line ~187): remove `transition-colors`
- MultiSelectPills button classes: remove any `transition-*`
- SingleSelectPills button classes: remove any `transition-*`
- SearchInput collapsed button: remove `transition-colors`
- Any other interactive element in the file

- [ ] **Step 6: Update search input border (line ~151)**

Current:
```tsx
className="w-full px-2.5 py-1.5 text-[11px] bg-panel2 border border-border text-text
  placeholder:text-muted2 focus:outline-none focus:border-muted"
```

Replace `border border-border` with `border-2 border-border`.

- [ ] **Step 7: Verify build compiles**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/Terminal/FilterBar.tsx
git commit -m "style: retro filter bar — text checkboxes, thick borders, no transitions"
```

---

### Task 5: TerminalWindow Chrome

Update content padding and loading state.

**Files:**
- Modify: `frontend/src/components/Terminal/TerminalWindow.tsx`

- [ ] **Step 1: Update content padding**

In `TerminalWindow.tsx`, find the content area div (line ~145):
```tsx
<div className="flex-1 overflow-y-auto p-3">
```
Change `p-3` to `p-4`.

- [ ] **Step 2: Update loading fallback**

Find the Suspense fallback (line ~146):
```tsx
<Suspense fallback={<div className="p-4 text-muted text-sm">Loading...</div>}>
```
Replace with:
```tsx
<Suspense fallback={<div className="p-4 text-muted text-sm animate-blink">█</div>}>
```

- [ ] **Step 3: Verify build compiles**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/TerminalWindow.tsx
git commit -m "style: retro terminal chrome — padding, blinking cursor loader"
```

---

### Task 6: Visual Verification

Use the dev server to verify the retro redesign looks correct.

- [ ] **Step 1: Start dev server and take screenshots**

Start the frontend dev server and use Claude Preview or Playwright to screenshot:
1. The sidebar + tab bar (any tab)
2. A page with a data table (Value tab if data available)
3. The FilterBar with a dropdown open

Verify:
- Green-tinted dark background (not pure black)
- Square corners on EVERYTHING (no rounded anywhere)
- Sidebar: visible border box on active category, BBQ logo at top, `──` separator
- Tab bar: active tab has colored background with dark text, bracket label `[ Soft ]`
- Tables: thicker borders, more padding, alternating row shading, left indicator on hover
- No box-shadows anywhere
- Existing accent colors (orange, purple, green, etc.) pop against the green base

- [ ] **Step 2: Final commit if any fixes needed**

If visual issues found, fix and commit with descriptive message.
