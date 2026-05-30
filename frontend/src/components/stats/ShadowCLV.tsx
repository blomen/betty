import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { ProviderName } from '@/components/ProviderName';
import { CHART } from './charts';
import type {
  OppSnapshotBreakdownRow,
  OppSnapshotHistoryPoint,
  OppSnapshotSummary,
  SportBlendComparisonRow,
  ShadingClvRow,
} from '@/services/api/oppSnapshots';

type SortDir = 'asc' | 'desc';

// ─────────────────────────────────────────────────────────────────────
// Shadow CLV view — opp_snapshots dashboard
// ─────────────────────────────────────────────────────────────────────

const TYPE_COLOR: Record<'value' | 'arb' | 'reverse_value', string> = {
  value: '#3fb950',
  arb: '#58a6ff',
  reverse_value: '#d29922',
};

export function ShadowCLVView() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['opp-snapshot-stats', 30],
    queryFn: () => api.getOppSnapshotStats(30),
    staleTime: 60_000,
  });

  if (isLoading) return <div className="text-muted text-xs p-3">Loading…</div>;
  if (error) return <div className="text-error text-xs p-3">Failed to load shadow CLV stats.</div>;
  if (!data || data.summary.total === 0) {
    return <div className="text-muted text-xs p-3">No backfilled snapshots yet — wait for tracked events to start.</div>;
  }

  return (
    <div className="space-y-3">
      <ShadowSummary summary={data.summary} />
      <MultiLineCLVChart history={data.history} />
      <BreakdownTable rows={data.breakdown} />
      <SportBlendTable rows={data.sport_blend_comparison} />
      <ShadingClvTable rows={data.shading_clv_breakdown ?? []} />
    </div>
  );
}

function ShadowSummary({ summary }: { summary: OppSnapshotSummary }) {
  const meanCls = summary.mean_pinnacle_clv_pct == null
    ? 'text-muted'
    : summary.mean_pinnacle_clv_pct >= 0 ? 'text-success' : 'text-error';
  const meanSign = (summary.mean_pinnacle_clv_pct ?? 0) >= 0 ? '+' : '';
  const beatCls = (summary.beat_close_pct ?? 0) >= 50 ? 'text-success' : 'text-error';
  return (
    <div className="border-l-2 border-tabBets">
      <div className="grid grid-cols-4 gap-px bg-border border border-border">
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Snapshots (CLV done)</div>
          <div className="text-text text-lg font-semibold">{summary.total.toLocaleString()}</div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Distinct events</div>
          <div className="text-text text-lg font-semibold">{summary.distinct_events.toLocaleString()}</div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Mean pin CLV</div>
          <div className={`text-lg font-semibold ${meanCls}`}>
            {summary.mean_pinnacle_clv_pct == null
              ? '-'
              : `${meanSign}${summary.mean_pinnacle_clv_pct.toFixed(2)}%`}
          </div>
        </div>
        <div className="bg-panel2 px-3 py-2.5">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Beat close</div>
          <div className={`text-lg font-semibold ${beatCls}`}>
            {summary.beat_close_pct == null ? '-' : `${summary.beat_close_pct.toFixed(0)}%`}
          </div>
        </div>
      </div>
      <div className="text-[10px] text-muted px-3 py-1.5 bg-panel2 border border-border border-t-0">
        Last 30 days. Snapshots are every opp the scanner detected (played or not) with realized CLV vs Pinnacle close.
      </div>
    </div>
  );
}

function MultiLineCLVChart({ history }: { history: OppSnapshotHistoryPoint[] }) {
  const { W, H, PL, PR, PT, PB } = CHART;

  // Cumulative average per type, plus order index per point for X positioning
  const byType = useMemo(() => {
    const out: Record<'value' | 'arb' | 'reverse_value', { idx: number; avg: number; clv: number }[]> = {
      value: [], arb: [], reverse_value: [],
    };
    const sums: Record<string, number> = { value: 0, arb: 0, reverse_value: 0 };
    const counts: Record<string, number> = { value: 0, arb: 0, reverse_value: 0 };
    history.forEach((p, i) => {
      const t = p.type;
      sums[t] += p.pinnacle_clv_pct;
      counts[t] += 1;
      out[t].push({ idx: i, avg: sums[t] / counts[t], clv: p.pinnacle_clv_pct });
    });
    return out;
  }, [history]);

  const n = history.length;
  if (n < 2) return null;

  const xPos = (i: number) => PL + (i / (n - 1 || 1)) * (W - PL - PR);

  // Y range from all cumulative averages
  const allAvgs = [
    ...byType.value.map(p => p.avg),
    ...byType.arb.map(p => p.avg),
    ...byType.reverse_value.map(p => p.avg),
  ];
  const rawMin = allAvgs.length ? Math.min(...allAvgs) : -5;
  const rawMax = allAvgs.length ? Math.max(...allAvgs) : 5;
  const pad = Math.max((rawMax - rawMin) * 0.2, 5);
  const yMin = Math.min(rawMin - pad, -5);
  const yMax = Math.max(rawMax + pad, 5);
  const range = yMax - yMin;
  const yPos = (v: number) => PT + (1 - (v - yMin) / range) * (H - PT - PB);
  const zeroY = yPos(0);

  // Downsample to ~80 pts per line for smoothness
  const downsample = (arr: { idx: number; avg: number }[]) => {
    if (arr.length <= 80) return arr;
    const step = Math.max(1, Math.floor(arr.length / 80));
    const out: { idx: number; avg: number }[] = [];
    for (let i = 0; i < arr.length; i += step) out.push(arr[i]);
    if (out[out.length - 1] !== arr[arr.length - 1]) out.push(arr[arr.length - 1]);
    return out;
  };

  const lineFor = (arr: { idx: number; avg: number }[]) => {
    const pts = downsample(arr).map(p => ({ x: xPos(p.idx), y: yPos(p.avg) }));
    if (pts.length < 2) return '';
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  };

  const typeStats = (['value', 'arb', 'reverse_value'] as const).map(t => {
    const arr = byType[t];
    if (!arr.length) return { t, n: 0, mean: null as number | null, beat: null as number | null };
    const mean = arr[arr.length - 1].avg;
    const beat = arr.filter(p => p.clv >= 0).length / arr.length * 100;
    return { t, n: arr.length, mean, beat };
  });

  return (
    <div className="bg-[#0d1117] overflow-hidden border border-border">
      <div className="px-3 py-2 flex items-center justify-between flex-wrap gap-x-3 gap-y-1">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">Cumulative CLV by strategy</span>
        <div className="flex items-center gap-3">
          {typeStats.map(s => s.n === 0 ? null : (
            <span key={s.t} className="flex items-center gap-1 text-[10px]">
              <span className="inline-block w-2 h-2 rounded-sm" style={{ background: TYPE_COLOR[s.t] }} />
              <span className="text-[#484f58]">{s.t.replace('_', ' ')}</span>
              <span className="text-[#8b949e]">n={s.n}</span>
              <span className={s.mean != null && s.mean >= 0 ? 'text-success' : 'text-error'}>
                {s.mean == null ? '-' : `${s.mean >= 0 ? '+' : ''}${s.mean.toFixed(1)}%`}
              </span>
              {s.beat != null && (
                <span className="text-[#484f58]">{s.beat.toFixed(0)}% beat</span>
              )}
            </span>
          ))}
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          {/* Zero line */}
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#484f58" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {(['value', 'arb', 'reverse_value'] as const).map(t => {
            const path = lineFor(byType[t]);
            if (!path) return null;
            return (
              <path
                key={t}
                d={path}
                fill="none"
                stroke={TYPE_COLOR[t]}
                strokeWidth="1.5"
                strokeLinejoin="round"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        </svg>
        <span className="absolute text-[10px] text-[#8b949e] -translate-y-1/2" style={{ top: `${(zeroY / H * 100).toFixed(2)}%`, right: '4px' }}>0%</span>
      </div>
    </div>
  );
}

type ShadowSortKey = 'provider' | 'type' | 'market' | 'n' | 'pin_clv' | 'prov_clv' | 'edge';

function BreakdownTable({ rows }: { rows: OppSnapshotBreakdownRow[] }) {
  const [sort, setSort] = useState<{ key: ShadowSortKey; dir: SortDir }>({ key: 'n', dir: 'desc' });

  const sorted = useMemo(() => {
    const arr = [...rows];
    arr.sort((a, b) => {
      const va = getShadowSortVal(a, sort.key);
      const vb = getShadowSortVal(b, sort.key);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === 'string' && typeof vb === 'string') {
        return sort.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      return sort.dir === 'asc' ? (va as number) - (vb as number) : (vb as number) - (va as number);
    });
    return arr;
  }, [rows, sort]);

  if (rows.length === 0) {
    return <div className="text-muted text-xs p-3 border border-border">No combos with n ≥ 3 yet.</div>;
  }

  const cycle = (key: ShadowSortKey) => () => {
    setSort(s => s.key === key
      ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: key === 'provider' || key === 'type' || key === 'market' ? 'asc' : 'desc' });
  };

  const header = (label: string, key: ShadowSortKey, align: 'left' | 'right' = 'left') => (
    <th
      onClick={cycle(key)}
      className={`px-2 py-1.5 text-[10px] uppercase tracking-wider cursor-pointer select-none hover:text-text ${
        align === 'right' ? 'text-right' : 'text-left'
      } ${sort.key === key ? 'text-text' : 'text-muted'}`}
    >
      {label}{sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  );

  const fmtClv = (v: number | null) => {
    if (v == null) return <span className="text-muted2">-</span>;
    const cls = v >= 0 ? 'text-success' : 'text-error';
    return <span className={cls}>{v >= 0 ? '+' : ''}{v.toFixed(2)}%</span>;
  };

  return (
    <div className="border border-border overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="bg-panel2 border-b border-border">
          <tr>
            {header('Provider', 'provider')}
            {header('Type', 'type')}
            {header('Market', 'market')}
            {header('n', 'n', 'right')}
            {header('Mean pin CLV', 'pin_clv', 'right')}
            {header('Mean prov CLV', 'prov_clv', 'right')}
            {header('Mean edge @ det', 'edge', 'right')}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={`${r.provider_id}-${r.type}-${r.market}-${i}`} className="border-b border-border/40 hover:bg-panel2/50">
              <td className="px-2 py-1 text-text"><ProviderName name={r.provider_id} /></td>
              <td className="px-2 py-1">
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-block w-1.5 h-1.5 rounded-sm" style={{ background: TYPE_COLOR[r.type] }} />
                  <span className="text-muted">{r.type.replace('_', ' ')}</span>
                </span>
              </td>
              <td className="px-2 py-1 text-muted">{r.market}</td>
              <td className="px-2 py-1 text-right text-text tabular-nums">{r.n}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmtClv(r.mean_pinnacle_clv_pct)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmtClv(r.mean_provider_clv_pct)}</td>
              <td className="px-2 py-1 text-right tabular-nums text-muted">
                {r.mean_edge_at_detection == null ? '-' : `${r.mean_edge_at_detection >= 0 ? '+' : ''}${r.mean_edge_at_detection.toFixed(2)}%`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function getShadowSortVal(r: OppSnapshotBreakdownRow, key: ShadowSortKey): number | string | null {
  switch (key) {
    case 'provider': return r.provider_id;
    case 'type': return r.type;
    case 'market': return r.market;
    case 'n': return r.n;
    case 'pin_clv': return r.mean_pinnacle_clv_pct;
    case 'prov_clv': return r.mean_provider_clv_pct;
    case 'edge': return r.mean_edge_at_detection;
  }
}

function SportBlendTable({ rows }: { rows: SportBlendComparisonRow[] }) {
  if (!rows.length) {
    return (
      <div className="text-muted text-xs p-3">
        No blended-vs-Pinnacle data yet — accumulating shadow CLV.
      </div>
    );
  }
  const fmt = (v: number | null) =>
    v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  return (
    <div className="mt-4">
      <h3 className="text-[10px] text-muted uppercase tracking-wider font-semibold mb-1">
        Blended sharp line vs Pinnacle (per sport)
      </h3>
      <div className="border border-border overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-panel2 border-b border-border">
            <tr>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-left text-muted">Sport</th>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-right text-muted">n</th>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-right text-muted">Pinnacle CLV</th>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-right text-muted">Blended CLV</th>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-right text-muted">Δ (blend − pin)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.sport} className="border-b border-border/40 hover:bg-panel2/50">
                <td className="px-2 py-1 text-text">{r.sport}</td>
                <td className="px-2 py-1 text-right text-text tabular-nums">{r.n}</td>
                <td className="px-2 py-1 text-right tabular-nums">{
                  r.mean_pinnacle_clv_pct == null
                    ? <span className="text-muted2">-</span>
                    : <span className={r.mean_pinnacle_clv_pct >= 0 ? 'text-success' : 'text-error'}>{fmt(r.mean_pinnacle_clv_pct)}</span>
                }</td>
                <td className="px-2 py-1 text-right tabular-nums">{
                  r.mean_blended_clv_pct == null
                    ? <span className="text-muted2">-</span>
                    : <span className={r.mean_blended_clv_pct >= 0 ? 'text-success' : 'text-error'}>{fmt(r.mean_blended_clv_pct)}</span>
                }</td>
                <td className="px-2 py-1 text-right tabular-nums">{
                  r.delta == null
                    ? <span className="text-muted2">-</span>
                    : <span className={r.delta > 0 ? 'text-success' : r.delta < 0 ? 'text-error' : 'text-muted'}>{fmt(r.delta)}</span>
                }</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Shading risk vs realized CLV per odds bucket (folded in from main's
// shading-aware diagnostic when this Stats refactor merged origin/main).
function ShadingClvTable({ rows }: { rows: ShadingClvRow[] }) {
  if (!rows.length) {
    return (
      <div className="text-muted text-xs p-3">
        No shading risk data yet — accumulating shadow CLV.
      </div>
    );
  }
  const fmt = (v: number | null) =>
    v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  return (
    <div className="mt-4">
      <h3 className="text-[10px] text-muted uppercase tracking-wider font-semibold mb-1">
        Shading risk vs CLV (per odds bucket)
      </h3>
      <div className="border border-border overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-panel2 border-b border-border">
            <tr>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-left text-muted">Odds bucket</th>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-left text-muted">Shading risk</th>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-right text-muted">n</th>
              <th className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-right text-muted">Mean CLV %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.odds_bucket}-${r.shading_risk}`} className="border-b border-border/40 hover:bg-panel2/50">
                <td className="px-2 py-1 text-text">{r.odds_bucket}</td>
                <td className="px-2 py-1 text-text">{r.shading_risk}</td>
                <td className="px-2 py-1 text-right text-text tabular-nums">{r.n}</td>
                <td className="px-2 py-1 text-right tabular-nums">{
                  r.mean_pinnacle_clv_pct == null
                    ? <span className="text-muted2">-</span>
                    : <span className={r.mean_pinnacle_clv_pct >= 0 ? 'text-success' : 'text-error'}>{fmt(r.mean_pinnacle_clv_pct)}</span>
                }</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
