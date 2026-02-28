import { useTiersProgress } from '@/hooks/useExtractionStatus';

const TIER_LABELS: Record<string, string> = {
  sharp: 'sharp',
  api_soft: 'soft',
  browser_soft: 'browser',
  boosts: 'specials',
};

const TIER_ORDER = ['sharp', 'api_soft', 'browser_soft', 'boosts'];

export function ExtractionProgressBar() {
  const tiersProgress = useTiersProgress();

  if (!tiersProgress) return null;

  // Find all running tiers
  const runningTiers: string[] = [];
  let totalProviders = 0;
  let completedProviders = 0;

  for (const key of TIER_ORDER) {
    const tier = tiersProgress.tiers[key];
    if (!tier) continue;
    if (tier.running) {
      runningTiers.push(TIER_LABELS[key] || key);
      totalProviders += tier.total_providers;
      completedProviders += tier.completed_providers;
    }
  }

  if (runningTiers.length === 0) return null;

  // Compute overall progress from all running tiers combined
  const pct = totalProviders > 0
    ? Math.min((completedProviders / totalProviders) * 100, 100)
    : 0;
  const filled = Math.round((pct / 100) * 24);

  return (
    <div className="border-l-2 border-tabExtract mb-3 px-3 py-1.5 text-xs font-mono flex items-center gap-2">
      <span className="text-tabExtract animate-blink">&#9632;</span>
      <span className="text-tabExtract/60 tracking-tight">
        {'█'.repeat(filled)}{'░'.repeat(24 - filled)}
      </span>
      <span className="text-muted">{pct.toFixed(0)}%</span>
      <span className="text-muted2">{runningTiers.join(' · ')}</span>
    </div>
  );
}
