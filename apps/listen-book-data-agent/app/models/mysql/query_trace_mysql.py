from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class QueryTraceMySQL(Base):
    """Persistent request-level audit metadata. Query result rows are never stored."""

    __tablename__ = "query_trace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    query_text: Mapped[str] = mapped_column(Text)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    parent_trace_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    regenerate_of_trace_id: Mapped[str | None] = mapped_column(
        String(36), index=True, nullable=True
    )
    standalone_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_plan_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answer_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    build_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    semantic_release_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    semantic_release_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query_set_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    query_set_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_rule_set_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    business_rule_set_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_admin_bypass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class QueryTracePhaseMySQL(Base):
    """One entry per emitted agent phase, without any query result payload."""

    __tablename__ = "query_trace_phase"

    trace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    step: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24))
    duration_ms: Mapped[int] = mapped_column(Integer)
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
