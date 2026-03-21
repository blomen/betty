import { useRef, useEffect, useState, useCallback } from 'react';
import {
  createChart,
  HistogramSeries,
  LineSeries,
  CandlestickSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type HistogramData,
  type CandlestickData,
  type LineData,
  type Time,
  ColorType,
} from 'lightweight-charts';
import { api } from '@/services/api';
import type { CandleData, ExpandedSession, TPOLiveProfile } from '@/types/market';

const INTERVAL = '1m';
const INITIAL_DAYS = 3;
const SCROLL_DAYS = 1;

// VP overlay config: which timeframes to show, with colors
const VP_OVERLAYS = [
  { tf: 'session', color: [168, 85, 247],  label: 'D' },   // purple
  { tf: 'weekly',  color: [236, 72, 153],  label: 'W' },   // pink
  { tf: 'monthly', color: [234, 179, 8],   label: 'M' },   // yellow
] as const;

// Session box definitions (CET/CEST times as hour*60+minute)
// Tokyo: 00:00 → 09:00 CET  (Globex open → London open)
// London: 09:00 → 15:30 CET  (London open → NY open)
// New York: 15:30 → 22:00 CET  (NY open → close)
const SESSION_DEFS = [
  { name: 'Tokyo',    startMin: 0,             endMin: 9 * 60,        color: 'rgba(6, 182, 212, 0.12)',  border: 'rgba(6, 182, 212, 0.35)',  label: '#06B6D4' },
  { name: 'London',   startMin: 9 * 60,        endMin: 15 * 60 + 30,  color: 'rgba(16, 185, 129, 0.12)', border: 'rgba(16, 185, 129, 0.35)', label: '#10B981' },
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

function epochToCETDate(epoch: number): string {
  const { year, month, day } = _parseCETDate(epoch);
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
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

interface Props {
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
  hiddenLevels?: Set<string>;
  tpo?: TPOLiveProfile | null;
}

/**
 * Filter fake/noisy wicks using neighbor validation + volume weighting.
 *
 * 1. Neighbor check: wicks can't exceed the close-price range of nearby bars
 *    plus a tolerance proportional to volume (high vol = more tolerance).
 * 2. Absolute cap: no single wick can exceed MAX_WICK points from the body.
 */
function filterWicks(candles: CandleData[]): CandleData[] {
  if (candles.length < 3) return candles;
  const RADIUS = 3;
  const BASE_TOLERANCE = 0.3;
  const MAX_WICK = 25;  // absolute max wick from body edge (points)

  return candles.map((c, i) => {
    const bodyHigh = Math.max(c.o, c.c);
    const bodyLow = Math.min(c.o, c.c);

    // Neighbor close-price range (tighter than open/close)
    const from = Math.max(0, i - RADIUS);
    const to = Math.min(candles.length, i + RADIUS + 1);
    let maxClose = -Infinity, minClose = Infinity;
    for (let j = from; j < to; j++) {
      maxClose = Math.max(maxClose, candles[j].c);
      minClose = Math.min(minClose, candles[j].c);
    }

    // Volume-weighted tolerance: sqrt(vol)/150, capped at 1.0
    const volFactor = Math.min(1, Math.sqrt(Math.max(c.v || 1, 1)) / 150);
    const neighborSpan = (maxClose - minClose) * BASE_TOLERANCE * volFactor;

    // Neighbor-based clamp
    let h = Math.min(c.h, Math.max(bodyHigh, maxClose + neighborSpan));
    let l = Math.max(c.l, Math.min(bodyLow, minClose - neighborSpan));

    // Absolute wick cap
    h = Math.min(h, bodyHigh + MAX_WICK);
    l = Math.max(l, bodyLow - MAX_WICK);

    return { ...c, h, l };
  });
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

function detectSessionBoxes(candles: CandleData[]): SessionBox[] {
  if (candles.length < 2) return [];

  const boxes: SessionBox[] = [];

  // Group candles by CET date — all sessions are simple intra-day ranges
  const dateGroups = new Map<string, CandleData[]>();
  for (const c of candles) {
    const cetDate = epochToCETDate(c.t);
    if (!dateGroups.has(cetDate)) dateGroups.set(cetDate, []);
    dateGroups.get(cetDate)!.push(c);
  }

  for (const [dateStr, dayCandles] of dateGroups) {
    for (const def of SESSION_DEFS) {
      const sessionCandles = dayCandles.filter(c => {
        const cetMin = epochToCETMinute(c.t);
        return cetMin >= def.startMin && cetMin < def.endMin;
      });

      if (sessionCandles.length < 2) continue;

      const high = Math.max(...sessionCandles.map(c => c.h));
      const low = Math.min(...sessionCandles.map(c => c.l));
      const startEpoch = Math.min(...sessionCandles.map(c => c.t));
      const endEpoch = Math.max(...sessionCandles.map(c => c.t));

      boxes.push({
        name: def.name,
        high,
        low,
        startEpoch,
        endEpoch,
        color: def.color,
        border: def.border,
        labelColor: def.label,
        cetDate: dateStr,
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

export function CandleChart({ lastCandle, session, hiddenLevels, tpo }: Props) {
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

  // VP overlay data
  const vpDataRef = useRef<Map<string, VPData>>(new Map());
  const [vpLoaded, setVpLoaded] = useState(0); // trigger redraws
  const hiddenRef = useRef(hiddenLevels);
  hiddenRef.current = hiddenLevels;

  // Session levels overlay data (per-day PDH/PDL, IB, Tokyo, London)
  const sessionLevelsRef = useRef<import('@/types/market').SessionLevelDay[]>([]);
  const [slLoaded, setSlLoaded] = useState(false);

  // TPO overlay data
  const tpoRef = useRef<TPOLiveProfile | null>(null);
  tpoRef.current = tpo ?? null;

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

    // --- Session boxes (Y from candle H/L, X from candle time range) ---
    const candles = candlesRef.current;
    const boxes = candles.length > 0 ? detectSessionBoxes(candles) : [];
    const slDays = sessionLevelsRef.current;
    const slByDate = new Map(slDays.map(d => [d.date, d]));

    if (boxes.length > 0) {
      for (const box of boxes) {
        const rawX1 = timeScale.timeToCoordinate(toLocalEpoch(box.startEpoch) as Time);
        const rawX2 = timeScale.timeToCoordinate(toLocalEpoch(box.endEpoch) as Time);
        const rawY1 = pSeries.priceToCoordinate(box.high);
        const rawY2 = pSeries.priceToCoordinate(box.low);

        // Null-clamp: if one edge is off-screen, extend to chart edge
        if (rawY1 === null && rawY2 === null) continue;
        if (rawX1 === null && rawX2 === null) continue;
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

        // Label at top-right of box
        ctx.font = '10px monospace';
        ctx.fillStyle = box.labelColor;
        ctx.textAlign = 'right';
        ctx.fillText(box.name, bx + bw - 3, by + 11);
      }
    }

    // --- VP histograms on right edge (daily / weekly / monthly stacked) ---
    const vpMap = vpDataRef.current;
    const priceScaleWidth = 65;
    const xRight = rect.width - priceScaleWidth;
    const maxBarWidth = 80;

    // Draw in reverse order so daily (most important) renders on top
    const hidden = hiddenRef.current;
    // VP hidden keys: vp_session, vp_weekly, vp_monthly
    for (let oi = VP_OVERLAYS.length - 1; oi >= 0; oi--) {
      const overlay = VP_OVERLAYS[oi];
      if (hidden?.has(`vp_${overlay.tf}`)) continue;
      const vp = vpMap.get(overlay.tf);
      if (!vp || !vp.levels.length) continue;

      const maxVol = Math.max(...vp.levels.map(l => l.volume));
      if (maxVol <= 0) continue;

      const [r, g, b] = overlay.color;

      for (const level of vp.levels) {
        const y = pSeries.priceToCoordinate(level.price);
        if (y === null || y < 0 || y > rect.height) continue;

        const barW = (level.volume / maxVol) * maxBarWidth;
        const isPOC = level.price === vp.poc;
        const inVA = level.price >= vp.val && level.price <= vp.vah;

        ctx.fillStyle = isPOC
          ? `rgba(${r}, ${g}, ${b}, 0.6)`
          : inVA
            ? `rgba(${r}, ${g}, ${b}, 0.2)`
            : `rgba(${r}, ${g}, ${b}, 0.06)`;

        ctx.fillRect(xRight - barW, y - 1, barW, 2);
      }
    }

    // --- Session H/L levels (persist from session end to day end) ---
    const slHidden = hiddenRef.current;
    const currentDay = [...slDays].sort((a, b) => b.date.localeCompare(a.date))[0];

    // Session level labels and hide-keys by session name (NY excluded — box is sufficient)
    const sessionLevelMeta: Record<string, { hKey: string; lKey: string; hLabel: string; lLabel: string; color: string; hField: 'tokyo_high' | 'london_high'; lField: 'tokyo_low' | 'london_low' }> = {
      'Tokyo':    { hKey: 'tokyo_h', lKey: 'tokyo_l', hLabel: 'TKY H', lLabel: 'TKY L', color: '#06B6D4', hField: 'tokyo_high', lField: 'tokyo_low' },
      'London':   { hKey: 'london_h', lKey: 'london_l', hLabel: 'LDN H', lLabel: 'LDN L', color: '#10B981', hField: 'london_high', lField: 'london_low' },
    };

    // Only show session levels for the current CET day (reset at 00:00 CET)
    const todayCET = epochToCETDate(Math.floor(Date.now() / 1000));
    const todayBoxes = boxes.filter(b => b.cetDate === todayCET);

    if (todayBoxes.length > 0) {
      // Draw session H/L lines from box end to day end (22:00 CET)
      for (const box of todayBoxes) {
        const meta = sessionLevelMeta[box.name];
        if (!meta) continue;

        const sl = slByDate.get(box.cetDate);
        // Use backend levels when available, fallback to candle-computed
        const lineHigh = sl?.[meta.hField] ?? box.high;
        const lineLow = sl?.[meta.lField] ?? box.low;

        // Day end = 22:00 CET: compute from box end + remaining CET minutes
        const boxEndCETMin = epochToCETMinute(box.endEpoch);
        const dayEndEpoch = box.endEpoch + (22 * 60 - boxEndCETMin) * 60;

        for (const { key, price, label } of [
          { key: meta.hKey, price: lineHigh, label: meta.hLabel },
          { key: meta.lKey, price: lineLow, label: meta.lLabel },
        ]) {
          if (slHidden?.has(key)) continue;
          const y = pSeries.priceToCoordinate(price);
          if (y === null) continue;

          const rawX1 = timeScale.timeToCoordinate(toLocalEpoch(box.endEpoch) as Time);
          const rawX2 = timeScale.timeToCoordinate(toLocalEpoch(dayEndEpoch) as Time);
          if (rawX1 === null && rawX2 === null) continue;
          const lx = rawX1 ?? 0;
          const rx = rawX2 ?? rect.width;
          if (rx < 0 || lx > rect.width) continue;
          const drawX1 = Math.max(0, lx);
          const drawX2 = Math.min(rect.width, rx);

          ctx.save();
          ctx.strokeStyle = meta.color;
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(drawX1, y);
          ctx.lineTo(drawX2, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = meta.color;
          ctx.textAlign = 'left';
          ctx.fillText(label, drawX1 + 3, y - 3);
          ctx.restore();
        }
      }

      // PDH/PDL scoped to today only (from day start 00:00 CET to day end 22:00 CET)
      const todaySL = slByDate.get(todayCET);
      const prevDayBoxes = boxes.filter(b => b.cetDate < todayCET);
      if (prevDayBoxes.length > 0) {
        const maxDate = prevDayBoxes.reduce((max, b) => b.cetDate > max ? b.cetDate : max, prevDayBoxes[0].cetDate);
        const lastDayBoxes = prevDayBoxes.filter(b => b.cetDate === maxDate);
        const pdh = todaySL?.pdh ?? Math.max(...lastDayBoxes.map(b => b.high));
        const pdl = todaySL?.pdl ?? Math.min(...lastDayBoxes.map(b => b.low));

        // Scope PDH/PDL to today's time range
        const dayStartEpoch = todaySL?.day_start ?? todayBoxes[0].startEpoch;
        const dayEndEpoch = todaySL?.day_end ?? todayBoxes[0].endEpoch + (22 * 60 - epochToCETMinute(todayBoxes[0].endEpoch)) * 60;

        for (const { key, price, label } of [
          { key: 'pdh', price: pdh, label: 'PDH' },
          { key: 'pdl', price: pdl, label: 'PDL' },
        ]) {
          if (slHidden?.has(key)) continue;
          const y = pSeries.priceToCoordinate(price);
          if (y === null) continue;

          const rawX1 = timeScale.timeToCoordinate(toLocalEpoch(dayStartEpoch) as Time);
          const rawX2 = timeScale.timeToCoordinate(toLocalEpoch(dayEndEpoch) as Time);
          if (rawX1 === null && rawX2 === null) continue;
          const lx = rawX1 ?? 0;
          const rx = rawX2 ?? rect.width;
          if (rx < 0 || lx > rect.width) continue;
          const drawX1 = Math.max(0, lx);
          const drawX2 = Math.min(rect.width, rx);

          ctx.save();
          ctx.strokeStyle = '#FB923C';
          ctx.lineWidth = 1;
          ctx.setLineDash([6, 3]);
          ctx.beginPath();
          ctx.moveTo(drawX1, y);
          ctx.lineTo(drawX2, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = '#FB923C';
          ctx.textAlign = 'left';
          ctx.fillText(label, drawX1 + 3, y - 3);
          ctx.restore();
        }
      }
    }

    // --- NY IB levels from session levels, anchored to NY session only ---
    // Only show after IB period is complete (10:30 ET, CET varies with DST)
    const nowEpoch = Math.floor(Date.now() / 1000);
    const ibComplete = currentDay && nowEpoch >= currentDay.ib_end;
    if (currentDay && ibComplete) {
      const ibLevels: Array<{ price: number; label: string; key: string }> = [];
      if (currentDay.ib_high && !slHidden?.has('ibh')) ibLevels.push({ price: currentDay.ib_high, label: 'NYIBH', key: 'ibh' });
      if (currentDay.ib_low && !slHidden?.has('ibl')) ibLevels.push({ price: currentDay.ib_low, label: 'NYIBL', key: 'ibl' });
      // Anchor from NY open (15:30 CET) to NY close (22:00 CET)
      const ibStartX = timeScale.timeToCoordinate(toLocalEpoch(currentDay.ny_start) as Time);
      const ibEndX = timeScale.timeToCoordinate(toLocalEpoch(currentDay.ny_end) as Time);
      // Skip if entire NY session is off-screen
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

    // --- TPO histogram on right edge (orange, next to VP histograms) ---
    const tpoData = tpoRef.current;
    if (tpoData && !hidden?.has('vp_tpo')) {
      const counts = tpoData.tpo_counts;
      const prices = Object.keys(counts).map(Number);
      if (prices.length > 0) {
        const maxCount = Math.max(...prices.map(p => counts[String(p)]));
        if (maxCount > 0) {
          const tpoBarMaxWidth = 60;
          // Offset TPO bars slightly left of VP bars to avoid overlap
          const tpoXRight = xRight - maxBarWidth - 4;

          for (const price of prices) {
            const y = pSeries.priceToCoordinate(price);
            if (y === null || y < 0 || y > rect.height) continue;

            const count = counts[String(price)];
            const barW = (count / maxCount) * tpoBarMaxWidth;
            const isPOC = price === tpoData.poc;
            const inVA = price >= tpoData.val && price <= tpoData.vah;

            const alpha = isPOC ? 0.6 : inVA ? 0.35 : 0.2;
            ctx.fillStyle = `rgba(255, 107, 53, ${alpha})`;
            ctx.fillRect(tpoXRight - barW, y - 1, barW, 2);
          }
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

    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10B981',
      downColor: '#EF4444',
      borderUpColor: '#10B981',
      borderDownColor: '#EF4444',
      wickUpColor: '#10B981',
      wickDownColor: '#EF4444',
      lastValueVisible: true,
      priceLineVisible: true,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const anchorSeries = chart.addSeries(LineSeries, {
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

    // Load candles immediately after chart is created
    (async () => {
      try {
        setLoading(true);
        const res = await api.getCandles('NQ', INTERVAL, undefined, INITIAL_DAYS);
        if (res.candles?.length) {
          // Ensure all timestamps are numbers (API might return strings)
          const cleaned = res.candles.map(c => ({ ...c, t: Number(c.t) })).filter(c => !isNaN(c.t) && c.t > 0);
          const sorted = dedupeAndSort(cleaned);
          candlesRef.current = sorted;
          try {
            priceSeries.setData(filterWicks(sorted).map(toCandle));
            volumeSeries.setData(sorted.map(toVolume));
          } catch (err) {
            console.error('Chart setData failed:', err, 'candles:', sorted.length);
            setNoData(true);
            return;
          }
          chart.timeScale().scrollToRealTime();
          setNoData(false);
        } else {
          setNoData(true);
        }
      } catch (err) {
        console.warn('Failed to load candles:', err);
        setNoData(true);
      } finally {
        setLoading(false);
      }
    })();

    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height });
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
  }, []);

  // Subscribe VP overlay redraws to chart events (separate from init)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const redraw = () => drawOverlays();
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);

    const observer = new ResizeObserver(() => requestAnimationFrame(redraw));
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(redraw);
      observer.disconnect();
    };
  }, [drawOverlays]);

  // Fetch VP curve data for all timeframes (daily, weekly, monthly)
  useEffect(() => {
    let cancelled = false;
    for (const overlay of VP_OVERLAYS) {
      api.getVolumeProfile('NQ', overlay.tf).then(data => {
        if (!cancelled && data.levels?.length) {
          vpDataRef.current.set(overlay.tf, data);
          setVpLoaded(n => n + 1);
          drawOverlays();
        }
      }).catch(() => { /* skip if not available */ });
    }
    return () => { cancelled = true; };
  }, [session, drawOverlays]); // refetch when session updates

  // Fetch session levels for multi-day overlay
  useEffect(() => {
    let cancelled = false;
    api.getSessionLevels('NQ', INITIAL_DAYS + 2).then(res => {
      if (!cancelled && res.days?.length) {
        sessionLevelsRef.current = res.days;
        setSlLoaded(true);
        drawOverlays();
      }
    }).catch(err => { console.warn('[SessionLevels] fetch failed:', err); });
    return () => { cancelled = true; };
  }, [session, drawOverlays]);

  // Redraw when VP data loads, TPO changes, or visibility changes
  useEffect(() => { drawOverlays(); }, [vpLoaded, slLoaded, hiddenLevels, tpo, drawOverlays]);

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

      api.getCandles('NQ', INTERVAL, endDate, SCROLL_DAYS)
        .then(res => {
          if (!res.candles?.length) { exhaustedRef.current = true; return; }
          const existing = new Set(candlesRef.current.map(c => c.t));
          const newCandles = res.candles.filter(c => !existing.has(c.t));
          if (newCandles.length === 0) { exhaustedRef.current = true; return; }

          const merged = dedupeAndSort([...newCandles, ...candlesRef.current]);
          candlesRef.current = merged;
          try {
            priceSeriesRef.current?.setData(filterWicks(merged).map(toCandle));
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
    // Don't update until initial data is loaded — prevents "Cannot update oldest data"
    if (loading || candlesRef.current.length === 0) return;
    try {
      // Filter wick using recent candles as context
      const recent = candlesRef.current.slice(-6).concat(lastCandle);
      const filtered = filterWicks(recent);
      priceSeriesRef.current.update(toCandle(filtered[filtered.length - 1]));
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
  }, [lastCandle, loading, drawOverlays]);

  // Anchor series for no-data state
  useEffect(() => {
    if (!noData || !session || !anchorSeriesRef.current) return;
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

    // Fetch tick-level VWAP from backend
    let cancelled = false;
    api.getDevelopingVwap('NQ', '1m').then(res => {
      if (cancelled || !res.vwap?.length || !chartRef.current) return;

      const toLD = (arr: typeof res.vwap, key: keyof typeof arr[0]): LineData<Time>[] =>
        arr.map(p => ({ time: toLocalEpoch(p.t) as Time, value: p[key] as number }));

      const addLine = (color: string, width: 1 | 2, style: number, title: string, data: LineData<Time>[]) => {
        // Dedupe + sort VWAP points to prevent lightweight-charts crash
        const seen = new Set<number>();
        const clean = data.filter(d => {
          const t = d.time as number;
          if (seen.has(t)) return false;
          seen.add(t);
          return true;
        }).sort((a, b) => (a.time as number) - (b.time as number));

        const s = chartRef.current!.addSeries(LineSeries, {
          color,
          lineWidth: width,
          lineStyle: style,
          lastValueVisible: true,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          title,
          zOrder: -1,  // render behind price
        } as any);
        s.setData(clean);
        vwapSeriesRefs.current.push(s);
      };

      addLine('#EAB308', 2, LineStyle.Solid, 'VWAP', toLD(res.vwap, 'vwap'));
      addLine('rgba(234,179,8,0.5)', 1, LineStyle.Solid, '+\u03C3', toLD(res.vwap, 'sd1_u'));
      addLine('rgba(234,179,8,0.5)', 1, LineStyle.Solid, '-\u03C3', toLD(res.vwap, 'sd1_l'));
      addLine('rgba(234,179,8,0.25)', 1, LineStyle.Dashed, '+2\u03C3', toLD(res.vwap, 'sd2_u'));
      addLine('rgba(234,179,8,0.25)', 1, LineStyle.Dashed, '-2\u03C3', toLD(res.vwap, 'sd2_l'));
      addLine('rgba(234,179,8,0.15)', 1, LineStyle.Dotted, '+3\u03C3', toLD(res.vwap, 'sd3_u'));
      addLine('rgba(234,179,8,0.15)', 1, LineStyle.Dotted, '-3\u03C3', toLD(res.vwap, 'sd3_l'));
    }).catch(err => console.warn('Failed to load VWAP:', err));

    return () => { cancelled = true; };
  }, [session, hiddenLevels]);

  // Static reference lines: IB, PDH/PDL, dPOC (these are flat — correct for structural levels)
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

    // TPO Profile levels (orange #ff6b35)
    add('t_poc', tpo?.poc, '#ff6b35', 'tPOC', LineStyle.Solid, 2);
    add('t_vah', tpo?.vah, '#ff6b35', 'tVAH', LineStyle.Dashed, 1);
    add('t_val', tpo?.val, '#ff6b35', 'tVAL', LineStyle.Dashed, 1);
  }, [session, hiddenLevels, tpo]);

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
          <div className="w-5 h-5 border-2 border-muted2 border-t-accent rounded-full animate-spin" />
        </div>
      )}
      {noData && !loading && !lastCandle && !session && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ zIndex: 2 }}>
          <span className="text-muted2 text-[10px] font-mono">No candle data available</span>
        </div>
      )}
    </div>
  );
}
