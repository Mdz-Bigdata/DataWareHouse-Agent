import logging
import sqlparse
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List
from .models import LogicalQuery, ResultSet

logger = logging.getLogger(__name__)

class SQLSafetyError(ValueError):
    """SQL 安全策略校验异常"""
    pass

class DataSourceAdapter(ABC):
    """数据源适配器基类：提供统一的安全过滤与只读 SQL 校验逻辑"""
    
    # 本地执行仅允许只读查询/元信息/执行计划语句，拒绝任何数据变更与结构修改。
    ALLOWED_SQL_KEYWORDS = {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC"}
    SELECT_LIKE_KEYWORDS = {"SELECT", "WITH"}

    @abstractmethod
    async def execute(self, query: LogicalQuery) -> ResultSet:
        """执行逻辑查询并返回标准化结果集"""
        pass
    
    @abstractmethod
    async def execute_summary(self, query: LogicalQuery) -> Dict[str, Any]:
        """执行聚合逻辑查询"""
        pass

    @abstractmethod
    async def execute_sql(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行物理只读 SQL 并返回统一格式的字段和行数据"""
        pass

    def _validate_sql_safety(self, sql: str) -> None:
        """
        利用 sqlparse 语法树解析对 SQL 语句进行深度安全审计。
        严格确保仅允许单条只读 SELECT 语句，禁止多语句及任何变更操作。
        """
        try:
            # 1. 清理注释与首尾空格
            formatted_sql = sqlparse.format(sql, strip_comments=True).strip()
            if not formatted_sql:
                raise SQLSafetyError("SQL 语句不能为空")
                
            parsed = sqlparse.parse(formatted_sql)
            if not parsed:
                raise SQLSafetyError("SQL 语法解析失败")
            
            # 2. 禁止多条语句执行（防堆叠注入）
            if len(parsed) > 1:
                raise SQLSafetyError("安全策略违规：禁止同时执行多条 SQL 语句")
            
            statement = parsed[0]
            stmt_type = statement.get_type()
            
            # 3. 严格拦截任何写操作与数据变更
            if stmt_type in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE"):
                raise SQLSafetyError(f"安全策略违规：禁止执行数据变更或结构修改操作 '{stmt_type}'")
            
            # 4. 深度检测第一个 Token 关键字，确保属于白名单
            first_token = statement.token_first(skip_cm=True, skip_ws=True)
            if not first_token:
                raise SQLSafetyError("SQL 语句解析出空 Token")
                
            keyword = first_token.value.upper()
            if keyword not in self.ALLOWED_SQL_KEYWORDS:
                raise SQLSafetyError(f"安全策略违规：禁止执行 '{keyword}' 指令。本地模式仅允许执行只读 SELECT 查询")

            # 5. 再次防御性核对 sqlparse 解析出的类型
            if keyword in self.SELECT_LIKE_KEYWORDS and stmt_type != "SELECT":
                # 即使首词是 SELECT/WITH 但被 sqlparse 识别为其他非法结构
                raise SQLSafetyError("安全策略违规：非法的 SQL 查询结构")
                
            return
            
        except SQLSafetyError:
            raise
        except Exception as e:
            logger.error(f"SQL 安全性审计发生异常: {e}")
            raise SQLSafetyError(f"安全策略异常，无法审计该 SQL 的安全性: {str(e)}")


def materialize_db_value(val: Any) -> Any:
    """在数据库连接关闭前将 LOB/二进制等延迟读取类型转为 Python 原生值。"""
    if val is None:
        return None
    read_fn = getattr(val, "read", None)
    if callable(read_fn):
        try:
            content = read_fn()
            if isinstance(content, bytes):
                return f"<binary {len(content)} bytes>"
            return content
        except Exception:
            return str(val)
    if isinstance(val, (bytes, bytearray, memoryview)):
        return f"<binary {len(val)} bytes>"
    return val


def standardize_value(val: Any) -> Any:
    import datetime
    from decimal import Decimal
    val = materialize_db_value(val)
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    return val


def standardize_items(items: List[List[Any]]) -> List[List[Any]]:
    return [
        [standardize_value(x) for x in row]
        for row in items
    ]
