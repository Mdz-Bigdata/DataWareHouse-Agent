"""Doris 方言策略。

Doris 兼容 MySQL 协议，大部分 SQL 语法与 MySQL 一致。
列类型查询用 INFORMATION_SCHEMA（Doris 支持）。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dialect.base import DialectStrategy


class DorisDialect(DialectStrategy):
    """Doris 方言（兼容 MySQL 协议）。"""

    @property
    def name(self) -> str:
        return "doris"

    @property
    def sqlglot_dialect(self) -> str:
        # Doris 语法接近 MySQL，sqlglot 用 mysql 方言解析
        return "mysql"

    @property
    def drivername(self) -> str:
        # Doris 兼容 MySQL 协议，用 MySQL 驱动
        return "mysql+asyncmy"

    def get_column_types_sql(self, table_id: str) -> str:
        # Doris 支持 INFORMATION_SCHEMA.COLUMNS
        parts = table_id.split(".", 1)
        table_name = parts[-1].strip("`")
        database = parts[0].strip("`") if len(parts) > 1 else "default"
        return (
            f"SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_NAME = '{table_name}'"
        )

    def get_distinct_values_sql(self, table_id: str, column_name: str, limit: int) -> str:
        return f"SELECT DISTINCT {column_name} FROM {table_id} LIMIT {limit}"

    def get_version_sql(self) -> str:
        return "SELECT version()"

    def explain_sql(self, sql: str) -> str:
        return f"EXPLAIN {sql}"

    async def apply_execution_timeout(self, session: AsyncSession, timeout_seconds: int) -> None:
        # Doris 兼容 MySQL 的 query_timeout 变量（秒）
        await session.execute(text(f"SET SESSION query_timeout = {timeout_seconds}"))

    async def reset_execution_timeout(self, session: AsyncSession) -> None:
        # 重置为默认值（Doris 默认 query_timeout 较长，这里恢复到一个安全的大值）
        await session.execute(text("SET SESSION query_timeout = 3600"))

    async def apply_read_only(self, session: AsyncSession) -> None:
        await session.execute(text("SET TRANSACTION READ ONLY"))

    async def reset_read_only(self, session: AsyncSession) -> None:
        await session.execute(text("SET SESSION TRANSACTION READ WRITE"))
