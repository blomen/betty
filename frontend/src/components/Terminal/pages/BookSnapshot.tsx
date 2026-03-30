import { useCallback } from 'react';
import type { StreamBookEvent, CandleData, ExpandedSession, VPLevel, TPOLiveProfile, SessionTPOResponse, SessionTPOData, TimeframeSwings } from '@/types/market';

// Level groups: toggling a group toggles all its children
const LEVEL_GROUPS: Record<string, string[]> = {
  vwap: ['vwap'],
  ib: ['ibh', 'ibl'],
  tokyo: ['tokyo_h', 'tokyo_l'],
  london: ['london_h', 'london_l'],
  daily_vp: ['d_poc', 'd_vah', 'd_val', 'vp_session'],
  weekly_vp: ['w_poc', 'w_vah', 'w_val', 'vp_weekly'],
  monthly_vp: ['m_poc', 'm_vah', 'm_val', 'vp_monthly'],
  tpo_tokyo:  ['tpo_tky_letters', 'tpo_tky_poc', 'tpo_tky_vah', 'tpo_tky_val', 'tpo_tky_ibh', 'tpo_tky_ibl'],
  tpo_london: ['tpo_ldn_letters', 'tpo_ldn_poc', 'tpo_ldn_vah', 'tpo_ldn_val', 'tpo_ldn_ibh', 'tpo_ldn_ibl'],
  tpo_ny:     ['tpo_ny_letters',  'tpo_ny_poc',  'tpo_ny_vah',  'tpo_ny_val',  'tpo_ny_ibh',  'tpo_ny_ibl'],
  daily_swing:   ['daily_swing_high', 'daily_swing_low'],
  weekly_swing:  ['weekly_swing_high', 'weekly_swing_low'],
  monthly_swing: ['monthly_swing_high', 'monthly_swing_low'],
};

interface Props {
  book: StreamBookEvent | null;
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
  hiddenLevels: Set<string>;
  setHiddenLevels: React.Dispatch<React.SetStateAction<Set<string>>>;
  tpo?: TPOLiveProfile | null;
  sessionTPO?: SessionTPOResponse | null;
}

export function BookSnapshot({ session, hiddenLevels, setHiddenLevels, tpo, sessionTPO }: Props) {
  const s = session?.session;
  const profiles = session?.profiles;
  const pricePos = session?.price_position;
  const swingStructure = session?.swing_structure;
  const structure = session?.structure;

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
        <button onClick={toggleAll} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer">Levels</button>
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
            </div>
          )}
        </Group>
      )}

      {/* Session Levels */}
      <div className="px-2 py-1 border-b border-border">
        <button onClick={() => toggleCluster(['tokyo', 'london'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">Session</button>

        <Group label="Tokyo" hidden={isGroupHidden('tokyo')} onToggle={() => toggleGroup('tokyo')}>
          {s?.tokyo_high != null && <Row label="Tokyo H" value={s.tokyo_high.toFixed(2)} color="text-cyan-300" />}
          {s?.tokyo_low != null && <Row label="Tokyo L" value={s.tokyo_low.toFixed(2)} color="text-cyan-300" />}
        </Group>

        <Group label="London" hidden={isGroupHidden('london')} onToggle={() => toggleGroup('london')}>
          {s?.london_high != null && <Row label="London H" value={s.london_high.toFixed(2)} color="text-emerald-300" />}
          {s?.london_low != null && <Row label="London L" value={s.london_low.toFixed(2)} color="text-emerald-300" />}
        </Group>
      </div>

      {/* Volume Profile — multi-timeframe */}
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
          <Row label="devPOC" value={profiles.developing_poc.toFixed(2)} color="text-white" />
        )}
      </div>

      {/* TPO Profiles (per-session) */}
      {sessionTPO?.sessions && (
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
      )}

      {/* DOW / Market Structure */}
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
      </div>

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
      {/* IB row */}
      <div className="flex justify-between">
        <span className="text-muted2">IBH</span>
        <span className="text-amber-400">{data.ib_valid ? data.ib_high.toFixed(0) : '—'}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-muted2">IBL</span>
        <span className="text-amber-400">{data.ib_valid ? data.ib_low.toFixed(0) : '—'}</span>
      </div>
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

// --- UI building blocks ---


/** Unified group component — click title to toggle. `section` adds px/border for top-level sections. */
function Group({ label, hidden, onToggle, section, children }: {
  label: string; hidden: boolean; onToggle: () => void; section?: boolean; children: React.ReactNode;
}) {
  if (section) {
    return (
      <div className={`px-2 py-1 border-b border-border ${hidden ? 'opacity-40' : ''}`}>
        <button onClick={onToggle} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">{label}</button>
        {children}
      </div>
    );
  }
  return (
    <div className={`mb-1 ${hidden ? 'opacity-40' : ''}`}>
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
  if (trend === 'uptrend') return 'text-emerald-400';
  if (trend === 'downtrend') return 'text-red-400';
  return 'text-amber-400';
}

function trendIcon(trend: string): string {
  if (trend === 'uptrend') return '▲';
  if (trend === 'downtrend') return '▼';
  return '◆';
}

function SwingTFRow({ label, tf, hidden, onToggle }: {
  label: string;
  tf: TimeframeSwings;
  hidden: boolean;
  onToggle: () => void;
}) {
  const latestHigh = tf.prior_high;
  const latestLow = tf.prior_low;
  return (
    <div className={`mb-1 ${hidden ? 'opacity-40' : ''}`}>
      <button onClick={onToggle} className="text-[10px] text-muted2 font-bold hover:text-text transition-colors cursor-pointer block">
        {label} <span className={trendColor(tf.structure)}>{trendIcon(tf.structure)}</span>
      </button>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-muted2">SH</span>
        <span className="text-text">{latestHigh?.toFixed(2) ?? '—'}</span>
      </div>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-muted2">SL</span>
        <span className="text-text">{latestLow?.toFixed(2) ?? '—'}</span>
      </div>
    </div>
  );
}

function Placeholder({ text }: { text: string }) {
  return <div className="text-muted2 text-center py-2 text-[10px]">{text}</div>;
}
