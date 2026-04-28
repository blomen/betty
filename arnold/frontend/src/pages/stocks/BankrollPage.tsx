import { useState, useEffect } from 'react'
import { api } from '@/hooks/useStocksApi'
import type { AccountResponse, PropFirm, PropFirmAccount } from '@/types/stocks'

const PRODUCT_LABELS: Record<string, string> = {
  PRAC: 'Practice',
  '50KTC': '50K Combine',
}

function formatCurrency(value: number | null | undefined, opts?: { decimals?: number }): string {
  if (value == null || Number.isNaN(value)) return '—'
  const decimals = opts?.decimals ?? 0
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

export function BankrollPage() {
  const [data, setData] = useState<AccountResponse | null>(null)

  useEffect(() => {
    const poll = () => {
      api.getAccount().then(setData).catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 10_000)
    return () => clearInterval(iv)
  }, [])

  const propFirms = data?.prop_firms ?? []
  const activeAccount: PropFirmAccount | null =
    propFirms.flatMap(f => f.accounts).find(a => a.active) ?? null

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
            {formatCurrency(activeAccount?.balance, { decimals: 2 })}
          </div>
        </div>
      </div>

      {propFirms.length === 0 ? (
        <EmptyPropFirm />
      ) : (
        propFirms.map(firm => <PropFirmCard key={firm.id} propFirm={firm} />)
      )}
    </div>
  )
}

function EmptyPropFirm() {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-xs font-mono text-zinc-400 uppercase tracking-wider mb-2">Prop Firm</div>
      <div className="text-sm text-zinc-500">
        No prop firm connected. Set <code className="text-zinc-300">STOCKS_AUTONOMOUS=true</code> and configure TopstepX credentials.
      </div>
    </div>
  )
}

function PropFirmCard({ propFirm }: { propFirm: PropFirm }) {
  const accounts = propFirm.accounts.filter(a => a.active)
  return (
    <div className="border-l-2 border-tabTradingBankroll">
      <div className="border border-zinc-800 bg-zinc-900 p-3 space-y-3">
        <div>
          <div className="text-xs font-mono text-zinc-400 uppercase tracking-wider">Prop Firm</div>
          <div className="text-text text-base font-semibold mt-0.5">{propFirm.name}</div>
        </div>
        <div className="space-y-3 pl-3 border-l border-zinc-800">
          {accounts.map(acct => <AccountCard key={acct.id} account={acct} />)}
        </div>
      </div>
    </div>
  )
}

function AccountCard({ account }: { account: PropFirmAccount }) {
  const productLabel = PRODUCT_LABELS[account.product] ?? account.product
  const status = account.can_trade ? 'Active' : 'Disabled'
  const statusColor = account.can_trade ? '#4ade80' : '#ef4444'

  return (
    <div className="space-y-2">
      <div>
        <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">Account</div>
        <div className="text-text font-mono text-sm mt-0.5">{account.name}</div>
        <div className="text-[10px] font-mono text-zinc-500 mt-0.5">
          {productLabel}
          {account.simulated && <span className="ml-2 px-1 py-0.5 border border-zinc-700 text-zinc-400">Simulated</span>}
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <StatCard label="Balance" value={formatCurrency(account.balance, { decimals: 2 })} color="#8b5cf6" />
        <StatCard label="Max Trail DD" value={formatCurrency(account.limits?.max_trailing_dd)} color="#ec4899" />
        <StatCard label="Daily Loss" value={formatCurrency(account.limits?.max_daily_loss)} color="#f97316" />
        <StatCard label="Status" value={status} color={statusColor} />
      </div>
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="border border-zinc-800 bg-zinc-950 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
    </div>
  )
}
