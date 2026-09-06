from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def report_sql_error(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """End an unsafe or uncorrectable SQL request without reaching the warehouse."""

    runtime.stream_writer(
        {
            "type": "error",
            "stage": state.get("error_stage") or "sql_validation",
            "message": state.get("error") or "SQL 校验失败",
            "reason": "sql_refinement_exhausted",
        }
    )
    return {}
