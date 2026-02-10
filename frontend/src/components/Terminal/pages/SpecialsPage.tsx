import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import type { SpecialItem, SpecialsFilters, StakePreviewResult } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { ExtractionProgressBar } from '../ExtractionProgressBar';


export function SpecialsPage() {
  const [specials, setSpecials] = useState<SpecialItem[]>([]);
  const [filters, setFilters] = useState<SpecialsFilters | null>(null);
  const [scrapedAt, setScrapedAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isScraping, setIsScraping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Active filters
  const [sportFilter, setSportFilter] = useState<string | null>(null);
  const [providerFilter, setProviderFilter] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);

  // Expanded row + bet placement
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [stakePreview, setStakePreview] = useState<StakePreviewResult | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [isPlacing, setIsPlacing] = useState(false);
  const [placementError, setPlacementError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.getSpecials({
        sport: sportFilter || undefined,
        provider: providerFilter || undefined,
        category: categoryFilter || undefined,
      });
      setSpecials(data.specials || []);
      setScrapedAt(data.scraped_at);
      if (data.filters) setFilters(data.filters);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load boosts');
    } finally {
      setIsLoading(false);
    }
  }, [sportFilter, providerFilter, categoryFilter]);

  const handleScrape = useCallback(async () => {
    setIsScraping(true);
    setError(null);
    try {
      const data = await api.scrapeSpecials();
      setSpecials(data.specials || []);
      setScrapedAt(data.scraped_at);
      if (data.filters) setFilters(data.filters);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scraping failed');
    } finally {
      setIsScraping(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useRefreshOnExtraction(fetchData);

  // Filter out expired specials client-side as extra safety
  const activeSpecials = specials.filter(s => {
    if (!s.expires_at) return true;
    try { return new Date(s.expires_at).getTime() > Date.now(); }
    catch { return true; }
  });

  // When a row is expanded, fetch the stake preview
  const handleRowClick = async (idx: number, special: SpecialItem) => {
    if (expandedIdx === idx) {
      setExpandedIdx(null);
      setStakePreview(null);
      setPlacementError(null);
      return;
    }

    setExpandedIdx(idx);
    setStakePreview(null);
    setPlacementError(null);

    if (!special.boosted_odds || !special.boost_pct) return;

    setIsLoadingPreview(true);
    try {
      const preview = await api.getBoostStakePreview({
        edge_pct: special.boost_pct,
        odds: special.boosted_odds,
        provider_id: special.provider,
      });
      setStakePreview(preview);
    } catch (err) {
      console.error('Failed to load stake preview:', err);
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handlePlaceBet = async (special: SpecialItem) => {
    if (!stakePreview || !special.boosted_odds) return;

    let stake = stakePreview.recommended_stake;
    // Cap at max_stake if set
    if (special.max_stake != null && stake > special.max_stake) {
      stake = special.max_stake;
    }
    if (stake <= 0) return;

    setIsPlacing(true);
    setPlacementError(null);
    try {
      await api.createBet({
        provider_id: special.provider,
        market: 'boost',
        outcome: special.title,
        odds: special.boosted_odds,
        stake,
        is_bonus: false,
      });
      setExpandedIdx(null);
      setStakePreview(null);
      fetchData();
    } catch (err) {
      setPlacementError(err instanceof Error ? err.message : 'Failed to place bet');
    } finally {
      setIsPlacing(false);
    }
  };

  const timeAgo = scrapedAt ? formatTimeAgo(scrapedAt) : null;

  if (isLoading && specials.length === 0) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-tabBonus" />
            Oddsboost
          </h2>
        </div>
        <div className="text-muted text-sm py-8 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabBonus" />
          Oddsboost
          <span className="text-muted text-sm font-normal ml-1">({activeSpecials.length})</span>
        </h2>
        <div className="flex items-center gap-3">
          {timeAgo && (
            <span className="text-muted text-xs">{timeAgo}</span>
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
      </div>

      <ExtractionProgressBar />

      {error && (
        <div className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded">{error}</div>
      )}

      {/* Filter pills */}
      {filters && (
        <div className="flex flex-wrap gap-2">
          {/* Sport filter */}
          <FilterGroup
            label="Sport"
            options={filters.sports}
            active={sportFilter}
            onSelect={(v) => { setSportFilter(v); setExpandedIdx(null); }}
          />
          {/* Provider filter */}
          <FilterGroup
            label="Provider"
            options={filters.providers}
            active={providerFilter}
            onSelect={(v) => { setProviderFilter(v); setExpandedIdx(null); }}
            format={formatProviderName}
          />
          {/* Category filter */}
          <FilterGroup
            label="Type"
            options={filters.categories}
            active={categoryFilter}
            onSelect={(v) => { setCategoryFilter(v); setExpandedIdx(null); }}
            format={(v) => v === 'superboost' ? 'Superboost' : 'Boost'}
          />
        </div>
      )}

      {/* Table */}
      {activeSpecials.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          No active boosts. Click Refresh to scrape latest data.
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          {/* Column header */}
          <div className="grid grid-cols-[1fr_2fr_2.5fr_80px_140px_70px_90px_90px_70px] gap-2 px-4 py-2 border-b border-border text-[11px] text-muted uppercase tracking-wider font-semibold">
            <div>Provider</div>
            <div>Event</div>
            <div>Bet</div>
            <div>Sport</div>
            <div className="text-right">Odds</div>
            <div className="text-right">Boost</div>
            <div className="text-right">Kickoff</div>
            <div className="text-right">Expires</div>
            <div className="text-right">Max</div>
          </div>

          {/* Rows */}
          <div className="divide-y divide-border/50">
            {activeSpecials.map((s, idx) => {
              const isExpanded = expandedIdx === idx;
              const boostPct = s.boost_pct;

              return (
                <div key={`${s.provider}-${idx}`}>
                  {/* Main row */}
                  <div
                    className={`grid grid-cols-[1fr_2fr_2.5fr_80px_140px_70px_90px_90px_70px] gap-2 px-4 py-2.5 cursor-pointer transition-colors text-sm
                      ${isExpanded ? 'bg-tabBonus/5' : 'hover:bg-panel2'}
                    `}
                    onClick={() => handleRowClick(idx, s)}
                  >
                    {/* Provider */}
                    <div className="flex flex-col justify-center min-w-0">
                      <span className="text-text text-sm truncate">{formatProviderName(s.provider)}</span>
                      {s.shared_providers && s.shared_providers.length > 0 && (
                        <div className="flex gap-1 mt-0.5 flex-wrap">
                          {s.shared_providers.map(sp => (
                            <span key={sp} className="text-[9px] text-muted bg-border px-1 rounded">
                              {formatProviderName(sp)}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Event */}
                    <div className="flex flex-col justify-center min-w-0">
                      <span className="text-text text-sm truncate">{s.event || '-'}</span>
                      {s.league && (
                        <span className="text-muted text-[11px] truncate">{s.league}</span>
                      )}
                    </div>

                    {/* Bet description (title) */}
                    <div className="flex items-center min-w-0">
                      <span className="text-text text-sm truncate leading-snug">{s.title}</span>
                      {s.category === 'superboost' && (
                        <span className="ml-1.5 px-1.5 py-0.5 text-[9px] font-bold bg-amber-500/20 text-amber-400 rounded shrink-0">
                          SUPER
                        </span>
                      )}
                    </div>

                    {/* Sport */}
                    <div className="flex items-center">
                      {s.sport !== 'unknown' ? (
                        <span className="px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-400 text-[11px] truncate">
                          {s.sport.replace(/_/g, ' ')}
                        </span>
                      ) : (
                        <span className="text-muted text-[11px]">-</span>
                      )}
                    </div>

                    {/* Odds: original → boosted */}
                    <div className="flex items-center justify-end gap-1.5">
                      {s.original_odds != null && (
                        <>
                          <span className="text-muted line-through text-xs">{s.original_odds.toFixed(2)}</span>
                          <span className="text-muted text-xs">&rarr;</span>
                        </>
                      )}
                      <span className="text-emerald-400 font-bold text-sm">
                        {s.boosted_odds != null ? s.boosted_odds.toFixed(2) : '-'}
                      </span>
                    </div>

                    {/* Boost % */}
                    <div className="flex items-center justify-end">
                      {boostPct != null && boostPct > 0 ? (
                        <span className={`font-semibold text-sm ${
                          boostPct >= 100 ? 'text-emerald-400' : 'text-emerald-400/80'
                        }`}>
                          +{boostPct.toFixed(0)}%
                        </span>
                      ) : (
                        <span className="text-muted text-sm">-</span>
                      )}
                    </div>

                    {/* Kickoff */}
                    <div className="flex items-center justify-end">
                      {s.event_time ? (
                        <span className="text-text text-xs">{formatEventTime(s.event_time)}</span>
                      ) : (
                        <span className="text-muted text-xs">-</span>
                      )}
                    </div>

                    {/* Expires (time left on boost) */}
                    <div className="flex items-center justify-end">
                      {s.expires_at ? (
                        <span className={`text-xs ${
                          isExpiringSoon(s.expires_at) ? 'text-amber-400' : 'text-muted'
                        }`}>
                          {formatTimeRemaining(s.expires_at)}
                        </span>
                      ) : (
                        <span className="text-muted text-xs">-</span>
                      )}
                    </div>

                    {/* Max stake */}
                    <div className="flex items-center justify-end">
                      <span className="text-muted text-sm">
                        {s.max_stake != null ? `${s.max_stake.toFixed(0)} kr` : '-'}
                      </span>
                    </div>
                  </div>

                  {/* Expanded: stake preview + place bet */}
                  {isExpanded && (
                    <ExpandedRow
                      special={s}
                      stakePreview={stakePreview}
                      isLoadingPreview={isLoadingPreview}
                      isPlacing={isPlacing}
                      placementError={placementError}
                      onPlaceBet={() => handlePlaceBet(s)}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}


// ============ Filter Group ============

function FilterGroup({
  label,
  options,
  active,
  onSelect,
  format,
}: {
  label: string;
  options: string[];
  active: string | null;
  onSelect: (value: string | null) => void;
  format?: (value: string) => string;
}) {
  if (options.length === 0) return null;

  return (
    <div className="flex items-center gap-1">
      <span className="text-muted text-[10px] uppercase tracking-wider mr-1">{label}</span>
      <button
        onClick={() => onSelect(null)}
        className={`px-2 py-0.5 text-[11px] rounded-full transition-colors ${
          active === null
            ? 'bg-tabBonus/20 text-tabBonus'
            : 'bg-panel2 text-muted hover:text-text'
        }`}
      >
        All
      </button>
      {options.map(opt => (
        <button
          key={opt}
          onClick={() => onSelect(active === opt ? null : opt)}
          className={`px-2 py-0.5 text-[11px] rounded-full transition-colors ${
            active === opt
              ? 'bg-tabBonus/20 text-tabBonus'
              : 'bg-panel2 text-muted hover:text-text'
          }`}
        >
          {format ? format(opt) : opt}
        </button>
      ))}
    </div>
  );
}


// ============ Expanded Row ============

function ExpandedRow({
  special,
  stakePreview,
  isLoadingPreview,
  isPlacing,
  placementError,
  onPlaceBet,
}: {
  special: SpecialItem;
  stakePreview: StakePreviewResult | null;
  isLoadingPreview: boolean;
  isPlacing: boolean;
  placementError: string | null;
  onPlaceBet: () => void;
}) {
  const stake = stakePreview
    ? Math.min(
        stakePreview.recommended_stake,
        special.max_stake ?? Infinity
      )
    : 0;
  const potentialReturn = stake * (special.boosted_odds ?? 0);
  const potentialProfit = potentialReturn - stake;
  const eventTimeLabel = special.event_time ? formatEventTime(special.event_time) : null;

  return (
    <div
      className="px-4 py-3 bg-panel2/50 border-t border-border/30"
      onClick={(e) => e.stopPropagation()}
    >
      {isLoadingPreview ? (
        <div className="text-muted text-sm">Calculating stake...</div>
      ) : stakePreview ? (
        <div className="flex items-center justify-between gap-6">
          {/* Left: details */}
          <div className="flex items-center gap-6 text-sm text-muted">
            <div>
              <span className="text-[10px] uppercase tracking-wider text-muted block">Kelly</span>
              <span className="text-text">{(stakePreview.kelly_fraction * 100).toFixed(1)}%</span>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-muted block">Stake</span>
              <span className="text-text font-medium">{stake.toFixed(0)} kr</span>
              {stakePreview.was_capped_single && (
                <span className="text-amber-400 text-[10px] ml-1" title="Capped by single bet limit">capped</span>
              )}
              {special.max_stake != null && stakePreview.recommended_stake > special.max_stake && (
                <span className="text-amber-400 text-[10px] ml-1" title="Capped by max stake">max</span>
              )}
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-muted block">Return</span>
              <span className="text-text">{potentialReturn.toFixed(0)} kr</span>
              <span className="text-emerald-400 text-xs ml-1">(+{potentialProfit.toFixed(0)})</span>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-muted block">Bankroll</span>
              <span className="text-text">{stakePreview.bankroll.toFixed(0)} kr</span>
            </div>
            {eventTimeLabel && (
              <div>
                <span className="text-[10px] uppercase tracking-wider text-muted block">Kickoff</span>
                <span className="text-text">{eventTimeLabel}</span>
              </div>
            )}
            {!stakePreview.bonus_cleared && (
              <div>
                <span className="text-[10px] uppercase tracking-wider text-amber-400 block">Bonus active</span>
                <span className="text-amber-400 text-xs">min odds {stakePreview.min_odds_applied.toFixed(2)}</span>
              </div>
            )}
          </div>

          {/* Right: place bet button */}
          <div className="flex items-center gap-3">
            {placementError && (
              <span className="text-red-400 text-xs max-w-[200px] truncate">{placementError}</span>
            )}
            {stakePreview.skip_reason ? (
              <span className="text-muted text-xs bg-border px-2 py-1 rounded">{stakePreview.skip_reason}</span>
            ) : (
              <button
                onClick={onPlaceBet}
                disabled={stake <= 0 || isPlacing}
                className="px-4 py-2 bg-emerald-600 text-white rounded text-sm font-medium hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                {isPlacing ? 'Placing...' : `Place ${stake.toFixed(0)} kr`}
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="text-muted text-sm">No preview available — missing boost data</div>
      )}
    </div>
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


function formatEventTime(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = date.getTime() - now.getTime();

    if (diffMs <= 0) return 'started';

    const diffMin = Math.floor(diffMs / 60000);
    const diffHrs = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHrs / 24);

    // If within 24h, show "in Xh Xm"
    if (diffHrs < 24) {
      if (diffMin < 60) return `in ${diffMin}m`;
      const remMin = diffMin % 60;
      return remMin > 0 ? `in ${diffHrs}h ${remMin}m` : `in ${diffHrs}h`;
    }

    // Otherwise show date + time
    if (diffDays < 7) {
      return date.toLocaleDateString('sv-SE', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
    }

    return date.toLocaleDateString('sv-SE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}


function formatTimeRemaining(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = date.getTime() - now.getTime();

    if (diffMs <= 0) return 'expired';

    const diffMin = Math.floor(diffMs / 60000);
    const diffHrs = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHrs / 24);

    if (diffMin < 60) return `${diffMin}m left`;
    if (diffHrs < 24) {
      const remMin = diffMin % 60;
      return remMin > 0 ? `${diffHrs}h ${remMin}m` : `${diffHrs}h left`;
    }
    if (diffDays < 7) return `${diffDays}d ${diffHrs % 24}h`;
    return `${diffDays}d left`;
  } catch {
    return '';
  }
}


function isExpiringSoon(isoString: string): boolean {
  try {
    const date = new Date(isoString);
    const diffMs = date.getTime() - Date.now();
    // Expiring within 6 hours
    return diffMs > 0 && diffMs < 6 * 60 * 60 * 1000;
  } catch {
    return false;
  }
}
