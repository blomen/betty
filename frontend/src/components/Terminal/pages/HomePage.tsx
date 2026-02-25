import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import type { SpecialItem } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { ExtractionProgressBar } from '../ExtractionProgressBar';
import type { TabName } from '../Sidebar';
import type { Bet, Opportunity, BankrollStats, BankrollExposure, PolymarketValueBet } from '@/types';

// ── Helpers ─────────────────────────────────────────────────────────

function resolveOutcome(bet: Bet): string {
  const outcome = bet.outcome || '-';
  if (outcome === 'home' && bet.home_team) return bet.home_team;
  if (outcome === 'away' && bet.away_team) return bet.away_team;
  if (outcome === 'draw') return 'Draw';
  if (outcome === 'over') return 'Over';
  if (outcome === 'under') return 'Under';
  return outcome;
}

/** Hours from NOW to kickoff (0 if already started) */
function getLiveTTK(bet: Bet): number | null {
  if (!bet.start_time) return null;
  const start = new Date(bet.start_time).getTime();
  const now = Date.now();
  return Math.max(0, (start - now) / (1000 * 60 * 60));
}

function formatTTK(hours: number): string {
  if (hours < 1 / 60) return '<1m';
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

const SPORT_SHORT: Record<string, string> = {
  football: 'FBL', soccer: 'FBL', tennis: 'TEN', basketball: 'BKT',
  ice_hockey: 'ICE', hockey: 'ICE', esports: 'ESP', mma: 'MMA',
  baseball: 'BSB', american_football: 'NFL', handball: 'HBL',
  volleyball: 'VLB', table_tennis: 'TT', boxing: 'BOX',
  cricket: 'CRK', rugby: 'RGY', darts: 'DRT', snooker: 'SNK',
};

function formatSport(sport?: string | null): string {
  if (!sport) return '?';
  return SPORT_SHORT[sport.toLowerCase()] || sport.slice(0, 3).toUpperCase();
}

function formatMarketShort(market?: string | null): string {
  if (!market) return '';
  if (market === '1x2' || market === 'moneyline') return '';
  if (market === 'spread') return 'HC';
  if (market === 'total') return 'O/U';
  return market;
}

// ── Section header ──────────────────────────────────────────────────

function SectionHeader({ icon, color, title, count, action }: {
  icon: string; color: string; title: string; count?: number;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex items-center justify-between">
      <h3 className="text-xs text-muted uppercase tracking-wider font-semibold flex items-center gap-2">
        <TabIcon name={icon} color={color} size={14} />
        <span>{title}</span>
        {count !== undefined && <span style={{ color }} className="font-mono">{count}</span>}
      </h3>
      {action && (
        <button
          onClick={action.onClick}
          className="text-[10px] text-muted hover:text-text transition-colors"
        >
          {action.label} &rarr;
        </button>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────

interface MonitorPageProps {
  onTabChange: (tab: TabName) => void;
}

export function MonitorPage({ onTabChange }: MonitorPageProps) {
  const [bets, setBets] = useState<Bet[]>([]);
  const [stats, setStats] = useState<BankrollStats | null>(null);
  const [exposure, setExposure] = useState<BankrollExposure | null>(null);
  const [valueOpps, setValueOpps] = useState<Opportunity[]>([]);
  const [reverseOpps, setReverseOpps] = useState<Opportunity[]>([]);
  const [polyOpps, setPolyOpps] = useState<PolymarketValueBet[]>([]);
  const [specials, setSpecials] = useState<SpecialItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setIsLoading(true);
    try {
      // Snapshot closing odds first (captures CLV when bets cross TTK 0)
      await api.closeStartedBets().catch(() => {});

      const [betsRes, statsRes, exposureRes, valueRes, reverseRes, polyRes, specialsRes] = await Promise.all([
        api.getBets('pending', 50),
        api.getBankrollStats(),
        api.getBankrollExposure(),
        api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 0),
        api.getOpportunities('reverse_value', true),
        api.getPolymarketValue(0),
        api.getSpecials({ sort: 'edge_pct', order: 'desc' }),
      ]);
      setBets(betsRes.bets);
      setStats(statsRes);
      setExposure(exposureRes);
      setValueOpps(valueRes.opportunities);
      setReverseOpps(reverseRes.opportunities);
      setPolyOpps(polyRes.value_bets);
      setSpecials(specialsRes.specials);
    } catch (err) {
      console.error('MonitorPage fetch failed:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 30000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // ── Derived data ────────────────────────────────────────────────

  // 2-way split: Settle (past start) | Upcoming (future start)
  const needsSettleBets = useMemo(() => {
    const now = Date.now();
    return bets
      .filter(b => b.start_time && new Date(b.start_time).getTime() <= now)
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : 0;
        const tb = b.start_time ? new Date(b.start_time).getTime() : 0;
        return tb - ta; // most recent first
      });
  }, [bets]);

  const upcomingBets = useMemo(() => {
    const now = Date.now();
    return bets
      .filter(b => !b.start_time || new Date(b.start_time).getTime() > now)
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb; // soonest first
      });
  }, [bets]);

  // Events already bet on — exclude from opportunity lists
  const bettedEventIds = useMemo(() => {
    const ids = new Set<string>();
    for (const b of bets) { if (b.event_id) ids.add(b.event_id); }
    return ids;
  }, [bets]);

  // Filtered opportunities (exclude events with existing bets)
  const unbettedValue = useMemo(() =>
    valueOpps.filter(o => !bettedEventIds.has(o.event_id)),
    [valueOpps, bettedEventIds]
  );
  const unbettedReverse = useMemo(() =>
    reverseOpps.filter(o => !bettedEventIds.has(o.event_id)),
    [reverseOpps, bettedEventIds]
  );
  const unbettedPoly = useMemo(() =>
    polyOpps.filter(o => !bettedEventIds.has(o.event_id)),
    [polyOpps, bettedEventIds]
  );

  // Top 3 from each (sorted by edge)
  const topValue = useMemo(() =>
    [...unbettedValue].sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0)).slice(0, 3),
    [unbettedValue]
  );
  const topReverse = useMemo(() =>
    [...unbettedReverse].sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0)).slice(0, 3),
    [unbettedReverse]
  );
  const topPoly = useMemo(() =>
    [...unbettedPoly].sort((a, b) => b.edge_pct - a.edge_pct).slice(0, 3),
    [unbettedPoly]
  );
  const topSpecials = useMemo(() =>
    [...specials]
      .filter(s => s.boost_pct != null && s.boost_pct > 0)
      .sort((a, b) => (b.boost_pct ?? 0) - (a.boost_pct ?? 0))
      .slice(0, 3),
    [specials]
  );

  const netWorth = exposure?.total_balance ?? 0;
  const pendingStake = exposure?.total_pending ?? 0;

  const handleManualSettle = async (bet: Bet, result: 'won' | 'lost' | 'void') => {
    const payout = result === 'won' ? bet.stake * bet.odds : result === 'void' ? bet.stake : 0;
    try {
      await api.settleBet(bet.id, { result, payout });
      fetchAll();
    } catch (err) {
      console.error('Manual settle failed:', err);
    }
  };

  if (isLoading && !stats) {
    return (
      <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
        Loading...
      </div>
    );
  }

  return (
    <div>
      {/* ── Sticky header: stats bar ──────────────────────────── */}
      <div className="sticky top-0 z-10 -mx-4 -mt-4 px-4 pt-4 pb-4 bg-bg space-y-4 border-b border-border">
        <ExtractionProgressBar />

      {/* ── Net Worth Banner ─────────────────────────────────────── */}
      <div className="border border-border bg-panel overflow-hidden">
        <div className="grid grid-cols-6 gap-px bg-border">
          {/* Total Balance */}
          <div className="bg-panel2 px-4 py-3 col-span-2">
            <div className="text-[10px] text-muted uppercase tracking-wider mb-1">Balance</div>
            <div className="text-2xl font-bold text-text">
              {netWorth.toLocaleString('en', { maximumFractionDigits: 0 })} <span className="text-sm text-muted font-normal">kr</span>
            </div>
            <div className="flex items-center gap-3 mt-1 text-[11px]">
              <span className="text-muted">
                Exposure: <span className="text-accent">{pendingStake.toLocaleString('en', { maximumFractionDigits: 0 })} kr</span>
                {netWorth > 0 && (
                  <span className="text-muted2 ml-1">({(pendingStake / netWorth * 100).toFixed(1)}%)</span>
                )}
              </span>
            </div>
          </div>
          {/* Stats grid */}
          {stats && (
            <>
              <div className="bg-panel2 px-3 py-3">
                <div className="text-[10px] text-muted uppercase tracking-wider mb-1">Profit</div>
                <div className={`text-lg font-semibold ${stats.total_profit >= 0 ? 'text-success' : 'text-error'}`}>
                  {stats.total_profit >= 0 ? '+' : ''}{stats.total_profit.toLocaleString('en', { maximumFractionDigits: 0 })} <span className="text-xs font-normal">kr</span>
                </div>
              </div>
              <div className="bg-panel2 px-3 py-3">
                <div className="text-[10px] text-muted uppercase tracking-wider mb-1">ROI</div>
                <div className={`text-lg font-semibold ${stats.roi_pct >= 0 ? 'text-success' : 'text-error'}`}>
                  {stats.roi_pct >= 0 ? '+' : ''}{stats.roi_pct.toFixed(1)}%
                </div>
              </div>
              <div className="bg-panel2 px-3 py-3">
                <div className="text-[10px] text-muted uppercase tracking-wider mb-1">Bets</div>
                <div className="text-lg font-semibold text-text">{stats.total_bets}</div>
                <div className="flex items-center gap-1.5 text-[10px]">
                  <span className="text-success">{stats.wins}W</span>
                  <span className="text-error">{stats.losses}L</span>
                </div>
              </div>
              <div className="bg-panel2 px-3 py-3">
                <div className="text-[10px] text-muted uppercase tracking-wider mb-1">Avg CLV</div>
                {stats.clv_count > 0 ? (
                  <div className={`text-lg font-semibold ${stats.avg_clv >= 0 ? 'text-success' : 'text-error'}`}>
                    {stats.avg_clv >= 0 ? '+' : ''}{stats.avg_clv.toFixed(1)}%
                  </div>
                ) : (
                  <div className="text-lg font-semibold text-muted">-</div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* ── Top Opportunities ─────────────────────────────────────── */}
      <div className="border border-border bg-panel overflow-hidden">
        {/* Column headers */}
        <div className="grid grid-cols-4 gap-px bg-border">
          {([
            { icon: 'value', color: TAB_COLORS.value, title: 'Value', count: unbettedValue.length, tab: 'value' as TabName },
            { icon: 'reverse', color: TAB_COLORS.reverse, title: 'Reverse', count: unbettedReverse.length, tab: 'reverse' as TabName },
            { icon: 'polymarket', color: TAB_COLORS.polymarket, title: 'Poly', count: unbettedPoly.length, tab: 'polymarket' as TabName },
            { icon: 'specials', color: TAB_COLORS.specials, title: 'Specials', count: specials.length, tab: 'specials' as TabName },
          ]).map(col => (
            <div key={col.tab} className="bg-panel2 px-3 py-2 flex items-center justify-between">
              <h3 className="text-[10px] text-muted uppercase tracking-wider font-semibold flex items-center gap-1.5">
                <TabIcon name={col.icon} color={col.color} size={12} />
                <span>{col.title}</span>
                <span style={{ color: col.color }} className="font-mono">{col.count}</span>
              </h3>
              <button
                onClick={() => onTabChange(col.tab)}
                className="text-[10px] text-muted hover:text-text transition-colors"
              >
                all &rarr;
              </button>
            </div>
          ))}
        </div>
        {/* Rows — 3 opportunity rows, each spanning 4 columns */}
        {[0, 1, 2].map(row => (
          <div key={row} className="grid grid-cols-4 gap-px bg-border">
            {/* Value */}
            <div className="bg-panel px-3 py-2 min-w-0">
              {topValue[row] ? (() => {
                const opp = topValue[row];
                return (
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-[11px] text-text truncate">
                        {opp.home_team && opp.away_team ? `${opp.home_team} v ${opp.away_team}` : opp.event_id.slice(0, 24)}
                      </div>
                      <div className="text-[10px] text-muted truncate">
                        {formatProviderName(opp.provider1)} {opp.outcome1} <span className="text-text font-medium">{opp.odds1.toFixed(2)}</span>
                      </div>
                    </div>
                    <span className="text-sm font-bold flex-shrink-0" style={{ color: TAB_COLORS.value }}>+{(opp.edge_pct ?? 0).toFixed(1)}%</span>
                  </div>
                );
              })() : <div className="text-muted2 text-[10px]">-</div>}
            </div>
            {/* Reverse */}
            <div className="bg-panel px-3 py-2 min-w-0">
              {topReverse[row] ? (() => {
                const opp = topReverse[row];
                return (
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-[11px] text-text truncate">
                        {opp.home_team && opp.away_team ? `${opp.home_team} v ${opp.away_team}` : opp.event_id.slice(0, 24)}
                      </div>
                      <div className="text-[10px] text-muted truncate">
                        Pinnacle {opp.outcome1} <span className="text-text font-medium">{opp.odds1.toFixed(2)}</span>
                      </div>
                    </div>
                    <span className="text-sm font-bold flex-shrink-0" style={{ color: TAB_COLORS.reverse }}>+{(opp.edge_pct ?? 0).toFixed(1)}%</span>
                  </div>
                );
              })() : <div className="text-muted2 text-[10px]">-</div>}
            </div>
            {/* Poly */}
            <div className="bg-panel px-3 py-2 min-w-0">
              {topPoly[row] ? (() => {
                const opp = topPoly[row];
                return (
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-[11px] text-text truncate">
                        {opp.home_team} v {opp.away_team}
                      </div>
                      <div className="text-[10px] text-muted truncate">
                        Polymarket {opp.outcome} <span className="text-text font-medium">{opp.polymarket_odds.toFixed(2)}</span>
                      </div>
                    </div>
                    <span className="text-sm font-bold flex-shrink-0" style={{ color: TAB_COLORS.polymarket }}>+{opp.edge_pct.toFixed(1)}%</span>
                  </div>
                );
              })() : <div className="text-muted2 text-[10px]">-</div>}
            </div>
            {/* Specials */}
            <div className="bg-panel px-3 py-2 min-w-0">
              {topSpecials[row] ? (() => {
                const sp = topSpecials[row];
                return (
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-[11px] text-text truncate">{sp.title}</div>
                      <div className="text-[10px] text-muted truncate">
                        {formatProviderName(sp.provider)} <span className="text-text font-medium">{sp.boosted_odds?.toFixed(2) ?? '-'}</span>
                        {sp.original_odds != null && <span className="text-muted2 line-through ml-1">{sp.original_odds.toFixed(2)}</span>}
                      </div>
                    </div>
                    <span className="text-sm font-bold flex-shrink-0" style={{ color: TAB_COLORS.specials }}>+{(sp.boost_pct ?? 0).toFixed(0)}%</span>
                  </div>
                );
              })() : <div className="text-muted2 text-[10px]">-</div>}
            </div>
          </div>
        ))}
      </div>
      </div>

      {/* ── Scrollable content ────────────────────────────────── */}
      <div className="space-y-4 pt-4">

      {/* ── Needs Settlement (past start, pending) ─────────────── */}
      {needsSettleBets.length > 0 && (
        <div>
          <SectionHeader
            icon="bets"
            color="#ef4444"
            title="Settle"
            count={needsSettleBets.length}
            action={{ label: 'History', onClick: () => onTabChange('stats') }}
          />
          <div className="mt-2 border-l-2 border-error">
            <table className="sq">
              <thead>
                <tr>
                  <th>Ago</th>
                  <th>Sport</th>
                  <th>Event</th>
                  <th>Pick</th>
                  <th className="text-right">Odds</th>
                  <th className="text-right">Prob</th>
                  <th className="text-right">Stake</th>
                  <th className="text-right">CLV</th>
                  <th className="text-right">Result</th>
                </tr>
              </thead>
              <tbody>
                {needsSettleBets.map(bet => {
                  const hoursAgo = bet.start_time
                    ? Math.max(0, (Date.now() - new Date(bet.start_time).getTime()) / (1000 * 60 * 60))
                    : null;
                  const agoStr = hoursAgo !== null
                    ? hoursAgo < 1 ? `${Math.round(hoursAgo * 60)}m`
                    : hoursAgo < 24 ? `${hoursAgo.toFixed(0)}h`
                    : `${(hoursAgo / 24).toFixed(0)}d`
                    : '?';
                  const prob = bet.selection_probability ?? (bet.odds > 0 ? 1 / bet.odds : null);
                  const mkt = formatMarketShort(bet.market);
                  return (
                    <tr key={bet.id} className="bg-error/[0.03]">
                      <td className="whitespace-nowrap">
                        <span className="text-[10px] font-medium text-error">{agoStr}</span>
                      </td>
                      <td className="whitespace-nowrap">
                        <span className="text-[10px] text-muted">{formatSport(bet.sport)}</span>
                        {mkt && <span className="text-[9px] text-muted2 ml-0.5">{mkt}</span>}
                      </td>
                      <td className="text-sm">
                        <span className="text-text font-medium">{bet.home_team || '?'}</span>
                        <span className="text-muted mx-1">v</span>
                        <span className="text-text font-medium">{bet.away_team || '?'}</span>
                      </td>
                      <td className="text-sm">
                        <span className="text-text">{resolveOutcome(bet)}</span>
                        {bet.point != null && <span className="text-muted2 text-[10px] ml-0.5">{bet.point > 0 ? '+' : ''}{bet.point}</span>}
                        {bet.provider_site_url ? (
                          <a
                            href={bet.provider_site_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-accent text-[10px] ml-1 hover:underline"
                            title="Check result on provider"
                          >{formatProviderName(bet.provider)} ↗</a>
                        ) : (
                          <span className="text-muted2 text-[10px] ml-1">{formatProviderName(bet.provider)}</span>
                        )}
                      </td>
                      <td className="text-right text-text text-sm font-medium">{bet.odds.toFixed(2)}</td>
                      <td className="text-right">
                        {prob != null ? (
                          <span className="text-[11px] text-muted">{(prob * 100).toFixed(0)}%</span>
                        ) : <span className="text-muted">-</span>}
                      </td>
                      <td className="text-right text-text text-sm">{bet.stake.toFixed(0)} kr</td>
                      <td className="text-right">
                        {bet.clv_pct != null ? (
                          <span className={`text-sm font-medium ${bet.clv_pct >= 0 ? 'text-success' : 'text-error'}`}>
                            {bet.clv_pct >= 0 ? '+' : ''}{bet.clv_pct.toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right">
                        <span className="inline-flex gap-1">
                          <button
                            className="text-[10px] px-1.5 py-0.5 bg-success/15 text-success hover:bg-success/30 transition-colors"
                            onClick={() => handleManualSettle(bet, 'won')}
                          >W</button>
                          <button
                            className="text-[10px] px-1.5 py-0.5 bg-error/15 text-error hover:bg-error/30 transition-colors"
                            onClick={() => handleManualSettle(bet, 'lost')}
                          >L</button>
                          <button
                            className="text-[10px] px-1.5 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                            onClick={() => handleManualSettle(bet, 'void')}
                          >V</button>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Upcoming Bets (sorted by TTK, closest first) ─────── */}
      {upcomingBets.length > 0 && (
        <div>
          <SectionHeader
            icon="bets"
            color={TAB_COLORS.bets}
            title="Upcoming"
            count={upcomingBets.length}
            action={{ label: 'History', onClick: () => onTabChange('stats') }}
          />
          <div className="mt-2 border border-border bg-panel overflow-hidden">
            <table className="sq">
              <thead>
                <tr>
                  <th>TTK</th>
                  <th>Sport</th>
                  <th>Event</th>
                  <th>Pick</th>
                  <th className="text-right">Odds</th>
                  <th className="text-right">Prob</th>
                  <th className="text-right">Stake</th>
                  <th className="text-right">Edge</th>
                  <th className="text-right">Return</th>
                </tr>
              </thead>
              <tbody>
                {upcomingBets.map(bet => {
                  const ttk = getLiveTTK(bet);
                  const prob = bet.selection_probability ?? (bet.odds > 0 ? 1 / bet.odds : null);
                  const mkt = formatMarketShort(bet.market);
                  return (
                    <tr key={bet.id}>
                      <td className="whitespace-nowrap">
                        <span className={`text-[10px] ${ttk !== null && ttk <= 1 ? 'text-warning' : ttk !== null && ttk <= 6 ? 'text-success' : 'text-muted'}`}>
                          {ttk !== null ? formatTTK(ttk) : '-'}
                        </span>
                      </td>
                      <td className="whitespace-nowrap">
                        <span className="text-[10px] text-muted">{formatSport(bet.sport)}</span>
                        {mkt && <span className="text-[9px] text-muted2 ml-0.5">{mkt}</span>}
                      </td>
                      <td className="text-sm">
                        <span className="text-text font-medium">{bet.home_team || '?'}</span>
                        <span className="text-muted mx-1">v</span>
                        <span className="text-text font-medium">{bet.away_team || '?'}</span>
                      </td>
                      <td className="text-sm">
                        <span className="text-text">{resolveOutcome(bet)}</span>
                        {bet.point != null && <span className="text-muted2 text-[10px] ml-0.5">{bet.point > 0 ? '+' : ''}{bet.point}</span>}
                        <span className="text-muted2 text-[10px] ml-1">{formatProviderName(bet.provider)}</span>
                      </td>
                      <td className="text-right text-text text-sm font-medium">{bet.odds.toFixed(2)}</td>
                      <td className="text-right">
                        {prob != null ? (
                          <span className="text-[11px] text-muted">{(prob * 100).toFixed(0)}%</span>
                        ) : <span className="text-muted">-</span>}
                      </td>
                      <td className="text-right text-text text-sm">{bet.stake.toFixed(0)} kr</td>
                      <td className="text-right">
                        {bet.edge_pct != null ? (
                          <span className={`text-sm font-medium ${bet.edge_pct >= 0 ? 'text-success' : 'text-error'}`}>
                            {bet.edge_pct >= 0 ? '+' : ''}{bet.edge_pct.toFixed(1)}%
                          </span>
                        ) : <span className="text-muted">-</span>}
                      </td>
                      <td className="text-right text-accent text-sm font-medium">{(bet.stake * bet.odds).toFixed(0)} kr</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      </div>
    </div>
  );
}

// Keep backward-compatible export
export { MonitorPage as HomePage };
