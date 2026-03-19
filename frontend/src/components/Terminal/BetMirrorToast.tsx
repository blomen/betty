import { useBetMirror, MirroredBet } from '../../hooks/useBetMirror';

function ToastItem({ toast, onDismiss }: { toast: MirroredBet; onDismiss: () => void }) {
  const isError = toast.status === 'error' || toast.status === 'rejected';
  const isDuplicate = toast.status === 'duplicate';

  const borderColor = isError ? 'border-error/40' : isDuplicate ? 'border-muted/40' : 'border-success/40';
  const bgColor = isError ? 'bg-error/10' : isDuplicate ? 'bg-muted/10' : 'bg-success/10';
  const iconColor = isError ? 'text-error' : isDuplicate ? 'text-muted' : 'text-success';
  const icon = isError ? '!' : isDuplicate ? '~' : '✓';

  return (
    <div
      className={`border ${borderColor} ${bgColor} text-xs font-mono px-3 py-2 flex items-center gap-2 cursor-pointer`}
      onClick={onDismiss}
    >
      <span className={`${iconColor} font-bold`}>{icon}</span>
      {toast.status === 'rejected' ? (
        <span className="text-error">Bet rejected by {toast.provider}</span>
      ) : isDuplicate ? (
        <span className="text-muted">Duplicate bet skipped ({toast.confirmation_id})</span>
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
