import { useState, useCallback, useEffect, useMemo, useRef, Fragment } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { ClusterBatchResult, ClusterBet, PendingBetsResponse } from '@/types';
import { NetworkError, TimeoutError } from '@/services/api/client';
import { resolveOutcome } from '@/utils/betting';
import { getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { TabIcon, TAB_COLORS } from '../TabBar';

// ─── Types ──────────────────────────────────────────────────────────────────

interface Settlement {
  bet_id: number;
  provider: string;
  event: string;
  odds: number;
  stake: number;
  result: string;
  payout: number;
}

interface SettlementGroup {
  provider: string;
  count: number;
  wins: number;
  losses: number;
  total_staked: number;
  total_payout: number;
  net: number;
  settlements: Settlement[];
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmt(v: number, tier: string): string {
  if (tier === 'polymarket') return `$${v.toFixed(1)} USDC`;
  return `${Math.round(v)} kr`;
}

function fmtCurrency(v: number, provider: string): string {
  if (provider === 'polymarket') return `$${v.toFixed(2)}`;
  return `${Math.round(v)} kr`;
}

function formatOddsAge(minutes: number | null): string {
  if (minutes == null) return '--';
  if (minutes < 1) return '<1m';
  if (minutes < 60) return `${Math.round(minutes)}m`;
  return `${(minutes / 60).toFixed(1)}h`;
}

function getOddsAgeColor(minutes: number | null): string {
  if (minutes == null) return 'text-muted';
  if (minutes <= 5) return 'text-success';
  if (minutes <= 15) return 'text-amber-400';
  return 'text-danger';
}

function betKey(b: ClusterBet): string {
  return `${b.cluster}:${b.event_id}:${b.market}:${b.outcome}:${b.point ?? ''}`;
}

function eventLabel(b: ClusterBet): string {
  const home = b.display_home || b.sport;
  const away = b.display_away || '';
  if (home && away) return `${home} v ${away}`;
  return home || away || b.event_id;
}

function outcomeLabel(b: ClusterBet): string {
  return resolveOutcome(
    b.outcome,
    { home_team: b.display_home, away_team: b.display_away, display_home: b.display_home, display_away: b.display_away, market: b.market },
    b.point,
  );
}

// ─── Component ──────────────────────────────────────────────────────────────

export function PlayPage() {
  const queryClient = useQueryClient();
  const [excludedBets, setExcludedBets] = useState<string[]>([]);

  // 1. Start mirror + open settle tabs
  const settleTabsOpened = useRef(false);
  useEffect(() => {
    (async () => {
      try {
        await api.ensureMirrorStarted();
        if (!settleTabsOpened.current) {
          settleTabsOpened.current = true;
          await api.openSettleTabs();
        }
      } catch { /* */ }
    })();
  }, []);

  // 2. Pending bets
  const { data: pendingData } = useQuery<PendingBetsResponse>({
    queryKey: ['pending-bets'],
    queryFn: () => api.getPendingBets(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  // 3. Batch
  const {
    data: batchData,
    isLoading: batchLoading,
    error: batchError,
  } = useQuery<ClusterBatchResult>({
    queryKey: ['play-batch', excludedBets],
    queryFn: () => api.getPlayBatch(excludedBets.length > 0 ? excludedBets : undefined),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

  // Track provider login status via SSE
  const [providerStatus, setProviderStatus] = useState<Map<string, 'opened' | 'logged_in'>>(new Map());

  // Settlement panel — populated from SSE or manual scan
  const [settlements, setSettlements] = useState<SettlementGroup | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmMsg, setConfirmMsg] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');
    es.addEventListener('provider_opened', (e: MessageEvent) => {
      try {
        const { provider } = JSON.parse(e.data);
        setProviderStatus(prev => {
          if (prev.get(provider) === 'logged_in') return prev;
          const next = new Map(prev);
          next.set(provider, 'opened');
          return next;
        });
      } catch { /* */ }
    });
    es.addEventListener('sync_available', (e: MessageEvent) => {
      try {
        const { provider } = JSON.parse(e.data);
        setProviderStatus(prev => { const next = new Map(prev); next.set(provider, 'logged_in'); return next; });
      } catch { /* */ }
    });
    es.addEventListener('balance_synced', (e: MessageEvent) => {
      try {
        const { provider } = JSON.parse(e.data);
        setProviderStatus(prev => { const next = new Map(prev); next.set(provider, 'logged_in'); return next; });
      } catch { /* */ }
    });
    // Settlement detection from mirror interceptor
    es.addEventListener('settlements_pending', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as SettlementGroup;
        setSettlements(data);
        setConfirmMsg(null);
      } catch { /* */ }
    });
    es.addEventListener('settlements_confirmed', () => {
      setSettlements(null);
      queryClient.invalidateQueries({ queryKey: ['pending-bets'] });
    });
    return () => es.close();
  }, [queryClient]);

  const handleConfirm = useCallback(async () => {
    setConfirming(true);
    try {
      const res = await api.confirmMirrorSettlements();
      setConfirmMsg(`Settled ${res.settled} bet${res.settled !== 1 ? 's' : ''}`);
      setSettlements(null);
      queryClient.invalidateQueries({ queryKey: ['pending-bets'] });
    } catch (err: any) {
      setConfirmMsg(`Error: ${err.message}`);
    } finally {
      setConfirming(false);
    }
  }, [queryClient]);

  const handleDismiss = useCallback(() => {
    api.rejectMirrorSettlements().catch(() => {});
    setSettlements(null);
  }, []);

  const handleScanPortfolio = useCallback(async () => {
    setScanning(true);
    try {
      await api.scrapePolyPortfolio();
      // SSE will deliver settlements_pending if any found
    } catch { /* */ }
    finally { setScanning(false); }
  }, []);

  const handleRemoveBet = useCallback((key: string) => {
    setExcludedBets(prev => [...prev, key]);
  }, []);

  // Lookups
  const settleMap = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of pendingData?.providers ?? []) m[p.provider_id] = p.bet_count;
    return m;
  }, [pendingData]);

  const batch = batchData?.batch ?? [];
  const summary = batchData?.summary;

  const clusterGroups = useMemo(() => {
    const groups: Record<string, { provider: string; bets: ClusterBet[]; tier: string; totalEv: number; totalStake: number }[]> = {};
    const byProvider: Record<string, ClusterBet[]> = {};
    for (const b of batch) {
      const pid = b.provider_id ?? 'unknown';
      if (!byProvider[pid]) byProvider[pid] = [];
      byProvider[pid].push(b);
    }
    for (const [pid, bets] of Object.entries(byProvider)) {
      const cluster = bets[0]?.cluster ?? pid;
      if (!groups[cluster]) groups[cluster] = [];
      groups[cluster].push({
        provider: pid, bets,
        tier: bets[0]?.tier || 'soft',
        totalEv: bets.reduce((s, b) => s + b.expected_profit, 0),
        totalStake: bets.reduce((s, b) => s + b.stake, 0),
      });
    }
    for (const list of Object.values(groups)) list.sort((a, b) => b.totalEv - a.totalEv);
    return Object.entries(groups).sort((a, b) => {
      const evA = a[1].reduce((s, p) => s + p.totalEv, 0);
      const evB = b[1].reduce((s, p) => s + p.totalEv, 0);
      return evB - evA;
    });
  }, [batch]);

  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const sekStake = batch.filter(b => b.tier !== 'polymarket').reduce((s, b) => s + b.stake, 0);
  const usdcStake = batch.filter(b => b.tier === 'polymarket').reduce((s, b) => s + b.stake, 0);
  const totalEV = summary?.total_expected_profit ?? 0;

  function batchErrorMessage(): string {
    if (!batchError) return 'No batch data available.';
    if (batchError instanceof NetworkError) return 'Backend is not reachable.';
    if (batchError instanceof TimeoutError) return 'Batch request timed out.';
    return batchError.message || 'Failed to load batch data.';
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="play" color={TAB_COLORS.play} size={16} />
          Play
        </h2>
      </div>

      {/* ─── Settlement breakdown panel ─── */}
      {settlements && (
        <div className="border border-border bg-panel">
          <div className="px-3 py-2 border-b border-border flex items-center gap-3">
            <span className="text-sm font-medium text-text uppercase">{settlements.provider}</span>
            <span className="text-xs text-muted">{settlements.count} positions detected</span>
            <span className="text-xs text-success">{settlements.wins}W</span>
            <span className="text-xs text-danger">{settlements.losses}L</span>
            {settlements.count - settlements.wins - settlements.losses > 0 && (
              <span className="text-xs text-muted">{settlements.count - settlements.wins - settlements.losses}V</span>
            )}
            <span className={`text-xs ml-auto font-semibold ${settlements.net >= 0 ? 'text-success' : 'text-danger'}`}>
              {settlements.net >= 0 ? '+' : ''}{fmtCurrency(settlements.net, settlements.provider)} net
            </span>
          </div>

          <table className="sq w-full">
            <thead>
              <tr className="text-muted text-[10px]">
                <th className="text-left pl-3">Event</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Stake</th>
                <th className="text-right">Result</th>
                <th className="text-right">Payout</th>
                <th className="text-right pr-3">P&L</th>
              </tr>
            </thead>
            <tbody>
              {settlements.settlements.map(s => {
                const pl = s.payout - s.stake;
                return (
                  <tr key={s.bet_id} className="border-t border-border">
                    <td className="pl-3 text-sm text-text truncate max-w-[250px]" title={s.event}>{s.event}</td>
                    <td className="text-right text-sm text-muted">{s.odds.toFixed(2)}</td>
                    <td className="text-right text-sm text-text">{fmtCurrency(s.stake, settlements.provider)}</td>
                    <td className="text-right text-sm">
                      <span className={
                        s.result === 'won' ? 'text-success font-semibold' :
                        s.result === 'lost' ? 'text-danger font-semibold' :
                        'text-amber-400 font-semibold'
                      }>
                        {s.result.toUpperCase()}
                      </span>
                    </td>
                    <td className="text-right text-sm">
                      <span className={s.payout > 0 ? 'text-success' : 'text-muted'}>
                        {fmtCurrency(s.payout, settlements.provider)}
                      </span>
                    </td>
                    <td className={`text-right text-sm pr-3 font-semibold ${pl >= 0 ? 'text-success' : 'text-danger'}`}>
                      {pl >= 0 ? '+' : ''}{fmtCurrency(pl, settlements.provider)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="flex items-center gap-2 px-3 py-2 border-t border-border">
            <span className="text-xs text-muted">
              Staked: {fmtCurrency(settlements.total_staked, settlements.provider)}
              {' '}| Payout: {fmtCurrency(settlements.total_payout, settlements.provider)}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <button onClick={handleDismiss} className="px-3 py-1 text-xs text-muted hover:text-foreground">Dismiss</button>
              <button
                onClick={handleConfirm}
                disabled={confirming}
                className="px-4 py-1.5 text-xs bg-success text-bg font-medium hover:opacity-90 disabled:opacity-50"
              >
                {confirming ? 'Saving...' : 'Confirm & Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmMsg && <div className="text-xs text-success px-3">{confirmMsg}</div>}

      {/* Batch */}
      {batchLoading ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Building batch...</div>
      ) : !batchData ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">{batchErrorMessage()}</div>
      ) : batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No bets in batch.</div>
      ) : (
        <div className="flex flex-col flex-1 min-h-0">
          {/* Summary */}
          <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
            <span className="text-text font-medium">{batch.length} bets</span>
            <span className="text-muted text-[10px]">
              {sekStake > 0 && `${sekStake.toFixed(0)} kr`}
              {sekStake > 0 && usdcStake > 0 && ' + '}
              {usdcStake > 0 && `${usdcStake.toFixed(2)} USDC`}
            </span>
            <span className="text-success text-sm">+{totalEV.toFixed(0)} kr EV</span>
          </div>

          {/* Cluster/provider list */}
          <div className="border border-border border-t-0 bg-panel flex-1 min-h-0 relative">
            <div className="absolute inset-0 overflow-y-auto">
              {clusterGroups.map(([cluster, clusterProviders]) => {
                const clusterBets = clusterProviders.reduce((s, p) => s + p.bets.length, 0);
                const clusterEv = clusterProviders.reduce((s, p) => s + p.totalEv, 0);
                const clusterTier = clusterProviders[0]?.tier || 'soft';

                return (
                  <Fragment key={cluster}>
                    <div className="flex items-center gap-3 px-3 py-1 bg-panel2/30 border-b border-border">
                      <span className="text-[10px] text-muted font-medium uppercase tracking-wider">{cluster}</span>
                      <span className="text-[10px] text-muted">{clusterBets} bets · {clusterProviders.length} providers</span>
                      <span className="text-[10px] text-success ml-auto">+{fmt(clusterEv, clusterTier)} EV</span>
                    </div>

                    {clusterProviders.map(({ provider, bets, tier, totalStake, totalEv }) => {
                      const isExpanded = expandedProvider === provider;
                      const settleCount = settleMap[provider] ?? 0;
                      const status = providerStatus.get(provider);
                      const dotColor = status === 'logged_in' ? 'text-success' : status === 'opened' ? 'text-amber-400' : 'text-muted/30';
                      return (
                        <Fragment key={provider}>
                          <div
                            className="flex items-center gap-3 px-3 pl-6 py-2 border-b border-border hover:bg-panel2/50 cursor-pointer transition-colors"
                            onClick={() => setExpandedProvider(isExpanded ? null : provider)}
                          >
                            <span className={`text-[10px] ${dotColor}`}>●</span>
                            <span className="text-sm font-medium text-text w-28 truncate uppercase">{provider}</span>
                            <span className="text-xs text-muted">{bets.length} bets</span>
                            <span className="text-xs text-muted">{fmt(totalStake, tier)}</span>
                            {settleCount > 0 && (
                              <span className="text-[10px] text-amber-400 font-medium">{settleCount} to settle</span>
                            )}
                            {settleCount > 0 && provider === 'polymarket' && (
                              <button
                                onClick={(e) => { e.stopPropagation(); handleScanPortfolio(); }}
                                disabled={scanning}
                                className="text-[10px] text-text bg-border px-2 py-0.5 hover:opacity-80 disabled:opacity-50"
                              >
                                {scanning ? 'Scanning...' : 'Scan'}
                              </button>
                            )}
                            <span className="text-xs text-success ml-auto">+{fmt(totalEv, tier)} EV</span>
                            <span className="text-muted text-xs w-3">{isExpanded ? '▾' : '▸'}</span>
                          </div>

                          {isExpanded && (
                            <div className="border-b border-border bg-panel2/20">
                              <table className="sq w-full">
                                <thead>
                                  <tr className="text-muted text-[10px]">
                                    <th className="text-left pl-8">Event</th>
                                    <th className="text-left">Outcome</th>
                                    <th className="text-right">Odds</th>
                                    <th className="text-right">Fair</th>
                                    <th className="text-right">Edge</th>
                                    <th className="text-right">Stake</th>
                                    <th className="text-right">TTK</th>
                                    <th className="text-right">Upd</th>
                                    <th className="w-6"></th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {bets.map(b => {
                                    const ttk = getTTKFromNow(b.start_time);
                                    return (
                                      <tr key={betKey(b)} className={`hover:bg-panel2/40 ${b.funded === false ? 'opacity-50' : ''}`}>
                                        <td className="pl-8 text-sm text-text truncate max-w-[200px]" title={eventLabel(b)}>{eventLabel(b)}</td>
                                        <td className="text-sm text-text truncate max-w-[100px]">{outcomeLabel(b)}</td>
                                        <td className="text-right text-sm text-text">{b.odds.toFixed(2)}</td>
                                        <td className="text-right text-sm text-muted">{b.fair_odds.toFixed(2)}</td>
                                        <td className={`text-right text-sm font-semibold ${b.edge_pct > 0 ? 'text-success' : 'text-error'}`}>+{b.edge_pct.toFixed(1)}%</td>
                                        <td className="text-right text-sm text-text">{fmt(b.stake, tier)}</td>
                                        <td className="text-right text-sm"><span className={getTTKColor(ttk)}>{formatTTKLabel(ttk)}</span></td>
                                        <td className={`text-right text-sm ${getOddsAgeColor(b.odds_age_minutes)}`}>{formatOddsAge(b.odds_age_minutes)}</td>
                                        <td className="text-right"><button onClick={() => handleRemoveBet(betKey(b))} className="text-muted hover:text-error text-xs">✕</button></td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </Fragment>
                      );
                    })}
                  </Fragment>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
