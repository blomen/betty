import { useState, useEffect, useMemo } from 'react'
import { api } from '@/hooks/useStocksApi'
import { TabIcon, TAB_COLORS } from '@/components/TabBar'
import { useProfiles } from '@/hooks/useProfiles'
import type { BrokerTrade } from '@/types/stocks'

// ── Sort types ───────────────────────────────────────────────────────

type SortKey = 'date' | 'side' | 'entry' | 'exit' | 'stop' | 'pnl' | 'r' | 'signal' | 'conf'
type SortDir = 'asc' | 'desc'

function getSortValue(t: BrokerTrade, key: SortKey): number | string {
  switch (key) {
    case 'date': return new Date(t.ts).getTime()
    case 'side': return t.side
    case 'entry': return t.entry_price
    case 'exit': return t.exit_price ?? -9999
    case 'stop': return t.stop_price ?? -9999
    case 'pnl': return t.pnl_dollars ?? -9999
    case 'r': return t.pnl_r ?? -9999
    case 'signal': return t.signal_action ?? ''
    case 'conf': return t.signal_confidence ?? -9999
    default: return 0
  }
}

// ── Charts ───────────────────────────────────────────────────────────

const CHART = { W: 600, H: 200, PL: 8, PR: 44, PT: 8, PB: 24 } as const

function polyChart(
  data: { x: number; y: number }[],
  { yMin, yMax, yFormat }: { yMin: number; yMax: number; yFormat: (v: number) => string },
) {
  const { H, PT, PB } = CHART
  const range = yMax - yMin || 1
  const ySteps = 5
  const yLines = Array.from({ length: ySteps }, (_, i) => {
    const v = yMin + (range * i) / (ySteps - 1)
    const py = PT + (1 - (v - yMin) / range) * (H - PT - PB)
    return { v, py, label: yFormat(v) }
  })
  const pathD = data.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  return { yLines, pathD }
}

function EquityChart({ trades }: { trades: BrokerTrade[] }) {
  const data = useMemo(() => {
    if (trades.length === 0) return []
    const sorted = [...trades].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
    const points: { date: Date; value: number }[] = []
    // Anchor at zero before the first trade so the line starts on the baseline.
    points.push({ date: new Date(sorted[0].ts), value: 0 })
    let cum = 0
    for (const t of sorted) {
      cum += t.pnl_dollars ?? 0
      points.push({ date: new Date(t.ts), value: cum })
    }
    return points
  }, [trades])

  if (data.length < 2) return null

  const { W, H, PL, PR, PT, PB } = CHART

  const values = data.map(d => d.value)
  const rawMin = Math.min(0, ...values)
  const rawMax = Math.max(0, ...values)
  const pad = (rawMax - rawMin) * 0.1 || 50
  const yMin = rawMin - pad
  const yMax = rawMax + pad
  const range = yMax - yMin
  const minDate = data[0].date.getTime()
  const maxDate = data[data.length - 1].date.getTime()
  const dateRange = maxDate - minDate || 1

  const xPos = (d: Date) => PL + (d.getTime() - minDate) / dateRange * (W - PL - PR)
  const yPos = (v: number) => PT + (1 - (v - yMin) / range) * (H - PT - PB)
  const zeroY = yPos(0)

  const pts = data.map(p => ({ x: xPos(p.date), y: yPos(p.value) }))
  const { yLines, pathD } = polyChart(pts, {
    yMin, yMax,
    yFormat: v => `${v >= 0 ? '+' : ''}$${v.toFixed(0)}`,
  })

  const lastVal = data[data.length - 1].value
  const isUp = lastVal >= 0
  const lineColor = isUp ? '#3fb950' : '#f85149'
  const lastPt = pts[pts.length - 1]

  // X labels — months
  const xLabels: { label: string; pos: number }[] = []
  const seen = new Set<string>()
  for (const p of data) {
    const key = `${p.date.getFullYear()}-${p.date.getMonth()}`
    if (!seen.has(key)) {
      seen.add(key)
      xLabels.push({ label: p.date.toLocaleDateString('en-US', { month: 'short' }), pos: xPos(p.date) })
    }
  }

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">Equity</span>
        <div className="flex items-center gap-3">
          <span className={`text-xs font-medium ${isUp ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
            {lastVal >= 0 ? '+' : ''}${lastVal.toFixed(2)}
          </span>
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          {yLines.map((l, i) => (
            <line key={i} x1={PL} y1={l.py} x2={W - PR} y2={l.py} stroke="#21262d" strokeWidth="0.5" strokeDasharray="2,3" vectorEffect="non-scaling-stroke" />
          ))}
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#484f58" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <defs>
            <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.25" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${pathD} L${pts[pts.length - 1].x.toFixed(1)},${zeroY} L${pts[0].x.toFixed(1)},${zeroY} Z`} fill="url(#equityGrad)" />
          <path d={pathD} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <circle cx={lastPt.x} cy={lastPt.y} r="3" fill={lineColor} vectorEffect="non-scaling-stroke" />
          <circle cx={lastPt.x} cy={lastPt.y} r="5" fill={lineColor} fillOpacity="0.15" vectorEffect="non-scaling-stroke" />
        </svg>
        {yLines.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-[#484f58] -translate-y-1/2" style={{ top: `${(l.py / H * 100).toFixed(2)}%`, right: '4px' }}>{l.label}</span>
        ))}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className="absolute text-[10px] text-[#484f58] -translate-x-1/2" style={{ bottom: '2px', left: `${(l.pos / W * 100).toFixed(2)}%` }}>{l.label}</span>
        ))}
      </div>
    </div>
  )
}

function RChart({ trades }: { trades: BrokerTrade[] }) {
  const data = useMemo(() => {
    if (trades.length === 0) return []
    const sorted = [...trades].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
    const points: { date: Date; r: number }[] = []
    points.push({ date: new Date(sorted[0].ts), r: 0 })
    let cum = 0
    for (const t of sorted) {
      cum += t.pnl_r ?? 0
      points.push({ date: new Date(t.ts), r: cum })
    }
    return points
  }, [trades])

  if (data.length < 2) return null

  const { W, H, PL, PR, PT, PB } = CHART
  const minDate = data[0].date.getTime()
  const maxDate = data[data.length - 1].date.getTime()
  const dateRange = maxDate - minDate || 1
  const xPos = (d: Date) => PL + (d.getTime() - minDate) / dateRange * (W - PL - PR)

  const values = data.map(d => d.r)
  const rawMin = Math.min(0, ...values)
  const rawMax = Math.max(0, ...values)
  const pad = Math.max((rawMax - rawMin) * 0.15, 1)
  const yMin = rawMin - pad
  const yMax = rawMax + pad
  const range = yMax - yMin
  const yPos = (v: number) => PT + (1 - (v - yMin) / range) * (H - PT - PB)
  const zeroY = yPos(0)

  const pts = data.map(p => ({ x: xPos(p.date), y: yPos(p.r) }))
  const { yLines, pathD } = polyChart(pts, {
    yMin, yMax,
    yFormat: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}R`,
  })

  const lastVal = data[data.length - 1].r
  const isUp = lastVal >= 0
  const lineColor = isUp ? '#3fb950' : '#f85149'
  const lastPt = pts[pts.length - 1]
  const wins = trades.filter(t => (t.pnl_r ?? 0) > 0).length
  const winPct = trades.length > 0 ? ((wins / trades.length) * 100).toFixed(0) : '0'

  // X labels — months
  const xLabels: { label: string; pos: number }[] = []
  const seen = new Set<string>()
  for (const p of data) {
    const key = `${p.date.getFullYear()}-${p.date.getMonth()}`
    if (!seen.has(key)) {
      seen.add(key)
      xLabels.push({ label: p.date.toLocaleDateString('en-US', { month: 'short' }), pos: xPos(p.date) })
    }
  }

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">R Trend</span>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-[#484f58]">{trades.length} trades</span>
          <span className="text-[10px] text-[#484f58]">{winPct}% wins</span>
          <span className={`text-sm font-semibold ${isUp ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
            {lastVal >= 0 ? '+' : ''}{lastVal.toFixed(2)}R
          </span>
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          {yLines.map((l, i) => (
            <line key={i} x1={PL} y1={l.py} x2={W - PR} y2={l.py} stroke="#21262d" strokeWidth="0.5" strokeDasharray="2,3" vectorEffect="non-scaling-stroke" />
          ))}
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#484f58" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <defs>
            <linearGradient id="rGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.25" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${pathD} L${pts[pts.length - 1].x.toFixed(1)},${zeroY} L${pts[0].x.toFixed(1)},${zeroY} Z`} fill="url(#rGrad)" />
          <path d={pathD} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          <circle cx={lastPt.x} cy={lastPt.y} r="3" fill={lineColor} vectorEffect="non-scaling-stroke" />
          <circle cx={lastPt.x} cy={lastPt.y} r="5" fill={lineColor} fillOpacity="0.15" vectorEffect="non-scaling-stroke" />
        </svg>
        <span className="absolute text-[10px] text-[#8b949e] font-medium -translate-y-1/2" style={{ top: `${(zeroY / H * 100).toFixed(2)}%`, right: '4px' }}>0R</span>
        {yLines.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-[#484f58] -translate-y-1/2" style={{ top: `${(l.py / H * 100).toFixed(2)}%`, right: '4px' }}>{l.label}</span>
        ))}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className="absolute text-[10px] text-[#484f58] -translate-x-1/2" style={{ bottom: '2px', left: `${(l.pos / W * 100).toFixed(2)}%` }}>{l.label}</span>
        ))}
      </div>
    </div>
  )
}

// ── Sortable header ──────────────────────────────────────────────────

function SortHeader({ label, sortKey, currentSort, onSort, align = 'left' }: {
  label: string
  sortKey: SortKey
  currentSort: { key: SortKey; dir: SortDir } | null
  onSort: (key: SortKey) => void
  align?: 'left' | 'right'
}) {
  const active = currentSort?.key === sortKey
  const dir = active ? currentSort.dir : null

  return (
    <th
      className={`cursor-pointer select-none hover:text-text transition-colors ${align === 'right' ? 'text-right' : ''}`}
      onClick={() => onSort(sortKey)}
    >
      <span className="inline-flex items-center gap-1">
        {align === 'right' && (
          <span className={`text-[8px] ${active ? 'text-accent' : 'text-muted2/50'}`}>
            {dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : '⇅'}
          </span>
        )}
        {label}
        {align === 'left' && (
          <span className={`text-[8px] ${active ? 'text-accent' : 'text-muted2/50'}`}>
            {dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : '⇅'}
          </span>
        )}
      </span>
    </th>
  )
}

// ── Stats ───────────────────────────────────────────────────────────

function computeStats(trades: BrokerTrade[]) {
  const empty = {
    winRate: 0, profitFactor: 0, totalPnL: 0, tradeCount: 0, totalR: 0, avgR: 0,
    avgWin: 0, avgLoss: 0, bestTrade: null as number | null, worstTrade: null as number | null,
    avgConfidence: 0, longCount: 0, shortCount: 0, wins: 0, losses: 0, breakeven: 0,
    breakevenWinRate: 0,
  }
  if (trades.length === 0) return empty

  let wins = 0, losses = 0, breakeven = 0
  let grossProfit = 0, grossLoss = 0, totalPnL = 0, totalR = 0
  let bestTrade: number | null = null, worstTrade: number | null = null
  let confSum = 0, confCount = 0, longCount = 0, shortCount = 0

  for (const t of trades) {
    const pnl = t.pnl_dollars ?? 0
    totalPnL += pnl
    totalR += t.pnl_r ?? 0

    if (pnl > 0) { wins++; grossProfit += pnl }
    else if (pnl < 0) { losses++; grossLoss += Math.abs(pnl) }
    else { breakeven++ }

    if (bestTrade === null || pnl > bestTrade) bestTrade = pnl
    if (worstTrade === null || pnl < worstTrade) worstTrade = pnl

    if (t.signal_confidence != null && t.signal_confidence > 0) {
      confSum += t.signal_confidence; confCount++
    }
    if (t.side === 'long') longCount++
    else shortCount++
  }

  const tradeCount = wins + losses + breakeven
  const decided = wins + losses
  const winRate = decided > 0 ? (wins / decided) * 100 : 0
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0
  const avgR = tradeCount > 0 ? totalR / tradeCount : 0
  const avgWin = wins > 0 ? grossProfit / wins : 0
  const avgLoss = losses > 0 ? grossLoss / losses : 0
  const avgConfidence = confCount > 0 ? confSum / confCount : 0

  // Breakeven win rate: the win rate at which expectancy = 0 given current avgWin/avgLoss.
  // E.g. 1:2 R:R needs 33%, 1:3 needs 25%. Used as a context anchor for "is our win rate good enough".
  const breakevenWinRate = avgWin > 0 && avgLoss > 0 ? avgLoss / (avgWin + avgLoss) : 0

  return { winRate, profitFactor, totalPnL, tradeCount, totalR, avgR, avgWin, avgLoss, bestTrade, worstTrade, avgConfidence, longCount, shortCount, wins, losses, breakeven, breakevenWinRate }
}

// ── Main page ───────────────────────────────────────────────────────

export function StatsPage() {
  const { activeProfile } = useProfiles()
  const [trades, setTrades] = useState<BrokerTrade[]>([])
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [historyCollapsed, setHistoryCollapsed] = useState(false)

  useEffect(() => {
    // No profile = no scoped view. Backend will return 403/empty regardless,
    // but skipping the fetch keeps the empty-state clean.
    if (!activeProfile) {
      setTrades([])
      setLoading(false)
      return
    }
    setLoading(true)
    api.getBrokerTrades(days)
      .then(d => setTrades((d.trades ?? []).filter(t => t.closed_at != null)))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [days, activeProfile?.id])

  if (!activeProfile) {
    return (
      <div className="space-y-3 min-w-0 overflow-y-auto overflow-x-hidden flex-1 min-h-0">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text flex items-center gap-2">
            <TabIcon name="stats" color={TAB_COLORS.stats} size={16} />
            Stats
          </h2>
        </div>
        <div className="border border-border bg-panel text-muted text-sm py-12 text-center">
          Select a profile to view trading stats.
          <div className="text-muted2 text-[11px] mt-1">
            Stats are scoped per profile so multiple TopstepX accounts stay isolated.
          </div>
        </div>
      </div>
    )
  }

  const stats = useMemo(() => computeStats(trades), [trades])

  const historyTrades = useMemo(() => {
    let result = trades

    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result.filter(t =>
        t.side.toLowerCase().includes(q) ||
        (t.signal_action ?? '').toLowerCase().includes(q) ||
        t.symbol.toLowerCase().includes(q)
      )
    }

    if (sort) {
      result = [...result].sort((a, b) => {
        const va = getSortValue(a, sort.key)
        const vb = getSortValue(b, sort.key)
        let cmp = 0
        if (typeof va === 'string' && typeof vb === 'string') cmp = va.localeCompare(vb)
        else cmp = (va as number) - (vb as number)
        return sort.dir === 'desc' ? -cmp : cmp
      })
    } else {
      result = [...result].sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime())
    }

    return result
  }, [trades, sort, search])

  const handleSort = (key: SortKey) => {
    setSort(prev => {
      if (prev?.key === key) {
        if (prev.dir === 'asc') return { key, dir: 'desc' }
        if (prev.dir === 'desc') return null
        return { key, dir: 'asc' }
      }
      const textCols: SortKey[] = ['side', 'signal', 'date']
      return { key, dir: textCols.includes(key) ? 'asc' : 'desc' }
    })
    setExpandedId(null)
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  const pfText = stats.profitFactor === Infinity ? '∞' : stats.profitFactor.toFixed(2)

  return (
    <div className="space-y-3 min-w-0 overflow-y-auto overflow-x-hidden flex-1 min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="stats" color={TAB_COLORS.stats} size={16} />
          Stats
        </h2>
        <div className="flex items-center gap-2">
          {[7, 30, 90].map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2 py-1 text-[10px] font-mono uppercase border ${
                days === d ? 'border-accent text-accent' : 'border-border text-muted2 hover:text-text'
              }`}
            >
              {d}d
            </button>
          ))}
          <input
            type="text"
            placeholder="Search side, signal..."
            className="px-2 py-1 text-xs bg-bg border border-border text-text placeholder:text-muted2 w-48 focus:border-tabBets focus:outline-none"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Stats Summary */}
      <div className="border-l-2 border-tabBets">
        <div className="grid grid-cols-4 gap-px bg-border border border-border">
          <div className="bg-panel2 px-3 py-2.5">
            <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Trades</div>
            <div className="text-text text-lg font-semibold">{stats.tradeCount}</div>
            <div className="flex items-center gap-2 text-[10px]">
              <span className="text-success">{stats.wins}W</span>
              <span className="text-error">{stats.losses}L</span>
              {stats.breakeven > 0 && <span className="text-muted">{stats.breakeven}B</span>}
            </div>
          </div>
          <div className="bg-panel2 px-3 py-2.5">
            <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Win Rate</div>
            <div className={`text-lg font-semibold ${stats.tradeCount === 0 ? 'text-muted' : (stats.profitFactor >= 1 ? 'text-success' : 'text-error')}`} title="Colored by profit factor — a low win rate is fine when avg win > avg loss">
              {stats.tradeCount > 0 ? `${stats.winRate.toFixed(1)}%` : '-'}
              {stats.tradeCount > 0 && (
                <span className="text-[10px] text-muted ml-1.5 font-normal">be {stats.tradeCount > 0 ? `${(stats.breakevenWinRate * 100).toFixed(0)}%` : '-'}</span>
              )}
            </div>
            <div className="text-[10px] text-muted">PF <span className={stats.profitFactor >= 1 ? 'text-success' : 'text-error'}>{stats.tradeCount > 0 ? pfText : '-'}</span></div>
          </div>
          <div className="bg-panel2 px-3 py-2.5">
            <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Net P&L</div>
            <div className={`text-lg font-semibold ${stats.tradeCount === 0 ? 'text-muted' : (stats.totalPnL >= 0 ? 'text-success' : 'text-error')}`}>
              {stats.totalPnL >= 0 ? '+' : ''}${stats.totalPnL.toFixed(0)}
            </div>
            <div className="text-[10px] text-muted">
              best <span className="text-success">${(stats.bestTrade ?? 0).toFixed(0)}</span> /
              worst <span className="text-error">${(stats.worstTrade ?? 0).toFixed(0)}</span>
            </div>
          </div>
          <div className="bg-panel2 px-3 py-2.5">
            <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Total R</div>
            <div className={`text-lg font-semibold ${stats.tradeCount === 0 ? 'text-muted' : (stats.totalR >= 0 ? 'text-success' : 'text-error')}`}>
              {stats.totalR >= 0 ? '+' : ''}{stats.totalR.toFixed(2)}R
            </div>
            <div className="text-[10px] text-muted">avg <span className={stats.avgR >= 0 ? 'text-success' : 'text-error'}>{stats.tradeCount > 0 ? `${stats.avgR >= 0 ? '+' : ''}${stats.avgR.toFixed(2)}R` : '-'}</span></div>
          </div>
        </div>
        {/* Context row */}
        <div className="flex items-center gap-4 px-3 py-1.5 bg-panel2 border border-border border-t-0 text-[10px]">
          <span className="text-muted">Avg win: <span className="text-success">{stats.avgWin > 0 ? `$${stats.avgWin.toFixed(2)}` : '-'}</span></span>
          <span className="text-muted">Avg loss: <span className="text-error">{stats.avgLoss > 0 ? `-$${stats.avgLoss.toFixed(2)}` : '-'}</span></span>
          <span className="text-muted">Avg conf: <span className="text-text">{stats.avgConfidence > 0 ? `${(stats.avgConfidence * 100).toFixed(0)}%` : '-'}</span></span>
          <span className="text-muted">Long/Short: <span className="text-text">{stats.longCount}/{stats.shortCount}</span></span>
        </div>
      </div>

      {/* Charts — side by side */}
      <div>
        <div className="grid grid-cols-2 gap-[1px] bg-[#161b22]">
          <EquityChart trades={trades} />
          <RChart trades={trades} />
        </div>
      </div>

      {/* Trade History */}
      <button
        className="flex items-center gap-2 w-full text-left cursor-pointer group"
        onClick={() => setHistoryCollapsed(c => !c)}
      >
        <span className={`text-[10px] text-muted2 transition-transform ${historyCollapsed ? '' : 'rotate-90'}`}>▶</span>
        <h3 className="text-xs text-muted uppercase tracking-wider font-semibold group-hover:text-text transition-colors">
          Trade History <span className="text-muted2">{historyTrades.length}</span>
        </h3>
      </button>
      {!historyCollapsed && (loading && trades.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
      ) : historyTrades.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          {search.trim() ? 'No matching trades.' : 'No trades found.'}
        </div>
      ) : (
        <div className="border-l-2 border-tabBets">
          <table className="sq">
            <thead>
              <tr>
                <SortHeader label="Date" sortKey="date" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Side" sortKey="side" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Entry" sortKey="entry" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Exit" sortKey="exit" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Stop" sortKey="stop" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="P&L $" sortKey="pnl" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="P&L R" sortKey="r" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Signal" sortKey="signal" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Conf" sortKey="conf" currentSort={sort} onSort={handleSort} align="right" />
              </tr>
            </thead>
            <tbody>
              {historyTrades.map(t => {
                const pnl = t.pnl_dollars ?? 0
                const r = t.pnl_r ?? 0
                const isExpanded = expandedId === t.id
                return (
                  <>
                    <tr
                      key={t.id}
                      className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`}
                      onClick={() => setExpandedId(isExpanded ? null : t.id)}
                    >
                      <td className="text-muted text-[11px] whitespace-nowrap">{formatDate(t.ts)}</td>
                      <td className={t.side === 'long' ? 'text-success text-sm' : 'text-error text-sm'}>
                        {t.side === 'long' ? 'Long' : 'Short'}
                      </td>
                      <td className="text-right text-text text-sm font-medium">{t.entry_price.toFixed(2)}</td>
                      <td className="text-right text-text text-sm">{t.exit_price?.toFixed(2) ?? '-'}</td>
                      <td className="text-right text-muted text-sm">{t.stop_price?.toFixed(2) ?? '-'}</td>
                      <td className="text-right">
                        <span className={`text-sm font-medium ${pnl >= 0 ? 'text-success' : 'text-error'}`}>
                          {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                        </span>
                      </td>
                      <td className="text-right">
                        <span className={`text-sm font-medium ${r >= 0 ? 'text-success' : 'text-error'}`}>
                          {r >= 0 ? '+' : ''}{r.toFixed(2)}R
                        </span>
                      </td>
                      <td className="text-text text-[11px]">{t.signal_action?.replace('enter_', '') ?? '-'}</td>
                      <td className="text-right text-muted text-[11px]">
                        {t.signal_confidence ? `${(t.signal_confidence * 100).toFixed(0)}%` : '-'}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${t.id}-expanded`}>
                        <td colSpan={9} className="!p-0">
                          <div className="px-3 py-2 bg-panel space-y-1">
                            <div className="flex items-center gap-6 text-xs text-muted flex-wrap">
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Symbol: </span>
                                <span className="text-text">{t.symbol}</span>
                              </div>
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Size: </span>
                                <span className="text-text">{t.size}</span>
                              </div>
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Session: </span>
                                <span className="text-text">{t.session_date}</span>
                              </div>
                              {t.closed_at && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">Closed: </span>
                                  <span className="text-text">{formatDate(t.closed_at)}</span>
                                </div>
                              )}
                              {t.signal_zone != null && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">Zone: </span>
                                  <span className="text-text">{t.signal_zone}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}
