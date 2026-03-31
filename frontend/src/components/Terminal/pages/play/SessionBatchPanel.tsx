import React, { useMemo, useState } from 'react';
import { resolveOutcome } from '@/utils/betting';
import { marketLabel } from '@/utils/betting';
import { getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
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
  onRemoveBet: (betKey: string) => void;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ClusterHeader({
  cluster,
  bets,
  expanded,
  onToggle,
}: {
  cluster: string;
  bets: ClusterBet[];
  expanded: boolean;
  onToggle: () => void;
}) {
  const totalStake = bets.reduce((s, b) => s + b.stake, 0);
  const totalEV = bets.reduce((s, b) => s + b.stake * (b.edge_pct / 100), 0);
  const isPolymarket = cluster === 'polymarket';
  const currency = isPolymarket ? 'USDC' : 'kr';

  return (
    <tr
      className="cursor-pointer hover:bg-panel2/60 transition-colors"
      onClick={onToggle}
    >
      <td colSpan={12} className="!py-1 !px-2 bg-panel border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-text text-sm w-3">{expanded ? '▾' : '▸'}</span>
          <span className="text-sm font-medium text-text uppercase tracking-wider">
            {cluster}
          </span>
          <span className="text-sm text-text">
            {bets.length} {bets.length === 1 ? 'bet' : 'bets'}
          </span>
          <span className="text-sm text-text">·</span>
          <span className="text-sm text-text">
            {isPolymarket ? totalStake.toFixed(2) : totalStake.toFixed(0)} {currency}
          </span>
          <span className="text-sm text-success">+{totalEV.toFixed(0)} {currency} EV</span>
        </div>
      </td>
    </tr>
  );
}

function BetRow({
  bet,
  onRemove,
}: {
  bet: ClusterBet;
  onRemove: () => void;
}) {
  const isPolymarket = bet.tier === 'polymarket';
  const currency = isPolymarket ? 'USDC' : 'kr';
  const market = marketLabel(bet.market);
  const outcome = outcomeLabel(bet);
  const event = eventLabel(bet);
  const prob = bet.fair_odds > 0 ? (1 / bet.fair_odds) * 100 : 0;

  return (
    <tr className="hover:bg-panel/60 transition-colors">
      {/* # */}
      <td className="text-muted text-sm">{bet.rank}</td>

      {/* Event */}
      <td className="text-sm text-text truncate max-w-[180px]" title={event}>
        {event}
        <div className="text-sm text-muted">
          {bet.sport}
        </div>
      </td>

      {/* Market */}
      <td className="text-sm text-muted">{market}</td>

      {/* Outcome */}
      <td className="text-sm text-text truncate max-w-[120px]" title={outcome}>
        {outcome}
      </td>

      {/* Cluster */}
      <td className="text-sm">
        <span className="text-muted uppercase tracking-wider text-[10px]">{bet.cluster}</span>
      </td>

      {/* Odds */}
      <td className="text-right text-sm font-medium text-text">
        {bet.odds.toFixed(2)}
      </td>

      {/* Fair */}
      <td className="text-right text-sm text-muted">
        {bet.fair_odds.toFixed(2)}
      </td>

      {/* Prob */}
      <td className="text-right text-sm text-muted">
        {prob.toFixed(0)}%
      </td>

      {/* Edge */}
      <td
        className={`text-right text-sm font-semibold ${
          bet.edge_pct > 0 ? 'text-success' : 'text-error'
        }`}
      >
        {bet.edge_pct > 0 ? '+' : ''}
        {bet.edge_pct.toFixed(1)}%
      </td>

      {/* Stake */}
      <td className="text-right text-sm text-text whitespace-nowrap">
        {isPolymarket
          ? `${bet.stake.toFixed(2)} ${currency}`
          : `${bet.stake.toFixed(0)} ${currency}`}
      </td>

      {/* TTK */}
      <td className="text-right text-sm">
        {(() => {
          const ttk = getTTKFromNow(bet.start_time);
          return <span className={getTTKColor(ttk)}>{formatTTKLabel(ttk)}</span>;
        })()}
      </td>

      {/* Remove */}
      <td className="text-right text-sm">
        <button
          onClick={onRemove}
          className="text-muted hover:text-error transition-colors"
          title="Remove from batch"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SessionBatchPanel({
  batch,
  summary,
  onRemoveBet,
}: Props) {
  // Group all bets by cluster uniformly
  const byCluster = useMemo(() => {
    const order: string[] = [];
    const groups: Record<string, ClusterBet[]> = {};
    for (const b of batch) {
      const c = b.cluster ?? 'ungrouped';
      if (!groups[c]) {
        order.push(c);
        groups[c] = [];
      }
      groups[c].push(b);
    }
    return order.map(c => ({ cluster: c, bets: groups[c] }));
  }, [batch]);

  // Collapsed state — all start collapsed
  const [expandedClusters, setExpandedClusters] = useState<Record<string, boolean>>({});
  const toggleCluster = (cluster: string) =>
    setExpandedClusters(prev => ({ ...prev, [cluster]: !prev[cluster] }));

  const sekBets = useMemo(() => batch.filter(b => b.tier !== 'polymarket'), [batch]);
  const usdcBets = useMemo(() => batch.filter(b => b.tier === 'polymarket'), [batch]);
  const sekStake = sekBets.reduce((s, b) => s + b.stake, 0);
  const usdcStake = usdcBets.reduce((s, b) => s + b.stake, 0);
  const totalEV = summary.total_expected_profit;

  return (
    <div className="flex flex-col min-h-0 flex-1">
      {/* Summary bar */}
      <div className="flex items-center gap-4 px-3 py-1.5 border border-border bg-panel text-sm flex-wrap">
        <span className="text-text font-medium">
          {batch.length} bets
        </span>
        <span className="text-muted">{sekStake.toFixed(0)} kr</span>
        {usdcStake > 0 && (
          <span className="text-muted">{usdcStake.toFixed(2)} USDC</span>
        )}
        <span className="text-success font-medium">
          +{totalEV.toFixed(0)} kr EV
        </span>
        {summary.polymarket_bets > 0 && (
          <>
            <span className="text-muted">|</span>
            <span className="text-tabPolymarket">
              POLY {summary.polymarket_bets} (+{summary.polymarket_ev.toFixed(0)} USDC)
            </span>
          </>
        )}
        {summary.pinnacle_bets > 0 && (
          <span className="text-tabReverse">
            PIN {summary.pinnacle_bets} (+{summary.pinnacle_ev.toFixed(0)} kr)
          </span>
        )}
        {summary.soft_bets > 0 && (
          <>
            <span className="text-success">
              SOFT {summary.soft_bets} (+{summary.soft_ev.toFixed(0)} kr)
            </span>
            {summary.tier_breakdown && Object.entries(summary.tier_breakdown)
              .sort(([a], [b]) => Number(a) - Number(b))
              .map(([tier, data]: [string, any]) => (
                <span key={tier} className="ml-2 text-muted">
                  T{tier}:{data.count}
                </span>
              ))
            }
          </>
        )}
      </div>

      {/* Batch table */}
      {batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border border-t-0 bg-panel">
          No bets in batch.
        </div>
      ) : (
        <div className="border-l-2 border-tabPlay flex-1 min-h-0 relative">
        <div className="absolute inset-0 overflow-y-auto">
          <table className="sq w-full table-fixed">
            <colgroup>
              <col style={{ width: '3%' }} />
              <col style={{ width: '19%' }} />
              <col style={{ width: '5%' }} />
              <col style={{ width: '12%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '5%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '4%' }} />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-panel">
              <tr>
                <th className="text-left">#</th>
                <th className="text-left">Event</th>
                <th className="text-left">Market</th>
                <th className="text-left">Outcome</th>
                <th className="text-left">Cluster</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Fair</th>
                <th className="text-right">Prob</th>
                <th className="text-right">Edge</th>
                <th className="text-right">Stake</th>
                <th className="text-right">TTK</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {byCluster.map(({ cluster, bets }) => (
                <React.Fragment key={cluster}>
                  <ClusterHeader
                    cluster={cluster}
                    bets={bets}
                    expanded={!!expandedClusters[cluster]}
                    onToggle={() => toggleCluster(cluster)}
                  />
                  {expandedClusters[cluster] && bets.map((b) => (
                    <BetRow
                      key={betKey(b)}
                      bet={b}
                      onRemove={() => onRemoveBet(betKey(b))}
                    />
                  ))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
        </div>
      )}
    </div>
  );
}
