"""MySQL 方言策略。

从原 dw_mysql_repository.py 提取的 MySQL 专属 SQL，保持行为一致。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dialect.base import DialectStrategy


class MySQLDialect(DialectStrategy):
    """MySQL 方言。"""

    @property
    def name(self) -> str:
        return "mysql"

    @property
    def sqlglot_dialect(self) -> str:
        return "mysql"

    @property
    def drivername(self) -> str:
        return "mysql+asyncmy"

    def get_column_types_sql(self, table_id: str) -> str:
        # MySQL 的 SHOW COLUMNS 返回 Field/Type 两列，符合接口约定
        return f"SHOW COLUMNS FROM {table_id}"

    def get_distinct_values_sql(self, table_id: str, column_name: str, limit: int) -> str:
        return f"SELECT DISTINCT {column_name} FROM {table_id} LIMIT {limit}"

    def get_version_sql(self) -> str:
        return "SELECT VERSION()"

    def explain_sql(self, sql: str) -> str:
        return f"EXPLAIN {sql}"

    async def apply_execution_timeout(self, session: AsyncSession, timeout_seconds: int) -> None:
        # MySQL 专属：用 MAX_EXECUTION_TIME 限制 SELECT 执行时长（毫秒）
        timeout_ms = timeout_seconds * 1000
        await session.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}"))

    async def reset_execution_timeout(self, session: AsyncSession) -> None:
        # 重置为不限制
        await session.execute(text("SET SESSION MAX_EXECUTION_TIME = 0"))

    async def apply_read_only(self, session: AsyncSession) -> None:
        await session.execute(text("SET TRANSACTION READ ONLY"))

    async def reset_read_only(self, session: AsyncSession) -> None:
        await session.execute(text("SET SESSION TRANSACTION READ WRITE"))
