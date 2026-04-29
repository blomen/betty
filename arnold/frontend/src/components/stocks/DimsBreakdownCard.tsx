import { useMemo, useState } from 'react'
import type { DQNInferenceEvent, ObservationSchema, ObservationSegment } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  schema: ObservationSchema | null
}

function fmt(v: number): string {
  if (!Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs === 0) return '0.00'
  if (abs < 0.01) return v.toExponential(1)
  if (abs >= 100) return v.toFixed(0)
  return v.toFixed(3)
}

function bar(v: number): string {
  const ratio = Math.max(0, Math.min(1, Math.abs(v)))
  const filled = Math.round(ratio * 8)
  return '▁▂▃▄▅▆▇█'.slice(filled, filled + 1) || '▁'
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
  const hotThreshold = useMemo(() => {
    if (!inputs.length) return 1
    const abs = inputs.map((v) => Math.abs(v)).filter((v) => Number.isFinite(v))
    if (!abs.length) return 1
    const sorted = abs.slice().sort((a, b) => a - b)
    const idx = Math.floor(sorted.length * 0.8)
    return Math.max(sorted[idx] ?? 0.5, 0.2)
  }, [inputs])

  if (!inference || !schema) return null

  return (
    <details
      open={showAll}
      onToggle={(e) => setShowAll((e.target as HTMLDetailsElement).open)}
      className="rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs font-mono"
    >
      <summary className="cursor-pointer text-zinc-400 hover:text-zinc-200 select-none flex justify-between">
        <span>
          <span className="text-zinc-500 uppercase tracking-wider">Raw dims</span>
          <span className="ml-2 text-zinc-500">
            {inputs.length} values, top-decile highlighted
          </span>
        </span>
        <span className="text-zinc-500 text-[10px]">{inference.model_type ?? ''}</span>
      </summary>
      <div className="mt-2 space-y-1">
        {schema.segments.map((seg) => (
          <GroupSection key={seg.name} segment={seg} inputs={inputs} threshold={hotThreshold} />
        ))}
      </div>
    </details>
  )
}
