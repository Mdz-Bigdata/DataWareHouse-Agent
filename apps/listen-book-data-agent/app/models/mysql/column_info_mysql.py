from sqlalchemy import Boolean, String, Text
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class ColumnInfoMySQL(Base):
    __tablename__ = "column_info"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="列编号"
    )
    build_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        comment="知识库构建编号",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        comment="列名称"
    )
    type: Mapped[str] = mapped_column(
        String(64),
        comment="数据类型"
    )
    role: Mapped[str] = mapped_column(
        String(32),
        comment="列类型(primary_key,foreign_key,measure,dimension)"
    )
    examples: Mapped[list] = mapped_column(
        JSON,
        comment="数据示例"
    )
    description: Mapped[str] = mapped_column(
        Text,
        comment="列描述"
    )
    alias: Mapped[list] = mapped_column(
        JSON,
        comment="列别名"
    )
    table_id: Mapped[str] = mapped_column(
        String(64),
        comment="所属表编号"
    )
    nullable: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        comment="是否允许为空",
    )
    sensitive: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="是否为禁止问数的敏感字段",
    )
    sync: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="是否同步枚举值到全文索引",
    )
    enum_values: Mapped[list] = mapped_column(
        JSON,
        default=list,
        comment="业务枚举值",
    )
