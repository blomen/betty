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
    return (
        (name or "")
        .lower()
        .strip()
        .replace(" vs. ", " v ")
        .replace(" vs ", " v ")
        .replace(" - ", " v ")
    )


def _bet_event(bet: dict) -> str:
    name = bet.get("event_name") or ""
    if not name:
        h, a = bet.get("home_team") or "", bet.get("away_team") or ""
        if h and a:
            name = f"{h} v {a}"
    return _normalize(name)


def _has_meaningful_diff(
    old: float | None, new: float | None, abs_tol: float = 0.01, pct_tol: float = 0.5
) -> bool:
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


def reconcile_from_history(
    db_pending: list[dict], history: list[dict]
) -> list[ReconcileDelta]:
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

    # Pass 2: fuzzy match for unmatched DB bets.
    #
    # Two sub-modes by DB-bet-odds:
    #   bet_odds > 0 (normal): name match ≥ 80 + odds within ±5%.
    #   bet_odds == 0 (placement interceptor lost the odds — cloudbet bet 792
    #     is the canonical case): name match ≥ 90, MUST be a terminal-status
    #     history entry, AND the event_name must uniquely match ONE history
    #     row. Multiple matches → ambiguous (could be ML + total + spread on
    #     the same event) → bail rather than mis-settle.
    for bet in db_pending:
        bet_id = bet.get("bet_id") or bet.get("id")
        if not bet_id or bet_id in matched_bets:
            continue
        bet_event = _bet_event(bet)
        bet_odds = float(bet.get("odds", 0) or 0)
        if not bet_event:
            continue
        zero_odds_mode = bet_odds <= 0
        name_threshold = 90 if zero_odds_mode else _FUZZY_NAME_THRESHOLD

        best: tuple[int, dict, float] | None = None  # (idx, entry, score)
        candidate_count = 0
        for idx, entry in enumerate(history):
            if idx in used_history:
                continue
            h_event = _normalize(entry.get("event_name") or "")
            if not h_event:
                continue
            score = fuzz.token_set_ratio(bet_event, h_event)
            if score < name_threshold:
                continue
            h_odds = float(entry.get("odds", 0) or 0)
            h_status = (entry.get("status") or "").lower()
            # Polymarket Loss rows (and other providers' closed-out entries)
            # report odds=0 — the bet was liquidated, no payout, original odds
            # not recoverable from the row. Trust the strong name match alone
            # for terminal statuses; non-terminal h_odds=0 still gets skipped
            # because matching an open bet by name without odds is meaningless.
            is_terminal = h_status in ("won", "lost", "void", "cashout")
            if zero_odds_mode and not is_terminal:
                # 0-odds DB bet only reconciles against settled history rows
                # — matching against an open entry leaves both sides pending.
                continue
            if h_odds <= 0:
                if not is_terminal:
                    continue
            elif not zero_odds_mode:
                odds_drift = abs(h_odds - bet_odds) / bet_odds * 100.0
                if odds_drift > _FUZZY_ODDS_TOL_PCT:
                    continue
            candidate_count += 1
            if best is None or score > best[2]:
                best = (idx, entry, score)

        if best is None:
            continue
        if zero_odds_mode and candidate_count > 1:
            logger.info(
                f"[reconcile] bet {bet_id} ({bet_event!r}): skipping 0-odds name-match — "
                f"{candidate_count} terminal candidates on same event (ambiguous)"
            )
            continue
        idx, entry, score = best
        used_history.add(idx)
        method = "name_terminal" if zero_odds_mode else "fuzzy"
        delta = _compute_delta(bet_id, bet, entry, method, score)
        if delta:
            deltas.append(delta)
            matched_bets.add(bet_id)

    # Pass 3: signature-only fallback for bets with neither id nor event_name.
    # Manually-recovered kalshi/polymarket bets sometimes land in the DB with
    # confirmation_id="" AND boost_event="" — the reactive-sync path bailed
    # too early and stripped the routing metadata. They never reconcile via
    # passes 1 or 2 because both keys are empty. (odds, stake) is unique
    # enough for these tiny-stake cents markets; only match against TERMINAL
    # history entries so we never accidentally settle an open bet.
    _SIG_ODDS_TOL_PCT = 5.0
    _SIG_STAKE_TOL_PCT = 5.0
    for bet in db_pending:
        bet_id = bet.get("bet_id") or bet.get("id")
        if not bet_id or bet_id in matched_bets:
            continue
        bet_odds = float(bet.get("odds", 0) or 0)
        bet_stake = float(bet.get("stake", 0) or 0)
        if bet_odds <= 0 or bet_stake <= 0:
            continue

        for idx, entry in enumerate(history):
            if idx in used_history:
                continue
            h_status = (entry.get("status") or "").lower()
            if h_status not in ("won", "lost", "void", "cashout"):
                continue
            h_odds = float(entry.get("odds", 0) or 0)
            h_stake = float(entry.get("stake", 0) or 0)
            if h_odds <= 0 or h_stake <= 0:
                continue
            if abs(h_odds - bet_odds) / bet_odds * 100.0 > _SIG_ODDS_TOL_PCT:
                continue
            if abs(h_stake - bet_stake) / bet_stake * 100.0 > _SIG_STAKE_TOL_PCT:
                continue
            used_history.add(idx)
            delta = _compute_delta(bet_id, bet, entry, "signature", 100.0)
            if delta:
                deltas.append(delta)
                matched_bets.add(bet_id)
            break

    return deltas


def _compute_delta(
    bet_id: int, bet: dict, entry: dict, method: str, confidence: float
) -> ReconcileDelta | None:
    """Build a ReconcileDelta with only the fields that meaningfully differ."""
    changes: dict[str, Any] = {}

    # status (only update if history says terminal — never push pending->pending)
    h_status = (entry.get("status") or "").lower()
    if h_status in ("won", "lost", "void", "cashout"):
        bet_status = (bet.get("result") or bet.get("status") or "pending").lower()
        if bet_status != h_status:
            changes["result"] = h_status

    # stake — 0 is a "not recoverable from this row" sentinel, NOT a real
    # value. Polymarket Loss/Redeemed history rows report stake=0 (the row
    # doesn't carry cost basis); Altenar settled rows can too. Overwriting a
    # real stake with 0 destroys the bet's P&L (2026-05-14: 8 polymarket bets
    # settled with stake wiped to 0 — the losses stopped being counted).
    # Only ever push a stake that's strictly positive.
    h_stake = entry.get("stake")
    bet_stake = bet.get("stake")
    if h_stake is not None and h_stake > 0 and _has_meaningful_diff(bet_stake, h_stake):
        changes["stake"] = float(h_stake)

    # odds — same sentinel rule. A real bet's odds are always > 1.0; 0 means
    # the history row didn't expose them, not that the bet had zero odds.
    h_odds = entry.get("odds")
    bet_odds = bet.get("odds")
    if (
        h_odds is not None
        and h_odds > 1.0
        and _has_meaningful_diff(bet_odds, h_odds, abs_tol=0.01, pct_tol=0.1)
    ):
        changes["odds"] = float(h_odds)

    # payout
    h_payout = entry.get("payout")
    if h_payout is not None and _has_meaningful_diff(bet.get("payout"), h_payout):
        changes["payout"] = float(h_payout)

    # provider_bet_id backfill — any match method that wasn't already keyed
    # on the id itself benefits from stamping it now so the next reconcile
    # cycle hits Pass 1 instead of re-fuzzying.
    if method in ("fuzzy", "name_terminal", "signature"):
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
    from local.http_client import tunnel_client as _tc

    if not hasattr(workflow, "fetch_history_for_bet"):
        return 0
    n = 0
    client = _tc()
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
            try:
                resp = await client.patch(
                    f"/api/bets/{delta.bet_id}", json=delta.changes, timeout=15.0
                )
                resp.raise_for_status()
            except Exception:
                logger.exception(
                    f"[reconcile-fallback] PATCH bet {delta.bet_id} failed"
                )
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
    from local.http_client import tunnel_client as _tc

    deltas = reconcile_from_history(db_pending, history)

    matched_ids: set = set()
    n = 0
    client = _tc()
    for delta in deltas:
        try:
            resp = await client.patch(
                f"/api/bets/{delta.bet_id}", json=delta.changes, timeout=15.0
            )
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
        unmatched = [
            b for b in db_pending if (b.get("bet_id") or b.get("id")) not in matched_ids
        ]
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
