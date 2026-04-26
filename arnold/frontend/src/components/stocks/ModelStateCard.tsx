import type { DQNInferenceEvent } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  inferenceAt: number | null
  lastPrice: number | null
}

export function ModelStateCard({ inference, inferenceAt, lastPrice }: Props) {
  if (!inference) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">Model</div>
        <div className="text-zinc-400">No inference yet</div>
      </div>
    )
  }

  const ageS = inferenceAt ? Math.round((Date.now() - inferenceAt) / 1000) : null
  const action = inference.action
  const actionColor = action === 'long' ? 'text-emerald-400' : action === 'short' ? 'text-red-400' : 'text-zinc-400'

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Model State</span>
        <span className="text-zinc-500">{ageS !== null ? `${ageS}s ago` : ''}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-zinc-300">
        <span className="text-zinc-500">Trigger</span><span>{inference.trigger}</span>
        <span className="text-zinc-500">Action</span><span className={actionColor}>{action}</span>
        <span className="text-zinc-500">Confidence</span><span>{inference.confidence?.toFixed(3) ?? '—'}</span>
        <span className="text-zinc-500">Cont P</span><span>{inference.cont_p?.toFixed(3) ?? '—'}</span>
        <span className="text-zinc-500">Rev P</span><span>{inference.rev_p?.toFixed(3) ?? '—'}</span>
        <span className="text-zinc-500">Stop ticks</span><span>{inference.stop_ticks ?? '—'}</span>
        <span className="text-zinc-500">Zone center</span><span>{inference.zone_center?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Zone members</span><span>{inference.zone_members ?? '—'}</span>
        <span className="text-zinc-500">Last price</span><span>{lastPrice?.toFixed(2) ?? '—'}</span>
      </div>
    </div>
  )
}
