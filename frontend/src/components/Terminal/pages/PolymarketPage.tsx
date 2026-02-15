import { useState, useEffect, useCallback } from 'react';
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
  const [totalBankroll, setTotalBankroll] = useState(0);
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
      setTotalBankroll(valueRes.total_bankroll ?? 0);
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

  const resolveOutcome = (vb: PolymarketValueBet): string => {
    const point = vb.point != null ? ` ${vb.point}` : '';
    if (vb.outcome === 'home' && vb.home_team) return vb.home_team;
    if (vb.outcome === 'away' && vb.away_team) return vb.away_team;
    if (vb.outcome === 'draw') return 'Draw';
    if (vb.outcome === 'over') return `Over${point}`;
    if (vb.outcome === 'under') return `Under${point}`;
    return vb.outcome;
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabPolymarket" />
          Polymarket
          <span className="text-muted text-sm font-normal ml-1">({valueBets.length})</span>
        </h2>
        <span className="text-muted text-xs">
          {polyStats
            ? `${totalBankroll.toLocaleString()} kr · ${polyStats.matched_events} pin matched${tiersProgress?.tiers?.sharp?.last_run ? ` · ${getTimeAgo(tiersProgress.tiers.sharp.last_run)}` : ''}`
            : ''}
        </span>
      </div>

      {/* Table */}
      {isLoading && valueBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          Loading...
        </div>
      ) : valueBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          {valueBets.length === 0
            ? 'No Polymarket value bets found. Run extraction first.'
            : 'No value bets found.'}
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          {/* Column headers */}
          <div className="grid grid-cols-[1fr_110px_65px_65px_55px_70px_65px] gap-3 px-4 py-2 border-b border-border text-[11px] text-muted uppercase tracking-wider font-semibold">
            <div>Event</div>
            <div className="text-right">Outcome</div>
            <div className="text-right">Odds</div>
            <div className="text-right">Fair</div>
            <div className="text-right">Prob</div>
            <div className="text-right">Stake</div>
            <div className="text-right">Edge</div>
          </div>

          {/* Rows */}
          <div className="divide-y divide-border/50">
            {valueBets.map((vb, idx) => {
              const isSelected = selectedOpp === idx;
              const hasStake = vb.final_stake != null && vb.final_stake > 0;
              const isSkipped = !!vb.skip_reason;
              const potentialReturn = hasStake ? vb.final_stake! * vb.polymarket_odds : 0;
              const potentialProfit = potentialReturn - (vb.final_stake || 0);

              return (
                <div key={`${vb.event_id}-${vb.outcome}`}>
                  {/* Main row */}
                  <div
                    className={`grid grid-cols-[1fr_110px_65px_65px_55px_70px_65px] gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm ${
                      isSkipped
                        ? 'opacity-50'
                        : isSelected
                          ? 'bg-tabPolymarket/5'
                          : 'hover:bg-panel2'
                    }`}
                    onClick={() => !isSkipped && handleSelectOpp(idx)}
                  >
                    {/* Event */}
                    <div className="flex flex-col justify-center min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-text text-sm truncate">
                          {vb.home_team} vs {vb.away_team}
                        </span>
                        {isSkipped && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-muted/15 text-muted rounded shrink-0">
                            {vb.skip_reason}
                          </span>
                        )}
                      </div>
                      <span className="text-muted text-[11px] truncate">
                        {vb.sport}
                        {vb.market && vb.market !== '1x2' && vb.market !== 'moneyline'
                          ? ` · ${vb.market}` : ''}
                        {vb.league ? ` · ${vb.league}` : ''}
                        {' · '}{formatTime(vb.start_time)}
                      </span>
                    </div>

                    {/* Outcome */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm truncate">{resolveOutcome(vb)}</span>
                    </div>

                    {/* Odds */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">{vb.polymarket_odds.toFixed(2)}</span>
                    </div>

                    {/* Fair odds */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm">{vb.fair_odds.toFixed(2)}</span>
                    </div>

                    {/* Prob (Pinnacle fair probability) */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm">
                        {(vb.fair_probability * 100).toFixed(0)}%
                      </span>
                    </div>

                    {/* Stake */}
                    <div className="flex items-center justify-end">
                      <span className="text-sm font-medium text-text">
                        {hasStake ? `${vb.final_stake!.toFixed(0)} kr` : '-'}
                      </span>
                    </div>

                    {/* Edge */}
                    <div className="flex items-center justify-end">
                      <span className="text-tabPolymarket font-semibold text-sm">
                        +{vb.edge_pct.toFixed(1)}%
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
                            <span className="text-[10px] uppercase tracking-wider text-muted block">Fair Prob</span>
                            <span className="text-text">{(vb.fair_probability * 100).toFixed(1)}%</span>
                          </div>
                          {hasStake && (
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted block">Return</span>
                              <span className="text-text">{potentialReturn.toFixed(0)} kr</span>
                              <span className="text-tabPolymarket text-xs ml-1">(+{potentialProfit.toFixed(0)} kr)</span>
                            </div>
                          )}
                          {vb.kelly_fraction != null && (
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted block">Kelly</span>
                              <span className="text-text">{(vb.kelly_fraction * 100).toFixed(1)}%</span>
                            </div>
                          )}
                          {vb.point != null && (
                            <div>
                              <span className="text-[10px] uppercase tracking-wider text-muted block">Line</span>
                              <span className="text-text">{vb.point}</span>
                            </div>
                          )}
                        </div>

                        <button
                          onClick={() => handlePlaceBet(vb)}
                          disabled={!hasStake || isPlacing}
                          className="px-4 py-2 bg-tabPolymarket text-bg rounded text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                        >
                          {isPlacing ? 'Placing...' : `Place ${hasStake ? vb.final_stake!.toFixed(0) : 0} kr`}
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

      {totalScanned > 0 && (
        <div className="text-muted text-xs text-center pt-1">
          {totalScanned} total value bets scanned across all providers
        </div>
      )}
    </div>
  );
}
