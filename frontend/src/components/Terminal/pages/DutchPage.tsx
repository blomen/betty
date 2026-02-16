import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { FilterBar, MultiSelectDropdown } from '../FilterBar';

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

const MAX_ROWS = 50;

export function DutchPage() {
  const [opportunities, setOpportunities] = useState<DutchOpp[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());

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
    for (const opp of opportunities) {
      for (const leg of opp.legs || []) {
        if (!leg.is_sharp) set.add(leg.provider);
      }
    }
    return Array.from(set).sort();
  }, [opportunities]);

  const filtered = useMemo(() => {
    let result = opportunities;
    if (selectedProviders.size > 0) {
      result = result.filter(d =>
        (d.legs || []).some(leg => !leg.is_sharp && selectedProviders.has(leg.provider))
      );
    }
    // Sort by edge descending, take top 50
    result = [...result].sort((a, b) => (b.edge_pct ?? -999) - (a.edge_pct ?? -999));
    return result.slice(0, MAX_ROWS);
  }, [opportunities, selectedProviders]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
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
          <span className="w-2 h-2 bg-success" />
          Dutch
          <span className="text-muted text-sm font-normal ml-1">
            ({filtered.length}{selectedProviders.size > 0 ? ` of ${opportunities.length}` : ''})
          </span>
        </h2>
      </div>

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
      ) : filtered.length === 0 ? (
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
                <th className="text-right">Edge</th>
                <th className="text-right">Stake</th>
                <th className="text-right">Profit</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((opp, idx) => {
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
                        <td colSpan={5} className="!p-0" onClick={e => e.stopPropagation()}>
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
                              </tr>
                            </thead>
                            <tbody>
                              {legs.map((leg, legIdx) => {
                                const legStake = leg.stake ?? (totalStake > 0 ? totalStake * leg.stake_pct / 100 : 0);
                                const legReturn = leg.potential_return ?? (legStake * leg.odds);
                                return (
                                  <tr key={legIdx}>
                                    <td>
                                      <span className={`inline-block w-1.5 h-1.5 mr-1.5 align-middle ${leg.edge_pct > 0 ? 'bg-success' : 'bg-muted2'}`} />
                                      {resolveOutcome(leg.outcome, opp.home_team, opp.away_team, opp.point)}
                                      {leg.is_sharp && <span className="text-[9px] ml-1 px-1 py-0.5 bg-muted/10 text-muted2">PIN</span>}
                                    </td>
                                    <td className="text-right">{formatProviderName(leg.provider)}</td>
                                    <td className="text-right font-medium">{leg.odds.toFixed(2)}</td>
                                    <td className="text-right text-muted">{leg.fair_odds.toFixed(2)}</td>
                                    <td className={`text-right font-medium ${leg.edge_pct > 0 ? 'text-success' : 'text-muted'}`}>
                                      {leg.edge_pct > 0 ? '+' : ''}{leg.edge_pct.toFixed(1)}%
                                    </td>
                                    <td className="text-right">
                                      {legStake > 0 ? `${legStake.toFixed(0)} kr` : '-'}
                                      {legStake > 0 && <span className="text-muted2 text-[10px] ml-1">({leg.stake_pct.toFixed(0)}%)</span>}
                                    </td>
                                    <td className="text-right">{legReturn > 0 ? `${legReturn.toFixed(0)} kr` : '-'}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                          {totalStake > 0 && (
                            <div className="px-3 py-2 border-t border-border bg-panel flex items-center gap-6 text-xs text-muted">
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
