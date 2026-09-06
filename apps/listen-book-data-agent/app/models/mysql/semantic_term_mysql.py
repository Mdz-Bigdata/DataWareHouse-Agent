from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class SemanticTermMySQL(Base):
    """A versioned business term scoped to one domain and datasource."""

    __tablename__ = "semantic_term"
    __table_args__ = (
        UniqueConstraint(
            "term_key",
            "domain",
            "datasource",
            "version",
            name="uq_semantic_term_scope_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    term_key: Mapped[str] = mapped_column(String(64), index=True)
    standard_term: Mapped[str] = mapped_column(String(128), index=True)
    synonyms: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    bindings: Mapped[list[dict]] = mapped_column(JSON, default=list)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    datasource: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True, default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
