import time

from langgraph.runtime import Runtime

from app.agent.complex_planning import select_query_semantics
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def select_semantics(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "Selector 选择语义", "status": "running"})
    try:
        selection = select_query_semantics(
            state["query_plan"],
            metric_infos=state["metric_infos"],
            table_infos=state["table_infos"],
            relationships=state.get("relationships", []),
        ).to_state()
        roles = [*state.get("planning_roles", []), "Selector"]
        writer(
            {
                "type": "context",
                "planning_roles": roles,
                "selected_semantics": selection,
            }
        )
        writer(
            {
                "type": "progress",
                "step": "Selector 选择语义",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {"selected_semantics": selection, "planning_roles": roles}
    except Exception as exc:
        writer(
            {
                "type": "progress",
                "step": "Selector 选择语义",
                "status": "error",
                "message": str(exc),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        raise


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
