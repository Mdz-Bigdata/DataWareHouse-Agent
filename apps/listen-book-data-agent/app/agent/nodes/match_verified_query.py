from __future__ import annotations

import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger


async def match_verified_query(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
):
    """Use exact published cases deterministically and approximate cases as context only."""

    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "匹配可信案例", "status": "running"})
    service = runtime.context.get("query_set_match_service")
    if service is None:
        writer({"type": "progress", "step": "匹配可信案例", "status": "skipped"})
        return {"verified_query_examples": []}

    access_policy = state.get("access_policy", {})
    domain = str(access_policy.get("domain") or "audio")
    datasource = str(access_policy.get("datasource") or app_config.db_dw.database)
    dialect = str(state.get("db_info", {}).get("dialect") or "mysql")
    try:
        result = await service.match(
            state["query"],
            parameters=state.get("query_parameters") or {},
            domain=domain,
            datasource=datasource,
            dialect=dialect,
        )
    except Exception as exc:
        logger.warning("可信案例召回降级：{}", exc)
        writer(
            {
                "type": "warning",
                "stage": "verified_query_retrieval",
                "message": "可信案例暂时不可用，已继续使用常规生成链路。",
            }
        )
        writer(
            {
                "type": "progress",
                "step": "匹配可信案例",
                "status": "degraded",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {"verified_query_examples": []}

    query_set = result.query_set
    query_set_metadata = (
        {
            "query_set_id": query_set.id,
            "query_set_version": query_set.version,
            "query_set_hash": query_set.content_hash,
        }
        if query_set is not None
        else {}
    )
    if result.semantic_release_id is not None:
        query_set_metadata.update(
            {
                "semantic_release_id": result.semantic_release_id,
                "semantic_release_version": result.semantic_release_version,
            }
        )
    if result.exact_sql is not None and result.exact_example is not None:
        match_metadata = _public_example(result.exact_example)
        writer(
            {
                "type": "trace_sql",
                "sql": result.exact_sql,
                "status": "generated",
                "source": "verified_exact",
            }
        )
        writer(
            {
                "type": "context",
                "generation_mode": app_config.query.generation_mode.lower(),
                "generation_source": "verified_exact",
                "verified_query_match": match_metadata,
                **query_set_metadata,
            }
        )
        writer(
            {
                "type": "progress",
                "step": "匹配可信案例",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {
            "sql": result.exact_sql,
            "generation_mode": app_config.query.generation_mode.lower(),
            "generation_source": "verified_exact",
            "verified_query_match": match_metadata,
            "verified_query_examples": [],
            **query_set_metadata,
        }

    examples = [_prompt_example(item) for item in result.semantic_examples]
    context_event = {
        "type": "context",
        "verified_query_examples": [_public_example(item) for item in result.semantic_examples],
        **query_set_metadata,
    }
    if result.exact_error:
        context_event["verified_exact_error"] = result.exact_error
    writer(context_event)
    writer(
        {
            "type": "progress",
            "step": "匹配可信案例",
            "status": "success",
            "duration_ms": _elapsed_ms(started_at),
        }
    )
    return {
        "verified_query_examples": examples,
        "verified_exact_error": result.exact_error,
        **query_set_metadata,
    }


def _prompt_example(example) -> dict:
    return {
        "case_key": example.case_key,
        "question": example.question,
        "sql_template": example.sql_template,
        "parameter_schema": example.parameter_schema,
        "expected_fields": example.expected_fields,
        "expected_metrics": example.expected_metrics,
        "similarity": round(example.score, 4),
    }


def _public_example(example) -> dict:
    return {
        "case_key": example.case_key,
        "revision_id": example.revision_id,
        "question": example.question,
        "similarity": round(example.score, 4),
    }


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
