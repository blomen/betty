import type { DQNInferenceEvent, ObservationSchema } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  schema: ObservationSchema | null
}

const PATTERN_HUMAN: Record<string, string> = {
  pin_bar_rejection: 'pin bar',
  absorption_wall: 'absorption wall',
  imbalance_cluster: 'imbalance cluster',
  delta_divergence: 'delta divergence',
  trapped_breakout: 'trapped breakout',
}

const SETUP_HUMAN: Record<string, string> = {
  poor_extreme: 'poor extreme',
  ib_break: 'IB break',
  spring: 'spring',
  sfp: 'SFP',
  rule_80: '80% rule',
  fakeout: 'fakeout',
  break_from_balance: 'break from balance',
  double_distribution: 'double distribution',
  news_directional: 'news directional',
  absorption: 'absorption',
  vwap_sd2_reversal: 'VWAP σ2 reversal',
  gap_logic: 'gap logic',
  pbd: 'PBD',
  squeeze: 'squeeze',
}

function activeLabels(
  inputs: number[],
  schema: ObservationSchema,
  segName: string,
  threshold: number,
  human?: Record<string, string>,
): string[] {
  const seg = schema.segments.find((s) => s.name === segName)
  if (!seg) return []
  const out: string[] = []
  for (let i = 0; i < seg.size; i++) {
    if ((inputs[seg.start + i] ?? 0) > threshold) {
      const k = seg.labels[i]
      out.push(human ? human[k] ?? k : k)
    }
  }
  return out
}

function Pill({ label, color }: { label: string; color: 'violet' | 'amber' | 'indigo' }) {
  const cls =
    color === 'violet'
      ? 'bg-violet-900/40 text-violet-200 border-violet-800'
      : color === 'amber'
        ? 'bg-amber-900/40 text-amber-200 border-amber-800'
        : 'bg-indigo-900/40 text-indigo-200 border-indigo-800'
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full border ${cls}`}>{label}</span>
  )
}

export function ContextStrip({ inference, schema }: Props) {
  if (!inference || !schema) return null
  const inputs = inference.inputs ?? []
  const confluence = activeLabels(inputs, schema, 'zone_composition', 0)
  const patterns = activeLabels(inputs, schema, 'pattern', 0.5, PATTERN_HUMAN)
  const setups = activeLabels(inputs, schema, 'setup', 0.5, SETUP_HUMAN)

  if (confluence.length + patterns.length + setups.length === 0) return null

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 font-mono text-xs flex flex-col gap-2">
      {confluence.length > 0 && (
        <div className="flex flex-wrap gap-1 items-center">
          <span className="text-zinc-500 uppercase tracking-wider mr-1 text-[10px]">
            Confluence ({confluence.length})
          </span>
          {confluence.map((c) => (
            <Pill key={c} label={c} color="violet" />
          ))}
        </div>
      )}
      {patterns.length > 0 && (
        <div className="flex flex-wrap gap-1 items-center">
          <span className="text-zinc-500 uppercase tracking-wider mr-1 text-[10px]">
            Patterns
          </span>
          {patterns.map((p) => (
            <Pill key={p} label={p} color="amber" />
          ))}
        </div>
      )}
      {setups.length > 0 && (
        <div className="flex flex-wrap gap-1 items-center">
          <span className="text-zinc-500 uppercase tracking-wider mr-1 text-[10px]">
            Setups
          </span>
          {setups.map((s) => (
            <Pill key={s} label={s} color="indigo" />
          ))}
        </div>
      )}
    </div>
  )
}
