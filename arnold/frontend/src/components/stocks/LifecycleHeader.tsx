import { useEffect, useState } from 'react'
import type { DQNInferenceEvent } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  inferenceAt: number | null
  lastPrice: number | null
}

function ageStr(ageS: number): string {
  if (ageS < 60) return `${ageS}s ago`
  if (ageS < 3600) return `${Math.floor(ageS / 60)}m ${ageS % 60}s ago`
  return `${(ageS / 3600).toFixed(1)}h ago`
}

export function LifecycleHeader({ inference, inferenceAt, lastPrice }: Props) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(iv)
  }, [])

  const ageS = inferenceAt ? Math.max(0, Math.round((now - inferenceAt) / 1000)) : null
  const isFresh = ageS !== null && ageS < 90
  const action = inference?.action ?? null
  const dirLabel = action ? action.toString().toUpperCase() : null
  const dirColor =
    isFresh && action === 'CONTINUATION'
      ? 'text-emerald-400'
      : isFresh && action === 'REVERSAL'
        ? 'text-orange-300'
        : isFresh && (action === 'SKIP' || action === 'skip')
          ? 'text-amber-400'
          : 'text-zinc-400'
  const dot =
    isFresh && (action === 'CONTINUATION' || action === 'REVERSAL')
      ? 'bg-emerald-500'
      : isFresh && (action === 'SKIP' || action === 'skip')
        ? 'bg-amber-400'
        : 'bg-zinc-600'

  if (!inference || ageS === null) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs font-mono flex items-center gap-3">
        <span className="inline-block h-2 w-2 rounded-full bg-zinc-600" />
        <span className="text-zinc-200 font-semibold">WAITING</span>
        <span className="text-zinc-500">no signal yet — last price {lastPrice?.toFixed(2) ?? '—'}</span>
      </div>
    )
  }

  const conf = inference.confidence ?? 0
  const zone = inference.zone_center

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs font-mono flex items-center gap-3">
      <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
      <span className="text-zinc-200 font-semibold">
        {isFresh ? 'ZONE ENTERED' : 'LAST SIGNAL'}
      </span>
      <span className={`font-semibold ${dirColor}`}>{dirLabel}</span>
      <span className="text-zinc-500">conf</span>
      <span className="text-zinc-200">{conf.toFixed(2)}</span>
      {zone !== undefined && zone !== null && (
        <>
          <span className="text-zinc-500">@</span>
          <span className="text-zinc-200">{zone.toFixed(2)}</span>
        </>
      )}
      <span className="text-zinc-500 ml-auto">{ageStr(ageS)}</span>
    </div>
  )
}
