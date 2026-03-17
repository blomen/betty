import { useState, useEffect, useCallback, useMemo } from 'react';
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

function BankrollChart({ bets, currentBankroll, totalStaked }: { bets: Bet[]; currentBankroll: number; totalStaked?: number }) {
  const data = useMemo(() => {
    const settled = bets
      .filter(b => b.result !== 'pending')
      .sort((a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime());

    if (settled.length === 0) return [];

    const totalProfit = settled.reduce((sum, b) => sum + toSEK(b.profit, b.currency), 0);
    const startBankroll = currentBankroll - totalProfit;

    let cumulative = startBankroll;
    const points = [{ date: new Date(settled[0].placed_at), value: startBankroll }];
    for (const bet of settled) {
      cumulative += toSEK(bet.profit, bet.currency);
      points.push({ date: new Date(bet.placed_at), value: cumulative });
    }
    return points;
  }, [bets, currentBankroll]);

  if (data.length < 2) return null;

  const W = 600;
  const H = 200;
  const PX = 40;
  const PR = 12;
  const PT = 12;
  const PB = 24;

  const minVal = Math.min(...data.map(d => d.value));
  const maxVal = Math.max(...data.map(d => d.value));
  const range = maxVal - minVal || 1;
  const minDate = data[0].date.getTime();
  const maxDate = data[data.length - 1].date.getTime();
  const dateRange = maxDate - minDate || 1;

  const x = (d: Date) => PX + (d.getTime() - minDate) / dateRange * (W - PX - PR);
  const y = (v: number) => PT + (1 - (v - minVal) / range) * (H - PT - PB);

  const pathD = data.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.date).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');

  const lastVal = data[data.length - 1].value;
  const firstVal = data[0].value;
  const isUp = lastVal >= firstVal;
  const stroke = isUp ? '#10b981' : '#ef4444';
  const gradId = 'bankroll-grad';

  const ySteps = 5;
  const yLabels = Array.from({ length: ySteps }, (_, i) => {
    const v = minVal + (range * i) / (ySteps - 1);
    return { value: v, label: `${(v / 1000).toFixed(1)}k`, yPos: y(v) };
  });

  // Generate month-based x labels
  const xLabels: { label: string; xPos: number }[] = [];
  {
    const seen = new Set<string>();
    for (const p of data) {
      const key = `${p.date.getFullYear()}-${p.date.getMonth()}`;
      if (!seen.has(key)) {
        seen.add(key);
        xLabels.push({
          label: p.date.toLocaleDateString('en-US', { month: 'short' }),
          xPos: x(p.date),
        });
      }
    }
    const last = data[data.length - 1];
    const lastKey = `${last.date.getFullYear()}-${last.date.getMonth()}-end`;
    if (!seen.has(lastKey) && xLabels.length > 0) {
      xLabels.push({
        label: last.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
        xPos: x(last.date),
      });
    }
  }

  const profit = lastVal - firstVal;
  const roiBase = totalStaked && totalStaked > 0 ? totalStaked : firstVal;
  const profitPct = ((profit / roiBase) * 100).toFixed(1);

  const xPct = (svgX: number) => `${(svgX / W * 100).toFixed(2)}%`;
  const yPct = (svgY: number) => `${(svgY / H * 100).toFixed(2)}%`;

  return (
    <div className="bg-panel overflow-hidden">
      <div className="px-3 py-2 border-b border-border flex items-center justify-between">
        <span className="text-xs text-muted uppercase tracking-wider font-medium">Bankroll</span>
        <div className="flex items-center gap-3">
          <span className={`text-xs ${isUp ? 'text-success/70' : 'text-error/70'}`}>
            {profit >= 0 ? '+' : ''}{profit.toFixed(0)} kr ({profit >= 0 ? '+' : ''}{profitPct}%)
          </span>
          <span className={`text-sm font-semibold ${isUp ? 'text-success' : 'text-error'}`}>
            {lastVal.toFixed(0)} kr
          </span>
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity="0.35" />
              <stop offset="60%" stopColor={stroke} stopOpacity="0.1" />
              <stop offset="100%" stopColor={stroke} stopOpacity="0.02" />
            </linearGradient>
          </defs>
          {yLabels.map((l, i) => (
            <line key={i} x1={PX} y1={l.yPos} x2={W - PR} y2={l.yPos} stroke="#2c2c2c" strokeWidth="0.5" strokeDasharray="4,4" />
          ))}
          <path
            d={`${pathD} L${x(data[data.length - 1].date).toFixed(1)},${(H - PB).toFixed(1)} L${x(data[0].date).toFixed(1)},${(H - PB).toFixed(1)} Z`}
            fill={`url(#${gradId})`}
          />
          <path d={pathD} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          <circle cx={x(data[data.length - 1].date)} cy={y(lastVal)} r="6" fill={stroke} fillOpacity="0.12" />
          <circle cx={x(data[data.length - 1].date)} cy={y(lastVal)} r="3" fill={stroke} />
        </svg>
        {yLabels.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-muted2 -translate-y-1/2" style={{ top: yPct(l.yPos), left: '2px' }}>{l.label}</span>
        ))}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className="absolute text-[10px] text-muted2 -translate-x-1/2" style={{ bottom: '2px', left: xPct(l.xPos) }}>{l.label}</span>
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

  const LINE_COLOR = '#1E88E5';
  const W = 600;
  const H = 200;
  const PL = 12;
  const PR = 40;
  const PT = 12;
  const PB = 24;

  const minVal = -50;
  const maxVal = 50;
  const range = maxVal - minVal || 1;
  const minDate = data[0].date.getTime();
  const maxDate = data[data.length - 1].date.getTime();
  const dateRange = maxDate - minDate || 1;

  const x = (d: Date) => PL + (d.getTime() - minDate) / dateRange * (W - PL - PR);
  const y = (v: number) => PT + (1 - (v - minVal) / range) * (H - PT - PB);

  const zeroY = y(0);

  const windowSize = Math.min(20, Math.ceil(data.length / 3));
  const avgPoints: { date: Date; avg: number }[] = [];
  for (let i = 0; i < data.length; i++) {
    const start = Math.max(0, i - windowSize + 1);
    const window = data.slice(start, i + 1);
    const avg = window.reduce((s, d) => s + d.clv, 0) / window.length;
    avgPoints.push({ date: data[i].date, avg });
  }
  const avgPathD = avgPoints
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.date).toFixed(1)},${y(p.avg).toFixed(1)}`)
    .join(' ');

  const totalAvg = data.reduce((s, d) => s + d.clv, 0) / data.length;
  const isPositive = totalAvg >= 0;
  const positiveCount = data.filter(d => d.clv >= 0).length;
  const beatPct = ((positiveCount / data.length) * 100).toFixed(0);

  const ySteps = 5;
  const yLabels = Array.from({ length: ySteps }, (_, i) => {
    const v = minVal + (range * i) / (ySteps - 1);
    return { label: `${v > 0 ? '+' : ''}${v.toFixed(0)}%`, yPos: y(v) };
  });

  // Generate month-based x labels
  const xLabels: { label: string; xPos: number }[] = [];
  {
    const seen = new Set<string>();
    for (const d of data) {
      const key = `${d.date.getFullYear()}-${d.date.getMonth()}`;
      if (!seen.has(key)) {
        seen.add(key);
        xLabels.push({
          label: d.date.toLocaleDateString('en-US', { month: 'short' }),
          xPos: x(d.date),
        });
      }
    }
  }

  const xPct = (svgX: number) => `${(svgX / W * 100).toFixed(2)}%`;
  const yPct = (svgY: number) => `${(svgY / H * 100).toFixed(2)}%`;

  return (
    <div className="bg-panel overflow-hidden">
      <div className="px-3 py-2 border-b border-border flex items-center justify-between">
        <span className="text-xs text-muted uppercase tracking-wider font-medium">CLV Trend</span>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-muted">{data.length} bets</span>
          <span className="text-[10px] text-muted">{beatPct}% beat close</span>
          <span className={`text-sm font-semibold ${isPositive ? 'text-success' : 'text-error'}`}>
            {totalAvg >= 0 ? '+' : ''}{totalAvg.toFixed(1)}% avg
          </span>
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          {yLabels.map((l, i) => (
            <line key={i} x1={PL} y1={l.yPos} x2={W - PR} y2={l.yPos} stroke="#2c2c2c" strokeWidth="0.5" strokeDasharray="2,4" />
          ))}
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#555" strokeWidth="1" />
          <path d={avgPathD} fill="none" stroke={LINE_COLOR} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          <circle cx={x(avgPoints[avgPoints.length - 1].date)} cy={y(avgPoints[avgPoints.length - 1].avg)} r="3" fill={LINE_COLOR} />
        </svg>
        {yLabels.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-muted2 -translate-y-1/2" style={{ top: yPct(l.yPos), right: '4px' }}>{l.label}</span>
        ))}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className="absolute text-[10px] text-muted2 -translate-x-1/2" style={{ bottom: '2px', left: xPct(l.xPos) }}>{l.label}</span>
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
  const [bets, setBets] = useState<Bet[]>([]);
  const [bankrollStats, setBankrollStats] = useState<BankrollStats | null>(null);
  const [currentBankroll, setCurrentBankroll] = useState<number>(0);
  const [isLoading, setIsLoading] = useState(true);
  const [activeBonuses, setActiveBonuses] = useState<[string, BonusProgressEntry][]>([]);
  // Sort & search (for history table)
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [search, setSearch] = useState('');

  // Inline editing state
  const [editingBetId, setEditingBetId] = useState<number | null>(null);
  const [editStake, setEditStake] = useState<string>('');
  const [editOdds, setEditOdds] = useState<string>('');
  const [editResult, setEditResult] = useState<string>('');

  // Cashout state
  const [cashoutBetId, setCashoutBetId] = useState<number | null>(null);
  const [cashoutAmount, setCashoutAmount] = useState<string>('');

  // Bet history collapsed state
  const [historyCollapsed, setHistoryCollapsed] = useState(false);

  const fetchBets = useCallback(async () => {
    setIsLoading(true);
    try {
      const [response, bankroll] = await Promise.all([
        api.getBets(undefined, 500),
        api.getBankroll(),
      ]);
      setBets(response.bets);
      setCurrentBankroll(bankroll.total);
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
        ([, b]) => ['trigger_needed', 'freebet_available', 'in_progress'].includes(b.status)
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
      await api.editBet(betId, changes);
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
      await api.editBet(betId, { result: 'void', payout: amount });
      cancelCashout();
      fetchBets();
      fetchStats();
    } catch (err) {
      console.error('Cashout failed:', err);
    }
  };

  return (
    <div className="space-y-3 min-w-0">
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
            {(bankrollStats.freebet_profit > 0 || bankrollStats.bonus_profit > 0) && (
              <>
                <span className="text-muted">|</span>
                <span className="text-muted">Bet P&L: <span className="text-text">{bankrollStats.bet_profit.toFixed(0)}</span></span>
                {bankrollStats.freebet_profit > 0 && (
                  <span className="text-accent">+{bankrollStats.freebet_profit.toFixed(0)} fb</span>
                )}
                {bankrollStats.bonus_profit > 0 && (
                  <span className="text-tabBonus">+{bankrollStats.bonus_profit.toFixed(0)} bonus</span>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Charts — side by side */}
      <div className="border-l-2 border-tabBets">
        <div className="grid grid-cols-2 gap-px bg-border border border-border">
          {bets.length > 0 && currentBankroll > 0 && (
            <BankrollChart bets={bets} currentBankroll={currentBankroll} totalStaked={bankrollStats?.total_staked} />
          )}
          <CLVChart bets={bets} />
        </div>
      </div>

      {/* Active Bonuses */}
      {activeBonuses.length > 0 && (
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
              {activeBonuses.map(([providerId, bonus]) => {
                const pct = Math.min(100, bonus.progress_pct);
                const days = bonus.days_remaining;
                const urgent = days !== null && days <= 10;
                const warning = days !== null && days > 10 && days <= 30;
                const remaining = bonus.wagering_requirement - bonus.wagered_amount;
                const hasProgress = (bonus.status === 'in_progress' || (bonus.status === 'trigger_needed' && bonus.bonus_type === 'bonusdeposit')) && bonus.wagering_requirement > 0;
                const estDays = bonus.prognosis?.est_weeks != null ? Math.round(bonus.prognosis.est_weeks * 7) : null;
                const onTrack = estDays !== null && days !== null && estDays <= days;
                const requiredPerWk = bonus.prognosis?.required_weekly_wagering ?? null;

                return (
                  <tr key={providerId}>
                    <td className="text-text text-sm font-medium"><ProviderName name={providerId} /></td>
                    <td>
                      <span className={`text-[10px] px-1.5 py-0.5 font-medium ${
                        bonus.status === 'freebet_available' ? 'bg-success/15 text-success' :
                        'bg-tabBets/15 text-tabBets'
                      }`}>
                        {bonus.bonus_type === 'freebet' ? 'FREEBET'
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
                      {hasProgress ? `${remaining.toFixed(0)} kr` : '-'}
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
