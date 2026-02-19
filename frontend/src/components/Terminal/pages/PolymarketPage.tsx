import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { useRefreshOnExtraction, useTiersProgress } from '@/hooks/useExtractionStatus';
import { useTableSort } from '@/hooks/useTableSort';
import { SortableHeader } from '../SortableHeader';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { PolymarketValueBet, PolymarketStats } from '@/types';

function getTimeAgo(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function PolymarketPage() {
  const [valueBets, setValueBets] = useState<PolymarketValueBet[]>([]);
  const [totalScanned, setTotalScanned] = useState(0);
  const [totalBankroll, setTotalBankroll] = useState(0);
  const [polyStats, setPolyStats] = useState<PolymarketStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const tiersProgress = useTiersProgress();

  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);

  // Odds editing state (uniform with ValuePage)
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [valueRes, stats] = await Promise.all([
        api.getPolymarketValue(3, undefined, 50),
        api.getPolymarketStats(),
      ]);
      setValueBets(valueRes.value_bets);
      setTotalScanned(valueRes.total_scanned);
      setTotalBankroll(valueRes.total_bankroll ?? 0);
      setPolyStats(stats);
    } catch (err) {
      console.error('Failed to fetch Polymarket data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  const formatTime = (dateStr: string | null) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const handleSelectOpp = (idx: number) => { setSelectedOpp(selectedOpp === idx ? null : idx); };

  const getOddsKey = (vb: PolymarketValueBet) =>
    `${vb.event_id}|${vb.outcome}|${vb.market}|${vb.point ?? ''}`;

  const getEffectiveOdds = (vb: PolymarketValueBet) =>
    oddsOverride[getOddsKey(vb)] ?? vb.polymarket_odds;

  const handlePlaceBet = async (vb: PolymarketValueBet) => {
    const stake = vb.final_stake;
    if (!stake || stake <= 0) return;
    const odds = getEffectiveOdds(vb);
    setIsPlacing(true);
    setBetError(null);
    setBetSuccess(null);
    try {
      await api.createBet({ event_id: vb.event_id, provider_id: 'polymarket', market: vb.market, outcome: vb.outcome, odds, stake, is_bonus: false });
      const outcomeLabel = resolveOutcome(vb);
      setBetSuccess(`Recorded: ${stake.toFixed(0)} kr on ${outcomeLabel} @ ${odds.toFixed(2)} (Polymarket)`);
      setTimeout(() => setBetSuccess(null), 5000);
      setSelectedOpp(null);
      fetchData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to place bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
    }
  };

  const resolveOutcome = (vb: PolymarketValueBet): string => {
    const point = vb.point != null ? ` ${vb.point}` : '';
    if (vb.outcome === 'home' && vb.home_team) return vb.home_team;
    if (vb.outcome === 'away' && vb.away_team) return vb.away_team;
    if (vb.outcome === 'draw') return 'Draw';
    if (vb.outcome === 'over') return `Over${point}`;
    if (vb.outcome === 'under') return `Under${point}`;
    return vb.outcome;
  };

  // Remove started/imminent events
  const activeValueBets = useMemo(() =>
    valueBets.filter(vb => { const ttk = getTTKFromNow(vb.start_time); return ttk === null || ttk > 1 / 60; }),
  [valueBets]);

  type PolySortCol = 'odds' | 'fair' | 'stake' | 'edge' | 'ttk';
  const polySortExtractors = useMemo(() => ({
    odds:  (vb: PolymarketValueBet) => vb.polymarket_odds,
    fair:  (vb: PolymarketValueBet) => vb.fair_odds,
    stake: (vb: PolymarketValueBet) => vb.final_stake ?? 0,
    edge:  (vb: PolymarketValueBet) => vb.edge_pct,
    ttk:   (vb: PolymarketValueBet) => getTTKFromNow(vb.start_time) ?? 99999,
  }), []);
  const { sorted: sortedBets, sort: polySort, toggle: togglePolySort } =
    useTableSort<PolymarketValueBet, PolySortCol>(activeValueBets, polySortExtractors, { column: 'edge', direction: 'desc' });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="polymarket" color={TAB_COLORS.polymarket} size={16} />
          Polymarket
          <span className="text-muted text-sm font-normal ml-1">({sortedBets.length})</span>
        </h2>
        <span className="text-muted text-xs">
          {polyStats ? `${totalBankroll.toLocaleString()} kr · ${polyStats.matched_events} pin matched${tiersProgress?.tiers?.sharp?.last_run ? ` · ${getTimeAgo(tiersProgress.tiers.sharp.last_run)}` : ''}` : ''}
        </span>
      </div>

      {/* Feedback toasts (uniform with ValuePage) */}
      {betSuccess && (
        <div className="px-3 py-2 bg-success/10 border border-success/30 text-success text-xs flex items-center justify-between">
          <span>{betSuccess}</span>
          <button onClick={() => setBetSuccess(null)} className="text-success/60 hover:text-success ml-2">x</button>
        </div>
      )}
      {betError && (
        <div className="px-3 py-2 bg-error/10 border border-error/30 text-error text-xs flex items-center justify-between">
          <span>{betError}</span>
          <button onClick={() => setBetError(null)} className="text-error/60 hover:text-error ml-2">x</button>
        </div>
      )}

      {isLoading && valueBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
      ) : sortedBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No Polymarket value bets found. Run extraction first.</div>
      ) : (
        <div className="border-l-2 border-tabPolymarket">
        <table className="sq">
          <thead>
            <tr>
              <th>Event</th>
              <th className="text-right">Outcome</th>
              <SortableHeader column="odds" label="Odds" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="fair" label="Fair" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="ttk" label="TTK" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="stake" label="Stake" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="edge" label="Edge" sort={polySort} onToggle={togglePolySort} />
            </tr>
          </thead>
          <tbody>
            {sortedBets.map((vb, idx) => {
              const isSelected = selectedOpp === idx;
              const hasStake = vb.final_stake != null && vb.final_stake > 0;
              const isSkipped = !!vb.skip_reason;

              return (
                <>
                  <tr
                    key={`${vb.event_id}-${vb.outcome}`}
                    className={`cursor-pointer ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''}`}
                    onClick={() => !isSkipped && handleSelectOpp(idx)}
                  >
                    <td>
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-text text-sm truncate">{vb.home_team} vs {vb.away_team}</span>
                        {isSkipped && <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{vb.skip_reason}</span>}
                      </div>
                      <div className="text-muted2 text-[11px]">
                        {vb.sport}{vb.market && vb.market !== '1x2' && vb.market !== 'moneyline' ? ` · ${vb.market}` : ''}{vb.league ? ` · ${vb.league}` : ''}{' · '}{formatTime(vb.start_time)}
                      </div>
                    </td>
                    <td className="text-right text-text text-sm">{resolveOutcome(vb)}</td>
                    <td className="text-right text-text text-sm font-medium">{vb.polymarket_odds.toFixed(2)}</td>
                    <td className="text-right text-muted text-sm">{vb.fair_odds.toFixed(2)}</td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(vb.start_time); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-sm font-medium text-text">{hasStake ? `${vb.final_stake!.toFixed(0)} kr` : '-'}</td>
                    <td className="text-right text-tabPolymarket font-semibold text-sm">+{vb.edge_pct.toFixed(1)}%</td>
                  </tr>
                  {isSelected && !isSkipped && (() => {
                    const oddsKey = getOddsKey(vb);
                    const effectiveOdds = getEffectiveOdds(vb);
                    const oddsChanged = oddsKey in oddsOverride;
                    const potentialReturn = hasStake ? vb.final_stake! * effectiveOdds : 0;
                    const potentialProfit = potentialReturn - (vb.final_stake || 0);

                    return (
                    <tr key={`${vb.event_id}-${vb.outcome}-exp`}>
                      <td colSpan={7} className="!p-0" onClick={e => e.stopPropagation()}>
                        {/* Top row: Kelly, Odds (editable), Return, Line — uniform with ValuePage */}
                        <div className="px-3 py-2 bg-panel border-b border-border flex items-center gap-6 text-xs text-muted">
                          {vb.kelly_fraction != null && (
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Kelly: </span>
                              <span className="text-text">{(vb.kelly_fraction * 100).toFixed(1)}%</span>
                            </div>
                          )}
                          <div className="flex items-center gap-1">
                            <span className="text-muted2 uppercase tracking-wider">Odds: </span>
                            {editingOdds === oddsKey ? (
                              <input
                                type="number"
                                step="0.01"
                                autoFocus
                                defaultValue={effectiveOdds.toFixed(2)}
                                className="w-16 bg-bg border border-tabPolymarket/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabPolymarket"
                                onBlur={(e) => {
                                  const val = parseFloat(e.target.value);
                                  if (!isNaN(val) && val >= 1.01) {
                                    setOddsOverride(prev => ({ ...prev, [oddsKey]: val }));
                                  }
                                  setEditingOdds(null);
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') {
                                    (e.target as HTMLInputElement).blur();
                                  } else if (e.key === 'Escape') {
                                    setEditingOdds(null);
                                  }
                                }}
                              />
                            ) : (
                              <span
                                onClick={() => setEditingOdds(oddsKey)}
                                className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabPolymarket/50 transition-colors ${oddsChanged ? 'text-tabPolymarket font-medium border-tabPolymarket/30' : 'text-text border-transparent'}`}
                                title="Click to adjust odds"
                              >
                                {effectiveOdds.toFixed(2)}
                              </span>
                            )}
                            {oddsChanged && (
                              <button
                                onClick={() => setOddsOverride(prev => {
                                  const next = { ...prev };
                                  delete next[oddsKey];
                                  return next;
                                })}
                                className="text-muted2 hover:text-text text-[10px] ml-0.5"
                                title="Reset to original"
                              >
                                x
                              </button>
                            )}
                          </div>
                          {hasStake && (
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Return: </span>
                              <span className="text-text">{potentialReturn.toFixed(0)} kr</span>
                              <span className="text-tabPolymarket text-xs ml-1">(+{potentialProfit.toFixed(0)})</span>
                            </div>
                          )}
                          <div>
                            <span className="text-muted2 uppercase tracking-wider">Market: </span>
                            <span className="text-text">{vb.market}</span>
                          </div>
                          {vb.point != null && (
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Line: </span>
                              <span className="text-text">{vb.point}</span>
                            </div>
                          )}
                        </div>
                        {/* Bottom row: Bet button — uniform with ValuePage */}
                        <div className="px-3 py-2 bg-panel flex items-center gap-2">
                          <button
                            onClick={() => handlePlaceBet(vb)}
                            disabled={!hasStake || isPlacing}
                            className="px-4 py-1.5 bg-tabPolymarket text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                          >
                            {isPlacing ? '...' : 'Place Bet'}
                          </button>
                        </div>
                      </td>
                    </tr>
                    );
                  })()}
                </>
              );
            })}
          </tbody>
        </table>
        </div>
      )}

      {totalScanned > 0 && <div className="text-muted text-xs text-center pt-1">{totalScanned} total value bets scanned across all providers</div>}
    </div>
  );
}
