"""Arb leg correlation — pairs unlinked anchor/counter bets into arb groups.

An arb is a two-leg position: a soft-book anchor + a Polymarket/Kalshi counter
on the same event, opposite sides, placed close in time. The legs are recorded
by different paths and arrive with no shared id. This pass infers the pairing
and stamps a shared bets.arb_group_id. Ambiguous matches are left unlinked — a
wrong pair corrupts the analytics this is meant to fix.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..db.models import Bet, Event

COUNTER_PROVIDERS = {"polymarket", "kalshi"}
PAIR_WINDOW_SECONDS = 2 * 3600.0
LOOKBACK_DAYS = 30

_STOP = {"vs", "v", "the", "fc", "cf", "sc", "fk", "ec", "esports"}
_COMPLEMENT = {"home": "away", "away": "home", "over": "under", "under": "over"}


def _tokens(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return {t for t in s.split() if len(t) >= 3 and t not in _STOP}


def _match_confidence(counter: Bet, anchor: Bet, events: dict) -> str | None:
    """'high'  = same event_id + complementary (or blank) counter outcome.
    'medium' = counter has no event_id but both of the anchor event's team
               names appear in the counter's boost_event title.
    None     = no confident match.
    """
    # HIGH — exact event match
    if counter.event_id and anchor.event_id and counter.event_id == anchor.event_id:
        c_out = (counter.outcome or "").lower()
        a_out = (anchor.outcome or "").lower()
        if not c_out:
            return "high"  # unmatched counter — same event is enough
        if a_out and _COMPLEMENT.get(a_out) == c_out:
            return "high"
        return None
    # MEDIUM — title contains both anchor team names
    if not counter.event_id:
        title = (counter.boost_event or "").lower()
        ev = events.get(anchor.event_id) if anchor.event_id else None
        if ev is None:
            return None
        home = (ev.home_team or "").lower()
        away = (ev.away_team or "").lower()
        if not home or not away:
            return None
        if home in title and away in title:
            return "medium"
        t = _tokens(title)
        if _tokens(home) & t and _tokens(away) & t:
            return "medium"
    return None


def _best_anchor(counter: Bet, anchors: list[Bet], events: dict) -> Bet | None:
    """Single best anchor for this counter, or None if ambiguous / no match."""
    highs: list[Bet] = []
    mediums: list[Bet] = []
    for a in anchors:
        if a is counter or a.provider_id == counter.provider_id:
            continue
        if counter.placed_at is None or a.placed_at is None:
            continue
        if abs((a.placed_at - counter.placed_at).total_seconds()) > PAIR_WINDOW_SECONDS:
            continue
        conf = _match_confidence(counter, a, events)
        if conf == "high":
            highs.append(a)
        elif conf == "medium":
            mediums.append(a)
    if len(highs) == 1:
        return highs[0]
    if highs:
        return None  # ambiguous — don't guess
    if len(mediums) == 1:
        return mediums[0]
    return None


def correlate_arbs(session: Session) -> dict:
    """Link ungrouped arb legs. Returns {"linked": n, "groups": n}."""
    # naive UTC — bets.placed_at is TIMESTAMP WITHOUT TIME ZONE (reads back naive)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)
    legs = session.query(Bet).filter(Bet.arb_group_id.is_(None), Bet.placed_at >= cutoff).all()
    counters = [b for b in legs if b.provider_id in COUNTER_PROVIDERS and b.bet_type == "arb_counter"]
    anchors = [b for b in legs if b.provider_id not in COUNTER_PROVIDERS]

    event_ids = {b.event_id for b in (anchors + counters) if b.event_id}
    events: dict = {}
    if event_ids:
        for e in session.query(Event).filter(Event.id.in_(event_ids)).all():
            events[e.id] = e

    linked = 0
    groups: set[str] = set()
    for counter in counters:
        anchor = _best_anchor(counter, anchors, events)
        if anchor is None:
            continue
        gid = anchor.arb_group_id or counter.arb_group_id or uuid.uuid4().hex[:12]
        counter.arb_group_id = gid
        anchor.arb_group_id = gid
        if not anchor.bet_type:
            anchor.bet_type = "arb_anchor"
        linked += 1
        groups.add(gid)

    if linked:
        session.commit()
    return {"linked": linked, "groups": len(groups)}
