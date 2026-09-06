import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.degradation import (
    InfrastructureFailure,
    degradation_message,
    is_infra_failure,
)
from app.core.log import logger
from app.repositories.dialect import get_dialect_strategy
from app.services.explain_budget_service import enforce_explain_budget
from app.services.sql_guard import (
    extract_filter_only_columns,
    extract_sensitive_columns,
    validate_and_normalize_sql,
)


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    # 1.获取流写入器
    writer = runtime.stream_writer
    # 2.写回正在运行状态
    writer({"type": "progress", "step": "校验SQL", "status": "running"})
    try:
        # 3.业务逻辑
        # 3.1 获取state中SQL
        sql = state["sql"]
        table_infos = state["table_infos"]
        # Phase 1.3：提取敏感列集合，执行前阻断敏感字段查询
        sensitive_columns = extract_sensitive_columns(table_infos)
        filter_only_columns = extract_filter_only_columns(table_infos)
        # Phase 1.1：JOIN 关系白名单，防止笛卡尔积/多对多放大
        relationships = state.get("relationships", [])
        # Phase 1.2：行级数据权限（admin 为空列表，不注入）
        row_level_scope = state.get("row_level_scope", [])
        access_policy = state.get("access_policy", {})
        # Phase 3.2：从 db_info 取方言，传给 guard 做方言化解析/生成
        configured_dialect = state.get("db_info", {}).get("dialect", "mysql")
        dialect = get_dialect_strategy(configured_dialect).sqlglot_dialect
        safe_sql = validate_and_normalize_sql(
            sql,
            table_infos,
            app_config.query.max_result_rows,
            sensitive_columns=sensitive_columns,
            filter_only_columns=filter_only_columns,
            relationships=relationships,
            row_level_scope=row_level_scope,
            analysis_plan=state.get("analysis_plan", {}),
            dialect=dialect,
            table_acl=access_policy.get("table_acl"),
            allowed_functions=access_policy.get("function_whitelist"),
        )
        # 3.2 使用 EXPLAIN 校验受限后的 SQL
        dw_mysql_repository = runtime.context["dw_mysql_repository"]
        estimate = await dw_mysql_repository.validate_sql(
            safe_sql.sql, app_config.query.timeout_seconds
        )
        estimate_state = None
        if estimate is not None:
            enforce_explain_budget(
                estimate,
                max_cost=app_config.query.explain_cost_budget,
                max_rows=app_config.query.explain_rows_budget,
            )
            estimate_state = estimate.to_state()
        validation_stages = [*safe_sql.validation_stages, "explain_cost"]
        writer(
            {
                "type": "context",
                "sql_validation_stages": validation_stages,
                "explain_estimate": estimate_state,
                "explain_budget": {
                    "max_cost": app_config.query.explain_cost_budget,
                    "max_rows": app_config.query.explain_rows_budget,
                },
            }
        )
        writer({"type": "sql", "sql": safe_sql.sql, "status": "validated"})
        # 4.业务没有异常，写回成功状态
        writer(
            {
                "type": "progress",
                "step": "校验SQL",
                "status": "success",
                "sql": safe_sql.sql,
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {
            "sql": safe_sql.sql,
            "pre_rls_sql": sql,
            "error": None,
            "explain_estimate": estimate_state or {},
            "sql_validation_stages": validation_stages,
        }
    except Exception as e:
        if is_infra_failure(e):
            message = degradation_message(e)
            writer(
                {
                    "type": "progress",
                    "step": "校验SQL",
                    "status": "error",
                    "message": message,
                    "sql": state.get("sql"),
                    "duration_ms": _elapsed_ms(started_at),
                }
            )
            logger.error("SQL EXPLAIN 基础设施失败:{}", e)
            raise InfrastructureFailure(
                message, stage="sql_validation", reason="warehouse_unavailable"
            ) from e
        writer(
            {
                "type": "progress",
                "step": "校验SQL",
                "status": "error",
                "message": str(e),
                "sql": state.get("sql"),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.error(f"校验SQL失败:{e}")
        return {
            "error": str(e),
            "error_kind": "sql_semantic",
            "error_stage": "sql_validation",
        }


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
