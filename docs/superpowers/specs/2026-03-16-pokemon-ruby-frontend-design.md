# Pokemon Ruby GBA-Style Frontend Redesign

**Date:** 2026-03-16
**Status:** Approved
**Scope:** Full pixel-art immersion — reskin the entire Firev frontend to feel like a Pokemon Ruby GBA interface

## Overview

Transform the current dark terminal UI into a full Pokemon Ruby / GBA aesthetic. Every panel becomes a GBA dialogue box, navigation feels like Pokemon menus, colors shift to the saturated 16-bit GBA palette, and pixel fonts appear on all chrome elements. Data tables retain monospace for readability.

## 1. Color Palette

### CSS Variables (in `index.css`)

| Token | Current | New | Source |
|-------|---------|-----|--------|
| `--bg` | `#0a0a0a` | `#182028` | Ruby night/indoor scenes |
| `--panel` | `#141414` | `#283848` | Dialogue box fills |
| `--panel2` | `#1c1c1c` | `#384858` | Menu headers |
| `--border` | `#2a2a2a` | `#e8e0c8` | GBA dialogue box borders |
| `--text` | `#d4d4d4` | `#f8f8f0` | GBA warm white text |
| `--muted` | `#737373` | `#88a0b0` | Desaturated blue |
| `--muted2` | `#525252` | `#607080` | Deeper muted blue |
| `--accent` | `#4FC3F7` | `#58a8f8` | Pokemon blue |
| `--accentBg` | `#111111` | `#1e2c3c` | Dark accent bg |
| `--accentBorder` | `#2a2a2a` | `#58a8f8` | Accent border matches accent |
| `--success` | `#4CAF50` | `#78c850` | Pokemon grass green |
| `--error` | `#EF5350` | `#f85848` | Pokemon fire red |
| `--warning` | `#FF9800` | `#f8d030` | Pokemon electric yellow |
| `--tableBorder` | `#2a2a2a` | `#384858` | Muted blue table lines |
| `--calloutBorder` | `#4CAF50` | `#78c850` | Green callout |

### Tab Accent Colors (CSS variables in `index.css`)

| Token | Current | New |
|-------|---------|-----|
| `--tab-arb` | `#22c55e` | `#78c850` |
| `--tab-value` | `#FF9800` | `#f8a830` |
| `--tab-bonus` | `#A78BFA` | `#a878f8` |
| `--tab-bets` | `#1E88E5` | `#58a8f8` |
| `--tab-bankroll` | `#EC4899` | `#f878a8` |
| `--tab-profiles` | `#7C3AED` | `#9858f8` |
| `--tab-polymarket` | `#A855F7` | `#b868f8` |
| `--tab-stats` | `#9AA0A6` | `#88a0b0` |
| `--tab-extract` | `#60a5fa` | `#68b0f8` |
| `--tab-reverse` | `#EF5350` | `#f85848` |
| `--tab-tradingBankroll` | `#EC4899` | `#f878a8` |
| `--tab-tradingToday` | `#FACC15` | `#f8d030` |
| `--tab-tradingBuilder` | `#22C55E` | `#78c850` |
| `--tab-tradingTrades` | `#4FC3F7` | `#58a8f8` |
| `--tab-tradingJournal` | `#A78BFA` | `#a878f8` |
| `--tab-tradingIntraday` | `#06B6D4` | `#48c8e0` |

### Hardcoded Tab Colors in `TabBar.tsx`

**`SPORTS_TABS` array** (update `color` field on each):

| Tab name | Current | New |
|----------|---------|-----|
| `polymarket` | `#A855F7` | `#b868f8` |
| `value` | `#FF9800` | `#f8a830` |
| `reverse` | `#EF5350` | `#f85848` |
| `dutch` | `#10b981` | `#78c850` |
| `bankroll` | `#EC4899` | `#f878a8` |
| `stats` | `#1E88E5` | `#58a8f8` |
| `postmortem` | `#14B8A6` | `#48c8a0` |

**`STOCKS_TABS` array:**

| Tab name | Current | New |
|----------|---------|-----|
| `tradingIntraday` | `#06B6D4` | `#48c8e0` |
| `tradingBankroll` | `#EC4899` | `#f878a8` |
| `tradingStats` | `#1E88E5` | `#58a8f8` |

**`TAB_COLORS` map** (all entries):

| Key | Current | New |
|-----|---------|-----|
| `value` | `#FF9800` | `#f8a830` |
| `dutch` | `#10b981` | `#78c850` |
| `reverse` | `#EF5350` | `#f85848` |
| `polymarket` | `#A855F7` | `#b868f8` |
| `stats` | `#1E88E5` | `#58a8f8` |
| `bankroll` | `#EC4899` | `#f878a8` |
| `specials` | `#A78BFA` | `#a878f8` |
| `bets` | `#1E88E5` | `#58a8f8` |
| `profiles` | `#A78BFA` | `#a878f8` |
| `settings` | `#9AA0A6` | `#88a0b0` |
| `success` | `#10b981` | `#78c850` |
| `tradingIntraday` | `#06B6D4` | `#48c8e0` |
| `tradingBankroll` | `#EC4899` | `#f878a8` |
| `tradingStats` | `#1E88E5` | `#58a8f8` |

### `FilterBar.tsx` `ACCENT_COLORS` map

Update all entries (camelCase keys):

| Key | Current | New |
|-----|---------|-----|
| `tabValue` | `#f59e0b` | `#f8a830` |
| `tabBonus` | `#a78bfa` | `#a878f8` |
| `tabArb` | `#22c55e` | `#78c850` |
| `tabExtract` | `#60a5fa` | `#68b0f8` |
| (add others as needed per FilterBar usage) |

### Tailwind Extras

| Token | Current | New |
|-------|---------|-----|
| `yellow` | `#FACC15` | `#f8d030` |

### Hardcoded Colors in TSX Files

**Critical:** Several TSX files contain hardcoded hex values that bypass CSS variables. ALL must be updated:

- **`TabBar.tsx`**: `SPORTS_TABS`, `STOCKS_TABS` arrays and `TAB_COLORS` map contain inline hex colors (e.g., `color: '#A855F7'`). Update every entry to its GBA equivalent.
- **`FilterBar.tsx`**: `ACCENT_COLORS` map (line ~16-24) contains hardcoded hex values. Update all entries.
- **Inline `style=` props**: Any component using `style={{ color: '#...' }}` or `style={{ backgroundColor: '#...' }}` with old palette values must be updated.

The implementer should grep for old hex values after the CSS variable swap to catch stragglers.

## 2. Window Chrome & Borders

Every panel gets GBA dialogue-box treatment:

### Dialogue Box Border System
- **Outer border:** 4px solid `#e8e0c8` (cream/parchment)
- **Inner border:** 2px inset via box-shadow `inset 0 0 0 2px #a8a088`
- **Corner treatment:** Sharp 90-degree (already `border-radius: 0` globally)
- **Panel fill:** `#283848` base
- **Depth shading:** Top-left highlight + bottom-right shadow via inset box-shadows

### CSS Implementation

New utility class `.gba-panel`:
```css
.gba-panel {
  background: var(--panel);
  border: 4px solid var(--border); /* #e8e0c8 */
  box-shadow:
    inset 0 0 0 2px #a8a088,       /* inner border */
    inset 2px 2px 0 4px #384858,    /* top-left highlight (offset by inner border) */
    inset -2px -2px 0 4px #182028;  /* bottom-right shadow */
}
```

### Applied To

Every page follows the same pattern (UI Uniformity Rule):
- **Page outermost `<div>`**: Gets `.gba-panel` wrapper
- **Sidebar** → Vertical `.gba-panel`
- **TabBar** → Menu bar `.gba-panel`
- **Filter bars** → Smaller `.gba-panel`
- **Tables** → Each `<table class="sq">` parent `<div>` gets `.gba-panel`
- **Expanded rows** → Nested `.gba-panel` inside table
- **Modals/popups** → Classic Pokemon text box `.gba-panel`

### Table Updates (`.sq` class)
- Header row: `--panel2` (`#384858`) bg, pixel-art 2px separator bottom
- Row borders: 1px `--tableBorder` (`#384858`, muted blue)
- Hover: `--accent` at 15% opacity + `▶` cursor via `td:first-child::before` pseudo-element (replaces current `box-shadow: inset 3px 0 0`)
- Selected/expanded: Solid 4px accent bar on left via `box-shadow: inset 4px 0 0` (replaces current `inset 3px 0 0`)
- Alternating rows: Even rows `#283848` (same as `--panel`), odd rows `#1e2c3c` (darker)

## 3. Navigation & Menus

### Sidebar
- Wrapped in `.gba-panel` chrome
- Category buttons (Sports/Stocks): Unicode pixel-style icons
  - Sports: `⚽` or a simple CSS 12x12 grid circle (Pokeball-inspired: top half red `#f85848`, bottom half white `#f8f8f0`, center dot)
  - Stocks: `📈` or a CSS 12x12 stepped line chart shape
- Active state: Solid accent fill, dark text `#182028` (GBA menu highlight)
- Settings icon: `⚙` in pixel font
- Profile icon: `♟` or `▲` in pixel font (trainer silhouette)
- BBQ logo: Existing logo with a CSS `image-rendering: pixelated` filter applied

### TabBar
- Blue gradient background: `linear-gradient(180deg, #3060a0, #284878)`
- Wrapped in `.gba-panel` chrome
- Active tab JSX: `▶ ${tab.label}` (Unicode U+25B6 followed by a space, then label)
  - White/cream text `--text`, bold
  - Highlighted background bar at 20% opacity of tab color
- Inactive tabs: `  ${tab.label}` (two spaces for alignment), dimmed `--muted` text
- Remove current bracket notation `[ Label ]`
- CSS transition: `background-color 0.15s ease` on tab elements

### FilterBar
- Dropdowns: Pokemon sub-menu style with `.gba-panel` chrome
- Dropdown items: `▶` arrow prefix on hover/focus via `::before` pseudo-element
- Pills: Rectangular, sharp corners, accent fill when selected, `--panel` when inactive
- Entire filter section wrapped in `.gba-panel`
- Update `ACCENT_COLORS` map values to match new tab color tokens

## 4. Typography

### Font Strategy
- **Body default**: Stays monospace (`--font-data`). Pixel font applied via explicit classes only.
- **Pixel font** (Press Start 2P, bundled locally): Tab names, sidebar labels, section headers, page titles, button text — all UI chrome
- **Monospace font** (keep JetBrains Mono stack): Table data, odds, percentages, team names, timestamps — scannable data

### Font Installation
Bundle the font file locally (do NOT use Google Fonts CDN — app may run offline as .exe):
```css
@font-face {
  font-family: 'Press Start 2P';
  src: url('/fonts/PressStart2P-Regular.woff2') format('woff2');
  font-display: swap;
}

:root {
  --font-pixel: 'Press Start 2P', cursive;
  --font-data: 'JetBrains Mono', 'Cascadia Code', 'SF Mono', 'Fira Code', Consolas, monospace;
}
```

Download `PressStart2P-Regular.woff2` from Google Fonts and place in `frontend/public/fonts/`.

### Sizing (8px multiples for pixel font clarity)
- Pixel font: **8px** for small labels (sidebar, filter pills), **16px** for page titles/headers
- Monospace: 12-13px for data (unchanged)

### CSS Classes
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

### Application
- Table headers: pixel font 8px, uppercase, `#f8d030` (electric yellow)
- Table data cells: monospace font (unchanged)
- Positive values: `#78c850` (grass green)
- Negative values: `#f85848` (fire red)
- Odds values: `#f8d030` (electric yellow)
- Buttons: pixel font 8px

### Tailwind Config Addition
Add to `tailwind.config.js` `fontFamily`:
```js
pixel: ['"Press Start 2P"', 'cursive'],
```

## 5. Background & Atmosphere

### Tile Pattern
- Main `--bg` gets a subtle CSS repeating tile grid overlay
- 16x16 pixel grid lines at ~3% opacity over `#182028` base
- `pointer-events: none` and `z-index: -1` (behind all content, no stacking issues)

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

### Scrollbar
- Thumb: `#a8a088` (inner border color — slightly muted, not full cream)
- Track: `#182028` (background color)
- Width: 8px (unchanged)

### Selection
Replace existing `::selection` rule (currently `rgba(79, 195, 247, 0.3)`) with:
```css
::selection {
  background: rgba(88, 168, 248, 0.3); /* --accent at 30% */
}
```

### Loading States
- Spinner: Simple CSS-only rotating animation — a 16x16 square with two-tone halves (red top `#f85848`, white bottom `#f8f8f0`) rotating via `@keyframes spin`. Mimics Pokeball without needing to be pixel-perfect.
- Empty states: Pixel font message centered in a `.gba-panel`

### Flash Animations
- Flash-up: Background `rgba(120, 200, 80, 0.15)`, text color `#78c850` → fade back
- Flash-down: Background `rgba(248, 88, 72, 0.15)`, text color `#f85848` → fade back
- New opportunity row-enter: Keep existing fadeIn, tinted with `rgba(88, 168, 248, 0.1)`

## 6. Files Modified

### Core styling
- `frontend/src/index.css` — CSS variables, `.gba-panel`, `.gba-header`, `.gba-label`, tile overlay, scrollbar, table styles, font-face, selection, loading spinner, flash animations
- `frontend/tailwind.config.js` — Color token updates, `fontFamily.pixel` addition

### Layout components
- `frontend/src/components/Terminal/Sidebar.tsx` — Pixel-style icons, `.gba-panel` wrapper, active state styling
- `frontend/src/components/Terminal/TabBar.tsx` — Blue gradient, `▶` cursor prefix, pixel font labels, update all hardcoded hex in `SPORTS_TABS`/`STOCKS_TABS`/`TAB_COLORS`
- `frontend/src/components/Terminal/TerminalWindow.tsx` — `.gba-panel` wrappers on layout sections
- `frontend/src/components/Terminal/FilterBar.tsx` — `.gba-panel` dropdowns, update `ACCENT_COLORS` map, styled pills
- `frontend/src/components/ErrorBoundary.tsx` — `.gba-panel` styling if it has custom colors
- `frontend/src/components/Terminal/ErrorNotificationBar.tsx` — Update any hardcoded colors

### Shared components outside pages/
- `frontend/src/components/Terminal/MyBetsSection.tsx` — Uses `TAB_COLORS`, update imports

### Page files (UI Uniformity: same pattern on every page)
- All files in `frontend/src/components/Terminal/pages/` — Wrap page content in `.gba-panel`, update table wrappers, ensure consistent application of new classes

### Assets
- `frontend/public/fonts/PressStart2P-Regular.woff2` — New file (bundled pixel font)

## 7. Non-Goals

- No actual sprite image assets (all pixel art via CSS/Unicode)
- No sound effects or music
- No gameplay mechanics or animations beyond CSS
- No changes to backend or API
- Data density and functionality remain identical
- No changes to data fetching, state management, or component logic
