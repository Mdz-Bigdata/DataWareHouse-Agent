from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class RelationshipInfoMySQL(Base):
    __tablename__ = "relationship_info"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    build_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(64), index=True)
    source_column: Mapped[str] = mapped_column(String(64))
    target_table: Mapped[str] = mapped_column(String(64), index=True)
    target_column: Mapped[str] = mapped_column(String(64))
    relationship_type: Mapped[str] = mapped_column(String(32), default="many_to_one")
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    physical: Mapped[bool] = mapped_column(Boolean, default=True)
