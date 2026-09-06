import time

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState, DateInfoState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt
from app.services.deterministic_sql_service import build_catalog_metric_sql, build_deterministic_sql


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    # 1.获取流写入器
    writer = runtime.stream_writer
    # 2.写回正在运行状态
    writer({"type": "progress", "step": "生成SQL", "status": "running"})
    try:
        llm_calls = state.get("llm_calls", 0)
        is_dsl_fallback = bool(state.get("dsl_fallback"))
        # 3.业务逻辑
        # 3.1 从state中获取生成SQL需要的数据
        query = state["query"]
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        relationships = state.get("relationships", [])
        analysis_plan = state.get("analysis_plan", {})
        db_info = state["db_info"]
        date_info = state["date_info"]
        business_rules = state.get("business_rules", [])
        # 3.2 规范化计划覆盖的常见聚合优先走确定性模板，避免重复 LLM 修复。
        result = None if business_rules else build_deterministic_sql(analysis_plan, table_infos)
        source = "deterministic"
        if result is None and not business_rules:
            result = build_catalog_metric_sql(query, metric_infos)
            source = "deterministic_metric" if result is not None else source
        if result is None:
            source = "llm"
            llm = await get_llm()
            llm_calls += 1
            prompt = PromptTemplate(
                template=load_prompt("generate_sql"),
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
                    "db_info",
                    "date_info",
                ],
            )
            chain = prompt | llm | StrOutputParser()
            result = await chain.ainvoke(
                {
                    "query": query,
                    "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False),
                    "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False),
                    "relationships": yaml.dump(relationships, allow_unicode=True, sort_keys=False),
                    "analysis_plan": yaml.dump(analysis_plan, allow_unicode=True, sort_keys=False),
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
                        state.get("verified_query_examples", []),
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    "business_rules": yaml.dump(
                        business_rules,
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                    "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                }
            )
        # 内部追踪事件：保存尚未校验的原始 SQL，查询失败时供管理员排查。
        generation_source = "legacy_fallback" if is_dsl_fallback else f"legacy_{source}"
        writer(
            {
                "type": "trace_sql",
                "sql": result,
                "status": "generated",
                "source": source,
            }
        )
        writer(
            {
                "type": "context",
                "generation_mode": "dsl" if is_dsl_fallback else "legacy",
                "generation_source": generation_source,
                "query_dsl": state.get("query_dsl") if is_dsl_fallback else None,
                "dsl_fallback_reason": (
                    state.get("dsl_fallback_reason") if is_dsl_fallback else None
                ),
                "dsl_attempts": state.get("dsl_attempts", 0),
                "llm_calls": llm_calls,
            }
        )
        # 3.3业务没有异常，写回成功状态
        writer(
            {
                "type": "progress",
                "step": "生成SQL",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.info("生成SQL成功（{}）：{}", source, result)
        # 3.4 更新state
        return {
            "sql": result,
            "generation_mode": "dsl" if is_dsl_fallback else "legacy",
            "generation_source": generation_source,
            "llm_calls": llm_calls,
        }
    except Exception as e:
        # 5.业务异常，写回错误状态，抛出异常
        writer(
            {
                "type": "progress",
                "step": "生成SQL",
                "status": "error",
                "message": str(e),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.error(f"生成SQL失败:{e}")
        raise


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))


if __name__ == "__main__":
    date_info = DateInfoState(date="2023-05-01", weekday="星期五", quarter="Q2")
    print(yaml.dump(date_info, allow_unicode=True, sort_keys=False))
