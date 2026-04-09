import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { ClusterBatchResult, ClusterBet, PendingBetsResponse } from '@/types';
import { NetworkError, TimeoutError } from '@/services/api/client';
import { resolveOutcome } from '@/utils/betting';
import { getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { SyncLane } from './play/SyncLane';
import { BettingLane } from './play/BettingLane';

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
        if (!data.provider || !data.matched) return;

        const currentIdx = activeProviderBets.findIndex(b => betKey(b) === activeBet);
        if (currentIdx < 0) return;
        const current = activeProviderBets[currentIdx];

        if (data.provider !== current.provider_id) return;
        if (!data.odds || !data.stake) return;
        const oddsMatch = Math.abs(data.odds - current.odds) / current.odds < 0.10;
        const stakeMatch = Math.abs(data.stake - current.stake) / current.stake < 0.30;
        if (!oddsMatch && !stakeMatch) return;

        setPlacedBets(prev => new Set(prev).add(activeBet));
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
        if (res.live_edge != null) {
          setLiveEdge(res.live_edge);
          if (res.live_edge <= 0 && activeBetObj) {
            const positives = [...batch].filter(b => b.edge_pct > 0 && !placedBets.has(betKey(b)));
            positives.sort((a, b) => b.edge_pct - a.edge_pct);
            const currentKey = betKey(activeBetObj);
            const nextIdx = positives.findIndex(b => betKey(b) === currentKey) + 1;
            if (nextIdx > 0 && nextIdx < positives.length) {
              handlePlayBet(positives[nextIdx]);
              return;
            }
          }
        }
        if (res.live_cents != null) setLiveCents(res.live_cents);
      } catch { /* */ }
    };
    poll();
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

  // Flat provider list (sorted by balance desc, then pending count)
  const providerList = useMemo(() => {
    const byProvider: Record<string, ClusterBet[]> = {};
    for (const b of batch) {
      const pid = b.provider_id ?? 'unknown';
      if (!byProvider[pid]) byProvider[pid] = [];
      byProvider[pid].push(b);
    }
    return Object.entries(byProvider)
      .map(([pid, bets]) => {
        bets.sort((a, b) => b.edge_pct - a.edge_pct);
        return {
          provider: pid,
          bets,
          tier: bets[0]?.tier || 'soft',
          totalEv: bets.reduce((s, b) => s + b.expected_profit, 0),
          totalStake: bets.reduce((s, b) => s + b.stake, 0),
          balance: balances[pid] ?? 0,
        };
      })
      .sort((a, b) => {
        if (a.balance !== b.balance) return b.balance - a.balance;
        const pendA = settleMap[a.provider] ?? 0;
        const pendB = settleMap[b.provider] ?? 0;
        return pendB - pendA;
      });
  }, [batch, balances, settleMap]);

  const [activeProvider, setActiveProvider] = useState<string | null>(null);

  // Auto-navigate to top edge bet when no active bet
  const lastAutoNav = useRef(0);
  useEffect(() => {
    if (activeBet || navigating || !batch.length || batchLoading) return;
    if (Date.now() - lastAutoNav.current < 3000) return;
    lastAutoNav.current = Date.now();
    const loggedIn = new Set([...providerStatus.entries()].filter(([_, s]) => s === 'logged_in').map(([p]) => p));
    const hasBalance = new Set(Object.entries(balances).filter(([_, b]) => b >= 1).map(([p]) => p));
    const playable = new Set([...loggedIn].filter(p => hasBalance.has(p)));
    if (playable.size === 0) return;

    const sorted = [...batch].filter(b => b.edge_pct > 0).sort((a, b) => b.edge_pct - a.edge_pct);
    const top = sorted.find(b => !placedBets.has(betKey(b)) && playable.has(b.provider_id));
    if (top) {
      setActiveProvider(top.provider_id);
      const providerBets = batch.filter(b => b.provider_id === top.provider_id);
      setActiveProviderBets(providerBets);
      handlePlayBet(top);
    }
  }, [batch, activeBet, navigating, batchLoading, placedBets]);

  const sekStake = batch.filter(b => b.tier !== 'polymarket').reduce((s, b) => s + b.stake, 0);
  const usdcStake = batch.filter(b => b.tier === 'polymarket').reduce((s, b) => s + b.stake, 0);
  const totalEV = summary?.total_expected_profit ?? 0;

  function batchErrorMessage(): string {
    if (!batchError) return 'No batch data available.';
    if (batchError instanceof NetworkError) return 'Backend is not reachable.';
    if (batchError instanceof TimeoutError) return 'Batch request timed out.';
    return (batchError as Error).message || 'Failed to load batch data.';
  }

  const activeBets = useMemo(() => {
    if (!activeProvider) return [];
    return providerList.find(p => p.provider === activeProvider)?.bets ?? [];
  }, [activeProvider, providerList]);

  const activeTier = providerList.find(p => p.provider === activeProvider)?.tier ?? 'soft';

  const handleSelectProvider = (pid: string) => {
    if (activeProvider === pid) return;
    setActiveProvider(pid);
    const provBets = batch.filter(b => b.provider_id === pid);
    setActiveProviderBets(provBets);
    setActiveBet(null);
    setLiveEdge(null);
    // Auto-navigate to first positive bet
    const first = provBets.find(b => b.edge_pct > 0 && !placedBets.has(betKey(b)));
    if (first) handlePlayBet(first);
  };

  const handleConfirmSettlements = useCallback(() => {
    // Trigger settle confirmation — invalidate pending bets
    queryClient.invalidateQueries({ queryKey: ['pending-bets'] });
  }, [queryClient]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Header */}
      <div className="flex items-center gap-3 px-3 py-1.5 border-b border-border bg-panel flex-shrink-0">
        <h2 className="text-sm font-semibold text-text flex items-center gap-2">
          <TabIcon name="play" color={TAB_COLORS.play} size={14} />
          Play
        </h2>
        {batchLoading ? (
          <span className="text-muted text-xs">Loading...</span>
        ) : batchData ? (
          <>
            <span className="text-text text-xs font-medium">{batch.length} bets</span>
            <span className="text-muted text-[10px]">
              {sekStake > 0 && `${sekStake.toFixed(0)} kr`}
              {sekStake > 0 && usdcStake > 0 && ' + '}
              {usdcStake > 0 && `${usdcStake.toFixed(2)} USDC`}
            </span>
            <span className="text-success text-xs">+{totalEV.toFixed(0)} kr EV</span>
          </>
        ) : (
          <span className="text-muted text-xs">{batchErrorMessage()}</span>
        )}
      </div>

      {/* Body */}
      {batchLoading ? (
        <div className="text-muted text-sm py-8 text-center flex-1">Building batch...</div>
      ) : !batchData ? (
        <div className="text-muted text-sm py-8 text-center flex-1">{batchErrorMessage()}</div>
      ) : batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center flex-1">No bets in batch.</div>
      ) : (
        <div className="flex flex-1 min-h-0">

          {/* ── Left lane: provider list + sync ────────────────────────── */}
          <div className="w-72 border-r border-zinc-800 flex flex-col min-h-0">
            {/* Provider list */}
            <div className="flex-shrink-0 overflow-y-auto" style={{ maxHeight: '40%' }}>
              {providerList.map(({ provider, bets, tier, totalEv, balance }) => {
                const isActive = activeProvider === provider;
                const settleCount = settleMap[provider] ?? 0;
                const status = providerStatus.get(provider);
                const dotColor = status === 'logged_in' ? 'text-success' : status === 'opened' ? 'text-amber-400' : 'text-muted/30';
                return (
                  <div
                    key={provider}
                    className={`flex items-center gap-2 px-3 py-2 border-b border-border cursor-pointer transition-colors ${isActive ? 'bg-panel2/60' : 'hover:bg-panel2/40'}`}
                    onClick={() => handleSelectProvider(provider)}
                  >
                    <span className={`text-[10px] flex-shrink-0 ${dotColor}`}>●</span>
                    <span className="text-xs font-medium text-text truncate uppercase w-24">{provider}</span>
                    <span className="text-[10px] text-muted">{bets.length}</span>
                    {balance > 0 && <span className="text-[10px] text-success">{fmt(balance, tier)}</span>}
                    {settleCount > 0 && <span className="text-[10px] text-amber-400">{settleCount}p</span>}
                    <span className="text-[10px] text-success ml-auto">+{fmt(totalEv, tier)}</span>
                  </div>
                );
              })}
            </div>

            {/* SyncLane for active provider */}
            <div className="flex-1 min-h-0 overflow-y-auto">
              <SyncLane
                providerId={activeProvider}
                onConfirmSettlements={handleConfirmSettlements}
              />
            </div>
          </div>

          {/* ── Right lane: bet table + betting lane ───────────────────── */}
          <div className="flex-1 flex flex-col min-h-0">
            {activeProvider ? (
              <>
                {/* Bet table */}
                <div className="flex-1 min-h-0 overflow-y-auto">
                  <table className="sq w-full">
                    <thead>
                      <tr className="text-muted text-[10px]">
                        <th className="text-left pl-3">Event</th>
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
                      {activeBets.filter(b => {
                        if (b.edge_pct <= 0) return false;
                        if (activeBet === betKey(b) && liveEdge != null && liveEdge <= 0) return false;
                        return true;
                      }).map(b => {
                        const ttk = getTTKFromNow(b.start_time);
                        return (
                          <tr
                            key={betKey(b)}
                            className={`hover:bg-panel2/40 cursor-pointer ${activeBet === betKey(b) ? 'bg-panel2/60' : ''}`}
                            onClick={() => handlePlayBet(b)}
                          >
                            <td className="pl-3 text-sm text-text truncate max-w-[180px]" title={eventLabel(b)}>
                              {activeBet === betKey(b) && navigating && <span className="text-amber-400 text-[10px] mr-1">⟳</span>}
                              {activeBet === betKey(b) && !navigating && <span className="text-success text-[10px] mr-1">▸</span>}
                              {eventLabel(b)}
                            </td>
                            <td className="text-sm text-text truncate max-w-[100px]">{outcomeLabel(b)}</td>
                            <td className="text-right text-sm text-text">
                              {b.odds.toFixed(2)}
                              {activeTier === 'polymarket' && (
                                activeBet === betKey(b) && liveCents != null
                                  ? <span className="text-amber-400 text-[10px] ml-0.5">{liveCents.toFixed(1)}¢</span>
                                  : <span className="text-muted text-[10px] ml-0.5">{(100 / b.odds).toFixed(1)}¢</span>
                              )}
                            </td>
                            <td className="text-right text-sm text-muted">
                              {b.fair_odds.toFixed(2)}
                              {activeTier === 'polymarket' && <span className="text-[10px] ml-0.5">{(100 / b.fair_odds).toFixed(1)}¢</span>}
                            </td>
                            <td className={`text-right text-sm font-semibold ${b.edge_pct > 0 ? 'text-success' : 'text-error'}`}>
                              {activeBet === betKey(b) && liveEdge != null
                                ? <span className={liveEdge > 0 ? 'text-success' : 'text-danger'}>{liveEdge > 0 ? '+' : ''}{liveEdge.toFixed(1)}%</span>
                                : `+${b.edge_pct.toFixed(1)}%`
                              }
                            </td>
                            <td className="text-right text-sm text-text">{fmt(b.stake, activeTier)}</td>
                            <td className="text-right text-sm"><span className={getTTKColor(ttk)}>{formatTTKLabel(ttk)}</span></td>
                            <td className={`text-right text-sm ${getOddsAgeColor(b.odds_age_minutes)}`}>{formatOddsAge(b.odds_age_minutes)}</td>
                            <td className="text-right pr-2">
                              <button
                                onClick={(e) => { e.stopPropagation(); handleRemoveBet(b); }}
                                className="text-muted hover:text-error text-sm px-1"
                              >✕</button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* BettingLane */}
                <div className="flex-shrink-0 border-t border-zinc-800">
                  <BettingLane providerId={activeProvider} />
                </div>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-muted text-sm">
                Select a provider
              </div>
            )}
          </div>

        </div>
      )}
    </div>
  );
}
