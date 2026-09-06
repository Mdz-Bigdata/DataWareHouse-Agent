import time

from langgraph.runtime import Runtime

from app.agent.complex_planning import decompose_nested_plan
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def decompose_query(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "Decomposer 拆解查询", "status": "running"})
    try:
        decomposition = decompose_nested_plan(state["query_plan"])
        roles = [*state.get("planning_roles", []), "Decomposer"]
        writer(
            {
                "type": "context",
                "planning_roles": roles,
                "decomposed_query": decomposition,
            }
        )
        writer(
            {
                "type": "progress",
                "step": "Decomposer 拆解查询",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {"decomposed_query": decomposition, "planning_roles": roles}
    except Exception as exc:
        writer(
            {
                "type": "progress",
                "step": "Decomposer 拆解查询",
                "status": "error",
                "message": str(exc),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        raise


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
