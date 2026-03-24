import { memo } from 'react';
import { ProviderName } from '../ProviderName';
import type { PlayCluster, PlaySibling } from '@/types';

interface ClusterPanelProps {
  clusters: PlayCluster[];
  activeCluster: string | null;
  activeProvider: string | null;
  onClusterSelect: (id: string | null) => void;
  onProviderSelect: (id: string) => void;
}

function formatBalance(val: number): string {
  if (val >= 1000) return `${(val / 1000).toFixed(1)}k`;
  return Math.round(val).toString();
}

function getBadge(sibling: PlaySibling): { text: string; color: string } | null {
  switch (sibling.lifecycle) {
    case 'deposited':
      if (sibling.trigger_mode === 'single') {
        return { text: `TRG ${sibling.bonus_amount}kr`, color: 'text-amber-400' };
      }
      return { text: `TRG ${sibling.wagering_progress_pct}%`, color: 'text-amber-400' };
    case 'freebet':
      return { text: 'FREE', color: 'text-blue-400' };
    case 'wagering':
      return { text: `WAGER ${sibling.wagering_progress_pct}%`, color: 'text-purple-400' };
    case 'limited':
      return { text: 'LTD', color: 'text-red-400' };
    default:
      return null;
  }
}

const ProviderCard = memo(function ProviderCard({
  sibling,
  isActive,
  onClick,
}: {
  sibling: PlaySibling;
  isActive: boolean;
  onClick: () => void;
}) {
  const progressPct = Math.min(100, sibling.wagering_progress_pct);
  const hasWagering = sibling.wagering_remaining > 0;
  const hasBalance = sibling.balance > 0;
  const badge = getBadge(sibling);

  return (
    <button
      onClick={onClick}
      className={`flex-shrink-0 px-3 py-2 rounded border text-left transition-all min-w-[140px] ${
        isActive
          ? 'border-tabValue bg-tabValue/10'
          : 'border-border hover:border-muted bg-surface'
      } ${!hasBalance ? 'opacity-50' : ''}`}
    >
      <div className="flex items-center gap-1.5 mb-1">
        <ProviderName name={sibling.provider_id} className="text-xs font-medium text-text" />
        {badge && (
          <span className={`text-[9px] px-1 py-0.5 bg-muted/20 rounded ${badge.color}`}>
            {badge.text}
          </span>
        )}
      </div>

      <div className="flex items-center justify-between text-[11px] mb-1">
        <span className={hasBalance ? 'text-text' : 'text-muted'}>{formatBalance(sibling.balance)} kr</span>
        {sibling.days_remaining !== null && (
          <span className="text-muted">{sibling.days_remaining}d left</span>
        )}
      </div>

      {hasWagering && (
        <div className="space-y-0.5">
          <div className="h-1.5 bg-muted/20 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${progressPct}%`,
                backgroundColor: progressPct >= 80 ? '#22c55e' : progressPct >= 40 ? '#f59e0b' : '#ef4444',
              }}
            />
          </div>
          <div className="flex justify-between text-[10px] text-muted">
            <span>{Math.round(progressPct)}% wagered</span>
            <span>{formatBalance(sibling.wagering_remaining)} left</span>
          </div>
        </div>
      )}

      {!hasWagering && (
        <div className="text-[10px] text-success">Wagering cleared</div>
      )}
    </button>
  );
});

export const ClusterPanel = memo(function ClusterPanel({
  clusters,
  activeCluster,
  activeProvider,
  onClusterSelect,
  onProviderSelect,
}: ClusterPanelProps) {
  const selectedCluster = clusters.find(c => c.id === activeCluster) ?? null;
  const activeSiblings = selectedCluster?.active_siblings ?? [];

  const totalBalance = activeSiblings.reduce((sum, s) => sum + s.balance, 0);
  const totalWageringRemaining = activeSiblings.reduce((sum, s) => sum + s.wagering_remaining, 0);

  return (
    <div className="space-y-2 mb-3">
      {/* Play pills */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[11px] text-muted mr-1">Play</span>
        {clusters.filter(c => c.playable_count > 0).map(c => (
          <button
            key={c.id}
            onClick={() => onClusterSelect(activeCluster === c.id ? null : c.id)}
            className={`text-[11px] px-2 py-0.5 rounded border transition-colors ${
              activeCluster === c.id
                ? 'border-tabValue bg-tabValue/15 text-tabValue'
                : 'border-border hover:border-muted text-muted hover:text-text'
            }`}
          >
            {c.label}
            <span className="ml-1 text-muted2">{c.playable_count}</span>
          </button>
        ))}
        {clusters.some(c => c.needs_deposit) && (
          <>
            <span className="text-[11px] text-muted ml-2 mr-1">Deploy</span>
            {clusters.filter(c => c.needs_deposit).map(c => (
              <button
                key={`dep-${c.id}`}
                onClick={() => onClusterSelect(activeCluster === c.id ? null : c.id)}
                className={`text-[11px] px-2 py-0.5 rounded border border-dashed transition-colors ${
                  activeCluster === c.id
                    ? 'border-success/50 bg-success/10 text-success'
                    : 'border-border/50 text-muted/60 hover:text-muted hover:border-muted'
                }`}
              >
                {c.label}
                <span className="ml-1 text-success/60">+{c.recommended_siblings.length}</span>
              </button>
            ))}
          </>
        )}
      </div>

      {/* Provider queue */}
      {activeCluster && activeSiblings.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="text-[11px] text-muted">
              {activeSiblings.length} providers · {formatBalance(totalBalance)} kr total · {formatBalance(totalWageringRemaining)} kr wagering left
            </div>
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-thin">
            {activeSiblings.map(s => (
              <ProviderCard
                key={s.provider_id}
                sibling={s}
                isActive={activeProvider === s.provider_id}
                onClick={() => onProviderSelect(s.provider_id)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Recommended siblings to deposit on */}
      {selectedCluster?.needs_deposit && selectedCluster.recommended_siblings.length > 0 && (
        <div className="flex items-center gap-2 text-[11px] px-1">
          <span className="text-muted">Deposit on:</span>
          {selectedCluster.recommended_siblings.map(s => (
            <span key={s.provider_id} className="text-success/80 border border-dashed border-success/30 px-2 py-0.5 rounded">
              <ProviderName name={s.provider_id} className="inline" />
              <span className="text-muted ml-1">({selectedCluster.unique_opps} opps)</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
});
