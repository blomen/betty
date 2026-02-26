"""
Recorder API — Chrome session recording for bookmaker navigation pathways.

Endpoints:
  POST   /api/recorder/sessions           — Start a new recording session
  POST   /api/recorder/sessions/{id}/stop  — Stop an active session
  GET    /api/recorder/sessions           — List saved sessions
  GET    /api/recorder/sessions/{id}      — Get session details
  GET    /api/recorder/sessions/{id}/actions — Get actions for a session
  DELETE /api/recorder/sessions/{id}      — Delete a session
  GET    /api/recorder/status             — Get current recording status
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db.models import get_session as get_db_session
from ...recorder.recorder_repo import RecorderRepo
from ...recorder.recorder_service import get_recorder_service
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recorder", tags=["recorder"])


# ---- Schemas ----

VALID_WORKFLOW_TYPES = {
    "place_bet",
    "my_bets",
    "bet_history",
    "check_balance",
    "deposit",
    "withdraw",
    "navigate",
    "view_score",
}


class StartRecordingRequest(BaseModel):
    cdp_url: str = "http://localhost:9222"
    action_type: str = "place_bet"
    label: Optional[str] = None


# ---- Helpers ----

def _get_repo(db: Session = Depends(get_db)) -> RecorderRepo:
    return RecorderRepo(db)


def _session_to_dict(s) -> dict:
    return {
        "id": s.id,
        "provider_id": s.provider_id,
        "action_type": s.action_type,
        "label": s.label,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "duration_seconds": s.duration_seconds,
        "action_count": s.action_count,
        "cdp_url": s.cdp_url,
        "status": s.status,
        "notes": s.notes,
    }


def _action_to_dict(a) -> dict:
    return {
        "id": a.id,
        "session_id": a.session_id,
        "action_type": a.action_type,
        "timestamp": a.timestamp.isoformat() if a.timestamp else None,
        "sequence": a.sequence,
        "url": a.url,
        "page_title": a.page_title,
        "provider_id": a.provider_id,
        "css_selector": a.css_selector,
        "xpath": a.xpath,
        "element_tag": a.element_tag,
        "element_text": a.element_text,
        "element_id": a.element_id,
        "element_class": a.element_class,
        "x": a.x,
        "y": a.y,
        "viewport_width": a.viewport_width,
        "viewport_height": a.viewport_height,
        "input_value": a.input_value,
        "input_type": a.input_type,
        "request_method": a.request_method,
        "request_url": a.request_url,
        "response_status": a.response_status,
        "meta": a.meta,
    }


# ---- Endpoints ----

@router.post("/sessions")
async def start_recording(req: StartRecordingRequest, repo: RecorderRepo = Depends(_get_repo)):
    """Start a new recording session — connects to Chrome via CDP."""
    if req.action_type not in VALID_WORKFLOW_TYPES:
        raise HTTPException(400, f"Invalid action_type '{req.action_type}'. Must be one of: {', '.join(sorted(VALID_WORKFLOW_TYPES))}")

    service = get_recorder_service()

    if service.is_recording:
        raise HTTPException(400, "Already recording. Stop the current session first.")

    # Create DB session record
    session = repo.create_session(
        action_type=req.action_type,
        label=req.label,
        cdp_url=req.cdp_url,
        status="recording",
    )

    # Wire up DB flush callback
    def make_flush_callback(session_id: int):
        async def flush(actions: list[dict]):
            db = get_db_session()
            try:
                r = RecorderRepo(db)
                r.add_actions(session_id, actions)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to flush recording actions: {e}")
            finally:
                db.close()
        return flush

    service._db_flush_callback = make_flush_callback(session.id)

    # Wire up WebSocket broadcast
    from ..state import recorder_ws_manager
    service._ws_broadcast = recorder_ws_manager.broadcast

    # Connect to Chrome and start capturing
    try:
        await service.start_recording(req.cdp_url, session.id)
    except Exception as e:
        # Clean up the DB record
        session.status = "abandoned"
        logger.error(f"Failed to start recording: {e}")
        raise HTTPException(502, f"Could not connect to Chrome: {e}")

    return {"session_id": session.id, "status": "recording"}


@router.post("/sessions/{session_id}/stop")
async def stop_recording(session_id: int, repo: RecorderRepo = Depends(_get_repo)):
    """Stop the active recording session."""
    service = get_recorder_service()

    if not service.is_recording or service.current_session_id != session_id:
        raise HTTPException(400, f"Session {session_id} is not actively recording.")

    result = await service.stop_recording()

    # Finalize DB record
    session = repo.complete_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found.")

    return {"success": True, "session": _session_to_dict(session)}


@router.get("/sessions")
async def list_sessions(
    provider_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    repo: RecorderRepo = Depends(_get_repo),
):
    """List saved recording sessions."""
    sessions = repo.list_sessions(provider_id=provider_id, status=status, limit=limit)
    return {
        "sessions": [_session_to_dict(s) for s in sessions],
        "count": len(sessions),
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: int, repo: RecorderRepo = Depends(_get_repo)):
    """Get details for a specific session."""
    session = repo.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found.")
    return _session_to_dict(session)


@router.get("/sessions/{session_id}/actions")
async def get_session_actions(
    session_id: int,
    action_type: Optional[str] = None,
    repo: RecorderRepo = Depends(_get_repo),
):
    """Get all actions for a recording session."""
    session = repo.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found.")

    actions = repo.get_actions(session_id, action_type=action_type)
    return {
        "actions": [_action_to_dict(a) for a in actions],
        "count": len(actions),
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, repo: RecorderRepo = Depends(_get_repo)):
    """Delete a recording session and its actions."""
    deleted = repo.delete_session(session_id)
    if not deleted:
        raise HTTPException(404, f"Session {session_id} not found.")
    return {"success": True}


@router.get("/status")
async def get_status():
    """Get current recording status."""
    service = get_recorder_service()
    return service.get_status()
