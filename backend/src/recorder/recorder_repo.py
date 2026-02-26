"""
Recorder Repository — data access for recording sessions and actions.

Follows the same pattern as BetRepo / ProfileRepo.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..db.models import RecordingSession, RecordedAction

logger = logging.getLogger(__name__)


class RecorderRepo:
    """Data access for recording sessions and actions."""

    def __init__(self, db: Session):
        self.db = db

    # ---- Sessions ----

    def create_session(self, **kwargs) -> RecordingSession:
        session = RecordingSession(**kwargs)
        self.db.add(session)
        self.db.flush()
        return session

    def get_session(self, session_id: int) -> RecordingSession | None:
        return self.db.query(RecordingSession).get(session_id)

    def list_sessions(
        self,
        provider_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[RecordingSession]:
        q = self.db.query(RecordingSession)
        if provider_id:
            q = q.filter(RecordingSession.provider_id == provider_id)
        if status:
            q = q.filter(RecordingSession.status == status)
        return q.order_by(RecordingSession.started_at.desc()).limit(limit).all()

    def complete_session(self, session_id: int) -> RecordingSession | None:
        session = self.get_session(session_id)
        if not session:
            return None
        now = datetime.now(timezone.utc)
        session.status = "completed"
        session.ended_at = now
        if session.started_at:
            session.duration_seconds = (now - session.started_at).total_seconds()
        session.action_count = (
            self.db.query(RecordedAction)
            .filter(RecordedAction.session_id == session_id)
            .count()
        )
        self.db.flush()
        return session

    def delete_session(self, session_id: int) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        self.db.delete(session)
        self.db.flush()
        return True

    # ---- Actions ----

    def add_actions(self, session_id: int, actions: list[dict]) -> int:
        """Bulk insert recorded actions. Returns count inserted."""
        objs = []
        for a in actions:
            a["session_id"] = session_id
            objs.append(RecordedAction(**a))
        self.db.bulk_save_objects(objs)
        self.db.flush()
        return len(objs)

    def add_action(self, **kwargs) -> RecordedAction:
        """Insert a single recorded action."""
        action = RecordedAction(**kwargs)
        self.db.add(action)
        self.db.flush()
        return action

    def get_actions(
        self,
        session_id: int,
        action_type: str | None = None,
    ) -> list[RecordedAction]:
        q = self.db.query(RecordedAction).filter(
            RecordedAction.session_id == session_id
        )
        if action_type:
            q = q.filter(RecordedAction.action_type == action_type)
        return q.order_by(RecordedAction.sequence).all()
