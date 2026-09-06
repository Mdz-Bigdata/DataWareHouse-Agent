from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class GovernanceAuditMySQL(Base):
    __tablename__ = "governance_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str] = mapped_column(String(128), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
