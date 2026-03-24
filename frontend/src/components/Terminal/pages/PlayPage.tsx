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

  const { data: playSession } = useQuery<PlaySession>({
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

  // Deploy recommendations
  const deployRecs = useMemo(() => {
    if (!playSession) return [];
    return playSession.clusters
      .filter((c) => c.needs_deposit && c.recommended_siblings.length > 0)
      .flatMap((c) => c.recommended_siblings.map((s) => s.provider_id));
  }, [playSession]);

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
    const eventName = `${displayTeamName(b.home_team, b.display_home)} v ${displayTeamName(b.away_team, b.display_away)}`;
    const outcomeLabel = resolveOutcome(b.outcome, { home_team: b.home_team, away_team: b.away_team, display_home: b.display_home, display_away: b.display_away, market: b.market } as any, b.point, true);

    return (
      <tr key={`${key}-${b.provider_id}-${idx}`} className={result ? (result.success ? 'bg-success/5' : 'bg-error/5') : ''}>
        <td className="text-muted text-xs">{b.rank}</td>
        <td className="text-xs">
          <ProviderName name={b.provider_id} />
          {b.is_bonus && (
            <span className="ml-1 text-[9px] px-1 py-0.5 bg-accent/20 text-accent">
              {b.bonus_type === 'freebet' ? 'FREE' : 'TRG'}
            </span>
          )}
        </td>
        <td className="text-xs text-text truncate max-w-[200px]" title={eventName}>
          {eventName}
          <div className="text-[10px] text-muted">
            {b.sport}{b.league ? ` · ${b.league}` : ''}{b.market !== '1x2' && b.market !== 'moneyline' ? ` · ${b.market}` : ''}
          </div>
        </td>
        <td className="text-xs text-text">{outcomeLabel}</td>
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

  function renderBalancePanel() {
    if (balanceStatus.length === 0) return null;

    return (
      <div className="border border-border bg-panel px-3 py-2">
        <div className="text-xs text-muted font-medium mb-1.5">BALANCE ALLOCATION</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-4 gap-y-1">
          {balanceStatus.map((bs) => {
            const hasShortfall = bs.shortfall != null && bs.shortfall > 0;
            return (
              <div key={bs.provider_id} className="flex items-center gap-1.5 text-xs">
                <span className={hasShortfall ? 'text-error' : 'text-success'}>
                  {hasShortfall ? 'x' : 'v'}
                </span>
                <ProviderName name={bs.provider_id} />
                <span className="text-muted ml-auto">
                  {bs.balance.toFixed(0)} &rarr; {bs.remaining.toFixed(0)}
                </span>
                {hasShortfall && (
                  <span className="text-error text-[10px]">
                    needs {bs.shortfall!.toFixed(0)} ({bs.missed_bets}b, +{bs.missed_ev.toFixed(0)})
                  </span>
                )}
              </div>
            );
          })}
        </div>
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

      {/* Batch table */}
      {batchLoading && batch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Building batch...
        </div>
      ) : activeBatch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          No bets in batch. Click "Build Batch" to generate.
        </div>
      ) : (
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
      )}

      {/* Balance panel */}
      {renderBalancePanel()}

      {/* Deploy recommendations */}
      {deployRecs.length > 0 && (
        <div className="text-xs text-muted px-2">
          Deploy: {deployRecs.map((p, i) => (
            <span key={p}>{i > 0 && ', '}<ProviderName name={p} /> +1</span>
          ))}
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
