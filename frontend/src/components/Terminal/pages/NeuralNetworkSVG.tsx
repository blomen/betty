// NeuralNetworkSVG.tsx — accurate Dueling DQN visualization
// Architecture: 139 → 256 (LN+ReLU) → 256 (LN+ReLU) → 128 (LN+ReLU) → 64 (ReLU)
//   → Value(64→32→1) + Advantage(64→32→3) → Q = V + (A - mean(A))
import { useMemo, useRef, useEffect, useState, useCallback } from 'react';
import {
  DQN_INPUTS, DQN_SEGMENTS, HIDDEN_LAYERS, ACTION_NAMES, ACTION_COLORS,
} from './dqnConfig';
import type { DQNInferenceEvent } from '@/types/market';

interface Props {
  dqnInference: DQNInferenceEvent | null;
}

// Layout — 6 columns: input, L1, L2, L3, L4, output
const W = 960;
const H = 420;
const INPUT_X = 80;
const L1_X = 200;
const L2_X = 340;
const L3_X = 480;
const L4_X = 600;
const OUTPUT_X = 760;
const LAYER_XS = [L1_X, L2_X, L3_X, L4_X];

// How many dots to show per hidden layer (sampled from full width)
const DOTS_PER_LAYER = [16, 16, 12, 8];

// Layer colors matching architecture
const LAYER_COLORS = ['#06b6d4', '#0891b2', '#8b5cf6', '#a78bfa'];

const FIRE_THRESHOLD = 0.5;

interface FiringNeuron {
  id: string;
  cx: number;
  cy: number;
  color: string;
  intensity: number;
  ts: number;
}

export function NeuralNetworkSVG({ dqnInference }: Props) {
  const [firingNeurons, setFiringNeurons] = useState<FiringNeuron[]>([]);
  const prevInferenceRef = useRef<DQNInferenceEvent | null>(null);
  const animFrameRef = useRef<number>(0);

  // ── Segment-level input positions (9 nodes) ──
  const segmentNodes = useMemo(() => {
    const count = DQN_SEGMENTS.length;
    const spacing = (H - 60) / (count - 1);
    return DQN_SEGMENTS.map((seg, i) => ({
      ...seg,
      x: INPUT_X,
      y: 30 + i * spacing,
    }));
  }, []);

  // ── Segment activations (average absolute input per segment) ──
  const segmentActivations = useMemo(() => {
    return DQN_SEGMENTS.map(seg => {
      if (!dqnInference) return 0;
      let sum = 0;
      let count = 0;
      for (let i = seg.start; i < seg.end; i++) {
        sum += Math.abs(dqnInference.inputs[i] ?? 0);
        count++;
      }
      return count > 0 ? Math.min(1, sum / count) : 0;
    });
  }, [dqnInference]);

  // ── Hidden layer dot positions ──
  const hiddenDots = useMemo(() => {
    return HIDDEN_LAYERS.map((size, li) => {
      const n = DOTS_PER_LAYER[li];
      const step = Math.max(1, Math.floor(size / n));
      const dots: { srcIdx: number; y: number }[] = [];
      for (let i = 0; i < size && dots.length < n; i += step) {
        dots.push({ srcIdx: i, y: 0 });
      }
      const spacing = (H - 60) / Math.max(dots.length - 1, 1);
      dots.forEach((d, j) => { d.y = 30 + j * spacing; });
      return dots;
    });
  }, []);

  // ── Hidden activations ──
  const hiddenActivations = useMemo(() => {
    return HIDDEN_LAYERS.map((_, li) => {
      const key = (['layer1', 'layer2', 'layer3', 'layer4'] as const)[li];
      const acts = dqnInference?.activations[key] ?? [];
      return hiddenDots[li].map(d => Math.min(1, Math.abs(acts[d.srcIdx] ?? 0)));
    });
  }, [dqnInference, hiddenDots]);

  // ── Output positions ──
  const outputNodes = useMemo(() => {
    const count = ACTION_NAMES.length;
    const spacing = 90;
    const center = H / 2;
    return ACTION_NAMES.map((name, i) => ({
      name,
      color: ACTION_COLORS[i],
      x: OUTPUT_X,
      y: center + (i - (count - 1) / 2) * spacing,
    }));
  }, []);

  const winnerIdx = useMemo(() => {
    if (!dqnInference) return -1;
    const qv = dqnInference.q_values;
    return qv.indexOf(Math.max(...qv));
  }, [dqnInference]);

  // ── Connection paths ──
  const connections = useMemo(() => {
    if (!dqnInference) return [];
    const lines: { d: string; color: string; width: number; opacity: number }[] = [];

    const curve = (x1: number, y1: number, x2: number, y2: number) => {
      const mx = (x1 + x2) / 2;
      return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    };

    // Segment → L1
    const segConns = new Map<string, { strength: number; sign: number }>();
    for (const c of dqnInference.connections.input_l1) {
      const segIdx = DQN_SEGMENTS.findIndex(s => c.from_idx >= s.start && c.from_idx < s.end);
      if (segIdx === -1) continue;
      const dotIdx = hiddenDots[0].findIndex(d => d.srcIdx === c.to_idx);
      if (dotIdx === -1) continue;
      const key = `${segIdx}-${dotIdx}`;
      const existing = segConns.get(key);
      if (!existing || c.strength > existing.strength) {
        segConns.set(key, { strength: c.strength, sign: c.sign });
      }
    }
    for (const [key, val] of segConns) {
      const [si, di] = key.split('-').map(Number);
      const sn = segmentNodes[si];
      const dn = hiddenDots[0][di];
      if (!sn || !dn) continue;
      lines.push({
        d: curve(sn.x + 6, sn.y, LAYER_XS[0] - 5, dn.y),
        color: val.sign === 1 ? sn.color : '#ef4444',
        width: Math.max(0.5, val.strength * 2.5),
        opacity: Math.max(0.04, Math.min(0.6, val.strength)),
      });
    }

    // Hidden → hidden: L1→L2, L2→L3, L3→L4
    const layerPairs = [
      { conns: dqnInference.connections.l1_l2, fromLi: 0, toLi: 1 },
      { conns: dqnInference.connections.l2_l3, fromLi: 1, toLi: 2 },
      { conns: dqnInference.connections.l3_l4 ?? dqnInference.connections.l3_output ?? [], fromLi: 2, toLi: 3 },
    ];
    for (const { conns, fromLi, toLi } of layerPairs) {
      for (const c of conns) {
        const fi = hiddenDots[fromLi].findIndex(d => d.srcIdx === c.from_idx);
        const ti = hiddenDots[toLi].findIndex(d => d.srcIdx === c.to_idx);
        if (fi === -1 || ti === -1) continue;
        lines.push({
          d: curve(LAYER_XS[fromLi] + 5, hiddenDots[fromLi][fi].y,
                   LAYER_XS[toLi] - 5, hiddenDots[toLi][ti].y),
          color: c.sign === 1 ? LAYER_COLORS[toLi] : '#ef4444',
          width: Math.max(0.5, c.strength * 2.5),
          opacity: Math.max(0.04, Math.min(0.6, c.strength)),
        });
      }
    }

    // L4 (features) → output Q-values (through dueling heads)
    const l4Conns = dqnInference.connections.l4_output ?? [];
    for (const c of l4Conns) {
      const fi = hiddenDots[3].findIndex(d => d.srcIdx === c.from_idx);
      const out = outputNodes[c.to_idx];
      if (fi === -1 || !out) continue;
      lines.push({
        d: curve(LAYER_XS[3] + 5, hiddenDots[3][fi].y, out.x - 14, out.y),
        color: c.sign === 1 ? out.color : '#ef4444',
        width: Math.max(0.5, c.strength * 2.5),
        opacity: Math.max(0.04, Math.min(0.6, c.strength)),
      });
    }

    return lines;
  }, [dqnInference, segmentNodes, hiddenDots, outputNodes]);

  // ── Firing neuron detection ──
  const spawnFirings = useCallback(() => {
    if (!dqnInference || dqnInference === prevInferenceRef.current) return;
    prevInferenceRef.current = dqnInference;

    const now = Date.now();
    const newFires: FiringNeuron[] = [];

    // Input segments firing
    segmentActivations.forEach((act, i) => {
      if (act > FIRE_THRESHOLD) {
        const sn = segmentNodes[i];
        newFires.push({
          id: `seg-${i}-${now}`, cx: sn.x, cy: sn.y,
          color: sn.color, intensity: act, ts: now,
        });
      }
    });

    // Hidden layers firing
    hiddenActivations.forEach((acts, li) => {
      acts.forEach((act, di) => {
        if (act > FIRE_THRESHOLD) {
          const dot = hiddenDots[li][di];
          newFires.push({
            id: `h${li}-${di}-${now}`, cx: LAYER_XS[li], cy: dot.y,
            color: LAYER_COLORS[li], intensity: act, ts: now,
          });
        }
      });
    });

    // Output firing
    if (winnerIdx >= 0) {
      const out = outputNodes[winnerIdx];
      newFires.push({
        id: `out-${winnerIdx}-${now}`, cx: out.x, cy: out.y,
        color: out.color, intensity: 1, ts: now,
      });
    }

    setFiringNeurons(prev => [...prev, ...newFires]);
  }, [dqnInference, segmentActivations, hiddenActivations, segmentNodes, hiddenDots, outputNodes, winnerIdx]);

  useEffect(() => { spawnFirings(); }, [spawnFirings]);

  // Cleanup expired firing animations (800ms lifetime)
  useEffect(() => {
    const tick = () => {
      const now = Date.now();
      setFiringNeurons(prev => {
        const filtered = prev.filter(f => now - f.ts < 800);
        return filtered.length !== prev.length ? filtered : prev;
      });
      animFrameRef.current = requestAnimationFrame(tick);
    };
    animFrameRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, []);

  const statusLabel = useMemo(() => {
    if (!dqnInference) return 'WAITING FOR LEVEL';
    if (dqnInference.trigger === 'approaching') return `APPROACHING ${dqnInference.level}`;
    return `AT LEVEL ${dqnInference.level}`;
  }, [dqnInference]);

  // Dueling formula midpoint for visualization bracket
  const duelingMidX = (L4_X + OUTPUT_X) / 2;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-full"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <filter id="nn-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
        <filter id="nn-pulse" x="-200%" y="-200%" width="500%" height="500%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>

      <style>{`
        @keyframes nn-fire {
          0% { r: 4; opacity: 0.8; }
          100% { r: 18; opacity: 0; }
        }
        @keyframes nn-flow {
          0% { stroke-dashoffset: 16; }
          100% { stroke-dashoffset: 0; }
        }
        .nn-firing { animation: nn-fire 0.8s ease-out forwards; }
        .nn-conn-active {
          stroke-dasharray: 8 8;
          animation: nn-flow 0.6s linear infinite;
        }
        .nn-node { transition: opacity 0.15s ease, r 0.15s ease; }
        .nn-conn { transition: opacity 0.2s ease, stroke-width 0.2s ease; }
      `}</style>

      {/* ── Connections ── */}
      {connections.map((c, i) => (
        <path
          key={i}
          d={c.d}
          fill="none"
          stroke={c.color}
          strokeWidth={c.width}
          opacity={c.opacity}
          className={`nn-conn ${c.opacity > 0.25 ? 'nn-conn-active' : ''}`}
        />
      ))}

      {/* ── Firing pulse rings ── */}
      {firingNeurons.map(f => {
        const age = (Date.now() - f.ts) / 800;
        const r = 4 + age * 16;
        const opacity = (1 - age) * f.intensity * 0.6;
        return (
          <circle
            key={f.id}
            cx={f.cx} cy={f.cy} r={r}
            fill="none"
            stroke={f.color}
            strokeWidth={1.5}
            opacity={Math.max(0, opacity)}
            filter="url(#nn-pulse)"
          />
        );
      })}

      {/* ── Input: segment nodes ── */}
      {segmentNodes.map((seg, i) => {
        const act = segmentActivations[i];
        const r = 4 + act * 3;
        const bright = Math.max(0.2, act);
        return (
          <g key={seg.name}>
            {act > FIRE_THRESHOLD && (
              <circle cx={seg.x} cy={seg.y} r={r + 4}
                fill={seg.color} opacity={act * 0.25} filter="url(#nn-glow)" />
            )}
            <circle
              cx={seg.x} cy={seg.y} r={r}
              fill={seg.color} opacity={bright}
              className="nn-node"
            />
            <text
              x={seg.x - 10} y={seg.y + 3}
              fill={bright > 0.4 ? seg.color : '#444'}
              fontSize="7" fontFamily="monospace" fontWeight="bold"
              textAnchor="end" opacity={Math.max(0.4, bright)}
            >
              {seg.name}
            </text>
          </g>
        );
      })}

      {/* ── Hidden layers ── */}
      {HIDDEN_LAYERS.map((size, li) => {
        const lx = LAYER_XS[li];
        const color = LAYER_COLORS[li];
        const dots = hiddenDots[li];
        const acts = hiddenActivations[li];
        // Show LayerNorm indicator for first 3 layers (they use LN+ReLU)
        const hasLN = li < 3;
        return (
          <g key={`layer-${li}`}>
            {/* Layer label */}
            <text
              x={lx} y={14}
              fill={color} fontSize="8" fontFamily="monospace"
              textAnchor="middle" fontWeight="bold" opacity={0.7}
            >
              {size}
            </text>
            {hasLN && (
              <text
                x={lx} y={H - 12}
                fill={color} fontSize="6" fontFamily="monospace"
                textAnchor="middle" opacity={0.3}
              >
                LN+ReLU
              </text>
            )}
            {!hasLN && (
              <text
                x={lx} y={H - 12}
                fill={color} fontSize="6" fontFamily="monospace"
                textAnchor="middle" opacity={0.3}
              >
                ReLU
              </text>
            )}
            {/* Dots */}
            {dots.map((dot, di) => {
              const act = acts[di] ?? 0;
              const r = 2.5 + act * 2;
              const bright = Math.max(0.1, act);
              return (
                <g key={di}>
                  {act > FIRE_THRESHOLD && (
                    <circle cx={lx} cy={dot.y} r={r + 3}
                      fill={color} opacity={act * 0.2} filter="url(#nn-glow)" />
                  )}
                  <circle
                    cx={lx} cy={dot.y} r={r}
                    fill={color} opacity={bright}
                    className="nn-node"
                  />
                </g>
              );
            })}
          </g>
        );
      })}

      {/* ── Dueling architecture annotation ── */}
      <text
        x={duelingMidX} y={outputNodes[0].y - 50}
        fill="#555" fontSize="7" fontFamily="monospace" textAnchor="middle"
      >
        Q-VALUES
      </text>
      {/* V+A bracket */}
      <text
        x={duelingMidX} y={outputNodes[outputNodes.length - 1].y + 30}
        fill="#444" fontSize="6" fontFamily="monospace" textAnchor="middle"
        opacity={0.5}
      >
        V + (A - mean(A))
      </text>

      {/* ── Output Q-value nodes ── */}
      {outputNodes.map((o, i) => {
        const qVal = dqnInference?.q_values[i] ?? 0;
        const isWinner = i === winnerIdx;
        const bright = isWinner ? 1.0 : 0.2;
        const r = isWinner ? 14 : 10;
        return (
          <g key={o.name}>
            {isWinner && (
              <circle cx={o.x} cy={o.y} r={r + 6}
                fill={o.color} opacity={0.15} filter="url(#nn-glow)" />
            )}
            <circle
              cx={o.x} cy={o.y} r={r}
              fill={o.color} opacity={bright}
              className="nn-node"
              filter={isWinner ? 'url(#nn-glow)' : undefined}
            />
            <text
              x={o.x + r + 8} y={o.y - 3}
              fill={o.color} fontSize="10" fontFamily="monospace" fontWeight="bold"
              opacity={bright}
            >
              {o.name}
            </text>
            <text
              x={o.x + r + 8} y={o.y + 10}
              fill={bright > 0.4 ? o.color : '#444'}
              fontSize="9" fontFamily="monospace"
            >
              {qVal.toFixed(3)}
            </text>
          </g>
        );
      })}

      {/* ── Status ── */}
      <text
        x={8} y={H - 8}
        fill="#444" fontSize="8" fontFamily="monospace"
      >
        {statusLabel}
      </text>
      <text
        x={W / 2} y={H - 8}
        fill="#333" fontSize="7" fontFamily="monospace" textAnchor="middle"
      >
        Dueling DQN: {DQN_INPUTS.length} → 256 → 256 → 128 → 64 → V+A → Q(3)
      </text>
    </svg>
  );
}
