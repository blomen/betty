import { useState, useCallback, useMemo, useEffect, useRef, Fragment } from 'react';
import { api } from '@/services/api';
import { formatProviderName, formatProviderWithPlatform, formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName, MAX_TTK_HOURS } from '@/utils/formatters';
import { resolveOutcome } from '@/utils/betting';
import { ProviderName } from '../ProviderName';
import { useTableSort } from '@/hooks/useTableSort';
import { SortableHeader } from '../SortableHeader';
import { MultiSelectDropdown } from '../FilterBar';
import type { Provider } from '@/types';

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
  starts_at?: string;
  detected_at?: string;
  guaranteed_profit_pct?: number;
  total_stake?: number;
  legs?: DutchLeg[];
  arb_profit_pct?: number | null;
  arb_legs?: DutchLeg[] | null;
}

interface DutchAnchorPageProps {
  providers: Provider[];
}

const MAX_ROWS = 50;
const DUTCH_ANCHOR_SETTINGS_KEY = 'dutch-anchor-settings';

function loadDutchAnchorSettings(): { providers: string[]; stake: string; limitedOnly: boolean; counterparts?: string[] } | null {
  try {
    const raw = localStorage.getItem(DUTCH_ANCHOR_SETTINGS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function saveDutchAnchorSettings(providers: Set<string>, stake: string, limitedOnly: boolean, counterparts: Set<string>) {
  if (providers.size === 0 && !stake) {
    localStorage.removeItem(DUTCH_ANCHOR_SETTINGS_KEY);
  } else {
    localStorage.setItem(DUTCH_ANCHOR_SETTINGS_KEY, JSON.stringify({
      providers: Array.from(providers), stake, limitedOnly,
      counterparts: Array.from(counterparts),
    }));
  }
}

export function DutchAnchorPage({ providers }: DutchAnchorPageProps) {
  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [search] = useState('');

  // Workflow panel state — initialized from localStorage
  const saved = useRef(loadDutchAnchorSettings());
  const [workflowProviders, setWorkflowProviders] = useState<Set<string>>(
    () => new Set(saved.current?.providers?.slice(0, 1) ?? [])
  );
  const [workflowStake, setWorkflowStake] = useState<string>(
    () => saved.current?.stake ?? ''
  );
  const [workflowMajorOnly, setWorkflowMajorOnly] = useState(
    () => saved.current?.limitedOnly ?? false
  );
  const [workflowResults, setWorkflowResults] = useState<DutchOpp[] | null>(null);
  const [anchorWagering, setAnchorWagering] = useState<Record<string, {
    status: string; wagered: number; requirement: number; remaining: number;
    progress_pct: number; min_odds: number; bonus_amount: number;
    bonus_type: string | null; days_remaining: number | null;
  }>>({});
  const [isScanning, setIsScanning] = useState(false);
  // Counterpart providers — restrict which providers can be used as opposing legs
  const [counterpartProviders, setCounterpartProviders] = useState<Set<string>>(
    () => new Set(saved.current?.counterparts ?? [])
  );

  // Persist settings to localStorage on change
  useEffect(() => {
    saveDutchAnchorSettings(workflowProviders, workflowStake, workflowMajorOnly, counterpartProviders);
  }, [workflowProviders, workflowStake, workflowMajorOnly, counterpartProviders]);

  // Odds override: key = "oppId|legIdx", value = new odds
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);

  // Anchor stake override: key = oppId, value = { legIdx, stake }
  const [anchorStake, setAnchorStake] = useState<Record<number, { legIdx: number; stake: number }>>({});
  const [editingStake, setEditingStake] = useState<string | null>(null);

  // Place bet state
  const [isPlacing, setIsPlacing] = useState(false);
  const [placingLeg, setPlacingLeg] = useState<string | null>(null);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);
  const [placedLegs, setPlacedLegs] = useState<Record<number, Set<number>>>({});

  const handleScan = useCallback(async () => {
    if (workflowProviders.size === 0) return;
    setIsScanning(true);
    setSelectedOpp(null);
    setOddsOverride({});
    setAnchorStake({});
    setPlacedLegs({});
    try {
      const res = await api.getDutchWorkflow(
        Array.from(workflowProviders),
        workflowMajorOnly,
        MAX_ROWS,
        counterpartProviders.size > 0 ? Array.from(counterpartProviders) : undefined,
      );
      const opps = (res.opportunities ?? []) as DutchOpp[];
      setAnchorWagering(res.anchor_wagering ?? {});
      // Auto-apply anchor stake to the first leg matching a workflow provider
      const stakeVal = parseFloat(workflowStake);
      if (!isNaN(stakeVal) && stakeVal > 0) {
        const anchors: Record<number, { legIdx: number; stake: number }> = {};
        for (const opp of opps) {
          const legs = opp.legs || [];
          const idx = legs.findIndex(l => workflowProviders.has(l.provider));
          if (idx >= 0) anchors[opp.id] = { legIdx: idx, stake: stakeVal };
        }
        setAnchorStake(anchors);
      }
      setWorkflowResults(opps);
    } catch (err) {
      console.error('Workflow scan failed:', err);
    } finally {
      setIsScanning(false);
    }
  }, [workflowProviders, workflowMajorOnly, workflowStake, counterpartProviders]);

  // Auto-scan on mount if settings are saved
  const didAutoScan = useRef(false);
  useEffect(() => {
    if (!didAutoScan.current && workflowProviders.size > 0) {
      didAutoScan.current = true;
      handleScan();
    }
  }, [handleScan, workflowProviders.size]);


  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    for (const p of providers) {
      if (p.is_enabled) set.add(p.id);
    }
    return Array.from(set).sort();
  }, [providers]);

  const balanceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of providers) m.set(p.id, p.balance);
    return m;
  }, [providers]);

  const hasBalance = (providerIds: string[]) =>
    providerIds.some(id => (balanceMap.get(id) ?? 0) > 0);

  const filtered = useMemo(() => {
    let result = workflowResults ?? [];
    result = result.filter(d => { const ttk = getTTKFromNow(d.starts_at); return ttk === null || (ttk > 1 / 60 && ttk <= MAX_TTK_HOURS); });
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(d =>
        (d.home_team?.toLowerCase().includes(q)) ||
        (d.away_team?.toLowerCase().includes(q)) ||
        (d.display_home?.toLowerCase().includes(q)) ||
        (d.display_away?.toLowerCase().includes(q)) ||
        (d.sport?.toLowerCase().includes(q)) ||
        (d.league?.toLowerCase().includes(q)) ||
        (d.legs || []).some(leg => leg.provider.toLowerCase().includes(q))
      );
    }
    return result.slice(0, MAX_ROWS);
  }, [workflowResults, search]);

  type AnchorSortCol = 'stake' | 'profit' | 'ttk';
  const anchorSortExtractors = useMemo(() => ({
    stake:  (d: DutchOpp) => {
      const anchor = anchorStake[d.id];
      if (anchor) {
        const legs = d.legs || [];
        const anchorPct = legs[anchor.legIdx]?.stake_pct ?? 0;
        if (anchorPct > 0) return anchor.stake / (anchorPct / 100);
      }
      return d.total_stake ?? 0;
    },
    profit: (d: DutchOpp) => d.guaranteed_profit_pct ?? d.profit_pct ?? 0,
    ttk:    (d: DutchOpp) => getTTKFromNow(d.starts_at) ?? 99999,
  }), [anchorStake]);
  const { sorted, sort: anchorSort, toggle: toggleAnchorSort } =
    useTableSort<DutchOpp, AnchorSortCol>(filtered, anchorSortExtractors, { column: 'profit', direction: 'desc' });

  const getEffectiveOdds = (oppId: number, legIdx: number, originalOdds: number): number => {
    const key = `${oppId}|${legIdx}`;
    return oddsOverride[key] ?? originalOdds;
  };

  const getEffectiveStakes = (opp: DutchOpp): { totalStake: number; legStakes: number[] } => {
    const legs = opp.legs || [];
    const anchor = anchorStake[opp.id];
    const baseTotalStake = opp.total_stake || 0;

    if (anchor && legs[anchor.legIdx]) {
      const anchorLeg = legs[anchor.legIdx];
      const anchorPct = anchorLeg.stake_pct;
      if (anchorPct > 0) {
        const newTotal = anchor.stake / (anchorPct / 100);
        return {
          totalStake: newTotal,
          legStakes: legs.map(leg => newTotal * leg.stake_pct / 100),
        };
      }
    }

    return {
      totalStake: baseTotalStake,
      legStakes: legs.map(leg => leg.stake ?? (baseTotalStake > 0 ? baseTotalStake * leg.stake_pct / 100 : 0)),
    };
  };

  const handlePlaceLeg = async (opp: DutchOpp, leg: DutchLeg, legIdx: number) => {
    const { legStakes } = getEffectiveStakes(opp);
    const legStake = legStakes[legIdx];
    if (legStake <= 0) return;

    const odds = getEffectiveOdds(opp.id, legIdx, leg.odds);
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
        odds,
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
      setBetSuccess(`Recorded: ${legStake.toFixed(0)} kr on ${outcomeLabel} @ ${odds.toFixed(2)} (${formatProviderName(leg.provider)})`);
      setTimeout(() => setBetSuccess(null), 5000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to place bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
      setPlacingLeg(null);
    }
  };

  const handlePlaceAll = async (opp: DutchOpp) => {
    const legs = opp.legs || [];
    const { totalStake: effTotal, legStakes } = getEffectiveStakes(opp);
    if (legs.length === 0 || effTotal <= 0) return;

    const batchLegs = legs.map((leg, legIdx) => {
      const legStake = legStakes[legIdx];
      const odds = getEffectiveOdds(opp.id, legIdx, leg.odds);
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
        if (errors.length > 0) {
          setBetError(errors.join(' · '));
        }
      } else {
        setBetError(errors.join(' · ') || 'Failed to place any legs');
      }

      setTimeout(() => { setBetSuccess(null); setBetError(null); }, 8000);
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
    <div className="space-y-2">
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

      {/* Workflow panel */}
      <div className="flex items-center gap-3 px-3 py-2 bg-panel border border-border text-xs">
        <MultiSelectDropdown
          label="Anchor"
          options={availableProviders}
          selected={workflowProviders}
          onToggle={(p) => setWorkflowProviders(prev =>
            prev.has(p) ? new Set() : new Set([p])
          )}
          onClear={() => setWorkflowProviders(new Set())}
          format={formatProviderWithPlatform}
          accentColor="success"
        />
        <MultiSelectDropdown
          label="Counter"
          options={availableProviders}
          selected={counterpartProviders}
          onToggle={(p) => setCounterpartProviders(prev => {
            const next = new Set(prev);
            if (next.has(p)) next.delete(p); else next.add(p);
            return next;
          })}
          onClear={() => setCounterpartProviders(new Set())}
          format={formatProviderWithPlatform}
          accentColor="success"
        />
        <div className="flex items-center gap-1">
          <input
            type="number"
            placeholder="Stake"
            value={workflowStake}
            onChange={e => setWorkflowStake(e.target.value)}
            className="w-20 bg-bg border border-border text-text text-xs px-2 py-1 text-right focus:outline-none focus:border-success"
          />
          <span className="text-muted2">kr</span>
        </div>
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={workflowMajorOnly}
            onChange={e => setWorkflowMajorOnly(e.target.checked)}
            className="accent-success"
          />
          <span className="text-muted">Limited</span>
        </label>
        <button
          onClick={handleScan}
          disabled={workflowProviders.size === 0 || isScanning}
          className="px-3 py-1 bg-success text-bg text-xs font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {isScanning ? 'Scanning...' : 'Scan'}
        </button>
        {workflowResults && (
          <button
            onClick={() => { setWorkflowResults(null); setAnchorStake({}); setOddsOverride({}); setPlacedLegs({}); }}
            className="text-muted2 hover:text-text text-[10px]"
            title="Clear workflow results"
          >
            clear
          </button>
        )}
        <div className="ml-auto flex items-center gap-3">
          {workflowResults && (
            <span className="text-muted2">{sorted.length} results</span>
          )}
        </div>
      </div>

      {/* Anchor wagering progress */}
      {Object.keys(anchorWagering).length > 0 && (
        <div className="flex flex-wrap gap-3 px-3 py-2 bg-panel border border-border text-xs">
          {Object.entries(anchorWagering).map(([pid, w]) => (
            <div key={pid} className="flex items-center gap-2">
              <span className="font-medium text-text">{formatProviderName(pid)}</span>
              <div className="flex items-center gap-1.5">
                <div className="w-24 h-1.5 bg-border rounded-full overflow-hidden">
                  <div
                    className="h-full bg-success rounded-full transition-all"
                    style={{ width: `${Math.min(100, w.progress_pct)}%` }}
                  />
                </div>
                <span className="text-muted">{w.progress_pct.toFixed(0)}%</span>
              </div>
              <span className="text-muted2">
                {w.remaining.toFixed(0)} kr left
                {w.requirement > 0 && ` of ${w.requirement.toFixed(0)}`}
              </span>
              {w.min_odds > 0 && (
                <span className="text-muted2">min {w.min_odds.toFixed(2)}</span>
              )}
              {w.days_remaining != null && (
                <span className={`${w.days_remaining <= 7 ? 'text-error' : 'text-muted2'}`}>
                  {w.days_remaining}d left
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {isScanning ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Scanning for dutch opportunities...
        </div>
      ) : !workflowResults ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Select anchor provider and scan to find dutch opportunities.
        </div>
      ) : sorted.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          No dutch opportunities found for selected provider.
        </div>
      ) : (
        <div className="border-l-2 border-success">
          <table className="sq">
            <thead>
              <tr>
                <th style={{ width: '35%' }}>Event</th>
                <th className="text-right">Providers</th>
                <SortableHeader column="ttk" label="TTK" sort={anchorSort} onToggle={toggleAnchorSort} />
                <SortableHeader column="stake" label="Stake" sort={anchorSort} onToggle={toggleAnchorSort} />
                <SortableHeader column="profit" label="Profit" sort={anchorSort} onToggle={toggleAnchorSort} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((opp, idx) => {
                const isSelected = selectedOpp === idx;
                const gp = opp.guaranteed_profit_pct ?? opp.profit_pct ?? 0;
                const legs = opp.legs || [];
                const { totalStake: effTotal } = getEffectiveStakes(opp);
                const uniqueProviders = [...new Set(legs.filter(l => !l.is_sharp).map(l => l.provider))];

                return (
                  <Fragment key={opp.id}>
                    <tr
                      className={`cursor-pointer ${isSelected ? 'expanded' : ''}`}
                      onClick={() => setSelectedOpp(isSelected ? null : idx)}
                    >
                      <td>
                        <div className="flex items-center gap-2 min-w-0 group/copy">
                          <span className="text-text text-sm truncate">{displayTeamName(opp.home_team, opp.display_home)} vs {displayTeamName(opp.away_team, opp.display_away)}</span>
                          <button
                            title="Copy event"
                            className="text-muted hover:text-text transition-colors opacity-0 group-hover/copy:opacity-100 flex-shrink-0"
                            onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(displayTeamName(opp.home_team, opp.display_home)); }}
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                          </button>
                        </div>
                        <div className="text-muted2 text-[11px]">
                          {opp.sport}
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
                      <td className="text-right text-text text-sm font-medium">
                        {effTotal > 0 ? `${effTotal.toFixed(0)} kr` : '-'}
                      </td>
                      <td className={`text-right font-semibold text-sm ${gp >= 0 ? 'text-success' : 'text-error'}`}>
                        {gp >= 0 ? `+${gp.toFixed(2)}%` : `${gp.toFixed(2)}%`}
                      </td>
                    </tr>

                    {isSelected && (() => {
                      const { totalStake: effTotalStake, legStakes: effLegStakes } = getEffectiveStakes(opp);
                      const hasAnchor = opp.id in anchorStake;
                      return (
                      <tr key={`${opp.id}-expanded`}>
                        <td colSpan={5} className="!p-0" onClick={e => e.stopPropagation()}>
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
                                const oddsKey = `${opp.id}|${legIdx}`;
                                const stakeKey = `${opp.id}|${legIdx}`;
                                const effectiveOdds = getEffectiveOdds(opp.id, legIdx, leg.odds);
                                const oddsChanged = oddsKey in oddsOverride;
                                const legStake = effLegStakes[legIdx];
                                const legReturn = legStake * effectiveOdds;
                                const isEditingThisOdds = editingOdds === oddsKey;
                                const isEditingThisStake = editingStake === stakeKey;
                                const isAnchorLeg = hasAnchor && anchorStake[opp.id].legIdx === legIdx;
                                const isPlacingThis = isPlacing && placingLeg === oddsKey;

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
                                                setOddsOverride(prev => ({ ...prev, [oddsKey]: val }));
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
                                            onClick={() => setEditingOdds(oddsKey)}
                                            className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-success/50 transition-colors ${oddsChanged ? 'text-success font-medium border-success/30' : 'text-text border-transparent'}`}
                                            title="Click to adjust odds"
                                          >
                                            {effectiveOdds.toFixed(2)}
                                          </span>
                                        )}
                                        {oddsChanged && (
                                          <button
                                            onClick={() => setOddsOverride(prev => { const next = { ...prev }; delete next[oddsKey]; return next; })}
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
                                                setAnchorStake(prev => ({ ...prev, [opp.id]: { legIdx, stake: val } }));
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
                                            onClick={() => setEditingStake(stakeKey)}
                                            className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-success/50 transition-colors ${isAnchorLeg ? 'text-success font-medium border-success/30' : 'text-text border-transparent'}`}
                                            title="Click to set anchor stake"
                                          >
                                            {legStake > 0 ? `${legStake.toFixed(0)} kr` : '-'}
                                          </span>
                                        )}
                                        {isAnchorLeg && (
                                          <button
                                            onClick={() => setAnchorStake(prev => { const next = { ...prev }; delete next[opp.id]; return next; })}
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
                                          onClick={() => handlePlaceLeg(opp, leg, legIdx)}
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
                                  <span className={`font-medium ${hasAnchor ? 'text-success' : 'text-text'}`}>{effTotalStake.toFixed(0)} kr</span>
                                  {hasAnchor && (opp.total_stake ?? 0) > 0 && (
                                    <span className="text-muted2 text-[10px] ml-1">(was {(opp.total_stake ?? 0).toFixed(0)})</span>
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
                                    onClick={() => handlePlaceAll(opp)}
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
                      );
                    })()}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
