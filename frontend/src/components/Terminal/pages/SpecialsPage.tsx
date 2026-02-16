import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import type { SpecialItem, SpecialsFilters, StakePreviewResult } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { FilterBar, MultiSelectDropdown, SingleSelectPills } from '../FilterBar';

interface GroupedSpecial {
  key: string;
  rep: SpecialItem;
  providers: string[];
}

export function SpecialsPage() {
  const [specials, setSpecials] = useState<SpecialItem[]>([]);
  const [filters, setFilters] = useState<SpecialsFilters | null>(null);
  const [scrapedAt, setScrapedAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [stakePreview, setStakePreview] = useState<StakePreviewResult | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [isPlacing, setIsPlacing] = useState(false);
  const [placementError, setPlacementError] = useState<string | null>(null);
  const [placingProvider, setPlacingProvider] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true); setError(null);
    try {
      const data = await api.getSpecials({ category: categoryFilter || undefined });
      setSpecials(data.specials || []);
      setScrapedAt(data.scraped_at);
      if (data.filters) setFilters(data.filters);
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed to load boosts'); }
    finally { setIsLoading(false); }
  }, [categoryFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  // Filter out expired/started specials
  const nonExpired = specials.filter(s => {
    if (s.event_time) { try { if (new Date(s.event_time).getTime() <= Date.now()) return false; } catch { /* keep */ } }
    if (!s.expires_at) return true;
    try { return new Date(s.expires_at).getTime() > Date.now(); } catch { return true; }
  });

  // Group: consolidate same boost across providers into one row
  const grouped = useMemo(() => {
    const groups: GroupedSpecial[] = [];
    for (const s of nonExpired) {
      const allProviders = [s.provider, ...(s.shared_providers || [])];
      groups.push({
        key: `${s.provider}-${s.title}-${s.boosted_odds}`,
        rep: s,
        providers: allProviders,
      });
    }
    return groups;
  }, [nonExpired]);

  // Filter by selected providers
  const activeGroups = useMemo(() => {
    if (selectedProviders.size === 0) return grouped;
    return grouped.filter(g =>
      g.providers.some(p => selectedProviders.has(p.toLowerCase()))
    );
  }, [grouped, selectedProviders]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => { const next = new Set(prev); const key = p.toLowerCase(); if (next.has(key)) next.delete(key); else next.add(key); return next; });
    setExpandedIdx(null);
  };

  const handleRowClick = async (idx: number, group: GroupedSpecial) => {
    if (expandedIdx === idx) { setExpandedIdx(null); setStakePreview(null); setPlacementError(null); return; }
    setExpandedIdx(idx); setStakePreview(null); setPlacementError(null);
    const s = group.rep;
    if (!s.boosted_odds || !s.boost_pct) return;
    setIsLoadingPreview(true);
    try { const preview = await api.getBoostStakePreview({ edge_pct: s.boost_pct, odds: s.boosted_odds, provider_id: s.provider }); setStakePreview(preview); }
    catch (err) { console.error('Failed to load stake preview:', err); }
    finally { setIsLoadingPreview(false); }
  };

  const handlePlaceBet = async (special: SpecialItem, providerId: string) => {
    if (!stakePreview || !special.boosted_odds) return;
    let stake = stakePreview.recommended_stake;
    if (special.max_stake != null && stake > special.max_stake) stake = special.max_stake;
    if (stake <= 0) return;
    setIsPlacing(true); setPlacingProvider(providerId); setPlacementError(null);
    try { await api.createBet({ provider_id: providerId, market: 'boost', outcome: special.title, odds: special.boosted_odds, stake, is_bonus: false }); setExpandedIdx(null); setStakePreview(null); fetchData(); }
    catch (err) { setPlacementError(err instanceof Error ? err.message : 'Failed to place bet'); }
    finally { setIsPlacing(false); setPlacingProvider(null); }
  };

  const timeAgo = scrapedAt ? formatTimeAgo(scrapedAt) : null;

  if (isLoading && specials.length === 0) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text flex items-center gap-2"><span className="w-2 h-2 bg-tabBonus" />Oddsboost</h2>
        </div>
        <div className="text-muted text-sm py-8 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 bg-tabBonus" />Oddsboost
          <span className="text-muted text-sm font-normal ml-1">({activeGroups.length})</span>
        </h2>
        {timeAgo && <span className="text-muted text-xs">{timeAgo}</span>}
      </div>

      {error && <div className="text-error text-sm bg-error/10 px-3 py-2 border border-error/20">{error}</div>}

      {filters && (
        <FilterBar>
          <MultiSelectDropdown label="Provider" options={filters.providers} selected={selectedProviders} onToggle={toggleProvider} onClear={() => { setSelectedProviders(new Set()); setExpandedIdx(null); }} format={formatProviderName} accentColor="tabBonus" />
          {filters.categories.length > 0 && (
            <><div className="w-px h-5 bg-border/50" /><SingleSelectPills label="Type" options={filters.categories} active={categoryFilter} onSelect={(v) => { setCategoryFilter(v); setExpandedIdx(null); }} format={(v) => v === 'superboost' ? 'Superboost' : 'Boost'} accentColor="tabBonus" /></>
          )}
        </FilterBar>
      )}

      {activeGroups.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No active boosts. Boosts are scraped automatically every 2 hours.</div>
      ) : (
        <div className="border-l-2 border-tabBonus">
        <table className="sq">
          <thead>
            <tr>
              <th>Boost</th>
              <th className="text-right">Providers</th>
              <th className="text-right">Odds</th>
              <th className="text-right">Prob</th>
              <th className="text-right">Max</th>
              <th className="text-right">Edge</th>
            </tr>
          </thead>
          <tbody>
            {activeGroups.map((group, idx) => {
              const s = group.rep;
              const isExpanded = expandedIdx === idx;
              const boostPct = s.boost_pct;
              const providerCount = group.providers.length;

              return (
                <>
                  <tr key={group.key} className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`} onClick={() => handleRowClick(idx, group)}>
                    <td>
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="text-text text-sm truncate">{s.title}</span>
                        {s.category === 'superboost' && <span className="px-1 py-0.5 text-[9px] font-bold bg-warning/20 text-warning">SUPER</span>}
                      </div>
                      <div className="text-muted2 text-[11px] truncate">
                        {s.event || ''}{s.sport && s.sport !== 'unknown' ? ` · ${s.sport.replace(/_/g, ' ')}` : ''}{s.league ? ` · ${s.league}` : ''}
                        {s.event_time && isFutureDate(s.event_time) ? ` · ${formatEventTime(s.event_time)}` : s.expires_at ? ` · ${formatTimeRemaining(s.expires_at)}` : ''}
                      </div>
                    </td>
                    <td className="text-right text-sm min-w-0">
                      {providerCount <= 3 ? (
                        <span className="text-text truncate">{group.providers.map(formatProviderName).join(', ')}</span>
                      ) : (
                        <span className="text-text truncate">
                          {formatProviderName(group.providers[0])}
                          <span className="text-muted ml-1">+{providerCount - 1}</span>
                        </span>
                      )}
                    </td>
                    <td className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        {s.original_odds != null && (<><span className="text-muted line-through text-xs">{s.original_odds.toFixed(2)}</span><span className="text-muted text-xs">&rarr;</span></>)}
                        <span className="text-success font-bold text-sm">{s.boosted_odds != null ? s.boosted_odds.toFixed(2) : '-'}</span>
                      </div>
                    </td>
                    <td className="text-right text-muted text-sm">{s.original_odds != null && s.original_odds > 1 ? `${(100 / s.original_odds).toFixed(0)}%` : '-'}</td>
                    <td className="text-right text-muted text-sm">{s.max_stake != null ? `${s.max_stake.toFixed(0)} kr` : '-'}</td>
                    <td className="text-right">{boostPct != null && boostPct > 0 ? <span className="text-accent font-semibold text-sm">+{boostPct.toFixed(0)}%</span> : <span className="text-muted text-sm">-</span>}</td>
                  </tr>
                  {isExpanded && (
                    <tr key={`${group.key}-exp`}>
                      <td colSpan={6} className="!p-0" onClick={e => e.stopPropagation()}>
                        <ExpandedRow
                          special={s}
                          providers={group.providers}
                          stakePreview={stakePreview}
                          isLoadingPreview={isLoadingPreview}
                          isPlacing={isPlacing}
                          placingProvider={placingProvider}
                          placementError={placementError}
                          onPlaceBet={(providerId) => handlePlaceBet(s, providerId)}
                        />
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}

function ExpandedRow({ special, providers, stakePreview, isLoadingPreview, isPlacing, placingProvider, placementError, onPlaceBet }: {
  special: SpecialItem;
  providers: string[];
  stakePreview: StakePreviewResult | null;
  isLoadingPreview: boolean;
  isPlacing: boolean;
  placingProvider: string | null;
  placementError: string | null;
  onPlaceBet: (providerId: string) => void;
}) {
  const stake = stakePreview ? Math.min(stakePreview.recommended_stake, special.max_stake ?? Infinity) : 0;
  const potentialReturn = stake * (special.boosted_odds ?? 0);
  const potentialProfit = potentialReturn - stake;
  const eventTimeLabel = special.event_time ? formatEventTime(special.event_time) : null;

  return (
    <div className="px-3 py-2 bg-panel">
      {isLoadingPreview ? (<div className="text-muted text-sm">Calculating stake...</div>) : stakePreview ? (
        <div className="space-y-2">
          <div className="flex items-center gap-6 text-xs text-muted">
            <div><span className="text-muted2 uppercase tracking-wider">Kelly: </span><span className="text-text">{(stakePreview.kelly_fraction * 100).toFixed(1)}%</span></div>
            <div><span className="text-muted2 uppercase tracking-wider">Stake: </span><span className="text-text font-medium">{stake.toFixed(0)} kr</span>{stakePreview.was_capped_single && <span className="text-warning text-[10px] ml-1">capped</span>}{special.max_stake != null && stakePreview.recommended_stake > special.max_stake && <span className="text-warning text-[10px] ml-1">max</span>}</div>
            <div><span className="text-muted2 uppercase tracking-wider">Return: </span><span className="text-text">{potentialReturn.toFixed(0)} kr</span><span className="text-success text-xs ml-1">(+{potentialProfit.toFixed(0)})</span></div>
            <div><span className="text-muted2 uppercase tracking-wider">Bankroll: </span><span className="text-text">{stakePreview.bankroll.toFixed(0)} kr</span></div>
            {eventTimeLabel && <div><span className="text-muted2 uppercase tracking-wider">Kickoff: </span><span className="text-text">{eventTimeLabel}</span></div>}
            {!stakePreview.bonus_cleared && <div><span className="text-warning uppercase tracking-wider text-[10px]">Bonus active </span><span className="text-warning text-xs">min odds {stakePreview.min_odds_applied.toFixed(2)}</span></div>}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {placementError && <span className="text-error text-xs max-w-[200px] truncate">{placementError}</span>}
            {stakePreview.skip_reason ? (
              <span className="text-muted text-xs bg-border px-2 py-1">{stakePreview.skip_reason}</span>
            ) : (
              providers.map(providerId => (
                <button
                  key={providerId}
                  onClick={() => onPlaceBet(providerId)}
                  disabled={stake <= 0 || isPlacing}
                  className="px-3 py-1.5 bg-tabBonus text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity whitespace-nowrap"
                >
                  {isPlacing && placingProvider === providerId ? '...' : `${formatProviderName(providerId)} ${stake.toFixed(0)} kr`}
                </button>
              ))
            )}
          </div>
        </div>
      ) : (<div className="text-muted text-sm">No preview available — missing boost data</div>)}
    </div>
  );
}

function formatTimeAgo(isoString: string): string { try { const date = new Date(isoString); const now = new Date(); const diffMs = now.getTime() - date.getTime(); const diffMin = Math.floor(diffMs / 60000); const diffHrs = Math.floor(diffMin / 60); if (diffMin < 1) return 'just now'; if (diffMin < 60) return `${diffMin}m ago`; if (diffHrs < 24) return `${diffHrs}h ago`; return date.toLocaleDateString('sv-SE'); } catch { return ''; } }
function formatEventTime(isoString: string): string { try { const date = new Date(isoString); const now = new Date(); const diffMs = date.getTime() - now.getTime(); if (diffMs <= 0) return 'started'; const diffMin = Math.floor(diffMs / 60000); const diffHrs = Math.floor(diffMin / 60); const diffDays = Math.floor(diffHrs / 24); if (diffHrs < 24) { if (diffMin < 60) return `in ${diffMin}m`; const remMin = diffMin % 60; return remMin > 0 ? `in ${diffHrs}h ${remMin}m` : `in ${diffHrs}h`; } if (diffDays < 7) return date.toLocaleDateString('sv-SE', { weekday: 'short', hour: '2-digit', minute: '2-digit' }); return date.toLocaleDateString('sv-SE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch { return ''; } }
function formatTimeRemaining(isoString: string): string { try { const date = new Date(isoString); const now = new Date(); const diffMs = date.getTime() - now.getTime(); if (diffMs <= 0) return 'expired'; const diffMin = Math.floor(diffMs / 60000); const diffHrs = Math.floor(diffMin / 60); const diffDays = Math.floor(diffHrs / 24); if (diffMin < 60) return `${diffMin}m left`; if (diffHrs < 24) { const remMin = diffMin % 60; return remMin > 0 ? `${diffHrs}h ${remMin}m` : `${diffHrs}h left`; } if (diffDays < 7) return `${diffDays}d ${diffHrs % 24}h`; return `${diffDays}d left`; } catch { return ''; } }
function isFutureDate(isoString: string): boolean { try { return new Date(isoString).getTime() > Date.now(); } catch { return false; } }
