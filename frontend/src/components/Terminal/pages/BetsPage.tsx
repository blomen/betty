import { useState, useEffect, useCallback, useMemo } from 'react';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useBetMutations } from '@/hooks/useBetMutations';
import { api } from '@/services/api';
import { displayTeamName } from '@/utils/formatters';
import { resolveOutcome as resolveOutcomeBase, fmtAmount, fmtProfit } from '@/utils/betting';
import { ProviderName } from '../ProviderName';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Bet, BankrollStats, BonusProgressEntry } from '@/types';

// ── Helpers (outside component to avoid re-creation) ─────────────────

/** Time-to-kickoff in hours from PLACED time (for history CLV confidence) */
function getTTK(bet: Bet): number | null {
  if (!bet.start_time || !bet.placed_at) return null;
  const start = new Date(bet.start_time).getTime();
  const placed = new Date(bet.placed_at).getTime();
  return Math.max(0, (start - placed) / (1000 * 60 * 60));
}

function formatTTK(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

type TTKConfidence = 'high' | 'good' | 'medium' | 'low' | 'very_low' | 'unknown';

function getTTKTier(ttkHours: number | null): { label: string; color: string; confidence: TTKConfidence } {
  if (ttkHours === null) return { label: '-', color: 'text-muted', confidence: 'unknown' };
  if (ttkHours <= 6) return { label: formatTTK(ttkHours), color: 'text-success', confidence: 'high' };
  if (ttkHours <= 12) return { label: formatTTK(ttkHours), color: 'text-yellow', confidence: 'good' };
  if (ttkHours <= 24) return { label: formatTTK(ttkHours), color: 'text-warning', confidence: 'medium' };
  if (ttkHours <= 48) return { label: formatTTK(ttkHours), color: 'text-error', confidence: 'low' };
  return { label: formatTTK(ttkHours), color: 'text-muted2', confidence: 'very_low' };
}

const CLV_BADGE: Record<TTKConfidence, { text: string; cls: string }> = {
  high: { text: 'CLV HIGH', cls: 'bg-success/15 text-success' },
  good: { text: 'CLV GOOD', cls: 'bg-yellow/15 text-yellow' },
  medium: { text: 'CLV MED', cls: 'bg-warning/15 text-warning' },
  low: { text: 'CLV LOW', cls: 'bg-error/15 text-error' },
  very_low: { text: 'CLV ~', cls: 'bg-muted2/15 text-muted2' },
  unknown: { text: '-', cls: 'text-muted2' },
};

// ── Sort types ───────────────────────────────────────────────────────

/** Exchange rates to SEK for aggregation. */
const RATE_TO_SEK: Record<string, number> = { USD: 10.50, USDC: 10.50, SEK: 1 };

/** Convert an amount from bet's native currency to SEK. */
function toSEK(amount: number, currency: string): number {
  return amount * (RATE_TO_SEK[currency] ?? 1);
}

type SortKey = 'date' | 'provider' | 'odds' | 'close' | 'clv' | 'edge' | 'stake' | 'profit' | 'prob' | 'ttk' | 'status';
type SortDir = 'asc' | 'desc';

function getSortValue(bet: Bet, key: SortKey): number | string {
  switch (key) {
    case 'date': return new Date(bet.placed_at).getTime();
    case 'provider': return bet.provider;
    case 'odds': return bet.odds;
    case 'close': return bet.closing_odds ?? -9999;
    case 'clv': return bet.clv_pct ?? -9999;
    case 'edge': return bet.placed_edge_pct ?? -9999;
    case 'stake': return bet.stake;
    case 'profit': return bet.profit;
    case 'prob': return bet.selection_probability ?? -9999;
    case 'ttk': return getTTK(bet) ?? 99999;
    case 'status': {
      const order: Record<string, number> = { pending: 0, won: 1, lost: 2, void: 3 };
      return order[bet.result] ?? 4;
    }
    default: return 0;
  }
}

// ── Charts ───────────────────────────────────────────────────────────

// Shared chart layout — Polymarket style
const CHART = { W: 600, H: 200, PL: 8, PR: 44, PT: 8, PB: 24 } as const;

function polyChart(
  data: { x: number; y: number }[],
  { yMin, yMax, yFormat }: { yMin: number; yMax: number; yFormat: (v: number) => string },
) {
  const { H, PT, PB } = CHART;
  const range = yMax - yMin || 1;

  // 4-5 nice Y grid lines
  const ySteps = 5;
  const yLines = Array.from({ length: ySteps }, (_, i) => {
    const v = yMin + (range * i) / (ySteps - 1);
    const py = PT + (1 - (v - yMin) / range) * (H - PT - PB);
    return { v, py, label: yFormat(v) };
  });

  // Line path — simple linear segments
  const pathD = data.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

  return { yLines, pathD };
}

function BankrollChart({ bets, netDeposited, totalStaked }: { bets: Bet[]; netDeposited: number; totalStaked?: number }) {
  const data = useMemo(() => {
    const settled = bets
      .filter(b => b.result !== 'pending')
      .sort((a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime());
    if (settled.length === 0) return [];

    const startBankroll = netDeposited;

    // Aggregate by day
    const dailyProfit = new Map<string, { date: Date; profit: number }>();
    for (const bet of settled) {
      const d = new Date(bet.placed_at);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      const existing = dailyProfit.get(key);
      if (existing) {
        existing.profit += toSEK(bet.profit, bet.currency);
      } else {
        dailyProfit.set(key, { date: d, profit: toSEK(bet.profit, bet.currency) });
      }
    }

    let cumulative = startBankroll;
    const points = [{ date: new Date(settled[0].placed_at), value: startBankroll }];
    for (const day of dailyProfit.values()) {
      cumulative += day.profit;
      points.push({ date: day.date, value: cumulative });
    }
    return points;
  }, [bets, netDeposited]);

  if (data.length < 2) return null;

  const { W, H, PL, PR, PT, PB } = CHART;

  const values = data.map(d => d.value);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const pad = (rawMax - rawMin) * 0.1 || 200;
  const yMin = rawMin - pad;
  const yMax = rawMax + pad;
  const range = yMax - yMin;
  const minDate = data[0].date.getTime();
  const maxDate = data[data.length - 1].date.getTime();
  const dateRange = maxDate - minDate || 1;

  const xPos = (d: Date) => PL + (d.getTime() - minDate) / dateRange * (W - PL - PR);
  const yPos = (v: number) => PT + (1 - (v - yMin) / range) * (H - PT - PB);

  const pts = data.map(p => ({ x: xPos(p.date), y: yPos(p.value) }));
  const { yLines, pathD } = polyChart(pts, {
    yMin, yMax,
    yFormat: v => `${(v / 1000).toFixed(1)}k`,
  });

  const lastVal = data[data.length - 1].value;
  const firstVal = data[0].value;
  const isUp = lastVal >= firstVal;
  const lineColor = isUp ? '#3fb950' : '#f85149';

  // X labels — months
  const xLabels: { label: string; pos: number }[] = [];
  const seen = new Set<string>();
  for (const p of data) {
    const key = `${p.date.getFullYear()}-${p.date.getMonth()}`;
    if (!seen.has(key)) {
      seen.add(key);
      xLabels.push({ label: p.date.toLocaleDateString('en-US', { month: 'short' }), pos: xPos(p.date) });
    }
  }

  const profit = lastVal - firstVal;
  const roiBase = totalStaked && totalStaked > 0 ? totalStaked : firstVal;
  const profitPct = ((profit / roiBase) * 100).toFixed(1);
  const lastPt = pts[pts.length - 1];

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">Bankroll</span>
        <div className="flex items-center gap-3">
          <span className={`text-xs font-medium ${isUp ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
            {profit >= 0 ? '+' : ''}{profit.toFixed(0)} kr ({profit >= 0 ? '+' : ''}{profitPct}%)
          </span>
          <span className="text-sm font-semibold text-[#e6edf3]">
            {lastVal.toFixed(0)} kr
          </span>
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          {/* Dotted horizontal grid lines */}
          {yLines.map((l, i) => (
            <line key={i} x1={PL} y1={l.py} x2={W - PR} y2={l.py} stroke="#21262d" strokeWidth="0.5" strokeDasharray="2,3" />
          ))}
          {/* Gradient fill under line */}
          <defs>
            <linearGradient id="bankrollGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.25" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${pathD} L${pts[pts.length - 1].x.toFixed(1)},${H - PB} L${pts[0].x.toFixed(1)},${H - PB} Z`} fill="url(#bankrollGrad)" />
          {/* Main line */}
          <path d={pathD} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          {/* Endpoint dot */}
          <circle cx={lastPt.x} cy={lastPt.y} r="3" fill={lineColor} />
          <circle cx={lastPt.x} cy={lastPt.y} r="5" fill={lineColor} fillOpacity="0.15" />
        </svg>
        {/* Y labels — right side */}
        {yLines.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-[#484f58] -translate-y-1/2" style={{ top: `${(l.py / H * 100).toFixed(2)}%`, right: '4px' }}>{l.label}</span>
        ))}
        {/* X labels */}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className="absolute text-[10px] text-[#484f58] -translate-x-1/2" style={{ bottom: '2px', left: `${(l.pos / W * 100).toFixed(2)}%` }}>{l.label}</span>
        ))}
      </div>
    </div>
  );
}

export function CLVChart({ bets }: { bets: Bet[]; showTTKLegend?: boolean }) {
  const data = useMemo(() => {
    return bets
      .filter(b => b.result !== 'pending' && b.clv_pct != null)
      .sort((a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime())
      .map(b => ({ date: new Date(b.placed_at), clv: b.clv_pct! }));
  }, [bets]);

  if (data.length < 2) return null;

  const { W, H, PL, PR, PT, PB } = CHART;

  const minDate = data[0].date.getTime();
  const maxDate = data[data.length - 1].date.getTime();
  const dateRange = maxDate - minDate || 1;

  const xPos = (d: Date) => PL + (d.getTime() - minDate) / dateRange * (W - PL - PR);

  // Cumulative average (running mean of ALL bets up to each point)
  const cumAvgAll: { date: Date; avg: number }[] = [];
  let cumSum = 0;
  for (let i = 0; i < data.length; i++) {
    cumSum += data[i].clv;
    cumAvgAll.push({ date: data[i].date, avg: cumSum / (i + 1) });
  }

  // Downsample to ~80 points for a smooth line
  const maxPts = 80;
  const step = Math.max(1, Math.floor(cumAvgAll.length / maxPts));
  const avgPoints: { date: Date; avg: number }[] = [];
  for (let i = 0; i < cumAvgAll.length; i += step) {
    avgPoints.push(cumAvgAll[i]);
  }
  // Always include the last point
  if (avgPoints[avgPoints.length - 1] !== cumAvgAll[cumAvgAll.length - 1]) {
    avgPoints.push(cumAvgAll[cumAvgAll.length - 1]);
  }

  // Dynamic Y range from rolling avg data
  const avgVals = avgPoints.map(p => p.avg);
  const rawMin = Math.min(...avgVals);
  const rawMax = Math.max(...avgVals);
  const pad = Math.max((rawMax - rawMin) * 0.2, 5);
  const yMin = Math.min(rawMin - pad, -5);
  const yMax = Math.max(rawMax + pad, 5);
  const range = yMax - yMin;

  const yPos = (v: number) => PT + (1 - (v - yMin) / range) * (H - PT - PB);
  const zeroY = yPos(0);

  const clvPts = avgPoints.map(p => ({ x: xPos(p.date), y: yPos(p.avg) }));
  const { yLines, pathD: avgPathD } = polyChart(clvPts, {
    yMin, yMax,
    yFormat: v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`,
  });

  const totalAvg = data.reduce((s, d) => s + d.clv, 0) / data.length;
  const isPositive = totalAvg >= 0;
  const positiveCount = data.filter(d => d.clv >= 0).length;
  const beatPct = ((positiveCount / data.length) * 100).toFixed(0);

  // X labels — months
  const xLabels: { label: string; pos: number }[] = [];
  const seen = new Set<string>();
  for (const d of data) {
    const key = `${d.date.getFullYear()}-${d.date.getMonth()}`;
    if (!seen.has(key)) {
      seen.add(key);
      xLabels.push({ label: d.date.toLocaleDateString('en-US', { month: 'short' }), pos: xPos(d.date) });
    }
  }

  const lastPt = clvPts[clvPts.length - 1];
  const lineColor = isPositive ? '#3fb950' : '#f85149';

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">CLV Trend</span>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-[#484f58]">{data.length} bets</span>
          <span className="text-[10px] text-[#484f58]">{beatPct}% beat close</span>
          <span className={`text-sm font-semibold ${isPositive ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
            {totalAvg >= 0 ? '+' : ''}{totalAvg.toFixed(1)}% avg
          </span>
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          {/* Dotted horizontal grid lines */}
          {yLines.map((l, i) => (
            <line key={i} x1={PL} y1={l.py} x2={W - PR} y2={l.py} stroke="#21262d" strokeWidth="0.5" strokeDasharray="2,3" />
          ))}
          {/* Zero line — clear and prominent */}
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#484f58" strokeWidth="1" />
          {/* Gradient fill under line */}
          <defs>
            <linearGradient id="clvGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.25" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${avgPathD} L${clvPts[clvPts.length - 1].x.toFixed(1)},${H - PB} L${clvPts[0].x.toFixed(1)},${H - PB} Z`} fill="url(#clvGrad)" />
          {/* Main line */}
          <path d={avgPathD} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          {/* Endpoint dot */}
          <circle cx={lastPt.x} cy={lastPt.y} r="3" fill={lineColor} />
          <circle cx={lastPt.x} cy={lastPt.y} r="5" fill={lineColor} fillOpacity="0.15" />
        </svg>
        {/* Zero label */}
        <span className="absolute text-[10px] text-[#8b949e] font-medium -translate-y-1/2" style={{ top: `${(zeroY / H * 100).toFixed(2)}%`, right: '4px' }}>0%</span>
        {/* Y labels — right side */}
        {yLines.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-[#484f58] -translate-y-1/2" style={{ top: `${(l.py / H * 100).toFixed(2)}%`, right: '4px' }}>{l.label}</span>
        ))}
        {/* X labels */}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className="absolute text-[10px] text-[#484f58] -translate-x-1/2" style={{ bottom: '2px', left: `${(l.pos / W * 100).toFixed(2)}%` }}>{l.label}</span>
        ))}
      </div>
    </div>
  );
}

// ── Sortable header ──────────────────────────────────────────────────

function SortHeader({ label, sortKey, currentSort, onSort, align = 'left' }: {
  label: string;
  sortKey: SortKey;
  currentSort: { key: SortKey; dir: SortDir } | null;
  onSort: (key: SortKey) => void;
  align?: 'left' | 'right';
}) {
  const active = currentSort?.key === sortKey;
  const dir = active ? currentSort.dir : null;

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
  );
}

// ── Main page — History only ────────────────────────────────────────

export function BetsPage() {
  const { editBet } = useBetMutations();
  const [bets, setBets] = useState<Bet[]>([]);
  const [bankrollStats, setBankrollStats] = useState<BankrollStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeBonuses, setActiveBonuses] = useState<[string, BonusProgressEntry][]>([]);
  // Sort & search (for history table)
  const [sort, setSort] = usePersistedState<{ key: SortKey; dir: SortDir } | null>('bbq_bets_sort', null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [search, setSearch] = usePersistedState('bbq_bets_search', '');

  // Inline editing state
  const [editingBetId, setEditingBetId] = useState<number | null>(null);
  const [editStake, setEditStake] = useState<string>('');
  const [editOdds, setEditOdds] = useState<string>('');
  const [editResult, setEditResult] = useState<string>('');

  // Cashout state
  const [cashoutBetId, setCashoutBetId] = useState<number | null>(null);
  const [cashoutAmount, setCashoutAmount] = useState<string>('');

  // Collapsed states
  const [wageringCollapsed, setWageringCollapsed] = usePersistedState('bbq_bets_wageringCollapsed', true);
  const [historyCollapsed, setHistoryCollapsed] = usePersistedState('bbq_bets_historyCollapsed', true);

  const fetchBets = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await api.getBets(undefined, 500);
      setBets(response.bets);
    } catch (err) {
      console.error('Failed to fetch bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const statsData = await api.getBankrollStats();
      setBankrollStats(statsData);
    } catch {
      // Stats are supplementary
    }
  }, []);

  const fetchBonuses = useCallback(async () => {
    try {
      const status = await api.getBankrollStatus();
      const active = Object.entries(status.bonus_progress).filter(
        ([, b]) => b.status !== 'available' && b.status !== 'completed'
          && b.wagering_requirement > 0
      );
      setActiveBonuses(active);
    } catch {
      // Silently ignore
    }
  }, []);

  useEffect(() => { fetchBets(); fetchStats(); fetchBonuses(); }, [fetchBets, fetchStats, fetchBonuses]);

  // ── History (settled bets only) ─────────────────────────────────

  const historyBets = useMemo(() => {
    let result = bets.filter(b => b.result !== 'pending');

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(b =>
        (b.home_team && b.home_team.toLowerCase().includes(q)) ||
        (b.away_team && b.away_team.toLowerCase().includes(q)) ||
        (b.display_home && b.display_home.toLowerCase().includes(q)) ||
        (b.display_away && b.display_away.toLowerCase().includes(q)) ||
        b.provider.toLowerCase().includes(q) ||
        (b.sport && b.sport.toLowerCase().includes(q)) ||
        (b.league && b.league.toLowerCase().includes(q))
      );
    }

    if (sort) {
      result = [...result].sort((a, b) => {
        const va = getSortValue(a, sort.key);
        const vb = getSortValue(b, sort.key);
        let cmp = 0;
        if (typeof va === 'string' && typeof vb === 'string') cmp = va.localeCompare(vb);
        else cmp = (va as number) - (vb as number);
        return sort.dir === 'desc' ? -cmp : cmp;
      });
    } else {
      result = [...result].sort((a, b) => new Date(b.placed_at).getTime() - new Date(a.placed_at).getTime());
    }

    return result;
  }, [bets, sort, search]);

  const handleSort = (key: SortKey) => {
    setSort(prev => {
      if (prev?.key === key) {
        if (prev.dir === 'asc') return { key, dir: 'desc' };
        if (prev.dir === 'desc') return null;
        return { key, dir: 'asc' };
      }
      const textCols: SortKey[] = ['provider', 'status', 'date'];
      return { key, dir: textCols.includes(key) ? 'asc' : 'desc' };
    });
    setExpandedIdx(null);
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const getStatusColor = (result: Bet['result']) => {
    switch (result) {
      case 'won': return 'text-success';
      case 'lost': return 'text-error';
      case 'void': return 'text-muted';
      default: return 'text-accent';
    }
  };

  const resolveOutcome = (bet: Bet): string =>
    resolveOutcomeBase(bet.outcome || '-', bet, bet.point);

  const startEditing = (bet: Bet) => {
    setEditingBetId(bet.id);
    setEditStake(bet.stake.toFixed(0));
    setEditOdds(bet.odds.toFixed(2));
    setEditResult(bet.result);
  };

  const cancelEditing = () => {
    setEditingBetId(null);
    setEditStake('');
    setEditOdds('');
    setEditResult('');
  };

  const saveEdit = async (betId: number) => {
    const original = bets.find(b => b.id === betId);
    if (!original) return;

    const changes: { stake?: number; odds?: number; result?: string } = {};
    const newStake = parseFloat(editStake);
    const newOdds = parseFloat(editOdds);
    if (!isNaN(newStake) && newStake !== original.stake) changes.stake = newStake;
    if (!isNaN(newOdds) && newOdds !== original.odds) changes.odds = newOdds;
    if (editResult && editResult !== original.result) changes.result = editResult;

    if (Object.keys(changes).length === 0) {
      cancelEditing();
      return;
    }

    try {
      await editBet.mutateAsync({ betId, data: changes });
      cancelEditing();
      fetchBets();
      fetchStats();
    } catch (err) {
      console.error('Edit bet failed:', err);
    }
  };

  const startCashout = (bet: Bet) => {
    setCashoutBetId(bet.id);
    setCashoutAmount('');
  };

  const cancelCashout = () => {
    setCashoutBetId(null);
    setCashoutAmount('');
  };

  const confirmCashout = async (betId: number) => {
    const amount = parseFloat(cashoutAmount);
    if (isNaN(amount) || amount < 0) return;
    try {
      await editBet.mutateAsync({ betId, data: { result: 'void', payout: amount } });
      cancelCashout();
      fetchBets();
      fetchStats();
    } catch (err) {
      console.error('Cashout failed:', err);
    }
  };

  return (
    <div className="space-y-3 min-w-0 overflow-y-auto flex-1 min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="stats" color={TAB_COLORS.stats} size={16} />
          Stats
        </h2>
        <input
          type="text"
          placeholder="Search event, provider, sport..."
          className="px-2 py-1 text-xs bg-bg border border-border text-text placeholder:text-muted2 w-64 focus:border-tabBets focus:outline-none"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* Stats Summary */}
      {bankrollStats && (
        <div className="border-l-2 border-tabBets">
          <div className="grid grid-cols-4 gap-px bg-border border border-border">
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Bets</div>
              <div className="text-text text-lg font-semibold">{bankrollStats.total_bets}</div>
              <div className="flex items-center gap-2 text-[10px]">
                <span className="text-success">{bankrollStats.wins}W</span>
                <span className="text-error">{bankrollStats.losses}L</span>
                <span className="text-muted">{bankrollStats.voids}V</span>
              </div>
            </div>
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">ROI</div>
              <div className={`text-lg font-semibold ${bankrollStats.roi_pct >= 0 ? 'text-success' : 'text-error'}`}>
                {bankrollStats.roi_pct >= 0 ? '+' : ''}{bankrollStats.roi_pct.toFixed(1)}%
              </div>
            </div>
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Profit</div>
              <div className={`text-lg font-semibold ${bankrollStats.total_profit >= 0 ? 'text-success' : 'text-error'}`}>
                {bankrollStats.total_profit >= 0 ? '+' : ''}{bankrollStats.total_profit.toFixed(0)} kr
              </div>
            </div>
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Avg CLV</div>
              {bankrollStats.clv_count > 0 ? (
                <>
                  <div className={`text-lg font-semibold ${bankrollStats.avg_clv >= 0 ? 'text-success' : 'text-error'}`}>
                    {bankrollStats.avg_clv >= 0 ? '+' : ''}{bankrollStats.avg_clv.toFixed(1)}%
                  </div>
                  <div className="text-[10px] text-muted">{bankrollStats.clv_positive_pct.toFixed(0)}% beat close</div>
                </>
              ) : (
                <>
                  <div className="text-lg font-semibold text-muted">-</div>
                  <div className="text-[10px] text-muted">No CLV data</div>
                </>
              )}
            </div>
          </div>
          {/* Financial context row */}
          <div className="flex items-center gap-4 px-3 py-1.5 bg-panel2 border border-border border-t-0 text-[10px]">
            <span className="text-muted">Win rate: <span className="text-text">{bankrollStats.win_rate.toFixed(1)}%</span></span>
            <span className="text-muted">Staked: <span className="text-text">{bankrollStats.total_staked.toFixed(0)} kr</span></span>
            {bankrollStats.total_deposited > 0 && (
              <span className="text-muted">Net deposited: <span className="text-text">{bankrollStats.net_deposited.toFixed(0)} kr</span></span>
            )}
          </div>
        </div>
      )}

      {/* Charts — side by side */}
      <div>
        <div className="grid grid-cols-2 gap-[1px] bg-[#161b22]">
          {bets.length > 0 && bankrollStats && bankrollStats.net_deposited > 0 && (
            <BankrollChart bets={bets.filter(b => !b.is_bonus)} netDeposited={bankrollStats.net_deposited} totalStaked={bankrollStats?.total_staked} />
          )}
          <CLVChart bets={bets.filter(b => !b.is_bonus)} />
        </div>
      </div>

      {/* Wagering Progress */}
      {activeBonuses.length > 0 && (<>
        <button
          className="flex items-center gap-2 w-full text-left cursor-pointer group"
          onClick={() => setWageringCollapsed(c => !c)}
        >
          <span className={`text-[10px] text-muted2 transition-transform ${wageringCollapsed ? '' : 'rotate-90'}`}>▶</span>
          <h3 className="text-xs text-muted uppercase tracking-wider font-semibold group-hover:text-text transition-colors">
            Wagering Progress <span className="text-muted2">{activeBonuses.length}</span>
          </h3>
        </button>
        {!wageringCollapsed && (
        <div className="border-l-2 border-tabBets">
          <table className="sq">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Type</th>
                <th className="text-right">Progress</th>
                <th className="text-right">Remaining</th>
                <th className="text-right">Kr/wk</th>
                <th className="text-right">Deadline</th>
                <th className="text-right">ETA</th>
              </tr>
            </thead>
            <tbody>
              {activeBonuses
                .sort((a, b) => {
                  const order = { in_progress: 0, trigger_needed: 1, freebet_available: 2, claimed: 3 };
                  return (order[a[1].status as keyof typeof order] ?? 9) - (order[b[1].status as keyof typeof order] ?? 9);
                })
                .map(([providerId, bonus]) => {
                const pct = Math.min(100, bonus.progress_pct);
                const days = bonus.days_remaining;
                const urgent = days !== null && days <= 10;
                const warning = days !== null && days > 10 && days <= 30;
                const remaining = bonus.wagering_requirement - bonus.wagered_amount;
                const isClaimed = bonus.status === 'claimed';
                const hasProgress = !isClaimed && (bonus.status === 'in_progress' || (bonus.status === 'trigger_needed' && bonus.bonus_type === 'bonusdeposit')) && bonus.wagering_requirement > 0;
                const estDays = bonus.prognosis?.est_weeks != null ? Math.round(bonus.prognosis.est_weeks * 7) : null;
                const onTrack = estDays !== null && days !== null && estDays <= days;
                const requiredPerWk = bonus.prognosis?.required_weekly_wagering ?? null;

                return (
                  <tr key={providerId} className={isClaimed ? 'opacity-60' : ''}>
                    <td className="text-text text-sm font-medium"><ProviderName name={providerId} /></td>
                    <td>
                      <span className={`text-[10px] px-1.5 py-0.5 font-medium ${
                        isClaimed ? 'bg-muted/15 text-muted' :
                        bonus.status === 'freebet_available' ? 'bg-success/15 text-success' :
                        'bg-tabBets/15 text-tabBets'
                      }`}>
                        {isClaimed ? 'NEEDED'
                          : bonus.bonus_type === 'freebet' ? 'FREEBET'
                          : bonus.status === 'trigger_needed' ? 'TRIGGER'
                          : 'WAGER'}
                      </span>
                    </td>
                    <td className="text-right">
                      {hasProgress ? (
                        <div className="flex items-center gap-2 justify-end">
                          <div className="w-16 h-1.5 bg-bg overflow-hidden">
                            <div
                              className={`h-full ${urgent ? 'bg-error' : warning ? 'bg-amber-400' : 'bg-tabBets'}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className="text-[10px] text-muted2">{pct.toFixed(0)}%</span>
                        </div>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                    <td className="text-right text-sm text-text">
                      {isClaimed ? `${bonus.wagering_requirement.toFixed(0)} kr`
                        : hasProgress ? `${remaining.toFixed(0)} kr` : '-'}
                    </td>
                    <td className="text-right">
                      {requiredPerWk != null && requiredPerWk > 0 ? (
                        <span className="text-sm text-text">{requiredPerWk.toFixed(0)}</span>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                    <td className="text-right">
                      {days !== null ? (
                        <span className={`text-sm ${urgent ? 'text-error font-medium' : warning ? 'text-amber-400' : 'text-muted'}`}>
                          {days}d
                        </span>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                    <td className="text-right">
                      {estDays !== null ? (
                        <span className={`text-sm ${onTrack ? 'text-success' : 'text-error'}`}>
                          ~{estDays}d
                        </span>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
      </>)}

      {/* Bet History */}
      <button
        className="flex items-center gap-2 w-full text-left cursor-pointer group"
        onClick={() => setHistoryCollapsed(c => !c)}
      >
        <span className={`text-[10px] text-muted2 transition-transform ${historyCollapsed ? '' : 'rotate-90'}`}>▶</span>
        <h3 className="text-xs text-muted uppercase tracking-wider font-semibold group-hover:text-text transition-colors">
          Bet History <span className="text-muted2">{historyBets.length}</span>
        </h3>
      </button>
      {!historyCollapsed && (isLoading && bets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
      ) : historyBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          {search.trim() ? 'No matching bets.' : 'No bets found.'}
        </div>
      ) : (
        <>
          <div className="border-l-2 border-tabBets">
          <table className="sq">
            <thead>
              <tr>
                <SortHeader label="Date" sortKey="date" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Provider" sortKey="provider" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Entry" sortKey="odds" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Close" sortKey="close" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="CLV" sortKey="clv" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Est Edge" sortKey="edge" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Stake" sortKey="stake" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Profit" sortKey="profit" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Prob" sortKey="prob" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Entry TTK" sortKey="ttk" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Status" sortKey="status" currentSort={sort} onSort={handleSort} align="right" />
              </tr>
            </thead>
            <tbody>
              {historyBets.map((bet) => {
                const isExpanded = expandedIdx === bet.id;
                const isEditing = editingBetId === bet.id;
                const isCashingOut = cashoutBetId === bet.id;
                const ttk = getTTK(bet);
                const tier = getTTKTier(ttk);
                return (
                  <>
                    <tr
                      key={bet.id}
                      className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`}
                      onClick={() => { if (!isEditing) setExpandedIdx(isExpanded ? null : bet.id); }}
                    >
                      <td className="text-muted text-[11px] whitespace-nowrap">{formatDate(bet.placed_at)}</td>
                      <td className="text-text text-sm"><ProviderName name={bet.provider} /></td>
                      <td className="text-right text-text text-sm font-medium">{bet.odds.toFixed(2)}</td>
                      <td className="text-right">
                        {bet.closing_odds != null ? (
                          <span className={`text-sm ${bet.closing_odds > bet.odds ? 'text-success' : bet.closing_odds < bet.odds ? 'text-error' : 'text-text'}`}>
                            {bet.closing_odds.toFixed(2)}
                          </span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right">
                        {bet.clv_pct != null ? (
                          <span className={`text-sm font-medium ${bet.clv_pct >= 0 ? 'text-success' : 'text-error'}`}>
                            {bet.clv_pct >= 0 ? '+' : ''}{bet.clv_pct.toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right">
                        {bet.placed_edge_pct != null ? (
                          <span className={`text-sm font-medium ${bet.placed_edge_pct >= 0 ? 'text-success' : 'text-error'}`}>
                            +{bet.placed_edge_pct.toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right text-text text-sm">{fmtAmount(bet.stake, bet.currency)}</td>
                      <td className="text-right">
                        <span className={`text-sm font-medium ${bet.profit >= 0 ? 'text-success' : 'text-error'}`}>
                          {fmtProfit(bet.profit, bet.currency)}
                        </span>
                      </td>
                      <td className="text-right">
                        {bet.selection_probability != null ? (
                          <span className="text-sm text-text">{(bet.selection_probability * 100).toFixed(0)}%</span>
                        ) : (
                          <span className="text-sm text-muted">-</span>
                        )}
                      </td>
                      <td className="text-right">
                        <span className={`text-sm ${tier.color}`}>{tier.label}</span>
                      </td>
                      <td className="text-right">
                        <span className={`text-sm capitalize ${getStatusColor(bet.result)}`}>{bet.result}</span>
                      </td>
                    </tr>
                    {isExpanded && (() => {
                      const badge = CLV_BADGE[tier.confidence];
                      return (
                      <tr key={`${bet.id}-expanded`}>
                        <td colSpan={11} className="!p-0" onClick={e => e.stopPropagation()}>
                          <div className="px-3 py-2 bg-panel space-y-2">
                            <div className="flex items-center gap-6 text-xs text-muted">
                              {bet.home_team && bet.away_team && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">Event: </span>
                                  <span className="text-text">{displayTeamName(bet.home_team, bet.display_home)} vs {displayTeamName(bet.away_team, bet.display_away)}</span>
                                  {bet.sport && <span className="text-muted2 ml-1">({bet.sport})</span>}
                                </div>
                              )}
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Selection: </span>
                                <span className="text-text">{resolveOutcome(bet)}</span>
                                {bet.market && <span className="text-muted2 ml-1">({bet.market}{bet.point != null ? ` ${bet.point}` : ''})</span>}
                              </div>
                              {bet.fair_odds_at_placement != null && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">Fair: </span>
                                  <span className="text-text">{bet.fair_odds_at_placement.toFixed(3)}</span>
                                </div>
                              )}
                              {ttk !== null && (
                                <div>
                                  <span className="text-muted2 uppercase tracking-wider">CLV Conf: </span>
                                  <span className={`text-[10px] px-1 py-0.5 ${badge.cls}`}>{badge.text}</span>
                                </div>
                              )}
                              {!isEditing && !isCashingOut && (
                                <div className="flex items-center gap-1.5 ml-auto">
                                  {bet.result === 'pending' && (
                                    <button
                                      className="text-[10px] px-1.5 py-0.5 bg-warning/15 text-warning hover:bg-warning/30 transition-colors"
                                      onClick={() => startCashout(bet)}
                                    >Cashout</button>
                                  )}
                                  <button
                                    className="text-[10px] px-1.5 py-0.5 bg-accent/15 text-accent hover:bg-accent/30 transition-colors"
                                    onClick={() => startEditing(bet)}
                                  >Edit</button>
                                </div>
                              )}
                            </div>
                            {isCashingOut && (
                              <div className="flex items-center gap-3 text-xs">
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Cashout Amount:</span>
                                  <input
                                    type="number"
                                    step="1"
                                    className="w-24 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={cashoutAmount}
                                    onChange={e => setCashoutAmount(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') confirmCashout(bet.id); if (e.key === 'Escape') cancelCashout(); }}
                                    placeholder={fmtAmount(bet.stake, bet.currency)}
                                    autoFocus
                                  />
                                  <span className="text-muted2">{bet.currency === 'USD' || bet.currency === 'USDC' ? '$' : 'kr'}</span>
                                </div>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-warning/15 text-warning hover:bg-warning/30 transition-colors"
                                  onClick={() => confirmCashout(bet.id)}
                                >Confirm</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                                  onClick={cancelCashout}
                                >Cancel</button>
                              </div>
                            )}
                            {isEditing && (
                              <div className="flex items-center gap-3 text-xs">
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Stake:</span>
                                  <input
                                    type="number"
                                    className="w-20 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editStake}
                                    onChange={e => setEditStake(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') saveEdit(bet.id); if (e.key === 'Escape') cancelEditing(); }}
                                    autoFocus
                                  />
                                </div>
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Odds:</span>
                                  <input
                                    type="number"
                                    step="0.01"
                                    className="w-20 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editOdds}
                                    onChange={e => setEditOdds(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') saveEdit(bet.id); if (e.key === 'Escape') cancelEditing(); }}
                                  />
                                </div>
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Result:</span>
                                  <select
                                    className="px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editResult}
                                    onChange={e => setEditResult(e.target.value)}
                                  >
                                    <option value="won">won</option>
                                    <option value="lost">lost</option>
                                    <option value="void">void</option>
                                  </select>
                                </div>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-success/15 text-success hover:bg-success/30 transition-colors"
                                  onClick={() => saveEdit(bet.id)}
                                >Save</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                                  onClick={cancelEditing}
                                >Cancel</button>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                      );
                    })()}
                  </>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      ))}
    </div>
  );
}
