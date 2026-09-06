import time

from langgraph.runtime import Runtime

from app.agent.complex_planning import refine_complex_plan
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def refine_query_plan(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "Refiner 校验计划", "status": "running"})
    try:
        query_plan = refine_complex_plan(
            state["query_plan"],
            state.get("selected_semantics", {}),
            state.get("decomposed_query", []),
        )
        roles = [*state.get("planning_roles", []), "Refiner"]
        writer(
            {
                "type": "context",
                "query_plan": query_plan,
                "planning_roles": roles,
                "query_plan_refined": True,
            }
        )
        writer(
            {
                "type": "progress",
                "step": "Refiner 校验计划",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {
            "query_plan": query_plan,
            "planning_roles": roles,
            "query_plan_refined": True,
        }
    except Exception as exc:
        writer(
            {
                "type": "progress",
                "step": "Refiner 校验计划",
                "status": "error",
                "message": str(exc),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        raise


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
