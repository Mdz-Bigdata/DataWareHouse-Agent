from sqlalchemy import String, Text
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base

class TableInfoMySQL(Base):
    __tablename__ = "table_info"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="表编号"
    )
    build_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        comment="知识库构建编号",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        comment="表名称"
    )
    role: Mapped[str] = mapped_column(
        String(32),
        comment="表类型(fact/dim)"
    )
    description: Mapped[str] = mapped_column(
        Text,
        comment="表描述"
    )
    domain: Mapped[str] = mapped_column(
        String(64),
        default="audio",
        comment="业务域",
    )
    alias: Mapped[list] = mapped_column(
        JSON,
        default=list,
        comment="表别名",
    )
