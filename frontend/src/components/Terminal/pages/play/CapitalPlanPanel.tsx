import { useState, useMemo } from 'react';
import type { CapitalAction, CapitalPlan } from '../../../../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ActionStatus = 'pending' | 'done' | 'dismissed';

interface Props {
  capitalPlan: CapitalPlan;
  onConfirm: (actions: CapitalAction[]) => void;
  onDismissAll: () => void;
  isLoading: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function actionKey(action: CapitalAction, idx: number): string {
  return `${action.type}-${action.provider_id ?? action.from_provider_id ?? ''}-${action.to_provider_id ?? ''}-${idx}`;
}

function formatCurrency(amount: number, currency: 'SEK' | 'USDC'): string {
  if (currency === 'SEK') return `${amount.toFixed(0)} kr`;
  return `${amount.toFixed(2)} USDC`;
}

function ActionBadge({ type }: { type: CapitalAction['type'] }) {
  const styles: Record<CapitalAction['type'], string> = {
    deposit:  'bg-success/20 text-success',
    transfer: 'bg-blue-500/20 text-blue-400',
    withdraw: 'bg-red-500/20 text-red-400',
  };
  return (
    <span className={`inline-block px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${styles[type]}`}>
      {type}
    </span>
  );
}

function ProviderLabel({ action }: { action: CapitalAction }) {
  if (action.type === 'transfer') {
    const from = action.from_provider_id ?? '?';
    const to = action.to_provider_id ?? '?';
    return (
      <span className="text-xs text-text">
        <span className="text-dark-400">{from}</span>
        <span className="text-dark-400 mx-1">→</span>
        <span className="text-text">{to}</span>
      </span>
    );
  }
  return (
    <span className="text-xs text-text">{action.provider_id ?? '—'}</span>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CapitalPlanPanel({ capitalPlan, onConfirm, onDismissAll, isLoading }: Props) {
  const [statuses, setStatuses] = useState<Record<string, ActionStatus>>({});
  const [collapsed, setCollapsed] = useState(false);

  const keys = useMemo(
    () => capitalPlan.actions.map((a, i) => actionKey(a, i)),
    [capitalPlan.actions],
  );

  function getStatus(key: string): ActionStatus {
    return statuses[key] ?? 'pending';
  }

  function setStatus(key: string, status: ActionStatus) {
    setStatuses((prev) => ({ ...prev, [key]: status }));
  }

  function toggleDone(key: string) {
    const current = getStatus(key);
    setStatus(key, current === 'done' ? 'pending' : 'done');
  }

  function toggleDismiss(key: string) {
    const current = getStatus(key);
    setStatus(key, current === 'dismissed' ? 'pending' : 'dismissed');
  }

  const doneActions = useMemo(
    () => capitalPlan.actions.filter((_, i) => getStatus(keys[i]) === 'done'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [capitalPlan.actions, keys, statuses],
  );

  const hasDone = doneActions.length > 0;

  // Summary figures — SEK and USDC net needed (pending + done actions only)
  const { netSEK, netUSDC, totalUnlocks, totalEV } = useMemo(() => {
    let sekNet = 0;
    let usdcNet = 0;
    let unlocks = 0;
    let ev = 0;

    capitalPlan.actions.forEach((action, i) => {
      const st = getStatus(keys[i]);
      if (st === 'dismissed') return;

      const sign = action.type === 'withdraw' ? -1 : 1;
      if (action.currency === 'SEK') sekNet += sign * action.amount;
      else usdcNet += sign * action.amount;

      unlocks += action.unlocks;
      ev += action.expected_ev;
    });

    return { netSEK: sekNet, netUSDC: usdcNet, totalUnlocks: unlocks, totalEV: ev };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capitalPlan.actions, keys, statuses]);

  // Collapsed / dismissed view
  if (collapsed) {
    return (
      <div className="border border-border bg-dark-900 px-3 py-2 flex items-center gap-2 text-xs text-dark-400">
        <span>Capital plan dismissed</span>
        <button
          className="text-blue-400 hover:text-blue-300 underline underline-offset-2 transition-colors"
          onClick={() => setCollapsed(false)}
        >
          show
        </button>
      </div>
    );
  }

  return (
    <div className="border border-border bg-dark-900 flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-border flex items-center justify-between">
        <span className="text-xs font-bold text-text tracking-wider uppercase">Capital Plan</span>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-dark-400">
            Deployed: {capitalPlan.total_deployed.toFixed(0)} kr
            {capitalPlan.withdrawable > 0 && (
              <> · Withdrawable: {capitalPlan.withdrawable.toFixed(0)} kr</>
            )}
          </span>
          <button
            className="text-[10px] text-dark-400 hover:text-text transition-colors"
            onClick={() => {
              setCollapsed(true);
              onDismissAll();
            }}
            title="Dismiss capital plan"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="sq w-full">
          <thead className="sticky top-0 z-10 bg-dark-900">
            <tr>
              <th className="text-left text-[10px]">Action</th>
              <th className="text-left text-[10px]">Provider</th>
              <th className="text-right text-[10px]">Amount</th>
              <th className="text-right text-[10px]">Unlocks</th>
              <th className="text-right text-[10px]">Avg Edge</th>
              <th className="text-right text-[10px]">+EV</th>
              <th className="text-center text-[10px]">Status</th>
            </tr>
          </thead>
          <tbody>
            {capitalPlan.actions.map((action, idx) => {
              const key = keys[idx];
              const status = getStatus(key);
              const isDone = status === 'done';
              const isDismissed = status === 'dismissed';

              return (
                <tr
                  key={key}
                  className={
                    isDone
                      ? 'bg-success/5 opacity-80'
                      : isDismissed
                      ? 'opacity-30'
                      : ''
                  }
                >
                  <td>
                    <div className="flex items-center gap-1.5">
                      <ActionBadge type={action.type} />
                      {action.priority_label && (
                        <span className="text-[9px] text-dark-400">{action.priority_label}</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <div>
                      <ProviderLabel action={action} />
                      {action.bonus_info && (
                        <div className="text-[9px] text-amber-500">{action.bonus_info}</div>
                      )}
                    </div>
                  </td>
                  <td className="text-right text-xs text-text font-medium">
                    {formatCurrency(action.amount, action.currency)}
                  </td>
                  <td className="text-right text-xs text-dark-400">
                    {action.unlocks > 0 ? `${action.unlocks}` : '—'}
                  </td>
                  <td className="text-right text-xs text-success">
                    {action.avg_edge > 0 ? `+${action.avg_edge.toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-right text-xs text-success font-medium">
                    {action.expected_ev > 0 ? `+${action.expected_ev.toFixed(0)}` : '—'}
                  </td>
                  <td className="text-center">
                    <div className="flex items-center justify-center gap-1">
                      <button
                        onClick={() => toggleDone(key)}
                        className={`px-1.5 py-0.5 text-[10px] font-medium border transition-colors ${
                          isDone
                            ? 'border-success text-success bg-success/10'
                            : 'border-border text-dark-400 hover:text-success hover:border-success/50'
                        }`}
                        title={isDone ? 'Mark as pending' : 'Mark as done'}
                      >
                        done
                      </button>
                      <button
                        onClick={() => toggleDismiss(key)}
                        className={`px-1.5 py-0.5 text-[10px] font-medium border transition-colors ${
                          isDismissed
                            ? 'border-red-500/50 text-red-400 bg-red-500/10'
                            : 'border-border text-dark-400 hover:text-red-400 hover:border-red-500/30'
                        }`}
                        title={isDismissed ? 'Restore action' : 'Skip this action'}
                      >
                        skip
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Summary bar + Confirm */}
      <div className="px-3 py-2 border-t border-border flex items-center gap-4 flex-wrap">
        {/* Net needed */}
        <div className="flex items-center gap-2 text-xs">
          <span className="text-dark-400">Net needed:</span>
          {netSEK !== 0 && (
            <span className={netSEK > 0 ? 'text-amber-500 font-medium' : 'text-success font-medium'}>
              {netSEK > 0 ? '+' : ''}{netSEK.toFixed(0)} kr
            </span>
          )}
          {netUSDC !== 0 && (
            <span className={netUSDC > 0 ? 'text-amber-500 font-medium' : 'text-success font-medium'}>
              {netUSDC > 0 ? '+' : ''}{netUSDC.toFixed(2)} USDC
            </span>
          )}
          {netSEK === 0 && netUSDC === 0 && (
            <span className="text-dark-400">—</span>
          )}
        </div>

        {totalUnlocks > 0 && (
          <div className="text-xs text-dark-400">
            Unlocks: <span className="text-text">{totalUnlocks}</span>
          </div>
        )}

        {totalEV > 0 && (
          <div className="text-xs text-dark-400">
            +EV: <span className="text-success font-medium">+{totalEV.toFixed(0)}</span>
          </div>
        )}

        {/* Confirm button */}
        {hasDone && (
          <button
            onClick={() => onConfirm(doneActions)}
            disabled={isLoading}
            className="ml-auto px-4 py-1.5 bg-success text-bg text-xs font-bold hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {isLoading ? 'Recalculating...' : `Confirm & Recalc (${doneActions.length})`}
          </button>
        )}
      </div>
    </div>
  );
}
