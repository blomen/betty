import { useMemo } from 'react';
import type { Bet } from '@/types';
import type { EquityPoint } from './equity';

// ── Charts ───────────────────────────────────────────────────────────

// Shared chart layout — Polymarket style
export const CHART = { W: 600, H: 200, PL: 8, PR: 44, PT: 8, PB: 24 } as const;

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

export function BankrollChart({ points, totalStaked }: { points: EquityPoint[]; totalStaked?: number }) {
  const data = points;
  if (data.length < 2) return null;

  const { W, H, PL, PR, PT, PB } = CHART;

  const values = data.map(d => d.value);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const pad = (rawMax - rawMin) * 0.1 || 200;
  const yMin = rawMin - pad;
  const yMax = rawMax + pad;
  const range = yMax - yMin;

  // X-axis = bet sequence, not wall-clock time. With wall-clock x, settling
  // bunched at the right edge whenever betting clustered (e.g. one bet in
  // January, hundreds in April), producing a flat-line-then-cliff that hid
  // the actual equity walk. Sequence-x gives every bet equal width and
  // traces the real shape; date markers still appear on the axis for context.
  const xPos = (i: number) => PL + (i / (data.length - 1 || 1)) * (W - PL - PR);
  const yPos = (v: number) => PT + (1 - (v - yMin) / range) * (H - PT - PB);

  const pts = data.map((p, i) => ({ x: xPos(i), y: yPos(p.value) }));
  const { yLines, pathD } = polyChart(pts, {
    yMin, yMax,
    yFormat: v => `${(v / 1000).toFixed(1)}k`,
  });

  const lastVal = data[data.length - 1].value;
  const firstVal = data[0].value;
  const isUp = lastVal >= firstVal;
  const lineColor = isUp ? '#3fb950' : '#f85149';

  // X labels — month at first occurrence in the sequence; sparse early
  // periods and dense late periods both stay legible since each bet has
  // equal width.
  const xLabels: { label: string; pos: number }[] = [];
  const seen = new Set<string>();
  for (let i = 0; i < data.length; i++) {
    const d = data[i].date;
    const key = `${d.getFullYear()}-${d.getMonth()}`;
    if (!seen.has(key)) {
      seen.add(key);
      xLabels.push({ label: d.toLocaleDateString('en-US', { month: 'short' }), pos: xPos(i) });
    }
  }

  const profit = lastVal - firstVal;
  const roiBase = totalStaked && totalStaked > 0 ? totalStaked : firstVal;
  const profitPct = ((profit / roiBase) * 100).toFixed(1);
  const lastPt = pts[pts.length - 1];

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">Bankroll (realized P/L)</span>
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
            <line key={i} x1={PL} y1={l.py} x2={W - PR} y2={l.py} stroke="#21262d" strokeWidth="0.5" strokeDasharray="2,3" vectorEffect="non-scaling-stroke" />
          ))}
          {/* Gradient fill under line */}
          <defs>
            <linearGradient id="bankrollGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.25" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${pathD} L${pts[pts.length - 1].x.toFixed(1)},${H - PB} L${pts[0].x.toFixed(1)},${H - PB} Z`} fill="url(#bankrollGrad)" />
          {/* Net deposited baseline — shows at a glance whether we're above or below starting capital */}
          {firstVal >= yMin && firstVal <= yMax && (
            <line x1={PL} y1={yPos(firstVal)} x2={W - PR} y2={yPos(firstVal)} stroke="#484f58" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          )}
          {/* Main line */}
          <path d={pathD} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          {/* Endpoint dot */}
          <circle cx={lastPt.x} cy={lastPt.y} r="3" fill={lineColor} vectorEffect="non-scaling-stroke" />
          <circle cx={lastPt.x} cy={lastPt.y} r="5" fill={lineColor} fillOpacity="0.15" vectorEffect="non-scaling-stroke" />
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

export function CLVChart({ bets, title = 'CLV Trend', recentWindow = 10 }: { bets: Bet[]; showTTKLegend?: boolean; title?: string; recentWindow?: number }) {
  const data = useMemo(() => {
    return bets
      .filter(b => b.result !== 'pending' && b.clv_pct != null)
      .sort((a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime())
      .map(b => ({ date: new Date(b.placed_at), clv: b.clv_pct! }));
  }, [bets]);

  if (data.length < 2) return null;

  const { W, H, PL, PR, PT, PB } = CHART;

  // X-axis = bet sequence (matches BankrollChart) so clustering doesn't
  // squeeze the line to one edge of the chart.
  const xPos = (i: number, n: number) => PL + (i / (n - 1 || 1)) * (W - PL - PR);

  // Cumulative average (running mean of ALL bets up to each point)
  const cumAvgAll: { date: Date; avg: number; idx: number }[] = [];
  let cumSum = 0;
  for (let i = 0; i < data.length; i++) {
    cumSum += data[i].clv;
    cumAvgAll.push({ date: data[i].date, avg: cumSum / (i + 1), idx: i });
  }

  // Downsample to ~80 points for a smooth line
  const maxPts = 80;
  const step = Math.max(1, Math.floor(cumAvgAll.length / maxPts));
  const avgPoints: { date: Date; avg: number; idx: number }[] = [];
  for (let i = 0; i < cumAvgAll.length; i += step) {
    avgPoints.push(cumAvgAll[i]);
  }
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

  const total = data.length;
  const clvPts = avgPoints.map(p => ({ x: xPos(p.idx, total), y: yPos(p.avg) }));
  const { yLines, pathD: avgPathD } = polyChart(clvPts, {
    yMin, yMax,
    yFormat: v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`,
  });

  const totalAvg = data.reduce((s, d) => s + d.clv, 0) / data.length;
  const isPositive = totalAvg >= 0;
  const positiveCount = data.filter(d => d.clv >= 0).length;
  const beatPct = ((positiveCount / data.length) * 100).toFixed(0);

  // Trailing-window CLV — a strategy that USED to beat the close but no
  // longer does shows up here first (cumulative avg lags by sample size).
  // Coloured RED when the last `recentWindow` bets average below zero, so
  // the user notices before the cumulative line crosses over.
  const recent = data.slice(-recentWindow);
  const recentAvg = recent.length ? recent.reduce((s, d) => s + d.clv, 0) / recent.length : 0;
  const recentPositive = recentAvg >= 0;

  // X labels — month at first occurrence in sequence
  const xLabels: { label: string; pos: number }[] = [];
  const seen = new Set<string>();
  for (let i = 0; i < data.length; i++) {
    const d = data[i].date;
    const key = `${d.getFullYear()}-${d.getMonth()}`;
    if (!seen.has(key)) {
      seen.add(key);
      xLabels.push({ label: d.toLocaleDateString('en-US', { month: 'short' }), pos: xPos(i, total) });
    }
  }

  const lastPt = clvPts[clvPts.length - 1];
  const lineColor = isPositive ? '#3fb950' : '#f85149';

  return (
    <div className="bg-[#0d1117] overflow-hidden">
      <div className="px-3 py-2 flex items-center justify-between">
        <span className="text-[11px] text-[#8b949e] uppercase tracking-wider font-medium">{title}</span>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-[#484f58]">{data.length} bets</span>
          <span className="text-[10px] text-[#484f58]">{beatPct}% beat close</span>
          {recent.length >= 3 && (
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded ${recentPositive ? 'bg-[#3fb950]/15 text-[#3fb950]' : 'bg-[#f85149]/15 text-[#f85149]'}`}
              title={`Trailing avg over the last ${recent.length} settled bets — flips red when the strategy stops beating the close.`}
            >
              last {recent.length}: {recentAvg >= 0 ? '+' : ''}{recentAvg.toFixed(1)}%
            </span>
          )}
          <span className={`text-sm font-semibold ${isPositive ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
            {totalAvg >= 0 ? '+' : ''}{totalAvg.toFixed(1)}% avg
          </span>
        </div>
      </div>
      <div className="relative" style={{ paddingBottom: '33.3%' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
          {/* Dotted horizontal grid lines */}
          {yLines.map((l, i) => (
            <line key={i} x1={PL} y1={l.py} x2={W - PR} y2={l.py} stroke="#21262d" strokeWidth="0.5" strokeDasharray="2,3" vectorEffect="non-scaling-stroke" />
          ))}
          {/* Zero line — clear and prominent */}
          <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="#484f58" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {/* Gradient fill under line */}
          <defs>
            <linearGradient id="clvGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.25" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${avgPathD} L${clvPts[clvPts.length - 1].x.toFixed(1)},${H - PB} L${clvPts[0].x.toFixed(1)},${H - PB} Z`} fill="url(#clvGrad)" />
          {/* Main line */}
          <path d={avgPathD} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          {/* Endpoint dot */}
          <circle cx={lastPt.x} cy={lastPt.y} r="3" fill={lineColor} vectorEffect="non-scaling-stroke" />
          <circle cx={lastPt.x} cy={lastPt.y} r="5" fill={lineColor} fillOpacity="0.15" vectorEffect="non-scaling-stroke" />
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
