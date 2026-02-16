import { useTiersProgress } from '@/hooks/useExtractionStatus';

function formatProviderName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

const TIER_CONFIG: Record<string, { label: string; color: string }> = {
  sharp:        { label: 'SHARP',   color: '#4CAF50' },
  api_soft:     { label: 'SOFT',    color: '#64B5F6' },
  browser_soft: { label: 'BROWSER', color: '#FF9800' },
};

function TierRow({ name, running, progressPct, currentProvider, completedProviders, totalProviders, lastRun, totalEvents }: {
  name: string;
  running: boolean;
  progressPct: number;
  currentProvider: string | null;
  completedProviders: number;
  totalProviders: number;
  lastRun: string | null;
  totalEvents: number;
}) {
  const config = TIER_CONFIG[name] || { label: name.toUpperCase(), color: '#9AA0A6' };
  const filled = Math.round((progressPct / 100) * 12);

  if (running) {
    return (
      <tr>
        <td className="whitespace-nowrap">
          <span style={{ color: config.color }} className="animate-blink">&#9632;</span>
          {' '}<span style={{ color: config.color }} className="font-semibold">{config.label}</span>
        </td>
        <td className="font-mono whitespace-nowrap" style={{ color: config.color, opacity: 0.6 }}>
          {'█'.repeat(filled)}{'░'.repeat(12 - filled)}
        </td>
        <td className="text-right text-muted whitespace-nowrap">{progressPct.toFixed(0)}%</td>
        <td className="text-muted2 truncate max-w-[140px]">
          {currentProvider ? formatProviderName(currentProvider) : ''}
        </td>
        <td className="text-right text-muted2 whitespace-nowrap">{completedProviders}/{totalProviders}</td>
      </tr>
    );
  }

  if (lastRun) {
    const ago = getTimeAgo(lastRun);
    return (
      <tr className="opacity-50">
        <td className="whitespace-nowrap">
          <span className="text-muted2">&#9632;</span>
          {' '}<span className="text-muted2">{config.label}</span>
        </td>
        <td className="font-mono whitespace-nowrap text-muted2/40">{'░'.repeat(12)}</td>
        <td></td>
        <td className="text-muted2" colSpan={2}>
          {totalEvents} {name === 'sharp' ? 'events' : 'pin matched'} | {ago}
        </td>
      </tr>
    );
  }

  return (
    <tr className="opacity-30">
      <td className="whitespace-nowrap">
        <span className="text-muted2">&#9632;</span>
        {' '}<span className="text-muted2">{config.label}</span>
      </td>
      <td className="font-mono whitespace-nowrap text-muted2/30">{'░'.repeat(12)}</td>
      <td></td>
      <td className="text-muted2" colSpan={2}>waiting</td>
    </tr>
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
  tiers?: string[];
}

export function ExtractionProgressBar({ tiers: visibleTiers }: ExtractionProgressBarProps = {}) {
  const tiersProgress = useTiersProgress();

  if (!tiersProgress || Object.keys(tiersProgress.tiers).length === 0) return null;

  const allTierOrder = ['sharp', 'api_soft', 'browser_soft'];
  const tierOrder = visibleTiers
    ? allTierOrder.filter(name => visibleTiers.includes(name))
    : allTierOrder;

  const tiers = tierOrder
    .filter(name => name in tiersProgress.tiers)
    .map(name => ({ name, ...tiersProgress.tiers[name] }));

  if (tiers.length === 0) return null;

  // Hide entirely when nothing is actively running
  const anyRunning = tiers.some(t => t.running);
  if (!anyRunning) return null;

  return (
    <div className="border-l-2 border-tabExtract mb-3">
    <table className="sq text-xs">
      <thead>
        <tr>
          <th style={{ width: '100px' }}>Tier</th>
          <th style={{ width: '150px' }}>Progress</th>
          <th style={{ width: '50px' }} className="text-right">%</th>
          <th>Current</th>
          <th style={{ width: '60px' }} className="text-right">Done</th>
        </tr>
      </thead>
      <tbody>
        {tiers.map(tier => (
          <TierRow
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
      </tbody>
    </table>
    </div>
  );
}
