import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.services.dry_plan_service import validate_dry_plan


async def validate_query_plan(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "Dry Plan 校验", "status": "running"})
    try:
        result = validate_dry_plan(
            state["query_plan"],
            metric_infos=state["metric_infos"],
            table_infos=state["table_infos"],
            relationships=state.get("relationships", []),
            access_policy=state.get("access_policy", {}),
            max_result_rows=app_config.query.max_result_rows,
        )
        checks = list(result.checks)
        writer(
            {
                "type": "context",
                "dry_plan_status": "validated",
                "dry_plan_checks": checks,
            }
        )
        writer(
            {
                "type": "progress",
                "step": "Dry Plan 校验",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {"dry_plan_status": "validated", "dry_plan_checks": checks}
    except Exception as exc:
        writer(
            {
                "type": "progress",
                "step": "Dry Plan 校验",
                "status": "error",
                "message": str(exc),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        raise


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
