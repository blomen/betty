export const LANE_ORDER = ['Value', 'Arb', 'Reverse', 'Boost', 'Other'] as const;
export type Lane = (typeof LANE_ORDER)[number];
export function laneLabel(lane: string): string {
  return lane;
}
