import { useState, useEffect, useRef, useCallback, useMemo, Fragment } from 'react';
import {
  fireWindowApi,
  type ProviderQueueItem,
  type FireResult,
} from '@/services/api/fireWindow';
import { ProviderName } from '../../ProviderName';
import type { BatchBet, WageringProjection } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Phase = 'queue' | 'firing' | 'result' | 'complete';

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
  onComplete: () => void;
  onBack: () => void;
  onNewBatch: () => void;
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


// ---------------------------------------------------------------------------
// FireWindow Component
// ---------------------------------------------------------------------------

export function FireWindow({ batch, wageringProjections: _wageringProjections, onComplete, onBack, onNewBatch }: Props) {
  const [phase, setPhase] = useState<Phase>('queue');
  const [queue, setQueue] = useState<ProviderQueueItem[]>([]);
  const [currentProvider, setCurrentProvider] = useState<string | null>(null);
  const [fireResult, setFireResult] = useState<FireResult | null>(null);
  const [providerResults, setProviderResults] = useState<ProviderResult[]>([]);
  const [error, setError] = useState<string | null>(null);
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

  // Combined activate + fire handler
  const handleFireProvider = useCallback(async (providerId: string) => {
    setError(null);
    setCurrentProvider(providerId);
    setPhase('firing');
    try {
      await fireWindowApi.activate(providerId);
      const result = await fireWindowApi.fire();
      if (closedRef.current) return;
      setFireResult(result);
      setProviderResults(prev => [...prev, {
        providerId: result.provider_id,
        placed: result.summary.fired,
        failed: result.summary.failed,
        excluded: result.summary.excluded,
      }]);
      setQueue(prev => prev.map(q =>
        q.provider_id === result.provider_id ? { ...q, fired: true } : q
      ));
      if (result.next_provider) {
        setPhase('result');
      } else {
        setPhase('complete');
      }
    } catch (err: any) {
      if (closedRef.current) return;
      setError(err.message || 'Failed to fire bets');
      setPhase('queue');
    }
  }, []);

  // Advance to next provider after result
  const handleNext = useCallback(() => {
    if (fireResult?.next_provider) {
      setCurrentProvider(fireResult.next_provider);
      setPhase('queue');
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
    // Group queue by cluster (derived from batch prop)
    const providerCluster: Record<string, string> = {};
    for (const b of batch) {
      if (b.cluster) providerCluster[b.provider_id] = b.cluster;
    }

    // Sort queue by cluster then EV
    const sortedQueue = [...queue].sort((a, b) => {
      const ca = providerCluster[a.provider_id] || a.provider_id;
      const cb = providerCluster[b.provider_id] || b.provider_id;
      if (ca !== cb) return ca.localeCompare(cb);
      return b.total_ev - a.total_ev;
    });

    // Build cluster groups
    const clusterGroups: { cluster: string; items: typeof sortedQueue }[] = [];
    let curCluster = '';
    for (const item of sortedQueue) {
      const c = providerCluster[item.provider_id] || item.provider_id;
      if (c !== curCluster) {
        curCluster = c;
        clusterGroups.push({ cluster: c, items: [] });
      }
      clusterGroups[clusterGroups.length - 1].items.push(item);
    }

    return (
      <div className="flex flex-col gap-2">
        {/* Summary header */}
        <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
          <span className="text-text font-medium">{stats.totalBets} bets</span>
          <span className="text-muted text-[10px]">
            {stats.stakeSek > 0 && `${stats.stakeSek} kr`}
            {stats.stakeSek > 0 && stats.stakeUsdc > 0 && ' + '}
            {stats.stakeUsdc > 0 && `${stats.stakeUsdc.toFixed(2)} USDC`}
          </span>
          <span className="text-success text-sm ml-auto">
            +{stats.evSek > 0 ? `${stats.evSek} kr` : ''}
            {stats.evSek > 0 && stats.evUsdc > 0 ? ' + ' : ''}
            {stats.evUsdc > 0 ? `${stats.evUsdc.toFixed(2)} USDC` : ''} EV
          </span>
        </div>

        {error && <p className="text-danger text-xs px-3">{error}</p>}

        {/* Provider queue grouped by cluster */}
        <div className="border border-border bg-panel">
          {clusterGroups.map(({ cluster, items }) => {
            const clusterBets = items.reduce((s, i) => s + i.bet_count, 0);
            const clusterEv = items.reduce((s, i) => s + i.total_ev, 0);
            const clusterTier = items[0]?.tier || 'soft';

            return (
              <Fragment key={cluster}>
                {/* Cluster header */}
                <div className="flex items-center gap-3 px-3 py-1 bg-panel2/30 border-b border-border">
                  <span className="text-[10px] text-muted font-medium uppercase tracking-wider">{cluster}</span>
                  <span className="text-[10px] text-muted">{clusterBets} bets · {items.length} {items.length === 1 ? 'provider' : 'providers'}</span>
                  <span className="text-[10px] text-success ml-auto">+{formatStake(clusterEv, clusterTier)} EV</span>
                </div>

                {/* Provider rows */}
                {items.map((item) => (
                  <button
                    key={item.provider_id}
                    onClick={() => !item.fired && handleFireProvider(item.provider_id)}
                    disabled={item.fired}
                    className={`w-full flex items-center gap-3 px-3 pl-6 py-2 border-b border-border transition-colors text-left ${
                      item.fired ? 'opacity-40' : 'hover:bg-panel2/50'
                    }`}
                  >
                    <span className={`text-[10px] ${item.fired ? 'text-success' : 'text-muted/30'}`}>
                      {item.fired ? '✓' : '●'}
                    </span>
                    <span className="text-sm font-medium text-text w-28 truncate uppercase">{item.provider_id}</span>
                    <span className="text-xs text-muted">{item.bet_count} bets</span>
                    <span className="text-xs text-muted">{formatStake(item.total_stake, item.tier)}</span>
                    <span className="text-xs text-success ml-auto">+{formatStake(item.total_ev, item.tier)} EV</span>
                    {!item.fired && <span className="text-xs text-tabPlay font-medium">Fire →</span>}
                  </button>
                ))}
              </Fragment>
            );
          })}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 px-1">
          <button
            onClick={onBack}
            className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90 transition-opacity"
          >
            Back
          </button>
          <button
            onClick={() => { fireWindowApi.close().catch(() => {}); onNewBatch(); }}
            className="px-3 py-1 text-xs text-muted hover:text-foreground transition-colors"
          >
            New Batch
          </button>
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
          Firing bets for <span className="font-medium">{currentProvider}</span>...
        </div>
        <div className="text-xs text-muted">Checking live prices and placing bets</div>
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
