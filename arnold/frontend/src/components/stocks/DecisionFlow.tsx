import { useMemo } from 'react'
import type { DQNInferenceEvent, ObservationSchema } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  schema: ObservationSchema | null
}

/**
 * Decision flow visualization — animated SVG showing how the live observation
 * vector compresses into the four-gate verdict. Four columns:
 *
 *   1. Feature segments  (23 nodes from observation_index.SEGMENTS)
 *   2. Group rollups     (4 super-groups: zone / orderflow / amt / narrative)
 *   3. Gates             (5 pass/fail nodes from inference.gates)
 *   4. Decision          (DISPATCHED / BLOCKED chip)
 *
 * Honest about the metaphor: the connectors are NOT the GBT/DQN's actual
 * weight matrix — the model is opaque, no per-feature attribution is
 * available. The connectors visualize where each feature group *would*
 * influence in a hand-built rule system: zones drive the action gate, OF
 * drives the OF gate, etc.
 */

type SuperGroup = 'zone' | 'orderflow' | 'amt' | 'narrative'

const SEG_GROUP: Record<string, SuperGroup> = {
  zone_composition: 'zone',
  zone_features: 'zone',
  zone_confluence: 'zone',
  zone_quality: 'zone',
  zone_memory: 'zone',
  structure: 'zone',
  tpo: 'zone',
  orderflow: 'orderflow',
  candles: 'orderflow',
  micro: 'orderflow',
  session_cvd: 'orderflow',
  big_abs: 'orderflow',
  of_alignment: 'orderflow',
  reaction: 'orderflow',
  pattern: 'orderflow',
  hvn_lvn: 'orderflow',
  amt: 'amt',
  amt_dynamics: 'amt',
  setup: 'amt',
  execution: 'amt',
  approach: 'amt',
  macro: 'narrative',
  exchange_stats: 'narrative',
}

const GROUP_META: Record<SuperGroup, { label: string; color: string; gateAffinity: GateName[] }> = {
  zone: { label: 'Zone', color: '#a78bfa', gateAffinity: ['action'] }, // violet
  orderflow: { label: 'Order flow', color: '#f97316', gateAffinity: ['orderflow'] }, // orange
  amt: { label: 'AMT', color: '#34d399', gateAffinity: ['action', 'confidence'] }, // emerald
  narrative: { label: 'Narrative', color: '#60a5fa', gateAffinity: ['confidence'] }, // blue
}

type GateName = 'action' | 'confidence' | 'orderflow' | 'flat' | 'live'

const GATE_LABELS: Record<GateName, string> = {
  action: 'Action',
  confidence: 'Confidence',
  orderflow: 'Order flow',
  flat: 'Flat',
  live: 'Live',
}

function meanAbs(slice: number[]): number {
  if (!slice.length) return 0
  let sum = 0
  let n = 0
  for (const v of slice) {
    if (Number.isFinite(v)) {
      sum += Math.abs(v)
      n++
    }
  }
  return n === 0 ? 0 : Math.min(sum / n, 1)
}

interface SegNode {
  name: string
  title: string
  super: SuperGroup
  hot: number  // 0-1 mean |value|
  size: number
  x: number
  y: number
}

interface GroupNode {
  name: SuperGroup
  label: string
  color: string
  hot: number
  x: number
  y: number
  segments: SegNode[]
}

interface GateNode {
  name: GateName
  label: string
  pass: boolean
  detail: string
  blocker: boolean
  x: number
  y: number
}

const W = 1000
const H = 380
const COL_X = { seg: 100, group: 380, gate: 660, decision: 920 }

export function DecisionFlow({ inference, schema }: Props) {
  const layout = useMemo(() => {
    if (!inference || !schema) return null
    const inputs = inference.inputs ?? []

    const segNodes: SegNode[] = schema.segments.map((seg) => ({
      name: seg.name,
      title: seg.title,
      super: SEG_GROUP[seg.name] ?? 'narrative',
      hot: meanAbs(inputs.slice(seg.start, seg.end)),
      size: seg.size,
      x: COL_X.seg,
      y: 0,  // assigned below
    }))

    // Stack segs by super-group so they cluster vertically
    const groupOrder: SuperGroup[] = ['zone', 'orderflow', 'amt', 'narrative']
    segNodes.sort((a, b) => groupOrder.indexOf(a.super) - groupOrder.indexOf(b.super))
    const segCount = segNodes.length
    const segSpan = H - 40
    segNodes.forEach((n, i) => {
      n.y = 20 + (i / Math.max(segCount - 1, 1)) * segSpan
    })

    const groupNodes: GroupNode[] = groupOrder.map((g, i) => {
      const segs = segNodes.filter((s) => s.super === g)
      const hot = segs.length ? segs.reduce((acc, s) => acc + s.hot, 0) / segs.length : 0
      return {
        name: g,
        label: GROUP_META[g].label,
        color: GROUP_META[g].color,
        hot,
        x: COL_X.group,
        y: 60 + i * ((H - 80) / (groupOrder.length - 1)),
        segments: segs,
      }
    })

    const g = inference.gates
    const gateNodes: GateNode[] = (
      [
        {
          name: 'action' as const,
          pass: g ? g.model_action !== 'SKIP' && g.model_action !== 'skip' : false,
          detail: g?.model_action ?? '—',
          blocker: g?.blocker === 'model_skip',
        },
        {
          name: 'confidence' as const,
          pass: g?.conf_pass ?? false,
          detail: g
            ? `${g.confidence.toFixed(2)} ≥ ${g.conf_floor.toFixed(2)}`
            : '—',
          blocker: g?.blocker === 'confidence',
        },
        {
          name: 'orderflow' as const,
          pass: g?.of_pass ?? false,
          detail: g
            ? `${g.of_score.toFixed(2)} ≥ ${g.of_floor.toFixed(2)}`
            : '—',
          blocker: g?.blocker === 'orderflow',
        },
        {
          name: 'flat' as const,
          pass: g?.is_flat ?? false,
          detail: g?.is_flat ? 'flat' : 'in position',
          blocker: g?.blocker === 'in_position',
        },
        {
          name: 'live' as const,
          pass: g ? !g.halted : true,
          detail: g?.halted ? 'halted' : 'live',
          blocker: g?.blocker === 'halted',
        },
      ] as Array<Omit<GateNode, 'x' | 'y' | 'label'>>
    ).map((gn, i) => ({
      ...gn,
      label: GATE_LABELS[gn.name],
      x: COL_X.gate,
      y: 40 + i * ((H - 80) / 4),
    }))

    return { segNodes, groupNodes, gateNodes }
  }, [inference, schema])

  if (!inference || !schema || !layout) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 px-3 py-8 text-xs font-mono text-center text-zinc-500">
        Waiting for first zone touch…
      </div>
    )
  }

  const { segNodes, groupNodes, gateNodes } = layout
  const gates = inference.gates
  const dispatched = gates?.decision === 'DISPATCHED'
  const action = inference.action ?? '—'

  // Decision node payload
  const conf = inference.confidence ?? 0
  const contP = inference.cont_p ?? 0
  const revP = inference.rev_p ?? 0
  const stopTicks = inference.stop_ticks
    ? Math.round(inference.stop_ticks)
    : null
  const sizeMult = inference.sizing_signal ?? null

  // Approach direction → SHORT vs LONG mapping (mirrors level_monitor)
  const zoneCenter = inference.zone_center ?? null
  const approachUp = zoneCenter !== null ? inference.price < zoneCenter : true
  const isShort =
    (action === 'REVERSAL' && approachUp) ||
    (action === 'CONTINUATION' && !approachUp)
  const direction =
    action === 'SKIP' || action === 'skip' ? 'SKIP' : isShort ? 'SHORT' : 'LONG'
  const dirColor =
    direction === 'LONG'
      ? '#34d399'
      : direction === 'SHORT'
        ? '#f87171'
        : '#fbbf24'

  // Stop price + dollars (NQ futures: $5 per tick per contract)
  const stopPrice =
    stopTicks !== null
      ? isShort
        ? inference.price + stopTicks * 0.25
        : inference.price - stopTicks * 0.25
      : null
  const stopDollars = stopTicks !== null ? stopTicks * 5 : null

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 font-mono text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Decision flow</span>
        <span className="text-zinc-500 text-[10px]">
          {inference.inputs?.length ?? 0} dims · {inference.model_type ?? '?'}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto select-none"
        style={{ maxHeight: 420 }}
      >
        <defs>
          <linearGradient id="flow-pass" x1="0" x2="1">
            <stop offset="0" stopColor="#34d399" stopOpacity="0.05" />
            <stop offset="1" stopColor="#34d399" stopOpacity="0.6" />
          </linearGradient>
          <linearGradient id="flow-fail" x1="0" x2="1">
            <stop offset="0" stopColor="#f87171" stopOpacity="0.05" />
            <stop offset="1" stopColor="#f87171" stopOpacity="0.6" />
          </linearGradient>
          <radialGradient id="dispatch-glow">
            <stop offset="0" stopColor="#10b981" stopOpacity="0.6" />
            <stop offset="1" stopColor="#10b981" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="block-glow">
            <stop offset="0" stopColor="#dc2626" stopOpacity="0.5" />
            <stop offset="1" stopColor="#dc2626" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Column headers */}
        <text x={COL_X.seg} y={14} textAnchor="middle" fill="#71717a" fontSize="10">
          FEATURES (23 segments)
        </text>
        <text x={COL_X.group} y={14} textAnchor="middle" fill="#71717a" fontSize="10">
          SIGNALS
        </text>
        <text x={COL_X.gate} y={14} textAnchor="middle" fill="#71717a" fontSize="10">
          GATES
        </text>
        <text x={COL_X.decision} y={14} textAnchor="middle" fill="#71717a" fontSize="10">
          VERDICT
        </text>

        {/* Segment → group connectors (background layer) */}
        {segNodes.map((s) => {
          const target = groupNodes.find((g) => g.name === s.super)!
          const opacity = 0.05 + s.hot * 0.4
          return (
            <path
              key={`s2g-${s.name}`}
              d={`M${s.x + 6},${s.y} C${(s.x + target.x) / 2},${s.y} ${(s.x + target.x) / 2},${target.y} ${target.x - 18},${target.y}`}
              stroke={target.color}
              strokeWidth={0.6 + s.hot * 1.6}
              fill="none"
              opacity={opacity}
            />
          )
        })}

        {/* Group → gate connectors */}
        {groupNodes.map((gnode) =>
          GROUP_META[gnode.name].gateAffinity.map((gateName) => {
            const gate = gateNodes.find((gt) => gt.name === gateName)
            if (!gate) return null
            const stroke = gate.pass ? 'url(#flow-pass)' : gate.blocker ? 'url(#flow-fail)' : '#3f3f46'
            const sw = 1.2 + gnode.hot * 2.5
            return (
              <path
                key={`g2gate-${gnode.name}-${gateName}`}
                d={`M${gnode.x + 18},${gnode.y} C${(gnode.x + gate.x) / 2},${gnode.y} ${(gnode.x + gate.x) / 2},${gate.y} ${gate.x - 16},${gate.y}`}
                stroke={stroke}
                strokeWidth={sw}
                fill="none"
                opacity={0.7}
              />
            )
          }),
        )}

        {/* Gate → decision connectors */}
        {gateNodes.map((gn) => {
          const stroke = gn.pass ? '#10b981' : gn.blocker ? '#ef4444' : '#52525b'
          const op = gn.blocker ? 0.9 : gn.pass ? 0.55 : 0.18
          return (
            <path
              key={`gate2d-${gn.name}`}
              d={`M${gn.x + 16},${gn.y} C${(gn.x + COL_X.decision) / 2},${gn.y} ${(gn.x + COL_X.decision) / 2},${H / 2} ${COL_X.decision - 50},${H / 2}`}
              stroke={stroke}
              strokeWidth={gn.blocker ? 2.5 : 1.6}
              fill="none"
              opacity={op}
            />
          )
        })}

        {/* Segment nodes */}
        {segNodes.map((s) => {
          const r = 2.2 + s.hot * 4.5
          const color = GROUP_META[s.super].color
          return (
            <g key={`seg-${s.name}`}>
              <circle cx={s.x} cy={s.y} r={r} fill={color} opacity={0.25 + s.hot * 0.7} />
              <title>
                {`${s.title} · ${s.size} dims · mean|v| = ${s.hot.toFixed(3)}`}
              </title>
            </g>
          )
        })}
        {/* Inputs column subtle label */}
        <text x={COL_X.seg - 60} y={H / 2} fill="#52525b" fontSize="9" transform={`rotate(-90 ${COL_X.seg - 60} ${H / 2})`}>
          observation
        </text>

        {/* Group nodes */}
        {groupNodes.map((g) => {
          const r = 18 + g.hot * 18
          return (
            <g key={`group-${g.name}`}>
              <circle
                cx={g.x}
                cy={g.y}
                r={r + 6}
                fill={g.color}
                opacity={0.08}
              />
              <circle
                cx={g.x}
                cy={g.y}
                r={r}
                fill="#18181b"
                stroke={g.color}
                strokeWidth={1.5}
                opacity={0.4 + g.hot * 0.6}
              />
              <text
                x={g.x}
                y={g.y - 2}
                textAnchor="middle"
                fill={g.color}
                fontSize="11"
                fontWeight="600"
              >
                {g.label}
              </text>
              <text
                x={g.x}
                y={g.y + 10}
                textAnchor="middle"
                fill="#a1a1aa"
                fontSize="9"
              >
                {g.hot.toFixed(2)}
              </text>
            </g>
          )
        })}

        {/* Gate nodes */}
        {gateNodes.map((gn) => {
          const stroke = gn.pass ? '#10b981' : gn.blocker ? '#ef4444' : '#52525b'
          const fill = gn.pass ? '#022c22' : gn.blocker ? '#450a0a' : '#18181b'
          return (
            <g key={`gate-${gn.name}`}>
              <rect
                x={gn.x - 60}
                y={gn.y - 16}
                width="120"
                height="32"
                rx="4"
                fill={fill}
                stroke={stroke}
                strokeWidth={gn.blocker ? 2 : 1}
              />
              <text
                x={gn.x - 50}
                y={gn.y - 4}
                fill={gn.pass ? '#34d399' : gn.blocker ? '#f87171' : '#a1a1aa'}
                fontSize="11"
                fontWeight="600"
              >
                {gn.pass ? '✓' : '✗'} {gn.label}
              </text>
              <text x={gn.x - 50} y={gn.y + 9} fill="#a1a1aa" fontSize="9">
                {gn.detail}
              </text>
            </g>
          )
        })}

        {/* Decision node */}
        <g>
          <circle
            cx={COL_X.decision}
            cy={H / 2}
            r="80"
            fill={dispatched ? 'url(#dispatch-glow)' : 'url(#block-glow)'}
          />
          <rect
            x={COL_X.decision - 70}
            y={H / 2 - 60}
            width="140"
            height="120"
            rx="8"
            fill="#0a0a0a"
            stroke={dispatched ? '#10b981' : '#ef4444'}
            strokeWidth="2"
          />
          <text
            x={COL_X.decision}
            y={H / 2 - 38}
            textAnchor="middle"
            fill={dispatched ? '#34d399' : '#f87171'}
            fontSize="13"
            fontWeight="700"
          >
            {dispatched ? 'DISPATCHED' : 'BLOCKED'}
          </text>
          {gates?.blocker && !dispatched && (
            <text
              x={COL_X.decision}
              y={H / 2 - 22}
              textAnchor="middle"
              fill="#fca5a5"
              fontSize="9"
            >
              {gates.blocker.replace('_', ' ')}
            </text>
          )}
          <text
            x={COL_X.decision}
            y={H / 2 - 2}
            textAnchor="middle"
            fill={dirColor}
            fontSize="22"
            fontWeight="800"
          >
            {direction}
          </text>
          <text
            x={COL_X.decision}
            y={H / 2 + 16}
            textAnchor="middle"
            fill="#d4d4d8"
            fontSize="10"
          >
            conf {conf.toFixed(2)} · {action}
          </text>
          <text
            x={COL_X.decision}
            y={H / 2 + 30}
            textAnchor="middle"
            fill="#71717a"
            fontSize="9"
          >
            cont {contP.toFixed(2)} · rev {revP.toFixed(2)}
          </text>
          {stopTicks !== null && stopPrice !== null && (
            <text
              x={COL_X.decision}
              y={H / 2 + 46}
              textAnchor="middle"
              fill="#a1a1aa"
              fontSize="9"
            >
              stop {stopTicks}t · ${stopDollars} @ {stopPrice.toFixed(2)}
            </text>
          )}
          {sizeMult !== null && (
            <text
              x={COL_X.decision}
              y={H / 2 + 56}
              textAnchor="middle"
              fill="#71717a"
              fontSize="9"
            >
              size {sizeMult.toFixed(2)}x
            </text>
          )}
        </g>
      </svg>

      {/* Group strength legend */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
        {groupNodes.map((g) => (
          <div
            key={g.name}
            className="flex items-center gap-2 rounded border border-zinc-800 bg-zinc-950/40 px-2 py-1"
          >
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: g.color }}
            />
            <span className="text-zinc-400">{g.label}</span>
            <span className="ml-auto text-zinc-200 tabular-nums">{g.hot.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
