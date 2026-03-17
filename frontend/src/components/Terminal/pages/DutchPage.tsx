import { useState, useEffect, useDeferredValue, useMemo, useRef, Fragment, memo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '@/services/api';
import { formatProviderWithPlatform, formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName, formatProviderName, MAX_TTK_HOURS } from '@/utils/formatters';
import { resolveOutcome } from '@/utils/betting';
import { ProviderName } from '../ProviderName';
import { useExtractionFreshness } from '@/hooks/useExtractionStatus';
import { useTableSort } from '@/hooks/useTableSort';
import { SortableHeader } from '../SortableHeader';
import { FilterBar, MultiSelectDropdown, FreshnessIndicator, SearchInput, relativeTime } from '../FilterBar';
import { MyBetsSection } from '../MyBetsSection';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Provider, Bet } from '@/types';

type DutchTab = 'dutch' | 'mybets';

const dutchBetFilter = (b: Bet) => b.bet_type === 'dutch';

interface DutchLeg {
  outcome: string;
  provider: string;
  odds: number;
  edge_pct: number;
  fair_odds: number;
  stake_pct: number;
  is_sharp: boolean;
  stake?: number;
  potential_return?: number;
}

interface DutchOpp {
  id: number;
  type: string;
  event_id: string;
  market: string;
  point?: number | null;
  profit_pct: number | null;
  edge_pct: number | null;
  sport?: string;
  league?: string;
  home_team?: string;
  away_team?: string;
  display_home?: string | null;
  display_away?: string | null;
  prov_home?: string | null;
  prov_away?: string | null;
  starts_at?: string;
  detected_at?: string;
  guaranteed_profit_pct?: number;
  total_stake?: number;
  legs?: DutchLeg[];
  arb_profit_pct?: number | null;
  arb_legs?: DutchLeg[] | null;
  odds_updated_at?: string | null;
}

interface DutchPageProps {
  providers?: Provider[];
}

const MAX_ROWS = 50;

// ─── DutchRow ────────────────────────────────────────────────────────────────

interface DutchRowProps {
  opp: DutchOpp;
  idx: number;
  isExpanded: boolean;
  onToggle: (idx: number) => void;
  balanceMap: Map<string, number>;
  placedLegs: Record<number, Set<number>>;
  isPlacing: boolean;
  placingLeg: string | null;
  onPlaceLeg: (opp: DutchOpp, leg: DutchLeg, legIdx: number, effectiveOdds: number, legStake: number) => void;
  onPlaceAll: (opp: DutchOpp, effTotalStake: number, legStakes: number[], oddsOverrideMap: Record<number, number>) => void;
}

const DutchRow = memo(function DutchRow({
  opp,
  idx,
  isExpanded,
  onToggle,
  balanceMap,
  placedLegs,
  isPlacing,
  placingLeg,
  onPlaceLeg,
  onPlaceAll,
}: DutchRowProps) {
  const legs = opp.legs || [];
  const totalStake = opp.total_stake || 0;
  const gp = opp.guaranteed_profit_pct ?? opp.profit_pct ?? 0;
  const uniqueProviders = [...new Set(legs.filter(l => !l.is_sharp).map(l => l.provider))];

  const hasBalance = (providerIds: string[]) =>
    providerIds.some(id => (balanceMap.get(id) ?? 0) > 0);

  // Per-row odds override state: key = legIdx
  const [oddsOverride, setOddsOverride] = useState<Record<number, number>>({});
  const [editingOdds, setEditingOdds] = useState<number | null>(null);

  // Per-row stake override: only one leg at a time can be the "anchor"
  const [stakeOverride, setStakeOverride] = useState<{ legIdx: number; value: number } | null>(null);
  const [editingStake, setEditingStake] = useState<number | null>(null);

  // Flash detection on profit %
  const prevGp = useRef(gp);
  const [flash, setFlash] = useState<'up' | 'down' | null>(null);
  useEffect(() => {
    if (gp !== prevGp.current) {
      setFlash(gp > prevGp.current ? 'up' : 'down');
      prevGp.current = gp;
      const timer = setTimeout(() => setFlash(null), 1500);
      return () => clearTimeout(timer);
    }
  }, [gp]);

  const getEffectiveOdds = (legIdx: number, originalOdds: number): number =>
    oddsOverride[legIdx] ?? originalOdds;

  const getEffectiveStakes = (): { totalStake: number; legStakes: number[] } => {
    if (stakeOverride !== null && legs[stakeOverride.legIdx]) {
      const anchorIdx = stakeOverride.legIdx;
      const anchorStake = stakeOverride.value;
      const anchorOdds = getEffectiveOdds(anchorIdx, legs[anchorIdx].odds);
      const payout = anchorStake * anchorOdds;
      const legStakes = legs.map((leg, i) => {
        if (i === anchorIdx) return anchorStake;
        const odds = getEffectiveOdds(i, leg.odds);
        return odds > 0 ? payout / odds : 0;
      });
      return { totalStake: legStakes.reduce((a, b) => a + b, 0), legStakes };
    }
    return {
      totalStake,
      legStakes: legs.map(leg => leg.stake ?? (totalStake > 0 ? totalStake * leg.stake_pct / 100 : 0)),
    };
  };

  const { totalStake: effTotalStake, legStakes: effLegStakes } = getEffectiveStakes();
  const hasStakeEdit = stakeOverride !== null;

  return (
    <Fragment>
      <tr
        className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`}
        onClick={() => onToggle(idx)}
      >
        <td>
          <div className="flex items-center gap-2 min-w-0 group/copy">
            <span className="text-text text-sm truncate">
              {displayTeamName(opp.home_team, opp.display_home ?? opp.prov_home)} vs {displayTeamName(opp.away_team, opp.display_away ?? opp.prov_away)}
            </span>
            <button
              title="Copy event"
              className="text-muted hover:text-text transition-colors opacity-0 group-hover/copy:opacity-100 flex-shrink-0"
              onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(displayTeamName(opp.home_team, opp.display_home ?? opp.prov_home)); }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
            </button>
          </div>
          <div className="text-muted2 text-[11px]">
            {opp.sport}
            {opp.league ? ` · ${opp.league}` : ''}
            {opp.market && opp.market !== '1x2' && opp.market !== 'moneyline' ? ` · ${opp.market}` : ''}
            {opp.point != null ? ` · ${opp.point}` : ''}
            {' · '}{formatDateTime(opp.starts_at)}
          </div>
        </td>
        <td className="text-right text-muted text-sm">
          <span className="inline-flex items-center gap-1.5 justify-end">
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${hasBalance(uniqueProviders) ? 'bg-success' : 'bg-error'}`} />
            {uniqueProviders.length <= 3
              ? uniqueProviders.map((p, i) => <span key={p}>{i > 0 && ', '}<ProviderName name={p} /></span>)
              : <><ProviderName name={uniqueProviders[0]} /> <span className="text-muted2">+{uniqueProviders.length - 1}</span></>
            }
          </span>
        </td>
        <td className="text-right">
          {(() => { const ttk = getTTKFromNow(opp.starts_at); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
        </td>
        <td className={`text-right font-semibold text-sm ${(opp.edge_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
          {opp.edge_pct != null ? `${opp.edge_pct >= 0 ? '+' : ''}${opp.edge_pct.toFixed(1)}%` : '-'}
        </td>
        <td className="text-right text-text text-sm font-medium">
          {totalStake > 0 ? `${totalStake.toFixed(0)} kr` : '-'}
        </td>
        <td className={`text-right font-semibold text-sm ${flash ? `flash-${flash}` : ''} ${gp >= 0 ? 'text-success' : 'text-error'}`}>
          {gp >= 0 ? `+${gp.toFixed(2)}%` : `${gp.toFixed(2)}%`}
        </td>
        {(() => { const rt = relativeTime(opp.odds_updated_at); return <td className={`text-right text-sm ${rt.className}`}>{rt.text}</td>; })()}
      </tr>

      {isExpanded && (
        <tr key={`${opp.id}-expanded`}>
          <td colSpan={7} className="!p-0" onClick={e => e.stopPropagation()}>
            <table className="sq">
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th className="text-right">Provider</th>
                  <th className="text-right">Odds</th>
                  <th className="text-right">Fair</th>
                  <th className="text-right">Edge</th>
                  <th className="text-right">Stake</th>
                  <th className="text-right">Return</th>
                  <th className="text-right"></th>
                </tr>
              </thead>
              <tbody>
                {legs.map((leg, legIdx) => {
                  const effectiveOdds = getEffectiveOdds(legIdx, leg.odds);
                  const oddsChanged = legIdx in oddsOverride;
                  const legStake = effLegStakes[legIdx];
                  const legReturn = legStake * effectiveOdds;
                  const isEditingThisOdds = editingOdds === legIdx;
                  const isEditingThisStake = editingStake === legIdx;
                  const isEditedLeg = stakeOverride?.legIdx === legIdx;
                  const legKey = `${opp.id}|${legIdx}`;
                  const isPlacingThis = isPlacing && placingLeg === legKey;

                  return (
                    <tr key={legIdx}>
                      <td>
                        <span className={`inline-block w-1.5 h-1.5 mr-1.5 align-middle ${leg.edge_pct > 0 ? 'bg-success' : 'bg-muted2'}`} />
                        {resolveOutcome(leg.outcome, opp, opp.point, true)}
                        {leg.is_sharp && <span className="text-[9px] ml-1 px-1 py-0.5 bg-muted/10 text-muted2">PIN</span>}
                      </td>
                      <td className="text-right"><ProviderName name={leg.provider} /></td>
                      <td className="text-right font-medium">
                        <div className="flex items-center justify-end gap-1">
                          {isEditingThisOdds ? (
                            <input
                              type="number"
                              step="0.01"
                              autoFocus
                              defaultValue={effectiveOdds.toFixed(2)}
                              className="w-16 bg-bg border border-success/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-success"
                              onBlur={(e) => {
                                const val = parseFloat(e.target.value);
                                if (!isNaN(val) && val >= 1.01) {
                                  setOddsOverride(prev => ({ ...prev, [legIdx]: val }));
                                }
                                setEditingOdds(null);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                else if (e.key === 'Escape') setEditingOdds(null);
                              }}
                            />
                          ) : (
                            <span
                              onClick={() => setEditingOdds(legIdx)}
                              className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-success/50 transition-colors ${oddsChanged ? 'text-success font-medium border-success/30' : 'text-text border-transparent'}`}
                              title="Click to adjust odds"
                            >
                              {effectiveOdds.toFixed(2)}
                            </span>
                          )}
                          {oddsChanged && (
                            <button
                              onClick={() => setOddsOverride(prev => { const next = { ...prev }; delete next[legIdx]; return next; })}
                              className="text-muted2 hover:text-text text-[10px]"
                              title="Reset to original"
                            >
                              x
                            </button>
                          )}
                        </div>
                      </td>
                      <td className="text-right text-muted">{leg.fair_odds.toFixed(2)}</td>
                      <td className={`text-right font-medium ${leg.edge_pct > 0 ? 'text-success' : 'text-muted'}`}>
                        {leg.edge_pct > 0 ? '+' : ''}{leg.edge_pct.toFixed(1)}%
                      </td>
                      <td className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          {isEditingThisStake ? (
                            <input
                              type="number"
                              step="1"
                              autoFocus
                              defaultValue={legStake > 0 ? legStake.toFixed(0) : ''}
                              placeholder="Stake"
                              className="w-20 bg-bg border border-success/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-success"
                              onBlur={(e) => {
                                const val = parseFloat(e.target.value);
                                if (!isNaN(val) && val > 0) {
                                  setStakeOverride({ legIdx, value: val });
                                }
                                setEditingStake(null);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                else if (e.key === 'Escape') setEditingStake(null);
                              }}
                            />
                          ) : (
                            <span
                              onClick={() => setEditingStake(legIdx)}
                              className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-success/50 transition-colors ${isEditedLeg ? 'text-success font-medium border-success/30' : 'text-text border-transparent'}`}
                              title="Click to set stake"
                            >
                              {legStake > 0 ? `${legStake.toFixed(0)} kr` : '-'}
                            </span>
                          )}
                          {isEditedLeg && (
                            <button
                              onClick={() => setStakeOverride(null)}
                              className="text-muted2 hover:text-text text-[10px]"
                              title="Reset to default stake"
                            >
                              x
                            </button>
                          )}
                        </div>
                        {legStake > 0 && <span className="text-muted2 text-[10px]">({leg.stake_pct.toFixed(0)}%)</span>}
                      </td>
                      <td className="text-right">{legReturn > 0 ? `${legReturn.toFixed(0)} kr` : '-'}</td>
                      <td className="text-right">
                        {placedLegs[opp.id]?.has(legIdx) ? (
                          <span className="text-success text-[10px] font-medium">✓ placed</span>
                        ) : legStake > 0 ? (
                          <button
                            onClick={() => onPlaceLeg(opp, leg, legIdx, effectiveOdds, legStake)}
                            disabled={isPlacing}
                            className="px-2 py-1 bg-panel2 text-muted text-[10px] font-medium hover:text-text hover:bg-panel2/80 disabled:opacity-50 transition-all whitespace-nowrap"
                          >
                            {isPlacingThis ? '...' : 'Place Bet'}
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {effTotalStake > 0 && (
              <div className="px-3 py-2 border-t border-border bg-panel flex items-center justify-between text-xs text-muted">
                <div className="flex items-center gap-6">
                  <div>
                    <span className="text-muted2 uppercase tracking-wider">Total Stake: </span>
                    <span className={`font-medium ${hasStakeEdit ? 'text-success' : 'text-text'}`}>{effTotalStake.toFixed(0)} kr</span>
                    {hasStakeEdit && totalStake > 0 && (
                      <span className="text-muted2 text-[10px] ml-1">(was {totalStake.toFixed(0)})</span>
                    )}
                  </div>
                  {gp !== 0 && (
                    <div>
                      <span className="text-muted2 uppercase tracking-wider">{gp > 0 ? 'Guaranteed' : 'Loss'}: </span>
                      <span className={gp > 0 ? 'text-success font-medium' : 'text-error font-medium'}>
                        {gp > 0 ? '+' : ''}{(effTotalStake * gp / 100).toFixed(0)} kr
                      </span>
                    </div>
                  )}
                </div>
                {(() => {
                  const allPlaced = placedLegs[opp.id]?.size === legs.length;
                  const isPlacingAll = isPlacing && placingLeg === `${opp.id}|all`;
                  return allPlaced ? (
                    <span className="text-success text-[10px] font-medium">✓ all legs placed</span>
                  ) : (
                    <button
                      onClick={() => onPlaceAll(opp, effTotalStake, effLegStakes, oddsOverride)}
                      disabled={isPlacing}
                      className="px-3 py-1.5 bg-success text-bg text-[11px] font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                    >
                      {isPlacingAll ? '...' : 'Place All'}
                    </button>
                  );
                })()}
              </div>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
});

// ─── DutchPage ────────────────────────────────────────────────────────────────

export function DutchPage({ providers = [] }: DutchPageProps) {
  const freshness = useExtractionFreshness();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<DutchTab>('dutch');
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [selectedLeagues, setSelectedLeagues] = useState<Set<string>>(new Set());
  const [searchInput, setSearchInput] = useState('');
  const search = useDeferredValue(searchInput);
  const [scanResults, setScanResults] = useState<DutchOpp[] | null>(null);
  const [isScanning, setIsScanning] = useState(false);

  // Place bet state (kept at page level for toasts / query invalidation)
  const [isPlacing, setIsPlacing] = useState(false);
  const [placingLeg, setPlacingLeg] = useState<string | null>(null);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);
  const [placedLegs, setPlacedLegs] = useState<Record<number, Set<number>>>({});
  const [myBetsCount, setMyBetsCount] = useState<number | null>(null);

  const { data: dutchData, isLoading } = useQuery({
    queryKey: ['opportunities', 'dutch'],
    queryFn: () => api.getOpportunities('dutch', true),
    placeholderData: keepPreviousData,
  });
  const opportunities = (dutchData?.opportunities ?? []) as unknown as DutchOpp[];

  const { data: betsData } = useQuery({
    queryKey: ['bets', 'pending'],
    queryFn: () => api.getBets('pending', 500),
    staleTime: 60_000,
  });

  // Sync myBetsCount from bets query data
  useEffect(() => {
    if (!betsData?.bets) return;
    setMyBetsCount(betsData.bets.filter(dutchBetFilter).length);
  }, [betsData]);

  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    for (const p of providers) set.add(p.id);
    for (const opp of opportunities) {
      for (const leg of opp.legs || []) {
        set.add(leg.provider);
      }
    }
    return Array.from(set).sort();
  }, [providers, opportunities]);

  const availableLeagues = useMemo(() => {
    const set = new Set<string>();
    for (const opp of opportunities) {
      if (opp.league) set.add(opp.league);
    }
    return Array.from(set).sort();
  }, [opportunities]);

  const balanceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of providers) m.set(p.id, p.balance);
    return m;
  }, [providers]);

  const filtered = useMemo(() => {
    let result = scanResults !== null ? scanResults : opportunities;
    result = result.filter(d => { const ttk = getTTKFromNow(d.starts_at); return ttk === null || (ttk > 1 / 60 && ttk <= MAX_TTK_HOURS); });
    if (selectedProviders.size > 0) {
      result = result.filter(d => {
        const legs = d.legs || [];
        if (!legs.every(leg => selectedProviders.has(leg.provider))) return false;
        if (selectedProviders.size < 3 && legs.length > selectedProviders.size) return false;
        return true;
      });
    }
    if (selectedLeagues.size > 0) {
      result = result.filter(d => d.league != null && selectedLeagues.has(d.league));
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(d =>
        (d.home_team?.toLowerCase().includes(q)) ||
        (d.away_team?.toLowerCase().includes(q)) ||
        (d.display_home?.toLowerCase().includes(q)) ||
        (d.display_away?.toLowerCase().includes(q)) ||
        (d.prov_home?.toLowerCase().includes(q)) ||
        (d.prov_away?.toLowerCase().includes(q)) ||
        (d.sport?.toLowerCase().includes(q)) ||
        (d.league?.toLowerCase().includes(q)) ||
        (d.legs || []).some(leg => leg.provider.toLowerCase().includes(q))
      );
    }
    return result.slice(0, MAX_ROWS);
  }, [opportunities, scanResults, selectedProviders, selectedLeagues, search]);

  type DutchSortCol = 'edge' | 'stake' | 'profit' | 'ttk';
  const dutchSortExtractors = useMemo(() => ({
    edge:   (d: DutchOpp) => d.edge_pct ?? 0,
    stake:  (d: DutchOpp) => d.total_stake ?? 0,
    profit: (d: DutchOpp) => d.guaranteed_profit_pct ?? d.profit_pct ?? 0,
    ttk:    (d: DutchOpp) => getTTKFromNow(d.starts_at) ?? 99999,
  }), []);
  const { sorted: sortedDutch, sort: dutchSort, toggle: toggleDutchSort } =
    useTableSort<DutchOpp, DutchSortCol>(filtered, dutchSortExtractors, { column: 'edge', direction: 'desc' });

  // Virtualizer
  const tableContainerRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: sortedDutch.length,
    getScrollElement: () => tableContainerRef.current,
    estimateSize: (idx) => {
      const isExpanded = selectedOpp === idx;
      return isExpanded ? 100 : 52;
    },
    overscan: 10,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const totalVirtualHeight = virtualizer.getTotalSize();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom = virtualItems.length > 0
    ? totalVirtualHeight - virtualItems[virtualItems.length - 1].end
    : 0;

  const toggleProvider = (p: string) => {
    setScanResults(null);
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
  };

  const handleScanBetween = async () => {
    const providerList = [...selectedProviders];
    setIsScanning(true);
    setBetError(null);
    try {
      const res = await api.getDutchWorkflow(providerList, false, 100, providerList);
      const items = (res.opportunities as unknown as DutchOpp[]).map((o, i) => ({
        ...o,
        id: -(i + 1),
      }));
      setScanResults(items);
    } catch {
      setBetError('Scan failed');
    } finally {
      setIsScanning(false);
    }
  };

  const toggleLeague = (l: string) => {
    setSelectedLeagues(prev => {
      const next = new Set(prev);
      if (next.has(l)) next.delete(l); else next.add(l);
      return next;
    });
  };

  const handlePlaceLeg = async (
    opp: DutchOpp,
    leg: DutchLeg,
    legIdx: number,
    effectiveOdds: number,
    legStake: number,
  ) => {
    if (legStake <= 0) return;
    const legKey = `${opp.id}|${legIdx}`;
    setIsPlacing(true);
    setPlacingLeg(legKey);
    setBetError(null);
    setBetSuccess(null);

    try {
      await api.createBet({
        event_id: opp.event_id,
        provider_id: leg.provider,
        market: opp.market,
        outcome: leg.outcome,
        odds: effectiveOdds,
        stake: legStake,
        point: opp.point,
        is_bonus: false,
        utility_score: leg.edge_pct != null ? leg.edge_pct / 100 : undefined,
        selection_probability: leg.fair_odds > 1 ? 1 / leg.fair_odds : undefined,
        bet_type: 'dutch',
      });
      setPlacedLegs(prev => {
        const existing = prev[opp.id] || new Set<number>();
        const next = new Set(existing);
        next.add(legIdx);
        return { ...prev, [opp.id]: next };
      });

      const outcomeLabel = resolveOutcome(leg.outcome, opp, opp.point, true);
      setBetSuccess(`Recorded: ${legStake.toFixed(0)} kr on ${outcomeLabel} @ ${effectiveOdds.toFixed(2)} (${formatProviderName(leg.provider)})`);
      setTimeout(() => setBetSuccess(null), 5000);
      queryClient.invalidateQueries({ queryKey: ['opportunities', 'dutch'] });
      queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to place bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
      setPlacingLeg(null);
    }
  };

  const handlePlaceAll = async (
    opp: DutchOpp,
    effTotalStake: number,
    legStakes: number[],
    oddsOverrideMap: Record<number, number>,
  ) => {
    const legs = opp.legs || [];
    if (legs.length === 0 || effTotalStake <= 0) return;

    const batchLegs = legs.map((leg, legIdx) => {
      const legStake = legStakes[legIdx];
      const odds = oddsOverrideMap[legIdx] ?? leg.odds;
      return {
        event_id: opp.event_id,
        provider_id: leg.provider,
        market: opp.market,
        outcome: leg.outcome,
        odds,
        stake: legStake,
        point: opp.point,
        is_bonus: false,
        utility_score: leg.edge_pct != null ? leg.edge_pct / 100 : undefined,
        selection_probability: leg.fair_odds > 1 ? 1 / leg.fair_odds : undefined,
        bet_type: 'dutch',
      };
    }).filter(l => l.stake > 0);

    if (batchLegs.length === 0) return;

    setIsPlacing(true);
    setPlacingLeg(`${opp.id}|all`);
    setBetError(null);
    setBetSuccess(null);

    try {
      const res = await api.createBatchBets(batchLegs);

      const successIdxs = new Set<number>();
      const errors: string[] = [];
      for (const r of res.results) {
        if (r.success) {
          successIdxs.add(r.leg_index);
        } else {
          errors.push(`${formatProviderName(r.provider_id)}: ${r.error}`);
        }
      }

      setPlacedLegs(prev => ({ ...prev, [opp.id]: successIdxs }));

      if (res.placed_count === res.total_legs) {
        setBetSuccess(`All ${res.placed_count} legs recorded — ${res.total_staked.toFixed(0)} kr total`);
      } else if (res.placed_count > 0) {
        setBetSuccess(`${res.placed_count}/${res.total_legs} legs recorded — ${res.total_staked.toFixed(0)} kr`);
        if (errors.length > 0) setBetError(errors.join(' · '));
      } else {
        setBetError(errors.join(' · ') || 'Failed to place any legs');
      }

      setTimeout(() => { setBetSuccess(null); setBetError(null); }, 8000);
      queryClient.invalidateQueries({ queryKey: ['opportunities', 'dutch'] });
      queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to place bets';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
      setPlacingLeg(null);
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="dutch" color={TAB_COLORS.dutch} size={16} />
          Dutch
        </h2>
        {activeTab === 'dutch' && (
          <SearchInput value={searchInput} onChange={setSearchInput} placeholder="Search event, provider..." accentColor="success" />
        )}
      </div>

      {/* Sub-tab selector */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: 'dutch' as DutchTab, label: 'Dutch Bets', count: sortedDutch.length },
          { id: 'mybets' as DutchTab, label: 'My Bets', count: myBetsCount },
        ]).map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
              activeTab === tab.id
                ? 'border-success text-success'
                : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {tab.label}
            {tab.count != null && <span className="ml-1 text-muted">({tab.count})</span>}
          </button>
        ))}
      </div>

      {activeTab === 'mybets' && (
        <MyBetsSection filter={dutchBetFilter} colorKey="dutch" />
      )}

      {activeTab === 'dutch' && <>
      {/* Feedback toasts */}
      {betSuccess && (
        <div className="px-3 py-2 bg-success/10 border border-success/30 text-success text-xs flex items-center justify-between">
          <span>{betSuccess}</span>
          <button onClick={() => setBetSuccess(null)} className="text-success/60 hover:text-success ml-2">x</button>
        </div>
      )}
      {betError && (
        <div className="px-3 py-2 bg-error/10 border border-error/30 text-error text-xs flex items-center justify-between">
          <span>{betError}</span>
          <button onClick={() => setBetError(null)} className="text-error/60 hover:text-error ml-2">x</button>
        </div>
      )}

      {/* Filters */}
      <FilterBar>
        {availableProviders.length > 0 && (
          <MultiSelectDropdown
            label="Provider"
            options={availableProviders}
            selected={selectedProviders}
            onToggle={toggleProvider}
            onClear={() => { setSelectedProviders(new Set()); setScanResults(null); }}
            format={formatProviderWithPlatform}
            accentColor="success"
          />
        )}
        {availableLeagues.length > 0 && (
          <MultiSelectDropdown
            label="League"
            options={availableLeagues}
            selected={selectedLeagues}
            onToggle={toggleLeague}
            onClear={() => setSelectedLeagues(new Set())}
            accentColor="success"
          />
        )}
        <div className="ml-auto"><FreshnessIndicator tiers={[['soft', freshness.soft], ['sharp', freshness.sharp]]} /></div>
      </FilterBar>

      {isLoading && opportunities.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Loading...
        </div>
      ) : sortedDutch.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel flex flex-col items-center gap-3">
          {isScanning ? (
            <span>Scanning between selected providers...</span>
          ) : scanResults !== null ? (
            <span>No dutch found between selected providers.</span>
          ) : selectedProviders.size >= 2 && opportunities.length > 0 ? (
            <>
              <span>No pre-computed dutch for selected providers.</span>
              <button
                onClick={handleScanBetween}
                className="px-3 py-1.5 text-xs bg-success/10 border border-success/30 text-success hover:bg-success/20 rounded"
              >
                Scan between {[...selectedProviders].map(formatProviderName).join(' + ')}
              </button>
            </>
          ) : selectedProviders.size >= 2 && opportunities.length === 0 ? (
            <>
              <span>No pre-computed dutch opportunities.</span>
              <button
                onClick={handleScanBetween}
                className="px-3 py-1.5 text-xs bg-success/10 border border-success/30 text-success hover:bg-success/20 rounded"
              >
                Scan between {[...selectedProviders].map(formatProviderName).join(' + ')}
              </button>
            </>
          ) : opportunities.length === 0 ? (
            <span>No dutch opportunities found. Run extraction first.</span>
          ) : (
            <span>No matches for current filters.</span>
          )}
        </div>
      ) : (
        <div className="border-l-2 border-success flex-1 min-h-0">
          {/* Scrollable virtualizer container */}
          <div
            ref={tableContainerRef}
            style={{ height: '100%', overflowY: 'auto' }}
          >
            <table className="sq" style={{ tableLayout: 'fixed', width: '100%' }}>
              <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
                <tr>
                  <th style={{ width: '35%' }}>Event</th>
                  <th className="text-right">Providers</th>
                  <SortableHeader column="ttk" label="TTK" sort={dutchSort} onToggle={toggleDutchSort} />
                  <SortableHeader column="edge" label="Edge" sort={dutchSort} onToggle={toggleDutchSort} />
                  <SortableHeader column="stake" label="Stake" sort={dutchSort} onToggle={toggleDutchSort} />
                  <SortableHeader column="profit" label="Profit" sort={dutchSort} onToggle={toggleDutchSort} />
                  <th className="text-right">Upd</th>
                </tr>
              </thead>
              <tbody>
                {paddingTop > 0 && (
                  <tr><td colSpan={7} style={{ height: paddingTop, padding: 0 }} /></tr>
                )}
                {virtualItems.map(virtualRow => {
                  const opp = sortedDutch[virtualRow.index];
                  return (
                    <DutchRow
                      key={opp.id}
                      opp={opp}
                      idx={virtualRow.index}
                      isExpanded={selectedOpp === virtualRow.index}
                      onToggle={(idx) => setSelectedOpp(selectedOpp === idx ? null : idx)}
                      balanceMap={balanceMap}
                      placedLegs={placedLegs}
                      isPlacing={isPlacing}
                      placingLeg={placingLeg}
                      onPlaceLeg={handlePlaceLeg}
                      onPlaceAll={handlePlaceAll}
                    />
                  );
                })}
                {paddingBottom > 0 && (
                  <tr><td colSpan={7} style={{ height: paddingBottom, padding: 0 }} /></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
      </>}
    </div>
  );
}
