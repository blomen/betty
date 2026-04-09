import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function BankrollPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['bankroll'],
    queryFn: () => apiFetch<any>('/api/bankroll/summary'),
    refetchInterval: 30_000,
  })

  if (isLoading) return <div className="p-4 text-zinc-500">Loading...</div>
  if (!data) return <div className="p-4 text-zinc-600">No bankroll data.</div>

  const providers = data.providers ?? []

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">Bankroll · {providers.length} providers</div>
      <table className="w-full text-xs">
        <thead><tr className="text-zinc-600 text-[10px]">
          <th className="text-left pl-3 py-1">Provider</th>
          <th className="text-right">Balance</th>
          <th className="text-right">Deposited</th>
          <th className="text-right">P&amp;L</th>
          <th className="text-right pr-3">ROI</th>
        </tr></thead>
        <tbody>
          {providers.map((p: any) => (
            <tr key={p.provider_id} className="border-b border-zinc-800/50">
              <td className="pl-3 py-1.5 text-zinc-300 uppercase">{p.provider_id}</td>
              <td className="text-right text-zinc-300">{(p.balance ?? 0).toFixed(0)} kr</td>
              <td className="text-right text-zinc-500">{(p.deposited ?? 0).toFixed(0)} kr</td>
              <td className={`text-right ${(p.profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(p.profit ?? 0) >= 0 ? '+' : ''}{(p.profit ?? 0).toFixed(0)} kr
              </td>
              <td className="text-right pr-3 text-zinc-400">{(p.roi_pct ?? 0).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
