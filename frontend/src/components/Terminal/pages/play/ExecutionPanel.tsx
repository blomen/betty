import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { ProviderName } from '../../ProviderName';
import { resolveOutcome, marketLabel } from '@/utils/betting';
import { api } from '@/services/api';
import type { BatchBet, WageringProjection } from '@/types';
import { fetchJson } from '@/services/api/client';



// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
  onBack: () => void;
}

interface ExecutionState {
  placedBets: string[];
  sessionStartTime: number;
  batchHash: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'play-v3-execution';
const MAX_SESSION_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours

const TIER_CLASSES: Record<string, string> = {
  polymarket: 'text-tabPolymarket',
  pinnacle: 'text-tabReverse',
  soft: 'text-success',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function betKey(b: BatchBet): string {
  return `${b.provider_id}:${b.event_id}:${b.market}:${b.outcome}:${b.point ?? ''}`;
}

function computeBatchHash(batch: BatchBet[]): string {
  const keys = batch.map(betKey).sort().join('|');
  return keys.slice(0, 64);
}

function formatElapsed(ms: number): string {
  const totalSecs = Math.floor(ms / 1000);
  const mins = Math.floor(totalSecs / 60);
  const secs = totalSecs % 60;
  if (mins === 0) return `${secs}s`;
  return `${mins}m${secs.toString().padStart(2, '0')}s`;
}

function loadState(batchHash: string): ExecutionState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: ExecutionState = JSON.parse(raw);
    if (parsed.batchHash !== batchHash) return null;
    if (Date.now() - parsed.sessionStartTime > MAX_SESSION_AGE_MS) return null;
    return parsed;
  } catch {
    return null;
  }
}

function saveState(state: ExecutionState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // localStorage full or unavailable — silently ignore
  }
}

// ---------------------------------------------------------------------------
// Provider grouping
// ---------------------------------------------------------------------------

interface ProviderGroup {
  providerId: string;
  tier: 'polymarket' | 'pinnacle' | 'soft';
  cluster: string | null;
  bets: BatchBet[];
  totalStake: number;
  totalEV: number;
  wageringRemaining: number | null;
  daysRemaining: number | null;
}

function groupByProvider(
  batch: BatchBet[],
  wageringProjections: WageringProjection[],
): ProviderGroup[] {
  const map = new Map<string, ProviderGroup>();

  for (const b of batch) {
    if (!map.has(b.provider_id)) {
      const proj = wageringProjections.find((p) => p.provider_id === b.provider_id);
      map.set(b.provider_id, {
        providerId: b.provider_id,
        tier: b.tier,
        cluster: b.cluster,
        bets: [],
        totalStake: 0,
        totalEV: 0,
        wageringRemaining: proj?.projected_remaining ?? null,
        daysRemaining: proj?.days_remaining ?? null,
      });
    }
    const g = map.get(b.provider_id)!;
    g.bets.push(b);
    g.totalStake += b.stake;
    g.totalEV += b.expected_profit;
  }

  // Sort: polymarket first, pinnacle second, soft by EV desc
  const groups = Array.from(map.values());
  groups.sort((a, b) => {
    if (a.tier === 'polymarket' && b.tier !== 'polymarket') return -1;
    if (b.tier === 'polymarket' && a.tier !== 'polymarket') return 1;
    if (a.tier === 'pinnacle' && b.tier !== 'pinnacle') return -1;
    if (b.tier === 'pinnacle' && a.tier !== 'pinnacle') return 1;
    return b.totalEV - a.totalEV;
  });

  return groups;
}

// ---------------------------------------------------------------------------
// StatusIcon
// ---------------------------------------------------------------------------

function StatusIcon({ status }: { status: 'done' | 'in-progress' | 'pending' }) {
  if (status === 'done') {
    return <span className="text-success text-sm font-bold">✓</span>;
  }
  if (status === 'in-progress') {
    return <span className="text-amber-400 text-sm font-bold">▶</span>;
  }
  return <span className="text-muted2 text-sm">○</span>;
}

// ---------------------------------------------------------------------------
// ProviderSection
// ---------------------------------------------------------------------------

interface ProviderSectionProps {
  group: ProviderGroup;
  isExpanded: boolean;
  onToggle: () => void;
  placedSet: Set<string>;
  onMarkAllDone: (keys: string[]) => void;
  mirrorProvider: string | null; // Currently detected provider in mirror
}

function ProviderSection({
  group,
  isExpanded,
  onToggle,
  placedSet,
  onMarkAllDone,
  mirrorProvider,
}: ProviderSectionProps) {
  // Polymarket: live edge from mirror tabs. Soft: null (use batch data).
  const [liveEdge, setLiveEdge] = useState<Record<string, any> | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  const betKeys = group.bets.map(betKey);
  const placedCount = betKeys.filter((k) => placedSet.has(k)).length;
  const totalCount = betKeys.length;
  const allDone = placedCount === totalCount;
  const anyDone = placedCount > 0;
  const status: 'done' | 'in-progress' | 'pending' = allDone
    ? 'done'
    : anyDone
    ? 'in-progress'
    : 'pending';

  const tierClass = TIER_CLASSES[group.tier] ?? 'text-success';
  const isPoly = group.providerId === 'polymarket';

  // Is the mirror currently connected to this provider?
  const isConnected = mirrorProvider === group.providerId;

  const batchPayload = useMemo(() => group.bets.map((b) => ({
    event_id: b.event_id,
    market: b.market,
    outcome: b.outcome,
    odds: b.odds,
    stake: b.stake,
  })), [group.bets]);

  // Scan: fetch live edge from mirror (Polymarket only)
  const handleScan = useCallback(async () => {
    setScanning(true);
    setScanError(null);
    try {
      const result = await api.getLiveEdge(batchPayload);
      const map: Record<string, any> = {};
      for (const b of result.bets ?? []) {
        map[b.bet_id] = b;
      }
      setLiveEdge(map);
    } catch (err: any) {
      setScanError(err.message || 'Scan failed');
    } finally {
      setScanning(false);
    }
  }, [batchPayload]);

  // Auto-scan when mirror connects to this provider (Polymarket)
  const autoScanned = useRef(false);
  useEffect(() => {
    if (isPoly && isConnected && isExpanded && !allDone && !liveEdge && !scanning && !autoScanned.current) {
      autoScanned.current = true;
      handleScan();
    }
  }, [isPoly, isConnected, isExpanded, allDone, liveEdge, scanning, handleScan]);

  // For all providers: has edge data been reviewed?
  // Poly: after scan. Soft: always ready (batch edge is current).
  const scanned = isPoly ? liveEdge !== null : true;

  // Build display data per bet
  const betRows = group.bets.map((b, i) => {
    const key = betKey(b);
    const placed = placedSet.has(key);
    const live = isPoly && liveEdge ? liveEdge[i] : null;

    const displayOdds = live?.live_odds ?? b.odds;
    const displayFair = live?.fair_odds ?? b.fair_odds;
    const displayEdge = live?.edge_pct ?? b.edge_pct;
    const betStatus = live?.status ?? (b.edge_pct > 0 ? 'value' : 'negative');

    return { b, i, key, placed, displayOdds, displayFair, displayEdge, betStatus };
  });

  const valueBets = betRows.filter(r => !r.placed && r.betStatus === 'value');
  const skippedBets = betRows.filter(r => !r.placed && r.betStatus !== 'value');

  // Confirm: mark all +EV bets as placed
  const handleConfirm = () => {
    const keysToPlace = valueBets.map(r => r.key);
    if (keysToPlace.length > 0) {
      onMarkAllDone(keysToPlace);
    }
  };

  const stakeDisplay = isPoly
    ? `$${Math.round(group.totalStake)} USDC`
    : `${Math.round(group.totalStake)} kr`;

  return (
    <div className={`border ${isExpanded ? 'border-border' : 'border-border/50'} bg-panel`}>
      {/* Header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-panel2/50 transition-colors text-left"
      >
        <StatusIcon status={status} />

        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${isConnected ? 'bg-success' : 'bg-border'}`}
          title={isConnected ? 'Mirror connected' : 'Not connected'} />

        <span className={`text-sm font-medium ${tierClass}`}>
          <ProviderName name={group.providerId} />
        </span>

        {group.cluster && !isPoly && (
          <span className="text-[10px] px-1.5 py-0.5 bg-border text-muted border border-border">
            {group.cluster}
          </span>
        )}

        <span className="text-sm text-muted ml-1">
          {placedCount}/{totalCount} bets
        </span>
        <span className="text-sm text-muted">·</span>
        <span className="text-sm text-text">{stakeDisplay}</span>

        <span className="ml-auto text-sm text-muted">
          {allDone ? 'Done' : anyDone ? 'In progress' : 'Pending'}
        </span>

        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`flex-shrink-0 text-muted transition-transform ${isExpanded ? 'rotate-180' : ''}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {/* Expanded content */}
      {isExpanded && (
        <div className="border-t border-border">
          <table className="sq w-full">
            <colgroup>
              <col />
              <col style={{ width: '60px' }} />
              <col style={{ width: '55px' }} />
              <col style={{ width: '55px' }} />
              <col style={{ width: '55px' }} />
              <col style={{ width: '65px' }} />
              <col style={{ width: '40px' }} />
            </colgroup>
            <thead className="bg-panel">
              <tr>
                <th className="text-left">Event · Outcome</th>
                <th className="text-right">Market</th>
                <th className="text-right">{isPoly && scanned ? 'Live' : 'Odds'}</th>
                <th className="text-right">Fair</th>
                <th className="text-right">Edge</th>
                <th className="text-right">Stake</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {betRows.map(({ b, key, placed, displayOdds, displayFair, displayEdge, betStatus }) => {
                const eventName = `${b.display_home} v ${b.display_away}`;
                const outcomeLabel = resolveOutcome(
                  b.outcome,
                  {
                    home_team: b.display_home,
                    away_team: b.display_away,
                    display_home: b.display_home,
                    display_away: b.display_away,
                    market: b.market,
                  },
                  b.point,
                  false,
                );
                const stakeText = isPoly
                  ? `$${b.stake.toFixed(1)}`
                  : `${Math.round(b.stake)} kr`;

                const edgeColor = displayEdge > 5
                  ? 'text-success'
                  : displayEdge > 0
                  ? 'text-amber-400'
                  : 'text-error';

                return (
                  <tr
                    key={key}
                    className={`${placed ? 'opacity-40 line-through' : betStatus === 'negative' ? 'opacity-50' : ''} transition-opacity`}
                  >
                    <td className="!py-1.5">
                      <div className="text-sm text-text truncate max-w-[280px]" title={eventName}>
                        {eventName}
                      </div>
                      <div className="text-[11px] text-muted">{outcomeLabel}{b.point != null ? ` (${b.point > 0 ? '+' : ''}${b.point})` : ''}</div>
                    </td>
                    <td className="text-right text-sm text-muted">{marketLabel(b.market)}</td>
                    <td className="text-right text-sm text-text font-medium">{displayOdds.toFixed(2)}</td>
                    <td className="text-right text-sm text-muted">{displayFair?.toFixed(2) ?? '—'}</td>
                    <td className={`text-right text-sm font-semibold ${edgeColor}`}>
                      {displayEdge != null ? `${displayEdge > 0 ? '+' : ''}${displayEdge.toFixed(1)}%` : '—'}
                    </td>
                    <td className="text-right text-sm text-text">{stakeText}</td>
                    <td className="text-center text-[10px]">
                      {placed && <span className="text-success font-bold">✓</span>}
                      {!placed && betStatus === 'negative' && <span className="text-error">skip</span>}
                      {!placed && betStatus === 'error' && <span className="text-error">err</span>}
                      {!placed && betStatus === 'no-sharp' && <span className="text-muted">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Actions */}
          {!allDone && (
            <div className="px-3 py-2 border-t border-border flex items-center justify-between">
              <div className="flex items-center gap-2">
                {scanning && <span className="text-[10px] text-muted animate-pulse">Scanning live prices...</span>}
                {scanError && <span className="text-[10px] text-error">{scanError}</span>}
                {scanned && !scanning && (
                  <span className="text-[10px] text-muted">
                    {valueBets.length} to place{skippedBets.length > 0 ? `, ${skippedBets.length} skipped` : ''}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {isPoly && !scanned && (
                  <button
                    onClick={handleScan}
                    disabled={scanning}
                    className="px-3 py-1 bg-tabPolymarket text-bg text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
                  >
                    {scanning ? 'Scanning...' : 'Scan'}
                  </button>
                )}
                {isPoly && scanned && (
                  <button
                    onClick={handleScan}
                    disabled={scanning}
                    className="px-2 py-1 bg-border text-text text-xs hover:opacity-90 transition-opacity disabled:opacity-50"
                  >
                    Rescan
                  </button>
                )}
                {scanned && valueBets.length > 0 && (
                  <button
                    onClick={handleConfirm}
                    className="px-3 py-1 bg-success text-bg text-xs font-medium hover:opacity-90 transition-opacity"
                  >
                    Confirm {valueBets.length} bets
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ExecutionPanel
// ---------------------------------------------------------------------------

export function ExecutionPanel({ batch, wageringProjections, onBack }: Props) {
  const batchHash = useMemo(() => computeBatchHash(batch), [batch]);

  // Load persisted state (or initialize fresh)
  const [placedBets, setPlacedBets] = useState<Set<string>>(() => {
    const saved = loadState(batchHash);
    return saved ? new Set(saved.placedBets) : new Set();
  });

  const [sessionStartTime] = useState<number>(() => {
    const saved = loadState(batchHash);
    return saved ? saved.sessionStartTime : Date.now();
  });

  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // Poll mirror status every 3s to detect connected provider
  const [mirrorProvider, setMirrorProvider] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await fetchJson<{ detected_provider?: string }>('/mirror/status');
        if (!cancelled) setMirrorProvider(status.detected_provider ?? null);
      } catch {
        if (!cancelled) setMirrorProvider(null);
      }
    };
    poll();
    const interval = setInterval(poll, 3_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // Tick elapsed time every second
  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(Date.now() - sessionStartTime);
    }, 1000);
    return () => clearInterval(interval);
  }, [sessionStartTime]);

  // Persist state whenever placedBets changes
  useEffect(() => {
    saveState({
      placedBets: Array.from(placedBets),
      sessionStartTime,
      batchHash,
    });
  }, [placedBets, sessionStartTime, batchHash]);

  // Reset when batch changes to a different hash
  useEffect(() => {
    const saved = loadState(batchHash);
    if (!saved) {
      setPlacedBets(new Set());
    }
  }, [batchHash]);

  // Group bets by provider
  const groups = useMemo(
    () => groupByProvider(batch, wageringProjections),
    [batch, wageringProjections],
  );

  // Progress stats
  const totalBets = batch.length;
  const placedCount = useMemo(
    () => batch.filter((b) => placedBets.has(betKey(b))).length,
    [batch, placedBets],
  );
  const totalProviders = groups.length;
  const doneProviders = useMemo(
    () =>
      groups.filter((g) =>
        g.bets.every((b) => placedBets.has(betKey(b))),
      ).length,
    [groups, placedBets],
  );

  const stakedSoFar = useMemo(
    () =>
      batch
        .filter((b) => placedBets.has(betKey(b)))
        .reduce((s, b) => s + b.stake, 0),
    [batch, placedBets],
  );
  const totalStake = batch.reduce((s, b) => s + b.stake, 0);
  const evCaptured = useMemo(
    () =>
      batch
        .filter((b) => placedBets.has(betKey(b)))
        .reduce((s, b) => s + b.expected_profit, 0),
    [batch, placedBets],
  );
  const totalEV = batch.reduce((s, b) => s + b.expected_profit, 0);

  const progressPct = totalBets > 0 ? (placedCount / totalBets) * 100 : 0;

  // Handlers
  const handleMarkAllDone = useCallback((keys: string[]) => {
    setPlacedBets((prev) => {
      const next = new Set(prev);
      for (const k of keys) next.add(k);
      return next;
    });
  }, []);

  const handleToggleProvider = useCallback((providerId: string) => {
    setExpandedProvider((prev) => (prev === providerId ? null : providerId));
  }, []);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col gap-2">
      {/* Progress bar */}
      <div className="border border-border bg-bg px-3 py-2">
        {/* Progress track */}
        <div className="h-1.5 bg-border mb-2 overflow-hidden">
          <div
            className="h-full bg-tabPlay transition-all"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        {/* Progress labels */}
        <div className="flex items-center gap-3 text-sm">
          <span className="text-text font-medium">
            {placedCount} / {totalBets} bets placed
          </span>
          <span className="text-muted">·</span>
          <span className="text-muted">
            {doneProviders} / {totalProviders} providers done
          </span>
          <span className="text-muted">·</span>
          <span className="text-muted">{formatElapsed(elapsed)} elapsed</span>
        </div>
      </div>

      {/* Provider accordion */}
      <div className="flex flex-col gap-1">
        {groups.map((group) => (
          <ProviderSection
            key={group.providerId}
            group={group}
            isExpanded={expandedProvider === group.providerId}
            onToggle={() => handleToggleProvider(group.providerId)}
            placedSet={placedBets}
            onMarkAllDone={handleMarkAllDone}
            mirrorProvider={mirrorProvider}
          />
        ))}
      </div>

      {/* Session summary bar */}
      <div className="border border-border bg-panel px-3 py-2 flex items-center gap-4 text-sm">
        <button
          onClick={onBack}
          className="px-3 py-1 text-xs bg-tabPlay text-bg font-medium hover:opacity-90 transition-opacity"
        >
          ← Back
        </button>
        <span className="text-muted">Session:</span>
        <span className="text-text font-medium">
          {Math.round(stakedSoFar)} / {Math.round(totalStake)} kr staked
        </span>
        <span className="text-muted">·</span>
        <span className="text-success font-medium">
          +{Math.round(evCaptured)} / +{Math.round(totalEV)} EV captured
        </span>
        <span className="text-muted">·</span>
        <span className="text-muted">{formatElapsed(elapsed)}</span>
      </div>
    </div>
  );
}
