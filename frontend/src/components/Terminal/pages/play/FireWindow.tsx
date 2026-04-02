import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  fireWindowApi,
  type ProviderQueueItem,
  type LiveState,
  type FireResult,
} from '@/services/api/fireWindow';
import { ProviderName } from '../../ProviderName';
import type { BatchBet, WageringProjection } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Phase = 'queue' | 'activating' | 'monitoring' | 'firing' | 'result' | 'complete';

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
  onComplete: () => void;
  onBack: () => void;
}

interface BatchStats {
  totalBets: number;
  stakeSek: number;
  stakeUsdc: number;
  evSek: number;
  evUsdc: number;
  providers: number;
  clusters: number;
}

interface ProviderResult {
  providerId: string;
  placed: number;
  failed: number;
  excluded: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function currencyLabel(tier: string): string {
  return tier === 'polymarket' ? 'USDC' : 'SEK';
}

function formatStake(amount: number, tier: string): string {
  const cur = currencyLabel(tier);
  if (cur === 'USDC') return `$${amount.toFixed(1)} USDC`;
  return `${Math.round(amount)} SEK`;
}

function formatTTK(startTime: string | null): string {
  if (!startTime) return '--';
  const diff = new Date(startTime).getTime() - Date.now();
  if (diff < 0) return 'LIVE';
  const hours = Math.floor(diff / 3_600_000);
  const mins = Math.floor((diff % 3_600_000) / 60_000);
  if (hours >= 24) return `${Math.floor(hours / 24)}d`;
  if (hours > 0) return `${hours}h${mins > 0 ? String(mins).padStart(2, '0') + 'm' : ''}`;
  return `${mins}m`;
}

const CATEGORY_CLASSES: Record<string, string> = {
  improved: 'text-success',
  stable: 'text-foreground',
  degraded: 'text-warning',
  negative: 'text-danger line-through opacity-60',
  pending: 'text-muted',
};

// ---------------------------------------------------------------------------
// FireWindow Component
// ---------------------------------------------------------------------------

export function FireWindow({ batch, wageringProjections, onComplete, onBack }: Props) {
  const [phase, setPhase] = useState<Phase>('queue');
  const [queue, setQueue] = useState<ProviderQueueItem[]>([]);
  const [currentProvider, setCurrentProvider] = useState<string | null>(null);
  const [liveState, setLiveState] = useState<LiveState | null>(null);
  const [fireResult, setFireResult] = useState<FireResult | null>(null);
  const [providerResults, setProviderResults] = useState<ProviderResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedRef = useRef(false);

  // Batch summary stats (computed once from the batch prop)
  const stats = useMemo<BatchStats>(() => {
    const funded = batch.filter((b) => b.funded);
    const providers = new Set(funded.map((b) => b.provider_id));
    const clusters = new Set(funded.filter((b) => b.cluster).map((b) => b.cluster));
    const sek = funded.filter((b) => b.tier !== 'polymarket');
    const usdc = funded.filter((b) => b.tier === 'polymarket');
    return {
      totalBets: funded.length,
      stakeSek: Math.round(sek.reduce((s, b) => s + b.stake, 0)),
      stakeUsdc: usdc.reduce((s, b) => s + b.stake, 0),
      evSek: Math.round(sek.reduce((s, b) => s + b.expected_profit, 0)),
      evUsdc: usdc.reduce((s, b) => s + b.expected_profit, 0),
      providers: providers.size,
      clusters: clusters.size,
    };
  }, [batch]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      closedRef.current = true;
      if (pollRef.current) clearInterval(pollRef.current);
      fireWindowApi.close().catch(() => {});
    };
  }, []);

  // Open fire window on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fireWindowApi.open(batch);
        if (cancelled) return;
        setQueue(res.queue);
        setCurrentProvider(res.current_provider);
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to open fire window');
      }
    })();
    return () => { cancelled = true; };
  }, [batch]);

  // Poll live state during monitoring phase
  useEffect(() => {
    if (phase !== 'monitoring') {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    const poll = async () => {
      if (closedRef.current) return;
      try {
        const state = await fireWindowApi.getState();
        setLiveState(state);
      } catch {
        // Ignore poll errors
      }
    };
    poll();
    pollRef.current = setInterval(poll, 3_000);
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [phase]);

  // Activate a provider
  const handleActivate = useCallback(async (providerId: string) => {
    setError(null);
    setPhase('activating');
    setCurrentProvider(providerId);
    try {
      const state = await fireWindowApi.activate(providerId);
      if (closedRef.current) return;
      setLiveState(state);
      setPhase('monitoring');
    } catch (err: any) {
      if (closedRef.current) return;
      setError(err.message || 'Failed to activate provider');
      setPhase('queue');
    }
  }, []);

  // Fire bets for current provider
  const handleFire = useCallback(async () => {
    setError(null);
    setPhase('firing');
    try {
      const result = await fireWindowApi.fire();
      if (closedRef.current) return;
      setFireResult(result);
      setProviderResults((prev) => [
        ...prev,
        {
          providerId: result.provider_id,
          placed: result.summary.fired,
          failed: result.summary.failed,
          excluded: result.summary.excluded,
        },
      ]);
      // Mark provider as fired in queue
      setQueue((prev) =>
        prev.map((q) =>
          q.provider_id === result.provider_id ? { ...q, fired: true } : q,
        ),
      );
      if (result.next_provider) {
        setPhase('result');
      } else {
        setPhase('complete');
      }
    } catch (err: any) {
      if (closedRef.current) return;
      setError(err.message || 'Failed to fire bets');
      setPhase('monitoring');
    }
  }, []);

  // Skip current provider
  const handleSkip = useCallback(async () => {
    setError(null);
    try {
      const res = await fireWindowApi.skip();
      if (closedRef.current) return;
      setQueue((prev) =>
        prev.map((q) =>
          q.provider_id === res.provider_id ? { ...q, fired: true } : q,
        ),
      );
      if (res.next_provider) {
        setCurrentProvider(res.next_provider);
        setPhase('queue');
        setLiveState(null);
        setFireResult(null);
      } else {
        setPhase('complete');
      }
    } catch (err: any) {
      if (closedRef.current) return;
      setError(err.message || 'Failed to skip provider');
    }
  }, []);

  // Advance to next provider after result
  const handleNext = useCallback(() => {
    if (fireResult?.next_provider) {
      setCurrentProvider(fireResult.next_provider);
      setPhase('queue');
      setLiveState(null);
      setFireResult(null);
    } else {
      setPhase('complete');
    }
  }, [fireResult]);

  // ---------------------------------------------------------------------------
  // Render: Error
  // ---------------------------------------------------------------------------

  if (error && phase === 'queue' && queue.length === 0) {
    return (
      <div className="border border-danger/50 bg-panel px-4 py-3">
        <p className="text-danger text-sm mb-2">Error: {error}</p>
        <button
          onClick={onBack}
          className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90 transition-opacity"
        >
          Back
        </button>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Queue Phase
  // ---------------------------------------------------------------------------

  if (phase === 'queue') {
    return (
      <div className="flex flex-col gap-2">
        {/* Summary header — mirrors CapitalPlanPanel style */}
        <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
          <span className="text-muted uppercase tracking-wider text-[10px]">Fire Window</span>
          <span className="text-foreground">
            {stats.totalBets} bets across {stats.providers} providers
          </span>
          <span className="text-muted text-[10px]">
            Deployed{' '}
            {stats.stakeSek > 0 && `${stats.stakeSek} kr`}
            {stats.stakeSek > 0 && stats.stakeUsdc > 0 && ' + '}
            {stats.stakeUsdc > 0 && `${stats.stakeUsdc.toFixed(2)} USDC`}
            {stats.stakeSek === 0 && stats.stakeUsdc === 0 && '0 kr'}
          </span>
          <span className="text-success text-[10px] ml-auto">
            +{stats.evSek > 0 ? `${stats.evSek} kr` : ''}
            {stats.evSek > 0 && stats.evUsdc > 0 ? ' + ' : ''}
            {stats.evUsdc > 0 ? `${stats.evUsdc.toFixed(2)} USDC` : ''} EV
          </span>
        </div>

        {/* Provider queue */}
        <div className="border border-border bg-panel px-3 py-2">
          {error && <p className="text-danger text-xs mb-2">{error}</p>}
          <div className="flex flex-col gap-1">
            {queue.map((item) => (
              <button
                key={item.provider_id}
                onClick={() => !item.fired && handleActivate(item.provider_id)}
                disabled={item.fired}
                className={`w-full flex items-center gap-3 px-3 py-2 border transition-colors text-left ${
                  item.fired
                    ? 'border-border/30 bg-panel2/50 opacity-40'
                    : 'border-border hover:bg-panel2/50'
                }`}
              >
                {item.fired ? (
                  <span className="text-success text-sm font-bold">&#10003;</span>
                ) : (
                  <span className="text-muted text-sm">&#9675;</span>
                )}
                <span className="text-sm font-medium text-foreground">
                  <ProviderName name={item.provider_id} />
                </span>
                <span className="text-xs text-muted">
                  {item.bet_count} bets
                </span>
                <span className="text-xs text-muted">
                  {formatStake(item.total_stake, item.tier)}
                </span>
                <span className="text-xs text-success ml-auto">
                  +{formatStake(item.total_ev, item.tier)} EV
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Wagering projections — same style as CapitalPlanPanel */}
        {wageringProjections.length > 0 && (
          <div className="border border-border bg-amber-500/5 px-3 py-1.5">
            <div className="flex items-center gap-1 mb-1">
              <span className="text-sm font-medium text-amber-500 tracking-wider uppercase">
                Wagering After Batch
              </span>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-0.5">
              {wageringProjections.map((proj) => {
                const total = proj.wagering_total || proj.wagering_remaining;
                const beforePct = total > 0 ? Math.round(((total - proj.wagering_remaining) / total) * 100) : 100;
                const afterPct = total > 0 ? Math.round(((total - proj.projected_remaining) / total) * 100) : 100;
                return (
                  <div
                    key={`${proj.provider_id}-${proj.cluster}`}
                    className="flex items-center gap-1.5 text-sm"
                  >
                    <span className="text-amber-400 font-medium">
                      {proj.provider_id}
                    </span>
                    <span className="text-muted">{beforePct}%</span>
                    <span className="text-muted2">→</span>
                    <span className={afterPct >= 100 ? 'text-success' : 'text-amber-300'}>{afterPct}%</span>
                    {proj.days_remaining != null && (
                      <span className="text-muted text-[10px]">
                        {proj.days_remaining}d
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Back button */}
        <div className="flex items-center gap-2 px-1">
          <button
            onClick={onBack}
            className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90 transition-opacity"
          >
            Back
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Activating Phase
  // ---------------------------------------------------------------------------

  if (phase === 'activating') {
    return (
      <div className="border border-border bg-panel px-4 py-6 flex flex-col items-center gap-3">
        <div className="text-sm text-foreground animate-pulse">
          Opening tabs for{' '}
          <span className="font-medium">
            <ProviderName name={currentProvider ?? ''} />
          </span>
          ...
        </div>
        <div className="text-xs text-muted">This may take up to 2 minutes for browser providers</div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Monitoring Phase
  // ---------------------------------------------------------------------------

  if (phase === 'monitoring' && liveState) {
    const activeBets = liveState.bets.filter((b) => b.category !== 'negative');
    const excludedBets = liveState.bets.filter((b) => b.category === 'negative');
    const tier = liveState.tier;
    const isPoly = tier === 'polymarket';

    return (
      <div className="flex flex-col gap-2">
        {/* Provider header */}
        <div className="border border-border bg-panel px-3 py-2 flex items-center gap-3">
          <span className="text-sm font-medium text-foreground">
            <ProviderName name={liveState.provider_id} />
          </span>
          <span className="text-xs text-muted">
            Provider {liveState.position} of {liveState.total_providers}
          </span>
          <span className="text-xs text-muted ml-auto">
            {liveState.summary.active_bets} active
            {liveState.summary.excluded_bets > 0 && (
              <span className="text-danger ml-1">
                ({liveState.summary.excluded_bets} excluded)
              </span>
            )}
          </span>
        </div>

        {error && (
          <div className="text-danger text-xs px-3">{error}</div>
        )}

        {/* Bet table */}
        <div className="border border-border bg-panel overflow-x-auto">
          <table className="text-xs sq w-full">
            <thead className="bg-panel">
              <tr>
                <th className="text-left px-2 py-1">Event / Outcome</th>
                <th className="text-right px-2 py-1">Stake</th>
                {isPoly && <th className="text-right px-2 py-1">&#162;</th>}
                <th className="text-right px-2 py-1">Orig</th>
                <th className="text-right px-2 py-1">Live</th>
                <th className="text-right px-2 py-1">Fair</th>
                <th className="text-right px-2 py-1">Edge</th>
                <th className="text-right px-2 py-1">Delta</th>
                <th className="text-right px-2 py-1">TTK</th>
              </tr>
            </thead>
            <tbody>
              {liveState.bets.map((bet) => {
                const catClass = CATEGORY_CLASSES[bet.category] ?? 'text-foreground';
                const edgeVal = bet.live_edge ?? bet.edge_pct;
                const edgeColor =
                  edgeVal > 5 ? 'text-success' : edgeVal > 0 ? 'text-warning' : 'text-danger';
                const deltaColor =
                  bet.delta > 0 ? 'text-success' : bet.delta < 0 ? 'text-danger' : 'text-muted';

                return (
                  <tr key={bet.bet_id} className={catClass}>
                    <td className="px-2 py-1">
                      <div className="truncate max-w-[260px]" title={`${bet.display_home} v ${bet.display_away}`}>
                        {bet.display_home} v {bet.display_away}
                      </div>
                      <div className="text-muted text-[10px]">
                        {bet.outcome}
                        {bet.point != null ? ` (${bet.point > 0 ? '+' : ''}${bet.point})` : ''}
                      </div>
                    </td>
                    <td className="text-right px-2 py-1">
                      {formatStake(bet.stake, tier)}
                    </td>
                    {isPoly && (
                      <td className="text-right px-2 py-1 text-tabPolymarket font-mono">
                        {bet.live_price_cents != null ? `${bet.live_price_cents}` : '--'}
                      </td>
                    )}
                    <td className="text-right px-2 py-1">{bet.odds.toFixed(2)}</td>
                    <td className="text-right px-2 py-1 font-medium">
                      {bet.live_odds != null ? bet.live_odds.toFixed(2) : '--'}
                    </td>
                    <td className="text-right px-2 py-1 text-muted">
                      {bet.fair_odds.toFixed(2)}
                    </td>
                    <td className={`text-right px-2 py-1 font-semibold ${edgeColor}`}>
                      {edgeVal > 0 ? '+' : ''}
                      {edgeVal.toFixed(1)}%
                    </td>
                    <td className={`text-right px-2 py-1 ${deltaColor}`}>
                      {bet.delta > 0 ? '+' : ''}
                      {bet.delta.toFixed(2)}
                    </td>
                    <td className="text-right px-2 py-1 text-muted">
                      {formatTTK(bet.start_time)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Summary + actions */}
        <div className="border border-border bg-panel px-3 py-2 flex items-center justify-between">
          <div className="text-xs text-muted">
            {activeBets.length} bets &middot;{' '}
            {formatStake(liveState.summary.total_stake, tier)} stake &middot;{' '}
            <span className="text-success">
              +{formatStake(liveState.summary.total_ev, tier)} EV
            </span>
            {excludedBets.length > 0 && (
              <span className="text-danger ml-2">
                {excludedBets.length} negative edge excluded
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleSkip}
              className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90 transition-opacity"
            >
              Skip
            </button>
            <button
              onClick={handleFire}
              className="px-3 py-1 text-xs bg-success text-bg font-medium hover:opacity-90 transition-opacity"
            >
              Fire {activeBets.length} bets
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Firing Phase
  // ---------------------------------------------------------------------------

  if (phase === 'firing') {
    return (
      <div className="border border-border bg-panel px-4 py-6 flex flex-col items-center gap-3">
        <div className="text-sm text-foreground animate-pulse">
          Firing bets for{' '}
          <span className="font-medium">
            <ProviderName name={currentProvider ?? ''} />
          </span>
          ...
        </div>
        <div className="text-xs text-muted">Do not close this page</div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Result Phase
  // ---------------------------------------------------------------------------

  if (phase === 'result' && fireResult) {
    const { summary } = fireResult;
    return (
      <div className="flex flex-col gap-2">
        <div className="border border-border bg-panel px-4 py-3">
          <div className="text-sm font-medium text-foreground mb-2">
            <ProviderName name={fireResult.provider_id} /> &mdash; Complete
          </div>
          <div className="flex items-center gap-4 text-xs">
            {summary.fired > 0 && (
              <span className="text-success">{summary.fired} placed</span>
            )}
            {summary.excluded > 0 && (
              <span className="text-warning">{summary.excluded} excluded</span>
            )}
            {summary.failed > 0 && (
              <span className="text-danger">{summary.failed} failed</span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 px-1">
          <button
            onClick={handleNext}
            className="px-3 py-1 text-xs bg-success text-bg font-medium hover:opacity-90 transition-opacity"
          >
            Next Provider
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Complete Phase
  // ---------------------------------------------------------------------------

  if (phase === 'complete') {
    const totalPlaced = providerResults.reduce((s, r) => s + r.placed, 0);
    const totalFailed = providerResults.reduce((s, r) => s + r.failed, 0);
    const totalExcluded = providerResults.reduce((s, r) => s + r.excluded, 0);

    return (
      <div className="flex flex-col gap-2">
        <div className="border border-border bg-panel px-4 py-3">
          <div className="text-sm font-medium text-foreground mb-3">
            Session Complete
          </div>
          <div className="flex flex-col gap-1 mb-3">
            {providerResults.map((r) => (
              <div key={r.providerId} className="flex items-center gap-3 text-xs">
                <span className="text-success font-bold">&#10003;</span>
                <span className="text-foreground font-medium w-28">
                  <ProviderName name={r.providerId} />
                </span>
                <span className="text-success">{r.placed} placed</span>
                {r.excluded > 0 && (
                  <span className="text-warning">{r.excluded} excluded</span>
                )}
                {r.failed > 0 && (
                  <span className="text-danger">{r.failed} failed</span>
                )}
              </div>
            ))}
          </div>
          <div className="border-t border-border pt-2 flex items-center gap-4 text-xs">
            <span className="text-foreground font-medium">
              Total: {totalPlaced} placed
            </span>
            {totalExcluded > 0 && (
              <span className="text-warning">{totalExcluded} excluded</span>
            )}
            {totalFailed > 0 && (
              <span className="text-danger">{totalFailed} failed</span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 px-1">
          <button
            onClick={() => {
              fireWindowApi.close().catch(() => {});
              onComplete();
            }}
            className="px-3 py-1 text-xs bg-success text-bg font-medium hover:opacity-90 transition-opacity"
          >
            Done
          </button>
          <button
            onClick={onBack}
            className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90 transition-opacity"
          >
            Back
          </button>
        </div>
      </div>
    );
  }

  // Fallback (loading)
  return (
    <div className="border border-border bg-panel px-4 py-4 text-sm text-muted animate-pulse">
      Loading fire window...
    </div>
  );
}
