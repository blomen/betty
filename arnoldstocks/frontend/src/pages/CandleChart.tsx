import { useRef, useEffect, useState, useCallback } from 'react';
import {
  createChart,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type HistogramData,
  type CandlestickData,
  type LineData,
  type Time,
  ColorType,
} from 'lightweight-charts';
import { api } from '@/hooks/useApi';
import { computeVP, computeVPByDay, computeVWAP, computeSessionLevels, computeAllDayTPOs } from '@/lib/indicators';
import type { CandleData, ExpandedSession, SessionTPOResponse, SessionTPOData, Signal, Fill, ExitEvent, ModelStatus } from '@/types';

const INITIAL_DAYS = 3;
const SCROLL_DAYS = 1;

// VP overlay config: which timeframes to show, with colors
const VP_OVERLAYS = [
  { tf: 'session', color: [168, 85, 247],  label: 'D' },   // purple
  { tf: 'weekly',  color: [236, 72, 153],  label: 'W' },   // pink
  { tf: 'monthly', color: [234, 179, 8],   label: 'M' },   // yellow
] as const;

// Session box definitions (CET/CEST times as hour*60+minute)
// Tokyo: 00:00 → 09:00 CET  (Asian session, overlaps London 08:00-09:00)
// London: 08:00 → 16:30 CET  (LSE hours, overlaps Tokyo & NY)
// New York: 15:30 → 22:00 CET  (NY open → close, overlaps London 15:30-16:30)
const SESSION_DEFS = [
  { name: 'Tokyo',    startMin: 0,             endMin: 9 * 60,        color: 'rgba(6, 182, 212, 0.12)',  border: 'rgba(6, 182, 212, 0.35)',  label: '#06B6D4' },
  { name: 'London',   startMin: 8 * 60,        endMin: 16 * 60 + 30,  color: 'rgba(16, 185, 129, 0.12)', border: 'rgba(16, 185, 129, 0.35)', label: '#10B981' },
  { name: 'New York', startMin: 15 * 60 + 30,  endMin: 22 * 60,       color: 'rgba(239, 68, 68, 0.10)',  border: 'rgba(239, 68, 68, 0.30)',  label: '#EF4444' },
];

// Accurate CET/CEST offset using Intl API — handles DST transitions correctly
const _cetFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Europe/Stockholm',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
});

function _parseCETDate(epoch: number): { year: number; month: number; day: number; hour: number; minute: number } {
  const parts = _cetFormatter.formatToParts(new Date(epoch * 1000));
  const get = (t: string) => parseInt(parts.find(p => p.type === t)?.value || '0', 10);
  return { year: get('year'), month: get('month'), day: get('day'), hour: get('hour'), minute: get('minute') };
}

function epochToCETMinute(epoch: number): number {
  const { hour, minute } = _parseCETDate(epoch);
  return hour * 60 + minute;
}



interface SessionBox {
  name: string;
  high: number;
  low: number;
  startEpoch: number;
  endEpoch: number;
  color: string;
  border: string;
  labelColor: string;
  cetDate: string;
}

type VPData = { levels: Array<{ price: number; volume: number }>; poc: number; vah: number; val: number };

// lightweight-charts displays UTC timestamps on its axis. To show local time,
// shift each epoch by the browser's UTC offset. This makes the axis display
// the user's local timezone (e.g., CET/CEST for Sweden) automatically,
// including DST transitions.
function toLocalEpoch(utcEpoch: number): number {
  const offsetSeconds = new Date(utcEpoch * 1000).getTimezoneOffset() * -60;
  return utcEpoch + offsetSeconds;
}

/** Get CET date string (YYYY-MM-DD) from a UTC epoch. CET = UTC+1, CEST = UTC+2. */
function epochToCETDate(epoch: number): string {
  // Stockholm timezone gives CET/CEST automatically
  const d = new Date(epoch * 1000);
  return d.toLocaleDateString('sv-SE', { timeZone: 'Europe/Stockholm' });
}

/** Get today's CET date string */
function todayCET(): string {
  return new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Stockholm' });
}

interface Props {
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
  hiddenLevels?: Set<string>;
  zones?: Array<{ price: number; members: number }>;
  signals?: Signal[];
  fills?: Fill[];
  exits?: ExitEvent[];
  modelStatus?: ModelStatus | null;
  interval?: '1m' | '5m' | '15m';
}

function toCandle(c: CandleData): CandlestickData<Time> {
  return { time: toLocalEpoch(c.t) as Time, open: c.o, high: c.h, low: c.l, close: c.c };
}

function toVolume(c: CandleData): HistogramData<Time> {
  const color = c.c >= c.o ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)';
  return { time: toLocalEpoch(c.t) as Time, value: c.v, color };
}

function epochToDateStr(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 10);
}

/** Build session boxes — X from backend time boundaries, Y from chart candles within the window.
 *  Only draws boxes for the latest day with session data. */
function buildSessionBoxes(
  slDays: import('@/types').SessionLevelDay[],
  candles: CandleData[],
): SessionBox[] {
  if (candles.length === 0) return [];

  const boxes: SessionBox[] = [];
  const mapping: Array<{
    name: string;
    startField: keyof import('@/types').SessionLevelDay;
    endField: keyof import('@/types').SessionLevelDay;
    def: typeof SESSION_DEFS[number];
  }> = [
    { name: 'Tokyo',    startField: 'tokyo_start',  endField: 'tokyo_end',  def: SESSION_DEFS[0] },
    { name: 'London',   startField: 'london_start', endField: 'london_end', def: SESSION_DEFS[1] },
    { name: 'New York', startField: 'ny_start',     endField: 'ny_end',     def: SESSION_DEFS[2] },
  ];

  for (const day of slDays) {
    if (day.ny_high == null && day.tokyo_high == null) continue;

    for (const m of mapping) {
      const sessionStart = day[m.startField] as number;
      const sessionEnd = day[m.endField] as number;
      if (!sessionStart || !sessionEnd) continue;

      const sessionCandles = candles.filter(c => c.t >= sessionStart && c.t < sessionEnd);
      if (sessionCandles.length === 0) continue;

      const high = Math.max(...sessionCandles.map(c => c.h));
      const low = Math.min(...sessionCandles.map(c => c.l));

      boxes.push({
        name: m.name,
        high,
        low,
        startEpoch: sessionStart,
        endEpoch: sessionEnd,
        color: m.def.color,
        border: m.def.border,
        labelColor: m.def.label,
        cetDate: day.date,
      });
    }
  }

  return boxes;
}

/** Deduplicate by timestamp and sort ascending — prevents lightweight-charts "Cannot update oldest data" crash. */
function dedupeAndSort(candles: CandleData[]): CandleData[] {
  const map = new Map<number, CandleData>();
  for (const c of candles) map.set(c.t, c); // last-write-wins for dupes
  return Array.from(map.values()).sort((a, b) => a.t - b.t);
}

export function CandleChart({ lastCandle, session, hiddenLevels, zones, signals, fills, exits, modelStatus, interval = '1m' }: Props) {
  const CACHE_KEY = `arnoldstocks_candles_v2_${interval}`;
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const [noData, setNoData] = useState(false);
  const [loading, setLoading] = useState(true);
  const priceLineRefs = useRef<Record<string, any>>({});
  const anchorSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapSeriesRefs = useRef<ISeriesApi<'Line'>[]>([]);

  // Scroll-back state
  const candlesRef = useRef<CandleData[]>([]);
  const fetchingRef = useRef(false);
  const exhaustedRef = useRef(false);

  // VP overlay data (global: weekly/monthly, today's session)
  const vpDataRef = useRef<Map<string, VPData>>(new Map());
  // Historical per-day session VP keyed by date string (YYYY-MM-DD)
  const vpHistoryRef = useRef<Map<string, VPData>>(new Map());
  const vpHistoryFetchedRef = useRef<Set<string>>(new Set());
  const [vpLoaded, setVpLoaded] = useState(0); // trigger redraws
  const hiddenRef = useRef(hiddenLevels);
  hiddenRef.current = hiddenLevels;

  // Session levels overlay data (per-day IB, Tokyo, London, swing levels)
  const sessionLevelsRef = useRef<import('@/types').SessionLevelDay[]>([]);
  const [slLoaded, setSlLoaded] = useState(false);
  const [structLoaded, setStructLoaded] = useState(false);

  // Swing pivot levels from server (stored separately so client-side recompute doesn't clobber them)
  const swingPivotsRef = useRef<import('@/types').SwingPivot[]>([]);

  // FVGs and order blocks from /levels endpoint
  const fvgsRef = useRef<Array<{ low: number; high: number; direction: string }>>([]);
  const obsRef = useRef<Array<{ low: number; high: number; direction: string }>>([]);

  // Per-day TPO data (date -> SessionTPOResponse)
  const sessionTPOMapRef = useRef<Map<string, SessionTPOResponse>>(new Map());
  const [sessionTPOLoaded, setSessionTPOLoaded] = useState(false);

  // Zones ref
  const zonesRef = useRef<Array<{ price: number; members: number }>>([]);
  useEffect(() => {
    zonesRef.current = zones ?? [];
  }, [zones]);

  // Signals/fills/exits refs
  const signalsRef = useRef<Signal[]>([]);
  useEffect(() => { signalsRef.current = signals ?? []; }, [signals]);
  const fillsRef = useRef<Fill[]>([]);
  useEffect(() => { fillsRef.current = fills ?? []; }, [fills]);
  const exitsRef = useRef<ExitEvent[]>([]);
  useEffect(() => { exitsRef.current = exits ?? []; }, [exits]);
  const modelStatusRef = useRef<ModelStatus | null>(null);
  useEffect(() => { modelStatusRef.current = modelStatus ?? null; }, [modelStatus]);

  // Draw VP histograms + session boxes on canvas
  const drawOverlays = useCallback(() => {
    const canvas = canvasRef.current;
    const chart = chartRef.current;
    const pSeries = priceSeriesRef.current;
    if (!canvas || !chart || !pSeries) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const timeScale = chart.timeScale();

    // --- Session boxes (full-height time columns from backend SessionLevelDay) ---
    const slDays = sessionLevelsRef.current;
    const boxes = slDays.length > 0 ? buildSessionBoxes(slDays, candlesRef.current) : [];

    if (boxes.length > 0) {
      for (const box of boxes) {
        const rawX1 = timeScale.timeToCoordinate(toLocalEpoch(box.startEpoch) as Time);
        const rawX2 = timeScale.timeToCoordinate(toLocalEpoch(box.endEpoch) as Time);
        const rawY1 = pSeries.priceToCoordinate(box.high);
        const rawY2 = pSeries.priceToCoordinate(box.low);

        if (rawX1 === null && rawX2 === null) continue;
        if (rawY1 === null && rawY2 === null) continue;
        const x1 = rawX1 != null ? Math.max(0, rawX1) : 0;
        const x2 = rawX2 != null ? Math.min(rect.width, rawX2) : rect.width;
        if (x2 < 0 || x1 > rect.width) continue;
        const y1 = rawY1 != null ? Math.max(0, rawY1) : 0;
        const y2 = rawY2 != null ? Math.min(rect.height, rawY2) : rect.height;

        const bx = Math.min(x1, x2);
        const bw = Math.abs(x2 - x1);
        const by = Math.min(y1, y2);
        const bh = Math.abs(y2 - y1);

        // Fill
        ctx.fillStyle = box.color;
        ctx.fillRect(bx, by, bw, bh);

        // Border
        ctx.strokeStyle = box.border;
        ctx.lineWidth = 1;
        ctx.strokeRect(bx, by, bw, bh);

        // Label at top-right
        ctx.font = '10px monospace';
        ctx.fillStyle = box.labelColor;
        ctx.textAlign = 'right';
        ctx.fillText(box.name, bx + bw - 3, by + 11);
      }
    }

    // --- VP histograms on right edge (daily / weekly / monthly stacked) ---
    const vpMap = vpDataRef.current;
    const vpHistory = vpHistoryRef.current;
    const priceScaleWidth = 65;
    const xRight = rect.width - priceScaleWidth;
    const maxBarWidth = 120;

    const hidden = hiddenRef.current;

    // Helper: draw a single VP histogram with pixel-bucketing for crisp rendering
    // showLabels: only draw POC/VAH/VAL text labels for today's daily VP (price lines handle the rest)
    const drawVPHistogram = (vp: VPData, color: [number, number, number], isDaily: boolean, showLabels = false) => {
      const maxVol = Math.max(...vp.levels.map(l => l.volume));
      if (maxVol <= 0) return;
      const [r, g, b] = color;

      // Bucket levels by pixel row — prevents fuzzy overlap when zoomed out
      const pixelBuckets = new Map<number, { volume: number; isPOC: boolean; inVA: boolean }>();
      const pocY = pSeries.priceToCoordinate(vp.poc);

      for (const level of vp.levels) {
        const rawY = pSeries.priceToCoordinate(level.price);
        if (rawY === null || rawY < -1 || rawY > rect.height + 1) continue;

        const py = Math.round(rawY);
        const existing = pixelBuckets.get(py);
        const isPOC = level.price === vp.poc;
        const inVA = level.price >= vp.val && level.price <= vp.vah;

        if (existing) {
          existing.volume += level.volume;
          if (isPOC) existing.isPOC = true;
          if (inVA) existing.inVA = true;
        } else {
          pixelBuckets.set(py, { volume: level.volume, isPOC, inVA });
        }
      }

      // Recompute max after bucketing
      let bucketMax = 0;
      for (const b of pixelBuckets.values()) {
        if (b.volume > bucketMax) bucketMax = b.volume;
      }
      if (bucketMax <= 0) return;

      // Determine bar height: at least 1px, scale with zoom level
      // When few buckets relative to height, bars can be thicker
      const barH = Math.max(1, Math.min(4, Math.floor(rect.height / Math.max(pixelBuckets.size, 1))));

      // Draw bars
      for (const [py, bucket] of pixelBuckets) {
        if (py < 0 || py > rect.height) continue;

        const barW = (bucket.volume / bucketMax) * maxBarWidth;
        if (barW < 0.5) continue; // skip invisible bars

        ctx.fillStyle = bucket.isPOC
          ? `rgba(${r}, ${g}, ${b}, 0.8)`
          : bucket.inVA
            ? `rgba(${r}, ${g}, ${b}, 0.35)`
            : `rgba(${r}, ${g}, ${b}, 0.12)`;

        ctx.fillRect(xRight - barW, py - Math.floor(barH / 2), barW, barH);
      }

      // POC label — only for today's daily VP (price lines show dPOC/wPOC/mPOC for all others)
      if (showLabels && pocY !== null && pocY >= 0 && pocY <= rect.height) {
        const pocPx = Math.round(pocY);
        const pocBucket = pixelBuckets.get(pocPx);
        const pocBarW = pocBucket ? (pocBucket.volume / bucketMax) * maxBarWidth : 0;
        ctx.font = '9px monospace';
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.9)`;
        ctx.textAlign = 'right';
        ctx.fillText('POC', xRight - pocBarW - 3, pocPx + 3);
      }

      // VAH/VAL dashed lines (only for today's daily VP — price lines handle the rest)
      if (showLabels && isDaily) {
        for (const { price, label } of [
          { price: vp.vah, label: 'VAH' },
          { price: vp.val, label: 'VAL' },
        ]) {
          const rawY = pSeries.priceToCoordinate(price);
          if (rawY === null || rawY < 0 || rawY > rect.height) continue;
          const py = Math.round(rawY);

          ctx.save();
          ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, 0.5)`;
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.moveTo(0, py + 0.5);
          ctx.lineTo(xRight, py + 0.5);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.7)`;
          ctx.textAlign = 'left';
          ctx.fillText(label, 3, py - 3);
          ctx.restore();
        }
      }
    };

    // Draw in reverse order so daily (most important) renders on top
    // Weekly/monthly: global VP
    for (let oi = VP_OVERLAYS.length - 1; oi >= 0; oi--) {
      const overlay = VP_OVERLAYS[oi];
      if (hidden?.has(`vp_${overlay.tf}`)) continue;
      if (overlay.tf === 'session') continue; // handled separately below
      const vp = vpMap.get(overlay.tf);
      if (!vp || !vp.levels.length) continue;
      drawVPHistogram(vp, overlay.color as unknown as [number, number, number], false);
    }

    // Daily session VP: today's + historical per-day
    if (!hidden?.has('vp_session')) {
      const dailyColor: [number, number, number] = [168, 85, 247]; // purple

      // Today's session VP (from global fetch) — only today gets canvas POC/VAH/VAL labels
      const todayVP = vpMap.get('session');
      if (todayVP && todayVP.levels.length) {
        drawVPHistogram(todayVP, dailyColor, true, true);
      }

      // Historical per-day VPs (lower opacity, reuse pixel-bucketing via drawVPHistogram)
      vpHistory.forEach((vp) => {
        if (!vp.levels.length) return;
        // Draw with dimmer opacity by using a slightly different color channel trick:
        // We reuse drawVPHistogram but pass isDaily=false (no VAH/VAL lines for historical)
        drawVPHistogram(vp, [148, 75, 217], false); // slightly dimmer purple
      });
    }

    // --- Session H/L levels (persist from session end to day end) ---
    const slHidden = hiddenRef.current;
    const latestSL = [...slDays].sort((a, b) => b.date.localeCompare(a.date))
      .find(d => d.ny_high != null || d.tokyo_high != null);
    // Session H/L extension lines — use backend session levels directly
    const sessionLineDefs: Array<{
      sessionName: string;
      hKey: string; lKey: string;
      hLabel: string; lLabel: string;
      color: string;
      startField: keyof import('@/types').SessionLevelDay;
      endField: keyof import('@/types').SessionLevelDay;
      highField: keyof import('@/types').SessionLevelDay;
      lowField: keyof import('@/types').SessionLevelDay;
    }> = [
      { sessionName: 'Tokyo', hKey: 'tokyo_h', lKey: 'tokyo_l', hLabel: 'TKY H', lLabel: 'TKY L', color: '#22D3EE', startField: 'tokyo_start', endField: 'tokyo_end', highField: 'tokyo_high', lowField: 'tokyo_low' },
      { sessionName: 'London', hKey: 'london_h', lKey: 'london_l', hLabel: 'LDN H', lLabel: 'LDN L', color: '#34D399', startField: 'london_start', endField: 'london_end', highField: 'london_high', lowField: 'london_low' },
    ];

    // Draw session H/L dashed lines from session end to day end (22:00 CET)
    if (latestSL) {
      for (const def of sessionLineDefs) {
        const sessionEnd = latestSL[def.endField] as number;
        if (!sessionEnd) continue;

        const lineHigh = latestSL[def.highField] as number | null;
        const lineLow = latestSL[def.lowField] as number | null;

        // Day end = 22:00 CET
        const boxEndCETMin = epochToCETMinute(sessionEnd);
        const dayEndEpoch = sessionEnd + (22 * 60 - boxEndCETMin) * 60;

        for (const { key, price, label } of [
          { key: def.hKey, price: lineHigh, label: def.hLabel },
          { key: def.lKey, price: lineLow, label: def.lLabel },
        ]) {
          if (price == null) continue;
          if (slHidden?.has(key)) continue;
          const y = pSeries.priceToCoordinate(price);
          if (y === null) continue;

          const rawX1 = timeScale.timeToCoordinate(toLocalEpoch(sessionEnd) as Time);
          const rawX2 = timeScale.timeToCoordinate(toLocalEpoch(dayEndEpoch) as Time);
          if (rawX1 === null && rawX2 === null) continue;
          const lx = rawX1 ?? 0;
          const rx = rawX2 ?? rect.width;
          if (rx < 0 || lx > rect.width) continue;
          const drawX1 = Math.max(0, lx);
          const drawX2 = Math.min(rect.width, rx);

          ctx.save();
          ctx.strokeStyle = def.color;
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(drawX1, y);
          ctx.lineTo(drawX2, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = def.color;
          ctx.textAlign = 'left';
          ctx.fillText(label, drawX1 + 3, y - 3);
          ctx.restore();
        }
      }
    }

    // Swing pivot levels are rendered as native price lines (see useEffect for session-levels fetch)
    // They're always visible on the price axis regardless of zoom level.

    // --- PDH/PDL levels — latest day only, full chart width ---
    if (latestSL) {
      const pdhpdlLevels: Array<{ price: number; label: string; key: string }> = [];
      if (latestSL.pdh != null && !slHidden?.has('pdh')) pdhpdlLevels.push({ price: latestSL.pdh, label: 'PDH', key: 'pdh' });
      if (latestSL.pdl != null && !slHidden?.has('pdl')) pdhpdlLevels.push({ price: latestSL.pdl, label: 'PDL', key: 'pdl' });

      for (const lvl of pdhpdlLevels) {
        const y = pSeries.priceToCoordinate(lvl.price);
        if (y === null) continue;

        ctx.save();
        ctx.strokeStyle = '#FB923C'; // orange-400
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(rect.width, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = '9px monospace';
        ctx.fillStyle = '#FB923C';
        ctx.textAlign = 'left';
        ctx.fillText(lvl.label, 3, y - 3);
        ctx.restore();
      }
    }

    // --- NY IB levels — latest day only ---
    const nowEpoch = Math.floor(Date.now() / 1000);
    if (latestSL) {
      const isHistorical = nowEpoch >= latestSL.day_end;
      const ibComplete = isHistorical || nowEpoch >= latestSL.ib_end;
      if (ibComplete && latestSL.ib_high != null && latestSL.ib_low != null) {
        const ibLevels: Array<{ price: number; label: string; key: string }> = [];
        if (!slHidden?.has('ibh')) ibLevels.push({ price: latestSL.ib_high, label: 'NYIBH', key: 'ibh' });
        if (!slHidden?.has('ibl')) ibLevels.push({ price: latestSL.ib_low, label: 'NYIBL', key: 'ibl' });
        // Anchor from NY open (15:30 CET) to NY close (22:00 CET)
        const ibStartX = timeScale.timeToCoordinate(toLocalEpoch(latestSL.ny_start) as Time);
        const ibEndX = timeScale.timeToCoordinate(toLocalEpoch(latestSL.ny_end) as Time);
        if (ibStartX !== null || ibEndX !== null) {
          for (const ib of ibLevels) {
            const y = pSeries.priceToCoordinate(ib.price);
            if (y === null) continue;
            const x1 = ibStartX != null ? Math.max(0, ibStartX) : 0;
            const x2 = ibEndX != null ? Math.min(rect.width, ibEndX) : rect.width;
            if (x2 < 0 || x1 > rect.width) continue;
            ctx.save();
            ctx.strokeStyle = '#F59E0B';
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.beginPath();
            ctx.moveTo(x1, y);
            ctx.lineTo(x2, y);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.font = '9px monospace';
            ctx.fillStyle = '#F59E0B';
            ctx.textAlign = 'left';
            ctx.fillText(ib.label, x1 + 3, y - 3);
            ctx.restore();
          }
        }
      }
    }

    // --- Per-session TPO histograms (VP-style bars inside session boxes) ---
    const tpoMap = sessionTPOMapRef.current;
    if (tpoMap.size > 0 && boxes.length > 0) {
      const SESSION_TPO_COLORS: Record<string, { hiddenKey: string; color: [number, number, number]; levelColor: string; sessionKey: 'tokyo' | 'london' | 'ny' }> = {
        'Tokyo':    { hiddenKey: 'tpo_tky_letters', color: [8, 145, 178],  levelColor: '#06B6D4', sessionKey: 'tokyo' },
        'London':   { hiddenKey: 'tpo_ldn_letters', color: [5, 150, 105],  levelColor: '#10B981', sessionKey: 'london' },
        'New York': { hiddenKey: 'tpo_ny_letters',  color: [220, 38, 38],  levelColor: '#EF4444', sessionKey: 'ny' },
      };

      for (const box of boxes) {
        const tpoMeta = SESSION_TPO_COLORS[box.name];
        if (!tpoMeta || hidden?.has(tpoMeta.hiddenKey)) continue;
        // Look up TPO data for this box's specific date
        const dayTPO = tpoMap.get(box.cetDate);
        if (!dayTPO) continue;
        const tpoSession = dayTPO.sessions[tpoMeta.sessionKey];
        if (!tpoSession) continue;
        const [r, g, b] = tpoMeta.color;
        const levelColor = tpoMeta.levelColor;

        // Box edges
        const boxLeftX = timeScale.timeToCoordinate(toLocalEpoch(box.startEpoch) as Time);
        const boxRightX = timeScale.timeToCoordinate(toLocalEpoch(box.endEpoch) as Time);
        if (boxLeftX === null && boxRightX === null) continue;
        const startX = (boxLeftX ?? 0) + 2;
        const endX = boxRightX ?? rect.width;
        const boxWidth = Math.abs(endX - startX);
        const maxBarWidth = Math.min(boxWidth * 0.5, 100);

        // Use tpo_counts for histogram (count of TPO prints at each price)
        const tpoCounts = tpoSession.tpo_counts;
        const priceKeys = Object.keys(tpoCounts);
        if (priceKeys.length === 0) continue;

        // Pixel-bucket like VP for crisp rendering
        const pixelBuckets = new Map<number, { count: number; isPOC: boolean; inVA: boolean }>();
        let maxCount = 0;

        for (const pk of priceKeys) {
          const priceNum = Number(pk);
          const count = tpoCounts[pk];
          if (count <= 0) continue;
          const rawY = pSeries.priceToCoordinate(priceNum);
          if (rawY === null || rawY < -1 || rawY > rect.height + 1) continue;

          const py = Math.round(rawY);
          const isPOC = priceNum === tpoSession.poc;
          const inVA = priceNum >= tpoSession.val && priceNum <= tpoSession.vah;
          const existing = pixelBuckets.get(py);

          if (existing) {
            existing.count += count;
            if (isPOC) existing.isPOC = true;
            if (inVA) existing.inVA = true;
          } else {
            pixelBuckets.set(py, { count, isPOC, inVA });
          }
        }

        for (const bucket of pixelBuckets.values()) {
          if (bucket.count > maxCount) maxCount = bucket.count;
        }
        if (maxCount <= 0) continue;

        const barH = Math.max(1, Math.min(4, Math.floor(rect.height / Math.max(pixelBuckets.size, 1))));

        // Draw histogram bars growing rightward from session box left edge
        for (const [py, bucket] of pixelBuckets) {
          if (py < 0 || py > rect.height) continue;
          const barW = (bucket.count / maxCount) * maxBarWidth;
          if (barW < 0.5) continue;

          ctx.fillStyle = bucket.isPOC
            ? `rgba(${r}, ${g}, ${b}, 0.8)`
            : bucket.inVA
              ? `rgba(${r}, ${g}, ${b}, 0.35)`
              : `rgba(${r}, ${g}, ${b}, 0.12)`;
          ctx.fillRect(startX, py - Math.floor(barH / 2), barW, barH);
        }

        ctx.globalAlpha = 1.0;

        // --- Session metadata footer at bottom of box ---
        const boxBottomY = pSeries.priceToCoordinate(box.low);
        if (boxBottomY !== null) {
          const ibRange = tpoSession.ib_valid
            ? ((tpoSession.ib_high - tpoSession.ib_low) / 0.25).toFixed(0)
            : '-';
          const arrow = tpoSession.opening_direction === 'up' ? '^'
            : tpoSession.opening_direction === 'down' ? 'v' : '-';
          const rf = tpoSession.rotation_factor ?? 0;
          const rfStr = `RF:${rf > 0 ? '+' : ''}${rf}`;
          const footerText = `${tpoSession.shape}  IB:${ibRange}  ${tpoSession.opening_type}${arrow}  ${rfStr}`;
          ctx.font = '8px monospace';
          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.5)`;
          ctx.textAlign = 'left';
          ctx.fillText(footerText, startX, boxBottomY + 12);
        }

        // --- POC/VAH/VAL dashed extension lines ---
        const dayEndEpoch = box.endEpoch + (22 * 60 - epochToCETMinute(box.endEpoch)) * 60;
        const lineEndX = timeScale.timeToCoordinate(toLocalEpoch(dayEndEpoch) as Time);

        const prefixMap: Record<string, string> = { 'Tokyo': 'tky', 'London': 'ldn', 'New York': 'ny' };
        const prefix = prefixMap[box.name] || '';

        const levels: Array<{ price: number; label: string; alpha: number; dash: number[]; key: string; color?: string }> = [
          { price: tpoSession.poc, label: `${prefix} tPOC`, alpha: 0.6, dash: [4, 3], key: `tpo_${prefix}_poc` },
          { price: tpoSession.vah, label: `${prefix} tVAH`, alpha: 0.4, dash: [2, 3], key: `tpo_${prefix}_vah` },
          { price: tpoSession.val, label: `${prefix} tVAL`, alpha: 0.4, dash: [2, 3], key: `tpo_${prefix}_val` },
        ];

        for (const lv of levels) {
          if (hidden?.has(lv.key)) continue;
          const y = pSeries.priceToCoordinate(lv.price);
          if (y === null) continue;

          const lx = startX;
          const rx = lineEndX ?? rect.width;
          if (rx < 0 || lx > rect.width) continue;

          const lvColor = lv.color ?? levelColor;
          ctx.save();
          ctx.strokeStyle = lvColor;
          ctx.globalAlpha = lv.alpha;
          ctx.lineWidth = 1;
          ctx.setLineDash(lv.dash);
          ctx.beginPath();
          ctx.moveTo(Math.max(0, lx), y);
          ctx.lineTo(Math.min(rect.width, rx), y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = lvColor;
          ctx.textAlign = 'left';
          ctx.fillText(lv.label, Math.max(0, lx) + 3, y - 3);
          ctx.globalAlpha = 1.0;
          ctx.restore();
        }
      }
    }

    // --- Zones overlay (dashed purple lines; cyan glow if FVG confluence) ---
    const currentZones = zonesRef.current;
    const currentFvgs = fvgsRef.current;
    if (currentZones.length > 0 && !hidden?.has('zones')) {
      for (const zone of currentZones) {
        const y = pSeries.priceToCoordinate(zone.price);
        if (y === null || y < 0 || y > rect.height) continue;

        // Check if this zone has FVG confluence
        const hasFvg = !hidden?.has('fvg') && currentFvgs.some(f => f.low <= zone.price && zone.price <= f.high);

        ctx.save();
        if (hasFvg) {
          // FVG confluence glow: wider band behind the zone line
          ctx.fillStyle = 'rgba(16, 185, 129, 0.08)';
          ctx.fillRect(0, y - 6, rect.width - priceScaleWidth, 12);
        }

        ctx.strokeStyle = hasFvg ? 'rgba(16, 185, 129, 0.8)' : 'rgba(168, 85, 247, 0.7)';
        ctx.lineWidth = hasFvg ? 1.5 : 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(rect.width - priceScaleWidth, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = '9px monospace';
        ctx.textAlign = 'left';
        const label = hasFvg ? `Z${zone.members} FVG` : `Z${zone.members}`;
        ctx.fillStyle = hasFvg ? 'rgba(16, 185, 129, 0.9)' : 'rgba(168, 85, 247, 0.8)';
        ctx.fillText(label, 3, y - 3);
        ctx.restore();
      }
    }

    // --- Signal markers (green ▲ CONT / red ▼ REV) ---
    const currentSignals = signalsRef.current;
    if (currentSignals.length > 0) {
      for (const sig of currentSignals) {
        if (!sig.ts || !sig.price) continue;

        const x = timeScale.timeToCoordinate(toLocalEpoch(sig.ts) as Time);
        const y = pSeries.priceToCoordinate(sig.price);
        if (x === null || y === null || x < 0 || x > rect.width) continue;

        const isCont = sig.action === 'CONT' || sig.action === 'enter_long' || sig.action === 'CONTINUATION';
        const color = isCont ? '#10B981' : '#EF4444'; // green / red
        const size = 6;

        ctx.save();
        ctx.fillStyle = color;
        ctx.beginPath();
        if (isCont) {
          // Up triangle (▲) below the candle
          ctx.moveTo(x, y + size + 4);
          ctx.lineTo(x - size, y + size * 2 + 4);
          ctx.lineTo(x + size, y + size * 2 + 4);
        } else {
          // Down triangle (▼) above the candle
          ctx.moveTo(x, y - size - 4);
          ctx.lineTo(x - size, y - size * 2 - 4);
          ctx.lineTo(x + size, y - size * 2 - 4);
        }
        ctx.closePath();
        ctx.fill();

        // Confidence label
        if (sig.confidence) {
          ctx.font = '8px monospace';
          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          const labelY = isCont ? y + size * 2 + 14 : y - size * 2 - 8;
          ctx.fillText(`${Math.round(sig.confidence * 100)}%`, x, labelY);
        }
        ctx.restore();
      }
    }

    // --- Fill markers (trade entries: blue ◆ buy / orange ◆ sell) ---
    const currentFills = fillsRef.current;
    if (currentFills.length > 0) {
      for (const fill of currentFills) {
        const x = timeScale.timeToCoordinate(toLocalEpoch(fill.ts) as Time);
        const y = pSeries.priceToCoordinate(fill.price);
        if (x === null || y === null || x < 0 || x > rect.width) continue;

        const isBuy = fill.side === 'Buy' || fill.side === 'buy';
        const color = isBuy ? '#3B82F6' : '#F97316'; // blue / orange
        const size = 5;

        ctx.save();
        // Diamond shape
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(x, y - size);
        ctx.lineTo(x + size, y);
        ctx.lineTo(x, y + size);
        ctx.lineTo(x - size, y);
        ctx.closePath();
        ctx.fill();

        // Size label
        ctx.font = '8px monospace';
        ctx.fillStyle = color;
        ctx.textAlign = 'center';
        ctx.fillText(`${fill.size}`, x, y - size - 3);
        ctx.restore();
      }
    }

    // --- Exit markers (trade exits: green ✓ profit / red ✕ stop) ---
    const currentExits = exitsRef.current;
    if (currentExits.length > 0) {
      for (const exit of currentExits) {
        const x = timeScale.timeToCoordinate(toLocalEpoch(exit.ts) as Time);
        const y = pSeries.priceToCoordinate(exit.price);
        if (x === null || y === null || x < 0 || x > rect.width) continue;

        const isStop = exit.was_stop;
        const color = isStop ? '#EF4444' : '#10B981'; // red stop / green profit
        const size = 5;

        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        if (isStop) {
          // X mark
          ctx.beginPath();
          ctx.moveTo(x - size, y - size);
          ctx.lineTo(x + size, y + size);
          ctx.moveTo(x + size, y - size);
          ctx.lineTo(x - size, y + size);
          ctx.stroke();
        } else {
          // Checkmark
          ctx.beginPath();
          ctx.moveTo(x - size, y);
          ctx.lineTo(x - 1, y + size);
          ctx.lineTo(x + size, y - size);
          ctx.stroke();
        }
        ctx.restore();
      }
    }

    // --- Live position overlay (entry + stop horizontal lines) ---
    const ms = modelStatusRef.current;
    if (ms && !ms.is_flat && ms.entry_price && ms.entry_price > 0) {
      const isLong = ms.position_side === 'long';
      const entryColor = isLong ? '#10B981' : '#EF4444'; // green long / red short
      const stopColor = '#F59E0B'; // amber stop

      // Entry line
      const entryY = pSeries.priceToCoordinate(ms.entry_price);
      if (entryY !== null && entryY >= 0 && entryY <= rect.height) {
        ctx.save();
        ctx.strokeStyle = entryColor;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(0, entryY);
        ctx.lineTo(rect.width - priceScaleWidth, entryY);
        ctx.stroke();
        ctx.setLineDash([]);

        // Entry label
        ctx.font = 'bold 9px monospace';
        ctx.fillStyle = entryColor;
        ctx.textAlign = 'left';
        const sideLabel = isLong ? 'LONG' : 'SHORT';
        const sizeLabel = ms.position_size ? `${ms.position_size}ct` : '';
        ctx.fillText(`▸ ${sideLabel} ${sizeLabel} @ ${ms.entry_price.toFixed(2)}`, 3, entryY - 4);

        // P&L at entry line (unrealized)
        const latestCandles = candlesRef.current;
        const latestCandle = latestCandles.length > 0 ? latestCandles[latestCandles.length - 1] : null;
        if (latestCandle) {
          const currentPrice = latestCandle.c;
          const pnlPts = isLong ? currentPrice - ms.entry_price : ms.entry_price - currentPrice;
          const pnlDollars = pnlPts * 20 * (ms.position_size ?? 1);
          const pnlColor = pnlDollars >= 0 ? '#10B981' : '#EF4444';
          ctx.fillStyle = pnlColor;
          ctx.textAlign = 'right';
          ctx.fillText(
            `${pnlDollars >= 0 ? '+' : ''}$${pnlDollars.toFixed(0)} (${pnlPts >= 0 ? '+' : ''}${pnlPts.toFixed(2)}pts)`,
            rect.width - priceScaleWidth - 4, entryY - 4,
          );
        }
        ctx.restore();
      }

      // Stop line
      if (ms.stop_price && ms.stop_price > 0) {
        const stopY = pSeries.priceToCoordinate(ms.stop_price);
        if (stopY !== null && stopY >= 0 && stopY <= rect.height) {
          ctx.save();
          ctx.strokeStyle = stopColor;
          ctx.lineWidth = 1.5;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.moveTo(0, stopY);
          ctx.lineTo(rect.width - priceScaleWidth, stopY);
          ctx.stroke();
          ctx.setLineDash([]);

          // Stop label
          ctx.font = 'bold 9px monospace';
          ctx.fillStyle = stopColor;
          ctx.textAlign = 'left';
          const riskPts = Math.abs(ms.entry_price - ms.stop_price);
          ctx.fillText(`✕ STOP @ ${ms.stop_price.toFixed(2)} (${riskPts.toFixed(2)}pts risk)`, 3, stopY - 4);
          ctx.restore();
        }
      }

      // Risk/reward shading between entry and stop
      if (ms.stop_price && ms.stop_price > 0 && entryY !== null) {
        const stopY = pSeries.priceToCoordinate(ms.stop_price);
        if (stopY !== null) {
          const top = Math.min(entryY, stopY);
          const height = Math.abs(stopY - entryY);
          ctx.save();
          ctx.fillStyle = isLong ? 'rgba(239, 68, 68, 0.04)' : 'rgba(239, 68, 68, 0.04)';
          ctx.fillRect(0, top, rect.width - priceScaleWidth, height);
          ctx.restore();
        }
      }
    }
  }, []);

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9AA0A6',
        fontSize: 10,
        fontFamily: 'monospace',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      crosshair: {
        vertLine: { color: 'rgba(255,255,255,0.15)', labelBackgroundColor: '#1a1e1a' },
        horzLine: { color: 'rgba(255,255,255,0.15)', labelBackgroundColor: '#1a1e1a' },
      },
      rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.08)',
        scaleMargins: { top: 0.05, bottom: 0.25 },
      },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.08)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        barSpacing: 7,
        minBarSpacing: 3,
      },
      handleScroll: { vertTouchDrag: false },
    });

    const priceSeries = chart.addCandlestickSeries({
      upColor: '#10B981',
      downColor: '#EF4444',
      borderUpColor: '#10B981',
      borderDownColor: '#EF4444',
      wickUpColor: '#10B981',
      wickDownColor: '#EF4444',
      lastValueVisible: true,
      priceLineVisible: true,
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const anchorSeries = chart.addLineSeries({
      color: 'transparent',
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    priceSeriesRef.current = priceSeries;
    volumeSeriesRef.current = volumeSeries;
    anchorSeriesRef.current = anchorSeries;

    // Load candles: render from sessionStorage cache instantly, then refresh from API
    const applyCandles = (sorted: CandleData[]) => {
      candlesRef.current = sorted;
      try {
        priceSeries.setData(sorted.map(toCandle));
        volumeSeries.setData(sorted.map(toVolume));
      } catch (err) {
        console.error('Chart setData failed:', err, 'candles:', sorted.length);
        setNoData(true);
        return false;
      }
      chart.timeScale().scrollToRealTime();
      setNoData(false);
      return true;
    };

    // Phase 1: Instant render from cache (if available)
    let hadCache = false;
    try {
      const cached = sessionStorage.getItem(CACHE_KEY);
      if (cached) {
        const parsed: CandleData[] = JSON.parse(cached);
        if (parsed.length > 0) {
          hadCache = applyCandles(parsed);
          if (hadCache) setLoading(false);
        }
      }
    } catch { /* corrupt cache, ignore */ }

    // Phase 2: Fetch fresh data from API (background if cache hit)
    (async () => {
      try {
        if (!hadCache) setLoading(true);
        const res = await api.getCandles(interval, INITIAL_DAYS);
        if (res.candles?.length) {
          const cleaned = res.candles.map(c => ({ ...c, t: Number(c.t) })).filter(c => !isNaN(c.t) && c.t > 0);
          const sorted = dedupeAndSort(cleaned);
          applyCandles(sorted);
          // Persist to cache for next page load
          try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(sorted)); } catch { /* quota */ }
        } else if (!hadCache) {
          setNoData(true);
        }
      } catch (err) {
        console.warn('Failed to load candles:', err);
        if (!hadCache) setNoData(true);
      } finally {
        setLoading(false);
      }
    })();

    let savedRange: { from: number; to: number } | null = null;
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width === 0 || height === 0) {
          // Tab hidden — save zoom before chart loses dimensions
          savedRange = chart.timeScale().getVisibleLogicalRange();
          return;
        }
        chart.applyOptions({ width, height });
        if (savedRange) {
          chart.timeScale().setVisibleLogicalRange(savedRange);
          savedRange = null;
        }
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      priceSeriesRef.current = null as any;
      volumeSeriesRef.current = null;
      anchorSeriesRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval]);

  // Subscribe VP overlay redraws to chart events (throttled to ~60fps)
  // Must re-run on interval change because chart instance is recreated
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    let rafId = 0;
    const redraw = () => {
      if (rafId) return; // already scheduled
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        drawOverlays();
      });
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
    chart.subscribeCrosshairMove(redraw);

    const observer = new ResizeObserver(redraw);
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(redraw);
      chart.unsubscribeCrosshairMove(redraw);
      observer.disconnect();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawOverlays, interval]);

  // --- Client-side indicator computation from candle data ---
  // Recompute VP, VWAP, session levels whenever candles change (instant, no server round-trip)
  const recomputeIndicators = useCallback(() => {
    const candles = candlesRef.current;
    if (!candles.length) return;

    // Session VP: compute from today's candles
    const today = todayCET();
    const todayCandles = candles.filter(c => epochToCETDate(c.t) === today);
    if (todayCandles.length > 0) {
      const sessionVP = computeVP(todayCandles);
      if (sessionVP.levels.length > 0) {
        vpDataRef.current.set('session', sessionVP);
      }
    }

    // Historical per-day VP: compute for all non-today days from loaded candles
    const histVPs = computeVPByDay(candles.filter(c => epochToCETDate(c.t) !== today));
    for (const [date, vp] of histVPs) {
      vpHistoryRef.current.set(date, vp);
    }

    // Session levels (PDH/PDL, IB, Tokyo/London H/L): compute from candles
    const levels = computeSessionLevels(candles);
    if (levels.length > 0) {
      sessionLevelsRef.current = levels;
      setSlLoaded(true);
    }

    // TPO profiles: compute for all days from loaded candles
    const tpoMap = computeAllDayTPOs(candles);
    if (tpoMap.size > 0) {
      sessionTPOMapRef.current = tpoMap;
      setSessionTPOLoaded(true);
    }

    // FVG confluence markers: detect FVGs and mark which zones/levels they reinforce
    // The model uses FVGs as binary confluence (fvg_overlap: 0/1), not as standalone levels.
    // So we only show FVGs that overlap with an existing zone — as a glow on that zone.
    const allFvgs: Array<{ low: number; high: number }> = [];
    for (let i = 1; i < candles.length - 1; i++) {
      const prev = candles[i - 1], next = candles[i + 1];
      if (prev.h + 1 < next.l) allFvgs.push({ low: prev.h, high: next.l });
      if (prev.l - 1 > next.h) allFvgs.push({ low: next.h, high: prev.l });
    }
    // Store FVGs that overlap with zones (for zone glow rendering)
    const zoneFvgs: Array<{ low: number; high: number; direction: string }> = [];
    for (const z of zonesRef.current) {
      for (const fvg of allFvgs) {
        if (fvg.low <= z.price && z.price <= fvg.high) {
          zoneFvgs.push({ low: fvg.low, high: fvg.high, direction: z.price > (fvg.low + fvg.high) / 2 ? 'bullish' : 'bearish' });
          break; // one FVG per zone is enough
        }
      }
    }
    fvgsRef.current = zoneFvgs;
    obsRef.current = []; // OBs not used as standalone — confluence only
    setStructLoaded(true);

    setVpLoaded(n => n + 1);
  }, []);

  // Trigger recompute when candles are loaded or updated
  const lastCandleCountRef = useRef(0);
  useEffect(() => {
    const count = candlesRef.current.length;
    if (count !== lastCandleCountRef.current) {
      lastCandleCountRef.current = count;
      recomputeIndicators();
    }
  });

  // Fetch swing pivot levels from server (needs months of daily data — can't compute client-side)
  useEffect(() => {
    let cancelled = false;
    api.getSessionLevels(INITIAL_DAYS + 2).then(res => {
      if (cancelled) return;
      swingPivotsRef.current = res.swings ?? [];

      // Render swing levels as native price lines (always visible on price axis)
      const pSeries = priceSeriesRef.current;
      if (pSeries) {
        // Remove old swing price lines
        for (const [key, line] of Object.entries(priceLineRefs.current)) {
          if (key.startsWith('swing_')) {
            pSeries.removePriceLine(line);
            delete priceLineRefs.current[key];
          }
        }

        const TF_COLORS: Record<string, string> = { daily: '#94A3B8', weekly: '#3B82F6' };
        const TF_PREFIX: Record<string, string> = { daily: 'D', weekly: 'W' };
        const hidden = hiddenRef.current;

        for (const pivot of res.swings ?? []) {
          const groupKey = `${pivot.tf}_swing`;
          if (hidden?.has(groupKey)) continue;
          const color = TF_COLORS[pivot.tf] ?? '#94A3B8';
          const alpha = pivot.rank === 0 ? 1.0 : pivot.rank === 1 ? 0.5 : 0.3;
          const label = `${TF_PREFIX[pivot.tf] ?? pivot.tf}-S${pivot.type === 'high' ? 'H' : 'L'}`;
          const lineKey = `swing_${pivot.tf}_${pivot.type}_${pivot.rank}`;

          const line = pSeries.createPriceLine({
            price: pivot.price,
            color,
            lineWidth: 1,
            lineStyle: 2, // dashed
            axisLabelVisible: pivot.rank === 0,
            title: label,
            lineVisible: true,
          });
          // Apply alpha via the line's options isn't directly supported,
          // but rank > 0 lines won't have axis labels so they're subtler
          priceLineRefs.current[lineKey] = line;
        }
      }

      setSlLoaded(true);
    }).catch(() => {});
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval]);

  // Fetch weekly/monthly VP from server (needs more data than loaded candles)
  useEffect(() => {
    let cancelled = false;
    for (const overlay of VP_OVERLAYS) {
      if (overlay.tf === 'session') continue; // computed client-side
      api.getVP(overlay.tf).then(data => {
        if (!cancelled && data.levels?.length) {
          vpDataRef.current.set(overlay.tf, data);
          setVpLoaded(n => n + 1);
        }
      }).catch(() => {});
    }
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval]);

  // Live refresh: recompute session VP from candles every 30s during market hours
  useEffect(() => {
    const timer = window.setInterval(() => {
      recomputeIndicators();
    }, 30_000);
    return () => clearInterval(timer);
  }, [recomputeIndicators]);

  // TPO is now computed client-side in recomputeIndicators()

  // Redraw when VP data loads, TPO changes, session/macro changes, signals/fills, or visibility changes
  useEffect(() => { drawOverlays(); }, [vpLoaded, slLoaded, structLoaded, sessionTPOLoaded, hiddenLevels, zones, signals, fills, exits, modelStatus, session, drawOverlays]);

  // Infinite scroll
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const onVisibleRangeChange = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range || fetchingRef.current || exhaustedRef.current) return;
      if (range.from > 10) return;

      const candles = candlesRef.current;
      if (candles.length === 0) return;

      const oldestTs = candles[0].t;
      const endDate = epochToDateStr(oldestTs);

      fetchingRef.current = true;

      api.getCandles(interval, SCROLL_DAYS, endDate)
        .then(res => {
          if (!res.candles?.length) { exhaustedRef.current = true; return; }
          const existing = new Set(candlesRef.current.map(c => c.t));
          const newCandles = res.candles.filter(c => !existing.has(c.t));
          if (newCandles.length === 0) { exhaustedRef.current = true; return; }

          const merged = dedupeAndSort([...newCandles, ...candlesRef.current]);
          candlesRef.current = merged;
          try {
            priceSeriesRef.current?.setData(merged.map(toCandle));
            volumeSeriesRef.current?.setData(merged.map(toVolume));
          } catch (err) {
            console.error('Chart scroll-back setData failed:', err);
          }
        })
        .catch(err => console.warn('Failed to load older candles:', err))
        .finally(() => { fetchingRef.current = false; });
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);
    return () => { chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange); };
  }, []);

  // (candle loading is done inside chart init effect above)

  // Live candle updates
  useEffect(() => {
    if (!lastCandle || !priceSeriesRef.current || !volumeSeriesRef.current) return;
    if (loading) return;

    // Seed series with first live candle if no historical data was loaded
    if (candlesRef.current.length === 0) {
      priceSeriesRef.current.setData([toCandle(lastCandle)]);
      volumeSeriesRef.current.setData([toVolume(lastCandle)]);
      candlesRef.current = [lastCandle];
      setNoData(false);
      drawOverlays();
      return;
    }

    try {
      priceSeriesRef.current.update(toCandle(lastCandle));
      volumeSeriesRef.current.update(toVolume(lastCandle));
    } catch (err) {
      // Stale or out-of-order candle — chart series can't display it,
      // but still update the array so session boxes track the full range.
      console.debug('Candle chart update skipped:', err);
    }

    const existing = candlesRef.current;
    if (existing.length && existing[existing.length - 1].t === lastCandle.t) {
      existing[existing.length - 1] = lastCandle;
    } else {
      existing.push(lastCandle);
    }
    // Redraw overlays so active session box follows price in real-time
    drawOverlays();

    // Periodically persist candles to cache so next page load is instant
    if (existing.length > 0 && existing.length % 10 === 0) {
      try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(existing)); } catch { /* quota */ }
    }
  }, [lastCandle, loading, drawOverlays]);

  // Anchor series for no-data state
  useEffect(() => {
    if (!noData || !session?.session || !anchorSeriesRef.current) return;
    const s = session.session;
    const anchor = s.vwap ?? session.price_position?.last_price;
    if (!anchor) return;

    const pad = s.ib_high && s.ib_low ? (s.ib_high - s.ib_low) * 1.5 : 200;
    const now = Math.floor(Date.now() / 1000);

    anchorSeriesRef.current.setData([
      { time: toLocalEpoch(now - 7200) as Time, value: anchor + pad },
      { time: toLocalEpoch(now) as Time,          value: anchor - pad },
    ] as LineData<Time>[]);
    chartRef.current?.timeScale().scrollToRealTime();
  }, [noData, session]);

  // Developing VWAP + SD bands from backend tick data (single source of truth)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove old VWAP series
    vwapSeriesRefs.current.forEach(s => {
      try { chart.removeSeries(s); } catch {}
    });
    vwapSeriesRefs.current = [];

    // Skip if VWAP hidden
    if (hiddenLevels?.has('vwap')) {
      return;
    }

    // Compute VWAP client-side from loaded candles (instant, no server round-trip)
    const candles = candlesRef.current;
    if (!candles.length) return;

    const days = computeVWAP(candles);
    if (!days.length) return;

    const bands: Array<{ color: string; width: 1 | 2; style: number; title: string; key: string }> = [
      { color: '#EAB308', width: 2, style: LineStyle.Solid, title: 'VWAP', key: 'vwap' },
      { color: 'rgba(234,179,8,0.5)', width: 1, style: LineStyle.Solid, title: '+σ', key: 'sd1_u' },
      { color: 'rgba(234,179,8,0.5)', width: 1, style: LineStyle.Solid, title: '-σ', key: 'sd1_l' },
      { color: 'rgba(234,179,8,0.25)', width: 1, style: LineStyle.Dashed, title: '+2σ', key: 'sd2_u' },
      { color: 'rgba(234,179,8,0.25)', width: 1, style: LineStyle.Dashed, title: '-2σ', key: 'sd2_l' },
      { color: 'rgba(234,179,8,0.15)', width: 1, style: LineStyle.Dotted, title: '+3σ', key: 'sd3_u' },
      { color: 'rgba(234,179,8,0.15)', width: 1, style: LineStyle.Dotted, title: '-3σ', key: 'sd3_l' },
    ];

    for (const dayData of days) {
      for (const band of bands) {
        const seen = new Set<number>();
        const data: LineData<Time>[] = dayData
          .map(p => ({ time: toLocalEpoch(p.t) as Time, value: (p as unknown as Record<string, number>)[band.key] }))
          .filter(d => {
            const t = d.time as number;
            if (seen.has(t)) return false;
            seen.add(t);
            return true;
          })
          .sort((a, b) => (a.time as number) - (b.time as number));

        const s = chart.addLineSeries({
          color: band.color,
          lineWidth: band.width,
          lineStyle: band.style,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        } as any);
        s.setData(data);
        vwapSeriesRefs.current.push(s);
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, hiddenLevels, interval]);

  // Static reference lines: IB, dPOC (these are flat — correct for structural levels)
  useEffect(() => {
    const series = priceSeriesRef.current;
    if (!series) return;

    Object.values(priceLineRefs.current).forEach(line => {
      try { series.removePriceLine(line); } catch {}
    });
    priceLineRefs.current = {};

    if (!session) return;
    const p = session.profiles;

    const h = hiddenLevels;
    const add = (key: string, price: number | undefined | null, color: string, title: string, style = LineStyle.Dashed, width: 1 | 2 = 1) => {
      if (price == null || price === 0 || h?.has(key)) return;
      priceLineRefs.current[key] = series.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title });
    };

    // Daily Volume Profile
    add('d_poc', p?.session?.poc, '#A855F7', 'dPOC', LineStyle.Solid, 2);
    add('d_vah', p?.session?.vah, '#A855F7', 'dVAH', LineStyle.Dashed, 1);
    add('d_val', p?.session?.val, '#A855F7', 'dVAL', LineStyle.Dashed, 1);

    // Weekly Volume Profile
    add('w_poc', p?.weekly?.poc, '#EC4899', 'wPOC', LineStyle.Solid, 2);
    add('w_vah', p?.weekly?.vah, '#EC4899', 'wVAH', LineStyle.Dashed, 1);
    add('w_val', p?.weekly?.val, '#EC4899', 'wVAL', LineStyle.Dashed, 1);

    // Monthly Volume Profile
    add('m_poc', p?.monthly?.poc, '#F59E0B', 'mPOC', LineStyle.Solid, 2);
    add('m_vah', p?.monthly?.vah, '#F59E0B', 'mVAH', LineStyle.Dashed, 1);
    add('m_val', p?.monthly?.val, '#F59E0B', 'mVAL', LineStyle.Dashed, 1);

    // TPO POC/VAH/VAL are now drawn per-session on the canvas overlay (see drawOverlays)
  }, [session, hiddenLevels]);

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" />
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full pointer-events-none"
        style={{ zIndex: 1 }}
      />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ zIndex: 2 }}>
          <div className="w-5 h-5 border-2 border-zinc-700 border-t-amber-500 rounded-full animate-spin" />
        </div>
      )}
      {noData && !loading && !lastCandle && !session && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ zIndex: 2 }}>
          <span className="text-zinc-600 text-[10px] font-mono">No candle data available</span>
        </div>
      )}
    </div>
  );
}
