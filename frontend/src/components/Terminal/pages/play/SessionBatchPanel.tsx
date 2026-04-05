import { useMemo, useState, useEffect, useCallback, useRef, Fragment } from 'react';
import { resolveOutcome } from '@/utils/betting';
import { getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { fireWindowApi } from '@/services/api/fireWindow';
import type { ClusterBet, BatchSummary } from '@/types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function betKey(b: ClusterBet): string {
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

function formatStake(amount: number, tier: string): string {
  if (tier === 'polymarket') return `$${amount.toFixed(1)} USDC`;
  return `${Math.round(amount)} kr`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  batch: ClusterBet[];
  summary: BatchSummary;
  providerBalances?: Record<string, number>;
  onRemoveBet: (betKey: string) => void;
}

// ---------------------------------------------------------------------------
// Main component — unified batch + fire view
// ---------------------------------------------------------------------------

export function SessionBatchPanel({ batch, summary, providerBalances, onRemoveBet }: Props) {
  // Group bets by provider, sorted by cluster then EV
  const byProvider = useMemo(() => {
    const groups: Record<string, ClusterBet[]> = {};
    for (const b of batch) {
      const pid = b.provider_id ?? 'unknown';
      if (!groups[pid]) groups[pid] = [];
      groups[pid].push(b);
    }
    const entries = Object.entries(groups).map(([pid, bets]) => ({
      provider: pid,
      bets,
      cluster: bets[0]?.cluster ?? pid,
      totalEv: bets.reduce((s, b) => s + b.expected_profit, 0),
    }));
    entries.sort((a, b) => {
      if (a.cluster !== b.cluster) return a.cluster.localeCompare(b.cluster);
      return b.totalEv - a.totalEv;
    });
    return entries;
  }, [batch]);

  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);

  // SSE: track provider states
  const [openedProviders, setOpenedProviders] = useState<Set<string>>(new Set());
  const [loggedInProviders, setLoggedInProviders] = useState<Set<string>>(new Set());
  const [liveBalances, setLiveBalances] = useState<Record<string, number>>({});

  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');
    const handleOpened = (e: MessageEvent) => {
      try {
        const p = JSON.parse(e.data).provider as string;
        setOpenedProviders(prev => prev.has(p) ? prev : new Set(prev).add(p));
      } catch { /* */ }
    };
    const handleLoggedIn = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const p = data.provider as string;
        setLoggedInProviders(prev => prev.has(p) ? prev : new Set(prev).add(p));
        if (data.balance != null) setLiveBalances(prev => ({ ...prev, [p]: data.balance }));
      } catch { /* */ }
    };
    es.addEventListener('provider_opened', handleOpened);
    es.addEventListener('sync_available', handleLoggedIn);
    es.addEventListener('balance_synced', handleLoggedIn);
    return () => es.close();
  }, []);

  // Auto-open tabs for providers with balance on first load
  const tabsOpenedRef = useRef(false);
  useEffect(() => {
    if (tabsOpenedRef.current || batch.length === 0) return;
    tabsOpenedRef.current = true;
    // Open fire window first (needed for open-tabs), then open tabs
    (async () => {
      try {
        await fireWindowApi.open(batch);
        await fireWindowApi.openTabs();
      } catch { /* non-critical */ }
    })();
  }, [batch]);

  // Betting state
  const [bettingProvider, setBettingProvider] = useState<string | null>(null);
  const [currentBet, setCurrentBet] = useState<any>(null);
  const [priceCheck, setPriceCheck] = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [placedCount, setPlacedCount] = useState(0);
  const [skippedCount, setSkippedCount] = useState(0);
  const [fireWindowOpen, setFireWindowOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Auto-start: when a provider becomes logged in, open fire window + start
  const autoStartRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (bettingProvider) return; // Already betting
    for (const pid of loggedInProviders) {
      if (autoStartRef.current.has(pid)) continue;
      autoStartRef.current.add(pid);
      startBetting(pid);
      break; // One at a time
    }
  }, [loggedInProviders, bettingProvider]);

  const startBetting = useCallback(async (providerId: string) => {
    setError(null);
    setBettingProvider(providerId);
    setPlacedCount(0);
    setSkippedCount(0);
    try {
      // Step 1: Check for settled bets first
      try {
        const scanResult = await fetch('/api/opportunities/play/settle-scan').then(r => r.json());
        if (scanResult.count > 0) {
          // Auto-confirm all settlements
          const settlements = scanResult.proposals
            .filter((p: any) => p.provider_id === providerId)
            .map((p: any) => ({ bet_id: p.bet_id, result: p.proposed_result }));
          if (settlements.length > 0) {
            await fetch('/api/opportunities/play/settle-batch', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(settlements),
            });
            console.log(`[Play] Auto-settled ${settlements.length} bets for ${providerId}`);
          }
        }
      } catch { /* non-critical */ }

      // Step 2: Open fire window + activate
      if (!fireWindowOpen) {
        await fireWindowApi.open(batch);
        setFireWindowOpen(true);
      }
      await fireWindowApi.activate(providerId);

      // Step 3: Start proposing bets
      fetchNextBet(providerId);
    } catch (err: any) {
      setError(err.message);
      setBettingProvider(null);
    }
  }, [batch, fireWindowOpen]);

  const fetchNextBet = useCallback(async (providerId: string) => {
    setCurrentBet(null);
    setPriceCheck(null);
    setChecking(false);
    setPlacing(false);
    try {
      const next = await fireWindowApi.getNextBet();
      if (next.done) {
        setBettingProvider(null);
        setCurrentBet(null);
        return;
      }
      setCurrentBet(next);
      setChecking(true);
      try {
        const check = await fireWindowApi.checkBet(next.bet_id);
        setPriceCheck(check);
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
      setError(err.message);
      setBettingProvider(null);
    }
  }, []);

  const handleConfirm = useCallback(async () => {
    if (!currentBet || !bettingProvider) return;
    setPlacing(true);
    try {
      await fireWindowApi.placeBet(currentBet.bet_id);
      setPlacedCount(prev => prev + 1);
    } catch (err: any) {
      setError(err.message);
    }
    fetchNextBet(bettingProvider);
  }, [currentBet, bettingProvider, fetchNextBet]);

  const handleSkip = useCallback(async () => {
    if (!currentBet || !bettingProvider) return;
    await fireWindowApi.skipBet(currentBet.bet_id);
    setSkippedCount(prev => prev + 1);
    fetchNextBet(bettingProvider);
  }, [currentBet, bettingProvider, fetchNextBet]);

  // Stats
  const sekBets = useMemo(() => batch.filter(b => b.tier !== 'polymarket'), [batch]);
  const usdcBets = useMemo(() => batch.filter(b => b.tier === 'polymarket'), [batch]);
  const sekStake = sekBets.reduce((s, b) => s + b.stake, 0);
  const usdcStake = usdcBets.reduce((s, b) => s + b.stake, 0);
  const totalEV = summary.total_expected_profit;

  const fmt = (v: number, tier: string) =>
    tier === 'polymarket' ? `$${v.toFixed(1)} USDC` : `${Math.round(v)} kr`;

  // ---------------------------------------------------------------------------
  // Render: Bet confirmation overlay
  // ---------------------------------------------------------------------------

  if (bettingProvider && currentBet) {
    const bet = currentBet;
    const tier = bettingProvider === 'polymarket' ? 'polymarket' : 'soft';
    const liveCents = priceCheck?.live_cents;
    const fairCents = bet.fair_cents || priceCheck?.fair_cents;
    const liveEdge = priceCheck?.live_edge;
    const displayEdge = liveEdge ?? bet.edge_pct;

    return (
      <div className="flex flex-col min-h-0 flex-1 gap-2">
        <div className="border border-border bg-panel px-3 py-2 flex items-center gap-3">
          <span className="text-sm font-medium text-text uppercase">{bettingProvider}</span>
          <span className="text-xs text-muted">{bet.remaining_bets} remaining</span>
          <span className="text-xs text-success">{placedCount} placed</span>
          {skippedCount > 0 && <span className="text-xs text-muted">{skippedCount} skipped</span>}
        </div>

        {error && <div className="text-danger text-xs px-3">{error}</div>}

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
                <td className="text-right text-sm font-medium text-text">{formatStake(bet.stake, tier)}</td>
                <td className={`text-right text-sm font-semibold ${displayEdge > 0 ? 'text-success' : 'text-error'}`}>
                  {displayEdge > 0 ? '+' : ''}{displayEdge?.toFixed(1)}%
                </td>
              </tr>
            </tbody>
          </table>
          {checking && <div className="text-xs text-muted animate-pulse px-3 py-1">Checking live price...</div>}
        </div>

        <div className="flex items-center gap-2 px-1">
          <button onClick={() => { setBettingProvider(null); setCurrentBet(null); }} className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90">Back</button>
          <button onClick={handleSkip} className="px-3 py-1 text-xs text-muted hover:text-foreground">Skip</button>
          <button onClick={handleConfirm} disabled={placing || checking} className="px-4 py-1.5 text-xs bg-success text-bg font-medium hover:opacity-90 disabled:opacity-50">
            {placing ? 'Placing...' : 'Confirm'}
          </button>
        </div>
      </div>
    );
  }

  // Loading state for betting
  if (bettingProvider && !currentBet) {
    return (
      <div className="border border-border bg-panel px-4 py-6 text-center">
        <div className="text-sm text-foreground animate-pulse">Loading next bet for {bettingProvider}...</div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Provider list (main view)
  // ---------------------------------------------------------------------------

  // Group by cluster
  const clusterGroups: { cluster: string; providers: typeof byProvider }[] = [];
  let curCluster = '';
  for (const entry of byProvider) {
    if (entry.cluster !== curCluster) {
      curCluster = entry.cluster;
      clusterGroups.push({ cluster: curCluster, providers: [] });
    }
    clusterGroups[clusterGroups.length - 1].providers.push(entry);
  }

  return (
    <div className="flex flex-col min-h-0 flex-1">
      {/* Summary */}
      <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
        <span className="text-text font-medium">{batch.length} bets</span>
        <span className="text-muted text-[10px]">
          {sekStake > 0 && `${sekStake.toFixed(0)} kr`}
          {sekStake > 0 && usdcStake > 0 && ' + '}
          {usdcStake > 0 && `${usdcStake.toFixed(2)} USDC`}
        </span>
        <span className="text-success text-sm">+{totalEV.toFixed(0)} kr EV</span>
        <span className="text-xs text-muted ml-auto">
          {loggedInProviders.size > 0 && `${loggedInProviders.size} logged in`}
          {loggedInProviders.size === 0 && openedProviders.size > 0 && 'Waiting for login...'}
          {loggedInProviders.size === 0 && openedProviders.size === 0 && 'Open provider sites to start'}
        </span>
      </div>

      {batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border border-t-0 bg-panel">No bets in batch.</div>
      ) : (
        <div className="border border-border border-t-0 bg-panel flex-1 min-h-0 relative">
          <div className="absolute inset-0 overflow-y-auto">
            {clusterGroups.map(({ cluster, providers: clusterProviders }) => {
              const clusterBets = clusterProviders.reduce((s, p) => s + p.bets.length, 0);
              const clusterEv = clusterProviders.reduce((s, p) => s + p.totalEv, 0);
              const clusterTier = clusterProviders[0]?.bets[0]?.tier || 'soft';

              return (
                <Fragment key={cluster}>
                  <div className="flex items-center gap-3 px-3 py-1 bg-panel2/30 border-b border-border">
                    <span className="text-[10px] text-muted font-medium uppercase tracking-wider">{cluster}</span>
                    <span className="text-[10px] text-muted">{clusterBets} bets · {clusterProviders.length} providers</span>
                    <span className="text-[10px] text-success ml-auto">+{fmt(clusterEv, clusterTier)} EV</span>
                  </div>

                  {clusterProviders.map(({ provider, bets }) => {
                    const tier = bets[0]?.tier || 'soft';
                    const totalStake = bets.reduce((s, b) => s + b.stake, 0);
                    const totalEv = bets.reduce((s, b) => s + b.expected_profit, 0);
                    const bal = liveBalances[provider] ?? providerBalances?.[provider] ?? null;
                    const hasBal = bal != null;
                    const canCover = hasBal && bal >= totalStake;
                    const shortfall = hasBal ? Math.max(0, totalStake - bal) : totalStake;
                    const isLoggedIn = loggedInProviders.has(provider);
                    const isOpened = openedProviders.has(provider);
                    const isExpanded = expandedProvider === provider;

                    const dotColor = isLoggedIn ? 'text-success' : isOpened ? 'text-amber-400' : 'text-muted/30';

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
                          {hasBal && (
                            <span className={`text-xs ${canCover ? 'text-success' : 'text-muted'}`}>bal {fmt(bal, tier)}</span>
                          )}
                          {!canCover && shortfall > 0 && (
                            <span className="text-[10px] text-amber-400">need +{fmt(shortfall, tier)}</span>
                          )}
                          {isLoggedIn && (
                            <span className="text-[10px] text-success">logged in</span>
                          )}
                          {isOpened && !isLoggedIn && (
                            <span className="text-[10px] text-amber-400">login...</span>
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
                                      <td className="text-right"><button onClick={() => onRemoveBet(betKey(b))} className="text-muted hover:text-error text-xs">✕</button></td>
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
      )}
    </div>
  );
}
