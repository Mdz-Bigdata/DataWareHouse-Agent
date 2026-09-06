"""Compile a valid QueryDSL and retain the existing SQL guard as final authority."""

from __future__ import annotations

import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.dsl import DSLCompiler, parse_query_dsl, validate_query_dsl
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger


async def compile_dsl(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "编译DSL", "status": "running"})
    try:
        dsl = validate_query_dsl(
            parse_query_dsl(state["query_dsl"]),
            state["metric_infos"],
            state["table_infos"],
            state.get("analysis_plan"),
            app_config.query.max_result_rows,
        )
        sql = DSLCompiler(app_config.query.max_result_rows).compile(
            dsl,
            state["metric_infos"],
            state.get("relationships", []),
            state["table_infos"],
            dialect=state.get("db_info", {}).get("dialect", "mysql"),
        )
        source = state.get("generation_source") or "dsl_compiled"
        writer({"type": "trace_sql", "sql": sql, "status": "dsl_compiled"})
        writer(
            {
                "type": "context",
                "generation_mode": "dsl",
                "generation_source": source,
                "query_dsl": dsl.model_dump(mode="json"),
                "dsl_attempts": state.get("dsl_attempts", 0),
                "llm_calls": state.get("llm_calls", 0),
            }
        )
        writer(
            {
                "type": "progress",
                "step": "编译DSL",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {
            "sql": sql,
            "dsl_error": None,
            "generation_mode": "dsl",
            "generation_source": source,
        }
    except Exception as exc:
        message = str(exc) or "DSL 编译失败"
        logger.warning("编译DSL失败，将尝试纠正或回退：{}", message)
        writer(
            {
                "type": "progress",
                "step": "编译DSL",
                "status": "error",
                "message": message,
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {"dsl_error": message, "generation_mode": "dsl"}


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
