import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { FilterBar, MultiSelectPills } from '../FilterBar';

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

interface DutchOpportunity {
  id: number;
  type: string;
  event_id: string;
  market: string;
  point?: number | null;
  provider1: string;
  provider2: string | null;
  odds1: number;
  odds2: number | null;
  outcome1: string;
  outcome2: string | null;
  profit_pct: number | null;
  edge_pct: number | null;
  detected_at: string;
  sport?: string;
  league?: string;
  home_team?: string;
  away_team?: string;
  starts_at?: string;
  // Dutch-specific fields from service
  guaranteed_profit_pct?: number;
  total_stake?: number;
  legs?: DutchLeg[];
}

export function DutchPage() {
  const [opportunities, setOpportunities] = useState<DutchOpportunity[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);

  // Filters
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [showGuaranteedOnly, setShowGuaranteedOnly] = useState(false);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await api.getOpportunities('dutch', true);
      setOpportunities(response.opportunities as DutchOpportunity[]);
    } catch (err) {
      console.error('Failed to fetch dutch opportunities:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  // Derive available providers from all legs
  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    for (const opp of opportunities) {
      if (opp.legs) {
        for (const leg of opp.legs) {
          set.add(leg.provider);
        }
      }
    }
    return Array.from(set).sort();
  }, [opportunities]);

  // Apply filters
  const filtered = useMemo(() => {
    let result = opportunities;

    if (showGuaranteedOnly) {
      result = result.filter(o => (o.guaranteed_profit_pct ?? o.profit_pct ?? 0) > 0);
    }

    if (selectedProviders.size > 0) {
      result = result.filter(o =>
        o.legs?.some(leg => selectedProviders.has(leg.provider)) ?? false
      );
    }

    return result;
  }, [opportunities, selectedProviders, showGuaranteedOnly]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  };

  const handleSelectOpp = (idx: number) => {
    setSelectedOpp(selectedOpp === idx ? null : idx);
  };

  const formatTime = (dateStr: string | undefined) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  };

  const gpCount = useMemo(() => (
    opportunities.filter(o => (o.guaranteed_profit_pct ?? o.profit_pct ?? 0) > 0).length
  ), [opportunities]);

  const resolveOutcome = (outcome: string, opp: DutchOpportunity, point?: number | null): string => {
    const p = point != null ? ` ${point}` : '';
    if (outcome === 'home' && opp.home_team) return opp.home_team;
    if (outcome === 'away' && opp.away_team) return opp.away_team;
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return `Over${p}`;
    if (outcome === 'under') return `Under${p}`;
    return outcome;
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabDutch" />
          Dutch
          <span className="text-muted text-sm font-normal ml-1">
            ({filtered.length})
          </span>
          {gpCount > 0 && (
            <span className="text-tabDutch text-sm font-normal">
              · {gpCount} guaranteed
            </span>
          )}
        </h2>
      </div>

      {/* Filters */}
      {availableProviders.length > 0 && (
        <FilterBar>
          <MultiSelectPills
            label="Provider"
            options={availableProviders}
            selected={selectedProviders}
            onToggle={toggleProvider}
            onClear={() => setSelectedProviders(new Set())}
            format={formatProviderName}
            accentColor="tabDutch"
          />
          <button
            onClick={() => setShowGuaranteedOnly(!showGuaranteedOnly)}
            className={`px-2.5 py-1 text-[11px] rounded-full transition-all duration-150 ${
              !showGuaranteedOnly ? 'bg-panel2 text-muted hover:text-text hover:bg-panel2/80' : ''
            }`}
            style={showGuaranteedOnly ? { background: '#10b98115', color: '#10b981', fontWeight: 500 } : undefined}
          >
            GP only
          </button>
        </FilterBar>
      )}

      {/* Table */}
      {isLoading && opportunities.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          Loading...
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          {opportunities.length === 0
            ? 'No dutch opportunities found. Run extraction + detection first.'
            : 'No matches for current filters.'}
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          {/* Column headers */}
          <div className="grid grid-cols-[1fr_140px_80px_80px_90px] gap-3 px-4 py-2 border-b border-border text-[11px] text-muted uppercase tracking-wider font-semibold">
            <div>Event</div>
            <div className="text-right">Legs</div>
            <div className="text-right">Edge</div>
            <div className="text-right">Stake</div>
            <div className="text-right">Profit</div>
          </div>

          {/* Rows */}
          <div className="divide-y divide-border/50">
            {filtered.map((opp, idx) => {
              const isSelected = selectedOpp === idx;
              const gp = opp.guaranteed_profit_pct ?? opp.profit_pct ?? 0;
              const isGP = gp > 0;
              const legs = opp.legs || [];
              const totalStake = opp.total_stake || 0;
              const sharpLegCount = legs.filter(l => l.is_sharp).length;
              const uniqueProviders = [...new Set(legs.map(l => l.provider))];

              // Summary providers: show unique providers truncated
              const providersSummary = uniqueProviders
                .map(formatProviderName)
                .join(' · ');

              return (
                <div key={opp.id}>
                  {/* Main row */}
                  <div
                    className={`grid grid-cols-[1fr_140px_80px_80px_90px] gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm ${
                      isSelected
                        ? 'bg-tabDutch/5'
                        : 'hover:bg-panel2'
                    }`}
                    onClick={() => handleSelectOpp(idx)}
                  >
                    {/* Event */}
                    <div className="flex flex-col justify-center min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-text text-sm truncate">
                          {opp.home_team} vs {opp.away_team}
                        </span>
                        {isGP && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-tabDutch/15 text-tabDutch rounded shrink-0 font-medium">
                            GP
                          </span>
                        )}
                        {sharpLegCount > 0 && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-muted/10 text-muted2 rounded shrink-0">
                            pin:{sharpLegCount}
                          </span>
                        )}
                      </div>
                      <span className="text-muted text-[11px] truncate">
                        {opp.sport}
                        {opp.market && opp.market !== '1x2' && opp.market !== 'moneyline'
                          ? ` · ${opp.market}` : ''}
                        {' · '}{formatTime(opp.starts_at)}
                      </span>
                    </div>

                    {/* Legs summary */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm truncate">{providersSummary}</span>
                    </div>

                    {/* Combined edge */}
                    <div className="flex items-center justify-end">
                      <span className="text-tabDutch font-semibold text-sm">
                        {opp.edge_pct != null ? `${opp.edge_pct > 0 ? '+' : ''}${opp.edge_pct.toFixed(1)}%` : '-'}
                      </span>
                    </div>

                    {/* Total stake */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">
                        {totalStake > 0 ? `${totalStake.toFixed(0)} kr` : '-'}
                      </span>
                    </div>

                    {/* GP% */}
                    <div className="flex items-center justify-end">
                      <span className={`font-semibold text-sm ${isGP ? 'text-tabDutch' : 'text-muted'}`}>
                        {gp > 0 ? `+${gp.toFixed(2)}%` : `${gp.toFixed(2)}%`}
                      </span>
                    </div>
                  </div>

                  {/* Expanded: leg details */}
                  {isSelected && (
                    <div
                      className="px-4 py-3 bg-panel2/50 border-t border-border/30"
                      onClick={e => e.stopPropagation()}
                    >
                      {/* Legs table */}
                      <div className="space-y-1.5">
                        {/* Leg header */}
                        <div className="grid grid-cols-[1fr_100px_65px_65px_65px_80px_80px] gap-2 text-[10px] text-muted2 uppercase tracking-wider font-semibold">
                          <div>Outcome</div>
                          <div className="text-right">Provider</div>
                          <div className="text-right">Odds</div>
                          <div className="text-right">Fair</div>
                          <div className="text-right">Edge</div>
                          <div className="text-right">Stake</div>
                          <div className="text-right">Return</div>
                        </div>

                        {/* Leg rows */}
                        {legs.map((leg, legIdx) => {
                          const legStake = leg.stake ?? (totalStake > 0 ? totalStake * leg.stake_pct / 100 : 0);
                          const legReturn = leg.potential_return ?? (legStake * leg.odds);

                          return (
                            <div
                              key={legIdx}
                              className="grid grid-cols-[1fr_100px_65px_65px_65px_80px_80px] gap-2 text-sm py-1"
                            >
                              {/* Outcome */}
                              <div className="flex items-center gap-1.5">
                                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                                  leg.edge_pct > 0 ? 'bg-tabDutch' : 'bg-muted2'
                                }`} />
                                <span className="text-text truncate">
                                  {resolveOutcome(leg.outcome, opp, opp.point)}
                                </span>
                                {leg.is_sharp && (
                                  <span className="text-[8px] px-1 py-0.5 bg-muted/10 text-muted2 rounded shrink-0">
                                    PIN
                                  </span>
                                )}
                              </div>

                              {/* Provider */}
                              <div className="flex items-center justify-end">
                                <span className={`text-sm ${leg.is_sharp ? 'text-muted' : 'text-text'}`}>
                                  {formatProviderName(leg.provider)}
                                </span>
                              </div>

                              {/* Odds */}
                              <div className="flex items-center justify-end">
                                <span className="text-text font-medium">{leg.odds.toFixed(2)}</span>
                              </div>

                              {/* Fair */}
                              <div className="flex items-center justify-end">
                                <span className="text-muted">{leg.fair_odds.toFixed(2)}</span>
                              </div>

                              {/* Edge */}
                              <div className="flex items-center justify-end">
                                <span className={`font-medium ${
                                  leg.edge_pct > 0 ? 'text-tabDutch' : 'text-muted'
                                }`}>
                                  {leg.edge_pct > 0 ? '+' : ''}{leg.edge_pct.toFixed(1)}%
                                </span>
                              </div>

                              {/* Stake */}
                              <div className="flex items-center justify-end">
                                <span className="text-text">
                                  {legStake > 0 ? `${legStake.toFixed(0)} kr` : '-'}
                                </span>
                                {legStake > 0 && (
                                  <span className="text-muted2 text-[10px] ml-1">
                                    ({leg.stake_pct.toFixed(0)}%)
                                  </span>
                                )}
                              </div>

                              {/* Return */}
                              <div className="flex items-center justify-end">
                                <span className="text-text">
                                  {legReturn > 0 ? `${legReturn.toFixed(0)} kr` : '-'}
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </div>

                      {/* Summary row */}
                      {totalStake > 0 && (
                        <div className="mt-3 pt-2 border-t border-border/30 flex items-center justify-between text-sm">
                          <div className="flex items-center gap-4 text-muted">
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted2 block">Market</span>
                              <span className="text-text">{opp.market}</span>
                            </div>
                            {opp.point != null && (
                              <div>
                                <span className="text-[10px] uppercase tracking-wider text-muted2 block">Line</span>
                                <span className="text-text">{opp.point}</span>
                              </div>
                            )}
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted2 block">Total Stake</span>
                              <span className="text-text font-medium">{totalStake.toFixed(0)} kr</span>
                            </div>
                            {isGP && totalStake > 0 && (
                              <div>
                                <span className="text-[10px] uppercase tracking-wider text-muted2 block">Guaranteed Profit</span>
                                <span className="text-tabDutch font-medium">
                                  +{(totalStake * gp / 100).toFixed(0)} kr
                                </span>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
