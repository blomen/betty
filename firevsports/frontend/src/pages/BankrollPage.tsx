import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function BankrollPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['bankroll'],
    queryFn: () => apiFetch<any>('/api/bankroll'),
    refetchInterval: 30_000,
  })
  const { data: stats } = useQuery({
    queryKey: ['bankroll-stats'],
    queryFn: () => apiFetch<any>('/api/bankroll/stats'),
    refetchInterval: 60_000,
  })

  if (isLoading) return <div className="p-4 text-zinc-500">Loading...</div>
  if (!data) return <div className="p-4 text-zinc-600">No bankroll data.</div>

  const providers = data.providers ?? []
  const total = data.total ?? {}

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Summary cards */}
      {stats && (
        <div className="grid grid-cols-5 gap-3 p-3 border-b border-zinc-800">
          <div className="bg-zinc-900 rounded p-2">
            <div className="text-[10px] text-zinc-500 uppercase">Bets</div>
            <div className="text-lg font-bold text-zinc-200">{stats.total_bets ?? 0}</div>
          </div>
          <div className="bg-zinc-900 rounded p-2">
            <div className="text-[10px] text-zinc-500 uppercase">W/L</div>
            <div className="text-lg font-bold text-zinc-200">{stats.wins ?? 0}/{stats.losses ?? 0}</div>
          </div>
          <div className="bg-zinc-900 rounded p-2">
            <div className="text-[10px] text-zinc-500 uppercase">ROI</div>
            <div className={`text-lg font-bold ${(stats.roi_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {(stats.roi_pct ?? 0) >= 0 ? '+' : ''}{(stats.roi_pct ?? 0).toFixed(1)}%
            </div>
          </div>
          <div className="bg-zinc-900 rounded p-2">
            <div className="text-[10px] text-zinc-500 uppercase">Profit</div>
            <div className={`text-lg font-bold ${(stats.total_profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {(stats.total_profit ?? 0) >= 0 ? '+' : ''}{(stats.total_profit ?? 0).toFixed(0)} kr
            </div>
          </div>
          <div className="bg-zinc-900 rounded p-2">
            <div className="text-[10px] text-zinc-500 uppercase">Deposited</div>
            <div className="text-lg font-bold text-zinc-300">{(stats.net_deposited ?? 0).toFixed(0)} kr</div>
          </div>
        </div>
      )}

      {/* Provider table */}
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">
        {providers.length} providers · Total: {(total.balance_sek ?? 0).toFixed(0)} kr
      </div>
      <table className="w-full text-xs">
        <thead><tr className="text-zinc-600 text-[10px]">
          <th className="text-left pl-3 py-1">Provider</th>
          <th className="text-right">Balance</th>
          <th className="text-right pr-3">Currency</th>
        </tr></thead>
        <tbody>
          {providers.map((p: any) => (
            <tr key={p.id} className="border-b border-zinc-800/50">
              <td className="pl-3 py-1.5 text-zinc-300 uppercase">{p.name || p.id}</td>
              <td className="text-right text-zinc-300">{(p.balance ?? 0).toFixed(p.currency === 'USD' ? 2 : 0)} {p.currency ?? 'SEK'}</td>
              <td className="text-right pr-3 text-zinc-500">{p.balance_sek ? `${p.balance_sek.toFixed(0)} SEK` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
