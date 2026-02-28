import { useTiersProgress } from '@/hooks/useExtractionStatus';
import type { TierProgress } from '@/services/api';

const TIER_LABELS: Record<string, string> = {
  sharp: 'sharp',
  api_soft: 'soft',
  browser_soft: 'browser',
  boosts: 'specials',
};

const TIER_ORDER = ['sharp', 'api_soft', 'browser_soft', 'boosts'];

/** Find the current activity label from running tiers (e.g. "pinnacle:football") */
function getCurrentActivity(tiers: Record<string, TierProgress>): string | null {
  for (const key of TIER_ORDER) {
    const tier = tiers[key];
    if (!tier?.running) continue;
    for (const [pid, prov] of Object.entries(tier.providers)) {
      if (prov.status === 'running' && prov.current_sport) {
        return `${pid}:${prov.current_sport}`;
      }
    }
    if (tier.current_provider) {
      return tier.current_provider;
    }
  }
  return null;
}

export function ExtractionProgressBar() {
  const tiersProgress = useTiersProgress();

  if (!tiersProgress) return null;

  const runningTiers: string[] = [];
  // Provider-weighted progress: each provider in the tier gets equal weight (1/total_providers).
  // Within each provider, progress = sports_completed / sports_total.
  // Providers not yet started (not in dict) contribute 0, preventing progress regression.
  let totalProviderSlots = 0;
  let providerProgressSum = 0;

  for (const key of TIER_ORDER) {
    const tier = tiersProgress.tiers[key];
    if (!tier) continue;
    if (tier.running) {
      runningTiers.push(TIER_LABELS[key] || key);
      totalProviderSlots += tier.total_providers;
      for (const prov of Object.values(tier.providers)) {
        if (prov.status === 'completed' || prov.status === 'failed') {
          providerProgressSum += 1.0;
        } else if (prov.status === 'running' && (prov.sports_total ?? 0) > 0) {
          providerProgressSum += (prov.sports_completed ?? 0) / prov.sports_total;
        }
      }
    }
  }

  if (runningTiers.length === 0) return null;

  const pct = totalProviderSlots > 0
    ? Math.min((providerProgressSum / totalProviderSlots) * 100, 100)
    : 0;
  const filled = Math.round((pct / 100) * 24);

  const activity = getCurrentActivity(tiersProgress.tiers);

  return (
    <div className="border-l-2 border-tabExtract mb-3 px-3 py-1.5 text-xs font-mono flex items-center gap-2">
      <span className="text-tabExtract animate-blink">&#9632;</span>
      <span className="text-tabExtract/60 tracking-tight">
        {'█'.repeat(filled)}{'░'.repeat(24 - filled)}
      </span>
      <span className="text-muted">{pct.toFixed(0)}%</span>
      <span className="text-muted2">{runningTiers.join(' · ')}</span>
      {activity && <span className="text-muted2/50">{activity}</span>}
    </div>
  );
}
