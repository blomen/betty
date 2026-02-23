import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import type { SpecialItem, SpecialsFilters, StakePreviewResult } from '@/services/api';
import { formatProviderName, getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { useTableSort } from '@/hooks/useTableSort';
import { SortableHeader } from '../SortableHeader';
import { FilterBar, MultiSelectDropdown } from '../FilterBar';
import { TabIcon, TAB_COLORS } from '../TabBar';

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
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [stakePreview, setStakePreview] = useState<StakePreviewResult | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [isPlacing, setIsPlacing] = useState(false);
  const [placementError, setPlacementError] = useState<string | null>(null);
  const [selectedBetProvider, setSelectedBetProvider] = useState<Record<string, number>>({});
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true); setError(null);
    try {
      const data = await api.getSpecials({});
      setSpecials(data.specials || []);
      setScrapedAt(data.scraped_at);
      if (data.filters) setFilters(data.filters);
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed to load boosts'); }
    finally { setIsLoading(false); }
  }, []);

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

  // Apply filters
  const activeGroups = useMemo(() => {
    let result = grouped;

    // Provider filter
    if (selectedProviders.size > 0) {
      result = result.filter(g =>
        g.providers.some(p => selectedProviders.has(p.toLowerCase()))
      );
    }

    return result;
  }, [grouped, selectedProviders]);

  type SpecialsSortCol = 'odds' | 'prob' | 'max' | 'edge' | 'ttk';
  const specialsSortExtractors = useMemo(() => ({
    odds:  (g: GroupedSpecial) => g.rep.boosted_odds ?? 0,
    prob:  (g: GroupedSpecial) => g.rep.fair_odds && g.rep.fair_odds > 1 ? 100 / g.rep.fair_odds : 0,
    max:   (g: GroupedSpecial) => g.rep.max_stake ?? 0,
    edge:  (g: GroupedSpecial) => g.rep.edge_pct ?? 0,
    ttk:   (g: GroupedSpecial) => getTTKFromNow(g.rep.event_time) ?? 99999,
  }), []);
  const { sorted: sortedSpecials, sort: specialsSort, toggle: toggleSpecialsSort } =
    useTableSort<GroupedSpecial, SpecialsSortCol>(activeGroups, specialsSortExtractors, { column: 'edge', direction: 'desc' });

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => { const next = new Set(prev); const key = p.toLowerCase(); if (next.has(key)) next.delete(key); else next.add(key); return next; });
    setExpandedIdx(null);
  };

  const handleRowClick = async (idx: number, group: GroupedSpecial) => {
    if (expandedIdx === idx) { setExpandedIdx(null); setStakePreview(null); setPlacementError(null); return; }
    setExpandedIdx(idx); setStakePreview(null); setPlacementError(null);
    const s = group.rep;
    if (!s.boosted_odds || !s.edge_pct) return;
    setIsLoadingPreview(true);
    try { const preview = await api.getBoostStakePreview({ edge_pct: s.edge_pct, odds: s.boosted_odds, provider_id: s.provider }); setStakePreview(preview); }
    catch (err) { console.error('Failed to load stake preview:', err); }
    finally { setIsLoadingPreview(false); }
  };

  const handlePlaceBet = async (special: SpecialItem, providerId: string, groupKey: string) => {
    if (!stakePreview || !special.boosted_odds) return;
    let stake = stakePreview.recommended_stake;
    if (special.max_stake != null && stake > special.max_stake) stake = special.max_stake;
    if (stake <= 0) return;
    const odds = oddsOverride[groupKey] ?? special.boosted_odds;
    setIsPlacing(true); setPlacementError(null);
    try { await api.createBet({ provider_id: providerId, market: 'boost', outcome: special.title, odds, stake, is_bonus: false, utility_score: special.edge_pct != null ? special.edge_pct / 100 : undefined, selection_probability: special.fair_odds != null && special.fair_odds > 1 ? 1 / special.fair_odds : undefined }); setExpandedIdx(null); setStakePreview(null); fetchData(); }
    catch (err) { setPlacementError(err instanceof Error ? err.message : 'Failed to place bet'); }
    finally { setIsPlacing(false); }
  };

  const timeAgo = scrapedAt ? formatTimeAgo(scrapedAt) : null;

  if (isLoading && specials.length === 0) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text flex items-center gap-2"><TabIcon name="specials" color={TAB_COLORS.specials} size={16} />Specials</h2>
        </div>
        <div className="text-muted text-sm py-8 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="specials" color={TAB_COLORS.specials} size={16} />Specials
          <span className="text-muted text-sm font-normal ml-1">({sortedSpecials.length})</span>
        </h2>
        {timeAgo && <span className="text-muted text-xs">{timeAgo}</span>}
      </div>

      {error && <div className="text-error text-sm bg-error/10 px-3 py-2 border border-error/20">{error}</div>}

      {filters && (
        <FilterBar>
          <MultiSelectDropdown label="Provider" options={filters.providers} selected={selectedProviders} onToggle={toggleProvider} onClear={() => { setSelectedProviders(new Set()); setExpandedIdx(null); }} format={formatProviderName} accentColor="tabBonus" />
        </FilterBar>
      )}

      {sortedSpecials.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No active boosts. Boosts are scraped automatically every 2 hours.</div>
      ) : (
        <div className="border-l-2 border-tabBonus">
        <table className="sq">
          <thead>
            <tr>
              <th>Boost</th>
              <th className="text-right">Providers</th>
              <SortableHeader column="odds" label="Odds" sort={specialsSort} onToggle={toggleSpecialsSort} />
              <SortableHeader column="prob" label="Prob" sort={specialsSort} onToggle={toggleSpecialsSort} />
              <SortableHeader column="ttk" label="TTK" sort={specialsSort} onToggle={toggleSpecialsSort} />
              <SortableHeader column="max" label="Max" sort={specialsSort} onToggle={toggleSpecialsSort} />
              <SortableHeader column="edge" label="Edge" sort={specialsSort} onToggle={toggleSpecialsSort} />
            </tr>
          </thead>
          <tbody>
            {sortedSpecials.map((group, idx) => {
              const s = group.rep;
              const isExpanded = expandedIdx === idx;
              const providerCount = group.providers.length;

              return (
                <>
                  <tr key={group.key} className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`} onClick={() => handleRowClick(idx, group)}>
                    <td>
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="text-text text-sm truncate">{s.title}</span>
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
                    <td className="text-right text-muted text-sm">{s.fair_odds != null && s.fair_odds > 1 ? `${(100 / s.fair_odds).toFixed(0)}%` : '-'}</td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(s.event_time); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-muted text-sm">{s.max_stake != null ? `${s.max_stake.toFixed(0)} kr` : '-'}</td>
                    <td className="text-right">
                      {s.edge_pct != null ? (
                        <span className={`font-semibold text-sm ${s.edge_pct > 0 ? 'text-tabBonus' : 'text-error'}`}>
                          {s.edge_pct > 0 ? '+' : ''}{s.edge_pct.toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-muted2 text-sm">-</span>
                      )}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr key={`${group.key}-exp`}>
                      <td colSpan={7} className="!p-0" onClick={e => e.stopPropagation()}>
                        <ExpandedRow
                          special={s}
                          groupKey={group.key}
                          providers={group.providers}
                          stakePreview={stakePreview}
                          isLoadingPreview={isLoadingPreview}
                          isPlacing={isPlacing}
                          placementError={placementError}
                          selectedProviderIdx={selectedBetProvider[group.key] ?? 0}
                          onSelectProvider={(idx) => setSelectedBetProvider(prev => ({ ...prev, [group.key]: idx }))}
                          onPlaceBet={(providerId) => handlePlaceBet(s, providerId, group.key)}
                          oddsOverride={oddsOverride[group.key] ?? null}
                          editingOdds={editingOdds === group.key}
                          onEditOdds={() => setEditingOdds(group.key)}
                          onSetOdds={(val) => { setOddsOverride(prev => ({ ...prev, [group.key]: val })); setEditingOdds(null); }}
                          onResetOdds={() => { setOddsOverride(prev => { const next = { ...prev }; delete next[group.key]; return next; }); setEditingOdds(null); }}
                          onCancelEdit={() => setEditingOdds(null)}
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

function ExpandedRow({ special, groupKey, providers, stakePreview, isLoadingPreview, isPlacing, placementError, selectedProviderIdx, onSelectProvider, onPlaceBet, oddsOverride, editingOdds, onEditOdds, onSetOdds, onResetOdds, onCancelEdit }: {
  special: SpecialItem;
  groupKey: string;
  providers: string[];
  stakePreview: StakePreviewResult | null;
  isLoadingPreview: boolean;
  isPlacing: boolean;
  placementError: string | null;
  selectedProviderIdx: number;
  onSelectProvider: (idx: number) => void;
  onPlaceBet: (providerId: string) => void;
  oddsOverride: number | null;
  editingOdds: boolean;
  onEditOdds: () => void;
  onSetOdds: (val: number) => void;
  onResetOdds: () => void;
  onCancelEdit: () => void;
}) {
  const stake = stakePreview ? Math.min(stakePreview.recommended_stake, special.max_stake ?? Infinity) : 0;
  const effectiveOdds = oddsOverride ?? special.boosted_odds ?? 0;
  const oddsChanged = oddsOverride != null;
  const potentialReturn = stake * effectiveOdds;
  const potentialProfit = potentialReturn - stake;
  const eventTimeLabel = special.event_time ? formatEventTime(special.event_time) : null;

  // Suppress unused var — groupKey used for keying by parent
  void groupKey;

  return (
    <div className="px-3 py-2 bg-panel">
      {isLoadingPreview ? (<div className="text-muted text-sm">Calculating stake...</div>) : stakePreview ? (
        <div className="space-y-2">
          <div className="flex items-center gap-6 text-xs text-muted flex-wrap">
            <div><span className="text-muted2 uppercase tracking-wider">Kelly: </span><span className="text-text">{(stakePreview.kelly_fraction * 100).toFixed(1)}%</span></div>
            <div className="flex items-center gap-1">
              <span className="text-muted2 uppercase tracking-wider">Odds: </span>
              {editingOdds ? (
                <input
                  type="number"
                  step="0.01"
                  autoFocus
                  defaultValue={effectiveOdds.toFixed(2)}
                  className="w-16 bg-bg border border-tabBonus/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabBonus"
                  onBlur={(e) => {
                    const val = parseFloat(e.target.value);
                    if (!isNaN(val) && val >= 1.01) onSetOdds(val);
                    else onCancelEdit();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                    else if (e.key === 'Escape') onCancelEdit();
                  }}
                />
              ) : (
                <span
                  onClick={onEditOdds}
                  className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabBonus/50 transition-colors ${oddsChanged ? 'text-tabBonus font-medium border-tabBonus/30' : 'text-text border-transparent'}`}
                  title="Click to adjust odds"
                >
                  {effectiveOdds.toFixed(2)}
                </span>
              )}
              {oddsChanged && (
                <button onClick={onResetOdds} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset to original">x</button>
              )}
            </div>
            <div><span className="text-muted2 uppercase tracking-wider">Stake: </span><span className="text-text font-medium">{stake.toFixed(0)} kr</span>{stakePreview.was_capped_single && <span className="text-warning text-[10px] ml-1">capped</span>}{special.max_stake != null && stakePreview.recommended_stake > special.max_stake && <span className="text-warning text-[10px] ml-1">max</span>}</div>
            <div><span className="text-muted2 uppercase tracking-wider">Return: </span><span className="text-text">{potentialReturn.toFixed(0)} kr</span><span className="text-success text-xs ml-1">(+{potentialProfit.toFixed(0)})</span></div>
            <div><span className="text-muted2 uppercase tracking-wider">Bankroll: </span><span className="text-text">{stakePreview.bankroll.toFixed(0)} kr</span></div>
            {eventTimeLabel && <div><span className="text-muted2 uppercase tracking-wider">Kickoff: </span><span className="text-text">{eventTimeLabel}</span></div>}
            {special.fair_odds != null && (
              <div><span className="text-muted2 uppercase tracking-wider">Fair: </span><span className="text-text">{special.fair_odds.toFixed(2)}</span></div>
            )}
            {special.boost_pct != null && (
              <div><span className="text-muted2 uppercase tracking-wider">Boost: </span><span className="text-tabBonus">{special.boost_pct > 0 ? '+' : ''}{special.boost_pct.toFixed(0)}%</span></div>
            )}
            {!stakePreview.bonus_cleared && <div><span className="text-warning uppercase tracking-wider text-[10px]">Bonus active </span><span className="text-warning text-xs">min odds {stakePreview.min_odds_applied.toFixed(2)}</span></div>}
          </div>
          <div className="flex items-center gap-2">
            {placementError && <span className="text-error text-xs max-w-[200px] truncate">{placementError}</span>}
            {stakePreview.skip_reason ? (
              <span className="text-muted text-xs bg-border px-2 py-1">{stakePreview.skip_reason}</span>
            ) : (
              <>
                <select
                  value={selectedProviderIdx}
                  onChange={(e) => onSelectProvider(Number(e.target.value))}
                  className="bg-bg border border-border text-text text-xs px-2 py-1.5 focus:outline-none focus:border-tabBonus/50 cursor-pointer"
                >
                  {providers.map((pid, i) => (
                    <option key={pid} value={i}>
                      {formatProviderName(pid)} {stake > 0 ? `${stake.toFixed(0)} kr` : ''}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => onPlaceBet(providers[selectedProviderIdx] || providers[0])}
                  disabled={stake <= 0 || isPlacing}
                  className="px-4 py-1.5 bg-tabBonus text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity whitespace-nowrap"
                >
                  {isPlacing ? '...' : 'Place Bet'}
                </button>
              </>
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
