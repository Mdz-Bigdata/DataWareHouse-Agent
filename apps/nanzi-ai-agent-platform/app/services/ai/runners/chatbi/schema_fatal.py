"""Schema fatal termination checks and user-facing responses."""

from __future__ import annotations

from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runners.chatbi.run_state import DataRunState


def is_schema_fatal(state: DataRunState) -> bool:
    return (
        state.schema_service_unavailable
        or state.no_authorized_schema
        or state.rag_not_synced
        or state.schema_miss_count >= 2
    )


def schema_fatal_response(state: DataRunState) -> tuple[str, str]:
    if state.schema_service_unavailable:
        return (
            "元数据服务不可用",
            DataQueryPrompts.SCHEMA_SERVICE_UNAVAILABLE_CONTENT,
        )
    if state.no_authorized_schema:
        return (
            "无授权数据集",
            DataQueryPrompts.NO_AUTHORIZED_SCHEMA_CONTENT,
        )
    if state.rag_not_synced:
        return (
            "元数据未同步知识库",
            DataQueryPrompts.RAG_NOT_SYNCED_CONTENT,
        )
    if state.schema_miss_count >= 2:
        return (
            "连续未命中数据集定义",
            DataQueryPrompts.SCHEMA_MISS_EXHAUSTED_CONTENT,
        )
    return (
        "Schema 获取失败",
        DataQueryPrompts.SCHEMA_SERVICE_UNAVAILABLE_CONTENT,
    )
