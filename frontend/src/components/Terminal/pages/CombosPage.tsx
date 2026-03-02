import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import type { CombosResponse, ComboRecommendation, ComboProviderResult } from '@/types';
import { formatProviderName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { FilterBar, MultiSelectDropdown } from '../FilterBar';
import { TAB_COLORS } from '../TabBar';

const ACCENT = TAB_COLORS.combos || '#22D3EE';

export function CombosPage() {
  const [data, setData] = useState<CombosResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.getCombos({ min_edge_pct: 2.0 });
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load combos');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  // Flatten all combos across providers for display
  const allCombos = useMemo(() => {
    if (!data?.providers) return [];
    const combos: (ComboRecommendation & { _config: ComboProviderResult['config'] })[] = [];
    for (const prov of data.providers) {
      if (selectedProviders.size > 0 && !selectedProviders.has(prov.provider)) continue;
      for (const combo of prov.combos) {
        combos.push({ ...combo, _config: prov.config });
      }
    }
    return combos;
  }, [data, selectedProviders]);

  // All provider names for filter
  const providerOptions = useMemo(() => {
    if (!data?.providers) return [];
    return data.providers.map(p => p.provider);
  }, [data]);

  // Boost table for the expanded combo's provider
  const getBoostTable = (providerName: string) => {
    const prov = data?.providers.find(p => p.provider === providerName);
    return prov?.config.boost_table || {};
  };

  if (isLoading && !data) {
    return (
      <div className="flex items-center justify-center h-64 text-muted">
        <span className="animate-pulse">Loading combo recommendations...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400">
        {error}
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <FilterBar>
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <span className="text-xs font-mono text-muted whitespace-nowrap">
            {allCombos.length} combos
            {data?.value_bets_scanned ? ` from ${data.value_bets_scanned} value bets` : ''}
          </span>
          {providerOptions.length > 0 && (
            <MultiSelectDropdown
              label="Provider"
              options={providerOptions}
              selected={selectedProviders}
              onToggle={(v) => setSelectedProviders(prev => {
                const next = new Set(prev);
                next.has(v) ? next.delete(v) : next.add(v);
                return next;
              })}
              onClear={() => setSelectedProviders(new Set())}
              format={formatProviderName}
              accentColor={ACCENT}
            />
          )}
          <button
            onClick={fetchData}
            disabled={isLoading}
            className="text-xs font-mono text-muted hover:text-text transition-colors ml-auto"
          >
            {isLoading ? '...' : 'Refresh'}
          </button>
        </div>
      </FilterBar>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {allCombos.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-muted text-sm">
            {data?.value_bets_scanned === 0
              ? 'No value bets found — run extraction first'
              : 'No combo boost providers configured or no eligible legs'}
          </div>
        ) : (
          <table className="sq w-full">
            <thead>
              <tr>
                <th className="text-left pl-3">Provider</th>
                <th className="text-center">Legs</th>
                <th className="text-right">Combined</th>
                <th className="text-right">Boost</th>
                <th className="text-right">Eff. Odds</th>
                <th className="text-right">Edge %</th>
                <th className="text-right">Win %</th>
                <th className="text-right">EV/unit</th>
                <th className="text-right pr-3">Stake</th>
              </tr>
            </thead>
            <tbody>
              {allCombos.map((combo, idx) => {
                const isExpanded = expandedIdx === idx;
                const isStakeable = combo.recommended_stake !== null;

                return (
                  <ComboRow
                    key={`${combo.provider}-${combo.num_legs}`}
                    combo={combo}
                    isExpanded={isExpanded}
                    isStakeable={isStakeable}
                    boostTable={getBoostTable(combo.provider)}
                    onToggle={() => setExpandedIdx(isExpanded ? null : idx)}
                  />
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function ComboRow({
  combo,
  isExpanded,
  isStakeable,
  boostTable,
  onToggle,
}: {
  combo: ComboRecommendation & { _config: ComboProviderResult['config'] };
  isExpanded: boolean;
  isStakeable: boolean;
  boostTable: Record<string, number>;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer transition-colors hover:bg-panel2 ${isExpanded ? 'bg-panel2' : ''}`}
      >
        <td className="pl-3 font-mono text-xs">
          {formatProviderName(combo.provider)}
        </td>
        <td className="text-center">
          <span
            className="inline-flex items-center justify-center px-2 py-0.5 rounded-full text-xs font-mono font-medium"
            style={{ backgroundColor: ACCENT + '22', color: ACCENT }}
          >
            {combo.num_legs}
          </span>
        </td>
        <td className="text-right font-mono text-xs">
          {combo.combined_offered_odds.toFixed(1)}
        </td>
        <td className="text-right font-mono text-xs font-medium" style={{ color: ACCENT }}>
          +{combo.boost_pct}%
        </td>
        <td className="text-right font-mono text-xs">
          {combo.effective_odds.toFixed(1)}
        </td>
        <td className="text-right font-mono text-xs font-medium text-green-400">
          +{combo.edge_pct.toFixed(1)}%
        </td>
        <td className="text-right font-mono text-xs text-muted">
          {(combo.win_probability * 100).toFixed(combo.win_probability >= 0.01 ? 1 : 2)}%
        </td>
        <td className="text-right font-mono text-xs">
          <span className={combo.ev_per_unit > 0 ? 'text-green-400' : 'text-red-400'}>
            {combo.ev_per_unit > 0 ? '+' : ''}{(combo.ev_per_unit * 100).toFixed(1)}%
          </span>
        </td>
        <td className="text-right pr-3 font-mono text-xs">
          {isStakeable ? (
            <span className="text-text">{combo.recommended_stake} kr</span>
          ) : (
            <span className="text-muted text-[10px]" title={combo.skip_reason || ''}>
              —
            </span>
          )}
        </td>
      </tr>

      {/* Expanded: show legs + boost table */}
      {isExpanded && (
        <tr>
          <td colSpan={9} className="p-0">
            <div className="bg-panel border-t border-b border-border px-4 py-3">
              {/* Boost table mini-bar */}
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <span className="text-[10px] text-muted uppercase tracking-wider mr-1">Boost table:</span>
                {Object.entries(boostTable)
                  .sort(([a], [b]) => Number(a) - Number(b))
                  .map(([legs, pct]) => (
                    <span
                      key={legs}
                      className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                        Number(legs) === combo.num_legs
                          ? 'font-bold'
                          : 'text-muted'
                      }`}
                      style={Number(legs) === combo.num_legs ? {
                        backgroundColor: ACCENT + '33',
                        color: ACCENT,
                      } : undefined}
                    >
                      {legs}→+{pct}%
                    </span>
                  ))
                }
              </div>

              {/* Legs table */}
              <table className="sq w-full">
                <thead>
                  <tr className="text-[10px] text-muted uppercase tracking-wider">
                    <th className="text-left pl-2">#</th>
                    <th className="text-left">Event</th>
                    <th className="text-left">Market</th>
                    <th className="text-left">Pick</th>
                    <th className="text-right">Odds</th>
                    <th className="text-right">Fair</th>
                    <th className="text-right pr-2">Edge</th>
                  </tr>
                </thead>
                <tbody>
                  {combo.legs.map((leg, i) => (
                    <tr key={leg.event_id + leg.market + leg.outcome} className="hover:bg-panel2">
                      <td className="pl-2 text-[10px] text-muted">{i + 1}</td>
                      <td className="text-xs font-mono">
                        {leg.home_team && leg.away_team
                          ? `${leg.home_team} v ${leg.away_team}`
                          : leg.event_id.split(':').slice(1, -1).join(' v ')}
                        {leg.sport && (
                          <span className="text-[10px] text-muted ml-1">
                            ({leg.sport})
                          </span>
                        )}
                      </td>
                      <td className="text-xs font-mono text-muted">
                        {leg.market}
                        {leg.point !== null && leg.point !== undefined && ` ${leg.point}`}
                      </td>
                      <td className="text-xs font-mono">{leg.outcome}</td>
                      <td className="text-right text-xs font-mono">{leg.provider_odds.toFixed(2)}</td>
                      <td className="text-right text-xs font-mono text-muted">{leg.fair_odds.toFixed(2)}</td>
                      <td className="text-right pr-2 text-xs font-mono text-green-400">
                        +{leg.edge_pct.toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Summary */}
              <div className="flex items-center gap-4 mt-3 text-[10px] text-muted">
                <span>
                  Combined: <span className="text-text">{combo.combined_offered_odds.toFixed(2)}</span>
                  {' → '}
                  Boosted: <span style={{ color: ACCENT }}>{combo.effective_odds.toFixed(2)}</span>
                  {' (+'}{combo.boost_pct}% on profit)
                </span>
                <span>
                  Fair: <span className="text-text">{combo.combined_fair_odds.toFixed(2)}</span>
                </span>
                <span>
                  Edge: <span className="text-green-400">+{combo.edge_pct.toFixed(1)}%</span>
                </span>
                {combo.skip_reason && (
                  <span className="text-yellow-500">
                    Skip: {combo.skip_reason}
                  </span>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
