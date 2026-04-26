import { useEffect, useState } from 'react'
import { TVOverlayStatus } from '@/components/stocks/TVOverlayStatus'
import { ZonesTable } from '@/components/stocks/ZonesTable'
import { PositionCard } from '@/components/stocks/PositionCard'
import { ModelStateCard } from '@/components/stocks/ModelStateCard'
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
    const poll = () => { api.getModelStatus().then(setModelStatus).catch(() => {}) }
    poll()
    const iv = setInterval(poll, 5000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="flex flex-col flex-1 min-h-0 p-3 gap-3 overflow-y-auto bg-zinc-950">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <TVOverlayStatus />
        <PositionCard
          positions={ws.positions}
          modelStatus={modelStatus}
          lastPrice={ws.lastPrice}
        />
        <ModelStateCard
          inference={ws.dqnInference}
          inferenceAt={ws.dqnInferenceAt}
          lastPrice={ws.lastPrice}
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <ZonesTable zones={ws.zones} lastPrice={ws.lastPrice} />
        <L2Ladder depth={ws.depth} lastPrice={ws.lastPrice} autonomous={ws.autonomous} />
      </div>
      <EventLog signals={ws.signals} fills={ws.fills} exits={ws.exits} />
    </div>
  )
}
