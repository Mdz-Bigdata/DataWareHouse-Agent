from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class ColumnMetricMySQL(Base):
    __tablename__ = "column_metric"

    column_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="列编号"
    )
    metric_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="指标编号"
    )
    build_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        comment="知识库构建编号",
    )
