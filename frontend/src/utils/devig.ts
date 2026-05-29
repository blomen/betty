// Port of backend/src/analysis/devig.py — keep in sync.
// Tests in devig.test.ts pin the outputs to the Python source.

export function calculateMargin(oddsList: number[]): number {
  if (!oddsList.length) return 0
  if (oddsList.some((o) => o <= 1)) return 0
  const impliedSum = oddsList.reduce((s, o) => s + 1 / o, 0)
  return impliedSum - 1
}

export function devigMultiplicative(oddsList: number[]): number[] {
  if (!oddsList.length || oddsList.some((o) => o <= 1)) return oddsList
  const margin = calculateMargin(oddsList)
  const scale = 1 + margin
  return oddsList.map((o) => o * scale)
}

export function devigPower(oddsList: number[]): number[] {
  if (!oddsList.length || oddsList.some((o) => o <= 1)) return oddsList
  const impliedProbs = oddsList.map((o) => 1 / o)
  let kLow = 0.5
  let kHigh = 2.0
  let k = 1.0
  for (let i = 0; i < 50; i++) {
    k = (kLow + kHigh) / 2
    const adjustedSum = impliedProbs.reduce((s, p) => s + p ** k, 0)
    if (Math.abs(adjustedSum - 1.0) < 0.0001) break
    if (adjustedSum > 1.0) kLow = k
    else kHigh = k
  }
  const fairProbs = impliedProbs.map((p) => p ** k)
  const total = fairProbs.reduce((s, p) => s + p, 0)
  const normalized = fairProbs.map((p) => p / total)
  return normalized.map((p) => (p > 0 ? 1 / p : 100.0))
}

export function getFairOddsForOutcome(
  outcome: string,
  marketOdds: Record<string, number>,
): number | null {
  if (!(outcome in marketOdds)) return null
  const outcomes = Object.keys(marketOdds)
  const oddsList = outcomes.map((o) => marketOdds[o])
  // Power for 3-way (1x2), multiplicative for 2-way (totals, spreads, moneyline).
  const fairList = oddsList.length >= 3 ? devigPower(oddsList) : devigMultiplicative(oddsList)
  return fairList[outcomes.indexOf(outcome)]
}
