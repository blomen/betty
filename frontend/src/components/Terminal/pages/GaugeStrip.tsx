import type { OrderflowIndicators, MacroSnapshot, MarketSession } from '@/types/market';

// ─── Gauge helper ────────────────────────────────────────────────────────────

function Gauge({ label, value, color, bar }: {
  label: string;
  value: string;
  color: string;   // tailwind text color class
  bar?: number;     // 0-1 fill fraction, omit for non-numeric gauges
}) {
  // Map text color to bar background color
  const barColor = bar != null
    ? color.includes('green') ? 'bg-green-500/30'
    : color.includes('red') ? 'bg-red-500/30'
    : color.includes('yellow') ? 'bg-yellow-500/30'
    : color.includes('orange') ? 'bg-orange-500/30'
    : 'bg-zinc-500/30'
    : undefined;

  return (
    <div className="border border-zinc-700 bg-zinc-900/50 px-2 py-1.5 min-w-[55px] text-center">
      <div className="text-[9px] text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className={`text-xs font-mono font-medium ${color}`}>{value}</div>
      {bar != null && barColor && (
        <div className="mt-0.5 h-1 bg-zinc-800 rounded-sm overflow-hidden">
          <div
            className={`h-full rounded-sm ${barColor}`}
            style={{ width: `${Math.min(Math.max(bar, 0), 1) * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}

// ─── Color helpers ───────────────────────────────────────────────────────────

function signColor(val: number): string {
  return val > 0 ? 'text-green-400' : val < 0 ? 'text-red-400' : 'text-zinc-400';
}

function fmtSigned(val: number | null | undefined): string {
  if (val == null) return '--';
  return `${val > 0 ? '+' : ''}${val.toLocaleString()}`;
}

// ─── Main ────────────────────────────────────────────────────────────────────

export function GaugeStrip({ session, orderflow, macro }: {
  session: MarketSession | undefined;
  orderflow: OrderflowIndicators | undefined;
  macro: MacroSnapshot | undefined;
}) {
  // Delta
  const delta = orderflow?.delta;
  const deltaColor = delta != null ? signColor(delta) : 'text-zinc-500';
  const deltaBar = delta != null ? Math.abs(delta) / 5000 : undefined;

  // CVD
  const cvd = orderflow?.cvd;
  const cvdTrend = orderflow?.cvd_trend ?? 'flat';
  const cvdIcon = cvdTrend === 'rising' ? ' ^' : cvdTrend === 'falling' ? ' v' : '';
  const cvdColor = cvdTrend === 'rising' ? 'text-green-400' : cvdTrend === 'falling' ? 'text-red-400' : 'text-zinc-400';
  const cvdBar = cvd != null ? Math.abs(cvd) / 10000 : undefined;

  // Rotation Factor
  const rf = session?.rotation_factor;
  const rfColor = rf != null ? signColor(rf) : 'text-zinc-500';
  const rfBar = rf != null ? Math.abs(rf) / 6 : undefined;

  // ASPR
  const aspr = session?.aspr;
  const asprPct = session?.aspr_percentile;
  const asprColor = asprPct != null
    ? asprPct < 0.3 ? 'text-green-400' : asprPct > 0.7 ? 'text-red-400' : 'text-yellow-400'
    : 'text-zinc-400';
  const asprBar = asprPct ?? undefined;

  // Imbalance
  const imbDir = orderflow?.stacked_imbalance_direction;
  const imbCount = orderflow?.stacked_imbalance_count ?? 0;
  const imbVal = imbDir && imbDir !== 'neutral' && imbCount > 0 ? `${imbDir} x${imbCount}` : '--';
  const imbColor = imbDir === 'buy' ? 'text-green-400' : imbDir === 'sell' ? 'text-red-400' : 'text-zinc-500';

  // Value migration
  const valMig = session?.value_migration;
  const valIcon = valMig === 'up' ? '^' : valMig === 'down' ? 'v' : '--';
  const valColor = valMig === 'up' ? 'text-green-400' : valMig === 'down' ? 'text-red-400' : 'text-zinc-500';

  // VIX
  const vix = macro?.vix;
  const vixColor = vix != null
    ? vix < 18 ? 'text-green-400' : vix > 25 ? 'text-red-400' : 'text-yellow-400'
    : 'text-zinc-500';

  // Big trades
  const bigCount = orderflow?.big_trades_count ?? 0;
  const bigNet = orderflow?.big_trades_net_delta ?? 0;
  const bigColor = bigCount > 0 ? (bigNet > 0 ? 'text-green-400' : 'text-red-400') : 'text-zinc-500';

  // Boolean gauges
  const vsaActive = orderflow?.vsa_absorption ?? false;
  const trappedActive = orderflow?.trapped_traders ?? false;
  const stopRunActive = orderflow?.stop_run_detected ?? false;

  return (
    <div className="flex flex-wrap gap-1">
      <Gauge label="DELTA" value={fmtSigned(delta)} color={deltaColor} bar={deltaBar} />
      <Gauge label="CVD" value={cvd != null ? `${cvd.toLocaleString()}${cvdIcon}` : '--'} color={cvdColor} bar={cvdBar} />
      <Gauge label="ROT.F" value={rf != null ? fmtSigned(rf) : '--'} color={rfColor} bar={rfBar} />
      <Gauge label="ASPR" value={aspr != null ? `${aspr.toFixed(0)}pt${asprPct != null ? ` P${(asprPct * 100).toFixed(0)}` : ''}` : '--'} color={asprColor} bar={asprBar} />
      <Gauge label="IMBAL" value={imbVal} color={imbColor} />
      <Gauge label="VALUE" value={valIcon} color={valColor} />
      <Gauge label="VIX" value={vix != null ? vix.toFixed(1) : '--'} color={vixColor} />
      <Gauge label="BIG" value={bigCount > 0 ? `x${bigCount}` : '--'} color={bigColor} />
      <Gauge label="VSA" value={vsaActive ? '✓' : '--'} color={vsaActive ? 'text-green-400' : 'text-zinc-600'} />
      <Gauge label="TRAP" value={trappedActive ? '✓' : '--'} color={trappedActive ? 'text-orange-400' : 'text-zinc-600'} />
      <Gauge label="STOP" value={stopRunActive ? '✓' : '--'} color={stopRunActive ? 'text-red-400' : 'text-zinc-600'} />
    </div>
  );
}
