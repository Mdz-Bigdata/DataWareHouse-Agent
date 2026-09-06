import asyncio

from sqlalchemy import Result, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dialect import DialectStrategy, get_dialect_strategy
from app.services.explain_budget_service import ExplainEstimate, summarize_explain


class DWMySQlRepository:
    """操作数据仓库dw库（事实表+维度表）持久层。

    Phase 3.2：方法内的方言专属 SQL 已抽离到 DialectStrategy，本类按注入的
    方言策略动态生成 SQL，支持 MySQL / PostgreSQL / ClickHouse / Doris。
    类名保留 DWMySQlRepository 以兼容既有引用（避免大面积改名）。
    """

    def __init__(self, session: AsyncSession, dialect: DialectStrategy | None = None):
        self.session = session
        # 默认 MySQL（兼容既有调用方）；若 session 能推断出方言则用推断结果
        if dialect is not None:
            self.dialect = dialect
        else:
            try:
                engine_dialect = session.get_bind().dialect.name
                self.dialect = get_dialect_strategy(engine_dialect)
            except Exception:
                # 推断失败时退回 MySQL（测试环境常走这里）
                self.dialect = get_dialect_strategy("mysql")

    async def get_column_types_by_table_id(self, table_id: str) -> dict[str, str]:
        """
        根据表ID得到表中每个字段数据类型
        :param table_id:  表id
        :return: 字段数据类型字典
        """
        sql = self.dialect.get_column_types_sql(table_id)
        result: Result = await self.session.execute(text(sql))
        # 统一取前两列作为 (列名, 类型)，兼容 SHOW COLUMNS 与 information_schema 两种结果集
        return {str(row[0]): str(row[1]) for row in result.fetchall()}

    async def get_column_values(self, table_id: str, column_name: str, limit: int = 10):
        """
        根据表ID查询指定字段实例取值
        :param table_id: 表ID
        :param column_name: 字段名称
        :param limit: 限制查询记录数
        :return: 取值列表
        """
        sql = self.dialect.get_distinct_values_sql(table_id, column_name, limit)
        result: Result = await self.session.execute(text(sql))
        # 执行自定义SQL返回结果单列多行
        return result.scalars().fetchall()

    async def get_db_info(self):
        """
        获取数据库信息（方言与版本）
        :return: 数据库信息
        """
        # 方言名取策略的 name（比 SQLAlchemy 推断更准）
        sql = self.dialect.get_version_sql()
        result = await self.session.execute(text(sql))
        return {"version": result.scalar(), "dialect": self.dialect.name}

    async def validate_sql(self, sql: str, timeout_seconds: int) -> ExplainEstimate:
        result = await self._execute_with_timeout(self.dialect.explain_sql(sql), timeout_seconds)
        rows = [dict(row_mapping) for row_mapping in result.mappings().fetchall()]
        return summarize_explain(rows, self.dialect.name)

    async def execute_sql(self, sql: str, timeout_seconds: int):
        if self.session.in_transaction():
            await self.session.rollback()
        await self.dialect.apply_read_only(self.session)
        try:
            result = await self._execute_with_timeout(sql, timeout_seconds)
            return [dict(row_mapping) for row_mapping in result.mappings().fetchall()]
        finally:
            await self.session.rollback()
            await self.dialect.reset_read_only(self.session)
            await self.session.rollback()

    async def _execute_with_timeout(self, sql: str, timeout_seconds: int) -> Result:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds 必须大于 0")
        # 通过方言策略设置会话级超时（各方言机制不同）
        await self.dialect.apply_execution_timeout(self.session, timeout_seconds)
        try:
            return await asyncio.wait_for(self.session.execute(text(sql)), timeout=timeout_seconds)
        finally:
            # 重置超时（MySQL 需要显式重置；PG/CK 的 SET LOCAL/无操作自动失效）
            await self.dialect.reset_execution_timeout(self.session)
