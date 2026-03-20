import { useEffect, useState, useCallback } from 'react';

export type ToastVariant = 'success' | 'error' | 'warning';

export interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
}

const DISMISS_MS = 5000;
const FADEOUT_MS = 500;

const variantStyles: Record<ToastVariant, { border: string; bg: string; text: string; glow: string; icon: string }> = {
  success: {
    border: 'border-success/30',
    bg: 'from-success/12 to-success/4',
    text: 'text-success',
    glow: 'shadow-[0_0_20px_rgba(76,175,80,0.08),0_4px_12px_rgba(0,0,0,0.3)]',
    icon: '✓',
  },
  error: {
    border: 'border-error/30',
    bg: 'from-error/12 to-error/4',
    text: 'text-error',
    glow: 'shadow-[0_0_20px_rgba(239,83,80,0.08),0_4px_12px_rgba(0,0,0,0.3)]',
    icon: '!',
  },
  warning: {
    border: 'border-warning/30',
    bg: 'from-warning/12 to-warning/4',
    text: 'text-warning',
    glow: 'shadow-[0_0_20px_rgba(255,152,0,0.08),0_4px_12px_rgba(0,0,0,0.3)]',
    icon: '~',
  },
};

const borderLeftColors: Record<ToastVariant, string> = {
  success: '#4CAF50',
  error: '#EF5350',
  warning: '#FF9800',
};

const progressColors: Record<ToastVariant, string> = {
  success: 'linear-gradient(90deg, #4CAF50, rgba(76,175,80,0.3))',
  error: 'linear-gradient(90deg, #EF5350, rgba(239,83,80,0.3))',
  warning: 'linear-gradient(90deg, #FF9800, rgba(255,152,0,0.3))',
};

function SingleToast({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  const [fadingOut, setFadingOut] = useState(false);
  const s = variantStyles[item.variant];

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
        ${s.glow} relative overflow-hidden cursor-pointer
        ${fadingOut ? 'toast-fade-out' : 'toast-slide-in'}
      `}
      style={{ borderLeftWidth: 3, borderLeftColor: borderLeftColors[item.variant] }}
      onClick={startFadeOut}
    >
      {/* Progress bar */}
      <div
        className="absolute bottom-0 left-0 h-[2px]"
        style={{
          background: progressColors[item.variant],
          animation: `toastProgress ${DISMISS_MS}ms linear forwards`,
        }}
      />
      <span className={`${s.text} font-bold text-sm`}>{s.icon}</span>
      <span className={`${s.text} flex-1`}>{item.message}</span>
      <button
        className={`${s.text} opacity-40 hover:opacity-80 text-base leading-none`}
        onClick={(e) => { e.stopPropagation(); startFadeOut(); }}
      >
        ×
      </button>
    </div>
  );
}

/** Inline toast container — render where toasts should appear */
export function ToastContainer({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: string) => void }) {
  if (toasts.length === 0) return null;

  return (
    <div className="flex flex-col gap-1">
      {toasts.map(t => (
        <SingleToast key={t.id} item={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

/** Hook for managing toast state */
let toastCounter = 0;
export function useToast() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = useCallback((message: string, variant: ToastVariant = 'success') => {
    const id = `toast-${++toastCounter}-${Date.now()}`;
    setToasts(prev => [...prev, { id, message, variant }]);
    return id;
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const clearToasts = useCallback(() => {
    setToasts([]);
  }, []);

  return { toasts, addToast, dismissToast, clearToasts };
}
