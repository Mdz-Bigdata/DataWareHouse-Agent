from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.retrieval import (
    batched_vector_search,
    lexical_rank,
    merge_retrieval_results,
    recall_terms,
)
from app.agent.state import DataAgentState
from app.core.log import logger


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Recall metric definitions with a metadata lexical fallback."""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": "召回指标", "status": "running"})
    terms = recall_terms(
        state["keywords"], state.get("analysis_plan"), state.get("query")
    )
    meta_repository = runtime.context["meta_mysql_repository"]
    list_metrics = getattr(meta_repository, "list_metric_infos", None)
    meta_repository_lock = runtime.context.get("meta_repository_lock")
    if callable(list_metrics) and meta_repository_lock is not None:
        async with meta_repository_lock:
            candidates = await list_metrics()
    else:
        candidates = await list_metrics() if callable(list_metrics) else []
    lexical_metrics = lexical_rank(
        candidates,
        terms,
        lambda item: " ".join([item.name, item.description, item.formula, *item.alias]),
    )
    try:
        vector_metrics = await batched_vector_search(
            terms=terms,
            embedding_client=runtime.context["embedding_client"],
            search=runtime.context["metric_qdrant_repository"].search,
        )
        metrics = merge_retrieval_results(lexical_metrics, vector_metrics, limit=12)
        warnings: list[str] = []
        if not vector_metrics and lexical_metrics:
            warnings.append("指标向量召回为空，已使用元数据词法结果补齐。")
            writer({"type": "context", "warnings": warnings})
        writer({"type": "progress", "step": "召回指标", "status": "success"})
        logger.info(
            "指标混合召回完成：{} 项（向量 {}、词法 {}）",
            len(metrics),
            len(vector_metrics),
            len(lexical_metrics),
        )
        return {"retrieved_metrics": metrics, "retrieval_warnings": warnings}
    except Exception:
        logger.warning("指标向量召回不可用，切换为元数据词法检索", exc_info=True)
        warning = "指标向量召回不可用，已切换为元数据词法检索。"
        writer({"type": "context", "warnings": [warning]})
        writer({"type": "progress", "step": "召回指标", "status": "success"})
        return {
            "retrieved_metrics": lexical_metrics[:12],
            "retrieval_warnings": [warning],
        }
