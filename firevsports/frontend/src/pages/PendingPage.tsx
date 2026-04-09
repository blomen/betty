import { useState, useEffect, useCallback } from 'react'
import { api } from '../hooks/useApi'

interface PendingBet {
  id: string
  event: string
  outcome: string
  odds: number
  stake: number
  status: string
  placed_at?: string
}

interface SettlementItem {
  bet_id: string
  event: string
  outcome: string
  result?: string
  profit?: number
}

interface ProviderPending {
  provider_id: string
  provider_name?: string
  open_bets: PendingBet[]
  pending_settlements: SettlementItem[]
}

interface PendingResponse {
  providers: ProviderPending[]
}

function fmtDate(s?: string) {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleString('sv-SE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return s
  }
}

function fmtProfit(p?: number) {
  if (p == null) return '—'
  const sign = p >= 0 ? '+' : ''
  return `${sign}${p.toFixed(2)}`
}

export default function PendingPage() {
  const [data, setData] = useState<PendingResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [confirmMsg, setConfirmMsg] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    try {
      const result = await api.getPendingBets()
      setData(result)
      setError(null)
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load])

  const handleConfirm = async (pid: string) => {
    setConfirming(pid)
    try {
      const res = await api.confirmSettlements(pid)
      setConfirmMsg(prev => ({ ...prev, [pid]: res?.message ?? 'Confirmed' }))
      await load()
    } catch (e: any) {
      setConfirmMsg(prev => ({ ...prev, [pid]: `Error: ${e.message}` }))
    } finally {
      setConfirming(null)
    }
  }

  const providers = data?.providers ?? []
  const totalOpen = providers.reduce((s, p) => s + p.open_bets.length, 0)
  const totalSettle = providers.reduce((s, p) => s + p.pending_settlements.length, 0)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Summary header */}
      <div className="flex items-center gap-4 px-3 py-1.5 border-b border-zinc-800 bg-zinc-900 text-xs">
        <span className="text-zinc-400">
          Open: <span className="text-zinc-200 font-mono">{totalOpen}</span>
        </span>
        <span className="text-zinc-400">
          Pending settle: <span className="text-amber-400 font-mono">{totalSettle}</span>
        </span>
        {error && <span className="text-red-400 ml-auto">{error}</span>}
        {!error && !data && <span className="text-zinc-500 ml-auto">Loading...</span>}
      </div>

      <div className="flex-1 overflow-y-auto">
        {providers.length === 0 && data && (
          <div className="p-4 text-zinc-500 text-xs">No open bets.</div>
        )}

        {providers.map(prov => {
          const name = prov.provider_name ?? prov.provider_id
          const hasSettlements = prov.pending_settlements.length > 0
          return (
            <div key={prov.provider_id} className="border-b border-zinc-800">
              {/* Provider header */}
              <div className="flex items-center gap-3 px-3 py-1.5 bg-zinc-900 border-b border-zinc-800">
                <span className="text-xs font-semibold text-amber-400 uppercase tracking-wide">{name}</span>
                <span className="text-xs text-zinc-500">
                  {prov.open_bets.length} open
                  {hasSettlements && (
                    <span className="ml-2 text-amber-400">{prov.pending_settlements.length} to settle</span>
                  )}
                </span>
                {hasSettlements && (
                  <button
                    onClick={() => handleConfirm(prov.provider_id)}
                    disabled={confirming === prov.provider_id}
                    className="ml-auto px-2 py-0.5 text-xs bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-zinc-950 font-semibold rounded transition-colors"
                  >
                    {confirming === prov.provider_id ? 'Confirming...' : 'Confirm'}
                  </button>
                )}
                {confirmMsg[prov.provider_id] && (
                  <span className="text-xs text-zinc-400 ml-2">{confirmMsg[prov.provider_id]}</span>
                )}
              </div>

              {/* Open bets */}
              {prov.open_bets.length > 0 && (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-zinc-500 border-b border-zinc-800">
                      <th className="text-left px-3 py-1 font-normal">Event</th>
                      <th className="text-left px-3 py-1 font-normal">Outcome</th>
                      <th className="text-right px-3 py-1 font-normal">Odds</th>
                      <th className="text-right px-3 py-1 font-normal">Stake</th>
                      <th className="text-right px-3 py-1 font-normal">Status</th>
                      <th className="text-right px-3 py-1 font-normal">Placed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prov.open_bets.map(bet => (
                      <tr key={bet.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/40">
                        <td className="px-3 py-1.5 text-zinc-200 max-w-[180px] truncate">{bet.event}</td>
                        <td className="px-3 py-1.5 text-zinc-300">{bet.outcome}</td>
                        <td className="px-3 py-1.5 text-right font-mono text-zinc-200">{bet.odds.toFixed(2)}</td>
                        <td className="px-3 py-1.5 text-right font-mono text-zinc-300">{bet.stake}</td>
                        <td className="px-3 py-1.5 text-right">
                          <span className={`font-mono ${
                            bet.status === 'won' ? 'text-green-400' :
                            bet.status === 'lost' ? 'text-red-400' :
                            'text-zinc-400'
                          }`}>{bet.status}</span>
                        </td>
                        <td className="px-3 py-1.5 text-right text-zinc-500">{fmtDate(bet.placed_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {/* Pending settlements */}
              {hasSettlements && (
                <div className="border-t border-zinc-800/50">
                  <div className="px-3 py-1 text-xs text-zinc-500 bg-zinc-900/50">Pending settlements</div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-zinc-500 border-b border-zinc-800">
                        <th className="text-left px-3 py-1 font-normal">Event</th>
                        <th className="text-left px-3 py-1 font-normal">Outcome</th>
                        <th className="text-right px-3 py-1 font-normal">Result</th>
                        <th className="text-right px-3 py-1 font-normal">Profit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {prov.pending_settlements.map(s => (
                        <tr key={s.bet_id} className="border-b border-zinc-800/50 hover:bg-zinc-800/40">
                          <td className="px-3 py-1.5 text-zinc-200 max-w-[180px] truncate">{s.event}</td>
                          <td className="px-3 py-1.5 text-zinc-300">{s.outcome}</td>
                          <td className="px-3 py-1.5 text-right">
                            <span className={`font-mono ${
                              s.result === 'won' ? 'text-green-400' :
                              s.result === 'lost' ? 'text-red-400' :
                              'text-zinc-400'
                            }`}>{s.result ?? '—'}</span>
                          </td>
                          <td className={`px-3 py-1.5 text-right font-mono ${
                            (s.profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                          }`}>{fmtProfit(s.profit)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
