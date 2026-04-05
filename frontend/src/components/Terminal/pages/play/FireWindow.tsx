import { useState, useEffect, useRef, useCallback, useMemo, Fragment } from 'react';
import {
  fireWindowApi,
  type ProviderQueueItem,
} from '@/services/api/fireWindow';
import type { BatchBet, WageringProjection } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Phase = 'queue' | 'betting' | 'complete';

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
  onComplete: () => void;
  onBack: () => void;
  onNewBatch: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatStake(amount: number, tier: string): string {
  if (tier === 'polymarket') return `$${amount.toFixed(1)} USDC`;
  return `${Math.round(amount)} kr`;
}

// ---------------------------------------------------------------------------
// FireWindow Component
// ---------------------------------------------------------------------------

export function FireWindow({ batch, wageringProjections: _wp, onComplete, onBack, onNewBatch }: Props) {
  const [phase, setPhase] = useState<Phase>('queue');
  const [queue, setQueue] = useState<ProviderQueueItem[]>([]);
  const [currentProvider, setCurrentProvider] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closedRef = useRef(false);

  // SSE-detected providers (logged in via mirror)
  const [detectedProviders, setDetectedProviders] = useState<Set<string>>(new Set());

  // Single-bet state (all hooks at top level — no conditionals)
  const [currentBet, setCurrentBet] = useState<any>(null);
  const [priceCheck, setPriceCheck] = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [placedCount, setPlacedCount] = useState(0);
  const [skippedCount, setSkippedCount] = useState(0);

  // Batch summary
  const stats = useMemo(() => {
    const providers = new Set(batch.map(b => b.provider_id));
    const sek = batch.filter(b => b.tier !== 'polymarket');
    const usdc = batch.filter(b => b.tier === 'polymarket');
    return {
      totalBets: batch.length,
      stakeSek: Math.round(sek.reduce((s, b) => s + b.stake, 0)),
      stakeUsdc: usdc.reduce((s, b) => s + b.stake, 0),
      providers: providers.size,
    };
  }, [batch]);

  // Provider cluster mapping
  const providerCluster = useMemo(() => {
    const map: Record<string, string> = {};
    for (const b of batch) if (b.cluster) map[b.provider_id] = b.cluster;
    return map;
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
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to open fire window');
      }
    })();
    return () => { cancelled = true; };
  }, [batch]);

  // SSE: detect providers + auto-start betting
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');
    const handle = (e: MessageEvent) => {
      try {
        const provider = JSON.parse(e.data).provider as string;
        setDetectedProviders(prev => {
          if (prev.has(provider)) return prev;
          const next = new Set(prev);
          next.add(provider);
          return next;
        });
      } catch { /* ignore */ }
    };
    es.addEventListener('sync_available', handle);
    es.addEventListener('balance_synced', handle);
    return () => es.close();
  }, []);

  // Load next bet, check price, show for confirmation
  const fetchNextBet = useCallback(async (providerId: string) => {
    setCurrentBet(null);
    setPriceCheck(null);
    setChecking(false);
    setPlacing(false);
    try {
      const next = await fireWindowApi.getNextBet();
      if (next.done) {
        setQueue(prev => prev.map(q =>
          q.provider_id === providerId ? { ...q, fired: true } : q
        ));
        setPhase('queue');
        return;
      }
      setCurrentBet(next);
      // Auto-check live price
      setChecking(true);
      try {
        const check = await fireWindowApi.checkBet(next.bet_id);
        setPriceCheck(check);
        // Auto-skip negative edge bets
        if (check.live_edge != null && check.live_edge <= 0) {
          setChecking(false);
          await fireWindowApi.skipBet(next.bet_id);
          setSkippedCount(prev => prev + 1);
          fetchNextBet(providerId);
          return;
        }
      } catch { /* show with DB odds */ }
      setChecking(false);
    } catch (err: any) {
      setError(err.message || 'Failed to load next bet');
      setPhase('queue');
    }
  }, []);

  // Start betting on a provider: activate → load first bet
  const startProvider = useCallback(async (providerId: string) => {
    setError(null);
    setCurrentProvider(providerId);
    setPlacedCount(0);
    setSkippedCount(0);
    try {
      await fireWindowApi.activate(providerId);
      setPhase('betting');
      fetchNextBet(providerId);
    } catch (err: any) {
      setError(err.message || 'Failed to activate provider');
    }
  }, [fetchNextBet]);

  // Auto-start: when a provider is detected and we're in queue phase
  const autoStartRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (phase !== 'queue' || queue.length === 0) return;
    const ready = queue.find(q => !q.fired && detectedProviders.has(q.provider_id) && !autoStartRef.current.has(q.provider_id));
    if (ready) {
      autoStartRef.current.add(ready.provider_id);
      const timer = setTimeout(() => startProvider(ready.provider_id), 500);
      return () => clearTimeout(timer);
    }
  }, [phase, queue, detectedProviders, startProvider]);

  const handleConfirm = useCallback(async () => {
    if (!currentBet || !currentProvider) return;
    setPlacing(true);
    try {
      await fireWindowApi.placeBet(currentBet.bet_id);
      setPlacedCount(prev => prev + 1);
    } catch (err: any) {
      setError(err.message);
    }
    fetchNextBet(currentProvider);
  }, [currentBet, currentProvider, fetchNextBet]);

  const handleSkip = useCallback(async () => {
    if (!currentBet || !currentProvider) return;
    await fireWindowApi.skipBet(currentBet.bet_id);
    setSkippedCount(prev => prev + 1);
    fetchNextBet(currentProvider);
  }, [currentBet, currentProvider, fetchNextBet]);

  // ---------------------------------------------------------------------------
  // Render: Error
  // ---------------------------------------------------------------------------

  if (error && phase === 'queue' && queue.length === 0) {
    return (
      <div className="border border-danger/50 bg-panel px-4 py-3">
        <p className="text-danger text-sm mb-2">Error: {error}</p>
        <button onClick={onBack} className="px-3 py-1 text-xs bg-border text-foreground">Back</button>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Betting Phase — one bet at a time
  // ---------------------------------------------------------------------------

  if (phase === 'betting') {
    const tier = currentProvider === 'polymarket' ? 'polymarket' : 'soft';
    const bet = currentBet;

    if (!bet) {
      return (
        <div className="border border-border bg-panel px-4 py-6 text-center">
          <div className="text-sm text-foreground animate-pulse">Loading next bet...</div>
        </div>
      );
    }

    const liveCents = priceCheck?.live_cents;
    const fairCents = bet.fair_cents || priceCheck?.fair_cents;
    const liveEdge = priceCheck?.live_edge;
    const displayEdge = liveEdge ?? bet.edge_pct;

    return (
      <div className="flex flex-col gap-2">
        {/* Provider + progress */}
        <div className="border border-border bg-panel px-3 py-2 flex items-center gap-3">
          <span className="text-sm font-medium text-foreground uppercase">{currentProvider}</span>
          <span className="text-xs text-muted">{bet.remaining_bets} remaining</span>
          <span className="text-xs text-success">{placedCount} placed</span>
          {skippedCount > 0 && <span className="text-xs text-muted">{skippedCount} skipped</span>}
        </div>

        {error && <div className="text-danger text-xs px-3">{error}</div>}

        {/* Current bet — table row style matching Poly tab */}
        <div className="border border-border bg-panel overflow-x-auto">
          <table className="sq" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th style={{ width: '30%' }}>Event</th>
                <th>Outcome</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Fair</th>
                <th className="text-right">Prob</th>
                <th className="text-right">TTK</th>
                <th className="text-right">Stake</th>
                <th className="text-right">Edge</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <div className="text-text text-sm">{bet.display_home} vs {bet.display_away}</div>
                  <div className="text-muted2 text-[11px]">
                    {bet.sport}{bet.start_time ? ` · ${new Date(bet.start_time).toLocaleString('sv-SE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` : ''}
                  </div>
                </td>
                <td className="text-text text-sm">
                  <span className="text-amber-400">{bet.outcome === 'home' ? bet.display_home : bet.outcome === 'away' ? bet.display_away : bet.outcome}</span>
                  <span className="text-muted ml-1">[{bet.market.toUpperCase()}]</span>
                  {bet.point != null && <span className="text-text font-medium ml-1">{bet.point > 0 ? '+' : ''}{bet.point}</span>}
                </td>
                <td className="text-right text-sm font-medium">
                  {liveCents != null ? (
                    <>
                      <span className={liveCents !== bet.cents ? 'text-amber-400' : 'text-text'}>{(100 / liveCents).toFixed(2)}</span>
                      <span className="text-muted text-xs"> ({liveCents}¢)</span>
                      {liveCents !== bet.cents && <span className="text-muted text-[10px] ml-1">db {bet.cents}¢</span>}
                    </>
                  ) : (
                    <><span className="text-text">{bet.odds.toFixed(2)}</span> <span className="text-muted text-xs">({bet.cents}¢)</span></>
                  )}
                </td>
                <td className="text-right text-sm text-muted">
                  {bet.fair_odds?.toFixed(2) || '—'} <span className="text-xs">({fairCents}¢)</span>
                </td>
                <td className="text-right text-sm text-muted">
                  {fairCents ? `${fairCents}%` : '—'}
                </td>
                <td className="text-right text-sm">
                  {bet.start_time ? (() => {
                    const h = Math.max(0, (new Date(bet.start_time).getTime() - Date.now()) / 3600000);
                    return <span className={h < 3 ? 'text-danger' : h < 12 ? 'text-amber-400' : 'text-success'}>
                      {h < 1 ? `${Math.round(h * 60)}m` : `${Math.round(h)}h`}
                    </span>;
                  })() : '—'}
                </td>
                <td className="text-right text-sm font-medium text-text">
                  {formatStake(bet.stake, tier)}
                </td>
                <td className={`text-right text-sm font-semibold ${displayEdge > 0 ? 'text-success' : 'text-error'}`}>
                  {displayEdge > 0 ? '+' : ''}{displayEdge?.toFixed(1)}%
                </td>
              </tr>
            </tbody>
          </table>

          {checking && (
            <div className="text-xs text-muted animate-pulse px-3 py-1">Checking live price...</div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 px-1">
          <button
            onClick={() => { setPhase('queue'); setCurrentBet(null); }}
            className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90"
          >
            Back
          </button>
          <button
            onClick={handleSkip}
            className="px-3 py-1 text-xs text-muted hover:text-foreground"
          >
            Skip
          </button>
          <button
            onClick={handleConfirm}
            disabled={placing || checking}
            className="px-4 py-1.5 text-xs bg-success text-bg font-medium hover:opacity-90 disabled:opacity-50"
          >
            {placing ? 'Placing...' : 'Confirm'}
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Queue Phase — waiting for providers
  // ---------------------------------------------------------------------------

  if (phase === 'queue') {
    // Group queue by cluster
    const sortedQueue = [...queue].sort((a, b) => {
      const ca = providerCluster[a.provider_id] || a.provider_id;
      const cb = providerCluster[b.provider_id] || b.provider_id;
      if (ca !== cb) return ca.localeCompare(cb);
      return b.total_ev - a.total_ev;
    });

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

    const allFired = queue.length > 0 && queue.every(q => q.fired);

    return (
      <div className="flex flex-col gap-2">
        {/* Summary */}
        <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
          <span className="text-text font-medium">{stats.totalBets} bets</span>
          <span className="text-muted text-[10px]">
            {stats.stakeSek > 0 && `${stats.stakeSek} kr`}
            {stats.stakeSek > 0 && stats.stakeUsdc > 0 && ' + '}
            {stats.stakeUsdc > 0 && `${stats.stakeUsdc.toFixed(2)} USDC`}
          </span>
          <span className="text-xs text-muted ml-auto">
            {detectedProviders.size > 0
              ? `${detectedProviders.size} providers detected — waiting for login`
              : 'Open provider sites in mirror to start'}
          </span>
        </div>

        {error && <div className="text-danger text-xs px-3">{error}</div>}

        {/* Provider queue grouped by cluster */}
        <div className="border border-border bg-panel">
          {clusterGroups.map(({ cluster, items }) => (
            <Fragment key={cluster}>
              <div className="flex items-center gap-3 px-3 py-1 bg-panel2/30 border-b border-border">
                <span className="text-[10px] text-muted font-medium uppercase tracking-wider">{cluster}</span>
                <span className="text-[10px] text-muted">{items.reduce((s, i) => s + i.bet_count, 0)} bets</span>
              </div>
              {items.map(item => (
                <div
                  key={item.provider_id}
                  className={`flex items-center gap-3 px-3 pl-6 py-2 border-b border-border ${
                    item.fired ? 'opacity-40' : ''
                  }`}
                >
                  <span className={`text-[10px] ${
                    item.fired ? 'text-success' :
                    detectedProviders.has(item.provider_id) ? 'text-success animate-pulse' :
                    'text-muted/30'
                  }`}>
                    {item.fired ? '✓' : '●'}
                  </span>
                  <span className="text-sm font-medium text-text w-28 truncate uppercase">{item.provider_id}</span>
                  <span className="text-xs text-muted">{item.bet_count} bets</span>
                  <span className="text-xs text-muted">{formatStake(item.total_stake, item.tier)}</span>
                  {!item.fired && (
                    <span className={`text-[10px] ${detectedProviders.has(item.provider_id) ? 'text-success' : 'text-muted'}`}>
                      {detectedProviders.has(item.provider_id) ? 'ready' : 'waiting'}
                    </span>
                  )}
                  <span className="text-xs text-success ml-auto">+{formatStake(item.total_ev, item.tier)} EV</span>
                </div>
              ))}
            </Fragment>
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 px-1">
          <button onClick={onBack} className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90">
            Back
          </button>
          <button
            onClick={() => { fireWindowApi.close().catch(() => {}); onNewBatch(); }}
            className="px-3 py-1 text-xs text-muted hover:text-foreground"
          >
            New Batch
          </button>
          {allFired && (
            <button onClick={onComplete} className="px-4 py-1.5 text-xs bg-success text-bg font-medium hover:opacity-90 ml-auto">
              Done
            </button>
          )}
        </div>
      </div>
    );
  }

  return null;
}
