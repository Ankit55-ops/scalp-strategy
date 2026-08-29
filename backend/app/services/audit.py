"""Audit logging: immutable records for risk decisions and privileged actions."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import AuditLog

logger = logging.getLogger("fxscalper.audit")


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        workspace_id: str | None,
        actor_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        payload: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            immutable=True,
            payload=payload,
        )
        self.db.add(entry)
        self.db.commit()
        logger.info(
            "audit",
            extra={
                "extra_fields": {
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "actor": actor_id,
                }
            },
        )
        return entry

    def record_risk_decision(self, workspace_id: str | None, audit: dict) -> AuditLog:
        return self.record(
            workspace_id=workspace_id,
            actor_id=audit.get("strategy_id"),
            action="risk_decision",
            resource_type="order",
            resource_id=audit.get("correlation_id"),
            payload=audit,
        )
