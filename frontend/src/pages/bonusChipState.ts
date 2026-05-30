// Pure decision logic for the Sports-tab freebet chip. No React, no I/O — kept
// in its own module so the branching (the bug-prone part) is unit-testable in
// isolation without mounting PlayPage. <BonusChip> in PlayPage.tsx renders the
// result; the resolver decides WHICH of the six states applies.

/** Subset of BonusProgressEntry (/bankroll/status) the resolver needs. */
export interface BonusChipProgress {
  status: string
  bonus_type: string | null
  bonus_amount: number
  wagering_requirement: number
  wagered_amount: number
  min_odds: number
}

/** Subset of a /bankroll/bonuses yaml entry the resolver needs. */
export interface ProviderBonusConfig {
  type?: string
  amount?: number
  min_odds?: number
}

export interface BonusChipInput {
  /** Provider balance in its OWN currency (compared against the native-currency freebet amount). */
  balanceNative: number
  /** Caller-supplied "near-empty" flag (PlayPage: bal < DRAIN_THRESHOLD_SEK). */
  isDrained: boolean
  pendingCount: number
  /** Live bonus row for this provider, or null if none exists yet. */
  progress: BonusChipProgress | null
  /** Static yaml bonus config for this provider, or null. */
  config: ProviderBonusConfig | null
  /** Display currency for the amount (e.g. 'SEK'). */
  triggerCurrency: string
}

export type BonusChipState =
  | { kind: 'none' }
  | { kind: 'deposit_hint'; amount: number; currency: string }
  | { kind: 'deposit_detected'; amount: number; currency: string }
  | { kind: 'wagering'; wagered: number; requirement: number; minOdds: number }
  | { kind: 'unlock_ready'; amount: number }
  | { kind: 'freebet_ready'; amount: number }

// A deposit "counts" once the balance reaches ~90% of the freebet amount —
// tolerant of rounding/fees on the bookmaker side. Below that the user still
// gets a manual "start tracking" button via deposit_hint, so they're never
// blocked by detection being slightly off.
const DEPOSIT_DETECT_RATIO = 0.9

export function resolveBonusChipState(input: BonusChipInput): BonusChipState {
  const { balanceNative, isDrained, pendingCount, progress, config, triggerCurrency } = input
  const status = progress?.status ?? null

  // 1) Active freebet lifecycle — live row is source of truth, independent of balance.
  if (status === 'trigger_needed') {
    const requirement = progress!.wagering_requirement
    const wagered = progress!.wagered_amount
    if (requirement > 0 && wagered >= requirement) {
      return { kind: 'unlock_ready', amount: progress!.bonus_amount }
    }
    return { kind: 'wagering', wagered, requirement, minOdds: progress!.min_odds }
  }
  if (status === 'freebet_available') {
    return { kind: 'freebet_ready', amount: progress!.bonus_amount }
  }
  // completed/claimed -> row drops out (existing behavior). in_progress is a
  // bonusdeposit phase, not handled by the freebet chip.
  if (status === 'completed' || status === 'claimed' || status === 'in_progress') {
    return { kind: 'none' }
  }

  // 2) No active row (status 'available' or absent). Need a freebet config.
  const bonusType = progress?.bonus_type ?? config?.type ?? null
  if (bonusType !== 'freebet' || !config) return { kind: 'none' }
  const amount = config.amount ?? 0
  if (amount <= 0) return { kind: 'none' }

  // Deposit detected: balance covers (most of) the freebet amount.
  if (balanceNative >= amount * DEPOSIT_DETECT_RATIO) {
    return { kind: 'deposit_detected', amount, currency: triggerCurrency }
  }
  // Pre-deposit hint: only for bonus-only providers (near-empty, no pending),
  // matching the existing onlyBonus gate so funded clusters aren't cluttered.
  if (isDrained && pendingCount === 0) {
    return { kind: 'deposit_hint', amount, currency: triggerCurrency }
  }
  return { kind: 'none' }
}
