import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName, getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { useTableSort } from '@/hooks/useTableSort';
import { SortableHeader } from '../SortableHeader';
import { FilterBar, MultiSelectDropdown } from '../FilterBar';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Provider } from '@/types';

interface DutchLeg {
  outcome: string;
  provider: string;
  odds: number;
  edge_pct: number;
  fair_odds: number;
  stake_pct: number;
  is_sharp: boolean;
  stake?: number;
  potential_return?: number;
}

interface DutchOpp {
  id: number;
  type: string;
  event_id: string;
  market: string;
  point?: number | null;
  profit_pct: number | null;
  edge_pct: number | null;
  sport?: string;
  home_team?: string;
  away_team?: string;
  starts_at?: string;
  guaranteed_profit_pct?: number;
  total_stake?: number;
  legs?: DutchLeg[];
}

interface DutchPageProps {
  providers: Provider[];
}

const MAX_ROWS = 50;

export function DutchPage({ providers }: DutchPageProps) {
  const [opportunities, setOpportunities] = useState<DutchOpp[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());

  // Odds override: key = "oppId|legIdx", value = new odds
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);

  // Place bet state
  const [isPlacing, setIsPlacing] = useState(false);
  const [placingLeg, setPlacingLeg] = useState<string | null>(null); // "oppId|legIdx" or "oppId|all"
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);
  // Track placed legs per opp: oppId -> Set of legIdx
  const [placedLegs, setPlacedLegs] = useState<Record<number, Set<number>>>({});

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const dutchRes = await api.getOpportunities('dutch', true);
      const all = dutchRes.opportunities as unknown as DutchOpp[];
      setOpportunities(all);
    } catch (err) {
      console.error('Failed to fetch dutch opportunities:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    for (const p of providers) {
      if (p.is_enabled) set.add(p.id);
    }
    for (const opp of opportunities) {
      for (const leg of opp.legs || []) {
        if (!leg.is_sharp) set.add(leg.provider);
      }
    }
    return Array.from(set).sort();
  }, [providers, opportunities]);

  const filtered = useMemo(() => {
    let result = opportunities;
    // Remove started/imminent events
    result = result.filter(d => { const ttk = getTTKFromNow(d.starts_at); return ttk === null || ttk > 1 / 60; });
    if (selectedProviders.size > 0) {
      result = result.filter(d =>
        (d.legs || []).some(leg => !leg.is_sharp && selectedProviders.has(leg.provider))
      );
    }
    return result.slice(0, MAX_ROWS);
  }, [opportunities, selectedProviders]);

  type DutchSortCol = 'edge' | 'stake' | 'profit' | 'ttk';
  const dutchSortExtractors = useMemo(() => ({
    edge:   (d: DutchOpp) => d.edge_pct ?? 0,
    stake:  (d: DutchOpp) => d.total_stake ?? 0,
    profit: (d: DutchOpp) => d.guaranteed_profit_pct ?? d.profit_pct ?? 0,
    ttk:    (d: DutchOpp) => getTTKFromNow(d.starts_at) ?? 99999,
  }), []);
  const { sorted: sortedDutch, sort: dutchSort, toggle: toggleDutchSort } =
    useTableSort<DutchOpp, DutchSortCol>(filtered, dutchSortExtractors, { column: 'edge', direction: 'desc' });

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
  };

  const getEffectiveOdds = (oppId: number, legIdx: number, originalOdds: number): number => {
    const key = `${oppId}|${legIdx}`;
    return oddsOverride[key] ?? originalOdds;
  };

  const handlePlaceLeg = async (opp: DutchOpp, leg: DutchLeg, legIdx: number) => {
    const totalStake = opp.total_stake || 0;
    const legStake = leg.stake ?? (totalStake > 0 ? totalStake * leg.stake_pct / 100 : 0);
    if (legStake <= 0) return;

    const odds = getEffectiveOdds(opp.id, legIdx, leg.odds);
    const legKey = `${opp.id}|${legIdx}`;
    setIsPlacing(true);
    setPlacingLeg(legKey);
    setBetError(null);
    setBetSuccess(null);

    try {
      await api.createBet({
        event_id: opp.event_id,
        provider_id: leg.provider,
        market: opp.market,
        outcome: leg.outcome,
        odds,
        stake: legStake,
        is_bonus: false,
        utility_score: leg.edge_pct != null ? leg.edge_pct / 100 : undefined,
        selection_probability: leg.fair_odds > 1 ? 1 / leg.fair_odds : undefined,
      });
      // Track this leg as placed
      setPlacedLegs(prev => {
        const existing = prev[opp.id] || new Set<number>();
        const next = new Set(existing);
        next.add(legIdx);
        return { ...prev, [opp.id]: next };
      });

      const outcomeLabel = resolveOutcome(leg.outcome, opp.home_team, opp.away_team, opp.point);
      setBetSuccess(`Recorded: ${legStake.toFixed(0)} kr on ${outcomeLabel} @ ${odds.toFixed(2)} (${formatProviderName(leg.provider)})`);
      setTimeout(() => setBetSuccess(null), 5000);
      fetchData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to place bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
      setPlacingLeg(null);
    }
  };

  const handlePlaceAll = async (opp: DutchOpp) => {
    const legs = opp.legs || [];
    const totalStake = opp.total_stake || 0;
    if (legs.length === 0 || totalStake <= 0) return;

    // Build legs array for batch API
    const batchLegs = legs.map((leg, legIdx) => {
      const legStake = leg.stake ?? (totalStake > 0 ? totalStake * leg.stake_pct / 100 : 0);
      const odds = getEffectiveOdds(opp.id, legIdx, leg.odds);
      return {
        event_id: opp.event_id,
        provider_id: leg.provider,
        market: opp.market,
        outcome: leg.outcome,
        odds,
        stake: legStake,
        is_bonus: false,
        utility_score: leg.edge_pct != null ? leg.edge_pct / 100 : undefined,
        selection_probability: leg.fair_odds > 1 ? 1 / leg.fair_odds : undefined,
      };
    }).filter(l => l.stake > 0);

    if (batchLegs.length === 0) return;

    setIsPlacing(true);
    setPlacingLeg(`${opp.id}|all`);
    setBetError(null);
    setBetSuccess(null);

    try {
      const res = await api.createBatchBets(batchLegs);

      // Track which legs were placed successfully
      const successIdxs = new Set<number>();
      const errors: string[] = [];
      for (const r of res.results) {
        if (r.success) {
          successIdxs.add(r.leg_index);
        } else {
          errors.push(`${formatProviderName(r.provider_id)}: ${r.error}`);
        }
      }

      setPlacedLegs(prev => ({ ...prev, [opp.id]: successIdxs }));

      if (res.placed_count === res.total_legs) {
        setBetSuccess(`All ${res.placed_count} legs recorded — ${res.total_staked.toFixed(0)} kr total`);
      } else if (res.placed_count > 0) {
        setBetSuccess(`${res.placed_count}/${res.total_legs} legs recorded — ${res.total_staked.toFixed(0)} kr`);
        if (errors.length > 0) {
          setBetError(errors.join(' · '));
        }
      } else {
        setBetError(errors.join(' · ') || 'Failed to place any legs');
      }

      setTimeout(() => { setBetSuccess(null); setBetError(null); }, 8000);
      fetchData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to place bets';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
      setPlacingLeg(null);
    }
  };

  const formatTime = (dateStr: string | undefined) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const resolveOutcome = (outcome: string, home?: string, away?: string, point?: number | null): string => {
    const p = point != null ? ` ${point}` : '';
    if (outcome === 'home' && home) return home;
    if (outcome === 'away' && away) return away;
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return `Over${p}`;
    if (outcome === 'under') return `Under${p}`;
    return outcome;
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="dutch" color={TAB_COLORS.dutch} size={16} />
          Dutch
          <span className="text-muted text-sm font-normal ml-1">
            ({sortedDutch.length}{selectedProviders.size > 0 ? ` of ${opportunities.length}` : ''})
          </span>
        </h2>
      </div>

      {/* Feedback toasts */}
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

      {availableProviders.length > 0 && (
        <FilterBar>
          <MultiSelectDropdown
            label="Provider"
            options={availableProviders}
            selected={selectedProviders}
            onToggle={toggleProvider}
            onClear={() => setSelectedProviders(new Set())}
            format={formatProviderName}
            accentColor="success"
          />
        </FilterBar>
      )}

      {isLoading && opportunities.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Loading...
        </div>
      ) : sortedDutch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          {opportunities.length === 0
            ? 'No dutch opportunities found. Run extraction first.'
            : 'No matches for current filters.'}
        </div>
      ) : (
        <div className="border-l-2 border-success">
          <table className="sq">
            <thead>
              <tr>
                <th>Event</th>
                <th className="text-right">Providers</th>
                <SortableHeader column="ttk" label="TTK" sort={dutchSort} onToggle={toggleDutchSort} />
                <SortableHeader column="edge" label="Edge" sort={dutchSort} onToggle={toggleDutchSort} />
                <SortableHeader column="stake" label="Stake" sort={dutchSort} onToggle={toggleDutchSort} />
                <SortableHeader column="profit" label="Profit" sort={dutchSort} onToggle={toggleDutchSort} />
              </tr>
            </thead>
            <tbody>
              {sortedDutch.map((opp, idx) => {
                const isSelected = selectedOpp === idx;
                const gp = opp.guaranteed_profit_pct ?? opp.profit_pct ?? 0;
                const legs = opp.legs || [];
                const totalStake = opp.total_stake || 0;
                const uniqueProviders = [...new Set(legs.filter(l => !l.is_sharp).map(l => l.provider))];

                return (
                  <>
                    <tr
                      key={opp.id}
                      className={`cursor-pointer ${isSelected ? 'expanded' : ''}`}
                      onClick={() => setSelectedOpp(isSelected ? null : idx)}
                    >
                      <td>
                        <div className="text-text text-sm">{opp.home_team} vs {opp.away_team}</div>
                        <div className="text-muted2 text-[11px]">
                          {opp.sport}
                          {opp.market && opp.market !== '1x2' && opp.market !== 'moneyline' ? ` · ${opp.market}` : ''}
                          {opp.point != null ? ` · ${opp.point}` : ''}
                          {' · '}{formatTime(opp.starts_at)}
                        </div>
                      </td>
                      <td className="text-right text-muted text-sm">
                        {uniqueProviders.length <= 3
                          ? uniqueProviders.map(formatProviderName).join(', ')
                          : <>{formatProviderName(uniqueProviders[0])} <span className="text-muted2">+{uniqueProviders.length - 1}</span></>
                        }
                      </td>
                      <td className="text-right">
                        {(() => { const ttk = getTTKFromNow(opp.starts_at); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                      </td>
                      <td className={`text-right font-semibold text-sm ${(opp.edge_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                        {opp.edge_pct != null ? `${opp.edge_pct >= 0 ? '+' : ''}${opp.edge_pct.toFixed(1)}%` : '-'}
                      </td>
                      <td className="text-right text-text text-sm font-medium">
                        {totalStake > 0 ? `${totalStake.toFixed(0)} kr` : '-'}
                      </td>
                      <td className={`text-right font-semibold text-sm ${gp >= 0 ? 'text-success' : 'text-error'}`}>
                        {gp >= 0 ? `+${gp.toFixed(2)}%` : `${gp.toFixed(2)}%`}
                      </td>
                    </tr>

                    {isSelected && (
                      <tr key={`${opp.id}-expanded`}>
                        <td colSpan={6} className="!p-0" onClick={e => e.stopPropagation()}>
                          <table className="sq">
                            <thead>
                              <tr>
                                <th>Outcome</th>
                                <th className="text-right">Provider</th>
                                <th className="text-right">Odds</th>
                                <th className="text-right">Fair</th>
                                <th className="text-right">Edge</th>
                                <th className="text-right">Stake</th>
                                <th className="text-right">Return</th>
                                <th className="text-right"></th>
                              </tr>
                            </thead>
                            <tbody>
                              {legs.map((leg, legIdx) => {
                                const oddsKey = `${opp.id}|${legIdx}`;
                                const effectiveOdds = getEffectiveOdds(opp.id, legIdx, leg.odds);
                                const oddsChanged = oddsKey in oddsOverride;
                                const legStake = leg.stake ?? (totalStake > 0 ? totalStake * leg.stake_pct / 100 : 0);
                                const legReturn = legStake * effectiveOdds;
                                const isEditingThis = editingOdds === oddsKey;
                                const isPlacingThis = isPlacing && placingLeg === oddsKey;

                                return (
                                  <tr key={legIdx}>
                                    <td>
                                      <span className={`inline-block w-1.5 h-1.5 mr-1.5 align-middle ${leg.edge_pct > 0 ? 'bg-success' : 'bg-muted2'}`} />
                                      {resolveOutcome(leg.outcome, opp.home_team, opp.away_team, opp.point)}
                                      {leg.is_sharp && <span className="text-[9px] ml-1 px-1 py-0.5 bg-muted/10 text-muted2">PIN</span>}
                                    </td>
                                    <td className="text-right">{formatProviderName(leg.provider)}</td>
                                    <td className="text-right font-medium">
                                      <div className="flex items-center justify-end gap-1">
                                        {isEditingThis ? (
                                          <input
                                            type="number"
                                            step="0.01"
                                            autoFocus
                                            defaultValue={effectiveOdds.toFixed(2)}
                                            className="w-16 bg-bg border border-success/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-success"
                                            onBlur={(e) => {
                                              const val = parseFloat(e.target.value);
                                              if (!isNaN(val) && val >= 1.01) {
                                                setOddsOverride(prev => ({ ...prev, [oddsKey]: val }));
                                              }
                                              setEditingOdds(null);
                                            }}
                                            onKeyDown={(e) => {
                                              if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                              else if (e.key === 'Escape') setEditingOdds(null);
                                            }}
                                          />
                                        ) : (
                                          <span
                                            onClick={() => setEditingOdds(oddsKey)}
                                            className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-success/50 transition-colors ${oddsChanged ? 'text-success font-medium border-success/30' : 'text-text border-transparent'}`}
                                            title="Click to adjust odds"
                                          >
                                            {effectiveOdds.toFixed(2)}
                                          </span>
                                        )}
                                        {oddsChanged && (
                                          <button
                                            onClick={() => setOddsOverride(prev => { const next = { ...prev }; delete next[oddsKey]; return next; })}
                                            className="text-muted2 hover:text-text text-[10px]"
                                            title="Reset to original"
                                          >
                                            x
                                          </button>
                                        )}
                                      </div>
                                    </td>
                                    <td className="text-right text-muted">{leg.fair_odds.toFixed(2)}</td>
                                    <td className={`text-right font-medium ${leg.edge_pct > 0 ? 'text-success' : 'text-muted'}`}>
                                      {leg.edge_pct > 0 ? '+' : ''}{leg.edge_pct.toFixed(1)}%
                                    </td>
                                    <td className="text-right">
                                      {legStake > 0 ? `${legStake.toFixed(0)} kr` : '-'}
                                      {legStake > 0 && <span className="text-muted2 text-[10px] ml-1">({leg.stake_pct.toFixed(0)}%)</span>}
                                    </td>
                                    <td className="text-right">{legReturn > 0 ? `${legReturn.toFixed(0)} kr` : '-'}</td>
                                    <td className="text-right">
                                      {placedLegs[opp.id]?.has(legIdx) ? (
                                        <span className="text-success text-[10px] font-medium">✓ placed</span>
                                      ) : legStake > 0 ? (
                                        <button
                                          onClick={() => handlePlaceLeg(opp, leg, legIdx)}
                                          disabled={isPlacing}
                                          className="px-2 py-1 bg-panel2 text-muted text-[10px] font-medium hover:text-text hover:bg-panel2/80 disabled:opacity-50 transition-all whitespace-nowrap"
                                        >
                                          {isPlacingThis ? '...' : 'Place Bet'}
                                        </button>
                                      ) : null}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                          {totalStake > 0 && (
                            <div className="px-3 py-2 border-t border-border bg-panel flex items-center justify-between text-xs text-muted">
                              <div className="flex items-center gap-6">
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">Total Stake: </span>
                                  <span className="text-text font-medium">{totalStake.toFixed(0)} kr</span>
                                </div>
                                {gp !== 0 && (
                                  <div>
                                    <span className="text-muted2 uppercase tracking-wider">{gp > 0 ? 'Guaranteed' : 'Loss'}: </span>
                                    <span className={gp > 0 ? 'text-success font-medium' : 'text-error font-medium'}>
                                      {gp > 0 ? '+' : ''}{(totalStake * gp / 100).toFixed(0)} kr
                                    </span>
                                  </div>
                                )}
                              </div>
                              {/* Place All button */}
                              {(() => {
                                const allPlaced = placedLegs[opp.id]?.size === legs.length;
                                const isPlacingAll = isPlacing && placingLeg === `${opp.id}|all`;
                                return allPlaced ? (
                                  <span className="text-success text-[10px] font-medium">✓ all legs placed</span>
                                ) : (
                                  <button
                                    onClick={() => handlePlaceAll(opp)}
                                    disabled={isPlacing}
                                    className="px-3 py-1.5 bg-success text-bg text-[11px] font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                                  >
                                    {isPlacingAll ? '...' : 'Place All'}
                                  </button>
                                );
                              })()}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
