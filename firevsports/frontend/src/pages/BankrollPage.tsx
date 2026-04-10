import { useState, useMemo } from 'react';
import { Card } from '@/components/Card';
import { BonusPopup } from '@/components/BonusPopup';
import { SortableHeader } from '@/components/SortableHeader';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { ProviderName } from '@/components/ProviderName';
import { useTableSort } from '@/hooks/useTableSort';
import { TabIcon, TAB_COLORS } from '@/components/TabBar';
import type { ProviderExposure, AllocationRecommendation } from '@/types';
import { useBankrollQuery } from '@/hooks/useBankrollQuery';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToast, ToastContainer } from '@/components/Toast';

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

const PRIORITY_LABELS: Record<number, { label: string; color: string }> = {
  0: { label: 'WITHDRAWALS', color: 'text-muted' },
  1: { label: 'BONUS DEPOSITS', color: 'text-tabBonus' },
  2: { label: 'WAGERING TOP-UP', color: 'text-tabBets' },
  3: { label: 'VALUE COVERAGE', color: 'text-tabValue' },
  4: { label: 'SPREAD', color: 'text-muted' },
};

export function BankrollPage() {
  const queryClient = useQueryClient();
  const { exposure, allocate, liquidBalance, setBalance, depositWithBonus, isLoading } = useBankrollQuery();
  const { data: providersData } = useQuery({ queryKey: ['providers'], queryFn: () => api.getProviders() });
  const providers = providersData?.providers ?? [];

  const [liquidInput, setLiquidInput] = useState('');
  const [allocations, setAllocations] = useState<AllocationRecommendation[] | null>(null);
  const [confirmedProviders, setConfirmedProviders] = useState<Set<string>>(new Set());
  const { toasts, addToast, dismissToast } = useToast();

  // Set balance inline state
  const [settingProvider, setSettingProvider] = useState<string | null>(null);
  const [setBalanceInput, setSetBalanceInput] = useState('');

  // Bonus popup for allocation confirmation
  const [bonusPopup, setBonusPopup] = useState<{
    providerId: string;
    amount: number;
    bonusAmount: number;
  } | null>(null);

  const fmtAmount = (providerId: string, amount: number) => {
    const prov = exposure?.providers.find(p => p.provider_id === providerId);
    if (prov?.currency && prov.currency !== 'SEK') {
      const sek = amount * (prov.exchange_rate_sek ?? 1);
      return `${sek.toFixed(0)} kr`;
    }
    return `${amount.toFixed(0)} kr`;
  };

  const getProviderBonus = (providerId: string) => {
    const provider = providers.find(p => p.id === providerId);
    if (!provider?.bonus) return null;
    return {
      bonus: provider.bonus,
      status: provider.bonus_status,
      hasUnclaimedBonus: !provider.bonus_status || provider.bonus_status === 'available',
    };
  };

  const handleAllocate = async () => {
    const amount = parseFloat(liquidInput);
    if (isNaN(amount) || amount <= 0) return;
    try {
      const result = await allocate.mutateAsync(amount);
      setAllocations(result.recommendations);
      setConfirmedProviders(new Set());
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Allocation failed', 'error');
    }
  };

  const handleConfirmAllocation = async (rec: AllocationRecommendation) => {
    if (rec.action === 'withdraw') return; // Withdrawals are informational only

    const bonusInfo = getProviderBonus(rec.provider_id);
    if (rec.bonus_type && bonusInfo?.hasUnclaimedBonus) {
      // Deposit with bonus
      try {
        const result = await depositWithBonus.mutateAsync({ providerId: rec.provider_id, amount: rec.amount });
        let msg = `Deposited ${fmtAmount(rec.provider_id, result.deposit)}`;
        if (result.bonus_claimed > 0) msg += ` + ${fmtAmount(rec.provider_id, result.bonus_claimed)} bonus`;
        addToast(msg, 'success');
        setConfirmedProviders(prev => new Set(prev).add(rec.provider_id));
      } catch (err) {
        addToast(err instanceof Error ? err.message : 'Deposit failed', 'error');
      }
    } else {
      // Regular deposit via set balance (add to existing)
      const prov = exposure?.providers.find(p => p.provider_id === rec.provider_id);
      const currentBalance = prov?.total_balance ?? 0;
      try {
        await setBalance.mutateAsync({ providerId: rec.provider_id, balance: currentBalance + rec.amount });
        addToast(`Deposited ${fmtAmount(rec.provider_id, rec.amount)} to ${formatProviderName(rec.provider_name)}`, 'success');
        setConfirmedProviders(prev => new Set(prev).add(rec.provider_id));
      } catch (err) {
        addToast(err instanceof Error ? err.message : 'Deposit failed', 'error');
      }
    }
  };

  const handleSetBalance = async (providerId: string) => {
    const balance = parseFloat(setBalanceInput);
    if (isNaN(balance) || balance < 0) return;
    try {
      const result = await setBalance.mutateAsync({ providerId, balance });
      addToast(`Balance set to ${fmtAmount(providerId, balance)} (was ${fmtAmount(providerId, result.old_balance)})`, 'success');
      setSettingProvider(null);
      setSetBalanceInput('');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Set balance failed', 'error');
    }
  };

  // Group allocations by priority
  const groupedAllocations = useMemo(() => {
    if (!allocations) return null;
    const groups = new Map<number, AllocationRecommendation[]>();
    for (const rec of allocations) {
      const group = groups.get(rec.priority) || [];
      group.push(rec);
      groups.set(rec.priority, group);
    }
    // Sort: deposits (1,2,3,4) first, then withdrawals (0)
    return Array.from(groups.entries()).sort(([a], [b]) => {
      if (a === 0) return 1;
      if (b === 0) return -1;
      return a - b;
    });
  }, [allocations]);

  const totalDeployed = useMemo(() => {
    if (!allocations) return 0;
    return allocations
      .filter(r => r.action === 'deposit')
      .reduce((sum, r) => sum + r.amount_sek, 0);
  }, [allocations]);

  const totalEV = useMemo(() => {
    if (!allocations) return 0;
    return allocations
      .filter(r => r.action === 'deposit')
      .reduce((sum, r) => sum + r.expected_ev, 0);
  }, [allocations]);

  // Provider table sorting
  const providerList = useMemo(() => exposure?.providers ?? [], [exposure]);
  const { sorted: tableSorted, sort: provSort, toggle: toggleProvSort } =
    useTableSort<ProviderExposure, BankrollSortCol>(providerList, bankrollSortExtractors, { column: 'balance', direction: 'desc' }, 'bbq_bankroll_sort');

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
      <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="bankroll" color={TAB_COLORS.bankroll} size={16} />
          Bankroll
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabBankroll" />
        Bankroll
      </h2>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

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

      {/* Fund Allocator */}
      <div className="border-l-2 border-tabBankroll">
        <Card title="Fund Allocator">
          <div className="flex items-center gap-3 mb-3">
            <label className="text-xs text-muted whitespace-nowrap">Liquid balance</label>
            <input
              type="number"
              value={liquidInput}
              onChange={(e) => setLiquidInput(e.target.value)}
              placeholder={liquidBalance > 0 ? liquidBalance.toFixed(0) : '0'}
              className="w-28 px-3 py-1.5 bg-panel2 border border-border text-text text-sm"
              onKeyDown={(e) => e.key === 'Enter' && handleAllocate()}
            />
            <span className="text-xs text-muted">kr</span>
            <button
              onClick={handleAllocate}
              disabled={allocate.isPending}
              className="px-4 py-1.5 text-xs font-medium bg-tabBankroll text-bg hover:opacity-90 disabled:opacity-40"
            >
              {allocate.isPending ? 'Allocating...' : 'Allocate'}
            </button>
          </div>

          {/* Allocation Results */}
          {groupedAllocations && groupedAllocations.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs text-muted border-b border-border pb-1">
                <span>ALLOCATION PLAN — {totalDeployed.toFixed(0)} kr deployed</span>
                <span className="text-success">Total EV: +{totalEV.toFixed(0)} kr</span>
              </div>

              {groupedAllocations.map(([priority, recs]) => {
                const meta = PRIORITY_LABELS[priority] || { label: `P${priority}`, color: 'text-muted' };
                return (
                  <div key={priority}>
                    <div className={`text-[10px] font-bold tracking-wider mb-1 ${meta.color}`}>
                      [{priority}] {meta.label}
                    </div>
                    <table className="sq">
                      <tbody>
                        {recs.map((rec) => {
                          const isConfirmed = confirmedProviders.has(rec.provider_id);
                          return (
                            <tr key={rec.provider_id} className={isConfirmed ? 'opacity-40' : ''}>
                              <td className="text-text w-32">
                                <ProviderName name={rec.provider_name} />
                              </td>
                              <td className={`text-xs w-16 ${rec.action === 'withdraw' ? 'text-error' : 'text-success'}`}>
                                {rec.action}
                              </td>
                              <td className="text-right text-text text-xs w-20">
                                {rec.amount_sek.toFixed(0)} kr
                              </td>
                              <td className="text-xs text-muted px-3 truncate max-w-[200px]" title={rec.reason}>
                                {rec.reason}
                              </td>
                              <td className="text-right text-xs w-20">
                                {rec.expected_ev > 0 && (
                                  <span className="text-success">+{rec.expected_ev.toFixed(0)} kr</span>
                                )}
                              </td>
                              <td className="text-right w-20">
                                {rec.action === 'deposit' && !isConfirmed && (
                                  <button
                                    onClick={() => handleConfirmAllocation(rec)}
                                    className="px-2 py-0.5 text-[10px] font-medium bg-success/20 text-success hover:bg-success/30"
                                  >
                                    Confirm
                                  </button>
                                )}
                                {isConfirmed && (
                                  <span className="text-[10px] text-success">Done</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                );
              })}
            </div>
          )}

          {allocations && allocations.length === 0 && (
            <div className="text-xs text-muted py-2">No allocation recommendations — no active opportunities or bonuses.</div>
          )}
        </Card>
      </div>

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
                {sortedProviders.map(provider => (
                  <tr key={provider.provider_id}>
                    <td className="text-text">
                      <ProviderName name={provider.provider_name} />
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
                                queryClient.invalidateQueries({ queryKey: ['providers'] });
                                queryClient.invalidateQueries({ queryKey: ['bankroll'] });
                              } catch (err) {
                                addToast(err instanceof Error ? err.message : 'Failed to claim bonus', 'error');
                              }
                            }}
                          >
                            {tag}
                          </button>
                        );
                      })()}
                    </td>
                    <td className="text-right text-text">
                      {provider.currency && provider.currency !== 'SEK' ? (
                        <span title={`$${provider.total_balance.toFixed(2)}`}>
                          {provider.balance_sek?.toFixed(0) ?? '?'} kr
                          <span className="text-muted2 text-[10px] ml-1">(${provider.total_balance.toFixed(2)})</span>
                        </span>
                      ) : (
                        <>{provider.total_balance.toFixed(0)} kr</>
                      )}
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
                      {settingProvider === provider.provider_id ? (
                        <div className="flex items-center justify-end gap-1">
                          <input
                            type="number"
                            value={setBalanceInput}
                            onChange={(e) => setSetBalanceInput(e.target.value)}
                            placeholder={provider.total_balance.toFixed(0)}
                            className="w-20 px-2 py-1 bg-panel2 border border-border text-text text-xs"
                            autoFocus
                            onKeyDown={(e) => e.key === 'Enter' && handleSetBalance(provider.provider_id)}
                          />
                          <button
                            onClick={() => handleSetBalance(provider.provider_id)}
                            className="px-2 py-1 text-xs bg-tabBankroll/20 text-tabBankroll hover:bg-tabBankroll/30"
                          >
                            =
                          </button>
                          <button
                            onClick={() => { setSettingProvider(null); setSetBalanceInput(''); }}
                            className="px-1 py-1 text-xs text-muted hover:text-text"
                          >
                            x
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setSettingProvider(provider.provider_id)}
                          className="px-2 py-1 text-xs text-tabBankroll hover:opacity-80"
                        >
                          Set
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}

      {/* Bonus Deposit Popup */}
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
                onClick={async () => {
                  setBonusPopup(null);
                  try {
                    const result = await depositWithBonus.mutateAsync({ providerId: bonusPopup.providerId, amount: bonusPopup.amount });
                    addToast(`Deposited ${bonusPopup.amount.toFixed(0)} kr + ${result.bonus_claimed.toFixed(0)} kr bonus`, 'success');
                    setConfirmedProviders(prev => new Set(prev).add(bonusPopup.providerId));
                  } catch (err) {
                    addToast(err instanceof Error ? err.message : 'Deposit failed', 'error');
                  }
                }}
                className="flex-1 px-3 py-2 text-xs font-medium bg-tabBonus text-bg hover:opacity-90 transition-opacity"
              >
                Accept Bonus
              </button>
              <button
                onClick={() => setBonusPopup(null)}
                className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted hover:text-text transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </BonusPopup>
      )}
    </div>
  );
}
