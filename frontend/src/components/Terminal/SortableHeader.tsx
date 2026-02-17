import type { SortState } from '@/hooks/useTableSort';

interface SortableHeaderProps<K extends string> {
  column: K;
  label: string;
  sort: SortState<K>;
  onToggle: (col: K) => void;
  align?: 'left' | 'right';
}

/**
 * Clickable table column header with sort indicator.
 *
 * Shows ▲/▼ next to the active sort column.
 * Uniform across all tab pages (ValuePage, PolymarketPage, DutchPage, SpecialsPage).
 */
export function SortableHeader<K extends string>({
  column,
  label,
  sort,
  onToggle,
  align = 'right',
}: SortableHeaderProps<K>) {
  const isActive = sort.column === column;
  const arrow = isActive ? (sort.direction === 'desc' ? ' ▼' : ' ▲') : '';

  return (
    <th
      className={`${align === 'right' ? 'text-right' : ''} cursor-pointer select-none hover:text-text transition-colors ${isActive ? 'text-text' : ''}`}
      onClick={() => onToggle(column)}
    >
      {label}{arrow}
    </th>
  );
}
