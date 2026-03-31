# Unified ConnectionManager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three independent backend health-check systems with a single ConnectionManager singleton that owns all connection state, gates SSE reconnection, and eliminates contradictory status decisions.

**Architecture:** A plain TypeScript singleton (`connectionManager`) polls `/health` and exposes `isUp()`, `waitForUp()`, and `subscribe()`. A thin React hook (`useConnectionStatus`) subscribes for UI. SSE hooks gate reconnect on `waitForUp()`. The `fetchWithRetry` fast-fail reads `isUp()`.

**Tech Stack:** TypeScript, React hooks, EventSource SSE

---

### Task 1: Create ConnectionManager singleton

**Files:**
- Create: `frontend/src/services/connectionManager.ts`

- [ ] **Step 1: Create the ConnectionManager module**

```ts
// frontend/src/services/connectionManager.ts

export type ConnectionState = 'checking' | 'connecting' | 'ok' | 'slow' | 'down';

type Listener = (state: ConnectionState, latencyMs: number | null, message: string) => void;

const POLL_OK_MS = 10_000;
const POLL_DOWN_MS = 3_000;
const SLOW_THRESHOLD_MS = 3000;
const STARTUP_GRACE_MS = 30_000;
const HEALTH_TIMEOUT_MS = 5000;
const CONSECUTIVE_FAIL_THRESHOLD = 2;

class ConnectionManager {
  private _state: ConnectionState = 'checking';
  private _latencyMs: number | null = null;
  private _message = 'Checking...';
  private _consecutiveFails = 0;
  private _everConnected = false;
  private _startTime = Date.now();
  private _listeners = new Set<Listener>();
  private _pollTimer: ReturnType<typeof setTimeout> | null = null;
  private _waitResolvers = new Set<() => void>();

  constructor() {
    this._poll();
  }

  // --- Public API ---

  getState(): ConnectionState {
    return this._state;
  }

  getLatency(): number | null {
    return this._latencyMs;
  }

  getMessage(): string {
    return this._message;
  }

  /** Sync check — true when state is 'ok' or 'slow' (backend is responding). */
  isUp(): boolean {
    return this._state === 'ok' || this._state === 'slow';
  }

  /** Async — resolves immediately if already up, otherwise waits for next 'ok'/'slow' transition. */
  waitForUp(): Promise<void> {
    if (this.isUp()) return Promise.resolve();
    return new Promise<void>((resolve) => {
      this._waitResolvers.add(resolve);
    });
  }

  /** Subscribe to state changes. Returns unsubscribe function. */
  subscribe(fn: Listener): () => void {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  // --- Internal ---

  private _setState(state: ConnectionState, latencyMs: number | null, message: string) {
    const changed = state !== this._state || latencyMs !== this._latencyMs || message !== this._message;
    this._state = state;
    this._latencyMs = latencyMs;
    this._message = message;

    if (changed) {
      for (const fn of this._listeners) fn(state, latencyMs, message);
    }

    // Resolve waitForUp promises when backend comes up
    if (this.isUp() && this._waitResolvers.size > 0) {
      for (const resolve of this._waitResolvers) resolve();
      this._waitResolvers.clear();
    }

    // Adjust poll interval based on state
    this._schedulePoll(
      state === 'down' || state === 'connecting' ? POLL_DOWN_MS : POLL_OK_MS
    );
  }

  private _schedulePoll(ms: number) {
    if (this._pollTimer) clearTimeout(this._pollTimer);
    this._pollTimer = setTimeout(() => this._poll(), ms);
  }

  private async _poll() {
    const t0 = performance.now();
    try {
      const controller = new AbortController();
      const tid = setTimeout(() => controller.abort('Health check timeout'), HEALTH_TIMEOUT_MS);
      const res = await fetch('/health', { signal: controller.signal });
      clearTimeout(tid);
      const latency = Math.round(performance.now() - t0);

      if (!res.ok) {
        this._onFail(latency, `API ${res.status}`);
      } else if (latency > SLOW_THRESHOLD_MS) {
        this._onSlow(latency);
      } else {
        this._onSuccess(latency);
      }
    } catch {
      const latency = Math.round(performance.now() - t0);
      this._onFail(latency, 'Backend unreachable');
    }
  }

  private _onSuccess(latency: number) {
    this._consecutiveFails = 0;
    this._everConnected = true;
    this._setState('ok', latency, '');
  }

  private _onSlow(latency: number) {
    this._consecutiveFails += 1;
    const inGrace = !this._everConnected && Date.now() - this._startTime < STARTUP_GRACE_MS;

    if (inGrace) {
      this._setState('connecting', latency, `Starting up (${latency}ms)`);
    } else if (this._consecutiveFails >= CONSECUTIVE_FAIL_THRESHOLD) {
      this._setState('slow', latency, `Event loop slow (${latency}ms)`);
    } else {
      // Single slow response — keep current state, don't alarm
      this._schedulePoll(POLL_OK_MS);
    }
  }

  private _onFail(latency: number, reason: string) {
    this._consecutiveFails += 1;
    const inGrace = !this._everConnected && Date.now() - this._startTime < STARTUP_GRACE_MS;

    if (inGrace) {
      this._setState('connecting', latency, 'Waiting for backend...');
    } else if (this._consecutiveFails >= CONSECUTIVE_FAIL_THRESHOLD) {
      this._setState('down', latency, reason);
    } else {
      // Single failure — keep current state, retry quickly
      this._schedulePoll(POLL_DOWN_MS);
    }
  }
}

export const connectionManager = new ConnectionManager();
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors related to `connectionManager.ts`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/connectionManager.ts
git commit -m "feat: add ConnectionManager singleton for unified backend health tracking"
```

---

### Task 2: Create useConnectionStatus React hook

**Files:**
- Create: `frontend/src/hooks/useConnectionStatus.ts`

- [ ] **Step 1: Create the hook**

```ts
// frontend/src/hooks/useConnectionStatus.ts
import { useState, useEffect } from 'react';
import { connectionManager, type ConnectionState } from '@/services/connectionManager';

export interface ConnectionStatus {
  status: ConnectionState;
  latencyMs: number | null;
  message: string;
}

export function useConnectionStatus(): ConnectionStatus {
  const [status, setStatus] = useState<ConnectionStatus>({
    status: connectionManager.getState(),
    latencyMs: connectionManager.getLatency(),
    message: connectionManager.getMessage(),
  });

  useEffect(() => {
    const unsub = connectionManager.subscribe((state, latencyMs, message) => {
      setStatus({ status: state, latencyMs, message });
    });
    return unsub;
  }, []);

  return status;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors related to `useConnectionStatus.ts`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useConnectionStatus.ts
git commit -m "feat: add useConnectionStatus hook bridging ConnectionManager to React"
```

---

### Task 3: Wire ConnectionErrorBar to useConnectionStatus

**Files:**
- Modify: `frontend/src/components/Terminal/ErrorNotificationBar.tsx`

- [ ] **Step 1: Replace ConnectionErrorBar internals**

Replace the entire file content with:

```tsx
// frontend/src/components/Terminal/ErrorNotificationBar.tsx
import { useConnectionStatus } from '@/hooks/useConnectionStatus';

/**
 * Shows extraction errors as a dismissible banner above page content.
 * Currently a no-op since the tiers progress endpoint was removed.
 */
export function ErrorNotificationBar() {
  return null;
}

/**
 * Shows a connection error banner when the backend API is unreachable.
 * Reads from the unified ConnectionManager — no independent polling.
 */
export function ConnectionErrorBar() {
  const { status, message } = useConnectionStatus();

  if (status === 'checking') return null;

  if (status === 'connecting') {
    return (
      <div
        className="mx-3 mt-2 border border-orange-500/30 bg-gradient-to-br from-orange-500/12 to-orange-500/4 text-xs font-mono px-3 py-2 flex items-center gap-2"
        style={{ borderLeftWidth: 3, borderLeftColor: '#F97316' }}
      >
        <span className="text-orange-400 animate-pulse">●</span>
        <span className="text-orange-400">Connecting to backend...</span>
      </div>
    );
  }

  if (status === 'down') {
    return (
      <div
        className="mx-3 mt-2 border border-error/30 bg-gradient-to-br from-error/12 to-error/4 text-xs font-mono px-3 py-2 flex items-center gap-2"
        style={{ borderLeftWidth: 3, borderLeftColor: '#EF5350' }}
      >
        <span className="text-error font-bold text-sm">!</span>
        <span className="text-error">Backend unreachable</span>
        <span className="text-muted">{message}</span>
      </div>
    );
  }

  return null;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/ErrorNotificationBar.tsx
git commit -m "refactor: ConnectionErrorBar reads from unified ConnectionManager"
```

---

### Task 4: Wire L1Page to useConnectionStatus

**Files:**
- Modify: `frontend/src/components/Terminal/pages/L1Page.tsx`

- [ ] **Step 1: Replace useBackendHealth with useConnectionStatus**

In `L1Page.tsx`, make these changes:

Replace the import:
```ts
// Old:
import { useBackendHealth } from '@/hooks/useBackendHealth';
// New:
import { useConnectionStatus } from '@/hooks/useConnectionStatus';
```

Replace the hook call (around line 27):
```ts
// Old:
const lastTickTs = useMemo(() => (lastTick ? Date.now() : null), [lastTick]);
const health = useBackendHealth(connected, lastTickTs);
// New:
const health = useConnectionStatus();
```

Remove the `useMemo` import if it's no longer used elsewhere in the file (check first — it's used for `enrichedSession` on line 41, so keep it).

The rest of L1Page already uses `health.status`, `health.message`, `health.latencyMs` — these match the `ConnectionStatus` interface, so no further changes needed.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/L1Page.tsx
git commit -m "refactor: L1Page uses unified useConnectionStatus instead of useBackendHealth"
```

---

### Task 5: Gate useMarketStream reconnect on ConnectionManager

**Files:**
- Modify: `frontend/src/hooks/useMarketStream.ts`

- [ ] **Step 1: Add waitForUp gating to reconnect**

Replace the entire file:

```ts
// frontend/src/hooks/useMarketStream.ts
import { useState, useEffect, useRef, useCallback } from 'react';
import { connectionManager } from '@/services/connectionManager';
import type { StreamTickEvent, StreamBookEvent, CandleData } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [book, setBook] = useState<StreamBookEvent | null>(null);
  const [lastCandle, setLastCandle] = useState<CandleData | null>(null);
  const [connected, setConnected] = useState(false);
  const [connectionId, setConnectionId] = useState(0);
  const esRef = useRef<EventSource | null>(null);
  const tickBuffer = useRef<StreamTickEvent[]>([]);
  const retryRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const retryDelayRef = useRef(500);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const es = new EventSource(`/api/trading/market/stream?symbol=${symbol}`);
    esRef.current = es;

    es.addEventListener('tick', (e) => {
      tickBuffer.current.push(JSON.parse(e.data));
    });

    es.addEventListener('book', (e) => {
      setBook(JSON.parse(e.data));
    });

    es.addEventListener('candle', (e) => {
      setLastCandle(JSON.parse(e.data));
    });

    es.onopen = () => {
      setConnected(true);
      setConnectionId(id => id + 1);
      retryDelayRef.current = 500;
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      esRef.current = null;

      // Wait for backend health confirmation before reconnecting
      connectionManager.waitForUp().then(() => {
        if (!mountedRef.current) return;
        retryRef.current = setTimeout(() => {
          retryDelayRef.current = Math.min(retryDelayRef.current * 2, 8_000);
          connect();
        }, retryDelayRef.current);
      });
    };
  }, [symbol]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    const flushId = setInterval(() => {
      if (tickBuffer.current.length > 0) {
        setLastTick(tickBuffer.current[tickBuffer.current.length - 1]);
        tickBuffer.current = [];
      }
    }, 500);

    return () => {
      mountedRef.current = false;
      clearTimeout(retryRef.current);
      esRef.current?.close();
      esRef.current = null;
      clearInterval(flushId);
      tickBuffer.current = [];
      setConnected(false);
    };
  }, [connect]);

  return { lastTick, book, lastCandle, connected, esRef, connectionId };
}
```

Key changes from original:
- Import `connectionManager`
- Remove `consecutiveErrorsRef` (no longer needed — ConnectionManager handles fail counting)
- `onerror`: set disconnected immediately (no consecutive threshold needed — the manager handles that), then `await waitForUp()` before scheduling reconnect
- `onopen`: no longer resets `consecutiveErrorsRef`

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useMarketStream.ts
git commit -m "refactor: useMarketStream gates reconnect on ConnectionManager.waitForUp()"
```

---

### Task 6: Gate useOddsStream reconnect on ConnectionManager

**Files:**
- Modify: `frontend/src/hooks/useOddsStream.ts`

- [ ] **Step 1: Add waitForUp gating to reconnect**

Replace the entire file:

```ts
// frontend/src/hooks/useOddsStream.ts
import { useEffect, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { connectionManager } from '@/services/connectionManager';

interface OpportunitiesResponse {
  opportunities: any[];
  [key: string]: any;
}

export function useOddsStream() {
  const queryClient = useQueryClient();
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef = useRef(1000);

  const connect = useCallback(() => {
    esRef.current?.close();
    const es = new EventSource('/api/extraction/stream');
    esRef.current = es;

    const resetDelay = () => { delayRef.current = 1000; };

    es.addEventListener('opportunity_update', (e) => {
      resetDelay();
      const update = JSON.parse(e.data);
      const queryKey = ['opportunities', update.type];
      queryClient.setQueryData<OpportunitiesResponse>(queryKey, (old) => {
        if (!old) return old;
        return {
          ...old,
          opportunities: old.opportunities.map((opp: any) =>
            opp.id === update.id ? { ...opp, ...update } : opp
          ),
        };
      });
    });

    es.addEventListener('opportunity_added', (e) => {
      resetDelay();
      const opp = JSON.parse(e.data);
      const queryKey = ['opportunities', opp.type];
      queryClient.setQueryData<OpportunitiesResponse>(queryKey, (old) => {
        if (!old) return { opportunities: [opp] };
        return {
          ...old,
          opportunities: [...old.opportunities, opp],
        };
      });
    });

    es.addEventListener('opportunity_removed', (e) => {
      resetDelay();
      const { id, type } = JSON.parse(e.data);
      const queryKey = ['opportunities', type];
      queryClient.setQueryData<OpportunitiesResponse>(queryKey, (old) => {
        if (!old) return old;
        return {
          ...old,
          opportunities: old.opportunities.filter((opp: any) => opp.id !== id),
        };
      });
    });

    es.addEventListener('tier_complete', () => {
      resetDelay();
      queryClient.invalidateQueries({ queryKey: ['opportunities'], refetchType: 'active' });
      queryClient.invalidateQueries({ queryKey: ['providers'], refetchType: 'active' });
      queryClient.invalidateQueries({ queryKey: ['specials'], refetchType: 'active' });
    });

    es.onerror = () => {
      es.close();
      queryClient.invalidateQueries({ queryKey: ['opportunities'], refetchType: 'active' });
      queryClient.invalidateQueries({ queryKey: ['providers'], refetchType: 'active' });

      // Wait for backend health confirmation before reconnecting
      connectionManager.waitForUp().then(() => {
        retryRef.current = setTimeout(() => {
          delayRef.current = Math.min(delayRef.current * 2, 30000);
          connect();
        }, delayRef.current);
      });
    };
  }, [queryClient]);

  useEffect(() => {
    connect();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      esRef.current?.close();
    };
  }, [connect]);
}
```

Key change: `onerror` calls `connectionManager.waitForUp()` before scheduling reconnect.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useOddsStream.ts
git commit -m "refactor: useOddsStream gates reconnect on ConnectionManager.waitForUp()"
```

---

### Task 7: Replace client.ts _backendDown with ConnectionManager

**Files:**
- Modify: `frontend/src/services/api/client.ts`

- [ ] **Step 1: Remove old health-check machinery, wire ConnectionManager**

At the top of `client.ts`, add the import (after existing imports, before `API_BASE`):

```ts
import { connectionManager } from '@/services/connectionManager';
```

Delete these lines (approximately lines 114-133):

```ts
// Fast connectivity state — avoids 45s+ hangs when backend is down.
// Only blocks requests when backend is *known* to be down. When status is
// unknown or up, requests proceed immediately (no await on health check).
let _backendDown = false;
let _lastHealthCheck = 0;
let _downSince = 0;
const HEALTH_CHECK_INTERVAL_MS = 3000;
// After this many ms of being "down", let requests through anyway to re-probe
const MAX_FAST_FAIL_MS = 10000;

function checkBackendInBackground(): void {
  const now = Date.now();
  if (now - _lastHealthCheck < HEALTH_CHECK_INTERVAL_MS) return;
  _lastHealthCheck = now;
  const controller = new AbortController();
  const tid = setTimeout(() => controller.abort('Health check timeout'), 3000);
  fetch('/health', { signal: controller.signal })
    .then(res => { clearTimeout(tid); _backendDown = !res.ok; if (res.ok) _downSince = 0; })
    .catch(() => { clearTimeout(tid); if (!_backendDown) { _backendDown = true; _downSince = now; } });
}
```

In `fetchWithRetry`, replace the fast-fail block (approximately lines 188-193):

```ts
// Old:
checkBackendInBackground();
if (_backendDown && _downSince && (Date.now() - _downSince) < MAX_FAST_FAIL_MS) {
  throw new NetworkError('Backend is not reachable', endpoint);
}

// New:
if (!connectionManager.isUp()) {
  throw new NetworkError('Backend is not reachable', endpoint);
}
```

Also remove the line inside the successful response handler that clears the old flag (approximately line 208):

```ts
// Delete this line:
_backendDown = false; // Backend responded — clear down flag
```

And remove the line in the network error catch block that sets the old flag (approximately line 271):

```ts
// Delete this line:
_backendDown = true;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api/client.ts
git commit -m "refactor: fetchWithRetry uses ConnectionManager.isUp() instead of _backendDown flag"
```

---

### Task 8: Delete useBackendHealth and verify no remaining imports

**Files:**
- Delete: `frontend/src/hooks/useBackendHealth.ts`

- [ ] **Step 1: Delete the file**

```bash
rm frontend/src/hooks/useBackendHealth.ts
```

- [ ] **Step 2: Search for any remaining imports**

Run: `grep -r "useBackendHealth" frontend/src/`
Expected: No matches. If any remain, update those files to use `useConnectionStatus` instead.

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add -u frontend/src/hooks/useBackendHealth.ts
git commit -m "cleanup: delete useBackendHealth, replaced by unified ConnectionManager"
```

---

### Task 9: Build verification and manual smoke test

**Files:** None (verification only)

- [ ] **Step 1: Full TypeScript build**

Run: `cd frontend && npx tsc --noEmit`
Expected: Clean build, zero errors

- [ ] **Step 2: Vite build**

Run: `cd frontend && npx vite build 2>&1 | tail -10`
Expected: Build succeeds

- [ ] **Step 3: Verify single health poller**

Open browser Network tab, filter by `/health`. Should see exactly ONE polling pattern:
- Every 10s when backend is up
- Every 3s when backend is down
- No duplicate pollers

- [ ] **Step 4: Commit build artifacts if tsconfig.tsbuildinfo changed**

```bash
git add frontend/tsconfig.tsbuildinfo 2>/dev/null
git diff --cached --quiet || git commit -m "chore: update tsbuildinfo after connection manager refactor"
```
