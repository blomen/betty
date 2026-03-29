import { useState, useEffect, useMemo } from 'react';
import { ProviderName } from '../../ProviderName';
import type { CapitalPlan, ProviderBalanceStatus, BatchBet } from '../../../../types';

interface Props {
  capitalPlan: CapitalPlan & { usdc_rate?: number };
  balanceStatus?: ProviderBalanceStatus[];
  batch: BatchBet[];
  onConfirm: () => void;
  onSkip: () => void;
  isLoading: boolean;
}

function fmt(amount: number, currency: 'SEK' | 'USDC'): string {
  if (currency === 'USDC') return `${amount.toFixed(2)} USDC`;
  return `${Math.round(amount)} kr`;
}

function lifecycleBadge(s: ProviderBalanceStatus): { text: string; color: string } | null {
  const wagerPct = s.wagering_total > 0
    ? Math.round(((s.wagering_total - s.wagering_remaining) / s.wagering_total) * 100)
    : 100;
  switch (s.lifecycle) {
    case 'deposited':
      if (s.trigger_mode === 'single') {
        return { text: `TRG ${s.bonus_amount}kr`, color: 'text-amber-400' };
      }
      return { text: `TRG ${wagerPct}%`, color: 'text-amber-400' };
    case 'freebet':
      return { text: 'FREE', color: 'text-blue-400' };
    case 'wagering':
      return { text: `WAGER ${wagerPct}%`, color: 'text-purple-400' };
    case 'limited':
      return { text: 'LTD', color: 'text-red-400' };
    default:
      return null;
  }
}

interface ClusterGroup {
  cluster: string;
  siblings: ProviderBalanceStatus[];
  bets: number;
  stake: number;
  ev: number;
  currency: 'SEK' | 'USDC';
  shortfall: number; // total allocated - total balance (if positive)
}

export function CapitalPlanPanel({ capitalPlan, balanceStatus, batch, onSkip }: Props) {
  const [liveBalances, setLiveBalances] = useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    if (balanceStatus) {
      for (const bs of balanceStatus) initial[bs.provider_id] = bs.balance;
    }
    return initial;
  });

  // SSE: track deposit progress
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');
    const handle = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const provider = data.provider as string;
        if (data.balance != null) setLiveBalances(prev => ({ ...prev, [provider]: data.balance }));
      } catch { /* ignore */ }
    };
    es.addEventListener('balance_synced', handle);
    es.addEventListener('deposit_detected', handle);
    return () => es.close();
  }, []);

  // Group balance_status by cluster, cross-reference with batch
  const clusters = useMemo(() => {
    if (!balanceStatus) return [];

    // Compute per-provider batch stats
    const providerBets: Record<string, { count: number; stake: number; ev: number }> = {};
    for (const b of batch) {
      const pid = b.provider_id;
      if (!providerBets[pid]) providerBets[pid] = { count: 0, stake: 0, ev: 0 };
      providerBets[pid].count += 1;
      providerBets[pid].stake += b.stake;
      providerBets[pid].ev += b.stake * (b.edge_pct / 100);
    }

    // Group siblings by cluster
    const order: string[] = [];
    const grouped: Record<string, ProviderBalanceStatus[]> = {};
    for (const bs of balanceStatus) {
      const c = bs.cluster || bs.provider_id;
      if (!grouped[c]) {
        order.push(c);
        grouped[c] = [];
      }
      grouped[c].push(bs);
    }

    // Build cluster groups — only include clusters that have bets in the batch
    const result: ClusterGroup[] = [];
    for (const c of order) {
      const siblings = grouped[c];
      let totalBets = 0;
      let totalStake = 0;
      let totalEV = 0;
      let totalBalance = 0;
      let totalAllocated = 0;

      for (const s of siblings) {
        const pb = providerBets[s.provider_id];
        if (pb) {
          totalBets += pb.count;
          totalStake += pb.stake;
          totalEV += pb.ev;
        }
        totalBalance += liveBalances[s.provider_id] ?? s.balance;
        totalAllocated += s.allocated;
      }

      // Skip clusters with no bets in this batch
      if (totalBets === 0) continue;

      const isUsdc = c === 'polymarket';
      result.push({
        cluster: c,
        siblings,
        bets: totalBets,
        stake: totalStake,
        ev: totalEV,
        currency: isUsdc ? 'USDC' : 'SEK',
        shortfall: Math.max(0, totalAllocated - totalBalance),
      });
    }

    return result;
  }, [balanceStatus, batch, liveBalances]);

  const totalDeployed = capitalPlan.total_deployed;
  const hasShortfall = clusters.some(c => c.shortfall > 0);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Summary */}
      <div className="flex items-center gap-3 px-3 py-1.5 border border-border bg-panel text-sm">
        <span className="text-muted uppercase tracking-wider text-[10px]">Capital Allocation</span>
        <span className="text-text">{totalDeployed.toFixed(0)} kr deployed</span>
        {hasShortfall && (
          <span className="text-amber-400 ml-auto">Deposits needed</span>
        )}
        {!hasShortfall && (
          <span className="text-success ml-auto">All funded</span>
        )}
      </div>

      {/* Cluster list */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="flex flex-col gap-1 mt-1">
          {clusters.map(({ cluster, siblings, bets, stake, ev, currency, shortfall }) => {
            const needsDeposit = shortfall > 0;

            return (
              <div key={cluster} className="border border-border">
                {/* Cluster header */}
                <div className="flex items-center justify-between px-3 py-1 bg-panel border-b border-border">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs ${needsDeposit ? 'text-amber-400' : 'text-success'}`}>
                      {needsDeposit ? '○' : '●'}
                    </span>
                    <span className="text-[10px] text-muted uppercase tracking-wider">{cluster}</span>
                    <span className="text-[10px] text-muted">
                      {bets} {bets === 1 ? 'bet' : 'bets'} · {fmt(stake, currency)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-[10px]">
                    {ev > 0 && <span className="text-tabPlay">+{ev.toFixed(0)} {currency === 'USDC' ? 'USDC' : 'kr'} EV</span>}
                    {needsDeposit && (
                      <span className="text-amber-400">need {fmt(shortfall, currency)}</span>
                    )}
                  </div>
                </div>

                {/* Siblings */}
                {siblings.map((s) => {
                  const bal = liveBalances[s.provider_id] ?? s.balance;
                  const badge = lifecycleBadge(s);
                  const hasBets = s.allocated > 0;
                  const siblingShort = s.allocated > bal;

                  return (
                    <div
                      key={s.provider_id}
                      className={`flex items-center gap-3 px-3 py-1.5 border-b border-border last:border-b-0 ${
                        !hasBets ? 'opacity-40' : ''
                      } ${siblingShort ? 'bg-amber-500/5' : ''}`}
                    >
                      <ProviderName name={s.provider_id} className="text-sm text-text min-w-[100px]" />

                      {badge && (
                        <span className={`text-[9px] px-1 py-0.5 bg-muted/20 ${badge.color}`}>
                          {badge.text}
                        </span>
                      )}

                      {s.days_remaining != null && (
                        <span className="text-[10px] text-muted">{s.days_remaining}d</span>
                      )}

                      <span className="text-sm text-muted ml-auto">
                        {fmt(bal, currency)}
                        {hasBets && (
                          <span className="text-muted2"> / {fmt(s.allocated, currency)} alloc</span>
                        )}
                      </span>

                      {siblingShort && (
                        <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-400">
                          deposit
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>

      {/* Action button */}
      <div className="flex items-center justify-end px-1 py-1">
        <button
          onClick={hasShortfall ? onSkip : onSkip}
          className="px-4 py-1.5 text-xs bg-tabPlay text-bg font-medium hover:opacity-90 transition-opacity"
        >
          {hasShortfall ? 'Skip → Execute' : 'Execute →'}
        </button>
      </div>
    </div>
  );
}
