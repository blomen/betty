"""reconcile_from_history — DB-mirrors-provider-truth reconciliation.

For each history entry from a provider, find the matching DB pending bet and
compute a delta. Returns the list of deltas (caller PATCHes them to the API
and broadcasts bet_reconciled events).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


@dataclass
class ReconcileDelta:
    bet_id: int
    match_method: str  # "id" | "fuzzy"
    confidence: float  # 0-100 for fuzzy; 100 for id
    changes: dict[str, Any]  # field name -> new value (only fields that differ)
    history_entry: dict  # the matched provider history entry (for logging/UI)


_FUZZY_NAME_THRESHOLD = 80  # rapidfuzz token_set_ratio
_FUZZY_ODDS_TOL_PCT = 5.0  # max % drift on odds for fuzzy match


def _normalize(name: str) -> str:
    return (name or "").lower().strip().replace(" vs. ", " v ").replace(" vs ", " v ").replace(" - ", " v ")


def _bet_event(bet: dict) -> str:
    name = bet.get("event_name") or ""
    if not name:
        h, a = bet.get("home_team") or "", bet.get("away_team") or ""
        if h and a:
            name = f"{h} v {a}"
    return _normalize(name)


def _has_meaningful_diff(old: float | None, new: float | None, abs_tol: float = 0.01, pct_tol: float = 0.5) -> bool:
    """Return True if old vs new differ enough to be worth recording.
    abs_tol: absolute floor (cents). pct_tol: percent of old."""
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    delta = abs(float(old) - float(new))
    if delta < abs_tol:
        return False
    return delta >= max(abs_tol, abs(float(old)) * pct_tol / 100.0)


def reconcile_from_history(db_pending: list[dict], history: list[dict]) -> list[ReconcileDelta]:
    """Compute reconciliation deltas: how each DB bet should be updated to match provider truth.

    Match precedence:
    1. Exact provider_bet_id match (confidence 100)
    2. Fuzzy name (rapidfuzz token_set_ratio >= 80) + odds within 5%

    Returns one ReconcileDelta per DB bet that needs an update.
    """
    deltas: list[ReconcileDelta] = []
    used_history: set[int] = set()

    # Pass 1: exact provider_bet_id matches
    by_pid: dict[str, tuple[int, dict]] = {}
    for idx, entry in enumerate(history):
        pid = str(entry.get("provider_bet_id") or "")
        if pid:
            by_pid[pid] = (idx, entry)

    matched_bets: set[int] = set()
    for bet in db_pending:
        bet_id = bet.get("bet_id") or bet.get("id")
        if not bet_id:
            continue
        bet_pid = str(bet.get("provider_bet_id") or "")
        if not bet_pid or bet_pid not in by_pid:
            continue
        idx, entry = by_pid[bet_pid]
        used_history.add(idx)
        delta = _compute_delta(bet_id, bet, entry, "id", 100.0)
        if delta:
            deltas.append(delta)
        matched_bets.add(bet_id)

    # Pass 2: fuzzy match for unmatched DB bets
    for bet in db_pending:
        bet_id = bet.get("bet_id") or bet.get("id")
        if not bet_id or bet_id in matched_bets:
            continue
        bet_event = _bet_event(bet)
        bet_odds = float(bet.get("odds", 0) or 0)
        if not bet_event or bet_odds <= 0:
            continue

        best: tuple[int, dict, float] | None = None  # (idx, entry, score)
        for idx, entry in enumerate(history):
            if idx in used_history:
                continue
            h_event = _normalize(entry.get("event_name") or "")
            if not h_event:
                continue
            score = fuzz.token_set_ratio(bet_event, h_event)
            if score < _FUZZY_NAME_THRESHOLD:
                continue
            h_odds = float(entry.get("odds", 0) or 0)
            if h_odds <= 0:
                continue
            odds_drift = abs(h_odds - bet_odds) / bet_odds * 100.0
            if odds_drift > _FUZZY_ODDS_TOL_PCT:
                continue
            if best is None or score > best[2]:
                best = (idx, entry, score)

        if best is None:
            continue
        idx, entry, score = best
        used_history.add(idx)
        delta = _compute_delta(bet_id, bet, entry, "fuzzy", score)
        if delta:
            deltas.append(delta)

    return deltas


def _compute_delta(bet_id: int, bet: dict, entry: dict, method: str, confidence: float) -> ReconcileDelta | None:
    """Build a ReconcileDelta with only the fields that meaningfully differ."""
    changes: dict[str, Any] = {}

    # status (only update if history says terminal — never push pending->pending)
    h_status = (entry.get("status") or "").lower()
    if h_status in ("won", "lost", "void", "cashout"):
        bet_status = (bet.get("result") or bet.get("status") or "pending").lower()
        if bet_status != h_status:
            changes["result"] = h_status

    # stake
    h_stake = entry.get("stake")
    bet_stake = bet.get("stake")
    if h_stake is not None and _has_meaningful_diff(bet_stake, h_stake):
        changes["stake"] = float(h_stake)

    # odds
    h_odds = entry.get("odds")
    bet_odds = bet.get("odds")
    if h_odds is not None and _has_meaningful_diff(bet_odds, h_odds, abs_tol=0.01, pct_tol=0.1):
        changes["odds"] = float(h_odds)

    # payout
    h_payout = entry.get("payout")
    if h_payout is not None and _has_meaningful_diff(bet.get("payout"), h_payout):
        changes["payout"] = float(h_payout)

    # provider_bet_id backfill (only when fuzzy matched and DB has none)
    if method == "fuzzy":
        h_pid = entry.get("provider_bet_id")
        bet_pid = bet.get("provider_bet_id")
        if h_pid and not bet_pid:
            changes["provider_bet_id"] = str(h_pid)

    if not changes:
        return None
    return ReconcileDelta(
        bet_id=int(bet_id),
        match_method=method,
        confidence=confidence,
        changes=changes,
        history_entry=entry,
    )


async def _fallback_reconcile_unmatched(
    page,
    workflow,
    proxy_url: str,
    auth_header: str,
    auth_value: str,
    provider_id: str,
    db_pending_unmatched: list[dict],
    broadcaster,
) -> int:
    """For each DB-pending bet not matched by paginated sync_history, attempt a
    targeted date-range query. PATCH + broadcast bet_reconciled per match.
    Returns the count of bets reconciled by the fallback path."""
    import httpx

    if not hasattr(workflow, "fetch_history_for_bet"):
        return 0
    n = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for bet in db_pending_unmatched:
            entries = await workflow.fetch_history_for_bet(page, bet)
            if not entries:
                continue
            # Convert HistoryEntry dataclasses to dicts shaped like sync_history output
            history = [
                {
                    "odds": e.odds,
                    "stake": e.stake,
                    "status": e.status,
                    "payout": e.payout,
                    "provider_bet_id": e.provider_bet_id,
                    "event_name": e.event_name,
                    "market": e.market,
                    "outcome": e.outcome,
                }
                for e in entries
            ]
            deltas = reconcile_from_history([bet], history)
            if not deltas:
                continue
            for delta in deltas:
                url = f"{proxy_url.rstrip('/')}/api/bets/{delta.bet_id}"
                try:
                    resp = await client.patch(url, json=delta.changes, headers={auth_header: auth_value})
                    resp.raise_for_status()
                except Exception:
                    logger.exception(f"[reconcile-fallback] PATCH bet {delta.bet_id} failed")
                    continue
                n += 1
                broadcaster.publish(
                    "bet_reconciled",
                    {
                        "provider_id": provider_id,
                        "bet_id": delta.bet_id,
                        "match_method": f"fallback_{delta.match_method}",
                        "confidence": delta.confidence,
                        "changes": delta.changes,
                        "event_name": delta.history_entry.get("event_name"),
                    },
                )
    return n


async def reconcile_and_publish(
    proxy_url: str,
    auth_header: str,
    auth_value: str,
    provider_id: str,
    db_pending: list[dict],
    history: list[dict],
    broadcaster,  # has .publish(event, data)
    *,
    page=None,
    workflow=None,
) -> int:
    """Compute reconciliation deltas, PATCH each, broadcast bet_reconciled.
    Returns the number of bets reconciled.

    Optional kwargs page + workflow enable the targeted date-range fallback:
    any DB-pending bet not matched by the main paginated history pass is
    re-queried via workflow.fetch_history_for_bet (if the workflow implements it).
    """
    import httpx

    deltas = reconcile_from_history(db_pending, history)

    matched_ids: set = set()
    n = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for delta in deltas:
            url = f"{proxy_url.rstrip('/')}/api/bets/{delta.bet_id}"
            try:
                resp = await client.patch(url, json=delta.changes, headers={auth_header: auth_value})
                resp.raise_for_status()
            except Exception:
                logger.exception(f"[reconcile] PATCH bet {delta.bet_id} failed")
                continue
            n += 1
            matched_ids.add(delta.bet_id)
            broadcaster.publish(
                "bet_reconciled",
                {
                    "provider_id": provider_id,
                    "bet_id": delta.bet_id,
                    "match_method": delta.match_method,
                    "confidence": delta.confidence,
                    "changes": delta.changes,
                    "event_name": delta.history_entry.get("event_name"),
                },
            )

    # Fallback for unmatched stale bets (requires page + workflow from caller)
    if page is not None and workflow is not None:
        unmatched = [b for b in db_pending if (b.get("bet_id") or b.get("id")) not in matched_ids]
        if unmatched:
            n_fallback = await _fallback_reconcile_unmatched(
                page,
                workflow,
                proxy_url,
                auth_header,
                auth_value,
                provider_id,
                unmatched,
                broadcaster,
            )
            return n + n_fallback

    return n
