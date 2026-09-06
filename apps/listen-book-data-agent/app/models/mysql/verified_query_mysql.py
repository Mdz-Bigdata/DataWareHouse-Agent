from datetime import datetime

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class VerifiedQueryRevisionMySQL(Base):
    __tablename__ = "verified_query_revision"
    __table_args__ = (
        UniqueConstraint(
            "case_key",
            "domain",
            "datasource",
            "revision",
            name="uq_verified_query_scope_revision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_key: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    datasource: Mapped[str] = mapped_column(String(128), index=True)
    question: Mapped[str] = mapped_column(Text)
    dialect: Mapped[str] = mapped_column(String(32))
    sql_template: Mapped[str] = mapped_column(Text)
    parameter_schema: Mapped[list[dict]] = mapped_column(JSON, default=list)
    expected_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    expected_metrics: Mapped[list[str]] = mapped_column(JSON, default=list)
    assertions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    source_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    lifecycle: Mapped[str] = mapped_column(String(24), index=True, default="candidate")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class QuerySetVersionMySQL(Base):
    """An append-only published manifest of reviewed query revisions."""

    __tablename__ = "query_set_version"
    __table_args__ = (
        UniqueConstraint(
            "domain",
            "datasource",
            "version",
            name="uq_query_set_scope_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(String(64))
    domain: Mapped[str] = mapped_column(String(64), index=True)
    datasource: Mapped[str] = mapped_column(String(128), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    manifest: Mapped[list[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="published", nullable=False)
    created_by: Mapped[str] = mapped_column(String(36))
    reviewer_id: Mapped[str] = mapped_column(String(36))
    published_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class QuerySetCaseMySQL(Base):
    __tablename__ = "query_set_case"

    query_set_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    verified_revision_id: Mapped[str] = mapped_column(String(36), index=True)


def _reject_query_set_mutation(_mapper, _connection, target) -> None:
    raise ValueError(
        f"Query Set 快照不可修改或删除：{target.query_set_id if hasattr(target, 'query_set_id') else target.id}"
    )


for _immutable_model in (QuerySetVersionMySQL, QuerySetCaseMySQL):
    event.listen(_immutable_model, "before_update", _reject_query_set_mutation)
    event.listen(_immutable_model, "before_delete", _reject_query_set_mutation)
