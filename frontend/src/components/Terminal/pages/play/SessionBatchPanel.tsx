import React, { useMemo, useState, useEffect } from 'react';
import { resolveOutcome } from '@/utils/betting';
import { getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import type { ClusterBet, BatchSummary } from '@/types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function betKey(b: ClusterBet): string {
  return `${b.cluster}:${b.event_id}:${b.market}:${b.outcome}:${b.point ?? ''}`;
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

function eventLabel(b: ClusterBet): string {
  const home = b.display_home || b.sport;
  const away = b.display_away || '';
  if (home && away) return `${home} v ${away}`;
  return home || away || b.event_id;
}

function outcomeLabel(b: ClusterBet): string {
  return resolveOutcome(
    b.outcome,
    {
      home_team: b.display_home,
      away_team: b.display_away,
      display_home: b.display_home,
      display_away: b.display_away,
      market: b.market,
    },
    b.point,
  );
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
// Main component
// ---------------------------------------------------------------------------

export function SessionBatchPanel({
  batch,
  summary,
  providerBalances,
  onRemoveBet,
}: Props) {
  // Group bets by provider, sorted by cluster then EV descending
  const byProvider = useMemo(() => {
    const groups: Record<string, ClusterBet[]> = {};
    for (const b of batch) {
      const pid = b.provider_id ?? 'unknown';
      if (!groups[pid]) groups[pid] = [];
      groups[pid].push(b);
    }
    // Sort: group by cluster, within cluster sort by total EV desc
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

  // SSE: track provider detection and live balance updates
  const [liveBalances, setLiveBalances] = useState<Record<string, number>>({});
  const [detectedProviders, setDetectedProviders] = useState<Set<string>>(new Set());

  // SSE: single persistent connection, not dependent on batch refresh
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    const handleSync = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const provider = data.provider as string;
        if (data.balance != null) {
          setLiveBalances(prev => ({ ...prev, [provider]: data.balance }));
        }
        setDetectedProviders(prev => {
          if (prev.has(provider)) return prev;
          const next = new Set(prev);
          next.add(provider);
          return next;
        });
      } catch { /* ignore */ }
    };

    es.addEventListener('sync_available', handleSync);
    es.addEventListener('balance_synced', handleSync);
    es.addEventListener('deposit_detected', handleSync);
    return () => es.close();
  }, []); // stable — no dependencies, persistent connection

  const sekBets = useMemo(() => batch.filter(b => b.tier !== 'polymarket'), [batch]);
  const usdcBets = useMemo(() => batch.filter(b => b.tier === 'polymarket'), [batch]);
  const sekStake = sekBets.reduce((s, b) => s + b.stake, 0);
  const usdcStake = usdcBets.reduce((s, b) => s + b.stake, 0);
  const totalEV = summary.total_expected_profit;

  const fmt = (v: number, tier: string) =>
    tier === 'polymarket' ? `$${v.toFixed(1)} USDC` : `${Math.round(v)} kr`;

  return (
    <div className="flex flex-col min-h-0 flex-1">
      {/* Summary header — fire window style */}
      <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
        <span className="text-text font-medium">{batch.length} bets</span>
        <span className="text-muted text-[10px]">
          {sekStake > 0 && `${sekStake.toFixed(0)} kr`}
          {sekStake > 0 && usdcStake > 0 && ' + '}
          {usdcStake > 0 && `${usdcStake.toFixed(2)} USDC`}
        </span>
        <span className="text-success text-sm ml-auto">
          +{totalEV.toFixed(0)} kr EV
        </span>
      </div>

      {/* Provider list grouped by cluster */}
      {batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border border-t-0 bg-panel">
          No bets in batch.
        </div>
      ) : (
        <div className="border border-border border-t-0 bg-panel flex-1 min-h-0 relative">
          <div className="absolute inset-0 overflow-y-auto">
            {(() => {
              // Group providers by cluster for headers
              const clusterGroups: { cluster: string; providers: typeof byProvider }[] = [];
              let currentCluster = '';
              for (const entry of byProvider) {
                if (entry.cluster !== currentCluster) {
                  currentCluster = entry.cluster;
                  clusterGroups.push({ cluster: currentCluster, providers: [] });
                }
                clusterGroups[clusterGroups.length - 1].providers.push(entry);
              }

              return clusterGroups.map(({ cluster, providers: clusterProviders }) => {
                const clusterBets = clusterProviders.reduce((s, p) => s + p.bets.length, 0);
                const clusterEv = clusterProviders.reduce((s, p) => s + p.totalEv, 0);
                const clusterTier = clusterProviders[0]?.bets[0]?.tier || 'soft';

                return (
                  <React.Fragment key={cluster}>
                    {/* Cluster header */}
                    <div className="flex items-center gap-3 px-3 py-1 bg-panel2/30 border-b border-border">
                      <span className="text-[10px] text-muted font-medium uppercase tracking-wider">{cluster}</span>
                      <span className="text-[10px] text-muted">{clusterBets} bets · {clusterProviders.length} {clusterProviders.length === 1 ? 'provider' : 'providers'}</span>
                      <span className="text-[10px] text-success ml-auto">+{fmt(clusterEv, clusterTier)} EV</span>
                    </div>

                    {/* Provider rows */}
                    {clusterProviders.map(({ provider, bets }) => {
                      const tier = bets[0]?.tier || 'soft';
                      const totalStake = bets.reduce((s, b) => s + b.stake, 0);
                      const totalEv = bets.reduce((s, b) => s + b.expected_profit, 0);
                      const isExpanded = expandedProvider === provider;
                      const isDetected = detectedProviders.has(provider);
                      // Use live SSE balance if available, else batch balance
                      const bal = liveBalances[provider] ?? providerBalances?.[provider] ?? null;
                      const hasBal = bal != null;
                      const canCover = hasBal && bal >= totalStake;
                      const shortfall = hasBal ? Math.max(0, totalStake - bal) : totalStake;

                      // Dot color: green = can cover all bets, amber = detected but short, dim = not detected
                      const dotColor = canCover ? 'text-success' : isDetected ? 'text-amber-400' : 'text-muted/30';

                      return (
                        <React.Fragment key={provider}>
                          <div
                            className="flex items-center gap-3 px-3 pl-6 py-2 border-b border-border hover:bg-panel2/50 cursor-pointer transition-colors"
                            onClick={() => setExpandedProvider(isExpanded ? null : provider)}
                          >
                            <span className={`text-[10px] ${dotColor}`}>●</span>
                            <span className="text-sm font-medium text-text w-28 truncate uppercase">{provider}</span>
                            <span className="text-xs text-muted">{bets.length} bets</span>
                            <span className="text-xs text-muted">{fmt(totalStake, tier)}</span>
                            {hasBal && (
                              <span className={`text-xs ${canCover ? 'text-success' : 'text-muted'}`}>
                                bal {fmt(bal, tier)}
                              </span>
                            )}
                            {!canCover && shortfall > 0 && (
                              <span className="text-[10px] text-amber-400">
                                need +{fmt(shortfall, tier)}
                              </span>
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
                          {bets.map((b) => {
                            const ttk = getTTKFromNow(b.start_time);
                            return (
                              <tr key={betKey(b)} className={`hover:bg-panel2/40 ${b.funded === false ? 'opacity-50' : ''}`}>
                                <td className="pl-8 text-sm text-text truncate max-w-[200px]" title={eventLabel(b)}>
                                  {eventLabel(b)}
                                </td>
                                <td className="text-sm text-text truncate max-w-[100px]">{outcomeLabel(b)}</td>
                                <td className="text-right text-sm text-text">{b.odds.toFixed(2)}</td>
                                <td className="text-right text-sm text-muted">{b.fair_odds.toFixed(2)}</td>
                                <td className={`text-right text-sm font-semibold ${b.edge_pct > 0 ? 'text-success' : 'text-error'}`}>
                                  +{b.edge_pct.toFixed(1)}%
                                </td>
                                <td className="text-right text-sm text-text">{fmt(b.stake, tier)}</td>
                                <td className="text-right text-sm">
                                  <span className={getTTKColor(ttk)}>{formatTTKLabel(ttk)}</span>
                                </td>
                                <td className={`text-right text-sm ${getOddsAgeColor(b.odds_age_minutes)}`}>
                                  {formatOddsAge(b.odds_age_minutes)}
                                </td>
                                <td className="text-right">
                                  <button onClick={() => onRemoveBet(betKey(b))} className="text-muted hover:text-error text-xs">✕</button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                        </React.Fragment>
                      );
                    })}
                  </React.Fragment>
                );
              });
            })()}
          </div>
        </div>
      )}

    </div>
  );
}

