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
        shadow-[0_0_20px_rgba(0,0,0,0.08),0_4px_12px_rgba(0,0,0,0.3)]
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

export function BetMirrorToast() {
  const { toasts, dismiss } = useBetMirror();

  if (toasts.length === 0) return null;

  return (
    <div className="mx-3 mt-2 flex flex-col gap-1">
      {toasts.map(toast => (
        <ToastItem key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
      ))}
    </div>
  );
}
