import type { GaugeBarProps } from './GaugeBar';
import type { BattleScreenData, OrderflowSnapshot } from '@/types/market';

export function orderflowToGauges(of: OrderflowSnapshot): GaugeBarProps[] {
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

export function structureToGauges(session: any): GaugeBarProps[] {
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

export function mlToGauges(ml: BattleScreenData['ml'], macro: BattleScreenData['macro'], confluence: string[]): GaugeBarProps[] {
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

// ============================================================
// Feature-dict transformers (from ml_features SSE snapshot)
// ============================================================

function fmt(v: any, decimals = 1): string {
  if (v == null) return '--';
  if (typeof v === 'boolean') return v ? 'YES' : 'NO';
  if (typeof v === 'number') return v.toFixed(decimals);
  return String(v);
}

function cap(v: number): number {
  return Math.min(1, Math.max(0, v));
}

/** Orderflow gauges built from feature dict (used when no OrderflowSnapshot available) */
export function featureOrderflowToGauges(f: Record<string, any>): GaugeBarProps[] {
  const delta = f.delta ?? null;
  const cvd = f.cvd ?? null;
  const absorption = f.vsa_absorption ?? false;
  const imbalCount = f.stacked_imbalance_count ?? 0;
  const imbalDir = f.stacked_imbalance_direction ?? 'neutral';
  const bigCount = f.big_trades_count ?? 0;
  const bigNetDelta = f.big_trades_net_delta ?? 0;
  const trapped = f.trapped_traders ?? false;
  const stopRun = f.stop_run_detected ?? false;
  const paRatio = f.passive_active_ratio ?? null;

  return [
    {
      label: 'DELTA',
      fill: delta != null ? cap(Math.abs(delta) / 5000) : 0,
      value: delta != null ? (delta > 0 ? `+${delta}` : `${delta}`) : '--',
      assessment: delta == null ? 'N/A' : delta > 200 ? 'BULLISH' : delta < -200 ? 'BEARISH' : 'FLAT',
      color: delta == null ? 'dim' : delta > 0 ? 'green' : delta < 0 ? 'red' : 'dim',
    },
    {
      label: 'CVD',
      fill: cvd != null ? cap(Math.abs(cvd) / 10000) : 0,
      value: cvd != null ? (cvd > 0 ? `+${Math.round(cvd)}` : `${Math.round(cvd)}`) : '--',
      assessment: cvd == null ? 'N/A' : cvd > 500 ? 'RISING' : cvd < -500 ? 'FALLING' : 'FLAT',
      color: cvd == null ? 'dim' : cvd > 0 ? 'green' : cvd < 0 ? 'red' : 'dim',
    },
    {
      label: 'ABSORB',
      fill: absorption ? 1.0 : 0.0,
      value: absorption ? 'YES' : '--',
      assessment: absorption ? 'HIGH' : 'NONE',
      color: absorption ? 'amber' : 'dim',
    },
    {
      label: 'IMBAL',
      fill: cap(imbalCount / 5),
      value: imbalCount ? `${imbalDir} x${imbalCount}` : '--',
      assessment: imbalCount >= 3 ? 'STACKING' : imbalCount ? 'BUILDING' : 'NONE',
      color: imbalDir === 'buy' ? 'green' : imbalDir === 'sell' ? 'red' : 'dim',
    },
    {
      label: 'BIG',
      fill: cap(bigCount / 10),
      value: bigCount ? `${bigCount}` : '--',
      assessment: bigNetDelta > 0 ? 'BUY SIDE' : bigNetDelta < 0 ? 'SELL SIDE' : 'NONE',
      color: bigNetDelta > 0 ? 'green' : bigNetDelta < 0 ? 'red' : 'dim',
    },
    {
      label: 'TRAPPED',
      fill: trapped ? 0.9 : 0.0,
      value: trapped ? 'YES' : '--',
      assessment: trapped ? 'DETECTED' : 'NONE',
      color: trapped ? 'amber' : 'dim',
    },
    {
      label: 'STOP RUN',
      fill: stopRun ? 0.9 : 0.0,
      value: stopRun ? 'YES' : '--',
      assessment: stopRun ? 'DETECTED' : 'NONE',
      color: stopRun ? 'amber' : 'dim',
    },
    {
      label: 'PA RATIO',
      fill: paRatio != null ? cap(paRatio / 4) : 0,
      value: paRatio != null ? paRatio.toFixed(1) : '--',
      assessment: paRatio == null ? 'N/A' : paRatio > 2 ? 'PASSIVE' : paRatio > 1 ? 'BALANCED' : 'ACTIVE',
      color: paRatio != null && paRatio > 2 ? 'amber' : 'dim',
    },
  ];
}

/** Temporal/momentum gauges — 9 bars */
export function featureTemporalToGauges(f: Record<string, any>): GaugeBarProps[] {
  const slp5 = f.delta_slope_5m ?? null;
  const slp10 = f.delta_slope_10m ?? null;
  const cvdAccel = f.cvd_acceleration ?? null;
  const volRoc = f.volume_roc_5m ?? null;
  const tickRoc = f.tick_roc_5m ?? null;
  const spreadComp = f.spread_compression ?? null;
  const pxVel = f.price_velocity ?? null;
  const absorptionBuilding = f.absorption_building ?? 0;
  const imbalTrend = f.imbalance_trend ?? null;

  return [
    {
      label: 'Δ SLP 5M',
      fill: slp5 != null ? cap(Math.abs(slp5) / 50) : 0,
      value: fmt(slp5),
      assessment: slp5 == null ? 'N/A' : Math.abs(slp5) < 2 ? 'FLAT' : slp5 > 0 ? 'ACCEL' : 'DECEL',
      color: slp5 == null ? 'dim' : slp5 > 0 ? 'green' : 'red',
    },
    {
      label: 'Δ SLP 10M',
      fill: slp10 != null ? cap(Math.abs(slp10) / 50) : 0,
      value: fmt(slp10),
      assessment: slp10 == null ? 'N/A' : Math.abs(slp10) < 2 ? 'FLAT' : slp10 > 0 ? 'ACCEL' : 'DECEL',
      color: slp10 == null ? 'dim' : slp10 > 0 ? 'green' : 'red',
    },
    {
      label: 'CVD ACCEL',
      fill: cvdAccel != null ? cap(Math.abs(cvdAccel) / 2000) : 0,
      value: fmt(cvdAccel, 0),
      assessment: cvdAccel == null ? 'N/A' : Math.abs(cvdAccel) < 100 ? 'FLAT' : cvdAccel > 0 ? 'RISING' : 'FALLING',
      color: cvdAccel == null ? 'dim' : cvdAccel > 0 ? 'green' : 'red',
    },
    {
      label: 'VOL ROC',
      fill: volRoc != null ? cap((volRoc - 0.5) / 1.5) : 0,
      value: fmt(volRoc),
      assessment: volRoc == null ? 'N/A' : volRoc > 1.5 ? 'SURGING' : volRoc > 1.1 ? 'RISING' : volRoc < 0.8 ? 'FADING' : 'NORMAL',
      color: volRoc == null ? 'dim' : volRoc > 1.1 ? 'green' : volRoc < 0.8 ? 'red' : 'amber',
    },
    {
      label: 'TICK ROC',
      fill: tickRoc != null ? cap((tickRoc - 0.5) / 1.5) : 0,
      value: fmt(tickRoc),
      assessment: tickRoc == null ? 'N/A' : tickRoc > 1.5 ? 'SURGING' : tickRoc > 1.1 ? 'RISING' : tickRoc < 0.8 ? 'FADING' : 'NORMAL',
      color: tickRoc == null ? 'dim' : tickRoc > 1.1 ? 'green' : tickRoc < 0.8 ? 'red' : 'amber',
    },
    {
      label: 'SPREAD',
      fill: spreadComp != null ? cap(1 - spreadComp) : 0,
      value: fmt(spreadComp),
      assessment: spreadComp == null ? 'N/A' : spreadComp < 0.7 ? 'TIGHT' : spreadComp > 1.3 ? 'WIDE' : 'NORMAL',
      color: spreadComp == null ? 'dim' : spreadComp < 0.7 ? 'green' : spreadComp > 1.3 ? 'red' : 'amber',
    },
    {
      label: 'PX VEL',
      fill: pxVel != null ? cap(Math.abs(pxVel) / 5) : 0,
      value: fmt(pxVel),
      assessment: pxVel == null ? 'N/A' : Math.abs(pxVel) > 2 ? 'FAST' : 'SLOW',
      color: pxVel == null ? 'dim' : Math.abs(pxVel) > 2 ? 'amber' : 'dim',
    },
    {
      label: 'ABSORB CT',
      fill: cap((absorptionBuilding || 0) / 10),
      value: fmt(absorptionBuilding, 0),
      assessment: (absorptionBuilding || 0) >= 4 ? 'HIGH' : (absorptionBuilding || 0) >= 2 ? 'MODERATE' : 'LOW',
      color: (absorptionBuilding || 0) >= 2 ? 'amber' : 'dim',
    },
    {
      label: 'IMBAL Δ',
      fill: imbalTrend != null ? cap(Math.abs(imbalTrend) / 5) : 0,
      value: fmt(imbalTrend),
      assessment: imbalTrend == null ? 'N/A' : Math.abs(imbalTrend) < 0.5 ? 'STABLE' : imbalTrend > 0 ? 'BUILDING' : 'FADING',
      color: imbalTrend == null ? 'dim' : imbalTrend > 0 ? 'green' : imbalTrend < 0 ? 'red' : 'dim',
    },
  ];
}

/** Session/market-profile gauges — 15 bars */
export function featureSessionToGauges(f: Record<string, any>): GaugeBarProps[] {
  const mktType = f.market_type ?? null;
  const openType = f.opening_type ?? null;
  const ibRange = f.ib_range ?? null;
  const ibVsAspr = f.ib_range_vs_aspr ?? null;
  const asprPct = f.aspr_percentile ?? null;
  const rotFactor = f.rotation_factor ?? null;
  const valMigr = f.value_migration ?? null;
  const vsVah = f.distance_from_vah ?? null;
  const vsVal = f.distance_from_val ?? null;
  const vsPoc = f.distance_from_poc ?? null;
  const inVa = f.price_in_va ?? null;
  const elapsed = f.session_elapsed_pct ?? null;
  const minOpen = f.minutes_since_open ?? null;
  const devPoc = f.developing_poc_direction ?? null;
  const touches = f.prior_touch_count ?? null;

  return [
    {
      label: 'MKT TYPE',
      fill: mktType === 'trending_up' || mktType === 'trending_down' ? 0.9 : mktType === 'balanced' ? 0.4 : 0,
      value: mktType ? String(mktType) : '--',
      assessment: mktType ? String(mktType).toUpperCase().replace('_', ' ') : 'N/A',
      color: mktType === 'trending_up' ? 'green' : mktType === 'trending_down' ? 'red' : mktType === 'balanced' ? 'amber' : 'dim',
    },
    {
      label: 'OPEN TYPE',
      fill: openType === 'OD' ? 0.9 : openType === 'OTD' ? 0.7 : openType === 'ORR' ? 0.8 : openType === 'OA' ? 0.5 : 0,
      value: openType ? String(openType) : '--',
      assessment: openType ? String(openType).toUpperCase() : 'N/A',
      color: openType === 'OD' ? 'green' : openType === 'OTD' ? 'amber' : openType === 'ORR' ? 'red' : openType === 'OA' ? 'amber' : 'dim',
    },
    {
      label: 'IB RANGE',
      fill: ibRange != null ? cap(ibRange / 100) : 0,
      value: ibRange != null ? ibRange.toFixed(1) : '--',
      assessment: ibRange == null ? 'N/A' : ibRange > 50 ? 'WIDE' : ibRange < 20 ? 'NARROW' : 'NORMAL',
      color: ibRange == null ? 'dim' : ibRange > 50 ? 'red' : ibRange < 20 ? 'green' : 'amber',
    },
    {
      label: 'IB/ASPR',
      fill: ibVsAspr != null ? cap(ibVsAspr / 3) : 0,
      value: fmt(ibVsAspr),
      assessment: ibVsAspr == null ? 'N/A' : ibVsAspr > 1.5 ? 'EXPANDED' : ibVsAspr < 0.7 ? 'COMPRESSED' : 'NORMAL',
      color: ibVsAspr == null ? 'dim' : ibVsAspr > 1.5 ? 'amber' : ibVsAspr < 0.7 ? 'green' : 'dim',
    },
    {
      label: 'ASPR %',
      fill: asprPct != null ? cap(asprPct / 100) : 0,
      value: asprPct != null ? `${Math.round(asprPct)}%` : '--',
      assessment: asprPct == null ? 'N/A' : asprPct > 70 ? 'HIGH' : asprPct < 30 ? 'LOW' : 'AVG',
      color: asprPct == null ? 'dim' : 'amber',
    },
    {
      label: 'ROT FACTR',
      fill: rotFactor != null ? cap(Math.abs(rotFactor) / 10) : 0,
      value: fmt(rotFactor),
      assessment: rotFactor == null ? 'N/A' : Math.abs(rotFactor) > 3 ? 'DIRECTIONAL' : 'ROTATIONAL',
      color: rotFactor == null ? 'dim' : Math.abs(rotFactor) > 3 ? 'amber' : 'dim',
    },
    {
      label: 'VAL MIGR',
      fill: 0.5,
      value: valMigr ? String(valMigr) : '--',
      assessment: valMigr ? String(valMigr).toUpperCase() : 'N/A',
      color: valMigr === 'up' ? 'green' : valMigr === 'down' ? 'red' : valMigr === 'overlapping' ? 'amber' : 'dim',
    },
    {
      label: 'vs VAH',
      fill: vsVah != null ? cap(0.5 + vsVah / 50) : 0,
      value: vsVah != null ? (vsVah > 0 ? `+${vsVah.toFixed(1)}` : vsVah.toFixed(1)) : '--',
      assessment: vsVah == null ? 'N/A' : Math.abs(vsVah) < 5 ? 'AT VAH' : vsVah > 0 ? 'ABOVE' : 'BELOW',
      color: vsVah == null ? 'dim' : Math.abs(vsVah) < 5 ? 'amber' : 'dim',
    },
    {
      label: 'vs VAL',
      fill: vsVal != null ? cap(0.5 + vsVal / 50) : 0,
      value: vsVal != null ? (vsVal > 0 ? `+${vsVal.toFixed(1)}` : vsVal.toFixed(1)) : '--',
      assessment: vsVal == null ? 'N/A' : Math.abs(vsVal) < 5 ? 'AT VAL' : vsVal > 0 ? 'ABOVE' : 'BELOW',
      color: vsVal == null ? 'dim' : Math.abs(vsVal) < 5 ? 'amber' : 'dim',
    },
    {
      label: 'vs POC',
      fill: vsPoc != null ? cap(0.5 + vsPoc / 50) : 0,
      value: vsPoc != null ? (vsPoc > 0 ? `+${vsPoc.toFixed(1)}` : vsPoc.toFixed(1)) : '--',
      assessment: vsPoc == null ? 'N/A' : Math.abs(vsPoc) < 5 ? 'AT POC' : vsPoc > 0 ? 'ABOVE' : 'BELOW',
      color: vsPoc == null ? 'dim' : Math.abs(vsPoc) < 5 ? 'amber' : 'dim',
    },
    {
      label: 'IN VA',
      fill: inVa ? 1.0 : 0.0,
      value: inVa == null ? '--' : inVa ? 'YES' : 'NO',
      assessment: inVa == null ? 'N/A' : inVa ? 'IN VA' : 'OUT',
      color: inVa ? 'green' : inVa === false ? 'dim' : 'dim',
    },
    {
      label: 'ELAPSED',
      fill: elapsed != null ? cap(elapsed / 100) : 0,
      value: elapsed != null ? `${Math.round(elapsed)}%` : '--',
      assessment: elapsed == null ? 'N/A' : elapsed < 30 ? 'EARLY' : elapsed < 70 ? 'MID' : 'LATE',
      color: elapsed == null ? 'dim' : 'amber',
    },
    {
      label: 'MIN OPEN',
      fill: minOpen != null ? cap(minOpen / 390) : 0,
      value: minOpen != null ? `${Math.round(minOpen)}m` : '--',
      assessment: minOpen == null ? 'N/A' : `${Math.round(minOpen)}min`,
      color: minOpen == null ? 'dim' : 'dim',
    },
    {
      label: 'DEV POC',
      fill: 0.5,
      value: devPoc ? String(devPoc) : '--',
      assessment: devPoc ? String(devPoc).toUpperCase() : 'N/A',
      color: devPoc === 'up' ? 'green' : devPoc === 'down' ? 'red' : devPoc === 'flat' ? 'dim' : 'dim',
    },
    {
      label: 'TOUCHES',
      fill: touches != null ? cap(touches / 5) : 0,
      value: touches != null ? `${touches}` : '--',
      assessment: touches == null ? 'N/A' : touches > 2 ? 'RETESTING' : touches === 0 ? 'FIRST' : `${touches}x`,
      color: touches != null && touches > 2 ? 'amber' : 'dim',
    },
  ];
}

/** Macro regime gauges — 5 bars */
export function featureMacroToGauges(f: Record<string, any>): GaugeBarProps[] {
  const vix = f.vix_level ?? null;
  const vixChg = f.vix_change ?? null;
  const regime = f.macro_regime ?? null;
  const regScore = f.regime_score ?? null;
  const bias = f.macro_bias ?? null;

  return [
    {
      label: 'VIX',
      fill: cap((vix ?? 20) / 40),
      value: vix != null ? vix.toFixed(1) : '--',
      assessment: vix == null ? 'N/A' : vix < 18 ? 'LOW' : vix > 25 ? 'HIGH' : 'NORMAL',
      color: vix == null ? 'dim' : vix < 18 ? 'green' : vix > 25 ? 'red' : 'amber',
    },
    {
      label: 'VIX CHG',
      fill: vixChg != null ? cap(Math.abs(vixChg) / 10) : 0,
      value: vixChg != null ? (vixChg > 0 ? `+${vixChg.toFixed(2)}` : vixChg.toFixed(2)) : '--',
      assessment: vixChg == null ? 'N/A' : vixChg > 1 ? 'RISK OFF' : vixChg < -1 ? 'RISK ON' : 'STABLE',
      color: vixChg == null ? 'dim' : vixChg > 0 ? 'red' : vixChg < 0 ? 'green' : 'dim',
    },
    {
      label: 'REGIME',
      fill: 0.5,
      value: regime ? String(regime) : '--',
      assessment: regime ? String(regime).toUpperCase().replace('_', ' ') : 'N/A',
      color: regime === 'risk_on' ? 'green' : regime === 'risk_off' ? 'red' : regime === 'mixed' ? 'amber' : 'dim',
    },
    {
      label: 'REG SCORE',
      fill: regScore != null ? cap((regScore + 1) / 2) : 0.5,
      value: fmt(regScore),
      assessment: regScore == null ? 'N/A' : regScore > 0.5 ? 'BULLISH' : regScore < -0.5 ? 'BEARISH' : 'NEUTRAL',
      color: regScore == null ? 'dim' : regScore > 0.3 ? 'green' : regScore < -0.3 ? 'red' : 'amber',
    },
    {
      label: 'BIAS',
      fill: 0.5,
      value: bias ? String(bias) : '--',
      assessment: bias ? String(bias).toUpperCase() : 'N/A',
      color: bias === 'bull' ? 'green' : bias === 'bear' ? 'red' : bias === 'neutral' ? 'amber' : 'dim',
    },
  ];
}

/** Candle pattern gauges — 5 bars */
export function featureCandleToGauges(f: Record<string, any>): GaugeBarProps[] {
  const last3 = f.last_3_candles_direction ?? null;
  const doji = f.recent_doji ?? null;
  const consec = f.consecutive_same_direction ?? null;
  const hiVolPos = f.highest_volume_candle_position ?? null;
  const rangeExp = f.range_expansion ?? null;

  return [
    {
      label: 'LAST 3',
      fill: last3 != null ? cap(Math.abs(last3) / 3) : 0,
      value: fmt(last3, 0),
      assessment: last3 == null ? 'N/A' : last3 > 1 ? 'UP BIAS' : last3 < -1 ? 'DOWN BIAS' : 'MIXED',
      color: last3 == null ? 'dim' : last3 > 1 ? 'green' : last3 < -1 ? 'red' : 'amber',
    },
    {
      label: 'DOJI',
      fill: doji ? 1.0 : 0.0,
      value: doji == null ? '--' : doji ? 'YES' : 'NO',
      assessment: doji == null ? 'N/A' : doji ? 'INDECISION' : 'NONE',
      color: doji ? 'amber' : 'dim',
    },
    {
      label: 'CONSEC',
      fill: consec != null ? cap(consec / 6) : 0,
      value: fmt(consec, 0),
      assessment: consec == null ? 'N/A' : (consec ?? 0) >= 3 ? 'MOMENTUM' : `${consec ?? 0}`,
      color: consec != null && consec >= 3 ? 'green' : 'dim',
    },
    {
      label: 'HI VOL',
      fill: hiVolPos != null ? cap(hiVolPos / 9) : 0,
      value: fmt(hiVolPos, 0),
      assessment: hiVolPos == null ? 'N/A' : hiVolPos >= 7 ? 'RECENT' : hiVolPos <= 2 ? 'OLD' : `BAR ${hiVolPos}`,
      color: hiVolPos == null ? 'dim' : hiVolPos >= 7 ? 'green' : 'dim',
    },
    {
      label: 'RANGE EXP',
      fill: rangeExp != null ? cap(rangeExp / 3) : 0,
      value: fmt(rangeExp),
      assessment: rangeExp == null ? 'N/A' : rangeExp > 1.5 ? 'EXPANDING' : rangeExp < 0.7 ? 'CONTRACTING' : 'NORMAL',
      color: rangeExp == null ? 'dim' : rangeExp > 1.5 ? 'green' : rangeExp < 0.7 ? 'red' : 'amber',
    },
  ];
}

/** Book + Volume gauges — bid/ask depth, buy/sell volume, spread */
export function featureBookToGauges(book: { bid_price?: number; bid_size?: number; ask_price?: number; ask_size?: number; spread?: number } | null, f: Record<string, any>): GaugeBarProps[] {
  const bidSz = book?.bid_size ?? null;
  const askSz = book?.ask_size ?? null;
  const spread = book?.spread ?? null;
  const buyVol = f.buy_volume ?? null;
  const sellVol = f.sell_volume ?? null;
  const totalVol = (buyVol != null && sellVol != null) ? buyVol + sellVol : null;
  const buyPct = totalVol && totalVol > 0 ? buyVol / totalVol : null;
  const lastDelta = f.last_candle_delta ?? null;
  const bodyRatio = f.last_candle_body_ratio ?? null;
  const deltaAligned = f.delta_aligned ?? null;
  const deltaDivergence = f.delta_divergence ?? null;

  return [
    {
      label: 'BID SIZE',
      fill: bidSz != null ? cap(bidSz / 200) : 0,
      value: bidSz != null ? `${bidSz}` : '--',
      assessment: bidSz == null ? 'N/A' : bidSz > 100 ? 'THICK' : bidSz > 30 ? 'NORMAL' : 'THIN',
      color: bidSz == null ? 'dim' : bidSz > 100 ? 'green' : 'dim',
    },
    {
      label: 'ASK SIZE',
      fill: askSz != null ? cap(askSz / 200) : 0,
      value: askSz != null ? `${askSz}` : '--',
      assessment: askSz == null ? 'N/A' : askSz > 100 ? 'THICK' : askSz > 30 ? 'NORMAL' : 'THIN',
      color: askSz == null ? 'dim' : askSz > 100 ? 'red' : 'dim',
    },
    {
      label: 'SPREAD',
      fill: spread != null ? cap(spread / 2) : 0,
      value: spread != null ? spread.toFixed(2) : '--',
      assessment: spread == null ? 'N/A' : spread <= 0.25 ? 'TIGHT' : spread > 0.75 ? 'WIDE' : 'NORMAL',
      color: spread == null ? 'dim' : spread <= 0.25 ? 'green' : spread > 0.75 ? 'red' : 'amber',
    },
    {
      label: 'BUY VOL',
      fill: buyPct != null ? cap(buyPct) : 0,
      value: buyVol != null ? `${Math.round(buyVol)}` : '--',
      assessment: buyPct == null ? 'N/A' : buyPct > 0.6 ? 'DOMINANT' : buyPct > 0.45 ? 'BALANCED' : 'WEAK',
      color: buyPct == null ? 'dim' : buyPct > 0.55 ? 'green' : buyPct < 0.45 ? 'red' : 'amber',
    },
    {
      label: 'SELL VOL',
      fill: buyPct != null ? cap(1 - buyPct) : 0,
      value: sellVol != null ? `${Math.round(sellVol)}` : '--',
      assessment: buyPct == null ? 'N/A' : buyPct < 0.4 ? 'DOMINANT' : buyPct < 0.55 ? 'BALANCED' : 'WEAK',
      color: buyPct == null ? 'dim' : buyPct < 0.45 ? 'red' : buyPct > 0.55 ? 'green' : 'amber',
    },
    {
      label: 'LAST Δ',
      fill: lastDelta != null ? cap(Math.abs(lastDelta) / 500) : 0,
      value: lastDelta != null ? (lastDelta > 0 ? `+${lastDelta}` : `${lastDelta}`) : '--',
      assessment: lastDelta == null ? 'N/A' : lastDelta > 50 ? 'BUY PUSH' : lastDelta < -50 ? 'SELL PUSH' : 'NEUTRAL',
      color: lastDelta == null ? 'dim' : lastDelta > 0 ? 'green' : lastDelta < 0 ? 'red' : 'dim',
    },
    {
      label: 'BODY',
      fill: bodyRatio != null ? cap(bodyRatio) : 0,
      value: bodyRatio != null ? bodyRatio.toFixed(2) : '--',
      assessment: bodyRatio == null ? 'N/A' : bodyRatio < 0.2 ? 'ABSORB' : bodyRatio > 0.7 ? 'CONVICTN' : 'NORMAL',
      color: bodyRatio == null ? 'dim' : bodyRatio < 0.3 ? 'amber' : 'dim',
    },
    {
      label: 'Δ ALIGN',
      fill: deltaAligned ? 0.9 : 0.0,
      value: deltaAligned == null ? '--' : deltaAligned ? 'YES' : 'NO',
      assessment: deltaAligned == null ? 'N/A' : deltaAligned ? 'CONFIRMED' : 'DIVERGENT',
      color: deltaAligned == null ? 'dim' : deltaAligned ? 'green' : 'red',
    },
    {
      label: 'Δ DIVERG',
      fill: deltaDivergence ? 0.9 : 0.0,
      value: deltaDivergence == null ? '--' : deltaDivergence ? 'YES' : 'NO',
      assessment: deltaDivergence == null ? 'N/A' : deltaDivergence ? 'EXHAUSTION' : 'NONE',
      color: deltaDivergence == null ? 'dim' : deltaDivergence ? 'amber' : 'dim',
    },
  ];
}

/** Open Interest / COT gauges — from weekly CFTC data */
export function featureCotToGauges(
  current: { open_interest: number; net_commercial: number; net_non_commercial: number; net_non_reportable: number } | null,
  previous: { open_interest: number; net_commercial: number; net_non_commercial: number } | null,
): GaugeBarProps[] {
  if (!current) return [];

  const oi = current.open_interest;
  const oiChg = previous ? oi - previous.open_interest : null;
  const netComm = current.net_commercial;
  const netSpec = current.net_non_commercial;
  const netCommChg = previous ? netComm - previous.net_commercial : null;
  const netSpecChg = previous ? netSpec - previous.net_non_commercial : null;

  return [
    {
      label: 'OPEN INT',
      fill: cap(oi / 600000),  // NQ OI typically 200k-500k
      value: oi >= 1000 ? `${(oi / 1000).toFixed(0)}k` : `${oi}`,
      assessment: oi > 400000 ? 'HIGH' : oi > 250000 ? 'NORMAL' : 'LOW',
      color: 'amber',
    },
    {
      label: 'OI CHG',
      fill: oiChg != null ? cap(Math.abs(oiChg) / 50000) : 0,
      value: oiChg != null ? (oiChg > 0 ? `+${(oiChg / 1000).toFixed(1)}k` : `${(oiChg / 1000).toFixed(1)}k`) : '--',
      assessment: oiChg == null ? 'N/A' : oiChg > 10000 ? 'EXPANDING' : oiChg < -10000 ? 'SHRINKING' : 'STABLE',
      color: oiChg == null ? 'dim' : oiChg > 0 ? 'green' : oiChg < 0 ? 'red' : 'dim',
    },
    {
      label: 'NET SPEC',
      fill: cap(0.5 + netSpec / 200000),  // Symmetric around 0
      value: netSpec >= 1000 || netSpec <= -1000 ? `${(netSpec / 1000).toFixed(1)}k` : `${netSpec}`,
      assessment: netSpec > 50000 ? 'LONG' : netSpec < -50000 ? 'SHORT' : 'NEUTRAL',
      color: netSpec > 20000 ? 'green' : netSpec < -20000 ? 'red' : 'amber',
    },
    {
      label: 'SPEC CHG',
      fill: netSpecChg != null ? cap(Math.abs(netSpecChg) / 30000) : 0,
      value: netSpecChg != null ? (netSpecChg > 0 ? `+${(netSpecChg / 1000).toFixed(1)}k` : `${(netSpecChg / 1000).toFixed(1)}k`) : '--',
      assessment: netSpecChg == null ? 'N/A' : netSpecChg > 5000 ? 'ADDING' : netSpecChg < -5000 ? 'CUTTING' : 'FLAT',
      color: netSpecChg == null ? 'dim' : netSpecChg > 0 ? 'green' : netSpecChg < 0 ? 'red' : 'dim',
    },
    {
      label: 'NET COMM',
      fill: cap(0.5 + netComm / 200000),
      value: netComm >= 1000 || netComm <= -1000 ? `${(netComm / 1000).toFixed(1)}k` : `${netComm}`,
      assessment: netComm > 50000 ? 'HEDGING L' : netComm < -50000 ? 'HEDGING S' : 'NEUTRAL',
      color: netComm > 20000 ? 'green' : netComm < -20000 ? 'red' : 'amber',
    },
    {
      label: 'COMM CHG',
      fill: netCommChg != null ? cap(Math.abs(netCommChg) / 30000) : 0,
      value: netCommChg != null ? (netCommChg > 0 ? `+${(netCommChg / 1000).toFixed(1)}k` : `${(netCommChg / 1000).toFixed(1)}k`) : '--',
      assessment: netCommChg == null ? 'N/A' : netCommChg > 5000 ? 'ADDING' : netCommChg < -5000 ? 'CUTTING' : 'FLAT',
      color: netCommChg == null ? 'dim' : netCommChg > 0 ? 'green' : netCommChg < 0 ? 'red' : 'dim',
    },
  ];
}

/** Level-context gauges — 7 bars */
export function featureLevelToGauges(f: Record<string, any>): GaugeBarProps[] {
  const lvlType = f.level_type ?? null;
  const lvlCat = f.level_category ?? null;
  const strength = f.level_strength ?? null;
  const confluence = f.level_confluence ?? null;
  const approach = f.approach_direction ?? null;
  const distPoc = f.distance_from_poc ?? null;
  const distVwap = f.distance_from_vwap ?? null;

  function levelCatColor(cat: string | null): GaugeBarProps['color'] {
    if (!cat) return 'dim';
    const c = String(cat).toLowerCase();
    if (c === 'session') return 'amber';
    if (c === 'band') return 'amber';
    if (c === 'prior') return 'amber';
    if (c === 'structure') return 'green';
    return 'dim';
  }

  return [
    {
      label: 'LVL TYPE',
      fill: lvlType ? 0.5 : 0,
      value: lvlType ? String(lvlType).toUpperCase() : '--',
      assessment: lvlType ? String(lvlType).toUpperCase() : 'N/A',
      color: 'amber',
    },
    {
      label: 'LVL CAT',
      fill: lvlCat ? 0.5 : 0,
      value: lvlCat ? String(lvlCat) : '--',
      assessment: lvlCat ? String(lvlCat).toUpperCase() : 'N/A',
      color: levelCatColor(lvlCat),
    },
    {
      label: 'STRENGTH',
      fill: strength != null ? cap(strength / 20) : 0,
      value: fmt(strength),
      assessment: strength == null ? 'N/A' : strength > 10 ? 'STRONG' : strength > 5 ? 'MODERATE' : 'WEAK',
      color: strength == null ? 'dim' : strength > 10 ? 'green' : strength > 5 ? 'amber' : 'dim',
    },
    {
      label: 'CONFLNCE',
      fill: confluence != null ? cap(confluence / 4) : 0,
      value: fmt(confluence, 0),
      assessment: confluence == null ? 'N/A' : confluence >= 3 ? 'STRONG' : confluence >= 1 ? 'MODERATE' : 'SINGLE',
      color: confluence == null ? 'dim' : confluence >= 3 ? 'green' : confluence >= 1 ? 'amber' : 'dim',
    },
    {
      label: 'APPROACH',
      fill: approach ? 0.7 : 0,
      value: approach ? String(approach) : '--',
      assessment: approach ? String(approach).toUpperCase().replace('_', ' ') : 'N/A',
      color: approach === 'from_below' ? 'green' : approach === 'from_above' ? 'red' : 'dim',
    },
    {
      label: 'DIST POC',
      fill: distPoc != null ? cap(Math.abs(distPoc) / 100) : 0,
      value: distPoc != null ? `${Math.round(Math.abs(distPoc))}t` : '--',
      assessment: distPoc == null ? 'N/A' : Math.abs(distPoc) < 10 ? 'NEAR' : Math.abs(distPoc) > 50 ? 'FAR' : `${Math.round(Math.abs(distPoc))}t`,
      color: distPoc == null ? 'dim' : Math.abs(distPoc) < 10 ? 'amber' : 'dim',
    },
    {
      label: 'DIST VWAP',
      fill: distVwap != null ? cap(Math.abs(distVwap) / 100) : 0,
      value: distVwap != null ? `${Math.round(Math.abs(distVwap))}t` : '--',
      assessment: distVwap == null ? 'N/A' : Math.abs(distVwap) < 10 ? 'NEAR' : Math.abs(distVwap) > 50 ? 'FAR' : `${Math.round(Math.abs(distVwap))}t`,
      color: distVwap == null ? 'dim' : Math.abs(distVwap) < 10 ? 'amber' : 'dim',
    },
  ];
}
