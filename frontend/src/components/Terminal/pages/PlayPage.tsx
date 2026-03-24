import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useBetMutations } from '@/hooks/useBetMutations';
import { api } from '@/services/api';
import { formatProviderName, displayTeamName } from '@/utils/formatters';
import { resolveOutcome } from '@/utils/betting';
import { ProviderName } from '../ProviderName';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useToast, ToastContainer } from '../Toast';
import type { BatchBet, BatchResult, PlaySession } from '@/types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function betKey(b: BatchBet): string {
  return `${b.event_id}|${b.market}|${b.outcome}|${b.point ?? ''}`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface PlayPageProps {
  providers?: { id: string; balance: number }[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PlayPage(_props: PlayPageProps) {
  const { placeBatchBets } = useBetMutations();
  const { toasts, addToast, dismissToast } = useToast();

  // ---- State ----
  const [removedBets, setRemovedBets] = useState<Set<string>>(new Set());
  const [fireResults, setFireResults] = useState<Map<string, { success: boolean; error?: string }>>(new Map());
  const [isFiring, setIsFiring] = useState(false);

  // ---- Queries ----
  const {
    data: batchData,
    isLoading: batchLoading,
    refetch: rebuildBatch,
    isFetching: batchFetching,
  } = useQuery<BatchResult>({
    queryKey: ['play-batch'],
    queryFn: () => api.getPlayBatch(),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  // PlaySession query kept for potential future use (cluster lifecycle data)
  useQuery<PlaySession>({
    queryKey: ['play-session'],
    queryFn: () => api.getPlaySession(),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  // ---- Derived data ----
  const batch = batchData?.batch ?? [];
  const summary = batchData?.summary;
  const balanceStatus = batchData?.balance_status ?? [];
  const missed = batchData?.missed_opportunities;
  const depositRecs = (batchData as any)?.deposit_recommendations ?? [];
  const withdrawalRecs = (batchData as any)?.withdrawal_recommendations ?? [];
  const capitalPlan = (batchData as any)?.capital_plan;

  const activeBatch = useMemo(
    () => batch.filter((b) => !removedBets.has(betKey(b))),
    [batch, removedBets],
  );

  const sharpBets = useMemo(() => activeBatch.filter((b) => b.tier === 'sharp'), [activeBatch]);
  const softBets = useMemo(() => activeBatch.filter((b) => b.tier === 'soft'), [activeBatch]);

  // Build shortfall set for fire filtering
  const shortfallProviders = useMemo(() => {
    const s = new Set<string>();
    for (const bs of balanceStatus) {
      if (bs.shortfall && bs.shortfall > 0) s.add(bs.provider_id);
    }
    return s;
  }, [balanceStatus]);

  const fireableBets = useMemo(
    () => activeBatch.filter((b) => !shortfallProviders.has(b.provider_id)),
    [activeBatch, shortfallProviders],
  );

  const fireableEV = useMemo(
    () => fireableBets.reduce((sum, b) => sum + b.expected_profit, 0),
    [fireableBets],
  );

  // ---- Handlers ----
  const removeBet = useCallback((b: BatchBet) => {
    setRemovedBets((prev) => new Set(prev).add(betKey(b)));
  }, []);

  const handleRebuild = useCallback(() => {
    setRemovedBets(new Set());
    setFireResults(new Map());
    rebuildBatch();
  }, [rebuildBatch]);

  const handleFire = useCallback(async () => {
    if (fireableBets.length === 0) return;
    setIsFiring(true);
    setFireResults(new Map());

    try {
      const legs = fireableBets.map((b) => ({
        event_id: b.event_id,
        provider_id: b.provider_id,
        market: b.market,
        outcome: b.outcome,
        odds: b.odds,
        stake: b.stake,
        point: b.point,
        is_bonus: b.is_bonus,
        bet_type: 'value' as const,
      }));

      const result = await placeBatchBets.mutateAsync(legs);

      const resultMap = new Map<string, { success: boolean; error?: string }>();
      for (const r of result.results) {
        const matchBet = fireableBets[r.leg_index];
        if (matchBet) {
          resultMap.set(betKey(matchBet), { success: r.success, error: r.error });
        }
      }
      setFireResults(resultMap);

      addToast(
        `Fired ${result.placed_count}/${result.total_legs} bets (${result.total_staked.toFixed(0)} kr)`,
        result.placed_count === result.total_legs ? 'success' : 'warning',
      );

      // Auto-rebuild after a short delay
      setTimeout(() => handleRebuild(), 2000);
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Batch fire failed', 'error');
    } finally {
      setIsFiring(false);
    }
  }, [fireableBets, placeBatchBets, addToast, handleRebuild]);

  const handleCopyDeposits = useCallback(() => {
    const lines = balanceStatus
      .filter((bs) => bs.shortfall && bs.shortfall > 0)
      .map((bs) => `${formatProviderName(bs.provider_id)}: needs ${bs.shortfall!.toFixed(0)} kr (${bs.missed_bets} bets, +${bs.missed_ev.toFixed(0)} EV)`)
      .join('\n');
    if (lines) {
      navigator.clipboard.writeText(lines);
      addToast('Deposit needs copied', 'success');
    }
  }, [balanceStatus, addToast]);

  // ---- Render helpers ----
  function renderBetRow(b: BatchBet, idx: number) {
    const key = betKey(b);
    const result = fireResults.get(key);
    const isBoost = b.market === 'boost';

    // Boosts: event name is the title, outcome is the full description
    // Regular bets: standard home v away format
    const eventName = isBoost
      ? (b.display_home && b.display_away ? `${b.display_home} v ${b.display_away}` : b.outcome)
      : `${displayTeamName(b.home_team, b.display_home)} v ${displayTeamName(b.away_team, b.display_away)}`;
    const outcomeLabel = isBoost
      ? b.outcome
      : resolveOutcome(b.outcome, { home_team: b.home_team, away_team: b.away_team, display_home: b.display_home, display_away: b.display_away, market: b.market } as any, b.point, true);

    return (
      <tr key={`${key}-${b.provider_id}-${idx}`} className={result ? (result.success ? 'bg-success/5' : 'bg-error/5') : ''}>
        <td className="text-muted text-xs">{b.rank}</td>
        <td className="text-xs">
          <ProviderName name={b.provider_id} />
          {isBoost && (
            <span className="ml-1 text-[9px] px-1 py-0.5 bg-purple-500/20 text-purple-400">
              BOOST
            </span>
          )}
          {b.is_bonus && (
            <span className="ml-1 text-[9px] px-1 py-0.5 bg-accent/20 text-accent">
              {b.bonus_type === 'freebet' ? 'FREE' : 'TRG'}
            </span>
          )}
        </td>
        <td className="text-xs text-text truncate max-w-[250px]" title={isBoost ? b.outcome : eventName}>
          {isBoost ? (
            <>
              <span className="text-purple-300">{b.outcome}</span>
              <div className="text-[10px] text-muted">
                {b.sport}{b.league ? ` · ${b.league}` : ''}
              </div>
            </>
          ) : (
            <>
              {eventName}
              <div className="text-[10px] text-muted">
                {b.sport}{b.league ? ` · ${b.league}` : ''}{b.market !== '1x2' && b.market !== 'moneyline' ? ` · ${b.market}` : ''}
              </div>
            </>
          )}
        </td>
        <td className="text-xs text-text">{isBoost ? '' : outcomeLabel}</td>
        <td className="text-right text-xs text-text font-medium">{b.odds.toFixed(2)}</td>
        <td className="text-right text-xs text-muted">{b.fair_odds.toFixed(2)}</td>
        <td className={`text-right text-xs font-semibold ${b.edge_pct > 0 ? 'text-success' : 'text-error'}`}>
          {b.edge_pct > 0 ? '+' : ''}{b.edge_pct.toFixed(1)}%
        </td>
        <td className="text-right text-xs text-text">{b.stake.toFixed(0)} kr</td>
        <td className={`text-right text-xs font-medium ${b.expected_profit > 0 ? 'text-success' : 'text-muted'}`}>
          +{b.expected_profit.toFixed(0)}
        </td>
        <td className="text-right text-xs">
          {result ? (
            result.success ? (
              <span className="text-success">OK</span>
            ) : (
              <span className="text-error" title={result.error}>FAIL</span>
            )
          ) : (
            <button
              onClick={() => removeBet(b)}
              className="text-muted hover:text-error transition-colors"
              title="Remove from batch"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </td>
      </tr>
    );
  }

  // ---- Actions panel ----
  function renderActionsPanel() {
    const hasWithdrawals = withdrawalRecs.length > 0;
    const hasDeposits = depositRecs.length > 0;
    const hasActions = hasWithdrawals || hasDeposits;
    if (!hasActions && !capitalPlan) return null;

    const totalWithdrawable = withdrawalRecs.reduce((s: number, w: any) => s + w.amount, 0);
    const totalDepositNeeded = depositRecs.reduce((s: number, d: any) => s + d.deposit_amount, 0);
    const totalMissedEV = depositRecs.reduce((s: number, d: any) => s + d.missed_ev, 0);

    const priorityColors: Record<string, string> = {
      sharp: 'text-success',
      no_wagering: 'text-blue-400',
      fast_clear_high_vol: 'text-emerald-400',
      fast_clear: 'text-emerald-400/70',
      medium_clear_high_vol: 'text-amber-400',
      medium_clear: 'text-amber-400/70',
      slow_clear_high_vol: 'text-orange-400',
      slow_clear_low_vol: 'text-orange-400/50',
      skip_infeasible: 'text-red-400/50 line-through',
    };

    const priorityIcons: Record<string, string> = {
      sharp: '◆',
      no_wagering: '●',
      fast_clear_high_vol: '▲▲',
      fast_clear: '▲',
      medium_clear_high_vol: '■■',
      medium_clear: '■',
      slow_clear_high_vol: '◇◇',
      slow_clear_low_vol: '◇',
      skip_infeasible: '✗',
    };

    return (
      <div className="border border-border bg-panel flex flex-col">
        {/* Header */}
        <div className="px-3 py-2 border-b border-border flex items-center justify-between">
          <span className="text-xs font-bold text-text tracking-wider uppercase">Capital Actions</span>
          {capitalPlan && (
            <span className="text-[10px] text-muted">
              Deployed: {capitalPlan.total_deployed?.toFixed(0)} kr
            </span>
          )}
        </div>

        {/* Step 1: Withdrawals */}
        {hasWithdrawals && (
          <div className="px-3 py-2 border-b border-border">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="w-5 h-5 rounded-full bg-success/20 text-success text-[10px] font-bold flex items-center justify-center">1</span>
              <span className="text-xs font-medium text-success">Withdraw</span>
              <span className="text-[10px] text-muted ml-auto">{totalWithdrawable.toFixed(0)} kr available</span>
            </div>
            <div className="space-y-1 ml-7">
              {withdrawalRecs.map((w: any) => (
                <div key={w.provider_id} className="flex items-center justify-between text-xs">
                  <ProviderName name={w.provider_id} className="text-text" />
                  <span className="text-success font-medium">{w.amount.toFixed(0)} kr</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Step 2: Deposit recommendations */}
        {hasDeposits && (
          <div className="px-3 py-2 border-b border-border">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="w-5 h-5 rounded-full bg-amber-500/20 text-amber-400 text-[10px] font-bold flex items-center justify-center">
                {hasWithdrawals ? '2' : '1'}
              </span>
              <span className="text-xs font-medium text-amber-400">Deposit</span>
              <span className="text-[10px] text-muted ml-auto">{totalDepositNeeded.toFixed(0)} kr → +{totalMissedEV.toFixed(0)} EV</span>
            </div>
            <div className="space-y-1.5 ml-7">
              {depositRecs
                .filter((d: any) => d.wagering_feasible !== false)
                .map((d: any) => (
                <div key={d.cluster} className="flex items-center gap-2 text-xs">
                  <span className="text-text font-medium min-w-[80px]">{d.cluster}</span>
                  <span className="text-amber-400">{d.deposit_amount.toFixed(0)} kr</span>
                  <span className="text-muted text-[10px]">
                    {d.missed_bets}b +{d.missed_ev.toFixed(0)}
                  </span>
                  {d.sessions_to_clear != null && (
                    <span className="text-[10px] text-muted ml-auto">
                      {d.sessions_to_clear}s / {d.days_remaining ?? '?'}d
                    </span>
                  )}
                </div>
              ))}
              {depositRecs.some((d: any) => d.wagering_feasible === false) && (
                <div className="text-[10px] text-red-400/60 mt-1">
                  Skipped: {depositRecs.filter((d: any) => !d.wagering_feasible).map((d: any) => d.cluster).join(', ')} (can't clear wagering in time)
                </div>
              )}
            </div>
          </div>
        )}

        {/* Step 3: Fire */}
        {fireableBets.length > 0 && (
          <div className="px-3 py-2 border-b border-border">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="w-5 h-5 rounded-full bg-blue-500/20 text-blue-400 text-[10px] font-bold flex items-center justify-center">
                {(hasWithdrawals ? 1 : 0) + (hasDeposits ? 1 : 0) + 1}
              </span>
              <span className="text-xs font-medium text-blue-400">Fire Batch</span>
              <span className="text-[10px] text-success ml-auto">{fireableBets.length} bets → +{fireableEV.toFixed(0)} kr</span>
            </div>
          </div>
        )}

        {/* Priority legend */}
        {capitalPlan?.targets?.length > 0 && (
          <div className="px-3 py-2">
            <div className="text-[10px] text-muted font-medium mb-1 uppercase tracking-wider">Priority Order</div>
            <div className="space-y-0.5">
              {capitalPlan.targets.map((t: any, i: number) => {
                const label = t.priority_label || 'unknown';
                const color = priorityColors[label] || 'text-muted';
                const icon = priorityIcons[label] || '·';
                const opps = t.unique_opps || 0;
                const evSession = t.ev_per_session || 0;
                return (
                  <div key={t.cluster || t.provider_id || i} className={`flex items-center gap-1.5 text-[11px] ${color}`}>
                    <span className="w-4 text-center text-[10px]">{icon}</span>
                    <span className="min-w-[70px]">{t.cluster || t.provider_id}</span>
                    {opps > 0 && (
                      <span className="text-muted text-[10px]">{opps} opps</span>
                    )}
                    {evSession > 0 && (
                      <span className="text-[10px] ml-auto">+{evSession.toFixed(0)}/s</span>
                    )}
                    {t.sessions_to_clear != null && (
                      <span className="text-muted text-[10px]">{t.sessions_to_clear}s</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    );
  }

  // ---- Main render ----
  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="play" color={TAB_COLORS.play} size={16} />
          Play
        </h2>
        <button
          onClick={handleRebuild}
          disabled={batchFetching}
          className="px-3 py-1 text-xs font-medium border border-border text-muted hover:text-text hover:border-tabValue/50 disabled:opacity-50 transition-colors"
        >
          {batchFetching ? 'Building...' : 'Build Batch'}
        </button>
      </div>

      {/* Summary bar */}
      {summary && (
        <div className="flex items-center gap-4 px-3 py-1.5 border border-border bg-panel text-xs">
          <span className="text-text font-medium">BATCH: {activeBatch.length} bets</span>
          <span className="text-muted">{activeBatch.reduce((s, b) => s + b.stake, 0).toFixed(0)} kr</span>
          <span className="text-success font-medium">+{activeBatch.reduce((s, b) => s + b.expected_profit, 0).toFixed(0)} kr EV</span>
          <span className="text-muted">|</span>
          <span className="text-success">Sharp: {sharpBets.length} (+{sharpBets.reduce((s, b) => s + b.expected_profit, 0).toFixed(0)})</span>
          <span className="text-tabValue">Soft: {softBets.length} (+{softBets.reduce((s, b) => s + b.expected_profit, 0).toFixed(0)})</span>
          {missed && missed.total_bets > 0 && (
            <>
              <span className="text-muted">|</span>
              <span className="text-error text-[10px]">Missed: {missed.total_bets} (+{missed.total_ev.toFixed(0)})</span>
            </>
          )}
        </div>
      )}

      {/* Toasts */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Main content: batch table + actions panel */}
      {batchLoading && batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Building batch...
        </div>
      ) : activeBatch.length === 0 ? (
        <div className="flex gap-2 flex-1 min-h-0">
          <div className="flex-1 text-muted text-sm py-8 text-center border border-border bg-panel">
            No bets in batch. Click "Build Batch" to generate.
          </div>
          {renderActionsPanel()}
        </div>
      ) : (
        <div className="flex gap-2 flex-1 min-h-0">
        <div className="flex-1 min-h-0 overflow-y-auto border border-border">
          <table className="sq w-full">
            <colgroup>
              <col style={{ width: '3%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '28%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '4%' }} />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-panel">
              <tr>
                <th className="text-left">#</th>
                <th className="text-left">Provider</th>
                <th className="text-left">Event</th>
                <th className="text-left">Outcome</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Fair</th>
                <th className="text-right">Edge%</th>
                <th className="text-right">Stake</th>
                <th className="text-right">EV</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {/* Sharp section */}
              {sharpBets.length > 0 && (
                <>
                  <tr>
                    <td colSpan={10} className="!py-1 !px-2">
                      <span className="text-[10px] font-bold text-success tracking-wider uppercase">
                        Sharp ({sharpBets.length})
                      </span>
                    </td>
                  </tr>
                  {sharpBets.map((b, i) => renderBetRow(b, i))}
                </>
              )}

              {/* Soft section */}
              {softBets.length > 0 && (
                <>
                  <tr>
                    <td colSpan={10} className="!py-1 !px-2">
                      <span className="text-[10px] font-bold text-tabValue tracking-wider uppercase">
                        Soft ({softBets.length})
                      </span>
                    </td>
                  </tr>
                  {softBets.map((b, i) => renderBetRow(b, sharpBets.length + i))}
                </>
              )}
            </tbody>
          </table>
        </div>

        {/* Actions panel — side by side with batch table */}
        <div className="w-[280px] flex-shrink-0 overflow-y-auto">
          {renderActionsPanel()}
        </div>
        </div>
      )}

      {/* Footer actions */}
      {activeBatch.length > 0 && (
        <div className="flex items-center gap-2 px-2 py-1.5 border-t border-border">
          <button
            onClick={handleFire}
            disabled={isFiring || fireableBets.length === 0}
            className="px-4 py-2 bg-success text-bg text-sm font-bold hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {isFiring
              ? 'Firing...'
              : `Fire playable (${fireableBets.length} bets, +${fireableEV.toFixed(0)} EV)`}
          </button>
          <button
            onClick={handleRebuild}
            disabled={batchFetching}
            className="px-3 py-2 text-xs font-medium border border-border text-muted hover:text-text disabled:opacity-50 transition-colors"
          >
            Rebuild
          </button>
          {balanceStatus.some((bs) => bs.shortfall && bs.shortfall > 0) && (
            <button
              onClick={handleCopyDeposits}
              className="px-3 py-2 text-xs font-medium border border-border text-muted hover:text-text transition-colors"
            >
              Copy deposits
            </button>
          )}
          <span className="ml-auto text-xs text-muted">
            {activeBatch.length} total | {fireableBets.length} fireable | {activeBatch.length - fireableBets.length} blocked
          </span>
        </div>
      )}
    </div>
  );
}
