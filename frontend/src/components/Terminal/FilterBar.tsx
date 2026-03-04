/**
 * Shared filter bar component used across Value Bets and Specials pages.
 *
 * Supports:
 * - Multi-select dropdown (compact popover with checkboxes + search)
 * - Multi-select pill groups (providers, sports, categories)
 * - Single-select pill groups (category type)
 * - Range inputs (min/max edge)
 * - Consistent dark theme styling with configurable accent colors
 */

import { useState, useRef, useEffect, useCallback } from 'react';

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


// ── Multi-select dropdown (compact popover with checkboxes) ──────────

interface MultiSelectDropdownProps {
  label: string;
  options: string[];
  selected: Set<string>;
  onToggle: (value: string) => void;
  onClear: () => void;
  format?: (value: string) => string;
  accentColor?: string;
}

export function MultiSelectDropdown({
  label,
  options,
  selected,
  onToggle,
  onClear,
  format,
  accentColor = 'tabValue',
}: MultiSelectDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const hex = getAccent(accentColor);
  const count = selected.size;
  const hasFilter = count > 0;

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen]);

  // Close on Escape
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false);
      setSearch('');
    }
  }, []);

  // Focus search when opening
  useEffect(() => {
    if (isOpen && searchRef.current) {
      searchRef.current.focus();
    }
  }, [isOpen]);

  const filtered = options.filter(opt => {
    if (!search) return true;
    const display = format ? format(opt) : opt;
    return display.toLowerCase().includes(search.toLowerCase());
  });

  const selectAll = () => {
    // If all visible are selected, deselect them; otherwise select all visible
    const allVisible = filtered.every(o => selected.has(o));
    if (allVisible) {
      onClear();
    } else {
      for (const opt of filtered) {
        if (!selected.has(opt)) onToggle(opt);
      }
    }
  };

  if (options.length === 0) return null;

  return (
    <div className="relative" ref={containerRef} onKeyDown={handleKeyDown}>
      {/* Trigger button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] transition-all duration-150 ${
          hasFilter ? '' : 'bg-panel2 text-muted hover:text-text hover:bg-panel2/80'
        }`}
        style={hasFilter ? { background: `${hex}15`, color: hex, fontWeight: 500 } : undefined}
      >
        <span className="text-muted2 text-[10px] uppercase tracking-wider mr-0.5">
          {label}
        </span>
        {hasFilter ? (
          <span style={{ color: hex }}>{count}</span>
        ) : (
          <span>All</span>
        )}
        <svg
          className={`w-3 h-3 transition-transform duration-150 ${isOpen ? 'rotate-180' : ''}`}
          style={{ color: hasFilter ? hex : undefined }}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Dropdown popover */}
      {isOpen && (
        <div className="absolute top-full left-0 mt-1 z-50 w-56 bg-panel border border-border shadow-xl shadow-black/30 overflow-hidden">
          {/* Search */}
          {options.length > 6 && (
            <div className="p-2 border-b border-border">
              <input
                ref={searchRef}
                type="text"
                placeholder="Search..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="w-full px-2.5 py-1.5 text-[11px] bg-panel2 border border-border text-text
                  placeholder:text-muted2 focus:outline-none focus:border-muted"
              />
            </div>
          )}

          {/* Select all / Clear */}
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
            <button
              onClick={selectAll}
              className="text-[10px] uppercase tracking-wider text-muted hover:text-text transition-colors"
            >
              {filtered.every(o => selected.has(o)) ? 'Deselect all' : 'Select all'}
            </button>
            {hasFilter && (
              <button
                onClick={() => { onClear(); setSearch(''); }}
                className="text-[10px] uppercase tracking-wider text-muted hover:text-text transition-colors"
              >
                Clear
              </button>
            )}
          </div>

          {/* Options list */}
          <div className="max-h-64 overflow-y-auto py-1 scrollbar-thin">
            {filtered.length === 0 ? (
              <div className="px-3 py-2 text-[11px] text-muted2">No matches</div>
            ) : (
              filtered.map(opt => {
                const isActive = selected.has(opt);
                const display = format ? format(opt) : opt;
                return (
                  <button
                    key={opt}
                    onClick={() => onToggle(opt)}
                    className="w-full flex items-center gap-2.5 px-3 py-1.5 text-left hover:bg-panel2 transition-colors"
                  >
                    {/* Checkbox */}
                    <span
                      className={`w-3.5 h-3.5 border flex items-center justify-center shrink-0 transition-all duration-150 ${
                        isActive ? 'border-transparent' : 'border-muted/40'
                      }`}
                      style={isActive ? { background: hex, borderColor: hex } : undefined}
                    >
                      {isActive && (
                        <svg className="w-2.5 h-2.5 text-bg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </span>
                    <span className={`text-[11px] truncate ${isActive ? 'text-text font-medium' : 'text-muted'}`}>
                      {display}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
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
        className={`px-2.5 py-1 text-[11px] transition-all duration-150 ${
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
            className={`px-2.5 py-1 text-[11px] transition-all duration-150 ${
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
          className="w-14 px-2 py-1 text-[11px] bg-panel2 border border-border text-text
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
          className="w-14 px-2 py-1 text-[11px] bg-panel2 border border-border text-text
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
        className={`px-2.5 py-1 text-[11px] transition-all duration-150 ${
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
          className={`px-2.5 py-1 text-[11px] transition-all duration-150 ${
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


// ── Extraction freshness indicators ───────────────────────────────────

function formatAge(isoTimestamp: string): { label: string; color: string } {
  const ageMs = Date.now() - new Date(isoTimestamp).getTime();
  const totalSec = Math.max(0, Math.floor(ageMs / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const label = `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;

  const ageMin = totalSec / 60;
  const ageHr = ageMin / 60;
  const color = ageMin < 15 ? 'text-success' : ageMin < 60 ? 'text-yellow' : ageHr < 3 ? 'text-warning' : 'text-error';
  return { label, color };
}

interface FreshnessIndicatorProps {
  /** Array of [label, isoTimestamp] pairs, e.g. [["soft", "2026-..."], ["sharp", "2026-..."]] */
  tiers: [string, string | null][];
}

/**
 * Shows extraction age per tier in h:m:s format with color coding.
 * Auto-refreshes every second for live countdown.
 */
export function FreshnessIndicator({ tiers }: FreshnessIndicatorProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1_000);
    return () => clearInterval(id);
  }, []);

  const visible = tiers.filter(([, ts]) => ts != null) as [string, string][];
  if (visible.length === 0) return null;

  return (
    <span className="ml-auto shrink-0 flex items-center gap-3">
      {visible.map(([tier, ts]) => {
        const { label, color } = formatAge(ts);
        return (
          <span key={tier} className={`text-[10px] ${color}`} title={new Date(ts).toLocaleString()}>
            <span className="text-muted2 uppercase">{tier}</span> {label}
          </span>
        );
      })}
    </span>
  );
}

// ── Filter bar container ─────────────────────────────────────────────

interface FilterBarProps {
  children: React.ReactNode;
  className?: string;
}

export function FilterBar({ children, className = '' }: FilterBarProps) {
  return (
    <div className={`flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 bg-panel border border-border ${className}`}>
      {children}
    </div>
  );
}
