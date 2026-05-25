"""Residual shared event-matching helper.

The Polymarket bet recorder moved server-side — its full insert + settle
implementation now lives in ``backend/src/recorders/polymarket_api.py`` and
runs 24/7 as a backend task (see ``backend/src/recorders/server_poller.py``).

What stays here is only the team-name → home/away matcher, because the local
Kalshi recorder (``kalshi_api.py``) imports ``_match_outcome`` from this
module. The backend recorder carries an identical copy of these functions.
"""

from __future__ import annotations

import re

_STOP = {"vs", "v", "the", "fc", "cf", "sc", "fk", "ec", "esports"}


def _tokens(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return {t for t in s.split() if t and len(t) >= 3 and t not in _STOP}


def _match_outcome(outcome_name: str, home: str, away: str) -> str | None:
    """Map outcome_name to 'home' or 'away' based on team-name substring."""
    on = (outcome_name or "").lower()
    h, a = (home or "").lower(), (away or "").lower()
    if not on or not h or not a:
        return None
    # Exact substring (either direction)
    if h and (h in on or on in h):
        return "home"
    if a and (a in on or on in a):
        return "away"
    # Token overlap fallback
    ton = _tokens(outcome_name)
    th, ta = _tokens(home), _tokens(away)
    if th and ton & th:
        return "home"
    if ta and ton & ta:
        return "away"
    return None
