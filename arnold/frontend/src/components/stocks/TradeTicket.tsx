import { useEffect, useMemo, useState } from 'react'
import type {
  DQNInferenceEvent,
  GateBlocker,
  ModelStatus,
  ObservationSchema,
  Quote,
} from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  inferenceAt: number | null
  schema: ObservationSchema | null
  lastPrice: number | null
  quote: Quote | null
  modelStatus: ModelStatus | null
}

const TICK_SIZE = 0.25
const NQ_DOLLARS_PER_TICK = 5  // NQ futures: $5 per tick per contract.

const BLOCKER_DETAIL: Record<NonNullable<GateBlocker>, string> = {
  halted: 'trading halted',
  model_skip: 'model SKIP',
  confidence: 'confidence below floor',
  orderflow: 'orderflow below floor',
  in_position: 'already in position',
}

function fmtAge(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  if (ms < 3600_000) {
    const m = Math.floor(ms / 60_000)
    const s = Math.round((ms % 60_000) / 1000)
    return `${m}m ${s}s`
  }
  const h = Math.floor(ms / 3600_000)
  const m = Math.round((ms % 3600_000) / 60_000)
  return `${h}h ${m}m`
}

function activeLabels(
  inputs: number[],
  schema: ObservationSchema,
  segName: string,
  threshold: number,
): string[] {
  const seg = schema.segments.find((s) => s.name === segName)
  if (!seg) return []
  const out: string[] = []
  for (let i = 0; i < seg.size; i++) {
    if ((inputs[seg.start + i] ?? 0) > threshold) out.push(seg.labels[i])
  }
  return out
}

function PriceLadder({
  entry,
  stop,
  last,
  zoneCenter,
  zoneUpper,
  zoneLower,
  isLong,
}: {
  entry: number
  stop: number
  last: number | null
  zoneCenter: number | null
  zoneUpper: number | null
  zoneLower: number | null
  isLong: boolean
}) {
  // Vertical ladder: top of canvas = highest price; for long, stop is below entry.
  const W = 240
  const H = 200
  const lo = Math.min(entry, stop, last ?? entry, zoneLower ?? entry)
  const hi = Math.max(entry, stop, last ?? entry, zoneUpper ?? entry)
  const pad = (hi - lo) * 0.15 || TICK_SIZE * 4
  const range = hi - lo + pad * 2
  const top = hi + pad
  const yOf = (p: number) => ((top - p) / range) * H

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ maxHeight: 220 }}>
      <rect width={W} height={H} fill="#09090b" rx="4" />
      {zoneUpper !== null && zoneLower !== null && (
        <rect
          x={20}
          y={yOf(zoneUpper)}
          width={W - 40}
          height={Math.max(2, yOf(zoneLower) - yOf(zoneUpper))}
          fill="#a78bfa"
          opacity="0.18"
        />
      )}
      {zoneCenter !== null && (
        <line
          x1={20}
          x2={W - 20}
          y1={yOf(zoneCenter)}
          y2={yOf(zoneCenter)}
          stroke="#a78bfa"
          strokeWidth={0.6}
          strokeDasharray="4 3"
          opacity="0.7"
        />
      )}
      {/* Stop */}
      <line
        x1={20}
        x2={W - 20}
        y1={yOf(stop)}
        y2={yOf(stop)}
        stroke="#ef4444"
        strokeWidth={1.5}
      />
      <text x={W - 24} y={yOf(stop) - 3} textAnchor="end" fill="#fca5a5" fontSize="10">
        STOP {stop.toFixed(2)}
      </text>
      {/* Entry */}
      <line
        x1={20}
        x2={W - 20}
        y1={yOf(entry)}
        y2={yOf(entry)}
        stroke="#fbbf24"
        strokeWidth={2}
      />
      <text x={W - 24} y={yOf(entry) - 3} textAnchor="end" fill="#fde68a" fontSize="10">
        ENTRY {entry.toFixed(2)}
      </text>
      {/* Last */}
      {last !== null && (
        <>
          <line
            x1={20}
            x2={W - 20}
            y1={yOf(last)}
            y2={yOf(last)}
            stroke={isLong === (last >= entry) ? '#34d399' : '#f87171'}
            strokeWidth={1.2}
            strokeDasharray="2 2"
          />
          <circle cx={W - 30} cy={yOf(last)} r={4} fill="#fafafa" />
          <text x={24} y={yOf(last) - 3} fill="#fafafa" fontSize="10">
            LAST {last.toFixed(2)}
          </text>
        </>
      )}
    </svg>
  )
}

function GateRow({
  pass,
  blocker,
  label,
  detail,
}: {
  pass: boolean
  blocker: boolean
  label: string
  detail: string
}) {
  const icon = pass ? '✓' : '✗'
  const color = pass
    ? 'text-emerald-400'
    : blocker
      ? 'text-red-400'
      : 'text-zinc-500'
  return (
    <li className="flex items-baseline justify-between gap-2">
      <span className="flex items-baseline gap-2">
        <span className={`font-bold w-3 ${color}`}>{icon}</span>
        <span className="text-zinc-300">{label}</span>
      </span>
      <span className={blocker ? 'text-red-300' : 'text-zinc-500'}>{detail}</span>
    </li>
  )
}

export function TradeTicket({
  inference,
  inferenceAt,
  schema,
  lastPrice,
  quote,
  modelStatus,
}: Props) {
  const [now, setNow] = useState(Date.now())
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(iv)
  }, [])

  const ageMs = inferenceAt !== null ? now - inferenceAt : null
  const isFresh = ageMs !== null && ageMs < 90_000
  const isStale = ageMs !== null && ageMs >= 90_000

  const action = inference?.action ?? '—'
  const isSkip = action === 'SKIP' || action === 'skip'

  const zoneCenter = inference?.zone_center ?? null
  const entryPrice = inference?.price ?? null
  const approachUp = zoneCenter !== null && entryPrice !== null ? entryPrice < zoneCenter : true
  const isShort =
    !isSkip &&
    ((action === 'REVERSAL' && approachUp) || (action === 'CONTINUATION' && !approachUp))
  const isLong = !isSkip && !isShort
  const direction = isSkip ? 'SKIP' : isShort ? 'SHORT' : 'LONG'

  const stopTicksRaw = inference?.stop_ticks ?? null
  const stopTicks = stopTicksRaw !== null ? Math.round(stopTicksRaw) : null
  const stopOffsetPts = stopTicks !== null ? stopTicks * TICK_SIZE : null
  const stopPrice =
    stopTicks !== null && entryPrice !== null
      ? isShort
        ? entryPrice + stopTicks * TICK_SIZE
        : entryPrice - stopTicks * TICK_SIZE
      : null
  const riskPerContract = stopTicks !== null ? stopTicks * NQ_DOLLARS_PER_TICK : null

  const breakdown = inference?.stop_breakdown ?? null
  const baseTicks = breakdown && typeof breakdown.base_ticks === 'number' ? breakdown.base_ticks : null
  const finalTicks = breakdown && typeof breakdown.final_ticks === 'number' ? breakdown.final_ticks : null
  const stopBreakdownLabel =
    baseTicks !== null && finalTicks !== null && Math.round(baseTicks) !== Math.round(finalTicks)
      ? `base ${Math.round(baseTicks)}t · final ${Math.round(finalTicks)}t (+${Math.round(finalTicks - baseTicks)}t)`
      : null

  const conf = inference?.confidence ?? 0
  const contP = inference?.cont_p ?? 0
  const revP = inference?.rev_p ?? 0
  const contEv = inference?.cont_ev ?? null
  const revEv = inference?.rev_ev ?? null
  const sizeMult = inference?.size_multiplier ?? inference?.sizing_signal ?? null

  // Live deviation from entry on the user's "what if I take this NOW" view.
  const liveDeltaPts = entryPrice !== null && lastPrice !== null ? lastPrice - entryPrice : null
  const liveDeltaTicks = liveDeltaPts !== null ? liveDeltaPts / TICK_SIZE : null
  const slippageBad =
    liveDeltaTicks !== null &&
    ((isLong && liveDeltaTicks > 2) || (isShort && liveDeltaTicks < -2))
  const slippageGood =
    liveDeltaTicks !== null &&
    ((isLong && liveDeltaTicks < -1) || (isShort && liveDeltaTicks > 1))

  // Zone band (best-effort — not directly in inference, derive from zone center).
  // The actual band came from the zone update broadcast; we don't have it here.
  // For the ladder we use ±10 ticks as a placeholder if we have center but no band.
  const zoneUpper = zoneCenter !== null ? zoneCenter + TICK_SIZE * 8 : null
  const zoneLower = zoneCenter !== null ? zoneCenter - TICK_SIZE * 8 : null

  // Pre-trade gates from server.
  const gates = inference?.gates ?? null
  const dispatched = gates?.decision === 'DISPATCHED'

  // Confluence + patterns + setups (active-only)
  const confluence = useMemo(
    () =>
      inference && schema
        ? activeLabels(inference.inputs ?? [], schema, 'zone_composition', 0)
        : [],
    [inference, schema],
  )
  const patterns = useMemo(
    () =>
      inference && schema ? activeLabels(inference.inputs ?? [], schema, 'pattern', 0.5) : [],
    [inference, schema],
  )
  const setups = useMemo(
    () =>
      inference && schema ? activeLabels(inference.inputs ?? [], schema, 'setup', 0.5) : [],
    [inference, schema],
  )

  const halted = modelStatus?.halted ?? false
  const isFlat = modelStatus?.is_flat ?? true

  if (!inference || !inferenceAt) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-4 text-xs font-mono text-center text-zinc-500">
        Waiting for first zone touch — no signal to ticket yet.
      </div>
    )
  }

  const dirColor = isSkip ? 'text-amber-400' : isLong ? 'text-emerald-400' : 'text-red-400'
  const borderColor = isStale
    ? 'border-zinc-800'
    : isSkip
      ? 'border-amber-700/60'
      : isLong
        ? 'border-emerald-700/60'
        : 'border-red-700/60'

  const copyTicket = async () => {
    const lines: string[] = []
    lines.push(`Arnold trade ticket · ${new Date().toISOString()}`)
    lines.push('')
    lines.push(`${direction} NQ · ${sizeMult ? sizeMult.toFixed(2) : '1.00'}x`)
    if (entryPrice !== null) lines.push(`Entry: ${entryPrice.toFixed(2)}`)
    if (stopPrice !== null && stopTicks !== null && riskPerContract !== null) {
      lines.push(`Stop:  ${stopPrice.toFixed(2)}  (${stopTicks}t · $${riskPerContract})`)
    }
    lines.push(
      `Edge: conf ${conf.toFixed(2)} · cont ${contP.toFixed(2)} / rev ${revP.toFixed(2)} · ${inference.model_type ?? '?'}`,
    )
    if (zoneCenter !== null) lines.push(`Zone center: ${zoneCenter.toFixed(2)}`)
    if (confluence.length) lines.push(`Confluence: ${confluence.join(', ')}`)
    if (patterns.length) lines.push(`Patterns: ${patterns.join(', ')}`)
    if (setups.length) lines.push(`Setups: ${setups.join(', ')}`)
    if (gates) {
      lines.push(`Server gates: ${gates.decision}${gates.blocker ? ` (${gates.blocker})` : ''}`)
    }
    lines.push(`Signal age: ${ageMs ? fmtAge(ageMs) : '?'}`)
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked */
    }
  }

  return (
    <div className={`rounded border-2 ${borderColor} bg-zinc-900 p-4 font-mono text-xs`}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-3">
        <span className="text-zinc-500 uppercase tracking-wider">Trade ticket</span>
        {ageMs !== null && (
          <span
            className={`text-[10px] px-2 py-0.5 rounded ${isFresh ? 'bg-emerald-900/40 text-emerald-300' : 'bg-zinc-800 text-zinc-500'}`}
          >
            {fmtAge(ageMs)} ago
            {isStale && ' · stale'}
          </span>
        )}
        {gates &&
          (dispatched ? (
            <span className="px-2 py-0.5 rounded text-[10px] uppercase tracking-wider bg-emerald-900/50 text-emerald-300">
              autonomous: dispatched
            </span>
          ) : (
            <span className="px-2 py-0.5 rounded text-[10px] uppercase tracking-wider bg-red-900/50 text-red-300">
              autonomous: blocked · {gates.blocker ? BLOCKER_DETAIL[gates.blocker] : '?'}
            </span>
          ))}
        <button
          type="button"
          onClick={copyTicket}
          className="ml-auto text-[10px] uppercase tracking-wider px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200"
        >
          {copied ? '✓ copied' : 'copy ticket'}
        </button>
      </div>

      {/* Main: 2 columns. Left = the trade. Right = price ladder. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-3">
          {/* Big direction line */}
          <div className="flex items-center gap-3">
            <span className={`text-3xl font-bold ${dirColor}`}>
              {isShort ? '▼' : isLong ? '▲' : '–'} {direction}
            </span>
            {!isSkip && sizeMult !== null && (
              <span className="text-zinc-500">
                size <span className="text-zinc-200">{sizeMult.toFixed(2)}x</span>
              </span>
            )}
            <span className="text-zinc-500">·</span>
            <span className="text-zinc-300">{action}</span>
            {!isFlat && (
              <span className="ml-auto px-2 py-0.5 rounded text-[10px] bg-amber-900/40 text-amber-300 uppercase">
                already in position
              </span>
            )}
            {halted && (
              <span className="px-2 py-0.5 rounded text-[10px] bg-red-900/50 text-red-300 uppercase">
                halted
              </span>
            )}
          </div>

          {/* Entry / Stop / Risk row */}
          <div className="grid grid-cols-3 gap-3 pt-2 border-t border-zinc-800">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">Entry</div>
              <div className="text-zinc-200 tabular-nums text-base">
                {entryPrice !== null ? entryPrice.toFixed(2) : '—'}
              </div>
              {liveDeltaTicks !== null && (
                <div
                  className={`text-[10px] tabular-nums ${slippageBad ? 'text-red-400' : slippageGood ? 'text-emerald-400' : 'text-zinc-500'}`}
                >
                  live {lastPrice?.toFixed(2)} · {liveDeltaTicks > 0 ? '+' : ''}
                  {liveDeltaTicks.toFixed(1)}t {slippageBad ? 'worse fill' : slippageGood ? 'better fill' : ''}
                </div>
              )}
              {quote && (
                <div className="text-[10px] tabular-nums text-zinc-500">
                  bid {quote.bid?.toFixed(2)} · ask {quote.ask?.toFixed(2)} · spread{' '}
                  {quote.bid && quote.ask
                    ? Math.round((quote.ask - quote.bid) / TICK_SIZE)
                    : '—'}
                  t
                </div>
              )}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">Stop</div>
              <div className="text-zinc-200 tabular-nums text-base">
                {stopPrice !== null ? stopPrice.toFixed(2) : '—'}
              </div>
              {stopTicks !== null && (
                <div className="text-[10px] tabular-nums text-zinc-500">
                  {stopTicks}t · {stopOffsetPts?.toFixed(2)}pt
                </div>
              )}
              {stopBreakdownLabel && (
                <div className="text-[10px] text-zinc-600">{stopBreakdownLabel}</div>
              )}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">
                Risk per contract
              </div>
              <div className="text-zinc-200 tabular-nums text-base">
                {riskPerContract !== null ? `$${riskPerContract}` : '—'}
              </div>
              {sizeMult !== null && riskPerContract !== null && (
                <div className="text-[10px] tabular-nums text-zinc-500">
                  ×{sizeMult.toFixed(2)} = ${(riskPerContract * sizeMult).toFixed(0)} suggested
                </div>
              )}
            </div>
          </div>

          {/* Edge */}
          <div className="grid grid-cols-3 gap-3 pt-2 border-t border-zinc-800">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">Confidence</div>
              <div className="text-zinc-200 tabular-nums text-base">{conf.toFixed(3)}</div>
              {inference.composite_confidence !== undefined && (
                <div className="text-[10px] tabular-nums text-zinc-500">
                  composite {inference.composite_confidence.toFixed(2)}
                </div>
              )}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">cont / rev</div>
              <div className="text-base tabular-nums">
                <span className={action === 'CONTINUATION' ? 'text-emerald-300' : 'text-zinc-300'}>
                  {contP.toFixed(2)}
                </span>
                <span className="text-zinc-600"> / </span>
                <span className={action === 'REVERSAL' ? 'text-orange-300' : 'text-zinc-300'}>
                  {revP.toFixed(2)}
                </span>
              </div>
              {(contEv !== null || revEv !== null) && (
                <div className="text-[10px] tabular-nums text-zinc-500">
                  EV {contEv?.toFixed(2) ?? '—'} / {revEv?.toFixed(2) ?? '—'}
                </div>
              )}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">Order flow</div>
              <div
                className={`text-base tabular-nums ${
                  gates?.of_score && gates.of_score >= 0.30
                    ? 'text-emerald-400'
                    : gates?.of_score && gates.of_score >= 0.15
                      ? 'text-amber-300'
                      : 'text-red-300'
                }`}
              >
                {gates?.of_score !== undefined ? gates.of_score.toFixed(2) : '—'}
              </div>
              {gates && (
                <div className="text-[10px] tabular-nums text-zinc-500">
                  floor {gates.of_floor.toFixed(2)}
                </div>
              )}
            </div>
          </div>

          {/* Pre-trade gates */}
          {gates && (
            <div className="pt-2 border-t border-zinc-800">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                Pre-trade gates (server view)
              </div>
              <ul className="space-y-1">
                <GateRow
                  pass={gates.model_action !== 'SKIP' && gates.model_action !== 'skip'}
                  blocker={gates.blocker === 'model_skip'}
                  label="Model action"
                  detail={gates.model_action}
                />
                <GateRow
                  pass={gates.conf_pass}
                  blocker={gates.blocker === 'confidence'}
                  label="Confidence"
                  detail={`${gates.confidence.toFixed(2)} ≥ ${gates.conf_floor.toFixed(2)}`}
                />
                <GateRow
                  pass={gates.of_pass}
                  blocker={gates.blocker === 'orderflow'}
                  label="Order flow"
                  detail={`${gates.of_score.toFixed(2)} ≥ ${gates.of_floor.toFixed(2)}`}
                />
                <GateRow
                  pass={gates.is_flat}
                  blocker={gates.blocker === 'in_position'}
                  label="Flat"
                  detail={gates.is_flat ? 'flat' : 'in position'}
                />
                <GateRow
                  pass={!gates.halted}
                  blocker={gates.blocker === 'halted'}
                  label="Not halted"
                  detail={gates.halted ? 'paused' : 'live'}
                />
              </ul>
              {!dispatched && (
                <div className="mt-2 text-[10px] text-amber-400">
                  Server skipped this. You may still take it manually if your conviction is higher
                  than the floor.
                </div>
              )}
            </div>
          )}

          {/* Confluence + patterns + setups */}
          {(confluence.length > 0 || patterns.length > 0 || setups.length > 0) && (
            <div className="pt-2 border-t border-zinc-800 space-y-1">
              {confluence.length > 0 && (
                <div className="flex flex-wrap gap-1 items-center">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500 mr-1">
                    Confluence ({confluence.length})
                  </span>
                  {confluence.map((c) => (
                    <span
                      key={c}
                      className="text-[10px] px-2 py-0.5 rounded-full border bg-violet-900/40 text-violet-200 border-violet-800"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
              {patterns.length > 0 && (
                <div className="flex flex-wrap gap-1 items-center">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500 mr-1">
                    Patterns
                  </span>
                  {patterns.map((p) => (
                    <span
                      key={p}
                      className="text-[10px] px-2 py-0.5 rounded-full border bg-amber-900/40 text-amber-200 border-amber-800"
                    >
                      {p}
                    </span>
                  ))}
                </div>
              )}
              {setups.length > 0 && (
                <div className="flex flex-wrap gap-1 items-center">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500 mr-1">
                    Setups
                  </span>
                  {setups.map((s) => (
                    <span
                      key={s}
                      className="text-[10px] px-2 py-0.5 rounded-full border bg-indigo-900/40 text-indigo-200 border-indigo-800"
                    >
                      {s}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right column: live ladder */}
        <div className="lg:col-span-1">
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            Price ladder
          </div>
          {entryPrice !== null && stopPrice !== null ? (
            <PriceLadder
              entry={entryPrice}
              stop={stopPrice}
              last={lastPrice}
              zoneCenter={zoneCenter}
              zoneUpper={zoneUpper}
              zoneLower={zoneLower}
              isLong={isLong}
            />
          ) : (
            <div className="text-zinc-500 text-[11px]">no entry/stop yet</div>
          )}
        </div>
      </div>
    </div>
  )
}
