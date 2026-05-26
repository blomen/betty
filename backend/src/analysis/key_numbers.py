"""NFL spread and total key-number annotation.

NFL games end on a small set of margins much more often than chance —
because scoring is 3 (FG) + 6/7 (TD) + 1/2 (PAT/2pt). The dominant
spread-margin landing spots are **3, 6, 7, 10**, and totals cluster
around **37, 41, 44, 47**.

A spread at -2.5 vs. one at -3.5 isn't a "half-point" difference in
practice — it's the difference between an outright win and a push on
the most common margin in the league. Same idea for buying a half-point
through 7. The standard half-point values widely cited:

    Through 3:  ~2.5pp (≈ 5–6¢ in juice)
    Through 7:  ~1.5pp
    Through 6:  ~1.0pp (often paired with 7 via the field-goal hook)
    Through 10: ~0.8pp

We don't apply these to the edge math automatically — that risks
double-counting whatever Pinnacle already prices in. Instead this
module annotates value bets so the bettor and downstream analytics can
see when a spread/total sits on a key number, and slice realized ROI
by key-number bucket.

This module is intentionally NFL-only. NCAAF and NBA have weaker
key-number signals (NCAAF has noisier scoring distributions; NBA games
land on a much wider spread). MLB run-line is at fixed +/- 1.5 by
convention so the concept doesn't apply.
"""

from __future__ import annotations

from dataclasses import dataclass

# Margin landing frequencies derived from public NFL play-by-play data
# (see Wong, Boyd, Sportsbookreview's standard half-point chart). Listed
# roughly in order of impact. These are NOT used for stake math — they
# are surfaced for downstream review.
NFL_SPREAD_KEY_NUMBERS: tuple[int, ...] = (3, 7, 6, 10, 14)

# Total-points landing clusters. Less impactful than spread keys but
# real: 41 and 44 are the densest, 37 and 47 secondary.
NFL_TOTAL_KEY_NUMBERS: tuple[int, ...] = (37, 41, 44, 47, 51)

# Half-point value (cover-rate delta in percentage points) for buying or
# selling through each spread key. Source: standard sharps half-point
# chart. Diagnostic only — we do not currently apply these to edge.
SPREAD_HALF_POINT_VALUE_PP: dict[int, float] = {
    3: 2.5,
    7: 1.5,
    6: 1.0,
    10: 0.8,
    14: 0.4,
}

# Sport-string variants we accept as NFL. The codebase uses
# "americanfootball_nfl" in events.sport per sports.yaml conventions,
# but other modules use shorter forms.
_NFL_SPORT_KEYS = frozenset({"americanfootball_nfl", "nfl", "americanfootball-nfl", "football_nfl"})


def is_nfl(sport: str | None) -> bool:
    if not sport:
        return False
    return sport.strip().lower() in _NFL_SPORT_KEYS


def _is_spread_market(market: str | None) -> bool:
    return (market or "").lower() in {"spread", "handicap", "runline", "puckline"}


def _is_total_market(market: str | None) -> bool:
    return (market or "").lower() in {"total", "totals", "over_under", "ou"}


@dataclass(frozen=True)
class KeyNumberInfo:
    """Per-bet annotation describing how a spread/total relates to a key number.

    `on_key`: the point exactly matches a key number (e.g. spread -3.0).
    `straddles_key`: the point sits on a half-point adjacent to a key
        number (e.g. spread -2.5 or -3.5 around the 3 key). These are
        the highest-leverage positions — half-point off a key is where
        the cover-rate cliff lives.
    `nearest_key`: the closest key number to this point (integer).
    `distance`: signed distance from the point to nearest key (so -0.5
        means the point sits half a point *below* the key — i.e. the
        better-priced side for the favorite on spreads).
    `half_point_value_pp`: estimated cover-rate value of buying/selling
        through the nearest key, in percentage points. None when the
        point isn't adjacent to a known key.
    """

    on_key: bool
    straddles_key: bool
    nearest_key: int
    distance: float
    half_point_value_pp: float | None

    def to_dict(self) -> dict:
        return {
            "on_key": self.on_key,
            "straddles_key": self.straddles_key,
            "nearest_key": self.nearest_key,
            "distance": round(self.distance, 2),
            "half_point_value_pp": self.half_point_value_pp,
        }


def _key_for(point: float, keys: tuple[int, ...]) -> tuple[int, float]:
    """Return (nearest_key, signed_distance) using absolute spread magnitude."""
    abs_point = abs(point)
    nearest = min(keys, key=lambda k: abs(abs_point - k))
    return nearest, abs_point - nearest


def annotate_spread(sport: str | None, point: float | None) -> KeyNumberInfo | None:
    """Annotation for an NFL spread point. None for non-NFL or no point."""
    if not is_nfl(sport) or point is None:
        return None
    nearest, distance = _key_for(point, NFL_SPREAD_KEY_NUMBERS)
    on_key = abs(distance) < 1e-9
    # "Straddles" = within a half-point of a key (the high-leverage zone).
    straddles = (not on_key) and abs(distance) <= 0.5 + 1e-9
    hpv = SPREAD_HALF_POINT_VALUE_PP.get(nearest) if (on_key or straddles) else None
    return KeyNumberInfo(
        on_key=on_key,
        straddles_key=straddles,
        nearest_key=nearest,
        distance=distance,
        half_point_value_pp=hpv,
    )


def annotate_total(sport: str | None, point: float | None) -> KeyNumberInfo | None:
    """Annotation for an NFL total. None for non-NFL or no point."""
    if not is_nfl(sport) or point is None:
        return None
    nearest, distance = _key_for(point, NFL_TOTAL_KEY_NUMBERS)
    on_key = abs(distance) < 1e-9
    straddles = (not on_key) and abs(distance) <= 0.5 + 1e-9
    # No published half-point chart for totals — leave None and let the
    # diagnostic value come from on_key / straddles_key flags.
    return KeyNumberInfo(
        on_key=on_key,
        straddles_key=straddles,
        nearest_key=nearest,
        distance=distance,
        half_point_value_pp=None,
    )


def annotate(sport: str | None, market: str | None, point: float | None) -> KeyNumberInfo | None:
    """Dispatcher: route to spread/total annotator based on market type.

    Returns None for non-NFL, non-spread/total markets, or missing point.
    Safe to call on every value bet — pure, no I/O.
    """
    if _is_spread_market(market):
        return annotate_spread(sport, point)
    if _is_total_market(market):
        return annotate_total(sport, point)
    return None
