import { describe, it, expect } from 'vitest';
import { LANE_ORDER, laneLabel } from './lanes';

describe('lanes', () => {
  it('orders lanes Value, Arb, Reverse, Boost, Other', () => {
    expect(LANE_ORDER).toEqual(['Value', 'Arb', 'Reverse', 'Boost', 'Other']);
  });
  it('labels are identity (backend already returns lane names)', () => {
    expect(laneLabel('Value')).toBe('Value');
  });
});
