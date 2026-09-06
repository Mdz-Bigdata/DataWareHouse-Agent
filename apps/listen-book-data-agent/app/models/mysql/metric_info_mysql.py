from sqlalchemy import String, Text
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class MetricInfoMySQL(Base):
    __tablename__ = "metric_info"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="指标编码"
    )
    build_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        comment="知识库构建编号",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        comment="指标名称"
    )
    description: Mapped[str] = mapped_column(
        Text,
        comment="指标描述"
    )
    relevant_columns: Mapped[list] = mapped_column(
        JSON,
        comment="关联字段"
    )
    alias: Mapped[list] = mapped_column(
        JSON,
        comment="指标别名"
    )
    formula: Mapped[str] = mapped_column(
        Text,
        default="",
        comment="指标SQL公式",
    )
    filters: Mapped[list] = mapped_column(
        JSON,
        default=list,
        comment="固定业务过滤条件",
    )
    time_column: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="默认时间字段",
    )
    unit: Mapped[str] = mapped_column(
        String(32),
        default="count",
        comment="指标单位",
    )
    currency_column: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="金额指标币种字段",
    )
    dimensions: Mapped[list] = mapped_column(
        JSON,
        default=list,
        comment="推荐分析维度",
    )
    snapshot: Mapped[bool] = mapped_column(
        default=False,
        comment="是否为当前快照指标",
    )
