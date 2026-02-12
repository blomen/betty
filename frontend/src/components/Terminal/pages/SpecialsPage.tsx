import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import type { SpecialItem, SpecialsFilters, StakePreviewResult } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { FilterBar, MultiSelectPills, SingleSelectPills } from '../FilterBar';


export function SpecialsPage() {
  const [specials, setSpecials] = useState<SpecialItem[]>([]);
  const [filters, setFilters] = useState<SpecialsFilters | null>(null);
  const [scrapedAt, setScrapedAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Active filters — providers is now multi-select
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
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
  }, [categoryFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useRefreshOnExtraction(fetchData);

  // Filter out expired specials client-side as extra safety
  const nonExpired = specials.filter(s => {
    if (!s.expires_at) return true;
    try { return new Date(s.expires_at).getTime() > Date.now(); }
    catch { return true; }
  });

  // Expand shared providers: each boost gets its own row per provider
  const expandedSpecials = nonExpired.flatMap(s => {
    const rows: (SpecialItem & { display_provider: string })[] = [
      { ...s, display_provider: s.provider },
    ];
    if (s.shared_providers) {
      for (const sp of s.shared_providers) {
        rows.push({ ...s, display_provider: sp });
      }
    }
    return rows;
  });

  // Apply provider filter on frontend (after expansion) — now multi-select
  const activeSpecials = useMemo(() => {
    if (selectedProviders.size === 0) return expandedSpecials;
    return expandedSpecials.filter(s =>
      selectedProviders.has(s.display_provider.toLowerCase())
    );
  }, [expandedSpecials, selectedProviders]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      const key = p.toLowerCase();
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setExpandedIdx(null);
  };

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
      <div className="space-y-3">
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
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabBonus" />
          Oddsboost
          <span className="text-muted text-sm font-normal ml-1">({activeSpecials.length})</span>
        </h2>
        {timeAgo && (
          <span className="text-muted text-xs">{timeAgo}</span>
        )}
      </div>

      {error && (
        <div className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded">{error}</div>
      )}

      {/* Filter bar */}
      {filters && (
        <FilterBar>
          <MultiSelectPills
            label="Provider"
            options={filters.providers}
            selected={selectedProviders}
            onToggle={toggleProvider}
            onClear={() => { setSelectedProviders(new Set()); setExpandedIdx(null); }}
            format={formatProviderName}
            accentColor="tabBonus"
          />
          {filters.categories.length > 0 && (
            <>
              <div className="w-px h-5 bg-border/50" />
              <SingleSelectPills
                label="Type"
                options={filters.categories}
                active={categoryFilter}
                onSelect={(v) => { setCategoryFilter(v); setExpandedIdx(null); }}
                format={(v) => v === 'superboost' ? 'Superboost' : 'Boost'}
                accentColor="tabBonus"
              />
            </>
          )}
        </FilterBar>
      )}

      {/* Table */}
      {activeSpecials.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          No active boosts. Boosts are scraped automatically every 2 hours.
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          {/* Column header */}
          <div className="grid grid-cols-[90px_1fr_1.5fr_100px_55px_70px_55px] gap-3 px-4 py-2 border-b border-border text-[11px] text-muted uppercase tracking-wider font-semibold">
            <div>Provider</div>
            <div>Event</div>
            <div>Bet</div>
            <div className="text-right">Odds</div>
            <div className="text-right">Boost</div>
            <div className="text-right">Kickoff</div>
            <div className="text-right">Max</div>
          </div>

          {/* Rows */}
          <div className="divide-y divide-border/50">
            {activeSpecials.map((s, idx) => {
              const isExpanded = expandedIdx === idx;
              const boostPct = s.boost_pct;

              return (
                <div key={`${s.display_provider}-${idx}`}>
                  {/* Main row */}
                  <div
                    className={`grid grid-cols-[90px_1fr_1.5fr_100px_55px_70px_55px] gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm
                      ${isExpanded ? 'bg-tabBonus/5' : 'hover:bg-panel2'}
                    `}
                    onClick={() => handleRowClick(idx, s)}
                  >
                    {/* Provider */}
                    <div className="flex items-center min-w-0">
                      <span className="text-text text-sm truncate">{formatProviderName(s.display_provider)}</span>
                    </div>

                    {/* Event + sport badge + league */}
                    <div className="flex flex-col justify-center min-w-0">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="text-text text-sm truncate">{s.event || '-'}</span>
                        {s.sport && s.sport !== 'unknown' && (
                          <span className="px-1 py-0.5 rounded bg-indigo-500/15 text-indigo-400 text-[9px] shrink-0">
                            {s.sport.replace(/_/g, ' ')}
                          </span>
                        )}
                      </div>
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

                    {/* Kickoff / Expires */}
                    <div className="flex flex-col items-end justify-center">
                      {s.event_time && isFutureDate(s.event_time) ? (
                        <span className="text-muted text-xs">{formatEventTime(s.event_time)}</span>
                      ) : s.expires_at ? (
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

    if (diffHrs < 24) {
      if (diffMin < 60) return `in ${diffMin}m`;
      const remMin = diffMin % 60;
      return remMin > 0 ? `in ${diffHrs}h ${remMin}m` : `in ${diffHrs}h`;
    }

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


function isFutureDate(isoString: string): boolean {
  try {
    return new Date(isoString).getTime() > Date.now();
  } catch {
    return false;
  }
}


function isExpiringSoon(isoString: string): boolean {
  try {
    const date = new Date(isoString);
    const diffMs = date.getTime() - Date.now();
    return diffMs > 0 && diffMs < 6 * 60 * 60 * 1000;
  } catch {
    return false;
  }
}
