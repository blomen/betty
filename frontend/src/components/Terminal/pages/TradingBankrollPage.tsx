import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { TradingAccount } from '@/types/trading';

export function TradingBankrollPage() {
  const queryClient = useQueryClient();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [adjustId, setAdjustId] = useState<number | null>(null);
  const [adjustAmount, setAdjustAmount] = useState('');

  const { data: accountsData, isLoading } = useQuery({
    queryKey: ['trading-accounts'],
    queryFn: () => api.getTradingAccounts(),
  });
  const accounts = accountsData?.accounts ?? [];

  const adjustMutation = useMutation({
    mutationFn: ({ id, amount }: { id: number; amount: number }) => api.adjustTradingBalance(id, amount),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['trading-accounts'] });
      setAdjustId(null);
      setAdjustAmount('');
    },
  });

  const resetDailyMutation = useMutation({
    mutationFn: (id: number) => api.resetTradingDaily(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['trading-accounts'] }),
  });

  const resetWeeklyMutation = useMutation({
    mutationFn: (id: number) => api.resetTradingWeekly(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['trading-accounts'] }),
  });

  const savePolicyMutation = useMutation({
    mutationFn: ({ id, updates }: { id: number; updates: Record<string, number> }) => api.updateTradingAccount(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['trading-accounts'] });
      setEditingId(null);
    },
  });

  const handleAdjust = (id: number) => {
    const amt = parseFloat(adjustAmount);
    if (isNaN(amt)) return;
    adjustMutation.mutate({ id, amount: amt });
  };

  const handleResetDaily = (id: number) => resetDailyMutation.mutate(id);
  const handleResetWeekly = (id: number) => resetWeeklyMutation.mutate(id);

  const handleSavePolicy = (acct: TradingAccount, field: string, value: string) => {
    const num = parseFloat(value);
    if (isNaN(num)) return;
    savePolicyMutation.mutate({ id: acct.id, updates: { [field]: field.includes('max_trades') || field.includes('stop_after') ? Math.floor(num) : num } });
  };

  const totals = accounts.reduce(
    (acc, a) => ({
      balance: acc.balance + a.balance,
      equity: acc.equity + a.equity,
      realized_pnl: acc.realized_pnl + a.realized_pnl,
      daily_pnl: acc.daily_pnl + a.daily_pnl,
      weekly_pnl: acc.weekly_pnl + a.weekly_pnl,
    }),
    { balance: 0, equity: 0, realized_pnl: 0, daily_pnl: 0, weekly_pnl: 0 }
  );

  const pnlColor = (v: number) => v > 0 ? 'text-success' : v < 0 ? 'text-error' : 'text-muted';
  const fmt = (v: number) => `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  if (isLoading) return <div className="text-muted text-sm">Loading accounts...</div>;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <TabIcon name="tradingBankroll" color={TAB_COLORS.tradingBankroll} />
        Trading Bankroll
      </h2>

      {/* Summary row */}
      <div className="grid grid-cols-5 gap-px bg-border rounded overflow-hidden">
        {[
          { label: 'Total Balance', value: fmt(totals.balance) },
          { label: 'Equity', value: fmt(totals.equity) },
          { label: 'Realized P&L', value: fmt(totals.realized_pnl), color: pnlColor(totals.realized_pnl) },
          { label: 'Daily P&L', value: fmt(totals.daily_pnl), color: pnlColor(totals.daily_pnl) },
          { label: 'Weekly P&L', value: fmt(totals.weekly_pnl), color: pnlColor(totals.weekly_pnl) },
        ].map(item => (
          <div key={item.label} className="bg-panel p-3 text-center">
            <div className="text-xs text-muted mb-1">{item.label}</div>
            <div className={`text-sm font-mono ${item.color || 'text-text'}`}>{item.value}</div>
          </div>
        ))}
      </div>

      {/* Account cards */}
      <div className="space-y-3">
        {accounts.map(acct => (
          <div key={acct.id} className="border border-border bg-panel rounded p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-text font-semibold">{acct.name}</span>
                <span className="text-xs text-muted bg-panel2 px-2 py-0.5 rounded">{acct.account_type}</span>
                {acct.is_daily_locked && <span className="text-xs text-error bg-error/10 px-2 py-0.5 rounded">Daily Locked</span>}
                {acct.is_weekly_locked && <span className="text-xs text-error bg-error/10 px-2 py-0.5 rounded">Weekly Locked</span>}
              </div>
              <div className="flex gap-2">
                <button onClick={() => setAdjustId(adjustId === acct.id ? null : acct.id)} className="text-xs text-muted hover:text-text px-2 py-1 border border-border rounded">
                  {adjustId === acct.id ? 'Cancel' : 'Adjust'}
                </button>
                <button onClick={() => setEditingId(editingId === acct.id ? null : acct.id)} className="text-xs text-muted hover:text-text px-2 py-1 border border-border rounded">
                  {editingId === acct.id ? 'Done' : 'Edit Policy'}
                </button>
              </div>
            </div>

            {/* Balances */}
            <div className="grid grid-cols-5 gap-3 mb-3">
              <div>
                <div className="text-xs text-muted">Balance</div>
                <div className="text-sm font-mono text-text">{fmt(acct.balance)}</div>
              </div>
              <div>
                <div className="text-xs text-muted">Equity</div>
                <div className="text-sm font-mono text-text">{fmt(acct.equity)}</div>
              </div>
              <div>
                <div className="text-xs text-muted">Realized</div>
                <div className={`text-sm font-mono ${pnlColor(acct.realized_pnl)}`}>{fmt(acct.realized_pnl)}</div>
              </div>
              <div>
                <div className="text-xs text-muted">Daily</div>
                <div className={`text-sm font-mono ${pnlColor(acct.daily_pnl)}`}>{fmt(acct.daily_pnl)}</div>
              </div>
              <div>
                <div className="text-xs text-muted">Weekly</div>
                <div className={`text-sm font-mono ${pnlColor(acct.weekly_pnl)}`}>{fmt(acct.weekly_pnl)}</div>
              </div>
            </div>

            {/* Drawdown bars */}
            <div className="flex gap-4 mb-3">
              <div className="flex-1">
                <div className="text-xs text-muted mb-1">Daily DD ({acct.balance > 0 ? (Math.abs(acct.daily_pnl) / acct.balance * 100).toFixed(1) : '0'}% / {acct.max_daily_loss_pct}%)</div>
                <div className="h-1.5 bg-panel2 rounded-full overflow-hidden">
                  <div className="h-full bg-error rounded-full transition-all" style={{ width: `${Math.min(100, acct.balance > 0 ? Math.abs(acct.daily_pnl) / acct.balance * 100 / acct.max_daily_loss_pct * 100 : 0)}%` }} />
                </div>
              </div>
              <div className="flex-1">
                <div className="text-xs text-muted mb-1">Weekly DD ({acct.balance > 0 ? (Math.abs(acct.weekly_pnl) / acct.balance * 100).toFixed(1) : '0'}% / {acct.max_weekly_loss_pct}%)</div>
                <div className="h-1.5 bg-panel2 rounded-full overflow-hidden">
                  <div className="h-full bg-warning rounded-full transition-all" style={{ width: `${Math.min(100, acct.balance > 0 ? Math.abs(acct.weekly_pnl) / acct.balance * 100 / acct.max_weekly_loss_pct * 100 : 0)}%` }} />
                </div>
              </div>
            </div>

            {/* Counters */}
            <div className="flex gap-4 text-xs text-muted">
              <span>Trades today: {acct.trades_today}/{acct.max_trades_per_day}</span>
              <span>Consecutive losses: {acct.consecutive_losses}/{acct.stop_after_consecutive_losses}</span>
              <button onClick={() => handleResetDaily(acct.id)} className="text-tabTradingBankroll hover:underline">Reset Daily</button>
              <button onClick={() => handleResetWeekly(acct.id)} className="text-tabTradingBankroll hover:underline">Reset Weekly</button>
            </div>

            {/* Adjust balance inline */}
            {adjustId === acct.id && (
              <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                <input
                  type="number"
                  value={adjustAmount}
                  onChange={e => setAdjustAmount(e.target.value)}
                  placeholder="Amount (+/-)"
                  className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-32 font-mono"
                />
                <button onClick={() => handleAdjust(acct.id)} className="text-xs bg-tabTradingBankroll/20 text-tabTradingBankroll px-3 py-1 rounded hover:bg-tabTradingBankroll/30">
                  Apply
                </button>
              </div>
            )}

            {/* Edit risk policy inline */}
            {editingId === acct.id && (
              <div className="mt-3 pt-3 border-t border-border grid grid-cols-3 gap-3">
                {[
                  { label: 'Risk/Trade %', field: 'risk_per_trade_pct', value: acct.risk_per_trade_pct },
                  { label: 'Max Daily Loss %', field: 'max_daily_loss_pct', value: acct.max_daily_loss_pct },
                  { label: 'Max Weekly Loss %', field: 'max_weekly_loss_pct', value: acct.max_weekly_loss_pct },
                  { label: 'Max Trades/Day', field: 'max_trades_per_day', value: acct.max_trades_per_day },
                  { label: 'Stop After Losses', field: 'stop_after_consecutive_losses', value: acct.stop_after_consecutive_losses },
                ].map(item => (
                  <div key={item.field}>
                    <label className="text-xs text-muted block mb-1">{item.label}</label>
                    <input
                      type="number"
                      defaultValue={item.value}
                      onBlur={e => handleSavePolicy(acct, item.field, e.target.value)}
                      className="bg-panel2 border border-border rounded px-2 py-1 text-sm text-text w-full font-mono"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
