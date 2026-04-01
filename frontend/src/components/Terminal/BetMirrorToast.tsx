import { useState, useEffect, useCallback } from 'react';
import { useBetMirror, MirroredBet } from '../../hooks/useBetMirror';

type Variant = 'success' | 'error' | 'warning';

const FADEOUT_MS = 500;
const DISMISS_MS = 5000;

const styles: Record<Variant, { border: string; bg: string; text: string; left: string; progress: string; icon: string }> = {
  success: {
    border: 'border-success/30',
    bg: 'from-success/12 to-success/4',
    text: 'text-success',
    left: '#4CAF50',
    progress: 'linear-gradient(90deg, #4CAF50, rgba(76,175,80,0.3))',
    icon: '✓',
  },
  error: {
    border: 'border-error/30',
    bg: 'from-error/12 to-error/4',
    text: 'text-error',
    left: '#EF5350',
    progress: 'linear-gradient(90deg, #EF5350, rgba(239,83,80,0.3))',
    icon: '!',
  },
  warning: {
    border: 'border-warning/30',
    bg: 'from-warning/12 to-warning/4',
    text: 'text-warning',
    left: '#FF9800',
    progress: 'linear-gradient(90deg, #FF9800, rgba(255,152,0,0.3))',
    icon: '~',
  },
};

function getVariant(toast: MirroredBet): Variant {
  if (toast.status === 'error' || toast.status === 'rejected' || toast.error) return 'error';
  if (toast.status === 'duplicate') return 'warning';
  return 'success';
}

function ToastItem({ toast, onDismiss }: { toast: MirroredBet; onDismiss: () => void }) {
  const [fadingOut, setFadingOut] = useState(false);
  const variant = getVariant(toast);
  const s = styles[variant];

  const startFadeOut = useCallback(() => {
    setFadingOut(true);
    setTimeout(onDismiss, FADEOUT_MS);
  }, [onDismiss]);

  useEffect(() => {
    const timer = setTimeout(startFadeOut, DISMISS_MS - FADEOUT_MS);
    return () => clearTimeout(timer);
  }, [startFadeOut]);

  return (
    <div
      className={`
        border ${s.border} bg-gradient-to-br ${s.bg}
        text-xs font-mono px-3 py-2.5 flex items-center gap-2
        relative overflow-hidden cursor-pointer
        ${fadingOut ? 'toast-fade-out' : 'toast-slide-in'}
      `}
      style={{ borderLeftWidth: 3, borderLeftColor: s.left }}
      onClick={startFadeOut}
    >
      <div
        className="absolute bottom-0 left-0 h-[2px]"
        style={{ background: s.progress, animation: `toastProgress ${DISMISS_MS}ms linear forwards` }}
      />
      <span className={`${s.text} font-bold text-sm`}>{s.icon}</span>
      <span className="flex-1 flex items-center gap-1.5 flex-wrap">
        {toast.status === 'rejected' ? (
          <span className="text-error">Bet rejected by {toast.provider}</span>
        ) : toast.status === 'duplicate' ? (
          <span className="text-warning">Duplicate bet skipped ({toast.confirmation_id})</span>
        ) : toast.error ? (
          <span className="text-error">Mirror error: {toast.error}</span>
        ) : (
          <>
            <span className="text-success">Bet captured:</span>
            <span className="text-text">{toast.event}</span>
            <span className="text-muted">{toast.market} {toast.outcome}</span>
            <span className="text-text">@ {toast.odds?.toFixed(2)}</span>
            <span className="text-muted">—</span>
            <span className="text-text">{toast.stake} kr</span>
            {!toast.matched && <span className="text-warning">(unmatched)</span>}
          </>
        )}
      </span>
      <button
        className={`${s.text} opacity-40 hover:opacity-80 text-base leading-none`}
        onClick={(e) => { e.stopPropagation(); startFadeOut(); }}
      >
        ×
      </button>
    </div>
  );
}

function SettlementBanner({ pendingSettlements, confirmSettlements, rejectSettlements }: {
  pendingSettlements: any;
  confirmSettlements: () => Promise<void>;
  rejectSettlements: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);

  if (!pendingSettlements) return null;

  const { provider, wins, losses, total_staked, total_payout, net, settlements } = pendingSettlements;

  const handleConfirm = async () => {
    setConfirming(true);
    await confirmSettlements();
    setConfirming(false);
  };

  return (
    <div className="mx-3 mt-2 border border-tabValue/30 bg-gradient-to-br from-tabValue/10 to-tabValue/4 text-xs font-mono overflow-hidden"
      style={{ borderLeftWidth: 3, borderLeftColor: '#FF9800' }}>
      <div className="px-3 py-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-1">
          <span className="text-tabValue font-bold text-sm">$</span>
          <span className="text-text">
            <span className="text-tabValue font-semibold">{provider}</span>
            {' — '}
            {settlements.length} bet{settlements.length !== 1 ? 's' : ''} to settle:
            {' '}
            <span className="text-success">{wins}W</span>
            {' '}
            <span className="text-error">{losses}L</span>
            {' — '}
            staked {total_staked.toFixed(0)}
            {' → '}
            payout {total_payout.toFixed(0)}
            {' = '}
            <span className={net >= 0 ? 'text-success' : 'text-error'}>
              {net >= 0 ? '+' : ''}{net.toFixed(0)} kr
            </span>
          </span>
        </div>
        <div className="flex gap-1.5 shrink-0">
          <button
            onClick={handleConfirm}
            disabled={confirming}
            className="px-2.5 py-1 text-xs bg-success/20 text-success border border-success/30 hover:bg-success/30 transition"
          >
            {confirming ? '...' : 'Confirm'}
          </button>
          <button
            onClick={rejectSettlements}
            className="px-2.5 py-1 text-xs bg-error/20 text-error border border-error/30 hover:bg-error/30 transition"
          >
            Reject
          </button>
        </div>
      </div>
      {/* Breakdown rows */}
      <div className="border-t border-muted/10 px-3 py-1.5 max-h-40 overflow-y-auto">
        {settlements.map((s: any) => (
          <div key={s.bet_id} className="flex items-center gap-2 py-0.5">
            <span className={`w-4 text-center font-bold ${s.result === 'won' ? 'text-success' : 'text-error'}`}>
              {s.result === 'won' ? 'W' : 'L'}
            </span>
            <span className="text-muted w-8 text-right">#{s.bet_id}</span>
            <span className="flex-1 text-text truncate">{s.event}</span>
            <span className="text-muted w-10 text-right">@{s.odds}</span>
            <span className="text-text w-10 text-right">{s.stake}kr</span>
            <span className={`w-14 text-right ${s.result === 'won' ? 'text-success' : 'text-error'}`}>
              {s.result === 'won' ? `+${s.payout}` : '-' + s.stake}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SyncBanner({ syncAvailable, onDismiss }: {
  syncAvailable: any;
  onDismiss: () => void;
}) {
  useEffect(() => {
    if (!syncAvailable) return;
    const timer = setTimeout(onDismiss, 5000);
    return () => clearTimeout(timer);
  }, [syncAvailable, onDismiss]);

  if (!syncAvailable) return null;

  const { provider, balance, pending_bets, pending_stake } = syncAvailable;

  return (
    <div className="mx-3 mt-2 border border-info/30 bg-gradient-to-br from-info/10 to-info/4 text-xs font-mono"
      style={{ borderLeftWidth: 3, borderLeftColor: '#42A5F5' }}>
      <div className="px-3 py-2.5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-1">
          <span className="text-info font-bold text-sm">~</span>
          <span className="text-text">
            <span className="text-info font-semibold">{provider}</span>
            {' detected — '}
            balance: <span className="text-text font-semibold">{balance.toFixed(0)} kr</span>
            {pending_bets > 0 && (
              <>, {pending_bets} pending bet{pending_bets !== 1 ? 's' : ''} ({pending_stake.toFixed(0)} kr)</>
            )}
            {' — '}
            <span className="text-muted">open bet history to settle</span>
          </span>
        </div>
        <button
          onClick={onDismiss}
          className="px-2 py-1 text-xs text-muted hover:text-text transition"
        >
          dismiss
        </button>
      </div>
    </div>
  );
}

export function BetMirrorToast() {
  const { toasts, dismiss, pendingSettlements, confirmSettlements, rejectSettlements, syncAvailable, dismissSync } = useBetMirror();

  return (
    <div className="flex flex-col">
      <SyncBanner syncAvailable={syncAvailable} onDismiss={dismissSync} />
      <SettlementBanner
        pendingSettlements={pendingSettlements}
        confirmSettlements={confirmSettlements}
        rejectSettlements={rejectSettlements}
      />
      {toasts.length > 0 && (
        <div className="mx-3 mt-2 flex flex-col gap-1">
          {toasts.map(toast => (
            <ToastItem key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
          ))}
        </div>
      )}
    </div>
  );
}
