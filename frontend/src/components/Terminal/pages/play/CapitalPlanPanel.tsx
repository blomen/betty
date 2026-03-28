import { useState, useEffect, useMemo } from 'react';
import type { CapitalAction, CapitalPlan, ProviderBalanceStatus } from '../../../../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ActionStatus = 'pending' | 'done' | 'dismissed';

interface Props {
  capitalPlan: CapitalPlan & { usdc_rate?: number };
  balanceStatus?: ProviderBalanceStatus[];
  onConfirm: () => void;
  onSkip: () => void;
  isLoading: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function actionKey(action: CapitalAction): string {
  return `${action.type}-${action.provider_id}-${action.priority}`;
}

function formatCurrency(amount: number, currency: 'SEK' | 'USDC'): string {
  if (currency === 'USDC') return `${amount.toFixed(2)} USDC`;
  return `${amount.toFixed(0)} kr`;
}

const ACTION_COLORS: Record<CapitalAction['type'], { badge: string; border: string; dot: string }> = {
  deposit:  { badge: 'bg-success/20 text-success', border: 'border-success/30', dot: 'bg-success' },
  withdraw: { badge: 'bg-red-500/20 text-red-400', border: 'border-red-500/30', dot: 'bg-red-500' },
};

function providerColor(pid: string): string {
  if (pid === 'pinnacle') return 'text-red-500';
  if (pid === 'polymarket') return 'text-purple-500';
  return 'text-text';
}

// ---------------------------------------------------------------------------
// ProgressRing — thin circle that fills as balance approaches target
// ---------------------------------------------------------------------------

function ProgressRing({ progress, size = 20, stroke = 2 }: { progress: number; size?: number; stroke?: number }) {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(1, progress));
  const offset = circumference * (1 - clamped);
  const color = clamped >= 1 ? '#22c55e' : clamped > 0 ? '#f59e0b' : '#3f3f46';

  return (
    <svg width={size} height={size} className="flex-shrink-0" style={{ transform: 'rotate(-90deg)' }}>
      {/* Background ring */}
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#27272a" strokeWidth={stroke} />
      {/* Fill ring */}
      <circle
        cx={size / 2} cy={size / 2} r={radius} fill="none"
        stroke={color} strokeWidth={stroke}
        strokeDasharray={circumference} strokeDashoffset={offset}
        strokeLinecap="round"
        style={{ transition: 'stroke-dashoffset 0.5s ease, stroke 0.3s ease' }}
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// ActionNode
// ---------------------------------------------------------------------------

function ActionNode({
  action,
  status,
  usdcRate,
  progress,
  liveBalance,
  onToggleDismiss,
}: {
  action: CapitalAction;
  status: ActionStatus;
  usdcRate: number;
  progress: number;
  liveBalance: number | undefined;
  onToggleDismiss: () => void;
}) {
  const colors = ACTION_COLORS[action.type];
  const isDone = status === 'done';
  const isDismissed = status === 'dismissed';

  return (
    <div className="relative py-1">
      {/* Progress ring replacing timeline dot */}
      <div className="absolute -left-[26px] top-[10px]">
        <ProgressRing progress={isDone ? 1 : isDismissed ? 0 : progress} />
      </div>

      {/* Action card */}
      <div
        className={`flex items-center gap-3 px-3 py-2 bg-dark-800 border rounded transition-opacity ${
          isDismissed ? 'opacity-30 border-dark-700' : isDone ? `${colors.border} bg-success/5` : colors.border
        }`}
      >
        {/* Left: action info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`inline-block px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${colors.badge}`}>
              {action.type}
            </span>
            <span className={`text-xs font-medium ${providerColor(action.provider_id)}`}>
              {action.provider_id}
            </span>
            <span className="text-xs text-text font-medium">
              {formatCurrency(action.amount, action.currency)}
              {action.currency === 'USDC' && usdcRate > 0 && (
                <span className="text-dark-400 font-normal ml-1">
                  ({Math.round(action.amount * usdcRate)} kr)
                </span>
              )}
            </span>
          </div>

          {/* Balance progress line */}
          {action.type === 'deposit' && action.target_balance > 0 && (() => {
            const bal = liveBalance ?? 0;
            return (
              <div className="text-[10px] mt-0.5 flex items-center gap-1">
                <span className={bal >= action.target_balance ? 'text-success' : 'text-amber-400'}>
                  {formatCurrency(bal, action.currency)}
                </span>
                <span className="text-dark-500">/</span>
                <span className="text-dark-400">{formatCurrency(action.target_balance, action.currency)}</span>
                {bal < action.target_balance && (
                  <span className="text-dark-500 ml-1">
                    ({formatCurrency(action.target_balance - bal, action.currency)} short)
                  </span>
                )}
              </div>
            );
          })()}

          {/* Details line */}
          <div className="text-[10px] text-dark-400 mt-0.5 flex items-center gap-2 flex-wrap">
            {action.unlocks > 0 && <span>→ {action.unlocks} bets</span>}
            {action.avg_edge > 0 && <span>· +{action.avg_edge.toFixed(1)}% avg</span>}
            {action.expected_ev > 0 && (
              <span className="text-success">· +{action.expected_ev.toFixed(0)} EV</span>
            )}
          </div>
        </div>

        {/* Right: skip button */}
        <div className="flex items-center flex-shrink-0">
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

export function CapitalPlanPanel({ capitalPlan, balanceStatus, onConfirm, onSkip, isLoading }: Props) {
  const usdcRate = capitalPlan.usdc_rate ?? 10.5;
  const [statuses, setStatuses] = useState<Record<string, ActionStatus>>({});
  // Track live balances per provider — seeded from batch data, updated by SSE
  const [liveBalances, setLiveBalances] = useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    if (balanceStatus) {
      for (const bs of balanceStatus) {
        initial[bs.provider_id] = bs.balance;
      }
    }
    return initial;
  });

  const keys = useMemo(
    () => capitalPlan.actions.map((a) => actionKey(a)),
    [capitalPlan.actions],
  );

  // Listen for mirror balance_synced SSE — track deposit progress + auto-mark done
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    const handleBalanceEvent = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const provider = data.provider as string;

        // Always track absolute live balance
        const balance = data.balance as number | undefined;
        if (balance != null) {
          setLiveBalances(prev => ({ ...prev, [provider]: balance }));
        }

        // Auto-mark done when a deposit delta matches recommended amount
        const delta = data.delta as number | undefined;
        if (delta && delta > 0) {
          capitalPlan.actions.forEach((action, i) => {
            if (action.type !== 'deposit' || action.provider_id !== provider) return;
            if (action.amount <= 0) return;
            const tolerance = Math.abs(delta - action.amount) / action.amount;
            if (tolerance <= 0.10) {
              setStatuses(prev => ({ ...prev, [keys[i]]: 'done' }));
            }
          });
        }
      } catch { /* ignore parse errors */ }
    };

    es.addEventListener('balance_synced', handleBalanceEvent);
    es.addEventListener('deposit_detected', handleBalanceEvent);

    return () => es.close();
  }, [capitalPlan.actions, keys]);

  function getStatus(key: string): ActionStatus {
    return statuses[key] ?? 'pending';
  }

  function toggleDismiss(key: string) {
    setStatuses(prev => ({
      ...prev,
      [key]: prev[key] === 'dismissed' ? 'pending' : 'dismissed',
    }));
  }

  const doneCount = useMemo(
    () => keys.filter(k => getStatus(k) === 'done').length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [keys, statuses],
  );

  const hasDone = doneCount > 0;
  const hasActions = capitalPlan.actions.length > 0;

  // Summary
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

  const usdcInSEK = netUSDC * usdcRate;
  const projectedDeployed = capitalPlan.total_deployed + netSEK + usdcInSEK;

  return (
    <div className="p-4 flex-1 min-h-0 overflow-y-auto flex flex-col items-center">
      <div className="w-full max-w-lg">

        {/* Current State */}
        <div className="border border-dark-600 bg-dark-800 rounded-md p-4 text-center">
          <div className="text-[10px] text-dark-400 uppercase tracking-widest mb-1">Current Capital</div>
          <div className="text-xl font-bold text-text">
            {capitalPlan.total_deployed.toFixed(0)} kr
          </div>
          {capitalPlan.withdrawable > 0 && (
            <div className="text-[10px] text-dark-400 mt-0.5">
              {capitalPlan.withdrawable.toFixed(0)} kr withdrawable
            </div>
          )}
        </div>

        {/* Arrow */}
        {hasActions && <div className="text-center text-dark-500 text-lg py-1">↓</div>}

        {/* Timeline grouped by cluster */}
        {hasActions && (() => {
          // Group actions by cluster, preserving order of first appearance
          const clusterOrder: string[] = [];
          const clusterActions: Record<string, { action: CapitalAction; idx: number }[]> = {};
          capitalPlan.actions.forEach((action, idx) => {
            const c = action.cluster;
            if (!clusterActions[c]) {
              clusterOrder.push(c);
              clusterActions[c] = [];
            }
            clusterActions[c].push({ action, idx });
          });

          return (
            <div className="space-y-2">
              {clusterOrder.map(cluster => {
                const items = clusterActions[cluster];
                const clusterTotal = items.reduce((s, { action }) => s + action.amount, 0);
                const clusterEV = items.reduce((s, { action }) => s + action.expected_ev, 0);
                const clusterUnlocks = items.length > 1
                  ? items[0].action.unlocks  // cluster siblings share the same opps
                  : items[0].action.unlocks;
                const allDismissed = items.every(({ idx: i }) => getStatus(keys[i]) === 'dismissed');
                const currency = items[0].action.currency;

                return (
                  <div key={cluster} className={`border border-dark-700 rounded-md overflow-hidden ${allDismissed ? 'opacity-30' : ''}`}>
                    {/* Cluster header */}
                    <div className="flex items-center justify-between px-3 py-1.5 bg-dark-800/50 border-b border-dark-700">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-dark-400 uppercase tracking-wider">{cluster}</span>
                        <span className="text-[10px] text-dark-500">
                          {formatCurrency(clusterTotal, currency)} total
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-[10px]">
                        {clusterUnlocks > 0 && <span className="text-dark-400">{clusterUnlocks} bets</span>}
                        {clusterEV > 0 && <span className="text-success">+{clusterEV.toFixed(0)} EV</span>}
                      </div>
                    </div>
                    {/* Actions in cluster */}
                    <div className="border-l-2 border-success/20 ml-[15px] pl-5 py-0.5">
                      {items.map(({ action, idx }) => (
                        <ActionNode
                          key={keys[idx]}
                          action={action}
                          status={getStatus(keys[idx])}
                          usdcRate={usdcRate}
                          progress={action.target_balance > 0 ? (liveBalances[action.provider_id] ?? 0) / action.target_balance : 0}
                          liveBalance={liveBalances[action.provider_id]}
                          onToggleDismiss={() => toggleDismiss(keys[idx])}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })()}

        {/* Arrow */}
        <div className="text-center text-dark-500 text-lg py-1">↓</div>

        {/* Projected State */}
        <div className="border border-success/30 bg-dark-800 rounded-md p-4 text-center">
          <div className="text-[10px] text-success uppercase tracking-widest mb-1">
            {hasActions ? 'Projected' : 'Ready'}
          </div>
          <div className="text-xl font-bold text-text">
            {projectedDeployed.toFixed(0)} kr
          </div>
          {netUSDC > 0 && (
            <div className="text-[10px] text-dark-400 mt-0.5">
              incl. {netUSDC.toFixed(2)} USDC ({Math.round(usdcInSEK)} kr)
            </div>
          )}
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
                onClick={onConfirm}
                disabled={isLoading}
                className="px-5 py-2 bg-success text-black text-xs font-bold rounded hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {isLoading ? 'Recalculating...' : `Recalc Batch →`}
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

        {/* Mirror hint */}
        {hasActions && (
          <div className="text-center text-dark-400 text-[10px] mt-3">
            Do deposits in the mirror browser — balances sync automatically.
          </div>
        )}
      </div>
    </div>
  );
}
