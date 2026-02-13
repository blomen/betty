import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { BonusPopup } from '../BonusPopup';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import type { BankrollExposure, Provider } from '@/types';

interface BonusProgressEntry {
  status: string;
  bonus_amount: number;
  wagering_requirement: number;
  wagered_amount: number;
  min_odds: number;
  progress_pct: number;
  is_cleared: boolean;
  claimed_at: string | null;
  expires_at: string | null;
  days_remaining: number | null;
}

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
  const [bonusProgress, setBonusProgress] = useState<Record<string, BonusProgressEntry>>({});

  // Bonus deposit popup state
  const [bonusPopup, setBonusPopup] = useState<{
    providerId: string;
    amount: number;
    bonusAmount: number;
  } | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [exposureData, statusData] = await Promise.all([
        api.getBankrollExposure(),
        api.getBankrollStatus().catch(() => null),
      ]);
      setExposure(exposureData);
      if (statusData?.bonus_progress) {
        setBonusProgress(statusData.bonus_progress);
      }
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
      hasUnclaimedBonus: provider.bonus_status !== 'completed' && provider.bonus_status !== 'in_progress' && provider.bonus_status !== 'claimed',
    };
  };

  const handleDeposit = (providerId: string) => {
    const amount = parseFloat(adjustAmount);
    if (isNaN(amount) || amount <= 0) return;

    const bonusInfo = getProviderBonus(providerId);
    const hasBonus = bonusInfo?.hasUnclaimedBonus && bonusInfo.bonus?.type === 'bonusdeposit';

    if (hasBonus) {
      // Show popup for bonus decision (accept or decline)
      const bonusAmount = Math.min(amount, bonusInfo!.bonus!.amount);
      setBonusPopup({ providerId, amount, bonusAmount });
    } else {
      // Regular deposit — no bonus available
      executeDeposit(providerId, amount, false);
    }
  };

  const executeDeposit = async (providerId: string, amount: number, withBonus: boolean) => {
    try {
      if (withBonus) {
        const result = await api.depositWithBonus(providerId, amount);
        const bonusMsg = result.bonus_claimed > 0
          ? ` + ${result.bonus_claimed.toFixed(0)} kr bonus`
          : '';
        setDepositResult({
          success: true,
          message: `Deposited ${result.deposit.toFixed(0)} kr${bonusMsg}. New balance: ${result.new_balance.toFixed(0)} kr`,
        });
      } else {
        await api.adjustBalance(providerId, amount);
        setDepositResult({
          success: true,
          message: `Deposited ${amount.toFixed(0)} kr`,
        });
      }
      setAdjustingProvider(null);
      setAdjustAmount('');
      setBonusPopup(null);
      fetchData();
      onRefresh();
    } catch (err) {
      setDepositResult({
        success: false,
        message: err instanceof Error ? err.message : 'Operation failed',
      });
      setBonusPopup(null);
    }
  };

  const handleWithdraw = async (providerId: string) => {
    const amount = parseFloat(adjustAmount);
    if (isNaN(amount) || amount <= 0) return;

    try {
      await api.adjustBalance(providerId, -amount);
      setDepositResult({
        success: true,
        message: `Withdrew ${amount.toFixed(0)} kr`,
      });
      setAdjustingProvider(null);
      setAdjustAmount('');
      fetchData();
      onRefresh();
    } catch (err) {
      setDepositResult({
        success: false,
        message: err instanceof Error ? err.message : 'Withdrawal failed',
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
                  const isAdjusting = adjustingProvider === provider.provider_id;

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
                              onClick={() => handleDeposit(provider.provider_id)}
                              className="px-2 py-1 text-xs bg-success/20 text-success rounded hover:bg-success/30"
                            >
                              +
                            </button>
                            <button
                              onClick={() => handleWithdraw(provider.provider_id)}
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

      {/* Active Bonus Wagering Progress */}
      {Object.keys(bonusProgress).length > 0 && (() => {
        const activeEntries = Object.entries(bonusProgress).filter(
          ([, b]) => b.status === 'in_progress' && b.wagering_requirement > 0
        );
        if (activeEntries.length === 0) return null;
        return (
          <Card title="Bonus Wagering">
            <div className="space-y-3">
              {activeEntries.map(([providerId, bonus]) => {
                const remaining = Math.max(0, bonus.wagering_requirement - bonus.wagered_amount);
                const pct = Math.min(100, bonus.progress_pct);
                const days = bonus.days_remaining;
                const urgent = days !== null && days <= 10;
                const warning = days !== null && days > 10 && days <= 30;

                return (
                  <div key={providerId} className="space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-text font-medium">{formatProviderName(providerId)}</span>
                      <div className="flex items-center gap-3">
                        <span className="text-muted">
                          {bonus.wagered_amount.toFixed(0)} / {bonus.wagering_requirement.toFixed(0)} kr
                        </span>
                        {days !== null && (
                          <span className={`font-mono ${urgent ? 'text-error' : warning ? 'text-amber-400' : 'text-success'}`}>
                            {days}d left
                          </span>
                        )}
                      </div>
                    </div>
                    {/* Progress bar */}
                    <div className="h-1.5 bg-panel rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          urgent ? 'bg-error' : warning ? 'bg-amber-400' : 'bg-tabBonus'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="flex items-center justify-between text-[10px] text-muted2">
                      <span>{pct.toFixed(0)}% wagered</span>
                      <span>
                        {remaining.toFixed(0)} kr remaining
                        {bonus.min_odds > 0 && ` · min odds ${bonus.min_odds.toFixed(2)}`}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        );
      })()}

      {/* Bonus Deposit Popup — shown when clicking +Bonus, lets user accept or decline */}
      {bonusPopup && (
        <BonusPopup
          title="Bonus Available"
          onClose={() => setBonusPopup(null)}
        >
          <div className="space-y-3">
            <div className="text-xs space-y-1.5">
              <div className="flex justify-between">
                <span className="text-muted">Deposit</span>
                <span className="text-text">{bonusPopup.amount.toFixed(0)} kr</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Bonus (matched)</span>
                <span className="text-tabBonus">+{bonusPopup.bonusAmount.toFixed(0)} kr</span>
              </div>
              <div className="flex justify-between border-t border-border pt-1.5 mt-1.5">
                <span className="text-muted">Total</span>
                <span className="text-text font-medium">
                  {(bonusPopup.amount + bonusPopup.bonusAmount).toFixed(0)} kr
                </span>
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => executeDeposit(bonusPopup.providerId, bonusPopup.amount, true)}
                className="flex-1 px-3 py-2 text-xs font-medium bg-tabBonus text-bg rounded hover:opacity-90 transition-opacity"
              >
                Accept Bonus
              </button>
              <button
                onClick={() => executeDeposit(bonusPopup.providerId, bonusPopup.amount, false)}
                className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted rounded hover:text-text transition-colors"
              >
                Decline
              </button>
            </div>
          </div>
        </BonusPopup>
      )}
    </div>
  );
}
