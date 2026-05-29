"""Multi-book sharp-blend orchestration.

Bridges the pure blend math (analysis/devig.compute_blended_sharp_fair) to
config (providers.yaml `sharp_blend`) and DB odds rows. Shadow-only this phase:
nothing here feeds the scanner's edge math — see
docs/superpowers/specs/2026-05-29-multi-book-sharp-blend-design.md.
"""

from __future__ import annotations

from ..config.loader import load_config
from .devig import BlendedFair, compute_blended_sharp_fair

# Sensible fallback if the config block is missing entirely.
_DEFAULT_WEIGHTS = {"pinnacle": 1.0, "max_dev_pct": 8}


def _blend_config() -> dict:
    return load_config().get_sharp_blend()


def get_members() -> list[str]:
    """Eligible blend providers. Always includes pinnacle."""
    members = list(_blend_config().get("members", []))
    if "pinnacle" not in members:
        members = ["pinnacle", *members]
    return members


def resolve_weights(sport: str | None) -> dict:
    """Per-sport member weights merged over `default`. Falls back when missing."""
    cfg = _blend_config()
    per_sport = cfg.get("per_sport", {})
    default = per_sport.get("default", _DEFAULT_WEIGHTS)
    if sport and sport in per_sport:
        merged = dict(default)
        merged.update(per_sport[sport])
        return merged
    return dict(default)


def blended_fair_from_rows(outcome: str, rows: list, sport: str | None) -> BlendedFair | None:
    """Build odds_by_outcome from Odds-like rows and compute the blend.

    `rows` must all belong to ONE (event, market, point, scope) group across the
    blend members; the caller is responsible for that filtering. Each row needs
    `.provider_id`, `.outcome`, `.odds`, and optionally `.depth_usd`.
    """
    cfg = _blend_config()
    members = get_members()
    liquidity_gated = set(cfg.get("liquidity_gated", []))
    liquidity_min_usd = float(cfg.get("liquidity_min_usd", 0) or 0)

    odds_by_outcome: dict[str, list[dict]] = {}
    for r in rows:
        if r.provider_id not in members:
            continue
        if r.odds is None or r.odds <= 1:
            continue
        odds_by_outcome.setdefault(r.outcome, []).append(
            {
                "provider": r.provider_id,
                "odds": r.odds,
                "depth_usd": getattr(r, "depth_usd", None),
            }
        )

    if outcome not in odds_by_outcome:
        return None

    return compute_blended_sharp_fair(
        outcome=outcome,
        odds_by_outcome=odds_by_outcome,
        members=members,
        weights=resolve_weights(sport),
        liquidity_gated=liquidity_gated,
        liquidity_min_usd=liquidity_min_usd,
    )
