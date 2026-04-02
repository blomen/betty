import { useCallback } from 'react';
import type { StreamBookEvent, CandleData, ExpandedSession, VPLevel, TPOLiveProfile, SessionTPOResponse, SessionTPOData, TimeframeSwings, StatisticsEvent } from '@/types/market';

// Level groups: toggling a group toggles all its children
const LEVEL_GROUPS: Record<string, string[]> = {
  vwap: ['vwap'],
  ib: ['ibh', 'ibl'],
  pdh_pdl: ['pdh', 'pdl'],
  tokyo: ['tokyo_h', 'tokyo_l'],
  london: ['london_h', 'london_l'],
  daily_vp: ['d_poc', 'd_vah', 'd_val', 'vp_session'],
  weekly_vp: ['w_poc', 'w_vah', 'w_val', 'vp_weekly'],
  monthly_vp: ['m_poc', 'm_vah', 'm_val', 'vp_monthly'],
  tpo_tokyo:  ['tpo_tky_letters', 'tpo_tky_poc', 'tpo_tky_vah', 'tpo_tky_val'],
  tpo_london: ['tpo_ldn_letters', 'tpo_ldn_poc', 'tpo_ldn_vah', 'tpo_ldn_val'],
  tpo_ny:     ['tpo_ny_letters',  'tpo_ny_poc',  'tpo_ny_vah',  'tpo_ny_val'],
  daily_swing:   ['daily_swing_high', 'daily_swing_low'],
  weekly_swing:  ['weekly_swing_high', 'weekly_swing_low'],
  monthly_swing: ['monthly_swing_high', 'monthly_swing_low'],
  amt:           ['amt_day_type', 'amt_opening', 'amt_rotation', 'amt_aspr', 'amt_migration'],
  macro:         ['macro_regime', 'macro_vix', 'macro_dxy', 'macro_yields', 'macro_cot', 'macro_gex', 'macro_pc'],
  exchange_stats: ['exchange_stats'],
};

interface Props {
  book: StreamBookEvent | null;
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
  hiddenLevels: Set<string>;
  setHiddenLevels: React.Dispatch<React.SetStateAction<Set<string>>>;
  tpo?: TPOLiveProfile | null;
  sessionTPO?: SessionTPOResponse | null;
  statistics?: StatisticsEvent | null;
}

export function BookSnapshot({ session, hiddenLevels, setHiddenLevels, tpo: _tpo, sessionTPO, statistics }: Props) {
  const s = session?.session;
  const profiles = session?.profiles;
  const pricePos = session?.price_position;
  const swingStructure = session?.swing_structure;
  const structure = session?.structure;
  const macro = session?.macro;
  const mlDayType = session?.ml_day_type;
  const mlDayTypeConf = session?.ml_day_type_confidence;

  const toggleGroup = useCallback((group: string) => {
    const keys = LEVEL_GROUPS[group];
    if (!keys) return;
    setHiddenLevels(prev => {
      const next = new Set(prev);
      const allHidden = keys.every(k => next.has(k));
      keys.forEach(k => allHidden ? next.delete(k) : next.add(k));
      return next;
    });
  }, [setHiddenLevels]);

  const toggleAll = useCallback(() => {
    setHiddenLevels(prev => {
      const allKeys = Object.values(LEVEL_GROUPS).flat();
      const allHidden = allKeys.every(k => prev.has(k));
      return new Set(allHidden ? [] : allKeys);
    });
  }, [setHiddenLevels]);

  const toggleCluster = useCallback((groups: string[]) => {
    const keys = groups.flatMap(g => LEVEL_GROUPS[g] ?? []);
    setHiddenLevels(prev => {
      const next = new Set(prev);
      const allHidden = keys.every(k => next.has(k));
      keys.forEach(k => allHidden ? next.delete(k) : next.add(k));
      return next;
    });
  }, [setHiddenLevels]);

  const isGroupHidden = (group: string) => {
    const keys = LEVEL_GROUPS[group];
    return keys ? keys.every(k => hiddenLevels.has(k)) : false;
  };


  return (
    <div className="flex flex-col h-full min-h-0 text-xs font-mono overflow-y-auto">

      {/* Master toggle */}
      <div className="px-2 py-1 border-b border-border">
        <button onClick={toggleAll} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer">Indicators</button>
      </div>

      {/* VWAP */}
      {s?.vwap != null && (
        <Group label="VWAP" hidden={isGroupHidden('vwap')} onToggle={() => toggleGroup('vwap')} section>
          <div className="flex items-baseline justify-between">
            <span className="text-yellow-400 text-sm font-bold">{s.vwap.toFixed(2)}</span>
            {pricePos?.vwap_deviation_sd != null && (
              <span className={`text-[11px] ${
                Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
                Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-amber-400' : 'text-muted2'
              }`}>
                {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)} SD
              </span>
            )}
          </div>
          {s.vwap_1sd_upper != null && s.vwap_1sd_lower != null && (
            <div className="flex flex-col gap-0.5 mt-1">
              <div className="flex justify-between">
                <span className="text-[10px] text-muted2">+1SD {s.vwap_1sd_upper.toFixed(2)}</span>
                <span className="text-[10px] text-muted2">-1SD {s.vwap_1sd_lower.toFixed(2)}</span>
              </div>
              {s.vwap_2sd_upper != null && s.vwap_2sd_lower != null && (
                <div className="flex justify-between">
                  <span className="text-[10px] text-muted2">+2SD {s.vwap_2sd_upper.toFixed(2)}</span>
                  <span className="text-[10px] text-muted2">-2SD {s.vwap_2sd_lower.toFixed(2)}</span>
                </div>
              )}
              {s.vwap_3sd_upper != null && s.vwap_3sd_lower != null && (
                <div className="flex justify-between">
                  <span className="text-[10px] text-muted2">+3SD {s.vwap_3sd_upper.toFixed(2)}</span>
                  <span className="text-[10px] text-muted2">-3SD {s.vwap_3sd_lower.toFixed(2)}</span>
                </div>
              )}
            </div>
          )}
          {/* Price vs VWAP */}
          {pricePos?.vs_vwap && pricePos.vs_vwap !== 'unknown' && (
            <div className="flex justify-between mt-1 pt-1 border-t border-zinc-800/50">
              <span className="text-[10px] text-muted2">Price</span>
              <PosTag value={pricePos.vs_vwap} />
            </div>
          )}
        </Group>
      )}

      {/* Session Levels */}
      {(() => {
        const sessionAllHidden = ['pdh_pdl', 'tokyo', 'london'].every(g => isGroupHidden(g));
        if (sessionAllHidden) return (
          <div className="px-2 py-0.5 border-b border-border">
            <button onClick={() => toggleCluster(['pdh_pdl', 'tokyo', 'london'])} className="text-[10px] text-muted uppercase tracking-wider opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity">Session</button>
          </div>
        );
        return (
          <div className="px-2 py-1 border-b border-border">
            <button onClick={() => toggleCluster(['pdh_pdl', 'tokyo', 'london'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">Session</button>

            {/* PDH/PDL */}
            {(s?.pdh != null || s?.pdl != null) && !isGroupHidden('pdh_pdl') && (
              <div className="mb-1">
                {s?.pdh != null && !hiddenLevels.has('pdh') && <Row label="PDH" value={s.pdh.toFixed(2)} color="text-orange-300" />}
                {s?.pdl != null && !hiddenLevels.has('pdl') && <Row label="PDL" value={s.pdl.toFixed(2)} color="text-orange-300" />}
              </div>
            )}

            {/* Overnight */}
            {(s?.overnight_high != null || s?.overnight_low != null) && (
              <div className="mb-1">
                {s?.overnight_high != null && <Row label="ON High" value={s.overnight_high.toFixed(2)} color="text-zinc-400" />}
                {s?.overnight_low != null && <Row label="ON Low" value={s.overnight_low.toFixed(2)} color="text-zinc-400" />}
              </div>
            )}

            <Group label="Tokyo" hidden={isGroupHidden('tokyo')} onToggle={() => toggleGroup('tokyo')}>
              {s?.tokyo_high != null && <Row label="Tokyo H" value={s.tokyo_high.toFixed(2)} color="text-cyan-300" />}
              {s?.tokyo_low != null && <Row label="Tokyo L" value={s.tokyo_low.toFixed(2)} color="text-cyan-300" />}
            </Group>

            <Group label="London" hidden={isGroupHidden('london')} onToggle={() => toggleGroup('london')}>
              {s?.london_high != null && <Row label="London H" value={s.london_high.toFixed(2)} color="text-emerald-300" />}
              {s?.london_low != null && <Row label="London L" value={s.london_low.toFixed(2)} color="text-emerald-300" />}
            </Group>
          </div>
        );
      })()}

      {/* Volume Profile — multi-timeframe */}
      {(() => {
        const vpAllHidden = ['daily_vp', 'weekly_vp', 'monthly_vp'].every(g => isGroupHidden(g));
        if (vpAllHidden) return (
          <div className="px-2 py-0.5 border-b border-border last:border-b-0">
            <button onClick={() => toggleCluster(['daily_vp', 'weekly_vp', 'monthly_vp'])} className="text-[10px] text-muted uppercase tracking-wider opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity">Volume Profile</button>
          </div>
        );
        return (
          <div className="px-2 py-1 border-b border-border last:border-b-0">
            <button onClick={() => toggleCluster(['daily_vp', 'weekly_vp', 'monthly_vp'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">Volume Profile</button>

            <Group label="Daily" hidden={isGroupHidden('daily_vp')} onToggle={() => toggleGroup('daily_vp')}>
              <VPRow vp={profiles?.session} color="text-purple-400" />
            </Group>

            <Group label="Weekly" hidden={isGroupHidden('weekly_vp')} onToggle={() => toggleGroup('weekly_vp')}>
              <VPRow vp={profiles?.weekly} color="text-pink-400" />
            </Group>

            <Group label="Monthly" hidden={isGroupHidden('monthly_vp')} onToggle={() => toggleGroup('monthly_vp')}>
              <VPRow vp={profiles?.monthly} color="text-yellow-400" />
            </Group>

            {profiles?.developing_poc != null && (
              <div className="flex items-center justify-between">
                <span className="text-muted2 text-[10px]">devPOC</span>
                <span className="flex items-center gap-1">
                  <span className="text-[11px] text-white">{profiles.developing_poc.toFixed(2)}</span>
                  {profiles.developing_poc_direction && profiles.developing_poc_direction !== 'flat' && (
                    <span className={`text-[9px] ${profiles.developing_poc_direction === 'up' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {profiles.developing_poc_direction === 'up' ? '↑' : '↓'}
                    </span>
                  )}
                </span>
              </div>
            )}

            {/* Naked POCs */}
            {profiles?.naked_pocs && profiles.naked_pocs.length > 0 && (
              <div className="mt-1 pt-1 border-t border-zinc-800/50">
                <span className="text-[9px] text-muted2 block mb-0.5">Naked POCs</span>
                {profiles.naked_pocs.slice(0, 5).map((np, i) => (
                  <div key={i} className="flex justify-between text-[10px]">
                    <span className="text-muted2">{np.date}</span>
                    <span className="text-purple-300">{np.price.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Price vs VA */}
            {pricePos?.vs_va && pricePos.vs_va !== 'unknown' && (
              <div className="flex justify-between mt-1 pt-1 border-t border-zinc-800/50">
                <span className="text-[10px] text-muted2">Price vs VA</span>
                <PosTag value={pricePos.vs_va} />
              </div>
            )}
          </div>
        );
      })()}

      {/* TPO Profiles (per-session) */}
      {sessionTPO?.sessions && (() => {
        const tpoAllHidden = ['tpo_tokyo', 'tpo_london', 'tpo_ny'].every(g => isGroupHidden(g));
        if (tpoAllHidden) return (
          <div className="px-2 py-0.5 border-b border-border last:border-b-0">
            <button onClick={() => toggleCluster(['tpo_tokyo', 'tpo_london', 'tpo_ny'])} className="text-[10px] text-muted uppercase tracking-wider opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity">TPO</button>
          </div>
        );
        return (
          <div className="px-2 py-1 border-b border-border last:border-b-0">
            <button onClick={() => toggleCluster(['tpo_tokyo', 'tpo_london', 'tpo_ny'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">TPO</button>

            <div className="space-y-2">
              <SessionTPOCard label="TKY" data={sessionTPO.sessions.tokyo} color="text-cyan-400" borderColor="border-cyan-900/50" hidden={isGroupHidden('tpo_tokyo')} onToggle={() => toggleGroup('tpo_tokyo')} />
              <SessionTPOCard label="LDN" data={sessionTPO.sessions.london} color="text-emerald-400" borderColor="border-emerald-900/50" hidden={isGroupHidden('tpo_london')} onToggle={() => toggleGroup('tpo_london')} />
              <SessionTPOCard label="NY" data={sessionTPO.sessions.ny} color="text-red-400" borderColor="border-red-900/50" hidden={isGroupHidden('tpo_ny')} onToggle={() => toggleGroup('tpo_ny')} />
            </div>

            {/* POC Migration */}
            {(sessionTPO.poc_migration_tokyo_london !== 0 || sessionTPO.poc_migration_london_ny !== 0) && (
              <div className="mt-1.5 pt-1 border-t border-zinc-800/50 grid grid-cols-2 gap-x-2 text-[10px]">
                <div className="flex justify-between">
                  <span className="text-muted2">TKY→LDN</span>
                  <PocMigration ticks={sessionTPO.poc_migration_tokyo_london} />
                </div>
                <div className="flex justify-between">
                  <span className="text-muted2">LDN→NY</span>
                  <PocMigration ticks={sessionTPO.poc_migration_london_ny} />
                </div>
              </div>
            )}
          </div>
        );
      })()}

      {/* DOW / Market Structure */}
      {(() => {
        const structureAllHidden = ['daily_swing', 'weekly_swing', 'monthly_swing'].every(g => isGroupHidden(g));
        if (structureAllHidden) return (
          <div className="px-2 py-0.5 border-b border-border last:border-b-0">
            <button onClick={() => toggleCluster(['daily_swing', 'weekly_swing', 'monthly_swing'])} className="text-[10px] text-muted uppercase tracking-wider opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity">Structure</button>
          </div>
        );
        return (
          <div className="px-2 py-1 border-b border-border last:border-b-0">
            <button onClick={() => toggleCluster(['daily_swing', 'weekly_swing', 'monthly_swing'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">Structure</button>

            {/* Multi-TF Swing Structure */}
            {swingStructure && (
              <>
                <SwingTFRow label="D" tf={swingStructure.daily} hidden={isGroupHidden('daily_swing')} onToggle={() => toggleGroup('daily_swing')} />
                <SwingTFRow label="W" tf={swingStructure.weekly} hidden={isGroupHidden('weekly_swing')} onToggle={() => toggleGroup('weekly_swing')} />
                <SwingTFRow label="M" tf={swingStructure.monthly} hidden={isGroupHidden('monthly_swing')} onToggle={() => toggleGroup('monthly_swing')} />

                {/* Trend Alignment */}
                <div className="flex items-center justify-between mt-1 pt-1 border-t border-zinc-800/50">
                  <span className="text-[10px] text-muted2">Alignment</span>
                  <span className={`text-[11px] font-bold ${
                    swingStructure.trend_alignment > 0.3 ? 'text-emerald-400' :
                    swingStructure.trend_alignment < -0.3 ? 'text-red-400' : 'text-amber-400'
                  }`}>
                    {swingStructure.trend_alignment > 0 ? '+' : ''}{swingStructure.trend_alignment.toFixed(2)}
                    {swingStructure.trend_alignment > 0.3 ? ' ↑' : swingStructure.trend_alignment < -0.3 ? ' ↓' : ' ↔'}
                  </span>
                </div>
              </>
            )}

            {/* 1m live structure */}
            {structure && (
              <div className="flex items-center justify-between mt-1 pt-1 border-t border-zinc-800/50">
                <span className="text-[10px] text-muted2">Live (1m)</span>
                <span className={`text-[10px] font-bold ${trendColor(structure.structure)}`}>
                  {structure.structure === 'uptrend' ? '▲ UP' : structure.structure === 'downtrend' ? '▼ DN' : '◆ RANGE'}
                </span>
              </div>
            )}

            {/* Structure swing points HH/HL/LH/LL */}
            {structure && (structure.last_hh != null || structure.last_hl != null || structure.last_lh != null || structure.last_ll != null) && (
              <div className="grid grid-cols-2 gap-x-2 mt-1 pt-1 border-t border-zinc-800/50 text-[10px]">
                {structure.last_hh != null && (
                  <div className="flex justify-between"><span className="text-muted2">HH</span><span className="text-emerald-400">{structure.last_hh.toFixed(2)}</span></div>
                )}
                {structure.last_hl != null && (
                  <div className="flex justify-between"><span className="text-muted2">HL</span><span className="text-emerald-400">{structure.last_hl.toFixed(2)}</span></div>
                )}
                {structure.last_lh != null && (
                  <div className="flex justify-between"><span className="text-muted2">LH</span><span className="text-red-400">{structure.last_lh.toFixed(2)}</span></div>
                )}
                {structure.last_ll != null && (
                  <div className="flex justify-between"><span className="text-muted2">LL</span><span className="text-red-400">{structure.last_ll.toFixed(2)}</span></div>
                )}
              </div>
            )}

            {/* Poor highs/lows + single prints (NN uses these) */}
            {(s?.poor_high || s?.poor_low || (s?.single_prints && s.single_prints.length > 0)) && (
              <div className="flex flex-wrap gap-x-2 mt-1 pt-1 border-t border-zinc-800/50 text-[10px]">
                {s?.poor_high && <span className="text-red-400/80">poor high</span>}
                {s?.poor_low && <span className="text-emerald-400/80">poor low</span>}
                {s?.single_prints && s.single_prints.length > 0 && (
                  <span className="text-amber-400/80">{s.single_prints.length} single prints</span>
                )}
              </div>
            )}
          </div>
        );
      })()}

      {/* AMT — Dalton day type, opening type, rotation, value migration */}
      {(() => {
        const amtHidden = isGroupHidden('amt');
        if (amtHidden) return (
          <div className="px-2 py-0.5 border-b border-border last:border-b-0">
            <button onClick={() => toggleGroup('amt')} className="text-[10px] text-muted uppercase tracking-wider opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity">AMT</button>
          </div>
        );
        return (
          <div className="px-2 py-1 border-b border-border last:border-b-0">
            <button onClick={() => toggleGroup('amt')} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">AMT</button>

            {/* Day type (ML or session) */}
            {(mlDayType || s?.market_type) && (
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted2">Day Type</span>
                <span className="flex items-center gap-1">
                  <span className="text-amber-300 font-bold">{mlDayType ?? s?.market_type}</span>
                  {mlDayTypeConf != null && (
                    <span className="text-muted2">({mlDayTypeConf.toFixed(0)}%)</span>
                  )}
                </span>
              </div>
            )}

            {/* Opening type */}
            {s?.opening_type && (
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted2">Opening</span>
                <span className="text-cyan-300 font-bold">{s.opening_type}</span>
              </div>
            )}

            {/* Distribution type */}
            {s?.distribution_type && (
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted2">Distribution</span>
                <span className="text-text">{s.distribution_type}</span>
              </div>
            )}

            {/* Rotation factor */}
            {s?.rotation_factor != null && (
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted2">Rotation</span>
                <span className={`font-bold ${
                  s.rotation_factor > 4 ? 'text-emerald-400' :
                  s.rotation_factor < -4 ? 'text-red-400' : 'text-muted2'
                }`}>{s.rotation_factor > 0 ? '+' : ''}{s.rotation_factor}</span>
              </div>
            )}

            {/* ASPR */}
            {s?.aspr != null && (
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted2">ASPR</span>
                <span className="text-text">
                  {s.aspr.toFixed(1)}
                  {s.aspr_percentile != null && <span className="text-muted2 ml-1">p{(s.aspr_percentile * 100).toFixed(0)}</span>}
                </span>
              </div>
            )}

            {/* Value migration */}
            {s?.value_migration && (
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted2">VA Migration</span>
                <span className={`font-bold ${
                  s.value_migration === 'up' ? 'text-emerald-400' :
                  s.value_migration === 'down' ? 'text-red-400' : 'text-amber-400'
                }`}>
                  {s.value_migration === 'up' ? '↑ Up' : s.value_migration === 'down' ? '↓ Down' : '↔ Neutral'}
                </span>
              </div>
            )}

            {/* Delta */}
            {s?.total_delta != null && (
              <div className="flex items-center justify-between text-[10px] mt-1 pt-1 border-t border-zinc-800/50">
                <span className="text-muted2">Delta</span>
                <span className="flex items-center gap-1">
                  <span className={s.total_delta > 0 ? 'text-emerald-400' : s.total_delta < 0 ? 'text-red-400' : 'text-muted2'}>
                    {s.total_delta > 0 ? '+' : ''}{s.total_delta.toLocaleString()}
                  </span>
                  {s.delta_divergence && <span className="text-amber-400 text-[9px]">DIV</span>}
                </span>
              </div>
            )}
          </div>
        );
      })()}

      {/* Macro — VIX, DXY, yields, regime, COT, news */}
      {(() => {
        const macroHidden = isGroupHidden('macro');
        if (macroHidden) return (
          <div className="px-2 py-0.5 border-b border-border last:border-b-0">
            <button onClick={() => toggleGroup('macro')} className="text-[10px] text-muted uppercase tracking-wider opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity">Macro</button>
          </div>
        );
        return (
          <div className="px-2 py-1 border-b border-border last:border-b-0">
            <button onClick={() => toggleGroup('macro')} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">Macro</button>

            {macro && (
              <>
                {/* Regime */}
                <div className="flex items-center justify-between text-[10px]">
                  <span className="text-muted2">Regime</span>
                  <span className={`font-bold ${
                    macro.regime === 'risk_on' ? 'text-emerald-400' :
                    macro.regime === 'risk_off' ? 'text-red-400' : 'text-amber-400'
                  }`}>
                    {macro.regime}
                    {macro.regime_score != null && <span className="text-muted2 ml-1 font-normal">({(macro.regime_score * 100).toFixed(0)})</span>}
                  </span>
                </div>

                {/* VIX */}
                {macro.vix != null && (
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-muted2">VIX</span>
                    <span className="flex items-center gap-1">
                      <span className={macro.vix > 25 ? 'text-red-400' : macro.vix > 18 ? 'text-amber-400' : 'text-emerald-400'}>
                        {macro.vix.toFixed(1)}
                      </span>
                      {macro.vix_change_pct != null && (
                        <span className={`text-[9px] ${macro.vix_change_pct > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                          {macro.vix_change_pct > 0 ? '+' : ''}{macro.vix_change_pct.toFixed(1)}%
                        </span>
                      )}
                    </span>
                  </div>
                )}

                {/* DXY */}
                {macro.dxy != null && (
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-muted2">DXY</span>
                    <span className="flex items-center gap-1">
                      <span className="text-text">{macro.dxy.toFixed(2)}</span>
                      {macro.dxy_change_pct != null && (
                        <span className={`text-[9px] ${macro.dxy_change_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {macro.dxy_change_pct > 0 ? '+' : ''}{macro.dxy_change_pct.toFixed(2)}%
                        </span>
                      )}
                    </span>
                  </div>
                )}

                {/* Yields */}
                {macro.us10y != null && (
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-muted2">10Y</span>
                    <span className="flex items-center gap-1">
                      <span className="text-text">{macro.us10y.toFixed(2)}%</span>
                      {macro.us10y_change_bps != null && (
                        <span className={`text-[9px] ${macro.us10y_change_bps > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                          {macro.us10y_change_bps > 0 ? '+' : ''}{macro.us10y_change_bps.toFixed(1)}bp
                        </span>
                      )}
                    </span>
                  </div>
                )}

                {macro.us2y != null && (
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-muted2">2Y</span>
                    <span className="text-text">{macro.us2y.toFixed(2)}%</span>
                  </div>
                )}

                {macro.yield_curve_spread != null && (
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-muted2">10Y-2Y</span>
                    <span className={macro.yield_curve_spread > 0 ? 'text-emerald-400' : 'text-red-400'}>
                      {macro.yield_curve_spread > 0 ? '+' : ''}{(macro.yield_curve_spread * 100).toFixed(0)}bp
                    </span>
                  </div>
                )}

                {/* COT / Positioning */}
                {(macro.cot_net_position != null || macro.cot_change_1w != null) && (
                  <div className="mt-1 pt-1 border-t border-zinc-800/50">
                    {macro.cot_net_position != null && (
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-muted2">COT Net</span>
                        <span className={macro.cot_net_position > 0 ? 'text-emerald-400' : 'text-red-400'}>
                          {macro.cot_net_position > 0 ? '+' : ''}{(macro.cot_net_position / 1000).toFixed(1)}k
                        </span>
                      </div>
                    )}
                    {macro.cot_change_1w != null && (
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-muted2">COT Chg</span>
                        <span className={macro.cot_change_1w > 0 ? 'text-emerald-400' : 'text-red-400'}>
                          {macro.cot_change_1w > 0 ? '+' : ''}{(macro.cot_change_1w / 1000).toFixed(1)}k
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {/* GEX / P/C */}
                {(macro.gex != null || macro.put_call_ratio != null) && (
                  <div className="mt-1 pt-1 border-t border-zinc-800/50">
                    {macro.gex != null && (
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-muted2">GEX</span>
                        <span className={macro.gex > 0 ? 'text-emerald-400' : 'text-red-400'}>
                          {macro.gex > 0 ? '+' : ''}{(macro.gex / 1e9).toFixed(1)}B
                        </span>
                      </div>
                    )}
                    {macro.put_call_ratio != null && (
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-muted2">P/C</span>
                        <span className={macro.put_call_ratio > 1 ? 'text-red-400' : 'text-emerald-400'}>
                          {macro.put_call_ratio.toFixed(2)}
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {/* News proximity (NN feature: news_proximity + news_importance) */}
                {macro.news_importance != null && macro.news_importance > 0 && (
                  <div className="mt-1 pt-1 border-t border-zinc-800/50">
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="text-muted2">News</span>
                      <span className="flex items-center gap-1">
                        <span className={macro.news_importance >= 3 ? 'text-red-400 font-bold' : macro.news_importance >= 2 ? 'text-amber-400' : 'text-muted2'}>
                          {'!'.repeat(macro.news_importance)}
                        </span>
                        {macro.news_proximity != null && (
                          <span className="text-muted2">
                            {Math.round((1 - macro.news_proximity) * 120)}m
                          </span>
                        )}
                      </span>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        );
      })()}

      {/* Exchange Statistics */}
      {statistics && !hiddenLevels.has('exchange_stats') && (
        <div className="flex gap-3 text-[10px] text-zinc-400 px-2 py-1 border-t border-zinc-800">
          {statistics.open_interest != null && (
            <span>OI: <span className="text-cyan-400">{(statistics.open_interest / 1000).toFixed(0)}k</span></span>
          )}
          {statistics.settlement_price != null && (
            <span>Sttl: <span className="text-amber-400">{statistics.settlement_price.toFixed(2)}</span></span>
          )}
          {statistics.cleared_volume != null && (
            <span>ClrVol: <span className="text-zinc-300">{(statistics.cleared_volume / 1000).toFixed(0)}k</span></span>
          )}
          {statistics.block_volume != null && (
            <span>BlkVol: <span className="text-zinc-300">{(statistics.block_volume / 1000).toFixed(0)}k</span></span>
          )}
        </div>
      )}

    </div>
  );
}

/** Per-session TPO card with structured rows */
function SessionTPOCard({ label, data, color, borderColor, hidden, onToggle }: {
  label: string; data: SessionTPOData | null; color: string; borderColor: string; hidden: boolean; onToggle: () => void;
}) {
  if (!data) return null;
  if (hidden) return (
    <button onClick={onToggle} className={`text-[10px] ${color} opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity`}>{label}</button>
  );
  const arrow = data.opening_direction === 'up' ? '↑' : data.opening_direction === 'down' ? '↓' : '↔';
  const ibTicks = data.ib_valid ? ((data.ib_high - data.ib_low) / 0.25).toFixed(0) : '—';
  const rf = data.rotation_factor ?? 0;
  const rfColor = rf > 4 ? 'text-emerald-400' : rf < -4 ? 'text-red-400' : 'text-muted2';
  const shapeColor = data.shape === 'p-shape' ? 'text-emerald-400'
    : data.shape === 'b-shape' ? 'text-red-400'
    : data.shape === 'B-shape' ? 'text-amber-400'
    : data.shape === 'd-shape' ? 'text-purple-400' : 'text-muted2';

  return (
    <div className={`border-l-2 ${borderColor} pl-1.5 text-[10px] leading-relaxed min-w-0`}>
      {/* Header: session name + shape + opening + RF */}
      <div className="flex items-center justify-between">
        <button onClick={onToggle} className={`${color} font-bold cursor-pointer hover:opacity-70 transition-opacity`}>{label}</button>
        <span className="flex items-center gap-1">
          <span className={shapeColor}>{data.shape}</span>
          <span className="text-muted2">{data.opening_type}{arrow}</span>
          <span className={rfColor}>{rf > 0 ? '+' : ''}{rf}</span>
        </span>
      </div>
      {/* Value Area — stacked rows */}
      <div className="flex justify-between">
        <span className="text-muted2">POC</span><span className={color}>{data.poc.toFixed(0)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-muted2">VAH</span><span className="text-text">{data.vah.toFixed(0)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-muted2">VAL</span><span className="text-text">{data.val.toFixed(0)}</span>
      </div>
      {/* IB levels */}
      {data.ib_valid && (
        <>
          <div className="flex justify-between">
            <span className="text-muted2">IBH</span><span className="text-amber-400">{data.ib_high.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted2">IBL</span><span className="text-amber-400">{data.ib_low.toFixed(2)}</span>
          </div>
        </>
      )}
      <div className="flex justify-between">
        <span className="text-muted2">IB Range</span>
        <span className="text-muted2">{ibTicks}t</span>
      </div>
      {/* Anomalies */}
      {(data.poor_high || data.poor_low || data.upper_excess > 0 || data.lower_excess > 0) && (
        <div className="flex flex-wrap gap-x-2 text-muted">
          {data.poor_high && <span className="text-red-400/70">poor high</span>}
          {data.poor_low && <span className="text-emerald-400/70">poor low</span>}
          {data.upper_excess > 0 && <span className="text-red-400/70">xs↑{data.upper_excess}</span>}
          {data.lower_excess > 0 && <span className="text-emerald-400/70">xs↓{data.lower_excess}</span>}
        </div>
      )}
    </div>
  );
}

function PocMigration({ ticks }: { ticks: number }) {
  const color = ticks > 0 ? 'text-emerald-400' : ticks < 0 ? 'text-red-400' : 'text-muted2';
  return <span className={color}>{ticks > 0 ? '+' : ''}{ticks.toFixed(0)}t</span>;
}

/** Position tag — above/below/inside */
function PosTag({ value }: { value: string }) {
  const color = value === 'above' ? 'text-emerald-400' : value === 'below' ? 'text-red-400' : 'text-amber-400';
  return <span className={`text-[10px] font-bold ${color}`}>{value}</span>;
}

// --- UI building blocks ---


/** Unified group component — click title to toggle. `section` adds px/border for top-level sections. */
function Group({ label, hidden, onToggle, section, children }: {
  label: string; hidden: boolean; onToggle: () => void; section?: boolean; children: React.ReactNode;
}) {
  if (section) {
    if (hidden) return (
      <div className="px-2 py-0.5 border-b border-border">
        <button onClick={onToggle} className="text-[10px] text-muted uppercase tracking-wider opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity">{label}</button>
      </div>
    );
    return (
      <div className="px-2 py-1 border-b border-border">
        <button onClick={onToggle} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">{label}</button>
        {children}
      </div>
    );
  }
  if (hidden) return (
    <button onClick={onToggle} className="text-[10px] text-muted2 font-bold opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity block mb-0.5">{label}</button>
  );
  return (
    <div className="mb-1">
      <button onClick={onToggle} className="text-[10px] text-muted2 font-bold hover:text-text transition-colors cursor-pointer block">{label}</button>
      {children}
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted2 text-[10px]">{label}</span>
      <span className={`text-[11px] ${color ?? 'text-text'}`}>{value}</span>
    </div>
  );
}

function VPRow({ vp, color }: { vp?: VPLevel | null; color: string }) {
  if (!vp || vp.poc == null) return null;
  return (
    <div className="grid grid-cols-3 gap-x-1 text-[10px]">
      <span className="text-muted2">VAH <span className="text-text">{vp.vah.toFixed(0)}</span></span>
      <span className="text-muted2">POC <span className={color}>{vp.poc.toFixed(0)}</span></span>
      <span className="text-muted2">VAL <span className="text-text">{vp.val.toFixed(0)}</span></span>
    </div>
  );
}

function trendColor(trend: string): string {
  if (trend === 'uptrend' || trend === 'reversing_up') return 'text-emerald-400';
  if (trend === 'downtrend' || trend === 'reversing_down') return 'text-red-400';
  return 'text-amber-400';
}

function trendIcon(trend: string): string {
  if (trend === 'uptrend') return '▲';
  if (trend === 'reversing_up') return '△';
  if (trend === 'downtrend') return '▼';
  if (trend === 'reversing_down') return '▽';
  return '◆';
}

function SwingTFRow({ label, tf, hidden, onToggle }: {
  label: string;
  tf: TimeframeSwings;
  hidden: boolean;
  onToggle: () => void;
}) {
  if (hidden) return (
    <button onClick={onToggle} className="text-[10px] text-muted2 font-bold opacity-40 line-through cursor-pointer hover:opacity-70 transition-opacity block mb-0.5">
      {label} <span className={trendColor(tf.structure)}>{trendIcon(tf.structure)}</span>
    </button>
  );

  // Latest swing high/low — arrays are newest-first from backend
  const latestHigh = tf.swing_highs.length > 0 ? tf.swing_highs[0].price : null;
  const latestLow = tf.swing_lows.length > 0 ? tf.swing_lows[0].price : null;

  // HH/LH and HL/LL classification (compare latest vs prior swing)
  const isHH = tf.swing_highs.length >= 2 ? tf.swing_highs[0].price > tf.swing_highs[1].price : null;
  const isHL = tf.swing_lows.length >= 2 ? tf.swing_lows[0].price > tf.swing_lows[1].price : null;

  return (
    <div className="mb-1.5">
      <button onClick={onToggle} className="text-[10px] text-muted2 font-bold hover:text-text transition-colors cursor-pointer block">
        {label} <span className={trendColor(tf.structure)}>{trendIcon(tf.structure)} {tf.structure.replace('_', ' ')}</span>
      </button>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-muted2">SH {isHH !== null && <span className={isHH ? 'text-emerald-400' : 'text-red-400'}>{isHH ? 'HH' : 'LH'}</span>}</span>
        <span className="text-text">{latestHigh?.toFixed(2) ?? '—'}</span>
      </div>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-muted2">SL {isHL !== null && <span className={isHL ? 'text-emerald-400' : 'text-red-400'}>{isHL ? 'HL' : 'LL'}</span>}</span>
        <span className="text-text">{latestLow?.toFixed(2) ?? '—'}</span>
      </div>
      {/* BOS / CHoCH events */}
      {(tf.bos_active || tf.choch_active) && (
        <div className="flex gap-x-2 text-[9px] mt-0.5">
          {tf.bos_active && tf.last_bos && (
            <span className={tf.last_bos.event_type.includes('bullish') ? 'text-emerald-400' : 'text-red-400'}>
              BOS {tf.last_bos.event_type.includes('bullish') ? '↑' : '↓'} {tf.last_bos.price.toFixed(0)}
            </span>
          )}
          {tf.choch_active && tf.last_choch && (
            <span className={tf.last_choch.event_type.includes('bullish') ? 'text-emerald-400' : 'text-red-400'}>
              CHoCH {tf.last_choch.event_type.includes('bullish') ? '↑' : '↓'} {tf.last_choch.price.toFixed(0)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
