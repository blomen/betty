import { useEffect, useState } from 'react'
import { TradeTicket } from '@/components/stocks/TradeTicket'
import { DecisionFlow } from '@/components/stocks/DecisionFlow'
import { DimsBreakdownCard } from '@/components/stocks/DimsBreakdownCard'
import { api } from '@/hooks/useStocksApi'
import type { DashboardState } from '@/hooks/useDashboardWS'
import type { ModelStatus } from '@/types/stocks'

interface Props {
  ws: DashboardState
}

/**
 * Stocks signal sheet — everything a manual trader needs in one screen.
 *
 *   1. Live position (always pinned, expanded when in position)
 *   2. Trade ticket  (the actionable signal: direction, entry, stop, risk,
 *                     edge, gates, confluence, live ladder, copy button)
 *   3. Decision flow (visual: features → signals → gates → verdict)
 *   4. Raw dims      (collapsible, debug)
 *
 * The lifecycle header is gone — its info (state + age + verdict) is now in
 * the TradeTicket header. The ContextStrip is gone — its chips moved into
 * the ticket so all signal context lives in one place.
 */
export default function SignalsPage({ ws }: Props) {
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)
  useEffect(() => {
    const poll = () => api.getModelStatus().then(setModelStatus).catch(() => {})
    poll()
    const iv = setInterval(poll, 3000)
    return () => clearInterval(iv)
  }, [])

  const zoneEntry = ws.dqnByTrigger.zone_entry
  const inference = zoneEntry?.event ?? ws.dqnInference
  const inferenceAt = zoneEntry?.at ?? ws.dqnInferenceAt

  return (
    <div className="flex flex-col flex-1 min-h-0 p-3 gap-3 overflow-y-auto bg-zinc-950">
      <TradeTicket
        inference={inference}
        inferenceAt={inferenceAt}
        schema={ws.observationSchema}
        lastPrice={ws.lastPrice}
        quote={ws.quote}
        modelStatus={modelStatus}
        zones={ws.zones}
      />
      <DecisionFlow inference={inference} schema={ws.observationSchema} />
      <DimsBreakdownCard inference={inference} schema={ws.observationSchema} />
    </div>
  )
}
