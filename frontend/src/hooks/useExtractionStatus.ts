import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api, type ExtractionProgress, type TiersProgressResponse } from '@/services/api';

interface ExtractionStatus {
  /** Whether extraction is currently running */
  running: boolean;
  /** Extraction progress (null until first poll) */
  progress: ExtractionProgress | null;
}

// ============ Shared progress store (single poller, many consumers) ============

let _latestProgress: ExtractionProgress | null = null;
let _listeners: Array<() => void> = [];

function emitProgressUpdate(data: ExtractionProgress | null) {
  _latestProgress = data;
  for (const listener of _listeners) {
    listener();
  }
}

function subscribeProgress(listener: () => void) {
  _listeners.push(listener);
  return () => {
    _listeners = _listeners.filter(l => l !== listener);
  };
}

function getProgressSnapshot(): ExtractionProgress | null {
  return _latestProgress;
}

/**
 * Subscribe to extraction progress without starting a poller.
 * The single poller in `useExtractionStatus` pushes updates here.
 */
export function useExtractionProgress(): ExtractionProgress | null {
  return useSyncExternalStore(subscribeProgress, getProgressSnapshot);
}

// ============ Shared tier progress store ============

let _latestTiers: TiersProgressResponse | null = null;
let _tierListeners: Array<() => void> = [];

function emitTierUpdate(data: TiersProgressResponse | null) {
  _latestTiers = data;
  for (const listener of _tierListeners) {
    listener();
  }
}

function subscribeTiers(listener: () => void) {
  _tierListeners.push(listener);
  return () => {
    _tierListeners = _tierListeners.filter(l => l !== listener);
  };
}

function getTiersSnapshot(): TiersProgressResponse | null {
  return _latestTiers;
}

/**
 * Subscribe to per-tier extraction progress.
 * Returns all tier states: sharp, api_soft, browser_soft.
 */
export function useTiersProgress(): TiersProgressResponse | null {
  return useSyncExternalStore(subscribeTiers, getTiersSnapshot);
}

/**
 * Monitors extraction progress via polling.
 *
 * Polls every 10s while running, every 30s while idle.
 * Only ONE instance should run (in App.tsx).
 */
export function useExtractionStatus(): ExtractionStatus {
  const [progress, setProgress] = useState<ExtractionProgress | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    let mounted = true;
    let timeoutId: ReturnType<typeof setTimeout>;

    async function poll() {
      if (!mounted) return;

      try {
        // Fetch both global progress and per-tier progress in parallel
        const [data, tiersData] = await Promise.all([
          api.getExtractionProgress(),
          api.getTiersProgress(),
        ]);
        if (!mounted) return;

        setProgress(data);
        setRunning(data.running);

        // Push to shared stores so all pages get the update
        emitProgressUpdate(data);
        emitTierUpdate(tiersData);

        // Use any_running from tiers (more accurate — handles tier transitions)
        const anyTierRunning = tiersData.any_running;

        // Poll while running (10s) or idle (30s) — gentle on slow PCs
        const interval = anyTierRunning ? 10_000 : 30_000;
        timeoutId = setTimeout(poll, interval);
      } catch {
        // Silent fail — extraction status is non-critical
        if (mounted) {
          timeoutId = setTimeout(poll, 30_000);
        }
      }
    }

    poll();

    return () => {
      mounted = false;
      clearTimeout(timeoutId);
    };
  }, []);

  return { running, progress };
}

export interface ExtractionFreshness {
  soft: string | null;
  sharp: string | null;
  poly: string | null;
  boosts: string | null;
}

/**
 * Hook that fetches extraction freshness timestamps.
 * - Fetches once on mount.
 * - Refetches every 60s only while idle (no extraction running).
 */
export function useExtractionFreshness(): ExtractionFreshness {
  const [freshness, setFreshness] = useState<ExtractionFreshness>({ soft: null, sharp: null, poly: null, boosts: null });

  const fetchFreshness = useRef(() => {
    api.getExtractionFreshness().then(setFreshness).catch(() => {});
  });

  useEffect(() => {
    fetchFreshness.current();
  }, []);

  // Periodic refetch only while idle (no extraction running)
  const tiersProgress = useTiersProgress();
  const anyRunning = tiersProgress?.any_running ?? false;

  useEffect(() => {
    if (anyRunning) return; // Don't refetch while extracting
    const id = setInterval(() => fetchFreshness.current(), 60_000);
    return () => clearInterval(id);
  }, [anyRunning]);

  return freshness;
}
