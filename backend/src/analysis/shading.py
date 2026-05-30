"""Per-outcome shading-RISK diagnostic.

Conservative, READ-ONLY signal. Never mutates edge or stake — it only labels how
likely a value bet's "edge" is a shading/devig artifact rather than real value,
so realized CLV can be sliced by (odds_bucket x shading_risk) and a live
correction considered LATER, from data.

Grounded in verified research (workflow understand-shading-gap, 2026-05-30):
  - Pinnacle barely shades toward the public → we do NOT build a Pinnacle
    un-shading offset (would add error).
  - The residual favorite-longshot bias is a devig-METHOD artifact, already
    neutralized on 1x2 by power devig (devig.get_fair_odds_for_outcome). So the
    FLB flag fires on 2-way markets only.
  - Over-correction is the dominant risk → this stays diagnostic; thresholds are
    starting HYPOTHESES to backtest on Betty's own CLV-by-bucket data, NOT laws.

The spine is the existing consensus_lean signal (soft-consensus vs Pinnacle): a
`stale_outlier` lean means the soft books price the outcome MORE likely than
Pinnacle, i.e. the Pinnacle price we're beating may itself be shaded/stale on
this side — the cleanest available shading proxy.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Tunable thresholds (BACKTEST HYPOTHESES, not established constants) ──
TWO_WAY_MARKETS: frozenset[str] = frozenset({"moneyline", "spread", "total"})
SHADING_FAV_EXTREME_PROB: float = 0.80
SHADING_ELEVATED_PP: float = 2.0
SHADING_HIGH_PP: float = 4.0


@dataclass(frozen=True)
class ShadingSignal:
    """Read-only shading-risk label for one outcome of a value bet."""

    risk: str  # "low" | "elevated" | "high"
    favorite_side: bool  # is this outcome the market favorite?
    fav_prob: float  # the outcome's devigged fair probability
    divergence_pp: float | None  # consensus_lean divergence (the spine)
    flb_contrib: bool  # favorite-longshot flag fired (2-way only)
    reason: str  # human-readable "why"

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "favorite_side": self.favorite_side,
            "fav_prob": round(self.fav_prob, 4),
            "divergence_pp": (round(self.divergence_pp, 2) if self.divergence_pp is not None else None),
            "flb_contrib": self.flb_contrib,
            "reason": self.reason,
        }


def compute_shading(
    fair_probability: float,
    market: str,
    consensus_lean: dict | None,
    *,
    fav_extreme_prob: float = SHADING_FAV_EXTREME_PROB,
    elevated_divergence_pp: float = SHADING_ELEVATED_PP,
    high_divergence_pp: float = SHADING_HIGH_PP,
) -> ShadingSignal | None:
    """Classify shading risk for one outcome. Returns None if no consensus_lean.

    Args:
        fair_probability: Pinnacle devigged fair prob for this outcome (0..1).
        market: market key ("moneyline"/"spread"/"total"/"1x2").
        consensus_lean: ConsensusLean.to_dict() ({"lean","divergence_pp",...}) or None.
    """
    if not consensus_lean:
        return None

    lean = consensus_lean.get("lean")
    divergence_pp = consensus_lean.get("divergence_pp")

    spine = "low"
    reasons: list[str] = []
    if lean == "stale_outlier" and isinstance(divergence_pp, (int, float)):
        adiv = abs(divergence_pp)
        if adiv >= high_divergence_pp:
            spine = "high"
            reasons.append(f"soft consensus diverges {divergence_pp:+.1f}pp (stale-outlier, high)")
        elif adiv >= elevated_divergence_pp:
            spine = "elevated"
            reasons.append(f"soft consensus diverges {divergence_pp:+.1f}pp (stale-outlier)")

    flb_contrib = False
    # Symmetric boundary: avoid IEEE-754 drift in (1.0 - fav_extreme_prob) so the
    # longshot side fires at EXACTLY 1 - fav_extreme_prob, mirroring the favorite side.
    if market in TWO_WAY_MARKETS and (
        fair_probability >= fav_extreme_prob or (1.0 - fair_probability) >= fav_extreme_prob
    ):
        flb_contrib = True
        side = "favorite" if fair_probability >= 0.5 else "longshot"
        reasons.append(f"extreme {side} on 2-way market (FLB-prone devig)")

    risk = spine
    if flb_contrib and risk == "low":
        risk = "elevated"

    if not reasons:
        reasons.append("no elevated shading signals")

    return ShadingSignal(
        risk=risk,
        favorite_side=fair_probability >= 0.5,
        fav_prob=fair_probability,
        divergence_pp=divergence_pp if isinstance(divergence_pp, (int, float)) else None,
        flb_contrib=flb_contrib,
        reason="; ".join(reasons),
    )
