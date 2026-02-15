import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { FilterBar, MultiSelectDropdown } from '../FilterBar';
import { BonusPopup } from '../BonusPopup';
import type { Opportunity, Provider } from '@/types';

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

/** A grouped value bet: same event+outcome+odds across multiple providers */
interface GroupedOpp {
  key: string;
  /** Representative opportunity (first in group) */
  rep: Opportunity;
  /** All opportunities in the group */
  opps: Opportunity[];
  providers: string[];
}

interface ValuePageProps {
  providers: Provider[];
}

export function ValuePage({ providers }: ValuePageProps) {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [dutchOpps, setDutchOpps] = useState<DutchOpp[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Betting workflow state
  const [selectedGroup, setSelectedGroup] = useState<number | null>(null);
  const [selectedDutch, setSelectedDutch] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);

  // Freebet popup state
  const [freebetPopup, setFreebetPopup] = useState<{
    opp: Opportunity;
    freebetAmount: number;
  } | null>(null);

  // Filters
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [valueRes, dutchRes] = await Promise.all([
        api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 3),
        api.getOpportunities('dutch', true),
      ]);
      setOpportunities(valueRes.opportunities);
      setDutchOpps(dutchRes.opportunities as unknown as DutchOpp[]);
    } catch (err) {
      console.error('Failed to fetch value bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  // Derive available providers from data
  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    for (const opp of opportunities) {
      if (opp.provider1) set.add(opp.provider1);
    }
    return Array.from(set).sort();
  }, [opportunities]);

  // Apply filters then group
  const grouped = useMemo(() => {
    let result = opportunities;

    if (selectedProviders.size > 0) {
      result = result.filter(o => selectedProviders.has(o.provider1));
    }

    // Group by event_id + outcome + market + point + odds
    const map = new Map<string, Opportunity[]>();
    for (const opp of result) {
      const key = `${opp.event_id}|${opp.outcome1}|${opp.market}|${opp.point ?? ''}|${opp.odds1}`;
      const arr = map.get(key);
      if (arr) arr.push(opp);
      else map.set(key, [opp]);
    }

    const groups: GroupedOpp[] = [];
    for (const [key, opps] of map) {
      groups.push({
        key,
        rep: opps[0],
        opps,
        providers: opps.map(o => o.provider1),
      });
    }

    return groups;
  }, [opportunities, selectedProviders]);

  // Total individual opps for the count display
  const filteredCount = useMemo(() =>
    grouped.reduce((acc, g) => acc + g.opps.length, 0),
  [grouped]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  };

  const formatTime = (dateStr: string | undefined) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  };

  const handleSelectGroup = (idx: number) => {
    setSelectedGroup(selectedGroup === idx ? null : idx);
    setSelectedDutch(null);
  };

  const handleSelectDutch = (idx: number) => {
    setSelectedDutch(selectedDutch === idx ? null : idx);
    setSelectedGroup(null);
  };

  // Check if a provider has an available freebet
  const getFreebetInfo = (providerId: string) => {
    const provider = providers.find(p => p.id === providerId);
    if (!provider?.bonus || provider.bonus.type !== 'freebet') return null;
    const status = provider.bonus_status;
    if (status === 'completed' || status === 'in_progress' || status === 'claimed') return null;
    return { amount: provider.bonus.amount };
  };

  const handlePlaceBetClick = (opp: Opportunity) => {
    const stake = opp.final_stake;
    if (!stake || stake <= 0) return;

    const freebetInfo = getFreebetInfo(opp.provider1);
    if (freebetInfo) {
      setFreebetPopup({ opp, freebetAmount: freebetInfo.amount });
    } else {
      executePlaceBet(opp, false);
    }
  };

  const executePlaceBet = async (opp: Opportunity, useFreebet: boolean) => {
    const stake = opp.final_stake;
    if (!stake || stake <= 0) return;

    setIsPlacing(true);
    setFreebetPopup(null);
    try {
      await api.createBet({
        event_id: opp.event_id,
        provider_id: opp.provider1,
        market: opp.market,
        outcome: opp.outcome1,
        odds: opp.odds1,
        stake,
        is_bonus: useFreebet,
        bonus_type: useFreebet ? 'freebet' : undefined,
      });
      setSelectedGroup(null);
      fetchData();
    } catch (err) {
      console.error('Failed to place bet:', err);
    } finally {
      setIsPlacing(false);
    }
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabValue" />
          Soft
          <span className="text-muted text-sm font-normal ml-1">({filteredCount})</span>
          {dutchOpps.length > 0 && (
            <span className="text-success text-sm font-normal">
              · {dutchOpps.length} dutch
            </span>
          )}
        </h2>
      </div>

      {/* Dutch section */}
      {dutchOpps.length > 0 && (
        <div className="bg-panel border border-success/30 rounded-lg overflow-hidden">
          <div className="px-4 py-2 border-b border-success/20 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-success" />
            <span className="text-[11px] text-success uppercase tracking-wider font-semibold">
              Dutch — all legs +EV
            </span>
          </div>
          <div className="divide-y divide-border/50">
            {dutchOpps.map((opp, idx) => {
              const isSelected = selectedDutch === idx;
              const gp = opp.guaranteed_profit_pct ?? opp.profit_pct ?? 0;
              const legs = opp.legs || [];
              const totalStake = opp.total_stake || 0;
              const uniqueProviders = [...new Set(legs.map(l => l.provider))];

              return (
                <div key={opp.id}>
                  <div
                    className={`grid grid-cols-[1fr_140px_80px_80px_90px] gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm ${
                      isSelected ? 'bg-success/5' : 'hover:bg-panel2'
                    }`}
                    onClick={() => handleSelectDutch(idx)}
                  >
                    {/* Event */}
                    <div className="flex flex-col justify-center min-w-0">
                      <span className="text-text text-sm truncate">
                        {opp.home_team} vs {opp.away_team}
                      </span>
                      <span className="text-muted text-[11px] truncate">
                        {opp.sport}
                        {opp.market && opp.market !== '1x2' && opp.market !== 'moneyline'
                          ? ` · ${opp.market}` : ''}
                        {' · '}{formatTime(opp.starts_at)}
                      </span>
                    </div>

                    {/* Providers */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm truncate">
                        {uniqueProviders.map(formatProviderName).join(' · ')}
                      </span>
                    </div>

                    {/* Combined edge */}
                    <div className="flex items-center justify-end">
                      <span className="text-success font-semibold text-sm">
                        {opp.edge_pct != null ? `+${opp.edge_pct.toFixed(1)}%` : '-'}
                      </span>
                    </div>

                    {/* Total stake */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">
                        {totalStake > 0 ? `${totalStake.toFixed(0)} kr` : '-'}
                      </span>
                    </div>

                    {/* Profit */}
                    <div className="flex items-center justify-end">
                      <span className="text-success font-semibold text-sm">
                        {gp > 0 ? `+${gp.toFixed(2)}%` : `${gp.toFixed(2)}%`}
                      </span>
                    </div>
                  </div>

                  {/* Expanded legs */}
                  {isSelected && (
                    <div
                      className="px-4 py-3 bg-panel2/50 border-t border-border/30"
                      onClick={e => e.stopPropagation()}
                    >
                      <div className="space-y-1.5">
                        <div className="grid grid-cols-[1fr_100px_65px_65px_65px_80px_80px] gap-2 text-[10px] text-muted2 uppercase tracking-wider font-semibold">
                          <div>Outcome</div>
                          <div className="text-right">Provider</div>
                          <div className="text-right">Odds</div>
                          <div className="text-right">Fair</div>
                          <div className="text-right">Edge</div>
                          <div className="text-right">Stake</div>
                          <div className="text-right">Return</div>
                        </div>

                        {legs.map((leg, legIdx) => {
                          const legStake = leg.stake ?? (totalStake > 0 ? totalStake * leg.stake_pct / 100 : 0);
                          const legReturn = leg.potential_return ?? (legStake * leg.odds);

                          return (
                            <div
                              key={legIdx}
                              className="grid grid-cols-[1fr_100px_65px_65px_65px_80px_80px] gap-2 text-sm py-1"
                            >
                              <div className="flex items-center gap-1.5">
                                <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-success" />
                                <span className="text-text truncate">
                                  {resolveOutcome(leg.outcome, opp.home_team, opp.away_team, opp.point)}
                                </span>
                              </div>
                              <div className="flex items-center justify-end">
                                <span className="text-text text-sm">{formatProviderName(leg.provider)}</span>
                              </div>
                              <div className="flex items-center justify-end">
                                <span className="text-text font-medium">{leg.odds.toFixed(2)}</span>
                              </div>
                              <div className="flex items-center justify-end">
                                <span className="text-muted">{leg.fair_odds.toFixed(2)}</span>
                              </div>
                              <div className="flex items-center justify-end">
                                <span className="text-success font-medium">+{leg.edge_pct.toFixed(1)}%</span>
                              </div>
                              <div className="flex items-center justify-end">
                                <span className="text-text">
                                  {legStake > 0 ? `${legStake.toFixed(0)} kr` : '-'}
                                </span>
                                {legStake > 0 && (
                                  <span className="text-muted2 text-[10px] ml-1">({leg.stake_pct.toFixed(0)}%)</span>
                                )}
                              </div>
                              <div className="flex items-center justify-end">
                                <span className="text-text">
                                  {legReturn > 0 ? `${legReturn.toFixed(0)} kr` : '-'}
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </div>

                      {totalStake > 0 && (
                        <div className="mt-3 pt-2 border-t border-border/30 flex items-center gap-4 text-sm text-muted">
                          <div>
                            <span className="text-[10px] uppercase tracking-wider text-muted2 block">Total Stake</span>
                            <span className="text-text font-medium">{totalStake.toFixed(0)} kr</span>
                          </div>
                          {gp > 0 && (
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted2 block">Guaranteed Profit</span>
                              <span className="text-success font-medium">+{(totalStake * gp / 100).toFixed(0)} kr</span>
                            </div>
                          )}
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

      {/* Filters */}
      {availableProviders.length > 0 && (
        <FilterBar>
          <MultiSelectDropdown
            label="Provider"
            options={availableProviders}
            selected={selectedProviders}
            onToggle={toggleProvider}
            onClear={() => setSelectedProviders(new Set())}
            format={formatProviderName}
            accentColor="tabValue"
          />
        </FilterBar>
      )}

      {/* Value bets table */}
      {isLoading && opportunities.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          Loading...
        </div>
      ) : grouped.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          {opportunities.length === 0
            ? 'No value bets found. Run extraction first.'
            : 'No matches for current filters.'}
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          {/* Column headers */}
          <div className="grid grid-cols-[1fr_140px_110px_65px_65px_55px_70px_65px] gap-3 px-4 py-2 border-b border-border text-[11px] text-muted uppercase tracking-wider font-semibold">
            <div>Event</div>
            <div className="text-right">Providers</div>
            <div className="text-right">Outcome</div>
            <div className="text-right">Odds</div>
            <div className="text-right">Fair</div>
            <div className="text-right">Prob</div>
            <div className="text-right">Stake</div>
            <div className="text-right">Edge</div>
          </div>

          {/* Rows */}
          <div className="divide-y divide-border/50">
            {grouped.map((group, idx) => {
              const { rep, opps, providers: groupProviders } = group;
              const isSelected = selectedGroup === idx;
              const hasStake = rep.final_stake != null && rep.final_stake > 0;
              const isSkipped = opps.every(o => !!o.skip_reason);
              const providerCount = groupProviders.length;

              return (
                <div key={group.key}>
                  {/* Main row */}
                  <div
                    className={`grid grid-cols-[1fr_140px_110px_65px_65px_55px_70px_65px] gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm ${
                      isSkipped
                        ? 'opacity-50'
                        : isSelected
                          ? 'bg-tabValue/5'
                          : 'hover:bg-panel2'
                    }`}
                    onClick={() => !isSkipped && handleSelectGroup(idx)}
                  >
                    {/* Event */}
                    <div className="flex flex-col justify-center min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-text text-sm truncate">
                          {rep.home_team} vs {rep.away_team}
                        </span>
                        {isSkipped && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-muted/15 text-muted rounded shrink-0">
                            {rep.skip_reason}
                          </span>
                        )}
                      </div>
                      <span className="text-muted text-[11px] truncate">
                        {rep.sport}{rep.market && rep.market !== '1x2' && rep.market !== 'moneyline' ? ` · ${rep.market}` : ''} · {formatTime(rep.starts_at)}
                      </span>
                    </div>

                    {/* Providers */}
                    <div className="flex items-center justify-end min-w-0">
                      {providerCount <= 3 ? (
                        <span className="text-text text-sm truncate">
                          {groupProviders.map(formatProviderName).join(', ')}
                        </span>
                      ) : (
                        <span className="text-text text-sm truncate">
                          {formatProviderName(groupProviders[0])}
                          <span className="text-muted ml-1">+{providerCount - 1}</span>
                        </span>
                      )}
                    </div>

                    {/* Outcome */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm truncate">{resolveOutcomeName(rep)}</span>
                    </div>

                    {/* Odds */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">{rep.odds1.toFixed(2)}</span>
                    </div>

                    {/* Fair odds */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm">{rep.fair_odds?.toFixed(2) || '-'}</span>
                    </div>

                    {/* Prob (Pinnacle fair probability) */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm">
                        {rep.fair_odds && rep.fair_odds > 1
                          ? `${(100 / rep.fair_odds).toFixed(0)}%`
                          : '-'}
                      </span>
                    </div>

                    {/* Stake */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">
                        {hasStake ? `${rep.final_stake!.toFixed(0)} kr` : '-'}
                      </span>
                    </div>

                    {/* Edge */}
                    <div className="flex items-center justify-end">
                      <span className="text-tabValue font-semibold text-sm">
                        +{rep.edge_pct?.toFixed(1)}%
                      </span>
                    </div>
                  </div>

                  {/* Expanded view — show per-provider rows with place buttons */}
                  {isSelected && !isSkipped && (
                    <div
                      className="bg-panel2/50 border-t border-border/30"
                      onClick={e => e.stopPropagation()}
                    >
                      {/* Stats row */}
                      <div className="px-4 pt-3 pb-2 flex items-center gap-6 text-sm text-muted">
                        <div>
                          <span className="text-[10px] uppercase tracking-wider text-muted block">Kelly</span>
                          <span className="text-text">
                            {rep.kelly_fraction != null ? `${(rep.kelly_fraction * 100).toFixed(1)}%` : '-'}
                          </span>
                        </div>
                        {hasStake && (
                          <div>
                            <span className="text-[10px] uppercase tracking-wider text-muted block">Return</span>
                            <span className="text-text">{(rep.final_stake! * rep.odds1).toFixed(0)} kr</span>
                            <span className="text-tabValue text-xs ml-1">(+{(rep.final_stake! * rep.odds1 - rep.final_stake!).toFixed(0)})</span>
                          </div>
                        )}
                        <div>
                          <span className="text-[10px] uppercase tracking-wider text-muted block">Market</span>
                          <span className="text-text">{rep.market}</span>
                        </div>
                        {rep.point != null && (
                          <div>
                            <span className="text-[10px] uppercase tracking-wider text-muted block">Line</span>
                            <span className="text-text">{rep.point}</span>
                          </div>
                        )}
                      </div>

                      {/* Per-provider place buttons */}
                      <div className="px-4 pb-3 flex flex-wrap gap-2">
                        {opps.map((opp) => {
                          const oppHasStake = opp.final_stake != null && opp.final_stake > 0;
                          return (
                            <button
                              key={opp.id}
                              onClick={() => handlePlaceBetClick(opp)}
                              disabled={!oppHasStake || isPlacing}
                              className="px-3 py-1.5 bg-tabValue text-bg rounded text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                            >
                              {isPlacing ? '...' : `${formatProviderName(opp.provider1)} ${oppHasStake ? opp.final_stake!.toFixed(0) : 0} kr`}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Freebet Popup */}
      {freebetPopup && (
        <BonusPopup
          title={`Freebet Available (${freebetPopup.freebetAmount.toFixed(0)} kr)`}
          onClose={() => setFreebetPopup(null)}
        >
          <div className="space-y-3">
            <div className="text-xs space-y-1.5">
              <div className="flex justify-between">
                <span className="text-muted">Match</span>
                <span className="text-text">
                  {freebetPopup.opp.home_team} vs {freebetPopup.opp.away_team}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Stake</span>
                <span className="text-text">
                  {freebetPopup.opp.final_stake?.toFixed(0)} kr @ {freebetPopup.opp.odds1.toFixed(2)}
                </span>
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => executePlaceBet(freebetPopup.opp, true)}
                disabled={isPlacing}
                className="flex-1 px-3 py-2 text-xs font-medium bg-tabBonus text-bg rounded hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                <div>Use Freebet</div>
                <div className="text-[10px] opacity-70">no deduction</div>
              </button>
              <button
                onClick={() => executePlaceBet(freebetPopup.opp, false)}
                disabled={isPlacing}
                className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted rounded hover:text-text disabled:opacity-50 transition-colors"
              >
                <div>Use Balance</div>
                <div className="text-[10px] opacity-70">deduct {freebetPopup.opp.final_stake?.toFixed(0)} kr</div>
              </button>
            </div>
          </div>
        </BonusPopup>
      )}
    </div>
  );
}


function resolveOutcomeName(opp: Opportunity): string {
  const outcome = opp.outcome1;
  const point = opp.point != null ? ` ${opp.point}` : '';
  if (outcome === 'home' && opp.home_team) return opp.home_team;
  if (outcome === 'away' && opp.away_team) return opp.away_team;
  if (outcome === 'draw') return 'Draw';
  if (outcome === 'over') return `Over${point}`;
  if (outcome === 'under') return `Under${point}`;
  return outcome;
}
