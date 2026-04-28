import { useMemo, useState } from 'react'
import type { DQNInferenceEvent, ObservationSchema, ObservationSegment } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  schema: ObservationSchema | null
}

interface LabeledDim {
  label: string
  value: number
}

function pickLabeled(
  inputs: number[],
  segments: ObservationSegment[] | undefined,
  segmentName: string,
  labels: string[],
): LabeledDim[] {
  if (!segments) return []
  const seg = segments.find((s) => s.name === segmentName)
  if (!seg) return []
  return labels
    .map((lbl) => {
      const idx = seg.labels.indexOf(lbl)
      if (idx < 0 || seg.start + idx >= inputs.length) return null
      return { label: lbl, value: inputs[seg.start + idx] }
    })
    .filter((x): x is LabeledDim => x !== null)
}

function fmt(v: number): string {
  if (!Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs === 0) return '0.00'
  if (abs < 0.01) return v.toExponential(1)
  if (abs >= 100) return v.toFixed(0)
  return v.toFixed(3)
}

function bar(v: number, max: number = 1): string {
  // Returns a unicode sparkbar segment representing |v|/max in [0,1]
  const ratio = Math.max(0, Math.min(1, Math.abs(v) / Math.max(max, 1e-9)))
  const filled = Math.round(ratio * 8)
  return '▁▂▃▄▅▆▇█'.slice(filled, filled + 1) || '▁'
}

function HeadlineTile({ title, rows }: { title: string; rows: LabeledDim[] }) {
  return (
    <div className="rounded border border-zinc-800 bg-zinc-950/40 p-2">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">{title}</div>
      {rows.length === 0 ? (
        <div className="text-zinc-500 text-[11px]">no data</div>
      ) : (
        <ul className="space-y-0.5">
          {rows.map((r) => (
            <li key={r.label} className="flex justify-between gap-2 text-[11px]">
              <span className="text-zinc-400 truncate">{r.label}</span>
              <span className="text-zinc-200 tabular-nums">{fmt(r.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function GroupSection({
  segment,
  inputs,
  threshold,
}: {
  segment: ObservationSegment
  inputs: number[]
  threshold: number
}) {
  const slice = inputs.slice(segment.start, segment.end)
  return (
    <details className="rounded border border-zinc-800 bg-zinc-950/40">
      <summary className="cursor-pointer px-2 py-1 text-[11px] flex justify-between items-center select-none hover:bg-zinc-900/40">
        <span className="text-zinc-300">{segment.title}</span>
        <span className="text-zinc-500 text-[10px]">
          {segment.size} dims · {segment.start}–{segment.end - 1}
        </span>
      </summary>
      <div className="p-2 grid grid-cols-2 md:grid-cols-3 gap-x-3 gap-y-0.5 text-[10px]">
        {slice.map((v, i) => {
          const isHot = Math.abs(v) >= threshold
          const label = segment.labels[i] ?? `dim_${segment.start + i}`
          return (
            <div
              key={i}
              className={`flex justify-between gap-2 ${isHot ? 'text-amber-300' : 'text-zinc-400'}`}
            >
              <span className="truncate">
                <span className="text-zinc-600 mr-1">{bar(v)}</span>
                {label}
              </span>
              <span className="tabular-nums">{fmt(v)}</span>
            </div>
          )
        })}
      </div>
    </details>
  )
}

export function DimsBreakdownCard({ inference, schema }: Props) {
  const [showAll, setShowAll] = useState(false)

  const inputs = inference?.inputs ?? []
  const segments = schema?.segments

  // Threshold for "hot" highlighting — 80th-percentile of |v| over all dims.
  const hotThreshold = useMemo(() => {
    if (!inputs.length) return 1
    const abs = inputs.map((v) => Math.abs(v)).filter((v) => Number.isFinite(v))
    if (!abs.length) return 1
    const sorted = abs.slice().sort((a, b) => a - b)
    const idx = Math.floor(sorted.length * 0.8)
    return Math.max(sorted[idx] ?? 0.5, 0.2)
  }, [inputs])

  const zoneRows = useMemo(() => {
    const rows: LabeledDim[] = []
    if (inference) {
      if (inference.zone_members !== undefined)
        rows.push({ label: 'members', value: inference.zone_members })
      if (inference.zone_hierarchy !== undefined)
        rows.push({ label: 'hierarchy', value: inference.zone_hierarchy })
    }
    rows.push(
      ...pickLabeled(inputs, segments, 'zone_features', ['session_relevance', 'width_norm']),
    )
    rows.push(
      ...pickLabeled(inputs, segments, 'zone_confluence', ['fvg_overlap', 'single_print_overlap']),
    )
    rows.push(...pickLabeled(inputs, segments, 'zone_quality', ['zone_quality']))
    return rows
  }, [inference, inputs, segments])

  const orderflowRows = useMemo(() => {
    const rows: LabeledDim[] = []
    if (inference?.gates && Number.isFinite(inference.gates.of_score)) {
      rows.push({ label: 'of_score', value: inference.gates.of_score })
    }
    rows.push(
      ...pickLabeled(inputs, segments, 'orderflow', [
        'cvd_trend',
        'stacked_imbalance_count',
        'stacked_direction',
        'big_trades_net_delta',
        'vsa_absorption',
        'absorption_strength',
      ]),
    )
    return rows
  }, [inference, inputs, segments])

  const amtRows = useMemo(() => {
    return pickLabeled(inputs, segments, 'amt_dynamics', [
      'developing_day_type',
      'day_type_confidence',
      'responsive_ratio',
      'initiative_ratio',
      'ib_ext_net_direction',
    ])
  }, [inputs, segments])

  const narrativeRows = useMemo(() => {
    const out: LabeledDim[] = []
    const narr = inference?.narrative ?? {}
    const wanted = [
      'regime_score',
      'htf_trend',
      'breakout_score',
      'trend_conviction',
      'value_migration',
      'excess_nearby',
    ]
    for (const k of wanted) {
      const v = narr[k]
      if (typeof v === 'number') out.push({ label: k, value: v })
    }
    return out
  }, [inference])

  if (!inference) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">Dims</div>
        <div className="text-zinc-400">No inference yet — waiting for next zone touch</div>
      </div>
    )
  }

  const totalDims = inputs.length
  const dimsLabel = schema && totalDims !== schema.total_dim ? `${totalDims}≠${schema.total_dim}` : `${totalDims}`

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Behind the curtain</span>
        <span className="text-zinc-500 text-[10px]">
          {dimsLabel} dims · {inference.model_type ?? 'model?'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-2 mb-3">
        <HeadlineTile title="Zone" rows={zoneRows} />
        <HeadlineTile title="Order flow" rows={orderflowRows} />
        <HeadlineTile title="AMT / Day-type" rows={amtRows} />
        <HeadlineTile title="Narrative" rows={narrativeRows} />
      </div>

      {!schema ? (
        <div className="text-zinc-500 text-[11px]">Schema not loaded — cannot show raw dims.</div>
      ) : (
        <details
          open={showAll}
          onToggle={(e) => setShowAll((e.target as HTMLDetailsElement).open)}
          className="border-t border-zinc-800 pt-2"
        >
          <summary className="cursor-pointer text-[11px] text-zinc-400 hover:text-zinc-200 select-none">
            Raw {totalDims} dims by group
          </summary>
          <div className="mt-2 space-y-1">
            {schema.segments.map((seg) => (
              <GroupSection
                key={seg.name}
                segment={seg}
                inputs={inputs}
                threshold={hotThreshold}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
