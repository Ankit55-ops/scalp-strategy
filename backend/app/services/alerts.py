"""Alert service: creates user-facing notices for risk/system events."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Alert


class AlertService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        workspace_id: str,
        title: str,
        message: str | None = None,
        level: str = "info",
    ) -> Alert:
        alert = Alert(
            workspace_id=workspace_id,
            title=title,
            message=message,
            level=level,
            is_read=False,
        )
        self.db.add(alert)
        self.db.commit()
        return alert

    def unread_count(self, workspace_id: str) -> int:
        return (
            self.db.query(Alert)
            .filter(Alert.workspace_id == workspace_id, Alert.is_read.is_(False))
            .count()
        )