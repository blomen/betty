interface GaugeBarProps {
  label: string;
  /** 0-1 fill amount */
  fill: number;
  /** Raw display value */
  value: string;
  /** Assessment text: "STRONG", "HIGH", "NONE", etc. */
  assessment: string;
  /** Color variant based on direction confirmation */
  color: 'green' | 'red' | 'amber' | 'dim';
}

const COLOR_MAP = {
  green: { bar: 'bg-emerald-500', text: 'text-emerald-400', label: 'text-emerald-300' },
  red: { bar: 'bg-red-500', text: 'text-red-400', label: 'text-red-300' },
  amber: { bar: 'bg-amber-500', text: 'text-amber-400', label: 'text-amber-300' },
  dim: { bar: 'bg-zinc-600', text: 'text-zinc-500', label: 'text-zinc-500' },
};

export type { GaugeBarProps };

export function GaugeBar({ label, fill, value, assessment, color }: GaugeBarProps) {
  const c = COLOR_MAP[color];
  const pct = Math.min(100, Math.max(0, fill * 100));

  return (
    <div className="flex items-center gap-2 font-mono text-xs min-w-[220px]">
      <span className="w-16 text-zinc-400 text-right shrink-0">{label}</span>
      <div className="flex-1 h-3 bg-zinc-800 rounded-sm overflow-hidden border border-zinc-700">
        <div className={`h-full ${c.bar} transition-all duration-300`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`w-14 text-right ${c.text} shrink-0`}>{value}</span>
      <span className={`w-16 text-right ${c.label} font-bold shrink-0`}>{assessment}</span>
    </div>
  );
}
