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
  capitalPlan?: any;
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
      <td colSpan={13} className="!py-1 !px-2 bg-panel border-b border-border">
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

      {/* Upd */}
      <td className={`text-right text-sm ${getOddsAgeColor(bet.odds_age_minutes)}`}>
        {formatOddsAge(bet.odds_age_minutes)}
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
  capitalPlan,
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
              <col style={{ width: '18%' }} />
              <col style={{ width: '5%' }} />
              <col style={{ width: '11%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '5%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '5%' }} />
              <col style={{ width: '5%' }} />
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
                <th className="text-right">Upd</th>
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

      {/* Inline capital plan */}
      {capitalPlan?.actions?.length > 0 && (
        <CapitalSummary actions={capitalPlan.actions} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline capital summary
// ---------------------------------------------------------------------------

function CapitalSummary({ actions }: { actions: any[] }) {
  const [expanded, setExpanded] = useState(false);
  const deposits = actions.filter((a: any) => a.type === 'deposit');
  const withdrawals = actions.filter((a: any) => a.type === 'withdraw');
  const totalDeposit = deposits.reduce((s: number, a: any) => s + (a.amount || 0), 0);

  if (deposits.length === 0 && withdrawals.length === 0) return null;

  return (
    <div className="border border-border bg-panel text-sm mt-1">
      <div
        className="flex items-center gap-4 px-3 py-1.5 cursor-pointer hover:bg-panel2/60"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-text text-xs w-3">{expanded ? '▾' : '▸'}</span>
        <span className="text-muted font-medium text-xs uppercase tracking-wider">Capital Plan</span>
        {deposits.length > 0 && (
          <span className="text-amber-400 text-xs">
            {deposits.length} deposit{deposits.length > 1 ? 's' : ''} needed · {Math.round(totalDeposit)} kr total
          </span>
        )}
      </div>
      {expanded && (
        <div className="pb-2">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted">
                <th className="text-left px-3 py-1 font-medium">Provider</th>
                <th className="text-right px-3 py-1 font-medium">Balance</th>
                <th className="text-right px-3 py-1 font-medium">Needed</th>
                <th className="text-right px-3 py-1 font-medium">Deposit</th>
                <th className="text-right px-3 py-1 font-medium">Bets</th>
                <th className="text-right px-3 py-1 font-medium">EV</th>
              </tr>
            </thead>
            <tbody>
              {deposits.map((a: any, i: number) => {
                const currentBal = (a.target_balance || 0) - (a.amount || 0);
                return (
                  <tr key={i} className="hover:bg-panel2/40">
                    <td className="px-3 py-0.5 text-text">{a.provider_id}</td>
                    <td className="px-3 py-0.5 text-right text-muted">
                      {Math.round(currentBal)} {a.currency === 'USDC' ? 'USDC' : 'kr'}
                    </td>
                    <td className="px-3 py-0.5 text-right text-text">
                      {Math.round(a.target_balance || 0)} {a.currency === 'USDC' ? 'USDC' : 'kr'}
                    </td>
                    <td className="px-3 py-0.5 text-right text-amber-400 font-medium">
                      +{Math.round(a.amount || 0)} {a.currency === 'USDC' ? 'USDC' : 'kr'}
                    </td>
                    <td className="px-3 py-0.5 text-right text-muted">
                      {a.unlocks ?? '—'}
                    </td>
                    <td className="px-3 py-0.5 text-right text-success">
                      +{Math.round(a.expected_ev || 0)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {withdrawals.length > 0 && (
            <div className="px-3 pt-2 border-t border-border mt-1">
              <span className="text-muted text-xs uppercase tracking-wider">Withdrawals</span>
              {withdrawals.map((a: any, i: number) => (
                <div key={i} className="flex items-center gap-3 text-xs mt-1">
                  <span className="text-text w-24">{a.provider_id}</span>
                  <span className="text-success font-medium">
                    {Math.round(a.amount || 0)} {a.currency === 'USDC' ? 'USDC' : 'kr'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
