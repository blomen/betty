import { useState, useEffect, useMemo, useRef } from 'react';
import { ProviderName } from '../../ProviderName';
import { useToast, ToastContainer } from '../../Toast';
import type { AllocationResult, SiblingAssignment, WageringProjection } from '../../../../types';

interface Props {
  allocation: AllocationResult;
  onExecute: () => void;
  onBack: () => void;
  onSkipSibling: (providerId: string) => void;
  onUnskipSibling: (providerId: string) => void;
  onRecalc: () => void;
  onBudgetRecalc: (sek: number | undefined, usdc: number | undefined) => void;
  hasPendingSkips: boolean;
  skippedSiblings: string[];
  isLoading: boolean;
  lockedAt: number | null;      // epoch ms when batch was locked
  lockTtlSeconds: number;       // TTL in seconds (default 1800)
  onLockExpired: () => void;    // callback when TTL expires
}

function fmt(amount: number, currency: 'SEK' | 'USDC'): string {
  if (currency === 'USDC') return `${amount.toFixed(2)} USDC`;
  return `${Math.round(amount)} kr`;
}

function lifecycleBadge(lifecycle: string, bonusBadge: string | null): { text: string; color: string } | null {
  if (bonusBadge) {
    switch (lifecycle) {
      case 'deposited':
        return { text: bonusBadge, color: 'text-amber-400' };
      case 'freebet':
        return { text: bonusBadge, color: 'text-blue-400' };
      case 'wagering':
        return { text: bonusBadge, color: 'text-purple-400' };
      case 'limited':
        return { text: bonusBadge, color: 'text-red-400' };
      default:
        return null;
    }
  }
  return null;
}

type ProviderState = 'idle' | 'opened' | 'deposited';

interface ClusterGroup {
  cluster: string;
  siblings: SiblingAssignment[];
  totalBets: number;
  totalStake: number;
  currency: 'SEK' | 'USDC';
  hasShortfall: boolean;
}

export function CapitalPlanPanel({ allocation, onExecute, onBack, onSkipSibling, onUnskipSibling, onRecalc, onBudgetRecalc, hasPendingSkips, skippedSiblings, lockedAt, lockTtlSeconds, onLockExpired }: Props) {
  const [liveBalances, setLiveBalances] = useState<Record<string, number>>({});
  const [providerStates, setProviderStates] = useState<Record<string, ProviderState>>({});
  // Budget editing state (local until user clicks recalc)
  const [editingSek, setEditingSek] = useState<string>('');
  const [editingUsdc, setEditingUsdc] = useState<string>('');
  const [budgetMode, setBudgetMode] = useState(false);
  const { toasts, addToast, dismissToast } = useToast();
  // Track which providers we've already toasted per event type
  const toastedOpened = useRef<Set<string>>(new Set());
  const toastedDeposits = useRef<Set<string>>(new Set());

  // Lock TTL countdown — warn at 5 min remaining, auto-rebuild at expiry
  const [lockRemaining, setLockRemaining] = useState<number | null>(null);
  useEffect(() => {
    if (!lockedAt) { setLockRemaining(null); return; }
    const update = () => {
      const elapsed = (Date.now() - lockedAt) / 1000;
      const remaining = Math.max(0, lockTtlSeconds - elapsed);
      setLockRemaining(remaining);
      if (remaining <= 0) onLockExpired();
    };
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [lockedAt, lockTtlSeconds, onLockExpired]);

  const lockWarning = lockRemaining !== null && lockRemaining <= 300; // 5 min

  // Build a set of provider_ids in the sibling plan for quick lookup
  const siblingProviderIds = useMemo(
    () => new Set(allocation.sibling_plan.map(s => s.provider_id)),
    [allocation.sibling_plan],
  );

  // SSE: track provider navigation + balance sync + deposit detection
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    // sync_available: user navigated to provider site
    const handleSyncAvailable = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const provider = data.provider as string;
        if (!siblingProviderIds.has(provider)) return;

        // Update balance if provided
        if (data.balance != null) {
          setLiveBalances(prev => ({ ...prev, [provider]: data.balance }));
        }

        // Mark as opened (don't downgrade from deposited)
        setProviderStates(prev => {
          if (prev[provider] === 'deposited') return prev;
          return { ...prev, [provider]: 'opened' };
        });

        if (!toastedOpened.current.has(provider)) {
          toastedOpened.current.add(provider);
          addToast(`${provider} site detected`, 'success');
        }
      } catch { /* ignore */ }
    };

    // balance_synced: balance changed (any direction)
    const handleBalanceSynced = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const provider = data.provider as string;
        if (!siblingProviderIds.has(provider)) return;
        if (data.balance != null) {
          setLiveBalances(prev => ({ ...prev, [provider]: data.balance }));
        }
      } catch { /* ignore */ }
    };

    // deposit_detected: balance increased
    const handleDepositDetected = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const provider = data.provider as string;
        if (!siblingProviderIds.has(provider)) return;

        if (data.balance != null) {
          setLiveBalances(prev => ({ ...prev, [provider]: data.balance }));
        }

        setProviderStates(prev => ({ ...prev, [provider]: 'deposited' }));

        if (!toastedDeposits.current.has(provider)) {
          toastedDeposits.current.add(provider);
          const delta = data.delta as number;
          addToast(`${provider} deposit +${Math.round(delta)} detected`, 'success');
        }
      } catch { /* ignore */ }
    };

    es.addEventListener('sync_available', handleSyncAvailable);
    es.addEventListener('balance_synced', handleBalanceSynced);
    es.addEventListener('deposit_detected', handleDepositDetected);
    return () => es.close();
  }, [siblingProviderIds, addToast]);

  // Group sibling plan by cluster
  const clusters = useMemo(() => {
    const order: string[] = [];
    const grouped: Record<string, SiblingAssignment[]> = {};

    for (const sib of allocation.sibling_plan) {
      const c = sib.cluster;
      if (!grouped[c]) {
        order.push(c);
        grouped[c] = [];
      }
      grouped[c].push(sib);
    }

    const result = order.map(c => {
      const siblings = grouped[c];
      // Exclude locally-skipped siblings from header totals
      const active = siblings.filter(sib => !skippedSiblings.includes(sib.provider_id));
      const totalBets = active.reduce((s, sib) => s + sib.bets_assigned, 0);
      const totalStake = active.reduce((s, sib) => s + sib.capital_needed, 0);
      const currency = (c === 'polymarket' ? 'USDC' : 'SEK') as 'SEK' | 'USDC';
      const hasShortfall = active.some(sib => {
        const bal = liveBalances[sib.provider_id] ?? sib.current_balance;
        return bal < sib.capital_needed;
      });

      return { cluster: c, siblings, totalBets, totalStake, currency, hasShortfall } satisfies ClusterGroup;
    });

    // Drop clusters with 0 bets (no allocatable opportunities)
    const filtered = result.filter(c => c.totalBets > 0);

    // Funded clusters on top (free capital), then unfunded; within each group sort by stake descending
    filtered.sort((a, b) => {
      if (a.hasShortfall !== b.hasShortfall) return a.hasShortfall ? 1 : -1;
      return b.totalStake - a.totalStake;
    });

    return filtered;
  }, [allocation, liveBalances, skippedSiblings]);

  const anyShortfall = clusters.some(c => c.hasShortfall);

  // Current deployed capital + deposit needed
  const capitalSummary = useMemo(() => {
    let deployedSek = 0;
    let deployedUsdc = 0;
    let depositSek = 0;
    let depositUsdc = 0;
    for (const sib of allocation.sibling_plan) {
      if (skippedSiblings.includes(sib.provider_id)) continue;
      const bal = liveBalances[sib.provider_id] ?? sib.current_balance;
      if (sib.currency === 'USDC') deployedUsdc += bal;
      else deployedSek += bal;
      const shortfall = sib.capital_needed - bal;
      if (shortfall > 0) {
        if (sib.currency === 'USDC') depositUsdc += shortfall;
        else depositSek += shortfall;
      }
    }
    return {
      deployedSek: Math.round(deployedSek),
      deployedUsdc: Math.round(deployedUsdc * 100) / 100,
      depositSek: Math.round(depositSek),
      depositUsdc: Math.round(depositUsdc * 100) / 100,
    };
    }, [allocation.sibling_plan, liveBalances, skippedSiblings]);

  // Shorthand for deposit needed (used in multiple places)
  const totalDepositNeeded = { sek: capitalSummary.depositSek, usdc: capitalSummary.depositUsdc };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Summary */}
      <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
        <span className="text-muted uppercase tracking-wider text-[10px]">Capital Allocation</span>
        <span className="text-text">
          {allocation.sibling_plan.length} siblings across {clusters.length} clusters
        </span>
        <span className="text-muted text-[10px]">
          Deployed {capitalSummary.deployedSek > 0 && `${capitalSummary.deployedSek} kr`}
          {capitalSummary.deployedSek > 0 && capitalSummary.deployedUsdc > 0 && ' + '}
          {capitalSummary.deployedUsdc > 0 && `${capitalSummary.deployedUsdc.toFixed(2)} USDC`}
          {capitalSummary.deployedSek === 0 && capitalSummary.deployedUsdc === 0 && '0 kr'}
        </span>
        {lockWarning && lockRemaining !== null && (
          <span className="text-error text-[10px] font-medium animate-pulse">
            Lock expires {Math.floor(lockRemaining / 60)}:{String(Math.floor(lockRemaining % 60)).padStart(2, '0')}
          </span>
        )}
        {budgetMode ? (
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-[10px] text-muted uppercase">Extra deposit</span>
            <div className="flex items-center gap-1">
              <input
                type="number"
                value={editingSek}
                onChange={(e) => setEditingSek(e.target.value)}
                placeholder="0"
                className="w-20 px-1.5 py-0.5 text-sm bg-bg border border-border text-text text-right"
                min={0}
              />
              <span className="text-[10px] text-muted">kr</span>
            </div>
            <div className="flex items-center gap-1">
              <input
                type="number"
                value={editingUsdc}
                onChange={(e) => setEditingUsdc(e.target.value)}
                placeholder="0"
                className="w-20 px-1.5 py-0.5 text-sm bg-bg border border-border text-text text-right"
                min={0}
                step={0.01}
              />
              <span className="text-[10px] text-muted">USDC</span>
            </div>
            <button
              onClick={() => {
                const sek = editingSek !== '' ? parseFloat(editingSek) : undefined;
                const usdc = editingUsdc !== '' ? parseFloat(editingUsdc) : undefined;
                onBudgetRecalc(sek, usdc);
                setBudgetMode(false);
              }}
              className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-400 hover:bg-amber-500/30"
            >
              Apply
            </button>
            <button
              onClick={() => setBudgetMode(false)}
              className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-muted hover:text-text"
            >
              ×
            </button>
          </div>
        ) : anyShortfall ? (
          <button
            onClick={() => {
              setEditingSek(String(totalDepositNeeded.sek));
              setEditingUsdc(String(totalDepositNeeded.usdc));
              setBudgetMode(true);
            }}
            className="text-amber-400 ml-auto hover:text-amber-300 transition-colors cursor-pointer"
            title="Click to set deposit budget"
          >
            Deposit {totalDepositNeeded.sek > 0 && `${totalDepositNeeded.sek} kr`}
            {totalDepositNeeded.sek > 0 && totalDepositNeeded.usdc > 0 && ' + '}
            {totalDepositNeeded.usdc > 0 && `${totalDepositNeeded.usdc.toFixed(2)} USDC`}
          </button>
        ) : (
          <button
            onClick={() => {
              setEditingSek('');
              setEditingUsdc('');
              setBudgetMode(true);
            }}
            className="text-success ml-auto hover:text-success/80 transition-colors cursor-pointer"
            title="Click to add extra deposit budget"
          >
            All funded
          </button>
        )}
      </div>

      {/* Cluster list — funded on top, then needs-deposit */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="flex flex-col gap-1 mt-1">
          {clusters.map(({ cluster, siblings, totalBets, totalStake, currency, hasShortfall }) => (
            <div key={cluster} className="border border-border">
              <div className="flex items-center px-3 py-1 bg-panel border-b border-border">
                <div className="flex items-center gap-2">
                  <span className={`text-xs ${hasShortfall ? 'text-amber-400' : 'text-success'}`}>
                    {hasShortfall ? '○' : '●'}
                  </span>
                  <span className="text-[10px] text-muted uppercase tracking-wider">{cluster}</span>
                  <span className="text-[10px] text-muted">
                    {totalBets} {totalBets === 1 ? 'bet' : 'bets'} · {fmt(totalStake, currency)}
                  </span>
                </div>
              </div>
              {siblings.map((sib) => {
                const isSkipped = skippedSiblings.includes(sib.provider_id);
                const liveBal = liveBalances[sib.provider_id] ?? sib.current_balance;
                const liveDelta = liveBal - sib.capital_needed;
                const badge = lifecycleBadge(sib.lifecycle, sib.bonus_badge);
                const needsDeposit = liveDelta < 0;

                if (isSkipped) {
                  const hasFunds = sib.current_balance > 0;
                  return (
                    <div
                      key={sib.provider_id}
                      className={`flex items-center gap-3 px-3 py-1.5 border-b border-border last:border-b-0 ${hasFunds ? 'opacity-70' : 'opacity-40'}`}
                    >
                      <ProviderName name={sib.provider_id} className="text-sm text-text min-w-[100px]" />
                      <span className="text-[10px] text-muted">
                        {hasFunds ? `balance-capped · ${fmt(sib.current_balance, currency)}` : 'removed'}
                      </span>
                      <button
                        onClick={() => onUnskipSibling(sib.provider_id)}
                        className="ml-auto px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-muted hover:text-text transition-colors"
                      >
                        restore
                      </button>
                    </div>
                  );
                }

                const pState = providerStates[sib.provider_id] || 'idle';

                return (
                  <div
                    key={sib.provider_id}
                    className={`flex items-center gap-3 px-3 py-1.5 border-b border-border last:border-b-0 transition-colors duration-500 ${
                      needsDeposit
                        ? pState === 'deposited' ? 'bg-success/5' : pState === 'opened' ? 'bg-blue-500/5' : 'bg-amber-500/5'
                        : 'bg-success/5'
                    }`}
                  >
                    {!needsDeposit ? (
                      <span className="text-success text-sm">✓</span>
                    ) : pState === 'deposited' ? (
                      <span className="text-success text-sm">✓</span>
                    ) : pState === 'opened' ? (
                      <span className="text-blue-400 text-sm animate-pulse">◉</span>
                    ) : null}

                    <ProviderName name={sib.provider_id} className="text-sm text-text min-w-[100px]" />
                    {badge && !badge.text.startsWith('WAGER') && (
                      <span className={`text-[9px] px-1 py-0.5 bg-muted/20 ${badge.color}`}>{badge.text}</span>
                    )}
                    <span className="text-[10px] text-muted">
                      {sib.bets_assigned} {sib.bets_assigned === 1 ? 'bet' : 'bets'}
                    </span>
                    <div className="flex items-center gap-2 ml-auto text-sm">
                      <span className="text-muted">{fmt(liveBal, currency)}</span>
                      <span className="text-muted2">→</span>
                      <span className="text-text">{fmt(sib.capital_needed, currency)}</span>
                      <span className={`font-medium min-w-[60px] text-right ${liveDelta >= 0 ? 'text-success' : 'text-amber-400'}`}>
                        {liveDelta >= 0 ? '+' : ''}{fmt(liveDelta, currency)}
                      </span>
                    </div>
                    {needsDeposit ? (
                      <>
                        {pState === 'deposited' ? (
                          <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-success/20 text-success">
                            deposited
                          </span>
                        ) : pState === 'opened' ? (
                          <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-blue-500/20 text-blue-400 animate-pulse">
                            syncing…
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-400">
                            needs deposit
                          </span>
                        )}
                        <button
                          onClick={() => onSkipSibling(sib.provider_id)}
                          className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-error/20 text-error hover:bg-error/30 transition-colors"
                          title="Skip this sibling — redistribute bets to others"
                        >
                          skip
                        </button>
                      </>
                    ) : (
                      <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-success/20 text-success">funded</span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Wagering projections */}
      {allocation.wagering_projections.length > 0 && (
        <div className="border border-border border-t-0 bg-amber-500/5 px-3 py-1.5">
          <div className="flex items-center gap-1 mb-1">
            <span className="text-sm font-medium text-amber-500 tracking-wider uppercase">
              Wagering After Batch
            </span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5">
            {allocation.wagering_projections.map((proj: WageringProjection) => (
              <div
                key={`${proj.provider_id}-${proj.cluster}`}
                className="flex items-center gap-1.5 text-sm"
              >
                <span className="text-amber-400 font-medium">
                  {proj.provider_id}
                </span>
                {(() => {
                  const total = proj.wagering_total || proj.wagering_remaining;
                  const beforePct = total > 0 ? Math.round(((total - proj.wagering_remaining) / total) * 100) : 100;
                  const afterPct = total > 0 ? Math.round(((total - proj.projected_remaining) / total) * 100) : 100;
                  return (
                    <>
                      <span className="text-muted">{beforePct}%</span>
                      <span className="text-muted2">→</span>
                      <span className={afterPct >= 100 ? 'text-success' : 'text-amber-300'}>{afterPct}%</span>
                    </>
                  );
                })()}
                {proj.days_remaining != null && (
                  <span className="text-muted text-[10px]">
                    {proj.days_remaining}d
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center justify-between px-1 py-1 shrink-0">
        <div className="flex items-center gap-2">
          <button
            onClick={onBack}
            className="px-4 py-1.5 text-xs bg-tabPlay text-bg font-medium hover:opacity-90 transition-opacity"
          >
            ← Back to Batch
          </button>
          {anyShortfall ? (
            <button
              onClick={() => onBudgetRecalc(0, 0)}
              className="px-3 py-1.5 text-xs bg-muted/20 text-muted font-medium hover:text-text hover:bg-muted/30 transition-colors"
            >
              Skip All Deposits
            </button>
          ) : !anyShortfall && allocation.sibling_plan.length > 0 ? (
            <button
              onClick={() => onBudgetRecalc(undefined, undefined)}
              className="px-3 py-1.5 text-xs bg-muted/20 text-muted font-medium hover:text-text hover:bg-muted/30 transition-colors"
            >
              Reset Budget
            </button>
          ) : null}
        </div>
        {hasPendingSkips ? (
          <button
            onClick={onRecalc}
            className="px-4 py-1.5 text-xs bg-amber-500 text-bg font-medium hover:opacity-90 transition-opacity"
          >
            Recalc Batch →
          </button>
        ) : (
          <button
            onClick={onExecute}
            className="px-4 py-1.5 text-xs bg-tabPlay text-bg font-medium hover:opacity-90 transition-opacity"
          >
            Fire Batch →
          </button>
        )}
      </div>
    </div>
  );
}
