from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class DatasourceMySQL(Base):
    """Phase 3.4：数据源管理 ORM 模型。

    password 字段存储 Fernet 加密后的密文，读取时由 mapper/service 解密。
    active 标记当前激活的数据源（建议同一时刻仅一个 active）。
    """

    __tablename__ = "datasource"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="数据源标识")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="展示名称")
    dialect: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="方言：mysql/postgresql/clickhouse/doris"
    )
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False)
    database: Mapped[str] = mapped_column(String(128), nullable=False)
    user: Mapped[str] = mapped_column(String(128), nullable=False)
    password: Mapped[str] = mapped_column(Text, nullable=False, comment="Fernet 加密密文")
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
