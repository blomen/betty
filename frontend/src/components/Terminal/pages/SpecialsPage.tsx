import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import type { SpecialItem, BonusValidation, BonusAlert, BonusChange, BonusValidationStatus } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';


export function SpecialsPage() {
  const [specials, setSpecials] = useState<SpecialItem[]>([]);
  const [scrapedAt, setScrapedAt] = useState<string | null>(null);
  const [bonusValidation, setBonusValidation] = useState<BonusValidation | null>(null);
  const [bonusStatus, setBonusStatus] = useState<BonusValidationStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isScraping, setIsScraping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [specialsData, bonusData] = await Promise.all([
        api.getSpecials(),
        api.getBonusStatus(),
      ]);
      setSpecials(specialsData.specials || []);
      setScrapedAt(specialsData.scraped_at);
      setBonusStatus(bonusData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load specials');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleScrape = useCallback(async () => {
    setIsScraping(true);
    setError(null);
    try {
      const data = await api.scrapeSpecials();
      setSpecials(data.specials || []);
      setScrapedAt(data.scraped_at);
      if (data.bonus_validation) {
        setBonusValidation(data.bonus_validation);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scraping failed');
    } finally {
      setIsScraping(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Group specials by provider
  const grouped: Record<string, SpecialItem[]> = {};
  for (const s of specials) {
    if (!grouped[s.provider]) grouped[s.provider] = [];
    grouped[s.provider].push(s);
  }
  const providerIds = Object.keys(grouped).sort();

  const timeAgo = scrapedAt ? formatTimeAgo(scrapedAt) : null;
  const sourceCount = new Set(specials.map(s => s.source)).size;

  // Merge bonus info: prefer fresh validation from scrape, fall back to cached status
  const effectiveBonusChecked = bonusValidation?.providers_checked ?? bonusStatus?.providers_checked ?? 0;
  const effectiveBonusMatches = bonusValidation?.matches ?? bonusStatus?.matches ?? 0;
  const effectiveBonusMismatches = bonusValidation?.mismatches ?? bonusStatus?.mismatches ?? 0;
  const effectiveChanges = bonusValidation?.changes ?? bonusStatus?.changes ?? [];
  const effectiveAlerts = bonusValidation?.alerts ?? [];
  const effectiveValidatedAt = bonusValidation?.validated_at ?? bonusStatus?.validated_at;

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabBonus" />
          Specials
        </h2>
        <div className="text-muted text-sm py-8 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabBonus" />
        Specials
      </h2>

      {error && (
        <div className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded">{error}</div>
      )}

      {/* Bonus Alerts */}
      {effectiveAlerts.length > 0 && (
        <AlertBanner alerts={effectiveAlerts} />
      )}

      {/* Bonus Validation Status */}
      {effectiveBonusChecked > 0 && (
        <BonusStatusCard
          checked={effectiveBonusChecked}
          matches={effectiveBonusMatches}
          mismatches={effectiveBonusMismatches}
          changes={effectiveChanges}
          validatedAt={effectiveValidatedAt}
        />
      )}

      {/* Odds Boosts */}
      <Card
        title={`Odds Boosts (${specials.length})`}
        headerRight={
          <div className="flex items-center gap-3">
            {timeAgo && (
              <span className="text-muted text-xs">
                {timeAgo} &middot; {sourceCount} {sourceCount === 1 ? 'source' : 'sources'}
              </span>
            )}
            <button
              onClick={handleScrape}
              disabled={isScraping}
              className={`
                px-3 py-1 text-xs rounded font-medium transition-colors
                ${isScraping
                  ? 'bg-border text-muted cursor-not-allowed'
                  : 'bg-panel2 text-text hover:bg-border'
                }
              `}
            >
              {isScraping ? 'Scraping...' : 'Refresh'}
            </button>
          </div>
        }
      >
        {specials.length === 0 ? (
          <div className="text-muted text-sm py-6 text-center">
            No specials available. Click Refresh to scrape latest boosts.
          </div>
        ) : (
          <div className="space-y-6">
            {providerIds.map(pid => (
              <div key={pid}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-semibold text-muted uppercase tracking-wider">
                    {formatProviderName(pid)}
                  </span>
                  <span className="text-[10px] text-muted bg-border px-1.5 py-0.5 rounded-full">
                    {grouped[pid].length}
                  </span>
                </div>
                <div className="space-y-1.5">
                  {grouped[pid].map((s, i) => (
                    <SpecialCard key={`${pid}-${i}`} special={s} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}


// ============ Bonus Validation Components ============

function AlertBanner({ alerts }: { alerts: BonusAlert[] }) {
  return (
    <div className="space-y-1.5">
      {alerts.map((alert, i) => (
        <div
          key={i}
          className={`px-3 py-2 rounded-lg text-sm flex items-start gap-2 ${
            alert.type === 'bonus_changed'
              ? 'bg-amber-500/10 border border-amber-500/20 text-amber-300'
              : 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-300'
          }`}
        >
          <span className="shrink-0 mt-0.5">
            {alert.type === 'bonus_changed' ? '⚠' : '✦'}
          </span>
          <div>
            <span className="font-medium">{formatProviderName(alert.provider_id)}</span>
            {': '}
            {alert.type === 'bonus_changed' ? (
              <span>
                {alert.field} changed from{' '}
                <span className="text-red-400 line-through">{String(alert.old_value)}</span>
                {' → '}
                <span className="text-emerald-400 font-medium">{String(alert.new_value)}</span>
              </span>
            ) : (
              <span>New bonus available</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}


function BonusStatusCard({
  checked,
  matches,
  mismatches,
  changes,
  validatedAt,
}: {
  checked: number;
  matches: number;
  mismatches: number;
  changes: BonusChange[];
  validatedAt?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const allMatch = mismatches === 0;

  return (
    <div className={`rounded-lg border px-4 py-3 ${
      allMatch
        ? 'bg-emerald-500/5 border-emerald-500/20'
        : 'bg-amber-500/5 border-amber-500/20'
    }`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={`w-2 h-2 rounded-full ${allMatch ? 'bg-emerald-400' : 'bg-amber-400'}`} />
          <div>
            <span className={`text-sm font-medium ${allMatch ? 'text-emerald-300' : 'text-amber-300'}`}>
              Bonus Stats {allMatch ? 'Verified' : `${mismatches} Change${mismatches !== 1 ? 's' : ''} Detected`}
            </span>
            <div className="flex items-center gap-2 text-muted text-xs mt-0.5">
              <span>{checked} providers checked</span>
              <span>&middot;</span>
              <span>{matches} match</span>
              {mismatches > 0 && (
                <>
                  <span>&middot;</span>
                  <span className="text-amber-400">{mismatches} mismatch</span>
                </>
              )}
              {validatedAt && (
                <>
                  <span>&middot;</span>
                  <span>{formatTimeAgo(validatedAt)}</span>
                </>
              )}
            </div>
          </div>
        </div>
        {changes.length > 0 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-muted hover:text-text transition-colors px-2 py-1"
          >
            {expanded ? 'Hide' : 'Details'}
          </button>
        )}
      </div>

      {expanded && changes.length > 0 && (
        <div className="mt-3 pt-3 border-t border-border space-y-2">
          {changes.map((change, i) => (
            <div key={i} className="text-xs">
              <span className="text-text font-medium">{formatProviderName(change.provider_id)}</span>
              <div className="ml-3 mt-0.5 space-y-0.5">
                {change.diffs.map((diff, j) => (
                  <div key={j} className="text-muted">
                    {diff.field}:{' '}
                    <span className="text-red-400">{String(diff.yaml)}</span>
                    {' → '}
                    <span className="text-amber-300">{String(diff.scraped)}</span>
                  </div>
                ))}
              </div>
              {change.sources.length > 0 && (
                <div className="text-muted ml-3 mt-0.5">
                  sources: {change.sources.join(', ')}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ============ Special Card Components ============

function SpecialCard({ special }: { special: SpecialItem }) {
  const boostPct = special.original_odds && special.boosted_odds
    ? ((special.boosted_odds / special.original_odds - 1) * 100)
    : null;

  const expiresLabel = special.expires_at ? formatExpiry(special.expires_at) : null;
  const isExpiringSoon = special.expires_at ? isWithinHours(special.expires_at, 6) : false;

  // Show event if it differs from title (bettingkollen has structured event + outcome)
  const showEvent = special.event
    && special.event !== special.title
    && !special.title.includes(special.event);

  return (
    <div className="px-3 py-2.5 bg-panel2 rounded-lg flex items-start justify-between gap-3">
      {/* Left: event + title + metadata */}
      <div className="flex-1 min-w-0 space-y-1">
        {showEvent && (
          <div className="text-muted text-[11px] truncate">{special.event}</div>
        )}
        <div className="text-text text-sm font-medium leading-snug">{special.title}</div>
        <div className="flex items-center gap-2 flex-wrap">
          {special.sport !== 'unknown' && <SportBadge sport={special.sport} />}
          {special.max_stake != null && (
            <span className="text-muted text-[11px]">
              max {special.max_stake.toFixed(0)} kr
            </span>
          )}
          {expiresLabel && (
            <span className={`text-[11px] ${isExpiringSoon ? 'text-amber-400' : 'text-muted'}`}>
              {expiresLabel}
            </span>
          )}
        </div>
      </div>

      {/* Right: odds + boost % */}
      {special.original_odds != null && special.boosted_odds != null && (
        <div className="flex flex-col items-end shrink-0">
          <div className="flex items-center gap-1.5 text-sm">
            <span className="text-muted line-through text-xs">{special.original_odds.toFixed(2)}</span>
            <span className="text-muted">&rarr;</span>
            <span className="text-emerald-400 font-bold">{special.boosted_odds.toFixed(2)}</span>
          </div>
          {boostPct != null && boostPct > 0 && (
            <span className="text-emerald-400/70 text-[11px] font-medium">
              +{boostPct.toFixed(0)}%
            </span>
          )}
        </div>
      )}
    </div>
  );
}


function SportBadge({ sport }: { sport: string }) {
  const icons: Record<string, string> = {
    football: '\u26BD',
    ice_hockey: '\u{1F3D2}',
    basketball: '\u{1F3C0}',
    tennis: '\u{1F3BE}',
    mma: '\u{1F94A}',
    esports: '\u{1F3AE}',
    american_football: '\u{1F3C8}',
    baseball: '\u26BE',
  };
  const icon = icons[sport] || '';
  const label = sport.replace('_', ' ');

  return (
    <span className="px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-400 text-[11px]">
      {icon && <span className="mr-0.5">{icon}</span>}
      {label}
    </span>
  );
}


// ============ Utility Functions ============

function formatTimeAgo(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    const diffHrs = Math.floor(diffMin / 60);

    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHrs < 24) return `${diffHrs}h ago`;
    return date.toLocaleDateString('sv-SE');
  } catch {
    return '';
  }
}


function formatExpiry(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = date.getTime() - now.getTime();

    if (diffMs <= 0) return 'expired';

    const diffMin = Math.floor(diffMs / 60000);
    const diffHrs = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHrs / 24);

    if (diffMin < 60) return `${diffMin}m left`;
    if (diffHrs < 24) return `${diffHrs}h left`;
    if (diffDays < 7) return `${diffDays}d left`;

    return date.toLocaleDateString('sv-SE', { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}


function isWithinHours(isoString: string, hours: number): boolean {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = date.getTime() - now.getTime();
    return diffMs > 0 && diffMs < hours * 3600000;
  } catch {
    return false;
  }
}
