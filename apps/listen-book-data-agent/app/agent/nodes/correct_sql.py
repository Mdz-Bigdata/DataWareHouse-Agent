import time

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    # 1.获取流写入器
    writer = runtime.stream_writer
    # 2.写回正在运行状态
    writer({"type": "progress", "step": "修复SQL", "status": "running"})
    correction_attempts = state.get("correction_attempts", 0)
    llm_calls = state.get("llm_calls", 0) + 1
    try:
        # 3.业务逻辑
        # 3.1 得到已有上下文：表格信息、指标信息、数据库信息、日期信息、用户问题、错误信息
        query = state["query"]
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        relationships = state.get("relationships", [])
        analysis_plan = state.get("analysis_plan", {})
        db_info = state["db_info"]
        date_info = state["date_info"]
        error = state["error"]
        sql = state["sql"]
        correction_attempts += 1

        # Phase 2.1：召回历史相似修复经验（Few-shot 自愈学习）
        few_shot_text = await _recall_few_shot_examples(runtime.context, query)

        # 3.2 再次调用llm，得到修复后的SQL（按当前启用的供应商配置热切换）
        llm = await get_llm()
        prompt = PromptTemplate(
            template=load_prompt("correct_sql"),
            input_variables=[
                "query",
                "table_infos",
                "metric_infos",
                "relationships",
                "analysis_plan",
                "query_plan",
                "selected_semantics",
                "decomposed_query",
                "db_info",
                "date_info",
                "sql",
                "error",
                "few_shot_examples",
                "business_rules",
            ],
        )
        chain = prompt | llm | StrOutputParser()
        result = await chain.ainvoke(
            {
                "query": query,
                "error": error,
                "sql": sql,
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
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "few_shot_examples": few_shot_text,
                "business_rules": yaml.dump(
                    state.get("business_rules", []),
                    allow_unicode=True,
                    sort_keys=False,
                ),
            }
        )
        # 每轮修复结果都更新追踪中的 SQL，最终失败时保留最后一次尝试。
        writer({"type": "trace_sql", "sql": result, "status": "corrected"})
        writer(
            {
                "type": "context",
                "generation_mode": state.get("generation_mode", "legacy"),
                "generation_source": state.get("generation_source", "legacy_llm"),
                "dsl_attempts": state.get("dsl_attempts", 0),
                "llm_calls": llm_calls,
                "sql_correction_attempts": correction_attempts,
            }
        )
        # 4.业务没有异常，写回成功状态
        logger.info("修复SQL成功：{}", result)
        writer(
            {
                "type": "progress",
                "step": "修复SQL",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        # Phase 2.1：记录修复前的失败 SQL 与报错，供 execute_sql 成功后回写经验对
        return {
            "sql": result,
            "correction_attempts": correction_attempts,
            "previous_error_sql": sql,
            "previous_error_message": error,
            "llm_calls": llm_calls,
        }
    except Exception as e:
        # 5.业务异常，写回错误状态，抛出异常
        writer(
            {
                "type": "progress",
                "step": "修复SQL",
                "status": "error",
                "message": str(e),
                "sql": state.get("sql"),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.error("修复SQL失败:{}", e)
        return {
            "error": str(e),
            "correction_attempts": correction_attempts,
            "llm_calls": llm_calls,
        }


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))


async def _recall_few_shot_examples(context: DataAgentContext, query: str) -> str:
    """Phase 2.1：召回历史相似修复对并格式化为 prompt 文本。

    service 缺失（如测试环境）或召回失败时返回空串，prompt 对应区块为空，
    等价于零样本纠错，向后兼容。
    """

    service = context.get("feedback_learning_service")
    if service is None:
        return ""
    try:
        entries = await service.recall_similar_fixes(query)
    except Exception:
        logger.warning("Few-shot 经验召回异常，回退零样本纠错", exc_info=True)
        return ""
    if not entries:
        return ""
    blocks = []
    for i, entry in enumerate(entries, 1):
        blocks.append(
            f"【经验{i}】相似问题：{entry.question}\n"
            f"错误SQL：{entry.error_sql}\n"
            f"正确SQL：{entry.corrected_sql}\n"
            f"当时报错：{entry.error_message}"
        )
    return "【历史相似修复经验】（仅供参考，需结合当前上下文独立判断）\n" + "\n".join(blocks) + "\n"
