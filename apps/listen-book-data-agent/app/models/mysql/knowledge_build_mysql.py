from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class KnowledgeBuildMySQL(Base):
    __tablename__ = "knowledge_build"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    column_collection: Mapped[str] = mapped_column(String(160))
    metric_collection: Mapped[str] = mapped_column(String(160))
    value_index: Mapped[str] = mapped_column(String(160))
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    column_count: Mapped[int] = mapped_column(Integer, default=0)
    metric_count: Mapped[int] = mapped_column(Integer, default=0)
    relationship_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActiveKnowledgeBuildMySQL(Base):
    __tablename__ = "active_knowledge_build"

    domain: Mapped[str] = mapped_column(String(64), primary_key=True)
    build_id: Mapped[str] = mapped_column(String(36), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class KnowledgeBuildValidationMySQL(Base):
    """Immutable Golden Suite result captured before a build can be activated."""

    __tablename__ = "knowledge_build_validation"

    build_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    semantic_accuracy: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_semantic_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    safety_accuracy: Mapped[float] = mapped_column(Float, nullable=False)
    p95_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_p95_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    report: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )
