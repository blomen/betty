import React, { useMemo, useState } from 'react';
import { ProviderName } from '../../ProviderName';
import { resolveOutcome } from '@/utils/betting';
import { marketLabel } from '@/utils/betting';
import type { BatchBet, BatchSummary, WageringProjection } from '@/types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function betKey(b: BatchBet): string {
  return `${b.provider_id}:${b.event_id}:${b.market}:${b.outcome}:${b.point ?? ''}`;
}

function eventLabel(b: BatchBet): string {
  const home = b.display_home || b.sport;
  const away = b.display_away || '';
  if (home && away) return `${home} v ${away}`;
  return home || away || b.event_id;
}

function outcomeLabel(b: BatchBet): string {
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
  batch: BatchBet[];
  summary: BatchSummary;
  wageringProjections: WageringProjection[];
  onRemoveBet: (betKey: string) => void;
}

// ---------------------------------------------------------------------------
// Tier config
// ---------------------------------------------------------------------------

const TIER_CONFIG = {
  polymarket: { color: '#a855f7' },
  pinnacle: { color: '#ef4444' },
  soft: { color: '#22c55e' },
} as const;

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
  bets: BatchBet[];
  expanded: boolean;
  onToggle: () => void;
}) {
  const totalStake = bets.reduce((s, b) => s + b.stake, 0);
  const totalEV = bets.reduce((s, b) => s + b.stake * (b.edge_pct / 100), 0);
  const isPolymarket = cluster === 'polymarket';
  const currency = isPolymarket ? 'USDC' : 'kr';

  return (
    <tr
      className="cursor-pointer hover:bg-dark-800/60 transition-colors"
      onClick={onToggle}
    >
      <td colSpan={10} className="!py-1 !px-2 bg-dark-850 border-b border-dark-700">
        <div className="flex items-center gap-2">
          <span className="text-dark-500 text-[10px] w-3">{expanded ? '▾' : '▸'}</span>
          <span className="text-[10px] font-medium text-dark-300 uppercase tracking-wider">
            {cluster}
          </span>
          <span className="text-[10px] text-dark-500">
            {bets.length} {bets.length === 1 ? 'bet' : 'bets'}
          </span>
          <span className="text-[10px] text-dark-500">·</span>
          <span className="text-[10px] text-dark-400">
            {isPolymarket ? totalStake.toFixed(2) : totalStake.toFixed(0)} {currency}
          </span>
          <span className="text-[10px] text-success">+{totalEV.toFixed(0)} EV</span>
        </div>
      </td>
    </tr>
  );
}

function BetRow({
  bet,
  onRemove,
}: {
  bet: BatchBet;
  onRemove: () => void;
}) {
  const isPolymarket = bet.tier === 'polymarket';
  const currency = isPolymarket ? 'USDC' : 'kr';
  const market = marketLabel(bet.market);
  const outcome = outcomeLabel(bet);
  const event = eventLabel(bet);
  const hasWageringBadge =
    bet.wagering_pct != null && bet.wagering_pct < 100;

  return (
    <tr className="hover:bg-dark-900/40 transition-colors">
      {/* # */}
      <td className="text-muted text-xs">{bet.rank}</td>

      {/* Event */}
      <td className="text-xs text-text truncate max-w-[180px]" title={event}>
        {event}
        <div className="text-[10px] text-muted">
          {bet.sport}
        </div>
      </td>

      {/* Market */}
      <td className="text-xs text-muted">{market}</td>

      {/* Outcome */}
      <td className="text-xs text-text truncate max-w-[120px]" title={outcome}>
        {outcome}
      </td>

      {/* Provider + cluster + wager badge */}
      <td className="text-xs">
        <div className="flex items-center gap-1 flex-wrap">
          <ProviderName name={bet.provider_id} />
          {bet.cluster && (
            <span className="text-dark-600 text-[10px]">{bet.cluster}</span>
          )}
          {hasWageringBadge && (
            <span className="bg-amber-500/20 text-amber-500 text-[9px] px-1 py-0.5 leading-none">
              {bet.wagering_pct!.toFixed(0)}%
            </span>
          )}
          {bet.is_bonus && (
            <span className="bg-accent/20 text-accent text-[9px] px-1 py-0.5 leading-none">
              {bet.bonus_type === 'freebet' ? 'FREE' : 'TRG'}
            </span>
          )}
        </div>
      </td>

      {/* Odds */}
      <td className="text-right text-xs font-medium text-text">
        {bet.odds.toFixed(2)}
      </td>

      {/* Fair */}
      <td className="text-right text-xs text-muted">
        {bet.fair_odds.toFixed(2)}
      </td>

      {/* Edge */}
      <td
        className={`text-right text-xs font-semibold ${
          bet.edge_pct > 0 ? 'text-success' : 'text-error'
        }`}
      >
        {bet.edge_pct > 0 ? '+' : ''}
        {bet.edge_pct.toFixed(1)}%
      </td>

      {/* Stake */}
      <td className="text-right text-xs text-text whitespace-nowrap">
        {isPolymarket
          ? `${bet.stake.toFixed(2)} ${currency}`
          : `${bet.stake.toFixed(0)} ${currency}`}
      </td>

      {/* Remove */}
      <td className="text-right text-xs">
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
  wageringProjections,
  onRemoveBet,
}: Props) {
  const polymarketBets = useMemo(
    () => batch.filter((b) => b.tier === 'polymarket'),
    [batch],
  );
  const pinnacleBets = useMemo(
    () => batch.filter((b) => b.tier === 'pinnacle'),
    [batch],
  );
  const softBets = useMemo(
    () => batch.filter((b) => b.tier === 'soft'),
    [batch],
  );

  // Group soft bets by cluster, preserving order of first appearance
  const softByCluster = useMemo(() => {
    const order: string[] = [];
    const groups: Record<string, BatchBet[]> = {};
    for (const b of softBets) {
      const c = b.cluster || b.provider_id;
      if (!groups[c]) {
        order.push(c);
        groups[c] = [];
      }
      groups[c].push(b);
    }
    return order.map(c => ({ cluster: c, bets: groups[c] }));
  }, [softBets]);

  // Collapsed state for clusters — all start collapsed
  const [expandedClusters, setExpandedClusters] = useState<Record<string, boolean>>({});
  const toggleCluster = (cluster: string) =>
    setExpandedClusters(prev => ({ ...prev, [cluster]: !prev[cluster] }));

  const sekBets = useMemo(() => batch.filter(b => b.tier !== 'polymarket'), [batch]);
  const usdcBets = useMemo(() => batch.filter(b => b.tier === 'polymarket'), [batch]);
  const sekStake = sekBets.reduce((s, b) => s + b.stake, 0);
  const usdcStake = usdcBets.reduce((s, b) => s + b.stake, 0);
  const totalEV = batch.reduce((s, b) => s + b.expected_profit, 0);

  const hasProjections = wageringProjections.length > 0;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* ------------------------------------------------------------------ */}
      {/* Summary bar                                                         */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex items-center gap-4 px-3 py-1.5 border border-border bg-dark-800 text-xs flex-wrap">
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
            <span style={{ color: TIER_CONFIG.polymarket.color }}>
              POLY {summary.polymarket_bets} (+{summary.polymarket_ev.toFixed(0)})
            </span>
          </>
        )}
        {summary.pinnacle_bets > 0 && (
          <span style={{ color: TIER_CONFIG.pinnacle.color }}>
            PIN {summary.pinnacle_bets} (+{summary.pinnacle_ev.toFixed(0)})
          </span>
        )}
        {summary.soft_bets > 0 && (
          <>
            <span style={{ color: TIER_CONFIG.soft.color }}>
              SOFT {summary.soft_bets} (+{summary.soft_ev.toFixed(0)})
            </span>
            {summary.tier_breakdown && Object.entries(summary.tier_breakdown)
              .sort(([a], [b]) => Number(a) - Number(b))
              .map(([tier, data]: [string, any]) => (
                <span key={tier} className="ml-2 text-zinc-500">
                  T{tier}:{data.count}
                </span>
              ))
            }
          </>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Batch table                                                          */}
      {/* ------------------------------------------------------------------ */}
      {batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border border-t-0 bg-dark-800">
          No bets in batch.
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-y-auto border border-border border-t-0">
          <table className="sq w-full">
            <colgroup>
              <col style={{ width: '3%' }} />
              <col style={{ width: '22%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '13%' }} />
              <col style={{ width: '18%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '11%' }} />
              <col style={{ width: '4%' }} />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-dark-800">
              <tr>
                <th className="text-left">#</th>
                <th className="text-left">Event</th>
                <th className="text-left">Market</th>
                <th className="text-left">Outcome</th>
                <th className="text-left">Provider</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Fair</th>
                <th className="text-right">Edge</th>
                <th className="text-right">Stake</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {/* Polymarket tier */}
              {polymarketBets.length > 0 && (
                <>
                  <ClusterHeader
                    cluster="polymarket"
                    bets={polymarketBets}
                    expanded={!!expandedClusters['polymarket']}
                    onToggle={() => toggleCluster('polymarket')}
                  />
                  {expandedClusters['polymarket'] && polymarketBets.map((b) => (
                    <BetRow
                      key={betKey(b)}
                      bet={b}
                      onRemove={() => onRemoveBet(betKey(b))}
                    />
                  ))}
                </>
              )}

              {/* Pinnacle tier */}
              {pinnacleBets.length > 0 && (
                <>
                  <ClusterHeader
                    cluster="pinnacle"
                    bets={pinnacleBets}
                    expanded={!!expandedClusters['pinnacle']}
                    onToggle={() => toggleCluster('pinnacle')}
                  />
                  {expandedClusters['pinnacle'] && pinnacleBets.map((b) => (
                    <BetRow
                      key={betKey(b)}
                      bet={b}
                      onRemove={() => onRemoveBet(betKey(b))}
                    />
                  ))}
                </>
              )}

              {/* Soft Value — grouped by cluster */}
              {softBets.length > 0 && (
                <>
                  {softByCluster.map(({ cluster, bets }) => (
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
                </>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Wagering projection bar                                             */}
      {/* ------------------------------------------------------------------ */}
      {hasProjections && (
        <div className="border border-border border-t-0 bg-amber-500/5 px-3 py-1.5">
          <div className="flex items-center gap-1 mb-1">
            <span className="text-[10px] font-bold text-amber-500 tracking-wider uppercase">
              Wagering After Batch
            </span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5">
            {wageringProjections.map((proj) => (
              <div
                key={`${proj.provider_id}-${proj.cluster}`}
                className="flex items-center gap-1.5 text-[11px]"
              >
                <span className="text-amber-400 font-medium">
                  {proj.provider_id}
                </span>
                {(() => {
                  const total = proj.wagering_total || proj.wagering_remaining;
                  const beforePct = total > 0 ? Math.round(((total - proj.wagering_remaining) / total) * 100) : 100;
                  const afterPct = total > 0 ? Math.round(((total - proj.projected_remaining) / total) * 100) : 100;
                  return (
                    <>
                      <span className="text-muted">{beforePct}%</span>
                      <span className="text-dark-500">→</span>
                      <span className={afterPct >= 100 ? 'text-success' : 'text-amber-300'}>{afterPct}%</span>
                    </>
                  );
                })()}
                {proj.days_remaining != null && (
                  <span className="text-muted text-[10px]">
                    {proj.days_remaining}d
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
