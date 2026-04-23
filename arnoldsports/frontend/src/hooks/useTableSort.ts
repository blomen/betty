import { useMemo, useCallback } from 'react';
import { usePersistedState } from './usePersistedState';

export type SortDirection = 'asc' | 'desc';

export interface SortState<K extends string = string> {
  column: K | null;
  direction: SortDirection;
}

/**
 * Generic table sorting hook.
 *
 * @param items  The array to sort (not mutated).
 * @param extractors  Map of column key -> function that extracts a sortable numeric value.
 * @param defaultSort  Optional initial sort state.
 *
 * Clicking the same column cycles: desc → asc → remove sort.
 * Clicking a different column starts with desc (highest first).
 */
export function useTableSort<T, K extends string>(
  items: T[],
  extractors: Record<K, (item: T) => number>,
  defaultSort?: SortState<K>,
  storageKey?: string,
) {
  const [sort, setSort] = usePersistedState<SortState<K>>(
    storageKey ?? '',
    defaultSort ?? { column: null, direction: 'desc' } as SortState<K>,
  );

  const toggle = useCallback((col: K) => {
    setSort(prev => {
      if (prev.column === col) {
        if (prev.direction === 'desc') return { column: col, direction: 'asc' as SortDirection };
        // asc → remove sort
        return { column: null, direction: 'desc' };
      }
      return { column: col, direction: 'desc' };
    });
  }, []);

  const sorted = useMemo(() => {
    if (!sort.column || !(sort.column in extractors)) return items;
    const extract = extractors[sort.column];
    const multiplier = sort.direction === 'desc' ? -1 : 1;
    return [...items].sort((a, b) => multiplier * (extract(a) - extract(b)));
  }, [items, sort, extractors]);

  return { sorted, sort, toggle } as const;
}
