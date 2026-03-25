import { useState, useMemo } from 'react';
import type { CapitalAction, CapitalPlan } from '../../../../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ActionStatus = 'pending' | 'done' | 'dismissed';

interface Props {
  capitalPlan: CapitalPlan;
  onConfirm: (actions: CapitalAction[]) => void;
  onSkip: () => void;
  isLoading: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function actionKey(action: CapitalAction, idx: number): string {
  return `${action.type}-${action.provider_id ?? action.from_provider_id ?? ''}-${action.to_provider_id ?? ''}-${idx}`;
}

function formatCurrency(amount: number, currency: 'SEK' | 'USDC'): string {
  if (currency === 'USDC') return `${amount.toFixed(0)} USDC`;
  return `${amount.toFixed(0)} kr`;
}

const ACTION_COLORS: Record<CapitalAction['type'], { badge: string; border: string; dot: string }> = {
  deposit:  { badge: 'bg-success/20 text-success', border: 'border-success/30', dot: 'bg-success' },
  transfer: { badge: 'bg-blue-500/20 text-blue-400', border: 'border-blue-500/30', dot: 'bg-blue-500' },
  withdraw: { badge: 'bg-red-500/20 text-red-400', border: 'border-red-500/30', dot: 'bg-red-500' },
};

// Provider color for sharp providers
function providerColor(pid: string | undefined): string {
  if (pid === 'pinnacle') return 'text-red-500';
  if (pid === 'polymarket') return 'text-purple-500';
  return 'text-text';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ActionNode({
  action,
  status,
  onToggleDone,
  onToggleDismiss,
}: {
  action: CapitalAction;
  status: ActionStatus;
  onToggleDone: () => void;
  onToggleDismiss: () => void;
}) {
  const colors = ACTION_COLORS[action.type];
  const isDone = status === 'done';
  const isDismissed = status === 'dismissed';

  return (
    <div className="relative py-1">
      {/* Timeline dot */}
      <div
        className={`absolute -left-[21px] top-[14px] w-2.5 h-2.5 rounded-full border-2 border-dark-900 ${
          isDone ? 'bg-success' : isDismissed ? 'bg-dark-600' : colors.dot
        }`}
      />

      {/* Action card */}
      <div
        className={`flex items-center gap-3 px-3 py-2 bg-dark-800 border rounded transition-opacity ${
          isDismissed ? 'opacity-30 border-dark-700' : isDone ? `${colors.border} bg-success/5` : colors.border
        }`}
      >
        {/* Left: action info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {/* Badge */}
            <span className={`inline-block px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${colors.badge}`}>
              {action.type}
            </span>

            {/* Provider */}
            {action.type === 'transfer' ? (
              <span className="text-xs">
                <span className="text-dark-400">{action.from_provider_id}</span>
                <span className="text-dark-500 mx-1">→</span>
                <span className={providerColor(action.to_provider_id)}>{action.to_provider_id}</span>
              </span>
            ) : (
              <span className={`text-xs font-medium ${providerColor(action.provider_id)}`}>
                {action.provider_id}
              </span>
            )}

            {/* Amount */}
            <span className="text-xs text-text font-medium">
              {formatCurrency(action.amount, action.currency)}
            </span>
          </div>

          {/* Details line */}
          <div className="text-[10px] text-dark-400 mt-0.5 flex items-center gap-2 flex-wrap">
            {action.unlocks > 0 && (
              <span>→ {action.unlocks} bets</span>
            )}
            {action.avg_edge > 0 && (
              <span>· +{action.avg_edge.toFixed(1)}% avg</span>
            )}
            {action.expected_ev > 0 && (
              <span className="text-success">· +{action.expected_ev.toFixed(0)} EV</span>
            )}
            {action.bonus_info && (
              <span className="text-amber-500">· {action.bonus_info}</span>
            )}
          </div>
        </div>

        {/* Right: action buttons */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={onToggleDone}
            className={`w-7 h-7 flex items-center justify-center border rounded transition-colors ${
              isDone
                ? 'border-success bg-success/20 text-success'
                : 'border-dark-600 text-dark-500 hover:border-success/50 hover:text-success'
            }`}
            title={isDone ? 'Mark as pending' : 'Mark as done'}
          >
            <span className="text-xs font-bold">✓</span>
          </button>
          <button
            onClick={onToggleDismiss}
            className={`w-7 h-7 flex items-center justify-center border rounded transition-colors ${
              isDismissed
                ? 'border-red-500/50 bg-red-500/10 text-red-400'
                : 'border-dark-600 text-dark-500 hover:border-red-500/30 hover:text-red-400'
            }`}
            title={isDismissed ? 'Restore' : 'Skip'}
          >
            <span className="text-xs">✕</span>
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CapitalPlanPanel({ capitalPlan, onConfirm, onSkip, isLoading }: Props) {
  const [statuses, setStatuses] = useState<Record<string, ActionStatus>>({});

  const keys = useMemo(
    () => capitalPlan.actions.map((a, i) => actionKey(a, i)),
    [capitalPlan.actions],
  );

  function getStatus(key: string): ActionStatus {
    return statuses[key] ?? 'pending';
  }

  function toggleDone(key: string) {
    setStatuses(prev => ({
      ...prev,
      [key]: prev[key] === 'done' ? 'pending' : 'done',
    }));
  }

  function toggleDismiss(key: string) {
    setStatuses(prev => ({
      ...prev,
      [key]: prev[key] === 'dismissed' ? 'pending' : 'dismissed',
    }));
  }

  const doneActions = useMemo(
    () => capitalPlan.actions.filter((_, i) => getStatus(keys[i]) === 'done'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [capitalPlan.actions, keys, statuses],
  );

  const hasDone = doneActions.length > 0;
  const hasActions = capitalPlan.actions.length > 0;

  // Summary: net capital needed per currency
  const { netSEK, netUSDC, totalUnlocks, totalEV } = useMemo(() => {
    let sek = 0, usdc = 0, unlocks = 0, ev = 0;
    capitalPlan.actions.forEach((action, i) => {
      if (getStatus(keys[i]) === 'dismissed') return;
      const sign = action.type === 'withdraw' ? -1 : 1;
      if (action.currency === 'USDC') usdc += sign * action.amount;
      else sek += sign * action.amount;
      unlocks += action.unlocks;
      ev += action.expected_ev;
    });
    return { netSEK: sek, netUSDC: usdc, totalUnlocks: unlocks, totalEV: ev };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capitalPlan.actions, keys, statuses]);

  // Projected deployed total
  const projectedDeployed = capitalPlan.total_deployed + netSEK;

  return (
    <div className="p-4 flex flex-col items-center">
      {/* Constrain width for the waterfall */}
      <div className="w-full max-w-lg">

        {/* Current State box */}
        <div className="border border-dark-600 bg-dark-800 rounded-md p-4 text-center">
          <div className="text-[10px] text-dark-400 uppercase tracking-widest mb-1">Current Capital</div>
          <div className="text-xl font-bold text-text">
            {capitalPlan.total_deployed.toFixed(0)} kr
          </div>
          <div className="text-[10px] text-dark-400 mt-0.5">
            {capitalPlan.withdrawable > 0 && (
              <span>{capitalPlan.withdrawable.toFixed(0)} kr withdrawable</span>
            )}
          </div>
        </div>

        {/* Arrow down */}
        {hasActions && (
          <div className="text-center text-dark-500 text-lg py-1">↓</div>
        )}

        {/* Timeline of actions */}
        {hasActions && (
          <div className="border-l-2 border-success/20 ml-5 pl-4">
            {capitalPlan.actions.map((action, idx) => (
              <ActionNode
                key={keys[idx]}
                action={action}
                status={getStatus(keys[idx])}
                onToggleDone={() => toggleDone(keys[idx])}
                onToggleDismiss={() => toggleDismiss(keys[idx])}
              />
            ))}
          </div>
        )}

        {/* Arrow down */}
        <div className="text-center text-dark-500 text-lg py-1">↓</div>

        {/* Projected State box */}
        <div className="border border-success/30 bg-dark-800 rounded-md p-4 text-center">
          <div className="text-[10px] text-success uppercase tracking-widest mb-1">
            {hasActions ? 'Projected' : 'Ready'}
          </div>
          <div className="text-xl font-bold text-text">
            {projectedDeployed.toFixed(0)} kr
            {netUSDC > 0 && (
              <span className="text-base text-success ml-2">+ {netUSDC.toFixed(0)} USDC</span>
            )}
          </div>
          {hasActions && (
            <div className="text-[10px] text-success mt-1">
              {totalUnlocks > 0 && <span>+{totalUnlocks} bets unlocked</span>}
              {totalEV > 0 && <span className="ml-2">+{totalEV.toFixed(0)} EV</span>}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center justify-center gap-2 mt-4">
            {hasDone ? (
              <button
                onClick={() => onConfirm(doneActions)}
                disabled={isLoading}
                className="px-5 py-2 bg-success text-black text-xs font-bold rounded hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {isLoading ? 'Recalculating...' : `Confirm & Calc Batch (${doneActions.length}) →`}
              </button>
            ) : (
              <button
                onClick={onSkip}
                className="px-5 py-2 bg-success text-black text-xs font-bold rounded hover:opacity-90 transition-opacity"
              >
                {hasActions ? 'Skip → Calc Batch' : 'Calc Batch →'}
              </button>
            )}
          </div>
        </div>

        {/* No actions hint */}
        {!hasActions && (
          <div className="text-center text-dark-400 text-[10px] mt-2">
            All providers funded optimally. No capital actions needed.
          </div>
        )}
      </div>
    </div>
  );
}
