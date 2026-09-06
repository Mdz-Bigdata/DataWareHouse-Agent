from typing import Any, Dict, List, Optional
from .base import DataSourceAdapter, SQLSafetyError, standardize_items
from .models import LogicalQuery, ResultSet
import aiomysql
import logging
import time
import re

logger = logging.getLogger(__name__)

# --- Jinja2 Environment for preview and column fetching ---
from jinja2 import Environment, BaseLoader, Undefined

class SqlLabUndefined(Undefined):
    def __str__(self): return "NULL"
    def __html__(self): return "NULL"
    def __iter__(self): return iter([])
    def __bool__(self): return False

SQL_LAB_ENV = Environment(loader=BaseLoader(), undefined=SqlLabUndefined)

class MySQLAdapter(DataSourceAdapter):
    """MySQL 数据库适配器：封装基于 aiomysql 连接池的物理查询、结构探测和安全限制"""

    def __init__(self, source_id: int):
        self.source_id = source_id

    async def execute(self, query: LogicalQuery) -> ResultSet:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute 方法。")

    async def execute_summary(self, query: LogicalQuery, agg_fields: List[str] = None) -> Dict[str, Any]:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute_summary 方法。")

    async def get_tables(self) -> List[Dict[str, str]]:
        """获取当前库下的所有表和视图名称（含中文备注）"""
        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)

        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT TABLE_NAME, IFNULL(TABLE_COMMENT, ''), TABLE_TYPE
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = DATABASE()
                    ORDER BY TABLE_NAME
                    """
                )
                rows = await cursor.fetchall()

        return [
            {
                "name": row[0],
                "comment": row[1] or "",
                "type": "VIEW" if "VIEW" in str(row[2]).upper() else "TABLE",
            }
            for row in rows
        ]

    async def get_columns(self, table_name: Optional[str] = None, custom_sql: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        """获取表或自定义查询 SQL 的字段列定义"""
        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        
        if custom_sql:
            raw_sql = custom_sql.strip()
            if raw_sql.endswith(";"):
                raw_sql = raw_sql[:-1]
                
            try:
                env = SQL_LAB_ENV
                template = env.from_string(raw_sql)
                render_ctx = params if params is not None else {}
                sql = template.render(**render_ctx)
            except Exception as e:
                logger.warning(f"Jinja2 模板渲染异常，回退使用原始 SQL: {e}")
                sql = raw_sql
                
            final_sql = f"SELECT * FROM ({sql}) as t LIMIT 0"
        elif table_name:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT COLUMN_NAME, DATA_TYPE, IFNULL(COLUMN_COMMENT, '')
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                        ORDER BY ORDINAL_POSITION
                        """,
                        (table_name,),
                    )
                    rows = await cursor.fetchall()
            return [
                {"name": row[0], "type": row[1] or "String", "comment": row[2] or ""}
                for row in rows
            ]
        else:
            return []

        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(final_sql)
                    columns = [desc[0] for desc in cursor.description]
                except Exception as e:
                    logger.error(f"MySQL 获取字段信息失败: {e}. SQL: {final_sql}")
                    raise ValueError(f"不合法的 SQL 语句或数据表: {e}")

        return [{"name": col, "type": "String", "comment": ""} for col in columns]

    async def execute_sql(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        执行物理只读 SQL 并返回统一的结果格式。
        """
        from app.services.pool_manager import DataSourcePoolManager
        
        if params is not None and not params:
            params = None
            
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        
        async with pool.acquire() as conn:
            # 使用默认 Tuple Cursor，保持字段返回的列顺序与描述顺序一致
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
                
                columns = []
                if cursor.description:
                    for desc in cursor.description:
                        # desc[0] 是字段名，desc[1] 是类型码
                        columns.append({"name": desc[0], "type": str(desc[1])})
                
                items = [list(row) for row in rows]
                
                return {
                    "columns": columns,
                    "items": standardize_items(items)
                }

    async def preview(
        self,
        sql: str,
        limit: int = 100,
        params: Dict[str, Any] = None,
        offset: int = 0,
        include_total: bool = False,
    ) -> Dict[str, Any]:
        """
        供数据源管理或 SQL Lab 调试的 Preview 接口，强制最大行数和只读拦截
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
                template = SQL_LAB_ENV.from_string(sql)
                rendered_sql = template.render(**params)
                self._validate_sql_safety(rendered_sql)
            except SQLSafetyError as e:
                raise ValueError(str(e))
            except Exception as e:
                raise ValueError(f"Jinja2 模板渲染失败: {str(e)}")

        # 3. 强制行数保护
        clean_sql = rendered_sql.strip().rstrip(";")
        limit_match = re.search(r"\bLIMIT\s+(\d+)", clean_sql, re.IGNORECASE)
        if limit_match:
            limit_val = int(limit_match.group(1))
            final_sql = (
                clean_sql[:limit_match.start(1)] + str(min(limit_val, limit)) + clean_sql[limit_match.end(1):]
            )
        else:
            final_sql = f"SELECT * FROM ({clean_sql}) AS _preview_sub LIMIT {limit}"
            
        start_time = time.perf_counter()
        
        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        
        total_count = None
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                if include_total and not limit_match:
                    count_sql = f"SELECT COUNT(*) AS _cnt FROM ({clean_sql}) AS _preview_cnt"
                    await cursor.execute(count_sql)
                    count_row = await cursor.fetchone()
                    total_count = int(count_row[0]) if count_row else 0

                logger.info(f"[MySQL Executor] 执行预览 SQL: {final_sql}")
                await cursor.execute(final_sql)
                rows = await cursor.fetchall()
                
                columns = []
                if cursor.description:
                    for desc in cursor.description:
                         columns.append({"name": desc[0], "type": str(desc[1])})
                
                items = [list(row) for row in rows]
                
        execution_time = (time.perf_counter() - start_time) * 1000
        
        result = {
            "columns": columns,
            "rows": standardize_items(items),
            "execution_time_ms": execution_time,
            "scanned_rows": 0,
            "offset": offset,
            "limit": limit,
        }
        if total_count is not None:
            result["total_count"] = total_count
        return result
