import time

from langgraph.runtime import Runtime

from app.agent.analysis_plan import build_analysis_plan
from app.agent.context import DataAgentContext
from app.agent.query_plan import build_query_plan_v1
from app.agent.state import DataAgentState
from app.core.log import logger


async def plan_analysis(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Build a deterministic plan before external retrieval starts."""

    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "分析问题", "status": "running"})
    try:
        plan = build_analysis_plan(state["query"]).to_state()
        query_plan = build_query_plan_v1(state["query"], plan).to_state()
        writer(
            {
                "type": "context",
                "analysis_plan": plan,
                "query_plan": query_plan,
                "warnings": [],
            }
        )
        writer(
            {
                "type": "progress",
                "step": "分析问题",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.info("分析计划：{}", query_plan)
        return {"analysis_plan": plan, "query_plan": query_plan}
    except Exception as exc:
        writer(
            {
                "type": "progress",
                "step": "分析问题",
                "status": "error",
                "message": str(exc),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.exception("分析问题失败")
        raise RuntimeError("无法解析查询分析计划") from exc


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
