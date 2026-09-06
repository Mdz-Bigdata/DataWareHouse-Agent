import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.services.chart_spec_service import build_chart_spec


async def generate_chart_spec(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
):
    """Create a deterministic ChartSpecV1 from the in-request result rows."""

    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "生成图表规格", "status": "running"})
    rows = state.get("result_rows", [])
    columns = list(rows[0].keys()) if rows else []
    spec = build_chart_spec(columns, rows)
    payload = spec.model_dump(mode="json")
    writer({"type": "visualization", "chart_spec": payload})
    writer(
        {
            "type": "progress",
            "step": "生成图表规格",
            "status": "success",
            "duration_ms": _elapsed_ms(started_at),
        }
    )
    logger.info("已生成受结果列约束的 ChartSpecV1：{}", spec.type)
    return {"chart_spec": payload}


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
