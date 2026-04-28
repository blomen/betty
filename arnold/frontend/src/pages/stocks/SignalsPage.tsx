import { useEffect, useState } from 'react'
import { TVOverlayStatus } from '@/components/stocks/TVOverlayStatus'
import { PositionCard } from '@/components/stocks/PositionCard'
import { LifecycleHeader } from '@/components/stocks/LifecycleHeader'
import { DecisionGatesCard } from '@/components/stocks/DecisionGatesCard'
import { DimsBreakdownCard } from '@/components/stocks/DimsBreakdownCard'
import { EventLog } from '@/components/stocks/EventLog'
import { L2Ladder } from '@/components/stocks/L2Ladder'
import { api } from '@/hooks/useStocksApi'
import type { DashboardState } from '@/hooks/useDashboardWS'
import type { ModelStatus } from '@/types/stocks'

interface Props {
  ws: DashboardState
}

export default function SignalsPage({ ws }: Props) {
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)

  useEffect(() => {
    const poll = () => {
      api.getModelStatus().then(setModelStatus).catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 5000)
    return () => clearInterval(iv)
  }, [])

  // Prefer the latest zone_entry inference for the gates + dims view: those
  // are the only events that carry a gate decision, so falling back to an
  // earlier "approaching" event would render an empty gates panel.
  const zoneEntry = ws.dqnByTrigger.zone_entry
  const decisionInference = zoneEntry?.event ?? ws.dqnInference

  return (
    <div className="flex flex-col flex-1 min-h-0 p-3 gap-3 overflow-y-auto bg-zinc-950">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <TVOverlayStatus />
        <PositionCard
          positions={ws.positions}
          modelStatus={modelStatus}
          lastPrice={ws.lastPrice}
        />
        <LifecycleHeader
          inference={ws.dqnInference}
          inferenceAt={ws.dqnInferenceAt}
          zones={ws.zones}
          lastPrice={ws.lastPrice}
        />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="lg:col-span-1">
          <DecisionGatesCard inference={decisionInference} />
        </div>
        <div className="lg:col-span-2">
          <DimsBreakdownCard inference={decisionInference} schema={ws.observationSchema} />
        </div>
      </div>
      <L2Ladder depth={ws.depth} lastPrice={ws.lastPrice} />
      <EventLog signals={ws.signals} fills={ws.fills} exits={ws.exits} />
    </div>
  )
}
