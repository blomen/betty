/**
 * Shared filter bar component used across Value Bets and Specials pages.
 *
 * Supports:
 * - Multi-select pill groups (providers, sports, categories)
 * - Single-select pill groups (category type)
 * - Range inputs (min/max edge)
 * - Consistent dark theme styling with configurable accent colors
 */

// Map accent color tokens to their hex values (avoids Tailwind purge issues with dynamic classes)
const ACCENT_COLORS: Record<string, string> = {
  tabValue: '#f59e0b',
  tabBonus: '#a78bfa',
  tabArb: '#22c55e',
  tabBets: '#22d3d8',
  tabExtract: '#60a5fa',
};

function getAccent(token: string): string {
  return ACCENT_COLORS[token] || '#f59e0b';
}


// ── Multi-select pill group ──────────────────────────────────────────

interface MultiSelectProps {
  label: string;
  options: string[];
  selected: Set<string>;
  onToggle: (value: string) => void;
  onClear: () => void;
  format?: (value: string) => string;
  accentColor?: string;
}

export function MultiSelectPills({
  label,
  options,
  selected,
  onToggle,
  onClear,
  format,
  accentColor = 'tabValue',
}: MultiSelectProps) {
  if (options.length === 0) return null;

  const hex = getAccent(accentColor);
  const allSelected = selected.size === 0;

  return (
    <div className="flex items-center gap-1 flex-wrap">
      <span className="text-muted2 text-[10px] uppercase tracking-wider mr-1 shrink-0">
        {label}
      </span>
      <button
        onClick={onClear}
        className={`px-2.5 py-1 text-[11px] rounded-full transition-all duration-150 ${
          !allSelected ? 'bg-panel2 text-muted hover:text-text hover:bg-panel2/80' : ''
        }`}
        style={allSelected ? { background: `${hex}15`, color: hex, fontWeight: 500 } : undefined}
      >
        All
      </button>
      {options.map(opt => {
        const isActive = selected.has(opt) || selected.has(opt.toLowerCase());
        return (
          <button
            key={opt}
            onClick={() => onToggle(opt)}
            className={`px-2.5 py-1 text-[11px] rounded-full transition-all duration-150 ${
              !isActive ? 'bg-panel2 text-muted hover:text-text hover:bg-panel2/80' : ''
            }`}
            style={isActive ? { background: `${hex}15`, color: hex, fontWeight: 500 } : undefined}
          >
            {format ? format(opt) : opt}
          </button>
        );
      })}
    </div>
  );
}


// ── Range input (min/max edge) ───────────────────────────────────────

interface RangeFilterProps {
  label: string;
  minValue: string;
  maxValue: string;
  onMinChange: (v: string) => void;
  onMaxChange: (v: string) => void;
  unit?: string;
  accentColor?: string;
}

export function RangeFilter({
  label,
  minValue,
  maxValue,
  onMinChange,
  onMaxChange,
  unit = '%',
  accentColor = 'tabValue',
}: RangeFilterProps) {
  const hex = getAccent(accentColor);

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-muted2 text-[10px] uppercase tracking-wider mr-1 shrink-0">
        {label}
      </span>
      <div className="flex items-center gap-1">
        <input
          type="number"
          placeholder="min"
          value={minValue}
          onChange={e => onMinChange(e.target.value)}
          style={{ '--focus-border': `${hex}80` } as React.CSSProperties}
          className="w-14 px-2 py-1 text-[11px] bg-panel2 border border-border rounded text-text
            placeholder:text-muted2 focus:outline-none focus:border-[var(--focus-border)]
            [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        />
        <span className="text-muted2 text-[10px]">–</span>
        <input
          type="number"
          placeholder="max"
          value={maxValue}
          onChange={e => onMaxChange(e.target.value)}
          style={{ '--focus-border': `${hex}80` } as React.CSSProperties}
          className="w-14 px-2 py-1 text-[11px] bg-panel2 border border-border rounded text-text
            placeholder:text-muted2 focus:outline-none focus:border-[var(--focus-border)]
            [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        />
        {unit && <span className="text-muted2 text-[10px]">{unit}</span>}
      </div>
    </div>
  );
}


// ── Single-select pill group ─────────────────────────────────────────

interface SingleSelectProps {
  label: string;
  options: string[];
  active: string | null;
  onSelect: (value: string | null) => void;
  format?: (value: string) => string;
  accentColor?: string;
}

export function SingleSelectPills({
  label,
  options,
  active,
  onSelect,
  format,
  accentColor = 'tabBonus',
}: SingleSelectProps) {
  if (options.length === 0) return null;

  const hex = getAccent(accentColor);

  return (
    <div className="flex items-center gap-1 flex-wrap">
      <span className="text-muted2 text-[10px] uppercase tracking-wider mr-1 shrink-0">
        {label}
      </span>
      <button
        onClick={() => onSelect(null)}
        className={`px-2.5 py-1 text-[11px] rounded-full transition-all duration-150 ${
          active !== null ? 'bg-panel2 text-muted hover:text-text hover:bg-panel2/80' : ''
        }`}
        style={active === null ? { background: `${hex}15`, color: hex, fontWeight: 500 } : undefined}
      >
        All
      </button>
      {options.map(opt => (
        <button
          key={opt}
          onClick={() => onSelect(active === opt ? null : opt)}
          className={`px-2.5 py-1 text-[11px] rounded-full transition-all duration-150 ${
            active !== opt ? 'bg-panel2 text-muted hover:text-text hover:bg-panel2/80' : ''
          }`}
          style={active === opt ? { background: `${hex}15`, color: hex, fontWeight: 500 } : undefined}
        >
          {format ? format(opt) : opt}
        </button>
      ))}
    </div>
  );
}


// ── Filter bar container ─────────────────────────────────────────────

interface FilterBarProps {
  children: React.ReactNode;
  className?: string;
}

export function FilterBar({ children, className = '' }: FilterBarProps) {
  return (
    <div className={`flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 bg-panel border border-border rounded-lg ${className}`}>
      {children}
    </div>
  );
}
