import { LivePositionPanel } from '@/components/stocks/LivePositionPanel'
import { LifecycleHeader } from '@/components/stocks/LifecycleHeader'
import { DecisionFlow } from '@/components/stocks/DecisionFlow'
import { ContextStrip } from '@/components/stocks/ContextStrip'
import { DimsBreakdownCard } from '@/components/stocks/DimsBreakdownCard'
import type { DashboardState } from '@/hooks/useDashboardWS'

interface Props {
  ws: DashboardState
}

export default function SignalsPage({ ws }: Props) {
  // The trader's signal sheet only cares about the latest zone_entry —
  // approaching/touched events run inference but don't carry a gate decision.
  const zoneEntry = ws.dqnByTrigger.zone_entry
  const inference = zoneEntry?.event ?? ws.dqnInference
  const inferenceAt = zoneEntry?.at ?? ws.dqnInferenceAt

  return (
    <div className="flex flex-col flex-1 min-h-0 p-3 gap-3 overflow-y-auto bg-zinc-950">
      <LivePositionPanel
        positions={ws.positions}
        lastPrice={ws.lastPrice}
        fills={ws.fills}
        exits={ws.exits}
      />
      <LifecycleHeader
        inference={inference}
        inferenceAt={inferenceAt}
        lastPrice={ws.lastPrice}
      />
      <DecisionFlow inference={inference} schema={ws.observationSchema} />
      <ContextStrip inference={inference} schema={ws.observationSchema} />
      <DimsBreakdownCard inference={inference} schema={ws.observationSchema} />
    </div>
  )
}
