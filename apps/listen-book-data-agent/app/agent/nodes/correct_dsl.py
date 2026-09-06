"""Correct one invalid QueryDSL attempt while keeping the same semantic context."""

from __future__ import annotations

import time

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.dsl import parse_query_dsl, validate_query_dsl
from app.agent.dsl.normalizer import normalize_query_dsl
from app.agent.dsl.schema import QueryDSL
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def correct_dsl(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "修复DSL", "status": "running"})
    attempts = state.get("dsl_attempts", 0) + 1
    llm_calls = state.get("llm_calls", 0) + 1
    raw = ""
    try:
        llm = await get_llm()
        prompt = PromptTemplate(
            template=load_prompt("correct_dsl"),
            input_variables=[
                "query",
                "analysis_plan",
                "query_plan",
                "selected_semantics",
                "decomposed_query",
                "table_infos",
                "metric_infos",
                "relationships",
                "date_info",
                "schema",
                "max_result_rows",
                "dsl",
                "error",
                "business_rules",
            ],
        )
        raw = await (prompt | llm | StrOutputParser()).ainvoke(
            {
                "query": state["query"],
                "analysis_plan": yaml.dump(
                    state.get("analysis_plan", {}), allow_unicode=True, sort_keys=False
                ),
                "query_plan": yaml.dump(
                    state.get("query_plan", {}), allow_unicode=True, sort_keys=False
                ),
                "selected_semantics": yaml.dump(
                    state.get("selected_semantics", {}),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "decomposed_query": yaml.dump(
                    state.get("decomposed_query", []),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "table_infos": yaml.dump(state["table_infos"], allow_unicode=True, sort_keys=False),
                "metric_infos": yaml.dump(
                    state["metric_infos"], allow_unicode=True, sort_keys=False
                ),
                "relationships": yaml.dump(
                    state.get("relationships", []), allow_unicode=True, sort_keys=False
                ),
                "date_info": yaml.dump(state["date_info"], allow_unicode=True, sort_keys=False),
                "schema": yaml.dump(
                    QueryDSL.model_json_schema(), allow_unicode=True, sort_keys=False
                ),
                "max_result_rows": app_config.query.max_result_rows,
                "dsl": yaml.dump(
                    state.get("query_dsl") or state.get("dsl_raw") or {},
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "error": state.get("dsl_error") or state.get("error") or "DSL 无法编译",
                "business_rules": yaml.dump(
                    state.get("business_rules", []),
                    allow_unicode=True,
                    sort_keys=False,
                ),
            }
        )
        dsl = normalize_query_dsl(
            state["query"],
            parse_query_dsl(raw),
            state["metric_infos"],
            state["table_infos"],
            state.get("analysis_plan"),
        )
        dsl = validate_query_dsl(
            dsl,
            state["metric_infos"],
            state["table_infos"],
            state.get("analysis_plan"),
            app_config.query.max_result_rows,
        )
        payload = dsl.model_dump(mode="json")
        writer(
            {
                "type": "context",
                "generation_mode": "dsl",
                "generation_source": "dsl_corrected",
                "query_dsl": payload,
                "dsl_attempts": attempts,
                "llm_calls": llm_calls,
            }
        )
        writer(
            {
                "type": "progress",
                "step": "修复DSL",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {
            "query_dsl": payload,
            "dsl_raw": raw,
            "dsl_error": None,
            "dsl_attempts": attempts,
            "llm_calls": llm_calls,
            "generation_mode": "dsl",
            "generation_source": "dsl_corrected",
        }
    except Exception as exc:
        message = str(exc) or "DSL 修复失败"
        logger.warning("修复DSL失败，将回退 legacy：{}", message)
        writer(
            {
                "type": "progress",
                "step": "修复DSL",
                "status": "error",
                "message": message,
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {
            "dsl_error": message,
            "dsl_raw": raw,
            "dsl_attempts": attempts,
            "llm_calls": llm_calls,
            "generation_mode": "dsl",
        }


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
