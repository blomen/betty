import { useEffect, useState } from 'react'
import type { DQNInferenceEvent, Zone } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  inferenceAt: number | null
  zones: Zone[]
  lastPrice: number | null
}

const TICK_SIZE = 0.25

type Phase =
  | { kind: 'waiting'; nearestTicks: number | null; nearestPrice: number | null }
  | { kind: 'approaching'; ageS: number }
  | { kind: 'touched'; ageS: number }
  | { kind: 'zone_entry'; ageS: number; action: string; confidence: number; zoneCenter: number | null }

function classify(
  inference: DQNInferenceEvent | null,
  inferenceAt: number | null,
  zones: Zone[],
  lastPrice: number | null,
  now: number,
): Phase {
  const ageS = inferenceAt ? Math.max(0, Math.round((now - inferenceAt) / 1000)) : null

  if (inference && inferenceAt && ageS !== null) {
    if (inference.trigger === 'zone_entry') {
      return {
        kind: 'zone_entry',
        ageS,
        action: inference.action ?? 'unknown',
        confidence: inference.confidence ?? 0,
        zoneCenter: inference.zone_center ?? null,
      }
    }
    if (inference.trigger === 'touched' && ageS < 10) {
      return { kind: 'touched', ageS }
    }
    if (inference.trigger === 'approaching' && ageS < 30) {
      return { kind: 'approaching', ageS }
    }
  }

  let nearestTicks: number | null = null
  let nearestPrice: number | null = null
  if (lastPrice !== null && zones.length > 0) {
    let best: number | null = null
    for (const z of zones) {
      const d = Math.abs(z.price - lastPrice)
      if (best === null || d < best) {
        best = d
        nearestPrice = z.price
      }
    }
    if (best !== null) {
      nearestTicks = Math.round(best / TICK_SIZE)
    }
  }
  return { kind: 'waiting', nearestTicks, nearestPrice }
}

export function LifecycleHeader({ inference, inferenceAt, zones, lastPrice }: Props) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(iv)
  }, [])

  const phase = classify(inference, inferenceAt, zones, lastPrice, now)
  const action = phase.kind === 'zone_entry' ? phase.action : null
  const decisionColor =
    action === 'long'
      ? 'text-emerald-400'
      : action === 'short'
        ? 'text-red-400'
        : action === 'SKIP' || action === 'skip'
          ? 'text-amber-400'
          : 'text-zinc-300'

  let dotColor = 'bg-zinc-500'
  let label = 'WAITING'
  let detail: React.ReactNode = (
    <span className="text-zinc-400">
      {phase.kind === 'waiting' && phase.nearestTicks !== null
        ? `nearest zone ${phase.nearestTicks}t @ ${phase.nearestPrice?.toFixed(2)}`
        : 'no zones loaded'}
    </span>
  )

  if (phase.kind === 'approaching') {
    dotColor = 'bg-amber-400'
    label = 'APPROACHING'
    detail = <span className="text-amber-300">computing… {phase.ageS}s ago</span>
  } else if (phase.kind === 'touched') {
    dotColor = 'bg-orange-400'
    label = 'TOUCHED'
    detail = <span className="text-orange-300">computing… {phase.ageS}s ago</span>
  } else if (phase.kind === 'zone_entry') {
    dotColor =
      action === 'long' ? 'bg-emerald-500' : action === 'short' ? 'bg-red-500' : 'bg-amber-400'
    label = 'ZONE ENTERED'
    detail = (
      <span className="text-zinc-300">
        <span className={decisionColor}>{phase.action.toUpperCase()}</span>
        {' · conf '}
        <span className="text-zinc-200">{phase.confidence.toFixed(2)}</span>
        {phase.zoneCenter !== null && (
          <>
            {' · '}
            <span className="text-zinc-500">@{phase.zoneCenter.toFixed(2)}</span>
          </>
        )}
        {' · '}
        <span className="text-zinc-500">{phase.ageS}s ago</span>
      </span>
    )
  }

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Lifecycle</span>
      </div>
      <div className="flex items-center gap-2">
        <span className={`inline-block h-2 w-2 rounded-full ${dotColor}`} />
        <span className="text-zinc-200 font-semibold">{label}</span>
      </div>
      <div className="mt-1 text-[11px]">{detail}</div>
    </div>
  )
}
