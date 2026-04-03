import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  fireWindowApi,
  type ProviderQueueItem,
  type LiveState,
  type FireResult,
} from '@/services/api/fireWindow';
import { ProviderName } from '../../ProviderName';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { resolveOutcome } from '@/utils/betting';
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

function oddsToCents(odds: number): number {
  return odds > 1 ? Math.round(100 / odds) : 0;
}


// ---------------------------------------------------------------------------
// FireWindow Component
// ---------------------------------------------------------------------------

export function FireWindow({ batch, wageringProjections, onComplete, onBack, onNewBatch }: Props) {
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
    pollRef.current = setInterval(poll, 1_000);
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
          {(liveState as any).balance != null && (
            <span className={`text-xs ${(liveState as any).balance >= liveState.summary.total_stake ? 'text-success' : 'text-amber-400'}`}>
              Balance: {formatStake((liveState as any).balance, tier)}
              {(liveState as any).balance < liveState.summary.total_stake && (
                <span className="text-muted ml-1">
                  (need {formatStake(liveState.summary.total_stake, tier)})
                </span>
              )}
            </span>
          )}
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

        {/* Bet table — with Upd + Delta */}
        <div className="border border-border bg-panel overflow-x-auto">
          <table className="sq" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th style={{ width: '35%' }}>Event</th>
                <th className="text-right">Outcome</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Fair</th>
                <th className="text-right">Prob</th>
                <th className="text-right">TTK</th>
                <th className="text-right">Stake</th>
                <th className="text-right">Edge</th>
                <th className="text-right">Upd</th>
                <th className="text-right">Delta</th>
              </tr>
            </thead>
            <tbody>
              {activeBets.map((bet) => {
                const isExcluded = false;
                const liveOdds = bet.live_odds ?? bet.odds;
                const liveCents = oddsToCents(liveOdds);
                const fairCents = oddsToCents(bet.fair_odds);
                const edgeVal = bet.live_edge ?? bet.edge_pct;
                const ttk = getTTKFromNow(bet.start_time);
                const deltaColor =
                  bet.delta > 1 ? 'text-success' : bet.delta < -1 ? 'text-danger' : 'text-muted';

                return (
                  <tr key={bet.bet_id} className={isExcluded ? 'opacity-50' : ''}>
                    <td>
                      <div className="flex items-center gap-2 min-w-0">
                        {bet.market_slug ? (
                          <a
                            href={`https://polymarket.com/event/${bet.market_slug}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-text text-sm truncate hover:text-tabPolymarket transition-colors"
                          >
                            {bet.display_home} vs {bet.display_away}
                          </a>
                        ) : (
                          <span className="text-text text-sm truncate">{bet.display_home} vs {bet.display_away}</span>
                        )}
                      </div>
                      <div className="text-muted2 text-[11px]">
                        {bet.sport} · {formatDateTime(bet.start_time)}
                      </div>
                    </td>
                    <td className="text-right text-text text-xs">
                      {resolveOutcome(bet.outcome, {
                        display_home: bet.display_home,
                        display_away: bet.display_away,
                        market: bet.market,
                      }, bet.point, true)}
                    </td>
                    <td className="text-right text-sm font-medium">
                      {liveOdds.toFixed(2)} <span className="text-muted text-xs font-normal">({liveCents}¢)</span>
                    </td>
                    <td className="text-right text-muted text-sm">
                      {bet.fair_odds.toFixed(2)} <span className="text-xs">({fairCents}¢)</span>
                    </td>
                    <td className="text-right text-muted text-sm">
                      {bet.fair_odds > 1 ? `${(100 / bet.fair_odds).toFixed(0)}%` : '-'}
                    </td>
                    <td className="text-right">
                      <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>
                    </td>
                    <td className="text-right text-sm font-medium">
                      {formatStake(bet.stake, tier)}
                    </td>
                    <td className={`text-right font-semibold text-sm ${edgeVal > 0 ? 'text-success' : 'text-error'}`}>
                      {edgeVal > 0 ? '+' : ''}{edgeVal.toFixed(1)}%
                    </td>
                    <td className={`text-right text-sm ${
                      bet.last_updated
                        ? ((Date.now() - new Date(bet.last_updated).getTime()) / 60000 <= 1
                          ? 'text-success'
                          : (Date.now() - new Date(bet.last_updated).getTime()) / 60000 <= 5
                            ? 'text-amber-400'
                            : 'text-danger')
                        : 'text-muted'
                    }`}>
                      {bet.last_updated
                        ? (() => {
                            const mins = (Date.now() - new Date(bet.last_updated).getTime()) / 60000;
                            return mins < 1 ? '<1m' : `${Math.round(mins)}m`;
                          })()
                        : '--'}
                    </td>
                    <td className={`text-right text-sm ${deltaColor}`}>
                      {bet.delta > 0 ? '+' : ''}{bet.delta.toFixed(1)}
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
              onClick={() => { fireWindowApi.close().catch(() => {}); onNewBatch(); }}
              className="px-3 py-1 text-xs text-muted hover:text-foreground transition-colors"
            >
              New Batch
            </button>
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
