"""Mirror state API — Phase 2 of the platform rebuild (2026-05-08).

The local mirror writes authoritative state here; the frontend reads from
here instead of trying to reconstruct from in-memory + ephemeral SSE +
React state. Eliminates the entire stale-state class of bugs (today's
"Log in to continue" red badge while runner was at ready_to_run is the
canonical example).

Six endpoints:
- POST /api/mirror/provider-state    — mirror upsert on login/balance/tab change
- POST /api/mirror/runner-state      — runner upsert on state transition
- POST /api/mirror/event             — mirror appends every SSE event (best-effort)
- GET  /api/mirror/state             — bulk fetch all providers (frontend mount)
- GET  /api/mirror/state/{pid}       — single provider lookup
- GET  /api/mirror/events            — replay since ts (frontend reconnect)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...db.models import MirrorEventLog, MirrorProviderState, MirrorRunnerState
from ..deps import get_db

router = APIRouter(prefix="/api/mirror", tags=["mirror-state"])


# ---------- Request/response schemas ----------


class ProviderStateUpsert(BaseModel):
    provider_id: str
    logged_in: bool | None = None
    balance: float | None = None
    balance_currency: str | None = None
    tab_url: str | None = None
    tab_open: bool | None = None


class RunnerStateUpsert(BaseModel):
    provider_id: str
    state: str | None = None
    mode: str | None = None
    current_arb_group_id: str | None = None
    current_opp_id: int | None = None
    last_idle_reason: str | None = None


class EventAppend(BaseModel):
    provider_id: str | None = None
    event_type: str
    data: dict[str, Any] | None = None


# ---------- Helpers ----------


def _utcnow():
    return datetime.now(timezone.utc)


def _is_postgres(db: Session) -> bool:
    return db.bind.dialect.name == "postgresql"


def _upsert_provider_state(db: Session, payload: ProviderStateUpsert) -> dict:
    """Upsert MirrorProviderState — only sets fields that were provided.

    Postgres uses ON CONFLICT for atomic upsert. SQLite (dev fallback)
    uses select-then-update/insert (race acceptable for dev).
    """
    fields = payload.model_dump(exclude_unset=True, exclude={"provider_id"})
    if _is_postgres(db):
        # PG upsert. Only update the columns that were sent.
        stmt = pg_insert(MirrorProviderState).values(provider_id=payload.provider_id, **fields)
        if fields:
            stmt = stmt.on_conflict_do_update(
                index_elements=[MirrorProviderState.provider_id],
                set_={**fields, "updated_at": _utcnow()},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=[MirrorProviderState.provider_id])
        db.execute(stmt)
        db.commit()
    else:
        existing = db.get(MirrorProviderState, payload.provider_id)
        if existing is None:
            db.add(MirrorProviderState(provider_id=payload.provider_id, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.updated_at = _utcnow()
        db.commit()

    row = db.get(MirrorProviderState, payload.provider_id)
    return _serialize_provider(row) if row else {}


def _upsert_runner_state(db: Session, payload: RunnerStateUpsert) -> dict:
    fields = payload.model_dump(exclude_unset=True, exclude={"provider_id"})
    if _is_postgres(db):
        stmt = pg_insert(MirrorRunnerState).values(provider_id=payload.provider_id, **fields)
        if fields:
            stmt = stmt.on_conflict_do_update(
                index_elements=[MirrorRunnerState.provider_id],
                set_={**fields, "updated_at": _utcnow()},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=[MirrorRunnerState.provider_id])
        db.execute(stmt)
        db.commit()
    else:
        existing = db.get(MirrorRunnerState, payload.provider_id)
        if existing is None:
            db.add(MirrorRunnerState(provider_id=payload.provider_id, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.updated_at = _utcnow()
        db.commit()

    row = db.get(MirrorRunnerState, payload.provider_id)
    return _serialize_runner(row) if row else {}


def _serialize_provider(row: MirrorProviderState) -> dict:
    return {
        "provider_id": row.provider_id,
        "logged_in": row.logged_in,
        "balance": row.balance,
        "balance_currency": row.balance_currency,
        "tab_url": row.tab_url,
        "tab_open": row.tab_open,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_runner(row: MirrorRunnerState) -> dict:
    return {
        "provider_id": row.provider_id,
        "state": row.state,
        "mode": row.mode,
        "current_arb_group_id": row.current_arb_group_id,
        "current_opp_id": row.current_opp_id,
        "last_idle_reason": row.last_idle_reason,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------- Endpoints ----------


@router.post("/provider-state")
def upsert_provider_state(payload: ProviderStateUpsert, db: Session = Depends(get_db)):
    return _upsert_provider_state(db, payload)


@router.post("/runner-state")
def upsert_runner_state(payload: RunnerStateUpsert, db: Session = Depends(get_db)):
    return _upsert_runner_state(db, payload)


@router.post("/event")
def append_event(payload: EventAppend, db: Session = Depends(get_db)):
    """Append-only — fire-and-forget from the local mirror's broadcaster.

    Failures here MUST NOT break the runner; the local writer should swallow
    HTTP errors. This endpoint returns 200 even on duplicate-event races
    (every event is unique by autoincrement id anyway).
    """
    row = MirrorEventLog(
        provider_id=payload.provider_id,
        event_type=payload.event_type,
        data=payload.data or {},
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "ts": row.ts.isoformat() if row.ts else None}


@router.get("/state")
def get_all_state(db: Session = Depends(get_db)):
    """Bulk fetch — used by the frontend on mount and every 5s.

    Replaces the polling against /mirror/play/status which only knew about
    in-memory runner state and had no replay across browser refresh.
    """
    providers = db.execute(select(MirrorProviderState)).scalars().all()
    runners = db.execute(select(MirrorRunnerState)).scalars().all()
    return {
        "providers": [_serialize_provider(p) for p in providers],
        "runners": [_serialize_runner(r) for r in runners],
    }


@router.get("/state/{provider_id}")
def get_provider_state(provider_id: str, db: Session = Depends(get_db)):
    p = db.get(MirrorProviderState, provider_id)
    r = db.get(MirrorRunnerState, provider_id)
    if not p and not r:
        raise HTTPException(404, f"No state recorded for provider_id={provider_id}")
    return {
        "provider": _serialize_provider(p) if p else None,
        "runner": _serialize_runner(r) if r else None,
    }


@router.get("/events")
def get_events(
    since: str | None = Query(None, description="ISO8601 timestamp; events with ts > since are returned"),
    provider_id: str | None = Query(None),
    limit: int = Query(500, le=2000),
    db: Session = Depends(get_db),
):
    """Replay events for SSE-reconnect gap-filling.

    Frontend tracks the last-seen event ts; on reconnect it fetches events
    since that ts to fill the gap that the ephemeral SSE broadcaster missed.
    """
    q = select(MirrorEventLog)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            q = q.where(MirrorEventLog.ts > since_dt)
        except ValueError:
            raise HTTPException(400, f"since must be ISO8601, got {since!r}") from None
    if provider_id:
        q = q.where(MirrorEventLog.provider_id == provider_id)
    q = q.order_by(MirrorEventLog.ts.asc()).limit(limit)
    rows = db.execute(q).scalars().all()
    return {
        "events": [
            {
                "id": r.id,
                "provider_id": r.provider_id,
                "event_type": r.event_type,
                "data": r.data,
                "ts": r.ts.isoformat() if r.ts else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


# -----------------------------------------------------------------------
# Phase 4 — provider health (auto-generated capability matrix)
#
# Killing the "matrix lies" pitfall: instead of a static markdown matrix
# claiming each provider is ✅ working, derive health from real signals:
#   - last_balance_intercept_at: did the local mirror see a balance API
#     response from this provider in the last 24h?
#   - last_placement_at: did anyone successfully place a bet?
#   - last_provider_skipped_at: did the runner abort?  with what reason?
#   - home_url_status: GET the provider's domain — 200 = green, 5xx = red.
#
# A daily cron (`backend/src/jobs/mirror_smoke.py` — TODO) probes home_urls
# and recomputes from the event log. The endpoint here serves the latest
# snapshot to the frontend; the rebuild script can call it ad-hoc.
# -----------------------------------------------------------------------


def _compute_overall(row: MirrorProviderHealth) -> str:
    """Roll up individual signals into one badge for the matrix."""
    if row.home_url_status == "red":
        return "red"
    if row.last_provider_skipped_at and (
        not row.last_balance_intercept_at or row.last_provider_skipped_at > row.last_balance_intercept_at
    ):
        # Most recent activity was a skip — amber (e.g. login_timeout, no_tab)
        return "amber"
    if row.last_balance_intercept_at:
        return "green"
    return "amber"


def _serialize_health(row: MirrorProviderHealth) -> dict:
    return {
        "provider_id": row.provider_id,
        "home_url_status": row.home_url_status,
        "home_url_http_code": row.home_url_http_code,
        "last_login_detected_at": row.last_login_detected_at.isoformat() if row.last_login_detected_at else None,
        "last_balance_intercept_at": row.last_balance_intercept_at.isoformat()
        if row.last_balance_intercept_at
        else None,
        "last_placement_at": row.last_placement_at.isoformat() if row.last_placement_at else None,
        "last_settled_at": row.last_settled_at.isoformat() if row.last_settled_at else None,
        "last_provider_skipped_at": row.last_provider_skipped_at.isoformat() if row.last_provider_skipped_at else None,
        "last_provider_skipped_reason": row.last_provider_skipped_reason,
        "overall": row.overall,
        "notes": row.notes,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
    }


@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    """Bulk health snapshot for all providers — feeds the frontend §9 matrix."""
    rows = db.execute(select(MirrorProviderHealth)).scalars().all()
    return {"providers": [_serialize_health(r) for r in rows]}


@router.post("/health/recompute")
def recompute_health(db: Session = Depends(get_db)):
    """Recompute every provider's health row from the event log.

    Idempotent. Cheap enough to call ad-hoc from the rebuild script or a
    Phase-4 cron. Walks `mirror_event_log` aggregating last-event-of-each-
    type per provider, plus does NOT do home_url HTTP probes (those need
    outbound network and are scheduled separately by the cron). The
    recompute simply refreshes all event-derived fields.
    """
    from sqlalchemy import func

    # Find each provider that has any event in the log
    pids = (
        db.execute(select(MirrorEventLog.provider_id).where(MirrorEventLog.provider_id.isnot(None)).distinct())
        .scalars()
        .all()
    )
    updated = 0
    for pid in pids:
        # Pull max(ts) for each event_type of interest
        def _last_ts(event_type: str):
            return db.execute(
                select(func.max(MirrorEventLog.ts)).where(
                    MirrorEventLog.provider_id == pid, MirrorEventLog.event_type == event_type
                )
            ).scalar()

        last_login = _last_ts("login_detected")
        last_balance = _last_ts("balance_intercepted")
        last_placement = _last_ts("bet_placed")
        last_settled = _last_ts("settlements_confirmed")
        last_skip = db.execute(
            select(MirrorEventLog)
            .where(MirrorEventLog.provider_id == pid, MirrorEventLog.event_type == "provider_skipped")
            .order_by(MirrorEventLog.ts.desc())
            .limit(1)
        ).scalar_one_or_none()

        existing = db.get(MirrorProviderHealth, pid)
        if existing is None:
            existing = MirrorProviderHealth(provider_id=pid)
            db.add(existing)
        existing.last_login_detected_at = last_login
        existing.last_balance_intercept_at = last_balance
        existing.last_placement_at = last_placement
        existing.last_settled_at = last_settled
        if last_skip:
            existing.last_provider_skipped_at = last_skip.ts
            existing.last_provider_skipped_reason = (last_skip.data or {}).get("reason")
        existing.overall = _compute_overall(existing)
        updated += 1
    db.commit()
    return {"updated": updated}
