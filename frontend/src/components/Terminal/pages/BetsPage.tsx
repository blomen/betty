import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
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

function BankrollChart({ bets, currentBankroll }: { bets: Bet[]; currentBankroll: number }) {
  const data = useMemo(() => {
    const settled = bets
      .filter(b => b.result !== 'pending')
      .sort((a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime());

    if (settled.length === 0) return [];

    const totalProfit = settled.reduce((sum, b) => sum + b.profit, 0);
    const startBankroll = currentBankroll - totalProfit;

    let cumulative = startBankroll;
    const points = [{ date: new Date(settled[0].placed_at), value: startBankroll }];
    for (const bet of settled) {
      cumulative += bet.profit;
      points.push({ date: new Date(bet.placed_at), value: cumulative });
    }
    return points;
  }, [bets, currentBankroll]);

  if (data.length < 2) return null;

  const W = 600;
  const H = 120;
  const PX = 32;
  const PR = 8;
  const PT = 8;
  const PB = 16;

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

  const ySteps = 4;
  const yLabels = Array.from({ length: ySteps }, (_, i) => {
    const v = minVal + (range * i) / (ySteps - 1);
    return { value: v, label: `${(v / 1000).toFixed(1)}k`, yPos: y(v) };
  });

  const xLabels = [data[0], data[data.length - 1]].map(p => ({
    label: p.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    xPos: x(p.date),
  }));

  const profit = lastVal - firstVal;
  const profitPct = ((profit / firstVal) * 100).toFixed(1);

  const xPct = (svgX: number) => `${(svgX / W * 100).toFixed(2)}%`;
  const yPct = (svgY: number) => `${(svgY / H * 100).toFixed(2)}%`;

  return (
    <div className="border border-border bg-panel overflow-hidden">
      <div className="px-3 py-1.5 border-b border-border flex items-center justify-between">
        <span className="text-[10px] text-muted uppercase tracking-wider">Bankroll</span>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] ${isUp ? 'text-success/70' : 'text-error/70'}`}>
            {profit >= 0 ? '+' : ''}{profit.toFixed(0)} kr ({profit >= 0 ? '+' : ''}{profitPct}%)
          </span>
          <span className={`text-xs font-semibold ${isUp ? 'text-success' : 'text-error'}`}>
            {lastVal.toFixed(0)} kr
          </span>
        </div>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" preserveAspectRatio="none">
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity="0.2" />
              <stop offset="100%" stopColor={stroke} stopOpacity="0" />
            </linearGradient>
          </defs>
          {yLabels.map((l, i) => (
            <line key={i} x1={PX} y1={l.yPos} x2={W - PR} y2={l.yPos} stroke="#2c2c2c" strokeWidth="0.5" strokeDasharray={i === 0 ? 'none' : '3,3'} />
          ))}
          <path
            d={`${pathD} L${x(data[data.length - 1].date).toFixed(1)},${y(minVal).toFixed(1)} L${x(data[0].date).toFixed(1)},${y(minVal).toFixed(1)} Z`}
            fill={`url(#${gradId})`}
          />
          <path d={pathD} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          {data.map((p, i) => (
            <circle key={i} cx={x(p.date)} cy={y(p.value)} r={i === data.length - 1 ? 2.5 : 1.2} fill={stroke} fillOpacity={i === data.length - 1 ? 1 : 0.4} />
          ))}
          <circle cx={x(data[data.length - 1].date)} cy={y(lastVal)} r="5" fill={stroke} fillOpacity="0.15" />
          <circle cx={x(data[data.length - 1].date)} cy={y(lastVal)} r="2.5" fill={stroke} />
        </svg>
        {yLabels.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-muted2 -translate-y-1/2" style={{ top: yPct(l.yPos), right: xPct(W - PX + 4) }}>{l.label}</span>
        ))}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className={`absolute text-[10px] text-muted2 ${i === 0 ? '' : 'translate-x-[-100%]'}`} style={{ bottom: 0, left: xPct(l.xPos) }}>{l.label}</span>
        ))}
      </div>
    </div>
  );
}

export function CLVChart({ bets, showTTKLegend = true }: { bets: Bet[]; showTTKLegend?: boolean }) {
  const data = useMemo(() => {
    return bets
      .filter(b => b.result !== 'pending' && b.clv_pct != null)
      .sort((a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime())
      .map(b => {
        const ttkHours = getTTK(b);
        const confidence = ttkHours === null ? 0.5 :
          ttkHours <= 6 ? 1.0 :
          ttkHours <= 12 ? 0.85 :
          ttkHours <= 24 ? 0.6 :
          ttkHours <= 48 ? 0.35 : 0.2;
        return { date: new Date(b.placed_at), clv: b.clv_pct!, ttkHours, confidence };
      });
  }, [bets]);

  if (data.length < 2) return null;

  const W = 600;
  const H = 120;
  const PX = 32;
  const PR = 8;
  const PT = 8;
  const PB = 16;

  const clvValues = data.map(d => d.clv);
  const absMax = Math.max(Math.abs(Math.min(...clvValues)), Math.abs(Math.max(...clvValues)), 5);
  const minVal = -absMax;
  const maxVal = absMax;
  const range = maxVal - minVal || 1;
  const minDate = data[0].date.getTime();
  const maxDate = data[data.length - 1].date.getTime();
  const dateRange = maxDate - minDate || 1;

  const x = (d: Date) => PX + (d.getTime() - minDate) / dateRange * (W - PX - PR);
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

  const lastAvg = avgPoints[avgPoints.length - 1].avg;
  const isPositive = lastAvg >= 0;
  const avgColor = isPositive ? '#10b981' : '#ef4444';
  const positiveCount = data.filter(d => d.clv >= 0).length;
  const beatPct = ((positiveCount / data.length) * 100).toFixed(0);

  const ySteps = 5;
  const yLabels = Array.from({ length: ySteps }, (_, i) => {
    const v = minVal + (range * i) / (ySteps - 1);
    return { label: `${v > 0 ? '+' : ''}${v.toFixed(0)}%`, yPos: y(v) };
  });

  const xLabels = [data[0], data[data.length - 1]].map(p => ({
    label: p.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    xPos: x(p.date),
  }));

  const xPct = (svgX: number) => `${(svgX / W * 100).toFixed(2)}%`;
  const yPct = (svgY: number) => `${(svgY / H * 100).toFixed(2)}%`;

  return (
    <div className="border border-border bg-panel overflow-hidden">
      <div className="px-3 py-1.5 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted uppercase tracking-wider">CLV Distribution</span>
          {showTTKLegend && (
            <div className="flex items-center gap-1.5 text-[9px] text-muted2">
              <span className="flex items-center gap-0.5"><span className="inline-block w-1.5 h-1.5 rounded-full bg-success opacity-80" /> &lt;6h</span>
              <span className="flex items-center gap-0.5"><span className="inline-block w-1.5 h-1.5 rounded-full bg-success opacity-65" /> 6-12h</span>
              <span className="flex items-center gap-0.5"><span className="inline-block w-1 h-1 rounded-full bg-success opacity-45" /> 12-24h</span>
              <span className="flex items-center gap-0.5"><span className="inline-block w-1 h-1 rounded-full bg-success opacity-30" /> 24-48h</span>
              <span className="flex items-center gap-0.5"><span className="inline-block w-1 h-1 rounded-full bg-success opacity-15" /> 48h+</span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted">{data.length} bets</span>
          <span className="text-[10px] text-muted">{beatPct}% beat close</span>
          <span className={`text-xs font-semibold ${isPositive ? 'text-success' : 'text-error'}`}>
            {lastAvg >= 0 ? '+' : ''}{lastAvg.toFixed(1)}% avg
          </span>
        </div>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" preserveAspectRatio="none">
          <defs>
            <linearGradient id="clv-avg-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={avgColor} stopOpacity="0.12" />
              <stop offset="100%" stopColor={avgColor} stopOpacity="0" />
            </linearGradient>
          </defs>
          <rect x={PX} y={PT} width={W - PX - PR} height={zeroY - PT} fill="#10b981" fillOpacity="0.02" />
          <rect x={PX} y={zeroY} width={W - PX - PR} height={H - PB - zeroY} fill="#ef4444" fillOpacity="0.02" />
          {yLabels.map((l, i) => (
            <line key={i} x1={PX} y1={l.yPos} x2={W - PR} y2={l.yPos} stroke="#2c2c2c" strokeWidth="0.5" strokeDasharray="3,3" />
          ))}
          <line x1={PX} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#7A7F87" strokeWidth="0.6" strokeDasharray="4,3" />
          {data.map((d, i) => {
            const cx = x(d.date);
            const cy = y(d.clv);
            const color = d.clv >= 0 ? '#10b981' : '#ef4444';
            const r = 1.2 + d.confidence * 1.2;
            const opacity = 0.25 + d.confidence * 0.55;
            return (
              <g key={i}>
                <line x1={cx} y1={zeroY} x2={cx} y2={cy} stroke={color} strokeWidth="0.8" strokeOpacity={opacity * 0.4} />
                <circle cx={cx} cy={cy} r={r} fill={color} fillOpacity={opacity} />
              </g>
            );
          })}
          <path
            d={`${avgPathD} L${x(avgPoints[avgPoints.length - 1].date).toFixed(1)},${zeroY.toFixed(1)} L${x(avgPoints[0].date).toFixed(1)},${zeroY.toFixed(1)} Z`}
            fill="url(#clv-avg-grad)"
          />
          <path d={avgPathD} fill="none" stroke={avgColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          <circle cx={x(avgPoints[avgPoints.length - 1].date)} cy={y(lastAvg)} r="5" fill={avgColor} fillOpacity="0.15" />
          <circle cx={x(avgPoints[avgPoints.length - 1].date)} cy={y(lastAvg)} r="2.5" fill={avgColor} />
        </svg>
        {yLabels.map((l, i) => (
          <span key={`y${i}`} className="absolute text-[10px] text-muted2 -translate-y-1/2" style={{ top: yPct(l.yPos), right: xPct(W - PX + 4) }}>{l.label}</span>
        ))}
        {xLabels.map((l, i) => (
          <span key={`x${i}`} className={`absolute text-[10px] text-muted2 ${i === 0 ? '' : 'translate-x-[-100%]'}`} style={{ bottom: 0, left: xPct(l.xPos) }}>{l.label}</span>
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
  const [providerIds, setProviderIds] = useState<string[]>([]);
  const [showProviderActions, setShowProviderActions] = useState(false);

  // Sort (for history table)
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  // Inline editing state
  const [editingBetId, setEditingBetId] = useState<number | null>(null);
  const [editStake, setEditStake] = useState<string>('');
  const [editOdds, setEditOdds] = useState<string>('');
  const [editResult, setEditResult] = useState<string>('');

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

  // Load provider list for quick actions
  useEffect(() => {
    api.getProviders().then((res) => {
      const ids = (res.providers || [])
        .filter((p: { is_enabled: boolean }) => p.is_enabled)
        .map((p: { id: string }) => p.id)
        .sort();
      setProviderIds(ids);
    }).catch(() => {});
  }, []);

  const openWorkflow = useCallback((_providerId: string, _workflow: string) => {
    // CDP navigation removed — manual placement only
  }, []);

  // ── History (settled bets only) ─────────────────────────────────

  const historyBets = useMemo(() => {
    let result = bets.filter(b => b.result !== 'pending');

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
  }, [bets, sort]);

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

  const resolveOutcome = (bet: Bet): string => {
    const outcome = bet.outcome || '-';
    if (outcome === 'home' && bet.home_team) return bet.home_team;
    if (outcome === 'away' && bet.away_team) return bet.away_team;
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return 'Over';
    if (outcome === 'under') return 'Under';
    return outcome;
  };

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

  return (
    <div className="space-y-3">
      {/* Header */}
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <TabIcon name="stats" color={TAB_COLORS.stats} size={16} />
        Stats
      </h2>

      {/* Stats Summary */}
      {bankrollStats && (
        <div className="border-l-2 border-tabBets">
          <div className="grid grid-cols-5 gap-px bg-border border border-border">
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
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Win Rate</div>
              <div className="text-text text-lg font-semibold">{bankrollStats.win_rate.toFixed(1)}%</div>
              <div className="text-[10px] text-muted">{bankrollStats.total_staked.toFixed(0)} kr staked</div>
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
        </div>
      )}

      {/* Bankroll Chart */}
      {bets.length > 0 && currentBankroll > 0 && (
        <BankrollChart bets={bets} currentBankroll={currentBankroll} />
      )}

      {/* CLV Trend Chart */}
      <CLVChart bets={bets} />

      {/* Active Bonuses */}
      {activeBonuses.length > 0 && (
        <div className="border-l-2 border-tabBonus">
          <div className="border border-border">
            <div className="px-3 py-2 border-b border-border bg-panel">
              <h3 className="text-muted font-semibold text-xs uppercase tracking-wider">Active Bonuses</h3>
            </div>
            <div className="divide-y divide-border">
              {activeBonuses.map(([providerId, bonus]) => {
                const pct = Math.min(100, bonus.progress_pct);
                const days = bonus.days_remaining;
                const urgent = days !== null && days <= 10;
                const warning = days !== null && days > 10 && days <= 30;

                return (
                  <div key={providerId} className="px-3 py-2.5 space-y-1.5">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-text text-sm font-medium">{formatProviderName(providerId)}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 font-medium ${
                          bonus.status === 'trigger_needed' ? 'bg-amber-400/15 text-amber-400' :
                          bonus.status === 'freebet_available' ? 'bg-success/15 text-success' :
                          'bg-tabBonus/15 text-tabBonus'
                        }`}>
                          {bonus.status === 'trigger_needed' ? 'TRIGGER NEEDED' :
                           bonus.status === 'freebet_available' ? 'FREEBET READY' :
                           `${pct.toFixed(0)}%`}
                        </span>
                        {days !== null && (
                          <span className={`text-[10px] font-mono ${urgent ? 'text-error' : warning ? 'text-amber-400' : 'text-muted'}`}>
                            {days}d left
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2" />
                    </div>

                    <div className="text-xs text-muted">{bonus.action_needed}</div>

                    {bonus.status === 'in_progress' && bonus.wagering_requirement > 0 && (
                      <div className="space-y-1">
                        <div className="h-1.5 bg-panel overflow-hidden">
                          <div
                            className={`h-full transition-all duration-500 ${
                              urgent ? 'bg-error' : warning ? 'bg-amber-400' : 'bg-tabBonus'
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <div className="flex items-center justify-between text-[10px] text-muted2">
                          <span>{bonus.wagered_amount.toFixed(0)} / {bonus.wagering_requirement.toFixed(0)} kr</span>
                          <span>{(bonus.wagering_requirement - bonus.wagered_amount).toFixed(0)} kr remaining</span>
                        </div>
                      </div>
                    )}

                    {bonus.prognosis && bonus.status === 'in_progress' && (() => {
                      const p = bonus.prognosis;
                      const betsPlaced = p.bets_per_week;
                      const needBetsWk = p.required_weekly_wagering > 0 && p.avg_stake > 0
                        ? Math.ceil(p.required_weekly_wagering / p.avg_stake)
                        : null;
                      const estDays = p.est_weeks !== null ? Math.round(p.est_weeks * 7) : null;
                      const onTrack = p.required_weekly_wagering > 0 && p.weekly_wagering >= p.required_weekly_wagering;

                      return (
                        <div className="flex items-center gap-3 text-[10px]">
                          {needBetsWk !== null ? (
                            <span className={onTrack ? 'text-success' : 'text-amber-400'}>
                              {Math.round(betsPlaced)}/{needBetsWk} bets/wk
                            </span>
                          ) : betsPlaced > 0 ? (
                            <span className="text-muted2">{Math.round(betsPlaced)} bets/wk</span>
                          ) : (
                            <span className="text-muted2">No qualifying bets yet</span>
                          )}
                          {p.avg_stake > 0 && (
                            <span className="text-muted2">{p.avg_stake} kr avg</span>
                          )}
                          {estDays !== null && days !== null ? (
                            estDays <= days ? (
                              <span className="text-success">~{estDays}d to clear</span>
                            ) : (
                              <span className="text-error">~{estDays}d to clear ({days}d left)</span>
                            )
                          ) : estDays !== null ? (
                            <span className="text-muted2">~{estDays}d to clear</span>
                          ) : null}
                          {p.required_weekly_wagering > 0 && !onTrack && (
                            <span className="text-amber-400">need {p.required_weekly_wagering} kr/wk</span>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Provider Quick Actions */}
      {providerIds.length > 0 && (
        <div className="border border-border bg-panel rounded">
          <button
            onClick={() => setShowProviderActions(p => !p)}
            className="w-full px-3 py-2 flex items-center justify-between text-xs text-muted hover:text-text"
          >
            <span className="font-medium uppercase tracking-wider">Provider Actions</span>
            <span>{showProviderActions ? '\u25BC' : '\u25B6'} {providerIds.length} providers</span>
          </button>
          {showProviderActions && (
            <div className="border-t border-border max-h-64 overflow-y-auto">
              <table className="w-full text-xs sq">
                <thead>
                  <tr className="text-muted text-left border-b border-border">
                    <th className="px-2 py-1.5">Provider</th>
                    <th className="px-2 py-1.5 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {providerIds.map(pid => (
                    <tr key={pid} className="border-t border-border/30 hover:bg-panel2">
                      <td className="px-2 py-1.5 font-medium">{formatProviderName(pid)}</td>
                      <td className="px-2 py-1.5">
                        <div className="flex gap-1 justify-center">
                          <button
                            onClick={() => openWorkflow(pid, 'my_bets')}
                            className="px-2 py-0.5 text-[11px] text-muted hover:text-text bg-panel2 rounded"
                          >
                            My Bets&thinsp;&#8599;
                          </button>
                          <button
                            onClick={() => openWorkflow(pid, 'bet_history')}
                            className="px-2 py-0.5 text-[11px] text-muted hover:text-text bg-panel2 rounded"
                          >
                            History&thinsp;&#8599;
                          </button>
                          <button
                            onClick={() => openWorkflow(pid, 'view_score')}
                            className="px-2 py-0.5 text-[11px] text-muted hover:text-text bg-panel2 rounded"
                          >
                            Score&thinsp;&#8599;
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Bet History */}
      {isLoading && bets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
      ) : historyBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No bets found.</div>
      ) : (
        <>
          <h3 className="text-xs text-muted uppercase tracking-wider font-semibold">
            History <span className="text-muted2">{historyBets.length}</span>
          </h3>
          <div className="border-l-2 border-tabBets">
          <table className="sq">
            <thead>
              <tr>
                <SortHeader label="Date" sortKey="date" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Provider" sortKey="provider" currentSort={sort} onSort={handleSort} />
                <SortHeader label="Odds" sortKey="odds" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Close" sortKey="close" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="CLV" sortKey="clv" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Edge" sortKey="edge" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Stake" sortKey="stake" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Profit" sortKey="profit" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Prob" sortKey="prob" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="TTK" sortKey="ttk" currentSort={sort} onSort={handleSort} align="right" />
                <SortHeader label="Status" sortKey="status" currentSort={sort} onSort={handleSort} align="right" />
              </tr>
            </thead>
            <tbody>
              {historyBets.map((bet) => {
                const isExpanded = expandedIdx === bet.id;
                const isEditing = editingBetId === bet.id;
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
                      <td className="text-text text-sm">{formatProviderName(bet.provider)}</td>
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
                      <td className="text-right text-text text-sm">{bet.stake.toFixed(0)} kr</td>
                      <td className="text-right">
                        <span className={`text-sm font-medium ${bet.profit >= 0 ? 'text-success' : 'text-error'}`}>
                          {bet.profit >= 0 ? '+' : ''}{bet.profit.toFixed(0)} kr
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
                              {!isEditing && (
                                <button
                                  className="text-[10px] px-1.5 py-0.5 bg-accent/15 text-accent hover:bg-accent/30 transition-colors ml-auto"
                                  onClick={() => startEditing(bet)}
                                >Edit</button>
                              )}
                            </div>
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
      )}
    </div>
  );
}
