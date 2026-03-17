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
  tabBets: '#1E88E5',
  tabExtract: '#60a5fa',
  success: '#22c55e',
  tabReverse: '#EF5350',
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
        className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] border-2 ${
          hasFilter ? 'font-medium' : 'border-border bg-panel2 text-muted hover:text-text hover:bg-panel2/80'
        }`}
        style={hasFilter ? { backgroundColor: hex, color: '#0a0e0a', borderColor: hex } : undefined}
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
          className={`w-3 h-3 ${isOpen ? 'rotate-180' : ''}`}
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
        <div className="absolute top-full left-0 mt-1 z-50 w-56 bg-panel border-2 border-border overflow-hidden">
          {/* Search */}
          {options.length > 6 && (
            <div className="p-2 border-b border-border">
              <input
                ref={searchRef}
                type="text"
                placeholder="Search..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="w-full px-2.5 py-1.5 text-[11px] bg-panel2 border-2 border-border text-text
                  placeholder:text-muted2 focus:outline-none focus:border-muted"
              />
            </div>
          )}

          {/* Select all / Clear */}
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
            <button
              onClick={selectAll}
              className="text-[10px] uppercase tracking-wider text-muted hover:text-text"
            >
              {filtered.every(o => selected.has(o)) ? 'Deselect all' : 'Select all'}
            </button>
            {hasFilter && (
              <button
                onClick={() => { onClear(); setSearch(''); }}
                className="text-[10px] uppercase tracking-wider text-muted hover:text-text"
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
                    className="w-full flex items-center gap-2.5 px-3 py-1.5 text-left hover:bg-panel2"
                  >
                    {/* Checkbox */}
                    <span
                      className={`font-mono text-xs shrink-0 ${isActive ? '' : 'text-muted'}`}
                      style={isActive ? { color: hex } : undefined}
                    >
                      {isActive ? '[x]' : '[ ]'}
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
        className={`px-2.5 py-1 text-[11px] ${
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
            className={`px-2.5 py-1 text-[11px] ${
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
        className={`px-2.5 py-1 text-[11px] ${
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
          className={`px-2.5 py-1 text-[11px] ${
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
 * Shows extraction age per tier in HH:MM:SS format with color coding.
 * Auto-refreshes every 30 seconds.
 */
export function FreshnessIndicator({ tiers }: FreshnessIndicatorProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const entries = tiers.filter(([, ts]) => ts != null);
  if (entries.length === 0) return null;

  return (
    <span className="ml-auto shrink-0 flex items-center gap-3">
      {entries.map(([tier, ts]) => {
        if (!ts) return null;
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

// ── Expandable search icon ───────────────────────────────────────────

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  accentColor?: string;
}

/**
 * Magnifying glass icon that expands into a search input on click.
 * Place in the page header's top-right corner.
 */
export function SearchInput({
  value,
  onChange,
  placeholder = 'Search...',
  accentColor = 'tabValue',
}: SearchInputProps) {
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hex = getAccent(accentColor);

  const isActive = open || !!value;

  // Focus input when opening
  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  // Close on outside click (only if empty)
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node) && !value) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open, value]);

  if (!isActive) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="p-1 text-muted2 hover:text-text"
        title="Search"
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <circle cx="11" cy="11" r="8" />
          <path strokeLinecap="round" d="M21 21l-4.35-4.35" />
        </svg>
      </button>
    );
  }

  return (
    <div className="relative" ref={containerRef}>
      <svg className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-muted2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <circle cx="11" cy="11" r="8" />
        <path strokeLinecap="round" d="M21 21l-4.35-4.35" />
      </svg>
      <input
        ref={inputRef}
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Escape') { onChange(''); setOpen(false); } }}
        style={{ '--focus-border': `${hex}80` } as React.CSSProperties}
        className="pl-7 pr-6 py-1 text-[11px] bg-bg border border-border text-text placeholder:text-muted2 w-48 focus:outline-none focus:border-[var(--focus-border)]"
      />
      <button
        onClick={() => { onChange(''); setOpen(false); }}
        className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted2 hover:text-text text-[10px]"
      >
        x
      </button>
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
    <div className={`flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 bg-panel border border-border ${className}`}>
      {children}
    </div>
  );
}
