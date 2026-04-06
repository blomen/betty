import { useState, useCallback, useEffect, useMemo, useRef, Fragment } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { ClusterBatchResult, ClusterBet, PendingBetsResponse, PendingBet } from '@/types';
import { NetworkError, TimeoutError } from '@/services/api/client';
import { resolveOutcome, marketLabel } from '@/utils/betting';
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

function clusterEventLabel(b: ClusterBet): string {
  const home = b.display_home || b.sport;
  const away = b.display_away || '';
  if (home && away) return `${home} v ${away}`;
  return home || away || b.event_id;
}

function clusterOutcomeLabel(b: ClusterBet): string {
  return resolveOutcome(
    b.outcome,
    { home_team: b.display_home, away_team: b.display_away, display_home: b.display_home, display_away: b.display_away, market: b.market },
    b.point,
  );
}

function pendingEventLabel(b: PendingBet): string {
  if (b.home_team && b.away_team) return `${b.home_team} v ${b.away_team}`;
  return b.home_team || b.away_team || b.event_id;
}

function pendingOutcomeLabel(b: PendingBet): string {
  return resolveOutcome(
    b.outcome,
    { home_team: b.home_team, away_team: b.away_team, display_home: b.home_team, display_away: b.away_team, market: b.market },
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

  // 2. Pending bets (settlement check)
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

  // SSE: provider status + settlements
  const [providerStatus, setProviderStatus] = useState<Map<string, 'opened' | 'logged_in'>>(new Map());
  const [settlements, setSettlements] = useState<SettlementGroup | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmMsg, setConfirmMsg] = useState<string | null>(null);

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
    es.addEventListener('settlements_pending', (e: MessageEvent) => {
      try {
        setSettlements(JSON.parse(e.data) as SettlementGroup);
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

  const handleRemoveBet = useCallback((key: string) => {
    setExcludedBets(prev => [...prev, key]);
  }, []);

  // Lookups
  const pendingProviders = pendingData?.providers ?? [];
  const pendingCount = pendingData?.total_bets ?? 0;
  const settleMap = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of pendingProviders) m[p.provider_id] = p.bet_count;
    return m;
  }, [pendingProviders]);

  const batch = batchData?.batch ?? [];
  const summary = batchData?.summary;
  const balances: Record<string, number> = (batchData as any)?.provider_balances ?? {};

  const clusterGroups = useMemo(() => {
    const groups: Record<string, { provider: string; bets: ClusterBet[]; tier: string; totalEv: number; totalStake: number; balance: number }[]> = {};
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
        balance: balances[pid] ?? 0,
      });
    }
    for (const list of Object.values(groups)) list.sort((a, b) => b.balance - a.balance);
    return Object.entries(groups).sort((a, b) => {
      const evA = a[1].reduce((s, p) => s + p.totalEv, 0);
      const evB = b[1].reduce((s, p) => s + p.totalEv, 0);
      return evB - evA;
    });
  }, [batch, balances]);

  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [expandedSettleProvider, setExpandedSettleProvider] = useState<string | null>(null);
  const sekStake = batch.filter(b => b.tier !== 'polymarket').reduce((s, b) => s + b.stake, 0);
  const usdcStake = batch.filter(b => b.tier === 'polymarket').reduce((s, b) => s + b.stake, 0);
  const totalEV = summary?.total_expected_profit ?? 0;

  const showRightPanel = pendingCount > 0 || settlements != null;

  function batchErrorMessage(): string {
    if (!batchError) return 'No batch data available.';
    if (batchError instanceof NetworkError) return 'Backend is not reachable.';
    if (batchError instanceof TimeoutError) return 'Batch request timed out.';
    return batchError.message || 'Failed to load batch data.';
  }

  // ─── Batch panel (left or full) ───────────────────────────────────────────

  const batchPanel = (
    <div className="flex flex-col flex-1 min-h-0 min-w-0">
      {batchLoading ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Building batch...</div>
      ) : !batchData ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">{batchErrorMessage()}</div>
      ) : batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No bets in batch.</div>
      ) : (
        <>
          <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
            <span className="text-text font-medium">{batch.length} bets</span>
            <span className="text-muted text-[10px]">
              {sekStake > 0 && `${sekStake.toFixed(0)} kr`}
              {sekStake > 0 && usdcStake > 0 && ' + '}
              {usdcStake > 0 && `${usdcStake.toFixed(2)} USDC`}
            </span>
            <span className="text-success text-sm">+{totalEV.toFixed(0)} kr EV</span>
          </div>

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
                      <span className="text-[10px] text-muted">{clusterBets} bets · {clusterProviders.length} prov</span>
                      <span className="text-[10px] text-success ml-auto">+{fmt(clusterEv, clusterTier)} EV</span>
                    </div>

                    {clusterProviders.map(({ provider, bets, tier, totalStake, totalEv, balance }) => {
                      const isExpanded = expandedProvider === provider;
                      const settleCount = settleMap[provider] ?? 0;
                      const status = providerStatus.get(provider);
                      const dotColor = status === 'logged_in' ? 'text-success' : status === 'opened' ? 'text-amber-400' : 'text-muted/30';
                      return (
                        <Fragment key={provider}>
                          <div
                            className="flex items-center gap-2 px-3 pl-6 py-1.5 border-b border-border hover:bg-panel2/50 cursor-pointer transition-colors"
                            onClick={() => setExpandedProvider(isExpanded ? null : provider)}
                          >
                            <span className={`text-[10px] ${dotColor}`}>●</span>
                            <span className="text-sm font-medium text-text truncate uppercase" style={{ width: showRightPanel ? '5rem' : '7rem' }}>{provider}</span>
                            <span className="text-xs text-muted">{bets.length}</span>
                            <span className="text-xs text-muted">{fmt(totalStake, tier)}</span>
                            {balance > 0 && <span className="text-xs text-success">bal {fmt(balance, tier)}</span>}
                            {settleCount > 0 && <span className="text-[10px] text-amber-400 font-medium">{settleCount}⏳</span>}
                            <span className="text-xs text-success ml-auto">+{fmt(totalEv, tier)}</span>
                            <span className="text-muted text-xs">{isExpanded ? '▾' : '▸'}</span>
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
                                    <th className="w-5"></th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {bets.map(b => {
                                    const ttk = getTTKFromNow(b.start_time);
                                    return (
                                      <tr key={betKey(b)} className={`hover:bg-panel2/40 ${b.funded === false ? 'opacity-50' : ''}`}>
                                        <td className="pl-8 text-sm text-text truncate max-w-[180px]" title={clusterEventLabel(b)}>{clusterEventLabel(b)}</td>
                                        <td className="text-sm text-text truncate max-w-[90px]">{clusterOutcomeLabel(b)}</td>
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
        </>
      )}
    </div>
  );

  // ─── Settle panel (right) ─────────────────────────────────────────────────

  const settlePanel = (
    <div className="flex flex-col min-h-0 min-w-0" style={{ width: '420px', flexShrink: 0 }}>
      {/* Settlement breakdown — detected from mirror */}
      {settlements ? (
        <div className="border border-border bg-panel flex flex-col flex-1 min-h-0">
          <div className="px-3 py-2 border-b border-border flex items-center gap-2">
            <span className="text-sm font-medium text-text uppercase">{settlements.provider}</span>
            <span className="text-xs text-muted">{settlements.count} detected</span>
            <span className="text-xs text-success">{settlements.wins}W</span>
            <span className="text-xs text-danger">{settlements.losses}L</span>
            {settlements.count - settlements.wins - settlements.losses > 0 && (
              <span className="text-xs text-amber-400">{settlements.count - settlements.wins - settlements.losses}V</span>
            )}
            <span className={`text-xs ml-auto font-semibold ${settlements.net >= 0 ? 'text-success' : 'text-danger'}`}>
              {settlements.net >= 0 ? '+' : ''}{fmtCurrency(settlements.net, settlements.provider)}
            </span>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto">
            <table className="sq w-full">
              <thead>
                <tr className="text-muted text-[10px]">
                  <th className="text-left pl-3">Event</th>
                  <th className="text-right">Odds</th>
                  <th className="text-right">Stake</th>
                  <th className="text-right">Result</th>
                  <th className="text-right pr-3">P&L</th>
                </tr>
              </thead>
              <tbody>
                {settlements.settlements.map(s => {
                  const pl = s.payout - s.stake;
                  return (
                    <tr key={s.bet_id} className="border-t border-border">
                      <td className="pl-3 text-sm text-text truncate max-w-[160px]" title={s.event}>{s.event}</td>
                      <td className="text-right text-sm text-muted">{s.odds.toFixed(2)}</td>
                      <td className="text-right text-sm text-text">{fmtCurrency(s.stake, settlements.provider)}</td>
                      <td className="text-right text-sm">
                        <span className={
                          s.result === 'won' ? 'text-success font-semibold' :
                          s.result === 'lost' ? 'text-danger font-semibold' :
                          'text-amber-400 font-semibold'
                        }>{s.result.toUpperCase()}</span>
                      </td>
                      <td className={`text-right text-sm pr-3 font-semibold ${pl >= 0 ? 'text-success' : 'text-danger'}`}>
                        {pl >= 0 ? '+' : ''}{fmtCurrency(pl, settlements.provider)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-2 px-3 py-2 border-t border-border">
            <button onClick={handleDismiss} className="px-3 py-1 text-xs text-muted hover:text-foreground">Dismiss</button>
            <button
              onClick={handleConfirm}
              disabled={confirming}
              className="px-4 py-1.5 text-xs bg-success text-bg font-medium hover:opacity-90 disabled:opacity-50 ml-auto"
            >
              {confirming ? 'Saving...' : 'Confirm & Save'}
            </button>
          </div>
        </div>
      ) : (
        /* Pending bets — waiting for settlement detection */
        <div className="border border-border bg-panel flex flex-col flex-1 min-h-0">
          <div className="px-3 py-1.5 border-b border-border flex items-center gap-2">
            <span className="text-[10px] text-muted font-medium uppercase tracking-wider">Settle</span>
            <span className="text-xs text-amber-400">{pendingCount} pending</span>
            {confirmMsg && <span className="text-xs text-success ml-auto">{confirmMsg}</span>}
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto">
            {pendingProviders.map(prov => {
              const status = providerStatus.get(prov.provider_id);
              const dotColor = status === 'logged_in' ? 'text-success' : status === 'opened' ? 'text-amber-400' : 'text-muted/30';
              const statusLabel = status === 'logged_in' ? 'logged in — open bet history' : status === 'opened' ? 'logging in...' : 'opening...';
              const isExpanded = expandedSettleProvider === prov.provider_id;

              return (
                <Fragment key={prov.provider_id}>
                  <div
                    className="flex items-center gap-2 px-3 py-2 border-b border-border hover:bg-panel2/50 cursor-pointer"
                    onClick={() => setExpandedSettleProvider(isExpanded ? null : prov.provider_id)}
                  >
                    <span className={`text-[10px] ${dotColor}`}>●</span>
                    <span className="text-sm font-medium text-text uppercase">{prov.provider_id}</span>
                    <span className="text-xs text-muted">{prov.bet_count} bets</span>
                    <span className="text-xs text-muted">{fmtCurrency(prov.total_stake, prov.provider_id)}</span>
                    <span className={`text-[10px] ml-auto ${status === 'logged_in' ? 'text-success' : 'text-muted'}`}>{statusLabel}</span>
                    <span className="text-muted text-xs">{isExpanded ? '▾' : '▸'}</span>
                  </div>

                  {isExpanded && (
                    <div className="border-b border-border bg-panel2/20">
                      <table className="sq w-full">
                        <thead>
                          <tr className="text-muted text-[10px]">
                            <th className="text-left pl-4">Event</th>
                            <th className="text-left">Pick</th>
                            <th className="text-right">Odds</th>
                            <th className="text-right pr-3">Stake</th>
                          </tr>
                        </thead>
                        <tbody>
                          {prov.bets.map(b => (
                            <tr key={b.id} className="hover:bg-panel2/40">
                              <td className="pl-4 text-sm text-text truncate max-w-[140px]" title={pendingEventLabel(b)}>{pendingEventLabel(b)}</td>
                              <td className="text-sm">
                                <span className="text-amber-400">{pendingOutcomeLabel(b)}</span>
                                <span className="text-muted text-[10px] ml-1">[{marketLabel(b.market)}]</span>
                              </td>
                              <td className="text-right text-sm text-text">{b.odds.toFixed(2)}</td>
                              <td className="text-right text-sm text-text pr-3">{fmtCurrency(b.stake, prov.provider_id)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Fragment>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );

  // ─── Layout ───────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="play" color={TAB_COLORS.play} size={16} />
          Play
        </h2>
      </div>

      {showRightPanel ? (
        <div className="flex flex-1 min-h-0 gap-2">
          {batchPanel}
          {settlePanel}
        </div>
      ) : (
        batchPanel
      )}
    </div>
  );
}
