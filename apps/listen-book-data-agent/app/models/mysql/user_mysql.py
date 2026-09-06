from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class UserMySQL(Base):
    """系统用户。password_hash 仅存 PBKDF2 校验串，role 仅 admin / user 两档。

    data_scope：访问策略 JSON。旧版可使用多维度约束列表：
        [{"column": "region", "value": "华东"}, {"column": "category", "value": "audio"}]
    含义：该用户查询时，SQL 自动注入这些列的等值过滤，实现行级数据隔离。
    普通用户 null/空/非法时拒绝查询；admin 通过显式审计的策略上下文绕过。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 行级权限/ACL 策略。仅 admin 允许 null。
    data_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
