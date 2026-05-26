"""Soft-consensus lean indicator — Arnold's free substitute for public bet-% data.

The "public vs sharp" framing every betting course teaches reduces to one
question: do the *other* soft books agree with the sharp price, or do
they collectively disagree (meaning the public has loaded one side and
the books shaded their lines)?

Anon's framework uses paid `bet_pct` / `money_pct` feeds to infer this.
Arnold extracts from 40+ soft books — we can read the same signal directly
from the cross-book consensus without paying for handle data.

Mechanism:
  1. Take the value bet's outcome and look at the implied probabilities
     across all soft books that priced that outcome.
  2. Take the median implied probability as the "soft consensus".
  3. Compare to Pinnacle's devigged fair probability.

Interpretation (positive divergence = softs say more likely than fair):

  • Softs price the outcome ~as fair (|divergence| ≤ 1.5pp):
        → market-lag value: only the one book we're betting at is slow.
        Standard +EV; no public-vs-sharp signal.

  • Softs collectively price the outcome MORE likely than fair (+1.5pp+):
        → public has loaded this side; books have shaded short to
        manage liability. Our value bet at one book is a STALE OUTLIER —
        more likely to move against us before tip-off. Heightened risk.

  • Softs collectively price the outcome LESS likely than fair (−1.5pp+):
        → public NOT on this side. We're with the sharps, against the
        public. SHARP VALUE — the cleanest scenario; the kind Anon
        describes when sharps "fade the public".

Diagnostic-only — does not modify edge or stake. Surfaces as a colored
badge on the value-bet row so the user can prioritize sharp-value plays
and treat stale-outlier rows with extra caution (or place them faster
before the line moves).
"""

from __future__ import annotations

from dataclasses import dataclass

# Minimum distinct soft books required for a stable median. Below this
# the "consensus" is noisy enough that we don't surface a verdict.
MIN_SOFT_BOOKS = 3

# Empirical adjustment: soft books carry ~3-5% market vig. On a typical
# 2-way market that's ~2pp per outcome of "extra" implied probability
# baked in vs the devigged Pinnacle fair. We subtract this so neutral
# markets land near zero divergence instead of systematically positive.
#
# Imperfect — different books carry different margins, and 3-way markets
# spread the vig across three outcomes — but the QUALITATIVE direction
# of strong divergences (>±3pp adjusted) is robust to this.
TYPICAL_SOFT_VIG_PP = 2.0

# Threshold for calling a divergence meaningful (in pp, after vig adjustment).
NEUTRAL_BAND_PP = 1.5

# Sharp providers excluded from "soft consensus" — these don't shade for
# public. Listed by id so we can filter `odds_snapshot` entries cleanly.
_SHARP_PROVIDER_IDS = frozenset({"pinnacle", "polymarket", "kalshi", "cloudbet"})


@dataclass(frozen=True)
class ConsensusLean:
    soft_consensus_pp: float  # Median implied prob across soft books, in %
    sharp_pp: float  # Pinnacle devigged fair probability, in %
    divergence_pp: float  # adjusted (soft - sharp), in pp
    lean: str  # one of: "sharp_value", "stale_outlier", "neutral", "market_lag"
    n_soft_books: int  # how many soft books contributed to the consensus

    def to_dict(self) -> dict:
        return {
            "soft_consensus_pp": round(self.soft_consensus_pp, 2),
            "sharp_pp": round(self.sharp_pp, 2),
            "divergence_pp": round(self.divergence_pp, 2),
            "lean": self.lean,
            "n_soft_books": self.n_soft_books,
        }


def _median(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        raise ValueError("median of empty list")
    s = sorted(values)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def compute_consensus_lean(
    odds_snapshot: list[dict] | None,
    sharp_fair_probability: float,
    bet_provider: str | None = None,
    min_books: int = MIN_SOFT_BOOKS,
) -> ConsensusLean | None:
    """Score a value bet against the soft-book consensus.

    Returns None when there's not enough cross-book data to form a
    meaningful consensus — better to surface nothing than a noisy
    verdict that misleads the user.

    Args:
        odds_snapshot: List of `{provider, odds, ...}` entries — the
            soft-book odds for the SAME outcome the value bet targets.
            Sharp providers (Pinnacle, Polymarket, Kalshi, Cloudbet)
            and the value-bet's own provider are excluded from the
            consensus so it's a true "OTHER soft books" signal.
        sharp_fair_probability: Pinnacle's devigged fair probability
            for this outcome (decimal in [0, 1]).
        bet_provider: The provider where the value bet sits. Excluded
            from the consensus so this row's own price doesn't anchor
            the verdict.

    Returns:
        `ConsensusLean` with the four-state `lean` label, or None.
    """
    if not odds_snapshot or sharp_fair_probability <= 0 or sharp_fair_probability >= 1:
        return None

    soft_implieds: list[float] = []
    for entry in odds_snapshot:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("provider")
        if not provider or provider in _SHARP_PROVIDER_IDS:
            continue
        if provider == bet_provider:
            continue
        odds = entry.get("odds")
        if not isinstance(odds, (int, float)) or odds <= 1.0:
            continue
        soft_implieds.append(1.0 / float(odds))

    if len(soft_implieds) < min_books:
        return None

    soft_consensus = _median(soft_implieds)
    soft_consensus_pp = soft_consensus * 100.0
    sharp_pp = sharp_fair_probability * 100.0
    raw_divergence_pp = soft_consensus_pp - sharp_pp
    # Vig adjustment: soft books carry margin, sharp baseline is devigged.
    # Subtract the typical per-outcome contribution so neutral markets
    # land near zero.
    divergence_pp = raw_divergence_pp - TYPICAL_SOFT_VIG_PP

    if abs(divergence_pp) <= NEUTRAL_BAND_PP:
        lean = "market_lag"
    elif divergence_pp < -NEUTRAL_BAND_PP:
        lean = "sharp_value"
    else:
        lean = "stale_outlier"

    return ConsensusLean(
        soft_consensus_pp=soft_consensus_pp,
        sharp_pp=sharp_pp,
        divergence_pp=divergence_pp,
        lean=lean,
        n_soft_books=len(soft_implieds),
    )
