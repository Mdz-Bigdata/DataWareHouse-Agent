"""PostgreSQL 方言策略。

列类型查询用 information_schema（标准 SQL，跨 PG 版本兼容）。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dialect.base import DialectStrategy


class PostgresDialect(DialectStrategy):
    """PostgreSQL 方言。"""

    @property
    def name(self) -> str:
        return "postgresql"

    @property
    def sqlglot_dialect(self) -> str:
        return "postgres"

    @property
    def drivername(self) -> str:
        return "postgresql+asyncpg"

    def get_column_types_sql(self, table_id: str) -> str:
        # table_id 形如 "schema.table" 或 "table"；拆分 schema
        parts = table_id.split(".", 1)
        table_name = parts[-1].strip('"')
        schema_name = parts[0].strip('"') if len(parts) > 1 else "public"
        # information_schema.columns 返回 column_name/data_type，符合接口约定
        return (
            f"SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'"
        )

    def get_distinct_values_sql(self, table_id: str, column_name: str, limit: int) -> str:
        return f"SELECT DISTINCT {column_name} FROM {table_id} LIMIT {limit}"

    def get_version_sql(self) -> str:
        return "SELECT version()"

    def explain_sql(self, sql: str) -> str:
        return f"EXPLAIN {sql}"

    async def apply_execution_timeout(self, session: AsyncSession, timeout_seconds: int) -> None:
        # PG：用 statement_timeout 限制单条语句执行时长（毫秒）
        await session.execute(text(f"SET LOCAL statement_timeout = {timeout_seconds * 1000}"))

    async def reset_execution_timeout(self, session: AsyncSession) -> None:
        # SET LOCAL 仅在事务内生效，事务结束自动重置，无需显式恢复
        pass

    async def apply_read_only(self, session: AsyncSession) -> None:
        await session.execute(text("SET TRANSACTION READ ONLY"))
