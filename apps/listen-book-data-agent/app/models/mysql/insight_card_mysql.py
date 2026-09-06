from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


class InsightCardMySQL(Base):
    """Lightweight saved insight metadata; query result rows are never stored."""

    __tablename__ = "insight_card"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer_summary: Mapped[str] = mapped_column(Text)
    sql_template: Mapped[str] = mapped_column(Text)
    parameter_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    chart_spec: Mapped[dict] = mapped_column(JSON)
    version_info: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
