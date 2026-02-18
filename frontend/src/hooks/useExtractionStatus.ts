import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api, type ExtractionProgress, type TiersProgressResponse } from '@/services/api';

/**
 * Custom event name dispatched on `window` when extraction completes.
 * Pages can listen for this to refresh their data.
 */
export const EXTRACTION_COMPLETE_EVENT = 'bankrollbbq:extraction-complete';

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
 * Monitors extraction progress and dispatches a window event when extraction
 * completes. Also calls `onComplete` callback if provided.
 *
 * Polls every 3s while running, every 10s while idle.
 * Only ONE instance should run (in App.tsx).
 */
export function useExtractionStatus(onComplete?: () => void): ExtractionStatus {
  const [progress, setProgress] = useState<ExtractionProgress | null>(null);
  const [running, setRunning] = useState(false);
  const wasRunningRef = useRef(false);

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

        // Detect running → stopped transition (extraction just completed)
        if (wasRunningRef.current && !anyTierRunning) {
          // Dispatch custom event so all pages can refresh
          window.dispatchEvent(new CustomEvent(EXTRACTION_COMPLETE_EVENT, {
            detail: {
              totalEvents: data.total_events,
              totalOdds: data.total_odds,
              elapsed: data.elapsed_seconds,
            },
          }));

          // Call callback
          onComplete?.();
        }

        wasRunningRef.current = anyTierRunning;

        // Poll faster while running (3s), slower while idle (10s)
        const interval = anyTierRunning ? 3000 : 10000;
        timeoutId = setTimeout(poll, interval);
      } catch {
        // Silent fail — extraction status is non-critical
        if (mounted) {
          timeoutId = setTimeout(poll, 10000);
        }
      }
    }

    poll();

    return () => {
      mounted = false;
      clearTimeout(timeoutId);
    };
  }, [onComplete]);

  return { running, progress };
}

/**
 * Hook for pages to re-fetch data when extraction completes.
 * Pass a callback that will be invoked on the EXTRACTION_COMPLETE_EVENT.
 */
export function useRefreshOnExtraction(callback: () => void) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    function handler() {
      callbackRef.current();
    }

    window.addEventListener(EXTRACTION_COMPLETE_EVENT, handler);
    return () => window.removeEventListener(EXTRACTION_COMPLETE_EVENT, handler);
  }, []);
}
