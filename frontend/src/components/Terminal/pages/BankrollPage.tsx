import { useState, useEffect, useCallback, useMemo } from 'react';
import { Card } from './Card';
import { BonusPopup } from '../BonusPopup';
import { SortableHeader } from '../SortableHeader';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useTableSort } from '@/hooks/useTableSort';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { BankrollExposure, Provider, ProviderExposure } from '@/types';

type BankrollSortCol = 'provider' | 'balance' | 'pending' | 'available' | 'withdraw';

const bankrollSortExtractors: Record<BankrollSortCol, (p: ProviderExposure) => number> = {
  provider: (p) => {
    const n = formatProviderName(p.provider_name).toLowerCase();
    return (n.charCodeAt(0) || 0) * 10000 + (n.charCodeAt(1) || 0) * 100 + (n.charCodeAt(2) || 0);
  },
  balance: (p) => p.total_balance,
  pending: (p) => p.pending_exposure,
  available: (p) => p.available,
  withdraw: (p) => p.is_locked ? 0 : p.available,
};

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
  // Bonus deposit popup state
  const [bonusPopup, setBonusPopup] = useState<{
    providerId: string;
    amount: number;
    bonusAmount: number;
  } | null>(null);
  // Freebet popup state
  const [freebetPopup, setFreebetPopup] = useState<{
    providerId: string;
    amount: number;
    freebetAmount: number;
    minOdds: number;
  } | null>(null);
  // Transfer popup state
  const [transferPopup, setTransferPopup] = useState<{
    fromProviderId: string;
    fromName: string;
    fromBalance: number;
  } | null>(null);
  const [transferAmount, setTransferAmount] = useState('');
  const [transferTo, setTransferTo] = useState('');

  // Two-step deposit: tracks which provider is awaiting deposit confirmation
  const [pendingDeposit, setPendingDeposit] = useState<{
    providerId: string;
    amount: number;
    withBonus: boolean;
    navUrl: string | null;
    windowName: string;
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

  // Format amount in SEK for a provider (converting non-SEK currencies)
  const fmtAmount = (providerId: string, amount: number) => {
    const prov = exposure?.providers.find(p => p.provider_id === providerId);
    if (prov?.currency && prov.currency !== 'SEK') {
      const sek = amount * (prov.exchange_rate_sek ?? 1);
      return `${sek.toFixed(0)} kr`;
    }
    return `${amount.toFixed(0)} kr`;
  };

  // Get provider bonus info from providers prop
  const getProviderBonus = (providerId: string) => {
    const provider = providers.find(p => p.id === providerId);
    if (!provider?.bonus) return null;
    return {
      bonus: provider.bonus,
      status: provider.bonus_status,
      hasUnclaimedBonus: !provider.bonus_status || provider.bonus_status === 'available',
    };
  };

  const handleDeposit = (providerId: string) => {
    const amount = parseFloat(adjustAmount);
    if (isNaN(amount) || amount <= 0) return;

    const bonusInfo = getProviderBonus(providerId);

    if (bonusInfo?.hasUnclaimedBonus && bonusInfo.bonus?.type === 'bonusdeposit') {
      const bonusAmount = Math.min(amount, bonusInfo.bonus.amount);
      setBonusPopup({ providerId, amount, bonusAmount });
    } else if (bonusInfo?.hasUnclaimedBonus && bonusInfo.bonus?.type === 'freebet') {
      setFreebetPopup({
        providerId,
        amount,
        freebetAmount: bonusInfo.bonus.amount,
        minOdds: bonusInfo.bonus.min_odds ?? 1.80,
      });
    } else {
      startDeposit(providerId, amount, false);
    }
  };

  // Step 1: Get deposit URL, enter pending state (user clicks Go↗ to navigate)
  const startDeposit = async (providerId: string, amount: number, withBonus: boolean) => {
    setBonusPopup(null);
    setFreebetPopup(null);

    setPendingDeposit({ providerId, amount, withBonus, navUrl: null, windowName: `bbq_${providerId}` });
  };

  // Step 2: Confirm deposit — record balance adjustment
  const confirmDeposit = async () => {
    if (!pendingDeposit) return;
    const { providerId, amount, withBonus } = pendingDeposit;

    try {
      if (withBonus) {
        const result = await api.depositWithBonus(providerId, amount);
        let msg = `Deposited ${fmtAmount(providerId, result.deposit)}`;
        if (result.bonus_claimed > 0) {
          msg += ` + ${fmtAmount(providerId, result.bonus_claimed)} bonus`;
        }
        if (result.bonus_type === 'freebet' && result.bonus_status === 'trigger_needed') {
          msg += `. Freebet activated — place trigger bet`;
        }
        msg += `. New balance: ${fmtAmount(providerId, result.new_balance)}`;
        setDepositResult({ success: true, message: msg });
      } else {
        await api.adjustBalance(providerId, amount);
        setDepositResult({
          success: true,
          message: `Deposited ${fmtAmount(providerId, amount)}`,
        });
      }
      setPendingDeposit(null);
      setAdjustingProvider(null);
      setAdjustAmount('');
      fetchData();
      onRefresh();
    } catch (err) {
      setDepositResult({
        success: false,
        message: err instanceof Error ? err.message : 'Operation failed',
      });
      setPendingDeposit(null);
    }
  };

  const handleWithdraw = async (providerId: string) => {
    const amount = parseFloat(adjustAmount);
    if (isNaN(amount) || amount <= 0) return;

    try {
      await api.adjustBalance(providerId, -amount);
      setDepositResult({
        success: true,
        message: `Withdrew ${fmtAmount(providerId, amount)}`,
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

  const handleSetBalance = async (providerId: string) => {
    const balance = parseFloat(adjustAmount);
    if (isNaN(balance) || balance < 0) return;

    try {
      const result = await api.setBalance(providerId, balance);
      setDepositResult({
        success: true,
        message: `Balance set to ${fmtAmount(providerId, balance)} (was ${fmtAmount(providerId, result.old_balance)})`,
      });
      setAdjustingProvider(null);
      setAdjustAmount('');
      fetchData();
      onRefresh();
    } catch (err) {
      setDepositResult({
        success: false,
        message: err instanceof Error ? err.message : 'Set balance failed',
      });
    }
  };

  const getTransferDestBonus = () => {
    if (!transferTo) return null;
    return getProviderBonus(transferTo);
  };

  const handleTransfer = async (withBonus = false) => {
    if (!transferPopup) return;
    const amount = parseFloat(transferAmount);
    if (isNaN(amount) || amount <= 0) return;
    if (!transferTo) return;

    try {
      const result = await api.transferFunds(transferPopup.fromProviderId, transferTo, amount, withBonus);
      const toName = exposure?.providers.find(p => p.provider_id === transferTo)?.provider_name || transferTo;
      let msg = `Transferred ${amount.toFixed(0)} kr from ${formatProviderName(transferPopup.fromName)} to ${formatProviderName(toName)}`;
      if (result.bonus_claimed > 0) {
        msg += ` + ${result.bonus_claimed.toFixed(0)} kr bonus`;
      }
      if (result.bonus_type === 'freebet' && result.bonus_status === 'trigger_needed') {
        msg += `. Freebet activated — place trigger bet`;
      }
      setDepositResult({ success: true, message: msg });
      setTransferPopup(null);
      setTransferAmount('');
      setTransferTo('');
      fetchData();
      onRefresh();
    } catch (err) {
      setDepositResult({
        success: false,
        message: err instanceof Error ? err.message : 'Transfer failed',
      });
      setTransferPopup(null);
      setTransferAmount('');
      setTransferTo('');
    }
  };

  // Sort provider balances table — default by bonus priority (freebet > bonusdeposit > none)
  const providerList = useMemo(() => exposure?.providers ?? [], [exposure]);
  const { sorted: tableSorted, sort: provSort, toggle: toggleProvSort } =
    useTableSort<ProviderExposure, BankrollSortCol>(providerList, bankrollSortExtractors, { column: 'balance', direction: 'desc' });

  // When no column sort is active, sort by bonus priority: freebet first, then bonusdeposit, then rest
  const sortedProviders = useMemo(() => {
    if (provSort.column !== null) return tableSorted;
    return [...tableSorted].sort((a, b) => {
      const bonusA = getProviderBonus(a.provider_id);
      const bonusB = getProviderBonus(b.provider_id);
      const rankOf = (info: ReturnType<typeof getProviderBonus>) => {
        if (!info?.hasUnclaimedBonus || !info.bonus) return 2;
        if (info.bonus.type === 'freebet') return 0;
        if (info.bonus.type === 'bonusdeposit') return 1;
        return 2;
      };
      return rankOf(bonusA) - rankOf(bonusB);
    });
  }, [tableSorted, provSort.column, providers]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="bankroll" color={TAB_COLORS.bankroll} size={16} />
          Bankroll
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabBankroll" />
        Bankroll
      </h2>

      {/* Deposit Result Message */}
      {depositResult && (
        <div className={`text-sm p-3 ${depositResult.success ? 'bg-success/10 text-success border border-success/20' : 'bg-error/10 text-error border border-error/20'}`}>
          {depositResult.message}
        </div>
      )}

      {/* Overview */}
      {exposure && (
        <div className="border-l-2 border-tabBankroll">
        <Card title="Overview">
          <table className="sq">
            <thead>
              <tr>
                <th>Total Balance</th>
                <th className="text-right">Pending</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="text-text text-xl font-semibold">{exposure.total_balance.toFixed(0)} kr</td>
                <td className="text-right text-tabBets text-xl font-semibold">{exposure.total_pending.toFixed(0)} kr</td>
              </tr>
            </tbody>
          </table>
        </Card>
        </div>
      )}

      {/* Provider Balances */}
      {exposure && (
        <div className="border-l-2 border-tabBankroll">
        <Card title="Provider Balances">
          <table className="sq">
            <thead>
              <tr>
                <SortableHeader column="provider" label="Provider" sort={provSort} onToggle={toggleProvSort} align="left" />
                <SortableHeader column="balance" label="Balance" sort={provSort} onToggle={toggleProvSort} />
                <SortableHeader column="pending" label="Pending" sort={provSort} onToggle={toggleProvSort} />
                <SortableHeader column="available" label="Available" sort={provSort} onToggle={toggleProvSort} />
                <SortableHeader column="withdraw" label="Withdraw" sort={provSort} onToggle={toggleProvSort} />
                <th className="text-right"></th>
              </tr>
            </thead>
            <tbody>
              {sortedProviders.map(provider => {
                const isAdjusting = adjustingProvider === provider.provider_id;

                return (
                  <tr key={provider.provider_id}>
                    <td className="text-text">
                      {formatProviderName(provider.provider_name)}
                      {(() => {
                        const bonus = getProviderBonus(provider.provider_id);
                        if (!bonus?.hasUnclaimedBonus || !bonus.bonus) return null;
                        const tag = bonus.bonus.type === 'freebet' ? 'f' : 'd';
                        return (
                          <button
                            className="ml-1.5 inline-flex items-center justify-center w-3.5 h-3.5 text-[9px] font-bold bg-tabBonus/20 text-tabBonus hover:bg-tabBonus/40 transition-colors cursor-pointer"
                            title={`Click to mark as claimed · ${bonus.bonus.type === 'freebet'
                              ? `Freebet ${bonus.bonus.amount} kr`
                              : `Bonus deposit up to ${bonus.bonus.amount} kr`}`}
                            onClick={async (e) => {
                              e.stopPropagation();
                              try {
                                await api.claimBonus(provider.provider_id);
                                onRefresh();
                              } catch (err) {
                                setDepositResult({ success: false, message: err instanceof Error ? err.message : 'Failed to claim bonus' });
                              }
                            }}
                          >
                            {tag}
                          </button>
                        );
                      })()}
                    </td>
                    <td className="text-right text-text">
                      <div>
                        {provider.currency && provider.currency !== 'SEK' ? (
                          <span title={`$${provider.total_balance.toFixed(2)}`}>
                            {provider.balance_sek?.toFixed(0) ?? '?'} kr
                            <span className="text-muted2 text-[10px] ml-1">(${provider.total_balance.toFixed(2)})</span>
                          </span>
                        ) : (
                          <>{provider.total_balance.toFixed(0)} kr</>
                        )}
                      </div>
                    </td>
                    <td className="text-right text-muted">
                      {provider.pending_exposure.toFixed(0)} kr
                      {provider.pending_bets_count > 0 && (
                        <span className="text-xs ml-1">({provider.pending_bets_count})</span>
                      )}
                    </td>
                    <td className="text-right text-success">
                      {provider.currency && provider.currency !== 'SEK' ? (
                        <span>
                          {(provider.available * (provider.exchange_rate_sek ?? 1)).toFixed(0)} kr
                          <span className="text-muted2 text-[10px] ml-1">(${provider.available.toFixed(2)})</span>
                        </span>
                      ) : (
                        <>{provider.available.toFixed(0)} kr</>
                      )}
                    </td>
                    <td className="text-right">
                      {provider.is_locked ? (
                        <span className="text-muted2">0 kr</span>
                      ) : provider.available > 0 ? (
                        <span className="text-success">
                          {provider.currency && provider.currency !== 'SEK'
                            ? `${(provider.available * (provider.exchange_rate_sek ?? 1)).toFixed(0)} kr`
                            : `${provider.available.toFixed(0)} kr`
                          }
                        </span>
                      ) : (
                        <span className="text-muted2">0 kr</span>
                      )}
                    </td>
                    <td className="text-right">
                      {pendingDeposit?.providerId === provider.provider_id ? (
                        <div className="flex items-center justify-end gap-2">
                          <span className="text-xs text-muted">
                            {provider.currency && provider.currency !== 'SEK'
                              ? `${(pendingDeposit.amount * (provider.exchange_rate_sek ?? 1)).toFixed(0)} kr ($${pendingDeposit.amount.toFixed(2)})`
                              : `${pendingDeposit.amount.toFixed(0)} kr`
                            }{pendingDeposit.withBonus ? ' + bonus' : ''}
                          </span>
                          <button
                            onClick={confirmDeposit}
                            className="px-3 py-1 text-xs bg-success text-bg font-medium hover:opacity-90"
                          >
                            Confirm
                          </button>
                          <button
                            onClick={() => { setPendingDeposit(null); setAdjustingProvider(null); setAdjustAmount(''); }}
                            className="px-2 py-1 text-xs text-muted hover:text-text"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : isAdjusting ? (
                        <div className="space-y-1">
                          <div className="flex items-center justify-end gap-2">
                            <input
                              type="number"
                              value={adjustAmount}
                              onChange={(e) => setAdjustAmount(e.target.value)}
                              placeholder={provider.currency && provider.currency !== 'SEK' ? `$${provider.total_balance.toFixed(2)}` : `${provider.total_balance.toFixed(0)}`}
                              step={provider.currency && provider.currency !== 'SEK' ? '0.01' : '1'}
                              className="w-20 px-2 py-1 bg-panel2 border border-border text-text text-xs"
                              autoFocus
                            />
                            <button
                              onClick={() => handleDeposit(provider.provider_id)}
                              className="px-2 py-1 text-xs bg-success/20 text-success hover:bg-success/30"
                              title="Deposit (add amount)"
                            >
                              +
                            </button>
                            <button
                              onClick={() => handleWithdraw(provider.provider_id)}
                              className="px-2 py-1 text-xs bg-error/20 text-error hover:bg-error/30"
                              title="Withdraw (subtract amount)"
                            >
                              -
                            </button>
                            <button
                              onClick={() => handleSetBalance(provider.provider_id)}
                              className="px-2 py-1 text-xs bg-tabBankroll/20 text-tabBankroll hover:bg-tabBankroll/30"
                              title="Set exact balance"
                            >
                              =
                            </button>
                            <button
                              onClick={() => { setAdjustingProvider(null); setAdjustAmount(''); }}
                              className="px-2 py-1 text-xs text-muted hover:text-text"
                            >
                              Cancel
                            </button>
                          </div>
                          {(() => {
                            const amt = parseFloat(adjustAmount);
                            const bonus = getProviderBonus(provider.provider_id);
                            if (!amt || amt <= 0 || !bonus?.hasUnclaimedBonus || !bonus.bonus) return null;
                            if (bonus.bonus.type === 'bonusdeposit') {
                              const matched = Math.min(amt, bonus.bonus.amount);
                              return (
                                <div className="text-[10px] text-tabBonus text-right">
                                  +{matched.toFixed(0)} kr bonus · total {(amt + matched).toFixed(0)} kr
                                </div>
                              );
                            }
                            if (bonus.bonus.type === 'freebet') {
                              return (
                                <div className="text-[10px] text-tabBonus text-right">
                                  +{bonus.bonus.amount} kr freebet · {bonus.bonus.min_odds ?? 1.80}+ odds trigger
                                </div>
                              );
                            }
                            return null;
                          })()}
                        </div>
                      ) : (
                        <div className="flex gap-1 justify-end">
                          <button
                            onClick={() => setAdjustingProvider(provider.provider_id)}
                            className="px-2 py-1 text-xs text-tabBankroll hover:opacity-80"
                          >
                            Adjust
                          </button>
                          {provider.available > 0 && (
                            <button
                              onClick={() => setTransferPopup({
                                fromProviderId: provider.provider_id,
                                fromName: provider.provider_name,
                                fromBalance: provider.available,
                              })}
                              className="px-2 py-1 text-xs text-muted hover:text-text"
                            >
                              Transfer
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
        </div>
      )}

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
                onClick={() => startDeposit(bonusPopup.providerId, bonusPopup.amount, true)}
                className="flex-1 px-3 py-2 text-xs font-medium bg-tabBonus text-bg hover:opacity-90 transition-opacity"
              >
                Accept Bonus
              </button>
              <button
                onClick={() => startDeposit(bonusPopup.providerId, bonusPopup.amount, false)}
                className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted hover:text-text transition-colors"
              >
                Decline
              </button>
            </div>
          </div>
        </BonusPopup>
      )}

      {/* Transfer Popup */}
      {transferPopup && (
        <BonusPopup
          title={`Transfer from ${formatProviderName(transferPopup.fromName)}`}
          onClose={() => { setTransferPopup(null); setTransferAmount(''); setTransferTo(''); }}
        >
          <div className="space-y-3">
            <div className="text-xs space-y-1.5">
              <div className="flex justify-between">
                <span className="text-muted">Available</span>
                <span className="text-text">{transferPopup.fromBalance.toFixed(0)} kr</span>
              </div>
            </div>
            <div className="space-y-2">
              <input
                type="number"
                value={transferAmount}
                onChange={(e) => setTransferAmount(e.target.value)}
                placeholder="Amount"
                className="w-full px-3 py-2 bg-panel2 border border-border text-text text-xs"
                autoFocus
              />
              <select
                value={transferTo}
                onChange={(e) => setTransferTo(e.target.value)}
                className="w-full px-3 py-2 bg-panel2 border border-border text-text text-xs"
              >
                <option value="">Select destination...</option>
                {exposure?.providers
                  .filter(p => p.provider_id !== transferPopup.fromProviderId)
                  .map(p => (
                    <option key={p.provider_id} value={p.provider_id}>
                      {formatProviderName(p.provider_name)} ({p.total_balance.toFixed(0)} kr)
                    </option>
                  ))
                }
              </select>
            </div>
            {transferAmount && parseFloat(transferAmount) > transferPopup.fromBalance && (
              <div className="text-[10px] text-error">
                Exceeds available balance
              </div>
            )}
            {/* Show bonus info when destination has unclaimed bonus */}
            {(() => {
              const destBonus = getTransferDestBonus();
              const amt = parseFloat(transferAmount);
              if (!destBonus?.hasUnclaimedBonus || !destBonus.bonus || !amt || amt <= 0) return null;
              if (destBonus.bonus.type === 'bonusdeposit') {
                const matched = Math.min(amt, destBonus.bonus.amount);
                return (
                  <div className="text-[10px] text-tabBonus border border-tabBonus/20 bg-tabBonus/5 p-2 space-y-0.5">
                    <div>+{matched.toFixed(0)} kr bonus available (matched deposit)</div>
                    <div className="text-muted">Total to {formatProviderName(exposure?.providers.find(p => p.provider_id === transferTo)?.provider_name || transferTo)}: {(amt + matched).toFixed(0)} kr</div>
                  </div>
                );
              }
              if (destBonus.bonus.type === 'freebet') {
                return (
                  <div className="text-[10px] text-tabBonus border border-tabBonus/20 bg-tabBonus/5 p-2 space-y-0.5">
                    <div>+{destBonus.bonus.amount} kr freebet available</div>
                    <div className="text-muted">Trigger: {destBonus.bonus.amount} kr @ {destBonus.bonus.min_odds ?? 1.80}+ odds</div>
                  </div>
                );
              }
              return null;
            })()}
            {(() => {
              const destBonus = getTransferDestBonus();
              const amt = parseFloat(transferAmount);
              const isValid = transferTo && amt > 0 && amt <= transferPopup.fromBalance;
              const hasBonus = destBonus?.hasUnclaimedBonus && destBonus.bonus;

              if (hasBonus) {
                return (
                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={() => handleTransfer(true)}
                      disabled={!isValid}
                      className="flex-1 px-3 py-2 text-xs font-medium bg-tabBonus text-bg hover:opacity-90 transition-opacity disabled:opacity-40"
                    >
                      Transfer + Bonus
                    </button>
                    <button
                      onClick={() => handleTransfer(false)}
                      disabled={!isValid}
                      className="flex-1 px-3 py-2 text-xs font-medium bg-tabBankroll text-bg hover:opacity-90 transition-opacity disabled:opacity-40"
                    >
                      Transfer Only
                    </button>
                  </div>
                );
              }

              return (
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => handleTransfer(false)}
                    disabled={!isValid}
                    className="flex-1 px-3 py-2 text-xs font-medium bg-tabBankroll text-bg hover:opacity-90 transition-opacity disabled:opacity-40"
                  >
                    Transfer
                  </button>
                  <button
                    onClick={() => { setTransferPopup(null); setTransferAmount(''); setTransferTo(''); }}
                    className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted hover:text-text transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              );
            })()}
          </div>
        </BonusPopup>
      )}

      {/* Freebet Popup — shown when depositing to a freebet provider */}
      {freebetPopup && (
        <BonusPopup
          title="Freebet Available"
          onClose={() => setFreebetPopup(null)}
        >
          <div className="space-y-3">
            <div className="text-xs space-y-1.5">
              <div className="flex justify-between">
                <span className="text-muted">Deposit</span>
                <span className="text-text">{freebetPopup.amount.toFixed(0)} kr</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Freebet value</span>
                <span className="text-tabBonus">+{freebetPopup.freebetAmount.toFixed(0)} kr</span>
              </div>
              <div className="flex justify-between border-t border-border pt-1.5 mt-1.5">
                <span className="text-muted">Trigger bet required</span>
                <span className="text-text font-medium">
                  {freebetPopup.freebetAmount.toFixed(0)} kr @ {freebetPopup.minOdds}+
                </span>
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => startDeposit(freebetPopup.providerId, freebetPopup.amount, true)}
                className="flex-1 px-3 py-2 text-xs font-medium bg-tabBonus text-bg hover:opacity-90 transition-opacity"
              >
                Activate Freebet
              </button>
              <button
                onClick={() => startDeposit(freebetPopup.providerId, freebetPopup.amount, false)}
                className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted hover:text-text transition-colors"
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
