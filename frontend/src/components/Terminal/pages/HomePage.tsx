import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import type { SpecialItem } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { ExtractionProgressBar } from '../ExtractionProgressBar';
import type { TabName } from '../Sidebar';
import type { Bet, Opportunity, BankrollStats, BankrollExposure, LiveEvent, PolymarketValueBet } from '@/types';

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

/** Live TTK: hours from NOW to kickoff (0 if already started) */
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

interface HomePageProps {
  onTabChange: (tab: TabName) => void;
}

export function HomePage({ onTabChange }: HomePageProps) {
  const [bets, setBets] = useState<Bet[]>([]);
  const [stats, setStats] = useState<BankrollStats | null>(null);
  const [exposure, setExposure] = useState<BankrollExposure | null>(null);
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([]);
  const [valueOpps, setValueOpps] = useState<Opportunity[]>([]);
  const [reverseOpps, setReverseOpps] = useState<Opportunity[]>([]);
  const [polyOpps, setPolyOpps] = useState<PolymarketValueBet[]>([]);
  const [specials, setSpecials] = useState<SpecialItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setIsLoading(true);
    try {
      const [betsRes, statsRes, exposureRes, liveRes, valueRes, reverseRes, polyRes, specialsRes] = await Promise.all([
        api.getBets('pending', 50),
        api.getBankrollStats(),
        api.getBankrollExposure(),
        api.getLiveEvents(),
        api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 0),
        api.getOpportunities('reverse_value', true),
        api.getPolymarketValue(0),
        api.getSpecials({ sort: 'edge_pct', order: 'desc' }),
      ]);
      setBets(betsRes.bets);
      setStats(statsRes);
      setExposure(exposureRes);
      setLiveEvents(liveRes.events);
      setValueOpps(valueRes.opportunities);
      setReverseOpps(reverseRes.opportunities);
      setPolyOpps(polyRes.value_bets);
      setSpecials(specialsRes.specials);
    } catch (err) {
      console.error('HomePage fetch failed:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 30000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // Auto-settle on load (background, fire-and-forget)
  useEffect(() => {
    api.closeStartedBets().catch(() => {});
    api.autoSettleBets().then(() => fetchAll()).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Derived data ────────────────────────────────────────────────

  // Match pending bets to live events by event_id
  const liveEventMap = useMemo(() => {
    const map = new Map<string, LiveEvent>();
    for (const ev of liveEvents) map.set(ev.id, ev);
    return map;
  }, [liveEvents]);

  // Split pending bets: Active = ONLY Pinnacle-confirmed live/finished
  const activeBets = useMemo(() => {
    return bets
      .filter(b => b.event_id && liveEventMap.has(b.event_id))
      .sort((a, b) => {
        const evA = a.event_id ? liveEventMap.get(a.event_id) : null;
        const evB = b.event_id ? liveEventMap.get(b.event_id) : null;
        return (evB?.match_minute ?? 0) - (evA?.match_minute ?? 0);
      });
  }, [bets, liveEventMap]);

  const upcomingBets = useMemo(() => {
    return bets
      .filter(b => !(b.event_id && liveEventMap.has(b.event_id)))
      .sort((a, b) => {
        const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
        const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
        return ta - tb;
      });
  }, [bets, liveEventMap]);

  // Top value (sorted by edge, take 3)
  const topValue = useMemo(() =>
    [...valueOpps].sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0)).slice(0, 3),
    [valueOpps]
  );

  // Top reverse (sorted by edge, take 3)
  const topReverse = useMemo(() =>
    [...reverseOpps].sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0)).slice(0, 3),
    [reverseOpps]
  );

  // Top polymarket (sorted by edge, take 3)
  const topPoly = useMemo(() =>
    [...polyOpps].sort((a, b) => b.edge_pct - a.edge_pct).slice(0, 3),
    [polyOpps]
  );

  // Top specials (sorted by boost_pct desc like Specials page, take 3)
  const topSpecials = useMemo(() =>
    [...specials]
      .filter(s => s.boost_pct != null && s.boost_pct > 0)
      .sort((a, b) => (b.boost_pct ?? 0) - (a.boost_pct ?? 0))
      .slice(0, 3),
    [specials]
  );

  const netWorth = exposure?.total_balance ?? 0;
  const pendingStake = exposure?.total_pending ?? 0;

  if (isLoading && !stats) {
    return (
      <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
        Loading...
      </div>
    );
  }

  return (
    <div className="space-y-4">
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
                {stats.bonus_profit > 0 && (
                  <div className="text-[10px] text-tabBonus">+{stats.bonus_profit.toLocaleString('en', { maximumFractionDigits: 0 })} bonus</div>
                )}
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

      {/* ── Active Bets (live/started events with scores) ──────── */}
      {activeBets.length > 0 && (
        <div>
          <SectionHeader
            icon="bets"
            color={TAB_COLORS.bets}
            title="Active"
            count={activeBets.length}
            action={{ label: 'History', onClick: () => onTabChange('stats') }}
          />
          <div className="mt-2 border border-border bg-panel overflow-hidden">
            <table className="sq">
              <thead>
                <tr>
                  <th>Score</th>
                  <th>Event</th>
                  <th>Pick</th>
                  <th className="text-right">Odds</th>
                  <th className="text-right">Stake</th>
                  <th className="text-right">CLV</th>
                  <th className="text-right">Return</th>
                </tr>
              </thead>
              <tbody>
                {activeBets.map(bet => {
                  const ev = bet.event_id ? liveEventMap.get(bet.event_id) : null;
                  const hasScore = ev && ev.home_score != null && ev.away_score != null;
                  const isFinished = ev?.match_status === 'finished' || bet.match_status === 'finished';
                  return (
                    <tr key={bet.id} className={isFinished ? 'bg-muted/[0.04]' : 'bg-warning/[0.03]'}>
                      <td className="whitespace-nowrap">
                        <span className={`text-[10px] px-1.5 py-0.5 font-medium ${
                          isFinished ? 'bg-muted/15 text-muted' : 'bg-warning/15 text-warning'
                        }`}>
                          {hasScore ? (
                            isFinished
                              ? <>{ev!.home_score}-{ev!.away_score} <span className="text-muted2">FT</span></>
                              : <>{ev!.home_score}-{ev!.away_score}{ev!.match_minute != null && <span className="text-muted2 ml-0.5">{ev!.match_minute}'</span>}</>
                          ) : isFinished
                            ? 'FT'
                            : ev?.match_minute != null
                              ? <>LIVE <span className="text-muted2">{ev.match_minute}'</span></>
                              : 'LIVE'
                          }
                        </span>
                      </td>
                      <td className="text-sm">
                        <span className="text-text font-medium">{bet.home_team || '?'}</span>
                        <span className="text-muted mx-1">v</span>
                        <span className="text-text font-medium">{bet.away_team || '?'}</span>
                      </td>
                      <td className="text-sm">
                        <span className="text-text">{resolveOutcome(bet)}</span>
                        {isFinished && !hasScore && bet.provider_site_url ? (
                          <a
                            href={bet.provider_site_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-accent text-[10px] ml-1 hover:underline"
                            title="Check result"
                          >{formatProviderName(bet.provider)} ↗</a>
                        ) : (
                          <span className="text-muted2 text-[10px] ml-1">{formatProviderName(bet.provider)}</span>
                        )}
                      </td>
                      <td className="text-right text-text text-sm font-medium">{bet.odds.toFixed(2)}</td>
                      <td className="text-right text-text text-sm">{bet.stake.toFixed(0)} kr</td>
                      <td className="text-right">
                        {bet.clv_pct != null ? (
                          <span className={`text-sm font-medium ${bet.clv_pct >= 0 ? 'text-success' : 'text-error'}`}>
                            {bet.clv_pct >= 0 ? '+' : ''}{bet.clv_pct.toFixed(1)}%
                          </span>
                        ) : <span className="text-muted">-</span>}
                      </td>
                      <td className="text-right">
                        {isFinished && !hasScore ? (
                          <span className="inline-flex gap-1">
                            <button
                              className="text-[10px] px-1.5 py-0.5 bg-success/15 text-success hover:bg-success/30 transition-colors"
                              onClick={async () => {
                                await api.settleBet(bet.id, { result: 'won', payout: bet.stake * bet.odds });
                                fetchAll();
                              }}
                            >W</button>
                            <button
                              className="text-[10px] px-1.5 py-0.5 bg-error/15 text-error hover:bg-error/30 transition-colors"
                              onClick={async () => {
                                await api.settleBet(bet.id, { result: 'lost', payout: 0 });
                                fetchAll();
                              }}
                            >L</button>
                            <button
                              className="text-[10px] px-1.5 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                              onClick={async () => {
                                await api.settleBet(bet.id, { result: 'void', payout: bet.stake });
                                fetchAll();
                              }}
                            >V</button>
                          </span>
                        ) : (
                          <span className="text-accent text-sm font-medium">{(bet.stake * bet.odds).toFixed(0)} kr</span>
                        )}
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
            action={activeBets.length === 0 ? { label: 'History', onClick: () => onTabChange('stats') } : undefined}
          />
          <div className="mt-2 border border-border bg-panel overflow-hidden">
            <table className="sq">
              <thead>
                <tr>
                  <th>TTK</th>
                  <th>Event</th>
                  <th>Pick</th>
                  <th className="text-right">Odds</th>
                  <th className="text-right">Stake</th>
                  <th className="text-right">Edge</th>
                  <th className="text-right">Return</th>
                </tr>
              </thead>
              <tbody>
                {upcomingBets.map(bet => {
                  const ttk = getLiveTTK(bet);
                  return (
                    <tr key={bet.id}>
                      <td className="whitespace-nowrap">
                        <span className={`text-[10px] ${ttk !== null && ttk <= 1 ? 'text-warning' : ttk !== null && ttk <= 6 ? 'text-success' : 'text-muted'}`}>
                          {ttk !== null ? formatTTK(ttk) : '-'}
                        </span>
                      </td>
                      <td className="text-sm">
                        <span className="text-text font-medium">{bet.home_team || '?'}</span>
                        <span className="text-muted mx-1">v</span>
                        <span className="text-text font-medium">{bet.away_team || '?'}</span>
                      </td>
                      <td className="text-sm">
                        <span className="text-text">{resolveOutcome(bet)}</span>
                        <span className="text-muted2 text-[10px] ml-1">{formatProviderName(bet.provider)}</span>
                      </td>
                      <td className="text-right text-text text-sm font-medium">{bet.odds.toFixed(2)}</td>
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

      {/* ── Top Opportunities ─────────────────────────────────────── */}
      <div className="border border-border bg-panel overflow-hidden">
        {/* Column headers */}
        <div className="grid grid-cols-4 gap-px bg-border">
          {([
            { icon: 'value', color: TAB_COLORS.value, title: 'Value', count: valueOpps.length, tab: 'value' as TabName },
            { icon: 'reverse', color: TAB_COLORS.reverse, title: 'Reverse', count: reverseOpps.length, tab: 'reverse' as TabName },
            { icon: 'polymarket', color: TAB_COLORS.polymarket, title: 'Poly', count: polyOpps.length, tab: 'polymarket' as TabName },
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
  );
}
