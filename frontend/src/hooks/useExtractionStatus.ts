import { useEffect, useRef, useState } from 'react';
import { api } from '@/services/api';

export interface ExtractionFreshness {
  soft: string | null;
  sharp: string | null;
  poly: string | null;
  boosts: string | null;
}

/**
 * Hook that fetches extraction freshness timestamps.
 * - Fetches once on mount.
 * - Refetches every 60s on a fixed interval.
 */
export function useExtractionFreshness(): ExtractionFreshness {
  const [freshness, setFreshness] = useState<ExtractionFreshness>({ soft: null, sharp: null, poly: null, boosts: null });

  const fetchFreshness = useRef(() => {
    api.getExtractionFreshness().then(setFreshness).catch(() => {});
  });

  useEffect(() => {
    fetchFreshness.current();
    const id = setInterval(() => fetchFreshness.current(), 60_000);
    return () => clearInterval(id);
  }, []);

  return freshness;
}
