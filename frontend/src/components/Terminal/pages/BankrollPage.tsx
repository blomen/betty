import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import type { BankrollExposure, Provider } from '@/types';

interface BankrollPageProps {
  providers: Provider[];
  onRefresh: () => void;
}

export function BankrollPage({ providers, onRefresh }: BankrollPageProps) {
  const [exposure, setExposure] = useState<BankrollExposure | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [adjustingProvider, setAdjustingProvider] = useState<string | null>(null);
  const [adjustAmount, setAdjustAmount] = useState('');
  const [depositResult, setDepositResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const exposureData = await api.getBankrollExposure();
      setExposure(exposureData);
    } catch (err) {
      console.error('Failed to fetch bankroll data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Clear deposit result after 5 seconds
  useEffect(() => {
    if (depositResult) {
      const timer = setTimeout(() => setDepositResult(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [depositResult]);

  // Get provider bonus info from providers prop
  const getProviderBonus = (providerId: string) => {
    const provider = providers.find(p => p.id === providerId);
    if (!provider?.bonus) return null;
    return {
      bonus: provider.bonus,
      status: provider.bonus_status,
      hasUnclaimedBonus: provider.bonus_status !== 'completed' && provider.bonus_status !== 'in_progress',
    };
  };

  const handleAdjust = async (providerId: string, isDeposit: boolean) => {
    const amount = parseFloat(adjustAmount);
    if (isNaN(amount) || amount <= 0) return;

    const bonusInfo = getProviderBonus(providerId);
    const hasUnclaimedBonus = bonusInfo?.hasUnclaimedBonus && bonusInfo.bonus?.type === 'bonusdeposit';

    try {
      if (isDeposit && hasUnclaimedBonus) {
        // Use deposit-with-bonus API
        const result = await api.depositWithBonus(providerId, amount);
        const bonusMsg = result.bonus_claimed > 0
          ? ` + ${result.bonus_claimed.toFixed(0)} kr bonus`
          : '';
        setDepositResult({
          success: true,
          message: `Deposited ${result.deposit.toFixed(0)} kr${bonusMsg}. New balance: ${result.new_balance.toFixed(0)} kr`,
        });
      } else {
        // Regular adjustment
        await api.adjustBalance(providerId, isDeposit ? amount : -amount);
        setDepositResult({
          success: true,
          message: `${isDeposit ? 'Deposited' : 'Withdrew'} ${amount.toFixed(0)} kr`,
        });
      }
      setAdjustingProvider(null);
      setAdjustAmount('');
      fetchData();
      onRefresh();
    } catch (err) {
      setDepositResult({
        success: false,
        message: err instanceof Error ? err.message : 'Operation failed',
      });
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabBankroll" />
          Bankroll
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabBankroll" />
        Bankroll
      </h2>

      {/* Deposit Result Message */}
      {depositResult && (
        <div className={`text-sm p-3 rounded ${depositResult.success ? 'bg-success/10 text-success border border-success/20' : 'bg-error/10 text-error border border-error/20'}`}>
          {depositResult.message}
        </div>
      )}

      {/* Overview */}
      {exposure && (
        <Card title="Overview">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <div className="text-muted text-sm">Total Balance</div>
              <div className="text-text text-xl font-semibold">{exposure.total_balance.toFixed(0)} kr</div>
            </div>
            <div>
              <div className="text-muted text-sm">Available</div>
              <div className="text-success text-xl font-semibold">{exposure.total_available.toFixed(0)} kr</div>
            </div>
            <div>
              <div className="text-muted text-sm">Pending</div>
              <div className="text-tabBets text-xl font-semibold">{exposure.total_pending.toFixed(0)} kr</div>
            </div>
          </div>
        </Card>
      )}

      {/* Provider Balances */}
      {exposure && (
        <Card title="Provider Balances">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left text-xs">
                  <th className="pb-2 pr-4">Provider</th>
                  <th className="pb-2 pr-4 text-right">Balance</th>
                  <th className="pb-2 pr-4 text-right">Pending</th>
                  <th className="pb-2 pr-4 text-right">Available</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {exposure.providers.map(provider => {
                  const bonusInfo = getProviderBonus(provider.provider_id);
                  const isAdjusting = adjustingProvider === provider.provider_id;
                  const depositNum = parseFloat(adjustAmount) || 0;
                  const bonusAmount = bonusInfo?.bonus?.amount || 0;
                  const matchedBonus = bonusInfo?.hasUnclaimedBonus && bonusInfo.bonus?.type === 'bonusdeposit'
                    ? Math.min(depositNum, bonusAmount)
                    : 0;

                  return (
                    <tr key={provider.provider_id} className="border-t border-border">
                      <td className="py-3 pr-4 text-text">{formatProviderName(provider.provider_name)}</td>
                      <td className="py-3 pr-4 text-right text-text">{provider.total_balance.toFixed(0)} kr</td>
                      <td className="py-3 pr-4 text-right text-muted">
                        {provider.pending_exposure.toFixed(0)} kr
                        {provider.pending_bets_count > 0 && (
                          <span className="text-xs ml-1">({provider.pending_bets_count})</span>
                        )}
                      </td>
                      <td className="py-3 pr-4 text-right text-success">{provider.available.toFixed(0)} kr</td>
                      <td className="py-3">
                        {isAdjusting ? (
                          <div className="space-y-2">
                            <div className="flex items-center gap-2">
                              <input
                                type="number"
                                value={adjustAmount}
                                onChange={(e) => setAdjustAmount(e.target.value)}
                                placeholder="Amount"
                                className="w-20 px-2 py-1 bg-panel2 border border-border rounded text-text text-xs"
                                autoFocus
                              />
                              <button
                                onClick={() => handleAdjust(provider.provider_id, true)}
                                className={`px-2 py-1 text-xs rounded hover:opacity-80 ${
                                  matchedBonus > 0
                                    ? 'bg-tabBonus/20 text-tabBonus'
                                    : 'bg-success/20 text-success'
                                }`}
                              >
                                {matchedBonus > 0 ? '+Bonus' : '+'}
                              </button>
                              <button
                                onClick={() => handleAdjust(provider.provider_id, false)}
                                className="px-2 py-1 text-xs bg-error/20 text-error rounded hover:bg-error/30"
                              >
                                -
                              </button>
                              <button
                                onClick={() => { setAdjustingProvider(null); setAdjustAmount(''); }}
                                className="px-2 py-1 text-xs text-muted hover:text-text"
                              >
                                Cancel
                              </button>
                            </div>
                            {/* Bonus preview */}
                            {matchedBonus > 0 && depositNum > 0 && (
                              <div className="text-xs space-y-0.5 p-2 bg-panel rounded border border-border">
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
                                  <span className="text-text font-medium">{(depositNum + matchedBonus).toFixed(0)} kr</span>
                                </div>
                              </div>
                            )}
                          </div>
                        ) : (
                          <button
                            onClick={() => setAdjustingProvider(provider.provider_id)}
                            className="px-2 py-1 text-xs text-tabBankroll hover:opacity-80"
                          >
                            Adjust
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
