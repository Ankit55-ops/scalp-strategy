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
        # Risk decisions are system-generated (risk engine / paper trader); the
        # actor is the engine itself, not the strategy. strategy_id stays in the
        # payload for traceability instead of being mislabeled as an actor.
        return self.record(
            workspace_id=workspace_id,
            actor_id=None,
            action="risk_decision",
            resource_type="order",
            resource_id=audit.get("correlation_id"),
            payload={**audit, "source": "risk_engine"},
        )
