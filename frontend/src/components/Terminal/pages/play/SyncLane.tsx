import { useSyncStream } from '../../../../hooks/useSyncStream';
import type { PendingBet, Settlement } from '../../../../hooks/useSyncStream';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SyncLaneProps {
  providerId: string | null;
  onConfirmSettlements: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso: string | null): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

function resultColor(result: Settlement['result']): string {
  if (result === 'won') return 'text-green-400';
  if (result === 'lost') return 'text-red-400';
  return 'text-amber-400';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ConnectionIndicator({ connected }: { connected: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <span
        className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
          connected ? 'bg-green-500' : 'bg-red-500'
        }`}
      />
      <span className={`text-xs ${connected ? 'text-zinc-400' : 'text-red-400'}`}>
        {connected ? 'Streaming' : 'Disconnected'}
      </span>
    </div>
  );
}

function BalanceSection({
  amount,
  currency,
  updatedAt,
}: {
  amount: number;
  currency: string;
  updatedAt: string | null;
}) {
  const formatted = amount.toLocaleString('sv-SE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (
    <div className="py-1">
      <div className="text-blue-400 text-[10px] uppercase tracking-wide mb-0.5">Balance</div>
      <div className="text-2xl font-semibold text-zinc-100 leading-none">
        {formatted}
        {currency && <span className="text-sm text-zinc-400 ml-1.5 font-normal">{currency}</span>}
      </div>
      {updatedAt && (
        <div className="text-[11px] text-zinc-500 mt-0.5">updated {relativeTime(updatedAt)}</div>
      )}
    </div>
  );
}

function PendingBetRow({ bet }: { bet: PendingBet }) {
  return (
    <div className="flex items-center gap-2 px-2 py-1.5 border-b border-zinc-800/60">
      <div className="flex-1 min-w-0">
        <div className="text-xs text-zinc-200 truncate">{bet.event_id}</div>
        <div className="text-[11px] text-zinc-500 flex items-center gap-1.5">
          {bet.market && <span>{bet.market}</span>}
          {bet.outcome && <span className="text-zinc-400">{bet.outcome}</span>}
        </div>
      </div>
      <div className="text-right flex-shrink-0">
        <div className="text-xs text-zinc-200 font-medium">{bet.odds.toFixed(2)}</div>
        <div className="text-[11px] text-zinc-500">{bet.stake.toFixed(0)}</div>
      </div>
    </div>
  );
}

function PendingBetsSection({ bets }: { bets: PendingBet[] }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-blue-400 text-[10px] uppercase tracking-wide">Pending bets</span>
        {bets.length > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 bg-zinc-700/60 text-zinc-300">
            {bets.length}
          </span>
        )}
      </div>
      <div className="border border-zinc-800 bg-zinc-900/40">
        {bets.length === 0 ? (
          <div className="px-2 py-2 text-xs text-zinc-600">No pending bets</div>
        ) : (
          bets.map(bet => <PendingBetRow key={bet.id} bet={bet} />)
        )}
      </div>
    </div>
  );
}

function SettlementRow({ settlement }: { settlement: Settlement }) {
  const won = settlement.result === 'won';
  return (
    <div className="flex items-center gap-2 px-2 py-1.5 border-b border-orange-900/30">
      <span className={`text-[10px] font-semibold uppercase w-8 flex-shrink-0 ${resultColor(settlement.result)}`}>
        {settlement.result}
      </span>
      <span className="text-[11px] text-zinc-400 flex-1 min-w-0 truncate">
        bet #{settlement.bet_id ?? '—'}
      </span>
      {won && settlement.payout > 0 && (
        <span className="text-xs text-green-400 flex-shrink-0">+{settlement.payout.toFixed(2)}</span>
      )}
    </div>
  );
}

function SettlementGate({
  settlements,
  onConfirm,
}: {
  settlements: Settlement[];
  onConfirm: () => void;
}) {
  if (settlements.length === 0) return null;

  return (
    <div className="border border-orange-500/40 bg-orange-950/20 p-2.5">
      <div className="text-[10px] font-medium text-orange-400 uppercase tracking-wide mb-1.5">
        Settlement gate — {settlements.length} pending
      </div>
      <div className="mb-2">
        {settlements.map(s => (
          <SettlementRow key={s.id} settlement={s} />
        ))}
      </div>
      <button
        onClick={onConfirm}
        className="w-full px-3 py-1.5 text-xs font-medium bg-orange-500/20 text-orange-400 border border-orange-500/40 hover:bg-orange-500/30 transition-colors"
      >
        Confirm All
      </button>
    </div>
  );
}

function NotificationRow({ label, muted }: { label: string; muted: boolean }) {
  return (
    <div className="flex items-center justify-between py-1 border-b border-zinc-800/50">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className={`text-[10px] font-medium ${muted ? 'text-green-500' : 'text-orange-400'}`}>
        {muted ? 'muted' : 'pending'}
      </span>
    </div>
  );
}

function NotificationsSection({
  notifications,
}: {
  notifications: { email: boolean; sms: boolean; push: boolean };
}) {
  return (
    <div>
      <div className="text-blue-400 text-[10px] uppercase tracking-wide mb-1">Notifications</div>
      <div className="border border-zinc-800 bg-zinc-900/40 px-2">
        <NotificationRow label="Email" muted={notifications.email} />
        <NotificationRow label="SMS" muted={notifications.sms} />
        <NotificationRow label="Push" muted={notifications.push} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SyncLane
// ---------------------------------------------------------------------------

export function SyncLane({ providerId, onConfirmSettlements }: SyncLaneProps) {
  const { balance, pendingBets, settlements, notifications, connected } = useSyncStream(providerId);

  return (
    <div
      className="flex flex-col gap-3 p-3 border-r border-zinc-800 overflow-y-auto"
      style={{ flex: 1 }}
    >
      <ConnectionIndicator connected={connected} />

      <BalanceSection
        amount={balance.amount}
        currency={balance.currency}
        updatedAt={balance.updatedAt}
      />

      <PendingBetsSection bets={pendingBets} />

      <SettlementGate settlements={settlements} onConfirm={onConfirmSettlements} />

      <NotificationsSection notifications={notifications} />
    </div>
  );
}
