from typing import Any, Dict, List, Optional, Tuple
import asyncio
from .base import DataSourceAdapter, SQLSafetyError, materialize_db_value, standardize_items
from .models import LogicalQuery, ResultSet
import logging
import inspect
try:
    import oracledb
except ImportError:
    oracledb = None

import re
import time
from jinja2 import Environment, BaseLoader, Undefined

logger = logging.getLogger(__name__)

class SqlLabUndefined(Undefined):
    def __str__(self): return "NULL"
    def __html__(self): return "NULL"
    def __iter__(self): return iter([])
    def __bool__(self): return False

SQL_LAB_ENV = Environment(loader=BaseLoader(), undefined=SqlLabUndefined)

class OracleAdapter(DataSourceAdapter):
    """Oracle 数据库适配器：封装基于 oracledb 连接池的物理查询、结构探测和安全限制，兼容 Thin (Async) 和 Thick (Sync) 模式"""

    def __init__(self, source_id: int):
        self.source_id = source_id

    async def execute(self, query: LogicalQuery) -> ResultSet:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute 方法。")

    async def execute_summary(self, query: LogicalQuery, agg_fields: List[str] = None) -> Dict[str, Any]:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute_summary 方法。")

    async def _run_query_internal(self, sql: str, params: Dict[str, Any] = None, fetch_all: bool = True) -> Tuple[List[Any], Any]:
        """
        内部辅助方法：执行 SQL 语句，自动兼容异步 (Thin) 和同步 (Thick) 连接池。
        """
        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        params = params or {}
        
        # 判断是 Thin (异步) 还是 Thick (同步)
        is_async = hasattr(pool, 'acquire') and inspect.iscoroutinefunction(pool.acquire)

        if is_async:
            conn = await pool.acquire()
            async with conn:
                async with conn.cursor() as cursor:
                    logger.debug(f"[Oracle Thin/Async] 执行 SQL: {sql[:200]}...")
                    await cursor.execute(sql, params)
                    rows = await cursor.fetchall() if fetch_all else []
                    materialized = [
                        [materialize_db_value(v) for v in row]
                        for row in rows
                    ]
                    return materialized, cursor.description
        else:
            def sync_execute():
                with pool.acquire() as conn:
                    with conn.cursor() as cursor:
                        logger.debug(f"[Oracle Thick/Sync] 执行 SQL: {sql[:200]}...")
                        cursor.execute(sql, params)
                        rows = cursor.fetchall() if fetch_all else []
                        desc = [list(d) for d in cursor.description] if cursor.description else None
                        materialized = [
                            [materialize_db_value(v) for v in row]
                            for row in rows
                        ]
                        return materialized, desc

            return await asyncio.to_thread(sync_execute)

    async def get_tables(self) -> List[Dict[str, str]]:
        """获取当前用户下的表和视图名称（含中文备注）"""
        sql = """
            SELECT t.table_name, t.table_type, NVL(tc.comments, '')
            FROM (
                SELECT table_name, 'TABLE' AS table_type FROM user_tables
                UNION ALL
                SELECT view_name AS table_name, 'VIEW' AS table_type FROM user_views
            ) t
            LEFT JOIN user_tab_comments tc
                ON tc.table_name = t.table_name AND tc.table_type = t.table_type
            ORDER BY t.table_type, t.table_name
        """
        rows, _ = await self._run_query_internal(sql)
        return [
            {"name": row[0], "comment": row[2] or "", "type": row[1]}
            for row in rows
        ]

    async def get_columns(self, table_name: Optional[str] = None, custom_sql: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        """获取表或自定义查询 SQL 的字段列定义"""
        if custom_sql:
            raw_sql = custom_sql.strip().rstrip(";")
            try:
                env = SQL_LAB_ENV
                sql = env.from_string(raw_sql).render(**(params or {}))
            except:
                sql = raw_sql
            final_sql = f"SELECT * FROM ({sql}) \"t\" WHERE ROWNUM <= 0"
            _, description = await self._run_query_internal(final_sql)
            return [{"name": desc[0], "type": str(desc[1]), "comment": ""} for desc in (description or [])]
        else:
            # Oracle 标准元数据表查询
            sql = """
                SELECT c.column_name, c.data_type, cm.comments
                FROM user_tab_columns c
                LEFT JOIN user_col_comments cm ON c.table_name = cm.table_name AND c.column_name = cm.column_name
                WHERE c.table_name = :t
                ORDER BY c.column_id
            """
            rows, _ = await self._run_query_internal(sql, {"t": table_name.upper() if table_name else ""})
            return [{"name": row[0], "type": row[1], "comment": row[2] or ""} for row in rows]

    async def execute_sql(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        执行物理只读 SQL 并返回统一结果格式。
        """
        rows, description = await self._run_query_internal(sql, params)
        columns = [{"name": desc[0], "type": str(desc[1])} for desc in description] if description else []
        return {"columns": columns, "items": standardize_items([list(row) for row in rows])}

    async def preview(
        self,
        sql: str,
        limit: int = 100,
        params: Dict[str, Any] = None,
        offset: int = 0,
        include_total: bool = False,
    ) -> Dict[str, Any]:
        """
        供数据源管理或 SQL Lab 调试的 Preview 接口，强制最大行数、限制超时和只读拦截
        """
        params = params or {}
        limit = min(max(int(limit or 100), 1), 1000)
        
        # 1. 安全过滤检查
        try:
            self._validate_sql_safety(sql)
        except SQLSafetyError as e:
            raise ValueError(str(e))
        
        # 2. Jinja2 渲染处理
        rendered_sql = sql
        if "{{" in sql or "{%" in sql:
            try:
                rendered_sql = SQL_LAB_ENV.from_string(sql).render(**params)
                self._validate_sql_safety(rendered_sql)
            except Exception as e:
                raise ValueError(f"Jinja2 模板渲染失败: {e}")

        # 3. 强制行数保护 (Oracle 11g 兼容模式)
        clean_sql = rendered_sql.strip().rstrip(";")
        sql_upper = clean_sql.upper()
        
        rownum_match = re.search(r"\bROWNUM\s*(<=|<)\s*(\d+)", sql_upper)
        fetch_match = re.search(r"\bFETCH\s+FIRST\s+(\d+)\s+ROWS", sql_upper)
        if rownum_match:
            limit_val = int(rownum_match.group(2))
            final_sql = (
                clean_sql[:rownum_match.start(2)] + str(min(limit_val, limit)) + clean_sql[rownum_match.end(2):]
            )
        elif fetch_match:
            limit_val = int(fetch_match.group(1))
            final_sql = (
                clean_sql[:fetch_match.start(1)] + str(min(limit_val, limit)) + clean_sql[fetch_match.end(1):]
            )
        else:
            final_sql = f"SELECT * FROM ({clean_sql}) WHERE ROWNUM <= {limit}"
        
        start_time = time.perf_counter()
        
        # 4. 参数绑定，仅存在占位符时传递
        execute_params = params if ":" in final_sql else {}
        
        rows, description = await self._run_query_internal(final_sql, execute_params)
        columns = [{"name": desc[0], "type": str(desc[1])} for desc in description] if description else []
        
        return {
            "columns": columns,
            "rows": standardize_items([list(row) for row in rows]),
            "execution_time_ms": (time.perf_counter() - start_time) * 1000,
            "scanned_rows": 0
        }
