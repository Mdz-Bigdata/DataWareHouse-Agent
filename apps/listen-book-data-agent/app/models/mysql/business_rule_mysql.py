from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class BusinessRuleRevisionMySQL(Base):
    __tablename__ = "business_rule_revision"
    __table_args__ = (
        UniqueConstraint(
            "rule_key",
            "domain",
            "datasource",
            "version",
            name="uq_business_rule_scope_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    rule_type: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    datasource: Mapped[str] = mapped_column(String(128), index=True)
    intents: Mapped[list[str]] = mapped_column(JSON, default=list)
    semantic_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(24), index=True, default="draft")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
