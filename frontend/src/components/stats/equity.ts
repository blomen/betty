export interface EquityPoint { date: Date; value: number }

/** Convert backend equity-curve points to baseline-anchored equity values.
 *  baseline = current_bankroll - total_profit; value = baseline + cum_profit.
 *  Guarantees the final point equals current bankroll. */
export function toEquityPoints(
  points: { t: string | null; cum_profit_sek: number }[],
  meta: { total_profit_sek: number; current_bankroll_sek: number },
): EquityPoint[] {
  const baseline = meta.current_bankroll_sek - meta.total_profit_sek;
  return points
    .filter((p) => p.t)
    .map((p) => ({ date: new Date(p.t as string), value: baseline + p.cum_profit_sek }));
}
