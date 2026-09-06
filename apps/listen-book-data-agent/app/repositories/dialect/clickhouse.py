"""ClickHouse 方言策略。

ClickHouse 适合时序/日志分析场景。无会话级超时设置，依赖 socket timeout。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dialect.base import DialectStrategy


class ClickHouseDialect(DialectStrategy):
    """ClickHouse 方言。"""

    @property
    def name(self) -> str:
        return "clickhouse"

    @property
    def sqlglot_dialect(self) -> str:
        return "clickhouse"

    @property
    def drivername(self) -> str:
        # clickhouse-connect 提供的异步驱动
        return "clickhouse+asynch"

    def get_column_types_sql(self, table_id: str) -> str:
        # system.columns 表存所有列的元信息：name/type
        parts = table_id.split(".", 1)
        table_name = parts[-1].strip("`")
        database = parts[0].strip("`") if len(parts) > 1 else "default"
        return (
            f"SELECT name, type FROM system.columns "
            f"WHERE database = '{database}' AND table = '{table_name}'"
        )

    def get_distinct_values_sql(self, table_id: str, column_name: str, limit: int) -> str:
        return f"SELECT DISTINCT {column_name} FROM {table_id} LIMIT {limit}"

    def get_version_sql(self) -> str:
        return "SELECT version()"

    def explain_sql(self, sql: str) -> str:
        return f"EXPLAIN ESTIMATE {sql}"

    async def apply_execution_timeout(self, session: AsyncSession, timeout_seconds: int) -> None:
        # ClickHouse 无会话级超时设置，超时由驱动 socket 层控制，这里空实现
        pass

    async def reset_execution_timeout(self, session: AsyncSession) -> None:
        pass

    async def apply_read_only(self, session: AsyncSession) -> None:
        await session.execute(text("SET readonly = 2"))

    async def reset_read_only(self, session: AsyncSession) -> None:
        await session.execute(text("SET readonly = 0"))
