import { useCallback } from 'react';
import type { StreamBookEvent, CandleData, ExpandedSession, VPLevel } from '@/types/market';

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
};

interface Props {
  book: StreamBookEvent | null;
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
  hiddenLevels: Set<string>;
  setHiddenLevels: React.Dispatch<React.SetStateAction<Set<string>>>;
}

export function BookSnapshot({ session, hiddenLevels, setHiddenLevels }: Props) {
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

  const allHidden = Object.values(LEVEL_GROUPS).flat().every(k => hiddenLevels.has(k));

  return (
    <div className="flex flex-col h-full min-h-0 text-xs font-mono overflow-y-auto">

      {/* Master toggle */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
        <span className="text-[10px] text-muted uppercase tracking-wider">Levels</span>
        <EyeBtn hidden={allHidden} onClick={toggleAll} />
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
      <div className="px-3 py-2 border-b border-border">
        <button onClick={() => toggleCluster(['ib', 'pd', 'tokyo', 'london'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-2 block">Session</button>

        <Group label="IB" hidden={isGroupHidden('ib')} onToggle={() => toggleGroup('ib')}>
          {s?.ib_high != null && s?.ib_low != null ? (
            <>
              <Row label="IBH" value={s.ib_high.toFixed(2)} color="text-amber-400" />
              <Row label="IBL" value={s.ib_low.toFixed(2)} color="text-amber-400" />
              <Row label="IB Range" value={(s.ib_high - s.ib_low).toFixed(2)} />
            </>
          ) : (
            <Placeholder text="Waiting for IB (15:30-16:30 CET)" />
          )}
        </Group>

        <Group label="PD" hidden={isGroupHidden('pd')} onToggle={() => toggleGroup('pd')}>
          {s?.pdh != null && <Row label="PDH" value={s.pdh.toFixed(2)} color="text-orange-400" />}
          {s?.pdl != null && <Row label="PDL" value={s.pdl.toFixed(2)} color="text-orange-400" />}
        </Group>

        <Group label="Tokyo" hidden={isGroupHidden('tokyo')} onToggle={() => toggleGroup('tokyo')}>
          {s?.tokyo_high != null && <Row label="Tokyo H" value={s.tokyo_high.toFixed(2)} color="text-cyan-400" />}
          {s?.tokyo_low != null && <Row label="Tokyo L" value={s.tokyo_low.toFixed(2)} color="text-cyan-400" />}
        </Group>

        <Group label="London" hidden={isGroupHidden('london')} onToggle={() => toggleGroup('london')}>
          {s?.london_high != null && <Row label="London H" value={s.london_high.toFixed(2)} color="text-emerald-400" />}
          {s?.london_low != null && <Row label="London L" value={s.london_low.toFixed(2)} color="text-emerald-400" />}
        </Group>
      </div>

      {/* Volume Profile — multi-timeframe */}
      <div className="px-3 py-2 border-b border-border last:border-b-0">
        <button onClick={() => toggleCluster(['daily_vp', 'weekly_vp', 'monthly_vp'])} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer mb-2 block">Volume Profile</button>

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

    </div>
  );
}

// --- UI building blocks ---

function EyeBtn({ hidden, onClick }: { hidden: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-muted2 hover:text-text transition-colors p-0.5"
      title={hidden ? 'Show' : 'Hide'}
    >
      {hidden ? <EyeOffIcon /> : <EyeIcon />}
    </button>
  );
}

function EyeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}

/** Unified group component — click title or eye to toggle. `section` adds px/border for top-level sections. */
function Group({ label, hidden, onToggle, section, children }: {
  label: string; hidden: boolean; onToggle: () => void; section?: boolean; children: React.ReactNode;
}) {
  if (section) {
    return (
      <div className={`px-3 py-2 border-b border-border ${hidden ? 'opacity-40' : ''}`}>
        <div className="flex items-center justify-between mb-2">
          <button onClick={onToggle} className="text-[10px] text-muted uppercase tracking-wider hover:text-text transition-colors cursor-pointer">{label}</button>
          <EyeBtn hidden={hidden} onClick={onToggle} />
        </div>
        {children}
      </div>
    );
  }
  return (
    <div className={`mb-1.5 ${hidden ? 'opacity-40' : ''}`}>
      <div className="flex items-center justify-between mb-0.5">
        <button onClick={onToggle} className="text-[10px] text-muted2 font-bold hover:text-text transition-colors cursor-pointer">{label}</button>
        <EyeBtn hidden={hidden} onClick={onToggle} />
      </div>
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
  if (!vp) return null;
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
