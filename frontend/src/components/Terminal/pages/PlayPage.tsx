import { useState, useCallback, useEffect, useMemo, useRef, Fragment } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { ClusterBatchResult, ClusterBet, PendingBetsResponse } from '@/types';
import { NetworkError, TimeoutError } from '@/services/api/client';
import { resolveOutcome } from '@/utils/betting';
import { getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { TabIcon, TAB_COLORS } from '../TabBar';

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmt(v: number, tier: string): string {
  if (tier === 'polymarket') return `$${v.toFixed(1)} USDC`;
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

  // Start mirror + open settle tabs
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

  // Pending bets (for settle indicators on rows)
  const { data: pendingData } = useQuery<PendingBetsResponse>({
    queryKey: ['pending-bets'],
    queryFn: () => api.getPendingBets(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  // Batch
  const {
    data: batchData,
    isLoading: batchLoading,
    error: batchError,
  } = useQuery<ClusterBatchResult>({
    queryKey: ['play-batch'],
    queryFn: () => api.getPlayBatch(),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

  // SSE: provider status
  const [providerStatus, setProviderStatus] = useState<Map<string, 'opened' | 'logged_in'>>(new Map());
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
    return () => es.close();
  }, []);

  const queryClient = useQueryClient();

  const handleRemoveBet = useCallback(async (b: ClusterBet) => {
    try {
      await api.blacklistBet(b.event_id, b.provider_id, b.market, b.outcome);
      queryClient.invalidateQueries({ queryKey: ['play-batch'] });
    } catch { /* */ }
  }, [queryClient]);

  // Active bet + placed tracking
  const [activeBet, setActiveBet] = useState<string | null>(null); // betKey
  const [activeBetObj, setActiveBetObj] = useState<ClusterBet | null>(null);
  const [liveEdge, setLiveEdge] = useState<number | null>(null);
  const [liveCents, setLiveCents] = useState<number | null>(null);
  const [navigating, setNavigating] = useState(false);
  const [placedBets, setPlacedBets] = useState<Set<string>>(new Set());
  const [activeProviderBets, setActiveProviderBets] = useState<ClusterBet[]>([]);

  // Listen for bet_mirrored SSE → mark placed, advance to next
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');
    es.addEventListener('bet_mirrored', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (!activeBet || !activeProviderBets.length) return;
        if (!data.provider || !data.matched) return; // Only act on confirmed matches

        const currentIdx = activeProviderBets.findIndex(b => betKey(b) === activeBet);
        if (currentIdx < 0) return;
        const current = activeProviderBets[currentIdx];

        // Must be same provider
        if (data.provider !== current.provider_id) return;

        // Must have odds AND stake that roughly match
        if (!data.odds || !data.stake) return;
        const oddsMatch = Math.abs(data.odds - current.odds) / current.odds < 0.10;
        const stakeMatch = Math.abs(data.stake - current.stake) / current.stake < 0.30;
        if (!oddsMatch && !stakeMatch) return;

        // Mark as placed
        setPlacedBets(prev => new Set(prev).add(activeBet));
        // Advance to next unplaced bet
        for (let i = currentIdx + 1; i < activeProviderBets.length; i++) {
          const next = activeProviderBets[i];
          if (!placedBets.has(betKey(next))) {
            handlePlayBet(next);
            return;
          }
        }
        setActiveBet(null);
        setLiveEdge(null);
      } catch { /* */ }
    });
    return () => es.close();
  }, [activeBet, activeProviderBets, placedBets]);

  const handlePlayBet = useCallback(async (b: ClusterBet) => {
    const key = betKey(b);
    setActiveBet(key);
    setActiveBetObj(b);
    setLiveEdge(null);
    setLiveCents(null);
    setNavigating(true);
    try {
      const res = await api.navigateBet({
        provider_id: b.provider_id,
        event_id: b.event_id,
        market: b.market,
        outcome: b.outcome,
        point: b.point,
        odds: b.odds,
        fair_odds: b.fair_odds,
        stake: b.stake,
        display_home: b.display_home,
        display_away: b.display_away,
      });
      if (res.live_edge != null) setLiveEdge(res.live_edge);
    } catch { /* */ }
    finally { setNavigating(false); }
  }, []);

  // Poll live price every 5s while a bet is active
  useEffect(() => {
    if (!activeBetObj || navigating) return;
    const poll = async () => {
      try {
        const res = await api.getLivePrice(
          activeBetObj.provider_id, activeBetObj.event_id,
          activeBetObj.market, activeBetObj.outcome,
          activeBetObj.fair_odds, activeBetObj.point,
          activeBetObj.display_home, activeBetObj.display_away,
        );
        if (res.live_edge != null) setLiveEdge(res.live_edge);
        if (res.live_cents != null) setLiveCents(res.live_cents);
      } catch { /* */ }
    };
    poll(); // Immediate first check
    const iv = setInterval(poll, 5000);
    return () => clearInterval(iv);
  }, [activeBetObj, navigating]);

  // Lookups
  const settleMap = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of pendingData?.providers ?? []) m[p.provider_id] = p.bet_count;
    return m;
  }, [pendingData]);

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
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="play" color={TAB_COLORS.play} size={16} />
          Play
        </h2>
      </div>

      {/* Batch */}
      {batchLoading ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Building batch...</div>
      ) : !batchData ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">{batchErrorMessage()}</div>
      ) : batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No bets in batch.</div>
      ) : (
        <div className="flex flex-col flex-1 min-h-0">
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
                      <span className="text-[10px] text-muted">{clusterBets} bets · {clusterProviders.length} providers</span>
                      <span className="text-[10px] text-success ml-auto">+{fmt(clusterEv, clusterTier)} EV</span>
                    </div>

                    {clusterProviders.map(({ provider, bets, tier, totalStake, totalEv, balance }) => {
                      const isExpanded = expandedProvider === provider;
                      const handleExpand = () => {
                        if (isExpanded) {
                          setExpandedProvider(null);
                          setActiveBet(null);
                          setLiveEdge(null);
                          setActiveProviderBets([]);
                        } else {
                          setExpandedProvider(provider);
                          setActiveProviderBets(bets);
                          // Auto-navigate to first unplaced bet
                          const first = bets.find(b => !placedBets.has(betKey(b)));
                          if (first) handlePlayBet(first);
                        }
                      };
                      const settleCount = settleMap[provider] ?? 0;
                      const status = providerStatus.get(provider);
                      const dotColor = status === 'logged_in' ? 'text-success' : status === 'opened' ? 'text-amber-400' : 'text-muted/30';
                      return (
                        <Fragment key={provider}>
                          <div
                            className="flex items-center gap-3 px-3 pl-6 py-2 border-b border-border hover:bg-panel2/50 cursor-pointer transition-colors"
                            onClick={handleExpand}
                          >
                            <span className={`text-[10px] ${dotColor}`}>●</span>
                            <span className="text-sm font-medium text-text w-28 truncate uppercase">{provider}</span>
                            <span className="text-xs text-muted">{bets.length} bets</span>
                            <span className="text-xs text-muted">{fmt(totalStake, tier)}</span>
                            {balance > 0 && <span className="text-xs text-success">bal {fmt(balance, tier)}</span>}
                            {settleCount > 0 && <span className="text-[10px] text-amber-400 font-medium">{settleCount} pending</span>}
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
                                      <tr
                                        key={betKey(b)}
                                        className={`hover:bg-panel2/40 cursor-pointer ${b.funded === false ? 'opacity-50' : ''} ${activeBet === betKey(b) ? 'bg-panel2/60' : ''} ${placedBets.has(betKey(b)) ? 'opacity-40' : ''}`}
                                        onClick={() => !placedBets.has(betKey(b)) && handlePlayBet(b)}
                                      >
                                        <td className="pl-8 text-sm text-text truncate max-w-[200px]" title={eventLabel(b)}>
                                          {placedBets.has(betKey(b)) && <span className="text-success text-[10px] mr-1">✓</span>}
                                          {activeBet === betKey(b) && navigating && <span className="text-amber-400 text-[10px] mr-1">⟳</span>}
                                          {activeBet === betKey(b) && !navigating && !placedBets.has(betKey(b)) && <span className="text-success text-[10px] mr-1">▸</span>}
                                          {eventLabel(b)}
                                        </td>
                                        <td className="text-sm text-text truncate max-w-[100px]">{outcomeLabel(b)}</td>
                                        <td className="text-right text-sm text-text">
                                          {b.odds.toFixed(2)}
                                          {tier === 'polymarket' && (
                                            activeBet === betKey(b) && liveCents != null
                                              ? <span className="text-amber-400 text-[10px] ml-0.5">{liveCents.toFixed(1)}¢</span>
                                              : <span className="text-muted text-[10px] ml-0.5">{(100 / b.odds).toFixed(1)}¢</span>
                                          )}
                                        </td>
                                        <td className="text-right text-sm text-muted">
                                          {b.fair_odds.toFixed(2)}
                                          {tier === 'polymarket' && <span className="text-[10px] ml-0.5">{(100 / b.fair_odds).toFixed(1)}¢</span>}
                                        </td>
                                        <td className={`text-right text-sm font-semibold ${b.edge_pct > 0 ? 'text-success' : 'text-error'}`}>
                                          {activeBet === betKey(b) && liveEdge != null
                                            ? <span className={liveEdge > 0 ? 'text-success' : 'text-danger'}>{liveEdge > 0 ? '+' : ''}{liveEdge.toFixed(1)}% live</span>
                                            : `+${b.edge_pct.toFixed(1)}%`
                                          }
                                        </td>
                                        <td className="text-right text-sm text-text">{fmt(b.stake, tier)}</td>
                                        <td className="text-right text-sm"><span className={getTTKColor(ttk)}>{formatTTKLabel(ttk)}</span></td>
                                        <td className={`text-right text-sm ${getOddsAgeColor(b.odds_age_minutes)}`}>{formatOddsAge(b.odds_age_minutes)}</td>
                                        <td className="text-right pr-2"><button onClick={(e) => { e.stopPropagation(); handleRemoveBet(b); }} className="text-muted hover:text-error text-sm px-1">✕</button></td>
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
