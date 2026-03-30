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
import type { CandleData, ExpandedSession, SessionTPOResponse, SessionTPOData } from '@/types/market';

const INTERVAL = '1m';
const INITIAL_DAYS = 3;
const SCROLL_DAYS = 1;
const CANDLE_CACHE_KEY = 'firev_candles_1m';

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

interface Props {
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
  hiddenLevels?: Set<string>;
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

/** Build session boxes from backend-computed SessionLevelDay data (today only).
 *  H/L come from backend 1m bars (with Databento backfill), not from chart candles.
 *  This ensures boxes are accurate even when chart data has gaps.
 *  Only draws boxes for today's CET date — prior days use dashed level lines instead. */
/** Build session boxes from chart candles + backend time boundaries.
 *  H/L and X bounds are computed from actual candles within each session window,
 *  ensuring boxes align with visible chart data in business-time mode. */
function buildSessionBoxes(
  slDays: import('@/types/market').SessionLevelDay[],
  candles: CandleData[],
): SessionBox[] {
  // Pick the most recent day that has actual session data (skip weekends/holidays)
  const sorted = [...slDays].sort((a, b) => b.date.localeCompare(a.date));
  const latest = sorted.find(d => d.ny_high != null || d.tokyo_high != null);
  if (!latest || candles.length === 0) return [];

  const boxes: SessionBox[] = [];

  const mapping: Array<{
    name: string;
    startField: keyof import('@/types/market').SessionLevelDay;
    endField: keyof import('@/types/market').SessionLevelDay;
    def: typeof SESSION_DEFS[number];
  }> = [
    { name: 'Tokyo',    startField: 'tokyo_start',  endField: 'tokyo_end',  def: SESSION_DEFS[0] },
    { name: 'London',   startField: 'london_start', endField: 'london_end', def: SESSION_DEFS[1] },
    { name: 'New York', startField: 'ny_start',     endField: 'ny_end',     def: SESSION_DEFS[2] },
  ];

  for (const m of mapping) {
    const sessionStart = latest[m.startField] as number;
    const sessionEnd = latest[m.endField] as number;

    // Filter candles that fall within this session's time window
    const sessionCandles = candles.filter(c => c.t >= sessionStart && c.t < sessionEnd);
    if (sessionCandles.length === 0) continue;

    // Compute H/L from actual chart candles (matches what user sees)
    const high = Math.max(...sessionCandles.map(c => c.h));
    const low = Math.min(...sessionCandles.map(c => c.l));

    // Use first/last candle timestamps as box boundaries (snaps to chart grid)
    const firstT = sessionCandles[0].t;
    const lastT = sessionCandles[sessionCandles.length - 1].t;

    boxes.push({
      name: m.name,
      high,
      low,
      startEpoch: firstT,
      endEpoch: lastT,
      color: m.def.color,
      border: m.def.border,
      labelColor: m.def.label,
      cetDate: latest.date,
    });
  }

  return boxes;
}

/** Deduplicate by timestamp and sort ascending — prevents lightweight-charts "Cannot update oldest data" crash. */
function dedupeAndSort(candles: CandleData[]): CandleData[] {
  const map = new Map<number, CandleData>();
  for (const c of candles) map.set(c.t, c); // last-write-wins for dupes
  return Array.from(map.values()).sort((a, b) => a.t - b.t);
}

export function CandleChart({ lastCandle, session, hiddenLevels }: Props) {
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

  // Session levels overlay data (per-day IB, Tokyo, London, swing levels)
  const sessionLevelsRef = useRef<import('@/types/market').SessionLevelDay[]>([]);
  const [slLoaded, setSlLoaded] = useState(false);

  // Per-session TPO letter grid data
  const sessionTPORef = useRef<SessionTPOResponse | null>(null);
  const [sessionTPOLoaded, setSessionTPOLoaded] = useState(false);

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

    // --- Session boxes (H/L + time boundaries from backend SessionLevelDay) ---
    const slDays = sessionLevelsRef.current;
    const boxes = slDays.length > 0 ? buildSessionBoxes(slDays, candlesRef.current) : [];


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
    // Skip weekend/holiday days with no session data
    const latestSL = [...slDays].sort((a, b) => b.date.localeCompare(a.date))
      .find(d => d.ny_high != null || d.tokyo_high != null);
    // Session H/L extension lines — use box H/L (from chart candles) for consistency
    const sessionLineMeta: Record<string, { hKey: string; lKey: string; hLabel: string; lLabel: string; color: string }> = {
      'Tokyo':    { hKey: 'tokyo_h', lKey: 'tokyo_l', hLabel: 'TKY H', lLabel: 'TKY L', color: '#22D3EE' },
      'London':   { hKey: 'london_h', lKey: 'london_l', hLabel: 'LDN H', lLabel: 'LDN L', color: '#34D399' },
    };

    // Draw session H/L dashed lines from box end to day end (22:00 CET)
    if (boxes.length > 0) {
      for (const box of boxes) {
        const meta = sessionLineMeta[box.name];
        if (!meta) continue;

        // Use box H/L (computed from chart candles — matches visible data)
        const lineHigh = box.high;
        const lineLow = box.low;

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

      // --- Swing levels from session-levels API ---
      if (latestSL) {
        const swingLevels: { key: string; price: number | null; label: string; color: string }[] = [
          { key: 'daily_swing_high', price: latestSL.daily_swing_high, label: 'D-SH', color: '#e2e8f0' },
          { key: 'daily_swing_low', price: latestSL.daily_swing_low, label: 'D-SL', color: '#e2e8f0' },
          { key: 'weekly_swing_high', price: latestSL.weekly_swing_high, label: 'W-SH', color: '#3b82f6' },
          { key: 'weekly_swing_low', price: latestSL.weekly_swing_low, label: 'W-SL', color: '#3b82f6' },
          { key: 'monthly_swing_high', price: latestSL.monthly_swing_high, label: 'M-SH', color: '#a855f7' },
          { key: 'monthly_swing_low', price: latestSL.monthly_swing_low, label: 'M-SL', color: '#a855f7' },
        ];

        for (const { key, price, label, color } of swingLevels) {
          if (price == null) continue;
          if (slHidden?.has(key)) continue;
          const y = pSeries.priceToCoordinate(price);
          if (y === null) continue;

          ctx.save();
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.setLineDash([6, 3]);
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(rect.width, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = color;
          ctx.textAlign = 'left';
          ctx.fillText(label, 3, y - 3);
          ctx.restore();
        }
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

    // --- Per-session TPO letter grids (inside session boxes) ---
    const sessionTPO = sessionTPORef.current;
    if (sessionTPO && boxes.length > 0) {
      const SESSION_TPO_MAP: Record<string, { data: SessionTPOData | null; hiddenKey: string; profileColor: string; levelColor: string }> = {
        'Tokyo':    { data: sessionTPO.sessions.tokyo,  hiddenKey: 'tpo_tky_letters', profileColor: '#0891B2', levelColor: '#06B6D4' },
        'London':   { data: sessionTPO.sessions.london, hiddenKey: 'tpo_ldn_letters', profileColor: '#059669', levelColor: '#10B981' },
        'New York': { data: sessionTPO.sessions.ny,     hiddenKey: 'tpo_ny_letters',  profileColor: '#DC2626', levelColor: '#EF4444' },
      };

      for (const box of boxes) {
        const tpoMeta = SESSION_TPO_MAP[box.name];
        if (!tpoMeta || !tpoMeta.data || hidden?.has(tpoMeta.hiddenKey)) continue;
        const tpoSession = tpoMeta.data;
        const profileColor = tpoMeta.profileColor;
        const levelColor = tpoMeta.levelColor;

        // Box edges
        const boxLeftX = timeScale.timeToCoordinate(toLocalEpoch(box.startEpoch) as Time);
        const boxRightX = timeScale.timeToCoordinate(toLocalEpoch(box.endEpoch) as Time);
        if (boxLeftX === null && boxRightX === null) continue;
        const startX = (boxLeftX ?? 0) + 2; // left edge + padding
        const endX = boxRightX ?? rect.width;
        const boxWidth = Math.abs(endX - startX);

        // TPO letter grid — classic market profile style
        const letterKeys = Object.keys(tpoSession.letters);
        if (letterKeys.length === 0) continue;

        // Compute cell height from price spacing on chart
        const sortedNums = letterKeys.map(Number).sort((a, b) => a - b);
        let cellH = 1;
        if (sortedNums.length >= 2) {
          const y0 = pSeries.priceToCoordinate(sortedNums[0]);
          const y1 = pSeries.priceToCoordinate(sortedNums[1]);
          if (y0 !== null && y1 !== null) {
            cellH = Math.max(2, Math.abs(y0 - y1) - 0.5);
          }
        }

        // Cell width: proportional to height, capped to fit
        const maxLetters = Math.max(...letterKeys.map(k => tpoSession.letters[k].length));
        const cellW = Math.min(Math.max(cellH * 0.8, 6), Math.floor(boxWidth * 0.4 / Math.max(maxLetters, 1)));
        const showText = cellH >= 7 && cellW >= 6;
        if (showText) {
          ctx.font = `${Math.min(cellH - 1, cellW - 1, 9)}px monospace`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
        }

        // IB letters (first two 30-min periods)
        const ibLetters = new Set(['A', 'B']);

        for (const pk of letterKeys) {
          const priceNum = Number(pk);
          const y = pSeries.priceToCoordinate(priceNum);
          if (y === null || y < 0 || y > rect.height) continue;

          const letters = tpoSession.letters[pk];
          const isPOC = priceNum === tpoSession.poc;
          const inVA = priceNum >= tpoSession.val && priceNum <= tpoSession.vah;

          for (let i = 0; i < letters.length; i++) {
            const letter = letters[i];
            const x = startX + i * cellW;
            if (x + cellW > endX) break;

            const isIB = ibLetters.has(letter);

            // Color: IB = brighter, VA = medium, outside VA = dimmer
            if (isPOC) {
              ctx.fillStyle = profileColor;
              ctx.globalAlpha = 0.85;
            } else if (isIB) {
              ctx.fillStyle = '#C084FC'; // purple for IB letters
              ctx.globalAlpha = inVA ? 0.7 : 0.5;
            } else {
              ctx.fillStyle = profileColor;
              ctx.globalAlpha = inVA ? 0.55 : 0.3;
            }

            // Draw cell block
            ctx.fillRect(x, y - cellH / 2, cellW - 0.5, cellH - 0.5);

            // Draw letter text inside cell
            if (showText) {
              ctx.fillStyle = isPOC ? '#fff' : isIB ? '#E9D5FF' : '#D1D5DB';
              ctx.globalAlpha = isPOC ? 1.0 : 0.9;
              ctx.fillText(letter, x + cellW / 2, y);
            }
          }

          // POC: bright left border accent
          if (isPOC) {
            ctx.fillStyle = '#fff';
            ctx.globalAlpha = 0.9;
            ctx.fillRect(startX - 1, y - cellH / 2, 1.5, cellH);
          }
        }
        ctx.globalAlpha = 1.0;

        // --- Session metadata footer at bottom of box ---
        const boxBottomY = pSeries.priceToCoordinate(box.low);
        if (boxBottomY !== null) {
          const ibRange = tpoSession.ib_valid
            ? ((tpoSession.ib_high - tpoSession.ib_low) / 0.25).toFixed(0)
            : '—';
          const arrow = tpoSession.opening_direction === 'up' ? '↑'
            : tpoSession.opening_direction === 'down' ? '↓' : '↔';
          const footerText = `${tpoSession.shape}  IB:${ibRange}  ${tpoSession.opening_type}${arrow}`;
          ctx.font = '8px monospace';
          ctx.fillStyle = profileColor;
          ctx.globalAlpha = 0.5;
          ctx.textAlign = 'left';
          ctx.fillText(footerText, startX, boxBottomY + 12);
          ctx.globalAlpha = 1.0;
        }

        // --- POC/VAH/VAL dashed extension lines (anchored to TPO profile) ---
        const dayEndEpoch = box.endEpoch + (22 * 60 - epochToCETMinute(box.endEpoch)) * 60;
        const lineEndX = timeScale.timeToCoordinate(toLocalEpoch(dayEndEpoch) as Time);

        const prefixMap: Record<string, string> = { 'Tokyo': 'tky', 'London': 'ldn', 'New York': 'ny' };
        const prefix = prefixMap[box.name] || '';

        const levels: Array<{ price: number; label: string; alpha: number; dash: number[]; key: string; color?: string }> = [
          { price: tpoSession.poc, label: `${prefix} tPOC`, alpha: 0.6, dash: [4, 3], key: `tpo_${prefix}_poc` },
          { price: tpoSession.vah, label: `${prefix} tVAH`, alpha: 0.4, dash: [2, 3], key: `tpo_${prefix}_vah` },
          { price: tpoSession.val, label: `${prefix} tVAL`, alpha: 0.4, dash: [2, 3], key: `tpo_${prefix}_val` },
        ];
        if (tpoSession.ib_valid) {
          levels.push(
            { price: tpoSession.ib_high, label: `${prefix} IBH`, alpha: 0.5, dash: [3, 3], key: `tpo_${prefix}_ibh`, color: '#F59E0B' },
            { price: tpoSession.ib_low, label: `${prefix} IBL`, alpha: 0.5, dash: [3, 3], key: `tpo_${prefix}_ibl`, color: '#F59E0B' },
          );
        }

        for (const lv of levels) {
          if (hidden?.has(lv.key)) continue;
          const y = pSeries.priceToCoordinate(lv.price);
          if (y === null) continue;

          // Anchor from TPO profile (left edge of session box)
          const lx = startX;
          const rx = lineEndX ?? rect.width;
          if (rx < 0 || lx > rect.width) continue;
          const drawX1 = Math.max(0, lx);
          const drawX2 = Math.min(rect.width, rx);

          const lvColor = lv.color ?? levelColor;
          ctx.save();
          ctx.strokeStyle = lvColor;
          ctx.globalAlpha = lv.alpha;
          ctx.lineWidth = 1;
          ctx.setLineDash(lv.dash);
          ctx.beginPath();
          ctx.moveTo(drawX1, y);
          ctx.lineTo(drawX2, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = lvColor;
          ctx.textAlign = 'left';
          ctx.fillText(lv.label, drawX1 + 3, y - 3);
          ctx.globalAlpha = 1.0;
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
      const cached = sessionStorage.getItem(CANDLE_CACHE_KEY);
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
        const res = await api.getCandles('NQ', INTERVAL, undefined, INITIAL_DAYS);
        if (res.candles?.length) {
          const cleaned = res.candles.map(c => ({ ...c, t: Number(c.t) })).filter(c => !isNaN(c.t) && c.t > 0);
          const sorted = dedupeAndSort(cleaned);
          applyCandles(sorted);
          // Persist to cache for next page load
          try { sessionStorage.setItem(CANDLE_CACHE_KEY, JSON.stringify(sorted)); } catch { /* quota */ }
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

  // Subscribe VP overlay redraws to chart events (throttled to ~60fps)
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

    const observer = new ResizeObserver(redraw);
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(redraw);
      observer.disconnect();
    };
  }, [drawOverlays]);

  // Fetch VP curve data for all timeframes (daily, weekly, monthly) — once on mount
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch session levels for multi-day overlay — once on mount
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch per-session TPO letter grid data — once on mount
  useEffect(() => {
    let cancelled = false;
    api.getSessionTPO('NQ').then(res => {
      if (!cancelled && res.sessions) {
        sessionTPORef.current = res;
        setSessionTPOLoaded(true);
        drawOverlays();
      }
    }).catch(err => { console.warn('[SessionTPO] fetch failed:', err); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Redraw when VP data loads, TPO changes, or visibility changes
  useEffect(() => { drawOverlays(); }, [vpLoaded, slLoaded, sessionTPOLoaded, hiddenLevels, drawOverlays]);

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
    // Don't update until initial data is loaded — prevents "Cannot update oldest data"
    if (loading || candlesRef.current.length === 0) return;
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
      try { sessionStorage.setItem(CANDLE_CACHE_KEY, JSON.stringify(existing)); } catch { /* quota */ }
    }
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
