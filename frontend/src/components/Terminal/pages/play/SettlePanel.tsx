import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { PendingProvider, PendingBet } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  onContinue: () => void;
  pendingCount: number;
  setPendingCount: (n: number) => void;
}

type BetSettleState = 'pending' | 'won' | 'lost' | 'void' | 'auto-settled';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('sv-SE', { month: 'short', day: 'numeric' });
}

function resultColor(result: string): string {
  if (result === 'won') return 'text-success';
  if (result === 'lost') return 'text-red-400';
  if (result === 'void') return 'text-amber-400';
  return 'text-muted';
}

// ---------------------------------------------------------------------------
// BetRow
// ---------------------------------------------------------------------------

function BetRow({
  bet,
  state,
  onSettle,
  isSettling,
}: {
  bet: PendingBet;
  state: BetSettleState;
  onSettle: (result: 'won' | 'lost' | 'void') => void;
  isSettling: boolean;
}) {
  const settled = state !== 'pending';

  return (
    <div
      className={`flex items-center gap-3 px-3 py-1.5 border-b border-border/50 transition-opacity ${
        settled ? 'opacity-50' : ''
      }`}
    >
      {/* Event info */}
      <div className="flex-1 min-w-0">
        <div className="text-sm text-text truncate">{bet.event_name}</div>
        <div className="text-[11px] text-muted flex items-center gap-2">
          {bet.market && <span>{bet.market}</span>}
          {bet.outcome && <span className="text-text">{bet.outcome}</span>}
        </div>
      </div>

      {/* Odds + Stake */}
      <div className="text-right flex-shrink-0">
        <div className="text-sm text-text font-medium">{bet.odds.toFixed(2)}</div>
        <div className="text-[11px] text-muted">
          {bet.stake.toFixed(0)} {bet.currency === 'USDC' ? 'USDC' : 'kr'}
        </div>
      </div>

      {/* Date */}
      <div className="text-[11px] text-muted2 w-12 text-right flex-shrink-0">
        {formatDate(bet.placed_at)}
      </div>

      {/* Actions or result */}
      <div className="flex items-center gap-1 flex-shrink-0 w-20 justify-end">
        {settled ? (
          <span className={`text-[10px] font-medium uppercase ${resultColor(state)}`}>
            {state === 'auto-settled' ? '✓ auto' : `✓ ${state}`}
          </span>
        ) : (
          <>
            <button
              onClick={() => onSettle('won')}
              disabled={isSettling}
              className="px-1.5 py-0.5 text-[10px] font-medium border border-success/40 text-success hover:bg-success/10 disabled:opacity-30 transition-colors"
              title="Won"
            >
              W
            </button>
            <button
              onClick={() => onSettle('lost')}
              disabled={isSettling}
              className="px-1.5 py-0.5 text-[10px] font-medium border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-30 transition-colors"
              title="Lost"
            >
              L
            </button>
            <button
              onClick={() => onSettle('void')}
              disabled={isSettling}
              className="px-1.5 py-0.5 text-[10px] font-medium border border-amber-500/40 text-amber-400 hover:bg-amber-500/10 disabled:opacity-30 transition-colors"
              title="Void"
            >
              V
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ProviderGroup
// ---------------------------------------------------------------------------

function ProviderGroup({
  group,
  settledBets,
  settlingBetId,
  onSettle,
}: {
  group: PendingProvider;
  settledBets: Record<number, BetSettleState>;
  settlingBetId: number | null;
  onSettle: (betId: number, result: 'won' | 'lost' | 'void') => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  const unsettledCount = group.bets.filter(b => !settledBets[b.id]).length;

  return (
    <div className="border border-border bg-panel mb-1">
      {/* Header */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-panel2/50 transition-colors"
      >
        <span className="text-[11px] text-muted">{collapsed ? '▸' : '▾'}</span>
        <span className="text-sm font-medium text-text">{group.provider_id}</span>
        <span className={`text-[10px] px-1.5 py-0.5 ${
          unsettledCount > 0 ? 'bg-amber-500/20 text-amber-400' : 'bg-success/20 text-success'
        }`}>
          {unsettledCount > 0 ? `${unsettledCount} pending` : 'all settled'}
        </span>
        <span className="text-sm text-muted ml-auto">
          {group.total_stake.toFixed(0)} kr staked
        </span>
      </button>

      {/* Bet rows */}
      {!collapsed && group.bets.map(bet => (
        <BetRow
          key={bet.id}
          bet={bet}
          state={settledBets[bet.id] || 'pending'}
          onSettle={(result) => onSettle(bet.id, result)}
          isSettling={settlingBetId === bet.id}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SettlePanel
// ---------------------------------------------------------------------------

interface MirrorSettlement {
  bet_id: number;
  provider: string;
  event: string;
  odds: number;
  stake: number;
  result: string;
  payout: number;
}

export function SettlePanel({ onContinue, setPendingCount }: Props) {
  const queryClient = useQueryClient();
  const [settledBets, setSettledBets] = useState<Record<number, BetSettleState>>({});
  const [settlingBetId, setSettlingBetId] = useState<number | null>(null);
  const [mirrorSettlements, setMirrorSettlements] = useState<MirrorSettlement[]>([]);
  const [isConfirming, setIsConfirming] = useState(false);

  // Fetch pending bets
  const { data, isLoading } = useQuery({
    queryKey: ['pending-bets'],
    queryFn: () => api.getPendingBets(),
    staleTime: 30_000,
  });

  // Update pending count for step indicator
  useEffect(() => {
    if (data) {
      const remaining = data.total_pending - Object.keys(settledBets).length;
      setPendingCount(Math.max(0, remaining));
    }
  }, [data, settledBets, setPendingCount]);

  // Manual settle mutation
  const settleMutation = useMutation({
    mutationFn: ({ betId, result }: { betId: number; result: 'won' | 'lost' | 'void' }) =>
      api.settleBet(betId, result),
    onMutate: ({ betId }) => setSettlingBetId(betId),
    onSuccess: (resp) => {
      setSettledBets(prev => ({ ...prev, [resp.bet_id]: resp.result as BetSettleState }));
      setSettlingBetId(null);
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
    },
    onError: () => setSettlingBetId(null),
  });

  // Listen for mirror settlements via SSE — stage for review, don't auto-confirm
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    es.addEventListener('settlements_pending', (e: MessageEvent) => {
      const payload = JSON.parse(e.data);
      const settlements = payload.settlements || [];
      if (settlements.length > 0) {
        setMirrorSettlements(settlements);
      }
    });

    return () => es.close();
  }, []);

  const handleConfirmMirror = useCallback(() => {
    setIsConfirming(true);
    api.confirmMirrorSettlements().then(() => {
      const autoSettled: Record<number, BetSettleState> = {};
      for (const s of mirrorSettlements) {
        autoSettled[s.bet_id] = 'auto-settled';
      }
      setSettledBets(prev => ({ ...prev, ...autoSettled }));
      setMirrorSettlements([]);
      setIsConfirming(false);
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['pending-bets'] });
    }).catch(err => {
      console.error('[settle] confirm failed', err);
      setIsConfirming(false);
    });
  }, [mirrorSettlements, queryClient]);

  const handleRejectMirror = useCallback(() => {
    fetch('/api/mirror/settlements/reject', { method: 'POST' });
    setMirrorSettlements([]);
  }, []);

  const [isScanning, setIsScanning] = useState(false);
  const handleScanPage = useCallback(() => {
    setIsScanning(true);
    fetch('/api/mirror/scrape-page-bets')
      .then(r => r.json())
      .then(d => {
        const staged = d?.data?.staged || 0;
        if (staged === 0) {
          console.log('[settle] scan found no matching pending bets');
        }
        // SSE will deliver the settlements_pending event
        setIsScanning(false);
      })
      .catch(err => {
        console.error('[settle] scan failed', err);
        setIsScanning(false);
      });
  }, []);

  const handleSettle = useCallback((betId: number, result: 'won' | 'lost' | 'void') => {
    settleMutation.mutate({ betId, result });
  }, [settleMutation]);

  if (isLoading) {
    return <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading pending bets...</div>;
  }

  const providers = data?.providers || [];
  const totalPending = data?.total_pending || 0;
  const settledCount = Object.keys(settledBets).length;
  const remaining = totalPending - settledCount;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 border border-border bg-panel text-sm">
        <div className="flex items-center gap-3">
          {totalPending > 0 ? (
            <>
              <span className="text-warning font-medium">{remaining}</span>
              <span className="text-muted">unsettled</span>
              {settledCount > 0 && (
                <span className="text-muted">({settledCount} done)</span>
              )}
            </>
          ) : (
            <span className="text-success font-medium">No pending bets</span>
          )}
        </div>
        {totalPending > 0 && (
          <button
            onClick={handleScanPage}
            disabled={isScanning}
            className="px-2 py-0.5 text-[9px] font-medium border border-accent/40 text-accent hover:bg-accent/10 disabled:opacity-30 transition-colors"
            title="Scan mirror browser page for bet results"
          >
            {isScanning ? 'Scanning...' : 'Scan page'}
          </button>
        )}
      </div>

      {/* Provider groups */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {providers.map(group => (
          <ProviderGroup
            key={group.provider_id}
            group={group}
            settledBets={settledBets}
            settlingBetId={settlingBetId}
            onSettle={handleSettle}
          />
        ))}

        {/* Mirror settlement review banner */}
        {mirrorSettlements.length > 0 && (
          <div className="border border-accent/30 bg-accent/5 p-3 mx-0">
            <div className="text-xs font-medium text-accent mb-2">
              Mirror detected {mirrorSettlements.length} settlement{mirrorSettlements.length > 1 ? 's' : ''}
            </div>
            <div className="space-y-1 mb-3">
              {mirrorSettlements.map(s => {
                const wins = s.result === 'won';
                return (
                  <div key={s.bet_id} className="flex items-center gap-2 text-[11px]">
                    <span className={`font-medium w-8 ${wins ? 'text-success' : s.result === 'void' ? 'text-amber-400' : 'text-red-400'}`}>
                      {s.result.toUpperCase()}
                    </span>
                    <span className="text-text truncate flex-1">{s.event}</span>
                    <span className="text-muted">{s.odds.toFixed(2)}</span>
                    <span className="text-muted">{s.stake.toFixed(0)} kr</span>
                    {wins && <span className="text-success">+{s.payout.toFixed(0)}</span>}
                  </div>
                );
              })}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleConfirmMirror}
                disabled={isConfirming}
                className="px-3 py-1 text-xs font-medium bg-accent text-bg hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {isConfirming ? 'Confirming...' : `Confirm ${mirrorSettlements.length} settlements`}
              </button>
              <button
                onClick={handleRejectMirror}
                className="px-3 py-1 text-xs text-muted border border-border hover:bg-panel transition-colors"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Continue button */}
      <div className="flex items-center justify-end px-1 py-1">
        <button
          onClick={onContinue}
          className="px-4 py-1.5 text-xs bg-tabPlay text-bg font-medium hover:opacity-90 transition-opacity"
        >
          {remaining > 0
            ? `Continue with ${remaining} unsettled →`
            : totalPending > 0
            ? 'All settled — Continue →'
            : 'No pending bets — Continue →'}
        </button>
      </div>
    </div>
  );
}
