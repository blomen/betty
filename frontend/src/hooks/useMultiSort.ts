import { useState, useMemo, useCallback } from 'react';

export type SortDirection = 'asc' | 'desc';

export interface SortEntry<K extends string = string> {
  column: K;
  direction: SortDirection;
}

/**
 * Single-column table sorting hook with 3-state cycle.
 *
 * Each column cycles through:
 *   1st click → desc (highest first)
 *   2nd click → asc (lowest first)
 *   3rd click → remove sort (unsorted)
 *
 * Clicking a different column switches to that column in desc.
 */
export function useMultiSort<T, K extends string>(
  items: T[],
  extractors: Record<K, (item: T) => number>,
  defaultSort?: SortEntry<K>,
) {
  const [sort, setSort] = useState<SortEntry<K> | null>(defaultSort ?? null);

  const toggle = useCallback((col: K) => {
    setSort(prev => {
      if (!prev || prev.column !== col) {
        return { column: col, direction: 'desc' as SortDirection };
      }
      if (prev.direction === 'desc') {
        return { column: col, direction: 'asc' as SortDirection };
      }
      // asc → remove
      return null;
    });
  }, []);

  const sorted = useMemo(() => {
    if (!sort || !(sort.column in extractors)) return items;
    const extract = extractors[sort.column];
    const mult = sort.direction === 'desc' ? -1 : 1;
    return [...items].sort((a, b) => mult * (extract(a) - extract(b)));
  }, [items, sort, extractors]);

  return { sorted, sort, toggle } as const;
}
