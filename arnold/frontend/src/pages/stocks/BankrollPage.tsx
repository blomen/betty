import { useState, useEffect } from 'react'
import { api } from '@/hooks/useStocksApi'
import type { Account } from '@/types/stocks'

const MAX_LOSS = 4500

export function BankrollPage() {
  const [account, setAccount] = useState<Account | null>(null)

  useEffect(() => {
    const poll = () => {
      api.getAccountInfo().then(setAccount).catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 10_000)
    return () => clearInterval(iv)
  }, [])

  const balance = account?.balance != null ? Number(account.balance) : null
  const buyingPower = account?.buyingPower != null ? Number(account.buyingPower) : null
  const status = account?.canTrade === false ? 'Disabled' : 'Active'
  const statusColor = account?.canTrade === false ? '#ef4444' : '#4ade80'

  return (
    <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabTradingBankroll" />
        Bankroll
      </h2>

      <div className="border-l-2 border-tabTradingBankroll">
        <div className="border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider mb-1">Total Capital</div>
          <div className="text-text text-3xl font-semibold">
            {balance != null ? `$${balance.toLocaleString()}` : '—'}
          </div>
        </div>
      </div>

      <div className="border-l-2 border-tabTradingBankroll">
        <div className="border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-xs font-mono text-zinc-400 uppercase tracking-wider mb-3">Prop Firm</div>
          <div className="grid grid-cols-3 gap-2">
            <StatCard
              label="Buying Power"
              value={buyingPower != null ? `$${buyingPower.toLocaleString()}` : '—'}
              color="#8b5cf6"
            />
            <StatCard
              label="Max DD"
              value={`$${MAX_LOSS.toLocaleString()}`}
              color="#ec4899"
            />
            <StatCard
              label="Status"
              value={status}
              color={statusColor}
              sub={account?.id ? `#${account.id}` : undefined}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, color, sub }: { label: string; value: string; color: string; sub?: string }) {
  return (
    <div className="border border-zinc-800 bg-zinc-950 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
      {sub && <div className="text-[10px] font-mono text-zinc-600 mt-0.5">{sub}</div>}
    </div>
  )
}
