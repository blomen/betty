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
import type { CandleData, ExpandedSession } from '@/types/market';

const INTERVAL = '1m';
const INITIAL_DAYS = 1;
const SCROLL_DAYS = 1;

// VP overlay config: which timeframes to show, with colors
// Only daily VP overlay for now — verified
const VP_OVERLAYS = [
  { tf: 'session', color: [168, 85, 247],  label: 'D' },   // purple
] as const;

type VPData = { levels: Array<{ price: number; volume: number }>; poc: number; vah: number; val: number };

interface Props {
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
}

function toLine(c: CandleData): LineData<Time> {
  return { time: c.t as Time, value: c.c };
}

function toVolume(c: CandleData): HistogramData<Time> {
  const color = c.c >= c.o ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)';
  return { time: c.t as Time, value: c.v, color };
}

function epochToDateStr(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 10);
}

export function CandleChart({ lastCandle, session }: Props) {
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

  // Draw VP histograms on canvas
  const drawVPOverlay = useCallback(() => {
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

    const vpMap = vpDataRef.current;
    if (vpMap.size === 0) return;

    // Draw session VP only as a single histogram on the right edge
    const vp = vpMap.get('session');
    if (!vp || !vp.levels.length) return;

    const maxVol = Math.max(...vp.levels.map(l => l.volume));
    if (maxVol === 0) return;

    const maxBarWidth = 80;
    const priceScaleWidth = 65;
    const xRight = rect.width - priceScaleWidth;

    for (const level of vp.levels) {
      const y = pSeries.priceToCoordinate(level.price);
      if (y === null || y < 0 || y > rect.height) continue;

      const barW = (level.volume / maxVol) * maxBarWidth;
      const inVA = level.price >= vp.val && level.price <= vp.vah;
      const isPOC = level.price === vp.poc;

      ctx.fillStyle = isPOC
        ? 'rgba(168, 85, 247, 0.6)'
        : inVA
          ? 'rgba(168, 85, 247, 0.2)'
          : 'rgba(168, 85, 247, 0.06)';

      ctx.fillRect(xRight - barW, y - 1, barW, 2);
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

    const redraw = () => drawVPOverlay();
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);

    const observer = new ResizeObserver(() => requestAnimationFrame(redraw));
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(redraw);
      observer.disconnect();
    };
  }, [drawVPOverlay]);

  // Fetch VP curve data for all overlays
  // Fetch VP profiles in parallel (these are slow — 1-3s each)
  useEffect(() => {
    let cancelled = false;
    const fetches = VP_OVERLAYS.map(async (overlay) => {
      try {
        const data = await api.getVolumeProfile(overlay.tf);
        if (!cancelled && data.levels?.length) {
          vpDataRef.current.set(overlay.tf, data);
        }
      } catch { /* skip if not available */ }
    });
    Promise.all(fetches).then(() => {
      if (!cancelled) {
        setVpLoaded(n => n + 1);
        drawVPOverlay();
      }
    });
    return () => { cancelled = true; };
  }, [session, drawVPOverlay]); // refetch when session updates

  // Redraw when VP data loads
  useEffect(() => { drawVPOverlay(); }, [vpLoaded, drawVPOverlay]);

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
      { time: (now - 7200) as Time, value: anchor + pad },
      { time: now as Time,          value: anchor - pad },
    ] as LineData<Time>[]);
    chartRef.current?.timeScale().scrollToRealTime();
  }, [noData, session]);

  // Developing VWAP + SD bands (computed from candle data)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove old VWAP series
    vwapSeriesRefs.current.forEach(s => {
      try { chart.removeSeries(s); } catch {}
    });
    vwapSeriesRefs.current = [];

    const candles = candlesRef.current;
    if (!candles.length) return;

    // Compute developing VWAP from candle data (HLC/3 for now, tick-based later)
    const vwapData: LineData<Time>[] = [];
    const sd1UpperData: LineData<Time>[] = [];
    const sd1LowerData: LineData<Time>[] = [];
    const sd2UpperData: LineData<Time>[] = [];
    const sd2LowerData: LineData<Time>[] = [];

    let cumTPV = 0;   // cumulative (typical_price * volume)
    let cumVol = 0;    // cumulative volume
    let cumTP2V = 0;   // cumulative (typical_price^2 * volume)

    // Find RTH start: look for the candle closest to 13:30 UTC (09:30 ET)
    // Reset VWAP at RTH open each day
    let lastDate = '';

    for (const c of candles) {
      const d = new Date(c.t * 1000);
      const dateStr = d.toISOString().slice(0, 10);
      const utcHour = d.getUTCHours();
      const utcMin = d.getUTCMinutes();
      const minuteOfDay = utcHour * 60 + utcMin;

      // RTH is ~13:30-20:00 UTC (09:30-16:00 ET, summer)
      // or ~14:30-21:00 UTC (09:30-16:00 ET, winter)
      // Use a simple heuristic: reset at first candle after 13:00 UTC on new day
      if (dateStr !== lastDate && minuteOfDay >= 780) {
        cumTPV = 0;
        cumVol = 0;
        cumTP2V = 0;
        lastDate = dateStr;
      }

      // Skip pre-RTH candles (before ~13:00 UTC)
      if (minuteOfDay < 780) continue;
      // Skip post-RTH candles (after ~21:00 UTC)
      if (minuteOfDay >= 1260) continue;

      const tp = (c.h + c.l + c.c) / 3;
      const vol = c.v || 1;

      cumTPV += tp * vol;
      cumVol += vol;
      cumTP2V += tp * tp * vol;

      if (cumVol === 0) continue;

      const vwap = cumTPV / cumVol;
      const variance = Math.max(0, (cumTP2V / cumVol) - vwap * vwap);
      const sd = Math.sqrt(variance);

      const t = c.t as Time;
      vwapData.push({ time: t, value: vwap });
      sd1UpperData.push({ time: t, value: vwap + sd });
      sd1LowerData.push({ time: t, value: vwap - sd });
      sd2UpperData.push({ time: t, value: vwap + 2 * sd });
      sd2LowerData.push({ time: t, value: vwap - 2 * sd });
    }

    if (!vwapData.length) return;

    // Create line series for VWAP and bands
    const addLine = (color: string, width: 1 | 2, style: number, data: LineData<Time>[]) => {
      const s = chart.addSeries(LineSeries, {
        color,
        lineWidth: width,
        lineStyle: style,
        lastValueVisible: true,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(data);
      vwapSeriesRefs.current.push(s);
      return s;
    };

    addLine('#06B6D4', 2, LineStyle.Solid, vwapData);        // VWAP
    addLine('rgba(6,182,212,0.5)', 1, LineStyle.Solid, sd1UpperData);  // +1SD
    addLine('rgba(6,182,212,0.5)', 1, LineStyle.Solid, sd1LowerData);  // -1SD
    addLine('rgba(6,182,212,0.25)', 1, LineStyle.Dashed, sd2UpperData); // +2SD
    addLine('rgba(6,182,212,0.25)', 1, LineStyle.Dashed, sd2LowerData); // -2SD

  }, [session, lastCandle]);

  // Static reference lines: IB, PDH/PDL, dPOC (these are flat — correct for structural levels)
  useEffect(() => {
    const series = priceSeriesRef.current;
    if (!series) return;

    Object.values(priceLineRefs.current).forEach(line => {
      try { series.removePriceLine(line); } catch {}
    });
    priceLineRefs.current = {};

    if (!session) return;
    const s = session.session;
    const p = session.profiles;

    const add = (key: string, price: number | undefined | null, color: string, title: string, style = LineStyle.Dashed, width: 1 | 2 = 1) => {
      if (price == null || price === 0) return;
      priceLineRefs.current[key] = series.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title });
    };

    // Initial Balance
    add('ibh', s.ib_high, '#F59E0B', 'IBH', LineStyle.Dotted);
    add('ibl', s.ib_low,  '#F59E0B', 'IBL', LineStyle.Dotted);

    // Prior Day High/Low
    add('pdh', s.pdh, '#FB923C', 'PDH', LineStyle.Dashed);
    add('pdl', s.pdl, '#FB923C', 'PDL', LineStyle.Dashed);

    // Daily Volume Profile POC only
    add('d_poc', p?.session?.poc, '#A855F7', 'dPOC', LineStyle.Solid, 1);
  }, [session]);

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
