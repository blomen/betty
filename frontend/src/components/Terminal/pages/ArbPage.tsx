import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import type { FullArbitrage, BankrollExposure } from '@/types';

export function ArbPage() {
  const [arbs, setArbs] = useState<FullArbitrage[]>([]);
  const [exposure, setExposure] = useState<BankrollExposure | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Betting workflow state
  const [selectedArb, setSelectedArb] = useState<number | null>(null);
  const [stakeInput, setStakeInput] = useState('');
  const [isPlacing, setIsPlacing] = useState(false);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [arbResponse, exposureData] = await Promise.all([
        api.scanArbitrage(2.0, 100),  // Min 2% profit, max 100 results
        api.getBankrollExposure(),
      ]);
      setArbs(arbResponse.opportunities);
      setExposure(exposureData);
    } catch (err) {
      console.error('Failed to fetch data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

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

  const handleSelectArb = (idx: number) => {
    setSelectedArb(selectedArb === idx ? null : idx);
    setStakeInput('');
  };

  const calculateLegStakes = (arb: FullArbitrage, totalStake: number) => {
    const totalImpliedProb = arb.legs.reduce((sum, l) => sum + (1 / l.odds), 0);
    return arb.legs.map(leg => ({
      ...leg,
      stake: (totalStake * (1 / leg.odds)) / totalImpliedProb,
      return: (totalStake * (1 / leg.odds)) / totalImpliedProb * leg.odds,
    }));
  };

  const handlePlaceBets = async () => {
    if (selectedArb === null || !stakeInput) return;
    const arb = arbs[selectedArb];
    const totalStake = parseFloat(stakeInput);
    if (isNaN(totalStake) || totalStake <= 0) return;

    setIsPlacing(true);
    try {
      const legStakes = calculateLegStakes(arb, totalStake);

      for (const leg of legStakes) {
        await api.createBet({
          event_id: arb.event_id,
          provider_id: leg.provider,
          market: arb.market,
          outcome: leg.outcome,
          odds: leg.odds,
          stake: leg.stake,
          is_bonus: false,
        });
      }

      setSelectedArb(null);
      setStakeInput('');
      fetchData();
    } catch (err) {
      console.error('Failed to place bets:', err);
    } finally {
      setIsPlacing(false);
    }
  };

  const totalBankroll = exposure?.total_available || 0;

  // Filter out suspect arbs (>7% profit likely data errors)
  const filteredArbs = arbs.filter(a => a.quality !== 'suspect');

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabArb" />
          Arbitrage Opportunities
        </h2>
        <button
          onClick={fetchData}
          disabled={isLoading}
          className="px-3 py-1 bg-panel2 border border-border text-text rounded text-sm font-medium hover:bg-border disabled:opacity-50"
        >
          {isLoading ? 'Loading...' : 'Refresh'}
        </button>
      </div>

      {/* Results */}
      <Card title={`Opportunities (${filteredArbs.length})`}>
        {isLoading ? (
          <div className="text-muted text-sm py-4 text-center">Loading...</div>
        ) : filteredArbs.length === 0 ? (
          <div className="text-muted text-sm py-4 text-center">
            No arbitrage opportunities found. Run extraction first.
          </div>
        ) : (
          <div className="space-y-4">
            {filteredArbs.map((arb, idx) => {
              const isSelected = selectedArb === idx;
              const stake = parseFloat(stakeInput) || 0;
              const legStakes = isSelected && stake > 0 ? calculateLegStakes(arb, stake) : null;

              return (
                <div
                  key={`${arb.event_id}-${idx}`}
                  className={`border rounded-lg p-4 cursor-pointer transition-colors ${
                    isSelected ? 'border-tabArb bg-tabArb/5' : 'border-border hover:border-muted2'
                  }`}
                  onClick={() => handleSelectArb(idx)}
                >
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <div className="text-text font-medium">
                        {arb.home_team} vs {arb.away_team}
                      </div>
                      <div className="text-muted text-xs mt-1">
                        {arb.sport} | {formatTime(arb.start_time)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-tabArb font-semibold">
                        +{arb.profit_pct.toFixed(2)}%
                      </div>
                      <div className="text-muted text-xs">{arb.market}</div>
                    </div>
                  </div>

                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-muted text-left text-xs">
                        <th className="pb-2">Outcome</th>
                        <th className="pb-2">Provider</th>
                        <th className="pb-2 text-right">Odds</th>
                        <th className="pb-2 text-right">Stake</th>
                        <th className="pb-2 text-right">Return</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(legStakes || arb.legs).map((leg, legIdx) => (
                        <tr key={legIdx} className="border-t border-border/50">
                          <td className="py-2 text-text capitalize">{leg.outcome}</td>
                          <td className="py-2 text-muted">{formatProviderName(leg.provider)}</td>
                          <td className="py-2 text-right text-text">{leg.odds.toFixed(2)}</td>
                          <td className="py-2 text-right text-muted">
                            {legStakes ? `${leg.stake.toFixed(2)} kr` : '-'}
                          </td>
                          <td className="py-2 text-right text-success">
                            {legStakes ? `${leg.return.toFixed(2)} kr` : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {/* Stake Input - shown when selected */}
                  {isSelected && (
                    <div className="mt-4 pt-4 border-t border-border" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-3">
                        <span className="text-muted text-sm">Total Stake:</span>
                        <input
                          type="number"
                          value={stakeInput}
                          onChange={(e) => setStakeInput(e.target.value)}
                          placeholder="Enter amount"
                          className="w-32 px-3 py-2 bg-panel2 border border-border rounded text-text text-sm"
                        />
                        <div className="flex gap-2">
                          {[0.01, 0.02, 0.05].map(pct => (
                            <button
                              key={pct}
                              onClick={() => setStakeInput((totalBankroll * pct).toFixed(0))}
                              className="px-2 py-1 text-xs bg-panel2 border border-border rounded text-muted hover:text-text"
                            >
                              {pct * 100}%
                            </button>
                          ))}
                        </div>
                        <button
                          onClick={handlePlaceBets}
                          disabled={!stakeInput || isPlacing}
                          className="px-4 py-2 bg-tabArb text-bg rounded text-sm font-medium hover:opacity-90 disabled:opacity-50 ml-auto"
                        >
                          {isPlacing ? 'Placing...' : 'Place Bets'}
                        </button>
                      </div>
                      {stake > 0 && (
                        <div className="mt-2 text-sm text-muted">
                          Guaranteed return: ${(stake / arb.legs.reduce((sum, l) => sum + (1 / l.odds), 0)).toFixed(2)}
                          <span className="text-tabArb ml-2">
                            (+${((stake / arb.legs.reduce((sum, l) => sum + (1 / l.odds), 0)) - stake).toFixed(2)} profit)
                          </span>
                        </div>
                      )}
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
