import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { ExtractionProgressBar } from '../ExtractionProgressBar';
import { FilterBar, MultiSelectPills } from '../FilterBar';
import { BonusPopup } from '../BonusPopup';
import type { Opportunity, Provider } from '@/types';

interface ValuePageProps {
  providers: Provider[];
}

export function ValuePage({ providers }: ValuePageProps) {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Betting workflow state
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
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
      const response = await api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 3);
      setOpportunities(response.opportunities);
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

  // Apply filters
  const filtered = useMemo(() => {
    let result = opportunities;

    if (selectedProviders.size > 0) {
      result = result.filter(o => selectedProviders.has(o.provider1));
    }

    return result;
  }, [opportunities, selectedProviders]);

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

  const handleSelectOpp = (idx: number) => {
    setSelectedOpp(selectedOpp === idx ? null : idx);
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
      // Show freebet popup
      setFreebetPopup({ opp, freebetAmount: freebetInfo.amount });
    } else {
      // Place directly with balance
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
      setSelectedOpp(null);
      fetchData();
    } catch (err) {
      console.error('Failed to place bet:', err);
    } finally {
      setIsPlacing(false);
    }
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabValue" />
          Soft
          <span className="text-muted text-sm font-normal ml-1">({filtered.length})</span>
        </h2>
      </div>

      <ExtractionProgressBar tiers={['api_soft', 'browser_soft']} />

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
            accentColor="tabValue"
          />
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
            ? 'No value bets found. Run extraction first.'
            : 'No matches for current filters.'}
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          {/* Column headers */}
          <div className="grid grid-cols-[1fr_100px_110px_70px_70px_75px_70px] gap-3 px-4 py-2 border-b border-border text-[11px] text-muted uppercase tracking-wider font-semibold">
            <div>Event</div>
            <div className="text-right">Provider</div>
            <div className="text-right">Outcome</div>
            <div className="text-right">Odds</div>
            <div className="text-right">Fair</div>
            <div className="text-right">Stake</div>
            <div className="text-right">Edge</div>
          </div>

          {/* Rows */}
          <div className="divide-y divide-border/50">
            {filtered.map((opp, idx) => {
              const isSelected = selectedOpp === idx;
              const hasStake = opp.final_stake != null && opp.final_stake > 0;
              const isSkipped = !!opp.skip_reason;
              const potentialReturn = hasStake ? opp.final_stake! * opp.odds1 : 0;
              const potentialProfit = potentialReturn - (opp.final_stake || 0);

              return (
                <div key={opp.id}>
                  {/* Main row */}
                  <div
                    className={`grid grid-cols-[1fr_100px_110px_70px_70px_75px_70px] gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm ${
                      isSkipped
                        ? 'opacity-50'
                        : isSelected
                          ? 'bg-tabValue/5'
                          : 'hover:bg-panel2'
                    }`}
                    onClick={() => !isSkipped && handleSelectOpp(idx)}
                  >
                    {/* Event */}
                    <div className="flex flex-col justify-center min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-text text-sm truncate">
                          {opp.home_team} vs {opp.away_team}
                        </span>
                        {isSkipped && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-muted/15 text-muted rounded shrink-0">
                            {opp.skip_reason}
                          </span>
                        )}
                      </div>
                      <span className="text-muted text-[11px] truncate">
                        {opp.sport}{opp.market && opp.market !== '1x2' && opp.market !== 'moneyline' ? ` · ${opp.market}` : ''} · {formatTime(opp.starts_at)}
                      </span>
                    </div>

                    {/* Provider */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm">{formatProviderName(opp.provider1)}</span>
                    </div>

                    {/* Outcome */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm truncate">{resolveOutcomeName(opp)}</span>
                    </div>

                    {/* Odds */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">{opp.odds1.toFixed(2)}</span>
                    </div>

                    {/* Fair odds */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm">{opp.fair_odds?.toFixed(2) || '-'}</span>
                    </div>

                    {/* Stake */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">
                        {hasStake ? `${opp.final_stake!.toFixed(0)} kr` : '-'}
                      </span>
                    </div>

                    {/* Edge */}
                    <div className="flex items-center justify-end">
                      <span className="text-tabValue font-semibold text-sm">
                        +{opp.edge_pct?.toFixed(1)}%
                      </span>
                    </div>
                  </div>

                  {/* Expanded view */}
                  {isSelected && !isSkipped && (
                    <div
                      className="px-4 py-3 bg-panel2/50 border-t border-border/30"
                      onClick={e => e.stopPropagation()}
                    >
                      <div className="flex items-center justify-between gap-6">
                        <div className="flex items-center gap-6 text-sm text-muted">
                          <div>
                            <span className="text-[10px] uppercase tracking-wider text-muted block">Kelly</span>
                            <span className="text-text">
                              {opp.kelly_fraction != null ? `${(opp.kelly_fraction * 100).toFixed(1)}%` : '-'}
                            </span>
                          </div>
                          {hasStake && (
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted block">Return</span>
                              <span className="text-text">{potentialReturn.toFixed(0)} kr</span>
                              <span className="text-tabValue text-xs ml-1">(+{potentialProfit.toFixed(0)})</span>
                            </div>
                          )}
                          <div>
                            <span className="text-[10px] uppercase tracking-wider text-muted block">Market</span>
                            <span className="text-text">{opp.market}</span>
                          </div>
                          {opp.point != null && (
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted block">Line</span>
                              <span className="text-text">{opp.point}</span>
                            </div>
                          )}
                        </div>

                        <button
                          onClick={() => handlePlaceBetClick(opp)}
                          disabled={!hasStake || isPlacing}
                          className="px-4 py-2 bg-tabValue text-bg rounded text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                        >
                          {isPlacing ? 'Placing...' : `Place ${hasStake ? opp.final_stake!.toFixed(0) : 0} kr`}
                        </button>
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
