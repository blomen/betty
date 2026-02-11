import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { useRefreshOnExtraction, useTiersProgress } from '@/hooks/useExtractionStatus';
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
  const [polyStats, setPolyStats] = useState<PolymarketStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const tiersProgress = useTiersProgress();

  // Betting workflow state
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [valueRes, stats] = await Promise.all([
        api.getPolymarketValue(3, undefined, 50),
        api.getPolymarketStats(),
      ]);
      setValueBets(valueRes.value_bets);
      setTotalScanned(valueRes.total_scanned);
      setPolyStats(stats);
    } catch (err) {
      console.error('Failed to fetch Polymarket data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useRefreshOnExtraction(fetchData);

  const formatTime = (dateStr: string | null) => {
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

  const handlePlaceBet = async (vb: PolymarketValueBet) => {
    const stake = vb.final_stake;
    if (!stake || stake <= 0) return;

    setIsPlacing(true);
    try {
      await api.createBet({
        event_id: vb.event_id,
        provider_id: 'polymarket',
        market: vb.market,
        outcome: vb.outcome,
        odds: vb.polymarket_odds,
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabPolymarket" />
          Polymarket
        </h2>
      </div>

      {/* Polymarket stats bar */}
      <div className="bg-panel2 border border-border rounded px-4 py-2 mb-3">
        <div className="flex items-center gap-2 text-xs font-mono">
          <span className="text-muted2/50">◆</span>
          <span className="text-muted2/60 w-16">POLY</span>
          <span className="text-muted2/40">{'░'.repeat(12)}</span>
          <span className="text-muted2/50 ml-auto">
            {polyStats
              ? `${polyStats.matched_events} pin matched | ${tiersProgress?.tiers?.sharp?.last_run ? getTimeAgo(tiersProgress.tiers.sharp.last_run) : ''}`
              : 'loading...'}
          </span>
        </div>
      </div>

      {/* Value Bets Card */}
      <Card title={`Value Bets (${valueBets.length})`}>
        {isLoading ? (
          <div className="text-muted text-sm py-4 text-center">Loading...</div>
        ) : valueBets.length === 0 ? (
          <div className="text-muted text-sm py-4 text-center">
            No Polymarket value bets found. Run extraction first.
          </div>
        ) : (
          <div className="space-y-2">
            {valueBets.map((vb, idx) => {
              const isSelected = selectedOpp === idx;
              const hasStake = vb.final_stake != null && vb.final_stake > 0;
              const isSkipped = !!vb.skip_reason;
              const potentialReturn = hasStake ? vb.final_stake! * vb.polymarket_odds : 0;
              const potentialProfit = potentialReturn - (vb.final_stake || 0);

              return (
                <div
                  key={`${vb.event_id}-${vb.outcome}`}
                  className={`border rounded-lg p-4 cursor-pointer transition-colors ${
                    isSkipped
                      ? 'border-border/50 bg-panel/50 opacity-60'
                      : isSelected
                        ? 'border-tabPolymarket bg-tabPolymarket/5'
                        : 'border-border hover:border-muted2'
                  }`}
                  onClick={() => !isSkipped && handleSelectOpp(idx)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <div className="text-text font-medium">
                          {vb.home_team} vs {vb.away_team}
                        </div>
                        {isSkipped && (
                          <span className="text-xs px-1.5 py-0.5 bg-muted/20 text-muted rounded">
                            {vb.skip_reason}
                          </span>
                        )}
                      </div>
                      <div className="text-muted text-xs mt-1">
                        {vb.sport} | {vb.market} | {formatTime(vb.start_time)}
                      </div>
                    </div>
                    <div className="flex items-center gap-6 text-sm">
                      <div className="text-center">
                        <div className="text-muted text-xs">Outcome</div>
                        <div className="text-text capitalize">{vb.outcome}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Poly Odds</div>
                        <div className="text-text">{vb.polymarket_odds.toFixed(2)}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Fair Odds</div>
                        <div className="text-text">{vb.fair_odds.toFixed(2)}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Stake</div>
                        <div className="text-text font-medium">
                          {hasStake ? `${vb.final_stake!.toFixed(0)} kr` : '-'}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-muted text-xs">Edge</div>
                        <div className="text-tabPolymarket font-medium">
                          +{vb.edge_pct.toFixed(1)}%
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Expanded view when selected */}
                  {isSelected && !isSkipped && (
                    <div
                      className="mt-4 pt-4 border-t border-border"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="flex items-center justify-between">
                        <div className="text-sm text-muted">
                          <span>Fair prob: {(vb.fair_probability * 100).toFixed(1)}%</span>
                          <span className="mx-3">|</span>
                          <span>League: {vb.league || '-'}</span>
                          {vb.point !== null && (
                            <>
                              <span className="mx-3">|</span>
                              <span>Line: {vb.point}</span>
                            </>
                          )}
                          {hasStake && (
                            <>
                              <span className="mx-3">|</span>
                              <span>
                                Potential: {potentialReturn.toFixed(0)} kr
                                <span className="text-tabPolymarket ml-1">(+{potentialProfit.toFixed(0)} kr)</span>
                              </span>
                            </>
                          )}
                        </div>
                        <button
                          onClick={() => handlePlaceBet(vb)}
                          disabled={!hasStake || isPlacing}
                          className="px-4 py-2 bg-tabPolymarket text-bg rounded text-sm font-medium hover:opacity-90 disabled:opacity-50"
                        >
                          {isPlacing ? 'Placing...' : `Place ${hasStake ? vb.final_stake!.toFixed(0) : 0} kr`}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {totalScanned > 0 && (
              <div className="text-muted text-xs text-center pt-2">
                {totalScanned} total value bets scanned across all providers
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
