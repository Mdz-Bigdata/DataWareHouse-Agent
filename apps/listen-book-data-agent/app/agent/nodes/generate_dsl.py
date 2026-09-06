"""Generate and validate a QueryDSL instead of asking the LLM for SQL text."""

from __future__ import annotations

import time

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.dsl import parse_query_dsl, validate_query_dsl
from app.agent.dsl.normalizer import (
    build_catalog_metric_dsl,
    build_status_compare_dsl,
    normalize_query_dsl,
)
from app.agent.dsl.schema import QueryDSL
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def generate_dsl(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Ask the LLM for a closed JSON plan and validate it against recalled metadata."""

    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "生成DSL", "status": "running"})
    attempts = state.get("dsl_attempts", 0) + 1
    llm_calls = state.get("llm_calls", 0)
    raw = ""
    try:
        business_rules = state.get("business_rules", [])
        catalog_dsl = None
        if not business_rules:
            catalog_dsl = build_catalog_metric_dsl(
                state["query"],
                state["metric_infos"],
                max_result_rows=app_config.query.max_result_rows,
            )
        deterministic_source = "dsl_deterministic_metric"
        if catalog_dsl is None and not business_rules:
            catalog_dsl = build_status_compare_dsl(
                state["query"],
                state["metric_infos"],
                state["table_infos"],
                max_result_rows=app_config.query.max_result_rows,
            )
            deterministic_source = "dsl_deterministic_compare"
        if catalog_dsl is not None:
            dsl = validate_query_dsl(
                catalog_dsl,
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
                    "generation_source": deterministic_source,
                    "query_dsl": payload,
                    "dsl_attempts": attempts,
                    "llm_calls": llm_calls,
                }
            )
            writer(
                {
                    "type": "progress",
                    "step": "生成DSL",
                    "status": "success",
                    "duration_ms": _elapsed_ms(started_at),
                }
            )
            return {
                "query_dsl": payload,
                "dsl_raw": None,
                "dsl_error": None,
                "dsl_attempts": attempts,
                "llm_calls": llm_calls,
                "generation_mode": "dsl",
                "generation_source": deterministic_source,
            }

        llm = await get_llm()
        llm_calls += 1
        prompt = PromptTemplate(
            template=load_prompt("generate_dsl"),
            input_variables=[
                "query",
                "table_infos",
                "metric_infos",
                "relationships",
                "analysis_plan",
                "query_plan",
                "selected_semantics",
                "decomposed_query",
                "verified_examples",
                "business_rules",
                "date_info",
                "schema",
                "max_result_rows",
            ],
        )
        raw = await (prompt | llm | StrOutputParser()).ainvoke(
            {
                "query": state["query"],
                "table_infos": yaml.dump(state["table_infos"], allow_unicode=True, sort_keys=False),
                "metric_infos": yaml.dump(
                    state["metric_infos"], allow_unicode=True, sort_keys=False
                ),
                "relationships": yaml.dump(
                    state.get("relationships", []), allow_unicode=True, sort_keys=False
                ),
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
                "verified_examples": yaml.dump(
                    _dsl_examples(state.get("verified_query_examples", [])),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "business_rules": yaml.dump(
                    business_rules,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "date_info": yaml.dump(state["date_info"], allow_unicode=True, sort_keys=False),
                "schema": yaml.dump(
                    QueryDSL.model_json_schema(), allow_unicode=True, sort_keys=False
                ),
                "max_result_rows": app_config.query.max_result_rows,
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
                "generation_source": "dsl_compiled",
                "query_dsl": payload,
                "dsl_attempts": attempts,
                "llm_calls": llm_calls,
            }
        )
        writer(
            {
                "type": "progress",
                "step": "生成DSL",
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
            "generation_source": "dsl_compiled",
        }
    except Exception as exc:
        message = str(exc) or "DSL 生成失败"
        logger.warning("生成DSL失败，将尝试纠正或回退：{}", message)
        writer(
            {
                "type": "progress",
                "step": "生成DSL",
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


def _dsl_examples(examples: list[dict]) -> list[dict]:
    """DSL few-shot receives semantic contracts, never raw SQL templates."""

    allowed = {
        "case_key",
        "question",
        "parameter_schema",
        "expected_fields",
        "expected_metrics",
        "similarity",
    }
    return [{key: value for key, value in item.items() if key in allowed} for item in examples]
