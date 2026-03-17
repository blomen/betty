import type { ExpandedSession } from '@/types/market';

interface Props {
  session: ExpandedSession | null;
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{children}</span>;
}

function Value({ children, color = 'text-zinc-300' }: { children: React.ReactNode; color?: string }) {
  return <span className={`text-xs font-mono ${color}`}>{children}</span>;
}

function Row({ label, value, color }: { label: string; value: string | number | null | undefined; color?: string }) {
  if (value == null || value === '') return null;
  return (
    <div className="flex justify-between items-baseline gap-2">
      <Label>{label}</Label>
      <Value color={color}>{value}</Value>
    </div>
  );
}

function typeColor(type: string | null | undefined): string {
  if (!type) return 'text-zinc-500';
  if (type.includes('trending_up')) return 'text-emerald-400';
  if (type.includes('trending_down')) return 'text-red-400';
  return 'text-amber-400';
}

function openColor(type: string | null | undefined): string {
  if (!type) return 'text-zinc-500';
  if (type === 'OD') return 'text-emerald-400';
  if (type === 'ORR') return 'text-red-400';
  return 'text-amber-400';
}

function regimeColor(regime: string | null | undefined): string {
  if (!regime) return 'text-zinc-500';
  if (regime === 'risk_on') return 'text-emerald-400';
  if (regime === 'risk_off') return 'text-red-400';
  return 'text-amber-400';
}

export function ContextSidebar({ session }: Props) {
  if (!session) {
    return (
      <div className="bg-panel border border-border p-3 text-zinc-600 text-xs">
        No session data
      </div>
    );
  }

  const s = session.session;
  const p = session.profiles;
  const m = session.macro;
  const pp = session.price_position;

  return (
    <div className="bg-panel border border-border p-3 space-y-3 overflow-y-auto text-xs">
      {/* Session */}
      <div className="space-y-1">
        <div className="text-zinc-400 text-[10px] font-semibold uppercase tracking-widest border-b border-zinc-800 pb-1 mb-1">Session</div>
        <Row label="Type" value={s?.market_type} color={typeColor(s?.market_type)} />
        <Row label="Open" value={s?.opening_type} color={openColor(s?.opening_type)} />
        <Row label="IB" value={s?.ib_high && s?.ib_low ? `${s.ib_low.toFixed(0)}-${s.ib_high.toFixed(0)} (${(s.ib_high - s.ib_low).toFixed(0)}pt)` : null} />
        <Row label="Distrib" value={s?.distribution_type} color="text-purple-400" />
      </div>

      {/* Volume Profile */}
      <div className="space-y-1">
        <div className="text-zinc-400 text-[10px] font-semibold uppercase tracking-widest border-b border-zinc-800 pb-1 mb-1">Volume Profile</div>
        <Row label="POC" value={p?.session?.poc?.toFixed(2)} color="text-cyan-400" />
        <Row label="VAH" value={p?.session?.vah?.toFixed(2)} />
        <Row label="VAL" value={p?.session?.val?.toFixed(2)} />
        {p?.developing_poc != null && (
          <Row label="Dev POC" value={`${p.developing_poc.toFixed(2)} ${p.developing_poc_direction === 'up' ? '↑' : p.developing_poc_direction === 'down' ? '↓' : '→'}`}
            color={p.developing_poc_direction === 'up' ? 'text-emerald-400' : p.developing_poc_direction === 'down' ? 'text-red-400' : 'text-zinc-400'} />
        )}
      </div>

      {/* VWAP */}
      <div className="space-y-1">
        <div className="text-zinc-400 text-[10px] font-semibold uppercase tracking-widest border-b border-zinc-800 pb-1 mb-1">VWAP</div>
        <Row label="Value" value={s?.vwap?.toFixed(2)} color="text-purple-400" />
        {pp?.vwap_deviation_sd != null && (
          <Row label="SD Dev"
            value={`${pp.vwap_deviation_sd > 0 ? '+' : ''}${pp.vwap_deviation_sd.toFixed(2)} SD`}
            color={Math.abs(pp.vwap_deviation_sd) > 2 ? 'text-red-400' : Math.abs(pp.vwap_deviation_sd) > 1 ? 'text-amber-400' : 'text-zinc-400'} />
        )}
      </div>

      {/* Macro */}
      <div className="space-y-1">
        <div className="text-zinc-400 text-[10px] font-semibold uppercase tracking-widest border-b border-zinc-800 pb-1 mb-1">Macro</div>
        <Row label="Regime" value={m?.regime} color={regimeColor(m?.regime)} />
        <Row label="VIX" value={m?.vix?.toFixed(1)}
          color={(m?.vix ?? 20) < 18 ? 'text-emerald-400' : (m?.vix ?? 20) > 25 ? 'text-red-400' : 'text-amber-400'} />
        <Row label="DXY" value={m?.dxy?.toFixed(2)} />
      </div>

      {/* ML */}
      <div className="space-y-1">
        <div className="text-zinc-400 text-[10px] font-semibold uppercase tracking-widest border-b border-zinc-800 pb-1 mb-1">ML</div>
        <Row label="Day Type" value={session.ml_day_type} color="text-purple-400" />
        {session.ml_day_type_confidence != null && (
          <Row label="Confidence" value={`${session.ml_day_type_confidence}%`} />
        )}
      </div>

      {/* Extremes */}
      {(s?.poor_high || s?.poor_low) && (
        <div className="space-y-1">
          <div className="text-zinc-400 text-[10px] font-semibold uppercase tracking-widest border-b border-zinc-800 pb-1 mb-1">Extremes</div>
          {s?.poor_high && <div className="text-amber-400 text-[10px]">⚠ Poor High (unfinished auction)</div>}
          {s?.poor_low && <div className="text-amber-400 text-[10px]">⚠ Poor Low (unfinished auction)</div>}
        </div>
      )}
    </div>
  );
}
