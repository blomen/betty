import { useRef, useEffect, useState, useCallback } from 'react';
import {
  createChart,
  HistogramSeries,
  LineSeries,
  AreaSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type HistogramData,
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
// Tokyo: 00:00 → 08:00 CET  (Globex open → London open)
// London: 08:00 → 15:30 CET  (London open → NY open)
// New York: 15:30 → 22:00 CET  (NY open → close)
const SESSION_DEFS = [
  { name: 'Tokyo',    startMin: 0,             endMin: 8 * 60,        color: 'rgba(6, 182, 212, 0.12)',  border: 'rgba(6, 182, 212, 0.35)',  label: '#06B6D4' },  // cyan
  { name: 'London',   startMin: 8 * 60,        endMin: 15 * 60 + 30,  color: 'rgba(16, 185, 129, 0.12)', border: 'rgba(16, 185, 129, 0.35)', label: '#10B981' },  // green
  { name: 'New York', startMin: 15 * 60 + 30,  endMin: 22 * 60,       color: 'rgba(239, 68, 68, 0.10)',  border: 'rgba(239, 68, 68, 0.30)',  label: '#EF4444' },  // red
] as const;

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

function toLine(c: CandleData): LineData<Time> {
  return { time: toLocalEpoch(c.t) as Time, value: c.c };
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

  for (const [, dayCandles] of dateGroups) {
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
      });
    }
  }

  return boxes;
}

export function CandleChart({ lastCandle, session, hiddenLevels, tpo }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceSeriesRef = useRef<ISeriesApi<'Area'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const [noData, setNoData] = useState(false);
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

    // --- Session boxes ---
    const candles = candlesRef.current;
    if (candles.length > 0) {
      const boxes = detectSessionBoxes(candles);
      for (const box of boxes) {
        const x1 = timeScale.timeToCoordinate(toLocalEpoch(box.startEpoch) as Time);
        const x2 = timeScale.timeToCoordinate(toLocalEpoch(box.endEpoch) as Time);
        const y1 = pSeries.priceToCoordinate(box.high);
        const y2 = pSeries.priceToCoordinate(box.low);

        if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
        if (x2 < 0 || x1 > rect.width) continue; // off-screen

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

    // --- Session level lines (time-scoped horizontal lines) ---
    const slDays = sessionLevelsRef.current;
    const slHidden = hiddenRef.current;

    const levelDefs: Array<{
      key: string;
      field: 'pdh' | 'pdl' | 'ib_high' | 'ib_low' | 'tokyo_high' | 'tokyo_low' | 'london_high' | 'london_low';
      label: string;
      color: string;
      dash: number[];
      startField: 'day_start' | 'ib_end' | 'tokyo_end' | 'london_end';
      endField: 'day_end';
    }> = [
      { key: 'pdh', field: 'pdh', label: 'PDH', color: '#FB923C', dash: [6, 3], startField: 'day_start', endField: 'day_end' },
      { key: 'pdl', field: 'pdl', label: 'PDL', color: '#FB923C', dash: [6, 3], startField: 'day_start', endField: 'day_end' },
      { key: 'ibh', field: 'ib_high', label: 'IBH', color: '#F59E0B', dash: [3, 3], startField: 'ib_end', endField: 'day_end' },
      { key: 'ibl', field: 'ib_low', label: 'IBL', color: '#F59E0B', dash: [3, 3], startField: 'ib_end', endField: 'day_end' },
      { key: 'tokyo_h', field: 'tokyo_high', label: 'TKY H', color: '#06B6D4', dash: [3, 3], startField: 'tokyo_end', endField: 'day_end' },
      { key: 'tokyo_l', field: 'tokyo_low', label: 'TKY L', color: '#06B6D4', dash: [3, 3], startField: 'tokyo_end', endField: 'day_end' },
      { key: 'london_h', field: 'london_high', label: 'LDN H', color: '#10B981', dash: [3, 3], startField: 'london_end', endField: 'day_end' },
      { key: 'london_l', field: 'london_low', label: 'LDN L', color: '#10B981', dash: [3, 3], startField: 'london_end', endField: 'day_end' },
    ];

    for (const day of slDays) {
      for (const def of levelDefs) {
        if (slHidden?.has(def.key)) continue;
        const price = day[def.field];
        if (price == null) continue;

        const startEpoch = day[def.startField];
        const endEpoch = day[def.endField];

        // timeToCoordinate returns null when off-screen — clamp to edges
        const rawX1 = timeScale.timeToCoordinate(toLocalEpoch(startEpoch) as Time);
        const rawX2 = timeScale.timeToCoordinate(toLocalEpoch(endEpoch) as Time);
        const y = pSeries.priceToCoordinate(price);

        if (y === null) continue;
        // Both off-screen on same side = skip; otherwise clamp
        if (rawX1 === null && rawX2 === null) continue;
        const lx = rawX1 ?? 0;
        const rx = rawX2 ?? rect.width;
        if (rx < 0 || lx > rect.width) continue;

        const drawX1 = Math.max(0, lx);
        const drawX2 = Math.min(rect.width, rx);

        ctx.save();
        ctx.strokeStyle = def.color;
        ctx.lineWidth = 1;
        ctx.setLineDash(def.dash);
        ctx.beginPath();
        ctx.moveTo(drawX1, y);
        ctx.lineTo(drawX2, y);
        ctx.stroke();

        // Label at left visible edge of line
        ctx.setLineDash([]);
        ctx.font = '9px monospace';
        ctx.fillStyle = def.color;
        ctx.textAlign = 'left';
        ctx.fillText(def.label, drawX1 + 3, y - 3);
        ctx.restore();
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

    const priceSeries = chart.addSeries(AreaSeries, {
      lineColor: '#E0E0E0',
      lineWidth: 1,
      topColor: 'rgba(255, 255, 255, 0.04)',
      bottomColor: 'rgba(255, 255, 255, 0.0)',
      lastValueVisible: true,
      priceLineVisible: true,
      crosshairMarkerVisible: true,
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
        const res = await api.getCandles('NQ', INTERVAL, undefined, INITIAL_DAYS);
        if (res.candles?.length) {
          candlesRef.current = res.candles;
          priceSeries.setData(res.candles.map(toLine));
          volumeSeries.setData(res.candles.map(toVolume));
          chart.timeScale().scrollToRealTime();
          setNoData(false);
        } else {
          setNoData(true);
        }
      } catch (err) {
        console.warn('Failed to load candles:', err);
        setNoData(true);
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

          candlesRef.current = [...newCandles, ...candlesRef.current];
          priceSeriesRef.current?.setData(candlesRef.current.map(toLine));
          volumeSeriesRef.current?.setData(candlesRef.current.map(toVolume));
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
    priceSeriesRef.current.update(toLine(lastCandle));
    volumeSeriesRef.current.update(toVolume(lastCandle));

    const existing = candlesRef.current;
    if (existing.length && existing[existing.length - 1].t === lastCandle.t) {
      existing[existing.length - 1] = lastCandle;
    } else {
      existing.push(lastCandle);
    }
  }, [lastCandle]);

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
        s.setData(data);
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
      {noData && !lastCandle && !session && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ zIndex: 2 }}>
          <span className="text-muted2 text-[10px] font-mono">No candle data available</span>
        </div>
      )}
    </div>
  );
}
