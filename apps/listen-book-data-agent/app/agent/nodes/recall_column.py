from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.retrieval import (
    allowed_columns,
    batched_vector_search,
    lexical_rank,
    merge_retrieval_results,
    recall_terms,
)
from app.agent.state import DataAgentState
from app.core.log import logger
from app.entities.column_info import ColumnInfo


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Recall safe fields, falling back to lexical metadata search when needed."""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": "召回字段", "status": "running"})
    terms = recall_terms(
        state["keywords"], state.get("analysis_plan"), state.get("query")
    )
    meta_repository = runtime.context["meta_mysql_repository"]
    list_columns = getattr(meta_repository, "list_allowed_column_infos", None)
    meta_repository_lock = runtime.context.get("meta_repository_lock")
    if callable(list_columns) and meta_repository_lock is not None:
        async with meta_repository_lock:
            candidates = await list_columns()
    else:
        candidates = await list_columns() if callable(list_columns) else []
    lexical_columns = lexical_rank(
        candidates,
        terms,
        lambda item: " ".join([item.name, item.description, *item.alias]),
        limit=24,
    )
    try:
        vector_columns = await batched_vector_search(
            terms=terms,
            embedding_client=runtime.context["embedding_client"],
            search=runtime.context["column_qdrant_repository"].search,
        )
        columns = merge_retrieval_results(
            lexical_columns,
            allowed_columns(vector_columns),
            limit=24,
        )
        warnings: list[str] = []
        if not vector_columns and lexical_columns:
            warnings.append("字段向量召回为空，已使用元数据词法结果补齐。")
            writer({"type": "context", "warnings": warnings})
        writer({"type": "progress", "step": "召回字段", "status": "success"})
        logger.info(
            "字段混合召回完成：{} 项（向量 {}、词法 {}）",
            len(columns),
            len(vector_columns),
            len(lexical_columns),
        )
        return {"retrieved_columns": columns, "retrieval_warnings": warnings}
    except Exception:
        logger.warning("字段向量召回不可用，切换为元数据词法检索", exc_info=True)
        warning = "字段向量召回不可用，已切换为元数据词法检索。"
        writer({"type": "context", "warnings": [warning]})
        writer({"type": "progress", "step": "召回字段", "status": "success"})
        return {
            "retrieved_columns": lexical_columns[:24],
            "retrieval_warnings": [warning],
        }
