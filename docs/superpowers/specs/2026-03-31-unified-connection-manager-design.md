# Unified ConnectionManager Design

## Problem

Three independent systems track backend connectivity with different thresholds, polling intervals, and state. They make contradictory decisions, causing "Backend unreachable" false alarms, SSE reconnect storms, and an endless cycle of band-aid fixes (15 connection-related commits in 5 days).

| System | Location | Polls `/health` | Threshold | Recovery |
|--------|----------|-----------------|-----------|----------|
| `ConnectionErrorBar` | ErrorNotificationBar.tsx | 30s ok / 3s down | 3 consecutive fails | 1 success |
| `useBackendHealth` | useBackendHealth.ts | 10s ok / 3s down | 2 consecutive fails | 1 success |
| `_backendDown` flag | client.ts | 3s background | 2 fails, auto-expires 10s | Auto-expire or 1 success |

Additionally, SSE streams (`useMarketStream`, `useOddsStream`) reconnect independently — they don't know whether the backend is up, so they hammer a dead backend with reconnect attempts.

## Solution

### ConnectionManager singleton

A plain TypeScript module (not a React hook) that owns all connection state. Importable by both React components and plain modules like `client.ts`.

**File:** `frontend/src/services/connectionManager.ts`

```ts
type ConnectionState = 'checking' | 'connecting' | 'ok' | 'slow' | 'down';
type Listener = (state: ConnectionState, latencyMs: number | null, message: string) => void;

class ConnectionManager {
  // --- Public API ---
  getState(): ConnectionState;
  getLatency(): number | null;
  getMessage(): string;
  isUp(): boolean;                    // sync — for fetchWithRetry fast-fail
  waitForUp(): Promise<void>;         // async — resolves when state becomes 'ok' or 'slow'
  subscribe(fn: Listener): () => void; // returns unsubscribe function

  // --- Internal ---
  private state: ConnectionState = 'checking';
  private latencyMs: number | null = null;
  private message: string = 'Checking...';
  private consecutiveFails: number = 0;
  private everConnected: boolean = false;
  private mountTime: number = Date.now();
  private listeners: Set<Listener>;
  private pollTimer: ReturnType<typeof setTimeout> | null;
  private waitResolvers: Set<() => void>;  // for waitForUp()
}

export const connectionManager = new ConnectionManager();
```

### State machine

```
checking  → ok          first successful health check
checking  → connecting  fail during 30s startup grace period
connecting → ok         health check succeeds
connecting → down       grace expires + 2 consecutive fails
ok        → down        2 consecutive fails
ok        → slow        2 consecutive slow responses (>3s)
down      → ok          1 successful health check (recover fast)
slow      → ok          1 successful health check
```

Key behavior:
- **Recover on first success** — no consecutive-success requirement, fast recovery
- **Fail on 2 consecutive failures** — skip transient blips
- **30s startup grace** — show "connecting" not "down" on cold start
- **Poll 10s when ok, 3s when down/connecting** — detect recovery fast without hammering

### useConnectionStatus hook

**File:** `frontend/src/hooks/useConnectionStatus.ts`

Thin React bridge — subscribes to the manager and triggers re-renders.

```ts
interface ConnectionStatus {
  status: ConnectionState;
  latencyMs: number | null;
  message: string;
}

export function useConnectionStatus(): ConnectionStatus;
```

### SSE reconnect gating

Both `useMarketStream` and `useOddsStream` gate reconnection on the manager:

```ts
// Before (current):
es.onerror = () => {
  es.close();
  retryRef.current = setTimeout(() => {
    connect();  // hammers dead backend
  }, retryDelayRef.current);
};

// After:
es.onerror = () => {
  es.close();
  // Wait for backend to be confirmed up before reconnecting
  connectionManager.waitForUp().then(() => {
    if (!mountedRef.current) return;
    retryRef.current = setTimeout(() => {
      retryDelayRef.current = Math.min(retryDelayRef.current * 2, 8_000);
      connect();
    }, retryDelayRef.current);
  });
};
```

This means: if the backend crashed, SSE streams park themselves and wait for the health poller to confirm recovery, then reconnect with backoff.

### fetchWithRetry integration

**File:** `frontend/src/services/api/client.ts`

Replace the `_backendDown` / `_downSince` / `checkBackendInBackground` machinery:

```ts
// Before:
checkBackendInBackground();
if (_backendDown && _downSince && (Date.now() - _downSince) < MAX_FAST_FAIL_MS) {
  throw new NetworkError('Backend is not reachable', endpoint);
}

// After:
if (!connectionManager.isUp()) {
  throw new NetworkError('Backend is not reachable', endpoint);
}
```

No more auto-expiring fast-fail timer. The manager's health poller is the source of truth — when it confirms recovery, `isUp()` returns true.

## Files Changed

### Deleted
| File/code | Reason |
|-----------|--------|
| `useBackendHealth.ts` | Replaced by `useConnectionStatus` |
| `ConnectionErrorBar` health polling logic | Reads from `useConnectionStatus` instead |
| `client.ts` `_backendDown`, `_downSince`, `_lastHealthCheck`, `checkBackendInBackground()`, `HEALTH_CHECK_INTERVAL_MS`, `MAX_FAST_FAIL_MS` | Replaced by `connectionManager.isUp()` |

### Created
| File | Purpose |
|------|---------|
| `frontend/src/services/connectionManager.ts` | Singleton: health polling, state machine, subscribe/isUp/waitForUp |
| `frontend/src/hooks/useConnectionStatus.ts` | Thin React hook: subscribes to manager |

### Modified
| File | Change |
|------|--------|
| `ErrorNotificationBar.tsx` | `ConnectionErrorBar` uses `useConnectionStatus()` instead of own polling |
| `useMarketStream.ts` | Gate reconnect with `connectionManager.waitForUp()` |
| `useOddsStream.ts` | Gate reconnect with `connectionManager.waitForUp()` |
| `client.ts` | Replace `_backendDown` machinery with `connectionManager.isUp()` |
| `L1Page.tsx` | Replace `useBackendHealth(connected, lastTickTs)` with `useConnectionStatus()` |

### Not changed
| File | Reason |
|------|--------|
| `TradingContainer.tsx` | Still uses `useMarketStream` — gating happens inside the hook |
| Backend endpoints | No changes needed — `/health`, SSE streams, keepalive all stay the same |

## Edge Cases

**Backend restarts during extraction:** Health poller detects failure within 3s (fast poll). SSE streams park via `waitForUp()`. Once health returns ok, streams reconnect with backoff. No request storm.

**Transient network blip (1 failed health check):** Single failure doesn't trigger state change (requires 2 consecutive). SSE streams may see an error, call `waitForUp()`, but it resolves immediately since manager is still in 'ok' state.

**Browser tab backgrounded:** Browser may throttle timers. On tab focus, health poller fires immediately (stale timer catches up). SSE EventSource handles reconnection natively via the browser.

**Backend slow but not down:** 2 consecutive responses >3s → state becomes 'slow'. `isUp()` still returns true (slow is not down). UI shows warning but requests proceed.

## Testing

- Manual: start frontend without backend → should show "Connecting..." then "Backend unreachable" after 30s
- Manual: start backend after frontend → should recover to green within 3s
- Manual: kill backend while streaming → SSE should park, recover when backend restarts
- Manual: verify no duplicate `/health` polling in Network tab (should be exactly one poller)
