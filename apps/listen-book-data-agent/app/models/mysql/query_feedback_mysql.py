from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class QueryFeedbackMySQL(Base):
    """One immutable user verdict per owned trace; query rows are never stored."""

    __tablename__ = "query_feedback"
    __table_args__ = (UniqueConstraint("trace_id", name="uq_query_feedback_trace"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    reasons: Mapped[list[str]] = mapped_column(JSON)
    comment: Mapped[str] = mapped_column(Text, default="")
    template_signature: Mapped[str] = mapped_column(String(64), index=True)
    candidate_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class QueryTemplateConfidenceMySQL(Base):
    """Positive-only confidence aggregate for a redacted, parameterized SQL template."""

    __tablename__ = "query_template_confidence"

    template_signature: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    datasource: Mapped[str] = mapped_column(String(128), index=True)
    sql_template: Mapped[str] = mapped_column(Text)
    parameter_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    positive_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_trace_id: Mapped[str] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
