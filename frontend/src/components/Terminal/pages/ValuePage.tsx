import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { ExtractionProgressBar } from '../ExtractionProgressBar';
import type { Opportunity } from '@/types';

export function ValuePage() {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Betting workflow state
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 5);  // Min 5% edge
      setOpportunities(response.opportunities);
    } catch (err) {
      console.error('Failed to fetch value bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useRefreshOnExtraction(fetchData);

  const formatTime = (dateStr: string | undefined) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const handleSelectOpp = (idx: number) => {
    setSelectedOpp(selectedOpp === idx ? null : idx);
  };

  const handlePlaceBet = async (opp: Opportunity) => {
    const stake = opp.final_stake;
    if (!stake || stake <= 0) return;

    setIsPlacing(true);
    try {
      await api.createBet({
        event_id: opp.event_id,
        provider_id: opp.provider1,
        market: opp.market,
        outcome: opp.outcome1,
        odds: opp.odds1,
        stake,
        is_bonus: false,
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
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabValue" />
        Value Bets
      </h2>

      <ExtractionProgressBar tiers={['api_soft', 'browser_soft']} />

      {/* Results */}
      <Card title={`Value Bets (${opportunities.length})`}>
        {isLoading ? (
          <div className="text-muted text-sm py-4 text-center">Loading...</div>
        ) : opportunities.length === 0 ? (
          <div className="text-muted text-sm py-4 text-center">
            No value bets found. Run extraction first.
          </div>
        ) : (
          <div className="space-y-2">
            {opportunities.map((opp, idx) => {
              const isSelected = selectedOpp === idx;
              const hasStake = opp.final_stake && opp.final_stake > 0;
              const isSkipped = !!opp.skip_reason;
              const potentialReturn = hasStake ? opp.final_stake! * opp.odds1 : 0;
              const potentialProfit = potentialReturn - (opp.final_stake || 0);

              return (
                <div
                  key={opp.id}
                  className={`border rounded-lg p-4 cursor-pointer transition-colors ${
                    isSkipped
                      ? 'border-border/50 bg-panel/50 opacity-60'
                      : isSelected
                        ? 'border-tabValue bg-tabValue/5'
                        : 'border-border hover:border-muted2'
                  }`}
                  onClick={() => !isSkipped && handleSelectOpp(idx)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-4">
                        <div>
                          <div className="flex items-center gap-2">
                            <div className="text-text font-medium">{opp.home_team} vs {opp.away_team}</div>
                            {isSkipped && (
                              <span className="text-xs px-1.5 py-0.5 bg-muted/20 text-muted rounded">
                                {opp.skip_reason}
                              </span>
                            )}
                          </div>
                          <div className="text-muted text-xs mt-1">{opp.sport} | {formatTime(opp.starts_at)}</div>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-6 text-sm">
                      <div className="text-center">
                        <div className="text-muted text-xs">Provider</div>
                        <div className="text-text">{formatProviderName(opp.provider1)}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Outcome</div>
                        <div className="text-text">{resolveOutcomeName(opp)}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Odds</div>
                        <div className="text-text">{opp.odds1.toFixed(2)}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Stake</div>
                        <div className="text-text font-medium">
                          {hasStake ? `${opp.final_stake!.toFixed(0)} kr` : '-'}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Edge</div>
                        <div className="text-tabValue font-medium">+{opp.edge_pct?.toFixed(1)}%</div>
                      </div>
                    </div>
                  </div>

                  {/* Expanded view when selected */}
                  {isSelected && !isSkipped && (
                    <div className="mt-4 pt-4 border-t border-border" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-between">
                        <div className="text-sm text-muted">
                          <span>Fair odds: {opp.fair_odds?.toFixed(2) || '-'}</span>
                          {hasStake && (
                            <>
                              <span className="mx-3">|</span>
                              <span>
                                Potential: {potentialReturn.toFixed(0)} kr
                                <span className="text-tabValue ml-1">(+{potentialProfit.toFixed(0)} kr)</span>
                              </span>
                            </>
                          )}
                        </div>
                        <button
                          onClick={() => handlePlaceBet(opp)}
                          disabled={!hasStake || isPlacing}
                          className="px-4 py-2 bg-tabValue text-bg rounded text-sm font-medium hover:opacity-90 disabled:opacity-50"
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
        )}
      </Card>
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
