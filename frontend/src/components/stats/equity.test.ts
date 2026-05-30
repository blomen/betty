import { describe, it, expect } from 'vitest';
import { toEquityPoints } from './equity';

describe('toEquityPoints', () => {
  it('anchors baseline so the last point equals current bankroll', () => {
    const pts = toEquityPoints(
      [{ t: '2026-01-01T00:00:00Z', cum_profit_sek: 100 },
       { t: '2026-01-02T00:00:00Z', cum_profit_sek: 0 }],
      { total_profit_sek: 0, current_bankroll_sek: 5000 },
    );
    expect(pts[pts.length - 1].value).toBe(5000);
    expect(pts[0].value).toBe(5100);
  });
});
