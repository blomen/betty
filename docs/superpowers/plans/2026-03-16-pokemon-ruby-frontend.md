# Pokemon Ruby GBA-Style Frontend Redesign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the Firev frontend from a dark terminal UI into a full Pokemon Ruby GBA aesthetic with dialogue-box chrome, pixel fonts, GBA color palette, and tile-textured backgrounds.

**Architecture:** CSS-first approach — most changes are in `index.css` (variables, new classes) and `tailwind.config.js` (tokens). Component changes are primarily class additions and hardcoded color swaps. No logic changes.

**Tech Stack:** React 19, TypeScript, Tailwind CSS, CSS custom properties, Press Start 2P font (bundled locally)

**Spec:** `docs/superpowers/specs/2026-03-16-pokemon-ruby-frontend-design.md`

---

## Chunk 1: Foundation (CSS Variables, Font, Tailwind Config)

### Task 1: Download and bundle pixel font

**Files:**
- Create: `frontend/public/fonts/PressStart2P-Regular.woff2`

- [ ] **Step 1: Download Press Start 2P font**

```bash
cd frontend/public && mkdir -p fonts
curl -L "https://fonts.gstatic.com/s/pressstart2p/v15/e3t4euO8T-267oIAQAu6jDQyK3nVivM.woff2" -o fonts/PressStart2P-Regular.woff2
```

- [ ] **Step 2: Verify font file exists and is non-empty**

```bash
ls -la frontend/public/fonts/PressStart2P-Regular.woff2
```
Expected: File exists, ~12-15KB

- [ ] **Step 3: Commit**

```bash
git add frontend/public/fonts/PressStart2P-Regular.woff2
git commit -m "feat: bundle Press Start 2P pixel font for GBA theme"
```

---

### Task 2: Update CSS variables and add GBA foundation classes

**Files:**
- Modify: `frontend/src/index.css`

This is the biggest single change — all CSS variables, new `.gba-panel` class, font-face, tile overlay, scrollbar, selection, flash animations, table styles, and loading spinner.

- [ ] **Step 1: Replace CSS variable block in `:root`**

In `index.css`, find the `:root` block (lines ~10-35) and replace all color variables with GBA palette:

```css
:root {
  --bg: #182028;
  --panel: #283848;
  --panel2: #384858;
  --border: #e8e0c8;
  --text: #f8f8f0;
  --muted: #88a0b0;
  --muted2: #607080;
  --accent: #58a8f8;
  --accentBg: #1e2c3c;
  --accentBorder: #58a8f8;
  --success: #78c850;
  --error: #f85848;
  --warning: #f8d030;
  --tableBorder: #384858;
  --calloutBorder: #78c850;

  --tab-arb: #78c850;
  --tab-value: #f8a830;
  --tab-bonus: #a878f8;
  --tab-bets: #58a8f8;
  --tab-bankroll: #f878a8;
  --tab-profiles: #9858f8;
  --tab-polymarket: #b868f8;
  --tab-stats: #88a0b0;
  --tab-extract: #68b0f8;
  --tab-reverse: #f85848;
  --tab-tradingBankroll: #f878a8;
  --tab-tradingToday: #f8d030;
  --tab-tradingBuilder: #78c850;
  --tab-tradingTrades: #58a8f8;
  --tab-tradingJournal: #a878f8;
  --tab-tradingIntraday: #48c8e0;
}
```

- [ ] **Step 2: Add font-face and font variables**

Add at the top of `index.css` (before the `:root` block):

```css
@font-face {
  font-family: 'Press Start 2P';
  src: url('/fonts/PressStart2P-Regular.woff2') format('woff2');
  font-display: swap;
}
```

Add inside `:root`:
```css
  --font-pixel: 'Press Start 2P', cursive;
  --font-data: 'JetBrains Mono', 'Cascadia Code', 'SF Mono', 'Fira Code', Consolas, ui-monospace, monospace;
```

- [ ] **Step 3: Add `.gba-panel` utility class**

Add after the `:root` block:

```css
.gba-panel {
  background: var(--panel);
  border: 4px solid var(--border);
  box-shadow:
    inset 0 0 0 2px #a8a088,
    inset 2px 2px 0 4px #384858,
    inset -2px -2px 0 4px #182028;
}
```

- [ ] **Step 4: Add `.gba-header` and `.gba-label` classes**

```css
.gba-header {
  font-family: var(--font-pixel);
  font-size: 16px;
  color: var(--text);
  letter-spacing: 1px;
}

.gba-label {
  font-family: var(--font-pixel);
  font-size: 8px;
  color: var(--text);
  letter-spacing: 0.5px;
}
```

- [ ] **Step 5: Add tile overlay on body::before**

```css
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
  background-size: 16px 16px;
  pointer-events: none;
  z-index: -1;
}
```

- [ ] **Step 6: Update scrollbar styling**

Find the scrollbar section (~lines 69-81) and update:
- `::-webkit-scrollbar-thumb` background → `#a8a088`
- `::-webkit-scrollbar-track` background → `#182028`

- [ ] **Step 7: Update `::selection` styling**

Replace `rgba(79, 195, 247, 0.3)` with `rgba(88, 168, 248, 0.3)`.

- [ ] **Step 8: Update flash animations**

Replace `.flash-up` colors:
- `color: #4ade80` → `color: #78c850`
- `background: rgba(74, 222, 128, 0.15)` → `background: rgba(120, 200, 80, 0.15)`

Replace `.flash-down` colors:
- `color: #f87171` → `color: #f85848`
- `background: rgba(248, 113, 113, 0.15)` → `background: rgba(248, 88, 72, 0.15)`

- [ ] **Step 9: Update `.sq` table styles**

Update these specific rules:
- `table.sq tbody tr:nth-child(even) td` background: `#0f0f0f` → `#1e2c3c`
- `table.sq th` — add `font-family: var(--font-pixel); font-size: 8px; color: #f8d030;`
- `table.sq tr:hover td` — add/update hover to include cursor indicator:
  ```css
  table.sq tbody tr:hover td:first-child::before {
    content: '▶';
    position: absolute;
    left: 2px;
    color: var(--accent);
    font-size: 10px;
  }
  table.sq tbody tr:hover td:first-child {
    position: relative;
    padding-left: 16px;
  }
  ```

- [ ] **Step 10: Add Pokeball-style loading spinner**

```css
.gba-spinner {
  width: 16px;
  height: 16px;
  border: 3px solid #f8f8f0;
  border-top-color: #f85848;
  border-radius: 50%;
  animation: gba-spin 0.8s linear infinite;
}

@keyframes gba-spin {
  to { transform: rotate(360deg); }
}
```

- [ ] **Step 11: Verify the frontend compiles**

```bash
cd frontend && npm run build
```
Expected: Build succeeds with no errors.

- [ ] **Step 12: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat: GBA color palette, gba-panel chrome, pixel font classes, tile overlay"
```

---

### Task 3: Update Tailwind config

**Files:**
- Modify: `frontend/tailwind.config.js`

- [ ] **Step 1: Update all color tokens to GBA palette**

Replace the colors in the `extend.colors` object:

```js
colors: {
  bg: '#182028',
  panel: '#283848',
  panel2: '#384858',
  border: '#e8e0c8',
  text: '#f8f8f0',
  muted: '#88a0b0',
  muted2: '#607080',
  accent: '#58a8f8',
  accentBg: '#1e2c3c',
  accentBorder: '#58a8f8',
  success: '#78c850',
  error: '#f85848',
  warning: '#f8d030',
  yellow: '#f8d030',
  tableBorder: '#384858',
  calloutBorder: '#78c850',
  tabExtract: '#68b0f8',
  tabArb: '#78c850',
  tabValue: '#f8a830',
  tabBonus: '#a878f8',
  tabBets: '#58a8f8',
  tabBankroll: '#f878a8',
  tabProfiles: '#9858f8',
  tabPolymarket: '#b868f8',
  tabReverse: '#f85848',
  tabStats: '#88a0b0',
  tabTradingBankroll: '#f878a8',
  tabTradingToday: '#f8d030',
  tabTradingBuilder: '#78c850',
  tabTradingTrades: '#58a8f8',
  tabTradingJournal: '#a878f8',
  tabTradingIntraday: '#48c8e0',
},
```

- [ ] **Step 2: Add pixel font family**

Add to `extend.fontFamily`:
```js
pixel: ['"Press Start 2P"', 'cursive'],
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/tailwind.config.js
git commit -m "feat: update Tailwind tokens to GBA palette, add pixel font family"
```

---

## Chunk 2: Navigation Components (TabBar, Sidebar)

### Task 4: Restyle TabBar with GBA menu look

**Files:**
- Modify: `frontend/src/components/Terminal/TabBar.tsx`

- [ ] **Step 1: Update SPORTS_TABS colors**

```typescript
const SPORTS_TABS: Tab[] = [
  { name: 'polymarket', label: 'Poly',      color: '#b868f8' },
  { name: 'value',      label: 'Soft',      color: '#f8a830' },
  { name: 'reverse',    label: 'Pinnacle',  color: '#f85848' },
  { name: 'dutch',      label: 'Dutch',     color: '#78c850' },
  { name: 'bankroll',   label: 'Bankroll',  color: '#f878a8' },
  { name: 'stats',      label: 'Stats',     color: '#58a8f8' },
  { name: 'postmortem', label: 'PM',        color: '#48c8a0' },
];
```

- [ ] **Step 2: Update STOCKS_TABS colors**

```typescript
const STOCKS_TABS: Tab[] = [
  { name: 'tradingIntraday', label: 'Intraday', color: '#48c8e0' },
  { name: 'tradingBankroll', label: 'Bankroll', color: '#f878a8' },
  { name: 'tradingStats',    label: 'Stats',    color: '#58a8f8' },
];
```

- [ ] **Step 3: Update TAB_COLORS map**

```typescript
export const TAB_COLORS: Record<string, string> = {
  value: '#f8a830',
  dutch: '#78c850',
  reverse: '#f85848',
  polymarket: '#b868f8',
  stats: '#58a8f8',
  bankroll: '#f878a8',
  specials: '#a878f8',
  bets: '#58a8f8',
  profiles: '#a878f8',
  settings: '#88a0b0',
  success: '#78c850',
  postmortem: '#48c8a0',
  tradingIntraday: '#48c8e0',
  tradingBankroll: '#f878a8',
  tradingStats: '#58a8f8',
};
```

- [ ] **Step 4: Restyle TabBar component rendering**

In the TabBar component JSX (around line 107-132), make these specific changes:

**Container:** Find the outer `<div>` with `border-b border-border`. Replace its className/style with:
```tsx
<div className="gba-panel flex gap-1 px-3" style={{ background: 'linear-gradient(180deg, #3060a0, #284878)' }}>
```

**Active tab:** Find where `[ ${tab.label} ]` is rendered. Replace with:
```tsx
<span style={{ fontFamily: 'var(--font-pixel)', fontSize: '8px', color: '#f8f8f0' }}>
  ▶ {tab.label}
</span>
```
Set the active tab button background to `backgroundColor: tab.color + '33'` (20% opacity hex).

**Inactive tab:** Replace the inactive tab text (remove brackets) with:
```tsx
<span style={{ fontFamily: 'var(--font-pixel)', fontSize: '8px', color: '#88a0b0' }}>
  {'  '}{tab.label}
</span>
```

**Dot indicator:** Remove the `●` colored dot — the `▶` cursor replaces it.

- [ ] **Step 5: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/TabBar.tsx
git commit -m "feat: GBA-style TabBar with blue gradient, pixel font, arrow cursor"
```

---

### Task 5: Restyle Sidebar with GBA panel chrome

**Files:**
- Modify: `frontend/src/components/Terminal/Sidebar.tsx`

- [ ] **Step 1: Add `.gba-panel` to sidebar container**

Find the main sidebar `<div>` (around line 45) with class `w-16 bg-panel border-r-2 border-border...` and:
- Replace `border-r-2 border-border` with the `gba-panel` class
- Keep `w-16 flex flex-col items-center py-4`

- [ ] **Step 2: Update category button styles**

In `SidebarButton` component (~line 15-39):
- Active state: Replace `border-text text-text` with a solid accent fill style: `backgroundColor: '#58a8f8', color: '#182028', border: '2px solid #e8e0c8'`
- Inactive state: Replace `border-transparent text-muted` with `border: '2px solid #607080', color: '#88a0b0'`
- Hover: `borderColor: '#e8e0c8', color: '#f8f8f0'`

- [ ] **Step 3: Replace sidebar icons with pixel-style equivalents**

Replace the SVG icons in the sidebar:
- **Sports category button**: Replace SVG with a Pokeball-inspired icon. Use a simple `<div>` with CSS: 12x12 circle, top half `bg-[#f85848]`, bottom half `bg-[#f8f8f0]`, 2px center line `bg-[#283848]`, center dot 4px `bg-[#283848]`. Or use Unicode: `<span style={{ fontFamily: 'var(--font-pixel)', fontSize: '16px' }}>⚾</span>`
- **Stocks category button**: Replace SVG with a pixel chart icon: `<span style={{ fontFamily: 'var(--font-pixel)', fontSize: '12px' }}>📈</span>` or a simple CSS stepped line.
- **Settings icon**: Replace SVG with `<span className="gba-label">⚙</span>`
- **Profile icon**: Replace SVG with `<span className="gba-label">▲</span>`

- [ ] **Step 4: Add `image-rendering: pixelated` to logo**

Find the logo element (~line 47-49) and add `style={{ imageRendering: 'pixelated' }}`.

- [ ] **Step 5: Update separator style**

Find the separator (~line 71). Change from dashes to a pixel-style double line or keep as-is but update color to `text-[#607080]`.

- [ ] **Step 6: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Terminal/Sidebar.tsx
git commit -m "feat: GBA-panel sidebar with pixel-style icons and buttons"
```

---

### Task 6: Update TerminalWindow layout with GBA panels

**Files:**
- Modify: `frontend/src/components/Terminal/TerminalWindow.tsx`

- [ ] **Step 1: Add `.gba-panel` to main content area**

Find the content wrapper div (~line 133-164). Add `gba-panel` class to the main content area that wraps TabBar + page content (the `flex-1 flex flex-col min-w-0` div).

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/TerminalWindow.tsx
git commit -m "feat: GBA-panel chrome on main content area"
```

---

## Chunk 3: Filter Components & Shared Components

### Task 7: Update FilterBar colors and styling

**Files:**
- Modify: `frontend/src/components/Terminal/FilterBar.tsx`

- [ ] **Step 1: Update ACCENT_COLORS map**

Replace the `ACCENT_COLORS` map (~lines 16-24):

```typescript
const ACCENT_COLORS: Record<string, string> = {
  tabValue: '#f8a830',
  tabBonus: '#a878f8',
  tabArb: '#78c850',
  tabBets: '#58a8f8',
  tabExtract: '#68b0f8',
  success: '#78c850',
  tabReverse: '#f85848',
};
```

- [ ] **Step 2: Add `.gba-panel` to dropdown menus**

In `MultiSelectDropdown` component, find the dropdown container div (the portal-rendered popup). Add `gba-panel` class to it, replacing existing `bg-panel border-2 border-border` styling.

- [ ] **Step 3: Update dropdown item hover to show `▶` cursor**

Add to dropdown list items on hover: a `::before` pseudo-element via inline style or a new CSS class. Simplest: add `▶ ` text prefix to hovered items via state, or use CSS:
```css
.gba-dropdown-item:hover::before {
  content: '▶ ';
  color: var(--accent);
}
```
Add this class to `index.css` and apply it to dropdown items.

- [ ] **Step 4: Update hardcoded color references**

Replace `'#0a0e0a'` (dark text on colored background) → `'#182028'` throughout FilterBar.

- [ ] **Step 5: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/FilterBar.tsx frontend/src/index.css
git commit -m "feat: GBA-styled FilterBar with updated accent colors and panel chrome"
```

---

### Task 8: Update MyBetsSection

**Files:**
- Modify: `frontend/src/components/Terminal/MyBetsSection.tsx`

- [ ] **Step 1: Update fallback color**

Change the fallback color on line ~37:
- `'#64748B'` → `'#88a0b0'` (matches `--muted`)

- [ ] **Step 2: Verify no other hardcoded colors need updating**

The component uses `TAB_COLORS` from TabBar (already updated in Task 4) and Tailwind classes that reference CSS variables. Scan for any remaining hardcoded hex values.

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/MyBetsSection.tsx
git commit -m "feat: update MyBetsSection fallback color for GBA palette"
```

---

### Task 8b: Update ErrorBoundary and ErrorNotificationBar

**Files:**
- Modify: `frontend/src/components/ErrorBoundary.tsx`
- Modify: `frontend/src/components/Terminal/ErrorNotificationBar.tsx` (if exists with hardcoded colors)

- [ ] **Step 1: Update ErrorBoundary**

ErrorBoundary uses Tailwind classes like `bg-terminal-bg`, `text-terminal-error`, etc. Verify these map through to the updated CSS variables. If it uses hardcoded hex colors, replace them with GBA equivalents. Add `gba-panel` class to the error container div.

- [ ] **Step 2: Update ErrorNotificationBar**

Check for hardcoded colors and replace with GBA palette equivalents. The notification bar should use `--error` (`#f85848`) background tint and `--text` color.

- [ ] **Step 3: Verify build & commit**

```bash
cd frontend && npm run build
git add frontend/src/components/ErrorBoundary.tsx frontend/src/components/Terminal/ErrorNotificationBar.tsx
git commit -m "feat: GBA styling for error components"
```

---

## Chunk 4: Page Components — Sports Pages

### Task 9: Update SettingsPage hardcoded colors

**Files:**
- Modify: `frontend/src/components/Terminal/pages/SettingsPage.tsx`

- [ ] **Step 1: Update TIER_BADGES**

Replace (~lines 6-10):
```typescript
const TIER_BADGES: Record<string, string> = {
  sharp: 'text-[#f8d030] bg-[#f8d030]/10',
  api_soft: 'text-[#58a8f8] bg-[#58a8f8]/10',
  browser_soft: 'text-[#f8a830] bg-[#f8a830]/10',
};
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/SettingsPage.tsx
git commit -m "feat: GBA palette for SettingsPage tier badges"
```

---

### Task 10: Update PostmortemPage colors

**Files:**
- Modify: `frontend/src/components/Terminal/pages/PostmortemPage.tsx`

- [ ] **Step 1: Verify PostmortemPage uses Tailwind semantic classes**

The `CLASSIFICATION_COLORS` and severity icons use `text-success`, `text-error`, `text-yellow`, `text-purple` — these are Tailwind classes that pull from the config (already updated in Task 3). **No changes needed** unless there are hardcoded hex values.

Scan for any hardcoded hex — if none found, skip this task.

- [ ] **Step 2: Verify build (if changes made)**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit (only if changes made)**

---

## Chunk 5: Page Components — Trading Pages

### Task 11: Update BattleScreen.tsx trading UI colors

**Files:**
- Modify: `frontend/src/components/Terminal/pages/BattleScreen.tsx`

This file has the most hardcoded Tailwind color classes (cyan, emerald, zinc, red, amber). Map them to GBA equivalents:

| Current Tailwind | GBA Replacement |
|-----------------|-----------------|
| `cyan-400/800/900` | `[#58a8f8]` / `[#1e2c3c]` / `[#182028]` |
| `emerald-400/800/900` | `[#78c850]` / `[#284020]` / `[#1e3018]` |
| `red-400/800/900` | `[#f85848]` / `[#582020]` / `[#401818]` |
| `amber-400/900` | `[#f8d030]` / `[#403010]` |
| `zinc-300/400/500/600/700/800/900` | `[#f8f8f0]` / `[#88a0b0]` / `[#607080]` / `[#506070]` / `[#384858]` / `[#283848]` / `[#182028]` |

- [ ] **Step 1: Replace all Tailwind color classes in BattleScreen**

Apply the mapping table above. Replace every instance of `text-cyan-400` → `text-[#58a8f8]`, `border-cyan-800` → `border-[#1e2c3c]`, `bg-zinc-900/80` → `bg-[#182028]/80`, etc.

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/BattleScreen.tsx
git commit -m "feat: GBA palette for BattleScreen trading UI"
```

---

### Task 12: Update GaugeBar.tsx colors

**Files:**
- Modify: `frontend/src/components/Terminal/pages/GaugeBar.tsx`

- [ ] **Step 1: Update COLOR_MAP**

Replace (~lines 13-18):
```typescript
const COLOR_MAP = {
  green: { bar: 'bg-[#78c850]', text: 'text-[#78c850]', label: 'text-[#78c850]' },
  red: { bar: 'bg-[#f85848]', text: 'text-[#f85848]', label: 'text-[#f85848]' },
  amber: { bar: 'bg-[#f8d030]', text: 'text-[#f8d030]', label: 'text-[#f8d030]' },
  dim: { bar: 'bg-[#607080]', text: 'text-[#607080]', label: 'text-[#607080]' },
};
```

- [ ] **Step 2: Update gauge container colors**

Replace `bg-zinc-800 border border-zinc-700` → `bg-[#283848] border border-[#384858]`.

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/GaugeBar.tsx
git commit -m "feat: GBA palette for GaugeBar"
```

---

### Task 13: Update LevelMonitorTable.tsx colors

**Files:**
- Modify: `frontend/src/components/Terminal/pages/LevelMonitorTable.tsx`

- [ ] **Step 1: Update STATUS_STYLES, STATUS_BADGES, CATEGORY_COLORS**

```typescript
const STATUS_STYLES: Record<string, string> = {
  watching: 'text-[#607080]',
  approaching: 'text-[#f8d030] animate-pulse',
  at_level: 'text-[#58a8f8] font-bold border-l-2 border-[#58a8f8]',
  triggered: 'text-[#78c850]',
  rejected: 'text-[#607080]',
};

const STATUS_BADGES: Record<string, string> = {
  watching: 'bg-[#283848] text-[#607080]',
  approaching: 'bg-[#f8d030]/10 text-[#f8d030]',
  at_level: 'bg-[#58a8f8]/10 text-[#58a8f8]',
  triggered: 'bg-[#78c850]/10 text-[#78c850]',
  rejected: 'bg-[#283848] text-[#607080]',
};

const CATEGORY_COLORS: Record<string, string> = {
  session: 'text-[#58a8f8]',
  band: 'text-[#a878f8]',
  prior: 'text-[#f8d030]',
  structure: 'text-[#48c8e0]',
  overnight: 'text-[#88a0b0]',
};
```

- [ ] **Step 2: Update any remaining zinc/gray references in JSX**

Replace `text-zinc-500` → `text-[#607080]`, `text-zinc-300` → `text-[#f8f8f0]`, etc.

- [ ] **Step 3: Verify build & commit**

```bash
cd frontend && npm run build
git add frontend/src/components/Terminal/pages/LevelMonitorTable.tsx
git commit -m "feat: GBA palette for LevelMonitorTable"
```

---

### Task 14: Update PositionManager.tsx colors

**Files:**
- Modify: `frontend/src/components/Terminal/pages/PositionManager.tsx`

- [ ] **Step 1: Replace Tailwind color classes**

Apply same mapping as BattleScreen (Task 11):
- `text-emerald-400` → `text-[#78c850]`
- `text-red-400` → `text-[#f85848]`
- `bg-emerald-900/50` → `bg-[#78c850]/10`
- `bg-red-900/50` → `bg-[#f85848]/10`
- `bg-cyan-900/50` → `bg-[#58a8f8]/10`
- `text-cyan-300` → `text-[#58a8f8]`
- `bg-zinc-800` → `bg-[#283848]`
- `text-zinc-400` → `text-[#88a0b0]`
- `text-zinc-300` → `text-[#f8f8f0]`
- `text-white` → `text-[#f8f8f0]`

- [ ] **Step 2: Verify build & commit**

```bash
cd frontend && npm run build
git add frontend/src/components/Terminal/pages/PositionManager.tsx
git commit -m "feat: GBA palette for PositionManager"
```

---

## Chunk 6: Remaining Pages & Final Polish

### Task 15: Add `.gba-panel` wrappers to all page components

**Files:**
- Modify: All files in `frontend/src/components/Terminal/pages/`

For UI uniformity, every page's outermost `<div>` should get the `.gba-panel` class. This applies to:

- ValuePage.tsx
- DutchPage.tsx
- ReversePage.tsx
- PolymarketPage.tsx
- BankrollPage.tsx
- BetsPage.tsx
- StatsPage.tsx
- ProfilePage.tsx
- SettingsPage.tsx
- PostmortemPage.tsx
- TradingIntradayPage.tsx
- TradingStatsPage.tsx
- TradingBankrollPage.tsx
- DrainPage.tsx
- WelcomePage.tsx

- [ ] **Step 1: Add `gba-panel` class to each page's outermost div**

For each page file, find the return statement's outermost `<div>` and add `className="gba-panel"` (or append to existing className). If the page uses `<div className="space-y-4">` or similar, wrap it in a new `<div className="gba-panel">`.

**Note:** TerminalWindow (Task 6) adds `.gba-panel` to the outer content area. Individual pages should NOT double-wrap their outermost div. Instead, `.gba-panel` goes on **inner sections** — specifically the table wrapper divs and any card/section containers within the page. The outer content area provides the main chrome; inner panels create the nested dialogue-box look for tables and filter sections.

- [ ] **Step 2: Wrap each page's main table in `.gba-panel`**

For pages with `<table className="sq">`, ensure the table's parent `<div>` has `.gba-panel`. If the table is directly inside the page div, wrap it:
```tsx
<div className="gba-panel overflow-x-auto">
  <table className="sq">...</table>
</div>
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/
git commit -m "feat: GBA-panel wrappers on all page components for UI uniformity"
```

---

### Task 16: Scan for remaining old colors and fix

**Files:**
- Potentially any frontend file

- [ ] **Step 1: Grep for old hex colors**

```bash
cd frontend/src
grep -rn "#0a0a0a\|#141414\|#1c1c1c\|#2a2a2a\|#d4d4d4\|#737373\|#525252\|#4FC3F7\|#111111\|#4CAF50\|#EF5350\|#FF9800\|#0f0f0f\|#A855F7\|#EC4899\|#1E88E5\|#10b981\|#14B8A6\|#06B6D4\|#A78BFA\|#7C3AED\|#22c55e\|#9AA0A6\|#60a5fa\|#FACC15\|#64748B" --include="*.tsx" --include="*.ts" --include="*.css"
```

- [ ] **Step 2: Fix any remaining old color references**

For each hit, replace with the corresponding GBA palette color from the spec.

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add -u frontend/src/
git commit -m "fix: replace remaining old color references with GBA palette"
```

---

### Task 17: Visual verification

- [ ] **Step 1: Start dev server and verify**

```bash
cd frontend && npm run dev
```

Open http://localhost:5173 and verify:
- GBA color palette applied globally (dark navy backgrounds, cream borders)
- `.gba-panel` dialogue box chrome visible on sidebar, tab bar, tables
- Pixel font on tab labels, headers, buttons
- Monospace font on table data
- Tile grid overlay visible on background (subtle)
- Tab bar has blue gradient and `▶` cursor on active tab
- Table hover shows `▶` cursor
- Flash animations use new green/red colors
- Scrollbar matches theme

- [ ] **Step 2: Commit any visual fixes**

```bash
git add -u frontend/src/
git commit -m "fix: visual polish after GBA theme verification"
```
