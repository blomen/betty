import { useCallback } from 'react';
import type { StreamBookEvent, CandleData, ExpandedSession, VPLevel, TPOLiveProfile, SessionTPOResponse, SessionTPOData } from '@/types/market';

// Level groups: toggling a group toggles all its children
const LEVEL_GROUPS: Record<string, string[]> = {
  vwap: ['vwap'],
  ib: ['ibh', 'ibl'],
  pd: ['pdh', 'pdl'],
  tokyo: ['tokyo_h', 'tokyo_l'],
  london: ['london_h', 'london_l'],
  daily_vp: ['d_poc', 'd_vah', 'd_val', 'vp_session'],
  weekly_vp: ['w_poc', 'w_vah', 'w_val', 'vp_weekly'],
  monthly_vp: ['m_poc', 'm_vah', 'm_val', 'vp_monthly'],
  tpo_tokyo:  ['tpo_tky_letters', 'tpo_tky_poc', 'tpo_tky_vah', 'tpo_tky_val'],
  tpo_london: ['tpo_ldn_letters', 'tpo_ldn_poc', 'tpo_ldn_vah', 'tpo_ldn_val'],
  tpo_ny:     ['tpo_ny_letters',  'tpo_ny_poc',  'tpo_ny_vah',  'tpo_ny_val'],
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
        <button onClick={() => toggleCluster(['pd', 'tokyo', 'london'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-1 block">Session</button>

        <Group label="PD" hidden={isGroupHidden('pd')} onToggle={() => toggleGroup('pd')}>
          {s?.pdh != null && <Row label="PDH" value={s.pdh.toFixed(2)} color="text-orange-400" />}
          {s?.pdl != null && <Row label="PDL" value={s.pdl.toFixed(2)} color="text-orange-400" />}
        </Group>

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

      {/* TPO Profiles (per-session toggles + stats) */}
      {sessionTPO?.sessions && (
        <div className="px-2 py-1 border-b border-border last:border-b-0">
          <div className="flex gap-2 mb-1">
            <button onClick={() => toggleCluster(['tpo_tokyo', 'tpo_london', 'tpo_ny'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer">TPO</button>
            <button onClick={() => toggleGroup('tpo_tokyo')} className={`text-[10px] cursor-pointer transition-colors ${isGroupHidden('tpo_tokyo') ? 'text-muted line-through' : 'text-cyan-400'}`}>TKY</button>
            <button onClick={() => toggleGroup('tpo_london')} className={`text-[10px] cursor-pointer transition-colors ${isGroupHidden('tpo_london') ? 'text-muted line-through' : 'text-emerald-400'}`}>LDN</button>
            <button onClick={() => toggleGroup('tpo_ny')} className={`text-[10px] cursor-pointer transition-colors ${isGroupHidden('tpo_ny') ? 'text-muted line-through' : 'text-red-400'}`}>NY</button>
          </div>

          <div className="space-y-1">
            <SessionTPOBlock label="TKY" data={sessionTPO.sessions.tokyo} color="text-cyan-600" levelColor="text-cyan-400" hidden={isGroupHidden('tpo_tokyo')} />
            <SessionTPOBlock label="LDN" data={sessionTPO.sessions.london} color="text-emerald-600" levelColor="text-emerald-400" hidden={isGroupHidden('tpo_london')} />
            <SessionTPOBlock label="NY" data={sessionTPO.sessions.ny} color="text-red-600" levelColor="text-red-400" hidden={isGroupHidden('tpo_ny')} showIBLevels />
          </div>
        </div>
      )}

    </div>
  );
}

/** Compact per-session TPO info block */
function SessionTPOBlock({ label, data, color, levelColor, hidden, showIBLevels }: { label: string; data: SessionTPOData | null; color: string; levelColor: string; hidden: boolean; showIBLevels?: boolean }) {
  if (!data || hidden) return null;
  const arrow = data.opening_direction === 'up' ? '↑' : data.opening_direction === 'down' ? '↓' : '↔';
  const ibTicks = data.ib_valid ? ((data.ib_high - data.ib_low) / 0.25).toFixed(0) : '—';
  return (
    <div className="text-[10px] leading-tight">
      <span className={`${color} font-bold`}>{label}</span>
      <span className="text-muted2 ml-1">
        {data.shape} {data.opening_type}{arrow}
      </span>
      <span className="text-muted2 ml-1">IB:{ibTicks}</span>
      {showIBLevels && data.ib_valid && (
        <div className="text-muted2 ml-2">
          <span className="text-amber-400">NYIBH</span> {data.ib_high.toFixed(2)}
          <span className="text-amber-400 ml-1">NYIBL</span> {data.ib_low.toFixed(2)}
          <span className="text-muted ml-1">Range</span> {(data.ib_high - data.ib_low).toFixed(2)}
        </div>
      )}
      <div className="text-muted2 ml-2">
        <span className={levelColor}>tPOC</span> {data.poc.toFixed(0)}
        <span className={`${levelColor} ml-1`}>tVAH</span> {data.vah.toFixed(0)}
        <span className={`${levelColor} ml-1`}>tVAL</span> {data.val.toFixed(0)}
      </div>
    </div>
  );
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

function Placeholder({ text }: { text: string }) {
  return <div className="text-muted2 text-center py-2 text-[10px]">{text}</div>;
}
