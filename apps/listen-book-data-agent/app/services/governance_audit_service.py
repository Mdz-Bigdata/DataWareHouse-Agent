from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.governance_audit_mysql import GovernanceAuditMySQL


class GovernanceAuditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict | None = None,
    ) -> GovernanceAuditMySQL:
        row = GovernanceAuditMySQL(
            id=str(uuid.uuid4()),
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
        self.session.add(row)
        await self.session.commit()
        return row
