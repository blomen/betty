import { useMemo } from 'react';
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
  polymarket: {
    label: 'POLYMARKET',
    sublabel: null,
    color: '#a855f7',
    colorClass: 'text-purple-500',
    bgClass: 'bg-purple-500/10',
  },
  pinnacle: {
    label: 'PINNACLE',
    sublabel: 'reverse value',
    color: '#ef4444',
    colorClass: 'text-red-500',
    bgClass: 'bg-red-500/10',
  },
  soft: {
    label: 'SOFT VALUE',
    sublabel: 'round-robin',
    color: '#22c55e',
    colorClass: 'text-green-500',
    bgClass: 'bg-green-500/10',
  },
} as const;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function TierHeader({
  tier,
  count,
}: {
  tier: 'polymarket' | 'pinnacle' | 'soft';
  count: number;
}) {
  const cfg = TIER_CONFIG[tier];
  return (
    <tr>
      <td
        colSpan={10}
        className="!py-1.5 !px-2 bg-dark-900 border-b border-border"
      >
        <span
          className="text-[10px] font-bold tracking-wider uppercase"
          style={{ color: cfg.color }}
        >
          {cfg.label}
          {cfg.sublabel && (
            <span className="font-normal opacity-60"> — {cfg.sublabel}</span>
          )}{' '}
          — {count} {count === 1 ? 'bet' : 'bets'}
        </span>
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
          {bet.league ? ` · ${bet.league}` : ''}
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

  const totalStake = summary.total_stake;
  const totalEV = summary.total_expected_profit;
  const providerCount = useMemo(
    () => new Set(batch.map((b) => b.provider_id)).size,
    [batch],
  );

  const hasProjections = wageringProjections.length > 0;

  return (
    <div className="flex flex-col min-h-0">
      {/* ------------------------------------------------------------------ */}
      {/* Summary bar                                                         */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex items-center gap-4 px-3 py-1.5 border border-border bg-dark-800 text-xs flex-wrap">
        <span className="text-text font-medium">
          {summary.total_bets} {summary.total_bets === 1 ? 'bet' : 'bets'}
        </span>
        <span className="text-muted">{totalStake.toFixed(0)} kr</span>
        <span className="text-success font-medium">
          +{totalEV.toFixed(0)} kr EV
        </span>
        <span className="text-muted">|</span>
        <span className="text-muted">
          {providerCount} provider{providerCount !== 1 ? 's' : ''}
        </span>
        <span className="text-muted">|</span>
        {summary.polymarket_bets > 0 && (
          <span style={{ color: TIER_CONFIG.polymarket.color }}>
            POLY {summary.polymarket_bets} (+{summary.polymarket_ev.toFixed(0)})
          </span>
        )}
        {summary.pinnacle_bets > 0 && (
          <span style={{ color: TIER_CONFIG.pinnacle.color }}>
            PIN {summary.pinnacle_bets} (+{summary.pinnacle_ev.toFixed(0)})
          </span>
        )}
        {summary.soft_bets > 0 && (
          <span style={{ color: TIER_CONFIG.soft.color }}>
            SOFT {summary.soft_bets} (+{summary.soft_ev.toFixed(0)})
          </span>
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
        <div className="overflow-y-auto border border-border border-t-0">
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
                  <TierHeader tier="polymarket" count={polymarketBets.length} />
                  {polymarketBets.map((b) => (
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
                  <TierHeader tier="pinnacle" count={pinnacleBets.length} />
                  {pinnacleBets.map((b) => (
                    <BetRow
                      key={betKey(b)}
                      bet={b}
                      onRemove={() => onRemoveBet(betKey(b))}
                    />
                  ))}
                </>
              )}

              {/* Soft Value tier */}
              {softBets.length > 0 && (
                <>
                  <TierHeader tier="soft" count={softBets.length} />
                  {softBets.map((b) => (
                    <BetRow
                      key={betKey(b)}
                      bet={b}
                      onRemove={() => onRemoveBet(betKey(b))}
                    />
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
                  {proj.cluster || proj.provider_id}
                </span>
                <span className="text-muted">
                  {proj.wagering_remaining.toFixed(0)} →{' '}
                  <span className="text-amber-300">
                    {proj.projected_remaining.toFixed(0)} kr
                  </span>
                </span>
                {proj.days_remaining != null && (
                  <span className="text-muted text-[10px]">
                    ({proj.days_remaining}d left)
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
