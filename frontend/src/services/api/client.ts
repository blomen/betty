import type { MlHealth } from '@/types/market';

// ============ Oddsboost Types ============

export interface SpecialItem {
  provider: string;
  title: string;
  description: string;
  original_odds: number | null;
  boosted_odds: number | null;
  boost_pct: number | null;
  max_stake: number | null;
  category: string;
  sport: string;
  league: string;
  event: string;
  event_time: string | null;
  expires_at: string | null;
  url: string;
  scraped_at: string;
  source: string;
  market_label: string;
  shared_providers: string[] | null;
  // Boost edge (boosted/original)
  edge_pct: number | null;
  is_positive_ev: boolean | null;
  fair_odds: number | null;
  // LLM enrichment
  llm_title: string | null;
  llm_probability: number | null;
  llm_fair_odds: number | null;
  llm_edge_pct: number | null;
  llm_reasoning: string | null;
  llm_confidence: string | null;
  // Pre-computed Kelly stake
  recommended_stake: number | null;
  kelly_fraction: number | null;
}

export interface SpecialsFilters {
  sports: string[];
  providers: string[];
  categories: string[];
}

export interface LlmHealth {
  status: string;              // ok | error | skipped | unknown
  anthropic_status: string | null;
  last_error: string | null;
  last_success_at: string | null;
  last_run_at: string | null;
  enriched_count: number;
  carried_count: number;
  candidate_count: number;
}

export interface SpecialsResponse {
  specials: SpecialItem[];
  count: number;
  ev_positive_count: number;
  matched_count: number;
  llm_count?: number;
  scraped_at: string | null;
  llm_health?: LlmHealth;
  filters?: SpecialsFilters;
}

export interface StakePreviewResult {
  recommended_stake: number;
  kelly_fraction: number;
  edge_raw: number;
  edge_used: number;
  bankroll: number;
  raw_kelly_stake: number;
  single_bet_cap: number;
  was_capped_single: boolean;
  skip_reason: string | null;
  bonus_cleared: boolean;
  min_odds_applied: number;
}

// ============ Settings Types ============

export interface ExtractionProvider {
  provider_id: string;
  name: string;
  enabled: boolean;
}

export interface ExtractionPlatform {
  platform_id: string;
  platform_name: string;
  tier: string;
  providers: ExtractionProvider[];
  sites: string[];
}

export interface ExtractionSettingsResponse {
  platforms: ExtractionPlatform[];
}

export const API_BASE = '/api';

export async function getMlHealth(): Promise<MlHealth> {
  const res = await fetch(`${API_BASE}/trading/market/ml/health`);
  return res.json();
}

// Configuration for fetch with retry
const DEFAULT_TIMEOUT_MS = 15000; // 15 seconds — fail fast, retry once
const DEFAULT_RETRIES = 1;        // 1 retry only — avoid retry storms when backend is busy
const INITIAL_BACKOFF_MS = 2000;  // 2 seconds — give backend breathing room

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
  const tid = setTimeout(() => controller.abort(), 2000);
  fetch('/health', { signal: controller.signal })
    .then(res => { clearTimeout(tid); _backendDown = !res.ok; if (res.ok) _downSince = 0; })
    .catch(() => { clearTimeout(tid); if (!_backendDown) { _backendDown = true; _downSince = now; } });
}

// Structured error classes
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public statusText: string,
    public endpoint: string,
    public isRetryable: boolean = false
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export class NetworkError extends Error {
  constructor(
    message: string,
    public endpoint: string,
    public isRetryable: boolean = true
  ) {
    super(message);
    this.name = 'NetworkError';
  }
}

export class TimeoutError extends Error {
  constructor(
    message: string = 'Request timed out',
    public endpoint: string,
    public timeoutMs: number
  ) {
    super(message);
    this.name = 'TimeoutError';
  }
}

// Determine if an error is retryable
function isRetryableStatus(status: number): boolean {
  // Retry on server errors and rate limits
  return status >= 500 || status === 429 || status === 408;
}

// Sleep helper for retry backoff
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function fetchWithRetry<T>(
  endpoint: string,
  options?: RequestInit,
  retries: number = DEFAULT_RETRIES,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  // Fast-fail if backend is known to be down (avoids 45s+ hangs per request)
  // But expire after MAX_FAST_FAIL_MS so we re-probe and recover
  checkBackendInBackground();
  if (_backendDown && _downSince && (Date.now() - _downSince) < MAX_FAST_FAIL_MS) {
    throw new NetworkError('Backend is not reachable', endpoint);
  }

  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);
      _backendDown = false; // Backend responded — clear down flag

      if (!response.ok) {
        const isRetryable = isRetryableStatus(response.status);

        // If not retryable or last attempt, throw immediately
        if (!isRetryable || attempt === retries) {
          // Try to extract error detail from response body
          let errorDetail = '';
          try {
            const errorBody = await response.json();
            errorDetail = errorBody.detail || errorBody.message || errorBody.error || '';
          } catch {
            // Ignore JSON parse errors
          }

          const errorMessage = errorDetail
            ? `${errorDetail}`
            : `API error: ${response.status} ${response.statusText}`;

          throw new ApiError(
            errorMessage,
            response.status,
            response.statusText,
            endpoint,
            isRetryable
          );
        }

        // Retryable error - calculate backoff and retry
        const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt);
        console.warn(
          `API request failed (attempt ${attempt + 1}/${retries + 1}): ${response.status}, retrying in ${backoffMs}ms`
        );
        await sleep(backoffMs);
        continue;
      }

      return response.json();
    } catch (error) {
      clearTimeout(timeoutId);

      // Handle abort/timeout
      if (error instanceof DOMException && error.name === 'AbortError') {
        lastError = new TimeoutError(
          `Request to ${endpoint} timed out after ${timeoutMs}ms`,
          endpoint,
          timeoutMs
        );

        // Retry on timeout
        if (attempt < retries) {
          const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt);
          console.warn(
            `Request timed out (attempt ${attempt + 1}/${retries + 1}), retrying in ${backoffMs}ms`
          );
          await sleep(backoffMs);
          continue;
        }
      }

      // Handle network errors — mark backend as down for fast-fail
      if (error instanceof TypeError && error.message.includes('fetch')) {
        _backendDown = true;
        lastError = new NetworkError(
          `Network error: ${error.message}`,
          endpoint
        );

        // Retry on network errors
        if (attempt < retries) {
          const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt);
          console.warn(
            `Network error (attempt ${attempt + 1}/${retries + 1}), retrying in ${backoffMs}ms`
          );
          await sleep(backoffMs);
          continue;
        }
      }

      // Re-throw ApiError or save for later
      if (error instanceof ApiError) {
        lastError = error;
        if (!error.isRetryable) {
          throw error;
        }
      } else {
        lastError = error instanceof Error ? error : new Error(String(error));
      }
    }
  }

  // All retries exhausted
  throw lastError || new Error(`Request failed after ${retries + 1} attempts`);
}

// Legacy fetchJson for backward compatibility (uses retry internally)
export async function fetchJson<T>(endpoint: string, options?: RequestInit, timeoutMs?: number): Promise<T> {
  return fetchWithRetry<T>(endpoint, options, DEFAULT_RETRIES, timeoutMs ?? DEFAULT_TIMEOUT_MS);
}
