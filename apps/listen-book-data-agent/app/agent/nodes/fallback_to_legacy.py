"""Record an explicit, observable hand-off from DSL to the legacy SQL path."""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def fallback_to_legacy(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    reason = state.get("dsl_error") or state.get("error") or "DSL 无法表达当前查询"
    runtime.stream_writer(
        {
            "type": "context",
            "generation_mode": "dsl",
            "generation_source": "legacy_fallback",
            "query_dsl": state.get("query_dsl"),
            "dsl_fallback_reason": reason,
            "dsl_attempts": state.get("dsl_attempts", 0),
            "llm_calls": state.get("llm_calls", 0),
        }
    )
    return {
        "dsl_fallback": True,
        "dsl_fallback_reason": reason,
        "generation_mode": "dsl",
        "generation_source": "legacy_fallback",
        "error": None,
    }
