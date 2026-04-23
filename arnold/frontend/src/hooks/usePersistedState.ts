import { useState, useEffect, useRef } from 'react';

/**
 * Drop-in replacement for useState that persists to localStorage.
 * Handles serialization of primitives, objects, Sets, and Records.
 */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (!key) return defaultValue;
    try {
      const stored = localStorage.getItem(key);
      if (stored === null) return defaultValue;
      const parsed = JSON.parse(stored);
      // Revive Set instances
      if (parsed && parsed.__type === 'Set') {
        return new Set(parsed.values) as unknown as T;
      }
      return parsed as T;
    } catch {
      return defaultValue;
    }
  });

  const isFirst = useRef(true);

  useEffect(() => {
    // Skip writing on initial mount (already read from storage) or if no key
    if (isFirst.current) {
      isFirst.current = false;
      return;
    }
    if (!key) return;
    try {
      // Serialize Set instances
      if (value instanceof Set) {
        localStorage.setItem(key, JSON.stringify({ __type: 'Set', values: [...value] }));
      } else {
        localStorage.setItem(key, JSON.stringify(value));
      }
    } catch {
      // localStorage full or unavailable — silently ignore
    }
  }, [key, value]);

  return [value, setValue];
}

/**
 * Like usePersistedState but for Record<K, Set<V>> structures
 * (e.g., placedLegs: Record<number, Set<number>>)
 */
export function usePersistedRecordOfSets<K extends string | number, V>(
  key: string,
  defaultValue: Record<K, Set<V>>,
): [Record<K, Set<V>>, React.Dispatch<React.SetStateAction<Record<K, Set<V>>>>] {
  const [value, setValue] = useState<Record<K, Set<V>>>(() => {
    try {
      const stored = localStorage.getItem(key);
      if (stored === null) return defaultValue;
      const parsed = JSON.parse(stored);
      // Revive: { "123": [1, 2], "456": [0] } → { 123: Set([1,2]), 456: Set([0]) }
      const result: Record<K, Set<V>> = {} as Record<K, Set<V>>;
      for (const [k, arr] of Object.entries(parsed)) {
        result[k as K] = new Set(arr as V[]);
      }
      return result;
    } catch {
      return defaultValue;
    }
  });

  const isFirst = useRef(true);

  useEffect(() => {
    if (isFirst.current) {
      isFirst.current = false;
      return;
    }
    try {
      // Serialize: { 123: Set([1,2]) } → { "123": [1, 2] }
      const serializable: Record<string, V[]> = {};
      for (const [k, s] of Object.entries(value)) {
        serializable[k] = [...(s as Set<V>)];
      }
      localStorage.setItem(key, JSON.stringify(serializable));
    } catch {
      // silently ignore
    }
  }, [key, value]);

  return [value, setValue];
}
