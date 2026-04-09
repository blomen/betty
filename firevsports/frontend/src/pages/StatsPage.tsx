import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function StatsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch<any>('/api/bets/stats'),
    refetchInterval: 60_000,
  })

  if (isLoading) return <div className="p-4 text-zinc-500">Loading...</div>
  if (!data) return <div className="p-4 text-zinc-600">No stats data.</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto p-4">
      <div className="grid grid-cols-4 gap-4 mb-6">
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">Total Bets</div>
          <div className="text-xl font-bold text-zinc-200">{data.total_bets ?? 0}</div>
        </div>
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">Win Rate</div>
          <div className="text-xl font-bold text-green-400">{(data.win_rate ?? 0).toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">ROI</div>
          <div className={`text-xl font-bold ${(data.roi ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {(data.roi ?? 0) >= 0 ? '+' : ''}{(data.roi ?? 0).toFixed(1)}%
          </div>
        </div>
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">Profit</div>
          <div className={`text-xl font-bold ${(data.total_profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {(data.total_profit ?? 0) >= 0 ? '+' : ''}{(data.total_profit ?? 0).toFixed(0)} kr
          </div>
        </div>
      </div>
    </div>
  )
}
