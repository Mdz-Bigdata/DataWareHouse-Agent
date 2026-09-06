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
from app.services.sql_guard import (
    extract_filter_only_columns,
    extract_sensitive_columns,
    validate_and_normalize_sql,
)


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 1.获取流写入器
    writer = runtime.stream_writer
    # 2.写回正在运行状态
    writer({"type": "progress", "step": "执行SQL", "status": "running"})
    try:
        # 3.业务逻辑
        # 3.1 获取state中SQL语句
        sql = state["sql"]
        table_infos = state["table_infos"]
        # Phase 1.3：执行前二次校验敏感列（纵深防御，防止校验后 SQL 被篡改）
        sensitive_columns = extract_sensitive_columns(table_infos)
        filter_only_columns = extract_filter_only_columns(table_infos)
        # Phase 1.1：执行前二次校验 JOIN 关系（纵深防御）
        relationships = state.get("relationships", [])
        # Phase 1.2：行级数据权限（执行前二次注入，纵深防御）
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
        pipeline = list(
            dict.fromkeys(
                [
                    *state.get("sql_validation_stages", safe_sql.validation_stages),
                    "read_only_timeout",
                ]
            )
        )
        writer(
            {
                "type": "context",
                "execution_mode": "read_only",
                "execution_timeout_seconds": app_config.query.timeout_seconds,
                "sql_validation_stages": pipeline,
            }
        )
        # 3.2 调用数仓执行重复校验后的受限 SQL
        dw_mysql_repository = runtime.context["dw_mysql_repository"]
        try:
            data = await dw_mysql_repository.execute_sql(
                safe_sql.sql, app_config.query.timeout_seconds
            )
        except Exception as db_exc:
            # Phase 4.2：区分基础设施故障与 SQL 错误。
            # 基础设施故障（连接不可达等）不可通过 SQL 修复恢复，返回降级提示。
            # SQL 错误继续向上抛出，走 correct_sql 修复流程。
            if is_infra_failure(db_exc):
                message = degradation_message(db_exc)
                logger.error("数据仓库不可达，查询失败: {}", db_exc)
                writer(
                    {
                        "type": "progress",
                        "step": "执行SQL",
                        "status": "error",
                        "message": message,
                    }
                )
                writer(
                    {
                        "type": "warning",
                        "stage": "execution",
                        "message": message,
                        "reason": "warehouse_unavailable",
                    }
                )
                raise InfrastructureFailure(
                    message, stage="execution", reason="warehouse_unavailable"
                ) from db_exc
            message = str(db_exc) or "SQL 执行失败"
            writer(
                {
                    "type": "progress",
                    "step": "执行SQL",
                    "status": "error",
                    "message": message,
                }
            )
            logger.error("SQL 语义执行失败，交由 Refiner: {}", db_exc)
            return {
                "error": message,
                "error_kind": "sql_semantic",
                "error_stage": "execution",
            }
        columns = list(data[0].keys()) if data else []
        truncated = (
            safe_sql.limit == app_config.query.max_result_rows
            and len(data) == app_config.query.max_result_rows
        )
        logger.info("执行 SQL 成功，返回 {} 行", len(data))
        # 3.3 .业务没有异常，写回成功状态
        writer({"type": "progress", "step": "执行SQL", "status": "success"})
        # 3.4 将SQL查询结果实时写回前端
        writer(
            {
                "type": "result",
                "data": data,
                "sql": safe_sql.sql,
                "columns": columns,
                "row_count": len(data),
                "truncated": truncated,
            }
        )
        # Phase 2.1：若经历过修复（correction_attempts > 0）且本次执行成功，回写 Few-shot 经验。
        # 回写是 best-effort，失败不影响已返回的查询结果。
        await _maybe_record_feedback(runtime.context, state, safe_sql.sql, safe_sql.tables)
        return {
            "sql": safe_sql.sql,
            "result_rows": data,
            "execution_mode": "read_only",
            "sql_validation_stages": pipeline,
        }
    except InfrastructureFailure:
        raise
    except Exception as e:
        if is_infra_failure(e):
            message = degradation_message(e)
            writer(
                {
                    "type": "progress",
                    "step": "执行SQL",
                    "status": "error",
                    "message": message,
                }
            )
            raise InfrastructureFailure(
                message, stage="execution", reason="warehouse_unavailable"
            ) from e
        message = str(e) or "SQL 执行失败"
        writer(
            {
                "type": "progress",
                "step": "执行SQL",
                "status": "error",
                "message": message,
            }
        )
        logger.error("SQL 执行前安全校验失败，交由 Refiner: {}", e)
        return {
            "error": message,
            "error_kind": "sql_semantic",
            "error_stage": "execution",
        }


async def _maybe_record_feedback(
    context: DataAgentContext,
    state: DataAgentState,
    success_sql: str,
    tables: tuple[str, ...],
) -> None:
    """Phase 2.1：执行成功后回写 Few-shot 经验对（仅当经历过修复）。

    条件：correction_attempts > 0 且 previous_error_sql 存在。
    失败静默（best-effort，不影响主流程）。
    """

    if state.get("correction_attempts", 0) < 1:
        return
    error_sql = state.get("previous_error_sql")
    error_message = state.get("previous_error_message") or ""
    if not error_sql or error_sql == success_sql:
        return
    service = context.get("feedback_learning_service")
    if service is None:
        return
    # 表签名：优先用 safe_sql 解析出的真实表名，退化到 state 里的 table_infos
    table_signature = (
        ",".join(sorted(tables))
        if tables
        else ",".join(sorted({t.get("name", "") for t in state.get("table_infos", [])}))
    )
    try:
        configured_dialect = state.get("db_info", {}).get("dialect", "mysql")
        dialect = get_dialect_strategy(configured_dialect).sqlglot_dialect
        await service.record_success_fix(
            question=state["query"],
            error_sql=error_sql,
            corrected_sql=state.get("pre_rls_sql") or success_sql,
            error_message=error_message,
            table_signature=table_signature,
            row_level_scope=state.get("row_level_scope", []),
            dialect=dialect,
        )
    except Exception:
        logger.warning("Few-shot 经验回写异常，已跳过", exc_info=True)
