import { GaugeBar } from './GaugeBar';
import type { GaugeBarProps } from './GaugeBar';
import type { BattleScreenData, OrderflowSnapshot } from '@/types/market';

interface Props {
  data: BattleScreenData;
  onTrade: (direction: 'long' | 'short', entry: number, stop: number, targets: { name: string; price: number }[]) => void;
  onDismiss: () => void;
}

function orderflowToGauges(of: OrderflowSnapshot): GaugeBarProps[] {
  const { long: l } = of;
  const deltaVal = l.delta ?? 0;
  const deltaDir = deltaVal > 0 ? 'green' : deltaVal < 0 ? 'red' : 'dim';

  return [
    {
      label: 'DELTA', fill: Math.min(1, Math.abs(deltaVal) / 5000),
      value: deltaVal > 0 ? `+${deltaVal}` : `${deltaVal}`,
      assessment: deltaVal > 200 ? 'BULLISH' : deltaVal < -200 ? 'BEARISH' : 'FLAT',
      color: deltaDir as any,
    },
    {
      label: 'CVD', fill: l.cvd_trend === 'rising' ? 0.8 : l.cvd_trend === 'falling' ? 0.8 : 0.3,
      value: l.cvd_trend === 'rising' ? '↑↑' : l.cvd_trend === 'falling' ? '↓↓' : '--',
      assessment: l.cvd_trend === 'rising' ? 'STRONG' : l.cvd_trend === 'falling' ? 'STRONG' : 'FLAT',
      color: l.cvd_trend === 'rising' ? 'green' : l.cvd_trend === 'falling' ? 'red' : 'dim',
    },
    {
      label: 'ABSORB', fill: l.vsa_absorption ? 1.0 : 0.0,
      value: l.vsa_absorption ? 'YES' : '--',
      assessment: l.vsa_absorption ? 'HIGH' : 'NONE',
      color: l.vsa_absorption ? 'amber' : 'dim',
    },
    {
      label: 'IMBAL', fill: Math.min(1, (l.stacked_imbalance_count ?? 0) / 5),
      value: l.stacked_imbalance_count ? `${l.stacked_imbalance_direction} x${l.stacked_imbalance_count}` : '--',
      assessment: (l.stacked_imbalance_count ?? 0) >= 3 ? 'STACKING' : l.stacked_imbalance_count ? 'BUILDING' : 'NONE',
      color: l.stacked_imbalance_direction === 'buy' ? 'green' : l.stacked_imbalance_direction === 'sell' ? 'red' : 'dim',
    },
    {
      label: 'BIG', fill: Math.min(1, (l.big_trades_count ?? 0) / 10),
      value: l.big_trades_count ? `${l.big_trades_count}` : '--',
      assessment: (l.big_trades_net_delta ?? 0) > 0 ? 'BUY SIDE' : (l.big_trades_net_delta ?? 0) < 0 ? 'SELL SIDE' : 'NONE',
      color: (l.big_trades_net_delta ?? 0) > 0 ? 'green' : (l.big_trades_net_delta ?? 0) < 0 ? 'red' : 'dim',
    },
    {
      label: 'TRAPPED', fill: l.trapped_traders ? 0.9 : 0.0,
      value: l.trapped_traders ? 'YES' : '--',
      assessment: l.trapped_traders ? 'DETECTED' : 'NONE',
      color: l.trapped_traders ? 'amber' : 'dim',
    },
    {
      label: 'STOP RUN', fill: l.stop_run_detected ? 0.9 : 0.0,
      value: l.stop_run_detected ? 'YES' : '--',
      assessment: l.stop_run_detected ? 'DETECTED' : 'NONE',
      color: l.stop_run_detected ? 'amber' : 'dim',
    },
    {
      label: 'PA RATIO', fill: Math.min(1, (l.passive_active_ratio ?? 0) / 4),
      value: l.passive_active_ratio?.toFixed(1) ?? '--',
      assessment: (l.passive_active_ratio ?? 0) > 2 ? 'PASSIVE' : (l.passive_active_ratio ?? 0) > 1 ? 'BALANCED' : 'ACTIVE',
      color: (l.passive_active_ratio ?? 0) > 2 ? 'amber' : 'dim',
    },
  ];
}

function structureToGauges(session: any): GaugeBarProps[] {
  if (!session) return [];
  return [
    {
      label: 'MKT TYPE', fill: session.market_type === 'trending_up' || session.market_type === 'trending_down' ? 0.9 : 0.4,
      value: session.market_type || '--',
      assessment: session.market_type?.includes('trending') ? 'TRENDING' : 'BALANCED',
      color: session.market_type?.includes('trending') ? 'green' : 'amber',
    },
    {
      label: 'OPEN', fill: session.opening_type === 'OD' ? 0.9 : 0.5,
      value: session.opening_type || '--',
      assessment: session.opening_type || 'UNKNOWN',
      color: session.opening_type === 'OD' ? 'green' : session.opening_type === 'ORR' ? 'red' : 'amber',
    },
    {
      label: 'DISTRIB', fill: session.distribution_type === 'double' ? 0.9 : 0.5,
      value: session.distribution_type || '--',
      assessment: (session.distribution_type || 'normal').toUpperCase(),
      color: session.distribution_type === 'p_shape' ? 'green' : session.distribution_type === 'b_shape' ? 'red' : 'amber',
    },
    {
      label: 'POOR H/L',
      fill: (session.poor_high || session.poor_low) ? 0.9 : 0.0,
      value: [session.poor_high && 'H', session.poor_low && 'L'].filter(Boolean).join('+') || '--',
      assessment: (session.poor_high || session.poor_low) ? 'UNFINISHED' : 'CLEAN',
      color: (session.poor_high || session.poor_low) ? 'amber' : 'dim',
    },
    {
      label: 'SWING', fill: 0.5,
      value: session.swing_structure || '--',
      assessment: session.swing_structure?.includes('up') ? 'HH/HL' : session.swing_structure?.includes('down') ? 'LH/LL' : 'RANGE',
      color: session.swing_structure?.includes('up') ? 'green' : session.swing_structure?.includes('down') ? 'red' : 'amber',
    },
    {
      label: 'SINGLES', fill: Math.min(1, (session.single_prints?.length || 0) / 5),
      value: `${session.single_prints?.length || 0}`,
      assessment: (session.single_prints?.length || 0) > 2 ? 'INITIATIVE' : 'FEW',
      color: (session.single_prints?.length || 0) > 2 ? 'amber' : 'dim',
    },
  ];
}

function mlToGauges(ml: BattleScreenData['ml'], macro: BattleScreenData['macro'], confluence: string[]): GaugeBarProps[] {
  return [
    {
      label: 'DAY TYPE',
      fill: (ml?.day_type_confidence || 0) / 100,
      value: ml?.day_type || '...',
      assessment: ml?.day_type_confidence ? `${ml.day_type_confidence}%` : 'LOADING',
      color: ml?.day_type ? 'amber' : 'dim',
    },
    {
      label: 'VIX',
      fill: Math.min(1, (macro?.vix || 20) / 40),
      value: macro?.vix?.toFixed(1) || '--',
      assessment: (macro?.vix || 20) < 18 ? 'LOW' : (macro?.vix || 20) > 25 ? 'HIGH' : 'NORMAL',
      color: (macro?.vix || 20) < 18 ? 'green' : (macro?.vix || 20) > 25 ? 'red' : 'amber',
    },
    {
      label: 'REGIME',
      fill: 0.5,
      value: macro?.regime || '--',
      assessment: (macro?.regime || 'neutral').toUpperCase(),
      color: macro?.regime === 'risk_on' ? 'green' : macro?.regime === 'risk_off' ? 'red' : 'amber',
    },
    {
      label: 'CONFLNC',
      fill: Math.min(1, confluence.length / 4),
      value: `${confluence.length + 1}`,
      assessment: confluence.length >= 2 ? 'STRONG' : confluence.length === 1 ? 'MODERATE' : 'SINGLE',
      color: confluence.length >= 2 ? 'green' : 'amber',
    },
  ];
}

export function BattleScreen({ data, onTrade, onDismiss }: Props) {
  const ofGauges = data.orderflow ? orderflowToGauges(data.orderflow) : [];
  const structGauges = structureToGauges(data.structure);
  const mlGauges = mlToGauges(data.ml, data.macro, data.confluence);

  return (
    <div className="border border-cyan-800 bg-zinc-900/80 rounded p-3 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-cyan-400 font-bold text-sm">⚔ BATTLE</span>
          <span className="text-white font-mono text-sm">{data.level}</span>
          <span className="text-zinc-400 text-xs">@ {data.level_price.toLocaleString()}</span>
          {data.confluence.length > 0 && (
            <span className="text-amber-400 text-xs">+{data.confluence.length} confluence</span>
          )}
        </div>
        <button onClick={onDismiss} className="text-zinc-500 hover:text-zinc-300 text-xs px-2 py-0.5 border border-zinc-700 rounded">
          DISMISS
        </button>
      </div>

      {/* Row 1: Orderflow */}
      {ofGauges.length > 0 && (
        <div>
          <div className="text-zinc-500 text-[10px] mb-1 uppercase tracking-wider">Orderflow</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {ofGauges.map(g => <GaugeBar key={g.label} {...g} />)}
          </div>
        </div>
      )}

      {/* Row 2: Structure */}
      {structGauges.length > 0 && (
        <div>
          <div className="text-zinc-500 text-[10px] mb-1 uppercase tracking-wider">Structure</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {structGauges.map(g => <GaugeBar key={g.label} {...g} />)}
          </div>
        </div>
      )}

      {/* Row 3: ML & Context */}
      <div>
        <div className="text-zinc-500 text-[10px] mb-1 uppercase tracking-wider">ML & Context</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          {mlGauges.map(g => <GaugeBar key={g.label} {...g} />)}
        </div>
      </div>

      {/* Trade Action Bar */}
      <div className="border-t border-zinc-700 pt-2 flex items-center justify-between gap-4 text-xs font-mono">
        <div className="flex gap-4 text-zinc-400">
          <span>ENTRY: <span className="text-white">{data.suggested_entry.toLocaleString()}</span></span>
          <span>STOP: <span className="text-red-400">{data.suggested_stop.toLocaleString()}</span></span>
          {data.targets.map((t, i) => (
            <span key={i}>T{i + 1}: <span className="text-emerald-400">{t.price.toLocaleString()}</span> <span className="text-zinc-500">({t.name})</span></span>
          ))}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => onTrade('long', data.suggested_entry, data.suggested_stop, data.targets)}
            className="px-3 py-1 bg-emerald-800 hover:bg-emerald-700 text-emerald-200 rounded text-xs font-bold"
          >
            TRADE LONG
          </button>
          <button
            onClick={() => onTrade('short', data.suggested_entry, data.suggested_stop, data.targets)}
            className="px-3 py-1 bg-red-800 hover:bg-red-700 text-red-200 rounded text-xs font-bold"
          >
            TRADE SHORT
          </button>
        </div>
      </div>
    </div>
  );
}
