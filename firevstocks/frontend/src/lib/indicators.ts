/**
 * Client-side indicator computation from OHLCV candles.
 * Eliminates server round-trips for VP, VWAP, and session levels.
 *
 * All times are UTC epochs. CET conversion uses Europe/Stockholm timezone.
 */
import type { CandleData, VPData, VWAPPoint, SessionLevelDay } from '@/types'

const TICK_SIZE = 0.25

// --- CET time helpers ---

function toCET(epoch: number): Date {
  // Create a Date in Stockholm timezone
  const s = new Date(epoch * 1000).toLocaleString('en-US', { timeZone: 'Europe/Stockholm' })
  return new Date(s)
}

function cetDate(epoch: number): string {
  return new Date(epoch * 1000).toLocaleDateString('sv-SE', { timeZone: 'Europe/Stockholm' })
}

function cetHourMin(epoch: number): number {
  const d = toCET(epoch)
  return d.getHours() * 60 + d.getMinutes()
}

// Session boundaries in CET minutes
const TOKYO_START = 0 * 60       // 00:00
const TOKYO_END = 9 * 60         // 09:00
const LONDON_START = 8 * 60      // 08:00
const LONDON_END = 16 * 60 + 30  // 16:30
const NY_START = 15 * 60 + 30    // 15:30
const NY_END = 22 * 60           // 22:00
const IB_DURATION = 60           // first 60 min of NY

// --- Volume Profile ---

export function computeVP(candles: CandleData[]): VPData {
  if (!candles.length) return { levels: [], poc: 0, vah: 0, val: 0, timeframe: 'session' }

  // Bucket volume by tick-snapped close price
  const buckets = new Map<number, number>()
  for (const c of candles) {
    const price = Math.round(c.c / TICK_SIZE) * TICK_SIZE
    buckets.set(price, (buckets.get(price) ?? 0) + c.v)
  }

  if (buckets.size === 0) return { levels: [], poc: 0, vah: 0, val: 0, timeframe: 'session' }

  // POC = price with highest volume
  let poc = 0, pocVol = 0
  for (const [p, v] of buckets) {
    if (v > pocVol) { poc = p; pocVol = v }
  }

  // Value Area = 70% of total volume expanding from POC
  const sortedPrices = [...buckets.keys()].sort((a, b) => a - b)
  const totalVol = [...buckets.values()].reduce((a, b) => a + b, 0)
  const vaTarget = totalVol * 0.70

  let pocIdx = sortedPrices.indexOf(poc)
  let lo = pocIdx, hi = pocIdx
  let vaVol = buckets.get(poc)!

  while (vaVol < vaTarget && (lo > 0 || hi < sortedPrices.length - 1)) {
    const upVol = hi < sortedPrices.length - 1 ? (buckets.get(sortedPrices[hi + 1]) ?? 0) : 0
    const dnVol = lo > 0 ? (buckets.get(sortedPrices[lo - 1]) ?? 0) : 0

    if (upVol >= dnVol && hi < sortedPrices.length - 1) {
      hi++; vaVol += buckets.get(sortedPrices[hi])!
    } else if (lo > 0) {
      lo--; vaVol += buckets.get(sortedPrices[lo])!
    } else {
      hi = Math.min(hi + 1, sortedPrices.length - 1)
      vaVol += buckets.get(sortedPrices[hi]) ?? 0
    }
  }

  const levels = sortedPrices
    .filter(p => (buckets.get(p) ?? 0) > 0)
    .map(p => ({ price: p, volume: buckets.get(p)! }))

  return {
    levels,
    poc,
    vah: sortedPrices[hi],
    val: sortedPrices[lo],
    timeframe: 'session',
  }
}

/** Compute per-day VPs from a multi-day candle array. Returns map of date→VPData. */
export function computeVPByDay(candles: CandleData[]): Map<string, VPData> {
  const byDay = new Map<string, CandleData[]>()
  for (const c of candles) {
    const d = cetDate(c.t)
    const arr = byDay.get(d)
    if (arr) arr.push(c); else byDay.set(d, [c])
  }

  const result = new Map<string, VPData>()
  for (const [date, bars] of byDay) {
    const vp = computeVP(bars)
    if (vp.levels.length > 0) result.set(date, vp)
  }
  return result
}

// --- VWAP with SD bands ---

export function computeVWAP(candles: CandleData[]): VWAPPoint[][] {
  if (!candles.length) return []

  // Group candles by CET date for daily reset
  const byDay = new Map<string, CandleData[]>()
  for (const c of candles) {
    const d = cetDate(c.t)
    const arr = byDay.get(d)
    if (arr) arr.push(c); else byDay.set(d, [c])
  }

  const days: VWAPPoint[][] = []

  for (const [, bars] of byDay) {
    const sorted = bars.sort((a, b) => a.t - b.t)
    let cumTPV = 0  // Σ(typical_price * volume)
    let cumV = 0    // Σ(volume)
    let cumTPV2 = 0 // Σ(typical_price² * volume) for variance
    const points: VWAPPoint[] = []

    for (const c of sorted) {
      const tp = (c.h + c.l + c.c) / 3
      cumTPV += tp * c.v
      cumV += c.v
      cumTPV2 += tp * tp * c.v

      if (cumV === 0) continue

      const vwap = cumTPV / cumV
      // Variance = Σ(tp²·v)/Σ(v) - vwap²
      const variance = Math.max(0, cumTPV2 / cumV - vwap * vwap)
      const sd = Math.sqrt(variance)

      points.push({
        t: c.t,
        vwap,
        sd1_u: vwap + sd,
        sd1_l: vwap - sd,
        sd2_u: vwap + 2 * sd,
        sd2_l: vwap - 2 * sd,
        sd3_u: vwap + 3 * sd,
        sd3_l: vwap - 3 * sd,
      })
    }

    if (points.length > 0) days.push(points)
  }

  return days
}

// --- Session Levels ---

/** Compute session levels (PDH/PDL, IB, Tokyo/London H/L) from candles. */
export function computeSessionLevels(candles: CandleData[]): SessionLevelDay[] {
  if (!candles.length) return []

  // Group by CET date
  const byDay = new Map<string, CandleData[]>()
  for (const c of candles) {
    const d = cetDate(c.t)
    const arr = byDay.get(d)
    if (arr) arr.push(c); else byDay.set(d, [c])
  }

  const sortedDates = [...byDay.keys()].sort()
  const results: SessionLevelDay[] = []

  for (let di = 0; di < sortedDates.length; di++) {
    const date = sortedDates[di]
    const bars = byDay.get(date)!.sort((a, b) => a.t - b.t)
    if (!bars.length) continue

    // PDH/PDL from previous day
    let pdh: number | null = null, pdl: number | null = null
    if (di > 0) {
      const prevBars = byDay.get(sortedDates[di - 1])!
      pdh = Math.max(...prevBars.map(b => b.h))
      pdl = Math.min(...prevBars.map(b => b.l))
    }

    // Session H/L scan
    let tokyoH: number | null = null, tokyoL: number | null = null
    let londonH: number | null = null, londonL: number | null = null
    let nyH: number | null = null, nyL: number | null = null
    let ibH: number | null = null, ibL: number | null = null

    // Track session epoch boundaries
    let tokyoStart = 0, tokyoEnd = 0
    let londonStart = 0, londonEnd = 0
    let nyStart = 0, nyEnd = 0
    let ibStart = 0, ibEnd = 0
    let dayStart = bars[0].t, dayEnd = bars[bars.length - 1].t

    for (const bar of bars) {
      const m = cetHourMin(bar.t)

      // Tokyo: 00:00 - 09:00 CET
      if (m >= TOKYO_START && m < TOKYO_END) {
        if (tokyoH === null || bar.h > tokyoH) tokyoH = bar.h
        if (tokyoL === null || bar.l < tokyoL) tokyoL = bar.l
        if (!tokyoStart) tokyoStart = bar.t
        tokyoEnd = bar.t
      }

      // London: 08:00 - 16:30 CET
      if (m >= LONDON_START && m < LONDON_END) {
        if (londonH === null || bar.h > londonH) londonH = bar.h
        if (londonL === null || bar.l < londonL) londonL = bar.l
        if (!londonStart) londonStart = bar.t
        londonEnd = bar.t
      }

      // NY: 15:30 - 22:00 CET
      if (m >= NY_START && m < NY_END) {
        if (nyH === null || bar.h > nyH) nyH = bar.h
        if (nyL === null || bar.l < nyL) nyL = bar.l
        if (!nyStart) nyStart = bar.t
        nyEnd = bar.t

        // IB = first 60 min of NY
        if (m < NY_START + IB_DURATION) {
          if (ibH === null || bar.h > ibH) ibH = bar.h
          if (ibL === null || bar.l < ibL) ibL = bar.l
          if (!ibStart) ibStart = bar.t
          ibEnd = bar.t
        }
      }
    }

    results.push({
      date,
      pdh, pdl,
      ib_high: ibH, ib_low: ibL,
      tokyo_high: tokyoH, tokyo_low: tokyoL,
      london_high: londonH, london_low: londonL,
      ny_high: nyH, ny_low: nyL,
      tokyo_start: tokyoStart, tokyo_end: tokyoEnd,
      london_start: londonStart, london_end: londonEnd,
      ib_start: ibStart, ib_end: ibEnd,
      ny_start: nyStart, ny_end: nyEnd,
      day_start: dayStart, day_end: dayEnd,
      // Swing levels need multi-week/month data — keep server-side
      daily_swing_high: null, daily_swing_low: null,
      weekly_swing_high: null, weekly_swing_low: null,
      monthly_swing_high: null, monthly_swing_low: null,
    })
  }

  return results
}
