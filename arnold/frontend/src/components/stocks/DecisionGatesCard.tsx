import type { DQNInferenceEvent, GateBlocker } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
}

const BLOCKER_LABEL: Record<NonNullable<GateBlocker>, string> = {
  halted: 'trading halted',
  model_skip: 'model said SKIP',
  confidence: 'confidence below floor',
  orderflow: 'orderflow below floor',
  in_position: 'already in position',
}

interface Row {
  label: string
  passed: boolean
  detail: string
  blocking: boolean
}

function rowsFor(inference: DQNInferenceEvent): Row[] {
  const g = inference.gates
  if (!g) {
    return []
  }
  const action = g.model_action
  const actionPass = action !== 'SKIP' && action !== 'skip'
  return [
    {
      label: 'Model action',
      passed: actionPass,
      detail: action,
      blocking: g.blocker === 'model_skip',
    },
    {
      label: 'Confidence',
      passed: g.conf_pass,
      detail: `${g.confidence.toFixed(3)} ≥ ${g.conf_floor.toFixed(2)}`,
      blocking: g.blocker === 'confidence',
    },
    {
      label: 'Order flow',
      passed: g.of_pass,
      detail: `${g.of_score.toFixed(3)} ≥ ${g.of_floor.toFixed(2)}`,
      blocking: g.blocker === 'orderflow',
    },
    {
      label: 'Flat',
      passed: g.is_flat,
      detail: g.is_flat ? 'flat' : 'in position',
      blocking: g.blocker === 'in_position',
    },
    {
      label: 'Not halted',
      passed: !g.halted,
      detail: g.halted ? 'trading_paused flag set' : 'live',
      blocking: g.blocker === 'halted',
    },
  ]
}

export function DecisionGatesCard({ inference }: Props) {
  if (!inference) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">Decision gates</div>
        <div className="text-zinc-400">No inference yet</div>
      </div>
    )
  }
  const g = inference.gates
  if (!g) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">Decision gates</div>
        <div className="text-zinc-400">
          Pre-decision event ({inference.trigger}) — gates only apply at zone_entry
        </div>
      </div>
    )
  }
  const rows = rowsFor(inference)
  const dispatched = g.decision === 'DISPATCHED'

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Decision gates</span>
        <span
          className={`px-2 py-0.5 rounded text-[10px] uppercase tracking-wider ${
            dispatched ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'
          }`}
        >
          {dispatched
            ? 'DISPATCHED'
            : `BLOCKED · ${g.blocker ? BLOCKER_LABEL[g.blocker] : 'unknown'}`}
        </span>
      </div>
      <ul className="space-y-1">
        {rows.map((r) => (
          <li key={r.label} className="flex items-center justify-between">
            <span className="flex items-center gap-2">
              <span
                className={`inline-block w-3 text-center font-bold ${
                  r.passed ? 'text-emerald-400' : r.blocking ? 'text-red-400' : 'text-zinc-500'
                }`}
              >
                {r.passed ? '✓' : '✗'}
              </span>
              <span className="text-zinc-300">{r.label}</span>
            </span>
            <span className={r.blocking ? 'text-red-400' : 'text-zinc-500'}>{r.detail}</span>
          </li>
        ))}
      </ul>
      {g.reckless && (
        <div className="mt-2 text-[10px] text-amber-500">
          RECKLESS_LEARNING_MODE — paper-trading floors (conf 0.05, OF 0.15)
        </div>
      )}
    </div>
  )
}
