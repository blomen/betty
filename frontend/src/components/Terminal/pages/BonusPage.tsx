import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { formatProviderName, outcomeToTeam } from '@/utils/formatters';
import type { BonusArbOpportunity, BonusArbLeg, Provider } from '@/types';

interface BonusPageProps {
  providers: Provider[];
  onProvidersChange?: () => void;
}

export function BonusPage({ providers, onProvidersChange }: BonusPageProps) {
  const [opportunities, setOpportunities] = useState<BonusArbOpportunity[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [bankrollInfo, setBankrollInfo] = useState<{
    total: number;
    anchor: number;
    bonus: { type: string; amount: number } | null;
    counterparts: string[];
  } | null>(null);

  // Expanded opportunity state
  const [expandedOpp, setExpandedOpp] = useState<number | null>(null);

  // Deposit form state
  const [depositAmount, setDepositAmount] = useState<string>('');
  const [isDepositing, setIsDepositing] = useState(false);
  const [depositResult, setDepositResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);

  const fetchBonusArbs = useCallback(async () => {
    if (!selectedProvider) return;
    setIsLoading(true);
    try {
      const response = await api.getBonusArbitrage(selectedProvider, 50);
      setOpportunities(response.opportunities);
      setBankrollInfo({
        total: response.total_bankroll,
        anchor: response.anchor_balance,
        bonus: response.anchor_bonus,
        counterparts: response.valid_counterparts,
      });
    } catch (err) {
      console.error('Failed to fetch bonus arbitrage:', err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedProvider]);

  useEffect(() => {
    if (selectedProvider) {
      fetchBonusArbs();
    }
  }, [selectedProvider, fetchBonusArbs]);

  const handleToggleOpp = (idx: number) => {
    setExpandedOpp(expandedOpp === idx ? null : idx);
  };

  // Handle deposit with bonus
  const handleDeposit = async (providerId: string) => {
    const amount = parseFloat(depositAmount);
    if (isNaN(amount) || amount <= 0) {
      setDepositResult({ success: false, message: 'Enter a valid amount' });
      return;
    }

    setIsDepositing(true);
    setDepositResult(null);

    try {
      const result = await api.depositWithBonus(providerId, amount);
      const bonusMsg = result.bonus_claimed > 0
        ? ` + ${result.bonus_claimed.toFixed(0)} kr bonus`
        : '';
      setDepositResult({
        success: true,
        message: `Deposited ${result.deposit.toFixed(0)} kr${bonusMsg}. New balance: ${result.new_balance.toFixed(0)} kr`,
      });
      setDepositAmount('');
      // Refresh provider data
      if (onProvidersChange) {
        onProvidersChange();
      }
      // Refresh bonus arbs if this provider was selected
      if (selectedProvider === providerId) {
        fetchBonusArbs();
      }
    } catch (err) {
      setDepositResult({
        success: false,
        message: err instanceof Error ? err.message : 'Deposit failed',
      });
    } finally {
      setIsDepositing(false);
    }
  };

  // Filter to providers with bonus config
  const bonusProviders = providers.filter(p => p.is_enabled && p.bonus && !['pinnacle', 'polymarket'].includes(p.id));
  const selectedProviderData = providers.find(p => p.id === selectedProvider);

  // Find providers with available double deposit bonus (for deposit UI)
  const depositableProviders = bonusProviders.filter(
    p => p.bonus?.type === 'doubledeposit' && p.bonus_status !== 'completed' && p.bonus_status !== 'in_progress'
  );

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabBonus" />
        Bonus Arbitrage
      </h2>

      {/* Deposit with Bonus Section - only show if there are depositable providers */}
      {depositableProviders.length > 0 && (
        <Card title="Deposit & Claim Bonus">
          <div className="space-y-3">
            {depositResult && (
              <div className={`text-sm p-2 rounded ${depositResult.success ? 'bg-success/10 text-success' : 'bg-error/10 text-error'}`}>
                {depositResult.message}
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {depositableProviders.map(provider => {
                const bonusAmount = provider.bonus?.amount || 0;
                const depositNum = parseFloat(depositAmount) || 0;
                const matchedBonus = Math.min(depositNum, bonusAmount);
                const totalAdded = depositNum + matchedBonus;

                return (
                  <div
                    key={provider.id}
                    className="p-4 rounded border border-border bg-panel2/30"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-text font-medium">{formatProviderName(provider.name)}</span>
                      <span className="text-xs text-tabBonus">Available</span>
                    </div>
                    <div className="text-muted text-xs mb-3">
                      Double Deposit: up to {bonusAmount} kr
                    </div>
                    <div className="text-muted text-xs mb-3">
                      Current Balance: {provider.balance.toFixed(0)} kr
                    </div>

                    {/* Deposit input */}
                    <div className="space-y-2">
                      <div className="flex gap-2">
                        <input
                          type="number"
                          placeholder="Amount"
                          value={depositAmount}
                          onChange={(e) => setDepositAmount(e.target.value)}
                          className="flex-1 px-2 py-1.5 text-sm bg-panel border border-border rounded text-text placeholder:text-muted focus:outline-none focus:border-tabBonus"
                        />
                        <span className="text-muted text-sm self-center">kr</span>
                      </div>

                      {depositNum > 0 && (
                        <div className="text-xs space-y-1 p-2 bg-panel rounded">
                          <div className="flex justify-between">
                            <span className="text-muted">Deposit:</span>
                            <span className="text-text">{depositNum.toFixed(0)} kr</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-muted">Bonus (matched):</span>
                            <span className="text-tabBonus">+{matchedBonus.toFixed(0)} kr</span>
                          </div>
                          <div className="flex justify-between border-t border-border pt-1 mt-1">
                            <span className="text-muted">Total:</span>
                            <span className="text-text font-medium">{totalAdded.toFixed(0)} kr</span>
                          </div>
                        </div>
                      )}

                      <button
                        onClick={() => handleDeposit(provider.id)}
                        disabled={isDepositing || depositNum <= 0}
                        className="w-full px-3 py-2 text-sm bg-tabBonus text-white rounded hover:bg-tabBonus/80 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      >
                        {isDepositing ? 'Depositing...' : 'Deposit & Claim Bonus'}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Card>
      )}

      {/* Provider Selection with Bonus Info */}
      <Card title="Select Bonus Provider">
        {bonusProviders.length === 0 ? (
          <div className="text-muted text-sm py-4 text-center">
            No providers with bonus configured. Check providers.yaml.
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
            {bonusProviders.map(provider => {
              const isSelected = selectedProvider === provider.id;
              const bonusType = provider.bonus?.type === 'freebet' ? 'Free Bet' : 'Double Deposit';
              const bonusAmount = provider.bonus?.amount || 0;
              const status = provider.bonus_status;

              return (
                <button
                  key={provider.id}
                  onClick={() => setSelectedProvider(provider.id)}
                  disabled={status === 'completed'}
                  className={`
                    p-3 rounded border text-sm text-left transition-colors
                    ${isSelected
                      ? 'border-tabBonus bg-tabBonus/10'
                      : status === 'completed'
                        ? 'border-border/50 bg-panel2/50 opacity-50 cursor-not-allowed'
                        : 'border-border hover:border-muted2'
                    }
                  `}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-text font-medium">{formatProviderName(provider.name)}</span>
                    {status === 'completed' && (
                      <span className="text-xs text-success">Done</span>
                    )}
                    {status === 'in_progress' && (
                      <span className="text-xs text-tabBonus">Active</span>
                    )}
                  </div>
                  <div className="text-muted text-xs mt-1">
                    {bonusType}: {bonusAmount} kr
                  </div>
                  <div className="text-muted text-xs">
                    Balance: {provider.balance.toFixed(0)} kr
                  </div>
                </button>
              );
            })}
          </div>
        )}
        {bankrollInfo && selectedProviderData && (
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex gap-6 text-sm flex-wrap">
              <div>
                <span className="text-muted">Total Bankroll:</span>
                <span className="text-text ml-2">{bankrollInfo.total.toFixed(0)} kr</span>
              </div>
              <div>
                <span className="text-muted">{formatProviderName(selectedProvider)} Balance:</span>
                <span className="text-text ml-2">{bankrollInfo.anchor.toFixed(0)} kr</span>
              </div>
              {bankrollInfo.bonus && (
                <div>
                  <span className="text-muted">Bonus:</span>
                  <span className="text-tabBonus ml-2">
                    {bankrollInfo.bonus.amount} kr {bankrollInfo.bonus.type === 'freebet' ? 'Free Bet' : 'Match'}
                  </span>
                </div>
              )}
              <div>
                <span className="text-muted">Counterparts:</span>
                <span className="text-text ml-2">{bankrollInfo.counterparts.length}</span>
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Results */}
      {selectedProvider && (() => {
        // Filter out suspect arbs (>7% profit likely data errors)
        const filteredOpps = opportunities.filter(o => o.quality !== 'suspect');

        return (
        <Card
          title={`Opportunities (${filteredOpps.length})`}
          headerRight={
            <button
              onClick={fetchBonusArbs}
              disabled={isLoading}
              className="text-xs bg-panel2 border border-border px-2 py-1 rounded text-text hover:bg-border disabled:opacity-50"
            >
              {isLoading ? 'Loading...' : 'Refresh'}
            </button>
          }
        >
          {isLoading ? (
            <div className="text-muted text-sm py-4 text-center">Loading...</div>
          ) : filteredOpps.length === 0 ? (
            <div className="text-muted text-sm py-4 text-center">
              No arbitrage opportunities found for {formatProviderName(selectedProvider)}.
              Run extraction with Pinnacle and {formatProviderName(selectedProvider)} first.
            </div>
          ) : (
            <div className="space-y-2">
              {filteredOpps.map((opp, idx) => {
                const isExpanded = expandedOpp === idx;
                const isPositiveProfit = opp.profit_pct >= 0;
                const anchorLeg = opp.legs.find(l => l.is_anchor);
                const hedgeLegs = opp.legs.filter(l => !l.is_anchor);

                // Convert outcome to team name for clarity
                const anchorTeamName = anchorLeg
                  ? outcomeToTeam(anchorLeg.outcome, opp.home_team ?? undefined, opp.away_team ?? undefined)
                  : opp.anchor_outcome;

                return (
                  <div
                    key={`${opp.event_id}-${opp.anchor_outcome}-${idx}`}
                    className={`border rounded-lg overflow-hidden transition-colors ${
                      isExpanded ? 'border-tabBonus' : 'border-border hover:border-muted2'
                    }`}
                  >
                    {/* Summary row - always visible */}
                    <div
                      className={`p-4 cursor-pointer ${isExpanded ? 'bg-tabBonus/5' : ''}`}
                      onClick={() => handleToggleOpp(idx)}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex-1 min-w-0">
                          <div className="text-text font-medium truncate">
                            {opp.home_team} <span className="text-muted text-xs">(H)</span> vs {opp.away_team} <span className="text-muted text-xs">(A)</span>
                          </div>
                          <div className="text-muted text-xs mt-1 flex items-center gap-2">
                            <span>{opp.sport}</span>
                            <span className="text-border">|</span>
                            <span>Bet on: <span className="text-tabBonus font-medium">{anchorTeamName}</span></span>
                          </div>
                        </div>
                        <div className="flex items-center gap-4 text-sm shrink-0">
                          <div className={`text-lg font-semibold ${isPositiveProfit ? 'text-success' : 'text-error'}`}>
                            {isPositiveProfit ? '+' : ''}{opp.profit_pct.toFixed(1)}%
                          </div>
                          <div className="text-muted">
                            {isExpanded ? '−' : '+'}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Expanded details - legs table */}
                    {isExpanded && (
                      <div className="border-t border-border bg-panel2/30 p-4">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-muted text-xs">
                              <th className="text-left pb-2">Bet on</th>
                              <th className="text-left pb-2">Provider</th>
                              <th className="text-right pb-2">Odds</th>
                            </tr>
                          </thead>
                          <tbody>
                            {anchorLeg && (
                              <LegRow leg={anchorLeg} isAnchor homeTeam={opp.home_team} awayTeam={opp.away_team} />
                            )}
                            {hedgeLegs.map((leg, legIdx) => (
                              <LegRow key={legIdx} leg={leg} homeTeam={opp.home_team} awayTeam={opp.away_team} />
                            ))}
                          </tbody>
                        </table>

                        {/* Bonus indicator */}
                        {anchorLeg?.bonus_type && (
                          <div className="mt-3 pt-3 border-t border-border flex items-center gap-2">
                            <span className="px-2 py-1 text-xs bg-tabBonus/20 text-tabBonus rounded">
                              {anchorLeg.bonus_type === 'freebet' ? 'FREE BET' : 'BONUS MATCH'}
                            </span>
                            <span className="text-muted text-xs">
                              {anchorLeg.bonus_amount} kr wagering requirement
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
        );
      })()}
    </div>
  );
}

function LegRow({ leg, isAnchor = false, homeTeam, awayTeam }: { leg: BonusArbLeg; isAnchor?: boolean; homeTeam?: string | null; awayTeam?: string | null }) {
  const teamName = outcomeToTeam(leg.outcome, homeTeam || undefined, awayTeam || undefined);

  return (
    <tr className={isAnchor ? 'text-tabBonus' : 'text-text'}>
      <td className="py-1 flex items-center gap-2">
        {teamName}
        {isAnchor && (
          <span className="text-xs bg-tabBonus/20 px-1.5 py-0.5 rounded">ANCHOR</span>
        )}
      </td>
      <td className="py-1">{formatProviderName(leg.provider)}</td>
      <td className="py-1 text-right">{leg.odds.toFixed(2)}</td>
    </tr>
  );
}
