import { useState, useEffect, useMemo, useCallback } from 'react';
import { ProviderName } from '../../ProviderName';
import { resolveOutcome, marketLabel } from '@/utils/betting';
import type { BatchBet, WageringProjection } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
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

const TIER_COLORS: Record<string, string> = {
  polymarket: '#a855f7',
  pinnacle: '#ef4444',
  soft: '#22c55e',
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
    return <span className="text-[#22c55e] text-sm font-bold">✓</span>;
  }
  if (status === 'in-progress') {
    return <span className="text-amber-400 text-sm font-bold">▶</span>;
  }
  return <span className="text-dark-600 text-sm">○</span>;
}

// ---------------------------------------------------------------------------
// CheckCircle
// ---------------------------------------------------------------------------

function CheckCircle({
  checked,
  onToggle,
}: {
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className={`w-5 h-5 rounded-full border flex items-center justify-center flex-shrink-0 transition-colors ${
        checked
          ? 'border-[#22c55e] bg-[#22c55e]/20 text-[#22c55e]'
          : 'border-dark-600 bg-transparent text-transparent hover:border-muted'
      }`}
      title={checked ? 'Mark as pending' : 'Mark as placed'}
    >
      <span className="text-[10px] font-bold leading-none">✓</span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// ProviderSection
// ---------------------------------------------------------------------------

interface ProviderSectionProps {
  group: ProviderGroup;
  isExpanded: boolean;
  onToggle: () => void;
  placedSet: Set<string>;
  onToggleBet: (key: string) => void;
  onMarkAllDone: (keys: string[]) => void;
}

function ProviderSection({
  group,
  isExpanded,
  onToggle,
  placedSet,
  onToggleBet,
  onMarkAllDone,
}: ProviderSectionProps) {
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

  const tierColor = TIER_COLORS[group.tier] ?? '#22c55e';

  return (
    <div className={`border ${isExpanded ? 'border-dark-700' : 'border-dark-800'} bg-dark-900`}>
      {/* Header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-dark-800/50 transition-colors text-left"
      >
        {/* Status icon */}
        <StatusIcon status={status} />

        {/* Provider name */}
        <span
          className="text-[12px] font-semibold"
          style={{ color: tierColor }}
        >
          <ProviderName name={group.providerId} />
        </span>

        {/* Cluster tag */}
        {group.cluster && (
          <span className="text-[10px] px-1.5 py-0.5 bg-dark-700 text-muted border border-dark-600">
            {group.cluster}
          </span>
        )}

        {/* Wagering badge */}
        {group.wageringRemaining !== null && (
          <span className="text-[10px] px-1.5 py-0.5 bg-purple-900/40 text-purple-400 border border-purple-800/40">
            {Math.round(group.wageringRemaining)} kr left
            {group.daysRemaining !== null && ` · ${group.daysRemaining}d`}
          </span>
        )}

        {/* Stats */}
        <span className="text-[11px] text-muted ml-1">
          {placedCount}/{totalCount} bets
        </span>
        <span className="text-[11px] text-muted">·</span>
        <span className="text-[11px] text-text">{Math.round(group.totalStake)} kr</span>
        <span className="text-[11px] text-muted">·</span>
        <span className="text-[11px] text-[#22c55e]">+{Math.round(group.totalEV)} EV</span>

        {/* Status text */}
        <span className="ml-auto text-[11px] text-muted">
          {allDone ? 'Done' : anyDone ? 'In progress' : 'Pending'}
        </span>

        {/* Chevron */}
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
        <div className="border-t border-dark-700">
          {/* Bet table */}
          <table className="sq w-full">
            <colgroup>
              <col style={{ width: '28px' }} />
              <col />
              <col style={{ width: '80px' }} />
              <col style={{ width: '50px' }} />
              <col style={{ width: '50px' }} />
              <col style={{ width: '55px' }} />
              <col style={{ width: '65px' }} />
              <col style={{ width: '55px' }} />
            </colgroup>
            <thead className="bg-dark-800">
              <tr>
                <th className="text-left"></th>
                <th className="text-left text-[11px]">Event · Outcome</th>
                <th className="text-right text-[11px]">Market</th>
                <th className="text-right text-[11px]">Odds</th>
                <th className="text-right text-[11px]">Fair</th>
                <th className="text-right text-[11px]">Edge%</th>
                <th className="text-right text-[11px]">Stake</th>
                <th className="text-right text-[11px]">EV</th>
              </tr>
            </thead>
            <tbody>
              {group.bets.map((b) => {
                const key = betKey(b);
                const placed = placedSet.has(key);
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

                return (
                  <tr
                    key={key}
                    className={`${placed ? 'opacity-40' : ''} transition-opacity`}
                  >
                    <td className="!py-1.5 !px-2">
                      <CheckCircle
                        checked={placed}
                        onToggle={() => onToggleBet(key)}
                      />
                    </td>
                    <td className="!py-1.5">
                      <div className="text-[11px] text-text truncate max-w-[220px]" title={eventName}>
                        {eventName}
                      </div>
                      <div className="text-[10px] text-muted">{outcomeLabel}</div>
                    </td>
                    <td className="text-right text-[11px] text-muted">{marketLabel(b.market)}</td>
                    <td className="text-right text-[11px] text-text font-medium">{b.odds.toFixed(2)}</td>
                    <td className="text-right text-[11px] text-muted">{b.fair_odds.toFixed(2)}</td>
                    <td
                      className={`text-right text-[11px] font-semibold ${
                        b.edge_pct > 0 ? 'text-[#22c55e]' : 'text-error'
                      }`}
                    >
                      {b.edge_pct > 0 ? '+' : ''}{b.edge_pct.toFixed(1)}%
                    </td>
                    <td className="text-right text-[11px] text-text">{Math.round(b.stake)} kr</td>
                    <td className="text-right text-[11px] text-[#22c55e]">+{Math.round(b.expected_profit)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Mark All Done */}
          {!allDone && (
            <div className="px-3 py-2 border-t border-dark-700 flex justify-end">
              <button
                onClick={() => onMarkAllDone(betKeys)}
                className="px-3 py-1 bg-success text-black text-[11px] font-bold hover:opacity-90 transition-opacity"
              >
                Mark All Done
              </button>
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

export function ExecutionPanel({ batch, wageringProjections }: Props) {
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
  const handleToggleBet = useCallback((key: string) => {
    setPlacedBets((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

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
      <div className="border border-dark-700 bg-dark-900 px-3 py-2">
        {/* Progress track */}
        <div className="h-1.5 bg-dark-700 mb-2 overflow-hidden">
          <div
            className="h-full bg-[#22c55e] transition-all"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        {/* Progress labels */}
        <div className="flex items-center gap-3 text-[12px]">
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
            onToggleBet={handleToggleBet}
            onMarkAllDone={handleMarkAllDone}
          />
        ))}
      </div>

      {/* Session summary bar */}
      <div className="border border-dark-700 bg-dark-800 px-3 py-2 flex items-center gap-4 text-[12px]">
        <span className="text-muted">Session:</span>
        <span className="text-text font-medium">
          {Math.round(stakedSoFar)} / {Math.round(totalStake)} kr staked
        </span>
        <span className="text-muted">·</span>
        <span className="text-[#22c55e] font-medium">
          +{Math.round(evCaptured)} / +{Math.round(totalEV)} EV captured
        </span>
        <span className="text-muted">·</span>
        <span className="text-muted">{formatElapsed(elapsed)}</span>
      </div>
    </div>
  );
}
