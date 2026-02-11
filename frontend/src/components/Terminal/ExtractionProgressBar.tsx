import { useTiersProgress } from '@/hooks/useExtractionStatus';

function formatProviderName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

const TIER_CONFIG: Record<string, { label: string; color: string; icon: string }> = {
  sharp:        { label: 'SHARP',   color: 'text-emerald-400', icon: '◆' },
  api_soft:     { label: 'SOFT',    color: 'text-blue-400',    icon: '●' },
  browser_soft: { label: 'BROWSER', color: 'text-amber-400',   icon: '●' },
};

function TierBar({ name, running, progressPct, currentProvider, completedProviders, totalProviders, lastRun, totalEvents }: {
  name: string;
  running: boolean;
  progressPct: number;
  currentProvider: string | null;
  completedProviders: number;
  totalProviders: number;
  lastRun: string | null;
  totalEvents: number;
}) {
  const config = TIER_CONFIG[name] || { label: name.toUpperCase(), color: 'text-muted', icon: '●' };
  const filled = Math.round((progressPct / 100) * 12);

  if (running) {
    return (
      <div className="flex items-center gap-2 text-xs font-mono">
        <span className={`${config.color} animate-blink`}>{config.icon}</span>
        <span className={`${config.color} font-semibold w-16`}>{config.label}</span>
        <span className={`${config.color}/60`}>
          {'█'.repeat(filled)}{'░'.repeat(12 - filled)}
        </span>
        <span className="text-muted w-8 text-right">{progressPct.toFixed(0)}%</span>
        {currentProvider && (
          <span className="text-muted2 truncate max-w-[120px]">{formatProviderName(currentProvider)}</span>
        )}
        <span className="text-muted2 ml-auto">{completedProviders}/{totalProviders}</span>
      </div>
    );
  }

  // Idle tier — show last run summary
  if (lastRun) {
    const ago = getTimeAgo(lastRun);
    return (
      <div className="flex items-center gap-2 text-xs font-mono">
        <span className="text-muted2/50">{config.icon}</span>
        <span className="text-muted2/60 w-16">{config.label}</span>
        <span className="text-muted2/40">{'░'.repeat(12)}</span>
        <span className="text-muted2/50 ml-auto">
          {totalEvents} {name === 'sharp' ? 'events' : 'pin matched'} | {ago}
        </span>
      </div>
    );
  }

  // No data yet
  return (
    <div className="flex items-center gap-2 text-xs font-mono">
      <span className="text-muted2/30">{config.icon}</span>
      <span className="text-muted2/40 w-16">{config.label}</span>
      <span className="text-muted2/30">{'░'.repeat(12)}</span>
      <span className="text-muted2/30 ml-auto">waiting</span>
    </div>
  );
}

function getTimeAgo(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

interface ExtractionProgressBarProps {
  /** Which tiers to display. Defaults to all tiers. */
  tiers?: string[];
}

export function ExtractionProgressBar({ tiers: visibleTiers }: ExtractionProgressBarProps = {}) {
  const tiersProgress = useTiersProgress();

  // Show nothing until first poll
  if (!tiersProgress || Object.keys(tiersProgress.tiers).length === 0) return null;

  const allTierOrder = ['sharp', 'api_soft', 'browser_soft'];
  // Filter to only requested tiers (or all if not specified)
  const tierOrder = visibleTiers
    ? allTierOrder.filter(name => visibleTiers.includes(name))
    : allTierOrder;

  const tiers = tierOrder
    .filter(name => name in tiersProgress.tiers)
    .map(name => ({ name, ...tiersProgress.tiers[name] }));

  // If no tiers have any data, skip rendering
  if (tiers.length === 0) return null;

  return (
    <div className="bg-panel2 border border-border rounded px-4 py-2 mb-3 space-y-1">
      {tiers.map(tier => (
        <TierBar
          key={tier.name}
          name={tier.name}
          running={tier.running}
          progressPct={tier.progress_pct}
          currentProvider={tier.current_provider}
          completedProviders={tier.completed_providers}
          totalProviders={tier.total_providers}
          lastRun={tier.last_run}
          totalEvents={tier.total_events}
        />
      ))}
    </div>
  );
}
