// frontend/src/services/connectionManager.ts

export type ConnectionState = 'checking' | 'connecting' | 'restarting' | 'ok' | 'slow' | 'down';

type Listener = (state: ConnectionState, latencyMs: number | null, message: string) => void;

const POLL_OK_MS = 10_000;
const POLL_DOWN_MS = 3_000;
const SLOW_THRESHOLD_MS = 3000;
const STARTUP_GRACE_MS = 30_000;
const HEALTH_TIMEOUT_MS = 10000;
const CONSECUTIVE_FAIL_THRESHOLD = 2;
// After this many consecutive fails, escalate from "restarting" to "down"
const DOWN_ESCALATION_THRESHOLD = 20; // ~60s at 3s intervals

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
  private _lastBootId: string | null = null;

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

  /** Sync check — true when backend is responding or state is unknown (don't block during startup). */
  isUp(): boolean {
    return this._state !== 'down' && this._state !== 'restarting';
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
      state === 'down' || state === 'connecting' || state === 'restarting' ? POLL_DOWN_MS : POLL_OK_MS
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
      } else {
        const data = await res.json().catch(() => null);
        const bootId = data?.boot_id ?? null;
        const uptime = data?.uptime ?? null;

        // Detect restart: boot_id changed means backend restarted
        const restarted = this._lastBootId !== null && bootId !== null && bootId !== this._lastBootId;
        if (bootId) this._lastBootId = bootId;

        if (latency > SLOW_THRESHOLD_MS) {
          this._onSlow(latency);
        } else {
          this._onSuccess(latency, restarted, uptime);
        }
      }
    } catch {
      const latency = Math.round(performance.now() - t0);
      this._onFail(latency, 'Backend unreachable');
    }
  }

  private _onSuccess(latency: number, restarted: boolean, uptime: number | null) {
    this._consecutiveFails = 0;
    this._everConnected = true;

    if (restarted && uptime !== null && uptime < 60) {
      // Backend just restarted — show brief "restarted" message, auto-clears on next poll
      this._setState('ok', latency, `Restarted (up ${uptime}s)`);
    } else {
      this._setState('ok', latency, '');
    }
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
    } else if (this._everConnected && this._consecutiveFails < DOWN_ESCALATION_THRESHOLD) {
      // Was connected before — likely a restart, not a real outage
      this._setState('restarting', latency, 'Backend restarting...');
    } else if (this._consecutiveFails >= CONSECUTIVE_FAIL_THRESHOLD) {
      this._setState('down', latency, reason);
    } else {
      // Single failure — keep current state, retry quickly
      this._schedulePoll(POLL_DOWN_MS);
    }
  }
}

export const connectionManager = new ConnectionManager();
