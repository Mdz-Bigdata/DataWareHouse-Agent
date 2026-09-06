import asyncio

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.retrieval import recall_terms
from app.agent.state import DataAgentState
from app.core.log import logger


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Recall non-sensitive enum values; an ES outage must not stop the query."""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": "召回字段取值", "status": "running"})
    terms = recall_terms(state["keywords"], state.get("analysis_plan"))
    try:
        groups = await asyncio.gather(
            *(runtime.context["value_es_repository"].search(term) for term in terms)
        )
        values = {}
        for group in groups:
            for item in group:
                values.setdefault((item.column_id, item.value), item)
        writer({"type": "progress", "step": "召回字段取值", "status": "success"})
        logger.info("字段取值召回完成：{} 项", len(values))
        return {"retrieved_values": list(values.values())[:24], "retrieval_warnings": []}
    except Exception:
        logger.warning("字段取值检索不可用，跳过枚举值召回", exc_info=True)
        warning = "字段取值检索不可用，已跳过枚举值召回。"
        writer({"type": "context", "warnings": [warning]})
        writer({"type": "progress", "step": "召回字段取值", "status": "success"})
        return {"retrieved_values": [], "retrieval_warnings": [warning]}
