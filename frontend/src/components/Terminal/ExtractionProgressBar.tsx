import { useExtractionProgress, useTiersProgress } from '@/hooks/useExtractionStatus';

const TIER_LABELS: Record<string, string> = {
  sharp: 'sharp',
  api_soft: 'soft',
  browser_soft: 'browser',
  boosts: 'specials',
};

const TIER_ORDER = ['sharp', 'api_soft', 'browser_soft', 'boosts'];

export function ExtractionProgressBar() {
  const progress = useExtractionProgress();
  const tiersProgress = useTiersProgress();

  const anyTierRunning = tiersProgress ? Object.values(tiersProgress.tiers).some(t => t.running) : false;
  if (!anyTierRunning && (!progress || !progress.running)) return null;

  const pct = Math.min(progress?.progress_pct ?? 0, 100);
  const filled = Math.round((pct / 100) * 24);

  // Build tier status: only show tiers still running
  const tierParts: string[] = [];
  if (tiersProgress) {
    for (const key of TIER_ORDER) {
      const tier = tiersProgress.tiers[key];
      if (!tier) continue;
      if (tier.running) {
        tierParts.push(TIER_LABELS[key] || key);
      }
    }
  }

  return (
    <div className="border-l-2 border-tabExtract mb-3 px-3 py-1.5 text-xs font-mono flex items-center gap-2">
      <span className="text-tabExtract animate-blink">&#9632;</span>
      <span className="text-tabExtract/60 tracking-tight">
        {'█'.repeat(filled)}{'░'.repeat(24 - filled)}
      </span>
      <span className="text-muted">{pct.toFixed(0)}%</span>
      {tierParts.length > 0 && (
        <span className="text-muted2">{tierParts.join(' · ')}</span>
      )}
    </div>
  );
}
