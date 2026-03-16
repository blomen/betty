# Retro Terminal Redesign — Shell-First

**Date:** 2026-03-16
**Scope:** Sidebar, TabBar, TerminalWindow chrome, global CSS tokens, table.sq, FilterBar
**Aesthetic:** Gameboy / retro terminal — subtle, professional, no gimmicks

## Design Principles

1. **No border-radius** — square corners everywhere. This is the #1 retro lever.
2. **No box-shadows** — depth via border thickness and color elevation only.
3. **No gradients** — flat colors, period.
4. **Thick borders over subtle backgrounds** — 2px borders define structure, not opacity tricks.
5. **Instant state changes** — no CSS transitions on interactive elements. Retro UIs snap.
6. **Existing accent colors preserved** — all tab/functional colors (orange, green, purple, red, pink, cyan) untouched.

---

## 1. Color Palette

Update both Tailwind config and CSS custom properties in sync.

| Token | Current | New | Purpose |
|-------|---------|-----|---------|
| `bg` | `#111111` | `#0a0e0a` | Deep dark with faint green tint |
| `panel` | `#1a1a1a` | `#131a13` | Panel elevation, green-shifted |
| `panel2` | `#202020` | `#1a231a` | Secondary panel, visible step up |
| `border` | `#1e2636` | `#2a3a2a` | Green-tinted borders like PCB traces |
| `text` | `#E6E8EB` | `#d4e0d4` | Warm white, easy on eyes |
| `muted` | `#9AA0A6` | `#7a9a7a` | Muted green-gray secondary text |
| `muted2` | `#7A7F87` | `#5a7a5a` | Dimmer green-gray |
| `accent` | `#4FC3F7` | `#4FC3F7` | Unchanged — cyan links/selections |
| `accentBg` | `#1a1f2a` | `#0a1a0a` | Green-shifted accent background |
| `accentBorder` | `#1e2636` | `#2a3a2a` | Matches new border |
| `tableBorder` | `#1e2636` | `#2a3a2a` | Matches new border |

All `tab*`, `success`, `warning`, `error`, `yellow`, `calloutBorder` colors: **unchanged**.

### Files
- `tailwind.config.js` — update `theme.extend.colors`
- `index.css` — update `:root` CSS custom properties to match

---

## 2. Sidebar

**File:** `Sidebar.tsx`

### Layout
- Width: `w-14` (56px) → `w-16` (64px)
- Padding: `py-3` → `py-4`
- Background: `bg-panel` (inherits new green-tinted panel)
- Right border: `border-r border-border` → `border-r-2 border-border`

### Logo
- Add the BBQ chicken icon (`TabIcon name="app"`) at the top, above category buttons
- Size: 24px
- Separated from category nav by `mb-4`

### Category Buttons
- Size: `w-10 h-10` → `w-12 h-12`
- Remove `rounded` class — square corners
- Active state: `border-2 border-text bg-transparent text-text` (visible selection box, no background fill)
- Inactive state: `border-2 border-transparent text-muted`
- Hover state: `border-2 border-muted text-text` (dotted border would require custom CSS, use solid muted instead for simplicity)
- Remove `transition-colors` — instant state changes

### Separator
- Between category nav and bottom buttons: replace `<div className="flex-1" />` with a flex-1 div containing a centered dashed line
- Implementation: `<div className="flex-1 flex items-center justify-center"><span className="text-muted2 text-[10px] select-none">──</span></div>`

### Bottom Buttons (Settings, Profile)
- Same treatment as category buttons: square, 2px border, no rounded
- Size: `w-12 h-12` (up from `w-10 h-10`)
- SVG icon sizes: increase from 18px to 20px to stay proportional in larger containers

---

## 3. Tab Bar

**File:** `TabBar.tsx`

### Container
- Background: `bg-panel` stays
- Bottom border: `border-b border-border` → `border-b-2 border-border`
- Padding: `px-2` → `px-3`

### Tab Buttons
- Remove `transition-colors duration-150`
- Remove `border-b-2 -mb-px` bottom border approach
- Add uppercase: `uppercase tracking-wider`
- Padding: `px-3 py-2` → `px-4 py-2.5`
- Gap between tabs: `gap-0` → `gap-1`

### Active Tab State
- **Inverted colors**: background = tab's accent color, text = `#0a0e0a` (bg color), font-weight = bold
- Applied via inline `style={{ backgroundColor: tab.color, color: '#0a0e0a' }}`
- No bottom border — the filled background IS the indicator
- Square corners (no rounded)

### Inactive Tab State
- Text: `text-muted`
- Background: transparent
- Hover: `text-text` (instant, no transition)

### Tab Icons
- Remove the `<TabIcon>` **call** from the tab button JSX (do NOT delete the `TabIcon` component definition — it's still used by Sidebar)
- Replace with a colored dot character: `<span style={{ color: tab.color }}>●</span>`
- Size inherits from parent `text-xs`

### Bracket Labels (Active Only)
- Active tab label renders as `[ Label ]` — brackets are part of the text
- Inactive tabs render just `Label` — no brackets
- Implementation: `{isActive ? `[ ${tab.label} ]` : tab.label}`

---

## 4. Table Styling (table.sq)

**File:** `index.css`

### Outer Frame
- Add `outline: 2px solid var(--border)` on `table.sq` — thick outer frame
- Inner cell borders stay 1px

### Header Row
- Background: `var(--panel2)` (up from `var(--panel)`)
- Font-size: `11px` (up from `10px`)
- Letter-spacing: `0.1em` (up from `0.05em`)
- Bottom border: `2px solid var(--border)` — double-weight separator from data
- Padding: `8px 12px` (up from `5px 8px`)

### Data Cells
- Padding: `8px 12px` (up from `5px 8px`)
- Line-height: `1.6` (up from inherited 1.4)
- Background: `var(--bg)` unchanged
- Remove `border-radius` if any inherited

### Row Hover
- Current: `background-color: var(--panel)`
- New: keep `background-color: var(--panel)` AND add a cursor-like left indicator
- Technique: `box-shadow: inset 3px 0 0 var(--muted)` on `tr:hover td:first-child` — avoids border-collapse conflicts, no layout shift

### Expanded Rows
- `tr.expanded td:first-child`: `box-shadow: inset 3px 0 0 var(--muted)` (solid, always visible)

### Alternating Rows
- `table.sq tbody tr:nth-child(even) td { background-color: #0f150f; }` — a value between bg and panel

---

## 5. FilterBar Components

**File:** `FilterBar.tsx`

### Dropdown Trigger Button
- Border: `2px solid` in `border` color (global border-radius reset handles square corners)
- When filter is active (has selections): **inverted** — accent background, dark text
- Implementation: `style={{ backgroundColor: hex, color: '#0a0e0a', borderColor: hex }}` when `hasFilter`

### Dropdown Popover
- Remove any `rounded` classes
- Border: `2px solid var(--border)` (up from 1px)
- Remove any `shadow` classes — no box-shadow
- Background: `panel`

### Checkboxes
- Replace native `<input type="checkbox">` with styled text characters
- Checked: `[x]` in accent color
- Unchecked: `[ ]` in muted color
- Implementation: `<span style={{ color: isChecked ? hex : undefined }} className={isChecked ? '' : 'text-muted'}>{isChecked ? '[x]' : '[ ]'}</span>`
- Followed by the label text with `ml-2`

### Search Input (in dropdown)
- 2px border (square corners via global reset)
- Focus: border-color changes to accent (via inline style)

### Other Sub-Components (MultiSelectPills, SingleSelectPills, RangeFilter, SearchInput)
- Remove any `transition-all duration-150` classes — matches Design Principle #5
- Square corners already handled by global reset
- No other structural changes needed

---

## 6. TerminalWindow & Global Chrome

**File:** `TerminalWindow.tsx`

### Content Area
- Padding: `p-3` → `p-4`
- Loading fallback: replace `"Loading..."` text with blinking cursor `█` using `animate-blink` class

### Connection/Error Bars
- Inherit new colors automatically via CSS tokens

**File:** `index.css`

### Global Border-Radius Reset
Add near the top (after Tailwind directives):
```css
*, *::before, *::after {
  border-radius: 0 !important;
}
```
This nuclear option ensures nothing has rounded corners. Retro = square.

### Scrollbar
- Width: `6px` → `8px` (chunkier)
- Thumb: `var(--border)` → `var(--muted2)` (more visible)

### Selection Highlight
- Update from cyan to green-tinted: `rgba(122, 154, 122, 0.3)` — matches the palette

### Transitions Removal
- Do NOT add a global `transition: none` — that would break the flash-up/flash-down animations on odds changes, which must stay
- Instead, remove transitions per-component in the component files (Sidebar, TabBar)
- Flash animations (`flash-up`, `flash-down`), `fadeIn`, `row-enter`/`row-exit` — all keep their existing CSS, untouched

---

## 7. What's NOT Changing

Explicitly preserved:
- **All tab accent colors** (orange, green, purple, red, pink, cyan, etc.)
- **JetBrains Mono** font family
- **Font size** (12.5px base, 12px table cells)
- **Flash animations** on odds updates (green up, red down)
- **Row fade-in/out** animations
- **Virtualization** (@tanstack/react-virtual)
- **Code splitting** (eager/lazy page loading)
- **Page-level layouts** — no structural changes to any of the 12 pages
- **All functional behavior** — filters, sorting, bet placement, etc.

---

## Files Modified (Complete List)

| File | Changes |
|------|---------|
| `tailwind.config.js` | Color palette update, no other changes |
| `index.css` | CSS variables, global border-radius reset, table.sq overhaul, scrollbar, selection color |
| `Sidebar.tsx` | Width, border treatment, logo, separator, square buttons |
| `TabBar.tsx` | Inverted active state, brackets, dot icons, uppercase, no transitions |
| `FilterBar.tsx` | Square corners, thick borders, text checkboxes, inverted active state |
| `TerminalWindow.tsx` | Content padding, loading state cursor |

**Total: 6 files.** All pages inherit the new look through CSS token changes + table.sq + FilterBar updates.
