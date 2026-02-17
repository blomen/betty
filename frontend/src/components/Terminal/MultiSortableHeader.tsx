import type { SortEntry } from '@/hooks/useMultiSort';

interface MultiSortableHeaderProps<K extends string> {
  column: K;
  label: string;
  sort: SortEntry<K> | null;
  onToggle: (col: K) => void;
  align?: 'left' | 'right';
}

/**
 * Clickable table column header with 3-state sort cycle.
 *
 * 1st click → desc ▼, 2nd click → asc ▲, 3rd click → unsorted.
 */
export function MultiSortableHeader<K extends string>({
  column,
  label,
  sort,
  onToggle,
  align = 'right',
}: MultiSortableHeaderProps<K>) {
  const isActive = sort?.column === column;
  const arrow = isActive ? (sort!.direction === 'desc' ? ' \u25BC' : ' \u25B2') : '';

  return (
    <th
      className={`${align === 'right' ? 'text-right' : ''} cursor-pointer select-none hover:text-text transition-colors ${isActive ? 'text-text' : ''}`}
      onClick={() => onToggle(column)}
    >
      {label}{arrow}
    </th>
  );
}
