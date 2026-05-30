import { describe, test, expect } from 'vitest'
import { resolveBonusChipState, type BonusChipInput } from './bonusChipState'

// Minimal base input: a freebet provider, fresh account, no deposit yet.
const base: BonusChipInput = {
  balanceNative: 0,
  isDrained: true,
  pendingCount: 0,
  progress: null,
  config: { type: 'freebet', amount: 1000, min_odds: 1.8 },
  triggerCurrency: 'SEK',
}

describe('resolveBonusChipState', () => {
  test('fresh freebet provider, no deposit -> deposit_hint', () => {
    expect(resolveBonusChipState(base)).toEqual({ kind: 'deposit_hint', amount: 1000, currency: 'SEK' })
  })

  test('balance covers the freebet amount, no row yet -> deposit_detected', () => {
    expect(resolveBonusChipState({ ...base, balanceNative: 1000, isDrained: false }))
      .toEqual({ kind: 'deposit_detected', amount: 1000, currency: 'SEK' })
  })

  test('deposit detection tolerates rounding (>= 90% of amount)', () => {
    expect(resolveBonusChipState({ ...base, balanceNative: 950, isDrained: false }).kind)
      .toBe('deposit_detected')
  })

  test('partial balance below detection but not drained -> none (no clutter)', () => {
    expect(resolveBonusChipState({ ...base, balanceNative: 300, isDrained: false }))
      .toEqual({ kind: 'none' })
  })

  test('no freebet config and no row -> none', () => {
    expect(resolveBonusChipState({ ...base, config: null })).toEqual({ kind: 'none' })
  })

  test('unknown bonus type config -> none', () => {
    expect(resolveBonusChipState({ ...base, config: { type: 'cashback', amount: 1000 } }))
      .toEqual({ kind: 'none' })
  })

  test('trigger_needed, wagering incomplete -> wagering', () => {
    const progress = { status: 'trigger_needed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 200, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, balanceNative: 1000, isDrained: false, progress }))
      .toEqual({ kind: 'wagering', wagered: 200, requirement: 1000, minOdds: 1.8 })
  })

  test('trigger_needed, wagering met -> unlock_ready', () => {
    const progress = { status: 'trigger_needed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, balanceNative: 0, progress }))
      .toEqual({ kind: 'unlock_ready', amount: 1000 })
  })

  test('freebet_available -> freebet_ready', () => {
    const progress = { status: 'freebet_available', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, progress }))
      .toEqual({ kind: 'freebet_ready', amount: 1000 })
  })

  test('completed -> none', () => {
    const progress = { status: 'completed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, progress })).toEqual({ kind: 'none' })
  })

  test('claimed -> none (already dismissed)', () => {
    const progress = { status: 'claimed', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 0, wagered_amount: 0, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, progress })).toEqual({ kind: 'none' })
  })

  test('active lifecycle wins even with config absent (live row is source of truth)', () => {
    const progress = { status: 'freebet_available', bonus_type: 'freebet', bonus_amount: 1000, wagering_requirement: 1000, wagered_amount: 1000, min_odds: 1.8 }
    expect(resolveBonusChipState({ ...base, config: null, progress }))
      .toEqual({ kind: 'freebet_ready', amount: 1000 })
  })
})

describe('resolveBonusChipState — bonusdeposit', () => {
  const bd = (over: Partial<BonusChipInput> = {}): BonusChipInput => ({
    balanceNative: 0, isDrained: true, pendingCount: 0,
    progress: null, config: { type: 'bonusdeposit', amount: 500 },
    triggerCurrency: 'SEK', ...over,
  })

  test('available bonusdeposit, drained -> bd_deposit with cap amount', () => {
    expect(resolveBonusChipState(bd())).toEqual({ kind: 'bd_deposit', amount: 500, currency: 'SEK' })
  })

  test('trigger_needed -> bd_trigger progress', () => {
    const progress = { status: 'trigger_needed', bonus_type: 'bonusdeposit', bonus_amount: 500, wagering_requirement: 500, wagered_amount: 200, min_odds: 1.5 }
    expect(resolveBonusChipState(bd({ progress, isDrained: false, balanceNative: 500 })))
      .toEqual({ kind: 'bd_trigger', wagered: 200, requirement: 500, minOdds: 1.5 })
  })

  test('in_progress -> bd_wagering progress with bonus amount', () => {
    const progress = { status: 'in_progress', bonus_type: 'bonusdeposit', bonus_amount: 500, wagering_requirement: 5000, wagered_amount: 1200, min_odds: 1.8 }
    expect(resolveBonusChipState(bd({ progress, isDrained: false, balanceNative: 1000 })))
      .toEqual({ kind: 'bd_wagering', wagered: 1200, requirement: 5000, minOdds: 1.8, bonusAmount: 500 })
  })

  test('completed -> none', () => {
    const progress = { status: 'completed', bonus_type: 'bonusdeposit', bonus_amount: 500, wagering_requirement: 5000, wagered_amount: 5000, min_odds: 1.8 }
    expect(resolveBonusChipState(bd({ progress }))).toEqual({ kind: 'none' })
  })

  test('bonusdeposit funded (not drained, no row) -> none (no clutter)', () => {
    expect(resolveBonusChipState(bd({ isDrained: false, balanceNative: 500 }))).toEqual({ kind: 'none' })
  })
})
