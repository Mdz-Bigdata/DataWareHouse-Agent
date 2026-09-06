from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class BusinessRuleSetVersionMySQL(Base):
    """Append-only snapshot of the rule revisions selected for a release."""

    __tablename__ = "business_rule_set_version"
    __table_args__ = (
        UniqueConstraint(
            "domain",
            "datasource",
            "version",
            name="uq_business_rule_set_scope_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    datasource: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    manifest: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )


class SemanticReleaseMySQL(Base):
    """Immutable release binding schema, trusted queries and typed rules."""

    __tablename__ = "semantic_release"
    __table_args__ = (
        UniqueConstraint(
            "domain",
            "datasource",
            "version",
            name="uq_semantic_release_scope_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    datasource: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    release_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    knowledge_build_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    query_set_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    business_rule_set_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )
    source_release_id: Mapped[str | None] = mapped_column(
        String(36), index=True, nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )


class ActiveSemanticReleaseMySQL(Base):
    __tablename__ = "active_semantic_release"

    domain: Mapped[str] = mapped_column(String(64), primary_key=True)
    datasource: Mapped[str] = mapped_column(String(128), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def _reject_snapshot_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"语义发布快照不可修改或删除：{target.id}")


for _immutable_model in (BusinessRuleSetVersionMySQL, SemanticReleaseMySQL):
    event.listen(_immutable_model, "before_update", _reject_snapshot_mutation)
    event.listen(_immutable_model, "before_delete", _reject_snapshot_mutation)
