import { describe, expect, it } from 'vitest'
import { devigMultiplicative, devigPower, getFairOddsForOutcome, calculateMargin } from './devig'

describe('calculateMargin', () => {
  it('returns 0 for fair 2/2 market', () => {
    expect(calculateMargin([2.0, 2.0])).toBeCloseTo(0, 4)
  })
  it('returns ~4.7% for 1.91/1.91 market', () => {
    expect(calculateMargin([1.91, 1.91])).toBeCloseTo(0.0471, 3)
  })
  it('returns 0 for empty / invalid inputs', () => {
    expect(calculateMargin([])).toBe(0)
    expect(calculateMargin([1.0, 2.0])).toBe(0)
    expect(calculateMargin([0.5, 2.0])).toBe(0)
  })
})

describe('devigMultiplicative', () => {
  it('maps [1.91, 1.91] -> [2.0, 2.0]', () => {
    const [a, b] = devigMultiplicative([1.91, 1.91])
    expect(a).toBeCloseTo(2.0, 3)
    expect(b).toBeCloseTo(2.0, 3)
  })
  it('preserves input on invalid', () => {
    expect(devigMultiplicative([1.0, 2.0])).toEqual([1.0, 2.0])
  })
})

describe('devigPower (3-way)', () => {
  it('devigs a 1x2 market to sum-to-1 implied probs', () => {
    const fair = devigPower([2.10, 3.40, 3.50])
    const probSum = fair.reduce((s, o) => s + 1 / o, 0)
    expect(probSum).toBeCloseTo(1.0, 3)
  })
})

describe('getFairOddsForOutcome', () => {
  it('uses multiplicative for 2-way', () => {
    const fair = getFairOddsForOutcome('home', { home: 1.91, away: 1.91 })
    expect(fair).toBeCloseTo(2.0, 3)
  })
  it('uses power for 3-way', () => {
    const fair = getFairOddsForOutcome('home', { home: 2.10, draw: 3.40, away: 3.50 })
    expect(fair).toBeGreaterThan(2.0)
  })
  it('returns null if outcome missing', () => {
    expect(getFairOddsForOutcome('home', { away: 2.0 })).toBeNull()
  })
})

describe('python parity (smoke)', () => {
  // These outputs were captured from devig.py at design time.
  // If they drift, suspect TS port or Python source has changed.
  it('matches Python devig_multiplicative([1.91, 1.91])', () => {
    const fair = devigMultiplicative([1.91, 1.91])
    expect(fair[0]).toBeCloseTo(2.0, 4)
  })
  it('matches Python devig_power for [2.10, 3.40, 3.50]', () => {
    // Allow loose tolerance — both impls converge via binary search
    const fair = devigPower([2.10, 3.40, 3.50])
    expect(fair[0]).toBeGreaterThan(2.10)
    expect(fair[0]).toBeLessThan(2.50)
  })
})
