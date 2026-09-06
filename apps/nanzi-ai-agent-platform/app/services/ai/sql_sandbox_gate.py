import logging
import re
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from app.services.ai.tools.data_api import call_external_sql_api

logger = logging.getLogger(__name__)

# 最大安全预估扫描行数：500万行
MAX_SAFE_ESTIMATED_ROWS = 5000000

@dataclass
class SQLSandboxResult:
    allowed: bool
    message: Optional[str] = None
    optimized_sql: str = ""

class SQLSandboxGate:
    """
    SQL 性能沙箱网关（与权威只读/安全校验 validate_sql 互补）：
    1. 多语句拦截：仅允许单条查询；
    2. 静态校验：校验 JOIN（含逗号隐式连接）是否有 ON/USING 条件（防隐式/显式笛卡尔积）；
    3. 动态预检：通过 EXPLAIN 评估预估扫描行数（Rows），熔断大于 5,000,000 行的超大慢查询。
    注：行数上限由下游本地执行（MAX_LOCAL_SQL_ROWS）统一兜底，本网关不再追加 LIMIT。
    """

    @classmethod
    async def verify_and_optimize(
        cls,
        session: AsyncSession,
        *,
        sql: str,
        data_source: str,
        dialect: str,
    ) -> SQLSandboxResult:
        sql_clean = sql.strip().rstrip(";")
        if not sql_clean:
            return SQLSandboxResult(allowed=True, optimized_sql=sql)

        # 1. 静态 AST 解析与校验
        # 说明：解析失败/异常时这里放行，由紧随其后的权威门禁 validate_sql 统一拦截
        # （validate_sql 同样基于 sqlglot 解析，无法解析的 SQL 会被判为语法错误而拒绝），
        # 因此本网关只承担「性能/笛卡尔积」职责，不重复做语法/只读校验。
        try:
            from app.services.sql_query_execution_service import to_sqlglot_dialect

            parsed = sqlglot.parse(sql_clean, read=to_sqlglot_dialect(dialect))
        except ParseError as e:
            logger.warning(f"[SQL Sandbox] Failed to parse SQL AST, defer to validate_sql: {e}")
            return SQLSandboxResult(allowed=True, optimized_sql=sql)
        except Exception as e:
            logger.warning(f"[SQL Sandbox] Unexpected AST parse failure, defer to validate_sql: {e}")
            return SQLSandboxResult(allowed=True, optimized_sql=sql)

        # 多语句必须拦截（不依赖下游）：避免分号拼接的注入/写操作绕过性能与安全网关。
        if parsed and len(parsed) > 1:
            return SQLSandboxResult(
                allowed=False,
                message="[Performance Blocked] 检测到多语句 SQL，仅允许单条只读查询。",
                optimized_sql=sql,
            )
        if not parsed:
            return SQLSandboxResult(allowed=True, optimized_sql=sql)

        expression = parsed[0]

        # 1.1 校验 JOIN 的笛卡尔积风险（显式/隐式连接必须存在 ON 或 USING）
        # 注意：逗号隐式连接 `FROM a, b` 在 sqlglot 中也会解析为不带 ON/USING 的 Join 节点，
        # 因此同样会被下方的「缺失关联条件」分支拦截。
        for join_node in expression.find_all(exp.Join):
            # sqlglot 中, CROSS join 或是 NATURAL join 属于显式无条件，但这里我们主要限制普通的 JOIN / INNER JOIN / LEFT JOIN
            # 注意：不同 sqlglot 版本可能把 CROSS 放在 method 或 kind 上，需同时判断（逗号隐式连接也常被解析为 kind=CROSS）。
            method = (join_node.args.get("method") or "").upper()
            kind = (join_node.args.get("kind") or "").upper()
            if method == "CROSS" or kind == "CROSS":
                # 显式 CROSS JOIN 仍然有极高的全表笛卡尔积风险
                return SQLSandboxResult(
                    allowed=False,
                    message="[Performance Blocked] 检测到 SQL 中包含 CROSS JOIN 笛卡尔积操作。这可能导致查询数据量呈指数级膨胀，请改用显式的 ON 条件关联进行数据收窄！",
                    optimized_sql=sql
                )
            
            on_clause = join_node.args.get("on")
            using_clause = join_node.args.get("using")
            if not on_clause and not using_clause:
                # 缺失关联条件，拦截
                table_display = str(join_node.this)
                return SQLSandboxResult(
                    allowed=False,
                    message=f"[Performance Blocked] SQL 包含没有关联条件的 JOIN 操作（检测到对表 '{table_display}' 的隐式笛卡尔积风险）。为保证数据库性能安全，JOIN 必须带有显式的 ON 或 USING 条件限制！",
                    optimized_sql=sql
                )

        optimized_sql = sql_clean

        # 2. 动态 EXPLAIN 预检 Rows 估算
        # 如果是 Explain 本身、或者 Dry Run，则不需要再进行 Explain 预检
        if re.match(r"^\s*EXPLAIN\b", optimized_sql, re.IGNORECASE):
            return SQLSandboxResult(allowed=True, optimized_sql=optimized_sql)

        try:
            explain_query = f"EXPLAIN {optimized_sql}"
            explain_res_str = await call_external_sql_api(explain_query, data_source=data_source)
            
            estimated_rows = cls._parse_explain_rows(explain_res_str, dialect=dialect)
            if estimated_rows is not None and estimated_rows > MAX_SAFE_ESTIMATED_ROWS:
                return SQLSandboxResult(
                    allowed=False,
                    message=(
                        f"[Performance Blocked] 该查询的物理执行计划预估扫描行数为 {estimated_rows:,} 行，"
                        f"已超过系统安全上限 ({MAX_SAFE_ESTIMATED_ROWS:,} 行)。"
                        f"这通常是由于未建立索引、全表扫描超大表或缺乏有效的时间/ID 过滤范围引起的。"
                        f"请添加时间分区（如 `create_time >= '...'`）、索引字段过滤或者收窄查询范围后再试。"
                    ),
                    optimized_sql=optimized_sql
                )
        except Exception as explain_err:
            # Explain 失败不硬性拦截，记录 warning（确保高可用与兼容性）
            logger.warning(f"[SQL Sandbox] EXPLAIN validation failed/skipped: {explain_err}")

        return SQLSandboxResult(allowed=True, optimized_sql=optimized_sql)

    @classmethod
    def _parse_explain_rows(cls, explain_json_str: str, dialect: str) -> Optional[int]:
        """
        解析不同数据库方言的 EXPLAIN 结果，提取预估的 rows 或扫描行数。
        """
        if not explain_json_str or "[TOOL_ERROR]" in explain_json_str:
            return None

        try:
            data = json.loads(explain_json_str)
        except Exception:
            return None

        # 标准的 rows 累积器
        max_rows = 0
        found_rows = False

        # 情况 1：MySQL 格式
        # 返回类似 [{"id": 1, "select_type": "SIMPLE", "table": "x", "rows": 1234, ...}] 的列表
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    # MySQL 通常为 rows 字段
                    for k, v in row.items():
                        if k.lower() == "rows" and v is not None:
                            try:
                                val = int(float(str(v)))
                                max_rows = max(max_rows, val)
                                found_rows = True
                            except (ValueError, TypeError):
                                pass
                        
                        # ClickHouse 的 EXPLAIN 偶尔也会返回 dict 结构
                        if isinstance(v, str):
                            m = re.search(r"\brows:?\s*(\d+)", v, re.IGNORECASE)
                            if m:
                                max_rows = max(max_rows, int(m.group(1)))
                                found_rows = True
                            
                        # 通用格式: "Rows: 123456" 或 "rows=12345" 或 "12345 rows"
                        if isinstance(v, str):
                            generic_match = re.search(r"\brows\s*[:=]\s*(\d+)", v, re.IGNORECASE)
                            if generic_match:
                                max_rows = max(max_rows, int(generic_match.group(1)))
                                found_rows = True
                                continue
                            
                            generic_match2 = re.search(r"(\d+)\s+rows", v, re.IGNORECASE)
                            if generic_match2:
                                max_rows = max(max_rows, int(generic_match2.group(1)))
                                found_rows = True
        
        # 情况 2：含有 text 列的 ClickHouse 或 Oracle
        # 返回如：[{"Explain": "Read 5000000 rows, ..."}, ...] 或 [{"PLAN_TABLE_OUTPUT": "..."}]
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    # 遍历 dict 的所有值拼接进行正规提取
                    for v in row.values():
                        val_str = str(v)
                        
                        # ClickHouse 格式: "Read 123456 rows"
                        ch_match = re.search(r"Read\s+(\d+)\s+rows", val_str, re.IGNORECASE)
                        if ch_match:
                            max_rows = max(max_rows, int(ch_match.group(1)))
                            found_rows = True
                            continue
                            
                        # 通用格式: "Rows: 123456" 或 "rows=12345" 或 "12345 rows"
                        generic_match = re.search(r"\brows\s*[:=]\s*(\d+)", val_str, re.IGNORECASE)
                        if generic_match:
                            max_rows = max(max_rows, int(generic_match.group(1)))
                            found_rows = True
                            continue
                            
                        generic_match2 = re.search(r"(\d+)\s+rows", val_str, re.IGNORECASE)
                        if generic_match2:
                            max_rows = max(max_rows, int(generic_match2.group(1)))
                            found_rows = True
                            
        if found_rows:
            return max_rows
        return None
