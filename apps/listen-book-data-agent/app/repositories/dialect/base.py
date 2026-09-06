"""Phase 3.1：数据源方言策略抽象基类。

每个方言实现本接口，封装该方言下"查询列类型/去重值/版本/EXPLAIN/超时"的差异。
目的：让 DWRepository 和 sql_guard 不再硬编码 MySQL，支持多数据源接入。

新增方言只需：继承 DialectStrategy + 实现各方法 + 在 get_dialect_strategy 注册。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class DialectStrategy(ABC):
    """数据源方言策略。每个方言一个实现，封装 SQL 与会话级配置差异。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """方言标识，如 mysql / postgresql / clickhouse / doris。"""

    @property
    @abstractmethod
    def sqlglot_dialect(self) -> str:
        """sqlglot 解析与生成 SQL 时使用的方言名。"""

    @property
    @abstractmethod
    def drivername(self) -> str:
        """SQLAlchemy 异步连接的 drivername，如 mysql+asyncmy / postgresql+asyncpg。"""

    @abstractmethod
    def get_column_types_sql(self, table_id: str) -> str:
        """查询指定表所有列名与类型的 SQL，返回形如 {(列名, 类型)} 的结果集。

        结果集必须有两列：第一列为列名，第二列为类型字符串。
        """

    @abstractmethod
    def get_distinct_values_sql(self, table_id: str, column_name: str, limit: int) -> str:
        """查询指定列去重值的 SQL，返回单列结果集。"""

    @abstractmethod
    def get_version_sql(self) -> str:
        """查询数据库版本号的 SQL，返回单行单列。"""

    @abstractmethod
    def explain_sql(self, sql: str) -> str:
        """构造 EXPLAIN 语句（各方言语法不同）。"""

    @abstractmethod
    async def apply_execution_timeout(self, session, timeout_seconds: int) -> None:
        """在会话上设置查询超时（方言差异最大处）。

        MySQL: SET SESSION MAX_EXECUTION_TIME
        PostgreSQL: SET LOCAL statement_timeout
        ClickHouse: 不支持会话级，靠 socket timeout
        Doris: 同 MySQL（Doris 兼容 MySQL 协议）
        """

    async def reset_execution_timeout(self, session) -> None:  # noqa: B027
        """重置查询超时为默认值（apply 的逆操作）。

        默认空实现——大多数方言在会话结束后自动重置。
        MySQL 需要显式重置（MAX_EXECUTION_TIME=0），故覆写。
        """

    @abstractmethod
    async def apply_read_only(self, session) -> None:
        """Make the next/current query transaction read-only at database level."""

    async def reset_read_only(self, session) -> None:  # noqa: B027
        """Restore the session default after the read-only transaction ends."""


def get_dialect_strategy(dialect: str) -> DialectStrategy:
    """根据方言名获取策略实例（工厂方法）。

    支持的方言：mysql / postgresql / clickhouse / doris。
    未知方言抛 ValueError，fail-fast 避免静默用错方言。
    """

    # 延迟 import 避免循环依赖
    from app.repositories.dialect.clickhouse import ClickHouseDialect
    from app.repositories.dialect.doris import DorisDialect
    from app.repositories.dialect.mysql import MySQLDialect
    from app.repositories.dialect.postgresql import PostgresDialect

    strategies: dict[str, type[DialectStrategy]] = {
        "mysql": MySQLDialect,
        "postgresql": PostgresDialect,
        "clickhouse": ClickHouseDialect,
        "doris": DorisDialect,
    }
    key = dialect.lower()
    if key not in strategies:
        raise ValueError(f"不支持的数据库方言：{dialect}（支持：{', '.join(strategies.keys())}）")
    return strategies[key]()
